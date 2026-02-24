"""Debug match: check all possible blockers"""
import asyncio, hashlib, hmac, uuid, random, aiohttp, logging
from google.protobuf.json_format import MessageToDict
from ms.base import MSRPCChannel
from ms.rpc import Lobby
import ms.protocol_pb2 as pb
from config import load_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

async def main():
    cfg = load_config()
    async with aiohttp.ClientSession() as session:
        async with session.get("https://game.maj-soul.com/1/version.json") as res:
            v = await res.json()
            version = v["version"]
            vc = version.replace(".w", "")
        async with session.get(f"https://game.maj-soul.com/1/v{version}/config.json") as res:
            config = await res.json()
            gws = config["ip"][0]["gateways"]
        gw = random.choice(gws)
        async with session.get(f"{gw['url']}/api/clientgate/routes?platform=Web&version={vc}") as res:
            rd = await res.json()
            routes = rd["data"]["routes"]
            idle = [r for r in routes if r["state"] == "idle"] or routes
            route = random.choice(idle)
            ep = f"{'wss' if route.get('ssl', True) else 'ws'}://{route['domain']}/"

    ch = MSRPCChannel(ep)
    lobby = Lobby(ch)
    await ch.connect("https://game.maj-soul.com")

    req = pb.ReqLogin()
    req.account = cfg.account.username
    req.password = hmac.new(b"lailai", cfg.account.password.encode(), hashlib.sha256).hexdigest()
    req.device.is_browser = True
    req.random_key = str(uuid.uuid1())
    req.gen_access_token = True
    req.client_version_string = f"web-{vc}"
    req.currency_platforms.append(2)
    req.reconnect = True
    res = await lobby.login(req)
    print(f"Login: {res.account.nickname} id={res.account_id}")
    print(f"  level: {res.account.level.id}, score: {res.account.level.score}")
    
    # Check stale game from login
    has_stale = bool(res.game_info and res.game_info.connect_token)
    print(f"Stale game (login): {has_stale}")
    if has_stale:
        print(f"  uuid: {res.game_info.game_uuid}")

    # Check gaming info
    gi = await lobby.fetch_gaming_info(pb.ReqCommon())
    gd = MessageToDict(gi, preserving_proto_field_name=True)
    game_info = gd.get("game_info", {})
    print(f"Gaming info: {gd}")
    
    # Init sequence
    await lobby.login_success(pb.ReqCommon())
    print("loginSuccess OK")
    
    lb = pb.ReqLoginBeat()
    lb.contract = ""
    await lobby.login_beat(lb)
    print("loginBeat OK")
    
    await lobby.fetch_info(pb.ReqCommon())
    print("fetchInfo OK")
    
    # fetchCurrentMatchInfo
    mi = await lobby.fetch_current_match_info(pb.ReqCurrentMatchInfo())
    mi_d = MessageToDict(mi, preserving_proto_field_name=True)
    matches = mi_d.get("matches", [])
    print(f"Current match modes: {len(matches)}")
    for m in matches[:5]:
        print(f"  mode_id={m.get('mode_id')} playing={m.get('playing_count')}")
    
    # Check if there's a room
    room_res = await lobby.fetch_room(pb.ReqCommon())
    room_d = MessageToDict(room_res, preserving_proto_field_name=True)
    print(f"Room: {room_d}")
    
    await asyncio.sleep(2)  # Wait a bit
    
    # Try match with various match_sid formats
    for sid in ["1:2", "1:1", "1:3"]:
        req2 = pb.ReqStartUnifiedMatch()
        req2.match_sid = sid
        req2.client_version_string = f"web-{vc}"
        res2 = await lobby.start_unified_match(req2)
        r = MessageToDict(res2, preserving_proto_field_name=True)
        err = r.get("error", {}).get("code", 0)
        print(f"Match '{sid}': error={err}")
        
        if err == 0:
            await asyncio.sleep(0.2)
            c = pb.ReqCancelUnifiedMatch()
            c.match_sid = sid
            await lobby.cancel_unified_match(c)
            print(f"  Cancelled")
            break
        
        await asyncio.sleep(0.5)
    
    # Try old API too
    for mode_id in [2, 1]:
        req3 = pb.ReqJoinMatchQueue()
        req3.match_mode = mode_id
        req3.client_version_string = f"web-{vc}"
        res3 = await lobby.match_game(req3)
        r3 = MessageToDict(res3, preserving_proto_field_name=True)
        err3 = r3.get("error", {}).get("code", 0)
        print(f"matchGame mode={mode_id}: error={err3}")
        
        if err3 == 0:
            await asyncio.sleep(0.2)
            c3 = pb.ReqCancelMatchQueue()
            await lobby.cancel_match(c3)
            print(f"  Cancelled")
            break
        
        await asyncio.sleep(0.5)

    await ch.close()
    print("Done")

asyncio.run(main())
