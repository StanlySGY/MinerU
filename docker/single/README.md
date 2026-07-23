# 单机部署

`single/` 在同一台机器启动一个 API 和一个 VLM。API 使用 CPU/小模型依赖，VLM 使用已经构建好的 GPU/NPU 推理镜像。

## 准备镜像和模型

```bash
cd docker/base
./build.sh import /path/to/export

cd ../single
mkdir -p models
# 将 VLM 模型复制到 models/，或在 .env 中设置绝对路径
cp env.example .env
```

至少确认 `.env` 中这些值正确：

```dotenv
MINERU_ENV_IMAGE=mineru-env:v1.0
MINERU_CODE_IMAGE=mineru-code:v1.0
MINERU_VLM_IMAGE=mineru-server:v1.0
VLM_DEVICE_TYPE=ascend
VLM_MODEL_HOST_PATH=./models
VLM_MODEL_PATH=/models/mineru
```

## 启动

```bash
./start.sh vlm
./start.sh api
# 或者同机一次启动
./start.sh all
```

`compose-vlm.yaml` 和 `compose-api.yaml` 都会先运行 `mineru-code-sync`，把代码镜像内容复制到共享卷。VLM 模型不会在离线环境自动下载。

`start.sh` 会根据 `VLM_DEVICE_TYPE` 自动叠加 `compose-vlm.ascend.yaml` 或 `compose-vlm.nvidia.yaml`，分别映射 Ascend 设备或 NVIDIA GPU。

```bash
./start.sh status
./start.sh logs
./start.sh stop
```

API 地址默认为 `http://localhost:18000`，VLM 地址默认为 `http://localhost:30000`。

API 为远程 VLM backend 启用了 `--allow-public-http-client`。生产环境应限制 API 端口的访问来源，避免把可指定远程地址的接口直接暴露到不可信网络。

## Backend

```bash
# 纯 Pipeline，不需要远程 VLM，但需要已准备 Pipeline 模型
curl -X POST http://localhost:18000/file_parse -F "files=@test.pdf" -F "backend=pipeline"

# 远程 VLM
curl -X POST http://localhost:18000/file_parse -F "files=@test.pdf" -F "backend=vlm-http-client"

# 本地小模型 + 远程 VLM
curl -X POST http://localhost:18000/file_parse -F "files=@test.pdf" -F "backend=hybrid-http-client"
```

## 更新代码

```bash
# 有网机器构建并导出新代码镜像
cd docker/base
MINERU_CODE_TAG=v3.4.3 ./build.sh code
MINERU_CODE_TAG=v3.4.3 ./build.sh export-code

# 离线机器导入，并修改 docker/single/.env
./build.sh import /path/to/export
# MINERU_CODE_IMAGE=mineru-code:v3.4.3
cd ../single
./start.sh stop
./start.sh all
```

只有 Python 依赖发生变化时才需要更新环境镜像。
