"""基础规则 AI

策略：
1. 出牌：优先打字牌（非役牌）→ 幺九牌 → 中张牌，保留对子和搭子
2. 吃碰杠：碰役牌（自风/场风/三元牌），不吃
3. 立直：听牌时立直
4. 和：能和就和
"""
import logging
from collections import Counter
from ai.base import BaseAI
from game_state import GameState
from tiles import (
    normalize_aka, tile_number, tile_suit, is_honor, is_terminal,
    tile_to_str, sort_tiles
)

logger = logging.getLogger("majsoul.ai")


class BasicAI(BaseAI):
    """基础规则 AI — 能跑通流程就行"""

    def decide_discard(self, state: GameState) -> str:
        """选择要打出的牌"""
        hand = state.get_full_hand()
        if not hand:
            logger.error("手牌为空!")
            return ""

        # 统计每张牌的出现次数
        norm_counts = Counter(normalize_aka(t) for t in hand)

        # 役牌：自风、场风、三元牌
        yakuhai = set()
        # 自风
        wind_tiles = {0: "1z", 1: "2z", 2: "3z", 3: "4z"}
        yakuhai.add(wind_tiles[state.my_wind])
        yakuhai.add(wind_tiles[state.round_wind])
        # 三元牌
        yakuhai.update(["5z", "6z", "7z"])

        def discard_priority(tile: str) -> float:
            """出牌优先度，值越大越优先打出"""
            norm = normalize_aka(tile)
            suit = tile_suit(norm)
            num = tile_number(norm)
            count = norm_counts.get(norm, 1)

            score = 0.0

            # 保留对子和刻子（出现次数越多越不想打）
            score -= count * 30

            if suit == "z":
                # 字牌
                if norm in yakuhai:
                    # 役牌，不太想打
                    score -= 10
                else:
                    # 客风牌，优先打
                    score += 20
            else:
                # 数牌
                if num == 1 or num == 9:
                    # 幺九牌，优先打
                    score += 10
                elif num == 2 or num == 8:
                    score += 5
                # 中张牌有更多搭子可能，不太想打
                # 检查是否有邻牌（搭子）
                neighbors = 0
                for delta in [-2, -1, 1, 2]:
                    neighbor_num = num + delta
                    if 1 <= neighbor_num <= 9:
                        neighbor = f"{neighbor_num}{suit}"
                        if neighbor in norm_counts:
                            neighbors += 1
                score -= neighbors * 15

            return score

        # 按优先度排序，打优先度最高的
        candidates = sorted(hand, key=discard_priority, reverse=True)
        choice = candidates[0]
        logger.info(f"AI 决定打出: {tile_to_str(choice)}")
        return choice

    def decide_action(self, state: GameState, actions: dict) -> dict | None:
        """决定是否执行操作"""
        if not actions:
            return None

        op_list = actions.get("operation_list", [])
        if not op_list:
            return None

        # 操作类型 (从 protobuf 定义):
        # 1 = 出牌
        # 2 = 吃
        # 3 = 碰
        # 4 = 暗杠
        # 5 = 明杠 (大明杠)
        # 6 = 加杠
        # 7 = 立直
        # 8 = 自摸
        # 9 = 荣和 (Ron)
        # 10 = 九种九牌
        # 11 = 拔北 (三麻)

        # 能和就和
        for op in op_list:
            if op.get("type") in [8, 9]:  # 自摸 or 荣和
                logger.info("AI 决定: 和了!")
                return {"type": op["type"]}

        # 立直
        for op in op_list:
            if op.get("type") == 7:
                logger.info("AI 决定: 立直!")
                # 选择打哪张立直
                combination = op.get("combination", [])
                if combination:
                    tile = combination[0]
                    return {"type": 7, "tile": tile}
                return {"type": 7}

        # 碰役牌
        wind_tiles = {0: "1z", 1: "2z", 2: "3z", 3: "4z"}
        yakuhai = {wind_tiles[state.my_wind], wind_tiles[state.round_wind],
                   "5z", "6z", "7z"}

        for op in op_list:
            if op.get("type") == 3:  # 碰
                combination = op.get("combination", [])
                if combination:
                    # 检查是否是役牌
                    tiles_in_combo = combination[0].split("|") if isinstance(combination[0], str) else combination
                    for t in tiles_in_combo if isinstance(tiles_in_combo, list) else [tiles_in_combo]:
                        if normalize_aka(str(t).split("|")[0] if "|" in str(t) else str(t)) in yakuhai:
                            logger.info(f"AI 决定: 碰 (役牌)")
                            return {"type": 3, "combination": combination}

        # 暗杠（随时杠）
        for op in op_list:
            if op.get("type") == 4:  # 暗杠
                logger.info("AI 决定: 暗杠")
                return {"type": 4, "combination": op.get("combination", [])}

        # 加杠
        for op in op_list:
            if op.get("type") == 6:  # 加杠
                logger.info("AI 决定: 加杠")
                return {"type": 6, "combination": op.get("combination", [])}

        # 默认不操作（跳过吃/碰非役牌等）
        logger.debug("AI 决定: 跳过")
        return None

    def on_round_start(self, state: GameState) -> None:
        logger.info("AI: 新一局开始")

    def on_game_end(self, result: dict) -> None:
        logger.info(f"AI: 对局结束")
