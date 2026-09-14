const state = {
  clients: [],
  devices: [],
  selectedDevice: null,
  busy: false,
  usbCursor: false,
  collapsed: {},
};

const els = {
  statusText: document.querySelector("#statusText"),
  deviceCount: document.querySelector("#deviceCount"),
  clientCount: document.querySelector("#clientCount"),
  devices: document.querySelector("#devices"),
  clients: document.querySelector("#clients"),
  refreshButton: document.querySelector("#refreshButton"),
  toast: document.querySelector("#toast"),
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: {"Content-Type": "application/json"},
    ...options,
  });
  const data = await response.json();
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || `HTTP ${response.status}`);
  }
  return data;
}

function showToast(message) {
  els.toast.textContent = message;
  els.toast.classList.add("visible");
  window.clearTimeout(showToast.timer);
  const timeout = message.length > 120 ? 9000 : 2800;
  showToast.timer = window.setTimeout(() => els.toast.classList.remove("visible"), timeout);
}

async function loadState() {
  if (state.busy) return;
  els.statusText.textContent = "Aktualisiere...";
  try {
    const data = await api("/api/state");
    state.clients = data.clients || [];
    state.devices = data.devices || [];
    render();
    const online = state.clients.filter((client) => client.online).length;
    els.statusText.textContent = `${online}/${state.clients.length} Clients online`;
  } catch (error) {
    els.statusText.textContent = "Status nicht erreichbar";
    showToast(error.message);
  }
}

function render() {
  els.deviceCount.textContent = String(state.devices.length);
  els.clientCount.textContent = String(state.clients.length);
  renderDevices();
  renderClients();
}

function renderDevices() {
  els.devices.innerHTML = "";
  if (!state.devices.length) {
    els.devices.innerHTML = renderGroup("usb", "USB Servers", `<div class="tree-row child empty-row"><span></span><span>Keine USB-Geraete gefunden</span></div>`);
    return;
  }

  const byHub = new Map();
  for (const device of state.devices) {
    const hub = device.hub || "VirtualHere Hub";
    if (!byHub.has(hub)) byHub.set(hub, []);
    byHub.get(hub).push(device);
  }

  const content = [...byHub.entries()].map(([hub, devices]) => {
    const hubId = hubIdFromDevices(devices);
    const rows = devices.map(renderServerDeviceRow).join("");
    const os = serverOsKind(hub);
    const key = `hub:${hub}`;
    const collapsed = Boolean(state.collapsed[key]);
    return `
      <div class="tree-node ${collapsed ? "collapsed" : ""}" data-node="${escapeHtml(key)}">
        <div class="tree-row hub-row" data-os="${escapeHtml(os)}">
          <span class="tree-toggle node-toggle" role="button" tabindex="0" aria-expanded="${collapsed ? "false" : "true"}">${collapsed ? "›" : "⌄"}</span>
          <span class="os-icon ${escapeHtml(os)}"></span>
          <span class="status-dot online"></span>
          <span class="row-label">${escapeHtml(hubLabel(hub, hubId))}</span>
        </div>
        <div class="tree-node-body">${rows}</div>
      </div>
    `;
  }).join("");
  els.devices.innerHTML = renderGroup("usb", "USB Servers", content);
  bindGroupToggles(els.devices);
  bindNodeToggles(els.devices);
  bindDeviceRows();
}

function renderClients() {
  els.clients.innerHTML = "";
  if (!state.clients.length) {
    els.clients.innerHTML = renderGroup("clients", "Clients", `<div class="tree-row child empty-row"><span></span><span>Keine Clients konfiguriert</span></div>`);
    return;
  }

  const groups = [
    {key: "desktop", title: "Desktop Clients", clients: state.clients.filter((client) => !isSingleBoardClient(client))},
    {key: "single-board", title: "Single Board Clients", clients: state.clients.filter(isSingleBoardClient)},
  ].filter((group) => group.clients.length);

  els.clients.innerHTML = groups.map((group) => renderGroup(group.key, group.title, group.clients.map(renderClientNode).join(""))).join("");
  bindGroupToggles(els.clients);
  bindNodeToggles(els.clients);
  bindClientRows();
}

