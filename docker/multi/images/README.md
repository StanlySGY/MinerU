# 离线镜像归档

现场离线部署时，把下面两个文件放在本目录：

```text
mineru-env-v1.0.tar.gz
mineru-code-v3.4.2-ops-ui20.tar.gz
```

构建并验证 UI21 后，将代码镜像归档名替换为
`mineru-code-v3.4.2-ops-ui21.tar.gz`，同时把 `env.multi` 中的
`MINERU_CODE_IMAGE` 改成对应标签。

然后在上一级目录执行：

```bash
./start-multi.sh import-images
```
