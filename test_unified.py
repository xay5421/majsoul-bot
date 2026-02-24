"""测试 startUnifiedMatch 接口"""
import asyncio
import logging
from google.protobuf.json_format import MessageToDict
from client import MajsoulClient
from config import load_config
import ms.protocol_pb2 as pb

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s", datefmt="%H:%M:%S")

async def main():
    config = load_config()
    client = MajsoulClient()
    
    await client.connect()
    success = await client.login(config.account.username, config.account.password)
    if not success:
        print("登录失败")
        return
    
    # 先看看 Lobby 有没有 startUnifiedMatch 方法
    print("Lobby 方法列表:")
    methods = [m for m in dir(client.lobby) if not m.startswith('_')]
    for m in sorted(methods):
        if 'match' in m.lower() or 'unified' in m.lower():
            print(f"  {m}")
    
    # 尝试不同的 match_sid 格式
    # 猜测格式可能类似: "rank_4e_copper" 或者某种ID
    test_sids = [
        # 可能的格式
        "match_copper_4e",
        "4e_copper",
        "copper_4e", 
        "rank_copper_4e",
        "1",
        "2",
        # 可能更像 "season_xxx" 格式
    ]
    
    if hasattr(client.lobby, 'start_unified_match'):
        for sid in test_sids:
            print(f"\n尝试 match_sid='{sid}'...")
            req = pb.ReqStartUnifiedMatch()
            req.match_sid = sid
            req.client_version_string = client.version
            try:
                res = await client.lobby.start_unified_match(req)
                d = MessageToDict(res, preserving_proto_field_name=True)
                error = d.get("error", {})
                code = error.get("code", 0)
                if code == 0:
                    print(f"  ✅ 成功! 立即取消...")
                    cancel = pb.ReqCancelUnifiedMatch()
                    cancel.match_sid = sid
                    await client.lobby.cancel_unified_match(cancel)
                else:
                    print(f"  ❌ error code={code}")
                    print(f"  响应: {d}")
            except Exception as e:
                print(f"  异常: {e}")
            await asyncio.sleep(0.5)
    else:
        print("没有 start_unified_match 方法")
        print("尝试查找匹配相关的所有方法:")
        for m in sorted(methods):
            print(f"  {m}")
    
    # 也尝试获取 match 配置信息
    if hasattr(client.lobby, 'fetch_current_match_info'):
        print("\n\n=== fetchCurrentMatchInfo ===")
        req = pb.ReqCurrentMatchInfo()
        try:
            res = await client.lobby.fetch_current_match_info(req)
            d = MessageToDict(res, preserving_proto_field_name=True)
            print(f"响应: {d}")
        except Exception as e:
            print(f"异常: {e}")
    
    await client.close()

asyncio.run(main())
