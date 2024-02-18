import datetime
import time
from typing import Any, Union
import sys
import aiomysql as aiomysql
from fastapi import FastAPI, Request  # 导入FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn               # uvicorn:主要用于加载和提供应用程序的服务器
from fastapi.responses import RedirectResponse
import os
import yaml
import hashlib
import base64
import hmac
from pydantic import BaseModel
import random
import string


config_file = "config.yaml"
default_doc = False
if len(sys.argv) > 1:
    env_name = sys.argv[1]
    config_file = f"config.{env_name}.yaml"
    if env_name == "dev":
        default_doc = True
with open(config_file) as f:
    config = yaml.safe_load(f)
sql_host = config['database']['host']
sql_port = int(config['database']['port'])
sql_user = config['database']['user']
sql_password = str(config['database']['password'])
sql_database = config['database']['database']
sign_secret = config['sign']['secret']
host = config['host']['host'] if (config.get('host') and config['host'].get('host')) is not None else None
port = int(config['host']['port']) if (config.get('host') and config['host'].get('port')) is not None else None

env = os.environ
# 创建一个app实例
app = FastAPI() if default_doc or (env.get("docs") is not None and env.get("docs").lower() == "true")\
    else FastAPI(openapi_url=None)


# 配置 CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 允许所有来源，可以根据需求进行配置
    allow_credentials=True,
    allow_methods=["*"],  # 允许所有请求方法
    allow_headers=["*"],  # 允许所有请求头
)


async def get_connect():
    conn = await aiomysql.connect(
        host=sql_host,
        port=sql_port,
        user=sql_user,
        password=sql_password,
        db=sql_database
    )
    return conn


async def get_sources() -> tuple:
    conn = await get_connect()
    async with conn.cursor() as cursor:
        await cursor.execute('SELECT source FROM surl')
        ret = await cursor.fetchall()
    conn.close()
    return tuple([i[0] for i in ret])


async def get_redirect_url(code: str) -> dict:
    conn = await get_connect()
    sql = f"SELECT id, source, target, createTime, expireTime FROM surl where source = '{code}';"
    async with conn.cursor() as cursor:
        await cursor.execute(sql)
        ret = await cursor.fetchall()
    conn.close()
    if ret == ():
        return {}
    url_info = {
        "id": ret[0][0],
        "source": ret[0][1],
        "target": ret[0][2],
        "createTime": ret[0][3],
        "expireTime": ret[0][4]
    }
    return url_info


async def get_is_expired(url_info: dict) -> bool:
    if url_info == {}:
        return True
    expire_time = url_info.get("expireTime")
    if expire_time is None or expire_time >= datetime.datetime.now():
        return False
    sql = f"DELETE FROM surl WHERE id = '{url_info.get('id')}';"
    conn = await get_connect()
    async with conn.cursor() as cursor:
        await cursor.execute(sql)
        await conn.commit()
    conn.close()
    return True


async def get_is_out_of_date(ts: int) -> bool:
    return abs(time.time() - ts) > 300


async def insert_surl(source: str, target: str, expire: Union[int, None] = None) -> None:
    sql = f"INSERT INTO surl (`source`, `target`) value('{source}', '{target}')"
    if expire:
        expire_time = datetime.datetime.fromtimestamp(expire)
        sql = f"INSERT INTO surl (`source`, `target`, `expireTime`) value('{source}', '{target}', '{expire_time}')"
    conn = await get_connect()
    async with conn.cursor() as cursor:
        await cursor.execute(sql)
        await conn.commit()
    conn.close()


async def update_target(source: str, target: str, expire: Union[int, None]) -> None:
    sql = f"UPDATE surl SET `target` = '{target}' WHERE `source` = '{source}'"
    if expire:
        expire_time = datetime.datetime.fromtimestamp(expire)
        sql = f"UPDATE surl SET `target` = '{target}', `expireTIme` = '{expire_time}' WHERE `source` = '{source}'"
    conn = await get_connect()
    print(sql)
    async with conn.cursor() as cursor:
        await cursor.execute(sql)
        await conn.commit()
    conn.close()


async def delete_surl(source: str) -> None:
    sql = f"DELETE FROM surl WHERE `source` = '{source}'"
    conn = await get_connect()
    async with conn.cursor() as cursor:
        await cursor.execute(sql)
        await conn.commit()
    conn.close()


async def get_all_surl_by_offset(page: Union[int, None], size: Union[int, None], base_url: Any) -> list:
    if not page:
        page = 0
    if not size:
        size = 20
    sql = (f"SELECT `source`, `target`, `createTime`, `expireTime` FROM surl "
           f"ORDER BY `createTime` LIMIT {size} OFFSET {page * size}")
    conn = await get_connect()
    async with conn.cursor() as cursor:
        await cursor.execute(sql)
        ret = await cursor.fetchall()
    conn.close()
    return [{
        "source": i[0],
        "target": i[1],
        "url": f"{base_url}s/{i[0]}",
        "created_time": i[2],
        "expire_time": i[3],
    } for i in ret]


async def gen_sign(timestamp: Union[str, int]) -> str:
    string_to_sign = '{}\n{}'.format(timestamp, sign_secret)
    hmac_code = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    sign = base64.b64encode(hmac_code).decode('utf-8')
    return sign


async def gen_new_source(length: int) -> str:
    letters = string.ascii_letters  # 包含所有字母的字符串
    return ''.join(random.choice(letters) for _ in range(length))


async def get_is_valid(timestamp: Union[str, int], sign: str) -> bool:
    return not await get_is_out_of_date(timestamp) and sign == await gen_sign(timestamp)


