"""测试不同 match_mode 值"""
import asyncio
import logging
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
    
    print(f"账号: {client.nickname} (段位 10103=初心3星)")
    print(f"版本: {client.version}")
    print()
    
    # 试不同的 match_mode
    # 雀魂段位赛模式可能是:
    # 金之间以下可能有不同编号
    # 也可能需要查询 matchmode 列表
    
    for mode_id in range(0, 30):
        req = pb.ReqJoinMatchQueue()
        req.match_mode = mode_id
        req.client_version_string = client.version
        res = await client.lobby.match_game(req)
        d = MessageToDict(res, preserving_proto_field_name=True)
        
        error = d.get("error", {})
        code = error.get("code", 0)
        
        if code == 0:
            print(f"  mode_id={mode_id}: ✅ 匹配成功! 立即取消...")
            # 取消匹配
            cancel_req = pb.ReqCancelMatchQueue()
            cancel_req.match_mode = mode_id
            await client.lobby.cancel_match(cancel_req)
            await asyncio.sleep(0.5)
        elif code == 1306:
            print(f"  mode_id={mode_id}: ❌ 1306 (段位不足)")
        elif code == 1303:
            print(f"  mode_id={mode_id}: ❌ 1303 (无效模式?)")
        else:
            print(f"  mode_id={mode_id}: ❌ {code}")
        
        await asyncio.sleep(0.3)
    
    await client.close()

asyncio.run(main())
