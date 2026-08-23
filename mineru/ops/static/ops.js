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
  config: null,
  configSchema: null,
  configHistory: [],
  configDraft: {},
  configValidationErrors: {},
  configPlan: null,
  configApplying: false,
  configApplyResult: null,
  labSubmitting: false,
  labRuns: [],
  labSelectedRuns: new Set(),
  labComparison: null,
  labFilters: {query: "", environment: "", hardware: ""},
  labComparing: false,
  problemPages: [],
  selectedProblemPages: new Set(),
  problemPagesSubmitting: false,
  runtimeDiagnostics: null,
  runtimeDiagnosticsLoading: false,
  configStatus: null,
  configStatusLoading: false,
  deepDiagnostics: null,
  deepDiagnosticsLoading: false,
  auditLogs: null,
  auditLoading: false,
  auditOffset: 0,
  token: sessionStorage.getItem("mineruOpsToken") || "",
};

const titles = {
  overview: "总览",
  services: "服务",
  tasks: "任务",
  batch: "批量测试",
  lab: "性能实验室",
  config: "配置中心",
  logs: "日志",
};
const AUDIT_PAGE_SIZE = 50;

const statusText = {
  pending: "等待中", queued: "等待处理", processing: "处理中", completed: "已完成", failed: "失败",
  partial_success: "部分成功", running: "运行中", paused: "已暂停", cancelling: "正在停止", cancelled: "已取消",
  completed_with_failures: "完成（存在失败）", interrupted: "已中断", unhealthy: "异常",
  healthy: "健康", unavailable: "不可用", unknown: "未知", success: "成功", partial: "部分成功",
  client_error: "客户端错误", wait_timeout: "停止等待（后台可能仍在运行）",
  monitoring_stopped: "停止监控", applied: "已生效", pending_restart: "等待重启生效",
  partially_applied: "部分服务已生效", not_created: "容器未创建", skipped: "已跳过",
  conflict: "配置冲突", inconsistent: "实例不一致"
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
  const cls = ["healthy", "completed", "running", "success", "applied"].includes(value) ? "good" :
    ["pending", "processing", "paused", "cancelling", "partial", "partial_success", "completed_with_failures", "wait_timeout", "monitoring_stopped", "pending_restart", "partially_applied", "not_created", "skipped", "inconsistent"].includes(value) ? "warn" :
    ["failed", "unhealthy", "unavailable", "cancelled", "interrupted", "conflict"].includes(value) ? "bad" : "";
  return `<span class="badge ${cls}">${esc(text)}</span>`;
}

function apiErrorMessage(payload, fallback) {
  const detail = payload?.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    return detail.error || detail.message || JSON.stringify(detail);
  }
  return payload?.error || payload?.message || fallback;
}

async function api(path, options = {}) {
  const headers = {...(options.headers || {})};
  if (state.token) headers["X-MinerU-Ops-Token"] = state.token;
  if (options.body && !(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {...options, headers});
  let payload;
  try { payload = await response.json(); } catch { payload = {detail: await response.text()}; }
  if (!response.ok) throw new Error(apiErrorMessage(payload, `HTTP ${response.status}`));
  return payload;
}

async function authorizedFetch(path, options = {}) {
  const headers = {...(options.headers || {})};
  if (state.token) headers["X-MinerU-Ops-Token"] = state.token;
  const response = await fetch(path, {...options, headers});
  if (!response.ok) {
    let payload = {};
    try { payload = await response.json(); } catch {}
    throw new Error(apiErrorMessage(payload, `HTTP ${response.status}`));
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
  const runtimeState = String(service.runtime?.state || "unknown").toLowerCase();
  const runtimeHealth = String(service.runtime?.health || "unknown").toLowerCase();
  const cls = service.health === "healthy" ? "good" : ["unhealthy", "unavailable"].includes(service.health) ? "bad" : "warn";
  const runtimeHealthClass = runtimeHealth === "healthy" ? "good" : runtimeHealth === "starting" ? "warn" : runtimeHealth === "unhealthy" ? "bad" : "";
  const runtimeText = {running: "运行中", restarting: "重启中", paused: "已暂停", exited: "已退出", dead: "已停止", created: "已创建", unknown: "未知"}[runtimeState] || runtimeState;
  const runtimeHealthText = {healthy: "健康", unhealthy: "异常", starting: "启动中", none: "无探活", unknown: "未知"}[runtimeHealth] || runtimeHealth;
  const windowSize = service.health_payload?.processing_window_size;
  const workload = windowSize ? `窗口 ${windowSize} · 处理中 ${service.health_payload?.processing_tasks || 0}` : "";
  const httpDetail = service.health_status_code ? `HTTP ${service.health_status_code}` : service.health_error || "未配置端点";
  return `<article class="service-card ${cls}"><div class="service-card-head"><div><h3>${esc(service.name)}</h3><span class="muted">${esc(String(service.role).toUpperCase())}</span></div>${badge(service.health)}</div><div class="service-card-metrics"><div><small>容器状态</small><strong>${esc(runtimeText)}</strong><span class="runtime-health ${runtimeHealthClass}">Docker 健康：${esc(runtimeHealthText)}</span></div><div><small>HTTP 探活</small><strong>${esc(httpDetail)}</strong><span class="muted">${esc(service.endpoint || "无检测端点")}</span></div></div>${workload ? `<p class="service-workload">${esc(workload)}</p>` : ""}</article>`;
}

async function loadServices() {
  state.services = await api("/api/services");
  renderServicesTable();
  updateLogServices();
}

function formatBytes(value) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes < 0) return "-";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = bytes;
  let index = 0;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  return `${size.toFixed(index ? 1 : 0)} ${units[index]}`;
}

function diagnosticFlag(value, trueText = "已配置", falseText = "未配置") {
  return `<span class="diagnostic-state ${value ? "good" : "bad"}">${value ? trueText : falseText}</span>`;
}

function renderRuntimeDiagnostics(payload) {
  const target = document.getElementById("runtime-diagnostics");
  if (!payload) {
    target.innerHTML = `<div class="empty-state">暂无诊断信息</div>`;
    return;
  }
  if (payload.load_error) {
    target.innerHTML = `<div class="empty-state bad-text">诊断读取失败：${esc(payload.load_error)}</div>`;
    return;
  }
  const offline = payload.offline || {};
  const configuration = payload.configuration || {};
  const sourceText = {local: "本地模型", modelscope: "ModelScope", huggingface: "Hugging Face", unknown: "未设置或不支持"}[offline.model_source] || offline.model_source || "未知";
  const networkText = offline.network_access === "not_checked" ? "未检测网络（诊断不会联网）" : (offline.network_access || "未知");
  const models = Array.isArray(payload.models) ? payload.models : [];
  const devices = Array.isArray(payload.devices) ? payload.devices : [];
  const commands = payload.commands || {};
  const warnings = Array.isArray(payload.warnings) ? payload.warnings : [];
  const modelCards = models.map(model => `<article class="runtime-diagnostic-card"><h3>模型路径：${esc(model.name || "未命名")}</h3><dl><div><dt>路径</dt><dd class="mono">${esc(model.path || "-")}</dd></div><div><dt>存在 / 可读</dt><dd>${diagnosticFlag(Boolean(model.exists), "存在", "不存在")} ${diagnosticFlag(Boolean(model.readable), "可读", "不可读")}</dd></div><div><dt>文件数</dt><dd>${esc(model.file_count ?? 0)}${model.scan_limited ? "（扫描已截断）" : ""}</dd></div><div><dt>已扫描大小</dt><dd>${esc(formatBytes(model.size_bytes))}</dd></div><div><dt>加载状态</dt><dd>${esc(model.load_state || "unknown")}</dd></div></dl>${model.scan_error ? `<p class="runtime-diagnostic-error">扫描错误：${esc(model.scan_error)}</p>` : ""}</article>`).join("");
  const commandCards = [["nvidia_smi", "NVIDIA GPU / nvidia-smi"], ["npu_smi", "昇腾 NPU / npu-smi"]].map(([key, label]) => {
    const command = commands[key] || {};
    const status = !command.available ? "工具不可用" : command.ok ? "执行成功" : "执行失败";
    const statusClass = command.ok ? "good" : command.available ? "warn" : "bad";
    return `<article class="runtime-diagnostic-card"><h3>${label}</h3><dl><div><dt>状态</dt><dd><span class="diagnostic-state ${statusClass}">${status}</span></dd></div>${command.return_code != null ? `<div><dt>退出码</dt><dd>${esc(command.return_code)}</dd></div>` : ""}<div><dt>输出截断</dt><dd>${command.truncated ? "是" : "否"}</dd></div></dl>${command.error ? `<p class="runtime-diagnostic-error">${esc(command.error)}</p>` : ""}${command.stdout ? `<details><summary>标准输出</summary><pre>${esc(command.stdout)}</pre></details>` : ""}${command.stderr ? `<details><summary>错误输出</summary><pre>${esc(command.stderr)}</pre></details>` : ""}</article>`;
  }).join("");
  target.innerHTML = `<div class="runtime-diagnostics-meta"><span>生成时间：${esc(formatDate(payload.generated_at))}</span><span>网络状态：${esc(networkText)}</span></div><div class="runtime-diagnostics-grid"><article class="runtime-diagnostic-card"><h3>离线与模型来源</h3><dl><div><dt>离线模式</dt><dd>${diagnosticFlag(Boolean(offline.configured), "已配置", "未完整配置")}</dd></div><div><dt>模型来源</dt><dd>${esc(sourceText)}</dd></div><div><dt>ModelScope 离线</dt><dd>${diagnosticFlag(Boolean(offline.modelscope_disabled), "开启", "关闭")}</dd></div><div><dt>Hugging Face 离线</dt><dd>${diagnosticFlag(Boolean(offline.huggingface_disabled), "开启", "关闭")}</dd></div><div><dt>Transformers 离线</dt><dd>${diagnosticFlag(Boolean(offline.transformers_disabled), "开启", "关闭")}</dd></div></dl></article><article class="runtime-diagnostic-card"><h3>关键配置可见性</h3><dl><div><dt>Tools 配置</dt><dd>${diagnosticFlag(Boolean(configuration.tools_config_configured))}</dd></div><div><dt>VLM 模型</dt><dd>${diagnosticFlag(Boolean(configuration.vlm_model_configured))}</dd></div><div><dt>CUDA 设备</dt><dd>${diagnosticFlag(Boolean(configuration.cuda_visible_devices_configured))}</dd></div><div><dt>昇腾设备</dt><dd>${diagnosticFlag(Boolean(configuration.ascend_visible_devices_configured))}</dd></div><div><dt>检测到的设备</dt><dd>${devices.length ? devices.map(device => esc(`${device.vendor || "unknown"} ${device.type || "device"}`)).join("、") : "未检测到"}</dd></div></dl></article>${modelCards}${commandCards}</div>${warnings.length ? `<div class="runtime-diagnostics-warnings"><strong>诊断提示</strong><ul>${warnings.map(item => `<li>${esc(item)}</li>`).join("")}</ul></div>` : ""}`;
}

