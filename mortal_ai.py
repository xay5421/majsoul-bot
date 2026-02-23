"""Mortal AI 引擎 — 直接加载 Mortal 模型进行推理

依赖:
- mortal/mortal.pth (模型权重)
- mortal/libriichi.so (Rust 编译的 Python 扩展)
- torch (PyTorch CPU 版)
- numpy
"""
import json
import sys
import pathlib
import logging

logger = logging.getLogger("majsoul.mortal")

MORTAL_DIR = pathlib.Path(__file__).parent / "mortal"


def check_mortal_available() -> tuple[bool, str]:
    """检查 Mortal 依赖是否可用"""
    pth_file = MORTAL_DIR / "mortal.pth"
    if not pth_file.exists():
        return False, f"模型文件不存在: {pth_file}"

    so_file = MORTAL_DIR / "libriichi.so"
    if not so_file.exists():
        return False, f"libriichi.so 不存在: {so_file}"

    try:
        import torch
    except ImportError:
        return False, "缺少 PyTorch: pip install torch --index-url https://download.pytorch.org/whl/cpu"

    try:
        import numpy
    except ImportError:
        return False, "缺少 numpy: pip install numpy"

    return True, "OK"


def _ensure_mortal_path():
    """确保 mortal/ 在 sys.path 中"""
    mortal_dir = str(MORTAL_DIR)
    if mortal_dir not in sys.path:
        sys.path.insert(0, mortal_dir)


def load_mortal_bot(seat: int):
    """加载 Mortal AI Bot

    Args:
        seat: 玩家座位号 (0-3)

    Returns:
        MortalBot 实例
    """
    import torch

    _ensure_mortal_path()

    from libriichi.mjai import Bot as LibriichBot
    from model import Brain, DQN, MortalEngine

    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    pth_file = MORTAL_DIR / "mortal.pth"
    logger.info(f"加载 Mortal 模型: {pth_file} (device={device})")

    state = torch.load(pth_file, map_location=device, weights_only=False)
    config = state['config']
    version = config['control']['version']

    logger.info(f"模型版本: {version}, "
                f"conv_channels={config['resnet']['conv_channels']}, "
                f"num_blocks={config['resnet']['num_blocks']}")

    brain = Brain(
        version=version,
        conv_channels=config['resnet']['conv_channels'],
        num_blocks=config['resnet']['num_blocks'],
    ).eval()
    dqn = DQN(version=version).eval()

    brain.load_state_dict(state['mortal'])
    dqn.load_state_dict(state['current_dqn'])

    engine = MortalEngine(
        brain, dqn,
        is_oracle=False,
        version=version,
        device=device,
        enable_amp=False,
        enable_quick_eval=False,
        enable_rule_based_agari_guard=True,
        name='mortal',
    )

    bot = LibriichBot(engine, seat)
    logger.info(f"Mortal AI 加载成功! 座位={seat}")
    return MortalBot(bot, seat)


class MortalBot:
    """Mortal AI 封装，接收 mjai 事件列表，返回 mjai 动作"""

    def __init__(self, libriichi_bot, seat: int):
        self._bot = libriichi_bot
        self.seat = seat

    def react(self, events: list[dict]) -> dict | None:
        """处理 mjai 事件并返回动作

        Args:
            events: mjai 格式事件列表（牌编码用 mjai 标准: E/S/W/N/P/F/C）

        Returns:
            mjai 格式动作 dict，或 None
        """
        result = None
        for event in events:
            if event["type"] == "end_game":
                return None

            event_json = json.dumps(event, separators=(",", ":"))
            try:
                action_json = self._bot.react(event_json)
                if action_json:
                    action = json.loads(action_json)
                    if action.get("type") != "none":
                        result = action
            except Exception as e:
                logger.error(f"Mortal react 错误: {e}")
                continue

        return result
