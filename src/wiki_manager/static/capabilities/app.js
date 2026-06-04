const DEFAULT_USER = window.WIKI_MANAGER_DEFAULT_USER || "root";

const API_HEADERS = {
  "Content-Type": "application/json",
  "X-Wiki-User": DEFAULT_USER,
};

let selectedServiceKey = "";
let services = [];
let currentView = "catalog";

const els = {
  form: document.getElementById("serviceForm"),
  servicesTable: document.getElementById("servicesTable"),
  catalogTable: document.getElementById("catalogTable"),
  profilesTable: document.getElementById("profilesTable"),
  logsTable: document.getElementById("logsTable"),
  messageArea: document.getElementById("messageArea"),
  reloadTools: document.getElementById("reloadTools"),
  reloadCatalog: document.getElementById("reloadCatalog"),
  reloadProfiles: document.getElementById("reloadProfiles"),
  reloadLogs: document.getElementById("reloadLogs"),
  refreshServices: document.getElementById("refreshServices"),
  selectedServiceHint: document.getElementById("selectedServiceHint"),
  toolsList: document.getElementById("toolsList"),
  claudeConfig: document.getElementById("claudeConfig"),
  serviceKey: document.getElementById("serviceKey"),
  serviceName: document.getElementById("serviceName"),
  endpointUrl: document.getElementById("endpointUrl"),
  headersJson: document.getElementById("headersJson"),
  description: document.getElementById("description"),
  tags: document.getElementById("tags"),
};

function showMessage(text, type = "success") {
  els.messageArea.textContent = text;
  els.messageArea.className = `message-area visible ${type}`;
}

function clearMessage() {
  els.messageArea.textContent = "";
  els.messageArea.className = "message-area";
}

async function apiRequest(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {
      ...API_HEADERS,
      ...(options.headers || {}),
    },
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = payload && payload.detail ? payload.detail : response.statusText;
    throw new Error(detail);
  }
  return payload;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderTags(tags) {
  if (!tags || tags.length === 0) {
    return '<span class="empty">未设置</span>';
  }
  return tags.map((tag) => `<span class="badge">${escapeHtml(tag)}</span>`).join("");
}

function statusText(status) {
  const map = {
    enabled: "启用",
    disabled: "停用",
    error: "异常",
    active: "启用",
    success: "成功",
    blocked: "已拦截",
  };
  return map[status] || status || "未知";
}

function setView(view) {
  currentView = view;
  document.querySelectorAll(".view").forEach((node) => {
    node.classList.toggle("active", node.id === `view-${view}`);
  });
  document.querySelectorAll(".nav-item[data-view]").forEach((node) => {
    node.classList.toggle("active", node.dataset.view === view);
  });
  if (view === "catalog") {
    loadCatalog();
  } else if (view === "services") {
    loadServices();
  } else if (view === "tools") {
    loadTools();
  } else if (view === "profiles") {
    loadProfiles();
  } else if (view === "logs") {
    loadLogs();
  } else if (view === "claude") {
    renderClaudeConfig();
  }
}

function renderServices() {
  if (services.length === 0) {
    els.servicesTable.innerHTML = '<tr><td colspan="5" class="empty">尚未登记 MCP 服务。</td></tr>';
    return;
  }

  els.servicesTable.innerHTML = services
    .map((service) => {
      const isEnabled = service.status === "enabled";
      const nextStatus = isEnabled ? "disabled" : "enabled";
      const statusAction = isEnabled ? "停用" : "启用";
      return `
        <tr>
          <td>
            <span class="service-name">${escapeHtml(service.name)}</span>
            <span class="service-key">${escapeHtml(service.service_key)}</span>
          </td>
          <td>${escapeHtml(service.endpoint_url)}</td>
          <td><span class="badge ${escapeHtml(service.status)}">${escapeHtml(statusText(service.status))}</span></td>
          <td>${renderTags(service.tags)}</td>
          <td>
            <div class="row-actions">
              <button type="button" data-action="select" data-service="${escapeHtml(service.service_key)}">查看工具</button>
              <button type="button" data-action="edit" data-service="${escapeHtml(service.service_key)}">编辑</button>
              <button type="button" data-action="sync" data-service="${escapeHtml(service.service_key)}">同步工具</button>
              <button type="button" data-action="status" data-status="${nextStatus}" data-service="${escapeHtml(service.service_key)}">${statusAction}</button>
            </div>
          </td>
        </tr>
      `;
    })
    .join("");
}

async function loadServices(options = {}) {
  if (!options.preserveMessage) {
    clearMessage();
  }
  els.servicesTable.innerHTML = '<tr><td colspan="5" class="empty">正在读取服务...</td></tr>';
  try {
    services = await apiRequest("/capabilities/mcp-services", { method: "GET" });
    renderServices();
    if (selectedServiceKey && services.some((service) => service.service_key === selectedServiceKey)) {
      await loadTools(selectedServiceKey);
    }
  } catch (error) {
    els.servicesTable.innerHTML = '<tr><td colspan="5" class="empty">服务读取失败。</td></tr>';
    showMessage(error.message, "error");
  }
}

