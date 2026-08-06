# MinerU Router PDF 批量诊断

`batch-router-diagnose.py` 会扫描一个目录中的 PDF，逐个提交给 `mineru-router` 的异步任务接口，并生成 Markdown 报告。

脚本默认串行测试，不会为了批量测试而额外压满 NPU。每个 PDF 完成后会立即刷新报告，中途按 `Ctrl+C` 时已完成的结果不会丢失。

脚本默认还会自动采集每个 PDF 任务时间窗口内的：

- `mineru-router` 日志。
- `mineru-api-1` 日志。
- 本地或 SSH 远程 VLM 容器日志。
- VLM 主机的 `npu-smi info`。
- 可选的 vLLM Prometheus Metrics。

关键日志行和 NPU 快照会直接写入 Markdown，便于现场拍照或转换成文字；更完整的内容保存在 raw 目录。

## 准备目录

```text
/data/mineru-diagnose/
├── success-sample.pdf
├── timeout-sample.pdf
└── other.pdf
```

## 基本用法

在 `docker/multi` 目录执行：

```bash
./batch-router-diagnose.py \
  /data/mineru-diagnose \
  --router-url http://127.0.0.1:8002
```

默认容器名与 `compose-multi.yaml` 一致：

```text
router_container=mineru-router
api_container=mineru-api-1
```

## 采集本机 VLM 日志

如果 VLM 容器与 Router 在同一台主机：

```bash
./batch-router-diagnose.py \
  /data/mineru-diagnose \
  --router-url http://127.0.0.1:8002 \
  --vlm-container mineru-vllm \
  --vlm-metrics-url http://127.0.0.1:30000/metrics
```

`mineru-vllm` 需要替换为 `docker ps` 中的实际 VLM 容器名。

## 采集远程 VLM 主机日志和 NPU 信息

如果 VLM 独立部署在另一台主机：

```bash
./batch-router-diagnose.py \
  /data/mineru-diagnose \
  --router-url http://127.0.0.1:8002 \
  --vlm-container mineru-vllm \
  --vlm-ssh maas@10.8.132.224 \
  --vlm-metrics-url http://10.8.132.224:30000/metrics
```

远程采集需要：

- Router 主机可以使用 SSH 密钥免密码登录 VLM 主机。
- SSH 用户有权执行 `docker logs` 和 `npu-smi info`。
- 脚本使用 `BatchMode=yes`，不会在批量过程中停下等待密码。

如果 SSH、Docker 权限或 `npu-smi` 不可用，Markdown 中会记录 `UNAVAILABLE` 和具体错误，不会中断 PDF 测试。

如果不需要任何自动日志和 NPU 采集：

```bash
./batch-router-diagnose.py \
  /data/mineru-diagnose \
  --router-url http://127.0.0.1:8002 \
  --no-collect-diagnostics
```

默认使用：

```text
backend=vlm-http-client
image_analysis=true
formula_enable=true
table_enable=true
poll_interval=5s
task_timeout=7200s/PDF
```

报告和原始响应默认生成在 PDF 目录：

```text
mineru-router-diagnostic-20260805-200000.md
mineru-router-diagnostic-20260805-200000-raw/
```

## 指定报告路径

```bash
./batch-router-diagnose.py \
  /data/mineru-diagnose \
  --router-url http://127.0.0.1:8002 \
  --output /data/mineru-diagnose/report.md
```

## 递归扫描子目录

```bash
./batch-router-diagnose.py \
  /data/mineru-diagnose \
  --router-url http://127.0.0.1:8002 \
  --recursive
```

## 仅测试前几个 PDF

```bash
./batch-router-diagnose.py \
  /data/mineru-diagnose \
  --router-url http://127.0.0.1:8002 \
  --limit 5
```

## 调整测试范围

`start_page_id` 和 `end_page_id` 从 0 开始。例如只测试 PDF 的第 51 至 100 页：

```bash
./batch-router-diagnose.py \
  /data/mineru-diagnose \
  --router-url http://127.0.0.1:8002 \
  --start-page-id 50 \
  --end-page-id 99
```

报告中的 `page_number` 从 1 开始，`page_idx` 从 0 开始。

## 关闭图像详细分析做 A/B 测试

```bash
./batch-router-diagnose.py \
  /data/mineru-diagnose \
  --router-url http://127.0.0.1:8002 \
  --no-image-analysis \
  --output /data/mineru-diagnose/report-no-image-analysis.md
```

## 使用其他后端

```bash
# Pipeline
./batch-router-diagnose.py /data/mineru-diagnose \
  --router-url http://127.0.0.1:8002 \
  --backend pipeline

# Hybrid HTTP Client
./batch-router-diagnose.py /data/mineru-diagnose \
  --router-url http://127.0.0.1:8002 \
  --backend hybrid-http-client \
  --effort high
```

## 报告内容

Markdown 报告包含：

- PDF 文件名、大小、Task ID 和总耗时。
- 成功、部分成功和失败数量。
- 服务端返回的 `file_results`。
- 跳过页的 `page_number`、`page_idx`、错误类型、耗时和尝试次数。
- Router/API 任务失败信息。
- Router、API 和 VLM 日志中的关键错误行。
- `npu-smi info` 快照及采集失败原因。
- vLLM running/waiting request、KV cache、TTFT、队列时间和端到端延迟等 Metrics（服务端启用时）。
- 提交、状态和结果接口的原始 JSON 文件链接。

如果报告显示服务端没有返回 `file_results`，需要确认现场已导入新代码镜像，并且容器内已启用：

```env
MINERU_VLM_FAILURE_POLICY=skip_page
```

## 退出码

- `0`：没有整体失败的 PDF，可能存在被跳过页。
- `1`：至少一个 PDF 整体失败或客户端请求失败。
- `2`：输入目录中没有 PDF。
- `3`：Router 健康检查失败。

批量诊断中出现失败是预期结果，因此脚本返回 `1` 不代表 Markdown 报告没有生成。
