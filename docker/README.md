# MinerU Docker 部署

## 文件结构

```
docker/
├── compose.yaml           # VLM Server（NPU/GPU 推理服务）
├── compose-api.yaml       # API Server（支持三种 backend）
├── compose-router.yaml    # Router 负载均衡（可选）
├── .env                   # 统一配置文件
├── start.sh               # 一键启动脚本
└── china/                 # 各硬件平台 Dockerfile
```

## 三种 Backend

| Backend | 说明 | 需要 VLM Server |
|---|---|---|
| `pipeline` | 纯本地小模型 | 不需要 |
| `vlm-http-client` | 远程 VLM | 需要 |
| `hybrid-http-client` | 本地小模型 + 远程 VLM | 需要 |

## 快速部署

### 1. NPU/GPU 机器（VLM Server）

```bash
cd docker
vim .env                    # 配置模型来源等
docker compose up -d        # 启动 VLM Server
```

### 2. CPU/GPU 机器（API Server）

```bash
cd docker
vim .env                    # 配置 VLM Server IP
docker compose -f compose-api.yaml up -d
```

### 3. 可选：Router 负载均衡

```bash
docker compose -f compose-router.yaml up -d
```

## 测试

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
