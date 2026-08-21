# Session handoff — 2026-08-20

## 2026-08-21 完成：运维控制台配置中心与性能实验室（第一阶段）

用户已确认实施“配置中心 + 性能实验室 + UI 产品化”。本轮已直接完成第一阶段代码，不再受此前 Spec Workflow `pending` 文案限制。

### 可见入口

运维控制台左侧导航新增两个入口，源码位于 `mineru/ops/static/index.html`：

```text
性能实验室
配置中心
```

现场若看不到这两个按钮，说明运行中的 `mineru-ops` 仍使用旧代码镜像/旧代码 volume，或浏览器仍缓存旧页面；并非按钮需要额外开关。升级到本次 `dev` 构建的 `mineru-code:v3.4.2-ops-ui7` 后再强制刷新浏览器。

### 性能实验室

- 汇总现有批次结果，展示批次状态、PDF 数、页面数、成功/异常页、总耗时、有效处理耗时和重试开销。
- 新建实验时可选择 VLM micro-batch `1 / 2 / 4 / 8 / 16`。
- 可按实验设置单页 soft timeout、任务等待上限和连接重试次数。
- 保留浏览器上传与服务端测试目录两种输入方式，复用现有批量测试接口。
- 第一阶段不开放 batch 32，也不把一个 PDF 人工拆成 8/32 个独立 Router task；batch 大于 1 仍使用现有失败回退逐页隔离逻辑。

### 配置中心

- Ops Web 通过 Unix Socket 调用宿主机 `docker/multi/mineru-ops-agent.py`，不向 Web 容器挂载 Docker Socket。
- Agent 读取部署目录中的 `env.multi`，按白名单 Schema 分类展示配置。
- Token、Password、Secret 类字段只返回脱敏值；不在白名单的字段只读。
- 页面可编辑候选值并执行类型、枚举、数值范围和换行符校验。
- 页面展示 `env.multi.bak-*` 历史记录，但第一阶段仅查看，不恢复。
- **安全边界：第一阶段是 `read_validate_only`，不会直接写回 `env.multi`，也不会自动重建容器。** 后续若开放“应用配置”，必须补原子写入、注释保留、Compose config 校验、差异确认、备份、选择性重建、健康检查、失败回滚和审计。

### 本轮涉及文件

```text
docker/multi/mineru-ops-agent.py
mineru/cli/ops.py
mineru/ops/static/index.html
mineru/ops/static/ops.css
mineru/ops/static/ops.js
tests/unit/test_ops_console.py
tests/unit/test_ops_agent_config.py
handoff.md
```

提交时必须逐文件暂存，不提交 `.gitignore`、诊断结果、运行数据库、测试 PDF、`.codegraph/`、`.mimocode/`、`.spec-workflow/` 等本地文件。

### 验证结果

```text
python -m py_compile docker/multi/mineru-ops-agent.py mineru/cli/ops.py
node --check mineru/ops/static/ops.js
python -m pytest -o addopts='' tests/unit/test_ops_agent_config.py -q   # 6 passed
python -m pytest -o addopts='' tests/unit/test_ops_console.py -q        # 27 passed
python -m pytest -o addopts='' tests/unit/test_batch_router_diagnose.py -q  # 11 passed
git diff --check
```

总计 44 个相关测试通过。

## 现场诊断脚本：multi 部署健康检查与本地模型排查

新增只读脚本：`docker/multi/diagnose-multi-deployment.sh`。

用途：由现场在实际 `docker/multi` 部署目录执行，收集以下信息后回传：

- 实际部署目录、Git 版本、Docker / Compose 版本；
- 脱敏后的 `env.multi` 关键配置；
- 根据 `MINERU_DEVICE_MODE` 自动选择 CPU/NPU/CUDA Compose 文件，并输出 `docker compose config` 关键展开结果；
- `mineru-api-1`、`mineru-router`、`mineru-ops`、`mineru-code-sync-multi` 的容器状态、Health、网络、挂载和镜像版本；
- API 容器实际收到的本地模型配置；
- `/etc/mineru/mineru.json` 和 `/models/pipeline` 内容；
- API 关键本地模型路径是否存在；
- Ops 容器到 `mineru-api-1:8000/health` 的 DNS/HTTP 检查；
- API 容器自身、宿主机映射端口的 `/health` 检查；
- Ops 实际使用的 `/config/compose-config.yaml`；
- API、Ops 和宿主机 Ops agent 的近期日志。

脚本不会启动、停止、重启或重建容器，不修改 `env.multi`、Compose 文件和 Docker volume，不下载模型，也不会主动访问 ModelScope。默认报告同时打印到终端并保存到 `/tmp/mineru-multi-diagnosis-时间.txt`，敏感字段会做脱敏处理。

现场默认执行：

```bash
bash diagnose-multi-deployment.sh
```

如果脚本不在实际部署目录，显式指定目录：

```bash
bash diagnose-multi-deployment.sh --project-dir /path/to/MinerU/docker/multi
```

如果现场启动时手工指定了 Compose overlay，必须按相同顺序传入：

```bash
bash diagnose-multi-deployment.sh \\
  --project-dir /path/to/MinerU/docker/multi \\
  --env-file env.multi \\
  --compose-file compose-multi.yaml \\
  --compose-file compose-multi.npu.yaml
```

本地验证：`bash -n docker/multi/diagnose-multi-deployment.sh` 和帮助输出均通过。该脚本目前尚未提交；不要将现场生成的报告、`env.multi` 或运行数据提交到 Git。

## 追加功能：异常页可调超时重试

本轮在 `dev` 上新增“重试异常页”功能，功能提交为 `5636efb2`（`feat: retry failed pages with custom VLM timeout`）。本轮提交范围如下；不要把本地 `.gitignore` 或运行数据带入后续提交：

```text
mineru/cli/ops.py
mineru/ops/static/index.html
mineru/ops/static/ops.css
mineru/ops/static/ops.js
tests/unit/test_ops_console.py
docker/multi/env.multi.example
handoff.md
```

功能行为：

