const capabilityState = {
  server: [],
  client: [],
};

const setupEls = {
  status: document.querySelector("#setupStatus"),
  toast: document.querySelector("#toast"),
  result: document.querySelector("#setupResult"),
  resultCount: document.querySelector("#resultCount"),
  serverName: document.querySelector("#serverName"),
  serverIp: document.querySelector("#serverIp"),
  serverOs: document.querySelector("#serverOs"),
  serverArch: document.querySelector("#serverArch"),
  serverLogin: document.querySelector("#serverLogin"),
  serverPassword: document.querySelector("#serverPassword"),
  serverSshPort: document.querySelector("#serverSshPort"),
  serverFolder: document.querySelector("#serverFolder"),
  controllerPort: document.querySelector("#controllerPort"),
  agentPort: document.querySelector("#agentPort"),
  vhHubPort: document.querySelector("#vhHubPort"),
  vhSslPort: document.querySelector("#vhSslPort"),
  useSsl: document.querySelector("#useSsl"),
  useClientCerts: document.querySelector("#useClientCerts"),
  clients: document.querySelector("#setupClients"),
  addClient: document.querySelector("#addClientButton"),
  loadConfig: document.querySelector("#loadConfigButton"),
  saveConfig: document.querySelector("#saveConfigButton"),
  plan: document.querySelector("#planButton"),
  dryRun: document.querySelector("#dryRunButton"),
  run: document.querySelector("#runButton"),
};

async function setupApi(path, payload) {
  const response = await fetch(path, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || `HTTP ${response.status}`);
  }
  return data;
}

async function setupGet(path) {
  const response = await fetch(path);
  const data = await response.json();
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || `HTTP ${response.status}`);
  }
  return data;
}

async function loadCapabilities() {
  const response = await fetch("/api/setup/capabilities");
  const data = await response.json();
  capabilityState.server = data.targets.server || [];
  capabilityState.client = data.targets.client || [];
  fillTargetSelects("server", setupEls.serverOs, setupEls.serverArch);
  addClientCard({
    id: "windows-pc",
    name: "Windows PC",
    os: "windows",
    arch: "x86_64",
    folder: "C:/vh-control",
  });
}

function fillTargetSelects(role, osSelect, archSelect, preferredOs, preferredArch) {
  const items = capabilityState[role];
  const osValues = [...new Set(items.map((item) => item.os))];
  osSelect.innerHTML = osValues.map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(osLabel(value))}</option>`).join("");
  if (preferredOs && osValues.includes(preferredOs)) osSelect.value = preferredOs;
  refreshArchSelect(role, osSelect, archSelect, preferredArch);
  osSelect.addEventListener("change", () => refreshArchSelect(role, osSelect, archSelect));
}

function refreshArchSelect(role, osSelect, archSelect, preferredArch) {
  const archValues = capabilityState[role]
    .filter((item) => item.os === osSelect.value)
    .map((item) => item.arch);
  archSelect.innerHTML = archValues.map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(archLabel(role, osSelect.value, value))}</option>`).join("");
  if (preferredArch && archValues.includes(preferredArch)) archSelect.value = preferredArch;
}

function addClientCard(defaults = {}) {
  const index = setupEls.clients.children.length + 1;
  const card = document.createElement("article");
  card.className = "client-form";
  card.innerHTML = `
    <div class="client-form-head">
      <strong>Client ${index}</strong>
      <button type="button" class="remove-client" title="Entfernen">×</button>
    </div>
    <div class="form-grid">
      <label>ID <input data-field="id" value="${escapeHtml(defaults.id || `client-${index}`)}" autocomplete="off"></label>
      <label>Name <input data-field="name" value="${escapeHtml(defaults.name || `Client ${index}`)}" autocomplete="off"></label>
      <label>IP oder Host <input data-field="ip" value="${escapeHtml(defaults.ip || "")}" autocomplete="off"></label>
      <label>OS <select data-field="os"></select></label>
      <label>Icon / OS-Variante <select data-field="os_variant">
        <option value="">Automatisch</option>
        <option value="windows">Windows</option>
        <option value="linux">Linux</option>
        <option value="debian">Debian</option>
        <option value="arch">Arch / CachyOS</option>
        <option value="ubuntu">Ubuntu</option>
        <option value="raspberry">Raspberry Pi OS</option>
      </select></label>
      <label>Binary / Architektur <select data-field="arch"></select></label>
      <label>SSH Login <input data-field="login" value="${escapeHtml(defaults.login || "")}" autocomplete="username"></label>
      <label>SSH Passwort <input data-field="password" type="password" autocomplete="current-password"></label>
      <label>SSH Port <input data-field="ssh_port" type="number" min="1" max="65535" value="${Number(defaults.ssh_port || defaults.sshPort || 22)}"></label>
      <label>Ordner <input data-field="install_dir" value="${escapeHtml(defaults.install_dir || defaults.installDir || defaults.folder || "/opt/vh-control")}" autocomplete="off"></label>
      <label>Agent Port <input data-field="agent_port" type="number" min="1" max="65535" value="${Number(defaults.agent_port || defaults.agentPort || setupEls.agentPort.value) || 9443}"></label>
    </div>
  `;
  const osSelect = card.querySelector('[data-field="os"]');
  const archSelect = card.querySelector('[data-field="arch"]');
  const variantSelect = card.querySelector('[data-field="os_variant"]');
  fillTargetSelects("client", osSelect, archSelect, defaults.os, defaults.arch);
  variantSelect.value = defaults.os_variant || defaults.osVariant || defaults.distro || "";
  osSelect.addEventListener("change", () => {
    const folder = card.querySelector('[data-field="install_dir"]');
    if (!folder.value || folder.value === "/opt/vh-control" || folder.value === "C:/vh-control") {
      folder.value = osSelect.value === "windows" ? "C:/vh-control" : "/opt/vh-control";
    }
  });
  card.querySelector(".remove-client").addEventListener("click", () => {
    card.remove();
    renderResult({steps: []});
  });
  setupEls.clients.appendChild(card);
}

