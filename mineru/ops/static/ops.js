const state = {
  view: "overview",
  services: [],
  tasks: [],
  activeTaskId: null,
  taskDetailRequestId: 0,
  taskTimingStatus: "all",
  taskTimingSort: "page_number",
  taskTimingOrder: "asc",
  taskTimingOffset: 0,
  taskRefreshLoading: false,
  batches: [],
  uploadItems: [],
  activeBatchId: null,
  activeBatchDetail: null,
  activeBatchDocument: null,
  activeTaskPreviewId: null,
  activeTaskPreview: null,
  taskPreviewObserver: null,
  taskPreviewGeneration: 0,
  taskPreviewPageQueue: [],
  taskPreviewPageActive: 0,
  previewObjectUrls: [],
  serviceFilter: "all",
  logRequestId: 0,
  logLoading: false,
  token: sessionStorage.getItem("mineruOpsToken") || "",
};

const titles = {overview: "总览", services: "服务", tasks: "任务", batch: "批量测试", logs: "日志"};
const statusText = {
  pending: "等待中", queued: "等待处理", processing: "处理中", completed: "已完成", failed: "失败",
  partial_success: "部分成功", running: "运行中", paused: "已暂停", cancelled: "已取消",
  completed_with_failures: "完成（存在失败）", interrupted: "已中断", unhealthy: "异常",
  healthy: "健康", unavailable: "不可用", unknown: "未知", success: "成功", partial: "部分成功",
  client_error: "客户端错误", wait_timeout: "停止等待（后台可能仍在运行）",
  monitoring_stopped: "停止监控"
};

const tokenInput = document.getElementById("token");
tokenInput.value = state.token;
tokenInput.addEventListener("change", () => {
  state.token = tokenInput.value.trim();
  sessionStorage.setItem("mineruOpsToken", state.token);
  refreshCurrent();
});

function esc(value) {
  return String(value ?? "").replace(/[&<>'"]/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"})[char]);
}

function badge(value) {
  const text = statusText[value] || value || "未知";
  const cls = ["healthy", "completed", "running", "success"].includes(value) ? "good" :
    ["pending", "processing", "paused", "partial", "partial_success", "completed_with_failures", "wait_timeout", "monitoring_stopped"].includes(value) ? "warn" :
    ["failed", "unhealthy", "unavailable", "cancelled", "interrupted"].includes(value) ? "bad" : "";
  return `<span class="badge ${cls}">${esc(text)}</span>`;
}

async function api(path, options = {}) {
  const headers = {...(options.headers || {})};
  if (state.token) headers["X-MinerU-Ops-Token"] = state.token;
  if (options.body && !(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {...options, headers});
  let payload;
  try { payload = await response.json(); } catch { payload = {detail: await response.text()}; }
  if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
  return payload;
}

async function authorizedFetch(path, options = {}) {
  const headers = {...(options.headers || {})};
  if (state.token) headers["X-MinerU-Ops-Token"] = state.token;
  const response = await fetch(path, {...options, headers});
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const payload = await response.json();
      detail = payload.detail || detail;
    } catch {}
    throw new Error(detail);
  }
  return response;
}

function artifactUrl(runId, kind, path) {
  const encodedPath = String(path).split("/").map(encodeURIComponent).join("/");
  return `/api/batch-runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(kind)}/${encodedPath}`;
}

function clearPreviewObjectUrls() {
  for (const url of state.previewObjectUrls) URL.revokeObjectURL(url);
  state.previewObjectUrls = [];
}

async function downloadPath(path, filename) {
  const response = await authorizedFetch(path);
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

function uploadForm(path, formData, onProgress) {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", path);
    request.responseType = "json";
    if (state.token) request.setRequestHeader("X-MinerU-Ops-Token", state.token);
    request.upload.addEventListener("progress", event => {
      if (event.lengthComputable) onProgress(Math.round(event.loaded * 100 / event.total));
    });
    request.addEventListener("load", () => {
      let payload = request.response;
      if (!payload) {
        try { payload = JSON.parse(request.responseText); } catch { payload = {detail: request.responseText}; }
      }
      if (request.status >= 200 && request.status < 300) resolve(payload);
      else reject(new Error(payload?.detail || `HTTP ${request.status}`));
    });
    request.addEventListener("error", () => reject(new Error("上传连接失败")));
    request.addEventListener("abort", () => reject(new Error("上传已取消")));
    request.send(formData);
  });
}

function notice(message, bad = true) {
  const element = document.getElementById("notice");
  if (!message) { element.classList.add("hidden"); return; }
  element.textContent = message;
  element.classList.remove("hidden");
  element.style.borderColor = bad ? "#e4a6a1" : "#8bc8b2";
  element.style.background = bad ? "#fff1f0" : "#edf8f4";
  element.style.color = bad ? "#7e201a" : "#13553f";
}

function setConnection(ok, text) {
  document.getElementById("connection-dot").className = `dot ${ok ? "good" : "bad"}`;
  document.getElementById("connection-text").textContent = text;
}

function switchView(view) {
  state.view = view;
  document.querySelectorAll(".view").forEach(el => el.classList.toggle("active", el.id === `view-${view}`));
  document.querySelectorAll(".nav-item").forEach(el => el.classList.toggle("active", el.dataset.view === view));
  document.getElementById("page-title").textContent = titles[view];
  refreshCurrent();
}

document.getElementById("nav").addEventListener("click", event => {
  const button = event.target.closest("[data-view]");
  if (button) switchView(button.dataset.view);
});
document.body.addEventListener("click", event => {
  const button = event.target.closest("[data-go]");
  if (button) switchView(button.dataset.go);
});
document.getElementById("refresh").addEventListener("click", refreshCurrent);

function formatDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", {hour12: false});
}

function formatElapsed(startValue, endValue = null) {
  if (!startValue) return "-";
  const start = new Date(startValue).getTime();
  const end = endValue ? new Date(endValue).getTime() : Date.now();
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return "-";
  const totalSeconds = Math.floor((end - start) / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours) return `${hours}小时 ${minutes}分 ${seconds}秒`;
  if (minutes) return `${minutes}分 ${seconds}秒`;
  return `${seconds}秒`;
}

function taskPreviewReady(task) {
  return Boolean(
    task.preview_available
    && (
      task.partial_success
      || ["completed", "completed_with_failures", "partial_success"].includes(task.status)
    ),
  );
}

function compactTaskRows(tasks, limit = 6) {
  if (!tasks.length) return `<div class="empty-state">暂无任务</div>`;
  return `<table><thead><tr><th>文件</th><th>状态</th><th>页进度</th><th>更新时间</th></tr></thead><tbody>${tasks.slice(0, limit).map(task => {
    const progress = task.progress || {};
    const finished = (progress.completed_pages || 0) + (progress.skipped_pages || 0) + (progress.failed_pages || 0);
    return `<tr data-task="${esc(task.task_id)}"><td>${esc((task.file_names || []).join(", "))}</td><td>${badge(task.partial_success ? "partial_success" : task.status)}</td><td>${finished}/${progress.total_pages || "-"}</td><td>${esc(formatDate(progress.updated_at || task.completed_at || task.created_at))}</td></tr>`;
  }).join("")}</tbody></table>`;
}

async function loadOverview() {
  const data = await api("/api/overview");
  state.services = data.services || [];
  state.batches = data.batch_runs || [];
  const taskData = await api("/api/tasks?limit=20");
  state.tasks = taskData.items || [];
  const healthy = state.services.filter(item => item.health === "healthy").length;
  const active = state.tasks.filter(item => ["pending", "processing"].includes(item.status)).length;
  const failed = state.tasks.filter(item => item.status === "failed").length;
  document.getElementById("summary-strip").innerHTML = [
    ["健康服务", healthy], ["服务总数", state.services.length], ["活动任务", active],
    ["跳过页面", data.skipped_pages || 0], ["失败任务", failed]
  ].map(([label, value]) => `<div class="summary-item"><span>${label}</span><strong>${value}</strong></div>`).join("");
  document.getElementById("overview-health-count").textContent = `${healthy}/${state.services.length} 健康`;
  document.getElementById("overview-services").innerHTML = state.services.map(serviceCard).join("") || `<div class="empty-state">没有发现服务</div>`;
  document.getElementById("overview-tasks").innerHTML = compactTaskRows(state.tasks.filter(item => ["pending", "processing"].includes(item.status)));
  document.getElementById("overview-batches").innerHTML = compactBatchRows(state.batches, 6);
  updateLogServices();
  document.getElementById("page-meta").textContent = `最后更新：${formatDate(data.updated_at)}`;
}

