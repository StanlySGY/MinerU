# VLM 服务部署

VLM 镜像必须与目标硬件匹配，例如 NVIDIA vLLM 或 Ascend vLLM。请先在 VLM 主机导入 `mineru-server` 镜像归档，并准备模型目录。

```bash
cd docker/deploy/vlm
cp .env.example .env
vim .env
```

至少设置：

```dotenv
MINERU_CODE_IMAGE=mineru-code:v1.0
MINERU_VLM_IMAGE=mineru-server:v1.0
VLM_DEVICE_TYPE=ascend
VLM_PORT=30000
ASCEND_DEVICE=0
VLM_ENGINE=auto
VLM_MODEL_HOST_PATH=/data/models/MinerU2.5-Pro-2604-1.2B
VLM_MODEL_PATH=/models/mineru
```

`VLM_MODEL_HOST_PATH` 是宿主机路径，`VLM_MODEL_PATH` 是容器内路径。Compose 会将前者只读挂载到后者；模型目录必须在离线主机上已经存在。

启动和检查：

```bash
docker compose -f compose.yaml -f compose.ascend.yaml --env-file .env up -d
docker compose --env-file .env ps
docker compose --env-file .env logs -f
curl http://localhost:30000/v1/models
```

NVIDIA 主机将上面的 `compose.ascend.yaml` 替换为 `compose.nvidia.yaml`。Ascend override 映射 `/dev/davinci*` 和宿主机 Ascend 驱动目录；NVIDIA override 使用 Compose 的 `gpus: all`。

修改代码版本时，导入新的代码镜像并更新 `MINERU_CODE_IMAGE`，然后重新执行 `up -d`。修改模型或硬件配置后同样需要重启 VLM。