function collectPayload(extra = {}) {
  return {
    server_name: setupEls.serverName.value.trim(),
    controller_port: Number(setupEls.controllerPort.value),
    agent_port: Number(setupEls.agentPort.value),
    vh_hub_port: Number(setupEls.vhHubPort.value),
    vh_ssl_port: Number(setupEls.vhSslPort.value),
    use_ssl: setupEls.useSsl.checked,
    use_client_certs: setupEls.useClientCerts.checked,
    server: {
      id: "server",
      name: setupEls.serverName.value.trim() || "server",
      os: setupEls.serverOs.value,
      arch: setupEls.serverArch.value,
      ip: setupEls.serverIp.value.trim(),
      login: setupEls.serverLogin.value.trim(),
      password: setupEls.serverPassword.value,
      ssh_port: Number(setupEls.serverSshPort.value),
      install_dir: setupEls.serverFolder.value.trim(),
    },
    clients: [...setupEls.clients.querySelectorAll(".client-form")].map((card) => ({
      id: fieldValue(card, "id"),
      name: fieldValue(card, "name"),
      os: fieldValue(card, "os"),
      arch: fieldValue(card, "arch"),
      os_variant: fieldValue(card, "os_variant"),
      ip: fieldValue(card, "ip"),
      login: fieldValue(card, "login"),
      password: fieldValue(card, "password"),
      ssh_port: Number(fieldValue(card, "ssh_port")),
      install_dir: fieldValue(card, "install_dir"),
      agent_port: Number(fieldValue(card, "agent_port")),
    })),
    ...extra,
  };
}

function fieldValue(card, field) {
  return card.querySelector(`[data-field="${field}"]`).value.trim();
}

async function submitSetup(path, extra = {}) {
  setBusy(true);
  try {
    const data = await setupApi(path, collectPayload(extra));
    renderResult(data);
    setupEls.status.textContent = data.dry_run ? "Testlauf abgeschlossen" : "Plan erstellt";
    if (path.endsWith("/run") && !data.dry_run) setupEls.status.textContent = "Setup abgeschlossen";
    showSetupToast(setupEls.status.textContent);
  } catch (error) {
    setupEls.status.textContent = "Fehler";
    showSetupToast(error.message);
    renderError(error.message);
  } finally {
    setBusy(false);
  }
}

async function saveSetupConfig() {
  setBusy(true);
  try {
    const data = await setupApi("/api/setup/config", collectPayload());
    setupEls.status.textContent = "Config gespeichert";
    showSetupToast(`Gespeichert: ${data.path}`);
  } catch (error) {
    setupEls.status.textContent = "Fehler";
    showSetupToast(error.message);
    renderError(error.message);
  } finally {
    setBusy(false);
  }
}

async function loadSetupConfig() {
  setBusy(true);
  try {
    const data = await setupGet("/api/setup/config");
    if (!data.exists) {
      showSetupToast("Keine gespeicherte Config gefunden");
      return;
    }
    applySetupConfig(data.config || {});
    setupEls.status.textContent = "Config geladen";
    showSetupToast("Config geladen. SSH-Passwoerter bitte neu eingeben.");
    renderResult({steps: []});
  } catch (error) {
    setupEls.status.textContent = "Fehler";
    showSetupToast(error.message);
    renderError(error.message);
  } finally {
    setBusy(false);
  }
}