function serviceCard(service) {
  const runtimeState = service.runtime?.state || "unknown";
  const cls = service.health === "healthy" ? "good" : service.health === "unavailable" ? "bad" : "warn";
  const windowSize = service.health_payload?.processing_window_size;
  const workload = windowSize ? ` · 窗口 ${windowSize} · 处理中 ${service.health_payload?.processing_tasks || 0}` : "";
  return `<article class="service-card ${cls}"><div class="service-card-head"><div><h3>${esc(service.name)}</h3><span class="muted">${esc(String(service.role).toUpperCase())}</span></div>${badge(service.health)}</div><p>容器：${esc(runtimeState)}${esc(workload)}</p><p>${esc(service.endpoint || service.health_error || "无检测端点")}</p></article>`;
}

async function loadServices() {
  state.services = await api("/api/services");
  renderServicesTable();
  updateLogServices();
}

function renderServicesTable() {
  const services = state.services.filter(item => state.serviceFilter === "all" || item.role === state.serviceFilter);
  document.getElementById("services-table").innerHTML = `<table><thead><tr><th>服务</th><th>角色</th><th>容器</th><th>健康</th><th>镜像/端点</th><th>操作</th></tr></thead><tbody>${services.map(service => {
    const actions = service.control_enabled ? ["check", "start", "stop", "restart"] : ["check"];
    return `<tr><td><strong>${esc(service.name)}</strong></td><td>${esc(service.role)}</td><td>${esc(service.runtime?.state || "unknown")}</td><td>${badge(service.health)}${service.health_message ? `<div class="muted">${esc(service.health_message)}</div>` : ""}</td><td><div class="mono">${esc(service.image || service.endpoint || "-")}</div></td><td><div class="actions">${actions.map(action => `<button class="action-button ${action === "stop" ? "danger" : ""}" data-service="${esc(service.name)}" data-action="${action}">${{check:"检查",start:"启动",stop:"停止",restart:"重启"}[action]}</button>`).join("")}</div></td></tr>`;
  }).join("")}</tbody></table>`;
}

document.getElementById("service-filter").addEventListener("click", event => {
  const button = event.target.closest("[data-filter]");
  if (!button) return;
  state.serviceFilter = button.dataset.filter;
  document.querySelectorAll("#service-filter button").forEach(el => el.classList.toggle("active", el === button));
  renderServicesTable();
});

document.getElementById("services-table").addEventListener("click", async event => {
  const button = event.target.closest("[data-service][data-action]");
  if (!button) return;
  const actionText = button.textContent;
  if (!["检查"].includes(actionText) && !confirm(`确认${actionText} ${button.dataset.service}？`)) return;
  button.disabled = true;
  try {
    await api(`/api/services/${encodeURIComponent(button.dataset.service)}/actions/${button.dataset.action}`, {method: "POST"});
    notice(`${button.dataset.service}：${actionText}完成`, false);
    await loadServices();
  } catch (error) { notice(error.message); }
  finally { button.disabled = false; }
});

document.getElementById("view-services").addEventListener("click", async event => {
  const button = event.target.closest("[data-system-action]");
  if (!button) return;
  const action = button.dataset.systemAction;
  if (action !== "test" && !confirm(`确认${button.textContent}？`)) return;
  button.disabled = true;
  try {
    const result = await api(`/api/system/actions/${action}`, {method: "POST"});
    const message = action === "test" ? `全链路测试完成：${result.task_id}` : `${button.textContent}完成`;
    notice(message, false);
    await loadServices();
  } catch (error) { notice(error.message); }
  finally { button.disabled = false; }
});

async function loadTasks() {
  const status = document.getElementById("task-status").value;
  const data = await api(`/api/tasks?limit=200${status ? `&status=${encodeURIComponent(status)}` : ""}`);
  state.tasks = data.items || [];
  renderTasks();
  if (data.source === "cache") notice(`Router 暂不可用，当前显示缓存：${data.error}`);
}

function renderTasks() {
  const query = document.getElementById("task-search").value.trim().toLowerCase();
  const tasks = state.tasks.filter(task => !query || String(task.task_id).toLowerCase().includes(query) || (task.file_names || []).join(" ").toLowerCase().includes(query));
  document.getElementById("tasks-table").innerHTML = `<table><thead><tr><th>文件</th><th>状态</th><th>后端</th><th>成功/总页数</th><th>跳过</th><th>耗时</th><th>开始时间</th></tr></thead><tbody>${tasks.map(task => {
    const p = task.progress || {};
    return `<tr data-task="${esc(task.task_id)}" class="${task.task_id === state.activeTaskId ? "selected" : ""}"><td><strong>${esc((task.file_names || []).join(", "))}</strong><div class="mono muted">${esc(task.task_id)}</div></td><td>${badge(task.partial_success ? "partial_success" : task.status)}</td><td>${esc(task.backend)}</td><td>${p.completed_pages || 0}/${p.total_pages || "-"}</td><td>${p.skipped_pages || 0}</td><td class="task-elapsed">${esc(formatElapsed(task.started_at || task.created_at, task.completed_at))}</td><td>${esc(formatDate(task.started_at || task.created_at))}</td></tr>`;
  }).join("")}</tbody></table>`;
}

document.getElementById("task-status").addEventListener("change", loadTasks);
document.getElementById("task-search").addEventListener("input", renderTasks);
document.getElementById("tasks-table").addEventListener("click", event => {
  const row = event.target.closest("[data-task]");
  if (row) loadTaskDetail(row.dataset.task);
});
document.getElementById("overview-tasks").addEventListener("click", event => {
  const row = event.target.closest("[data-task]");
  if (row) { switchView("tasks"); setTimeout(() => loadTaskDetail(row.dataset.task), 0); }
});

const phaseText = {
  queued: "任务排队中",
  vlm_queue: "页面正在等待 VLM",
  vlm_inference: "VLM 正在识别页面",
  completed: "处理完成",
  failed: "处理失败",
  monitoring_stopped: "控制台已停止监控，后台任务可能仍在运行",
  unknown: "等待进度信息",
};

function pageLegend() {
  return `<div class="page-legend" aria-label="页面状态图例"><span><i class="legend-dot queued"></i>等待处理</span><span><i class="legend-dot processing"></i>正在处理</span><span><i class="legend-dot completed"></i>处理完成</span><span><i class="legend-dot skipped"></i>超时跳过</span><span><i class="legend-dot failed"></i>处理失败</span></div>`;
}

function pageTitle(page, fileName) {
  const parts = [`${fileName} 第 ${page.page_number} 页`, statusText[page.status] || page.status || "未知"];
  if (page.attempts) parts.push(`尝试 ${page.attempts} 次`);
  if (page.queue_seconds != null) parts.push(`排队 ${page.queue_seconds} 秒`);
  if (page.vlm_request_seconds != null) parts.push(`VLM 请求 ${page.vlm_request_seconds} 秒`);
  else if (page.inference_seconds != null) parts.push(`VLM 请求 ${page.inference_seconds} 秒`);
  if (page.retry_wait_seconds != null && page.retry_wait_seconds > 0) parts.push(`重试等待 ${page.retry_wait_seconds} 秒`);
  if (page.total_seconds != null) parts.push(`总计 ${page.total_seconds} 秒`);
  if (page.error) parts.push(page.error);
  return parts.join(" · ");
}

