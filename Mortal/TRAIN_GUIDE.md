# Mortal 训练指南 (Windows + RTX 5070)

## 硬件要求
- GPU: RTX 5070 (12GB VRAM)
- 推荐配置: `conv_channels=96, num_blocks=12` (~800万参数)
- 满血 `192×40` 需要 40GB+ VRAM，5070 跑不了

## 一、环境搭建

### 1. 安装 Rust
```powershell
# 下载 rustup-init.exe: https://rustup.rs/
rustup-init.exe
# 重启终端后验证
rustc --version
cargo --version
```

### 2. 安装 Python 环境 (推荐 miniconda)
```powershell
# 下载: https://docs.conda.io/en/latest/miniconda.html
# 创建环境
conda create -n mortal python=3.11
conda activate mortal
```

### 3. 安装 PyTorch (CUDA)
```powershell
# 根据你的 CUDA 版本选择，5070 用 CUDA 12.x
pip install torch --index-url https://download.pytorch.org/whl/cu124
# 验证
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name())"
```

### 4. 安装其他 Python 依赖
```powershell
pip install toml numpy tqdm tensorboard
```

### 5. 编译 libriichi
```powershell
cd Mortal
cargo build -p libriichi --lib --release
# Windows: 复制 .pyd 文件
copy target\release\riichi.dll mortal\libriichi.pyd
# 验证
cd mortal
python -c "import libriichi; print('OK')"
```

## 二、准备数据集

Mortal 需要 **mjai 格式** 的天凤牌谱 (.json.gz)。

### 方案 A: 下载已有数据集 (推荐)

天凤牌谱归档项目:
- https://github.com/ApricotSoda/tenhou-log (有 json 格式)
- 或自行搜索 "tenhou log mjai format"

每个 .json.gz 文件是一局牌谱，格式如下:
```json
{"type":"start_game","names":["player1","player2","player3","player4"],"rule":{"disp":"...","aka":1}}
{"type":"start_kyoku","bakaze":"E","dora_marker":"2p","kyoku":1,"honba":0,"kyotaku":0,"oya":0,"scores":[25000,25000,25000,25000],"tehais":[["1m","3m",...],["?","?",...],["?","?",...],["?","?",...,]]}
{"type":"tsumo","actor":0,"pai":"5m"}
{"type":"dahai","actor":0,"pai":"1m","tsumogiri":false}
...
{"type":"end_kyoku"}
{"type":"end_game"}
```

### 方案 B: 自行转换天凤牌谱

1. 下载天凤原始牌谱 (xml 格式):
   - `https://tenhou.net/sc/raw/dat/scb{YYYYMMDD}HH.log.gz`
   - `sca` = 一般卓, `scb` = 上级卓/特上卓/凤凰卓

2. 用 haitoani 或 convlog 转换:
   - https://github.com/Equim-chan/convlog (Rust, tenhou6 → mjai)
   - 或写 Python 脚本解析 xml → mjai json

### 数据筛选建议
- **只用高段位玩家的数据** — 凤凰卓 > 特上卓 > 上级卓
- **四人南 (四麻)** — Mortal 训练用的规则
- 数据量: 最少 10 万局，理想 50 万局以上

### 数据目录结构
```
D:\mortal-data\
├── dataset\
│   ├── 2024\
│   │   ├── game001.json.gz
│   │   ├── game002.json.gz
│   │   └── ...
│   └── 2025\
│       └── ...
└── player_names.txt    (可选: 高段位玩家名单，每行一个)
```

## 三、训练配置

### 1. 创建训练配置文件

将 `mortal/config.toml` 替换为以下内容:

