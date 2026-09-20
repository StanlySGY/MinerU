# 实施计划

1. **后端瘦身**:`slim_task_for_list()` 去掉列表里的逐页数组;`/api/overview` 改为 SQL 聚合计数;
   `page_samples` 换成 `page_sample_count` + 精简最慢页;服务探测与产物体积加 TTL 缓存。
2. **#3 死循环**:`loadTaskDetail()` 的 catch 里置 `taskDetailLoaded`;新增 `isMissingTaskError()`
   与 `clearTaskSelection()`;删除/终止成功后一起清 URL 里的 `taskId`。
3. **#4 取消**:API 侧 `AsyncTaskManager.cancel()` + `DELETE /tasks/{id}`;Router 侧
   `RouterTaskRegistry.remove()` + `DELETE /tasks/{id}` 转发上游;控制台 `/api/tasks/{id}/cancel`;
   详情面板加「终止任务」按钮。
4. **#2/#5 布局**:`.table-scroll`(表内滚动 + 表头吸顶 + 列宽下限);`.split-view` 在 1500px
   以下改上下堆叠。
5. **#6 口径统一**:总览 tile 用 `/api/overview` 的 `task_counts`,不再在前端数列表前 20 条;
   总览请求前先同步一次任务快照,消除 tile 慢一拍。
6. **SSE 收尾**:`/api/tasks/{id}/events` 在 404 与终态时结束流,不再每秒重试。
7. **测试**:`tests/unit/test_task_cancel.py`(API 取消语义 + Router 转发/保留语义)、
   `test_ops_console.py`(slim payload、SQL 计数、控制台 cancel、SSE 收尾、静态资源约定)。
8. **真机验证**:桩上游 + 真实 Router + 控制台跑通取消链路;Playwright 覆盖各视口、缩放、
   删除缓存、终止任务按钮。
9. 审查 diff,更新任务记录并提交/推送 `dev`。