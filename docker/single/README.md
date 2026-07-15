# 单机部署

一个 API Server + 一个 VLM Server，适合开发测试或单卡环境。

## 架构

```
API Server (:18000) ──→ VLM Server (:30000)
  (CPU，跑 Pipeline 小模型)    (NPU/GPU，跑 VLM 推理)
```

## 支持的 Backend

| Backend | 说明 |
|---|---|
| `pipeline` | 纯本地小模型，不需要 VLM Server |
| `vlm-http-client` | 远程 VLM 推理 |
| `hybrid-http-client` | 本地小模型 + 远程 VLM |

## 部署步骤

### 1. 在有网机器构建镜像

```bash
cd docker/base
./build.sh all
./build.sh export
```

### 2. 拷贝到离线机器

```bash
# 拷贝镜像
scp -r export/ user@server:/path/to/docker/base/

# 拷贝部署文件
scp -r docker/single/ user@server:/path/to/docker/

# 在离线机器导入镜像
cd docker/base
./build.sh import export/
```

### 3. 配置

```bash
cd docker/single
cp env.example .env
vim .env    # 编辑 VLM 服务器 IP 等配置
```

### 4. 启动

```bash
# 同机部署（VLM 和 API 在同一台机器）
./start.sh all

# 或分开部署
./start.sh vlm    # 在 NPU/GPU 机器上启动 VLM
./start.sh api    # 在 CPU 机器上启动 API
```

### 5. 验证

```bash
./start.sh status
./start.sh logs
```

访问 `http://<服务器IP>:18000/docs` 查看 API 文档。

## 测试不同 Backend

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

## 文件说明

| 文件 | 说明 |
|---|---|
| `compose-api.yaml` | API Server 配置 |
| `env.example` | 配置模板 |
| `start.sh` | 启动脚本 |

## 配置项

| 配置 | 说明 | 默认值 |
|---|---|---|
| `VLM_SERVER_IP` | VLM 服务器 IP | `127.0.0.1` |
| `VLM_PORT` | VLM 服务端口 | `30000` |
| `API_PORT` | API 服务端口 | `18000` |
| `MINERU_API_MAX_CONCURRENT_REQUESTS` | 最大并发请求数 | `3` |
| `MINERU_PROCESSING_WINDOW_SIZE` | 每窗口处理页数 | `32` |

## 更新代码

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
cd docker/single
./start.sh stop
./start.sh api
```
