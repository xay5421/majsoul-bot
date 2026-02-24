"""人类行为模拟 — 让操作看起来更自然

反检测策略:
1. 思考时间：根据局面复杂度动态调整，模拟真人思考节奏
2. 操作延迟：不同操作类型有不同的反应时间分布
3. 随机波动：加入正态分布的随机抖动
4. 疲劳效应：长时间打牌后反应变慢
5. 快速操作：简单决策（如摸切）偶尔很快
"""
import random
import time
import math
import logging

logger = logging.getLogger("majsoul.human")


class HumanBehavior:
    """模拟人类打牌行为"""

    def __init__(self):
        self._game_start_time: float = 0
        self._round_start_time: float = 0
        self._last_action_time: float = 0
        self._turn_count: int = 0
        self._total_actions: int = 0

    def on_game_start(self) -> None:
        self._game_start_time = time.time()
        self._total_actions = 0

    def on_round_start(self) -> None:
        self._round_start_time = time.time()
        self._turn_count = 0

    def on_action(self) -> None:
        self._last_action_time = time.time()
        self._turn_count += 1
        self._total_actions += 1

    @property
    def _fatigue_factor(self) -> float:
        """疲劳系数：打得越久反应越慢"""
        if self._game_start_time == 0:
            return 1.0
        elapsed_min = (time.time() - self._game_start_time) / 60
        # 每30分钟反应慢 10%，最多慢 50%
        return 1.0 + min(elapsed_min / 30 * 0.1, 0.5)

    def get_discard_delay(self, is_tsumogiri: bool = False,
                          hand_size: int = 13,
                          is_riichi: bool = False) -> float:
        """出牌延迟 — 缩短版，避免服务端超时自动出牌"""
        if is_riichi:
            return max(0.2, random.gauss(0.3, 0.1))

        if is_tsumogiri:
            return max(0.2, random.gauss(0.4, 0.15))
        else:
            # 手切：稍微思考
            base = random.gauss(0.8, 0.3)
            return max(0.3, base * self._fatigue_factor)

    def get_call_delay(self, call_type: str = "pon") -> float:
        """吃碰杠的反应延迟 — 缩短版"""
        if call_type in ("ron", "tsumo"):
            base = random.gauss(0.3, 0.1)
        else:
            base = random.gauss(0.5, 0.2)
        return max(0.2, base)

    def get_skip_delay(self) -> float:
        """跳过操作的延迟 — 缩短版"""
        return max(0.1, random.gauss(0.3, 0.1))

    def get_riichi_delay(self) -> float:
        """立直决策延迟"""
        return max(0.3, random.gauss(0.8, 0.3))

    def get_new_round_delay(self) -> float:
        """新一局确认延迟"""
        return random.gauss(1.0, 0.3)

    def should_emoji(self) -> bool:
        """是否发表情（偶尔互动一下）"""
        return random.random() < 0.02  # 2% 概率

    def get_reconnect_jitter(self) -> float:
        """重连时的随机延迟，避免断线重连模式太规律"""
        return random.uniform(1.0, 5.0)
