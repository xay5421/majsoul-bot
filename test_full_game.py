"""完整友人房 AI 对战 — 简单摸切 (v3: 直接读 protobuf 属性)"""
import asyncio, logging, re
import ms.protocol_pb2 as pb
from google.protobuf.json_format import MessageToDict
from ms.base import MSRPCChannel
from ms.rpc import Lobby, FastTest
from client import MajsoulClient

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger("game")

MY_SEAT = -1
GAME_FT: FastTest = None
ACTION_COUNT = 0
HAND = []
GAME_DONE = asyncio.Event()
WIND = ['东','南','西','北']

# ─── XOR 解密 ────────────────────────────────────
_KEYS = [0x84, 0x5e, 0x4e, 0x42, 0x39, 0xa2, 0x1f, 0x60, 0x1c]
def decode_action(data: bytes) -> bytes:
    data = bytearray(data)
    for i in range(len(data)):
        u = (23 ^ len(data)) + 5 * i + _KEYS[i % len(_KEYS)] & 255
        data[i] ^= u
    return bytes(data)

# ─── Action handlers (直接读 protobuf 字段) ──────

async def handle_new_round(data):
    global HAND
    nr = pb.ActionNewRound()
    nr.ParseFromString(data)
    HAND = list(nr.tiles)
    doras = list(nr.doras) or ([nr.dora] if nr.dora else [])
    log.info(f"═══ {WIND[nr.chang]}{nr.ju+1}局 {nr.ben}本场 (剩余{nr.left_tile_count}) ═══")
    log.info(f"  手牌({len(HAND)}): {' '.join(HAND)}")
    log.info(f"  宝牌: {' '.join(doras)}  点数: {list(nr.scores)}")
    
    # 庄家14张要出一张
    if len(HAND) == 14 or (nr.operation and nr.operation.operation_list):
        await asyncio.sleep(1.5)
        tile = HAND[-1]
        req = pb.ReqSelfOperation()
        req.type = 7
        req.tile = tile
        req.moqie = False
        try:
            await GAME_FT.input_operation(req)
            HAND.remove(tile)
            log.info(f"  → 出牌: {tile}")
        except Exception as e:
            log.error(f"  出牌失败: {e}")

async def handle_deal_tile(data):
    global HAND
    dt = pb.ActionDealTile()
    dt.ParseFromString(data)
    seat = dt.seat  # 直接读 protobuf 字段，seat=0 不会丢失
    
    if seat != MY_SEAT:
        # 他家摸牌，不用管
        return
    
    tile = dt.tile
    if not tile:
        return  # 没有 tile 字段 = 不是我的回合
    
    left = dt.left_tile_count
    HAND.append(tile)
    log.info(f"  摸牌: {tile} (剩余{left}) 手牌: {' '.join(HAND)}")
    
    # 简单摸切
    await asyncio.sleep(1.5)
    req = pb.ReqSelfOperation()
    req.type = 7
    req.tile = tile
    req.moqie = True
    try:
        await GAME_FT.input_operation(req)
        HAND.remove(tile)
        log.info(f"  → 摸切: {tile}")
    except Exception as e:
        log.error(f"  出牌失败: {e}")

async def handle_discard_tile(data):
    dt = pb.ActionDiscardTile()
    dt.ParseFromString(data)
    seat = dt.seat
    tile = dt.tile
    moqie = dt.moqie
    
    mark = '(摸切)' if moqie else ''
    who = '我' if seat == MY_SEAT else f'玩家{seat}'
    log.info(f"  {who}出牌: {tile} {mark}")
    
    if seat == MY_SEAT:
        return
    
    # 有操作? 跳过
    if dt.operation and dt.operation.operation_list:
        ops = [x.type for x in dt.operation.operation_list]
        log.info(f"    可操作: {ops} → 跳过")
        await asyncio.sleep(0.5)
        req = pb.ReqSelfOperation()
        req.cancel_operation = True
        try:
            await GAME_FT.input_operation(req)
        except Exception as e:
            log.error(f"    取消操作失败: {e}")

