let dashboardState = { initialized: false, refreshId: null, logRefreshId: null };

function renderDashboard(d) {
  const card = (label, value, sub = "") => `<div class="card"><div class="text-xs text-slate-500">${label}</div><div class="text-2xl font-semibold">${value}</div><div class="text-xs text-slate-500">${sub}</div></div>`;
  const disk = $("#dash-cards");
  if (disk) {
    disk.innerHTML = [
      card("Mounts", d.mounts.total, `${d.mounts.mounted} mounted · ${d.mounts.healthy} healthy`),
      card("Persistent (fstab)", d.fstab_entries),
      card("Sync jobs", d.sync_jobs),
      card("Media jobs", d.media_jobs),
      card("Rclone remotes", d.rclone_remotes),
    ].join("");
  }

  const toGB = (b) => (b / 1024 ** 3).toFixed(1);
  const dashDisks = $("#dash-disks");
  if (dashDisks) {
    dashDisks.innerHTML = (d.disks || []).map((x) => `<div class="disk-item"><div class="disk-row"><span class="disk-path font-mono text-slate-400">${window.escapeHtml ? window.escapeHtml(x.path) : x.path}</span><span class="disk-pct font-mono text-xs">${x.used_pct}%</span></div><div class="progress-track"><div class="progress-fill ${x.used_pct > 90 ? "bg-red-500" : x.used_pct > 70 ? "bg-amber-500" : "bg-accent"}" style="width:${x.used_pct}%"></div></div><div class="disk-details"><span class="disk-used font-mono text-xs text-slate-400">${toGB(x.total - x.free)} GB used</span><span class="disk-free font-mono text-xs text-slate-400">${toGB(x.free)} GB free</span></div></div>`).join("") || "—";
  }

  const dashMounts = $("#dash-mounts");
  if (dashMounts) {
    dashMounts.innerHTML = (d.mount_usage || []).map((x) => `<div class="disk-item"><div class="disk-row"><span class="disk-path font-mono text-slate-400">${x.name}</span><span class="disk-pct font-mono text-xs">${x.type.toUpperCase()}</span><span class="disk-pct font-mono text-xs">${x.used_pct}%</span></div><div class="disk-path font-mono text-xs text-slate-500 mb-1">${x.mount_point}</div><div class="progress-track"><div class="progress-fill ${x.used_pct > 90 ? "bg-red-500" : x.used_pct > 70 ? "bg-amber-500" : "bg-accent"}" style="width:${x.used_pct}%"></div></div><div class="disk-details"><span class="disk-used font-mono text-xs text-slate-400">${toGB(x.total - x.free)} GB used</span><span class="disk-free font-mono text-xs text-slate-400">${toGB(x.free)} GB free</span></div></div>`).join("") || "<div class='text-xs text-slate-400 p-4 text-center'>No mounted network mounts</div>";
  }

  const runs = $("#dash-runs");
  if (runs) {
    runs.innerHTML = (d.recent_runs || []).map((r) => `<tr><td>${r.kind}</td><td class="font-mono text-xs">${r.job_id}</td>
      <td>${r.status === "ok" ? '<span class="badge ok">ok</span>' : `<span class="badge bad">${r.status}</span>`}</td><td>${new Date(r.finished_at).toLocaleString()}</td><td class="text-xs">${r.message}</td></tr>`).join("") || `<tr><td colspan="5" class="text-slate-400">No runs yet</td></tr>`;
  }

  renderSysMetrics(d.system || {});
}