- 批次结束后，列表和详情页都提供“重试异常页”。
- 弹窗可单独填写 `page_timeout_seconds`、`task_timeout`、`page_connect_max_retries` 和 `vlm_batch_size`。
- 默认建议值会把原单页超时加倍，最低建议为 1200 秒、最大 7200 秒；连接重试默认 0、micro-batch 默认 1。
- 后端只提取真实终态为 `skipped` / `failed` 的页，生成新的问题页 PDF 并直接创建新批次，不会修改原 PDF 或在原 Router task 内续跑。
- 新批次 `settings` 记录 `source_type=problem_page_retry`、`retry_of_run_id`、问题页数量和 `problem_pages_manifest_path=manifest.json`；完整原始页码映射保存在新批次输入目录的 `manifest.json` / `manifest.csv`，避免批次列表接口因大量异常页而膨胀。
- `task_timeout` 在后端至少自动提升到 `page_timeout_seconds + 60`，避免诊断脚本比单页 soft timeout 更早停止等待。
- 原始 PDF 已被清理、移走或超出允许测试目录时，接口会明确报错，不能凭现有结果重建问题页。

新增接口：

```http
POST /api/batch-runs/{run_id}/retry-problem-pages
Content-Type: application/json

{
  "page_timeout_seconds": 1200,
  "task_timeout": 7200,
  "page_connect_max_retries": 0,
  "vlm_batch_size": 1
}
```

已执行验证：

```text
python -m py_compile mineru/cli/ops.py
node --check mineru/ops/static/ops.js
python -m pytest -o addopts='' tests/unit/test_ops_console.py tests/unit/test_task_progress.py -q
git diff --check
结果：32 passed
```

现场使用 1200 秒重试前仍需确认 `env.multi` 中 `MINERU_VLM_CLIENT_HTTP_TIMEOUT` 不小于 1200；当前示例为 7200。修改 hard timeout 后必须重建 API 容器，仅重启旧容器不会更新启动参数。

## 本次交付状态

- 当前分支：`dev`。
- 异常页可调超时重试功能提交：`5636efb2`。
- 已将最新 `origin/master`（`4fe4bde1`，MinerU `3.4.5`）合并到 `dev`，合并提交：`bab95584878e4f231240c70c332d8548598ba472`。
- 本次运维控制台与 VLM 基准测试改动提交：`5807835d`（`feat: improve ops timeout diagnostics and VLM benchmarks`）。
- 上一轮 handoff 已提交为 `1efb0a53`；本次补充现场既有目录、`ops-ui7` 镜像命名和 `env.multi.example` 易读注释后，再次提交并推送 `dev`。
- 服务器和现场均应使用最新 `dev`，不要从旧的 `master` 构建本次测试镜像。
- 本次只提交了明确列出的功能、测试和 handoff 文件；`.gitignore`、`.codegraph/`、`.mimocode/`、`docs/analysis-error/`、`mineru-ops-data/`、`mineru_diag.py`、`test.pdf` 等本地内容不要整体暂存或清理。

## 五项问题的核查结论与实现

### 1. 控制台等待超时会产生重复的“失败 + 成功”显示

问题属实。原来的 `task_timeout` 只是 `batch-router-diagnose.py` 停止轮询 Router 的等待上限，不会取消 Router/API 中已经提交的后台任务。等待超时后，旧逻辑会把整份 PDF 尚未确认的页面伪造成失败；后台任务继续执行并成功后，同一页就可能同时出现失败与成功记录。

本次修改：

- 新增 `wait_timeout` / `monitoring_stopped` 语义，明确表示“诊断脚本停止等待”，而不是“后台解析失败”。
- 超时时保留最后一次 Router 返回的真实 progress；尚无终态的页面显示为 `unknown`，不再伪造成 `failed`。
- `GET /api/tasks/{task_id}` 优先查询 Router 的实时状态，Router 不可用时才回退到 Ops 本地缓存。
- 控制台的停止动作明确为停止批次脚本，不再暗示能取消 Router/API 后台任务。

限制仍然存在：当前 MinerU/Router 没有真正的任务取消接口；停止监控不会停止后台解析。若以后需要强制取消，必须另行设计 task cancellation、子进程/协程取消和中间产物清理协议。

### 2. 测试慢页真实耗时、可调 600 秒超时、导出问题页重新上传

新增批次级参数并贯通 Ops UI → 批次脚本 → Router multipart → API request options → VLM 分析：

| 参数 | 范围 | Ops 批量测试建议默认值 | 含义 |
| --- | ---: | ---: | --- |
| `page_timeout_seconds` | 1–7200 秒 | 600 | 单页/微批次 VLM soft timeout，可按批次覆盖。 |
| `page_connect_max_retries` | 0–3 | 0 | 瞬时连接错误的页级重试次数；公共 API 默认仍为 1。 |
| `vlm_batch_size` | 1–16 | 1 | 每次送入 VLM 的页面数；失败或返回数量异常时自动回退逐页隔离。 |

超时分为两层：

- `MINERU_VLM_PAGE_TIMEOUT_SECONDS=600`：默认页面 soft timeout，控制台可按批次覆盖。
- `MINERU_VLM_CLIENT_HTTP_TIMEOUT=7200`：HTTP Client hard timeout。必须大于等于现场计划测试的最大 soft timeout，否则 soft timeout 调高后仍会被底层 HTTP 提前断开。

新增问题页导出：

```http
GET /api/batch-runs/{run_id}/export?format=problem_pages
```

导出的 ZIP 包含：

```text
problem-pages/0001-<source>-problem-pages.pdf
manifest.json
manifest.csv
```

每个源 PDF 单独生成一个问题页 PDF，只包含真实终态为 `skipped` 或 `failed` 的页面，可重新上传进行长超时、小批量专项测试；`unknown`/仅停止监控的页面不会被冒充为问题页。

### 3. 重试是否值得、以及重试耗时如何统计

慢页如果是稳定的内容复杂度或模型吞吐问题，立即重试通常不会解决问题，反而会重复消耗完整超时时间。因此 Ops 基准测试默认 `page_connect_max_retries=0`，先测出单次请求的真实耗时；仅对连接中断、502/503/504 等明确的瞬时错误考虑重试。公共 API 为兼容现有行为仍默认 1 次。

本次为每次 attempt 记录：

```text
attempt_no, started_at, completed_at, outcome, error_type, error,
request_seconds, retry_wait_before_seconds, timeout_seconds, batch_size
```

页面汇总同时提供：

```text
successful_attempt_seconds, failed_attempt_seconds, retry_overhead_seconds,
vlm_request_seconds, with_retry_wall_seconds, attempt_details
```

