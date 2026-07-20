# 多卡部署（Router + 多组 API+VLM）

## 架构

```
Router (:8002)  ← 统一入口，负载均衡
  │
  ├── NPU 卡0: API-1 (:18000) + VLM-1 (:30000)
  ├── NPU 卡1: API-2 (:18001) + VLM-2 (:30001)
  └── NPU 卡2: API-3 (:18002) + VLM-3 (:30002)
```

## 镜像说明

所有服务使用**同一个镜像** `mineru-full:v1.0`，只是启动命令不同：

| 服务 | 入口命令 | 说明 |
|---|---|---|
| API Server | `mineru-api --host 0.0.0.0 --port 8000 --allow-public-http-client` | 文档解析服务 |
| Router | `mineru-router --host 0.0.0.0 --port 8002 --local-gpus none --upstream-url ...` | 负载均衡入口 |

不需要为 Router 单独构建镜像。

## 支持的 Backend

| Backend | 说明 |
|---|---|
| `pipeline` | 纯本地小模型，不需要 VLM |
| `vlm-http-client` | 远程 VLM 推理 |
| `hybrid-http-client` | 本地小模型 + 远程 VLM |

---

## 离线部署步骤

### 第一步：在有网机器构建并导出

```bash
cd docker/base

# 构建环境镜像 + 代码镜像
./build.sh all

# 导出环境镜像和代码包
./build.sh export
```

导出文件：

```
docker/base/export/
├── mineru-env-v1.0.tar.gz    # 环境镜像（~6GB，只传一次）
└── mineru-code.tar.gz        # 代码包（~几MB，更新代码时传这个）
```

### 第二步：拷贝到离线机器

```bash
# 拷贝镜像包
scp -r docker/base/export/ user@npu-server:/path/to/docker/base/

# 拷贝部署文件
scp -r docker/multi/ user@npu-server:/path/to/docker/
scp docker/base/Dockerfile.code user@npu-server:/path/to/docker/base/
scp docker/base/Dockerfile.env user@npu-server:/path/to/docker/base/
scp docker/base/build.sh user@npu-server:/path/to/docker/base/
```

### 第三步：在离线机器导入镜像

```bash
cd /path/to/docker/base

# 导入环境镜像
./build.sh import export/

# 构建代码镜像（使用本地已有的 mineru-env）
./build.sh code
```

验证镜像：

```bash
docker images | grep -E "mineru-env|mineru-full"
# 应该看到：
# mineru-full   v1.0   xxx   xx minutes ago   xxGB
# mineru-env    v1.0   xxx   xx minutes ago   xxGB
```

### 第四步：配置

```bash
cd /path/to/docker/multi
cp env.multi.example env.multi
vim env.multi
```

**必须修改的配置项**（根据现场实际情况填写）：

```bash
# ========== VLM Server 配置 ==========
# 每张 NPU 卡的 VLM Server 端口（vllm serve 启动时指定的端口）
VLM_1_PORT=30000    # NPU 卡0 的 VLM 端口
VLM_2_PORT=30001    # NPU 卡1 的 VLM 端口
VLM_3_PORT=30002    # NPU 卡2 的 VLM 端口

# ========== API Server 端口 ==========
# 每个 API 实例的端口（不能重复）
API_1_PORT=18000
API_2_PORT=18001
API_3_PORT=18002

# ========== Router 端口 ==========
ROUTER_PORT=8002

# ========== API 服务器 IP ==========
# 如果所有 API 在同一台机器，填 127.0.0.1
# 如果分布在不同机器，分别填写各机器的实际 IP
API_1_IP=127.0.0.1
API_2_IP=127.0.0.1
API_3_IP=127.0.0.1
```

### 第五步：启动

```bash
cd /path/to/docker/multi
./start-multi.sh start
```

启动后的服务：

| 服务 | 地址 | 说明 |
|---|---|---|
| Router | `http://<服务器IP>:8002/docs` | **统一入口，所有请求走这里** |
| API-1 | `http://<服务器IP>:18000/docs` | 直接访问（调试用） |
| API-2 | `http://<服务器IP>:18001/docs` | 直接访问（调试用） |
| API-3 | `http://<服务器IP>:18002/docs` | 直接访问（调试用） |
| VLM-1 | `http://<服务器IP>:30000/v1/models` | 模型状态检查 |
| VLM-2 | `http://<服务器IP>:30001/v1/models` | 模型状态检查 |
| VLM-3 | `http://<服务器IP>:30002/v1/models` | 模型状态检查 |

### 第六步：验证

```bash
# 查看服务状态
./start-multi.sh status

# 查看日志
./start-multi.sh logs

# 测试 Router
curl -s http://localhost:8002/health | python3 -m json.tool

# 测试 API
curl -s http://localhost:18000/health | python3 -m json.tool
```

---

## 后续更新代码

```bash
# 1. 在有网机器
cd docker/base
./build.sh export-code

# 2. 拷贝 mineru-code.tar.gz 到离线机器

# 3. 在离线机器
cd /path/to/MinerU（仓库根目录）
tar xzf /path/to/mineru-code.tar.gz

cd docker/base
./build.sh code

# 4. 重启服务
cd /path/to/docker/multi
./start-multi.sh stop
./start-multi.sh start
```

---

## 文件说明

| 文件 | 说明 |
|---|---|
| `compose-multi.yaml` | Router + 多组 API+VLM 的 Docker Compose 配置 |
| `env.multi.example` | 配置模板（复制为 `env.multi` 后修改） |
| `start-multi.sh` | 启动脚本（build/start/stop/status/logs） |

## 配置项说明

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
| `MINERU_VLM_MODEL` | VLM 模型名称 | `OpenDataLab/MinerU2.5-Pro-2604-1.2B` |
| `VLLM_GPU_MEMORY_UTILIZATION` | GPU 显存利用率 | `0.9` |
| `MINERU_API_MAX_CONCURRENT_REQUESTS` | 每个 API 最大并发数 | `3` |
| `MINERU_PROCESSING_WINDOW_SIZE` | 每窗口处理页数 | `32` |

---

## 扩展

### 增加更多卡

1. 在 `compose-multi.yaml` 中复制 `mineru-vlm-3` 和 `mineru-api-3` 的配置块
2. 修改容器名、端口、环境变量
3. 在 `env.multi` 中添加对应端口配置
4. 在 `compose-multi.yaml` 的 Router `command` 中添加新的 `--upstream-url`

### 跨机器部署

如果 API 分布在不同物理机器上：

1. 每台机器上各自启动 API+VLM（使用各自的 `env.multi`）
2. 在其中一台机器上启动 Router
3. Router 的 `--upstream-url` 指向所有 API 的**实际网络 IP**

```yaml
# compose-multi.yaml 中 Router 的 command
command:
  --host 0.0.0.0
  --port 8002
  --local-gpus none
  --upstream-url http://10.8.132.100:18000    # 机器1的API
  --upstream-url http://10.8.132.101:18000    # 机器2的API
  --upstream-url http://10.8.132.102:18000    # 机器3的API
```

### 调整并发数

如果 VLM 处理变慢（从日志看到 `s/it` 数值增大），说明并发过高导致资源竞争：

```bash
# 在 env.multi 中降低并发
MINERU_API_MAX_CONCURRENT_REQUESTS=1
```

如果想加快单个文档处理但接受总吞吐量下降，调为 1。
如果想最大化总吞吐量，保持 3 或适当增加（但要观察 VLM 处理速度）。
