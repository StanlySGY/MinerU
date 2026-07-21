# API 解析服务部署

## 说明

接收文档解析请求，渲染 PDF 为图片，调用 VLM 提取内容。需要先部署 VLM。

## 部署步骤

```bash
cd api

# 1. 配置（必须填写 VLM 地址）
cp .env.example .env
vim .env

# 2. 启动
docker compose --env-file .env up -d

# 3. 验证
curl -s http://localhost:18000/health
```

## 必须修改的配置

| 配置 | 说明 | 默认值 |
|---|---|---|
| `API_PORT` | API 对外端口 | `18000` |
| `VLM_IP` | VLM Server 的 IP 地址 | **必填** |
| `VLM_PORT` | VLM Server 的端口 | `6002` |

## 支持的 Backend

| Backend | 说明 |
|---|---|
| `pipeline` | 纯本地小模型 |
| `vlm-http-client` | 远程 VLM 推理 |
| `hybrid-http-client` | 本地小模型 + 远程 VLM |

## 停止服务

```bash
docker compose --env-file .env down
```
