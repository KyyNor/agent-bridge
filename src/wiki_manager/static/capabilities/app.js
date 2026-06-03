const API_HEADERS = {
  "Content-Type": "application/json",
  "X-Wiki-User": "root",
};

let selectedServiceKey = "";
let services = [];

const els = {
  form: document.getElementById("serviceForm"),
  servicesTable: document.getElementById("servicesTable"),
  messageArea: document.getElementById("messageArea"),
  reloadTools: document.getElementById("reloadTools"),
  refreshServices: document.getElementById("refreshServices"),
  selectedServiceHint: document.getElementById("selectedServiceHint"),
  toolsList: document.getElementById("toolsList"),
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
    return '<span class="empty">None</span>';
  }
  return tags.map((tag) => `<span class="badge">${escapeHtml(tag)}</span>`).join("");
}

function renderServices() {
  if (services.length === 0) {
    els.servicesTable.innerHTML = '<tr><td colspan="5" class="empty">No MCP services registered.</td></tr>';
    return;
  }

  els.servicesTable.innerHTML = services
    .map((service) => {
      const isEnabled = service.status === "enabled";
      const nextStatus = isEnabled ? "disabled" : "enabled";
      const statusText = isEnabled ? "Disable" : "Enable";
      return `
        <tr>
          <td>
            <span class="service-name">${escapeHtml(service.name)}</span>
            <span class="service-key">${escapeHtml(service.service_key)}</span>
          </td>
          <td>${escapeHtml(service.endpoint_url)}</td>
          <td><span class="badge ${escapeHtml(service.status)}">${escapeHtml(service.status)}</span></td>
          <td>${renderTags(service.tags)}</td>
          <td>
            <div class="row-actions">
              <button type="button" data-action="select" data-service="${escapeHtml(service.service_key)}">Tools</button>
              <button type="button" data-action="edit" data-service="${escapeHtml(service.service_key)}">Edit</button>
              <button type="button" data-action="sync" data-service="${escapeHtml(service.service_key)}">Sync</button>
              <button type="button" data-action="status" data-status="${nextStatus}" data-service="${escapeHtml(service.service_key)}">${statusText}</button>
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
  els.servicesTable.innerHTML = '<tr><td colspan="5" class="empty">Loading services...</td></tr>';
  try {
    services = await apiRequest("/capabilities/mcp-services", { method: "GET" });
    renderServices();
    if (selectedServiceKey && services.some((service) => service.service_key === selectedServiceKey)) {
      await loadTools(selectedServiceKey);
    }
  } catch (error) {
    els.servicesTable.innerHTML = '<tr><td colspan="5" class="empty">Unable to load services.</td></tr>';
    showMessage(error.message, "error");
  }
}

function parseHeaders() {
  const raw = els.headersJson.value.trim();
  if (!raw) {
    return {};
  }
  const parsed = JSON.parse(raw);
  if (parsed === null || Array.isArray(parsed) || typeof parsed !== "object") {
    throw new Error("Headers JSON must be an object.");
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
  els.headersJson.placeholder = "Leave blank to keep existing headers.";
  els.description.value = service.description || "";
  els.tags.value = (service.tags || []).join(", ");
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
    showMessage(`Saved ${payload.service_key}.`);
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
    showMessage(`${serviceKey} is now ${status}.`);
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
    showMessage(`Synced ${result.tool_count} tools from ${serviceKey}.`);
  } catch (error) {
    await loadServices({ preserveMessage: true });
    showMessage(error.message, "error");
  }
}

async function loadTools(serviceKey = selectedServiceKey) {
  if (!serviceKey) {
    els.reloadTools.disabled = true;
    els.selectedServiceHint.textContent = "Select a service to inspect synced tools.";
    els.toolsList.className = "tools-list empty";
    els.toolsList.textContent = "No service selected.";
    return;
  }

  selectedServiceKey = serviceKey;
  els.reloadTools.disabled = false;
  els.selectedServiceHint.textContent = serviceKey;
  els.toolsList.className = "tools-list empty";
  els.toolsList.textContent = "Loading tools...";

  try {
    const tools = await apiRequest(`/capabilities/mcp-services/${encodeURIComponent(serviceKey)}/tools`, {
      method: "GET",
    });
    if (tools.length === 0) {
      els.toolsList.className = "tools-list empty";
      els.toolsList.textContent = "No active tools. Run sync after registering the service.";
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
            <p>${escapeHtml(tool.description || "No description.")}</p>
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

els.form.addEventListener("submit", saveService);
els.refreshServices.addEventListener("click", loadServices);
els.reloadTools.addEventListener("click", () => loadTools());
els.servicesTable.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) {
    return;
  }
  const serviceKey = button.dataset.service;
  const service = services.find((item) => item.service_key === serviceKey);
  if (button.dataset.action === "select") {
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
loadServices();
