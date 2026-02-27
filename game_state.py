"""游戏状态机 - 跟踪对局中的所有状态"""
import logging
from dataclasses import dataclass, field
from tiles import sort_tiles, tile_to_str, tiles_to_str, normalize_aka

logger = logging.getLogger("majsoul.game")


@dataclass
class PlayerState:
    """单个玩家的状态"""
    account_id: int = 0
    nickname: str = ""
    seat: int = 0           # 座位号 0-3
    score: int = 25000      # 点数
    discards: list = field(default_factory=list)     # 弃牌
    melds: list = field(default_factory=list)         # 副露
    riichi: bool = False     # 是否立直
    riichi_turn: int = -1    # 立直的巡目


@dataclass
class Meld:
    """副露信息"""
    type: str = ""           # chi/pon/kan/ankan/kakan
    tiles: list = field(default_factory=list)
    from_who: int = 0        # 从谁那吃/碰


class GameState:
    """
    一局游戏的完整状态。

    跟踪:
    - 手牌、摸牌
    - 四家弃牌、副露
    - 场风、自风、宝牌指示牌
    - 剩余牌数
    - 当前可执行的操作
    """

    def __init__(self, seat: int, player_count: int = 4):
        self.seat = seat                     # 自己的座位号
        self.player_count = player_count     # 玩家数
        self.hand: list[str] = []            # 手牌
        self.draw: str | None = None         # 最新摸的牌

        # 四家状态
        self.players: list[PlayerState] = [
            PlayerState(seat=i) for i in range(player_count)
        ]

        # 场况
        self.round_wind: int = 0             # 场风: 0=东, 1=南, 2=西, 3=北
        self.dealer: int = 0                 # 庄家座位
        self.round_num: int = 0              # 第几局 (0-indexed)
        self.honba: int = 0                  # 本场
        self.riichi_sticks: int = 0          # 供托 (立直棒数)
        self.tiles_left: int = 70            # 牌山剩余
        self.dora_indicators: list[str] = [] # 宝牌指示牌
        self.turn: int = 0                   # 当前巡目
        self.my_discard_count: int = 0       # 自己本局出牌次数

        # 所有可见的牌（用于计算剩余牌数）
        self._visible_tiles: list[str] = []

        # 当前可以执行的操作
        self.pending_operation: dict | None = None

    @property
    def my_wind(self) -> int:
        """自风 (相对于庄家)"""
        return (self.seat - self.dealer) % self.player_count

    @property
    def my_rank(self) -> int:
        """当前自己的顺位 (1=一位, 2=二位, ...)"""
        my_score = self.players[self.seat].score
        rank = 1
        for i, p in enumerate(self.players):
            if i != self.seat and p.score > my_score:
                rank += 1
        return rank

    @property
    def all_discards(self) -> list[str]:
        """所有玩家的弃牌"""
        result = []
        for p in self.players:
            result.extend(p.discards)
        return result

    def new_round(self, data) -> None:
        """新一局开始"""
        self.round_wind = data.get("chang", 0)
        self.round_num = data.get("ju", 0)
        self.dealer = self.round_num % self.player_count
        self.honba = data.get("ben", 0)
        self.riichi_sticks = data.get("liqibang", 0)
        self.turn = 0
        self.my_discard_count = 0
        self.last_discard_seat = -1  # 最后出牌者（用于荣和 target）
        self.tiles_left = 70 if self.player_count == 4 else 55
        self._visible_tiles = []
        self.pending_operation = None

        # 宝牌指示牌
        self.dora_indicators = data.get("doras", [])
        self._visible_tiles.extend(self.dora_indicators)

        # 各家点数
        scores = data.get("scores", [])
        for i, p in enumerate(self.players):
            p.score = scores[i] if i < len(scores) else 25000
            p.discards = []
            p.melds = []
            p.riichi = False
            p.riichi_turn = -1

        # 手牌
        tiles = data.get("tiles", [])
        # 最后一张可能是庄家的第14张（摸牌）
        if len(tiles) == 14:
            self.hand = sort_tiles(tiles[:13])
            self.draw = tiles[13]
        else:
            self.hand = sort_tiles(tiles)
            self.draw = None

        self._visible_tiles.extend(self.hand)
        if self.draw:
            self._visible_tiles.append(self.draw)

        logger.info(
            f"=== 新一局 {'东南西北'[self.round_wind]}{self.round_num + 1}局 "
            f"{self.honba}本场 ==="
        )
        logger.info(f"自风: {'东南西北'[self.my_wind]} | 座位: {self.seat}")
        logger.info(f"手牌: {tiles_to_str(sort_tiles(self.hand))}")
        if self.draw:
            logger.info(f"摸牌: {tile_to_str(self.draw)}")
        logger.info(f"宝牌指示: {tiles_to_str(self.dora_indicators)}")

    def _remove_from_hand(self, tile: str, check_draw: bool = False) -> bool:
        """从手牌（或摸牌）中安全扣除一张牌，精确匹配优先，赤牌兜底。
        
        Returns: True 如果成功扣除
        """
        if check_draw and self.draw == tile:
            self.draw = None
            return True
        if tile in self.hand:
            self.hand.remove(tile)
            return True
        # 兜底：赤牌映射 (0m↔5m)
        norm = normalize_aka(tile)
        for i, h in enumerate(self.hand):
            if normalize_aka(h) == norm:
                self.hand.pop(i)
                return True
        return False

    def on_draw(self, seat: int, tile: str) -> None:
        """摸牌事件"""
        self.tiles_left -= 1
        if seat == self.seat:
            self.draw = tile
            self._visible_tiles.append(tile)
            logger.info(
                f"[巡{self.turn}] 摸牌: {tile_to_str(tile)} | "
                f"手牌: {tiles_to_str(sort_tiles(self.hand))} | "
                f"牌山剩余: {self.tiles_left}"
            )
        else:
            logger.debug(f"[巡{self.turn}] 玩家{seat}摸牌")

    def on_discard(self, seat: int, tile: str, is_draw: bool = False,
                   is_riichi: bool = False) -> None:
        """出牌事件"""
        self.last_discard_seat = seat  # 记录最后出牌者（用于荣和 target）
        self.turn += 1
        self.players[seat].discards.append(tile)
        self._visible_tiles.append(tile)

        if is_riichi:
            self.players[seat].riichi = True
            self.players[seat].riichi_turn = self.turn

        if seat == self.seat:
            self.my_discard_count += 1
            # 自己出牌：从手牌中移除
            if not self._remove_from_hand(tile, check_draw=True):
                logger.debug(
                    f"出牌 {tile} 不在手牌中 (可能是重连恢复), "
                    f"手牌: {self.hand}"
                )
            # 如果打的不是摸的牌，把摸的牌加入手牌
            if self.draw is not None:
                self.hand.append(self.draw)
                self.hand = sort_tiles(self.hand)
                self.draw = None

            riichi_str = " [立直!]" if is_riichi else ""
            logger.info(
                f"[巡{self.turn}] 打出: {tile_to_str(tile)}{riichi_str} | "
                f"手牌: {tiles_to_str(sort_tiles(self.hand))}"
            )
        else:
            tsumogiri = " (摸切)" if is_draw else ""
            riichi_str = " [立直!]" if is_riichi else ""
            logger.info(
                f"[巡{self.turn}] 玩家{seat}打出: "
                f"{tile_to_str(tile)}{tsumogiri}{riichi_str}"
            )

    def on_chi_peng_gang(self, seat: int, type_: int, tiles: list[str],
                          froms: list[int]) -> None:
        """吃/碰/杠事件"""
        type_names = {0: "吃", 1: "碰", 2: "杠"}
        name = type_names.get(type_, f"未知({type_})")

        meld = Meld(type=name, tiles=tiles)
        self.players[seat].melds.append(meld)
        self._visible_tiles.extend(tiles)

        if seat == self.seat:
            for t in tiles:
                self._remove_from_hand(t)
            logger.info(
                f"[巡{self.turn}] {name}: {tiles_to_str(tiles)} | "
                f"手牌: {tiles_to_str(sort_tiles(self.hand))}"
            )
        else:
            logger.info(
                f"[巡{self.turn}] 玩家{seat} {name}: {tiles_to_str(tiles)}"
            )

    def on_ankan(self, seat: int, tiles: list[str]) -> None:
        """暗杠事件"""
        meld = Meld(type="暗杠", tiles=tiles)
        self.players[seat].melds.append(meld)
        self._visible_tiles.extend(tiles)

        if seat == self.seat:
            for t in tiles:
                self._remove_from_hand(t)
            logger.info(
                f"[巡{self.turn}] 暗杠: {tiles_to_str(tiles)} | "
                f"手牌: {tiles_to_str(sort_tiles(self.hand))}"
            )
        else:
            logger.info(f"[巡{self.turn}] 玩家{seat} 暗杠")

    def on_kakan(self, seat: int, tile: str) -> None:
        """加杠事件"""
        if seat == self.seat:
            self._remove_from_hand(tile, check_draw=True)
        self._visible_tiles.append(tile)
        logger.info(
            f"[巡{self.turn}] 玩家{seat} 加杠: {tile_to_str(tile)}"
        )

    def on_new_dora(self, tile: str) -> None:
        """新宝牌指示牌"""
        self.dora_indicators.append(tile)
        self._visible_tiles.append(tile)
        logger.info(f"新宝牌指示: {tile_to_str(tile)}")

    def get_full_hand(self) -> list[str]:
        """获取完整手牌（含摸牌）"""
        tiles = list(self.hand)
        if self.draw:
            tiles.append(self.draw)
        return sort_tiles(tiles)

    def get_visible_tiles(self) -> list[str]:
        """获取所有可见的牌"""
        return list(self._visible_tiles)
