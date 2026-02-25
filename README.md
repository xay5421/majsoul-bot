# Majsoul Bot 🀄

雀魂自动打麻将机器人 — 纯协议对接 CN 服，无需浏览器。

通过 WebSocket + Protobuf 直接与雀魂服务器通信，支持段位赛 / AI 对战，可接入 Mortal 等强 AI 引擎。

## 快速开始

### 1. 克隆 & 安装依赖

```bash
git clone https://github.com/xay5421/majsoul-bot.git
cd majsoul-bot

python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

依赖：
- `protobuf>=4.22`
- `websockets>=12.0`（已适配 16.0）
- `aiohttp>=3.9`
- `pyyaml>=6.0`

### 2. 配置

```bash
cp config.example.yaml config.yaml
```

编辑 `config.yaml`，填入雀魂 CN 服账号：

```yaml
account:
  username: "your_email@example.com"
  password: "your_password"

match:
  mode: "rank"          # rank (段位赛) / ai (AI对战)
  room_type: "4e"       # 4e (四人东) / 4s (四人南)
  level: "copper"       # copper (铜之间) / silver / gold

ai:
  type: "mortal"        # basic / shanten / mortal / mjai_subprocess / mjai_http
  mortal_dir: "/path/to/Mortal/mortal"  # Mortal 目录路径

run:
  max_games: 1          # 连续打几局 (0 = 不限)
  game_interval: 5      # 每局间隔秒数
  log_level: "INFO"
```

### 3. 测试连接

```bash
python test_connect.py -u 你的账号 -p 你的密码
```

看到 `🎉 所有步骤测试通过！` 说明连接正常。

### 4. 开始打牌

```bash
python bot.py
```

Ctrl+C 或 `kill` 可优雅退出。

## AI 后端

支持多种 AI，可插拔切换：

### basic — 规则 AI

开箱即用，策略简单（打客风→幺九，碰役牌，能和就和），适合跑通流程。

### shanten — 向听数贪心 AI

基于向听数计算的贪心策略，比 basic 强。断线重连时 Mortal 崩溃会自动 fallback 到这个。

### mortal — Mortal 深度学习 AI（推荐）

[Mortal](https://github.com/Equim-chan/Mortal) 是目前最强的开源日麻 AI（天凤十段+水平），CPU ~3ms/决策。

**安装步骤：**

1. 克隆 Mortal 仓库：
   ```bash
   git clone https://github.com/Equim-chan/Mortal.git
   cd Mortal/mortal
   ```

2. 创建独立 venv 并安装依赖：
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install torch --index-url https://download.pytorch.org/whl/cpu
   pip install numpy
   ```

