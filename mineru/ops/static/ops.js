const state = {
  view: "overview",
  services: [],
  tasks: [],
  batches: [],
  serviceFilter: "all",
  token: sessionStorage.getItem("mineruOpsToken") || "",
};

const titles = {overview: "总览", services: "服务", tasks: "任务", batch: "批量测试", logs: "日志"};
const statusText = {
  pending: "等待中", processing: "处理中", completed: "已完成", failed: "失败",
  partial_success: "部分成功", running: "运行中", paused: "已暂停", cancelled: "已取消",
  completed_with_failures: "完成（存在失败）", interrupted: "已中断", unhealthy: "异常",
  healthy: "健康", unavailable: "不可用", unknown: "未知"
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
  const cls = ["healthy", "completed", "running"].includes(value) ? "good" :
    ["pending", "processing", "paused", "partial_success", "completed_with_failures"].includes(value) ? "warn" :
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
    return `<tr><td><strong>${esc(service.name)}</strong></td><td>${esc(service.role)}</td><td>${esc(service.runtime?.state || "unknown")}</td><td>${badge(service.health)}</td><td><div class="mono">${esc(service.image || service.endpoint || "-")}</div></td><td><div class="actions">${actions.map(action => `<button class="action-button ${action === "stop" ? "danger" : ""}" data-service="${esc(service.name)}" data-action="${action}">${{check:"检查",start:"启动",stop:"停止",restart:"重启"}[action]}</button>`).join("")}</div></td></tr>`;
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
    return `<tr><td><strong>${esc(run.settings?.input_path || run.input_path)}</strong><div class="mono muted">${esc(run.run_id)}</div></td><td>${badge(run.status)}</td><td>${run.settings?.pdf_count || "-"}</td><td>${esc(formatDate(run.started_at || run.created_at))}</td><td>${run.report_ready ? `<button class="text-button" data-export="markdown" data-batch="${run.run_id}">Markdown</button> · <button class="text-button" data-export="zip" data-batch="${run.run_id}">ZIP</button>` : "-"}</td><td><div class="actions">${active ? `<button class="action-button" data-batch="${run.run_id}" data-batch-action="${run.status === "paused" ? "resume" : "pause"}">${run.status === "paused" ? "继续" : "暂停"}</button><button class="action-button danger" data-batch="${run.run_id}" data-batch-action="cancel">取消</button>` : `<button class="action-button" data-batch="${run.run_id}" data-batch-action="retry">重试</button>`}</div></td></tr>${run.log_tail ? `<tr><td colspan="6"><pre class="mono">${esc(run.log_tail)}</pre></td></tr>` : ""}`;
  }).join("")}</tbody></table>`;
}

async function loadBatches() {
  const data = await api("/api/batch-runs");
  state.batches = data.items || [];
  document.getElementById("batch-table").innerHTML = compactBatchRows(state.batches);
}

const batchFilesInput = document.getElementById("batch-files");
const batchFolderInput = document.getElementById("batch-folder");
const uploadSummary = document.getElementById("upload-summary");

function pdfFiles(input) {
  return Array.from(input.files || []).filter(file => file.name.toLowerCase().endsWith(".pdf"));
}

function formatBytes(bytes) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function updateUploadSummary() {
  const directFiles = pdfFiles(batchFilesInput);
  const folderFiles = pdfFiles(batchFolderInput);
  const files = directFiles.length ? directFiles : folderFiles;
  if (!files.length) {
    uploadSummary.textContent = "当前使用服务器测试目录";
    return;
  }
  const source = directFiles.length ? "浏览器文件" : "浏览器文件夹";
  const ignored = (directFiles.length ? batchFilesInput.files.length : batchFolderInput.files.length) - files.length;
  const totalBytes = files.reduce((total, file) => total + file.size, 0);
  uploadSummary.textContent = `${source}：${files.length} 个 PDF，${formatBytes(totalBytes)}${ignored ? `；忽略 ${ignored} 个非 PDF 文件` : ""}`;
}

batchFilesInput.addEventListener("change", () => {
  if (batchFilesInput.files.length) batchFolderInput.value = "";
  updateUploadSummary();
});

batchFolderInput.addEventListener("change", () => {
  if (batchFolderInput.files.length) batchFilesInput.value = "";
  updateUploadSummary();
});

document.getElementById("batch-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = new FormData(event.target);
  const directFiles = pdfFiles(batchFilesInput);
  const folderFiles = pdfFiles(batchFolderInput);
  const submitButton = document.getElementById("batch-submit");
  if (directFiles.length && folderFiles.length) {
    notice("文件和文件夹不能同时上传，请保留一种选择");
    return;
  }
  const payload = {
    input_path: form.get("input_path"), backend: form.get("backend"), lang: form.get("lang"),
    task_timeout: Number(form.get("task_timeout")), server_url: form.get("server_url") || null,
    recursive: form.get("recursive") === "on", effort: "medium", parse_method: "auto", pause_seconds: 2
  };
  const browserFiles = directFiles.length ? directFiles : folderFiles;
  try {
    submitButton.disabled = true;
    if (browserFiles.length) {
      const upload = new FormData();
      for (const file of browserFiles) upload.append("files", file, file.webkitRelativePath || file.name);
      upload.append("backend", payload.backend);
      upload.append("lang", payload.lang);
      upload.append("task_timeout", String(payload.task_timeout));
      upload.append("server_url", payload.server_url || "");
      upload.append("recursive", String(payload.recursive || folderFiles.length > 0));
      submitButton.textContent = `正在上传 0%`;
      await uploadForm("/api/batch-runs/upload", upload, percent => {
        submitButton.textContent = percent < 100 ? `正在上传 ${percent}%` : "正在启动测试";
      });
      batchFilesInput.value = "";
      batchFolderInput.value = "";
      updateUploadSummary();
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

document.getElementById("batch-table").addEventListener("click", async event => {
  const exportButton = event.target.closest("[data-export]");
  if (exportButton) {
    try {
      const headers = state.token ? {"X-MinerU-Ops-Token": state.token} : {};
      const response = await fetch(`/api/batch-runs/${exportButton.dataset.batch}/export?format=${exportButton.dataset.export}`, {headers});
      if (!response.ok) throw new Error(`导出失败：HTTP ${response.status}`);
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = exportButton.dataset.export === "zip" ? `mineru-batch-${exportButton.dataset.batch}.zip` : `BATCH_DIAGNOSIS-${exportButton.dataset.batch}.md`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (error) { notice(error.message); }
    return;
  }
  const button = event.target.closest("[data-batch-action]");
  if (!button) return;
  if (["cancel", "retry"].includes(button.dataset.batchAction) && !confirm(`确认${button.textContent}该批次？`)) return;
  try {
    await api(`/api/batch-runs/${button.dataset.batch}/${button.dataset.batchAction}`, {method: "POST"});
    await loadBatches();
  } catch (error) { notice(error.message); }
});

function updateLogServices() {
  const select = document.getElementById("log-service");
  const selected = select.value;
  select.innerHTML = state.services.filter(item => item.role !== "vlm" || item.runtime).map(item => `<option value="${esc(item.name)}">${esc(item.name)}</option>`).join("");
  if ([...select.options].some(option => option.value === selected)) select.value = selected;
}

document.getElementById("load-logs").addEventListener("click", async () => {
  const service = document.getElementById("log-service").value;
  const tail = document.getElementById("log-tail").value;
  const output = document.getElementById("log-output");
  output.textContent = "正在读取...";
  try {
    const data = await api(`/api/services/${encodeURIComponent(service)}/logs?tail=${tail}`);
    output.textContent = data.logs || "没有日志输出。";
  } catch (error) { output.textContent = error.message; }
});

async function refreshCurrent() {
  notice("");
  try {
    if (state.view === "overview") await loadOverview();
    else if (state.view === "services") await loadServices();
    else if (state.view === "tasks") await loadTasks();
    else if (state.view === "batch") await loadBatches();
    else if (state.view === "logs" && !state.services.length) await loadServices();
    setConnection(true, "控制台已连接");
  } catch (error) {
    setConnection(false, "连接异常");
    notice(error.message);
  }
}

setInterval(() => {
  if (["overview", "tasks", "batch"].includes(state.view)) refreshCurrent();
}, 5000);
refreshCurrent();
