# MinerU Docker 部署

## 镜像职责

```text
mineru-env:<tag>   系统依赖、Python 依赖和运行时
mineru-code:<tag>  MinerU 源码，独立的小镜像
mineru-server:<tag> VLM 推理镜像，按 GPU/NPU 平台单独构建
```

API 和 Router 使用 `mineru-env`，通过共享卷加载 `mineru-code`。VLM 使用平台对应的 `mineru-server`，同样可以挂载代码卷。环境镜像和 VLM 镜像包含硬件/依赖绑定，不能用代码镜像替代。

## 部署模式

| 目录 | 作用 |
|---|---|
| `base/` | 构建、导出和导入环境/代码镜像 |
| `single/` | 同一台机器上的一个 API 和一个 VLM |
| `multi/` | 三个 API + Router，VLM 地址由配置指定 |
| `deploy/` | API、Router、VLM 分机部署 |

## 首次离线部署

```bash
cd docker/base
./build.sh all
./build.sh export
```

将 `export/`、部署目录和模型文件复制到离线环境，然后：

```bash
./build.sh import export/
```

在部署目录复制 `.env.example` 并设置 `MINERU_ENV_IMAGE`、`MINERU_CODE_IMAGE`。使用 `local` 模型源时，必须把模型文件预置到 VLM 的 `VLM_MODEL_HOST_PATH`；离线环境不会自动下载模型。

## 只更新代码

```bash
# 有网机器
cd docker/base
MINERU_CODE_TAG=v3.4.3 ./build.sh code
MINERU_CODE_TAG=v3.4.3 ./build.sh export-code

# 离线机器
./build.sh import /path/to/export
# 修改部署目录 .env：MINERU_CODE_IMAGE=mineru-code:v3.4.3
docker compose --env-file .env up -d
```

只有依赖变化时才需要更新 `mineru-env`。模型文件也应作为独立的离线部署资产管理。
