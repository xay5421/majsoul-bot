"""尝试用登录后获取的信息找到 match_sid 格式

通过 fetchServerSettings / fetchServerTime 等接口获取配置
"""
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
    
    # 列出 Lobby 所有可用方法
    print("=== Lobby 所有方法 ===")
    methods = [m for m in dir(client.lobby) if not m.startswith('_') and callable(getattr(client.lobby, m, None))]
    for m in sorted(methods):
        print(f"  {m}")
    
    # 尝试获取各种配置
    print("\n=== fetchServerSettings ===")
    if hasattr(client.lobby, 'fetch_server_settings'):
        try:
            req = pb.ReqCommon()
            res = await client.lobby.fetch_server_settings(req)
            d = MessageToDict(res, preserving_proto_field_name=True)
            import json
            print(json.dumps(d, indent=2, ensure_ascii=False)[:3000])
        except Exception as e:
            print(f"异常: {e}")
    
    # 查看 matchGameRule
    print("\n=== fetchMatchGameRule ===")
    for method_name in ['fetch_match_game_rule', 'fetch_match_info']:
        if hasattr(client.lobby, method_name):
            try:
                req = pb.ReqCommon()
                res = await getattr(client.lobby, method_name)(req)
                d = MessageToDict(res, preserving_proto_field_name=True)
                import json
                print(f"{method_name}: {json.dumps(d, indent=2, ensure_ascii=False)[:2000]}")
            except Exception as e:
                print(f"{method_name} 异常: {e}")
    
    # fetchGameLiveList - 看实时对局
    print("\n=== fetchGameLiveList ===")
    if hasattr(client.lobby, 'fetch_game_live_list'):
        try:
            req = pb.ReqGameLiveList()
            req.filter_id = 0
            res = await client.lobby.fetch_game_live_list(req)
            d = MessageToDict(res, preserving_proto_field_name=True)
            import json
            # 只打印第一个
            lives = d.get('live_list', [])
            if lives:
                print(f"总数: {len(lives)}")
                print(f"第一个: {json.dumps(lives[0], indent=2, ensure_ascii=False)[:1000]}")
        except Exception as e:
            print(f"异常: {e}")

    await client.close()

asyncio.run(main())
