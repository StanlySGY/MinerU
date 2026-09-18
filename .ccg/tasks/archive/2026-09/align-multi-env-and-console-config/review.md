# 审查记录

## 结果

- 部署默认值已与现场对齐：`mineru-env:v1.0`、UI20 默认代码镜像、API 端口 `6002/6003/8001/8002`。
- `MINERU_DEPLOY_ROLE=api/router/all` 和 `MINERU_API_NODE_IDS` 控制本机服务范围。
- Router/Ops 主机从 `MINERU_ROUTER_UPSTREAM_URLS_JSON` 生成远程 API 健康检查节点；远程节点没有 Docker 启停操作。
- 现场拓扑修正为：`32.15.75.232` 使用 `all + 1,2`，`32.15.84.31` 使用 `api + 3,4`。混合模式下本机 API 与远程 API 不重复展示。
- 配置中心每个分组独立提交当前分组的候选值，仍复用既有校验、Compose 预览、备份、重建、健康检查和失败回滚。
- UI21 仅需构建/导出 `mineru-code:v3.4.2-ops-ui21`；现场已有 `mineru-env:v1.0` 不需要重建。

## 验证

```text
129 passed, 1 skipped
Python syntax check passed
JavaScript syntax check passed
Bash syntax check passed
CPU Compose config passed
NPU Compose config passed
git diff --check passed
```

## 审查限制

外部 Antigravity 审查因地区资格限制退出，Claude wrapper 启动失败；因此以本地 diff、回归测试、Compose 展开检查和人工边界审查为依据。没有发现敏感配置进入本次提交，也没有暂存现场诊断文件。