@app.get("/s/{source}")
async def redirect_target(source: str) -> Any:
    """
        短链接重定向

        当访问/s/xxxx时，重定向到xxxx对应的链接

        无返回值，直接重定向到目标链接
    """
    url_info = await get_redirect_url(source)
    if not await get_is_expired(url_info):
        return RedirectResponse(url_info["target"])
    return {"code": 404, "msg": "Not Found"}


@app.get("/surl/{source}")
async def redirect_target(source: str, request: Request) -> dict:
    """
        短链接重定向地址查询

        当访问/surl/xxxx时，返回xxxx对应的链接

        链接有效，正常返回

            {
                "code": 200,
                "msg": "success",
                "data": {
                    "source": 短链接后缀,
                    "target": 目标链接,
                    "url": 短链接
                }
            }

        链接无效，错误返回

            {
                "code": 404,
                "msg" "Not Found"
            }
    """
    url_info = await get_redirect_url(source)
    if not await get_is_expired(url_info):
        url = f"{request.base_url}s/{source}"
        return {"code": 200, "msg": "success", "data": {"source": source, "target": url_info["target"], "url": url}}
    return {"code": 404, "msg": "Not Found"}


class CreateShortURLRequest(BaseModel):
    sign: str
    url: str
    ts: int
    source: Union[str, None] = None
    expire_time: Union[int, None] = None


@app.post("/create_short_url")
async def create_short_url(params: CreateShortURLRequest, request: Request) -> dict:
    """
        创建短链接接口

        Params:

            {
                "sign": str   # 签名，用于验证请求有效性
                "url": str    # 目标url
                "ts": int     # 发出请求时的时间戳
                "source": str   # 自定义后缀（可选）
                "expire_time": int    # 过期时间戳（可选）
            }

        Return:

            {
                "code": 200,
                "msg": success,
                "data": {
                    "source": 随机生成的短链接后缀,
                    "target": 目标url,
                    "url": 短链接,
                }
            }
    """
    source = params.source
    if not source:
        source = await gen_new_source(5)
    elif source in await get_sources():
        return {"code": -1, "msg": "source exists"}
    if not await get_is_valid(params.ts, params.sign):
        return {"code": 400, "msg": "bad request"}
    await insert_surl(source, params.url, params.expire_time)
    url = f"{request.base_url}s/{source}"
    return {"code": 200, "msg": "success", "data": {"source": source, "target": params.url, "url": url}}


class EditShortURLRequest(BaseModel):
    sign: str
    url: str
    ts: int
    source: str
    expire_at: Union[int, None] = None


@app.post("/update_short_url")
async def update_short_url(params: EditShortURLRequest, request: Request) -> dict:
    """
        修改短链接接口

        Params:

            {
                "sign": str   # 签名，用于验证请求有效性
                "url": str    # 目标url
                "ts": int     # 发出请求时的时间戳
                "source": str   # 短链接后缀
                "expire_at": int    # 过期时间戳（可选）
            }

        Return:

            {
                "code": 200,
                "msg": success,
                "data": {
                    "source": 短链接后缀,
                    "target": 目标url,
                    "url": 短链接,
                    "expire_time": 过期时间,（如果有expire_time）
                    "expire_at": 过期时间戳（如果有expire_time）
                }
            }
    """
    if not await get_is_valid(params.ts, params.sign) or params.source not in await get_sources():
        return {"code": 400, "msg": "bad request"}
    await update_target(params.source, params.url, params.expire_at)
    url = f"{request.base_url}s/{params.source}"
    if params.expire_at:
        return {"code": 200, "msg": "success", "data": {
            "source": params.source,
            "target": params.url,
            "url": url,
            "expire_time": datetime.datetime.fromtimestamp(params.expire_at),
            "expire_at": params.expire_at
            }
        }
    return {"code": 200, "msg": "success", "data": {"source": params.source, "target": params.url, "url": url}}


class DeleteShortURLRequest(BaseModel):
    sign: str
    ts: int
    source: str


@app.post("/delete_short_url")
async def delete_short_url(params: DeleteShortURLRequest) -> dict:
    """
        删除短链接接口

        Params:

            {
                "sign": str   # 签名，用于验证请求有效性
                "ts": int     # 发出请求时的时间戳
                "source": str   # 短链接后缀
            }

        Return:

            {
                "code": 200,
                "msg": success,
            }
    """
    if not await get_is_valid(params.ts, params.sign) or params.source not in await get_sources():
        return {"code": 400, "msg": "bad request"}
    await delete_surl(params.source)
    return {"code": 200, "msg": "success"}


class ListShortURLRequest(BaseModel):
    sign: str
    ts: int
    page: Union[int, None] = None
    size: Union[int, None] = None


@app.post("/list_short_url")
async def list_short_url(params: ListShortURLRequest, request: Request) -> dict:
    """
        查询所有短链接接口

        Params:

            {
                "sign": str     # 签名，用于验证请求有效性
                "ts": int       # 发出请求时的时间戳
                "page": int     # 页数（可选），默认第一页
                "size": int     # 每一页的数量（可选），默认20个
            }

        Return:

            {
                "code": 200,
                "msg": success,
                "data": {
                    "source": 短链接后缀,
                    "target": 目标地址,
                    "url": 短链接,
                    "created_time": 创建时间,
                    "expire_time": 过期时间,
                }
            }
    """
    if not await get_is_valid(params.ts, params.sign):
        return {"code": 400, "msg": "bad request"}
    surl_list = await get_all_surl_by_offset(params.page, params.size, request.base_url)
    return {"code": 200, "msg": "success", "data": surl_list}


if __name__ == '__main__':
    host = (env.get("HOST") if env.get("HOST") is not None else host) or "0.0.0.0"
    port = (int(env.get("PORT")) if env.get("PORT") is not None else port) or 8000
    uvicorn.run(app='main:app', host=host, port=port, reload=True)
