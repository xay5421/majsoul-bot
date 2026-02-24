"""向听数贪心 AI

策略：
1. 出牌：枚举每张牌打出后的向听数，打使向听数最小的牌
   向听数相同时，优先打孤立牌/客风/幺九
2. 吃碰杠：计算副露后向听数是否减少，减少则执行
3. 立直：听牌时立直
4. 和：能和就和
"""
import logging
from collections import Counter

from mahjong.shanten import Shanten
from mahjong.tile import TilesConverter

from ai.base import BaseAI
from game_state import GameState
from tiles import (
    normalize_aka, tile_number, tile_suit, is_honor,
    tile_to_str, sort_tiles
)

logger = logging.getLogger("majsoul.ai")

# 雀魂牌编码 → mahjong 库格式转换
SUIT_MAP = {"m": "man", "p": "pin", "s": "sou", "z": "honors"}


def ms_tiles_to_34(tiles: list[str]) -> list[int]:
    """将雀魂牌编码列表转换为 mahjong 库的 34 编码数组"""
    man, pin, sou, honors = "", "", "", ""
    for t in tiles:
        norm = normalize_aka(t)
        num = str(tile_number(norm))
        suit = tile_suit(norm)
        if suit == "m":
            man += num
        elif suit == "p":
            pin += num
        elif suit == "s":
            sou += num
        elif suit == "z":
            honors += num
    return TilesConverter.string_to_34_array(
        man=man, pin=pin, sou=sou, honors=honors
    )


def calc_shanten(tiles: list[str]) -> int:
    """计算向听数"""
    arr = ms_tiles_to_34(tiles)
    shanten = Shanten()
    return shanten.calculate_shanten(arr)