function formatPageSeconds(value) {
  if (value == null || !Number.isFinite(Number(value))) return "-";
  return `${Number(value).toFixed(2)} 秒`;
}

function renderPageTimingSummary(summary) {
  if (!summary || !summary.recorded_pages) {
    return `<div class="task-timing-empty">暂无已保存的页级耗时记录</div>`;
  }
  const cards = [
    ["已记录页面", summary.recorded_pages],
    ["平均 VLM 请求", formatPageSeconds(summary.completed_average_vlm_request_seconds)],
    ["P50", formatPageSeconds(summary.completed_p50_vlm_request_seconds)],
    ["P95", formatPageSeconds(summary.completed_p95_vlm_request_seconds)],
    ["最慢请求", formatPageSeconds(summary.completed_max_vlm_request_seconds)],
    ["慢页数量", summary.slow_pages || 0],
  ];
  return `<div class="task-timing-summary">${cards.map(([label, value]) => `<div><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`).join("")}</div>`;
}

function renderAttemptDetails(attempts) {
  if (!Array.isArray(attempts) || !attempts.length) return "-";
  const outcomeText = {completed: "成功", retry: "失败后重试", skipped: "跳过", failed: "失败", batch_fallback: "批处理失败，转逐页"};
  const rows = attempts.map(attempt => {
    const outcome = outcomeText[attempt.outcome] || attempt.outcome || "未知";
    const retryWait = formatPageSeconds(attempt.retry_wait_before_seconds);
    const error = attempt.error_type ? ` · ${esc(attempt.error_type)}${attempt.error ? `: ${esc(attempt.error)}` : ""}` : "";
    return `<li><strong>第 ${esc(attempt.attempt_no ?? "-")} 次</strong> · ${esc(outcome)} · 请求 ${formatPageSeconds(attempt.request_seconds)} · 重试前等待 ${retryWait} · 超时上限 ${formatPageSeconds(attempt.timeout_seconds)} · batch ${esc(attempt.batch_size ?? 1)}${error}</li>`;
  }).join("");
  return `<details><summary>${attempts.length} 条记录</summary><ol class="attempt-detail-list">${rows}</ol></details>`;
}

function renderPageTimingTable(payload, taskId) {
  const status = state.taskTimingStatus;
  const sort = state.taskTimingSort;
  const order = state.taskTimingOrder;
  const items = payload?.items || [];
  const statusOptions = [["all", "全部"], ["completed", "完成"], ["skipped", "跳过"], ["failed", "失败"]];
  const rows = items.map(page => `<tr><td>${esc(page.file_name)} 第 ${esc(page.page_number)} 页</td><td>${badge(page.status)}</td><td>${formatPageSeconds(page.queue_seconds)}</td><td>${formatPageSeconds(page.successful_attempt_seconds)}</td><td>${formatPageSeconds(page.failed_attempt_seconds)}</td><td>${formatPageSeconds(page.retry_overhead_seconds ?? page.retry_wait_seconds)}</td><td>${formatPageSeconds(page.total_seconds ?? page.elapsed_seconds)}</td><td>${esc(page.attempts ?? "-")}</td><td>${renderAttemptDetails(page.attempt_details)}</td><td>${esc(page.error || "-")}</td></tr>`).join("");
  return `<section class="task-timing-panel"><div class="task-timing-heading"><h3>页面耗时</h3><div class="task-timing-controls"><label>状态 <select data-task-timing-status>${statusOptions.map(([value, label]) => `<option value="${value}" ${status === value ? "selected" : ""}>${label}</option>`).join("")}</select></label><label>排序 <select data-task-timing-sort><option value="page_number" ${sort === "page_number" ? "selected" : ""}>页码</option><option value="total_seconds" ${sort === "total_seconds" ? "selected" : ""}>含重试总耗时</option><option value="vlm_request_seconds" ${sort === "vlm_request_seconds" ? "selected" : ""}>VLM 请求累计</option></select></label><label>顺序 <select data-task-timing-order><option value="asc" ${order === "asc" ? "selected" : ""}>升序</option><option value="desc" ${order === "desc" ? "selected" : ""}>降序</option></select></label></div></div>${renderPageTimingSummary(payload?.summary)}<div class="task-timing-table-wrap"><table class="task-timing-table"><thead><tr><th>页面</th><th>状态</th><th>排队</th><th>最终成功尝试</th><th>失败尝试累计</th><th>重试等待</th><th>含重试总耗时</th><th>尝试次数</th><th>逐次记录</th><th>错误</th></tr></thead><tbody>${rows || `<tr><td colspan="10" class="muted">暂无符合条件的页面记录</td></tr>`}</tbody></table></div><div class="task-timing-pagination"><button type="button" class="text-button" data-task-timing-prev ${payload?.offset ? "" : "disabled"}>上一页</button><span>${items.length ? `${payload.offset + 1}-${payload.offset + items.length} / ${payload.total}` : "0 / 0"}</span><button type="button" class="text-button" data-task-timing-next ${payload && payload.offset + items.length < payload.total ? "" : "disabled"}>下一页</button></div></section>`;
}

function currentTaskPosition(task, progress, pages) {
  const processing = pages.filter(page => page.status === "processing");
  if (processing.length) {
    const locations = processing.map(page => `${page.file_name} 第 ${page.page_number} 页`).join("；");
    return {kind: "processing", title: "正在处理", detail: locations};
  }
  if ((progress.inflight_page_numbers || []).length) {
    return {kind: "processing", title: "正在处理", detail: `第 ${progress.inflight_page_numbers.join("、")} 页`};
  }
  if (["completed", "partial_success", "completed_with_failures"].includes(task.status)) return {kind: "completed", title: "任务已完成", detail: `已处理 ${progress.completed_pages || 0} 页，跳过 ${progress.skipped_pages || 0} 页`};
  if (task.status === "failed") return {kind: "failed", title: "任务失败", detail: task.error || "查看下方错误信息"};
  if ((progress.queued_pages || 0) > 0) return {kind: "queued", title: "等待处理", detail: `还有 ${progress.queued_pages} 页在队列中`};
  return {kind: "queued", title: phaseText[progress.phase] || progress.phase || "等待进度", detail: "正在等待新的页面状态"};
}

