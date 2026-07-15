# 基础镜像

## 镜像分层架构

```
mineru-env:v1.0     ← 环境镜像（依赖包，不常变，构建一次长期使用）
  │
  └── mineru-full:v1.0  ← 代码镜像（MinerU 代码，常更新）
```

### 为什么要分离？

| 镜像 | 大小 | 更新频率 | 说明 |
|---|---|---|---|
| `mineru-env` | ~2-3 GB | 很少 | torch、paddleocr 等依赖包 |
| `mineru-full` | ~50-100 MB | 频繁 | MinerU 代码 |

离线部署时：
- 首次：传输两个镜像
- 更新代码：只传 `mineru-full`（小）
- 更新依赖：才需要重新传 `mineru-env`（大）

## 文件说明

| 文件 | 说明 |
|---|---|
| `Dockerfile.env` | 环境镜像（依赖包） |
| `Dockerfile.code` | 代码镜像（MinerU 代码） |
| `Dockerfile.full` | 一体镜像（兼容旧方式） |
| `compose.yaml` | VLM Server 配置 |
| `build.sh` | 构建与导出脚本 |

## 在有网机器上构建

```bash
cd docker/base

# 构建全部镜像
./build.sh all

# 导出为 tar.gz
./build.sh export
```

导出的文件在 `export/` 目录：
```
export/
├── mineru-env-v1.0.tar.gz    # 环境镜像（~2-3 GB）
└── mineru-full-v1.0.tar.gz   # 代码镜像（~50-100 MB）
```

## 拷贝到离线机器

```bash
# 拷贝 export 目录到离线机器
scp -r export/ user@offline-server:/path/to/docker/base/

# 在离线机器上导入
cd docker/base
./build.sh import export/
```

## 更新代码（离线机器）

```bash
# 1. 在有网机器：更新代码后重新构建
cd docker/base
./build.sh code
./build.sh export-code

# 2. 拷贝 mineru-full-v1.0.tar.gz 到离线机器

# 3. 在离线机器：只导入代码镜像
./build.sh import export/
```

## 支持的 Backend

| Backend | 说明 |
|---|---|
| `pipeline` | 纯本地小模型 |
| `vlm-http-client` | 远程 VLM |
| `hybrid-http-client` | 本地小模型 + 远程 VLM |
