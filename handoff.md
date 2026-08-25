# Session handoff — 2026-08-23

## 2026-08-25：运维控制台 UI/UX 重构（UI20）

### 本轮状态

- 用户已完成并推送运维控制台改进，当前 `dev` 分支提交为：
  - `bb9f1bd2 refactor(ops): console UI/UX overhaul — design tokens, SSE, routing, cards`
- 本轮正式修改文件：

```text
mineru/cli/ops.py
mineru/ops/static/index.html
mineru/ops/static/ops.js
mineru/ops/static/ops.css
```

### 本轮改动摘要

- 建立统一的前端设计 token、颜色、字号、圆角、动效和状态层级，整理服务卡片、配置卡片、弹窗、通知、空状态、错误状态和骨架屏。
- 增加深色主题适配、键盘焦点样式和 `prefers-reduced-motion` 支持；压平过度装饰性的渐变和嵌套视觉层级。
- 任务详情改为 SSE 增量更新：通过 `GET /api/tasks/{task_id}/events` 实时更新任务行和详情面板，终态后自动关闭连接，替代原先每 2 秒整体刷新表格。
- 增加 URL hash 路由和浏览器前进/后退支持，任务详情支持 `#tasks?taskId=<任务ID>` 深链接，批量测试或外部链接进入后可以直接定位任务。
- 统一任务、服务和配置状态的视觉语义为 running/good/warn/bad 四级，补齐加载中、空数据和请求失败状态。
- 重构首页概览 KPI、服务卡片和任务检查器；增加 VRAM/温度信息、原始 PDF 与 Markdown/LaTeX/结构化表格双栏预览、复制 Markdown、通知关闭按钮等交互。
- `mineru/cli/ops.py` 现在从单一 `OPS_VERSION = "2.0.0"` 注入首页、CSS 和 JS 的缓存版本及页面版本徽标；本次代码镜像发布标签按既有规则递增为 `ops-ui20`。

### 本次构建和导出命令

服务器项目目录固定为：`/data/maas/sgy_arm/gd-dev/MinerU`

```bash
cd /data/maas/sgy_arm/gd-dev/MinerU
git fetch origin dev
git checkout dev
git pull --ff-only origin dev

docker build -t mineru-code:v3.4.2-ops-ui20 -f docker/base/Dockerfile.code .

docker save mineru-code:v3.4.2-ops-ui20 | gzip > /data/maas/sgy_arm/gd-dev/MinerU/docker/base/export/mineru-code-v3.4.2-ops-ui20.tar.gz
```

构建标签和导出文件名必须保持一致：

```text
镜像：mineru-code:v3.4.2-ops-ui20
归档：/data/maas/sgy_arm/gd-dev/MinerU/docker/base/export/mineru-code-v3.4.2-ops-ui20.tar.gz
```

### 服务器更新步骤

服务器直接构建镜像时，在 `docker/multi/env.multi` 中把代码镜像改为本次标签：

```bash
cd /data/maas/sgy_arm/gd-dev/MinerU/docker/multi
sed -i 's|^MINERU_CODE_IMAGE=.*|MINERU_CODE_IMAGE=mineru-code:v3.4.2-ops-ui20|' env.multi
grep '^MINERU_CODE_IMAGE=' env.multi
```

然后执行检查、停止并重新启动，使 `mineru-code-sync` 把新镜像内容写入共享 `mineru-code` 卷，并让 Router、API 和 Ops 重新使用新代码：

```bash
./start-multi.sh check
./start-multi.sh stop
./start-multi.sh start
docker restart mineru-ops
./start-multi.sh status
```

`start-multi.sh stop` 默认保留 Ops 容器，因此这里必须在代码卷同步完成后显式重启 `mineru-ops`，让 Ops Python 进程重新加载本轮新增的 SSE 接口和页面路由逻辑。

如果是把归档带到另一台离线服务器，先导入镜像，再将 `env.multi` 设置为同一标签：

```bash
docker load < /data/maas/sgy_arm/gd-dev/MinerU/docker/base/export/mineru-code-v3.4.2-ops-ui20.tar.gz
```

### 升级后核验

- `./start-multi.sh status` 确认 `mineru-code-sync-multi`、`mineru-api-1`、`mineru-router`、`mineru-ops` 状态正常；`mineru-code-sync-multi` 正常退出（`Exited (0)`）即可。
- 访问 `http://服务器IP:19000`，浏览器执行 `Ctrl+F5` 清除旧静态资源缓存。
- 检查首页 HTML 中 CSS/JS 资源带有 `?v=2.0.0`，配置中心版本徽标显示 `2.0.0`。
- 打开任务详情，确认任务状态通过 SSE 增量变化；刷新或复制带 `#tasks?taskId=...` 的链接，确认能直接定位任务。
- 检查服务卡片、配置中心、日志页和任务预览的加载中、空数据、失败及深色主题状态。

### 部署注意

- 本轮只改了 Ops Web/后端控制台代码，没有修改 `docker/multi/mineru-ops-agent.py`；宿主机 Agent 无需因本提交单独替换，但若现场 Agent 文件不是当前 `dev` 版本，仍应按现场流程同步。
- 使用共享 `/app` 或 `mineru-code` code volume 时，只替换环境镜像或只重启浏览器不会更新源码；必须让 `mineru-code-sync` 运行并重建/启动 `mineru-api-1`、Router 和 `mineru-ops`。
- `mineru-ops-data` 是独立数据卷，重建容器不会删除控制台任务历史、实验归档和审计日志。
- 如果现场使用 `docker/multi/update-code.sh`，可执行 `./update-code.sh v3.4.2-ops-ui20`；该脚本会构建代码镜像、更新 `MINERU_CODE_IMAGE` 并重启业务服务。离线导入归档时使用上面的 `docker load` 加手动更新 `env.multi` 流程。

## 2026-08-25：补充 MinerU 与 RAGFlow 提取效果结论

- 更新 `docs/analysis-error/双环境PDF解析性能综合对比报告-修改版.md`。
- 新增明确判断：在本次技术 PDF 交付场景下，MinerU 的提取效果整体优于 RAGFlow，更适合公式/矩阵、表格行列结构、标题与段落阅读顺序、图片/图注关系，以及 Markdown、Middle JSON、Content List 等结构化结果。
- 明确 RAGFlow 更适合后续知识库切分、检索和问答，不作为本次高保真 PDF 提取效果的替代基准。
- 同时保留边界说明：当前没有统一版本、模型、解析器和参数下的逐页盲测，因此不虚构具体准确率提升百分比。

## 2026-08-25：重写双环境 MinerU 性能报告，弱化 RAGFlow 速度结论

- 重写 `docs/analysis-error/双环境PDF解析性能综合对比报告.md`，报告主线改为 MinerU 两套环境的同条件性能、超时、慢页和输出能力。
- RAGFlow 两侧速度表、合计数字和方向性比较不再作为正文核心结论；仅说明现有资料缺少版本、解析器、模型、完成页数和质量记录，无法增强硬件或 MinerU 结论。
- 新增 MinerU 输出能力背景：公式/LaTeX、表格结构、Markdown、Middle JSON、Content List、图文版面和标题层级，强调本次技术 PDF 的交付目标不是单纯 OCR 或任务耗时。
- 新增官方 Issue 外部佐证并保留证据边界：
  - #5361：昇腾 910B3 长时间高负载解析成功率下降、ACL `507035` / `ACL_ERROR_RT_VECTOR_CORE_EXCEPTION`；
  - #5322：NPU + vLLM 解析速度偏低，扩展节点后吞吐反而下降，对照 NVIDIA 3090 更快；
  - #5293：Windows managed parse-server 渲染超时，说明超时还需区分服务运行上下文，不能简单归咎于 PDF 内容。
