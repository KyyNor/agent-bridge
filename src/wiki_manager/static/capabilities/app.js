const DEFAULT_USER = window.WIKI_MANAGER_DEFAULT_USER || "root";

const API_HEADERS = {
  "Content-Type": "application/json",
  "X-Wiki-User": DEFAULT_USER,
};

let selectedServiceKey = "";
let services = [];
let allTools = [];
let profiles = [];
let currentView = "catalog";

const VALID_VIEWS = new Set(["catalog", "services", "tools", "profiles", "logs", "claude"]);

const TOOL_TYPE_LABELS = {
  unconfigured: "未配置",
  overview: "目录",
  search: "检索",
  detail: "明细",
  action: "操作",
};

const TOOL_LAYER_CLASSES = {
  unconfigured: "tool-layer-unconfigured",
  overview: "tool-layer-overview",
  search: "tool-layer-search",
  detail: "tool-layer-detail",
  action: "tool-layer-action",
};

const TAG_CLASSES = ["tag-blue", "tag-green", "tag-amber", "tag-teal", "tag-violet", "tag-slate"];

const els = {
  form: document.getElementById("serviceForm"),
  servicesTable: document.getElementById("servicesTable"),
  catalogTable: document.getElementById("catalogTable"),
  profilesTable: document.getElementById("profilesTable"),
  logsTable: document.getElementById("logsTable"),
  messageArea: document.getElementById("messageArea"),
  openServiceDialog: document.getElementById("openServiceDialog"),
  serviceDialog: document.getElementById("serviceDialog"),
  closeServiceDialog: document.getElementById("closeServiceDialog"),
  cancelServiceDialog: document.getElementById("cancelServiceDialog"),
  serviceDialogTitle: document.getElementById("serviceDialogTitle"),
  serviceDialogHint: document.getElementById("serviceDialogHint"),
  profileForm: document.getElementById("profileForm"),
  openProfileDialog: document.getElementById("openProfileDialog"),
  profileDialog: document.getElementById("profileDialog"),
  closeProfileDialog: document.getElementById("closeProfileDialog"),
  cancelProfileDialog: document.getElementById("cancelProfileDialog"),
  profileKey: document.getElementById("profileKey"),
  profileName: document.getElementById("profileName"),
  profileDescription: document.getElementById("profileDescription"),
  profileStatus: document.getElementById("profileStatus"),
  profileRulesForm: document.getElementById("profileRulesForm"),
  profileRulesDialog: document.getElementById("profileRulesDialog"),
  closeProfileRulesDialog: document.getElementById("closeProfileRulesDialog"),
  cancelProfileRulesDialog: document.getElementById("cancelProfileRulesDialog"),
  profileRulesDialogTitle: document.getElementById("profileRulesDialogTitle"),
  profileRulesDialogHint: document.getElementById("profileRulesDialogHint"),
  profileRulesKey: document.getElementById("profileRulesKey"),
  profileRulesTable: document.getElementById("profileRulesTable"),
  selectedServiceHint: document.getElementById("selectedServiceHint"),
  toolsList: document.getElementById("toolsList"),
  toolServiceFilter: document.getElementById("toolServiceFilter"),
  toolTypeFilter: document.getElementById("toolTypeFilter"),
  toolsTableBody: document.getElementById("toolsTableBody"),
  toolTypeForm: document.getElementById("toolTypeForm"),
  toolTypeDialog: document.getElementById("toolTypeDialog"),
  closeToolTypeDialog: document.getElementById("closeToolTypeDialog"),
  cancelToolTypeDialog: document.getElementById("cancelToolTypeDialog"),
  toolTypeDialogTitle: document.getElementById("toolTypeDialogTitle"),
  toolTypeDialogHint: document.getElementById("toolTypeDialogHint"),
  toolTypeService: document.getElementById("toolTypeService"),
  toolTypeName: document.getElementById("toolTypeName"),
  toolTypeValue: document.getElementById("toolTypeValue"),
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
    return '<span class="empty-inline">未设置</span>';
  }
  return `<span class="tag-list">${tags
    .map((tag, index) => `<span class="tag-chip ${tagClassFor(tag, index)}">${escapeHtml(tag)}</span>`)
    .join("")}</span>`;
}

