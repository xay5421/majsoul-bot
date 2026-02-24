"""暴力搜索有效的 match_sid"""
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
    
    print("格式: type:id")
    print("已知: 1:1 返回 1307 (有效格式但场次不存在)")
    print()
    
    # 测试 type 范围 0-10, id 范围 0-50
    found = []
    for type_id in range(0, 15):
        for match_id in range(0, 60):
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
                    found.append(sid)
                    cancel = pb.ReqCancelUnifiedMatch()
                    cancel.match_sid = sid
                    await client.lobby.cancel_unified_match(cancel)
                    await asyncio.sleep(0.5)
                elif code == 1306:
                    print(f"  🔒 '{sid}': 1306 (段位不足但SID有效!)")
                    found.append(f"{sid} (locked)")
                elif code == 1307:
                    pass  # 场次不存在, 跳过
                elif code == 1303:
                    pass  # 无效SID, 跳过
                elif code == 1302:
                    print(f"  ⚠️ '{sid}': 1302 (已在匹配中?)")
                else:
                    print(f"  ❓ '{sid}': code={code}")
            except Exception as e:
                print(f"  💥 '{sid}': {e}")
            await asyncio.sleep(0.05)
        # 打印进度
        if type_id % 5 == 4:
            print(f"  ... 已测试 type 0-{type_id}")
    
    print(f"\n=== 找到的有效 SID ===")
    for s in found:
        print(f"  {s}")
    
    await client.close()

asyncio.run(main())