- Issue 内容只作为外部工程背景，不与本报告现场数据混算，也不宣称完全复现。

本轮报告修改尚未单独构建镜像；提交后按既有流程推送 GitHub `dev` 分支。

## 2026-08-24：UI19 日志排除过滤、批量删除与配置界面整理

### 本轮完成

- 日志页新增“排除关键词”输入框，支持逗号分隔多个排除词；默认隐藏 `GET /health`、`/ready`、`/live` 等成功探活日志，仍可关闭隐藏并查看原始行。
- 批量测试页新增可删除终态记录的多选和批量删除；新增 `POST /api/batch-runs/bulk-delete`，逐条返回 `deleted`/`failed`，活动中的记录不会被删除。
- 性能实验室新增“选择当前筛选”和批量删除，复用批量删除接口；删除后自动刷新实验归档和批量测试记录。
- 批量测试关联 Task 链接统一进入任务页并立即调用 `loadTaskDetail`，跳转后直接打开对应任务详情。
- 配置最终有效值卡片调整为更明确的分层卡片布局；配置项帮助按钮增加兜底说明和使用提示，缺失 schema `help` 时不再出现空白弹窗。
- 静态资源版本更新为 `ui19`。

### 修改文件

```text
mineru/cli/ops.py
mineru/ops/static/index.html
mineru/ops/static/ops.js
mineru/ops/static/ops.css
tests/unit/test_ops_console.py
handoff.md
```

### 验证

```text
python -m pytest -o addopts='' tests/unit/test_ops_console.py -q
# 43 passed
python -m py_compile mineru/cli/ops.py
node --check mineru/ops/static/ops.js
git diff --check
```

### 注意

- 批量删除只接受终态记录；运行中、暂停中或仍有本地批处理进程的记录会返回失败原因，需要先停止。
- 日志过滤在浏览器端对已拉取的 tail 内容生效，不改变服务端日志采集范围；排查时可先增大 tail，再组合包含/排除条件。

### 提交与服务器构建发布流程

- 本轮完成后需要提交并推送 GitHub `dev` 分支；后续每轮完成也沿用该流程。
- 服务器项目目录固定为：`/data/maas/sgy_arm/gd-dev/MinerU`。
- 服务器拉取代码：

```bash
cd /data/maas/sgy_arm/gd-dev/MinerU
git fetch origin dev
git checkout dev
git pull --ff-only origin dev
```

- 代码镜像版本按小版本号递增。上次示例为 `mineru-code:v3.4.2-ops-ui17`；如果下一次发布使用 UI18，服务器构建命令为：

```bash
cd /data/maas/sgy_arm/gd-dev/MinerU
docker build -t mineru-code:v3.4.2-ops-ui18 -f docker/base/Dockerfile.code .
```

- 保存镜像：

```bash
docker save mineru-code:v3.4.2-ops-ui18 | gzip > /data/maas/sgy_arm/gd-dev/MinerU/docker/base/export/mineru-code-v3.4.2-ops-ui18.tar.gz
```

- 以后继续递增 `ops-ui18`、`ops-ui19`、`ops-ui20`，构建标签和导出文件名必须保持一致。本轮工作区静态资源已更新为 `ui19`，若部署本轮代码应使用 `mineru-code:v3.4.2-ops-ui19` 和对应的 `mineru-code-v3.4.2-ops-ui19.tar.gz`。
- 若服务器使用共享 `/app` code volume，构建镜像后仍需按现场流程刷新或重建 code volume，并重建 `mineru-ops`、API、Router 等相关服务；部署后浏览器执行 `Ctrl+F5`。

## 2026-08-23 完成：UI15 性能实验档案、统计与对比导出

本轮把运维控制台的性能实验从“临时批量测试”提升为可复盘的实验档案闭环，方便现场对不同 batch、超时和重试策略进行对比，而不需要手工抄录结果。

### 性能实验能力

- 批量测试表单新增性能实验元数据：实验名称、环境名称、硬件类型、引擎和备注。
- 支持 batch `1/2/4/8/16/32`，并可分别设置单页 VLM timeout、任务 timeout 和连接重试次数。
- 性能实验使用 `settings.experiment_type == "performance_lab"` 标记；普通批量测试仍为 `batch_test`，历史普通测试不会混入实验归档。
- 实验启动时快照 Git commit/dirty 状态、MinerU 版本、容器镜像、主机名、平台、Python 版本、effective config、PDF 文件清单、页数、文件 SHA256、数据集 SHA256 和请求参数。
- 配置快照对 token、secret、password、API key、access key、private key、auth 等敏感字段脱敏；快照获取失败只记录 warning，不阻断实验。
- RAGFlow/其他引擎目前只是实验归档标签，自动执行逻辑仍然是 MinerU。

### 页级统计与比较

- 归档总页数、成功页、失败页、待处理页、P50/P95/max 页耗时、最慢页 Top 10、timeout 页、retry 页。
- 同时记录成功 attempt 耗时、失败 attempt 耗时、retry 等待/开销、无重试耗时、有重试耗时、总耗时和 pages/minute。
- 只要某页任意 attempt 出现 timeout，该页就统计为 timeout page；本轮同时修复了原始/旧归档记录未带 `timeout` 字段时漏计的问题。
- 支持实验归档搜索、环境/硬件筛选、多选比较，以及 JSON、CSV、Markdown 导出。
- 比较基线优先选择：已完成记录 → batch=1 → 相同 dataset SHA256 → 最早创建记录。

### 文件、资源与验证

正式修改文件：

```text
mineru/cli/ops.py
mineru/ops/static/index.html
mineru/ops/static/ops.js
mineru/ops/static/ops.css
tests/unit/test_ops_console.py
handoff.md
```

验证结果：

```text
python -m pytest -o addopts='' tests/unit/test_ops_console.py -q
# 43 passed

python -m pytest -o addopts='' tests/unit/test_ops_console.py tests/unit/test_ops_agent_config.py tests/unit/test_api_request.py tests/unit/test_vlm_resilience.py tests/unit/test_batch_router_diagnose.py -q
# 109 passed

python -m py_compile mineru/cli/ops.py       # 通过
node --check mineru/ops/static/ops.js       # 通过
git diff --check                           # 通过
```

目标镜像：

```text
mineru-code:v3.4.2-ops-ui15
/data/maas/sgy_arm/gd-dev/MinerU/docker/base/export/mineru-code-v3.4.2-ops-ui15.tar.gz
```

### 现场升级注意事项

- 性能实验归档仅收录 `settings.experiment_type == "performance_lab"` 的批量记录。
- 共享 `/app` code volume 时，只换镜像标签可能仍运行旧代码；必须按现场既有流程刷新或重建 code volume，并重建 `mineru-ops`、API、Router 等相关服务。
- 宿主机单独部署 `mineru-ops-agent.py` 时，也要同步本轮代码并重启 Agent。
- 升级后浏览器执行 `Ctrl+F5`，确认加载 `ops.js?v=ui15`、`ops.css?v=ui15`。
- 配置/实验参数变更只影响重建后的新进程和新请求，正在处理的请求不会动态继承新配置。

