# API 服务部署

API 使用环境镜像运行，通过 `mineru-code-sync` 加载代码镜像。部署前请在本机导入匹配的 `mineru-env-*.tar.gz` 和 `mineru-code-*.tar.gz`。

```bash
cd docker/deploy/api
cp .env.example .env
vim .env
docker compose --env-file .env up -d
docker compose --env-file .env ps
curl http://localhost:18000/health
```

`.env` 中必须填写 VLM 的可达地址：

```dotenv
MINERU_ENV_IMAGE=mineru-env:v1.0
MINERU_CODE_IMAGE=mineru-code:v1.0
API_PORT=18000
VLM_IP=10.8.132.224
VLM_PORT=30000
```

常用命令：

```bash
docker compose --env-file .env logs -f
docker compose --env-file .env restart
docker compose --env-file .env down
```

修改代码版本后，更新 `MINERU_CODE_IMAGE`，再执行 `up -d`；不需要重传环境镜像。

API 配置启用了 `--allow-public-http-client` 以连接远程 VLM。请只在受控网络中开放 API 端口，并使用防火墙限制来源地址。