SQLite 新增 `task_page_attempts` 表。因此报表可以同时观察成功请求耗时、失败尝试耗时、重试等待开销，以及包含全部重试的墙钟总时长，避免只看一个“总时长”误判性能。

### 4. 逐页处理可能使 PDF 从约 1 分钟变成约 6 分钟

该怀疑合理。逐页隔离提高了失败定位和跳页能力，但会损失 VLM batching 吞吐，并增加逐请求调度、图像编码和网络开销。本次新增 `vlm_batch_size` micro-batch：

- 默认 1，保持最强失败隔离和最准确的逐页诊断。
- 现场建议在同一 PDF、同一并发和同一超时下依次比较 1、2、4。
- 一组 micro-batch 整体失败或返回页数异常时，自动回退逐页处理，避免一页拖垮整批。
- 不建议未经实测直接提高到 16；NPU/VLM 显存、上下文长度和队列延迟都可能成为新瓶颈。

### 5. 与官方 master 同步

已把最新 `origin/master` 合并到 `dev`，无冲突。同步内容包括 MinerU `3.4.5`、DOCX 表格特殊字符处理、PDF Unicode surrogate pair 恢复及对应上游测试。本次部署应以包含合并提交 `bab95584` 和功能提交 `5807835d` 的最新 `dev` 为准。

## 验证结果

2026-08-20 在本地执行成功：

```bash
python -m pytest -o addopts='' tests/unit -q
# 50 passed, 1 skipped

python -m py_compile \
  docker/multi/batch-router-diagnose.py \
  mineru/backend/vlm/resilience.py \
  mineru/backend/vlm/vlm_analyze.py \
  mineru/cli/api_request.py \
  mineru/cli/fast_api.py \
  mineru/cli/ops.py

node --check mineru/ops/static/ops.js
git diff --check
```

上游两个额外专项测试在当前环境缺少 `mammoth` / `pdftext` 时无法收集，这属于环境依赖缺失，不是本次断言失败。

## 构建与部署注意事项

- 本次改动同时涉及 API、Router、Ops 页面和挂载的批次脚本，必须重建 `mineru-code` 镜像，并重新创建/启动 `mineru-code-sync`、`mineru-api-1`、`mineru-router`、`mineru-ops`；只重启旧容器不足以保证新代码卷已同步。
- `docker/multi/update-code.sh <tag>` 会构建代码镜像、更新 `env.multi` 中的 `MINERU_CODE_IMAGE`，随后通过 `start-multi.sh stop/start` 重建 Router/API，并重启 Ops。离线现场使用导入镜像时，应手动把 `env.multi` 的 `MINERU_CODE_IMAGE` 改成导入后的 tag，再执行 `start-multi.sh stop && start-multi.sh start`。
- `MINERU_VLM_CLIENT_HTTP_TIMEOUT=7200` 是 API 容器启动参数，修改 `env.multi` 后必须重新创建 API 容器才会生效。
- 建议保留 `MINERU_VLM_CLIENT_MAX_RETRIES=0`，避免底层 HTTP Client 和页级重试叠加。

本次沿用服务器既有目录与上一版命名规则，只把 `ops-ui6` 升级为 `ops-ui7`：

```text
服务器仓库：/data/maas/sgy_arm/gd-dev/MinerU
上一版归档：mineru-code-v3.4.2-ops-ui6.tar.gz
本次镜像：mineru-code:v3.4.2-ops-ui7
本次归档：/data/maas/sgy_arm/gd-dev/MinerU/docker/base/export/mineru-code-v3.4.2-ops-ui7.tar.gz
```

服务器构建和导出命令：

```bash
cd /data/maas/sgy_arm/gd-dev/MinerU
git fetch origin
git switch dev
git pull --ff-only origin dev

cd /data/maas/sgy_arm/gd-dev/MinerU/docker/base
MINERU_CODE_TAG=v3.4.2-ops-ui7 ./build.sh code
MINERU_CODE_TAG=v3.4.2-ops-ui7 ./build.sh export-code

docker image inspect mineru-code:v3.4.2-ops-ui7
ls -lh /data/maas/sgy_arm/gd-dev/MinerU/docker/base/export/mineru-code-v3.4.2-ops-ui7.tar.gz

cd /data/maas/sgy_arm/gd-dev/MinerU/docker/base/export
sha256sum mineru-code-v3.4.2-ops-ui7.tar.gz \
  > mineru-code-v3.4.2-ops-ui7.tar.gz.sha256
```

注意：`mineru-code` 归档只包含代码镜像中的 `mineru/` 和 `pyproject.toml`，不会携带本次同样有改动的 `docker/multi/batch-router-diagnose.py`、`docker/multi/compose-multi.yaml` 和 `docker/multi/env.multi.example`。因此现场部署目录也必须更新到最新 `dev`，不能只导入代码镜像。如果现场不直接拉 Git，可在服务器额外生成部署文件包并与代码镜像一起交付：

```bash
cd /data/maas/sgy_arm/gd-dev/MinerU
git archive \
  --format=tar.gz \
  --output=docker/base/export/mineru-multi-v3.4.2-ops-ui7.tar.gz \
  HEAD docker/multi
```

现场覆盖 `docker/multi` 前必须备份实际使用的 `env.multi`，不要让示例文件覆盖现场密钥、地址和设备配置。现场 `env.multi` 至少确认：

```env
MINERU_CODE_IMAGE=mineru-code:v3.4.2-ops-ui7
MINERU_VLM_PAGE_TIMEOUT_SECONDS=600
MINERU_VLM_CONNECT_MAX_RETRIES=1
MINERU_VLM_CLIENT_MAX_RETRIES=0
MINERU_VLM_CLIENT_HTTP_TIMEOUT=7200
```

`page_timeout_seconds`、`page_connect_max_retries`、`vlm_batch_size` 是 Ops 控制台每个批次的输入参数，不是在 `env.multi` 中新增的变量。`docker/multi/env.multi.example` 已补充中文注释，说明默认值、控制台覆盖方式、建议测试值，以及 hard timeout 修改后必须重新创建 API 容器。

## 现场验收建议