提交和推送状态：已提交并推送到 GitHub `dev` 分支。提交为 `d91beef9 feat: archive and compare performance experiments`。

## 2026-08-23 完成：UI14 配置最终态核验与失败自动回滚可视化

本轮把配置中心从“写入 env.multi 并重建服务”提升为“验证最终生效值、分步展示执行过程、失败后自动恢复”的闭环，目标是让现场人员能够确认配置究竟是否被 API/Router 实际采用，而不是只看到配置文件已经保存。

### 最终有效配置

- 新增 Agent action `config_effective` 和后端接口 `GET /api/config/effective`；原有 `GET /api/config/status` 继续兼容保留。
- 对 `mineru-api`、`mineru-api-*`、Router 和 Ops 的适用配置逐项展示配置值、容器环境变量、命令行参数、已知默认值及最终有效值。
- 最终值按 `Docker 实际启动 command > 容器环境变量 > 已知默认值` 推断。这里是基于 `docker inspect` 的可解释推断，不是直接进入 Python 进程读取变量。
- 支持解析 `--flag value` 和 `--flag=value`，覆盖 API 的 `--max-concurrency`、`--max-retries`、`--http-timeout`。
- 新增并纳入配置中心：`MINERU_VLM_CLIENT_MAX_CONCURRENCY`、`MINERU_VLM_CLIENT_MAX_RETRIES`、`MINERU_VLM_CLIENT_HTTP_TIMEOUT`。
- 多 API 实例最终值不同会显示 `inconsistent`；HTTP client timeout 小于 page timeout 会显示 `conflict`；Compose 状态无法读取时整体状态为 `unknown`，不再误报已生效。
- `docker inspect` 中的敏感环境变量和命令行参数在进入响应前即脱敏，返回结构再递归脱敏，避免运行容器中的旧 token/password/secret 泄漏。

### 安全应用与回滚

配置 Apply/Restore 统一展示七步执行结果：

1. 配置校验；
2. Compose 校验；
3. 配置备份；
4. 写入配置；
5. 重建服务；
6. 健康检查；
7. 最终生效验证。

- 每一步标记 `completed`、`failed`、`skipped` 或 `pending`。
- 最终有效值与目标值不一致会判定应用失败并触发自动回滚。
- 失败返回并展示 `rollback_status`、`env_restored`、`services_restored`、`original_error`、`rollback_error` 和 `manual_actions`。
- 自动恢复不完整时，控制台会给出现场手工操作命令，并提供复制按钮。
- `MINERU_OPS_*` 仍不会由当前请求自重启 Ops，界面会明确提示现场手工重启，避免中断自身请求。
- Compose 服务状态读取失败时，健康等待会快速失败，不再无意义等待完整超时时间。

### UI 与版本

- 配置中心新增“最终有效配置”和七步 Apply/Restore 结果区域，支持来源、实例状态、health、warning、冲突和不一致展示。
- 业务失败保留 Agent 返回的结构化步骤和回滚信息；浏览器网络异常也会生成统一失败结果。
- 配置最终态会自动刷新；移动端布局同步适配。
- 首页徽标更新为 `UI14`，静态资源为 `ops.js?v=ui14`、`ops.css?v=ui14`。
- 目标镜像：`mineru-code:v3.4.2-ops-ui14`。
- 导出文件：`/data/maas/sgy_arm/gd-dev/MinerU/docker/base/export/mineru-code-v3.4.2-ops-ui14.tar.gz`。

### 本轮正式文件

```text
docker/multi/mineru-ops-agent.py
mineru/cli/ops.py
mineru/ops/static/index.html
mineru/ops/static/ops.js
mineru/ops/static/ops.css
tests/unit/test_ops_agent_config.py
tests/unit/test_ops_console.py
handoff.md
```

### 验证结果

```text
python -m py_compile docker/multi/mineru-ops-agent.py mineru/cli/ops.py   # 通过
node --check mineru/ops/static/ops.js                                    # 通过
python -m pytest -o addopts='' tests/unit/test_ops_agent_config.py tests/unit/test_ops_console.py -q
# 73 passed
python -m pytest -o addopts='' tests/unit/test_ops_console.py tests/unit/test_ops_agent_config.py tests/unit/test_api_request.py tests/unit/test_vlm_resilience.py tests/unit/test_batch_router_diagnose.py -q
# 106 passed
git diff --check                                                         # 通过
```

### 现场升级提示

- 若使用共享 `/app` code volume，必须按现场既有流程刷新或重建 code volume；只更新镜像标签仍可能运行旧代码。
- `mineru-ops-agent.py` 属于宿主机 Agent 部署内容时，也要同步本轮文件并重启 Agent。
- 重建 `mineru-ops`、受影响 API 及 Router 后，浏览器执行 `Ctrl+F5`，确认加载 `ops.js?v=ui14`、`ops.css?v=ui14`。
- 配置变更只影响重建后的新进程和新请求；正在运行的请求不会动态继承新配置。

## 2026-08-22 完成：独立版双环境 PDF 解析性能报告

- 新增独立报告：`docs/analysis-error/双环境PDF解析性能综合对比报告.md`。
- 报告直接基于当前五本 PDF、现场/公司两份 Excel、现场慢页单页复测数据和 RAGFlow 数据编写，不引用其他版本报告，也不使用“上一版/新增/补测”等历史叙述。
- 统一纳入 MinerU 分文档耗时、五本合计耗时、吞吐、VLM 平均/P50/P95/最慢请求、现场慢页真实处理时间、RAGFlow 任务耗时及数据限制。
- 保留并说明空气动力学 PDF 物理页数 484 与 Excel 记录 485 的差异；汇总计算按 Excel 记录口径使用。
- 本报告核心汇总：MinerU 现场 443 分钟、公司 166 分钟，现场为 2.67 倍；现场扣除超时等待估算为 303 分钟，仍为公司 1.83 倍；RAGFlow 现场 106 分钟、公司 215 分钟。

## 2026-08-22 完成：五文档及 RAGFlow 补测版性能报告

### 本轮完成

- 基于 `docs/analysis-error/现场解析测试.xlsx` 和 `docs/analysis-error/公司解析测试.xlsx`，重新整理五本 PDF 的 MinerU 对比结果。
- 纳入现场新增的 14 个明确慢页单页复测数据，并区分 Excel 的“慢页数量”和“跳页页码”统计口径。
- 纳入现场与公司 RAGFlow 五本 PDF 的用时数据，但明确标注 RAGFlow 缺少版本、解析器、模型、参数、完成页数和质量数据，暂不作硬件性能结论。
- 核验 PDF 文件页数：空气动力学 PDF 物理页数为 484，而 Excel 最新测试记录为 485；报告按 Excel 任务口径统计并专门说明差异。
- 新增报告：`docs/analysis-error/双环境PDF解析速度对比报告（五文档及RAGFlow补测版）.md`。旧报告未覆盖。

### 核心数据

