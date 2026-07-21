# Router 负载均衡服务部署

## 说明

统一入口，接收用户请求，根据负载情况分发到后端 API。需要先部署 API。

## 部署步骤

```bash
cd router

# 1. 配置（必须填写 API 地址）
cp .env.example .env
vim .env

# 2. 启动
docker compose --env-file .env up -d

# 3. 验证
curl -s http://localhost:8002/health
```

## 必须修改的配置

| 配置 | 说明 | 默认值 |
|---|---|---|
| `ROUTER_PORT` | Router 对外端口 | `8002` |
| `API_1_IP` | 第 1 个 API 的 IP 地址 | **必填** |
| `API_1_PORT` | 第 1 个 API 的端口 | `18000` |

## 添加更多 API

1. 在 `.env` 中添加 `API_2_IP`、`API_2_PORT` 等
2. 在 `compose.yaml` 的 Router command 中取消注释对应的 `--upstream-url`
3. 重启：`docker compose --env-file .env up -d`

## 负载均衡策略

- 最少连接 + 随机化
- 自动剔除不健康的 API
- 故障自动转移

## 停止服务

```bash
docker compose --env-file .env down
```
