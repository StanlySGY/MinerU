# 审查

## 改动清单

| 文件 | 改动 |
| --- | --- |
| `mineru/cli/fast_api.py` | `cancelled` 终态;`AsyncTaskManager.cancel()`(取消 processor / 出队前拦截);`processors` 映射;`DELETE /tasks/{task_id}` |
| `mineru/cli/router.py` | `cancelled` 终态;`RouterTaskRegistry.remove()`;`DELETE /tasks/{task_id}` 转发上游并在上游不可达时保留记录 |
| `mineru/cli/ops.py` | `slim_task_for_list()` / `strip_pages_from_progress()`;`count_tasks_by_status()` / `count_failed_pages()`;`invalidate_task_sync_cache()`;服务探测与产物体积 TTL 缓存;`POST /tasks/{id}/cancel`;overview 先同步再计数;SSE 在 404/终态收尾;`page_samples` → `page_sample_count` |
| `mineru/ops/static/index.html` | 四个列表容器加 `table-scroll` |
| `mineru/ops/static/ops.css` | 表内滚动 + 表头吸顶 + 列宽下限;`.split-view` 1500px 以下堆叠;缓存提示条 |
| `mineru/ops/static/ops.js` | 总览计数改用服务端聚合;详情失败不再死循环;`clearTaskSelection()`;「终止任务」按钮;缓存提示条 |
| `tests/unit/test_task_cancel.py` | 新增:API 取消语义 + Router 转发/保留/404 语义 |
| `tests/unit/test_ops_console.py` | 新增:slim payload、SQL 计数、控制台 cancel、SSE 收尾、静态资源约定 |
| `.gitignore` | 忽略 `.ace-tool/` |

## 验证结果

**单测**:`python -m pytest tests/unit -q -o addopts=""` → **153 passed**(改动前 144)。

**取消链路(真实进程,非 mock)**
- 桩上游(`/tmp/stub_upstream.py`)+ 真实 Router(`--local-gpus none`)+ 控制台真实进程。
- `POST /api/tasks/{id}/cancel` → Router `DELETE` → 上游 `DELETE`:
  - 控制台返回 `{"ok":true,"cancelled":true}`;
  - Router 记录 `GET` 变 404;
  - **上游侧被取消的任务 `GET` 也是 404,`processing_tasks` 归零** —— 证明任务真的停了,不只是从列表消失。
- Router 单测另覆盖:终态任务不转发、上游不可达保留记录并报 502、上游 404 仍清本地记录。

**列表瘦身(120 条真实规模快照)**
- `/api/tasks?limit=100`:72,697 B → 若带逐页数组则为 3,861,997 B,**减少 98.1%**。
- `/api/overview`:0.81 s,计数 `{completed:57, failed:22, pending:23, processing:19}`,与缓存实际条数一致。

**浏览器实测(Playwright,真实控制台)**
- 布局:2560/1920/1600/1520/1501 左右并排;1500/1440/1366/1280 堆叠。两条分支下七列全部可见,`fits=true`;
  1366 时表内滚动 75px 可达「开始时间」,表头 `position: sticky` 在 `scrollTop=1200` 后仍贴顶,滚动条在视口内。
- 缩放等效视口:1920@100%/125%/150%、1366@100%/125% 全部 `fits=true`;
  1366@150%(等效 911px)需要在表内横向滚 102px,但滚动条在视口内且最后一列可达 —— 这是窄视口的预期行为,不是截断。
- 删除缓存:删除后 `activeTaskId=null`、`hash=#tasks`、详情回到「选择一个任务」,25 秒内详情请求 **0 次新增**,无 404 刷屏。
- 深链到一个不存在的任务:只发 **1** 次详情请求,提示「任务已不存在,可能已被终止或超出保留期」,33 秒后仍无重试。
- 缓存兜底:Router 不认识但控制台有缓存时,显示「Router 已无此任务记录,当前显示的是控制台缓存」,不再把陈旧状态当实时状态展示。

## 已知边界

- `API_PROTOCOL_VERSION` 未变。取消是新增可选端点:旧上游没有 `DELETE /tasks/{id}` 时 Router 会拿到 404,
  记录上游状态、丢掉本地记录并记录 warning,不会报错给控制台。因此多机灰度期间新旧版本可共存。
- 取消是三层一致的传播:控制台 → Router → 承接该任务的那台上游 API。若该上游进程已死,Router 会报 502
  并保留记录(任务本身也已不可能继续),这是刻意的保守选择。
- 现场若有人开了浏览器缩放,等效视口小于 ~1100px 时列表仍需表内横向滚动;此时滚动条在视口内,列可达。

## 遗留

- 更彻底的取消语义(逐页中断时"已完成页是否保留产物")未做,当前语义是停止任务并移除记录。
- `/api/tasks` 的 `total` 与 `items` 仍来自不同来源(cache 计数 + live 合并),极端并发下可能差 1~2 条。