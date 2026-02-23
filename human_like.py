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
        """出牌延迟

        Args:
            is_tsumogiri: 是否摸切（摸什么打什么）
            hand_size: 手牌数量
            is_riichi: 是否已立直（立直后只能摸切）
        """
        if is_riichi:
            # 立直后强制摸切，但人类还是会看一眼
            base = random.gauss(0.5, 0.2)
            return max(0.3, base)

        if is_tsumogiri:
            # 摸切：有时很快（确定不要），有时会犹豫一下
            if random.random() < 0.3:
                # 30% 概率秒切
                base = random.gauss(0.6, 0.15)
            else:
                base = random.gauss(1.2, 0.4)
        else:
            # 手切：需要思考打哪张
            # 巡目越早选择越多，思考越久
            complexity = min(hand_size / 13, 1.0)
            base = random.gauss(1.5 + complexity * 1.5, 0.6)

            # 偶尔长考（5% 概率）
            if random.random() < 0.05:
                base += random.gauss(3.0, 1.0)

        return max(0.3, base * self._fatigue_factor)

    def get_call_delay(self, call_type: str = "pon") -> float:
        """吃碰杠的反应延迟

        Args:
            call_type: "chi" / "pon" / "kan" / "ron" / "tsumo"
        """
        if call_type in ("ron", "tsumo"):
            # 和牌：通常很快点确认，但不能秒点
            base = random.gauss(0.8, 0.3)
        elif call_type == "pon":
            # 碰：需要判断要不要碰
            base = random.gauss(1.5, 0.5)
        elif call_type == "chi":
            # 吃：需要考虑吃哪组
            base = random.gauss(1.8, 0.6)
        elif call_type == "kan":
            # 杠：需要考虑要不要杠
            base = random.gauss(1.3, 0.4)
        else:
            base = random.gauss(1.5, 0.5)

        return max(0.3, base * self._fatigue_factor)

    def get_skip_delay(self) -> float:
        """跳过操作（不吃碰杠）的延迟

        人类在看到可以操作时也需要时间决定"不操作"
        """
        # 有时候几乎不犹豫，有时候要想一下
        if random.random() < 0.5:
            base = random.gauss(0.4, 0.15)  # 快速跳过
        else:
            base = random.gauss(1.0, 0.3)   # 想了一下才跳过

        return max(0.2, base)

    def get_riichi_delay(self) -> float:
        """立直决策延迟 — 通常需要较长思考"""
        base = random.gauss(2.5, 0.8)

        # 20% 概率长考
        if random.random() < 0.2:
            base += random.gauss(2.0, 0.8)

        return max(0.8, base * self._fatigue_factor)

    def get_new_round_delay(self) -> float:
        """新一局确认延迟 — 看看上一局结果"""
        return random.gauss(2.0, 0.5)

    def should_emoji(self) -> bool:
        """是否发表情（偶尔互动一下）"""
        return random.random() < 0.02  # 2% 概率

    def get_reconnect_jitter(self) -> float:
        """重连时的随机延迟，避免断线重连模式太规律"""
        return random.uniform(1.0, 5.0)
