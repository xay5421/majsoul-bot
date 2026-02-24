"""Mortal AI — 深度强化学习麻将 AI (mjai subprocess 接口)"""
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from ai.base import BaseAI
from game_state import GameState
from tiles import normalize_aka

logger = logging.getLogger("majsoul.ai")

# 雀魂牌编码 → mjai 牌编码
_MS_TO_MJAI = {}
for i in range(1, 10):
    _MS_TO_MJAI[f"{i}m"] = f"{i}m"
    _MS_TO_MJAI[f"{i}p"] = f"{i}p"
    _MS_TO_MJAI[f"{i}s"] = f"{i}s"
# 赤牌
_MS_TO_MJAI["0m"] = "5mr"
_MS_TO_MJAI["0p"] = "5pr"
_MS_TO_MJAI["0s"] = "5sr"
# 字牌: 1z=E, 2z=S, 3z=W, 4z=N, 5z=P(白), 6z=F(发), 7z=C(中)
_WIND_MAP = {"1z": "E", "2z": "S", "3z": "W", "4z": "N",
             "5z": "P", "6z": "F", "7z": "C"}
_MS_TO_MJAI.update(_WIND_MAP)

# mjai → 雀魂
_MJAI_TO_MS = {v: k for k, v in _MS_TO_MJAI.items()}


def ms_to_mjai(tile: str) -> str:
    """雀魂牌编码转 mjai 编码"""
    # 处理 "5z|5" 这种带来源的格式
    if "|" in tile:
        tile = tile.split("|")[0]
    return _MS_TO_MJAI.get(tile, tile)


def mjai_to_ms(tile: str) -> str:
    """mjai 牌编码转雀魂编码"""
    return _MJAI_TO_MS.get(tile, tile)


# 场风映射
_BAKAZE = {0: "E", 1: "S", 2: "W", 3: "N"}