async function loadCatalog() {
  clearMessage();
  els.catalogTable.innerHTML = '<tr><td colspan="4" class="empty">正在读取能力目录...</td></tr>';
  try {
    const data = await apiRequest("/capability-catalog", { method: "GET" });
    const sources = data.sources || [];
    if (sources.length === 0) {
      els.catalogTable.innerHTML = '<tr><td colspan="4" class="empty">暂无可见能力。</td></tr>';
      return;
    }
    els.catalogTable.innerHTML = sources
      .map(
        (source) => `
          <tr>
            <td>
              <span class="service-name">${escapeHtml(source.name)}</span>
              <span class="service-key">${escapeHtml(source.source_key)}</span>
            </td>
            <td>${escapeHtml(source.description || "未填写描述")}</td>
            <td><span class="badge ${escapeHtml(source.status)}">${escapeHtml(statusText(source.status))}</span></td>
            <td>${renderTags(source.tags)}</td>
          </tr>
        `
      )
      .join("");
  } catch (error) {
    els.catalogTable.innerHTML = '<tr><td colspan="4" class="empty">能力目录读取失败。</td></tr>';
    showMessage(error.message, "error");
  }
}

async function loadProfiles() {
  clearMessage();
  els.profilesTable.innerHTML = '<tr><td colspan="4" class="empty">正在读取 Project Profile...</td></tr>';
  try {
    const profiles = await apiRequest("/capability-profiles", { method: "GET" });
    if (profiles.length === 0) {
      els.profilesTable.innerHTML = '<tr><td colspan="4" class="empty">尚未创建 Project Profile。</td></tr>';
      return;
    }
    els.profilesTable.innerHTML = profiles
      .map(
        (profile) => `
          <tr>
            <td>
              <span class="service-name">${escapeHtml(profile.name)}</span>
              <span class="service-key">${escapeHtml(profile.profile_key)}</span>
            </td>
            <td><span class="badge ${escapeHtml(profile.status)}">${escapeHtml(statusText(profile.status))}</span></td>
            <td>${Number(profile.allow_count || 0)}</td>
            <td>${Number(profile.deny_count || 0)}</td>
          </tr>
        `
      )
      .join("");
  } catch (error) {
    els.profilesTable.innerHTML = '<tr><td colspan="4" class="empty">Project Profile 读取失败。</td></tr>';
    showMessage(error.message, "error");
  }
}

async function loadLogs() {
  clearMessage();
  els.logsTable.innerHTML = '<tr><td colspan="6" class="empty">正在读取调用日志...</td></tr>';
  try {
    const logs = await apiRequest("/tool-call-logs", { method: "GET" });
    if (logs.length === 0) {
      els.logsTable.innerHTML = '<tr><td colspan="6" class="empty">暂无调用日志。</td></tr>';
      return;
    }
    els.logsTable.innerHTML = logs
      .map(
        (log) => `
          <tr>
            <td><span class="service-key">${escapeHtml(log.log_id)}</span></td>
            <td>${escapeHtml(log.entrypoint)}</td>
            <td>${escapeHtml(log.source_key || "-")}</td>
            <td>${escapeHtml(log.tool_name || "-")}</td>
            <td><span class="badge ${escapeHtml(log.status)}">${escapeHtml(statusText(log.status))}</span></td>
            <td>${escapeHtml(log.created_at)}</td>
          </tr>
        `
      )
      .join("");
  } catch (error) {
    els.logsTable.innerHTML = '<tr><td colspan="6" class="empty">调用日志读取失败。</td></tr>';
    showMessage(error.message, "error");
  }
}

function renderClaudeConfig() {
  const command = [
    "wiki metamcp add --name agent-capability-hub \\",
    "  --url http://127.0.0.1:8000/mcp \\",
    "  --profile safe-readonly",
  ].join("\n");
  els.claudeConfig.textContent = command;
}

function parseHeaders() {
  const raw = els.headersJson.value.trim();
  if (!raw) {
    return {};
  }
  const parsed = JSON.parse(raw);
  if (parsed === null || Array.isArray(parsed) || typeof parsed !== "object") {
    throw new Error("请求头 JSON 必须是对象。");
  }
  return parsed;
}

function servicePayloadFromForm() {
  const payload = {
    service_key: els.serviceKey.value.trim(),
    name: els.serviceName.value.trim(),
    endpoint_url: els.endpointUrl.value.trim(),
    description: els.description.value.trim(),
    tags: els.tags.value
      .split(",")
      .map((tag) => tag.trim())
      .filter(Boolean),
  };
  if (els.headersJson.value.trim()) {
    payload.headers = parseHeaders();
  }
  return payload;
}