1. 用同一问题 PDF，先设置 `page_connect_max_retries=0`、`vlm_batch_size=1`。
2. 依次测试 `page_timeout_seconds=600`、`900`、`1200`，必要时继续增加，但不得超过 hard timeout 7200 秒。
3. 对导出的真实 `skipped`/`failed` 问题页重新上传，记录首次成功所需时间。
4. 确认等待超时只显示 `monitoring_stopped`/未知页，不再生成整份 PDF 的伪失败；后台完成后任务详情能刷新到 Router 的真实成功状态。
5. 对同一 PDF 依次测试 `vlm_batch_size=1`、`2`、`4`，比较总墙钟时间、成功率、失败回退次数和 VLM 队列指标。
6. 检查导出 ZIP 中的问题页 PDF、`manifest.json`、`manifest.csv` 与页面终态一致。
7. 对有重试的样本分别核对成功 attempt、失败 attempt、retry wait 和 with-retry wall time。

---

## 历史记录（仅供追溯）

以下 2026-08-10 记录保留用于追溯当时上下文，其中的 HEAD、未提交文件和部署状态已经过期；执行当前交付时应以上方 2026-08-20 记录和实际 Git 状态为准。

# Session handoff — 2026-08-10

## Repository checkpoint

- Branch: `dev`
- HEAD: `4b5f733c7833244bf7760bb44c15c2e35f2d3d1e` (`fix: optimize and guard task PDF previews`)
- `origin/dev` was at the same commit when this handoff was generated.
- Worktree is dirty. Do **not** reset, clean, restore, or commit everything wholesale: it contains deployment edits, diagnostic evidence, a local ops database, test input, tool metadata, and an unexplained tracked deletion.

## What is already committed and should be treated as the current ops baseline

Recent commits immediately before this handoff:

| Commit | Technical state |
| --- | --- |
| `4b5f733c` | Optimizes/guards task PDF previews in `mineru/cli/ops.py` and `mineru/ops/static/*`; adds coverage in `tests/unit/test_ops_console.py`. |
| `37ada274` | Restores batch task visibility after Router restart by rebuilding ops-side task state; touches `docker/multi/batch-router-diagnose.py`, `mineru/cli/ops.py`, and ops tests. |
| `1f41fdc5` | Refines task previews and progress; updates VLM progress emission, `mineru/utils/task_progress.py`, ops UI, and unit tests. |
| `47567a52` | Improves ops task-progress readability in the static console. |

These changes are committed. The remaining dirty diff includes deployment documentation/configuration, local diagnostics, the report work below, and an active uncommitted page-timing/Ops enhancement under `mineru/` and `tests/unit/`. Do not reset or stage unrelated paths wholesale.

## Active page timing and Ops persistence work

A complete uncommitted implementation now records and retains per-page timing:

- `mineru/backend/vlm/resilience.py`: measures cumulative client-observed VLM request time, retry wait, extraction elapsed time, and terminal status for every page; emits one stable `event=vlm_page_timing` log for completed/skipped/failed pages. Successful slow pages are warnings; skipped pages warnings; fatal pages errors. The log contains task/file/page identifiers and timing metadata only, never page content.
- `mineru/utils/task_progress.py`: preserves the first page start across retries, exposes `queue_seconds`, compatibility `inference_seconds`, `vlm_request_seconds`, `retry_wait_seconds`, `elapsed_seconds`, and `total_seconds`, and copies the same terminal timing into both page state and terminal events.
- `mineru/cli/ops.py`: adds normalized SQLite `task_page_timings` storage, idempotent extraction from `progress.files[].pages[]`, legacy snapshot backfill, summary/P50/P95/max/slow-page queries, `GET /api/tasks/{task_id}/page-timings`, and a 5-second always-on Router synchronization loop. Unchanged task payloads are skipped to avoid rewriting large terminal snapshots on every poll.
- `mineru/ops/static/ops.js` / `ops.css`: task detail now shows timing summary cards, paginated/filterable/sortable page rows, and clarified tooltip labels (`VLM 请求` rather than pure inference).
- Tests expanded in `tests/unit/test_task_progress.py`, `tests/unit/test_vlm_resilience.py`, and `tests/unit/test_ops_console.py` for retry-aware timing, structured logs, SQLite idempotency/statistics, background sync, route/static contracts, and compatibility.
- Validation: `29 passed, 1 skipped` across task-progress, VLM resilience/failure isolation, and Ops tests; `ruff check --select E,F,I` passed; `node --check mineru/ops/static/ops.js` passed; `git diff --check` passed. A real Ops server was launched on a temporary port/data directory; `/api/health`, task detail, page timing query/summary, dashboard HTML, and served timing UI asset all returned correctly. Browser screenshots were not possible because this environment has no Chrome binary.

## Per-page timing export work (uncommitted, 2026-08-19)

Both reporting exits now emit the timing of **every** page, not just skipped/failed pages. This is an additive layer on top of the page-timing work above; no existing route, schema, or report section was changed or removed.

Single-task export (Ops console task detail):

- `mineru/cli/ops.py`: adds `markdown_table_cell()` / `format_seconds_cell()` helpers, `OpsStore.all_page_timings(task_id, status=None)` (unpaginated, ordered by file name then page number, rejects unknown status values with `ValueError`), `OpsRuntime.task_report_markdown()`, `OpsRuntime.task_report_csv()`, and `GET /api/tasks/{task_id}/report?format=markdown|csv&status=`. The route falls back to a live Router fetch when the task is not cached, returns `400` for an unsupported format and `404` for an unknown task, and sets `Content-Disposition` so the browser saves `task-report-<id>.md` / `.csv`.
- The Markdown report contains task metadata, the timing summary (recorded/completed/skipped/failed counts, average, P50, P95, max, slow-page threshold and count), and one table row per page (file, page number, status, queue, VLM request, retry wait, total, attempts, error). Rows are never truncated away; only individual long cell values are clipped, and pipes/newlines are escaped so they cannot break the table.
- The CSV carries the same rows with 11 fixed columns (`file_name, page_number, page_idx, status, queue_seconds, vlm_request_seconds, retry_wait_seconds, total_seconds, attempts, error_type, error`) and unformatted raw numbers, for spreadsheet analysis.
- `mineru/ops/static/ops.js`: adds 导出 Markdown 报告 / 导出 CSV buttons to the task detail header and a `[data-task-report]` click handler that reuses the existing `downloadPath()` helper.

Batch diagnosis report (`BATCH_DIAGNOSIS.md`):