class MortalAI(BaseAI):
    """Mortal 深度强化学习 AI

    通过 subprocess 与 Mortal 的 mortal.py 进程通信。
    使用 mjai 协议 (stdin/stdout JSON lines)。
    """

    def __init__(self, mortal_dir: str | None = None):
        mortal_dir = mortal_dir or self._find_mortal_dir()
        self.mortal_dir = Path(mortal_dir)
        self.process: subprocess.Popen | None = None
        self.player_id: int = 0
        self._last_reaction: dict | None = None
        self._reach_pending: bool = False
        self._fallback: BaseAI | None = None  # fallback AI
        self._game_active = False
        self._mjai_log: list[str] = []  # 记录所有发送给 Mortal 的事件
        logger.info(f"MortalAI: mortal_dir={self.mortal_dir}")

    @staticmethod
    def _find_mortal_dir() -> str:
        """自动查找 Mortal 目录"""
        candidates = [
            os.path.expanduser("~/workspace/Mortal/mortal"),
            os.path.expanduser("~/Mortal/mortal"),
        ]
        for d in candidates:
            if os.path.isfile(os.path.join(d, "mortal.py")):
                return d
        raise FileNotFoundError(
            "找不到 Mortal 目录，请在 config.yaml 中设置 ai.mortal_dir"
        )

    def _force_fallback(self):
        """强制切换到 fallback AI（断线重连时用）"""
        if self._fallback is None:
            from ai.shanten import ShantenAI
            self._fallback = ShantenAI()
        # 杀掉 Mortal 进程
        if self.process and self.process.poll() is None:
            self.process.kill()
            self.process = None
        self._last_reaction = None
        logger.warning("已强制切换到 ShantenAI fallback")

    def clear_last_reaction(self):
        """清除缓存的最后决策（重放后需要清除）"""
        self._last_reaction = None
        self._reach_pending = False

    def _restart_mortal(self):
        """重启 Mortal 进程（新一局开始时恢复）"""
        if self._fallback is not None:
            logger.info("新一局开始，重启 Mortal 进程")
            self._fallback = None
        self._start_process()

    def _start_process(self):
        """启动 Mortal 子进程"""
        if self.process and self.process.poll() is None:
            self.process.kill()

        env = os.environ.copy()
        env["MORTAL_CFG"] = "config.toml"
        # 确保 libriichi.so 在路径中
        env["PYTHONPATH"] = str(self.mortal_dir)

        # 优先用 Mortal 自己的 venv python（有独立依赖）
        mortal_python = self.mortal_dir / ".venv" / "bin" / "python"
        if not mortal_python.exists():
            mortal_python = self.mortal_dir / ".venv" / "Scripts" / "python.exe"
        if not mortal_python.exists():
            mortal_python = Path(sys.executable)
            logger.warning(f"Mortal 没有独立 venv，使用当前 python: {mortal_python}")

        cmd = [
            str(mortal_python), "mortal.py", str(self.player_id)
        ]
        logger.info(f"启动 Mortal: {' '.join(cmd)}")
        self.process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(self.mortal_dir),
            env=env,
            text=True,
            bufsize=1,  # line buffered
        )

        # 等待进程启动（最多 30 秒）
        import time
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                stderr = self.process.stderr.read() if self.process.stderr else ""
                raise RuntimeError(f"Mortal 进程启动失败 (code={self.process.returncode}): {stderr[:500]}")
            # 进程还活着就行
            time.sleep(0.1)
            break  # Popen 成功就继续，不需要等

    def _send(self, event: dict) -> str | None:
        """发送 mjai 事件，返回 Mortal 的响应（如果有）"""
        if not self.process or self.process.poll() is not None:
            # 读取 stderr 获取崩溃原因（只读一次）
            if self.process and self.process.stderr:
                try:
                    stderr = self.process.stderr.read()
                    if stderr:
                        logger.error(f"Mortal 崩溃原因: {stderr[:500]}")
                        # dump mjai 事件日志用于排查
                        log_file = "mortal_crash.log"
                        with open(log_file, "w") as f:
                            f.write("\n".join(self._mjai_log))
                        logger.error(f"Mortal 事件日志已保存到 {log_file} ({len(self._mjai_log)} 条)")
                        self.process = None  # 清理，避免重复读
                except Exception:
                    pass
            # 不重复刷日志
            return None

        line = json.dumps(event, ensure_ascii=False)
        self._mjai_log.append(line)
        logger.debug(f"→ Mortal: {line}")
        try:
            self.process.stdin.write(line + "\n")
            self.process.stdin.flush()
        except BrokenPipeError:
            stderr = self.process.stderr.read() if self.process.stderr else ""
            logger.error(f"Mortal 进程已退出: {stderr[:500]}")
            return None

        # Mortal 只在需要决策时输出响应
        # 它对每个它能响应的事件输出一行
        # 我们需要读取所有可用的输出
        return self._read_reaction()

    def _read_reaction(self, timeout: float = 10) -> str | None:
        """读取 Mortal 的一行输出（带超时）"""
        if not self.process or self.process.poll() is not None:
            return None
        import select
        try:
            # 用 select 实现超时，避免永久阻塞
            ready, _, _ = select.select([self.process.stdout], [], [], timeout)
            if not ready:
                logger.warning(f"Mortal 响应超时 ({timeout}s)")
                return None
            line = self.process.stdout.readline()
            if line:
                line = line.strip()
                logger.debug(f"← Mortal: {line}")
                return line
        except Exception as e:
            logger.error(f"读取 Mortal 输出失败: {e}")
        return None

    def _send_and_collect(self, event: dict) -> dict | None:
        """发送事件并收集响应，解析为 dict"""
        resp = self._send(event)
        if resp:
            try:
                data = json.loads(resp)
                # 只在有实际操作时更新 _last_reaction
                if data.get("type") != "none":
                    self._last_reaction = data
                return data
            except json.JSONDecodeError:
                logger.warning(f"Mortal 输出无法解析: {resp}")
        return None

    # ─── BaseAI 接口实现 ──────────────────────────

    def on_game_start(self, state: GameState) -> None:
        """对局开始：启动 Mortal 并发送 start_game"""
        self.player_id = state.seat
        self._start_process()
        self._send_and_collect({"type": "start_game"})
        self._game_active = True
        logger.info(f"Mortal AI 已启动 (seat={self.player_id})")

    def on_round_start(self, state: GameState) -> None:
        """新一局开始：发送 start_kyoku"""
        self._last_reaction = None
        self._reach_pending = False
        self._mjai_log = []  # 新一局清空事件日志

        # 如果之前 fallback 了（断线重连/崩溃），新一局重启 Mortal
        if self._fallback is not None or self.process is None or self.process.poll() is not None:
            self._restart_mortal()
            # 重新发 start_game
            self._send_and_collect({"type": "start_game"})
            self._game_active = True
        # 构建 tehais（四家手牌，只有自己的可见）
        tehais = []
        my_tiles = list(state.hand)
        # 如果有摸牌（庄家14张），分离出来
        tsumo_tile = None
        if state.draw:
            tsumo_tile = state.draw
            # hand 不包含 draw，所以 my_tiles 就是 13 张

        for i in range(state.player_count):
            if i == state.seat:
                tehais.append([ms_to_mjai(t) for t in my_tiles])
            else:
                tehais.append(["?"] * 13)

        # dora_marker
        dora_markers = [ms_to_mjai(d) for d in state.dora_indicators]
        dora_marker = dora_markers[0] if dora_markers else "?"

        event = {
            "type": "start_kyoku",
            "bakaze": _BAKAZE.get(state.round_wind, "E"),
            "dora_marker": dora_marker,
            "kyoku": state.round_num + 1,  # mjai 从 1 开始
            "honba": state.honba,
            "kyotaku": state.riichi_sticks,
            "oya": state.dealer,
            "scores": [p.score for p in state.players],
            "tehais": tehais,
        }
        self._send_and_collect(event)

        # 庄家的第14张牌作为 tsumo 发送
        if tsumo_tile:
            self.send_tsumo(state.seat, tsumo_tile)

        logger.info(f"Mortal: 新一局 (seat={state.seat}, tsumo={tsumo_tile})")

    def on_game_end(self, result: dict) -> None:
        """对局结束：关闭 Mortal 进程"""
        self._game_active = False
        if self.process and self.process.poll() is None:
            try:
                self.process.stdin.close()
                self.process.wait(timeout=5)
            except Exception:
                self.process.kill()
        self.process = None
        logger.info("Mortal AI 已关闭")

    def send_tsumo(self, actor: int, tile: str | None) -> dict | None:
        """发送摸牌事件"""
        event = {
            "type": "tsumo",
            "actor": actor,
            "pai": ms_to_mjai(tile) if (actor == self.player_id and tile) else "?",
        }
        reaction = self._send_and_collect(event)

        # 如果 Mortal 回复 reach（立直），需要再发一次 reach 事件获取 dahai
        if reaction and reaction.get("type") == "reach":
            self._reach_pending = True
            # 发送 reach 事件给 Mortal，获取立直出牌
            reach_event = {"type": "reach", "actor": actor}
            dahai = self._send_and_collect(reach_event)
            if dahai and dahai.get("type") == "dahai":
                # 合并立直信息到 _last_reaction
                self._last_reaction = {
                    "type": "reach_dahai",
                    "pai": dahai["pai"],
                    "actor": actor,
                    "meta": dahai.get("meta"),
                }
            return self._last_reaction

        return reaction

    def send_dahai(self, actor: int, tile: str, tsumogiri: bool) -> dict | None:
        """发送出牌事件"""
        event = {
            "type": "dahai",
            "actor": actor,
            "pai": ms_to_mjai(tile),
            "tsumogiri": tsumogiri,
        }
        return self._send_and_collect(event)

    def send_reach(self, actor: int) -> dict | None:
        """发送立直宣言"""
        return self._send_and_collect({"type": "reach", "actor": actor})

    def send_reach_accepted(self, actor: int) -> dict | None:
        """发送立直成立"""
        return self._send_and_collect({"type": "reach_accepted", "actor": actor})

    def send_chi(self, actor: int, target: int, pai: str,
                 consumed: list[str]) -> dict | None:
        """发送吃"""
        return self._send_and_collect({
            "type": "chi",
            "actor": actor,
            "target": target,
            "pai": ms_to_mjai(pai),
            "consumed": [ms_to_mjai(t) for t in consumed],
        })

    def send_pon(self, actor: int, target: int, pai: str,
                 consumed: list[str]) -> dict | None:
        """发送碰"""
        return self._send_and_collect({
            "type": "pon",
            "actor": actor,
            "target": target,
            "pai": ms_to_mjai(pai),
            "consumed": [ms_to_mjai(t) for t in consumed],
        })

    def send_daiminkan(self, actor: int, target: int, pai: str,
                       consumed: list[str]) -> dict | None:
        """发送大明杠"""
        return self._send_and_collect({
            "type": "daiminkan",
            "actor": actor,
            "target": target,
            "pai": ms_to_mjai(pai),
            "consumed": [ms_to_mjai(t) for t in consumed],
        })

    def send_kakan(self, actor: int, pai: str,
                   consumed: list[str]) -> dict | None:
        """发送加杠"""
        return self._send_and_collect({
            "type": "kakan",
            "actor": actor,
            "pai": ms_to_mjai(pai),
            "consumed": [ms_to_mjai(t) for t in consumed],
        })

    def send_ankan(self, actor: int, consumed: list[str]) -> dict | None:
        """发送暗杠"""
        return self._send_and_collect({
            "type": "ankan",
            "actor": actor,
            "consumed": [ms_to_mjai(t) for t in consumed],
        })

    def send_hora(self, actor: int, target: int, pai: str) -> dict | None:
        """发送和牌"""
        return self._send_and_collect({
            "type": "hora",
            "actor": actor,
            "target": target,
            "pai": ms_to_mjai(pai),
        })

    def send_ryukyoku(self) -> dict | None:
        """发送流局"""
        return self._send_and_collect({"type": "ryukyoku"})

    def send_end_kyoku(self) -> dict | None:
        """发送局结束"""
        return self._send_and_collect({"type": "end_kyoku"})

    def send_none(self) -> dict | None:
        """发送 none（跳过响应）"""
        return self._send_and_collect({"type": "none"})

    # ─── 决策接口 (兼容 BaseAI) ────────────────────

    def decide_discard(self, state: GameState) -> str:
        """从 Mortal 最后的 reaction 中提取出牌决策"""
        if self._last_reaction and self._last_reaction.get("type") == "dahai":
            tile = mjai_to_ms(self._last_reaction["pai"])
            logger.info(f"Mortal 决定打: {tile}")
            return tile

        # Mortal 挂了或没决策，fallback 到 shanten
        if not self.process or self.process.poll() is not None:
            if self._fallback is None:
                from ai.shanten import ShantenAI
                self._fallback = ShantenAI()
                logger.warning("Mortal 已崩溃，fallback 到 ShantenAI")
            return self._fallback.decide_discard(state)

        # 其他情况 fallback 摸切
        logger.warning("Mortal 无决策，fallback 摸切")
        return state.draw or state.hand[-1]

    def decide_action(self, state: GameState, actions: dict) -> dict | None:
        """从 Mortal 最后的 reaction 中提取操作决策"""
        if not self._last_reaction:
            # Mortal 挂了，fallback
            if not self.process or self.process.poll() is not None:
                if self._fallback is None:
                    from ai.shanten import ShantenAI
                    self._fallback = ShantenAI()
                    logger.warning("Mortal 已崩溃，fallback 到 ShantenAI")
                return self._fallback.decide_action(state, actions)
            return None

        r = self._last_reaction
        rtype = r.get("type", "none")

        if rtype == "none":
            return None
        elif rtype == "reach_dahai":
            # Mortal 立直 + 出牌
            tile = mjai_to_ms(r["pai"])
            logger.info(f"Mortal 决定立直打: {tile}")
            return {"type": 7, "tile": tile}
        elif rtype == "dahai":
            # 普通出牌（不做特殊操作）
            return None  # 让 bot.py 走 _do_discard 分支
        elif rtype == "chi":
            consumed = [mjai_to_ms(t) for t in r.get("consumed", [])]
            return {"type": 2, "combination": consumed}
        elif rtype == "pon":
            consumed = [mjai_to_ms(t) for t in r.get("consumed", [])]
            return {"type": 3, "combination": consumed}
        elif rtype == "daiminkan":
            consumed = [mjai_to_ms(t) for t in r.get("consumed", [])]
            return {"type": 5, "combination": consumed}
        elif rtype == "ankan":
            consumed = [mjai_to_ms(t) for t in r.get("consumed", [])]
            return {"type": 4, "combination": consumed}
        elif rtype == "kakan":
            consumed = [mjai_to_ms(t) for t in r.get("consumed", [])]
            return {"type": 6, "combination": consumed}
        elif rtype == "hora":
            # 判断自摸/荣和
            if r.get("actor") == r.get("target"):
                return {"type": 8}  # 自摸
            else:
                return {"type": 9}  # 荣和

        return None