async function loadTaskDetail(taskId, {silent = false} = {}) {
  const requestId = ++state.taskDetailRequestId;
  if (state.activeTaskId !== taskId) {
    state.taskTimingOffset = 0;
    state.taskTimingStatus = "all";
    state.taskTimingSort = "page_number";
    state.taskTimingOrder = "asc";
  }
  state.activeTaskId = taskId;
  renderTasks();
  try {
    const task = await api(`/api/tasks/${encodeURIComponent(taskId)}`);
    let timingPayload = {items: [], total: 0, offset: state.taskTimingOffset, summary: null};
    try {
      const timingParams = new URLSearchParams({
        sort: state.taskTimingSort,
        order: state.taskTimingOrder,
        limit: "100",
        offset: String(state.taskTimingOffset),
      });
      if (state.taskTimingStatus !== "all") timingParams.set("status", state.taskTimingStatus);
      timingPayload = await api(`/api/tasks/${encodeURIComponent(taskId)}/page-timings?${timingParams}`);
    } catch (error) {
      if (!silent) notice(`页级耗时暂不可用：${error.message}`);
    }
    if (requestId !== state.taskDetailRequestId || state.activeTaskId !== taskId) return;
    const p = task.progress || {};
    const done = (p.completed_pages || 0) + (p.skipped_pages || 0) + (p.failed_pages || 0);
    const percent = p.total_pages ? Math.min(100, Math.round(done * 100 / p.total_pages)) : 0;
    const pages = (p.files || []).flatMap(file => (file.pages || []).map(page => ({...page, file_name: file.file_name})));
    const current = currentTaskPosition(task, p, pages);
    const previewReady = taskPreviewReady(task);
    const taskFinished = task.partial_success || ["completed", "completed_with_failures", "partial_success"].includes(task.status);
    const previewAction = previewReady
      ? `<button type="button" class="primary-button" data-task-preview="${esc(task.task_id)}">预览结果</button>`
      : (taskFinished ? `<span class="task-preview-unavailable" title="该任务没有保留原始 PDF 或提取产物">未保留预览产物</span>` : "");
    const reportActions = `<button type="button" class="secondary-button" data-task-report="markdown">导出 Markdown 报告</button><button type="button" class="secondary-button" data-task-report="csv">导出 CSV</button>`;
    const elapsed = formatElapsed(task.started_at || task.created_at, task.completed_at);
    const fileSections = (p.files || []).map(file => `<section class="task-file-pages"><div class="task-file-heading"><strong>${esc(file.file_name)}</strong><span>${(file.pages || []).length} 页</span></div><div class="page-list">${(file.pages || []).map(page => `<span class="page-chip ${esc(page.status || "queued")}" title="${esc(pageTitle(page, file.file_name))}">${page.page_number}</span>`).join("")}</div></section>`).join("");
    const problemPages = pages.filter(page => page.status === "skipped" || page.status === "failed");
    document.getElementById("task-detail").innerHTML = `<div class="task-detail-header"><div><h2>${esc((task.file_names || []).join(", "))}</h2><div class="mono muted">${esc(task.task_id)}</div></div><div class="task-detail-actions">${badge(task.partial_success ? "partial_success" : task.status)}${previewAction}${reportActions}</div></div><div class="task-live-position ${current.kind}"><span class="live-indicator"></span><div><small>当前处理位置</small><strong>${esc(current.title)}</strong><p>${esc(current.detail)}</p></div><time>${esc(formatDate(p.updated_at))}</time></div><div class="task-progress-row"><div class="progress"><span style="width:${percent}%"></span></div><strong>${percent}%</strong></div><div class="task-stat-grid"><div><span>总页数</span><strong>${p.total_pages || 0}</strong></div><div><span>已完成</span><strong>${p.completed_pages || 0}</strong></div><div><span>处理中</span><strong>${p.processing_pages || 0}</strong></div><div><span>等待中</span><strong>${p.queued_pages || 0}</strong></div><div><span>已跳过</span><strong>${p.skipped_pages || 0}</strong></div><div><span>失败</span><strong>${p.failed_pages || 0}</strong></div></div><div class="task-meta-line"><span>阶段：<strong>${esc(phaseText[p.phase] || p.phase || "-")}</strong></span><span>后端：<strong>${esc(task.backend || "-")}</strong></span><span>耗时：<strong class="task-elapsed">${esc(elapsed)}</strong></span>${task.error ? `<span class="bad-text">任务错误：${esc(task.error)}</span>` : ""}</div><div class="task-pages-heading"><h3>页面状态</h3><span>${pages.length} 个页面事件</span></div>${pageLegend()}<div class="task-file-page-list">${fileSections || `<div class="empty-state">尚未收到页级进度，任务启动后会自动显示</div>`}</div>${renderPageTimingTable(timingPayload, taskId)}${problemPages.length ? `<div class="task-problem-list"><h3>跳过和失败页面</h3>${problemPages.map(page => `<div class="task-problem-row"><strong>${esc(page.file_name)} 第 ${page.page_number} 页</strong>${badge(page.status)}<span>${esc(page.error_type || "")}</span><p>${esc(page.error || "未返回错误详情")}</p></div>`).join("")}</div>` : ""}`;
  } catch (error) {
    if (!silent) notice(error.message);
  }
}

document.getElementById("task-detail").addEventListener("click", event => {
  const button = event.target.closest("[data-task-preview]");
  if (button) openTaskPreview(button.dataset.taskPreview);
});

document.getElementById("task-detail").addEventListener("click", async event => {
  const button = event.target.closest("[data-task-report]");
  if (!button || !state.activeTaskId) return;
  const format = button.dataset.taskReport;
  const extension = format === "csv" ? "csv" : "md";
  try {
    await downloadPath(
      `/api/tasks/${encodeURIComponent(state.activeTaskId)}/report?format=${encodeURIComponent(format)}`,
      `task-report-${state.activeTaskId}.${extension}`,
    );
  } catch (error) {
    notice(error.message);
  }
});

document.getElementById("task-detail").addEventListener("change", event => {
  const target = event.target;
  if (target.matches("[data-task-timing-status]")) state.taskTimingStatus = target.value;
  if (target.matches("[data-task-timing-sort]")) state.taskTimingSort = target.value;
  if (target.matches("[data-task-timing-order]")) state.taskTimingOrder = target.value;
  if (target.matches("[data-task-timing-status], [data-task-timing-sort], [data-task-timing-order]")) {
    state.taskTimingOffset = 0;
    if (state.activeTaskId) loadTaskDetail(state.activeTaskId, {silent: true});
  }
});

document.getElementById("task-detail").addEventListener("click", event => {
  const button = event.target.closest("[data-task-timing-prev], [data-task-timing-next]");
  if (!button || button.disabled || !state.activeTaskId) return;
  state.taskTimingOffset = Math.max(0, state.taskTimingOffset + (button.matches("[data-task-timing-next]") ? 100 : -100));
  loadTaskDetail(state.activeTaskId, {silent: true});
});

async function refreshTasksView() {
  if (state.taskRefreshLoading) return;
  state.taskRefreshLoading = true;
  try {
    await loadTasks();
    if (state.activeTaskId) await loadTaskDetail(state.activeTaskId, {silent: true});
    setConnection(true, "控制台已连接");
  } catch (error) {
    setConnection(false, "连接异常");
    notice(error.message);
  } finally {
    state.taskRefreshLoading = false;
  }
}

function compactBatchRows(items, limit = 100) {
  if (!items.length) return `<div class="empty-state">暂无批量测试</div>`;
  return `<table><thead><tr><th>目录</th><th>状态</th><th>PDF</th><th>开始时间</th><th>记录</th><th>操作</th></tr></thead><tbody>${items.slice(0, limit).map(run => {
    const active = ["pending", "running", "paused", "cancelling"].includes(run.status);
    const previewText = run.input_preview_ready || run.result_preview_ready ? "可预览" : "";
    return `<tr><td><strong>${esc(run.settings?.input_path || run.input_path)}</strong><div class="mono muted">${esc(run.run_id)}</div></td><td>${badge(run.status)}${previewText ? `<div class="muted">${previewText}</div>` : ""}</td><td>${run.settings?.pdf_count || "-"}</td><td>${esc(formatDate(run.started_at || run.created_at))}</td><td><button class="text-button" data-batch-detail="${run.run_id}">查看记录</button></td><td><div class="actions">${active ? `<button class="action-button" data-batch="${run.run_id}" data-batch-action="${run.status === "paused" ? "resume" : "pause"}">${run.status === "paused" ? "继续" : "暂停"}</button><button class="action-button danger" data-batch="${run.run_id}" data-batch-action="cancel">停止批次脚本</button>` : `<button class="action-button" data-batch="${run.run_id}" data-batch-action="retry">重试</button><button class="action-button danger" data-batch="${run.run_id}" data-batch-action="delete">删除</button>`}</div></td></tr>`;
  }).join("")}</tbody></table>`;
}

async function loadBatches() {
  const data = await api("/api/batch-runs");
  state.batches = data.items || [];
  const storage = data.storage || {};
  document.getElementById("batch-storage").textContent = storage.max_bytes ? `产物空间 ${formatBytes(storage.used_bytes || 0)} / ${formatBytes(storage.max_bytes)}（${storage.usage_percent || 0}%），保留 ${storage.retention_days} 天` : "默认串行提交 PDF";
  document.getElementById("batch-table").innerHTML = compactBatchRows(state.batches);
}