function renderGroup(kind, title, content) {
  const collapsed = Boolean(state.collapsed[kind]);
  return `
    <section class="tree-group ${escapeHtml(kind)} ${collapsed ? "collapsed" : ""}" data-group="${escapeHtml(kind)}">
      <div class="tree-group-head" role="button" tabindex="0" aria-expanded="${collapsed ? "false" : "true"}">
        <span class="tree-toggle">${collapsed ? "›" : "⌄"}</span>
        <span class="group-icon"></span>
        <strong>${escapeHtml(title)}</strong>
      </div>
      <div class="tree-group-body">${content}</div>
    </section>
  `;
}

function renderServerDeviceRow(device) {
  const assigned = state.clients.find((client) => client.id === device.assigned_to);
  const selected = state.selectedDevice === device.address ? " selected" : "";
  const label = device.name || device.address;
  return `
    <div class="tree-row device-row child${selected}" draggable="true" data-address="${escapeHtml(device.address)}">
      <span class="indent"></span>
      <span class="device-icon">${deviceIcon(label)}</span>
      <span class="row-label">${escapeHtml(label)}</span>
      <span class="row-address">${escapeHtml(deviceAddressPart(device.address))}</span>
      <button type="button" class="classic-mini release" title="${assigned ? "Freigeben" : "Frei"}" ${assigned ? "" : "disabled"}>${assigned ? "Freigeben" : "Frei"}</button>
    </div>
  `;
}


function hubIdFromDevices(devices) {
  for (const device of devices) {
    const match = String(device.address || "").match(/(?:^|[-_])([A-Za-z]*\d+[A-Za-z0-9]*)\./);
    if (match) return match[1];
  }
  return "";
}

function hubLabel(hub, hubId) {
  return hubId ? `${hub} (${hubId})` : hub;
}

function deviceAddressPart(address) {
  const parts = String(address || "").split(".");
  return parts.length > 1 ? parts[parts.length - 1] : address;
}

function updateCursorMode() {
  document.body.classList.toggle("usb-cursor-active", Boolean(state.selectedDevice));
}

function renderClientNode(client) {
  const assignedDevices = state.devices.filter((device) => device.assigned_to === client.id);
  const os = clientOsKind(client);
  const key = `client:${client.id}`;
  const collapsed = Boolean(state.collapsed[key]);
  const rows = assignedDevices.length
    ? assignedDevices.map((device) => `
      <div class="tree-row assigned-device child2">
        <span class="indent"></span>
        <span class="device-icon">${deviceIcon(device.name || device.address)}</span>
        <span class="row-label">${escapeHtml(device.name || device.address)}</span>
      </div>
    `).join("")
    : `<div class="tree-row assigned-device child2 muted-row"><span class="indent"></span><span></span><span>Keine Zuweisung</span></div>`;
  return `
    <div class="tree-node ${collapsed ? "collapsed" : ""}" data-node="${escapeHtml(key)}">
      <div class="tree-row client-row ${client.online ? "online" : "offline"}" data-client-id="${escapeHtml(client.id)}" data-os="${escapeHtml(os)}">
        <span class="tree-toggle node-toggle" role="button" tabindex="0" aria-expanded="${collapsed ? "false" : "true"}">${collapsed ? "›" : "⌄"}</span>
        <span class="os-icon ${escapeHtml(os)}"></span>
        <span class="status-dot ${client.online ? "online" : "offline"}"></span>
        <span class="row-label">${escapeHtml(client.name)}</span>
        <span class="row-type">${escapeHtml(client.type || "client")}</span>
      </div>
      <div class="tree-node-body">${rows}</div>
    </div>
  `;
}

function bindGroupToggles(root) {
  root.querySelectorAll(".tree-group-head").forEach((head) => {
    const group = head.closest(".tree-group");
    const toggle = () => {
      const key = group.dataset.group;
      state.collapsed[key] = !state.collapsed[key];
      render();
    };
    head.addEventListener("click", toggle);
    head.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        toggle();
      }
    });
  });
}

function bindNodeToggles(root) {
  root.querySelectorAll(".node-toggle").forEach((toggleEl) => {
    const toggle = (event) => {
      event.preventDefault();
      event.stopPropagation();
      const node = toggleEl.closest(".tree-node");
      const key = node.dataset.node;
      state.collapsed[key] = !state.collapsed[key];
      render();
    };
    toggleEl.addEventListener("click", toggle);
    toggleEl.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") toggle(event);
    });
  });
}

