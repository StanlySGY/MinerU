# MinerU 现场部署指南

## 架构

```
用户 → Router (:8002) → API (:18000) → VLM (:30000)
```

三个服务独立部署，互不影响。每个服务有自己的 compose 文件和配置文件。

## 部署顺序

```
1. 先部署 VLM（模型推理服务）
2. 再部署 API（文档解析服务，指向 VLM）
3. 最后部署 Router（负载均衡入口，指向 API）
```

## 目录结构

```
deploy/
├── vlm/                 # VLM 推理服务
│   ├── compose.yaml     # VLM 启动配置
│   ├── .env.example     # VLM 配置模板
│   └── README.md        # VLM 部署文档
│
├── api/                 # API 解析服务
│   ├── compose.yaml     # API 启动配置
│   ├── .env.example     # API 配置模板
│   └── README.md        # API 部署文档
│
├── router/              # Router 负载均衡
│   ├── compose.yaml     # Router 启动配置
│   ├── .env.example     # Router 配置模板
│   └── README.md        # Router 部署文档
│
└── README.md            # 本文件（总览）
```

## 快速部署

### 1. 部署 VLM

```bash
cd vlm
cp .env.example .env
vim .env                          # 修改端口、卡号、模型路径
docker compose --env-file .env up -d
```

### 2. 部署 API

```bash
cd api
cp .env.example .env
vim .env                          # 修改 VLM 地址、端口
docker compose --env-file .env up -d
```

### 3. 部署 Router

```bash
cd router
cp .env.example .env
vim .env                          # 修改 API 地址
docker compose --env-file .env up -d
```

## 验证

```bash
# VLM
curl -s http://localhost:30000/v1/models

# API
curl -s http://localhost:18000/health

# Router
curl -s http://localhost:8002/health
```

## 镜像说明

| 镜像 | 用途 | 构建方式 |
|---|---|---|
| `mineru-server:v1.0` | VLM 推理服务 | 从已有的 NPU 服务器导出，或用 `china/npu.Dockerfile` 构建 |
| `mineru-full:v1.0` | API + Router | `cd docker/base && ./build.sh code` |

## 扩展

### 添加新的 API + VLM

1. 在新的 NPU 机器上部署 VLM（修改 `.env` 中的端口和卡号）
2. 在新的机器上部署 API（修改 `.env` 中的 VLM 地址）
3. 在 Router 的 `.env` 中添加新的 API 地址，重启 Router

### 跨机器部署

每个服务可以部署在不同的机器上，只要网络互通即可。修改 `.env` 中的 IP 地址即可。