async def handle_chi_peng_gang(data):
    cpg = pb.ActionChiPengGang()
    cpg.ParseFromString(data)
    names = {0:'吃', 1:'碰', 2:'杠'}
    who = '我' if cpg.seat == MY_SEAT else f'玩家{cpg.seat}'
    log.info(f"  {who} {names.get(cpg.type, f'副露{cpg.type}')}: {list(cpg.tiles)}")

async def handle_an_gang_add_gang(data):
    ag = pb.ActionAnGangAddGang()
    ag.ParseFromString(data)
    names = {2:'暗杠', 3:'加杠'}
    who = '我' if ag.seat == MY_SEAT else f'玩家{ag.seat}'
    log.info(f"  {who} {names.get(ag.type, f'杠{ag.type}')}: {ag.tiles}")
    
    # 如果有操作(抢杠胡)
    if ag.operation and ag.operation.operation_list:
        ops = [x.type for x in ag.operation.operation_list]
        log.info(f"    可操作: {ops} → 跳过")
        await asyncio.sleep(0.5)
        req = pb.ReqSelfOperation()
        req.cancel_operation = True
        try:
            await GAME_FT.input_operation(req)
        except: pass

async def handle_hule(data):
    h = pb.ActionHule()
    h.ParseFromString(data)
    log.info(f"  🎊 和牌! 变化: {list(h.delta_scores)} → {list(h.scores)}")
    for hi in h.hules:
        who = '我' if hi.seat == MY_SEAT else f'玩家{hi.seat}'
        zimo = '(自摸)' if hi.zimo else ''
        log.info(f"    {who}: {hi.count}番 {zimo}")

async def handle_no_tile(data):
    nt = pb.ActionNoTile()
    nt.ParseFromString(data)
    log.info(f"  📭 荒牌流局")

async def handle_liu_ju(data):
    lj = pb.ActionLiuJu()
    lj.ParseFromString(data)
    types = {1:'九种九牌', 2:'四风连打', 3:'四杠散了', 4:'四家立直'}
    log.info(f"  🌊 流局: {types.get(lj.type, lj.type)}")

# ─── Dispatcher ──────────────────────────────────

HANDLERS = {
    'ActionNewRound': handle_new_round,
    'ActionDealTile': handle_deal_tile,
    'ActionDiscardTile': handle_discard_tile,
    'ActionChiPengGang': handle_chi_peng_gang,
    'ActionAnGangAddGang': handle_an_gang_add_gang,
    'ActionHule': handle_hule,
    'ActionNoTile': handle_no_tile,
    'ActionLiuJu': handle_liu_ju,
}

async def on_action(data):
    global ACTION_COUNT
    msg = pb.ActionPrototype()
    msg.ParseFromString(data)
    ACTION_COUNT += 1
    handler = HANDLERS.get(msg.name)
    if handler:
        try:
            decrypted = decode_action(msg.data)
            await handler(decrypted)
        except Exception as e:
            log.error(f"  处理 {msg.name} 出错: {e}")
    else:
        log.debug(f"  未处理: {msg.name}")

async def on_game_end(data):
    try:
        msg = pb.NotifyGameEndResult()
        msg.ParseFromString(data)
        d = MessageToDict(msg, preserving_proto_field_name=True)
        log.info(f"🏁 对局结束!")
        for p in d.get('result',{}).get('players',[]):
            log.info(f"  玩家{p.get('seat','?')}: {p.get('total_point',0)}pt")
    except:
        log.info("🏁 对局结束!")
    GAME_DONE.set()

async def on_game_terminate(data):
    log.info("⚠️ 对局终止!")
    GAME_DONE.set()

# ─── Game server ─────────────────────────────────

