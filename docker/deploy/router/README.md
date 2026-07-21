# Router 负载均衡服务部署

## 这是什么？

Router 是整个系统的统一入口。用户的所有请求都先发给 Router，Router 根据各个 API 服务的负载情况，智能地把请求分配给最空闲的那个 API。

好处：
- 用户只需要记住一个地址（Router 的地址）
- 多个 API 实例可以同时工作，提高处理速度
- 某个 API 挂了，Router 会自动把请求转给其他正常的 API

## 部署前准备

### 确认镜像已导入

```bash
docker images | grep mineru-full
```

如果没有看到 `mineru-full` 镜像，需要先导入：

```bash
docker load < mineru-full-v1.0.tar.gz
```

### 确认 API 服务已启动

```bash
# 测试 API 是否正常（替换为实际的 API 地址）
curl -s http://10.8.132.100:18000/health
```

如果返回了健康状态，说明 API 正常。记下 API 的 IP 和端口，后面配置需要用到。

## 部署步骤

### 第一步：进入部署目录

```bash
cd deploy/router
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
# Router 服务端口（一般不用改）
ROUTER_PORT=8002

# 第 1 个 API 的 IP 地址（改成 API 服务器的实际 IP）
API_1_IP=10.8.132.100

# 第 1 个 API 的端口（改成 API 服务器的实际端口）
API_1_PORT=18000
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

看到类似 `Uvicorn running on http://0.0.0.0:8002` 的日志说明启动成功。

### 第六步：验证服务

```bash
# 检查服务状态
docker compose --env-file .env ps

# 测试健康接口（应该返回健康状态）
curl -s http://localhost:8002/health
```

如果返回了类似这样的 JSON，说明部署成功：

```json
{"status":"healthy","version":"3.4.4",...}
```

## 后续添加更多 API

当你部署了新的 API 服务后，需要把它们添加到 Router：

### 第一步：修改 .env 文件

```bash
vim .env
```

取消注释并填写新的 API 地址：

```bash
# 第 2 个 API 的 IP 地址
API_2_IP=10.8.132.101

# 第 2 个 API 的端口
API_2_PORT=18000
```

### 第二步：修改 compose.yaml

```bash
vim compose.yaml
```

找到 Router 的 command 部分，取消注释对应的行：

```yaml
command:
  --host 0.0.0.0
  --port 8002
  --local-gpus none
  --upstream-url http://${API_1_IP}:${API_1_PORT}
  --upstream-url http://${API_2_IP}:${API_2_PORT}    # 取消这行的注释
```

### 第三步：重启 Router

```bash
docker compose --env-file .env down
docker compose --env-file .env up -d
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
vim compose.yaml
docker compose --env-file .env down
docker compose --env-file .env up -d
```

## 常见问题

### Q: 启动后 curl 测试不通？

A: 检查服务是否在运行：`docker compose --env-file .env ps`。如果状态是 `Exit`，看日志：`docker compose --env-file .env logs`。

### Q: 日志报错没有健康的后端？

A: 检查 `.env` 文件中的 `API_1_IP` 和 `API_1_PORT` 是否正确。确保 API 服务已经启动且健康。

### Q: 端口被占用？

A: 修改 `.env` 文件中的 `ROUTER_PORT`，换成其他端口（比如 8003）。

### Q: Router 和 API 可以部署在同一台机器吗？

A: 可以。把 `API_1_IP` 填成 `127.0.0.1` 即可。
