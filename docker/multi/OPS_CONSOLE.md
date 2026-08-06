# MinerU 运维控制台

`mineru-ops` 与 Router、API 使用同一份 `compose-multi.yaml` 和 `env.multi`。
`start-multi.sh` 在每次启动前生成解析后的 Compose 配置快照，控制台据此发现服务、镜像、端点和角色。

## 配置

在 `env.multi` 中配置：

```dotenv
MINERU_OPS_PORT=19000
MINERU_OPS_TEST_HOST_PATH=/data/mineru-test-pdfs
MINERU_OPS_DATA_VOLUME=mineru-ops-data-multi
MINERU_OPS_AUTH_TOKEN=replace-with-a-long-random-token
MINERU_OPS_SMOKE_BACKEND=vlm-http-client
MINERU_OPS_SMOKE_TIMEOUT_SECONDS=900
MINERU_OPS_MAX_UPLOAD_MB=2048
```

- `MINERU_OPS_TEST_HOST_PATH` 是批量测试允许读取的唯一宿主机根目录。
- `MINERU_OPS_AUTH_TOKEN` 留空时，状态、任务和日志保持只读，禁止服务控制和批量测试。
- `MINERU_OPS_MAX_UPLOAD_MB` 限制一次浏览器上传的 PDF 总大小，默认 2048 MB。
- “全链路测试”自动生成一页 PDF，并使用 `MINERU_OPS_SMOKE_BACKEND` 经过 Router、API 和 VLM。
- 控制台历史、报告和审计日志保存在 `MINERU_OPS_DATA_VOLUME`，不会随容器重建删除。

## 启动和停止

```bash
./start-multi.sh check
./start-multi.sh start
./start-multi.sh status
```

启动完成后访问：

```text
http://服务器IP:19000
```

生命周期命令：

```bash
./start-multi.sh stop       # 停止 Router/API，保留控制台
./start-multi.sh restart    # 重启 Router/API，控制台持续可用
./start-multi.sh ops        # 单独启动或更新控制台
./start-multi.sh stop-all   # 停止全部容器和控制代理
```

控制页面通过宿主机 Unix Socket 连接 `mineru-ops-agent.py`。代理只接受当前 Compose 项目中的服务名，以及 `start`、`stop`、`restart`、`logs` 和诊断采集等固定操作，不接受任意 Shell 命令。

## 页面级进度

VLM 页面处理会记录以下事件：

```text
page_queued
page_inference_started
page_completed
page_skipped
page_failed
```

任务页面分别显示：

- 排队页、推理中页、成功页、跳过页和失败页；
- 页码、排队耗时、推理耗时、尝试次数和错误信息；
- 文件级 `partial_success` 和最终 `file_results`。

页面并发处理时不会显示误导性的单一“当前页”，而是显示所有正在推理的页码。

## 批量测试

1. 打开“批量测试”，从浏览器选择多个 PDF、选择整个文件夹，或填写服务器测试目录。
2. 浏览器选择文件夹时会保留相对目录结构，非 PDF 文件不会提交。
3. 选择后端并开始测试。
4. 页面默认一次提交一个 PDF，避免与 `MINERU_PROCESSING_WINDOW_SIZE` 和页面并发叠加。
5. 完成后导出 Markdown 或 ZIP。

浏览器上传内容临时保存在 `MINERU_OPS_DATA_VOLUME`。批量任务结束或取消后自动删除原始 PDF，任务记录、日志和诊断报告继续保留。使用服务器目录时，控制台仍然只允许读取 `MINERU_OPS_TEST_HOST_PATH` 下的内容。

ZIP 包含：

```text
BATCH_DIAGNOSIS.md
batch.log
host-diagnostics.json
raw/
```

`host-diagnostics.json` 包含 Router/API 日志、`npu-smi` 和 Docker 版本采集结果。远程 VLM 的详细日志仍需在诊断脚本中配置 SSH 采集参数。

## 数据和安全

- Compose 配置和诊断脚本以只读方式挂载到控制台。
- Docker Socket 不挂载到 Web 容器。
- 服务控制通过未对外监听的 Unix Socket 完成。
- env 内容不会通过控制台 API 返回给浏览器。
- 所有服务控制和批量测试操作写入 SQLite 审计日志。
- 建议只在管理网开放 `MINERU_OPS_PORT`，不要直接暴露到互联网。
