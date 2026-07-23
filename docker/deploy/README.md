# 分机部署指南

分机部署包含三个服务：

```text
客户端 -> Router -> API -> VLM
```

API 和 Router 使用 `mineru-env` + `mineru-code`；VLM 使用目标 GPU/NPU 平台对应的 `mineru-server` 镜像。三个服务可以运行在不同主机上。

## 镜像和模型

先在有网机器构建并导出：

```bash
cd docker/base
./build.sh all
./build.sh export
```

在 API 和 Router 主机导入 `mineru-env-*.tar.gz`、`mineru-code-*.tar.gz`；在 VLM 主机导入平台对应的 `mineru-server` 归档。VLM 模型文件也必须复制到 VLM 主机，并在 `vlm/.env` 中配置宿主机路径。离线环境不会自动下载模型。若现场保留了 `docker/base/build.sh`，可执行 `cd docker/base && ./build.sh import /path/to/export`；否则直接执行两个 `docker load` 命令。

## 部署顺序

### 1. VLM

```bash
cd docker/deploy/vlm
cp .env.example .env
vim .env
docker compose -f compose.yaml -f compose.ascend.yaml --env-file .env up -d
curl http://localhost:30000/v1/models
```

NVIDIA 主机使用 `compose.nvidia.yaml` 替换 `compose.ascend.yaml`。

### 2. API

```bash
cd docker/deploy/api
cp .env.example .env
vim .env
docker compose --env-file .env up -d
curl http://localhost:18000/health
```

### 3. Router

```bash
cd docker/deploy/router
cp .env.example .env
vim .env
docker compose --env-file .env up -d
curl http://localhost:8002/health
```

每个 Compose 项目都会运行一次 `mineru-code-sync`，把同版本代码复制到共享卷。API/Router 的 `.env` 必须使用与已导入归档匹配的 `MINERU_ENV_IMAGE`、`MINERU_CODE_IMAGE`。

## 更新代码

有网机器构建并导出新代码镜像：

```bash
cd docker/base
MINERU_CODE_TAG=v3.4.3 ./build.sh code
MINERU_CODE_TAG=v3.4.3 ./build.sh export-code
```

将代码归档分别复制到 API、Router、VLM 主机并执行：

```bash
cd docker/base
./build.sh import /path/to/export
# 修改对应 .env：MINERU_CODE_IMAGE=mineru-code:v3.4.3
docker compose --env-file .env up -d
```

只有依赖发生变化时才需要更新 `mineru-env`。模型版本变化时，需要单独替换 VLM 模型目录并重启 VLM。
