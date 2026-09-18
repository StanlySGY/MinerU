# 需求

## 部署配置

- `docker/multi/env.multi.example` 和 Compose 默认值与现场保持一致：环境镜像 `mineru-env:v1.0`，代码镜像默认保留当前现场 `mineru-code:v3.4.2-ops-ui20`，代码标签可单独替换为 UI21。
- 四个 API 节点使用现场端口：6002、6003、8001、8002；Router upstream 使用两台现场主机的四个地址。
- 不改变现场已有 `env.multi`、模型目录、数据卷和 UI20 运行状态。

## 动态服务展示

- API 主机根据 `MINERU_DEPLOY_ROLE=api` 和 `MINERU_API_NODE_IDS` 只展示本机实际配置的 API 节点。
- Router/Ops 主机根据 `MINERU_ROUTER_UPSTREAM_URLS_JSON` 展示远程 API 节点，并提供远程健康检查；远程节点不可在本控制台执行 Docker 启停操作。
- 总览和服务页显示实际 API 节点数量。

## 分组配置应用

- 每个配置分组提供独立的“保存并应用”按钮。
- 分组应用只提交该分组中用户修改的字段，使用现有校验、Compose 预览、备份、服务重建、健康检查和失败回滚流程。
- 保留全局“保存并应用”入口，方便一次提交多组变更。

## 验收

- Python 单测、JS 语法检查、Bash 语法检查和 CPU/NPU Compose 展开通过。
- 不泄漏敏感配置，不删除现场已有未跟踪诊断文件。