function tagClassFor(value, index) {
  const text = String(value || "");
  const hash = Array.from(text).reduce((sum, char) => sum + char.charCodeAt(0), index);
  return TAG_CLASSES[hash % TAG_CLASSES.length];
}

function normalizeToolType(toolType) {
  return Object.prototype.hasOwnProperty.call(TOOL_TYPE_LABELS, toolType) ? toolType : "unconfigured";
}

function renderToolLayer(toolType) {
  const normalized = normalizeToolType(toolType || "unconfigured");
  return `<span class="tag-chip tool-layer-badge ${TOOL_LAYER_CLASSES[normalized]}">${escapeHtml(
    TOOL_TYPE_LABELS[normalized]
  )}</span>`;
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

function routeFromLocation() {
  const params = new URLSearchParams(window.location.search);
  const view = VALID_VIEWS.has(params.get("view")) ? params.get("view") : "catalog";
  return {
    view,
    service: params.get("service") || "",
    type: params.get("type") || "",
  };
}

function writeRoute(view = currentView, serviceKey = selectedServiceKey, mode = "push", toolType = els.toolTypeFilter ? els.toolTypeFilter.value : "") {
  const params = new URLSearchParams();
  if (view && view !== "catalog") {
    params.set("view", view);
  }
  if (view === "tools" && serviceKey) {
    params.set("service", serviceKey);
  }
  if (view === "tools" && toolType) {
    params.set("type", toolType);
  }
  const nextUrl = `${window.location.pathname}${params.toString() ? `?${params}` : ""}`;
  if (`${window.location.pathname}${window.location.search}` === nextUrl) {
    return;
  }
  if (mode === "replace") {
    history.replaceState({ view, service: serviceKey }, "", nextUrl);
  } else {
    history.pushState({ view, service: serviceKey }, "", nextUrl);
  }
}

function navigateTo(view, serviceKey = "", toolType = "") {
  selectedServiceKey = serviceKey;
  writeRoute(view, serviceKey, "push", toolType);
  setView(view, { preserveRoute: true });
}

function setView(view, options = {}) {
  if (!VALID_VIEWS.has(view)) {
    view = "catalog";
  }
  currentView = view;
  document.querySelectorAll(".view").forEach((node) => {
    node.classList.toggle("active", node.id === `view-${view}`);
  });
  document.querySelectorAll(".nav-item[data-view]").forEach((node) => {
    node.classList.toggle("active", node.dataset.view === view);
  });
  if (!options.preserveRoute) {
    writeRoute(view);
  }
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
    renderToolFilters();
    if (currentView === "tools") {
      await loadAllTools();
    }
  } catch (error) {
    els.servicesTable.innerHTML = '<tr><td colspan="5" class="empty">服务读取失败。</td></tr>';
    showMessage(error.message, "error");
  }
}

function renderToolFilters() {
  const selectedService = selectedServiceKey || "";
  const serviceOptions = [
    '<option value="">全部服务</option>',
    ...services.map((service) => {
      const selected = service.service_key === selectedService ? " selected" : "";
      return `<option value="${escapeHtml(service.service_key)}"${selected}>${escapeHtml(service.name)} (${escapeHtml(service.service_key)})</option>`;
    }),
  ];
  els.toolServiceFilter.innerHTML = serviceOptions.join("");
  els.toolServiceFilter.value = selectedService;
}

