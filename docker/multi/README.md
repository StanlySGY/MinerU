# 多卡部署

Router + 多组 API+VLM，每张 NPU 卡一组，适合生产环境。

## 架构

```
Router (:8002)  ← 统一入口，负载均衡
  │
  ├── NPU 卡0: API-1 (:18000) + VLM-1 (:30000)
  ├── NPU 卡1: API-2 (:18001) + VLM-2 (:30001)
  └── NPU 卡2: API-3 (:18002) + VLM-3 (:30002)
```

## 支持的 Backend

| Backend | 说明 |
|---|---|
| `pipeline` | 纯本地小模型，不需要 VLM |
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
scp -r export/ user@npu-server:/path/to/docker/base/

# 拷贝部署文件
scp -r docker/multi/ user@npu-server:/path/to/docker/

# 在离线机器导入镜像
cd docker/base
./build.sh import export/
```

### 3. 配置

```bash
cd docker/multi
cp env.multi.example env.multi
vim env.multi    # 编辑端口和 IP
```

### 4. 启动

```bash
./start-multi.sh start
```

### 5. 验证

```bash
./start-multi.sh status
./start-multi.sh logs
```

Router 统一入口：`http://<服务器IP>:8002/docs`

## 负载均衡策略

Router 使用**最少连接 + 随机化**策略：

1. 排除不健康的 API 实例
2. 随机打乱候选列表（避免扎堆）
3. 按负载分数排序（排队少 + 处理中少 = 优先）
4. 故障自动转移

## 文件说明

| 文件 | 说明 |
|---|---|
| `compose-multi.yaml` | Router + 多组 API+VLM 配置 |
| `env.multi.example` | 配置模板 |
| `start-multi.sh` | 启动脚本 |

## 配置项

| 配置 | 说明 | 默认值 |
|---|---|---|
| `VLM_1_PORT` | VLM-1 端口 | `30000` |
| `VLM_2_PORT` | VLM-2 端口 | `30001` |
| `VLM_3_PORT` | VLM-3 端口 | `30002` |
| `API_1_PORT` | API-1 端口 | `18000` |
| `API_2_PORT` | API-2 端口 | `18001` |
| `API_3_PORT` | API-3 端口 | `18002` |
| `ROUTER_PORT` | Router 端口 | `8002` |
| `API_1_IP` | API-1 服务器 IP | `127.0.0.1` |
| `API_2_IP` | API-2 服务器 IP | `127.0.0.1` |
| `API_3_IP` | API-3 服务器 IP | `127.0.0.1` |

## 扩展

### 增加更多卡

1. 在 `compose-multi.yaml` 中复制 `mineru-vlm-3` 和 `mineru-api-3` 的配置
2. 修改容器名、端口、环境变量
3. 在 `env.multi` 中添加对应配置
4. 在 Router 的 `--upstream-url` 中添加新 API 地址

### 跨机器部署

如果 API 分布在不同机器：

1. 每台机器上启动各自的 API+VLM
2. 在其中一台机器上启动 Router
3. Router 的 `--upstream-url` 指向所有 API 的实际 IP

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
cd docker/multi
./start-multi.sh stop
./start-multi.sh start
```