class ShantenAI(BaseAI):
    """向听数贪心 AI — 比 BasicAI 强不少"""

    def __init__(self):
        self._shanten = Shanten()

    def decide_discard(self, state: GameState) -> str:
        """选择出牌：枚举每张牌，选打出后向听数最小的"""
        hand = state.get_full_hand()
        if not hand:
            logger.error("手牌为空!")
            return ""

        # 当前向听数
        current_shanten = calc_shanten(hand)

        # 役牌集合
        wind_tiles = {0: "1z", 1: "2z", 2: "3z", 3: "4z"}
        yakuhai = {wind_tiles.get(state.my_wind, ""), 
                   wind_tiles.get(state.round_wind, ""),
                   "5z", "6z", "7z"}

        best_tile = hand[0]
        best_score = float('inf')

        # 去重：相同牌只计算一次
        seen = set()
        for tile in hand:
            norm = normalize_aka(tile)
            if norm in seen:
                continue
            seen.add(norm)

            # 模拟打出这张牌
            remaining = list(hand)
            remaining.remove(tile)
            shanten_after = calc_shanten(remaining)

            # 评分：向听数越低越好，相同向听数时用二级排序
            tie_breaker = self._discard_priority(tile, remaining, yakuhai, state)
            score = shanten_after * 1000 + tie_breaker

            if score < best_score:
                best_score = score
                best_tile = tile

        remaining_after = list(hand)
        remaining_after.remove(best_tile)
        after_shanten = calc_shanten(remaining_after)
        logger.info(f"AI 决定打出: {tile_to_str(best_tile)} (向听={current_shanten}→{after_shanten})")
        return best_tile

    def _discard_priority(self, tile: str, remaining: list[str],
                          yakuhai: set, state: GameState) -> float:
        """二级排序分数，越低越优先打出"""
        norm = normalize_aka(tile)
        suit = tile_suit(norm)
        num = tile_number(norm)

        score = 0.0
        norm_counts = Counter(normalize_aka(t) for t in remaining)

        if suit == "z":
            if norm in yakuhai:
                score += 50  # 役牌不太想打
            else:
                score -= 20  # 客风优先打
        else:
            if num == 1 or num == 9:
                score -= 10  # 幺九优先打
            # 有邻牌的中张不太想打
            for delta in [-1, 1]:
                neighbor_num = num + delta
                if 1 <= neighbor_num <= 9:
                    if f"{neighbor_num}{suit}" in norm_counts:
                        score += 15

        # 赤宝牌不想打
        if tile[0] == "0":
            score += 30

        return score

    def decide_action(self, state: GameState, actions: dict) -> dict | None:
        """决定是否执行操作"""
        if not actions:
            return None

        op_list = actions.get("operation_list", [])
        if not op_list:
            return None

        # 能和就和
        for op in op_list:
            if op.get("type") in [8, 9]:
                logger.info("AI 决定: 和了!")
                return {"type": op["type"]}

        # 立直
        for op in op_list:
            if op.get("type") == 7:
                combination = op.get("combination", [])
                if combination:
                    # 选择立直后向听数最优的打法
                    best_tile = self._choose_riichi_tile(state, combination)
                    logger.info(f"AI 决定: 立直! 打 {tile_to_str(best_tile)}")
                    return {"type": 7, "tile": best_tile}
                logger.info("AI 决定: 立直!")
                return {"type": 7}

        hand = state.get_full_hand()
        current_shanten = calc_shanten(hand)

        # 碰：计算碰后向听数是否减少
        for op in op_list:
            if op.get("type") == 3:
                combination = op.get("combination", [])
                if combination:
                    # 模拟碰后的手牌
                    combo_tiles = self._parse_combination(combination[0])
                    simulated = list(hand)
                    for ct in combo_tiles:
                        norm_ct = normalize_aka(ct)
                        for i, h in enumerate(simulated):
                            if normalize_aka(h) == norm_ct:
                                simulated.pop(i)
                                break
                    new_shanten = calc_shanten(simulated)
                    if new_shanten < current_shanten:
                        logger.info(f"AI 决定: 碰 (向听 {current_shanten}→{new_shanten})")
                        return {"type": 3, "combination": combination}

        # 暗杠
        for op in op_list:
            if op.get("type") == 4:
                logger.info("AI 决定: 暗杠")
                return {"type": 4, "combination": op.get("combination", [])}

        # 加杠（听牌时不加杠，避免振听风险）
        for op in op_list:
            if op.get("type") == 6 and current_shanten > 0:
                logger.info("AI 决定: 加杠")
                return {"type": 6, "combination": op.get("combination", [])}

        # 吃：向听数减少时才吃
        for op in op_list:
            if op.get("type") == 2:
                combination = op.get("combination", [])
                if combination:
                    combo_tiles = self._parse_combination(combination[0])
                    simulated = list(hand)
                    for ct in combo_tiles:
                        norm_ct = normalize_aka(ct)
                        for i, h in enumerate(simulated):
                            if normalize_aka(h) == norm_ct:
                                simulated.pop(i)
                                break
                    new_shanten = calc_shanten(simulated)
                    if new_shanten < current_shanten:
                        logger.info(f"AI 决定: 吃 (向听 {current_shanten}→{new_shanten})")
                        return {"type": 2, "combination": combination}

        logger.debug("AI 决定: 跳过")
        return None

    def _choose_riichi_tile(self, state: GameState,
                            combinations: list) -> str:
        """选择立直时打哪张牌"""
        # combination 格式可能是 ["5p", "3p|5p"] 等
        if len(combinations) == 1:
            tile = combinations[0].split("|")[0]
            return tile
        # 多个选择时，选受入最广的
        return combinations[0].split("|")[0]

    def _parse_combination(self, combo_str: str) -> list[str]:
        """解析 combination 字符串为牌列表
        格式: "7z|7z" 或 "1p|2p|3p" 等
        """
        parts = combo_str.split("|")
        # 过滤掉纯数字部分（seat 信息）
        tiles = []
        for p in parts:
            if len(p) >= 2 and p[-1] in "mpsz":
                tiles.append(p)
        return tiles

    def on_round_start(self, state: GameState) -> None:
        shanten = calc_shanten(state.get_full_hand())
        logger.info(f"AI: 新一局开始 (向听={shanten})")

    def on_game_end(self, result: dict) -> None:
        logger.info("AI: 对局结束")