async function loadCatalog() {
  clearMessage();
  els.catalogTable.innerHTML = '<tr><td colspan="5" class="empty">正在读取能力目录...</td></tr>';
  try {
    const data = await apiRequest("/capability-catalog", { method: "GET" });
    const sources = data.sources || [];
    if (sources.length === 0) {
      els.catalogTable.innerHTML = '<tr><td colspan="5" class="empty">暂无可见能力。</td></tr>';
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
            <td>
              <button type="button" data-action="catalog-tools" data-service="${escapeHtml(source.source_key)}">查看工具</button>
            </td>
          </tr>
        `
      )
      .join("");
  } catch (error) {
    els.catalogTable.innerHTML = '<tr><td colspan="5" class="empty">能力目录读取失败。</td></tr>';
    showMessage(error.message, "error");
  }
}

async function loadProfiles() {
  clearMessage();
  els.profilesTable.innerHTML = '<tr><td colspan="5" class="empty">正在读取 Project Profile...</td></tr>';
  try {
    profiles = await apiRequest("/capability-profiles", { method: "GET" });
    if (profiles.length === 0) {
      els.profilesTable.innerHTML = '<tr><td colspan="5" class="empty">尚未创建 Project Profile。</td></tr>';
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
            <td>
              <div class="row-actions">
                <button type="button" data-action="profile-rules" data-profile="${escapeHtml(profile.profile_key)}">配置规则</button>
              </div>
            </td>
          </tr>
        `
      )
      .join("");
  } catch (error) {
    els.profilesTable.innerHTML = '<tr><td colspan="5" class="empty">Project Profile 读取失败。</td></tr>';
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

function showServiceDialog() {
  if (typeof els.serviceDialog.showModal === "function") {
    els.serviceDialog.showModal();
  } else {
    els.serviceDialog.setAttribute("open", "");
  }
}

function closeServiceDialog() {
  if (typeof els.serviceDialog.close === "function") {
    els.serviceDialog.close();
  } else {
    els.serviceDialog.removeAttribute("open");
  }
}

function resetServiceForm() {
  els.form.reset();
  els.serviceDialogTitle.textContent = "登记服务";
  els.serviceDialogHint.textContent = "登记内部 MCP 端点并同步其工具定义。";
  els.serviceKey.readOnly = false;
  els.serviceKey.placeholder = "mysql";
  els.headersJson.placeholder = '{"Authorization":"Bearer token"}';
}

function openServiceForm(service = null) {
  resetServiceForm();
  if (service) {
    els.serviceDialogTitle.textContent = "编辑服务";
    els.serviceDialogHint.textContent = "更新服务名称、端点、描述、标签或请求头。";
    els.serviceKey.value = service.service_key || "";
    els.serviceKey.readOnly = true;
    els.serviceName.value = service.name || "";
    els.endpointUrl.value = service.endpoint_url || "";
    els.headersJson.value = "";
    els.headersJson.placeholder = "留空表示保留已有请求头。";
    els.description.value = service.description || "";
    els.tags.value = (service.tags || []).join(", ");
  }
  showServiceDialog();
  els.serviceName.focus();
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
    closeServiceDialog();
    showMessage(`已保存服务 ${payload.service_key}。`);
  } catch (error) {
    showMessage(error.message, "error");
  }
}

function showProfileDialog() {
  if (typeof els.profileDialog.showModal === "function") {
    els.profileDialog.showModal();
  } else {
    els.profileDialog.setAttribute("open", "");
  }
}

function closeProfileDialog() {
  if (typeof els.profileDialog.close === "function") {
    els.profileDialog.close();
  } else {
    els.profileDialog.removeAttribute("open");
  }
}

function showProfileRulesDialog() {
  if (typeof els.profileRulesDialog.showModal === "function") {
    els.profileRulesDialog.showModal();
  } else {
    els.profileRulesDialog.setAttribute("open", "");
  }
}

function closeProfileRulesDialog() {
  if (typeof els.profileRulesDialog.close === "function") {
    els.profileRulesDialog.close();
  } else {
    els.profileRulesDialog.removeAttribute("open");
  }
}

function showToolTypeDialog() {
  if (typeof els.toolTypeDialog.showModal === "function") {
    els.toolTypeDialog.showModal();
  } else {
    els.toolTypeDialog.setAttribute("open", "");
  }
}

function closeToolTypeDialog() {
  if (typeof els.toolTypeDialog.close === "function") {
    els.toolTypeDialog.close();
  } else {
    els.toolTypeDialog.removeAttribute("open");
  }
}

function openProfileForm() {
  els.profileForm.reset();
  els.profileStatus.value = "active";
  showProfileDialog();
  els.profileKey.focus();
}

function profilePayloadFromForm() {
  return {
    profile_key: els.profileKey.value.trim(),
    name: els.profileName.value.trim(),
    description: els.profileDescription.value.trim(),
    status: els.profileStatus.value,
  };
}

async function saveProfile(event) {
  event.preventDefault();
  try {
    const payload = profilePayloadFromForm();
    await apiRequest("/capability-profiles", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    await loadProfiles();
    closeProfileDialog();
    showMessage(`已保存 Profile ${payload.profile_key}。`);
  } catch (error) {
    showMessage(error.message, "error");
  }
}

function renderRuleEffectOptions(selected) {
  const options = [
    ["", "未设置"],
    ["allow", "Allow"],
    ["deny", "Deny"],
  ];
  return options
    .map(([value, label]) => {
      const isSelected = value === selected ? " selected" : "";
      return `<option value="${escapeHtml(value)}"${isSelected}>${escapeHtml(label)}</option>`;
    })
    .join("");
}

function renderProfileRulesTable(rules = []) {
  if (services.length === 0) {
    els.profileRulesTable.innerHTML = '<tr><td colspan="3" class="empty">尚未登记 MCP 服务。</td></tr>';
    return;
  }
  const ruleByService = new Map(
    rules
      .filter((rule) => rule.source_type === "mcp_service")
      .map((rule) => [rule.source_key, rule.effect])
  );
  els.profileRulesTable.innerHTML = services
    .map((service) => {
      const selected = ruleByService.get(service.service_key) || "";
      return `
        <tr>
          <td>
            <span class="service-name">${escapeHtml(service.name)}</span>
            <span class="service-key">${escapeHtml(service.service_key)}</span>
          </td>
          <td>${renderTags(service.tags)}</td>
          <td>
            <select class="rule-effect-select" data-service="${escapeHtml(service.service_key)}" aria-label="服务策略">
              ${renderRuleEffectOptions(selected)}
            </select>
          </td>
        </tr>
      `;
    })
    .join("");
}

async function openProfileRulesDialog(profileKey) {
  const profile = profiles.find((item) => item.profile_key === profileKey);
  els.profileRulesKey.value = profileKey;
  els.profileRulesDialogTitle.textContent = profile ? `配置规则：${profile.name}` : `配置规则：${profileKey}`;
  els.profileRulesDialogHint.textContent = `${profileKey} · 按 MCP 服务设置 Allow / Deny`;
  els.profileRulesTable.innerHTML = '<tr><td colspan="3" class="empty">正在读取服务规则...</td></tr>';
  showProfileRulesDialog();
  try {
    if (services.length === 0) {
      await loadServices({ preserveMessage: true });
    }
    const detail = await apiRequest(`/capability-profiles/${encodeURIComponent(profileKey)}`, { method: "GET" });
    renderProfileRulesTable(detail.rules || []);
    const firstSelect = els.profileRulesTable.querySelector("select");
    if (firstSelect) {
      firstSelect.focus();
    }
  } catch (error) {
    els.profileRulesTable.innerHTML = '<tr><td colspan="3" class="empty">规则读取失败。</td></tr>';
    showMessage(error.message, "error");
  }
}

async function saveProfileRules(event) {
  event.preventDefault();
  const profileKey = els.profileRulesKey.value;
  const rules = Array.from(els.profileRulesTable.querySelectorAll("select[data-service]"))
    .filter((select) => select.value)
    .map((select) => ({
      source_type: "mcp_service",
      source_key: select.dataset.service,
      effect: select.value,
    }));
  try {
    await apiRequest(`/capability-profiles/${encodeURIComponent(profileKey)}/rules`, {
      method: "PUT",
      body: JSON.stringify({ rules }),
    });
    await loadProfiles();
    closeProfileRulesDialog();
    showMessage(`已保存 Profile ${profileKey} 的规则。`);
  } catch (error) {
    showMessage(error.message, "error");
  }
}

function openToolTypeDialog(tool) {
  const toolType = normalizeToolType(tool.tool_type || "unconfigured");
  els.toolTypeService.value = tool.service || "";
  els.toolTypeName.value = tool.tool || "";
  els.toolTypeValue.value = toolType;
  els.toolTypeDialogTitle.textContent = `修改层级：${tool.name || tool.tool}`;
  els.toolTypeDialogHint.textContent = `${tool.service} / ${tool.tool}`;
  showToolTypeDialog();
  els.toolTypeValue.focus();
}

async function saveToolTypeFromDialog(event) {
  event.preventDefault();
  const serviceKey = els.toolTypeService.value;
  const toolName = els.toolTypeName.value;
  const toolType = els.toolTypeValue.value;
  if (!serviceKey || !toolName) {
    return;
  }
  const saved = await saveToolType(serviceKey, toolName, toolType);
  if (saved) {
    closeToolTypeDialog();
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

async function saveToolType(serviceKey, toolName, toolType) {
  try {
    const tool = await apiRequest(
      `/capabilities/mcp-services/${encodeURIComponent(serviceKey)}/tools/${encodeURIComponent(toolName)}/type`,
      {
        method: "PUT",
        body: JSON.stringify({ tool_type: toolType }),
      }
    );
    await loadAllTools();
    showMessage(`已将 ${tool.tool} 配置为${TOOL_TYPE_LABELS[tool.tool_type] || tool.tool_type}。`);
    return true;
  } catch (error) {
    showMessage(error.message, "error");
    return false;
  }
}

function filteredTools() {
  const serviceFilter = els.toolServiceFilter.value;
  const typeFilter = els.toolTypeFilter.value;
  return allTools.filter((tool) => {
    if (serviceFilter && tool.service !== serviceFilter) {
      return false;
    }
    if (typeFilter && tool.tool_type !== typeFilter) {
      return false;
    }
    return true;
  });
}

function renderToolsTable() {
  const tools = filteredTools();
  const serviceFilter = els.toolServiceFilter.value;
  const typeFilter = els.toolTypeFilter.value;
  selectedServiceKey = serviceFilter;
  writeRoute("tools", serviceFilter, "replace", typeFilter);
  els.selectedServiceHint.textContent = serviceFilter
    ? `${serviceFilter} · ${tools.length} 个工具`
    : `全部服务 · ${tools.length} 个工具`;
  if (tools.length === 0) {
    els.toolsTableBody.innerHTML = '<tr><td colspan="6" class="empty">没有匹配的工具。</td></tr>';
    return;
  }
  els.toolsTableBody.innerHTML = tools
    .map((tool) => {
      const service = escapeHtml(tool.service);
      const toolName = escapeHtml(tool.tool);
      return `
        <tr>
          <td>
            <span class="service-name">${escapeHtml(tool.name || tool.tool)}</span>
            <span class="service-key">${toolName}</span>
          </td>
          <td><span class="service-key">${service}</span></td>
          <td class="tool-level-cell">${renderToolLayer(tool.tool_type)}</td>
          <td>${renderTags(tool.tags)}</td>
          <td class="tool-description-cell">${escapeHtml(tool.description || "未填写描述。")}</td>
          <td class="tool-config-cell">
            <button type="button" data-action="edit-tool-type" data-service="${service}" data-tool="${toolName}">修改</button>
          </td>
        </tr>
      `;
    })
    .join("");
}

async function loadAllTools() {
  if (services.length === 0) {
    await loadServices({ preserveMessage: true });
    if (services.length === 0) {
      els.toolsTableBody.innerHTML = '<tr><td colspan="6" class="empty">尚未登记 MCP 服务。</td></tr>';
      renderToolFilters();
      return;
    }
  }
  renderToolFilters();
  els.toolsTableBody.innerHTML = '<tr><td colspan="6" class="empty">正在读取工具...</td></tr>';
  try {
    const serviceFilter = els.toolServiceFilter.value;
    const targetServices = services.filter((service) => !serviceFilter || service.service_key === serviceFilter);
    const results = await Promise.all(
      targetServices.map(async (service) => {
        try {
          const tools = await apiRequest(`/capabilities/mcp-services/${encodeURIComponent(service.service_key)}/tools`, {
            method: "GET",
          });
          return tools.map((tool) => ({ ...tool, service_name: service.name }));
        } catch (_error) {
          return [];
        }
      })
    );
    allTools = results.flat();
    renderToolsTable();
  } catch (error) {
    els.toolsTableBody.innerHTML = '<tr><td colspan="6" class="empty">工具读取失败。</td></tr>';
    showMessage(error.message, "error");
  }
}

async function loadTools(serviceKey = selectedServiceKey) {
  selectedServiceKey = serviceKey || "";
  if (els.toolServiceFilter.value !== selectedServiceKey) {
    els.toolServiceFilter.value = selectedServiceKey;
  }
  await loadAllTools();
}

document.querySelectorAll(".nav-item[data-view]").forEach((button) => {
  button.addEventListener("click", () => navigateTo(button.dataset.view));
});

els.form.addEventListener("submit", saveService);
els.profileForm.addEventListener("submit", saveProfile);
els.profileRulesForm.addEventListener("submit", saveProfileRules);
els.toolTypeForm.addEventListener("submit", saveToolTypeFromDialog);
els.openServiceDialog.addEventListener("click", () => openServiceForm());
els.closeServiceDialog.addEventListener("click", closeServiceDialog);
els.cancelServiceDialog.addEventListener("click", closeServiceDialog);
els.serviceDialog.addEventListener("click", (event) => {
  if (event.target === els.serviceDialog) {
    closeServiceDialog();
  }
});
els.openProfileDialog.addEventListener("click", openProfileForm);
els.closeProfileDialog.addEventListener("click", closeProfileDialog);
els.cancelProfileDialog.addEventListener("click", closeProfileDialog);
els.profileDialog.addEventListener("click", (event) => {
  if (event.target === els.profileDialog) {
    closeProfileDialog();
  }
});
els.closeProfileRulesDialog.addEventListener("click", closeProfileRulesDialog);
els.cancelProfileRulesDialog.addEventListener("click", closeProfileRulesDialog);
els.profileRulesDialog.addEventListener("click", (event) => {
  if (event.target === els.profileRulesDialog) {
    closeProfileRulesDialog();
  }
});
els.closeToolTypeDialog.addEventListener("click", closeToolTypeDialog);
els.cancelToolTypeDialog.addEventListener("click", closeToolTypeDialog);
els.toolTypeDialog.addEventListener("click", (event) => {
  if (event.target === els.toolTypeDialog) {
    closeToolTypeDialog();
  }
});
els.toolServiceFilter.addEventListener("change", () => {
  selectedServiceKey = els.toolServiceFilter.value;
  writeRoute("tools", selectedServiceKey, "replace", els.toolTypeFilter.value);
  loadAllTools();
});
els.toolTypeFilter.addEventListener("change", () => {
  renderToolsTable();
});
els.catalogTable.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-action='catalog-tools']");
  if (!button) {
    return;
  }
  navigateTo("tools", button.dataset.service);
});
els.servicesTable.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) {
    return;
  }
  const serviceKey = button.dataset.service;
  const service = services.find((item) => item.service_key === serviceKey);
  if (button.dataset.action === "select") {
    navigateTo("tools", serviceKey);
  } else if (button.dataset.action === "edit" && service) {
    openServiceForm(service);
  } else if (button.dataset.action === "sync") {
    syncService(serviceKey);
  } else if (button.dataset.action === "status") {
    setServiceStatus(serviceKey, button.dataset.status);
  }
});