- `docker/multi/batch-router-diagnose.py`: adds `collect_all_pages(progress)` — flattens `progress.files[].pages[]` from the Router status payload, keeps only terminal pages (`completed` / `skipped` / `failed`), tags each row with its file name, sorts by file name then page number, and tolerates malformed payloads (non-list `files`, non-dict entries, non-list `pages`) by skipping them. Returns `[]` on deployments that report no page-level progress, in which case the new section is omitted entirely.
- Adds `format_page_seconds()` and a per-file `#### 逐页耗时` table rendered after the existing 跳过/失败页面 table. All pages are listed with no truncation (confirmed with the user), reusing the script's existing `markdown_escape()` for cell safety.
- The data was already available: `diagnose_pdf()` already stored `status_payload["progress"]` in each result, so no change to the collection path was needed.

Tests and validation:

- `tests/unit/test_ops_console.py`: adds `test_task_report_markdown_lists_every_page`, `test_task_report_csv_contains_full_page_rows`, `test_all_page_timings_filters_by_status`, and extends `test_ops_app_serves_dashboard_and_health` with the new route and the two new button labels.
- `tests/unit/test_batch_router_diagnose.py`: adds coverage for `collect_all_pages` (flatten/sort/tag, non-terminal and malformed-entry skipping, empty progress), `format_page_seconds`, and `render_report` both emitting the per-page table and omitting it when there is no page progress.
- Validation: `40 passed, 1 skipped` for the full `tests/unit/` suite. `ruff check --select E,F,I` is clean for `mineru/cli/ops.py` and `tests/unit/test_ops_console.py`; `docker/multi/batch-router-diagnose.py` and `tests/unit/test_batch_router_diagnose.py` still report the same 3 findings (2× `I001`, 1× `E501` at line 971) that already exist at `HEAD` — none were introduced here and none were "fixed" to avoid unrelated churn. `node --check mineru/ops/static/ops.js` and `git diff --check` passed.
- End-to-end check with `fastapi.testclient` against a real `create_app()` instance and a seeded task: Markdown returned `200 text/markdown; charset=utf-8` with all 4 pages and the pipe in an error escaped as `\|`; CSV returned `200 text/csv; charset=utf-8` with a header plus 4 rows; `format=bogus` returned `400`; an unknown task returned `404`. Browser screenshots remain impossible in this environment (no Chrome binary).

## Active report work

A dual-environment comparison draft exists at `docs/analysis-error/双环境PDF解析对比报告.md`. A separate field-facing runbook now exists at `docs/analysis-error/现场PDF对比测试执行与拍照说明.md`; give field personnel this runbook rather than the internal comparison report.

The report was simplified on user instruction: expected extracted content is the same, and the purpose is to demonstrate that the field accelerator/runtime is substantially slower while code, PDFs, model, page range, and business parameters are held constant. It no longer attempts a broad forensic comparison or detailed output-quality study.

Current state:

- The company self-test baseline has been entered for the same two target PDFs using Router/MinerU `3.4.2` and `vlm-http-client`.
- Company result for `高等代数 第五版 (北大数学系前线代小组 王萼芳 石生明) .pdf`: 325/325 pages, 38m 5s, 7.03 s/page, 8.53 pages/min, no skipped/failed pages.
- Company result for `空气动力学基础(扫描版)(无目录) (徐华舫).pdf`: 484/484 pages, 54m 5s, 6.70 s/page, 8.95 pages/min, no skipped/failed pages.
- Combined company baseline: 809/809 pages, 92m 10s, weighted 6.84 s/page and 8.78 pages/min.
- Company deployment information is recorded in Sections 4.1–4.4: Kylin V10 ARM64 Router/API controller (64 logical CPUs, 252 GiB RAM; Docker 28.2.2/Compose 2.28.1) calls remote Debian 12 x86_64 host `eastcom224` (2× Xeon Silver 4210, 40 logical CPUs, 251 GiB RAM) with 3× Tesla T4 (15360 MiB each).
- Router/API/Ops use `mineru-env:v1.0`, image ID `sha256:aeb5b704…ad726`; code-sync image is `mineru-code:v3.4.2-ops-ui5`. The runtime container has no Git metadata, so no exact commit was recovered.
- Company API model setting is `OpenDataLab/MinerU2.5-Pro-2604-1.2B`; VLM loads the corresponding local path and exposes served name `mineru2.5-1.2b`. API env explicitly sets both `MINERU_VLM_SERVER_URL` and `MINERU_VL_SERVER` to `http://10.8.132.224:6002/v1`.
- Company API settings recorded: processing window `8`, API max concurrent requests `3`, global page concurrency `2`, page timeout `600 s`, connect retries `1`, failure policy `skip_page`, formula/table enabled. API CLI additionally specifies client concurrency `4`, retries `0`, and HTTP timeout `600 s`.
- Company vLLM is a host process managed via systemd → `start_mineru.sh`, not Docker. PID `2352658` started 2026-06-30 11:05:03 and listens on port `6002`; settings are GPU memory utilization `0.90`, max model length `8192`, prefix caching/chunked prefill enabled, max batched tokens `4096`. Its EngineCore PID `2353167` is on GPU 1. NVIDIA driver and nvcc toolkit are both CUDA 12.6 series.
- Exact VLM runtime versions are now captured: Python 3.10.20, vLLM 0.13.0, torch 2.9.0, transformers 4.57.6, tokenizers 0.22.2, safetensors 0.7.0, triton 3.5.0; xformers/flash-attn are not installed. `/health` returns 200, `/v1/models` maps `mineru2.5-1.2b` to the expected model root with max length 8192, and `/metrics` is reachable (sample running=0, waiting=0).
- A fresh Python process using the VLM interpreter failed at `import torch` with `libtorch_cuda.so: undefined symbol: ncclCommWindowRegister`, despite the long-running vLLM service remaining healthy. Treat this as a dynamic-library/environment consistency risk for future service restarts, not proof that the current running service or historical results are invalid. Revalidate health and a minimal inference after any restart.
- Temporal linkage is now more precise: current Router/API/Ops containers were created/started around 2026-08-07 02:56–02:57Z. The High Algebra task began at 03:03:23Z and therefore used this current container instance; retained Router logs show its repeated status polling and final result download. The Aerodynamics task ran on 2026-08-06 and necessarily used a previous Router/API container instance.
- VLM service predates both tasks by more than a month, so neither benchmark was a model-service startup cold start. Request-level warm state/prefix-cache hits and benchmark-time competing load remain unknown.
- Environment/GPU evidence was captured on 2026-08-10 and cannot establish benchmark-time contention. Historical tasks have expired from the current Router registry (`404`), and status payloads do not retain `server_url`, so endpoint provenance is strong but not formally complete—especially for the August 6 task.
- Company-side unknowns are non-blocking for a basic comparison: Router/API CPU model, previous August 6 container parameters, formal historical endpoint provenance, benchmark-time load/metrics/cache state, the fresh-process torch/NCCL symbol mismatch cause, and output-quality inspection.
- Field results have been entered (2026-08-12). Field ran the same two PDFs sequentially; code/PDF/model/parameters are confirmed identical to company (terminal photo confirmation still pending). Field timings: 高等代数 2h 27m 57s (8877 s), 6 pages skipped (137, 171, 226, 249, 250, 253); 空气动力学基础 3h 10m 55.89s (11455.89 s), 6 pages skipped (161, 318, 325, 400, 445, 448). Combined: 797/809 success, 12 skipped, 5h 38m 53s (20332.89 s), weighted 25.13 s/page, 2.39 pages/min. Ratios vs company: 高等代数 3.88×, 空气动力学基础 3.53×, combined 3.68×. User confirmed the original `525` entry was a typo for page `325`.
- The report's Section 5 (field results), Section 6 (speed ratios), Section 7.1 (current conclusion with 6 points including the 12 skipped pages vs. 0 company-side), Section 7.2 (final statement, placeholder removed), and Section 2 (field column updated from 【待确认】 to "与公司一致", with a premise-pending-photo note) are now filled in.
- Field hardware info arrived (2026-08-12). Field VLM host is `xunlian-01` at `32.15.75.232`, uptime 524 days, ~1 TB RAM, load ~10.66/10.50. Accelerator is **8× Ascend 910B3 NPU** (single-card HBM ~64 GB); at photo time all 8 cards were at AI-Core 83.8%–96.8%, with multiple concurrent `VLLMEngineCore`/`VLLMStageEngi`/`uvicorn`/`python3` processes spread across cards 0–7. This is an 8-card host, superficially different from company's single-T4/single-vLLM-instance exclusive form. Field version/params photo also confirmed identical: MinerU 3.4.2, image `mineru-env:v1.0`, `/health` healthy, model `mineru2.5-1.2b`, and all business params (window 8, API concurrency 3, page concurrency 2, timeout 600 s, retries 1, skip_page, formula/table on) match company.
- Field confirmed the final attribution inputs (2026-08-12). The field host runs two API instances `mineru-api-1`/`mineru-api-2`; since each PDF was submitted one at a time, only `mineru-api-1` was used. `mineru-api-1` targets the VLM on **card 6**, and **card 6 only runs that one large-model service** (no other service on that card). No other concurrent parse tasks ran during the benchmark. This closes the two competing explanations (same-card contention by other services; concurrent parse task load): the benchmark ran on card 6 in a single-vLLM-instance, no-concurrent-parse, near-exclusive state. The slowdown is therefore attributed primarily to **the 910B3 single card (vLLM Ascend edition, `mineru2.5-1.2b`) being slower than the company Tesla T4 (vLLM 0.13.0) on this MinerU VLM workload**, with the host-level concurrent processes on other cards possibly contributing minor CPU/memory-bandwidth/I/O effects but not contending for card 6's inference itself. The 12 skipped pages reflect long-tail pages on 910B3 approaching/exceeding the 600 s timeout.
- The report's Section 3.2 (field hardware + card-6/no-concurrent-parse confirmation), Section 2 (consistency note confirmed-by-photo), Section 7.1 (now 7 points with the card-6/no-concurrency attribution, point 7 = closed), and Section 7.2 (final statement with the 910B3-vs-T4 attribution) are now filled in. No remaining field verification items.