function fillForm(service) {
  els.serviceKey.value = service.service_key || "";
  els.serviceName.value = service.name || "";
  els.endpointUrl.value = service.endpoint_url || "";
  els.headersJson.value = "";
  els.headersJson.placeholder = "留空表示保留已有请求头。";
  els.description.value = service.description || "";
  els.tags.value = (service.tags || []).join(", ");
  setView("services");
  els.serviceKey.focus();
}

async function saveService(event) {
  event.preventDefault();
  try {
    const payload = servicePayloadFromForm();
    await apiRequest("/capabilities/mcp-services", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    selectedServiceKey = payload.service_key;
    await loadServices({ preserveMessage: true });
    showMessage(`已保存服务 ${payload.service_key}。`);
  } catch (error) {
    showMessage(error.message, "error");
  }
}

async function setServiceStatus(serviceKey, status) {
  try {
    await apiRequest(`/capabilities/mcp-services/${encodeURIComponent(serviceKey)}/status`, {
      method: "POST",
      body: JSON.stringify({ status }),
    });
    await loadServices({ preserveMessage: true });
    showMessage(`${serviceKey} 已切换为 ${status}。`);
  } catch (error) {
    showMessage(error.message, "error");
  }
}

async function syncService(serviceKey) {
  try {
    const result = await apiRequest(`/capabilities/mcp-services/${encodeURIComponent(serviceKey)}/sync`, {
      method: "POST",
    });
    selectedServiceKey = serviceKey;
    await loadServices({ preserveMessage: true });
    await loadTools(serviceKey);
    showMessage(`已从 ${serviceKey} 同步 ${result.tool_count} 个工具。`);
  } catch (error) {
    await loadServices({ preserveMessage: true });
    showMessage(error.message, "error");
  }
}

async function loadTools(serviceKey = selectedServiceKey) {
  if (!serviceKey) {
    els.reloadTools.disabled = true;
    els.selectedServiceHint.textContent = "选择一个服务查看已同步工具。";
    els.toolsList.className = "tools-list empty";
    els.toolsList.textContent = "尚未选择服务。";
    return;
  }

  selectedServiceKey = serviceKey;
  els.reloadTools.disabled = false;
  els.selectedServiceHint.textContent = serviceKey;
  els.toolsList.className = "tools-list empty";
  els.toolsList.textContent = "正在读取工具...";

  try {
    const tools = await apiRequest(`/capabilities/mcp-services/${encodeURIComponent(serviceKey)}/tools`, {
      method: "GET",
    });
    if (tools.length === 0) {
      els.toolsList.className = "tools-list empty";
      els.toolsList.textContent = "暂无活跃工具。登记服务后请先同步工具。";
      return;
    }
    els.toolsList.className = "tools-list";
    els.toolsList.innerHTML = tools
      .map(
        (tool) => `
          <article class="tool-item">
            <div class="tool-title">
              <strong>${escapeHtml(tool.name || tool.tool)}</strong>
              <span class="badge">${escapeHtml(tool.tool_type)}</span>
            </div>
            <p>${escapeHtml(tool.description || "未填写描述。")}</p>
          </article>
        `
      )
      .join("");
  } catch (error) {
    els.toolsList.className = "tools-list empty";
    els.toolsList.textContent = error.message;
    showMessage(error.message, "error");
  }
}

function refreshCurrentView() {
  if (currentView === "catalog") {
    loadCatalog();
  } else if (currentView === "services") {
    loadServices();
  } else if (currentView === "tools") {
    loadTools();
  } else if (currentView === "profiles") {
    loadProfiles();
  } else if (currentView === "logs") {
    loadLogs();
  } else {
    renderClaudeConfig();
  }
}

document.querySelectorAll(".nav-item[data-view]").forEach((button) => {
  button.addEventListener("click", () => setView(button.dataset.view));
});

els.form.addEventListener("submit", saveService);
els.refreshServices.addEventListener("click", refreshCurrentView);
els.reloadCatalog.addEventListener("click", loadCatalog);
els.reloadProfiles.addEventListener("click", loadProfiles);
els.reloadLogs.addEventListener("click", loadLogs);
els.reloadTools.addEventListener("click", () => loadTools());
els.servicesTable.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) {
    return;
  }
  const serviceKey = button.dataset.service;
  const service = services.find((item) => item.service_key === serviceKey);
  if (button.dataset.action === "select") {
    setView("tools");
    loadTools(serviceKey);
  } else if (button.dataset.action === "edit" && service) {
    fillForm(service);
  } else if (button.dataset.action === "sync") {
    syncService(serviceKey);
  } else if (button.dataset.action === "status") {
    setServiceStatus(serviceKey, button.dataset.status);
  }
});

window.loadServices = loadServices;
window.loadCatalog = loadCatalog;
window.loadProfiles = loadProfiles;
window.loadLogs = loadLogs;

renderClaudeConfig();
loadServices({ preserveMessage: true });
loadCatalog();
