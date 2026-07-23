# 多实例部署

`multi/` 启动三个 API 实例和一个 Router。每个 API 连接一个外部 VLM Server；VLM 可以部署在同一台机器，也可以部署在其他机器。Compose 本身不创建 VLM 容器。

```text
VLM-1 <- API-1 ┐
VLM-2 <- API-2 ├─ Router (:8002)
VLM-3 <- API-3 ┘
```

## 首次部署

```bash
cd docker/base
./build.sh import /path/to/export

cd ../multi
cp env.multi.example env.multi
vim env.multi
```

在 `env.multi` 中填写三个 VLM 的实际地址和端口，以及镜像标签：

```dotenv
MINERU_ENV_IMAGE=mineru-env:v1.0
MINERU_CODE_IMAGE=mineru-code:v1.0
VLM_1_IP=10.0.0.11
VLM_1_PORT=30000
VLM_2_IP=10.0.0.12
VLM_2_PORT=30000
VLM_3_IP=10.0.0.13
VLM_3_PORT=30000
```

启动和检查：

```bash
./start-multi.sh start
./start-multi.sh status
./start-multi.sh logs
```

API 默认暴露在 `18000`、`18001`、`18002`，Router 默认暴露在 `8002`。Router 通过 Compose 网络中的服务名访问三个 API，不需要填写 API IP。

API 和 Router 为远程 VLM backend 启用了 `--allow-public-http-client`，应只部署在受控网络，并通过防火墙限制对外端口。

## 更新代码

```bash
# 有网机器
cd docker/base
MINERU_CODE_TAG=v3.4.3 ./build.sh code
MINERU_CODE_TAG=v3.4.3 ./build.sh export-code

# 离线机器
./build.sh import /path/to/export
# 将 multi/env.multi 中的 MINERU_CODE_IMAGE 改为 mineru-code:v3.4.3
cd ../multi
./start-multi.sh stop
./start-multi.sh start
```

代码同步服务会先清理共享卷，因此删除过的旧源码不会残留。依赖变化时重新导入并切换 `MINERU_ENV_IMAGE`。
