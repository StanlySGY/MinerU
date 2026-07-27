# 基础镜像与离线更新

本目录提供两个独立镜像：

```text
mineru-env:v1.0   系统库、Python 运行时和 MinerU 依赖
mineru-code:v1.0  只包含 MinerU 源码，基于 BusyBox，体积很小
```

API、Router 和 VLM 容器使用环境镜像运行，并在启动时由 `mineru-code` 容器把源码同步到名为 `mineru-code` 的只读卷。因此更新代码只需要导入新的代码镜像，不需要重新传输环境镜像。

## 有网机器构建

```bash
cd docker/base

# 首次构建或依赖发生变化时执行
MINERU_ENV_TAG=v1.0 ./build.sh env

# 每次代码发布执行。建议每个版本使用新的标签
MINERU_CODE_TAG=v3.4.2 ./build.sh code

# 构建两个镜像
./build.sh all
```

`Dockerfile.env` 默认使用 `mineru==3.4.2` 解析 Pipeline 依赖。升级依赖时显式传入 `MINERU_DEPENDENCY_VERSION` 并同时升级环境标签。

### Ascend NPU 环境镜像

`Dockerfile.env` 是 CPU 环境，不包含 `torch_npu`。Ascend Pipeline 必须使用
`Dockerfile.env.npu`，并传入与离线现场 CANN/驱动版本匹配的官方 ARM64
Ascend PyTorch 基础镜像：

```bash
cd docker/base

MINERU_NPU_BASE_IMAGE='<官方 Ascend PyTorch ARM64 镜像>' \
MINERU_ENV_TAG=npu-v1.0 \
MINERU_DEPENDENCY_VERSION=3.4.2 \
./build.sh env-npu
```

基础镜像必须已经安装 `torch_npu`，并且 PyTorch 版本满足 `>=2.6,<3`。
ARM CPU 构建机没有 `libascend_hal.so`，因此构建阶段使用
`TORCH_DEVICE_BACKEND_AUTOLOAD=0` 检查 `torch/torch-npu/torchvision` 的包版本，
不直接加载 NPU 硬件扩展。`torch_npu` 导入、NPU 可用性和张量分配必须在
离线 NPU 现场通过 `docker/multi/start-multi.sh check` 验证。

同时构建环境镜像和小型代码镜像：

```bash
MINERU_NPU_BASE_IMAGE='<官方 Ascend PyTorch ARM64 镜像>' \
MINERU_ENV_TAG=npu-v1.0 \
MINERU_CODE_TAG=v3.4.2 \
./build.sh all-npu
```

不要使用 x86_64 服务器直接构建现场 ARM64 镜像。可以在联网 ARM64 CPU
服务器构建和导出，但必须在离线 NPU 现场完成真机验证；基础镜像必须依据
华为版本配套表选择，不能只按“最新版本”选择。

`build.sh code` 会临时组装只包含 `mineru/`、`pyproject.toml` 和代码 Dockerfile 的构建上下文，不会把仓库中的测试资料或其他镜像层发送给 Docker。

脚本默认设置 `DOCKER_BUILDKIT=0`，兼容现场旧版 Docker。若服务器已确认支持 BuildKit，可在命令前设置 `DOCKER_BUILDKIT=1` 覆盖默认值。

## 导出和导入

```bash
# 导出环境镜像和代码镜像
MINERU_ENV_TAG=v1.0 MINERU_CODE_TAG=v3.4.2 ./build.sh export

# 离线机导入目录中的全部 MinerU 镜像
./build.sh import /path/to/export
```

导出文件类似：

```text
mineru-env-v1.0.tar.gz       # 只在首次部署或依赖升级时传输
mineru-code-v3.4.2.tar.gz    # 每次代码更新时传输
```

`docker save` 的代码归档只包含 BusyBox 和源码层，不包含环境镜像的 Python/Torch 层。

NPU 环境镜像使用单独标签时，相应导出命令为：

```bash
MINERU_ENV_TAG=npu-v1.0 MINERU_CODE_TAG=v3.4.2 ./build.sh export
sha256sum export/*.tar.gz > export/SHA256SUMS
```

## 离线更新代码

有网机器：

```bash
cd docker/base
MINERU_CODE_TAG=v3.4.3 ./build.sh code
MINERU_CODE_TAG=v3.4.3 ./build.sh export-code
```

离线机器：

```bash
./build.sh import /path/to/export
# 在对应部署目录的 .env 中设置：
# MINERU_CODE_IMAGE=mineru-code:v3.4.3
docker compose --env-file .env up -d
```

Compose 会重新运行 `mineru-code-sync`，清空旧代码卷后复制新代码，再启动 API/Router/VLM。

## 依赖更新

如果 `pyproject.toml` 的依赖或 Python 版本发生变化，必须重新构建并导出环境镜像：

```bash
MINERU_ENV_TAG=v1.1 MINERU_DEPENDENCY_VERSION=3.4.3 ./build.sh env
MINERU_ENV_TAG=v1.1 ./build.sh export-env
```

同时更新部署目录中的 `MINERU_ENV_IMAGE`。代码镜像不能提供新的 Python 依赖。
