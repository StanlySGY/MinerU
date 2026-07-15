# 基础镜像

## Dockerfile.full

基于 `python:3.11-slim`，安装 `mineru[pipeline]`，支持所有 backend。

### 支持的 Backend

| Backend | 说明 |
|---|---|
| `pipeline` | 纯本地小模型 |
| `vlm-http-client` | 远程 VLM |
| `hybrid-http-client` | 本地小模型 + 远程 VLM |

### 构建

```bash
cd base
DOCKER_BUILDKIT=0 docker build -t mineru-full:v1.0 -f Dockerfile.full .
```

### 依赖

- `mineru[pipeline]>=3.0.0`（包含 torch、paddleocr 等）
- `six`（Python 2/3 兼容库）

### 用途

所有部署模式（单机、多卡）都使用此镜像作为 API Server 和 Router 的基础镜像。