async function loadRuntimeDiagnostics({force = false} = {}) {
  if (state.runtimeDiagnosticsLoading) return;
  if (!force && state.runtimeDiagnostics !== null) {
    renderRuntimeDiagnostics(state.runtimeDiagnostics);
    return;
  }
  const target = document.getElementById("runtime-diagnostics");
  const button = document.getElementById("runtime-diagnostics-refresh");
  state.runtimeDiagnosticsLoading = true;
  button.disabled = true;
  target.innerHTML = `<div class="empty-state">正在读取运行时诊断...</div>`;
  try {
    state.runtimeDiagnostics = await api("/api/diagnostics/runtime");
    renderRuntimeDiagnostics(state.runtimeDiagnostics);
  } catch (error) {
    state.runtimeDiagnostics = {load_error: error.message};
    renderRuntimeDiagnostics(state.runtimeDiagnostics);
  } finally {
    state.runtimeDiagnosticsLoading = false;
    button.disabled = false;
  }
}

function renderServicesTable() {
  const services = state.services.filter(item => state.serviceFilter === "all" || item.role === state.serviceFilter);
  document.getElementById("services-table").innerHTML = `<table><thead><tr><th>服务</th><th>角色</th><th>容器</th><th>健康</th><th>镜像/端点</th><th>操作</th></tr></thead><tbody>${services.map(service => {
    const actions = service.control_enabled ? ["check", "start", "stop", "restart"] : ["check"];
    const runtimeState = String(service.runtime?.state || "unknown").toLowerCase();
    const runtimeHealth = String(service.runtime?.health || "unknown").toLowerCase();
    const runtimeLabel = {running: "运行中", restarting: "重启中", paused: "已暂停", exited: "已退出", dead: "已停止", created: "已创建", unknown: "未知"}[runtimeState] || runtimeState;
    const runtimeHealthLabel = {healthy: "健康", unhealthy: "异常", starting: "启动中", none: "无探活", unknown: "未知"}[runtimeHealth] || runtimeHealth;
    const endpointDetail = service.health_status_code ? `HTTP ${service.health_status_code}` : service.health_error || "-";
    return `<tr><td><strong>${esc(service.name)}</strong></td><td>${esc(service.role)}</td><td><strong>${esc(runtimeLabel)}</strong><div class="muted">Docker 健康：${esc(runtimeHealthLabel)}</div></td><td>${badge(service.health)}<div class="muted">${esc(endpointDetail)}</div>${service.health_message ? `<div class="muted">${esc(service.health_message)}</div>` : ""}</td><td><div class="mono service-endpoint">${esc(service.image || service.endpoint || "-")}</div></td><td><div class="actions">${actions.map(action => `<button class="action-button ${action === "stop" ? "danger" : ""}" data-service="${esc(service.name)}" data-action="${action}">${{check:"检查",start:"启动",stop:"停止",restart:"重启"}[action]}</button>`).join("")}</div></td></tr>`;
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

function formatRate(value) {
  if (value == null || !Number.isFinite(Number(value))) return "-";
  return `${(Number(value) * 100).toFixed(1)}%`;
}

function renderPageTimingSummary(summary) {
  if (!summary || !summary.recorded_pages) {
    return `<div class="task-timing-empty">暂无已保存的页级耗时记录</div>`;
  }
  const cards = [
    ["已记录页面", summary.recorded_pages],
    ["完成页", summary.completed_pages || 0],
    ["跳过/失败页", (summary.skipped_pages || 0) + (summary.failed_pages || 0)],
    ["超时页", `${summary.timeout_pages || 0}（${formatRate(summary.timeout_rate)}）`],
    ["发生重试页", `${summary.retry_pages || 0}（${formatRate(summary.retry_rate)}）`],
    ["平均 VLM 请求", formatPageSeconds(summary.completed_average_vlm_request_seconds)],
    ["P50", formatPageSeconds(summary.completed_p50_vlm_request_seconds)],
    ["P95", formatPageSeconds(summary.completed_p95_vlm_request_seconds)],
    ["P99", formatPageSeconds(summary.completed_p99_vlm_request_seconds)],
    ["最慢请求", formatPageSeconds(summary.completed_max_vlm_request_seconds)],
    ["成功尝试总时长", formatPageSeconds(summary.successful_attempt_seconds)],
    ["失败尝试总时长", formatPageSeconds(summary.failed_attempt_seconds)],
    ["重试等待", formatPageSeconds(summary.retry_overhead_seconds)],
    ["含重试总耗时", formatPageSeconds(summary.with_retry_wall_seconds)],
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

function batchActions(run) {
  const runId = esc(run.run_id);
  if (run.status === "cancelling") {
    return `<button class="action-button cancel-pending" disabled aria-disabled="true">正在停止…</button>`;
  }
  if (run.status === "running") {
    return `<button class="action-button" data-batch="${runId}" data-batch-action="pause">暂停</button><button class="action-button danger" data-batch="${runId}" data-batch-action="cancel">停止批次脚本</button>`;
  }
  if (run.status === "paused") {
    return `<button class="action-button" data-batch="${runId}" data-batch-action="resume">继续</button><button class="action-button danger" data-batch="${runId}" data-batch-action="cancel">停止批次脚本</button>`;
  }
  if (run.status === "pending") {
    return `<button class="action-button danger" data-batch="${runId}" data-batch-action="cancel">停止批次脚本</button>`;
  }
  return `<button class="action-button" data-problem-pages-retry="${runId}">重试异常页</button><button class="action-button" data-batch="${runId}" data-batch-action="retry">重试全部</button><button class="action-button danger" data-batch="${runId}" data-batch-action="delete">删除</button>`;
}

function compactBatchRows(items, limit = 100) {
  if (!items.length) return `<div class="empty-state">暂无批量测试</div>`;
  return `<table><thead><tr><th>目录</th><th>状态</th><th>PDF</th><th>开始时间</th><th>记录</th><th>操作</th></tr></thead><tbody>${items.slice(0, limit).map(run => {
    const previewText = run.input_preview_ready || run.result_preview_ready ? "可预览" : "";
    const retrySource = run.settings?.source_type === "problem_page_retry" && run.settings?.retry_of_run_id
      ? `<div class="muted">异常页重试 · 来源 ${esc(run.settings.retry_of_run_id)}</div>`
      : "";
    return `<tr><td><strong>${esc(run.settings?.input_path || run.input_path)}</strong>${retrySource}<div class="mono muted">${esc(run.run_id)}</div></td><td>${badge(run.status)}${previewText ? `<div class="muted">${previewText}</div>` : ""}</td><td>${run.settings?.pdf_count || "-"}</td><td>${esc(formatDate(run.started_at || run.created_at))}</td><td><button class="text-button" data-batch-detail="${esc(run.run_id)}">查看记录</button></td><td><div class="actions">${batchActions(run)}</div></td></tr>`;
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
  const problemRetryButton = event.target.closest("[data-problem-pages-retry]");
  if (problemRetryButton) {
    await openProblemPagesRetry(problemRetryButton.dataset.problemPagesRetry);
    return;
  }
  const detailButton = event.target.closest("[data-batch-detail]");
  if (detailButton) {
    await loadBatchDetail(detailButton.dataset.batchDetail);
    return;
  }
  const button = event.target.closest("[data-batch-action]");
  if (!button || button.disabled) return;
  const action = button.dataset.batchAction;
  const prompt = action === "cancel"
    ? "确认停止这个批次诊断脚本？\n\n这只会停止运维控制台本地的 batch-router-diagnose.py。\n已经提交到 MinerU Router/API 的远程任务不会被取消，可能继续运行。"
    : `确认${button.textContent}该批次？`;
  if (["cancel", "retry", "delete"].includes(action) && !confirm(prompt)) return;
  try {
    await api(`/api/batch-runs/${button.dataset.batch}/${button.dataset.batchAction}`, {method: "POST"});
    await loadBatches();
  } catch (error) { notice(error.message); }
}

document.getElementById("batch-table").addEventListener("click", handleBatchTableClick);
document.getElementById("overview-batches").addEventListener("click", handleBatchTableClick);

const problemPagesRetryDialog = document.getElementById("problem-pages-retry-dialog");
const problemPagesRetryForm = document.getElementById("problem-pages-retry-form");

function closeProblemPagesRetry() {
  if (problemPagesRetryDialog.open) problemPagesRetryDialog.close();
}

function problemPageSourcePath(page, file, preview) {
  return String(
    page?.source_path
    || page?.relative_path
    || file?.source_path
    || file?.relative_path
    || preview?.source_path
    || preview?.relative_path
    || file?.file_name
    || preview?.file_name
    || "unknown.pdf"
  ).replaceAll("\\", "/");
}

function problemPageIsTimeout(page) {
  const text = `${page?.error_type || ""} ${page?.error || ""}`.toLowerCase();
  return page?.status === "skipped" || /timeout|timed out|wait_timeout|超时/.test(text);
}

function extractProblemPages(detail) {
  const previews = detail?.artifacts?.previews || detail?.previews || [];
  const pages = [];
  const addPage = (page, file = {}, preview = {}) => {
    if (!page || !["skipped", "failed"].includes(String(page.status || "").toLowerCase())) return;
    const pageNumber = Number(page.page_number ?? page.page_idx + 1);
    if (!Number.isInteger(pageNumber) || pageNumber < 1) return;
    const sourcePath = problemPageSourcePath(page, file, preview);
    const key = JSON.stringify([sourcePath, pageNumber]);
    if (pages.some(item => item.key === key)) return;
    pages.push({
      key,
      source_path: sourcePath,
      page_number: pageNumber,
      status: String(page.status || "failed").toLowerCase(),
      error_type: page.error_type || "",
      error: page.error || "",
      vlm_request_seconds: page.vlm_request_seconds ?? page.inference_seconds ?? null,
      total_seconds: page.total_seconds ?? page.elapsed_seconds ?? null,
    });
  };
  for (const preview of previews) {
    for (const file of preview?.progress?.files || []) {
      for (const page of file?.pages || []) addPage(page, file, preview);
    }
    for (const page of preview?.failed_pages || []) addPage(page, {}, preview);
  }
  for (const preview of detail?.progress?.files || []) {
    for (const page of preview?.pages || []) addPage(page, preview, detail);
  }
  return pages.sort((left, right) => left.source_path.localeCompare(right.source_path) || left.page_number - right.page_number);
}

function renderProblemPagesSelector() {
  const list = document.getElementById("problem-pages-list");
  const summary = document.getElementById("problem-page-selection-summary");
  const pages = state.problemPages;
  const selected = pages.filter(page => state.selectedProblemPages.has(page.key)).length;
  document.getElementById("problem-pages-select-all").disabled = !pages.length;
  document.getElementById("problem-pages-select-none").disabled = !selected;
  document.getElementById("problem-pages-select-timeout").disabled = !pages.some(problemPageIsTimeout);
  document.getElementById("problem-pages-select-failed").disabled = !pages.some(page => page.status === "failed");
  summary.textContent = pages.length
    ? `共 ${pages.length} 页异常，已选择 ${selected} 页。默认全选；提交时只会截取选中的页面。`
    : "没有发现可重试的跳过或失败页面。";
  list.innerHTML = pages.length ? pages.map(page => {
    const timeout = problemPageIsTimeout(page);
    const timing = [
      page.vlm_request_seconds != null ? `VLM ${formatPageSeconds(page.vlm_request_seconds)}` : "",
      page.total_seconds != null ? `总计 ${formatPageSeconds(page.total_seconds)}` : "",
    ].filter(Boolean).join(" · ");
    const reason = [page.error_type, page.error].filter(Boolean).join("：");
    return `<label class="problem-page-row"><input type="checkbox" data-problem-page-key="${esc(page.key)}" ${state.selectedProblemPages.has(page.key) ? "checked" : ""}><span class="problem-page-main"><strong>${esc(page.source_path)} · 第 ${esc(page.page_number)} 页</strong><span class="muted">${badge(page.status)}${timeout ? ' <span class="problem-page-timeout">超时</span>' : ""}${timing ? ` · ${esc(timing)}` : ""}</span>${reason ? `<small>${esc(reason)}</small>` : ""}</span></label>`;
  }).join("") : `<div class="empty-state">该批次没有可截取的异常页。</div>`;
  const submit = document.getElementById("submit-problem-pages-retry");
  submit.disabled = !selected || state.problemPagesSubmitting;
  submit.textContent = selected ? `重试 ${selected} 页` : "没有可重试页面";
}

function updateProblemPagesSelection(predicate) {
  state.selectedProblemPages = new Set(state.problemPages.filter(predicate).map(page => page.key));
  renderProblemPagesSelector();
}

async function openProblemPagesRetry(runId) {
  try {
    const detail = state.activeBatchId === runId && state.activeBatchDetail
      ? state.activeBatchDetail
      : await api(`/api/batch-runs/${encodeURIComponent(runId)}`);
    if (["pending", "running", "paused", "cancelling"].includes(detail.status)) {
      throw new Error("请等待原批次停止后再重试异常页");
    }
    const settings = detail.settings || {};
    const previousTimeout = Number(settings.page_timeout_seconds) || 600;
    const suggestedTimeout = Math.min(7200, Math.max(1200, previousTimeout * 2));
    state.problemPages = extractProblemPages(detail);
    state.selectedProblemPages = new Set(state.problemPages.map(page => page.key));
    problemPagesRetryForm.elements.run_id.value = runId;
    problemPagesRetryForm.elements.page_timeout_seconds.value = String(suggestedTimeout);
    problemPagesRetryForm.elements.task_timeout.value = String(Math.max(
      Number(settings.task_timeout) || 7200,
      Math.ceil(suggestedTimeout) + 60,
    ));
    problemPagesRetryForm.elements.page_connect_max_retries.value = "0";
    problemPagesRetryForm.elements.vlm_batch_size.value = "1";
    document.getElementById("problem-pages-retry-meta").textContent = `${settings.input_path || runId} · 原超时 ${previousTimeout} 秒 · 来源批次 ${runId}`;
    renderProblemPagesSelector();
    if (!problemPagesRetryDialog.open) problemPagesRetryDialog.showModal();
  } catch (error) {
    notice(error.message);
  }
}

problemPagesRetryForm.elements.page_timeout_seconds.addEventListener("change", () => {
  const timeout = Number(problemPagesRetryForm.elements.page_timeout_seconds.value) || 1;
  const taskTimeout = Number(problemPagesRetryForm.elements.task_timeout.value) || 60;
  if (taskTimeout < Math.ceil(timeout) + 60) {
    problemPagesRetryForm.elements.task_timeout.value = String(Math.ceil(timeout) + 60);
  }
});

document.getElementById("problem-pages-list").addEventListener("change", event => {
  const checkbox = event.target.closest("[data-problem-page-key]");
  if (!checkbox) return;
  if (checkbox.checked) state.selectedProblemPages.add(checkbox.dataset.problemPageKey);
  else state.selectedProblemPages.delete(checkbox.dataset.problemPageKey);
  renderProblemPagesSelector();
});
document.getElementById("problem-pages-select-all").addEventListener("click", () => updateProblemPagesSelection(() => true));
document.getElementById("problem-pages-select-none").addEventListener("click", () => updateProblemPagesSelection(() => false));
document.getElementById("problem-pages-select-timeout").addEventListener("click", () => updateProblemPagesSelection(problemPageIsTimeout));
document.getElementById("problem-pages-select-failed").addEventListener("click", () => updateProblemPagesSelection(page => page.status === "failed"));

problemPagesRetryForm.addEventListener("submit", async event => {
  event.preventDefault();
  if (state.problemPagesSubmitting) return;
  const runId = problemPagesRetryForm.elements.run_id.value;
  const selectedPages = state.problemPages.filter(page => state.selectedProblemPages.has(page.key));
  if (!selectedPages.length) {
    notice("请至少选择一个异常页");
    return;
  }
  const submitButton = document.getElementById("submit-problem-pages-retry");
  const payload = {
    page_timeout_seconds: Number(problemPagesRetryForm.elements.page_timeout_seconds.value),
    task_timeout: Number(problemPagesRetryForm.elements.task_timeout.value),
    page_connect_max_retries: Number(problemPagesRetryForm.elements.page_connect_max_retries.value),
    vlm_batch_size: Number(problemPagesRetryForm.elements.vlm_batch_size.value),
    selected_pages: selectedPages.map(page => ({
      source_path: page.source_path,
      page_number: page.page_number,
    })),
  };
  try {
    state.problemPagesSubmitting = true;
    renderProblemPagesSelector();
    submitButton.textContent = `正在截取 ${selectedPages.length} 页`;
    const result = await api(`/api/batch-runs/${encodeURIComponent(runId)}/retry-problem-pages`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    closeProblemPagesRetry();
    notice(`异常页重试批次已开始：${result.run_id}`, false);
    await loadBatches();
  } catch (error) {
    notice(error.message);
  } finally {
    state.problemPagesSubmitting = false;
    renderProblemPagesSelector();
  }
});

document.getElementById("close-problem-pages-retry").addEventListener("click", closeProblemPagesRetry);
document.getElementById("cancel-problem-pages-retry").addEventListener("click", closeProblemPagesRetry);
document.querySelector("[data-active-problem-pages-retry]").addEventListener("click", () => {
  if (state.activeBatchId) openProblemPagesRetry(state.activeBatchId);
});

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
    document.getElementById("batch-detail-meta").textContent = batchDetailMeta(detail);
    document.querySelector("[data-active-problem-pages-retry]").disabled = ["pending", "running", "paused", "cancelling"].includes(detail.status);
    const reportButton = document.querySelector('[data-batch-document="markdown"]');
    reportButton.disabled = !detail.report_ready;
    if (!dialog.open) dialog.showModal();
    await showBatchDocument(detail.report_ready ? "markdown" : "process_markdown");
  } catch (error) { notice(error.message); }
}

function batchDetailMeta(detail) {
  const source = detail.settings?.source_type === "problem_page_retry" && detail.settings?.retry_of_run_id
    ? ` · 异常页来源 ${detail.settings.retry_of_run_id}`
    : "";
  return `${detail.run_id} · ${statusText[detail.status] || detail.status} · ${detail.settings?.pdf_count || 0} 个 PDF${source} · 产物 ${formatBytes(detail.artifacts?.storage_bytes || 0)} · 保留 ${detail.artifacts?.retention_days ?? "-"} 天`;
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
    document.getElementById("batch-detail-meta").textContent = batchDetailMeta(detail);
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

function labRunSettings(run) {
  return run.settings || run.config || {};
}

function labRunMetrics(run) {
  return run.metrics || {};
}

function labMetric(value, digits = 1) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(digits) : "-";
}

function labRunElapsed(run) {
  return formatElapsed(
    run.started_at || run.created_at,
    run.completed_at || (run.status === "running" ? null : run.updated_at || null),
  );
}

function labHardwareLabel(value) {
  return {
    "ascend-npu": "昇腾 NPU",
    "nvidia-t4": "NVIDIA T4",
    other: "其他",
  }[value] || value || "其他";
}

function labRunDatasetHash(run) {
  return run.snapshot?.dataset_sha256 || run.settings?.snapshot?.dataset_sha256 || "";
}

function labIsStable(run) {
  const metrics = labRunMetrics(run);
  return Boolean(
    metrics.complete
    && Number(metrics.total_pages) > 0
    && Number(metrics.successful_pages) > 0
    && Number(metrics.failed_pages || 0) === 0
    && Number(metrics.timeout_pages || 0) === 0
  );
}

function filteredLabRuns() {
  const {query, environment, hardware} = state.labFilters;
  const needle = query.trim().toLowerCase();
  return state.labRuns.filter(run => {
    const settings = labRunSettings(run);
    const searchable = [
      run.run_id,
      run.input_path,
      settings.input_path,
      run.experiment_name,
      run.environment_name,
      run.notes,
      run.engine,
      settings.engine,
    ].filter(Boolean).join(" ").toLowerCase();
    return (!needle || searchable.includes(needle))
      && (!environment || run.environment_name === environment)
      && (!hardware || run.hardware_type === hardware);
  });
}

function populateLabEnvironments() {
  const select = document.getElementById("lab-filter-environment");
  const current = state.labFilters.environment;
  const values = [...new Set(state.labRuns.map(run => run.environment_name).filter(Boolean))].sort((a, b) => a.localeCompare(b, "zh-CN"));
  select.innerHTML = `<option value="">全部环境</option>${values.map(value => `<option value="${esc(value)}">${esc(value)}</option>`).join("")}`;
  select.value = values.includes(current) ? current : "";
  state.labFilters.environment = select.value;
}

function renderLabComparison() {
  const panel = document.getElementById("lab-comparison-panel");
  const comparison = state.labComparison;
  if (!comparison) {
    panel.classList.add("hidden");
    panel.innerHTML = "";
    return;
  }
  const warnings = comparison.warnings || [];
  const rows = comparison.items || [];
  panel.classList.remove("hidden");
  panel.innerHTML = `<div class="section-heading"><h3>实验对比</h3><span class="muted">基线：${esc(comparison.baseline_run_id || "-")}</span></div>${warnings.length ? `<div class="lab-warning-list"><strong>比较提醒</strong>${warnings.map(item => `<span>• ${esc(item)}</span>`).join("")}</div>` : `<p class="lab-comparison-ok">当前所选实验没有发现明显的可比性提醒。</p>`}<div class="lab-comparison-table"><table><thead><tr><th>实验</th><th>环境 / 硬件</th><th>batch</th><th>页数</th><th>成功 / 失败 / 超时</th><th>吞吐</th><th>P50 / P95 / max</th><th>含重试 / 不含重试</th><th>相对基线</th><th>提醒</th></tr></thead><tbody>${rows.map(item => {
    const settings = labRunSettings(item);
    const metrics = labRunMetrics(item);
    const warningText = (item.warnings || []).join("；");
    const baseline = item.run_id === comparison.baseline_run_id;
    return `<tr class="${baseline ? "lab-baseline-row" : ""}"><td><strong>${esc(item.experiment_name || "未命名实验")}</strong><div class="mono muted">${esc(item.run_id || "")}</div></td><td>${esc(item.environment_name || "未标记环境")}<div class="muted">${esc(labHardwareLabel(item.hardware_type))}</div></td><td>${esc(settings.vlm_batch_size ?? "-")}</td><td>${esc(metrics.total_pages ?? "-")}</td><td>${esc(metrics.successful_pages ?? 0)} / ${esc(metrics.failed_pages ?? 0)} / ${esc(metrics.timeout_pages ?? 0)}</td><td>${labMetric(metrics.pages_per_minute)} 页/分</td><td>${labMetric(metrics.p50_page_seconds)} / ${labMetric(metrics.p95_page_seconds)} / ${labMetric(metrics.max_page_seconds)}</td><td>${labMetric(metrics.without_retry_seconds)} / ${labMetric(metrics.with_retry_seconds)}</td><td class="lab-speedup">${baseline ? "1.00×" : item.speedup == null ? "-" : `${labMetric(item.speedup, 2)}×`}<div class="muted">${item.throughput_ratio == null ? "" : `${labMetric(item.throughput_ratio, 2)}× 吞吐`}</div></td><td class="${warningText ? "lab-failure" : "muted"}">${esc(warningText || "-")}</td></tr>`;
  }).join("")}</tbody></table></div>${rows.some(item => (item.metrics?.slowest_pages || []).length) ? `<div class="lab-slowest-grid">${rows.map(item => {
    const slowest = item.metrics?.slowest_pages || [];
    if (!slowest.length) return "";
    return `<div><strong>${esc(item.experiment_name || item.run_id)}</strong><ol>${slowest.slice(0, 3).map(page => `<li>第 ${esc(page.page_number ?? "-")} 页 · ${labMetric(page.seconds)} 秒${page.timeout ? " · 超时" : ""}</li>`).join("")}</ol></div>`;
  }).join("")}</div>` : ""}`;
}

function renderLabRuns() {
  const status = document.getElementById("lab-status");
  const table = document.getElementById("lab-table");
  const summary = document.getElementById("lab-summary");
  const runs = filteredLabRuns();
  const completeRuns = state.labRuns.filter(run => labRunMetrics(run).complete);
  const selectedVisible = runs.filter(run => state.labSelectedRuns.has(run.run_id)).length;
  status.textContent = `${runs.length}/${state.labRuns.length} 条性能实验记录`;
  document.getElementById("lab-selection-count").textContent = `已选择 ${state.labSelectedRuns.size} 条`;
  document.getElementById("lab-compare").disabled = state.labSelectedRuns.size < 2 || state.labComparing;
  summary.innerHTML = `<div class="lab-metric"><span>归档实验</span><strong>${state.labRuns.length}</strong></div><div class="lab-metric"><span>已完成</span><strong>${completeRuns.length}</strong></div><div class="lab-metric"><span>当前筛选</span><strong>${runs.length}</strong></div><div class="lab-metric"><span>已选可见</span><strong>${selectedVisible}</strong></div>`;
  if (!runs.length) {
    table.innerHTML = `<div class="empty-state">没有符合条件的性能实验。可以清除筛选，或先填写服务器目录开始实验。</div>`;
    renderLabComparison();
    return;
  }
  table.innerHTML = `<table><thead><tr><th>选择</th><th>实验 / 环境 / 硬件</th><th>batch</th><th>页数</th><th>成功 / 异常 / 超时</th><th>吞吐</th><th>P50 / P95 / max</th><th>重试开销</th><th>总耗时</th><th>状态</th><th>记录</th></tr></thead><tbody>${runs.map(run => {
    const settings = labRunSettings(run);
    const metrics = labRunMetrics(run);
    const selected = state.labSelectedRuns.has(run.run_id);
    const statusText = metrics.complete ? (labIsStable(run) ? "稳定" : "有异常") : "处理中";
    return `<tr><td><input class="lab-checkbox" type="checkbox" data-lab-select="${esc(run.run_id)}" ${selected ? "checked" : ""} aria-label="选择 ${esc(run.experiment_name || run.run_id)}"></td><td><strong>${esc(run.experiment_name || "未命名实验")}</strong><div>${esc(run.environment_name || "未标记环境")} · ${esc(labHardwareLabel(run.hardware_type))}</div><div class="mono muted">${esc(run.run_id || "")} · ${esc(settings.input_path || run.input_path || "-")}</div></td><td>${esc(settings.vlm_batch_size ?? "-")}</td><td>${esc(metrics.total_pages ?? "-")}</td><td>${esc(metrics.successful_pages ?? 0)} / <span class="${metrics.failed_pages ? "lab-failure" : ""}">${esc(metrics.failed_pages ?? 0)}</span> / <span class="${metrics.timeout_pages ? "lab-failure" : ""}">${esc(metrics.timeout_pages ?? 0)}</span></td><td class="lab-table-number">${labMetric(metrics.pages_per_minute)} 页/分</td><td class="lab-table-number">${labMetric(metrics.p50_page_seconds)} / ${labMetric(metrics.p95_page_seconds)} / ${labMetric(metrics.max_page_seconds)}</td><td>${labMetric(metrics.retry_overhead_seconds)} 秒<div class="muted">${esc(metrics.retry_pages ?? 0)} 页重试</div></td><td>${esc(labRunElapsed(run))}<div class="muted">含重试 ${labMetric(metrics.with_retry_seconds)} 秒</div></td><td>${badge(run.status)}<div class="muted">${statusText}</div>${metrics.pending_pages ? `<div class="muted">待处理 ${esc(metrics.pending_pages)} 页</div>` : ""}</td><td><button class="text-button" data-batch-detail="${esc(run.run_id)}">查看记录</button></td></tr>`;
  }).join("")}</tbody></table>`;
  renderLabComparison();
}

function clearLabSelection() {
  state.labSelectedRuns.clear();
  state.labComparison = null;
  renderLabRuns();
}

async function compareLabRuns() {
  if (state.labSelectedRuns.size < 2) {
    notice("请至少选择 2 条性能实验");
    return;
  }
  state.labComparing = true;
  renderLabRuns();
  try {
    state.labComparison = await api("/api/performance-experiments/compare", {
      method: "POST",
      body: JSON.stringify({run_ids: [...state.labSelectedRuns]}),
    });
    renderLabRuns();
  } catch (error) {
    notice(error.message);
  } finally {
    state.labComparing = false;
    renderLabRuns();
  }
}

async function exportLabRuns(format) {
  if (state.labSelectedRuns.size < 2) {
    notice("请至少选择 2 条性能实验后导出");
    return;
  }
  try {
    const query = encodeURIComponent([...state.labSelectedRuns].join(","));
    const response = await authorizedFetch(`/api/performance-experiments/export?run_ids=${query}&format=${encodeURIComponent(format)}`);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `mineru-performance-experiments.${format === "markdown" ? "md" : format}`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    notice(`已导出 ${format.toUpperCase()}`, false);
  } catch (error) {
    notice(error.message);
  }
}

async function loadLab() {
  const data = await api("/api/performance-experiments");
  state.labRuns = data.items || [];
  const available = new Set(state.labRuns.map(run => run.run_id));
  state.labSelectedRuns = new Set([...state.labSelectedRuns].filter(runId => available.has(runId)));
  populateLabEnvironments();
  renderLabRuns();
}

async function startLabExperiment(event) {
  event.preventDefault();
  if (state.labSubmitting) return;
  const form = new FormData(event.currentTarget);
  const inputPath = String(form.get("input_path") || "").trim();
  if (!inputPath) {
    notice("请填写服务器测试目录");
    return;
  }
  const submitButton = document.getElementById("lab-submit");
  const payload = {
    input_path: inputPath,
    experiment_name: String(form.get("experiment_name") || "").trim() || null,
    environment_name: String(form.get("environment_name") || "").trim() || null,
    hardware_type: form.get("hardware_type"),
    engine: form.get("engine"),
    notes: String(form.get("notes") || "").trim() || null,
    backend: form.get("backend"),
    lang: String(form.get("lang") || "ch").trim() || "ch",
    task_timeout: Number(form.get("task_timeout")),
    page_timeout_seconds: Number(form.get("page_timeout_seconds")),
    page_connect_max_retries: Number(form.get("page_connect_max_retries")),
    vlm_batch_size: Number(form.get("vlm_batch_size")),
    experiment_type: "performance_lab",
    server_url: String(form.get("server_url") || "").trim() || null,
    recursive: form.get("recursive") === "on",
    effort: "medium",
    parse_method: "auto",
    pause_seconds: 2,
  };
  state.labSubmitting = true;
  submitButton.disabled = true;
  submitButton.textContent = "正在启动实验";
  try {
    await api("/api/batch-runs", {method: "POST", body: JSON.stringify(payload)});
    notice("性能实验已开始", false);
    await loadLab();
  } catch (error) {
    notice(error.message);
  } finally {
    state.labSubmitting = false;
    submitButton.disabled = false;
    submitButton.textContent = "开始性能实验";
  }
}

document.getElementById("lab-filter-query").addEventListener("input", event => {
  state.labFilters.query = event.target.value;
  renderLabRuns();
});
document.getElementById("lab-filter-environment").addEventListener("change", event => {
  state.labFilters.environment = event.target.value;
  renderLabRuns();
});
document.getElementById("lab-filter-hardware").addEventListener("change", event => {
  state.labFilters.hardware = event.target.value;
  renderLabRuns();
});
document.getElementById("lab-compare").addEventListener("click", compareLabRuns);
document.getElementById("lab-clear-selection").addEventListener("click", clearLabSelection);
document.querySelectorAll("[data-lab-export]").forEach(button => button.addEventListener("click", () => exportLabRuns(button.dataset.labExport)));
document.getElementById("lab-table").addEventListener("change", event => {
  const checkbox = event.target.closest("[data-lab-select]");
  if (!checkbox) return;
  if (checkbox.checked) state.labSelectedRuns.add(checkbox.dataset.labSelect);
  else state.labSelectedRuns.delete(checkbox.dataset.labSelect);
  state.labComparison = null;
  renderLabRuns();
});
document.getElementById("lab-form").addEventListener("submit", startLabExperiment);
document.getElementById("lab-table").addEventListener("click", handleBatchTableClick);

function configItemValue(item) {
  if (item.sensitive || item.type === "secret") return "";
  return item.value ?? item.display_value ?? "";
}

function configControl(item) {
  const key = esc(item.key);
  const current = configItemValue(item);
  const draftExists = Object.prototype.hasOwnProperty.call(state.configDraft, item.key);
  const candidate = draftExists ? state.configDraft[item.key] : current;
  const error = state.configValidationErrors[item.key];
  const readonly = item.editable === false;
  const displayValue = item.display_value ?? current ?? "-";
  let control = `<span class="config-value" title="${esc(displayValue)}">${esc(displayValue)}</span>`;
  if (!readonly) {
    if (item.type === "enum" && Array.isArray(item.choices)) {
      control = `<select data-config-key="${key}"><option value="">请选择候选值</option>${item.choices.map(choice => `<option value="${esc(choice)}"${candidate === choice ? " selected" : ""}>${esc(choice)}</option>`).join("")}</select>`;
    } else {
      const type = item.type === "integer" ? "number" : item.type === "secret" ? "password" : "text";
      const bounds = item.type === "integer" ? ` min="${esc(item.minimum ?? "")}" max="${esc(item.maximum ?? "")}" step="1"` : "";
      const placeholder = item.type === "secret" ? "留空表示保留当前敏感值" : current;
      control = `<input type="${type}" data-config-key="${key}" value="${esc(candidate || "")}" placeholder="${esc(placeholder || "")}"${bounds}>`;
    }
  }
  return `<div class="config-item${error ? " has-error" : ""}"><div class="config-key">${key}<span>${readonly ? "只读" : "可编辑"}</span></div><div class="config-control">${control}</div><p class="config-description">${esc(item.description || "")}</p>${error ? `<p class="config-error">${esc(error)}</p>` : ""}</div>`;
}

function renderConfig() {
  const config = state.config || {};
  const items = config.items || [];
  const modeText = {safe_apply: "安全应用", read_validate_only: "只读与校验"};
  document.getElementById("config-mode").textContent = modeText[config.mode] || config.mode || "-";
  document.getElementById("config-agent-status").innerHTML = config.ok === false ? badge("unavailable") : badge("healthy");
  document.getElementById("config-env-file").textContent = config.env_file || "-";
  document.getElementById("config-modified-at").textContent = formatDate(config.modified_at);
  const versionElement = document.getElementById("config-version");
  const shaElement = document.getElementById("config-sha256");
  const hashElement = document.getElementById("config-hash");
  const overallElement = document.getElementById("config-application-overall");
  if (versionElement) versionElement.textContent = config.version || "-";
  if (shaElement) {
    shaElement.textContent = config.sha256 ? config.sha256.slice(0, 12) : "-";
    shaElement.title = config.sha256 || "";
  }
  if (hashElement) {
    hashElement.textContent = config.config_hash ? config.config_hash.slice(0, 12) : "-";
    hashElement.title = config.config_hash || "";
  }
  if (overallElement) overallElement.innerHTML = badge(state.configStatus?.overall || "unknown");
  const groups = new Map();
  for (const item of items) groups.set(item.category || "其他配置", [...(groups.get(item.category || "其他配置") || []), item]);
  document.getElementById("config-groups").innerHTML = [...groups.entries()].map(([category, group]) => `<section class="panel config-group"><div class="section-heading"><h2>${esc(category)}</h2><span>${group.length} 项</span></div>${group.map(item => configControl(item)).join("")}</section>`).join("") || `<div class="empty-state">没有可显示的配置。</div>`;
}

function configStatusValue(value) {
  if (value == null || value === "") return "-";
  return String(value);
}

function configMismatchHtml(item) {
  const mismatch = typeof item === "string" ? {key: item, sensitive: false} : (item || {});
  const detail = mismatch.missing
    ? "：容器未加载该变量"
    : mismatch.sensitive
      ? "：敏感值不一致"
      : "：当前容器值与 env.multi 不一致";
  return `<li class="config-mismatch-item"><strong>${esc(mismatch.key || "未知配置")}</strong>${detail}</li>`;
}

const CONFIG_SOURCE_LABELS = {
  command: "命令行参数",
  environment: "容器环境变量",
  default: "已知默认值",
  unknown: "未知",
};

function renderLegacyConfigStatus(payload) {
  const env = payload.env_file || {};
  const summary = payload.summary || {};
  const services = payload.services && typeof payload.services === "object" ? payload.services : {};
  const counts = [
    ["已生效", summary.applied || 0, "good"],
    ["等待重启", summary.pending_restart || 0, "warn"],
    ["未知", summary.unknown || 0, "bad"],
    ["容器未创建", summary.not_created || 0, "warn"],
  ];
  const countHtml = counts.map(([label, value, cls]) => `<div class="config-status-count"><span>${label}</span><strong class="${cls}">${esc(value)}</strong></div>`).join("");
  const serviceEntries = Object.entries(services);
  const serviceHtml = serviceEntries.length ? serviceEntries.map(([name, service]) => {
    const mismatches = Array.isArray(service.mismatched_keys) ? service.mismatched_keys : [];
    const missing = Array.isArray(service.missing_keys) ? service.missing_keys : [];
    const warnings = Array.isArray(service.warnings) ? service.warnings : [];
    return `<article class="config-service-status"><div class="section-heading"><div><h3>${esc(name)}</h3><p class="muted mono">${esc(service.container || "容器未创建")}</p></div>${badge(service.status || "unknown")}</div><dl><div><dt>容器状态</dt><dd>${esc(configStatusValue(service.state))}</dd></div><div><dt>健康状态</dt><dd>${esc(configStatusValue(service.health))}</dd></div><div><dt>比对变量</dt><dd>${esc(service.compared_keys?.length || 0)} 项（匹配 ${esc(service.matching_keys?.length || 0)}）</dd></div></dl>${mismatches.length || missing.length ? `<div class="config-mismatch-list"><strong>不一致项</strong><ul>${mismatches.map(configMismatchHtml).join("")}${missing.map(key => configMismatchHtml({key, sensitive: false, missing: true})).join("")}</ul></div>` : ""}${warnings.length ? `<div class="config-diagnostic-warning"><strong>提示</strong><ul>${warnings.map(item => `<li>${esc(item)}</li>`).join("")}</ul></div>` : ""}</article>`;
  }).join("") : `<div class="empty-state">没有可检查的服务。</div>`;
  return `<div class="config-status-meta"><span>兼容模式：旧版配置状态接口</span><span>检查时间：${esc(formatDate(payload.generated_at))}</span><span>Env 版本：${esc(env.version || "-")}</span></div><div class="config-status-counts">${countHtml}</div><div class="config-status-grid">${serviceHtml}</div>`;
}

function renderConfigStatus(payload = state.configStatus) {
  const target = document.getElementById("config-application-status");
  if (!target) return;
  if (!payload) {
    target.innerHTML = `<div class="empty-state">暂无最终有效配置</div>`;
    return;
  }
  if (payload.load_error || payload.ok === false) {
    target.innerHTML = `<div class="empty-state bad-text">最终有效配置读取失败：${esc(payload.load_error || payload.error || "未知错误")}</div>`;
    return;
  }
  if (!Array.isArray(payload.items) && payload.services) {
    target.innerHTML = renderLegacyConfigStatus(payload);
    return;
  }
  const env = payload.env_file || {};
  const summary = payload.summary || {};
  const counts = [
    ["已生效", summary.applied || 0, "good"],
    ["等待重启", summary.pending_restart || 0, "warn"],
    ["配置冲突", summary.conflict || 0, "bad"],
    ["实例不一致", summary.inconsistent || 0, "warn"],
    ["未知", summary.unknown || 0, "bad"],
    ["容器未创建", summary.not_created || 0, "warn"],
  ];
  const countHtml = counts.map(([label, value, cls]) => `<div class="config-status-count"><span>${label}</span><strong class="${cls}">${esc(value)}</strong></div>`).join("");
  const itemHtml = (payload.items || []).map(item => {
    const services = Object.entries(item.services || {});
    const serviceHtml = services.map(([name, detail]) => {
      const source = CONFIG_SOURCE_LABELS[detail.effective_source] || detail.effective_source_label || "未知";
      const warnings = Array.isArray(detail.warnings) ? detail.warnings : [];
      return `<article class="config-effective-service"><div class="section-heading"><div><h4>${esc(name)}</h4><p class="muted mono">${esc(detail.container || "容器未创建")}</p></div>${badge(detail.status || "unknown")}</div><dl><div><dt>最终有效值</dt><dd class="mono">${esc(configStatusValue(detail.effective_value))}</dd></div><div><dt>值来源</dt><dd><span class="config-source">${esc(source)}</span>${detail.inferred ? " · 推断值" : ""}</dd></div><div><dt>容器状态</dt><dd>${esc(configStatusValue(detail.state))}</dd></div><div><dt>健康状态</dt><dd>${esc(configStatusValue(detail.health))}</dd></div></dl>${warnings.length ? `<div class="config-diagnostic-warning"><strong>提示</strong><ul>${warnings.map(warning => `<li>${esc(warning)}</li>`).join("")}</ul></div>` : ""}</article>`;
    }).join("");
    return `<article class="config-effective-item"><div class="section-heading"><div><h3 class="mono">${esc(item.key)}</h3><p class="muted">${esc(item.description || item.label || "")}</p></div></div><div class="config-effective-configured"><span>env.multi 配置值</span><code>${esc(configStatusValue(item.configured_value))}</code></div><div class="config-effective-services">${serviceHtml || `<div class="empty-state">没有适用的运行服务。</div>`}</div></article>`;
  }).join("");
  target.innerHTML = `<div class="config-status-meta"><span>检查时间：${esc(formatDate(payload.generated_at))}</span><span>总体状态：${badge(payload.overall || "unknown")}</span><span>Env 版本：${esc(env.version || "-")}</span><span title="${esc(env.sha256 || "")}">SHA256：${esc(env.sha256 ? env.sha256.slice(0, 12) : "-")}</span><span>说明：最终值为 Docker inspect、容器环境变量和已知默认值的推断结果</span></div><div class="config-status-counts">${countHtml}</div><div class="config-effective-list">${itemHtml || `<div class="empty-state">没有可展示的最终配置。</div>`}</div>${Array.isArray(payload.warnings) && payload.warnings.length ? `<div class="config-diagnostic-warning"><strong>整体提示</strong><ul>${payload.warnings.map(item => `<li>${esc(item)}</li>`).join("")}</ul></div>` : ""}`;
}

async function loadConfigStatus({force = false} = {}) {
  if (state.configStatusLoading) return;
  if (!force && state.configStatus !== null) {
    renderConfigStatus();
    renderConfig();
    return;
  }
  const target = document.getElementById("config-application-status");
  const button = document.getElementById("config-status-refresh");
  state.configStatusLoading = true;
  if (button) button.disabled = true;
  if (target) target.innerHTML = `<div class="empty-state">正在读取最终有效配置...</div>`;
  try {
    try {
      state.configStatus = await api("/api/config/effective");
    } catch (effectiveError) {
      try {
        state.configStatus = await api("/api/config/status");
      } catch (legacyError) {
        throw new Error(`${effectiveError.message}；兼容接口同样失败：${legacyError.message}`);
      }
    }
    renderConfigStatus();
    renderConfig();
  } catch (error) {
    state.configStatus = {load_error: error.message};
    renderConfigStatus();
    renderConfig();
  } finally {
    state.configStatusLoading = false;
    if (button) button.disabled = false;
  }
}

function diagnosticJson(value, emptyText) {
  if (value == null || (Array.isArray(value) && value.length === 0) || (typeof value === "object" && !Array.isArray(value) && Object.keys(value).length === 0)) {
    return `<span class="muted">${esc(emptyText)}</span>`;
  }
  return `<pre>${esc(JSON.stringify(value, null, 2))}</pre>`;
}

function renderDeepDiagnostics(payload = state.deepDiagnostics) {
  const target = document.getElementById("deep-diagnostics");
  if (!target) return;
  if (!payload) {
    target.innerHTML = `<div class="empty-state">暂无深度诊断信息</div>`;
    return;
  }
  if (payload.load_error || payload.ok === false) {
    target.innerHTML = `<div class="empty-state bad-text">深度诊断读取失败：${esc(payload.load_error || payload.error || "未知错误")}</div>`;
    return;
  }
  const services = payload.services && typeof payload.services === "object" ? payload.services : {};
  const cards = Object.entries(services).map(([name, service]) => {
    const models = Array.isArray(service.models) ? service.models : [];
    const mounts = Array.isArray(service.mounts) ? service.mounts : [];
    const warnings = Array.isArray(service.warnings) ? service.warnings : [];
    const modelText = models.length ? diagnosticJson(models, "未发现可检查的绝对模型路径") : `<p class="muted">未发现可检查的绝对模型路径</p>`;
    return `<article class="deep-service-card"><div class="section-heading"><div><h3>${esc(name)}</h3><p class="muted mono">${esc(service.container || "容器未创建")}</p></div>${badge(service.status || "unknown")}</div><dl><div><dt>镜像</dt><dd class="mono">${esc(service.image || "-")}</dd></div><div><dt>运行状态</dt><dd>${esc(service.state || "-")} / ${esc(service.health || "-")}</dd></div><div><dt>配置应用</dt><dd>${service.status === "applied" ? "无需重启" : service.status === "pending_restart" ? "需要重启" : "暂无法确认"}</dd></div></dl><details open><summary>实际环境变量</summary>${diagnosticJson(service.environment, "未读取到可展示的 MinerU 环境变量")}</details><details><summary>模型路径检查</summary>${modelText}</details><details><summary>容器挂载</summary>${diagnosticJson(mounts, "未发现容器挂载")}</details><details><summary>设备检测</summary>${diagnosticJson(service.devices, "未执行设备检测")}</details>${warnings.length ? `<div class="deep-diagnostic-warning"><strong>诊断提示</strong><ul>${warnings.map(item => `<li>${esc(item)}</li>`).join("")}</ul></div>` : ""}</article>`;
  }).join("");
  target.innerHTML = `<div class="config-status-meta"><span>检查时间：${esc(formatDate(payload.generated_at))}</span><span>Env 文件：${esc(payload.env_file?.path || payload.env_file?.name || "-")}</span><span>总体状态：${badge(payload.overall || "unknown")}</span></div><div class="deep-service-grid">${cards || `<div class="empty-state">没有可诊断的服务。</div>`}</div>${Array.isArray(payload.warnings) && payload.warnings.length ? `<div class="deep-diagnostic-warning"><strong>整体提示</strong><ul>${payload.warnings.map(item => `<li>${esc(item)}</li>`).join("")}</ul></div>` : ""}`;
}

async function loadDeepDiagnostics({force = false} = {}) {
  if (state.deepDiagnosticsLoading) return;
  if (!force && state.deepDiagnostics !== null) {
    renderDeepDiagnostics();
    return;
  }
  const target = document.getElementById("deep-diagnostics");
  const button = document.getElementById("deep-diagnostics-refresh");
  state.deepDiagnosticsLoading = true;
  if (button) button.disabled = true;
  if (target) target.innerHTML = `<div class="empty-state">正在读取跨容器深度诊断...</div>`;
  try {
    state.deepDiagnostics = await api("/api/diagnostics/deep");
    renderDeepDiagnostics();
  } catch (error) {
    state.deepDiagnostics = {load_error: error.message};
    renderDeepDiagnostics();
  } finally {
    state.deepDiagnosticsLoading = false;
    if (button) button.disabled = false;
  }
}

function configChangeHtml(change) {
  if (change.sensitive) {
    return `<div class="config-diff-item"><strong>${esc(change.key)}</strong><div class="config-diff-values"><span class="muted">当前敏感值</span><span class="config-diff-arrow">→</span><span>敏感值已修改</span></div></div>`;
  }
  return `<div class="config-diff-item"><strong>${esc(change.key)}</strong><div class="config-diff-values"><code>${esc(change.old_value ?? "(未设置)")}</code><span class="config-diff-arrow">→</span><code>${esc(change.new_value ?? "(未设置)")}</code></div></div>`;
}

function renderConfigPlan() {
  const panel = document.getElementById("config-plan-panel");
  const plan = state.configPlan;
  if (!plan) {
    panel.innerHTML = "";
    panel.classList.add("hidden");
    return;
  }
  const valid = plan.ok && plan.valid !== false && plan.compose_valid !== false;
  const services = plan.affected_services || [];
  const errors = plan.errors || {};
  panel.classList.remove("hidden");
  panel.innerHTML = `<div class="config-plan-header"><div><span class="config-plan-status ${valid ? "good" : "bad"}">${valid ? "预览通过" : "预览未通过"}</span><h3>${esc(plan.message || plan.error || (valid ? "候选配置可以安全应用。" : "候选配置不可应用。"))}</h3></div><span>${(plan.changes || []).length} 项变更</span></div>
    ${Object.keys(errors).length ? `<div class="config-plan-errors">${Object.entries(errors).map(([key, value]) => `<p><strong>${esc(key)}</strong>：${esc(value)}</p>`).join("")}</div>` : ""}
    ${plan.no_changes ? `<div class="empty-state">没有实际变更，不会写入 env，也不会重建服务。</div>` : `<div class="config-diff">${(plan.changes || []).map(configChangeHtml).join("")}</div>`}
    <div class="config-dialog-meta"><div><span>Compose 校验</span><strong>${plan.compose_valid === false ? "失败" : "通过"}</strong></div><div><span>受影响服务</span><strong>${esc(services.join("、") || "无")}</strong></div><div><span>失败保护</span><strong>自动回滚</strong></div></div>
    ${plan.requires_ops_restart ? `<p class="config-plan-warning">涉及 mineru-ops 自身配置：业务服务应用后，请现场手工重启 mineru-ops。</p>` : ""}`;
}

function configApplyStepLabel(step) {
  return step?.label || step?.name || step?.key || "未命名步骤";
}

function configApplyStatusMeta(status) {
  const values = {
    completed: {label: "已完成", cls: "good"},
    failed: {label: "失败", cls: "bad"},
    skipped: {label: "已跳过", cls: "warn"},
    pending: {label: "等待执行", cls: "unknown"},
  };
  return values[status] || {label: status || "未知", cls: "unknown"};
}

function manualActionParts(action) {
  if (typeof action === "string") {
    return {label: "现场手工操作", message: "", command: action};
  }
  const value = action && typeof action === "object" ? action : {};
  return {
    label: value.label || "现场手工操作",
    message: value.message || "",
    command: value.command || value.cmd || "",
  };
}

function renderConfigApplyResult(result = state.configApplyResult) {
  const target = document.getElementById("config-apply-result");
  if (!target) return;
  if (!result) {
    target.innerHTML = "";
    target.classList.add("hidden");
    return;
  }

  const rollbackStatus = result.rollback_status;
  const isRestore = Object.prototype.hasOwnProperty.call(result, "restored");
  const actionLabel = isRestore ? "配置回滚" : "配置应用";
  let title = `${actionLabel}执行完成`;
  if (!result.ok && rollbackStatus === "completed") title = `${actionLabel}失败，已自动回滚`;
  else if (!result.ok && rollbackStatus === "partial") title = `${actionLabel}失败，自动回滚不完整`;
  else if (!result.ok) title = `${actionLabel}失败`;

  const steps = Array.isArray(result.steps) ? result.steps : [];
  const stepHtml = steps.map(step => {
    const meta = configApplyStatusMeta(step?.status);
    return `<div class="config-apply-step ${esc(meta.cls)}"><div><strong>${esc(configApplyStepLabel(step))}</strong>${step?.detail ? `<div class="config-apply-step-detail">${esc(step.detail)}</div>` : ""}</div><span class="diagnostic-state ${esc(meta.cls)}">${esc(meta.label)}</span></div>`;
  }).join("");
  const originalError = result.original_error || result.error || result.message || "";
  const rollbackLabel = {
    completed: "已完成",
    partial: "部分完成",
    failed: "失败",
  }[rollbackStatus] || (rollbackStatus || "未触发");
  const booleanLabel = value => value === true ? "是" : value === false ? "否" : "-";
  const showRollback = Boolean(rollbackStatus || result.rolled_back || result.rollback_error)
    || Object.prototype.hasOwnProperty.call(result, "env_restored")
    || Object.prototype.hasOwnProperty.call(result, "services_restored");
  const rollbackHtml = showRollback ? `<div class="config-rollback-summary"><div><span>自动回滚状态</span><strong>${esc(rollbackLabel)}</strong></div><div><span>env 已恢复</span><strong>${esc(booleanLabel(result.env_restored))}</strong></div><div><span>服务已恢复</span><strong>${esc(booleanLabel(result.services_restored))}</strong></div></div>` : "";
  const actions = Array.isArray(result.manual_actions) ? result.manual_actions : [];
  const actionsHtml = actions.map((action, index) => {
    const parts = manualActionParts(action);
    return `<div class="manual-action"><div class="manual-action-head"><div><strong>${esc(parts.label)}</strong>${parts.message ? `<div class="config-apply-step-detail">${esc(parts.message)}</div>` : ""}</div>${parts.command ? `<button type="button" class="secondary-button" data-copy-command-index="${index}">复制命令</button>` : ""}</div>${parts.command ? `<pre class="manual-action-command">${esc(parts.command)}</pre>` : ""}</div>`;
  }).join("");

  target.classList.remove("hidden");
  target.innerHTML = `<div class="config-apply-result-header"><div><h2>${esc(title)}</h2><p class="muted">${result.ok ? "执行结果已保留，可继续核对最终有效配置。" : "请核对失败步骤、回滚状态和现场手工操作。"}</p></div>${badge(result.ok ? "completed" : "failed")}</div>${steps.length ? `<div class="config-apply-steps">${stepHtml}</div>` : ""}${originalError ? `<div class="config-diagnostic-warning"><strong>原始错误</strong><div>${esc(originalError)}</div></div>` : ""}${result.rollback_error ? `<div class="config-diagnostic-warning"><strong>回滚错误</strong><div>${esc(result.rollback_error)}</div></div>` : ""}${rollbackHtml}${actionsHtml}`;
}

function renderConfigHistory() {
  const target = document.getElementById("config-history");
  if (!state.configHistory.length) {
    target.innerHTML = `<div class="empty-state">暂无备份记录</div>`;
    return;
  }
  target.innerHTML = state.configHistory.map(item => `<div class="config-history-item"><strong>${esc(item.name)}</strong><span>${esc(formatDate(item.created_at))}</span><small>${esc(item.size_bytes || 0)} bytes · 可恢复</small><button type="button" class="secondary-button" data-config-restore="${esc(item.name)}">回滚到此版本</button></div>`).join("");
}

function setConfigBusy(busy, label = "") {
  state.configApplying = busy;
  for (const id of ["config-reload", "config-validate", "config-plan", "config-apply", "config-confirm-apply"]) {
    const button = document.getElementById(id);
    if (button) button.disabled = busy;
  }
  document.getElementById("config-groups").classList.toggle("loading", busy);
  if (label) document.getElementById("config-validation-status").textContent = label;
}

async function loadConfig() {
  state.configDraft = {};
  state.configValidationErrors = {};
  state.configPlan = null;
  state.configApplying = false;
  const [schema, config, history] = await Promise.all([
    api("/api/config/schema"),
    api("/api/config"),
    api("/api/config/history"),
  ]);
  state.configSchema = schema;
  state.config = config;
  state.configHistory = history.items || [];
  renderConfig();
  renderConfigHistory();
  renderConfigPlan();
  renderConfigApplyResult();
  setConfigBusy(false);
  void loadConfigStatus({force: true});
  const status = document.getElementById("config-validation-status");
  status.textContent = "修改输入框后先预览变更；确认后才会写入并重建受影响服务。";
  status.classList.remove("bad-text");
  void loadAuditLogs();
}

function auditDetailText(detail) {
  if (detail == null || detail === "") return "-";
  if (typeof detail === "object") return JSON.stringify(detail, null, 2);
  try {
    const parsed = JSON.parse(detail);
    return parsed && typeof parsed === "object"
      ? JSON.stringify(parsed, null, 2)
      : String(detail);
  } catch {
    return String(detail);
  }
}

function renderAuditLogs(payload) {
  const target = document.getElementById("audit-log");
  if (payload?.load_error) {
    target.innerHTML = `<div class="empty-state bad-text">审计记录读取失败：${esc(payload.load_error)}</div>`;
    return;
  }
  const items = Array.isArray(payload?.items) ? payload.items : [];
  const total = Number(payload?.total || 0);
  if (!items.length) {
    target.innerHTML = `<div class="empty-state">暂无操作审计记录</div>`;
    return;
  }
  const rows = items.map(item => {
    const success = Boolean(item.success);
    const result = success
      ? `<span class="diagnostic-state good">成功</span>`
      : `<span class="diagnostic-state bad">失败</span>`;
    return `<tr><td>${esc(formatDate(item.created_at))}</td><td>${esc(item.action || "-")}</td><td>${esc(item.target || "-")}</td><td>${result}</td><td><pre class="audit-detail">${esc(auditDetailText(item.detail))}</pre></td></tr>`;
  }).join("");
  const hasMore = items.length < total;
  target.innerHTML = `<div class="audit-table-wrap"><table class="audit-table"><thead><tr><th>时间</th><th>操作</th><th>对象</th><th>结果</th><th>详情</th></tr></thead><tbody>${rows}</tbody></table></div>${hasMore ? `<button type="button" class="secondary-button audit-load-more" data-audit-load-more>加载更多（${esc(items.length)}/${esc(total)}）</button>` : `<p class="muted audit-load-more">已显示全部 ${esc(total)} 条记录</p>`}`;
}

async function loadAuditLogs({append = false} = {}) {
  if (state.auditLoading) return;
  const target = document.getElementById("audit-log");
  const button = document.getElementById("audit-refresh");
  const existingItems = append && Array.isArray(state.auditLogs?.items)
    ? state.auditLogs.items
    : [];
  const offset = append ? existingItems.length : 0;
  state.auditLoading = true;
  button.disabled = true;
  if (!append) target.innerHTML = `<div class="empty-state">正在读取操作审计...</div>`;
  try {
    const payload = await api(`/api/audit?limit=${AUDIT_PAGE_SIZE}&offset=${offset}`);
    const nextItems = Array.isArray(payload.items) ? payload.items : [];
    state.auditLogs = {
      ...payload,
      offset: 0,
      items: append ? [...existingItems, ...nextItems] : nextItems,
    };
    state.auditOffset = state.auditLogs.items.length;
    renderAuditLogs(state.auditLogs);
  } catch (error) {
    if (append && existingItems.length) {
      state.auditLogs = {...state.auditLogs, load_more_error: error.message};
      renderAuditLogs(state.auditLogs);
      const message = document.createElement("p");
      message.className = "runtime-diagnostic-error";
      message.textContent = `加载更多失败：${error.message}`;
      target.appendChild(message);
    } else {
      state.auditLogs = {items: [], total: 0, load_error: error.message};
      renderAuditLogs(state.auditLogs);
    }
  } finally {
    state.auditLoading = false;
    button.disabled = false;
  }
}

async function validateConfig() {
  if (!Object.keys(state.configDraft).length) {
    notice("请先修改至少一个候选值");
    return;
  }
  const status = document.getElementById("config-validation-status");
  status.textContent = "正在校验候选配置...";
  try {
    const result = await api("/api/config/validate", {method: "POST", body: JSON.stringify({values: state.configDraft})});
    state.configValidationErrors = result.errors || {};
    renderConfig();
    status.textContent = result.valid ? "候选值校验通过，可继续预览 Compose 变更。" : (result.message || "候选配置存在错误。");
    status.classList.toggle("bad-text", !result.valid);
  } catch (error) {
    status.textContent = `校验失败：${error.message}`;
    status.classList.add("bad-text");
  }
}

async function planConfig() {
  if (!Object.keys(state.configDraft).length) {
    notice("请先修改至少一个候选值");
    return null;
  }
  const status = document.getElementById("config-validation-status");
  setConfigBusy(true, "正在生成变更预览并校验 Compose...");
  try {
    const result = await api("/api/config/plan", {method: "POST", body: JSON.stringify({values: state.configDraft})});
    state.configPlan = result;
    state.configValidationErrors = result.errors || {};
    renderConfig();
    renderConfigPlan();
    const valid = result.ok && result.valid !== false && result.compose_valid !== false;
    status.textContent = result.message || result.error || (valid ? "预览通过。" : "预览失败。");
    status.classList.toggle("bad-text", !valid);
    return result;
  } catch (error) {
    state.configPlan = {ok: false, error: error.message, changes: [], affected_services: []};
    renderConfigPlan();
    status.textContent = `预览失败：${error.message}`;
    status.classList.add("bad-text");
    return null;
  } finally {
    setConfigBusy(false);
  }
}

function configConfirmationHtml(plan) {
  const changes = plan.changes || [];
  return `<div class="config-confirm-intro">即将写入 <strong>${changes.length}</strong> 项配置，并选择性重建受影响服务。</div>
    <div class="config-diff">${changes.map(configChangeHtml).join("")}</div>
    <div class="config-dialog-meta"><div><span>Compose 校验</span><strong>${plan.compose_valid ? "已通过" : "未通过"}</strong></div><div><span>重建服务</span><strong>${esc((plan.affected_services || []).join("、") || "无")}</strong></div><div><span>失败保护</span><strong>恢复 env + 尝试恢复服务</strong></div></div>
    ${plan.requires_ops_restart ? `<p class="config-plan-warning">应用完成后还需现场手工重启 mineru-ops，避免当前请求把自己中断。</p>` : ""}`;
}

async function openConfigApplyDialog() {
  if (state.configApplying) return;
  let plan = state.configPlan;
  if (!plan) plan = await planConfig();
  if (!plan || !plan.ok || plan.valid === false || plan.compose_valid === false) {
    notice(plan?.error || plan?.message || "请先修正配置并通过预览");
    return;
  }
  if (plan.no_changes) {
    notice("候选值与当前配置一致，无需应用。", false);
    return;
  }
  document.getElementById("config-apply-summary").innerHTML = configConfirmationHtml(plan);
  document.getElementById("config-apply-dialog").showModal();
}

async function applyConfig() {
  if (state.configApplying) return;
  const dialog = document.getElementById("config-apply-dialog");
  setConfigBusy(true, "正在写入配置并重建受影响服务，请勿关闭页面...");
  try {
    const result = await api("/api/config/apply", {method: "POST", body: JSON.stringify({values: state.configDraft})});
    state.configApplyResult = result;
    renderConfigApplyResult(result);
    if (!result.ok) {
      dialog.close();
      const message = result.rollback_status === "completed"
        ? "应用失败，已自动回滚"
        : result.rollback_status === "partial"
          ? "应用失败，自动回滚不完整"
          : "应用失败，请查看执行详情";
      document.getElementById("config-validation-status").textContent = message;
      document.getElementById("config-validation-status").classList.add("bad-text");
      notice(`${message}：${result.error || result.original_error || "未知错误"}`);
      void loadConfigStatus({force: true});
      return;
    }
    dialog.close();
    const restartHint = result.requires_ops_restart ? "；请现场手工重启 mineru-ops" : "";
    notice(`配置已安全应用${restartHint}`, false);
    await loadConfig();
  } catch (error) {
    state.configApplyResult = {
      ok: false,
      original_error: error.message,
      error: error.message,
      steps: [],
      manual_actions: [],
    };
    renderConfigApplyResult();
    document.getElementById("config-validation-status").textContent = error.message;
    document.getElementById("config-validation-status").classList.add("bad-text");
    notice(error.message);
  } finally {
    setConfigBusy(false);
  }
}

async function restoreConfig(name) {
  if (state.configApplying) return;
  if (!window.confirm(`确认回滚到 ${name}？系统会校验备份并重建受影响服务。`)) return;
  setConfigBusy(true, `正在回滚到 ${name}...`);
  try {
    const result = await api("/api/config/restore", {method: "POST", body: JSON.stringify({name})});
    state.configApplyResult = {...result, restored: result.ok};
    renderConfigApplyResult(state.configApplyResult);
    if (!result.ok) {
      const message = result.rollback_status === "completed"
        ? "回滚失败，已恢复回滚前配置"
        : result.rollback_status === "partial"
          ? "回滚失败，恢复过程不完整"
          : "回滚失败，请查看执行详情";
      document.getElementById("config-validation-status").textContent = message;
      document.getElementById("config-validation-status").classList.add("bad-text");
      notice(`${message}：${result.error || result.original_error || "未知错误"}`);
      void loadConfigStatus({force: true});
      return;
    }
    const restartHint = result.requires_ops_restart ? "；请现场手工重启 mineru-ops" : "";
    notice(`已回滚到 ${name}${restartHint}`, false);
    await loadConfig();
  } catch (error) {
    state.configApplyResult = {
      ok: false,
      restored: false,
      original_error: error.message,
      error: error.message,
      steps: [],
      manual_actions: [],
    };
    renderConfigApplyResult();
    document.getElementById("config-validation-status").textContent = error.message;
    document.getElementById("config-validation-status").classList.add("bad-text");
    notice(error.message);
  } finally {
    setConfigBusy(false);
  }
}

function markConfigDraft(input) {
  state.configDraft[input.dataset.configKey] = input.value;
  delete state.configValidationErrors[input.dataset.configKey];
  state.configPlan = null;
  renderConfigPlan();
  const status = document.getElementById("config-validation-status");
  status.textContent = "已有候选修改，请重新预览变更。";
  status.classList.remove("bad-text");
}

document.getElementById("config-reload").addEventListener("click", loadConfig);
document.getElementById("config-validate").addEventListener("click", validateConfig);
document.getElementById("config-plan").addEventListener("click", planConfig);
document.getElementById("config-apply").addEventListener("click", openConfigApplyDialog);
document.getElementById("config-confirm-apply").addEventListener("click", applyConfig);
document.getElementById("config-cancel-apply").addEventListener("click", () => document.getElementById("config-apply-dialog").close());
document.getElementById("config-groups").addEventListener("input", event => {
  const input = event.target.closest("[data-config-key]");
  if (input) markConfigDraft(input);
});
document.getElementById("config-groups").addEventListener("change", event => {
  const input = event.target.closest("[data-config-key]");
  if (input) markConfigDraft(input);
});
document.getElementById("config-history").addEventListener("click", event => {
  const button = event.target.closest("[data-config-restore]");
  if (button) restoreConfig(button.dataset.configRestore);
});
document.getElementById("config-apply-result")?.addEventListener("click", async event => {
  const button = event.target.closest("[data-copy-command-index]");
  if (!button) return;

  const actions = Array.isArray(state.configApplyResult?.manual_actions)
    ? state.configApplyResult.manual_actions
    : [];
  const action = actions[Number(button.dataset.copyCommandIndex)];
  const command = typeof action === "string"
    ? action
    : action?.command || action?.cmd || "";
  if (!command) {
    notice("没有可复制的命令");
    return;
  }
  try {
    await navigator.clipboard.writeText(command);
    notice("命令已复制", false);
  } catch {
    notice("复制失败，请手动复制命令");
  }
});
document.getElementById("runtime-diagnostics-refresh").addEventListener("click", () => {
  void loadRuntimeDiagnostics({force: true});
});
document.getElementById("config-status-refresh")?.addEventListener("click", () => {
  void loadConfigStatus({force: true});
});
document.getElementById("deep-diagnostics-refresh")?.addEventListener("click", () => {
  void loadDeepDiagnostics({force: true});
});
document.getElementById("audit-refresh").addEventListener("click", () => {
  void loadAuditLogs();
});
document.getElementById("audit-log").addEventListener("click", event => {
  if (event.target.closest("[data-audit-load-more]")) {
    void loadAuditLogs({append: true});
  }
});

async function refreshCurrent() {
  notice("");
  try {
    if (state.view === "overview") await loadOverview();
    else if (state.view === "services") {
      await loadServices();
      if (state.runtimeDiagnostics === null) {
        void loadRuntimeDiagnostics();
      }
      if (state.deepDiagnostics === null) {
        void loadDeepDiagnostics();
      }
    }
    else if (state.view === "tasks") await refreshTasksView();
    else if (state.view === "batch") await loadBatches();
    else if (state.view === "lab") await loadLab();
    else if (state.view === "config") await loadConfig();
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
  if (["overview", "services", "batch", "lab"].includes(state.view)) refreshCurrent();
  if (state.activeBatchId) refreshActiveBatchDocument();
}, 5000);
setInterval(() => {
  if (state.view === "logs" && document.getElementById("log-live").checked) loadSelectedServiceLogs();
}, 2000);
setInterval(() => {
  if (state.view === "tasks") refreshTasksView();
}, 2000);
refreshCurrent();