- 五本按 Excel 共 1342 页。MinerU 现场总用时 443 分钟，公司总用时 166 分钟，现场为公司的 2.67 倍，现场原始吞吐约为公司 37.5%。
- 按 Excel“去除超时页总用时”估算，现场为 303 分钟，仍为公司 1.83 倍；该列被报告明确称为扣除超时等待后的估算，不等同于完整无超时重跑。
- 现场平均 VLM 请求耗时均高于公司，倍率 1.55–3.09 倍；P95 倍率 2.15–4.26 倍；高代和空气动力学最慢请求约为公司 8.93–9.16 倍。
- 14 个明确列出的慢页复测中，13 页为 25–61 分钟，最慢为空气动力学第 325 页 61 分钟；第 207 页 0.5 分钟，报告作为异常例外处理。
- RAGFlow 现场合计 106 分钟、公司合计 215 分钟；报告仅作为当前部署补充观察。

### 当前待确认事项

1. 如需正式对外/供应商评审，补充两侧完整环境快照、逐页事件时间线和现场慢页重复测试。
2. 补齐 RAGFlow 两侧版本、解析器、模型、批处理参数、完成页数、失败页和质量抽样。
3. 确认 Excel 中“慢页数量”与“跳页页码”不一致的业务含义。
4. 进一步确认 600 秒超时后 HTTP 请求、VLM 推理、并发槽和任务状态是否真正取消/释放。

## 2026-08-22 完成：UI11 性能实验室与 micro-batch 32

本轮继续增强性能实验室，目标是让现场可以用同一批 PDF 对比不同 VLM micro-batch 的吞吐、平均页耗时、异常页和 batch=1 基线，并避免把普通批量测试混入实验历史。

### 已完成

- `vlm_batch_size` 已贯通 API 校验、VLM resilience、诊断脚本和运维控制台，允许范围从 `1-32`，默认仍为 `1`。
- 新增 `experiment_type`：普通批量测试为 `batch_test`，性能实验为 `performance_lab`；性能实验历史单独过滤。
- 批次 API 增加 `metrics`：总页数、成功页、异常页、待处理页、已处理页、总耗时、页/分钟、成功页/分钟、平均页耗时和是否完整结束。
- 性能实验表格增加吞吐量、平均页耗时、相对 batch=1 基线和稳定配置推荐；完整结束且无异常页的结果才可推荐，快速失败或有异常页不作为稳定推荐。
- 性能实验 UI 增加 32 档和压力测试提示；16/32 可能增加显存/NPU 内存、耗时和失败概率，建议先测 1、2、4、8。

### 修改文件

```text
mineru/cli/ops.py
mineru/cli/api_request.py
mineru/backend/vlm/resilience.py
docker/multi/batch-router-diagnose.py
docker/multi/env.multi.example
mineru/ops/static/index.html
mineru/ops/static/ops.js
mineru/ops/static/ops.css
tests/unit/test_ops_console.py
tests/unit/test_vlm_resilience.py
tests/unit/test_api_request.py
tests/unit/test_batch_router_diagnose.py
handoff.md
```

### 现场镜像

```text
mineru-code:v3.4.2-ops-ui11
/data/maas/sgy_arm/gd-dev/MinerU/docker/base/export/mineru-code-v3.4.2-ops-ui11.tar.gz
```

现场拉取 `dev` 后构建并导出；如果使用共享 `/app` code volume，仍需按现场既有流程刷新或重建 code volume，并重建 `mineru-ops`、Router/API 等相关服务。浏览器升级后执行 `Ctrl+F5`，确认静态资源为 `ops.js?v=ui11`、`ops.css?v=ui11`。

### 当前验证

```text
node --check mineru/ops/static/ops.js
python -m py_compile mineru/cli/ops.py mineru/cli/api_request.py mineru/backend/vlm/resilience.py docker/multi/batch-router-diagnose.py
python -m pytest -o addopts='' tests/unit/test_ops_console.py tests/unit/test_task_progress.py tests/unit/test_api_request.py tests/unit/test_vlm_resilience.py tests/unit/test_batch_router_diagnose.py -q
git diff --check
```

## 2026-08-22 完成：UI10 服务状态与界面易用性优化

本轮继续增强运维控制台的现场可读性和响应式体验，重点解决服务状态容易混淆、配置长值难以查看以及批量详情在不同屏幕尺寸下使用不舒适的问题。

### 服务状态展示

- 服务卡片和服务表格明确区分：容器运行状态、Docker health、HTTP 探活状态和服务 endpoint。
- 容器运行状态统一显示“运行中/重启中/已暂停/已退出”等中文状态。
- Docker health 统一显示“健康/异常/启动中/无探活/未知”，并根据 Docker health 使用对应颜色；不会再错误地用容器运行状态给 Docker health 着色。
- Docker health 和 runtime 值统一转为小写后再判断，兼容 Docker 返回值大小写差异。
- HTTP 探活显示 HTTP 状态码或具体错误信息；服务 endpoint 支持长地址换行。
- 服务卡片保留整体服务可用性颜色，同时增加 processing window 和正在处理任务数，便于判断 API 是否有负载。

### 配置中心与批量详情界面

- 配置中心只读长值支持自动换行，并通过鼠标悬停 `title` 查看完整值。
- 配置错误项移除负 margin，避免错误提示挤压相邻配置项。
- 批量详情对话框桌面端最大约 `1400×900`，中等屏幕保留可用空间，移动端继续全屏显示。
- 批量表单、统计卡片和任务问题列表优化 `minmax()`、边框和响应式布局，减少窄屏错位与内容压缩。
- 侧栏底部状态文字提高对比度，方便现场查看连接状态。

### 资源与版本

- 页面徽标更新为 `UI10`。
- 静态资源使用 `ops.js?v=ui10`、`ops.css?v=ui10`；现场升级后建议浏览器执行 `Ctrl+F5`。
- 目标代码镜像：`mineru-code:v3.4.2-ops-ui10`。
- 目标导出文件：`/data/maas/sgy_arm/gd-dev/MinerU/docker/base/export/mineru-code-v3.4.2-ops-ui10.tar.gz`。

### 本轮涉及文件

```text
mineru/ops/static/ops.js
mineru/ops/static/ops.css
mineru/ops/static/index.html
tests/unit/test_ops_console.py
handoff.md
```

### 验证结果

```text
node --check mineru/ops/static/ops.js                                      # 通过
python -m py_compile mineru/cli/ops.py                                    # 通过
python -m pytest -o addopts='' tests/unit/test_ops_console.py -q           # 35 passed
python -m pytest -o addopts='' tests/unit/test_ops_console.py tests/unit/test_task_progress.py tests/unit/test_api_request.py tests/unit/test_vlm_resilience.py tests/unit/test_batch_router_diagnose.py -q  # 68 passed
git diff --check                                                        # 通过
```

### 现场升级提示

- 现场拉取 `dev` 后，用 `docker/base/Dockerfile.code` 构建上述 UI10 代码镜像。
- 导出 tar.gz 后在现场执行 `docker load`，并将 `env.multi` 中代码镜像改为 `mineru-code:v3.4.2-ops-ui10`。
- 若代码通过共享 code volume 注入容器，必须按现场既有流程刷新或重建该 code volume，再重建 `mineru-ops` 和相关服务；仅替换镜像标签可能仍然使用旧 volume 内容。
- 配置中心变更后仍需重建受影响 API/Router 容器；正在处理的请求不会动态继承新配置。

## 2026-08-21 完成：UI9 批量测试可靠停止

