"""查找 match_sid 和匹配配置"""
import asyncio
import logging
import json
from google.protobuf.json_format import MessageToDict
from client import MajsoulClient
from config import load_config
import ms.protocol_pb2 as pb

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s: %(message)s", datefmt="%H:%M:%S")

async def main():
    config = load_config()
    client = MajsoulClient()
    
    await client.connect()
    success = await client.login(config.account.username, config.account.password)
    if not success:
        print("登录失败")
        return
    
    # 1. fetchQueueInfo
    print("=== fetchQueueInfo ===")
    try:
        req = pb.ReqCommon()
        res = await client.lobby.fetch_queue_info(req)
        d = MessageToDict(res, preserving_proto_field_name=True)
        print(json.dumps(d, indent=2, ensure_ascii=False))
    except Exception as e:
        print(f"异常: {e}")
    
    # 2. fetchGamingInfo - 看看有没有正在进行的对局
    print("\n=== fetchGamingInfo ===")
    try:
        req = pb.ReqCommon()
        res = await client.lobby.fetch_gaming_info(req)
        d = MessageToDict(res, preserving_proto_field_name=True)
        print(json.dumps(d, indent=2, ensure_ascii=False))
    except Exception as e:
        print(f"异常: {e}")
    
    # 3. fetchMisc - 可能有配置信息
    print("\n=== fetchMisc ===")
    try:
        req = pb.ReqCommon()
        res = await client.lobby.fetch_misc(req)
        d = MessageToDict(res, preserving_proto_field_name=True)
        print(json.dumps(d, indent=2, ensure_ascii=False)[:3000])
    except Exception as e:
        print(f"异常: {e}")

    # 4. fetchInfo 
    print("\n=== fetchInfo ===")
    try:
        req = pb.ReqCommon()
        res = await client.lobby.fetch_info(req)
        d = MessageToDict(res, preserving_proto_field_name=True)
        txt = json.dumps(d, indent=2, ensure_ascii=False)
        # 找 match 相关
        for line in txt.split('\n'):
            if 'match' in line.lower() or 'sid' in line.lower() or 'mode' in line.lower() or 'queue' in line.lower():
                print(line)
    except Exception as e:
        print(f"异常: {e}")
    
    # 5. fetchConnectionInfo
    print("\n=== fetchConnectionInfo ===")
    try:
        req = pb.ReqCommon()
        res = await client.lobby.fetch_connection_info(req)
        d = MessageToDict(res, preserving_proto_field_name=True)
        print(json.dumps(d, indent=2, ensure_ascii=False)[:2000])
    except Exception as e:
        print(f"异常: {e}")

    # 6. 尝试更多 match_sid 格式
    print("\n=== 暴力测试 match_sid ===")
    # 从代码看格式是 type+':'+something
    # type 可能是数字 (如场次类型)
    # something 可能是赛季ID
    candidates = [
        "1:1", "2:1", "3:1", "4:1",
        "1:0", "2:0", "3:0", "4:0",
        "rank:1", "rank:2", "rank:copper",
        "normal:1", "normal:2",
        "0:1", "0:2", "0:3", "0:4",
        "match:1", "match:2",
    ]
    
    for sid in candidates:
        req = pb.ReqStartUnifiedMatch()
        req.match_sid = sid
        req.client_version_string = client.version
        try:
            res = await client.lobby.start_unified_match(req)
            d = MessageToDict(res, preserving_proto_field_name=True)
            error = d.get("error", {})
            code = error.get("code", 0)
            if code == 0:
                print(f"  '{sid}': ✅ 成功! 取消...")
                cancel = pb.ReqCancelUnifiedMatch()
                cancel.match_sid = sid
                await client.lobby.cancel_unified_match(cancel)
            elif code != 1303:
                print(f"  '{sid}': code={code} (不是 1303!)")
                print(f"    {d}")
            # 1303 = 无效SID，跳过不打印
        except Exception as e:
            print(f"  '{sid}': 异常 {e}")
        await asyncio.sleep(0.2)

    await client.close()

asyncio.run(main())
