# 审查记录

## 本地审查

- Python 编译通过。
- `bash -n docker/multi/start-multi.sh` 通过。
- CPU 和 NPU Compose 配置展开通过。
- `node --check mineru/ops/static/ops.js` 通过。
- `PYTHONPATH=. pytest -q -o addopts='' tests/unit`：124 passed, 1 skipped。
- `git diff --check` 通过。

## 外部模型审查

已按 CCG 要求并行启动 antigravity 和 claude reviewer，但当前沙箱禁止模型进程监听本地端口，且其日志目录只读；两者均在输出报告前退出。因此本记录以本地代码审阅和测试结果为准。

## 已知边界

- Ops Agent 只能控制其所在主机的 Compose；Router 配置中心可以新增/修改已部署 API 的 upstream，但不能跨主机自动创建 API 容器。
- 批次脚本被看门狗终止时，已经提交给远端 API 的任务不会被远端取消；Router/API 当前没有统一的任务取消协议，控制台会将本地批次记录标记为 `interrupted`。
- 现场四节点的 VLM 地址未提供，API 主机仍需分别填写能访问的 `VLM_N_IP/VLM_N_PORT`。
