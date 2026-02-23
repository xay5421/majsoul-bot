# Majsoul Bot 🀄

雀魂自动打麻将机器人 — 纯协议对接，无需浏览器。

## 架构

```
majsoul-bot/
├── bot.py              # 主入口：登录 → 匹配 → 打牌循环
├── client.py           # 雀魂 WebSocket 客户端（连接/登录/匹配）
├── game_state.py       # 游戏状态机（手牌/牌河/场况）
├── mjai_proto.py       # mjai 协议编解码（牌编码转换/事件构造）
├── mjai_engine.py      # mjai AI 引擎接口（子进程/HTTP/文件）
├── ai/
│   ├── base.py         # AI 基类
│   └── basic.py        # 基础规则 AI（先跑通流程）
├── tiles.py            # 麻将牌编码/解码工具
├── config.py           # 配置管理
├── config.example.yaml # 配置模板
├── requirements.txt    # 依赖
└── ms/                 # mahjong_soul_api 协议库（vendored）
```

## 技术路线

1. **协议层**：直接 WebSocket 连接雀魂 CN 服务器，Protobuf 通信
2. **状态层**：维护完整游戏状态（手牌、弃牌、副露、场风、点数等）
3. **AI 层**：可插拔 AI 后端，支持 mjai 协议标准

## AI 后端

支持三种 AI 接入方式：

| 模式 | 配置 | 说明 |
|------|------|------|
| `basic` | 内置 | 基础规则 AI，能跑通流程 |
| `mjai_subprocess` | `command` | 子进程 stdin/stdout JSON，兼容 mjai.app 标准 |
| `mjai_http` | `url` | HTTP POST，兼容 Akagi/Mortal Flask 后端 |

### 接入 Mortal（最强开源日麻 AI）

1. 获取 Mortal 模型权重
2. 启动 Mortal mjai 服务
3. 配置 `config.yaml`:

```yaml
ai:
  type: "mjai_http"
  url: "http://127.0.0.1:7331"
```

## 快速开始

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp config.example.yaml config.yaml
# 编辑 config.yaml 填入账号密码

python bot.py
```

## 支持模式

- [x] 四人麻将（四麻东/南）
- [ ] 三人麻将（三麻东/南）

## CN 服登录

CN 服支持账号密码直接登录，无需浏览器。

## 免责声明

本项目仅供学习研究，使用机器人违反雀魂服务条款，风险自负。