本轮完成运维控制台批量测试的可靠停止机制，目标是避免点击停止后页面状态与本地批量脚本实际状态不一致，也避免暂停中的脚本无法被终止。

### 停止行为

- 支持停止 `pending`、`running`、`paused` 批次。
- 新增 `cancelling` 过渡状态；进入停止流程后，前端显示“正在停止…”并禁用重复停止。
- 停止期间禁止暂停、继续、重试全部、重试异常页和删除，避免并发操作破坏批次记录。
- `paused` 批次停止前先发送 `SIGCONT`，再发送 `terminate()`，确保被 `SIGSTOP` 挂起的子进程能够收到终止信号。
- `terminate()` 默认等待 `5 秒`；超时后执行 `kill()`，避免批次脚本或其子进程卡死导致控制台一直等待。
- 运维控制台 runner 在取消后不会再把状态覆盖成 `completed`、`completed_with_failures` 或其他成功状态。
- 关闭 OpsRuntime 时也会停止活动批次，并对暂停中的批次执行恢复、终止和必要的强制杀死。

### 重要边界

这里停止的是运维控制台本地启动的：

```text
batch-router-diagnose.py
```

它不会取消已经提交到 MinerU Router/API 的远程任务。当前 MinerU 的远程任务取消能力不完整，因此点击“停止批次脚本”后，已经发给 Router/API 的任务仍可能继续运行；重新上传或重试前应确认现场资源状态。

批量测试里的页面 soft timeout 与停止机制是两件事：

- `page_timeout_seconds` / `MINERU_VLM_PAGE_TIMEOUT_SECONDS` 只控制单页请求等待上限；
- `MINERU_VLM_CLIENT_HTTP_TIMEOUT`、API `--http-timeout`、网关和外部调用方 timeout 属于另一层 hard timeout；
- 配置中心修改后必须重建受影响的 API/Router 容器，后续新请求才会读取新配置；正在运行的请求不会动态继承新值。

### UI9 资源与版本

- 首页显示 `UI9`。
- 静态资源使用 `ops.js?v=ui9`、`ops.css?v=ui9`，并保留首页 `Cache-Control: no-store`；现场升级后建议浏览器执行 `Ctrl+F5`。
- 目标代码镜像：`mineru-code:v3.4.2-ops-ui9`。
- 目标导出文件：`/data/maas/sgy_arm/gd-dev/MinerU/docker/base/export/mineru-code-v3.4.2-ops-ui9.tar.gz`。
- 若现场仍未同步 UI8 Agent，配置中心的“保存并应用/回滚”能力还需要同步宿主机上的 `docker/multi/mineru-ops-agent.py`；UI9 本轮只改批量停止相关控制台代码。

### 本轮涉及文件

```text
mineru/cli/ops.py
mineru/ops/static/ops.js
mineru/ops/static/index.html
mineru/ops/static/ops.css
tests/unit/test_ops_console.py
handoff.md
```

### 验证结果

```text
python -m pytest -o addopts='' tests/unit/test_ops_console.py -q  # 35 passed
python -m pytest -o addopts='' tests/unit/test_ops_console.py tests/unit/test_task_progress.py tests/unit/test_api_request.py tests/unit/test_vlm_resilience.py tests/unit/test_batch_router_diagnose.py -q  # 68 passed
node --check mineru/ops/static/ops.js                       # 通过
python -m py_compile mineru/cli/ops.py                      # 通过
git diff --check                                           # 通过
```

新增/覆盖的停止测试包括：pending 停止、running 终止、paused 先 SIGCONT、terminate 超时后 kill、runner 不覆盖 cancelled、重复停止返回 409、关闭 Runtime 时停止 paused 批次，以及活动状态禁止异常页重试。

## 2026-08-21 完成：外部 Router/API 继承配置中心 VLM 超时设置

修复了外部系统直接调用 MinerU Router 或 API 时，未传超时参数便固定使用 `600 秒 / 1 次重试`、无法继承运维控制台配置中心设置的问题。

### 生效规则

`POST /tasks` 和 `POST /file_parse` 现在统一按以下优先级解析：

```text
请求显式参数 > 配置中心环境值 > 内置默认值
```

对应参数与环境变量：

```text
page_timeout_seconds       -> MINERU_VLM_PAGE_TIMEOUT_SECONDS       -> 默认 600
page_connect_max_retries   -> MINERU_VLM_CONNECT_MAX_RETRIES        -> 默认 1
```

- 外部调用方不传字段时，API 直接读取自身环境配置；Router 读取自身环境配置后，把最终值补入转发给上游 API 的 multipart 请求。
- 外部调用方显式传入字段时，Router 不覆盖该值，因此单次请求仍可做独立实验。
- `docker/multi/compose-multi.yaml` 已把这两个变量同时注入 `mineru-api-1` 和 `mineru-router`。
- 在运维控制台执行“保存并应用”并成功重建受影响的 API/Router 后，后续外部调用会使用新配置；正在运行中的旧请求不会被动态修改。

### 超时边界说明

这里不是无限等待：

- 单页 soft timeout 允许范围为 `1-7200` 秒；
- `MINERU_VLM_CLIENT_HTTP_TIMEOUT` / API `--http-timeout` 是另一层 HTTP hard timeout，配置中心当前允许到 `14400` 秒；
- 最终实际可等待时长仍取决于整条链路中最短的超时，包括页面 soft timeout、MinerU HTTP client、VLM 服务、Nginx/负载均衡和外部调用方自己的 HTTP client timeout。

因此现场若设置 `page_timeout_seconds=1200`，还应确保 HTTP hard timeout、网关及调用方超时均不小于 1200 秒。

### 涉及文件

```text
mineru/cli/api_request.py
mineru/cli/router.py
docker/multi/compose-multi.yaml
tests/unit/test_api_request.py
handoff.md
```

### 验证结果

```text
python -m py_compile mineru/cli/api_request.py mineru/cli/router.py                         # 通过
python -m pytest -o addopts='' tests/unit/test_api_request.py -q                          # 8 passed
python -m pytest -o addopts='' tests/unit/test_vlm_resilience.py -q                       # 9 passed
python -m pytest -o addopts='' tests/unit/test_ops_agent_config.py tests/unit/test_ops_console.py tests/unit/test_batch_router_diagnose.py -q  # 48 passed
docker compose --env-file docker/multi/env.multi.example -f docker/multi/compose-multi.yaml config --quiet  # 通过
git diff --check                                                                            # 通过
```

共 65 个相关测试通过。

## 2026-08-21 完成：UI8 配置安全应用、回滚与界面升级

本节是配置中心的最新状态，覆盖下方 UI7“第一阶段只读”的旧记录。目标分支为 `dev`，提交标题为 `feat: enable safe ops configuration apply workflow`。

### 用户可见入口与按钮

进入 **运维控制台 → 左侧“配置中心”**，可看到：

```text
重新读取
校验候选配置
预览变更
保存并应用
回滚到此版本（配置历史每条记录内）
```

页面静态资源使用 `ops.css?v=ui8`、`ops.js?v=ui8`，首页响应增加 `Cache-Control: no-store`。现场若看不到按钮，说明仍在使用旧代码镜像、旧共享 code volume、旧 `mineru-ops` 容器或浏览器缓存，并非功能开关未打开。

### 配置写入与回滚安全机制

