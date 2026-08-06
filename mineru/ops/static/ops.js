const state = {
  view: "overview",
  services: [],
  tasks: [],
  batches: [],
  uploadItems: [],
  activeBatchId: null,
  activeBatchDetail: null,
  activeBatchFilePath: null,
  activeBatchPreviewSignature: null,
  batchPreviewRequestId: 0,
  previewObjectUrls: [],
  serviceFilter: "all",
  logRequestId: 0,
  logLoading: false,
  token: sessionStorage.getItem("mineruOpsToken") || "",
};

const titles = {overview: "总览", services: "服务", tasks: "任务", batch: "批量测试", logs: "日志"};
const statusText = {
  pending: "等待中", processing: "处理中", completed: "已完成", failed: "失败",
  partial_success: "部分成功", running: "运行中", paused: "已暂停", cancelled: "已取消",
  completed_with_failures: "完成（存在失败）", interrupted: "已中断", unhealthy: "异常",
  healthy: "健康", unavailable: "不可用", unknown: "未知", success: "成功", partial: "部分成功",
  client_error: "客户端错误"
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
    ["pending", "processing", "paused", "partial", "partial_success", "completed_with_failures"].includes(value) ? "warn" :
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
  document.getElementById("tasks-table").innerHTML = `<table><thead><tr><th>文件</th><th>状态</th><th>后端</th><th>成功/总页数</th><th>跳过</th><th>开始时间</th></tr></thead><tbody>${tasks.map(task => {
    const p = task.progress || {};
    return `<tr data-task="${esc(task.task_id)}"><td><strong>${esc((task.file_names || []).join(", "))}</strong><div class="mono muted">${esc(task.task_id)}</div></td><td>${badge(task.partial_success ? "partial_success" : task.status)}</td><td>${esc(task.backend)}</td><td>${p.completed_pages || 0}/${p.total_pages || "-"}</td><td>${p.skipped_pages || 0}</td><td>${esc(formatDate(task.started_at || task.created_at))}</td></tr>`;
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

async function loadTaskDetail(taskId) {
  try {
    const task = await api(`/api/tasks/${encodeURIComponent(taskId)}`);
    const p = task.progress || {};
    const done = (p.completed_pages || 0) + (p.skipped_pages || 0) + (p.failed_pages || 0);
    const percent = p.total_pages ? Math.min(100, Math.round(done * 100 / p.total_pages)) : 0;
    const pages = (p.files || []).flatMap(file => (file.pages || []).map(page => ({...page, file_name: file.file_name})));
    document.getElementById("task-detail").innerHTML = `<div class="section-heading"><h2>${esc((task.file_names || []).join(", "))}</h2>${badge(task.partial_success ? "partial_success" : task.status)}</div><div class="progress"><span style="width:${percent}%"></span></div><dl class="detail-grid"><dt>Task ID</dt><dd class="mono">${esc(task.task_id)}</dd><dt>阶段</dt><dd>${esc(p.phase || "-")}</dd><dt>页数</dt><dd>${done}/${p.total_pages || "-"}，进行中 ${p.processing_pages || 0}</dd><dt>跳过页</dt><dd>${esc((p.skipped_page_numbers || []).join(", ") || "-")}</dd><dt>失败页</dt><dd>${esc((p.failed_page_numbers || []).join(", ") || "-")}</dd><dt>错误</dt><dd>${esc(task.error || "-")}</dd></dl><div class="section-heading"><h3>页面状态</h3><span>${pages.length} 页</span></div><div class="page-list">${pages.map(page => `<span class="page-chip ${esc(page.status)}" title="${esc(page.file_name)} ${esc(page.error || "")}">${page.page_number}</span>`).join("") || `<span class="muted">尚无页面事件</span>`}</div>${pages.filter(page => page.status === "skipped" || page.status === "failed").map(page => `<dl class="detail-grid"><dt>第 ${page.page_number} 页</dt><dd>${badge(page.status)} ${esc(page.error_type || "")} ${esc(page.error || "")}</dd></dl>`).join("")}`;
  } catch (error) { notice(error.message); }
}

function compactBatchRows(items, limit = 100) {
  if (!items.length) return `<div class="empty-state">暂无批量测试</div>`;
  return `<table><thead><tr><th>目录</th><th>状态</th><th>PDF</th><th>开始时间</th><th>报告</th><th>操作</th></tr></thead><tbody>${items.slice(0, limit).map(run => {
    const active = ["pending", "running", "paused", "cancelling"].includes(run.status);
    const previewText = run.input_preview_ready || run.result_preview_ready ? "可预览" : "";
    return `<tr><td><strong>${esc(run.settings?.input_path || run.input_path)}</strong><div class="mono muted">${esc(run.run_id)}</div></td><td>${badge(run.status)}${previewText ? `<div class="muted">${previewText}</div>` : ""}</td><td>${run.settings?.pdf_count || "-"}</td><td>${esc(formatDate(run.started_at || run.created_at))}</td><td><button class="text-button" data-batch-detail="${run.run_id}">详情/预览</button> · <button class="text-button" data-export="process_markdown" data-batch="${run.run_id}">过程日志</button>${run.report_ready ? ` · <button class="text-button" data-export="markdown" data-batch="${run.run_id}">报告</button> · <button class="text-button" data-export="zip" data-batch="${run.run_id}">ZIP</button>` : ""}</td><td><div class="actions">${active ? `<button class="action-button" data-batch="${run.run_id}" data-batch-action="${run.status === "paused" ? "resume" : "pause"}">${run.status === "paused" ? "继续" : "暂停"}</button><button class="action-button danger" data-batch="${run.run_id}" data-batch-action="cancel">取消</button>` : `<button class="action-button" data-batch="${run.run_id}" data-batch-action="retry">重试</button><button class="action-button danger" data-batch="${run.run_id}" data-batch-action="delete">删除</button>`}</div></td></tr>`;
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
    task_timeout: Number(form.get("task_timeout")), server_url: form.get("server_url") || null,
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
  const exportButton = event.target.closest("[data-export]");
  if (exportButton) {
    try {
      await exportBatch(exportButton.dataset.batch, exportButton.dataset.export);
    } catch (error) { notice(error.message); }
    return;
  }
  const button = event.target.closest("[data-batch-action]");
  if (!button) return;
  if (["cancel", "retry", "delete"].includes(button.dataset.batchAction) && !confirm(`确认${button.textContent}该批次？`)) return;
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

function batchFiles(detail) {
  const items = new Map();
  for (const original of detail.artifacts?.originals || []) {
    items.set(original.path, {path: original.path, name: original.name, original});
  }
  for (const preview of detail.artifacts?.previews || []) {
    const path = preview.relative_path || preview.file_name;
    const current = items.get(path) || {path, name: preview.file_name || path};
    current.preview = preview;
    items.set(path, current);
  }
  return [...items.values()];
}

function batchPreviewSignature(item) {
  const preview = item?.preview || {};
  return JSON.stringify([
    item?.original?.path || null,
    item?.original?.kind || null,
    preview.task_status || null,
    preview.classification || null,
    preview.error || null,
    preview.preview?.markdown_path || null,
    preview.preview?.archive_path || null,
    (preview.failed_pages || []).length,
  ]);
}

function renderBatchFileList(detail, preferredPath = null) {
  const files = batchFiles(detail);
  const active = ["pending", "running", "paused", "cancelling"].includes(detail.status);
  document.getElementById("batch-detail-files").innerHTML = files.length ? files.map(item => {
    const originalStatus = item.original ? "有原始 PDF" : "无原始 PDF";
    const resultStatus = item.preview?.preview?.markdown_path ? "有 Markdown" : (active ? "等待结果" : "无 Markdown");
    return `<button type="button" class="batch-file-button ${item.path === preferredPath ? "active" : ""}" data-file-path="${esc(item.path)}"><strong>${esc(item.name || item.path)}</strong><small>${esc(item.path)} · ${statusText[item.preview?.classification] || item.preview?.classification || "处理中"}</small><small>${originalStatus} · ${resultStatus}</small></button>`;
  }).join("") : `<div class="empty-state">${active ? "任务正在运行，文件和结果产物生成后会自动出现" : "该任务没有发现可预览的文件产物"}</div>`;
  const selected = files.find(item => item.path === preferredPath) || files[0] || null;
  return {files, selected};
}

async function fetchArtifactBlob(runId, kind, path) {
  const response = await authorizedFetch(artifactUrl(runId, kind, path));
  return response.blob();
}

async function selectBatchFile(detail, item) {
  const requestId = ++state.batchPreviewRequestId;
  state.activeBatchFilePath = item.path;
  state.activeBatchPreviewSignature = batchPreviewSignature(item);
  clearPreviewObjectUrls();
  document.querySelectorAll(".batch-file-button").forEach(button => button.classList.toggle("active", button.dataset.filePath === item.path));
  const originalFrame = document.getElementById("original-preview");
  const originalEmpty = document.getElementById("original-preview-empty");
  const resultPreview = document.getElementById("result-preview");
  const resultImages = document.getElementById("result-images");
  const downloadOriginal = document.getElementById("download-original");
  const downloadResult = document.getElementById("download-result");
  originalFrame.removeAttribute("src");
  originalFrame.style.display = "none";
  originalEmpty.classList.remove("hidden");
  originalEmpty.textContent = item.original ? "正在读取原始 PDF..." : "当前文件没有可访问的原始 PDF";
  downloadOriginal.classList.add("hidden");
  downloadResult.classList.add("hidden");
  resultPreview.innerHTML = `<div class="empty-state">${item.preview?.preview?.markdown_path ? "正在读取并渲染 Markdown..." : "提取结果尚未生成，任务运行时会自动刷新"}</div>`;
  resultImages.innerHTML = "";

  const preview = item.preview;
  const failedPages = preview?.failed_pages || [];
  document.getElementById("batch-file-status").innerHTML = `${badge(preview?.classification || preview?.task_status || "unknown")} <strong>${esc(item.path)}</strong>${preview?.task_id ? ` <span class="mono">${esc(preview.task_id)}</span>` : ""}${failedPages.length ? `<div>跳过/失败页：${esc(failedPages.map(page => page.page_number || Number(page.page_idx) + 1).join(", "))}</div>` : ""}${preview?.error ? `<div class="bad-text">${esc(preview.error)}</div>` : ""}`;

  const originalPromise = (async () => {
    if (item.original) {
      try {
        const blob = await fetchArtifactBlob(detail.run_id, item.original.kind || "input", item.original.path);
        if (requestId !== state.batchPreviewRequestId) return;
        const url = URL.createObjectURL(blob);
        state.previewObjectUrls.push(url);
        originalFrame.src = url;
        originalFrame.style.display = "block";
        originalEmpty.classList.add("hidden");
        downloadOriginal.classList.remove("hidden");
        downloadOriginal.dataset.path = item.original.path;
        downloadOriginal.dataset.kind = item.original.kind || "input";
        downloadOriginal.dataset.filename = item.original.name;
      } catch (error) {
        if (requestId === state.batchPreviewRequestId) originalEmpty.textContent = `原始 PDF 读取失败：${error.message}`;
      }
    } else {
      originalEmpty.textContent = detail.settings?.source_type === "server_directory" ? "服务器目录中的源 PDF 当前不可访问或已被移动" : "原始 PDF 已超过保留期或已被清理";
    }
  })();

  const previewFiles = preview?.preview || {};
  const resultPromise = (async () => {
    if (previewFiles.markdown_path) {
      try {
        const response = await authorizedFetch(artifactUrl(detail.run_id, "results", previewFiles.markdown_path));
        const markdown = await response.text();
        if (requestId !== state.batchPreviewRequestId) return;
        resultPreview.innerHTML = renderMarkdown(markdown);
      } catch (error) {
        if (requestId === state.batchPreviewRequestId) resultPreview.innerHTML = `<div class="empty-state">提取结果读取失败：${esc(error.message)}</div>`;
      }
    } else {
      resultPreview.innerHTML = `<div class="empty-state">${["pending", "running", "paused", "cancelling"].includes(detail.status) ? "该文件仍在处理，Markdown 生成后会自动显示" : "该文件没有生成 Markdown 提取结果"}</div>`;
    }
    if (previewFiles.archive_path && requestId === state.batchPreviewRequestId) {
      downloadResult.classList.remove("hidden");
      downloadResult.dataset.path = previewFiles.archive_path;
      downloadResult.dataset.filename = `${item.name || "result"}-result.zip`;
    }
    const imagePaths = (previewFiles.image_paths || []).slice(0, 30);
    const loadedImages = await Promise.all(imagePaths.map(async path => {
      try {
        const blob = await fetchArtifactBlob(detail.run_id, "results", path);
        if (requestId !== state.batchPreviewRequestId) return null;
        const url = URL.createObjectURL(blob);
        state.previewObjectUrls.push(url);
        return {url, path};
      } catch { return null; }
    }));
    if (requestId === state.batchPreviewRequestId) resultImages.innerHTML = loadedImages.filter(Boolean).map(image => `<img src="${image.url}" alt="${esc(image.path)}" title="${esc(image.path)}">`).join("");
  })();

  await Promise.allSettled([originalPromise, resultPromise]);
}

async function loadBatchProcessLog(runId, {preserveScroll = false} = {}) {
  const output = document.getElementById("batch-process-log");
  const nearBottom = output.scrollHeight - output.scrollTop - output.clientHeight < 48;
  try {
    const response = await authorizedFetch(`/api/batch-runs/${encodeURIComponent(runId)}/export?format=process_markdown`);
    const content = await response.text();
    if (state.activeBatchId !== runId) return;
    output.textContent = content;
    if (!preserveScroll || nearBottom) output.scrollTop = output.scrollHeight;
  } catch (error) {
    if (state.activeBatchId === runId) output.textContent = `过程日志读取失败：${error.message}`;
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
    const {selected} = renderBatchFileList(detail);
    document.getElementById("batch-process-log").textContent = "正在读取过程日志...";
    if (!dialog.open) dialog.showModal();
    const previewPromise = selected ? selectBatchFile(detail, selected) : Promise.resolve();
    await Promise.allSettled([previewPromise, loadBatchProcessLog(runId)]);
  } catch (error) { notice(error.message); }
}

document.getElementById("batch-detail-files").addEventListener("click", async event => {
  const button = event.target.closest("[data-file-path]");
  if (!button || !state.activeBatchDetail) return;
  const item = batchFiles(state.activeBatchDetail).find(candidate => candidate.path === button.dataset.filePath);
  if (item) await selectBatchFile(state.activeBatchDetail, item);
});

document.getElementById("download-original").addEventListener("click", async event => {
  if (!state.activeBatchId) return;
  try { await downloadPath(artifactUrl(state.activeBatchId, event.currentTarget.dataset.kind || "input", event.currentTarget.dataset.path), event.currentTarget.dataset.filename); }
  catch (error) { notice(error.message); }
});

document.getElementById("download-result").addEventListener("click", async event => {
  if (!state.activeBatchId) return;
  try { await downloadPath(artifactUrl(state.activeBatchId, "results", event.currentTarget.dataset.path), event.currentTarget.dataset.filename); }
  catch (error) { notice(error.message); }
});

document.querySelector(".dialog-actions").addEventListener("click", async event => {
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
  state.activeBatchFilePath = null;
  state.activeBatchPreviewSignature = null;
  state.batchPreviewRequestId += 1;
  if (dialog.open) dialog.close();
}

document.getElementById("close-batch-detail").addEventListener("click", closeBatchDetail);
document.getElementById("batch-detail-dialog").addEventListener("close", () => {
  clearPreviewObjectUrls();
  state.activeBatchId = null;
  state.activeBatchDetail = null;
  state.activeBatchFilePath = null;
  state.activeBatchPreviewSignature = null;
  state.batchPreviewRequestId += 1;
});

async function refreshActiveBatchLog() {
  const runId = state.activeBatchId;
  if (!runId) return;
  try {
    const detail = await api(`/api/batch-runs/${encodeURIComponent(runId)}`);
    if (state.activeBatchId !== runId) return;
    state.activeBatchDetail = detail;
    document.getElementById("batch-detail-meta").textContent = `${detail.run_id} · ${statusText[detail.status] || detail.status} · ${detail.settings?.pdf_count || 0} 个 PDF · 产物 ${formatBytes(detail.artifacts?.storage_bytes || 0)} · 保留 ${detail.artifacts?.retention_days ?? "-"} 天`;
    const {selected} = renderBatchFileList(detail, state.activeBatchFilePath);
    if (selected && (selected.path !== state.activeBatchFilePath || batchPreviewSignature(selected) !== state.activeBatchPreviewSignature)) {
      await selectBatchFile(detail, selected);
    }
    await loadBatchProcessLog(runId, {preserveScroll: true});
  } catch {}
}

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
    else if (state.view === "tasks") await loadTasks();
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
  if (["overview", "tasks", "batch"].includes(state.view)) refreshCurrent();
  if (state.activeBatchId) refreshActiveBatchLog();
}, 5000);
setInterval(() => {
  if (state.view === "logs" && document.getElementById("log-live").checked) loadSelectedServiceLogs();
}, 2000);
refreshCurrent();
