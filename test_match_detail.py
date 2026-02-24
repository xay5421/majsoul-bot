"""详细测试匹配，查看错误细节"""
import asyncio
import logging
import json
from google.protobuf.json_format import MessageToDict
from client import MajsoulClient
from config import load_config
import ms.protocol_pb2 as pb

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s: %(message)s", datefmt="%H:%M:%S")

async def main():
    config = load_config()
    client = MajsoulClient()
    
    await client.connect()
    success = await client.login(config.account.username, config.account.password)
    if not success:
        print("登录失败")
        return
    
    print(f"账号: {client.nickname}, 版本: {client.version}")
    print(f"四麻段位 ID: 10103 (初心三星)")
    print()
    
    # 看看 mode_id 和段位的对应关系
    # 猜测: mode_id 0-6 四麻, 7-13 对应什么? 14-19 三麻?
    # playing_count 有值的: 2,3,5,6,8,9,11,12,16,17,18,19
    # 
    # 标准雀魂段位赛:
    # 四麻东: 铜/银/金/玉/王座
    # 四麻南: 铜/银/金/玉/王座
    # 三麻东: 铜/银/金/玉/王座
    # 三麻南: 铜/银/金/玉/王座
    #
    # 可能的映射:
    # 四麻东: 2(铜) 3(银) (人最多的是入门段位)
    # 四麻南: 5(铜) 6(银)
    # 三麻东: 8(铜) 9(银)...
    
    # 错误码 1306 详细看看
    print("=== 详细测试 mode 2 (猜测铜之间四东) ===")
    req = pb.ReqJoinMatchQueue()
    req.match_mode = 2
    req.client_version_string = client.version
    res = await client.lobby.match_game(req)
    d = MessageToDict(res, preserving_proto_field_name=True)
    print(f"完整响应: {json.dumps(d, indent=2, ensure_ascii=False)}")
    print(f"原始 error: code={res.error.code if res.error else 'none'}")
    
    # 看看 res 的所有字段
    print(f"所有字段:")
    for field in res.DESCRIPTOR.fields:
        val = getattr(res, field.name, None)
        print(f"  {field.name} = {val}")
    
    # 尝试用 client_version_string 格式 "web-x.x.x" 
    print("\n=== 尝试不同的 client_version_string 格式 ===")
    version_clean = client.version.replace(".w", "")
    for ver_fmt in [client.version, f"web-{version_clean}", version_clean, ""]:
        req2 = pb.ReqJoinMatchQueue()
        req2.match_mode = 2
        if ver_fmt:
            req2.client_version_string = ver_fmt
        res2 = await client.lobby.match_game(req2)
        d2 = MessageToDict(res2, preserving_proto_field_name=True)
        code = d2.get("error", {}).get("code", 0)
        print(f"  version='{ver_fmt}': code={code}")
        await asyncio.sleep(0.2)
    
    # 也试试 unified match
    print("\n=== unified match 尝试 ===")
    # 可能 unified match 的 match_sid 格式是完全不同的
    # 从 web 客户端抓包看
    for sid in ["2", "mode_2", "2:0", "0:2", "rank:2", "normal:2"]:
        req3 = pb.ReqStartUnifiedMatch()
        req3.match_sid = sid
        req3.client_version_string = f"web-{version_clean}"
        res3 = await client.lobby.start_unified_match(req3)
        d3 = MessageToDict(res3, preserving_proto_field_name=True)
        code = d3.get("error", {}).get("code", 0)
        if code not in (1303, 1307):
            print(f"  sid='{sid}': code={code} → {d3}")
        await asyncio.sleep(0.1)
    
    await client.close()

asyncio.run(main())