- `预览变更` 只生成 old → new 差异、Compose 校验结果、受影响服务和执行计划，不修改 `env.multi`。
- `保存并应用` 必须先确认计划，写操作要求设置 `MINERU_OPS_AUTH_TOKEN`，前端使用 `X-MinerU-Ops-Token`；未配置 Token 时读取、校验和预览可用，应用与回滚返回 403。
- 写入时保留原有注释、空行、未知变量、变量顺序和可选 `export` 前缀；新增白名单变量会附带易懂中文注释。
- 使用同目录临时文件原子替换 `env.multi`，应用前自动生成 `env.multi.bak-*` 备份。
- Token、Password、Secret 等敏感项在读取、预览、响应与审计中均脱敏；敏感输入留空表示保留原值。
- 候选配置先执行类型、枚举、范围和换行校验，再执行 `docker compose ... config --quiet`。
- 只对受影响的 API/Router 服务执行 `up -d --no-deps --force-recreate`；只修改 `MINERU_OPS_*` 时不会错误重建 API/Router。
- `mineru-ops` 不自行重建自身。计划和结果会明确提示现场手动重建 Ops，使端口、Token 等 Ops 配置生效。
- 服务重建失败时恢复原 `env.multi`，并尝试按旧配置恢复相关服务；响应明确标记 `rolled_back`。
- 配置历史可点击 `回滚到此版本`。Agent 只允许恢复当前 `env.multi` 同目录、符合备份命名规则的文件，避免路径穿越。
- 无实际变化时按 no-op 处理，不生成备份、不重建服务。
- `MINERU_VLM_FAILURE_POLICY` 可选值与实际实现保持一致：`fail_fast` / `skip_page`。

### UI8 涉及文件

```text
docker/multi/mineru-ops-agent.py
mineru/cli/ops.py
mineru/ops/static/index.html
mineru/ops/static/ops.css
mineru/ops/static/ops.js
tests/unit/test_ops_agent_config.py
tests/unit/test_ops_console.py
handoff.md
```

### UI8 验证结果

```text
python -m py_compile docker/multi/mineru-ops-agent.py mineru/cli/ops.py                    # 通过
node --check mineru/ops/static/ops.js                                                    # 通过
python -m pytest -o addopts='' tests/unit/test_ops_agent_config.py -q                    # 10 passed
python -m pytest -o addopts='' tests/unit/test_ops_console.py -q                         # 27 passed
python -m pytest -o addopts='' tests/unit/test_batch_router_diagnose.py -q               # 11 passed
git diff --check                                                                          # 通过
```

共 48 个相关测试通过。

### UI8 现场升级重点

代码镜像固定为：

```text
mineru-code:v3.4.2-ops-ui8
```

导出文件固定为：

```text
/data/maas/sgy_arm/gd-dev/MinerU/docker/base/export/mineru-code-v3.4.2-ops-ui8.tar.gz
```

现场必须同时更新两部分：

1. 导入 UI8 代码镜像并设置 `MINERU_CODE_IMAGE=mineru-code:v3.4.2-ops-ui8`，让 `mineru-code-sync-multi` 把新代码写入共享 code volume。
2. 更新部署宿主机上的 `docker/multi/mineru-ops-agent.py`，随后通过 `start-multi.sh` 重建/启动业务服务和 `mineru-ops`。

只更新镜像但未刷新共享 code volume，或只更新 Web 容器但未更新宿主机 Agent，都会导致按钮或应用配置能力不完整。升级后重建 `mineru-ops` 并在浏览器执行 `Ctrl+F5`。

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

---

## 2026-08-23：运维控制台 P1 改进收尾（UI12）

本轮继续完成运维控制台 P1 的实现核对、测试补齐和回归验证。没有构建镜像，没有创建 commit，也没有推送远端。

### 已完成能力

1. **异常页逐页选择和重试**
   - 批次详情中的异常页支持逐页勾选。
   - 支持全选、取消全选、仅选择超时页、仅选择失败页。
   - 重试时可单独设置：VLM 页请求超时、任务等待上限、连接重试次数、VLM batch size。
   - 后端通过 `source_path + page_number` 标识页面，避免不同 PDF 的相同页码互相混淆。
   - 未传 `selected_pages` 时兼容原行为，导出全部异常页；显式传空列表或选择正常页时返回 400。

2. **页级耗时统计增强**
   - 页记录增加 `attempt_details`，保留每次尝试的耗时和结果。
   - 汇总增加成功尝试耗时、失败尝试耗时、重试额外耗时和包含重试的墙钟耗时。
   - 增加 P99 VLM 请求耗时、超时页数量/比例、重试页数量/比例。
   - 可同时观察“有效处理耗时”和“重试造成的额外耗时”，避免总时长掩盖重试成本。

3. **运行时诊断**
   - 新增 `GET /api/diagnostics/runtime`。
   - 展示关键运行时环境、模型路径检查结果、GPU/NPU 命令探测结果和诊断警告。
   - 诊断过程不联网、不主动加载模型；`nvidia-smi`、`npu-smi info` 均设置短超时并允许无设备时降级。

4. **配置操作审计**
   - 新增 `GET /api/audit?limit=100&offset=0`。
   - 配置校验、保存、恢复等操作可在配置页查看时间、动作、目标、成功状态和详情。
   - 审计列表支持倒序和分页参数。

5. **前端 UI12**
   - 服务页增加运行时诊断卡片和刷新按钮。
   - 配置页增加审计日志表格和刷新按钮。
   - 异常页重试弹窗增加逐页选择、筛选和重试参数。
   - 更新相关布局、表格、状态和异常页列表样式。

### 本轮主要修改文件

```text
mineru/cli/ops.py
mineru/ops/static/index.html
mineru/ops/static/ops.js
mineru/ops/static/ops.css
tests/unit/test_ops_console.py
handoff.md
```

### 新增或补强的测试覆盖

- `test_ops_store_lists_audit_logs`
  - 审计日志倒序、分页、总数和布尔状态转换。
- `test_runtime_diagnostics_is_offline_and_degrades_without_devices`
  - 本地模型配置、离线变量、GPU/NPU 不可用时降级、禁止联网探测。
- `test_write_problem_pages_supports_per_page_selection_and_source_identity`
  - 全部异常页导出、逐页选择、跨 PDF 同页码隔离、去重和非法选择校验。
- 运维控制台静态资源测试更新到 UI12，并覆盖新增 API、DOM 标识、JS 请求和 CSS 类名。

### 验证结果

```bash
python -m pytest -o addopts='' tests/unit/test_ops_console.py -q
# 40 passed in 4.81s

python -m py_compile mineru/cli/ops.py
# passed

node --check mineru/ops/static/ops.js
# passed

git diff --check
# passed

python -m pytest -o addopts='' \
  tests/unit/test_ops_console.py \
  tests/unit/test_ops_agent_config.py \
  tests/unit/test_api_request.py \
  tests/unit/test_vlm_resilience.py \
  tests/unit/test_batch_router_diagnose.py -q
# 83 passed in 4.48s
```

### 当前限制

1. 运维控制台展示的是 **ops 容器自身视角** 的环境、模型路径和设备命令结果，不能替代对 `mineru-api` 或独立 VLM 容器的容器内诊断。
2. 模型目录存在只代表路径和挂载可见，不代表模型已经真实初始化成功；最终仍需执行一次真实解析请求验证。
3. 控制台的任务等待超时不会取消 MinerU/VLM 端已经发出的远程任务；它只结束当前等待或记录超时状态。
4. 当前运行时诊断不会访问互联网，也不会为了验证模型而触发下载，这是离线现场环境下的刻意设计。

