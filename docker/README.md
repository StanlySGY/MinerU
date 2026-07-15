# MinerU Docker 部署

## 目录结构

```
docker/
├── base/                   # 基础镜像
│   ├── Dockerfile.full     # 全功能镜像（pipeline + vlm + hybrid）
│   └── README.md           # 基础镜像说明
│
├── single/                 # 单机部署（1 API + 1 VLM）
│   ├── compose-api.yaml    # API Server 配置
│   ├── env.example         # 配置模板
│   ├── start.sh            # 启动脚本
│   └── README.md           # 单机部署文档
│
├── multi/                  # 多卡部署（Router + 多组 API+VLM）
│   ├── compose-multi.yaml  # Router + 多组 API+VLM 配置
│   ├── env.multi.example   # 配置模板
│   ├── start-multi.sh      # 启动脚本
│   └── README.md           # 多卡部署文档
│
├── china/                  # 各硬件平台 Dockerfile
│   ├── Dockerfile          # 国内版（NPU/GPU）
│   ├── Dockerfile.cpu-arm  # CPU/ARM 版
│   ├── npu.Dockerfile      # 昇腾 NPU 版
│   └── ...
│
└── global/                 # 海外版 Dockerfile
```

## 三种 Backend

| Backend | 说明 | 需要 VLM Server |
|---|---|---|
| `pipeline` | 纯本地小模型 | 不需要 |
| `vlm-http-client` | 远程 VLM | 需要 |
| `hybrid-http-client` | 本地小模型 + 远程 VLM | 需要 |

## 选择部署模式

| 场景 | 推荐模式 | 说明 |
|---|---|---|
| 开发测试 | `single/` | 一个 API + 一个 VLM |
| 单卡生产 | `single/` | 一个 API + 一个 VLM |
| 多卡生产 | `multi/` | Router + 多组 API+VLM |
| 跨机器部署 | `multi/` | Router 在一台，API+VLM 分布在多台 |

## 快速开始

### 单机部署

```bash
cd docker/single
cp env.example .env
vim .env                    # 配置 VLM 服务器 IP
./start.sh build
./start.sh all
```

### 多卡部署

```bash
cd docker/multi
cp env.multi.example env.multi
vim env.multi               # 配置端口和 IP
./start-multi.sh build
./start-multi.sh start
```

## 测试 Backend

```bash
# pipeline（纯本地）
curl -X POST http://localhost:18000/file_parse \
  -F "files=@test.pdf" -F "backend=pipeline"

# vlm-http-client（远程 VLM）
curl -X POST http://localhost:18000/file_parse \
  -F "files=@test.pdf" -F "backend=vlm-http-client"

# hybrid-http-client（本地 + 远程）
curl -X POST http://localhost:18000/file_parse \
  -F "files=@test.pdf" -F "backend=hybrid-http-client"
```