const batchFilesInput = document.getElementById("batch-files");
const batchFolderInput = document.getElementById("batch-folder");
const uploadSummary = document.getElementById("upload-summary");
const uploadDropZone = document.getElementById("upload-drop-zone");
const uploadFileList = document.getElementById("upload-file-list");

function formatBytes(bytes) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  if (bytes >= 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function updateUploadSummary() {
  if (!state.uploadItems.length) {
    uploadSummary.textContent = "当前使用服务器测试目录";
    uploadFileList.innerHTML = "";
    document.getElementById("clear-upload").classList.add("hidden");
    return;
  }
  const totalBytes = state.uploadItems.reduce((total, item) => total + item.file.size, 0);
  const folderUpload = state.uploadItems.some(item => item.path.includes("/"));
  uploadSummary.textContent = `${folderUpload ? "浏览器文件/文件夹" : "浏览器文件"}：${state.uploadItems.length} 个 PDF，${formatBytes(totalBytes)}`;
  document.getElementById("clear-upload").classList.remove("hidden");
  const visibleItems = state.uploadItems.slice(0, 100);
  uploadFileList.innerHTML = visibleItems.map((item, index) => `<div class="upload-file-row"><span title="${esc(item.path)}">${esc(item.path)}</span><span class="muted">${formatBytes(item.file.size)}</span><button type="button" data-upload-remove="${index}">移除</button></div>`).join("") + (state.uploadItems.length > visibleItems.length ? `<div class="muted">另有 ${state.uploadItems.length - visibleItems.length} 个文件未展开显示</div>` : "");
}

function setUploadItems(items, append = false) {
  const selected = append ? [...state.uploadItems] : [];
  const knownPaths = new Set(selected.map(item => item.path.toLowerCase()));
  let ignored = 0;
  for (const item of items) {
    if (!item.file.name.toLowerCase().endsWith(".pdf")) { ignored += 1; continue; }
    const normalizedPath = String(item.path || item.file.name).replaceAll("\\", "/").replace(/^\/+/, "");
    if (!normalizedPath || knownPaths.has(normalizedPath.toLowerCase())) continue;
    knownPaths.add(normalizedPath.toLowerCase());
    selected.push({file: item.file, path: normalizedPath});
  }
  state.uploadItems = selected;
  updateUploadSummary();
  if (ignored) notice(`已忽略 ${ignored} 个非 PDF 文件`, false);
}

function clearUploadItems() {
  state.uploadItems = [];
  batchFilesInput.value = "";
  batchFolderInput.value = "";
  updateUploadSummary();
}

function readFileEntry(entry, path) {
  return new Promise((resolve, reject) => entry.file(file => resolve({file, path: `${path}${file.name}`}), reject));
}

function readDirectoryEntries(reader) {
  return new Promise((resolve, reject) => reader.readEntries(resolve, reject));
}

async function collectDroppedEntry(entry, prefix = "") {
  if (entry.isFile) return [await readFileEntry(entry, prefix)];
  if (!entry.isDirectory) return [];
  const directoryPrefix = `${prefix}${entry.name}/`;
  const reader = entry.createReader();
  const collected = [];
  while (true) {
    const entries = await readDirectoryEntries(reader);
    if (!entries.length) break;
    for (const child of entries) collected.push(...await collectDroppedEntry(child, directoryPrefix));
  }
  return collected;
}

batchFilesInput.addEventListener("change", () => {
  setUploadItems(Array.from(batchFilesInput.files || []).map(file => ({file, path: file.name})));
});

batchFolderInput.addEventListener("change", () => {
  setUploadItems(Array.from(batchFolderInput.files || []).map(file => ({file, path: file.webkitRelativePath || file.name})));
});

document.getElementById("choose-files").addEventListener("click", () => batchFilesInput.click());
document.getElementById("choose-folder").addEventListener("click", () => batchFolderInput.click());
document.getElementById("clear-upload").addEventListener("click", clearUploadItems);
uploadFileList.addEventListener("click", event => {
  const button = event.target.closest("[data-upload-remove]");
  if (!button) return;
  state.uploadItems.splice(Number(button.dataset.uploadRemove), 1);
  updateUploadSummary();
});

for (const eventName of ["dragenter", "dragover"]) {
  uploadDropZone.addEventListener(eventName, event => {
    event.preventDefault();
    uploadDropZone.classList.add("drag-active");
  });
}
for (const eventName of ["dragleave", "drop"]) {
  uploadDropZone.addEventListener(eventName, event => {
    event.preventDefault();
    uploadDropZone.classList.remove("drag-active");
  });
}
uploadDropZone.addEventListener("drop", async event => {
  try {
    const entries = Array.from(event.dataTransfer.items || []).map(item => item.webkitGetAsEntry?.()).filter(Boolean);
    let items = [];
    if (entries.length) {
      for (const entry of entries) items.push(...await collectDroppedEntry(entry));
    } else {
      items = Array.from(event.dataTransfer.files || []).map(file => ({file, path: file.webkitRelativePath || file.name}));
    }
    setUploadItems(items, true);
  } catch (error) { notice(`读取拖拽文件失败：${error.message}`); }
});
uploadDropZone.addEventListener("keydown", event => {
  if (event.key === "Enter" || event.key === " ") { event.preventDefault(); batchFilesInput.click(); }
});

document.getElementById("batch-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = new FormData(event.target);
  const submitButton = document.getElementById("batch-submit");
  const payload = {
    input_path: form.get("input_path"), backend: form.get("backend"), lang: form.get("lang"),
    task_timeout: Number(form.get("task_timeout")),
    page_timeout_seconds: Number(form.get("page_timeout_seconds")),
    page_connect_max_retries: Number(form.get("page_connect_max_retries")),
    vlm_batch_size: Number(form.get("vlm_batch_size")),
    server_url: form.get("server_url") || null,
    recursive: form.get("recursive") === "on", effort: "medium", parse_method: "auto", pause_seconds: 2
  };
  try {
    submitButton.disabled = true;
    if (state.uploadItems.length) {
      const upload = new FormData();
      for (const item of state.uploadItems) upload.append("files", item.file, item.path);
      upload.append("backend", payload.backend);
      upload.append("lang", payload.lang);
      upload.append("task_timeout", String(payload.task_timeout));
      upload.append("page_timeout_seconds", String(payload.page_timeout_seconds));
      upload.append("page_connect_max_retries", String(payload.page_connect_max_retries));
      upload.append("vlm_batch_size", String(payload.vlm_batch_size));
      upload.append("server_url", payload.server_url || "");
      upload.append("recursive", String(payload.recursive || state.uploadItems.some(item => item.path.includes("/"))));
      submitButton.textContent = `正在上传 0%`;
      await uploadForm("/api/batch-runs/upload", upload, percent => {
        submitButton.textContent = percent < 100 ? `正在上传 ${percent}%` : "正在启动测试";
      });
      clearUploadItems();
    } else {
      if (!String(payload.input_path || "").trim()) throw new Error("请上传 PDF，或填写服务器测试目录");
      submitButton.textContent = "正在启动测试";
      await api("/api/batch-runs", {method: "POST", body: JSON.stringify(payload)});
    }
    notice("批量测试已开始", false);
    await loadBatches();
  } catch (error) {
    notice(error.message);
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = "开始测试";
  }
});

function exportFilename(runId, format) {
  if (format === "zip") return `mineru-batch-${runId}.zip`;
  if (format === "problem_pages") return `mineru-problem-pages-${runId}.zip`;
  if (format === "process_markdown") return `PROCESS_LOG-${runId}.md`;
  return `BATCH_DIAGNOSIS-${runId}.md`;
}

async function exportBatch(runId, format) {
  await downloadPath(
    `/api/batch-runs/${encodeURIComponent(runId)}/export?format=${encodeURIComponent(format)}`,
    exportFilename(runId, format),
  );
}