function renderSysMetrics(sys) {
  const sysUptime = $("#sys-uptime");
  if (sysUptime) sysUptime.textContent = sys.uptime || "—";
  const sysCpu = $("#sys-cpu");
  if (sysCpu) sysCpu.textContent = (sys.cpu && sys.cpu.used_pct !== undefined) ? `${sys.cpu.used_pct}%` : "—";
  const sysMem = $("#sys-mem");
  if (sysMem) sysMem.textContent = (sys.memory && sys.memory.used_pct !== undefined) ? `${sys.memory.used_pct}%` : "—";
  const sysProcs = $("#sys-procs");
  if (sysProcs) sysProcs.textContent = sys.process_count !== undefined ? sys.process_count : "—";

  const cpuBar = $("#sys-cpu-bar");
  const memBar = $("#sys-mem-bar");
  const cpuPct = $("#sys-cpu-pct");
  const memPct = $("#sys-mem-pct");
  if (sys.cpu && cpuBar) {
    const p = sys.cpu.used_pct;
    cpuBar.style.width = p + "%";
    cpuBar.className = "progress-fill " + (p > 90 ? "bg-red-500" : p > 70 ? "bg-amber-500" : "bg-accent");
    if (cpuPct) cpuPct.textContent = p + "%";
  }
  if (sys.memory && memBar) {
    const p = sys.memory.used_pct;
    memBar.style.width = p + "%";
    memBar.className = "progress-fill " + (p > 90 ? "bg-red-500" : p > 70 ? "bg-amber-500" : "bg-emerald");
    if (memPct) memPct.textContent = p + "%";
  }

  const procsBody = $("#sys-procs-body");
  if (procsBody) {
    const procs = sys.processes || [];
    procsBody.innerHTML = procs.map((p) => `<tr><td class="font-mono text-xs">${p.pid}</td><td class="font-mono text-xs">${p.name}</td><td class="font-mono text-xs">${p.state}</td></tr>`).join("") || `<tr><td colspan="3" class="text-slate-400 text-xs">No processes</td></tr>`;
  }
}

async function initLogsPanel() {
  const d = await shared.api("GET", "/api/logs");
  const sel = $("#log-name");
  if (!sel) return;
  sel.innerHTML = d.logs.map((n) => `<option value="${n}">${n}</option>`).join("");
  if (d.logs.length) loadLog(d.logs[0]);
}

async function loadLog(name) {
  if (!name) {
    const sel = $("#log-name");
    if (sel) name = sel.value;
    if (!name) return;
  }
  const d = await shared.api("GET", `/api/logs/${encodeURIComponent(name)}?lines=${$("#log-lines").value}&level=${$("#log-level").value}`);
  const box = $("#log-out");
  if (box) { box.textContent = d.lines.join("\n") || "(empty)"; box.scrollTop = box.scrollHeight; }
}

export async function init(shared) {
  if (dashboardState.initialized) return;
  dashboardState.initialized = true;

  async function refreshDashboard() {
    try {
      const d = await shared.api("GET", "/api/dashboard");
      renderDashboard(d);
    } catch (err) { /* ignore */ }
  }

  const logName = $("#log-name");
  if (logName) logName.addEventListener("change", (e) => loadLog(e.target.value));
  const logLevel = $("#log-level");
  if (logLevel) logLevel.addEventListener("change", () => loadLog());
  const logRefresh = $("#log-refresh");
  if (logRefresh) logRefresh.addEventListener("click", () => loadLog());

  dashboardState.refreshId = setInterval(() => {
    if (shared.getActiveTab() === "dashboard") refreshDashboard();
  }, 5000);

  dashboardState.logRefreshId = setInterval(() => {
    const follow = $("#log-follow");
    if (shared.getActiveTab() === "dashboard" && follow && follow.checked) loadLog();
  }, 3000);

  const d = await shared.api("GET", "/api/dashboard");
  await initLogsPanel();
  renderDashboard(d);
}

export async function teardown() {
  if (dashboardState.refreshId) clearInterval(dashboardState.refreshId);
  if (dashboardState.logRefreshId) clearInterval(dashboardState.logRefreshId);
  dashboardState.refreshId = null;
  dashboardState.logRefreshId = null;
}

window.__tabRefreshers.dashboard = () => init(window.__tabRefreshers.shared || globalThis.__shared || {});









































































































































"},{