### 后续建议（P2/P3）

- **P2：跨容器深度诊断**：由 ops 通过受控接口分别获取 router、API、VLM 容器的有效配置、模型加载状态、设备信息和连通性，明确“配置已保存”和“服务已应用”的区别。
- **P2：配置应用状态**：展示配置版本、保存时间、各服务实际加载版本，以及哪些配置需要重启后生效。
- **P2：性能实验对比**：将 batch size、页超时、重试策略、设备环境和结果指标保存为可对比的实验记录。
- **P3：真正的任务取消**：需要 MinerU/VLM 协议侧提供取消能力；仅在运维控制台停止等待无法终止远端计算。
- **P3：权限与审计增强**：增加登录鉴权、操作人、导出审计记录和敏感配置脱敏。

---

## 2026-08-23：运维控制台 P2 配置状态与跨容器诊断收尾（UI13）

本轮完成运维控制台 P2/UI13 的配置可见性和跨容器诊断增强。没有构建镜像，没有创建 commit，也没有推送远端；工作区已有的其他未提交、未跟踪文件未清理、未删除、未重置。

### 已完成能力

1. **配置版本与指纹展示**
   - 配置页展示 `env.multi` 的版本号、SHA256 和配置内容 Hash。
   - 配置版本用于区分文件是否发生变化，Hash 用于辅助判断配置内容是否一致。
   - 敏感值只展示脱敏结果，不在状态接口或页面中泄露明文。

2. **配置生效状态比对**
   - 运维 Agent 解析 `env.multi`，并分别读取 API、Router、Ops 容器的实际环境变量。
   - 新增 `config_status` 能力，支持展示：`applied`、`pending_restart`、`unknown`、`not_created`。
   - 显示服务级总体状态、摘要、需要重启的服务、比较过的变量、匹配变量、缺失变量和不一致变量。
   - 对敏感变量只报告“不一致”，不返回源值和容器值。
   - 缺失变量明确标记为“容器未加载该变量”，避免与普通值不一致混淆。

3. **跨容器深度诊断**
   - 新增深度诊断接口和 UI 面板，可检查 API、Router、Ops 的容器运行状态、实际环境变量、模型路径存在性/可读性/文件数量、挂载信息以及 GPU/NPU 探测结果。
   - 诊断结果包含警告和配置应用状态，帮助区分“配置文件已保存”“容器已加载配置”和“容器未创建”。
   - 深度诊断支持手动刷新，适合现场修改 env、重建容器后复核。

4. **UI13 可用性和缓存处理**
   - 更新静态资源版本标识为 UI13：`ops.js?v=ui13`、`ops.css?v=ui13`。
   - 增加配置状态卡、诊断状态卡、刷新按钮、响应式布局和状态颜色。
   - 优化 `unknown`、`not_created`、缺失变量和无差异场景的提示文案。

5. **API 与测试**
   - 新增 `GET /api/config/status`。
   - 新增 `GET /api/diagnostics/deep`。
   - 增加 Agent 配置解析、脱敏、状态比对、诊断和运维控制台静态资源/API 的测试覆盖。

### 主要修改文件

```text
docker/multi/mineru-ops-agent.py
mineru/cli/ops.py
mineru/ops/static/index.html
mineru/ops/static/ops.js
mineru/ops/static/ops.css
tests/unit/test_ops_agent_config.py
tests/unit/test_ops_console.py
handoff.md
```

### 验证结果

```bash
python -m pytest -o addopts='' tests/unit/test_ops_agent_config.py -q
# 23 passed in 0.08s

python -m pytest -o addopts='' \\
  tests/unit/test_ops_console.py \\
  tests/unit/test_ops_agent_config.py -q
# 63 passed in 2.47s

python -m pytest -o addopts='' \\
  tests/unit/test_ops_console.py \\
  tests/unit/test_ops_agent_config.py \\
  tests/unit/test_api_request.py \\
  tests/unit/test_vlm_resilience.py \\
  tests/unit/test_batch_router_diagnose.py -q
# 96 passed in 2.43s

python -m py_compile docker/multi/mineru-ops-agent.py mineru/cli/ops.py
# passed

node --check mineru/ops/static/ops.js
# passed

git diff --check
# passed
```

### 已知限制与后续建议

1. 当前 `config_status` 主要比较白名单环境变量；Compose `command` 参数尚未纳入 `Config.Env` 的配置比对，因此命令行参数与 env 共同决定的配置仍需结合启动命令检查。
2. 深度诊断不会联网，也不会主动加载模型；“模型路径存在、可读且有文件”不等于模型已经成功初始化，仍需通过真实解析请求验证。
3. GPU/NPU 检测依赖容器中存在 `npu-smi` 或 `nvidia-smi`，现场没有对应工具时只能给出降级结果。
4. `mineru-ops` 自身配置修改后可能仍需现场手工重启或重建容器，才能让新的环境变量进入进程。
5. 运维控制台等待超时不等于远端 VLM 请求真正取消；真正取消仍属于后续 P3 协议能力，需要 MinerU/VLM API 提供取消接口或可中断任务句柄。
6. 如果把 `MINERU_VLM_PAGE_TIMEOUT_SECONDS` 从 600 秒调大，必须同时确认 `MINERU_VLM_CLIENT_HTTP_TIMEOUT` 不小于新的页级软超时，并重建实际承载 API 的容器/代码镜像，不能只刷新浏览器或只保存配置文件。

### 现场使用提醒

- 本轮代码仍在本地 `dev` 工作区，未执行 `git commit`、`git push`、Docker build 或镜像导出。
- 现场需要自行拉取代码、构建代码镜像并按既有 Compose 流程重建/刷新共享 code volume 和相关服务。
- 部署后若页面仍显示旧 UI，浏览器执行强制刷新（如 `Ctrl+F5`），并确认加载的是 `ops.js?v=ui13` 和 `ops.css?v=ui13`。

## 2026-08-24：UI16 诊断准确性、任务联立与控制台交互修复

本轮针对公司服务器现场反馈完成 UI16 修复。当前工作区未执行 commit、push 或 Docker 构建；无关的未跟踪诊断文件和用户已有修改未处理。

### 本轮完成

1. **运行时诊断改查实际 API 容器**
   - `/api/diagnostics/runtime` 改为通过宿主机 Agent 检查 `mineru-api` 实际容器。
   - `MINERU_MODEL_SOURCE`、`MINERU_VLM_MODEL`、`/models/pipeline`、`/etc/mineru/mineru.json` 和设备命令不再从 Ops 容器臆测。
   - 无 GPU/NPU 工具时明确说明是 API 容器能力缺失，不再提示“Ops 容器不可用”。
   - `mineru-code-sync` 的设备检测标记为不适用，一次性代码同步容器不再显示“未执行设备检测”。

2. **配置状态判断统一有效值优先级**
   - `config_status` 现在按命令行参数 > 容器环境变量比较实际值，修复 API 启动参数覆盖 env 时的假性“等待重启”。
   - 服务健康和配置是否已加载继续分开显示：健康只代表进程可用，`pending_restart` 代表当前容器尚未加载 env.multi 新值。

