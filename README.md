# Majsoul Bot 🀄

雀魂自动打麻将机器人 — 纯协议对接 CN 服，无需浏览器。

通过 WebSocket + Protobuf 直接与雀魂服务器通信，支持段位赛 / AI 对战，内置 [Mortal](https://github.com/Equim-chan/Mortal) 深度学习 AI。

## 快速开始

### 环境要求

- **Python 3.10 - 3.12**（libriichi 不支持 3.13+）
- Linux / macOS / Windows
- 网络能连通雀魂 CN 服（`maj-soul.com`）

### 1. 克隆 & 安装

```bash
git clone https://github.com/xay5421/majsoul-bot.git
cd majsoul-bot

python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. 配置 Mortal AI

项目内置了 Mortal 源码（`Mortal/` 目录），但需要手动准备两个文件：

| 文件 | 说明 | 放置路径 |
|------|------|----------|
| `mortal.pth` | 模型权重（~5-91MB） | `Mortal/mortal/mortal.pth` |
| `libriichi.so` | Rust 推理引擎 | `Mortal/mortal/libriichi.so` |

> **获取方式**：从 [Akagi v2](https://github.com/shinkuan/Akagi/tree/v2/mjai_bot/mortal) 下载对应 Python 版本和平台的文件。Windows 用 `libriichi.pyd`。

安装 Mortal 的 Python 依赖到 bot 的 venv 中：

```bash
# 在 .venv 激活状态下
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install numpy
```

验证 Mortal 能运行：

```bash
echo '{"type":"start_game","id":0}' | python Mortal/mortal/mortal.py 0
# 应该输出 {"type":"none"}
```

### 3. 配置账号

```bash
cp config.example.yaml config.yaml
```

编辑 `config.yaml`：

```yaml
account:
  username: "your_email@qq.com"
  password: "your_password"

match:
  mode: "rank"          # rank (段位赛) / ai (AI对战)
  room_type: "4s"       # 4e (四人东) / 4s (四人南)
  level: "silver"       # copper / silver / gold

ai:
  type: "mortal"        # basic / shanten / mortal
  nerf_turns: 0         # 装弱：第一名时前 N 手用随机采样（0=关闭）

run:
  max_games: 5          # 连续打几局 (0 = 不限)
  game_interval: 5      # 每局间隔秒数
  log_level: "INFO"
```

### 4. 运行

```bash
python bot.py
```

Ctrl+C 优雅退出（会先 logout 再断开连接）。

## AI 引擎

| 类型 | 说明 | 强度 |
|------|------|------|
| `basic` | 规则 AI（打客风→幺九，碰役牌，能和就和） | ⭐ |
| `shanten` | 向听数贪心（断线重连 fallback） | ⭐⭐ |
| `mortal` | Mortal 深度学习 AI，subprocess 调用 | ⭐⭐⭐⭐⭐ |

### 装弱模式（反 AI 检测）

`nerf_turns: 10` — 当自己是第一名时，每局自己的前 10 手出牌使用 Mortal Q 值的 softmax 带权随机采样（temperature=2.0），偶尔打次优牌但不会打出离谱的牌。超过 N 手后恢复正常。

## 功能特性

- **纯协议**：WebSocket + Protobuf，不依赖浏览器或代理
- **段位赛**：铜 / 银 / 金之间，四人东 / 四人南
- **断线重连**：自动检测残留对局 → 重连 game-gateway → GameRestore 恢复 → Mortal 状态重放
- **Mortal 状态同步**：服务端超时自动摸切时，检测出牌不一致并重启 Mortal 重放修正事件序列
- **操作竞争保护**：防止网络延迟导致重复出牌
- **独立日志**：每局 `logs/game_*.log` + 全局 `logs/bot_*.log`
- **仿人延迟**：摸切 / 手切 / 鸣牌 / 和了各有不同随机延迟

## 项目结构

```
majsoul-bot/
├── bot.py              # 主入口：登录 → 匹配 → 打牌循环
├── client.py           # 雀魂 WebSocket 客户端（路由 / 登录 / 匹配 / 对局）
├── game_state.py       # 游戏状态机（手牌 / 牌河 / 场况 / 顺位）
├── codec.py            # ActionPrototype XOR 解码
├── config.py           # 配置管理
├── display.py          # 终端彩色输出
├── human_like.py       # 仿人操作延迟
├── tiles.py            # 麻将牌编码转换
├── ai/
│   ├── base.py         # AI 基类接口
│   ├── basic.py        # 基础规则 AI
│   ├── shanten.py      # 向听数贪心 AI
│   └── mortal.py       # Mortal AI 封装（subprocess + mjai 协议）
├── ms/                 # mahjong_soul_api 协议库（vendored）
├── Mortal/             # Mortal AI 源码（含训练代码）
│   ├── mortal/
│   │   ├── mortal.py       # 推理入口（mjai stdin/stdout）
│   │   ├── config.toml     # 模型配置
│   │   ├── mortal.pth      # 模型权重（需手动放置，git 不追踪）
│   │   ├── libriichi.so    # Rust 推理引擎（需手动放置）
│   │   ├── train.py        # 训练脚本
│   │   └── data/           # 训练数据目录
│   └── libriichi/          # Rust 源码（编译用）
├── config.example.yaml
├── logs/               # 运行日志（git 不追踪）
└── requirements.txt
```

## 环境搭建踩坑记录

### libriichi 兼容性

- `libriichi.so` / `.pyd` 是平台特定的 native 库，必须匹配 **Python 版本 + 操作系统 + CPU 架构**
- 支持 Python 3.10 - 3.12，**不支持 3.13+**
- 如需自行编译：安装 Rust，然后 `cd Mortal && cargo build -p libriichi --lib --release`，产物在 `target/release/`

### PyTorch

- 推理只需 CPU 版：`pip install torch --index-url https://download.pytorch.org/whl/cpu`
- 训练需要 GPU 版：`pip install torch --index-url https://download.pytorch.org/whl/cu124`（根据 CUDA 版本选择）
- CPU 版 ~200MB，GPU 版 ~2GB，别装错

### websockets 版本

- 代码已适配 `websockets 14.x-16.x`
- 16.0 移除了 `.open` 属性，用 `state == State.OPEN` 替代
- `ping_interval=None` 禁用内建 ping（bot 自己发心跳）

### protobuf

- 需要 `protobuf>=4.22`，旧版 API 不兼容
- `ms/protocol_pb2.py` 是预生成的，一般不需要重新生成

### Mortal config.toml

- `conv_channels` 和 `num_blocks` **必须与模型权重匹配**，否则加载报错
- 常见配置：
  - 小模型：`conv_channels=32, num_blocks=2`（~5MB，CPU ~3ms）
  - 大模型：`conv_channels=256, num_blocks=54`（~91MB，CPU ~350ms）
- `version = 4` 对应最新的观测空间格式

### 雀魂协议注意事项

- **ActionPrototype XOR 解码**：实时对局中 `ActionPrototype.data` 需要 XOR 解码，但 **GameRestore 的数据是明文**，不要重复解码
- **ActionAnGangAddGang**：`type=2` 是加杠（kakan），`type=3` 是暗杠（ankan），不要搞反
- **ReqSelfOperation**：`type=1` 出牌，`type=7` 立直出牌（不是 6 和 7）
- **操作时限**：铜/银之间操作时限较短（~2-3 秒），延迟不能太大
- **匹配 API**：用 `startUnifiedMatch`，旧的 `matchGame` 已废弃（会返回 1306）

### 训练自己的模型

Mortal 训练管线在 `Mortal/mortal/` 下：

```bash
cd Mortal/mortal

# 一键脚本：下载数据 → 转换 → 训练 GRP → 训练主模型
./setup_and_train.sh all

# 或分步执行
./setup_and_train.sh data    # 下载天凤牌谱
./setup_and_train.sh grp     # 训练 GRP（局面评估）
./setup_and_train.sh train   # 训练主模型（offline CQL）
```

训练完成后导出推理权重：

```bash
python export_weights.py best_state.pth mortal.pth
```

需要修改 `config.toml` 的 `conv_channels` 和 `num_blocks` 匹配训练配置。

> **硬件建议**：训练需要 GPU。RTX 5070（12GB）推荐 `conv_channels=128, num_blocks=20`（~25M 参数）。

## 常见问题

**Q: 匹配返回 1306？**
A: 旧 `matchGame` API 已废弃，确保使用 `startUnifiedMatch`。

**Q: 出牌和 AI 决策不一致（全摸切）？**
A: 操作超时，服务端自动出牌。检查 `human_like.py` 延迟设置。如果是断线重连后出现，可能是 Mortal 状态不同步，查日志中的 `⚠️ 出牌不一致!` 和重同步记录。

**Q: Mortal 崩溃 BrokenPipeError？**
A: mjai 协议不同步。常见原因：立直协议未完成（需 reach → dahai → reach_accepted 三步），或碰/杠的 consumed 牌不正确。

**Q: libriichi 加载失败？**
A: 确认 Python 版本（3.10-3.12）和系统架构匹配。`ldd libriichi.so` 检查动态库依赖。

**Q: torch 报错 "No module named 'torch'"？**
A: 确认在 bot 的 `.venv` 里装了 torch。Mortal 不再需要独立 venv，共用 bot 的即可。

## 免责声明

本项目仅供学习研究，使用机器人违反雀魂服务条款，后果自负。

## 致谢 & 许可

| 项目 | 作者 | 许可证 | 用途 |
|------|------|--------|------|
| [Mortal](https://github.com/Equim-chan/Mortal) | [Equim-chan](https://github.com/Equim-chan) | AGPL-3.0 | 麻将 AI 引擎 |
| [mahjong_soul_api](https://github.com/MahjongRepository/mahjong_soul_api) | MahjongRepository | MIT | 雀魂协议库（`ms/`） |
| [mjlog2mjai](https://github.com/fstqwq/mjlog2mjai) | [fstqwq](https://github.com/fstqwq) | MIT | 天凤牌谱转换 |

本项目因包含 Mortal（AGPL-3.0），整体以 **AGPL-3.0** 许可分发。
