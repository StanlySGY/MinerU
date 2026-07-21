# VLM 推理服务部署

## 说明

运行 VLM 大模型（MinerU2.5-Pro），提供文档理解能力。需要 NPU/CPU 卡。

## 部署步骤

```bash
cd vlm

# 1. 配置
cp .env.example .env
vim .env

# 2. 启动
docker compose --env-file .env up -d

# 3. 验证
curl -s http://localhost:30000/v1/models
```

## 必须修改的配置

| 配置 | 说明 | 默认值 |
|---|---|---|
| `VLM_PORT` | VLM 对外端口 | `30000` |
| `ASCEND_DEVICE` | NPU 卡号 | `0` |
| `VLM_MODEL_PATH` | 模型权重路径（留空自动下载） | 空 |

## 停止服务

```bash
docker compose --env-file .env down
```