3. **批量测试与任务页面联立**
   - 浏览器上传记录显示实际 PDF 文件名，不再统一显示“浏览器上传（1 个 PDF）”。
   - 批量记录返回关联 Task ID 和显示名称，任务页显示“批量测试 · 文件名”。
   - 批量页可直接点击关联任务跳转任务详情。

4. **性能实验和日志体验**
   - 性能实验的实验名称、环境名称和备注为空时传空字符串，不再传 `null` 触发 Pydantic `string_type` 错误。
   - 服务器测试目录明确为相对于 Ops 测试根目录的路径，并拦截宿主机绝对路径输入。
   - 日志页增加关键词/字段筛选，支持 `ERROR`、`timeout`、`task_id` 等文本过滤并显示匹配行数。

5. **配置帮助和页面切换**
   - 配置 schema 增加详细 `help`，每个配置项增加问号按钮和说明弹窗，重点解释两个 VLM timeout 的区别。
   - 左侧页面切换时取消旧页面请求，延迟到下一帧刷新，避免旧诊断/日志请求阻塞新页面。
   - 静态资源版本更新为 `ops.js?v=ui16`、`ops.css?v=ui16`。

### 验证

```bash
python -m py_compile mineru/cli/ops.py docker/multi/mineru-ops-agent.py
node --check mineru/ops/static/ops.js
git diff --check
python -m pytest -o addopts='' \
  tests/unit/test_ops_console.py \
  tests/unit/test_ops_agent_config.py \
  tests/unit/test_api_request.py \
  tests/unit/test_vlm_resilience.py \
  tests/unit/test_batch_router_diagnose.py -q
# 109 passed
```

### 部署提示

- 构建并部署 UI16 后，需要刷新 `mineru-code-sync` 共享代码卷，并重建 `mineru-ops`、`mineru-api`、`mineru-router` 等实际使用代码卷的服务。
- 现场修改配置后，应在服务页分别查看 HTTP 健康和配置应用状态；两者同时显示“健康”和“等待重启生效”是可能且有意义的。
- 性能实验目录填写 `.` 或测试根目录下的相对目录，例如 `company-set-01`，不能填写宿主机路径。

## 2026-08-24：UI17 性能实验 batch 序列、窗口覆盖与日志清理

本轮根据现场性能实验反馈完成 UI17。实现了多 batch 顺序测试，并修复 batch=32 在 `MINERU_PROCESSING_WINDOW_SIZE=8` 时实际仍只能按 8 页窗口运行的问题。

### 本轮完成

1. **性能实验室支持多选 batch 串行执行**
   - `1、2、4、8、16、32` 改为复选框，可一次选择多个值。
   - 控制台按数值从小到大逐个创建实验；前一个实验进入终态后才启动下一个，不会并发压测。
   - 每个 batch 保存为独立记录，实验名称自动追加 `batch-N`。
   - 页面显示当前序列进度，某个实验失败、取消或中断时停止后续 batch。

2. **性能实验支持请求级处理窗口**
   - 性能实验请求会传递 `processing_window_size=vlm_batch_size`。
   - API 端使用 `max(MINERU_PROCESSING_WINDOW_SIZE, 请求窗口)`，因此现场 env 为 8 时，batch=32 会使用 32 页窗口；batch=1/2/4/8 仍由 micro-batch 参数决定实际 VLM 批量。
   - 该覆盖只由 `performance_lab` 使用，普通批量测试和外部 Router 调用仍遵循 env 配置，不会被控制台实验改变。
   - VLM/Hybrid 日志现在记录 `configured_window_size`、`requested_window_size`、`effective_window_size` 和窗口数量。
   - resilience 日志新增 `event=vlm_micro_batch requested=... effective=...`，便于确认实际批量。

3. **清理日志中的终端控制字符**
   - 运维控制台读取 Compose 日志时移除 ANSI 控制序列和回车覆盖符。
   - 性能实验 `batch.log` 落盘前也清理这些字符。
   - `tqdm` 日志中的 `][A`、问号方块等浏览器显示污染不再出现；进度会按普通文本换行保存。

### 重要说明

- 你提供的日志中 `queue_seconds=105.68`、`118.8` 是页面等待前序处理窗口的排队时间，不是 VLM 单页请求耗时；对应批次的 `vlm_request_seconds` 约为 11 秒。
- 性能实验 batch=32 的日志应出现类似：`configured_window_size=8, requested_window_size=32, effective_window_size=32`，窗口日志中的 `(... pages)` 应最多为 32 页。
- 该功能仍受 VLM 服务自身显存/NPU 能力限制；batch=16/32 可能增加内存压力或触发服务端失败，应按 1、2、4、8、16、32 逐档观察。
- `env.multi` 不需要新增变量；性能实验的请求级窗口由控制台自动传递。

### 验证

```bash
python -m py_compile \
  mineru/cli/ops.py \
  mineru/cli/api_request.py \
  mineru/cli/fast_api.py \
  mineru/backend/vlm/vlm_analyze.py \
  mineru/backend/vlm/resilience.py \
  mineru/backend/hybrid/hybrid_analyze.py \
  docker/multi/mineru-ops-agent.py \
  docker/multi/batch-router-diagnose.py
node --check mineru/ops/static/ops.js
git diff --check
python -m pytest -o addopts='' \
  tests/unit/test_ops_console.py \
  tests/unit/test_ops_agent_config.py \
  tests/unit/test_api_request.py \
  tests/unit/test_vlm_resilience.py \
  tests/unit/test_batch_router_diagnose.py -q
# 114 passed
```

### 部署

- UI 版本：`ui17`
- 代码镜像建议标签：`mineru-code:v3.4.2-ops-ui17`
- 代码镜像导出文件：`mineru-code-v3.4.2-ops-ui17.tar.gz`
- 本轮提交前会保留用户工作区已有的无关诊断文件，不纳入提交。

## 2026-08-24：UI18 性能指标说明与实验记录删除

### 本轮完成

- 性能实验归档增加指标口径说明：总耗时是整批 wall time；P50/P95/max 是 VLM 请求延迟；micro-batch 内多个页面会共享同一次批量请求耗时。
- 原“含重试”展示改为“页面请求累计”，明确它是并发请求耗时的求和，不能与总耗时直接比较。batch=32 时同一批请求耗时会归属到该批的多个页面，因此累计值可能远大于 wall time，这是统计口径而非任务实际运行了几千秒。
- 性能实验归档表增加删除按钮。后端原有 `POST /api/batch-runs/{run_id}/delete` 已支持终态记录删除，本轮补齐实验室页面入口，并在删除后同时刷新批量测试和实验归档。
- UI 版本更新为 `ui18`。

### 现场数据解读

- batch=1：每页单独请求，固定网络、HTTP、调度和模型服务排队开销重复发生，吞吐最低。
- batch=2/4：一次请求携带多个页面，摊薄固定开销；当前 T4 数据中 batch=4 达到最高吞吐。
- batch=8/16/32：单次请求处理更多页面，单请求延迟明显上升，吞吐没有继续线性增长，说明已经接近 VLM 服务或硬件的吞吐平台；不能只看 batch 越大越快。
- 这组数据比较 batch 时应优先看整批 wall time 和 pages/minute，再看请求延迟与异常率；“页面请求累计”只用于观察请求工作量和重试成本。
