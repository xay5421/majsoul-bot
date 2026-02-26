"""人类行为模拟 — 操作延迟"""
import random


class HumanBehavior:
    """模拟人类打牌行为（仅延迟）"""

    def on_game_start(self) -> None:
        pass

    def on_round_start(self) -> None:
        pass

    def on_action(self) -> None:
        pass

    def get_discard_delay(self, is_tsumogiri: bool = False,
                          hand_size: int = 13,
                          is_riichi: bool = False) -> float:
        if is_riichi or is_tsumogiri:
            return random.uniform(0.3, 0.6)
        return random.uniform(0.4, 0.8)

    def get_call_delay(self, call_type: str = "pon") -> float:
        if call_type in ("ron", "tsumo"):
            return random.uniform(0.3, 0.6)
        return random.uniform(0.5, 1.0)

    def get_skip_delay(self) -> float:
        return random.uniform(0.2, 0.5)

    def get_riichi_delay(self) -> float:
        return random.uniform(0.5, 1.0)

    def get_new_round_delay(self) -> float:
        return random.uniform(1.0, 2.0)