- Skip-page content review (updated after visual inspection). User confirmed the original qd `525` record was a typo for page `325`; all 12 pages are now valid (ga 137/171/226/249/250/253; qd 161/318/325/400/445/448). All 12 pages and their immediate neighbors were rendered and visually inspected. GA failures mostly contain block matrices, Jordan forms, and multi-level formula alignment. QD failures contain dense scan text mixed with curves and technical diagrams; qd 325 (four dense coordinate plots plus a shock diagram) and qd 445 (circular characteristic-line plots plus a dense fan grid) are visually more complex than their immediate neighbors. Page content can amplify VLM visual/layout/structured-output work, but most failures resemble adjacent successful pages and none has abnormal dimensions. The report now distinguishes: direct cause = `600 s` timeout plus `skip_page`; page structures = potential long-tail amplifier; field 910B3 + Ascend vLLM overall speed/long-tail behavior = primary background explaining why the same PDFs skip only on site. Rendered review images remain under `/tmp/mineru-skip-review`; no PDF was modified.
- Highest-priority missing data is field-only: two task-detail photo sets (status, timing, total/success/failed pages), one short Router/API version-and-parameter screen, one accelerator screen, and a note on whether another parse task was running.
- Field collection was reduced to roughly four photos. The standalone field runbook contains the two sequential task instructions, one short Router/API read-only command, one short accelerator read-only command, required photo fields, fallback behavior, and explicit prohibited actions. It does not request OS/package inventories, process trees, VLM metrics, logs, archives, hashes, exported JSON/results, or a third model/command photo.
- The company diagnostic reports did not collect task-time VLM metrics or stage timings, so current numbers are Router end-to-end baselines rather than pure VLM inference benchmarks. This does not block the intended speed-ratio comparison.
- Code and the two PDFs were delivered to the field on the same physical media as the company inputs. The report treats code/input sameness as a test premise and only requires a concise field screen to confirm version, model, and key business parameters.
- Final conclusion wording should emphasize: both environments complete the same extraction; with code/model/parameters aligned and no competing parse task, the field slowdown is primarily attributable to the field accelerator and its driver/inference runtime. Avoid claiming the chip model alone is mathematically proven as the sole cause.

The report is complete and the attribution is now confirmed closed. Code/PDF/model/params are confirmed identical by field terminal photo; the benchmark ran one book at a time via `mineru-api-1` against the VLM on card 6 only (card 6 runs only that model service, no other service on the card), with no concurrent parse tasks during the benchmark. The competing explanations (same-card service contention, concurrent parse-task load) are excluded; the slowdown is attributed primarily to the Ascend 910B3 single card (vLLM Ascend edition) vs the company Tesla T4 (vLLM 0.13.0) on this MinerU VLM workload, with minor host-level effects from other cards' processes possible but not card-6 inference contention. Company single-T4 exclusive baseline vs field card-6 near-exclusive 910B3, 3.68× combined slowdown, 12 skipped pages vs 0. Do not expand the scope back into forensic telemetry or output-quality analysis unless the user explicitly requests it.