function bindDeviceRows() {
  els.devices.querySelectorAll(".device-row").forEach((row) => {
    row.addEventListener("dragstart", (event) => {
      state.selectedDevice = row.dataset.address;
      event.dataTransfer.setData("text/plain", row.dataset.address);
      event.dataTransfer.effectAllowed = "move";
      render();
    });
    row.addEventListener("click", (event) => {
      if (event.target.classList.contains("release")) return;
      state.selectedDevice = state.selectedDevice === row.dataset.address ? null : row.dataset.address;
      updateCursorMode();
      render();
    });
    row.querySelector(".release").addEventListener("click", () => releaseDevice(row.dataset.address));
  });
}

function bindClientRows() {
  els.clients.querySelectorAll(".client-row").forEach((row) => {
    row.addEventListener("dragover", (event) => {
      event.preventDefault();
      row.classList.add("drop");
    });
    row.addEventListener("dragleave", () => row.classList.remove("drop"));
    row.addEventListener("drop", async (event) => {
      event.preventDefault();
      row.classList.remove("drop");
      const address = event.dataTransfer.getData("text/plain");
      if (address) await assignDevice(address, row.dataset.clientId);
    });
    row.addEventListener("click", async () => {
      if (state.selectedDevice) await assignDevice(state.selectedDevice, row.dataset.clientId);
    });
  });
}

function isSingleBoardClient(client) {
  const value = `${client.id || ""} ${client.name || ""} ${client.os_variant || ""}`.toLowerCase();
  return value.includes("raspberry") || value.includes(" pi") || value.includes("pizero") || value.includes("zero 2") || value.includes("openwrt") || value.includes("router");
}

function clientOsKind(client) {
  if (client.os_variant) return client.os_variant;
  const value = `${client.id || ""} ${client.name || ""} ${client.type || ""}`.toLowerCase();
  if (value.includes("raspberry") || value.includes("raspbian") || value.includes("pizero") || value.includes("zero 2")) return "raspberry";
  if (value.includes("arch") || value.includes("cachy")) return "arch";
  if (value.includes("ubuntu")) return "ubuntu";
  if (value.includes("debian")) return "debian";
  if (value.includes("windows") || value.includes("win")) return "windows";
  if (value.includes("linux")) return "linux";
  return "unknown-os";
}

function serverOsKind(hubName) {
  const value = String(hubName || "").toLowerCase();
  if (value.includes("openwrt") || value.includes("router") || value.includes("gl-")) return "openwrt";
  if (value.includes("raspberry") || value.includes("raspbian") || value.includes("pi ")) return "raspberry";
  if (value.includes("arch") || value.includes("cachy")) return "arch";
  if (value.includes("ubuntu")) return "ubuntu";
  if (value.includes("debian")) return "debian";
  if (value.includes("windows") || value.includes("win")) return "windows";
  if (value.includes("linux")) return "linux";
  return "server";
}

function deviceIcon(name) {
  const value = String(name || "").toLowerCase();
  if (value.includes("keyboard") || value.includes("mouse")) return "⌨";
  if (value.includes("receiver")) return "◌";
  if (value.includes("reader") || value.includes("card")) return "▤";
  return "◆";
}

async function assignDevice(deviceAddress, clientId) {
  const client = state.clients.find((item) => item.id === clientId);
  if (!client || !client.online) {
    showToast("Client ist offline");
    return;
  }
  state.busy = true;
  try {
    await api("/api/assign", {
      method: "POST",
      body: JSON.stringify({deviceAddress, clientId}),
    });
    state.selectedDevice = null;
    updateCursorMode();
    showToast("Geraet zugewiesen");
    await loadState();
  } catch (error) {
    showToast(error.message);
  } finally {
    state.busy = false;
  }
}

async function releaseDevice(deviceAddress) {
  state.busy = true;
  try {
    await api("/api/release", {
      method: "POST",
      body: JSON.stringify({deviceAddress}),
    });
    showToast("Geraet freigegeben");
    await loadState();
  } catch (error) {
    showToast(error.message);
  } finally {
    state.busy = false;
  }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

els.refreshButton.addEventListener("click", loadState);
updateCursorMode();
loadState();
window.setInterval(loadState, 10000);
