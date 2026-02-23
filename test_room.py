"""测试友人房 + AI 对战的完整流程"""
import asyncio
import logging
import ms.protocol_pb2 as pb
from google.protobuf.json_format import MessageToDict
from ms.base import MSRPCChannel
from ms.rpc import Lobby, FastTest
from client import MajsoulClient

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(name)s %(levelname)s: %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("test")


async def main():
    c = MajsoulClient()
    await c.connect()
    await c.login('2449670833@qq.com', 'wxwakioi')

    # 检查是否有残留对局
    gi = await c.lobby.fetch_gaming_info(pb.ReqCommon())
    gd = MessageToDict(gi, preserving_proto_field_name=True)
    if gd.get('game_info', {}).get('connect_token'):
        logger.warning(f"发现残留对局: {gd['game_info']['game_uuid']}")
        logger.warning("等待清理... 如果一直不清，可能需要在网页版手动结束")
        # 等一会看看会不会自动清理
        await asyncio.sleep(5)
        gi2 = await c.lobby.fetch_gaming_info(pb.ReqCommon())
        gd2 = MessageToDict(gi2, preserving_proto_field_name=True)
        if gd2.get('game_info', {}).get('connect_token'):
            logger.error("残留对局仍在，无法创建新房间")
            await c.close()
            return

    # 检查是否有残留房间
    ri = await c.lobby.fetch_room(pb.ReqCommon())
    rd = MessageToDict(ri, preserving_proto_field_name=True)
    if rd.get('room'):
        logger.info(f"发现残留房间 {rd['room'].get('room_id')}，离开...")
        await c.lobby.leave_room(pb.ReqCommon())
        await asyncio.sleep(1)

    # ─── 注册事件 hook ─────────────────────────────

    game_started = asyncio.Event()
    game_ended = asyncio.Event()
    seat = -1
    action_count = 0

    async def on_room_game_start(data):
        nonlocal seat
        msg = pb.NotifyRoomGameStart()
        msg.ParseFromString(data)
        full = MessageToDict(msg, preserving_proto_field_name=True)
        logger.info(f"🎮 NotifyRoomGameStart FULL: {full}")

        game_url = msg.game_url
        # game_url 可能是内网 IP，需要转换为 route 地址
        # 雀魂用 route-X.maj-soul.com 做反向代理
        # 友人房 AI 对战可能需要连接到另一个 route
        logger.info(f"  game_url: {game_url}")
        logger.info(f"  connect_token: {msg.connect_token}")
        logger.info(f"  game_uuid: {msg.game_uuid}")

        # 构建可访问的 game URL
        # 如果是内网 IP，尝试用当前 lobby 的路由域名
        if game_url.startswith("172.") or game_url.startswith("10.") or game_url.startswith("192.168."):
            # 内网地址，尝试在 lobby channel 上直接 auth
            logger.info("  内网地址，尝试在 lobby channel 上 auth...")
            ft = FastTest(c.channel)
        else:
            # 外网地址，新建连接
            if not game_url.startswith("wss://"):
                game_url = f"wss://{game_url}"
            logger.info(f"  连接 game server: {game_url}")
            from ms.base import MSRPCChannel
            game_ch = MSRPCChannel(game_url)
            game_ch.add_hook('.lq.ActionPrototype', on_action_prototype)
            game_ch.add_hook('.lq.NotifyGameEndResult', on_game_end)
            game_ch.add_hook('.lq.NotifyGameTerminate', on_game_terminate)
            await game_ch.connect('https://game.maj-soul.com')
            ft = FastTest(game_ch)

        auth_req = pb.ReqAuthGame()
        auth_req.account_id = c.account_id
        auth_req.token = msg.connect_token
        auth_req.game_uuid = msg.game_uuid

        auth_res = await ft.auth_game(auth_req)
        auth_d = MessageToDict(auth_res, preserving_proto_field_name=True)
        logger.info(f"auth: {auth_d}")

        if auth_d.get('error'):
            logger.error(f"认证失败: {auth_d['error']}")
            return

        seat_list = auth_d.get('seat_list', [])
        if c.account_id in seat_list:
            seat = seat_list.index(c.account_id)
        logger.info(f"座位: {seat}")

        # enterGame
        enter_res = await ft.enter_game(pb.ReqCommon())
        enter_d = MessageToDict(enter_res, preserving_proto_field_name=True)
        logger.info(f"enter: is_end={enter_d.get('is_end')}, keys={list(enter_d.keys())}")

        # syncGame
        try:
            sync_res = await ft.sync_game(pb.ReqCommon())
            sync_d = MessageToDict(sync_res, preserving_proto_field_name=True)
            logger.info(f"sync: is_end={sync_d.get('is_end')}")
        except Exception as e:
            logger.debug(f"syncGame: {e}")

        game_started.set()

    async def on_action_prototype(data):
        nonlocal action_count
        msg = pb.ActionPrototype()
        msg.ParseFromString(data)
        action_count += 1
        logger.info(f"  🀄 Action #{action_count}: {msg.name} ({len(msg.data)} bytes, step={msg.step})")

        # 简单解析看看内容
        action_name = msg.name
        action_data = msg.data

        if action_name == "ActionNewRound":
            nr = pb.RecordNewRound()
            nr.ParseFromString(action_data)
            d = MessageToDict(nr, preserving_proto_field_name=True)
            tiles = d.get('tiles', [])
            doras = d.get('doras', [])
            logger.info(f"  新一局! 手牌: {tiles}, 宝牌: {doras}")

        elif action_name == "ActionDealTile":
            dt = pb.RecordDealTile()
            dt.ParseFromString(action_data)
            d = MessageToDict(dt, preserving_proto_field_name=True)
            if d.get('seat') == seat:
                logger.info(f"  摸牌: {d.get('tile')} 剩余: {d.get('left_tile_count')}")
                # 需要出牌! 简单出最后一张
                op = d.get('operation')
                if not op:
                    # 普通摸牌，打刚摸的牌 (摸切)
                    ft = FastTest(c.channel)
                    req = pb.ReqSelfOperation()
                    req.tile = d.get('tile', '')
                    req.moqie = True
                    await asyncio.sleep(1)  # 模拟思考
                    await ft.input_operation(req)
                    logger.info(f"  → 出牌: {d.get('tile')} (摸切)")

        elif action_name == "ActionDiscardTile":
            dt = pb.RecordDiscardTile()
            dt.ParseFromString(action_data)
            d = MessageToDict(dt, preserving_proto_field_name=True)
            logger.info(f"  玩家{d.get('seat')}出牌: {d.get('tile')}")

    async def on_game_end(data):
        logger.info("🏁 对局结束!")
        game_ended.set()

    async def on_game_terminate(data):
        logger.info("⚠️ 对局终止!")
        game_ended.set()

    # 在 lobby channel 注册 hooks
    c.channel.add_hook(".lq.NotifyRoomGameStart", on_room_game_start)
    c.channel.add_hook(".lq.ActionPrototype", on_action_prototype)
    c.channel.add_hook(".lq.NotifyGameEndResult", on_game_end)
    c.channel.add_hook(".lq.NotifyGameTerminate", on_game_terminate)

    # ─── 创建房间 ──────────────────────────────────

    req = pb.ReqCreateRoom()
    req.player_count = 4
    req.mode.mode = 2  # 四人东
    req.mode.ai = True
    req.client_version_string = c.version

    dr = req.mode.detail_rule
    dr.time_fixed = 5
    dr.time_add = 20
    dr.dora_count = 3
    dr.shiduan = 1
    dr.init_point = 25000
    dr.fandian = 30000
    dr.can_jifei = True
    dr.have_liujumanguan = True
    dr.have_biao_dora = True
    dr.have_gang_biao_dora = True
    dr.have_li_dora = True
    dr.have_gang_li_dora = True
    dr.have_sifenglianda = True
    dr.have_sigangsanle = True
    dr.have_sijializhi = True
    dr.have_jiuzhongjiupai = True
    dr.have_toutiao = True
    dr.have_helelianzhuang = True
    dr.have_helezhongju = True
    dr.have_tingpailianzhuang = True
    dr.have_tingpaizhongju = True

    res = await c.lobby.create_room(req)
    d = MessageToDict(res, preserving_proto_field_name=True)

    if d.get('error'):
        logger.error(f"创建房间失败: {d['error']}")
        await c.close()
        return

    room_id = d['room']['room_id']
    logger.info(f"✅ 房间创建成功: {room_id}")

    # 添加 AI
    for i in range(3):
        add_req = pb.ReqAddRoomRobot()
        add_req.position = i + 1
        await c.lobby.add_room_robot(add_req)
        logger.info(f"  AI {i+1} 加入")

    # 开始对局
    start_res = await c.lobby.start_room(pb.ReqRoomStart())
    start_d = MessageToDict(start_res, preserving_proto_field_name=True)
    logger.info(f"startRoom: {start_d}")

    if start_d.get('error'):
        logger.error(f"开始失败: {start_d['error']}")
        await c.close()
        return

    # 等待对局开始通知
    logger.info("等待 NotifyRoomGameStart...")
    try:
        await asyncio.wait_for(game_started.wait(), timeout=30)
    except asyncio.TimeoutError:
        logger.error("等待超时! 没收到 NotifyRoomGameStart")
        # 打印这段时间收到的消息
        await c.close()
        return

    logger.info(f"对局开始! 座位={seat}")

    # 等待一些事件或结束
    try:
        await asyncio.wait_for(game_ended.wait(), timeout=120)
    except asyncio.TimeoutError:
        logger.info(f"等待超时，共收到 {action_count} 个事件")

    logger.info(f"总计收到 {action_count} 个 ActionPrototype 事件")
    await c.close()


if __name__ == "__main__":
    asyncio.run(main())