## Uncommitted tracked changes

### 1. Standalone deployment templates: documentation-heavy edits

Changed files:

- `docker/deploy/api/.env.example`
- `docker/deploy/api/compose.yaml`
- `docker/deploy/router/.env.example`
- `docker/deploy/router/compose.yaml`
- `docker/deploy/vlm/.env.example`
- `docker/deploy/vlm/compose.yaml`
- `docker/deploy/vlm/compose.ascend.yaml`
- `docker/deploy/vlm/compose.nvidia.yaml`

Current intent:

- Make field deployment understandable without editing Compose directly.
- Explain the split image model: `mineru-code-sync` copies the small code image into a named volume; API/Router/VLM mount that code at `/app:ro` and start modules with `python -m`.
- Document that `mineru-code-sync` exiting with status 0 is expected.
- Document actual container networking constraints: Router/API/VLM addresses must be reachable from the relevant container; container-local `127.0.0.1` is not the host.
- Default standalone VLM port in `docker/deploy/api/.env.example` was changed from `6002` to `30000`.
- VLM deployment now explicitly requires the base file plus exactly one hardware overlay:
  - Ascend: `compose.yaml + compose.ascend.yaml`
  - NVIDIA: `compose.yaml + compose.nvidia.yaml`
- Health checks remain:
  - API: `GET /health` on container port `8000`
  - Router: `GET /health` on container port `8002`
  - VLM: `GET /health` on container port `30000`, with 120 s startup grace
- Named code volume can be isolated with `MINERU_CODE_VOLUME` for parallel versions.

Important caveat: the API Compose still exports `MINERU_VL_SERVER` and `MINERU_VLM_SERVER_URL`, but the multi-deployment documentation explicitly records that current `mineru/` code does not consume these to select a remote VLM. For `backend=hybrid-http-client`, callers must pass `server_url` explicitly. Do not claim the environment variables alone route Hybrid to the remote server without verifying/changing code.

### 2. Multi-deployment documentation

Changed files:

- `docker/multi/现场部署指南.md`
- `docker/multi/交接上下文.md` is reported modified by Git, but its ordinary content diff is empty. This is likely metadata/encoding/line-ending state; inspect carefully before staging or normalizing it.

`现场部署指南.md` was updated to match the current multi-compose architecture:

- `MINERU_DEVICE_MODE=cpu`: base Compose only, no accelerator mapping.
- `MINERU_DEVICE_MODE=npu`: add `compose-multi.npu.yaml`; validate Ascend devices/driver and `torch_npu`.
- `MINERU_DEVICE_MODE=cuda`: add `compose-multi.nvidia.yaml`; requires NVIDIA Container Toolkit and CUDA torch image.
- Image architecture must match `uname -m`; the sample `mineru-env:npu-v1.0` is only for ARM64 + Ascend.
- `VLM_1_IP` may be left empty for Pipeline-only operation; external-VLM checks should then be skipped.
- Hybrid smoke request now passes `server_url=http://<VLM>:30000` explicitly.

Relevant existing implementation (not newly dirty) is in:

- `docker/multi/start-multi.sh`: resolves the hardware overlay from `MINERU_DEVICE_MODE`; provides `import-images`, `check`, `start`, `stop`, `stop-all`, `restart`, `ops`, `status`, `logs`, and `test FILE.pdf`.
- `docker/multi/compose-multi.yaml`: one external VLM + one API + Router + ops console; base file is hardware-neutral.
- `docker/multi/compose-multi.npu.yaml` and `docker/multi/compose-multi.nvidia.yaml`: hardware mappings.
- `docker/multi/env.multi.example`: NPU-oriented example, including page-skip/timeouts/concurrency values.

### 3. Unexplained deletion

- `test-pdf/benchmark_result.md` is deleted in the worktree (75 tracked lines).
- No evidence in this session established that deletion as intentional. Do not commit or restore it without checking with the user or determining its relationship to the ongoing work.

## Untracked diagnostics and local state

### `mineru_diag.py`

Standalone field diagnostic script for VLM HTTP-client latency. It depends on `pypdfium2` plus the standard library; Pillow is optional. It:

- profiles every PDF page (dimensions, rendered megapixels, text count, embedded images, bad pages),
- times pdfium rendering separately from VLM inference,
- exports representative full/half-size samples,
- probes `/health`, `/v1/models`, and `/v1/chat/completions` with `max_tokens=1` vs `64`,
- optionally creates a page-range reproduction slice,
- extracts relevant Docker logs,
- writes `report.txt` and `report.json`, while printing a short photo/OCR-friendly summary card.

The default VLM URL is site-specific: `http://32.15.75.232:6003`. Always override `--url` outside that environment. The script currently shells out with interpolated paths/container names in a few diagnostic-only commands; treat it as a trusted local field tool, not a hardened service endpoint.

### `docs/analysis-error/`

Contains three problem-page images, a Markdown root-cause report, and a DOCX report. Current diagnosis:

- Failure occurs after MinerU sends the VLM request: API waits for Ascend vLLM `/v1/chat/completions` and hits `httpx.ReadTimeout`.
- Suspected cause is content-complexity long-tail latency (low-quality scans, handwriting, mixed scripts/orientations, dense tables) amplified by Ascend throughput/scheduling, not anomalous page pixel dimensions.
- Exact split among server queueing, visual prefill, and decoding is still unproven because original PDFs, per-request token counts, vLLM timing, and NPU telemetry were not captured.
- Recommended initial NPU settings recorded in the report:

```env
MINERU_PROCESSING_WINDOW_SIZE=8
MINERU_API_MAX_CONCURRENT_REQUESTS=1
MINERU_VLM_FAILURE_POLICY=skip_page
MINERU_VLM_GLOBAL_PAGE_CONCURRENCY=2
MINERU_VLM_PAGE_TIMEOUT_SECONDS=600
MINERU_VLM_CONNECT_MAX_RETRIES=1
MINERU_VLM_CLIENT_MAX_CONCURRENCY=4
MINERU_VLM_CLIENT_MAX_RETRIES=0
MINERU_VLM_CLIENT_HTTP_TIMEOUT=600
```