async function handleBatchTableClick(event) {
  const detailButton = event.target.closest("[data-batch-detail]");
  if (detailButton) {
    await loadBatchDetail(detailButton.dataset.batchDetail);
    return;
  }
  const button = event.target.closest("[data-batch-action]");
  if (!button) return;
  const action = button.dataset.batchAction;
  const prompt = action === "cancel"
    ? "确认停止这个批次诊断脚本？这不会取消已经提交到 MinerU Router/API 的后台任务，后台处理可能继续运行。"
    : `确认${button.textContent}该批次？`;
  if (["cancel", "retry", "delete"].includes(action) && !confirm(prompt)) return;
  try {
    await api(`/api/batch-runs/${button.dataset.batch}/${button.dataset.batchAction}`, {method: "POST"});
    await loadBatches();
  } catch (error) { notice(error.message); }
}

document.getElementById("batch-table").addEventListener("click", handleBatchTableClick);
document.getElementById("overview-batches").addEventListener("click", handleBatchTableClick);

function renderInlineMarkdown(value) {
  const tokens = [];
  const stash = html => {
    const token = `@@MINERU_MD_${tokens.length}@@`;
    tokens.push(html);
    return token;
  };
  let source = esc(value);
  source = source.replace(/`([^`]+)`/g, (_, code) => stash(`<code>${code}</code>`));
  source = source.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, (_, alt, target) => (
    stash(`<span class="markdown-image-ref" title="${esc(target)}">图片 ${alt || esc(target)}</span>`)
  ));
  source = source.replace(/\[([^\]]+)\]\(([^)\s]+)(?:\s+&quot;[^&]*&quot;)?\)/g, (match, label, target) => {
    const normalized = target.trim();
    if (/^(?:javascript|data|vbscript):/i.test(normalized)) return label;
    const external = /^(?:https?:|mailto:)/i.test(normalized);
    return stash(`<a href="${normalized}"${external ? ' target="_blank" rel="noopener noreferrer"' : ""}>${label}</a>`);
  });
  source = source
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/__([^_]+)__/g, "<strong>$1</strong>")
    .replace(/~~([^~]+)~~/g, "<del>$1</del>")
    .replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>");
  tokens.forEach((html, index) => { source = source.replace(`@@MINERU_MD_${index}@@`, html); });
  return source;
}

function splitMarkdownTableRow(line) {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").replaceAll("\\|", "\u0000").split("|").map(cell => cell.trim().replaceAll("\u0000", "|"));
}

function sanitizeMarkdownHtmlTable(value) {
  const documentRoot = new DOMParser().parseFromString(String(value || ""), "text/html");
  const allowedTags = new Set([
    "table", "caption", "colgroup", "col", "thead", "tbody", "tfoot",
    "tr", "th", "td", "br", "p", "span", "strong", "b", "em", "i",
    "u", "del", "sup", "sub", "code", "ul", "ol", "li",
  ]);
  const renderNode = node => {
    if (node.nodeType === 3) return esc(node.nodeValue || "");
    if (node.nodeType !== 1) return "";
    const tag = String(node.localName || "").toLowerCase();
    const children = Array.from(node.childNodes || []).map(renderNode).join("");
    if (!allowedTags.has(tag)) return children;
    const attributes = [];
    if (["td", "th"].includes(tag)) {
      for (const name of ["colspan", "rowspan"]) {
        const raw = node.getAttribute(name);
        const numeric = Number(raw);
        if (Number.isInteger(numeric) && numeric >= 1 && numeric <= 100) attributes.push(`${name}="${numeric}"`);
      }
      const align = String(node.getAttribute("align") || "").toLowerCase();
      if (["left", "center", "right"].includes(align)) attributes.push(`align="${align}"`);
    }
    if (tag === "col") {
      const span = Number(node.getAttribute("span"));
      if (Number.isInteger(span) && span >= 1 && span <= 100) attributes.push(`span="${span}"`);
    }
    const suffix = attributes.length ? ` ${attributes.join(" ")}` : "";
    if (["br", "col"].includes(tag)) return `<${tag}${suffix}>`;
    return `<${tag}${suffix}>${children}</${tag}>`;
  };
  const table = documentRoot.body.querySelector("table");
  if (!table) return `<pre><code>${esc(value)}</code></pre>`;
  return `<div class="markdown-table-wrap markdown-html-table">${renderNode(table)}</div>`;
}

function extractMarkdownHtmlTables(value) {
  const blocks = [];
  const markdown = String(value || "").replace(/<table\b[\s\S]*?<\/table>/gi, table => {
    const token = `MINERUHTMLTABLEBLOCK${blocks.length}`;
    blocks.push(sanitizeMarkdownHtmlTable(table));
    return `\n${token}\n`;
  });
  return {markdown, blocks};
}

function renderMarkdown(value) {
  const extracted = extractMarkdownHtmlTables(value);
  const lines = extracted.markdown.replaceAll("\r\n", "\n").replaceAll("\r", "\n").split("\n");
  const output = [];
  let codeLines = [];
  let inCode = false;
  let codeLanguage = "";
  let paragraph = [];
  let listType = null;
  let listItems = [];
  let quoteLines = [];

  const flushParagraph = () => {
    if (!paragraph.length) return;
    output.push(`<p>${paragraph.map(renderInlineMarkdown).join("<br>")}</p>`);
    paragraph = [];
  };
  const flushList = () => {
    if (!listItems.length || !listType) return;
    output.push(`<${listType}>${listItems.map(item => `<li>${item}</li>`).join("")}</${listType}>`);
    listItems = [];
    listType = null;
  };
  const flushQuote = () => {
    if (!quoteLines.length) return;
    output.push(`<blockquote><p>${quoteLines.map(renderInlineMarkdown).join("<br>")}</p></blockquote>`);
    quoteLines = [];
  };
  const flushTextBlocks = () => {
    flushParagraph();
    flushList();
    flushQuote();
  };

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const fence = line.trim().match(/^```\s*([^\s`]*)/);
    if (fence) {
      if (inCode) {
        output.push(`<pre><code${codeLanguage ? ` class="language-${esc(codeLanguage)}"` : ""}>${esc(codeLines.join("\n"))}</code></pre>`);
        codeLines = [];
        codeLanguage = "";
      } else {
        flushTextBlocks();
        codeLanguage = fence[1] || "";
      }
      inCode = !inCode;
      continue;
    }
    if (inCode) { codeLines.push(line); continue; }

    const htmlTableBlock = line.trim().match(/^MINERUHTMLTABLEBLOCK(\d+)$/);
    if (htmlTableBlock) {
      flushTextBlocks();
      output.push(extracted.blocks[Number(htmlTableBlock[1])] || "");
      continue;
    }

    const nextLine = lines[index + 1] || "";
    if (line.includes("|") && /^\s*\|?\s*:?-{3,}/.test(nextLine) && nextLine.includes("|")) {
      flushTextBlocks();
      const headers = splitMarkdownTableRow(line);
      index += 1;
      const rows = [];
      while (index + 1 < lines.length && lines[index + 1].includes("|") && lines[index + 1].trim()) {
        rows.push(splitMarkdownTableRow(lines[index + 1]));
        index += 1;
      }
      output.push(`<div class="markdown-table-wrap"><table><thead><tr>${headers.map(cell => `<th>${renderInlineMarkdown(cell)}</th>`).join("")}</tr></thead><tbody>${rows.map(row => `<tr>${headers.map((_, cellIndex) => `<td>${renderInlineMarkdown(row[cellIndex] || "")}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
      continue;
    }

    if (!line.trim()) {
      flushTextBlocks();
      continue;
    }

    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      flushTextBlocks();
      const level = heading[1].length;
      output.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
    } else if (/^\s*(?:---+|___+|\*\*\*+)\s*$/.test(line)) {
      flushTextBlocks();
      output.push("<hr>");
    } else if (/^>\s?/.test(line)) {
      flushParagraph();
      flushList();
      quoteLines.push(line.replace(/^>\s?/, ""));
    } else {
      const unordered = line.match(/^\s*[-*+]\s+(.+)$/);
      const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
      if (unordered || ordered) {
        flushParagraph();
        flushQuote();
        const nextType = ordered ? "ol" : "ul";
        if (listType && listType !== nextType) flushList();
        listType = nextType;
        let item = (unordered || ordered)[1];
        const task = item.match(/^\[([ xX])\]\s+(.+)$/);
        if (task) item = `<span class="markdown-task-marker">${task[1].toLowerCase() === "x" ? "✓" : "○"}</span>${renderInlineMarkdown(task[2])}`;
        else item = renderInlineMarkdown(item);
        listItems.push(item);
      } else {
        flushList();
        flushQuote();
        paragraph.push(line.trim());
      }
    }
  }
  flushTextBlocks();
  if (codeLines.length) output.push(`<pre><code>${esc(codeLines.join("\n"))}</code></pre>`);
  return output.join("") || `<div class="empty-state">提取结果为空</div>`;
}