els.profilesTable.addEventListener("click", (event) => {
  const button = event.target.closest('button[data-action="profile-rules"]');
  if (!button) {
    return;
  }
  openProfileRulesDialog(button.dataset.profile);
});

els.toolsTableBody.addEventListener("click", (event) => {
  const button = event.target.closest('button[data-action="edit-tool-type"]');
  if (!button) {
    return;
  }
  const serviceKey = button.dataset.service;
  const toolName = button.dataset.tool;
  const tool = allTools.find((item) => item.service === serviceKey && item.tool === toolName);
  if (!tool) {
    return;
  }
  openToolTypeDialog(tool);
});

window.loadServices = loadServices;
window.loadCatalog = loadCatalog;
window.loadProfiles = loadProfiles;
window.loadLogs = loadLogs;

window.addEventListener("popstate", () => {
  const route = routeFromLocation();
  selectedServiceKey = route.service;
  els.toolTypeFilter.value = route.type;
  setView(route.view, { preserveRoute: true });
});

async function initialize() {
  renderClaudeConfig();
  await loadServices({ preserveMessage: true });
  const route = routeFromLocation();
  selectedServiceKey = route.service;
  els.toolTypeFilter.value = route.type;
  writeRoute(route.view, route.service, "replace", route.type);
  setView(route.view, { preserveRoute: true });
}

initialize();