The report states that page skipping limits batch damage but does not make slow pages faster. The next evidence-gathering step is single-page A/B testing on NVIDIA vs Ascend, concurrency 1 vs 2, and `image_analysis=true` vs `false`, with vLLM arrival/first-token/end times and NPU metrics.

### Other untracked paths

- `test.pdf`: two-page local diagnostic fixture; used successfully with `mineru_diag.py --skip-vlm`.
- `mineru-ops-data/ops.db`: local SQLite runtime state. Do not commit casually; may contain task history/site state.
- `.codegraph/`: local code-index metadata (`.gitignore` currently visible as untracked).
- `.mimocode/.cron-lock`: local tool/runtime lock; not implementation.

## Validation performed in this session

Successful:

```bash
# Full unit suite after the 2026-08-19 per-page export work.
python -m pytest -o addopts='' tests/unit/ -q
# Result: 40 passed, 1 skipped

# Focused tests for the recent committed ops/progress behavior.
python -m pytest -o addopts='' \
  tests/unit/test_ops_console.py \
  tests/unit/test_task_progress.py -q
# Result: 21 passed in 1.49s

# Syntax/import check for the diagnostic script.
python -m py_compile mineru_diag.py

# Local smoke run without contacting a VLM.
python mineru_diag.py test.pdf \
  --skip-vlm \
  --render-pages 1 \
  --outdir /tmp/mineru-diag-handoff-check
# Result: 2 pages profiled; median render ~0.017 s; no render errors.

# Standalone Compose rendering.
docker compose -f docker/deploy/api/compose.yaml \
  --env-file docker/deploy/api/.env.example config --quiet
docker compose -f docker/deploy/router/compose.yaml \
  --env-file docker/deploy/router/.env.example config --quiet
docker compose -f docker/deploy/vlm/compose.yaml \
  -f docker/deploy/vlm/compose.ascend.yaml \
  --env-file docker/deploy/vlm/.env.example config --quiet
docker compose -f docker/deploy/vlm/compose.yaml \
  -f docker/deploy/vlm/compose.nvidia.yaml \
  --env-file docker/deploy/vlm/.env.example config --quiet

# Multi-deployment Compose overlays.
docker compose -f docker/multi/compose-multi.yaml \
  -f docker/multi/compose-multi.npu.yaml \
  --env-file docker/multi/env.multi.example config --quiet
docker compose -f docker/multi/compose-multi.yaml \
  -f docker/multi/compose-multi.nvidia.yaml \
  --env-file docker/multi/env.multi.example config --quiet

bash -n docker/multi/start-multi.sh
git diff --check
```

One initially attempted test command failed because this environment does not have the pytest-cov plugin while `pyproject.toml` injects `--cov=mineru --cov-report html` through `addopts`. Use `-o addopts=''` for focused tests here, or install the test extra/plugin before running the repository defaults.

## Commands for the next session

### Re-establish exact state

```bash
git branch --show-current
git log -1 --oneline --decorate
git -c core.quotepath=false status --short
git diff --stat
git diff -- docker/deploy docker/multi/现场部署指南.md
git status --porcelain=v2 -- docker/multi/交接上下文.md test-pdf/benchmark_result.md
```

### Re-run code/unit validation

```bash
python -m pytest -o addopts='' \
  tests/unit/test_ops_console.py \
  tests/unit/test_task_progress.py -q
python -m py_compile mineru_diag.py
git diff --check
```

### Re-run Compose validation

```bash
docker compose -f docker/deploy/api/compose.yaml \
  --env-file docker/deploy/api/.env.example config --quiet

docker compose -f docker/deploy/router/compose.yaml \
  --env-file docker/deploy/router/.env.example config --quiet

docker compose -f docker/deploy/vlm/compose.yaml \
  -f docker/deploy/vlm/compose.ascend.yaml \
  --env-file docker/deploy/vlm/.env.example config --quiet

docker compose -f docker/deploy/vlm/compose.yaml \
  -f docker/deploy/vlm/compose.nvidia.yaml \
  --env-file docker/deploy/vlm/.env.example config --quiet

docker compose -f docker/multi/compose-multi.yaml \
  -f docker/multi/compose-multi.npu.yaml \
  --env-file docker/multi/env.multi.example config --quiet

docker compose -f docker/multi/compose-multi.yaml \
  -f docker/multi/compose-multi.nvidia.yaml \
  --env-file docker/multi/env.multi.example config --quiet
```

### Validate against a real site/VLM

```bash
# Substitute the actual field VLM address.
curl -f http://<VLM_IP>:<VLM_PORT>/health
curl -f http://<VLM_IP>:<VLM_PORT>/v1/models

python mineru_diag.py /path/to/problem.pdf \
  /path/to/known-good.pdf \
  --url http://<VLM_IP>:<VLM_PORT> \
  --vlm-timeout 900 \
  --outdir /path/to/mineru-diag-output

# Multi deployment: requires docker/multi/env.multi and local images/models.
cd docker/multi
./start-multi.sh check
./start-multi.sh start
./start-multi.sh status
./start-multi.sh test /absolute/path/to/test.pdf
```

Do not interpret passing `docker compose config` as runtime validation: it does not verify image availability, model mounts, GPU/NPU devices, VLM reachability, health transitions, or a complete parse request.

## Remaining decisions / TODO

1. Decide the intended deliverable boundary:
   - deployment templates/docs only,
   - diagnostic script/report/assets,
   - or both in separate commits.
2. Resolve the unexplained deletion of `test-pdf/benchmark_result.md` before staging.
3. Inspect why `docker/multi/交接上下文.md` is marked modified with no normal content diff; avoid accidental encoding/line-ending churn.
4. Decide whether `mineru_diag.py`, `docs/analysis-error/`, and `test.pdf` are repository artifacts or temporary/site-sensitive evidence. Sanitize hard-coded IPs and document contents before committing if necessary.
5. Exclude local/runtime data (`mineru-ops-data/ops.db`, `.mimocode/.cron-lock`, probably `.codegraph/`) unless the project explicitly wants it tracked.
6. Run real field validation. No live VLM/NPU request or production Compose startup was performed in this session.
7. If the intended behavior is “Hybrid automatically uses the VLM URL from Compose environment,” implement and test that explicitly; current documentation says callers must supply `server_url`.