async function fetchArtifactBlob(runId, kind, path) {
  const response = await authorizedFetch(artifactUrl(runId, kind, path));
  return response.blob();
}

async function showBatchDocument(format, {preserveScroll = false} = {}) {
  if (!state.activeBatchId) return;
  const preview = document.getElementById("batch-document-preview");
  const previousRatio = preserveScroll && preview.scrollHeight > preview.clientHeight ? preview.scrollTop / (preview.scrollHeight - preview.clientHeight) : 0;
  state.activeBatchDocument = format;
  document.querySelectorAll("[data-batch-document]").forEach(button => button.classList.toggle("active", button.dataset.batchDocument === format));
  preview.innerHTML = `<div class="empty-state">正在读取...</div>`;
  try {
    const response = await authorizedFetch(`/api/batch-runs/${encodeURIComponent(state.activeBatchId)}/export?format=${encodeURIComponent(format)}`);
    preview.innerHTML = renderMarkdown(await response.text());
    if (preserveScroll) preview.scrollTop = previousRatio * Math.max(0, preview.scrollHeight - preview.clientHeight);
  } catch (error) {
    preview.innerHTML = `<div class="empty-state">读取失败：${esc(error.message)}</div>`;
  }
}

async function loadBatchDetail(runId) {
  const dialog = document.getElementById("batch-detail-dialog");
  try {
    const detail = await api(`/api/batch-runs/${encodeURIComponent(runId)}`);
    state.activeBatchId = runId;
    state.activeBatchDetail = detail;
    document.getElementById("batch-detail-title").textContent = detail.settings?.input_path || "批量任务详情";
    document.getElementById("batch-detail-meta").textContent = `${runId} · ${statusText[detail.status] || detail.status} · ${detail.settings?.pdf_count || 0} 个 PDF · 产物 ${formatBytes(detail.artifacts?.storage_bytes || 0)} · 保留 ${detail.artifacts?.retention_days ?? "-"} 天`;
    const reportButton = document.querySelector('[data-batch-document="markdown"]');
    reportButton.disabled = !detail.report_ready;
    if (!dialog.open) dialog.showModal();
    await showBatchDocument(detail.report_ready ? "markdown" : "process_markdown");
  } catch (error) { notice(error.message); }
}

document.getElementById("batch-document-tabs").addEventListener("click", event => {
  const button = event.target.closest("[data-batch-document]");
  if (button && !button.disabled) showBatchDocument(button.dataset.batchDocument);
});

document.getElementById("export-current-batch-document").addEventListener("click", async () => {
  if (!state.activeBatchId) return;
  try { await exportBatch(state.activeBatchId, state.activeBatchDocument || "process_markdown"); }
  catch (error) { notice(error.message); }
});

document.querySelector("#batch-detail-dialog .dialog-actions").addEventListener("click", async event => {
  const button = event.target.closest("[data-dialog-export]");
  if (!button || !state.activeBatchId) return;
  try { await exportBatch(state.activeBatchId, button.dataset.dialogExport); }
  catch (error) { notice(error.message); }
});

function closeBatchDetail() {
  const dialog = document.getElementById("batch-detail-dialog");
  clearPreviewObjectUrls();
  state.activeBatchId = null;
  state.activeBatchDetail = null;
  state.activeBatchDocument = null;
  if (dialog.open) dialog.close();
}

document.getElementById("close-batch-detail").addEventListener("click", closeBatchDetail);
document.getElementById("batch-detail-dialog").addEventListener("close", () => {
  clearPreviewObjectUrls();
  state.activeBatchId = null;
  state.activeBatchDetail = null;
  state.activeBatchDocument = null;
});

async function refreshActiveBatchDocument() {
  const runId = state.activeBatchId;
  if (!runId) return;
  try {
    const detail = await api(`/api/batch-runs/${encodeURIComponent(runId)}`);
    if (state.activeBatchId !== runId) return;
    state.activeBatchDetail = detail;
    document.getElementById("batch-detail-meta").textContent = `${detail.run_id} · ${statusText[detail.status] || detail.status} · ${detail.settings?.pdf_count || 0} 个 PDF · 产物 ${formatBytes(detail.artifacts?.storage_bytes || 0)} · 保留 ${detail.artifacts?.retention_days ?? "-"} 天`;
    const reportButton = document.querySelector('[data-batch-document="markdown"]');
    reportButton.disabled = !detail.report_ready;
    if (state.activeBatchDocument === "process_markdown") await showBatchDocument("process_markdown", {preserveScroll: true});
  } catch {}
}

async function loadTaskPdfPage(taskId, pageElement, generation) {
  if (pageElement.dataset.loaded === "true" || pageElement.dataset.loading === "true") return;
  pageElement.dataset.loading = "true";
  const pageNumber = Number(pageElement.dataset.pageNumber);
  try {
    const response = await authorizedFetch(`/api/tasks/${encodeURIComponent(taskId)}/preview/pages/${pageNumber}`);
    const blob = await response.blob();
    if (state.activeTaskPreviewId !== taskId || state.taskPreviewGeneration !== generation) return;
    const url = URL.createObjectURL(blob);
    state.previewObjectUrls.push(url);
    pageElement.innerHTML = `<img src="${url}" alt="第 ${pageNumber} 页"><span>第 ${pageNumber} 页</span>`;
    pageElement.dataset.loaded = "true";
  } catch (error) {
    pageElement.innerHTML = `<div class="empty-state">第 ${pageNumber} 页读取失败：${esc(error.message)}</div>`;
  } finally {
    pageElement.dataset.loading = "false";
  }
}

function drainTaskPdfPageQueue() {
  while (state.taskPreviewPageActive < 2 && state.taskPreviewPageQueue.length) {
    const item = state.taskPreviewPageQueue.shift();
    item.pageElement.dataset.queued = "false";
    if (
      item.generation !== state.taskPreviewGeneration
      || item.taskId !== state.activeTaskPreviewId
    ) continue;
    state.taskPreviewPageActive += 1;
    loadTaskPdfPage(item.taskId, item.pageElement, item.generation).finally(() => {
      state.taskPreviewPageActive -= 1;
      drainTaskPdfPageQueue();
    });
  }
}

function queueTaskPdfPage(taskId, pageElement, generation) {
  if (
    pageElement.dataset.loaded === "true"
    || pageElement.dataset.loading === "true"
    || pageElement.dataset.queued === "true"
  ) return;
  pageElement.dataset.queued = "true";
  state.taskPreviewPageQueue.push({taskId, pageElement, generation});
  drainTaskPdfPageQueue();
}

