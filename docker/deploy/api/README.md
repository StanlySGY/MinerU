# API 解析服务部署

## 这是什么？

API 服务负责接收文档解析请求，将 PDF 文档渲染成图片，然后调用 VLM 模型提取文字、表格、公式等内容。它是用户和 VLM 之间的桥梁。

## 部署前准备

### 确认镜像已导入

```bash
docker images | grep mineru-full
```

如果没有看到 `mineru-full` 镜像，需要先导入：

```bash
docker load < mineru-full-v1.0.tar.gz
```

### 确认 VLM 服务已启动

```bash
# 测试 VLM 是否正常（替换为实际的 VLM 地址）
curl -s http://10.8.132.224:30000/v1/models
```

如果返回了模型信息，说明 VLM 正常。记下 VLM 的 IP 和端口，后面配置需要用到。

## 部署步骤

### 第一步：进入部署目录

```bash
cd deploy/api
```

### 第二步：创建配置文件

```bash
cp .env.example .env
```

### 第三步：编辑配置文件

```bash
vim .env
```

**必须修改的配置项**（其他保持默认即可）：

```bash
# API 服务端口（一般不用改）
API_PORT=18000

# VLM Server 的 IP 地址（改成 VLM 服务器的实际 IP）
VLM_IP=10.8.132.224

# VLM Server 的端口（改成 VLM 服务器的实际端口）
VLM_PORT=30000
```

**编辑方法**：
1. 用方向键移动到要修改的行
2. 按 `i` 进入编辑模式
3. 修改内容
4. 按 `Esc` 退出编辑模式
5. 输入 `:wq` 按回车保存退出

### 第四步：启动服务

```bash
docker compose --env-file .env up -d
```

### 第五步：等待服务启动

```bash
# 查看启动日志（按 Ctrl+C 退出日志查看）
docker compose --env-file .env logs -f
```

看到类似 `Uvicorn running on http://0.0.0.0:8000` 的日志说明启动成功。

### 第六步：验证服务

```bash
# 检查服务状态
docker compose --env-file .env ps

# 测试健康接口（应该返回健康状态）
curl -s http://localhost:18000/health
```

如果返回了类似这样的 JSON，说明部署成功：

```json
{"status":"healthy","version":"3.4.4",...}
```

## 常用命令

```bash
# 查看服务状态
docker compose --env-file .env ps

# 查看日志（实时跟踪）
docker compose --env-file .env logs -f

# 停止服务
docker compose --env-file .env down

# 重启服务
docker compose --env-file .env restart

# 修改配置后重新启动
vim .env
docker compose --env-file .env down
docker compose --env-file .env up -d
```

## 常见问题

### Q: 启动后 curl 测试不通？

A: 检查服务是否在运行：`docker compose --env-file .env ps`。如果状态是 `Exit`，看日志：`docker compose --env-file .env logs`。

### Q: 日志报错连接不上 VLM？

A: 检查 `.env` 文件中的 `VLM_IP` 和 `VLM_PORT` 是否正确。确保 API 服务器能访问到 VLM 服务器（网络互通）。

### Q: 端口被占用？

A: 修改 `.env` 文件中的 `API_PORT`，换成其他端口（比如 18001）。

### Q: 想同时运行多个 API 实例？

A: 复制整个 `api/` 目录，修改 `.env` 中的端口（比如 18001），然后分别启动即可。
