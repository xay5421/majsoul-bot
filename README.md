# Majsoul Bot 🀄

雀魂自动打麻将机器人 — 纯协议对接 CN 服，无需浏览器。

纯 Python 实现，直接通过 WebSocket + Protobuf 与雀魂服务器通信，支持接入 Mortal 等强 AI。

## 快速开始

### 1. 安装

```bash
git clone https://github.com/xay5421/majsoul-bot.git
cd majsoul-bot

python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. 配置

```bash
cp config.example.yaml config.yaml
```

编辑 `config.yaml`，填入雀魂 CN 服账号密码：

```yaml
account:
  username: "your_email@example.com"
  password: "your_password"
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

## 配置说明

```yaml
account:
  username: ""          # 雀魂 CN 服账号（邮箱）
  password: ""          # 密码

match:
  mode: "rank"          # rank (段位赛), casual (休闲赛)
  room_type: "4e"       # 4e (四人东), 4s (四人南), 3e (三人东), 3s (三人南)
  level: "copper"       # copper (铜之间), silver (银之间), gold (金之间)

ai:
  type: "basic"         # basic / mortal / mjai_subprocess / mjai_http

run:
  max_games: 1          # 连续打几局 (0 = 不限)
  game_interval: 5      # 每局间隔秒数
  log_level: "INFO"     # DEBUG / INFO / WARNING
```

## AI 后端

支持四种 AI 模式，可插拔切换：

### basic — 内置规则 AI

开箱即用，无需额外依赖。策略简单（打客风→幺九，碰役牌，能和就和），适合跑通流程。

```yaml
ai:
  type: "basic"
```

### mortal — Mortal 本地推理（推荐）

[Mortal](https://github.com/Equim-chan/Mortal) 是目前最强的开源日麻 AI（天凤十段+水平）。

**准备工作：**

1. 安装 PyTorch（CPU 版即可）：
   ```bash
   pip install torch --index-url https://download.pytorch.org/whl/cpu
   pip install numpy
   ```

2. 获取模型文件，放到 `mortal/` 目录下：
   - `mortal.pth` — 模型权重（~5MB），可从 [Akagi](https://github.com/shinkuan/Akagi) 仓库获取
   - `libriichi.so` — Rust 编译的推理引擎（~21MB），需匹配你的系统和 Python 版本
     - Linux x86_64: `libriichi-3.1X-x86_64-unknown-linux-gnu.so`
     - macOS ARM: `libriichi-3.1X-aarch64-apple-darwin.so`
     - Windows: `libriichi-3.1X-x86_64-pc-windows-msvc.pyd`
   - 重命名为 `libriichi.so`（Linux/macOS）或 `libriichi.pyd`（Windows）

3. 配置：
   ```yaml
   ai:
     type: "mortal"
   ```

### mjai_subprocess — mjai 子进程

兼容 [mjai.app](https://mjai.app) 标准格式。通过 stdin/stdout JSON line 通信。

```yaml
ai:
  type: "mjai_subprocess"
  command: "python path/to/your/bot.py"
```

### mjai_http — mjai HTTP 服务

兼容 [Akagi](https://github.com/shinkuan/Akagi) 的 Flask 后端。通过 HTTP POST 通信。

```yaml
ai:
  type: "mjai_http"
  url: "http://127.0.0.1:7331"
```

## 项目结构

```
majsoul-bot/
├── bot.py              # 主入口：登录 → 匹配 → 打牌循环
├── client.py           # 雀魂 WebSocket 客户端（连接/登录/匹配）
├── game_state.py       # 游戏状态机（手牌/牌河/场况）
├── mjai_proto.py       # mjai 协议编解码（牌编码转换/事件构造）
├── mjai_engine.py      # mjai AI 引擎接口（子进程/HTTP/Mortal）
├── mortal_ai.py        # Mortal AI 本地推理封装
├── ai/
│   ├── base.py         # AI 基类（可插拔接口）
│   └── basic.py        # 基础规则 AI
├── tiles.py            # 麻将牌编码/解码工具
├── config.py           # 配置管理
├── config.example.yaml # 配置模板
├── test_connect.py     # 连接测试脚本
├── mortal/             # Mortal 模型文件（不进 git）
│   ├── mortal.pth      # 模型权重
│   ├── libriichi.so    # Rust 推理引擎
│   ├── model.py        # 网络结构定义
│   └── bot_mortal.py   # Mortal Bot 封装
├── ms/                 # mahjong_soul_api 协议库（vendored）
└── requirements.txt    # 依赖
```

## 技术细节

- **协议层**：直接 WebSocket 连接雀魂 CN 服，Protobuf 序列化，无需浏览器或 MITM
- **路由发现**：通过 `/api/clientgate/routes` API 获取可用路由节点
- **登录**：CN 服支持账号密码直接登录（HMAC-SHA256 签名）
- **对局**：完整实现匹配→认证→对局→出牌→吃碰杠→立直→和牌→下一局循环
- **AI 接口**：统一 mjai 协议标准，任何兼容 mjai 的 AI 都可以接入

## 进度

- [x] 连接/登录/匹配
- [x] 完整对局循环
- [x] 基础规则 AI
- [x] mjai 协议适配（子进程/HTTP/Mortal）
- [x] 四人麻将
- [ ] 三人麻将
- [ ] 对局日志记录
- [ ] 断线重连

## 免责声明

本项目仅供学习研究，使用机器人违反雀魂服务条款，后果自负。