async function openTaskPreview(taskId) {
  const dialog = document.getElementById("task-preview-dialog");
  try {
    const detail = await api(`/api/tasks/${encodeURIComponent(taskId)}/preview`);
    const generation = ++state.taskPreviewGeneration;
    state.activeTaskPreviewId = taskId;
    state.activeTaskPreview = detail;
    clearPreviewObjectUrls();
    document.getElementById("task-preview-title").textContent = detail.file_name || "任务结果预览";
    document.getElementById("task-preview-meta").textContent = `${taskId} · ${statusText[detail.classification] || detail.classification || "已完成"}`;
    document.getElementById("task-pdf-page-count").textContent = `${detail.original.page_count} 页`;
    document.getElementById("task-preview-status").textContent = detail.failed_pages?.length ? `跳过/失败 ${detail.failed_pages.length} 页` : "完整结果";
    const pageRoot = document.getElementById("task-original-pages");
    pageRoot.innerHTML = Array.from({length: detail.original.page_count}, (_, index) => `<div class="task-pdf-page" data-page-number="${index + 1}"><div class="empty-state">第 ${index + 1} 页加载中...</div></div>`).join("");
    const resultPreview = document.getElementById("task-result-preview");
    resultPreview.innerHTML = `<div class="empty-state">正在读取 Markdown...</div>`;
    const resultButton = document.getElementById("download-task-result");
    resultButton.disabled = !detail.preview.archive_path;
    if (!dialog.open) dialog.showModal();

    state.taskPreviewObserver?.disconnect();
    state.taskPreviewObserver = new IntersectionObserver(entries => {
      for (const entry of entries) if (entry.isIntersecting) queueTaskPdfPage(taskId, entry.target, generation);
    }, {root: pageRoot, rootMargin: "300px 0px"});
    pageRoot.querySelectorAll("[data-page-number]").forEach(element => state.taskPreviewObserver.observe(element));

    if (detail.preview.markdown_path) {
      const response = await authorizedFetch(artifactUrl(detail.run_id, "results", detail.preview.markdown_path));
      if (state.activeTaskPreviewId === taskId) resultPreview.innerHTML = renderMarkdown(await response.text());
    } else {
      resultPreview.innerHTML = `<div class="empty-state">该任务没有生成 Markdown 结果</div>`;
    }
  } catch (error) { notice(error.message); }
}

function closeTaskPreview() {
  const dialog = document.getElementById("task-preview-dialog");
  state.taskPreviewObserver?.disconnect();
  state.taskPreviewObserver = null;
  state.taskPreviewGeneration += 1;
  state.taskPreviewPageQueue = [];
  state.activeTaskPreviewId = null;
  state.activeTaskPreview = null;
  clearPreviewObjectUrls();
  if (dialog.open) dialog.close();
}

document.getElementById("close-task-preview").addEventListener("click", closeTaskPreview);
document.getElementById("task-preview-dialog").addEventListener("close", closeTaskPreview);
document.getElementById("download-task-original").addEventListener("click", async () => {
  const detail = state.activeTaskPreview;
  if (!detail) return;
  try { await downloadPath(artifactUrl(detail.run_id, detail.original.kind || "input", detail.original.path), detail.original.name); }
  catch (error) { notice(error.message); }
});
document.getElementById("download-task-result").addEventListener("click", async () => {
  const detail = state.activeTaskPreview;
  if (!detail?.preview?.archive_path) return;
  try { await downloadPath(artifactUrl(detail.run_id, "results", detail.preview.archive_path), `${detail.file_name || "result"}-result.zip`); }
  catch (error) { notice(error.message); }
});

let taskPreviewSyncFrame = false;
function syncTaskPreviewScroll(source, target) {
  if (!document.getElementById("task-preview-sync").checked || taskPreviewSyncFrame) return;
  const sourceRange = source.scrollHeight - source.clientHeight;
  const targetRange = target.scrollHeight - target.clientHeight;
  if (sourceRange <= 0 || targetRange <= 0) return;
  taskPreviewSyncFrame = true;
  target.scrollTop = (source.scrollTop / sourceRange) * targetRange;
  requestAnimationFrame(() => { taskPreviewSyncFrame = false; });
}
const taskPdfScroll = document.getElementById("task-original-pages");
const taskMarkdownScroll = document.getElementById("task-result-preview");
taskPdfScroll.addEventListener("scroll", () => syncTaskPreviewScroll(taskPdfScroll, taskMarkdownScroll));
taskMarkdownScroll.addEventListener("scroll", () => syncTaskPreviewScroll(taskMarkdownScroll, taskPdfScroll));

function updateLogServices() {
  const select = document.getElementById("log-service");
  const selected = select.value;
  select.innerHTML = state.services.filter(item => item.role !== "vlm" || item.runtime).map(item => `<option value="${esc(item.name)}">${esc(item.name)}</option>`).join("");
  if ([...select.options].some(option => option.value === selected)) select.value = selected;
  if (!select.value && select.options.length) select.selectedIndex = 0;
}

function setLogStatus(message, bad = false) {
  const status = document.getElementById("log-status");
  status.textContent = message;
  status.classList.toggle("bad-text", bad);
}

async function loadSelectedServiceLogs({showLoading = false} = {}) {
  const service = document.getElementById("log-service").value;
  const tail = document.getElementById("log-tail").value;
  const output = document.getElementById("log-output");
  if (!service || state.logLoading) return;
  const requestId = ++state.logRequestId;
  const follow = document.getElementById("log-follow").checked;
  const nearBottom = output.scrollHeight - output.scrollTop - output.clientHeight < 48;
  state.logLoading = true;
  if (showLoading && !output.textContent.trim()) output.textContent = "正在读取...";
  setLogStatus(`${service} · 正在刷新`);
  try {
    const data = await api(`/api/services/${encodeURIComponent(service)}/logs?tail=${tail}`);
    if (requestId !== state.logRequestId || service !== document.getElementById("log-service").value) return;
    output.textContent = data.logs || "没有日志输出。";
    if (follow || nearBottom) output.scrollTop = output.scrollHeight;
    setLogStatus(`${service} · ${new Date().toLocaleTimeString("zh-CN", {hour12: false})} 已更新`);
  } catch (error) {
    if (requestId === state.logRequestId) {
      output.textContent = `日志读取失败：${error.message}`;
      setLogStatus(`${service} · 读取失败`, true);
    }
  } finally {
    if (requestId === state.logRequestId) state.logLoading = false;
  }
}

document.getElementById("load-logs").addEventListener("click", () => loadSelectedServiceLogs({showLoading: true}));
document.getElementById("log-service").addEventListener("change", () => {
  state.logRequestId += 1;
  state.logLoading = false;
  document.getElementById("log-output").textContent = "正在切换服务日志...";
  loadSelectedServiceLogs({showLoading: true});
});
document.getElementById("log-tail").addEventListener("change", () => loadSelectedServiceLogs({showLoading: true}));
document.getElementById("log-live").addEventListener("change", event => {
  setLogStatus(event.currentTarget.checked ? "动态刷新已开启" : "动态刷新已暂停");
  if (event.currentTarget.checked) loadSelectedServiceLogs();
});
document.getElementById("log-follow").addEventListener("change", event => {
  if (event.currentTarget.checked) {
    const output = document.getElementById("log-output");
    output.scrollTop = output.scrollHeight;
  }
});

async function refreshCurrent() {
  notice("");
  try {
    if (state.view === "overview") await loadOverview();
    else if (state.view === "services") await loadServices();
    else if (state.view === "tasks") await refreshTasksView();
    else if (state.view === "batch") await loadBatches();
    else if (state.view === "logs") {
      if (!state.services.length) await loadServices();
      else updateLogServices();
      await loadSelectedServiceLogs({showLoading: true});
    }
    setConnection(true, "控制台已连接");
  } catch (error) {
    setConnection(false, "连接异常");
    notice(error.message);
  }
}

setInterval(() => {
  if (["overview", "services", "batch"].includes(state.view)) refreshCurrent();
  if (state.activeBatchId) refreshActiveBatchDocument();
}, 5000);
setInterval(() => {
  if (state.view === "logs" && document.getElementById("log-live").checked) loadSelectedServiceLogs();
}, 2000);
setInterval(() => {
  if (state.view === "tasks") refreshTasksView();
}, 2000);
refreshCurrent();
