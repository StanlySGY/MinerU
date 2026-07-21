# VLM 推理服务部署

## 这是什么？

VLM（Vision Language Model）是一个 AI 大模型服务，负责理解文档中的文字、表格、公式、图片等内容。它是整个 MinerU 系统的核心引擎。

## 部署前准备

### 确认镜像已导入

```bash
docker images | grep mineru-server
```

如果没有看到 `mineru-server` 镜像，需要先导入：

```bash
docker load < mineru-server-v1.0.tar.gz
```

### 确认 NPU 卡可用

```bash
# 查看 NPU 卡状态
npu-smi
```

确认你要使用的卡号（比如第 7 张卡的卡号是 7）。

### 确认模型权重已下载

```bash
# 查看模型文件是否存在
ls /你的模型路径/MinerU2.5-Pro-2604-1.2B/
```

如果模型还没下载，可以留空 `VLM_MODEL_PATH`，启动时会自动下载（需要网络）。

## 部署步骤

### 第一步：进入部署目录

```bash
cd deploy/vlm
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
# VLM 服务端口（一般不用改）
VLM_PORT=30000

# NPU 卡号（改成你实际使用的卡号）
# 例如用第 7 张卡，就填 7
ASCEND_DEVICE=7

# 模型权重路径（改成你服务器上模型的实际路径）
# 例如：/home/user/models/MinerU2.5-Pro-2604-1.2B
# 如果模型还没下载，把这行注释掉（前面加 #）
VLM_MODEL_PATH=/你的模型路径/MinerU2.5-Pro-2604-1.2B
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

### 第五步：等待模型加载

模型加载需要 1-2 分钟，耐心等待。

```bash
# 查看启动日志（按 Ctrl+C 退出日志查看）
docker compose --env-file .env logs -f
```

看到类似 `Uvicorn running on http://0.0.0.0:30000` 的日志说明启动成功。

### 第六步：验证服务

```bash
# 检查服务状态
docker compose --env-file .env ps

# 测试接口（应该返回模型信息）
curl -s http://localhost:30000/v1/models
```

如果返回了类似这样的 JSON，说明部署成功：

```json
{
    "object": "list",
    "data": [
        {
            "id": "mineru2.5-1.2b",
            ...
        }
    ]
}
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

### Q: 启动后 npu-smi 报错？

A: 检查 NPU 驱动是否正确安装，以及 Docker 是否能访问 NPU 设备。

### Q: curl 测试返回 Connection refused？

A: 服务可能还在启动中，等 1-2 分钟再试。如果还是不行，看日志：`docker compose --env-file .env logs`。

### Q: 日志显示找不到模型文件？

A: 检查 `.env` 文件中的 `VLM_MODEL_PATH` 路径是否正确。用 `ls` 确认路径存在。

### Q: 端口被占用？

A: 修改 `.env` 文件中的 `VLM_PORT`，换成其他端口（比如 30001）。
