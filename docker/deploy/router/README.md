# Router 服务部署

Router 使用环境镜像运行，通过 `mineru-code-sync` 加载代码镜像。部署前请在本机导入匹配的环境和代码镜像。

```bash
cd docker/deploy/router
cp .env.example .env
vim .env
docker compose --env-file .env up -d
docker compose --env-file .env ps
curl http://localhost:8002/health
```

配置示例：

```dotenv
MINERU_ENV_IMAGE=mineru-env:v1.0
MINERU_CODE_IMAGE=mineru-code:v1.0
ROUTER_PORT=8002
API_1_IP=10.8.132.100
API_1_PORT=18000
```

更多 API 可在 `compose.yaml` 的 `command` 中增加成对的 `--upstream-url` 参数。Router 和 API 在同一主机时，使用 API 主机实际可访问的地址，不要在跨容器场景下使用对方容器的 `127.0.0.1`。

配置启用了 `--allow-public-http-client`，用于转发 `vlm-http-client` 和 `hybrid-http-client` 请求。该选项允许客户端提供远程服务地址，只应在受控网络中暴露 Router，并配合防火墙限制访问。

```bash
docker compose --env-file .env logs -f
docker compose --env-file .env restart
docker compose --env-file .env down
```
