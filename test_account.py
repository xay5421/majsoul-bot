"""查询账号信息和段位"""
import asyncio
import logging
from google.protobuf.json_format import MessageToDict
from client import MajsoulClient
from config import load_config
import ms.protocol_pb2 as pb

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("test")

async def main():
    config = load_config()
    client = MajsoulClient()
    
    await client.connect()
    success = await client.login(config.account.username, config.account.password)
    if not success:
        print("登录失败")
        return
    
    print(f"账号ID: {client.account_id}")
    print(f"昵称: {client.nickname}")
    
    # 查询账号详情
    req = pb.ReqAccountInfo()
    req.account_id = client.account_id
    res = await client.lobby.fetch_account_info(req)
    d = MessageToDict(res, preserving_proto_field_name=True)
    
    account = d.get("account", {})
    print(f"\n=== 账号信息 ===")
    print(f"等级ID: {account.get('level', {})}")
    print(f"登录时间: {account.get('login_time', 0)}")
    
    # 打印完整信息方便调试
    import json
    print(f"\n=== 完整响应 ===")
    # 只打印关键字段
    for key in ['level', 'level3', 'avatar_id', 'title', 'verified', 'loading_image']:
        if key in account:
            print(f"  {key}: {account[key]}")
    
    # 看看有没有 level 相关
    level = account.get('level', {})
    level3 = account.get('level3', {})
    print(f"\n四麻段位: {level}")
    print(f"三麻段位: {level3}")
    
    await client.close()

asyncio.run(main())