function applySetupConfig(config) {
  setupEls.serverName.value = config.server_name || config.serverName || config.server?.name || "VirtualHere Server";
  setupEls.controllerPort.value = Number(config.controller_port || config.controllerPort || 8080);
  setupEls.agentPort.value = Number(config.agent_port || config.agentPort || 9443);
  setupEls.vhHubPort.value = Number(config.vh_hub_port || config.vhHubPort || 7575);
  setupEls.vhSslPort.value = Number(config.vh_ssl_port || config.vhSslPort || 7574);
  setupEls.useSsl.checked = config.use_ssl ?? config.useSsl ?? true;
  setupEls.useClientCerts.checked = config.use_client_certs ?? config.useClientCerts ?? false;

  const server = config.server || {};
  setupEls.serverIp.value = server.ip || server.host || "";
  setupEls.serverLogin.value = server.login || server.username || "";
  setupEls.serverPassword.value = "";
  setupEls.serverSshPort.value = Number(server.ssh_port || server.sshPort || 22);
  setupEls.serverFolder.value = server.install_dir || server.installDir || "/opt/vh-control";
  setTargetSelects("server", setupEls.serverOs, setupEls.serverArch, server.os || server.os_type || server.type, server.arch);

  setupEls.clients.innerHTML = "";
  const clients = config.clients && config.clients.length ? config.clients : [];
  for (const client of clients) {
    addClientCard({
      ...client,
      password: "",
      folder: client.install_dir || client.installDir,
    });
  }
  if (!setupEls.clients.children.length) addClientCard();
}

function setTargetSelects(role, osSelect, archSelect, osValue, archValue) {
  const osValues = [...new Set(capabilityState[role].map((item) => item.os))];
  if (osValue && osValues.includes(osValue)) osSelect.value = osValue;
  refreshArchSelect(role, osSelect, archSelect, archValue);
}

function osLabel(value) {
  const labels = {
    openwrt: "OpenWrt",
    linux: "Linux",
    windows: "Windows",
  };
  return labels[value] || value;
}

function archLabel(role, osValue, archValue) {
  const download = capabilityState[role].find((item) => item.os === osValue && item.arch === archValue);
  const binary = download && download.binary ? ` - ${download.binary}` : "";
  if (osValue === "openwrt" && archValue === "armhf") return `ARM 32-bit${binary}`;
  if (osValue === "openwrt" && archValue === "aarch64") return `ARM 64-bit${binary}`;
  if (archValue === "armhf") return `ARM 32-bit${binary}`;
  if (archValue === "aarch64") return `ARM 64-bit${binary}`;
  if (archValue === "x86_64") return `x86 64-bit${binary}`;
  return `${archValue}${binary}`;
}

function renderResult(data) {
  const steps = data.steps || [];
  const log = data.log || [];
  setupEls.resultCount.textContent = String(steps.length || log.length);
  if (!steps.length && !log.length) {
    setupEls.result.innerHTML = `<div class="empty">Noch kein Plan erstellt</div>`;
    return;
  }
  const stepHtml = steps.map((step) => `
    <div class="setup-step">
      <span>${escapeHtml(step.target)}</span>
      <strong>${escapeHtml(step.label)}</strong>
    </div>
  `).join("");
  const logHtml = log.map((item) => `
    <div class="setup-step ${item.ok ? "ok" : "bad"}">
      <span>${escapeHtml(item.target)}</span>
      <strong>${escapeHtml(item.message)}</strong>
    </div>
  `).join("");
  setupEls.result.innerHTML = stepHtml + logHtml;
}

function renderError(message) {
  setupEls.resultCount.textContent = "1";
  setupEls.result.innerHTML = `
    <div class="setup-step bad">
      <span>setup</span>
      <strong>${escapeHtml(message)}</strong>
    </div>
  `;
}

function setBusy(isBusy) {
  document.body.classList.toggle("setup-busy", Boolean(isBusy));
  for (const button of [setupEls.plan, setupEls.dryRun, setupEls.run, setupEls.addClient, setupEls.loadConfig, setupEls.saveConfig]) {
    button.disabled = isBusy;
  }
  setupEls.status.textContent = isBusy ? "Arbeite..." : setupEls.status.textContent;
}

function showSetupToast(message) {
  setupEls.toast.textContent = message;
  setupEls.toast.classList.add("visible");
  window.clearTimeout(showSetupToast.timer);
  showSetupToast.timer = window.setTimeout(() => setupEls.toast.classList.remove("visible"), 3200);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

setupEls.addClient.addEventListener("click", () => addClientCard());
setupEls.loadConfig.addEventListener("click", loadSetupConfig);
setupEls.saveConfig.addEventListener("click", saveSetupConfig);
setupEls.plan.addEventListener("click", () => submitSetup("/api/setup/plan"));
setupEls.dryRun.addEventListener("click", () => submitSetup("/api/setup/run", {dry_run: true}));
setupEls.run.addEventListener("click", () => submitSetup("/api/setup/run"));
loadCapabilities().catch((error) => renderError(error.message));