async def connect_game(c: MajsoulClient, game_url, token, uuid):
    global MY_SEAT, GAME_FT
    m = re.match(r'(wss://[^/]+)', c.channel._endpoint)
    route_base = m.group(1)
    game_ws = f'{route_base}/game-gateway'
    log.info(f"连接: {game_ws}")
    game_ch = MSRPCChannel(game_ws)
    game_ch.add_hook('.lq.ActionPrototype', on_action)
    game_ch.add_hook('.lq.NotifyGameEndResult', on_game_end)
    game_ch.add_hook('.lq.NotifyGameTerminate', on_game_terminate)
    await game_ch.connect('https://game.maj-soul.com')
    GAME_FT = FastTest(game_ch)
    req = pb.ReqAuthGame()
    req.account_id = c.account_id
    req.token = token
    req.game_uuid = uuid
    req.session = c.access_token
    res = await GAME_FT.auth_game(req)
    d = MessageToDict(res, preserving_proto_field_name=True)
    if d.get('error'):
        log.error(f"auth 失败: {d['error']}"); return False
    seat_list = d.get('seat_list', [])
    MY_SEAT = seat_list.index(c.account_id) if c.account_id in seat_list else -1
    log.info(f"✅ auth! 座位={MY_SEAT}")
    enter = await GAME_FT.enter_game(pb.ReqCommon())
    log.info(f"enterGame OK")
    return True

# ─── Main ────────────────────────────────────────

async def main():
    c = MajsoulClient()
    await c.connect()
    await c.login('2449670833@qq.com', 'wxwakioi')

    # 清理
    ft = FastTest(c.channel)
    for _ in range(3):
        try: await asyncio.wait_for(ft.terminate_game(pb.ReqCommon()), 3)
        except: pass
        try: await c.lobby.leave_room(pb.ReqCommon())
        except: pass
        await asyncio.sleep(0.5)

    gi = await c.lobby.fetch_gaming_info(pb.ReqCommon())
    gd = MessageToDict(gi, preserving_proto_field_name=True)
    if gd.get('game_info',{}).get('connect_token'):
        info = gd['game_info']
        log.info(f"恢复残留: {info['game_uuid'][:20]}...")
        ok = await connect_game(c, '', info['connect_token'], info['game_uuid'])
        if ok:
            try: await asyncio.wait_for(GAME_DONE.wait(), timeout=600)
            except: log.info(f"超时 ({ACTION_COUNT} events)")
            await c.close(); return

    # 新房间
    game_started = asyncio.Event()
    async def on_room_start(data):
        msg = pb.NotifyRoomGameStart()
        msg.ParseFromString(data)
        log.info(f"🎮 game_url={msg.game_url}")
        ok = await connect_game(c, msg.game_url, msg.connect_token, msg.game_uuid)
        if ok: game_started.set()
    c.channel.add_hook('.lq.NotifyRoomGameStart', on_room_start)

    req = pb.ReqCreateRoom()
    req.player_count = 4; req.mode.mode = 2; req.mode.ai = True
    req.client_version_string = c.version
    dr = req.mode.detail_rule
    dr.time_fixed = 5; dr.time_add = 20; dr.dora_count = 3; dr.shiduan = 1
    dr.init_point = 25000; dr.fandian = 30000; dr.can_jifei = True
    dr.have_liujumanguan = True; dr.have_biao_dora = True
    dr.have_gang_biao_dora = True; dr.have_li_dora = True; dr.have_gang_li_dora = True
    res = await c.lobby.create_room(req)
    d = MessageToDict(res, preserving_proto_field_name=True)
    if d.get('error'):
        log.error(f"创建房间失败: {d['error']}"); await c.close(); return
    log.info(f"✅ 房间 {d['room']['room_id']}")
    for i in range(3):
        await c.lobby.add_room_robot(pb.ReqAddRoomRobot(position=i+1))
    await c.lobby.start_room(pb.ReqRoomStart())
    log.info("等待开始...")
    try: await asyncio.wait_for(game_started.wait(), timeout=15)
    except: log.error("超时"); await c.close(); return
    log.info("🀄 对局中!")
    try: await asyncio.wait_for(GAME_DONE.wait(), timeout=600)
    except: log.info(f"超时 ({ACTION_COUNT} events)")
    log.info(f"✅ 完成! {ACTION_COUNT} events")
    await c.close()

if __name__ == "__main__":
    asyncio.run(main())
