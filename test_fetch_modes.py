"""查询服务器当前可用的匹配模式"""
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
    
    print(f"账号: {client.nickname}, 版本: {client.version}")
    
    # 1. fetchCurrentMatchInfo - 查当前各模式在线人数
    print("\n=== fetchCurrentMatchInfo ===")
    # 传入所有可能的 mode_id
    req = pb.ReqCurrentMatchInfo()
    for i in range(0, 30):
        req.mode_list.append(i)
    res = await client.lobby.fetch_current_match_info(req)
    d = MessageToDict(res, preserving_proto_field_name=True)
    print(json.dumps(d, indent=2, ensure_ascii=False))
    
    # 2. 从 web 端 JS 找配置 - 获取 liqidbdef 
    print("\n=== 尝试从配置获取匹配信息 ===")
    import aiohttp
    async with aiohttp.ClientSession() as session:
        # 获取 config.json
        url = f"https://game.maj-soul.com/1/v{client.version}/config.json"
        async with session.get(url) as resp:
            config_data = await resp.json()
            # 看看有没有匹配相关的配置
            for key in config_data:
                if 'match' in str(key).lower() or 'mode' in str(key).lower():
                    print(f"  {key}: {config_data[key]}")
        
        # 获取 liqidbdef.json 或类似的配置
        for fname in ['liqidbdef.json', 'res/config/liqidbdef.json']:
            try:
                url = f"https://game.maj-soul.com/1/v{client.version}/{fname}"
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        print(f"\n{fname}:")
                        print(json.dumps(data, indent=2, ensure_ascii=False)[:3000])
            except:
                pass
    
    # 3. 用更大范围查
    print("\n=== fetchCurrentMatchInfo (range 0-100) ===")
    req2 = pb.ReqCurrentMatchInfo()
    for i in range(0, 100):
        req2.mode_list.append(i)
    res2 = await client.lobby.fetch_current_match_info(req2)
    d2 = MessageToDict(res2, preserving_proto_field_name=True)
    matches = d2.get("matches", [])
    if matches:
        print(f"找到 {len(matches)} 个有效模式:")
        for m in matches:
            print(f"  mode_id={m.get('mode_id')}, playing={m.get('playing_count', 0)}")
    else:
        print("没有返回任何匹配模式数据")
        print(f"完整响应: {json.dumps(d2, indent=2, ensure_ascii=False)}")
    
    await client.close()

asyncio.run(main())
