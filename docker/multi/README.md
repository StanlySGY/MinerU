# Multi 离线交付包

本目录用于把 MinerU API、Router、Pipeline 和 Hybrid HTTP Client 交付到离线的麒麟 ARM/NPU 现场。

实际流程分三处：

1. 联网 WSL（x86_64）：修改代码，下载并打包 Pipeline 模型；
2. 联网 ARM/NPU 服务器：从 GitHub 拉代码，构建并验证 ARM64 `mineru-env` 和 `mineru-code` 镜像；
3. 离线麒麟 NPU 现场：导入 ARM64 镜像、解压模型、启动并测试。

完整背景见 `交接上下文.md`，逐步操作见 `现场部署指南.md`。

## WSL 下载并打包 Pipeline 模型

当前 WSL 已安装 `uv`。在本目录执行一条命令：

```bash
./prepare-pipeline-models.sh
```

默认从 ModelScope 下载完整的 `OpenDataLab/PDF-Extract-Kit-1.0`。如果 ModelScope 不通：

```bash
./prepare-pipeline-models.sh huggingface
```

输出文件：

```text
artifacts/mineru-pipeline-models-3.4.2.tar.gz
artifacts/mineru-pipeline-models-3.4.2.tar.gz.sha256
```

脚本会检查七组 Pipeline 必需模型，并使用 `tar --dereference` 打包，避免符号链接在离线现场失效。

准备好 `artifacts/` 和 `images/` 后，可把整个交付目录打成一个文件：

```bash
cd ..
tar -czf mineru-offline-multi.tar.gz -C . multi
sha256sum mineru-offline-multi.tar.gz > mineru-offline-multi.tar.gz.sha256
```

## 最终交付目录

把 ARM 服务器构建的代码镜像和现场适配的环境镜像放进 `images/`：

```text
docker/multi/
├── artifacts/
│   ├── mineru-pipeline-models-3.4.2.tar.gz
│   └── mineru-pipeline-models-3.4.2.tar.gz.sha256
├── images/
│   ├── mineru-env-npu-v1.0.tar.gz
│   └── mineru-code-v3.4.2.tar.gz
├── compose-multi.yaml
├── compose-multi.npu.yaml
├── compose-multi.nvidia.yaml
├── env.multi.example
├── mineru-ops-agent.py
├── batch-router-diagnose.py
├── BATCH_DIAGNOSIS.md
├── OPS_CONSOLE.md
├── mineru.pipeline.json
├── prepare-pipeline-models.sh
├── start-multi.sh
├── 交接上下文.md
└── 现场部署指南.md
```

`mineru-env` 必须是适配现场 ARM64、麒麟、CANN/torch_npu 的环境镜像；不能使用 WSL 的 x86_64 镜像代替。

## 现场执行顺序

```bash
cd docker/multi

cd artifacts
sha256sum -c mineru-pipeline-models-3.4.2.tar.gz.sha256
cd ..
mkdir -p models/PDF-Extract-Kit-1.0
tar -xzf artifacts/mineru-pipeline-models-3.4.2.tar.gz \
  -C models/PDF-Extract-Kit-1.0

cp env.multi.example env.multi
# 编辑 VLM 地址、镜像标签和设备模式
./start-multi.sh import-images
./start-multi.sh check
./start-multi.sh start
./start-multi.sh test /data/test.pdf
```

`start` 会同时启动运维控制台，默认地址为 `http://服务器IP:19000`。控制台读取同一份 Compose 和 env，显示 Router/API/VLM 健康状态、任务页级进度，并提供文件夹批量诊断和报告导出。详细配置见 [OPS_CONSOLE.md](./OPS_CONSOLE.md)。

`stop` 只停止 Router/API，保留控制台用于检查和恢复；`stop-all` 才会停止全部容器和宿主机控制代理。

`check` 会检查镜像、模型目录、JSON 和容器挂载；CPU 模式会实际导入 `torch`，防止误用依赖 Ascend 驱动的 NPU 镜像；`MINERU_DEVICE_MODE=npu` 时还会检查 NPU 设备节点和 `torch_npu`。只有填写了 `VLM_1_IP` 才检查外部 VLM 连通性。`test` 会测试 `pipeline`，填写了 `VLM_1_IP` 时再测试 `hybrid-http-client`。

只部署 Router + API 的纯 CPU 主机（x86_64 或 ARM64），在 `env.multi` 中设置 `MINERU_DEVICE_MODE=cpu`、把 `MINERU_ENV_IMAGE` 换成同架构的 CPU 版镜像、`VLM_1_IP` 留空即可，不需要 `/dev/davinci*`。