3. 下载模型权重和推理引擎：
   - `mortal.pth` — 从 [Akagi v2](https://github.com/shinkuan/Akagi/tree/v2/mjai_bot/mortal) 下载
   - `libriichi.so`（Linux）/ `libriichi.pyd`（Windows）— 同上，选择对应 Python 版本和系统架构的文件
   - 放到 `Mortal/mortal/` 目录下

4. 创建 `config.toml`：
   ```toml
   [control]
   version = 4
   state_file = 'mortal.pth'

   [resnet]
   conv_channels = 32
   num_blocks = 2
   ```

5. 验证能运行：
   ```bash
   echo '{"type":"start_game","id":0}' | python mortal.py 0
   ```

6. 在 majsoul-bot 的 `config.yaml` 中配置路径：
   ```yaml
   ai:
     type: "mortal"
     mortal_dir: "/path/to/Mortal/mortal"
   ```

> **注意**：Mortal 需要独立 venv（含 PyTorch + libriichi），bot 通过 subprocess 调用，两边环境互不影响。  
> libriichi 支持 Python 3.10-3.12。  
> `mortal_dir` 自动查找：项目内 `Mortal/mortal/` 目录，或在 `config.yaml` 中手动指定 `ai.mortal_dir`

### mjai_subprocess — mjai 子进程

兼容 [mjai.app](https://mjai.app) 标准格式，通过 stdin/stdout JSON line 通信。

```yaml
ai:
  type: "mjai_subprocess"
  command: "python path/to/your/bot.py"
```

### mjai_http — mjai HTTP 服务

兼容 [Akagi](https://github.com/shinkuan/Akagi) 的 HTTP 后端。

```yaml
ai:
  type: "mjai_http"
  url: "http://127.0.0.1:7331"
```

## 功能特性

- **纯协议**：WebSocket + Protobuf，无需浏览器或 MITM 代理
- **段位赛**：`startUnifiedMatch` API，支持铜/银/金之间
- **断线重连**：登录时检测残留对局 → 重连 game-gateway → GameRestore 状态恢复 → Mortal AI 重放同步
- **Mortal 状态同步**：重连后将历史 action 转换为 mjai 事件重放给 Mortal，包括 reach 协议处理
- **操作竞争保护**：防止网络延迟导致 bot 和服务端出牌冲突
- **实时对局日志**：`game_live.log` 记录所有玩家的出牌、摸牌、副露、立直
- **优雅退出**：SIGINT/SIGTERM 正确终止所有异步任务

## 项目结构

```
majsoul-bot/
├── bot.py              # 主入口：登录 → 匹配 → 打牌循环
├── client.py           # 雀魂 WebSocket 客户端
├── game_state.py       # 游戏状态机（手牌/牌河/场况）
├── codec.py            # ActionPrototype XOR 解码
├── display.py          # 终端彩色输出
├── human_like.py       # 仿人操作延迟
├── config.py           # 配置管理
├── tiles.py            # 麻将牌编码工具
├── ai/
│   ├── base.py         # AI 基类接口
│   ├── basic.py        # 基础规则 AI
│   ├── shanten.py      # 向听数贪心 AI
│   └── mortal.py       # Mortal AI 封装 (subprocess)
├── mjai_proto.py       # mjai 协议编解码
├── mjai_engine.py      # mjai AI 引擎接口
├── mortal_ai.py        # Mortal 直接调用封装
├── ms/                 # mahjong_soul_api 协议库 (vendored)
│   ├── base.py         # WebSocket 连接管理
│   ├── rpc.py          # RPC 调用
│   ├── protocol_pb2.py # Protobuf 生成代码
│   └── liqi.json       # API 定义
├── config.example.yaml # 配置模板
├── test_connect.py     # 连接测试
└── requirements.txt
```

## 技术细节

- **路由发现**：`/api/clientgate/routes` 获取可用网关节点
- **登录认证**：CN 服邮箱密码登录（HMAC-SHA256）
- **段位匹配**：`startUnifiedMatch(match_sid="1:2")` — 格式 `"{type}:{id}"`，type=1 段位赛，id=2 铜之间四人东
- **对局通信**：game-gateway 路径连接，`ActionPrototype.data` 需 XOR 解码（GameRestore 数据不需要）
- **心跳保活**：lobby 用 `heatbeat(ReqHeatBeat)`，game server 用 `check_network_delay(ReqCommon)`
- **websockets 16.0 兼容**：连接状态检测用 `state == State.OPEN`（`.open` 属性已移除），`ping_interval=None` 禁用内建 ping

## 常见问题

**Q: 匹配一直返回 1306？**  
A: 旧 `matchGame` API 已废弃，确保代码使用 `startUnifiedMatch`。

**Q: 出牌和 AI 决策不一致（服务端摸切）？**  
A: 操作超时，服务端自动出牌。检查 `human_like.py` 中的延迟设置，铜之间操作时限较短。

**Q: Mortal 崩溃 BrokenPipeError？**  
A: 通常是 mjai 协议不同步。常见原因：reach 协议未完成（需要 reach → dahai → reach_accepted 三步），或者碰/杠的 consumed 牌不正确。

**Q: 断线重连后全部摸切？**  
A: GameRestore 重放失败，检查日志中的 WARNING。常见原因：f-string 格式错误被吞掉、XOR 解码误用（GameRestore 数据是明文）。

**Q: `libriichi` 加载失败？**  
A: 确认 Python 版本（3.10-3.12）和系统架构匹配。libriichi 是平台特定的 native 库。

## 免责声明

本项目仅供学习研究，使用机器人违反雀魂服务条款，后果自负。
