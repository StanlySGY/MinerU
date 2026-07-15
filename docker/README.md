# MinerU Docker 部署

## 目录结构

```
docker/
├── base/                   # 基础镜像与构建工具
│   ├── Dockerfile.env      # 环境镜像（依赖包）
│   ├── Dockerfile.code     # 代码镜像（MinerU 代码）
│   ├── Dockerfile.full     # 一体镜像（兼容旧方式）
│   ├── build.sh            # 构建与导出脚本
│   ├── compose.yaml        # VLM Server 配置
│   └── README.md           # 基础镜像说明
│
├── single/                 # 单机部署（1 API + 1 VLM）
│   ├── compose-api.yaml    # API Server 配置
│   ├── env.example         # 配置模板
│   ├── start.sh            # 启动脚本
│   └── README.md           # 单机部署文档
│
├── multi/                  # 多卡部署（Router + 多组 API+VLM）
│   ├── compose-multi.yaml  # Router + 多组 API+VLM 配置
│   ├── env.multi.example   # 配置模板
│   ├── start-multi.sh      # 启动脚本
│   └── README.md           # 多卡部署文档
│
├── china/                  # 各硬件平台 Dockerfile
├── global/                 # 海外版 Dockerfile
└── README.md               # 本文件
```

## 镜像分层架构

```
mineru-env:v1.0     ← 环境镜像（torch、paddleocr 等依赖，~2-3 GB）
  │
  └── mineru-full:v1.0  ← 代码镜像（MinerU 代码，~50-100 MB）
```

**为什么要分离？**
- 环境镜像构建一次，长期使用
- 代码更新时只传代码镜像（小）
- 离线部署更方便

## 三种 Backend

| Backend | 说明 | 需要 VLM Server |
|---|---|---|
| `pipeline` | 纯本地小模型 | 不需要 |
| `vlm-http-client` | 远程 VLM | 需要 |
| `hybrid-http-client` | 本地小模型 + 远程 VLM | 需要 |

## 选择部署模式

| 场景 | 推荐模式 | 说明 |
|---|---|---|
| 开发测试 | `single/` | 一个 API + 一个 VLM |
| 单卡生产 | `single/` | 一个 API + 一个 VLM |
| 多卡生产 | `multi/` | Router + 多组 API+VLM |
| 跨机器部署 | `multi/` | Router 在一台，API+VLM 分布在多台 |

## 离线部署流程

### 首次部署

```bash
# 1. 在有网机器：构建并导出
cd docker/base
./build.sh all
./build.sh export

# 2. 拷贝 export/ 目录到离线机器
scp -r export/ user@offline-server:/path/to/docker/base/

# 3. 在离线机器：导入镜像
cd docker/base
./build.sh import export/

# 4. 选择部署模式（single 或 multi）
cd docker/single   # 或 docker/multi
cp env.example .env
vim .env
./start.sh all     # 或 ./start-multi.sh start
```

### 更新代码

```bash
# 1. 在有网机器：更新代码后重新构建
cd docker/base
./build.sh code
./build.sh export-code

# 2. 拷贝 mineru-full-v1.0.tar.gz 到离线机器

# 3. 在离线机器：导入新代码镜像
cd docker/base
./build.sh import export/

# 4. 重启服务
```

## 测试 Backend

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
