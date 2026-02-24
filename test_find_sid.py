"""精准查找有效的段位赛 match_sid"""
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
    
    print(f"账号: {client.nickname}, 段位: 10103 (初心三星)")
    print(f"版本: {client.version}")
    print()
    
    # === 方法1: 旧接口 match_game (ReqJoinMatchQueue) ===
    print("=== 方法1: match_game (旧接口) ===")
    for mode_id in range(0, 20):
        req = pb.ReqJoinMatchQueue()
        req.match_mode = mode_id
        req.client_version_string = client.version
        try:
            res = await client.lobby.match_game(req)
            d = MessageToDict(res, preserving_proto_field_name=True)
            error = d.get("error", {})
            code = error.get("code", 0)
            if code == 0:
                print(f"  mode={mode_id}: ✅ 匹配成功! 立即取消...")
                cancel = pb.ReqCancelMatchQueue()
                cancel.match_mode = mode_id
                await client.lobby.cancel_match(cancel)
                await asyncio.sleep(0.3)
            elif code != 1303:  # 1303=无效
                print(f"  mode={mode_id}: code={code} ({d})")
        except Exception as e:
            print(f"  mode={mode_id}: 异常 {e}")
        await asyncio.sleep(0.1)
    
    # === 方法2: start_unified_match (新接口) ===
    print("\n=== 方法2: start_unified_match (新接口) ===")
    # 格式猜测: "type:id" 
    # type: 0-10, id: 0-60
    found = []
    for type_id in range(0, 10):
        for match_id in range(0, 50):
            sid = f"{type_id}:{match_id}"
            req = pb.ReqStartUnifiedMatch()
            req.match_sid = sid
            req.client_version_string = client.version
            try:
                res = await client.lobby.start_unified_match(req)
                d = MessageToDict(res, preserving_proto_field_name=True)
                error = d.get("error", {})
                code = error.get("code", 0)
                if code == 0:
                    print(f"  ✅ '{sid}': 匹配成功! 取消中...")
                    found.append(("ok", sid))
                    cancel = pb.ReqCancelUnifiedMatch()
                    cancel.match_sid = sid
                    await client.lobby.cancel_unified_match(cancel)
                    await asyncio.sleep(0.3)
                elif code == 1306:
                    print(f"  🔒 '{sid}': 1306 (段位不足)")
                    found.append(("locked", sid))
                elif code == 1302:
                    print(f"  ⚠️ '{sid}': 1302 (已在匹配中?)")
                elif code == 1307 or code == 1303:
                    pass  # 无效，跳过
                else:
                    print(f"  ❓ '{sid}': code={code}")
            except Exception as e:
                print(f"  💥 '{sid}': {e}")
            await asyncio.sleep(0.05)
    
    print(f"\n=== 找到的有效 SID ===")
    for status, s in found:
        print(f"  [{status}] {s}")
    
    # === 方法3: 也试试其他格式 ===
    print("\n=== 方法3: 其他格式 ===")
    other_sids = [
        # 可能是赛季相关的
        "rank_4e", "rank_4s", "rank_3e", "rank_3s",
        "copper_4e", "silver_4e", "gold_4e",
        "4e", "4s", "3e", "3s",
        "normal", "rank",
        # 数字ID
        "1", "2", "3", "4", "5", "6", "7", "8", "9", "10",
        "11", "12", "13", "14", "15", "16", "17", "18", "19", "20",
    ]
    for sid in other_sids:
        req = pb.ReqStartUnifiedMatch()
        req.match_sid = sid
        req.client_version_string = client.version
        try:
            res = await client.lobby.start_unified_match(req)
            d = MessageToDict(res, preserving_proto_field_name=True)
            error = d.get("error", {})
            code = error.get("code", 0)
            if code == 0:
                print(f"  ✅ '{sid}': 匹配成功!")
                cancel = pb.ReqCancelUnifiedMatch()
                cancel.match_sid = sid
                await client.lobby.cancel_unified_match(cancel)
            elif code == 1306:
                print(f"  🔒 '{sid}': 段位不足")
            elif code not in (1303, 1307):
                print(f"  ❓ '{sid}': code={code}")
        except Exception as e:
            pass
        await asyncio.sleep(0.05)
    
    await client.close()

asyncio.run(main())
