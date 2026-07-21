# MinerU 现场部署指南

## 架构说明

MinerU 由三个服务组成，每个服务独立部署：

```
用户请求 → Router（统一入口）→ API（文档解析）→ VLM（模型推理）
```

- **Router**：接收用户请求，智能分配给空闲的 API 服务
- **API**：解析 PDF 文档，调用 VLM 提取文字、表格、公式等内容
- **VLM**：运行 AI 大模型，理解文档内容

## 部署顺序（很重要！）

必须按顺序部署，不能跳步：

```
第一步：部署 VLM（模型推理服务）
第二步：部署 API（文档解析服务，需要知道 VLM 的地址）
第三步：部署 Router（负载均衡入口，需要知道 API 的地址）
```

## 需要准备的文件

在部署前，确保你有以下文件（从开发机拷贝过来）：

```
deploy/
├── vlm/                 # VLM 部署文件
├── api/                 # API 部署文件
├── router/              # Router 部署文件
└── README.md            # 本文件
```

以及镜像文件：

```
mineru-server:v1.0       # VLM 用的镜像（从 NPU 服务器导出）
mineru-full:v1.0         # API 和 Router 用的镜像（从构建服务器导出）
```

## 第一步：导入镜像

在部署服务器上执行：

```bash
# 导入 VLM 镜像（如果还没有的话）
docker load < mineru-server-v1.0.tar.gz

# 导入 API/Router 镜像（如果还没有的话）
docker load < mineru-full-v1.0.tar.gz

# 验证镜像是否导入成功
docker images | grep -E "mineru-server|mineru-full"
```

应该看到类似这样的输出：

```
mineru-server   v1.0   xxx   xx minutes ago   xxGB
mineru-full     v1.0   xxx   xx minutes ago   xxGB
```

## 第二步：部署 VLM

```bash
# 进入 VLM 部署目录
cd deploy/vlm

# 复制配置文件
cp .env.example .env

# 编辑配置文件（必须修改）
vim .env
```

**必须修改的配置**（用键盘方向键移动，按 `i` 进入编辑模式，修改完按 `Esc`，输入 `:wq` 保存退出）：

```bash
# VLM 服务端口（一般不用改）
VLM_PORT=30000

# NPU 卡号（改成你实际使用的卡号，比如用第 7 张卡就填 7）
ASCEND_DEVICE=7

# 模型权重路径（改成你服务器上模型的实际路径）
# 如果模型已经下载好了，填路径；如果没下载，留空会自动下载
VLM_MODEL_PATH=/你的模型路径/MinerU2.5-Pro-2604-1.2B
```

保存后启动：

```bash
# 启动 VLM 服务
docker compose --env-file .env up -d

# 等待 1-2 分钟让模型加载完成，然后检查状态
docker compose --env-file .env ps

# 测试 VLM 是否正常（应该返回模型信息）
curl -s http://localhost:30000/v1/models
```

如果返回了模型信息，说明 VLM 部署成功。记下这个地址（比如 `10.8.132.224:30000`），后面 API 配置需要用到。

## 第三步：部署 API

```bash
# 进入 API 部署目录
cd deploy/api

# 复制配置文件
cp .env.example .env

# 编辑配置文件
vim .env
```

**必须修改的配置**：

```bash
# API 服务端口（一般不用改）
API_PORT=18000

# VLM Server 的 IP 地址（改成第二步中 VLM 的实际 IP）
VLM_IP=10.8.132.224

# VLM Server 的端口（改成第二步中 VLM 的实际端口）
VLM_PORT=30000
```

保存后启动：

```bash
# 启动 API 服务
docker compose --env-file .env up -d

# 等待 10 秒，然后检查状态
sleep 10
docker compose --env-file .env ps

# 测试 API 是否正常（应该返回健康状态）
curl -s http://localhost:18000/health
```

如果返回了 `{"status":"healthy"...}`，说明 API 部署成功。记下这个地址（比如 `10.8.132.100:18000`），后面 Router 配置需要用到。

## 第四步：部署 Router

```bash
# 进入 Router 部署目录
cd deploy/router

# 复制配置文件
cp .env.example .env

# 编辑配置文件
vim .env
```

**必须修改的配置**：

```bash
# Router 服务端口（一般不用改）
ROUTER_PORT=8002

# 第 1 个 API 的 IP 地址（改成第三步中 API 的实际 IP）
API_1_IP=10.8.132.100

# 第 1 个 API 的端口（改成第三步中 API 的实际端口）
API_1_PORT=18000
```

保存后启动：

```bash
# 启动 Router 服务
docker compose --env-file .env up -d

# 等待 10 秒，然后检查状态
sleep 10
docker compose --env-file .env ps

# 测试 Router 是否正常
curl -s http://localhost:8002/health
```

如果返回了健康状态，说明全部部署完成！

## 第五步：验证完整流程

```bash
# 通过 Router 提交一个 PDF 文件测试
curl -X POST http://localhost:8002/file_parse \
  -F "files=@你的测试文件.pdf" \
  -F "backend=vlm-http-client"
```

如果返回了解析结果，说明整个链路（Router → API → VLM）都正常。

## 常用命令

```bash
# 查看服务状态
docker compose --env-file .env ps

# 查看服务日志（实时跟踪）
docker compose --env-file .env logs -f

# 停止服务
docker compose --env-file .env down

# 重启服务
docker compose --env-file .env restart

# 修改配置后重新启动
vim .env
docker compose --env-file .env up -d
```

## 常见问题

### Q: 启动后 curl 测试不通？

A: 检查服务是否在运行：`docker compose --env-file .env ps`。如果状态是 `Exit`，看日志：`docker compose --env-file .env logs`。

### Q: VLM 启动很慢？

A: 正常现象，模型加载需要 1-2 分钟。等 `start_period` 过了再测试。

### Q: API 报错连接不上 VLM？

A: 检查 `.env` 文件中的 `VLM_IP` 和 `VLM_PORT` 是否正确。确保 API 服务器能访问到 VLM 服务器（网络互通）。

### Q: Router 报错没有健康的后端？

A: 检查 `.env` 文件中的 `API_1_IP` 和 `API_1_PORT` 是否正确。确保 API 服务已经启动且健康。