```toml
[control]
version = 4
online = false

state_file = 'mortal.pth'
best_state_file = 'best.pth'
tensorboard_dir = 'tb_logs'

device = 'cuda:0'
enable_cudnn_benchmark = true
enable_amp = true          # 混合精度，节省 VRAM
enable_compile = false     # Windows 上可能有问题，先关

batch_size = 256           # 12GB VRAM，256 应该够
opt_step_every = 2         # 梯度累积，等效 batch=512

save_every = 500
test_every = 10000
submit_every = 500

[test_play]
games = 400
log_dir = 'test_play'

[dataset]
globs = ['D:/mortal-data/dataset/**/*.json.gz']
file_index = 'file_index.pth'
file_batch_size = 20
reserve_ratio = 0.0
num_workers = 2            # Windows 上不要太多
player_names_files = []    # 留空=学所有玩家; 或 ['D:/mortal-data/player_names.txt']
num_epochs = 1
enable_augmentation = false
augmented_first = false

[env]
gamma = 1
pts = [6.0, 4.0, 2.0, 0.0]

# ===== 中等模型配置 (适合 12GB VRAM) =====
[resnet]
conv_channels = 96
num_blocks = 12

[cql]
min_q_weight = 5

[aux]
next_rank_weight = 0.2

[freeze_bn]
mortal = false

[optim]
eps = 1e-8
betas = [0.9, 0.999]
weight_decay = 0.1
max_grad_norm = 0

[optim.scheduler]
peak = 5e-5
final = 1e-5
warm_up_steps = 1000
max_steps = 200000         # 根据数据量调整

# GRP 配置 (第一步训练需要)
[grp]
state_file = 'grp.pth'

[grp.network]
hidden_size = 64
num_layers = 2

[grp.control]
device = 'cuda:0'
enable_cudnn_benchmark = true
tensorboard_dir = 'grp_logs'
batch_size = 512
save_every = 2000
val_steps = 400

[grp.dataset]
train_globs = ['D:/mortal-data/dataset/**/*.json.gz']
val_globs = []             # 可以留空或分一部分做验证
file_index = 'grp_file_index.pth'
file_batch_size = 50

[grp.optim]
lr = 1e-5

# Baseline (test_play 的对手，用小模型自身)
[baseline.train]
device = 'cuda:0'
enable_compile = false
state_file = 'mortal.pth'

[baseline.test]
device = 'cuda:0'
enable_compile = false
state_file = 'mortal.pth'
```

## 四、训练步骤

### 第一步: 训练 GRP (Group Rating Predictor)

GRP 用于在训练中估算玩家的相对实力。
```powershell
cd mortal
python train_grp.py
```
这个训练较快 (几十分钟到几小时)，会生成 `grp.pth`。

### 第二步: 训练主模型

```powershell
cd mortal
python train.py
```

训练日志通过 TensorBoard 查看:
```powershell
tensorboard --logdir tb_logs
# 浏览器打开 http://localhost:6006
```

### 训练时间估计
- 96×12 模型 + 10 万局数据: ~1-2 天
- 96×12 模型 + 50 万局数据: ~3-7 天
- 取决于 5070 的实际性能

### VRAM 不够怎么办?
1. 减小 `batch_size` (128 或 64)
2. 增大 `opt_step_every` (4 或 8) 保持等效 batch
3. 减小模型: `conv_channels=64, num_blocks=8`
4. 确保 `enable_amp = true`

## 五、使用训练好的模型

训练完成后，把 `mortal.pth` (或 `best.pth`) 拷贝到 bot 的 Mortal 目录:
```powershell
copy mortal.pth ..\..\majsoul-bot\Mortal\mortal\mortal.pth
```

同时修改 `config.toml`:
```toml
[resnet]
conv_channels = 96
num_blocks = 12
```

这个配置必须和训练时一致，否则加载权重会报错。

## 六、注意事项

1. **数据质量 > 数据数量** — 100 万局低段位数据不如 10 万局凤凰卓数据
2. **训练不要中断** — 中断后从 `state_file` 继续（自动 resume）
3. **观察 TensorBoard** — `dqn_loss` 持续下降说明在学习，`test_play/avg_rank` 越低越好
4. **过拟合风险** — 如果 train loss 降但 test_play 变差，需要更多数据或更小模型
5. **Windows 路径** — config.toml 里用 `/` 或 `\\`，不要用单 `\`
