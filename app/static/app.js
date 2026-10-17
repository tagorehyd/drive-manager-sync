"use strict";
// Drive Manager single-page UI (vanilla JS, no build step).

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtTime = (iso) => (iso ? new Date(iso).toLocaleString() : "—");
const yes = (v, okText = "yes", badText = "no") => (v ? `<span class="badge ok">${okText}</span>` : `<span class="badge bad">${badText}</span>`);
const tri = (v) => (v === undefined || v === null ? `<span class="badge muted">?</span>` : yes(v));

function toast(msg, kind = "info") {
  const el = document.createElement("div");
  el.className = "toast";
  el.style.background = kind === "error" ? "#b91c1c" : kind === "ok" ? "#15803d" : "#334155";
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), kind === "error" ? 9000 : 4000);
}

async function api(method, url, body) {
  const opts = { method, headers: {}, credentials: "same-origin" };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(url, opts);
  if (res.status === 401) { location.href = "/login"; throw new Error("not authenticated"); }
  let data = {};
  try { data = await res.json(); } catch (_) { /* empty body */ }
  if (!res.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join("; ") : data.detail;
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return data;
}

async function act(btn, fn, okMsg) {
  if (btn) btn.disabled = true;
  try {
    const r = await fn();
    if (r && r.ok === false) toast((r.result && r.result.stderr) || r.error || r.stderr || "Action failed", "error");
    else if (okMsg) toast(okMsg, "ok");
    return r;
  } catch (e) {
    toast(e.message, "error");
  } finally {
    if (btn) btn.disabled = false;
  }
}

function formData(form) {
  const out = {};
  for (const el of form.elements) {
    if (!el.name) continue;
    out[el.name] = el.type === "checkbox" ? el.checked : el.type === "number" ? Number(el.value) : el.value;
  }
  return out;
}

function fillForm(form, data) {
  for (const el of form.elements) {
    if (!el.name || !(el.name in data)) continue;
    if (el.type === "checkbox") el.checked = !!data[el.name];
    else el.value = data[el.name] ?? "";
  }
}

// ------------------------------------------------------------ tabs
const loaders = {};
let currentTab = $("main").dataset.currentTab;

function showTab(tab, push = true) {
  stopSmbLogPoll();
  currentTab = tab;
  $$("section[data-section]").forEach((s) => s.classList.toggle("hidden", s.dataset.section !== tab));
  $$("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.tab === tab));
  if (push) history.pushState({ tab }, "", tab === "dashboard" ? "/" : `/${tab}`);
  if (loaders[tab]) Promise.resolve(loaders[tab]()).catch((e) => toast(e.message, "error"));
}
$$("#nav a").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); showTab(a.dataset.tab); }));
window.addEventListener("popstate", (e) => showTab((e.state && e.state.tab) || "dashboard", false));

// ------------------------------------------------------------ folder picker
const picker = { target: null, path: "", parent: null };

async function pickerLoad(path) {
  try {
    const d = await api("GET", `/api/browse?path=${encodeURIComponent(path || "")}`);
    picker.path = d.path; picker.parent = d.parent;
    $("#picker-path").textContent = d.path || "Roots";
    $("#picker-list").innerHTML = d.dirs.length
      ? d.dirs.map((x) => `<li class="px-3 py-2 hover:bg-slate-50 cursor-pointer" data-path="${esc(x.path)}">📁 ${esc(x.name)}</li>`).join("")
      : `<li class="px-3 py-2 text-slate-400">No sub-folders</li>`;
    $("#picker-select").disabled = !d.path;
    $("#picker-up").disabled = !d.path;
  } catch (e) { toast(e.message, "error"); }
}
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-browse]");
  if (!b) return;
  picker.target = $(b.dataset.browse);
  $("#picker").classList.remove("hidden");
  // Determine starting path based on field type and mount type
  let start = "";
  if (picker.target.name.startsWith("mf-")) {
    // Mount form fields - use mount type to determine default
    const mountType = $( "#mf-type" ).value || "nfs";
    if (picker.target.name === "mf-mp") {
      const nameEl = $( "#mf-name" );
      const name = nameEl ? nameEl.value.replace(/[^A-Za-z0-9_-]/g, "") : "new-mount";
      start = mountType === "rclone" ? `rclone:${name}` : `/mnt/${name}`;
    } else if (picker.target.name === "mf-source" || picker.target.name === "mf-dest") {
      start = mountType === "rclone" ? `rclone:` : `/mnt`;
    }
  } else {
    start = picker.target.value && picker.target.value.startsWith("/") ? picker.target.value : "";
  }
  pickerLoad(start).then(() => { if (start && !picker.path) pickerLoad(""); });
});
$("#picker-list").addEventListener("click", (e) => { const li = e.target.closest("li[data-path]"); if (li) pickerLoad(li.dataset.path); });
$("#picker-up").addEventListener("click", () => pickerLoad(picker.parent || ""));
$("#picker-close").addEventListener("click", () => $("#picker").classList.add("hidden"));
$("#picker-select").addEventListener("click", () => {
  if (picker.target && picker.path) { picker.target.value = picker.path; picker.target.dispatchEvent(new Event("change")); }
  $("#picker").classList.add("hidden");
});

// ------------------------------------------------------------ typed confirmation
function typedConfirm(title, text, word) {
  return new Promise((resolve) => {
    $("#confirm-title").textContent = title;
    $("#confirm-text").innerHTML = `${esc(text)}<br>Type <b class="font-mono">${esc(word)}</b> to confirm.`;
    const input = $("#confirm-input"), ok = $("#confirm-ok");
    input.value = ""; ok.disabled = true;
    $("#confirm").classList.remove("hidden");
    input.focus();
    input.oninput = () => { ok.disabled = input.value !== word; };
    const close = (v) => { $("#confirm").classList.add("hidden"); resolve(v); };
    $("#confirm-cancel").onclick = () => close(null);
    $("#confirm-form").onsubmit = (e) => { e.preventDefault(); if (input.value === word) close(input.value); };
  });
}

// ------------------------------------------------------------ import fstab modal
function openImportModal(mountpoint, device, fstype, options) {
  // Detect type from device and fstype
  let type = "nfs";
  if (fstype === "cifs" || fstype === "smb") type = "smb";
  else if (device.startsWith("//")) type = "smb";
  else if (device.startsWith("UUID=")) type = "nfs"; // treat UUID as nfs for now

  $("#import-mountpoint").value = mountpoint;
  $("#import-type").value = type;
  $("#import-name").value = mountpoint.split("/").pop() || "imported-mount";
  $("#import-host").value = "";
  $("#import-export").value = "";
  $("#import-share").value = "";
  $("#import-username").value = "";
  $("#import-password").value = "";
  $("#import-options").value = options || "";
  $("#import-auto-mount").checked = true;

  // Pre-fill based on device
  if (type === "nfs" && device.includes(":")) {
    const [host, exportPath] = device.split(":");
    $("#import-host").value = host;
    $("#import-export").value = exportPath;
  } else if (type === "smb" && device.startsWith("//")) {
    const parts = device.substring(2).split("/");
    $("#import-host").value = parts[0];
    $("#import-share").value = parts[1] || "";
  } else if (device.startsWith("UUID=") || device.startsWith("LABEL=")) {
    // Local filesystem - not importable as NFS/SMB
    toast("Local filesystem (ext4/xfs/etc) cannot be imported as NFS/SMB mount", "error");
    return;
  }

  // Show/hide fields based on type
  $$("#import-form .mf").forEach((el) => el.classList.toggle("hidden", !el.classList.contains(type)));

  $("#import-modal").classList.remove("hidden");
  $("#import-name").focus();
}
$("#import-cancel").onclick = () => $("#import-modal").classList.add("hidden");
$("#import-form").onsubmit = async (e) => {
  e.preventDefault();
  const data = formData(e.target);
  const mountpoint = data.mountpoint; delete data.mountpoint;
  const r = await act(e.submitter, () => api("POST", "/api/fstab/import", data), "Imported as managed mount");
  if (r && r.ok) {
    $("#import-modal").classList.add("hidden");
    loaders.fstab();
    loaders.mounts();
  }
};

// ------------------------------------------------------------ dashboard
loaders.dashboard = async () => {
  const d = await api("GET", "/api/dashboard");
  await initLogsPanel();
  renderDashboard(d);
};

function renderDashboard(d) {
  const card = (label, value, sub = "") => `<div class="card"><div class="text-xs text-slate-500">${label}</div><div class="text-2xl font-semibold">${value}</div><div class="text-xs text-slate-500">${sub}</div></div>`;
  $("#dash-cards").innerHTML = [
    card("Mounts", d.mounts.total, `${d.mounts.mounted} mounted · ${d.mounts.healthy} healthy`),
    card("Persistent (fstab)", d.fstab_entries),
    card("Sync jobs", d.sync_jobs),
    card("Media jobs", d.media_jobs),
    card("Rclone remotes", d.rclone_remotes),
  ].join("");
  const toGB = (b) => (b / 1024 ** 3).toFixed(1);
  $("#dash-disks").innerHTML = d.disks.map((x) => `<div class="disk-item">
    <div class="disk-row">
      <span class="disk-path font-mono text-slate-400">${esc(x.path)}</span>
      <span class="disk-pct font-mono text-xs">${x.used_pct}%</span>
    </div>
    <div class="progress-track"><div class="progress-fill ${x.used_pct > 90 ? "bg-red-500" : x.used_pct > 70 ? "bg-amber-500" : "bg-accent"}" style="width:${x.used_pct}%"></div></div>
    <div class="disk-details">
      <span class="disk-used font-mono text-xs text-slate-400">${toGB(x.total - x.free)} GB used</span>
      <span class="disk-free font-mono text-xs text-slate-400">${toGB(x.free)} GB free</span>
    </div>
  </div>`).join("") || "—";
  const toGB2 = (b) => (b / 1024 ** 3).toFixed(1);
  $("#dash-mounts").innerHTML = d.mount_usage.map((x) => `<div class="disk-item">
    <div class="disk-row">
      <span class="disk-path font-mono text-slate-400">${esc(x.name)}</span>
      <span class="disk-pct font-mono text-xs">${x.type.toUpperCase()}</span>
      <span class="disk-pct font-mono text-xs">${x.used_pct}%</span>
    </div>
    <div class="disk-path font-mono text-xs text-slate-500 mb-1">${esc(x.mount_point)}</div>
    <div class="progress-track"><div class="progress-fill ${x.used_pct > 90 ? "bg-red-500" : x.used_pct > 70 ? "bg-amber-500" : "bg-accent"}" style="width:${x.used_pct}%"></div></div>
    <div class="disk-details">
      <span class="disk-used font-mono text-xs text-slate-400">${toGB2(x.total - x.free)} GB used</span>
      <span class="disk-free font-mono text-xs text-slate-400">${toGB2(x.free)} GB free</span>
    </div>
  </div>`).join("") || "<div class='text-xs text-slate-400 p-4 text-center'>No mounted network mounts</div>";
  $("#dash-runs").innerHTML = d.recent_runs.map((r) => `<tr><td>${esc(r.kind)}</td><td class="font-mono text-xs">${esc(r.job_id)}</td>
    <td>${r.status === "ok" ? yes(true, "ok") : yes(false, "", esc(r.status))}</td><td>${fmtTime(r.finished_at)}</td><td class="text-xs">${esc(r.message)}</td></tr>`).join("")
    || `<tr><td colspan="5" class="text-slate-400">No runs yet</td></tr>`;

  // System metrics panels
  renderSysMetrics(d.system || {});
}

function renderSysMetrics(sys) {
  $("#sys-uptime").textContent = sys.uptime || "—";
  $("#sys-cpu").textContent = (sys.cpu && sys.cpu.used_pct !== undefined) ? sys.cpu.used_pct + "%" : "—";
  $("#sys-mem").textContent = (sys.memory && sys.memory.used_pct !== undefined) ? sys.memory.used_pct + "%" : "—";
  $("#sys-procs").textContent = sys.process_count !== undefined ? sys.process_count : "—";

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
    procsBody.innerHTML = procs.map((p) => `<tr><td class="font-mono text-xs">${p.pid}</td>
      <td class="font-mono text-xs">${esc(p.name)}</td><td class="font-mono text-xs">${esc(p.state)}</td></tr>`).join("")
      || `<tr><td colspan="3" class="text-slate-400 text-xs">No processes</td></tr>`;
  }
}

// Realtime refresh of all dashboard metrics while dashboard tab is active
setInterval(async () => {
  if (currentTab !== "dashboard") return;
  try {
    const d = await api("GET", "/api/dashboard");
    renderDashboard(d);
  } catch { /* ignore refresh failures */ }
}, 5000);

async function initLogsPanel() {
  const d = await api("GET", "/api/logs");
  const sel = $("#log-name");
  if (!sel) return;
  sel.innerHTML = d.logs.map((n) => `<option value="${esc(n)}">${esc(n)}</option>`).join("");
  if (d.logs.length) loadLog(d.logs[0]);
}
$("#log-name").addEventListener("change", (e) => loadLog(e.target.value));
$("#log-level").addEventListener("change", loadLog);
$("#log-refresh").addEventListener("click", loadLog);
setInterval(() => {
  if (currentTab === "dashboard" && $("#log-follow") && $("#log-follow").checked) loadLog();
}, 3000);

async function loadLog(name) {
  if (!name) {
    const sel = $("#log-name");
    if (sel) name = sel.value;
    if (!name) return;
  }
  const d = await api("GET", `/api/logs/${encodeURIComponent(name)}?lines=${$("#log-lines").value}&level=${$("#log-level").value}`);
  const box = $("#log-out");
  if (box) box.textContent = d.lines.join("\n") || "(empty)";
  if (box) box.scrollTop = box.scrollHeight;
}

// ------------------------------------------------------------ mounts
let mountDefaults = {};
function mountTypeChanged() {
  const t = $("#mf-type").value;
  // Show/hide fields based on mount type
  $$("#mount-form .mf").forEach((el) => el.classList.toggle("hidden", !el.classList.contains(t)));
  // Set options placeholder based on type
  const placeholders = {
    nfs: "hard,noatime,_netdev,nofail,x-systemd.automount (default)",
    smb: "vers=3.0,uid=1000,gid=1000,file_mode=0664,dir_mode=0775,_netdev,nofail (default)",
    rclone: "VFS cache settings (see rclone docs), pass/password are obscured automatically"
  };
  $("#mf-options").placeholder = placeholders[t] || "";
  // Set hint text
  const hints = {
    nfs: "NFSv4 with hard,noatime,_netdev,nofail,x-systemd.automount by default",
    smb: "Credentials are stored only on the host in a root-only file.",
    rclone: "Rclone mounts use a VFS write cache in /data/rclone-cache. OAuth remotes: run rclone config on desktop."
  };
  $("#mf-hint").textContent = hints[t] || "";
}
$("#mf-type").addEventListener("change", mountTypeChanged);

// Set initial state on page load
document.addEventListener("DOMContentLoaded", mountTypeChanged);

// Import form type change handler
function importTypeChanged() {
  const t = $("#import-type").value;
  $$("#import-form .mf").forEach((el) => el.classList.toggle("hidden", !el.classList.contains(t)));
}
$("#import-type").addEventListener("change", importTypeChanged);

function ownershipCell(o) {
  if (!o) return "—";
  if (o.error) return `<span class="badge bad">${esc(o.error)}</span>`;
  return `<div class="text-xs">owner ${o.owner_uid}:${o.owner_gid} · app ${o.app_uid}:${o.app_gid} ${o.mode ? "· " + esc(o.mode) : ""}</div>
    ${o.uid_match && o.gid_match ? '<span class="badge ok">match</span>' : ''}`;
}

loaders.mounts = async () => {
  const [d, r] = await Promise.all([api("GET", "/api/mounts"), api("GET", "/api/rclone/remotes")]);
  mountDefaults = d.defaults;
  $("#mf-remote").innerHTML = r.remotes.map((x) => `<option>${esc(x.name)}</option>`).join("") || "<option value=''>(no remotes)</option>";
  mountTypeChanged();
  $("#mounts-body").innerHTML = d.mounts.map((m) => {
    const s = m.status || {};
    const src = m.type === "nfs" ? `${m.host}:${m.export}` : m.type === "smb" ? `//${m.host}/${m.share}` : `${m.remote}:${m.subpath || ""}`;
    return `<tr><td><b>${esc(m.name)}</b><div class="text-xs text-slate-500">${esc(m.type)}${m.persist ? " · fstab" : ""}${m.auto_mount ? " · auto" : ""}</div></td>
      <td class="font-mono text-xs">${esc(src)}</td><td class="font-mono text-xs">${esc(m.mount_point)}</td>
      <td>${yes(m.mounted)}</td><td>${tri(s.readable)}</td><td>${tri(s.writable)}</td><td>${tri(s.rename_ok)}</td><td>${tri(s.delete_ok)}</td>
      <td>${ownershipCell(s.ownership)}${s.error ? `<div class="text-xs text-red-700">${esc(s.error)}</div>` : ""}<div class="text-xs text-slate-400">${fmtTime(s.checked_at)}</div></td>
      <td class="whitespace-nowrap">
        ${!m.mounted ? `<button class="btn btn-sm" data-m="mount" data-id="${m.id}">Mount</button>` : `<button class="btn btn-sm" data-m="unmount" data-id="${m.id}">Unmount</button>`}
        <button class="btn btn-sm" data-m="check" data-id="${m.id}">Check</button>
        <button class="btn btn-sm btn-danger" data-m="delete" data-id="${m.id}" data-name="${esc(m.name)}">Delete</button>
      </td></tr>`;
  }).join("") || `<tr><td colspan="10" class="text-slate-400">No managed mounts yet</td></tr>`;
};

$("#mounts-body").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-m]");
  if (!b) return;
  const id = b.dataset.id, action = b.dataset.m;
  if (action === "delete") {
    if (!(await typedConfirm("Delete mount", `Unmount and remove "${b.dataset.name}" (and its fstab line if persisted).`, "DELETE"))) return;
    await act(b, () => api("DELETE", `/api/mounts/${id}`), "Mount removed");
  } else {
    await act(b, () => api("POST", `/api/mounts/${id}/${action}`), action === "check" ? "Checked" : `${action} done`);
  }
  loaders.mounts();
});
$("#mounts-check-all").addEventListener("click", async (e) => { await act(e.target, () => api("POST", "/api/mounts/check-all"), "All mounts checked"); loaders.mounts(); });
$("#mount-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const r = await act(e.submitter, () => api("POST", "/api/mounts", formData(e.target)));
  if (r && r.ok) {
    toast(r.result && !r.result.ok ? `Saved, but mount failed: ${r.result.result.stderr || "see status"}` : "Mount added", r.result && !r.result.ok ? "error" : "ok");
    e.target.reset(); loaders.mounts();
  }
});

// ------------------------------------------------------------ file browser
let fbPath = "";
let fbExpanded = new Set();
let fbCache = new Map();
function fmtSize(bytes) {
  if (bytes == null) return "—";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0, s = bytes;
  while (s >= 1024 && i < u.length - 1) { s /= 1024; i++; }
  return `${s.toFixed(i ? 1 : 0)} ${u[i]}`;
}
function fileIcon(name) {
  const l = name.toLowerCase();
  if (l.endsWith(".jpg") || l.endsWith(".jpeg") || l.endsWith(".png") || l.endsWith(".gif") || l.endsWith(".webp")) return "🖼️";
  if (l.endsWith(".mp4") || l.endsWith(".mkv") || l.endsWith(".avi") || l.endsWith(".mov")) return "🎬";
  if (l.endsWith(".mp3") || l.endsWith(".wav") || l.endsWith(".flac") || l.endsWith(".ogg")) return "🎵";
  if (l.endsWith(".pdf")) return "📄";
  if (l.endsWith(".zip") || l.endsWith(".tar") || l.endsWith(".gz") || l.endsWith(".rar")) return "📦";
  if (l.endsWith(".doc") || l.endsWith(".docx")) return "📘";
  if (l.endsWith(".xls") || l.endsWith(".xlsx")) return "📗";
  if (l.endsWith(".txt") || l.endsWith(".md") || l.endsWith(".log")) return "📝";
  if (l.endsWith(".json") || l.endsWith(".yaml") || l.endsWith(".yml") || l.endsWith(".toml") || l.endsWith(".ini") || l.endsWith(".conf") || l.endsWith(".cfg")) return "⚙️";
  if (l.endsWith(".sh") || l.endsWith(".py") || l.endsWith(".js") || l.endsWith(".ts") || l.endsWith(".html") || l.endsWith(".css")) return "📜";
  return "📄";
}
function depthOf(path) {
  if (!path) return 0;
  return path.split("/").filter(Boolean).length;
}
function rootIndexOf(path, roots) {
  for (let i = 0; i < roots.length; i++) {
    const r = roots[i];
    if (path === r || path.startsWith(r + "/")) return i;
  }
  return -1;
}
function isAncestorOrSelf(anc, path) {
  return path === anc || path.startsWith(anc + "/");
}
function childrenOf(parentPath, d) {
  const kids = [];
  const pd = depthOf(parentPath);
  for (const dir of (d.dirs || [])) {
    if (depthOf(dir.path) === pd + 1 && isAncestorOrSelf(parentPath, dir.path)) kids.push({ ...dir, type: "dir" });
  }
  for (const f of (d.files || [])) {
    if (depthOf(f.path) === pd + 1 && isAncestorOrSelf(parentPath, f.path)) kids.push({ ...f, type: "file" });
  }
  kids.sort((a, b) => {
    const ta = a.type === "dir" ? 0 : 1;
    const tb = b.type === "dir" ? 0 : 1;
    if (ta !== tb) return ta - tb;
    return a.name.localeCompare(b.name);
  });
  return kids;
}
function fbPathBarHtml(d) {
  const roots = d.roots || [];
  const parts = [];
  if (d.path) {
    const rootIdx = rootIndexOf(d.path, roots);
    const rootPath = rootIdx >= 0 ? roots[rootIdx] : "";
    const rel = rootPath ? d.path.slice(rootPath.length) : d.path;
    const segs = rel.split("/").filter(Boolean);
    let acc = rootPath;
    for (const s of segs) {
      acc = acc + "/" + s;
      parts.push({ name: s, path: acc });
    }
  }
  const html = [];
  html.push(`<span class="cursor-pointer text-indigo-600 hover:underline px-1" data-fb-nav="" data-fb-path="">Roots</span>`);
  for (const p of parts) {
    html.push(`<span class="text-xs text-slate-400 mx-0.5">/</span><span class="cursor-pointer text-indigo-600 hover:underline px-1" data-fb-nav="" data-fb-path="${esc(p.path)}">${esc(p.name)}</span>`);
  }
  return html.join("");
}
async function loadDirIntoCache(path) {
  if (fbCache.has(path)) return fbCache.get(path);
  try {
    const d = await api("GET", `/api/browse?path=${encodeURIComponent(path)}`);
    fbCache.set(path, d);
    return d;
  } catch (e) { toast(e.message, "error"); return null; }
}
async function ensurePathLoaded(curPath) {
  if (!curPath) { await loadDirIntoCache(""); return; }
  const parts = curPath.split("/").filter(Boolean);
  let acc = "";
  for (const p of parts) {
    acc = acc + "/" + p;
    if (!fbCache.has(acc)) await loadDirIntoCache(acc);
  }
}
function renderTreeHtml(curPath, roots) {
  const html = [];
  const visibleRoots = roots.filter(r => {
    const ri = rootIndexOf(r, roots);
    return ri >= 0 && r === roots[ri];
  });
  for (const root of visibleRoots) {
    const rootData = fbCache.get(root) || { dirs: [], files: [], roots: [] };
    const kids = childrenOf(root, rootData);
    renderTreeNode(html, root, curPath, 0, kids);
  }
  return html.join("");
}
function renderTreeNode(html, nodePath, curPath, level, kids) {
  const nodeName = nodePath.split("/").pop() || nodePath;
  const pad = level * 18;
  const selected = nodePath === curPath ? "bg-indigo-50" : "";
  const isExpanded = fbExpanded.has(nodePath) || nodePath === curPath || isAncestorOrSelf(nodePath, curPath);
  const isDir = true; // renderTreeNode is only called for dirs
  const expander = `<span class="w-4 text-center cursor-pointer hover:text-indigo-600 shrink-0" data-fb-expand="${esc(nodePath)}">${isExpanded ? "▼" : "▶"}</span>`;

  html.push(`<div class="flex items-center gap-1 px-3 py-1.5 text-sm rounded ${selected} border-transparent hover:bg-slate-100 cursor-pointer" data-fb-navdir="" data-fb-path="${esc(nodePath)}" style="padding-left:${pad}px">
    ${expander}
    <span class="w-4 text-center">📁</span>
    <div class="flex-1 min-w-0 break-all text-slate-800">${esc(nodeName)}</div>
    <span class="w-16 text-right text-xs font-mono text-slate-500">—</span>
    <span class="text-xs text-slate-400">dir</span>
  </div>`);

  if (isExpanded && kids && kids.length > 0) {
    for (const kid of kids) {
      if (kid.type === "dir") {
        const kidData = fbCache.get(kid.path) || { dirs: [], files: [] };
        const kidKids = childrenOf(kid.path, kidData);
        renderTreeNode(html, kid.path, curPath, level + 1, kidKids);
      } else {
        const kpad = (level + 1) * 18;
        const ksize = fmtSize(kid.size);
        html.push(`<div class="flex items-center gap-1 px-3 py-1.5 text-sm rounded hover:bg-slate-50" data-fb-item="" data-fb-path="${esc(kid.path)}" data-fb-type="file" style="padding-left:${kpad}px">
          <span class="w-4 text-center">${fileIcon(kid.name)}</span>
          <div class="flex-1 min-w-0 break-all text-slate-600">${esc(kid.name)}</div>
          <span class="w-16 text-right text-xs font-mono text-slate-500">${ksize}</span>
          <span class="text-xs text-slate-400">file</span>
        </div>`);
      }
    }
  }
}
async function renderTree(curPath) {
  const container = $("#fb-tree");
  const empty = $("#fb-empty");
  const pathBar = $("#fb-path-bar");
  const upBtn = $("#fb-up");
  const rootBtn = $("#fb-root");
  const loading = $("#fb-loading");
  if (!container || !empty || !pathBar || !upBtn || !rootBtn || !loading) return;

  await ensurePathLoaded(curPath || "");
  const d = fbCache.get(curPath || "") || { dirs: [], files: [], roots: [] };
  const roots = d.roots || [];

  pathBar.innerHTML = fbPathBarHtml(d);
  empty.classList.add("hidden");
  loading.classList.add("hidden");

  if (curPath) {
    upBtn.classList.remove("opacity-50", "cursor-not-allowed");
    rootBtn.classList.remove("opacity-50", "cursor-not-allowed");
  } else {
    upBtn.classList.add("opacity-50", "cursor-not-allowed");
    rootBtn.classList.add("opacity-50", "cursor-not-allowed");
  }

  const html = renderTreeHtml(curPath, roots);
  container.innerHTML = html || `<div class="px-3 py-2 text-slate-500 text-sm">Empty</div>`;

  container.onclick = async (e) => {
    const exp = e.target.closest("[data-fb-expand]");
    if (exp) {
      const p = exp.dataset.fbExpand;
      // Load children if not cached yet
      if (!fbCache.has(p)) {
        const childData = await loadDirIntoCache(p);
        if (!childData) return;
      }
      if (fbExpanded.has(p)) fbExpanded.delete(p); else fbExpanded.add(p);
      renderTree(curPath);
      return;
    }
    const dirLink = e.target.closest("[data-fb-navdir]");
    if (dirLink) {
      const p = dirLink.dataset.fbPath;
      fbPath = p;
      let pp = p;
      while (pp) {
        fbExpanded.add(pp);
        const idx = pp.lastIndexOf("/");
        if (idx <= 0) break;
        pp = pp.substring(0, idx);
      }
      renderTree(p);
      return;
    }
  };
}

loaders.files = async () => {
  fbPath = "";
  fbExpanded = new Set();
  fbCache = new Map();
  await renderTree("");
};

$("#fb-up").addEventListener("click", async () => {
  if (!fbPath) return;
  const parent = fbPath.substring(0, fbPath.lastIndexOf("/"));
  fbPath = parent || "";
  await renderTree(fbPath);
});
$("#fb-root").addEventListener("click", async () => {
  fbPath = "";
  fbExpanded = new Set();
  fbCache = new Map();
  await renderTree("");
});

document.addEventListener("click", async (e) => {
  const nav = e.target.closest("[data-fb-nav]");
  if (!nav) return;
  const targetPath = nav.dataset.fbPath;
  if (!targetPath) { fbPath = ""; fbExpanded = new Set(); fbCache = new Map(); await renderTree(""); return; }
  fbPath = targetPath;
  let pp = targetPath;
  while (pp) {
    fbExpanded.add(pp);
    const idx = pp.lastIndexOf("/");
    if (idx <= 0) break;
    pp = pp.substring(0, idx);
  }
  await renderTree(targetPath);
});

// ------------------------------------------------------------ fstab
let fstabData = { entries: [] };
function renderFstab() {
  const q = $("#fstab-filter").value.toLowerCase();
  const rows = fstabData.entries.filter((x) => !q || `${x.device} ${x.mountpoint} ${x.fstype} ${x.options}`.toLowerCase().includes(q));
  $("#fstab-body").innerHTML = rows.map((x) => `<tr><td class="font-mono text-xs break-all">${esc(x.device)}</td><td class="font-mono text-xs">${esc(x.mountpoint)}</td>
    <td>${esc(x.fstype)}</td><td class="font-mono text-xs break-all">${esc(x.options)}</td><td>${yes(x.mounted)}</td>
    <td>${x.automount ? '<span class="badge ok">yes</span>' : '<span class="badge muted">no</span>'}</td><td>${x.nofail ? '<span class="badge ok">yes</span>' : '<span class="badge muted">no</span>'}</td>
    <td class="whitespace-nowrap">
      ${!x.mounted ? `<button class="btn btn-sm" data-f="mount" data-mp="${esc(x.mountpoint)}">Mount</button>` : `<button class="btn btn-sm" data-f="unmount" data-mp="${esc(x.mountpoint)}">Unmount</button>`}
      <button class="btn btn-sm btn-indigo" data-f="edit" data-mp="${esc(x.mountpoint)}">Edit</button>
      <button class="btn btn-sm" data-f="import" data-mp="${esc(x.mountpoint)}" data-device="${esc(x.device)}" data-fstype="${esc(x.fstype)}" data-options="${esc(x.options)}">Import</button>
      <button class="btn btn-sm btn-danger" data-f="remove" data-mp="${esc(x.mountpoint)}">Remove</button>
    </td></tr>`).join("")
    || `<tr><td colspan="8" class="text-slate-400">No entries</td></tr>`;
  $("#fstab-meta").textContent = `${fstabData.entries.length} entries · read ${fmtTime(fstabData.read_at)}${fstabData.error ? " · ERROR: " + fstabData.error : ""}`;
}
loaders.fstab = async () => { fstabData = await api("GET", "/api/fstab"); renderFstab(); };
$("#fstab-filter").addEventListener("input", renderFstab);
$("#fstab-reload").addEventListener("click", async (e) => {
  const r = await act(e.target, () => api("POST", "/api/fstab/reload"), "fstab reloaded");
  if (r) { fstabData = r; renderFstab(); }
});
$("#fstab-body").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-f]");
  if (!b) return;
  const body = { mountpoint: b.dataset.mp };
  if (b.dataset.f === "remove") {
    const word = await typedConfirm("Remove fstab entry", `Remove ${b.dataset.mp} from the host /etc/fstab (a backup is written to /etc/fstab.drive-manager.bak).`, "REMOVE");
    if (!word) return;
    body.confirm = word;
  } else if (b.dataset.f === "import") {
    // Open import modal
    const device = b.dataset.device;
    const fstype = b.dataset.fstype;
    const options = b.dataset.options;
    openImportModal(b.dataset.mp, device, fstype, options);
    return;
  } else if (b.dataset.f === "edit") {
    const entry = fstabData.entries.find(x => x.mountpoint === b.dataset.mp);
    if (!entry) return;
    openFstabEditModal(entry);
    return;
  }
  const r = await act(b, () => api("POST", `/api/fstab/${b.dataset.f}`, body), `${b.dataset.f} done`);
  if (r && r.fstab) { fstabData = r.fstab; renderFstab(); }
});

// ------------------------------------------------------------ fstab entry edit modal
function openFstabEditModal(entry) {
  const modal = $("#fstab-edit-modal");
  const form = $("#fstab-edit-form");
  $("#edit-mountpoint").value = entry.mountpoint;
  $("#edit-mountpoint-display").textContent = entry.mountpoint;
  $("#edit-noauto").checked = entry.options.includes("noauto");
  $("#edit-automount").checked = entry.automount === true;
  $("#edit-nofail").checked = entry.nofail === true;
  $("#edit-options").value = entry.options || "";
  modal.classList.remove("hidden");
}

$("#fstab-edit-cancel").addEventListener("click", () => {
  $("#fstab-edit-modal").classList.add("hidden");
});

$("#fstab-edit-ok").addEventListener("click", async () => {
  const mp = $("#edit-mountpoint").value;
  const body = {
    mountpoint: mp,
    automount: $("#edit-automount").checked,
    nofail: $("#edit-nofail").checked,
    noauto: !$("#edit-noauto").checked,
    options: $("#edit-options").value || undefined,
  };
  const r = await api("POST", "/api/fstab/edit", body);
  if (r) {
    $("#fstab-edit-modal").classList.add("hidden");
    toast("fstab entry updated", "ok");
    fstabData = r.fstab;
    renderFstab();
  }
});

// Close modal on backdrop click
$("#confirm").addEventListener("click", (e) => {
  if (e.target === $("#confirm")) $("#confirm").classList.add("hidden");
});
$("#fstab-edit-modal").addEventListener("click", (e) => {
  if (e.target === $("#fstab-edit-modal")) $("#fstab-edit-modal").classList.add("hidden");
});

// ------------------------------------------------------------ schedule builder (shared)
function scheduleWidget(root) {
  const get = (k) => $(`[data-s="${k}"]`, root);
  const spec = () => {
    const type = get("type").value;
    return { type, every: Number(get("every").value), minute: Number(get("minute").value), time: get("time").value,
      weekday: Number(get("weekday").value), day: Number(get("day").value), cron: get("cron").value };
  };
  const refreshVisibility = () => {
    const t = get("type").value;
    $$("[data-for]", root).forEach((el) => el.classList.toggle("hidden", !el.dataset.for.split(" ").includes(t)));
  };
  let timer;
  const preview = () => {
    refreshVisibility();
    clearTimeout(timer);
    timer = setTimeout(async () => {
      try {
        const r = await api("POST", "/api/schedule/preview", { spec: spec() });
        get("preview").textContent = `${r.text}${r.cron ? `  ·  cron: ${r.cron}` : ""}${r.next_runs.length ? `  ·  next: ${fmtTime(r.next_runs[0])}` : ""}`;
        get("preview").classList.remove("bad");
        get("preview").classList.add("ok");
      } catch (e) { get("preview").textContent = e.message; get("preview").classList.remove("ok"); get("preview").classList.add("bad"); }
    }, 250);
  };
  $$("[data-s]", root).forEach((el) => el.addEventListener("input", preview));
  return {
    spec,
    set(s) {
      s = s || { type: "manual" };
      get("type").value = s.type || "manual";
      for (const k of ["every", "minute", "time", "weekday", "day", "cron"]) if (s[k] !== undefined) get(k).value = s[k];
      preview();
    },
    preview,
  };
}

// ------------------------------------------------------------ sync jobs
const syncSchedule = scheduleWidget($('[data-schedule="sj"]'));
let syncJobs = [];
function resetSyncForm() {
  const f = $("#sync-form");
  f.reset(); f.elements.id.value = "";
  $("#sj-title").textContent = "New sync job";
  syncSchedule.set({ type: "manual" });
}
loaders.sync = async () => {
  syncJobs = (await api("GET", "/api/sync/jobs")).jobs;
  $("#sync-body").innerHTML = syncJobs.map((j) => {
    const lr = j.last_run || {};
    const st = j.running ? '<span class="badge warn">running</span>' : lr.status ? (lr.status === "ok" ? yes(true, "ok") : yes(false, "", esc(lr.status))) : '<span class="badge muted">never</span>';
    return `<tr><td><b>${esc(j.name)}</b>${j.enabled ? "" : ' <span class="badge muted">disabled</span>'}</td>
      <td class="font-mono text-xs">${esc(j.source)}<br>→ ${esc(j.destination)}</td><td>${esc(j.mode)}</td><td class="text-xs">${esc(j.schedule_text)}</td>
      <td>${st}<div class="text-xs text-slate-500">${fmtTime(lr.finished_at || lr.started_at)}</div>${lr.error ? `<div class="text-xs text-red-700">${esc(lr.error)}</div>` : ""}</td>
      <td class="whitespace-nowrap"><button class="btn btn-sm" data-j="run" data-id="${j.id}">Run</button>
        <button class="btn btn-sm" data-j="edit" data-id="${j.id}">Edit</button>
        <button class="btn btn-sm" data-j="log" data-id="${j.id}">Log</button>
        <button class="btn btn-sm btn-danger" data-j="delete" data-id="${j.id}" data-name="${esc(j.name)}">Delete</button></td></tr>`;
  }).join("") || `<tr><td colspan="6" class="text-slate-400">No sync jobs</td></tr>`;
};
$("#sync-body").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-j]");
  if (!b) return;
  const id = b.dataset.id;
  if (b.dataset.j === "run") { await act(b, () => api("POST", `/api/sync/jobs/${id}/run`), "Started"); setTimeout(loaders.sync, 1500); }
  if (b.dataset.j === "edit") {
    const j = syncJobs.find((x) => x.id === id);
    fillForm($("#sync-form"), j);
    syncSchedule.set(j.schedule_spec);
    $("#sj-title").textContent = `Edit: ${j.name}`;
    $("#sync-form").scrollIntoView({ behavior: "smooth" });
  }
  if (b.dataset.j === "log") { showTab("dashboard"); loadLog(`sync-${id}.log`); }
  if (b.dataset.j === "delete") {
    if (!(await typedConfirm("Delete sync job", `Delete "${b.dataset.name}"? Files are not touched.`, "DELETE"))) return;
    await act(b, () => api("DELETE", `/api/sync/jobs/${id}`), "Deleted"); loaders.sync();
  }
});
$("#sync-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const data = formData(e.target);
  data.schedule_spec = syncSchedule.spec();
  const id = data.id; delete data.id;
  const r = await act(e.submitter, () => (id ? api("PUT", `/api/sync/jobs/${id}`, data) : api("POST", "/api/sync/jobs", data)), "Job saved");
  if (r && r.ok) { resetSyncForm(); loaders.sync(); }
});
$("#sj-reset").addEventListener("click", resetSyncForm);

// ------------------------------------------------------------ rclone
loaders.rclone = async () => {
  const d = await api("GET", "/api/rclone/remotes");
  $("#rclone-meta").textContent = d.available ? `${d.remotes.length} remote(s) in /config/rclone.conf` : "rclone binary not available!";
  $("#rclone-body").innerHTML = d.remotes.map((r) => `<tr><td><b>${esc(r.name)}</b><div class="text-xs text-slate-500 font-mono">${esc(Object.entries(r.options).map(([k, v]) => `${k}=${v}`).join(" "))}</div></td>
    <td>${esc(r.type)}</td><td class="font-mono text-xs">${esc(r.mount_point)}</td><td>${yes(r.mounted)}</td>
    <td class="whitespace-nowrap"><button class="btn btn-sm" data-r="mount" data-n="${esc(r.name)}">Mount</button>
      <button class="btn btn-sm" data-r="unmount" data-n="${esc(r.name)}">Unmount</button>
      <button class="btn btn-sm" data-r="edit" data-n="${esc(r.name)}">Edit</button>
      <button class="btn btn-sm btn-danger" data-r="delete" data-n="${esc(r.name)}">Delete</button></td></tr>`).join("")
    || `<tr><td colspan="5" class="text-slate-400">No remotes</td></tr>`;
  loaders.rclone.data = d.remotes;
};
$("#rclone-body").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-r]");
  if (!b) return;
  const n = encodeURIComponent(b.dataset.n);
  if (b.dataset.r === "edit") {
    const r = loaders.rclone.data.find((x) => x.name === b.dataset.n);
    fillForm($("#remote-form"), { name: r.name, type: r.type, options: Object.entries(r.options).map(([k, v]) => `${k}=${v}`).join("\n") });
    return;
  }
  if (b.dataset.r === "delete") {
    if (!(await typedConfirm("Delete remote", `Delete rclone remote "${b.dataset.n}"?`, "DELETE"))) return;
    await act(b, () => api("DELETE", `/api/rclone/remotes/${n}`), "Remote deleted");
  } else {
    await act(b, () => api("POST", `/api/rclone/remotes/${n}/${b.dataset.r}`), `${b.dataset.r} done`);
  }
  loaders.rclone();
});
$("#remote-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const d = formData(e.target);
  const options = {};
  d.options.split("\n").forEach((line) => { const i = line.indexOf("="); if (i > 0) options[line.slice(0, i).trim()] = line.slice(i + 1).trim(); });
  const r = await act(e.submitter, () => api("POST", "/api/rclone/remotes", { name: d.name, type: d.type, options }), "Remote saved");
  if (r && r.ok) { e.target.reset(); loaders.rclone(); }
});
$("#rsync-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  await act(e.submitter, () => api("POST", "/api/rclone/sync", formData(e.target)), "Sync started");
});

// ------------------------------------------------------------ media
let mediaJobs = [];
function resetMediaForm() { const f = $("#media-form"); f.reset(); f.elements.id.value = ""; $("#mj-title").textContent = "New media job"; }
loaders.media = async () => {
  const [j, s] = await Promise.all([api("GET", "/api/media/jobs"), api("GET", "/api/media/settings")]);
  mediaJobs = j.jobs;
  const st = s.settings, f = $("#media-settings");
  fillForm(f, st);
  f.elements.category_rules.value = Object.entries(st.category_rules || {}).map(([k, v]) => `${k}=${v}`).join("\n");
  $("#llm-key").textContent = s.llm_key_set ? "API key set" : "no API key";
  $("#llm-key").className = `badge ml-1 ${s.llm_key_set ? "ok" : "warn"}`;
  $("#media-body").innerHTML = mediaJobs.map((m) => {
    const ls = m.last_scan || {};
    const c = ls.counts || {};
    return `<tr><td><b>${esc(m.name)}</b>${m.enabled ? "" : ' <span class="badge muted">disabled</span>'}</td>
      <td class="font-mono text-xs">${esc(m.source)}<br>→ ${esc(m.destination)}</td><td>${esc(m.mode)}</td>
      <td>${m.running ? '<span class="badge warn">scanning</span>' : ls.status ? (ls.status === "ok" ? yes(true, "ok") : yes(false, "", "failed")) : '<span class="badge muted">never</span>'}
        <div class="text-xs text-slate-500">${fmtTime(ls.finished_at)} · ${c.processed ?? 0} processed · ${c.unrecognised ?? 0} unrecognised · ${c.failed ?? 0} failed</div>
        ${(ls.errors || []).map((x) => `<div class="text-xs text-red-700">${esc(x)}</div>`).join("")}</td>
      <td class="whitespace-nowrap"><button class="btn btn-sm" data-mj="scan" data-id="${m.id}">Scan now</button>
        <button class="btn btn-sm" data-mj="records" data-id="${m.id}">Files</button>
        <button class="btn btn-sm" data-mj="edit" data-id="${m.id}">Edit</button>
        <button class="btn btn-sm" data-mj="log" data-id="${m.id}">Log</button>
        <button class="btn btn-sm btn-danger" data-mj="delete" data-id="${m.id}" data-name="${esc(m.name)}">Delete</button></td></tr>`;
  }).join("") || `<tr><td colspan="5" class="text-slate-400">No media jobs</td></tr>`;
};
$("#media-body").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-mj]");
  if (!b) return;
  const id = b.dataset.id;
  if (b.dataset.mj === "scan") { await act(b, () => api("POST", `/api/media/jobs/${id}/scan`), "Scan started"); setTimeout(loaders.media, 2000); }
  if (b.dataset.mj === "edit") { const m = mediaJobs.find((x) => x.id === id); fillForm($("#media-form"), m); $("#mj-title").textContent = `Edit: ${m.name}`; }
  if (b.dataset.mj === "log") { showTab("dashboard"); loadLog(`media-${id}.log`); }
  if (b.dataset.mj === "records") {
    const r = await api("GET", `/api/media/jobs/${id}/records`);
    $("#media-records-card").classList.remove("hidden");
    $("#media-records-job").textContent = `(${mediaJobs.find((x) => x.id === id).name})`;
    $("#media-records").innerHTML = r.records.map((x) => `<tr><td class="font-mono text-xs break-all">${esc(x.source_path)}</td>
      <td>${x.status === "done" ? yes(true, "done") : yes(false, "", esc(x.status))}<div class="text-xs">${esc(x.message)}</div></td>
      <td class="font-mono text-xs break-all">${esc(x.target)}</td><td>${fmtTime(x.updated_at)}</td></tr>`).join("") || `<tr><td colspan="4" class="text-slate-400">Nothing processed yet</td></tr>`;
  }
  if (b.dataset.mj === "delete") {
    if (!(await typedConfirm("Delete media job", `Delete "${b.dataset.name}"? Files are not touched.`, "DELETE"))) return;
    await act(b, () => api("DELETE", `/api/media/jobs/${id}`), "Deleted"); loaders.media();
  }
});
$("#media-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const d = formData(e.target); const id = d.id; delete d.id;
  const r = await act(e.submitter, () => (id ? api("PUT", `/api/media/jobs/${id}`, d) : api("POST", "/api/media/jobs", d)), "Media job saved");
  if (r && r.ok) { resetMediaForm(); loaders.media(); }
});
$("#mj-reset").addEventListener("click", resetMediaForm);
$("#media-settings").addEventListener("submit", async (e) => {
  e.preventDefault();
  const d = formData(e.target);
  const rules = {};
  d.category_rules.split("\n").forEach((line) => { const i = line.indexOf("="); if (i > 0) rules[line.slice(0, i).trim()] = line.slice(i + 1).trim(); });
  d.category_rules = rules;
  await act(e.submitter, () => api("PUT", "/api/media/settings", d), "Settings saved");
});
$("#media-preview").addEventListener("submit", async (e) => {
  e.preventDefault();
  const sf = $("#media-settings");
  const useLlm = sf && sf.elements.use_llm ? sf.elements.use_llm.checked : false;
  const r = await act(e.submitter, () => api("POST", "/api/media/preview", { filename: e.target.elements.filename.value, use_llm: useLlm }));
  if (r) { $("#media-preview-out").classList.remove("hidden"); $("#media-preview-out").textContent = JSON.stringify(r, null, 2); }
});

// ------------------------------------------------------------ settings
loaders.settings = async () => {
  const [n, roots] = await Promise.all([api("GET", "/api/settings/notifications"), api("GET", "/api/settings/browse-roots")]);
  fillForm($("#notify-form"), n);
  $("#notify-form").elements.bot_token.value = "";
  $("#tg-token-set").textContent = n.bot_token_set ? "(saved)" : "(not set)";
  $("#roots-form").elements.roots.value = roots.roots.join("\n");
  $("#roots-env").textContent = roots.env_override ? "BROWSE_ROOTS environment variable overrides this list." : "One absolute path per line.";
};
$("#notify-form").addEventListener("submit", async (e) => { e.preventDefault(); await act(e.submitter, () => api("PUT", "/api/settings/notifications", formData(e.target)), "Saved"); loaders.settings(); });
$("#notify-test").addEventListener("click", (e) => act(e.target, () => api("POST", "/api/settings/notifications/test"), "Test sent (check Telegram / webhook)"));
$("#password-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const r = await act(e.submitter, () => api("POST", "/api/auth/change-password", formData(e.target)), "Password changed");
  if (r && r.ok) e.target.reset();
});
$("#roots-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const roots = e.target.elements.roots.value.split("\n").map((x) => x.trim()).filter(Boolean);
  await act(e.submitter, () => api("PUT", "/api/settings/browse-roots", { roots }), "Saved");
});

// ------------------------------------------------------------ servers (SMB)
let smbShares = [];
let smbLogTimer = null;

function stopSmbLogPoll() {
  if (smbLogTimer) { clearInterval(smbLogTimer); smbLogTimer = null; }
}

async function fetchSmbLogs() {
  try {
    const d = await api("GET", "/api/servers/smb/logs");
    $("#smb-logs-out").textContent = d.logs || "(no logs)";
    $("#smb-logs-out").scrollTop = $("#smb-logs-out").scrollHeight;
  } catch { /* ignore */ }
}

function startSmbLogPoll() {
  stopSmbLogPoll();
  smbLogTimer = setInterval(fetchSmbLogs, 3000);
}

loaders.servers = async () => {
  const [smb, ftp, webdav] = await Promise.all([
    api("GET", "/api/servers/smb"),
    api("GET", "/api/servers/ftp"),
    api("GET", "/api/servers/webdav"),
  ]);
  renderSmb(smb);
  renderFtp(ftp);
  renderWebdav(webdav);
};

function renderSmb(d) {
  stopSmbLogPoll();
  const state = d.state;
  const cfg = d.config || {};

  $("#smb-state-not-installed").classList.add("hidden");
  $("#smb-state-configure").classList.add("hidden");
  $("#smb-state-running").classList.add("hidden");

  const badge = $("#smb-state-badge");
  if (state === "not_installed") {
    $("#smb-state-not-installed").classList.remove("hidden");
    badge.className = "badge muted";
    badge.textContent = "Not installed";
  } else if (state === "running") {
    $("#smb-state-running").classList.remove("hidden");
    badge.className = "badge ok";
    badge.textContent = "Running";
    $("#smb-running-status").textContent = d.container_status || "running";
    $("#smb-running-status").className = "badge ok";
    fetchSmbLogs();
    startSmbLogPoll();
  } else {
    $("#smb-state-configure").classList.remove("hidden");
    badge.className = d.running ? "badge ok" : "badge bad";
    badge.textContent = d.running ? "Running" : "Stopped";
    const form = $("#smb-config-form");
    form.elements.username.value = cfg.username || "";
    form.elements.password.value = cfg.password || "";
    smbShares = (cfg.shares || []).map((s) => ({ name: s.name || "", path: s.path || "" }));
    if (smbShares.length === 0) smbShares = [{ name: "", path: "" }];
    renderSmbShareRows();
    updateSmbStartButton();
    $("#smb-config-error").textContent = "";
  }
}

function renderSmbShareRows() {
  const container = $("#smb-shares-rows");
  container.innerHTML = smbShares.map((s, i) => `
    <div class="smb-share-row flex flex-wrap gap-2 items-end" data-index="${i}">
      <div class="flex-1 min-w-0">
        <label class="block text-xs text-slate-500 mb-1">Host path</label>
        <div class="flex gap-2">
          <input class="inp flex-1" name="share_path" id="smb-sp-${i}" required placeholder="/mnt/share" value="${esc(s.path)}">
          <button type="button" class="btn btn-sm" data-browse="#smb-sp-${i}">Browse</button>
        </div>
      </div>
      <div class="min-w-[140px]">
        <label class="block text-xs text-slate-500 mb-1">Share name</label>
        <input class="inp" name="share_name" required placeholder="media" value="${esc(s.name)}">
      </div>
      <button type="button" class="btn btn-sm btn-danger" data-remove-share>Remove</button>
    </div>
  `).join("");
}

function updateSmbStartButton() {
  const form = $("#smb-config-form");
  const user = form.elements.username.value.trim();
  const pass = form.elements.password.value;
  let valid = Boolean(user && pass && smbShares.length > 0);
  for (const s of smbShares) {
    if (!s.name.trim() || !s.path.trim()) valid = false;
  }
  $("#smb-start").disabled = !valid;
}

function gatherSmbConfig() {
  const form = $("#smb-config-form");
  const shares = [];
  $("#smb-shares-rows").querySelectorAll(".smb-share-row").forEach((row) => {
    shares.push({
      name: row.querySelector('[name="share_name"]').value,
      path: row.querySelector('[name="share_path"]').value,
    });
  });
  return {
    username: form.elements.username.value,
    password: form.elements.password.value,
    shares,
  };
}

$("#smb-install").addEventListener("click", async () => {
  const r = await act(null, () => api("POST", "/api/servers/smb/install"), "Image pulled");
  if (r) loaders.servers();
});

$("#smb-add-share").addEventListener("click", () => {
  smbShares.push({ name: "", path: "" });
  renderSmbShareRows();
  updateSmbStartButton();
});

$("#smb-shares-rows").addEventListener("input", (e) => {
  if (e.target.name === "share_path" || e.target.name === "share_name") updateSmbStartButton();
});

$("#smb-shares-rows").addEventListener("click", (e) => {
  const b = e.target.closest("[data-remove-share]");
  if (!b) return;
  if (smbShares.length <= 1) { toast("At least one share is required", "error"); return; }
  const row = b.closest(".smb-share-row");
  smbShares.splice(Number(row.dataset.index), 1);
  renderSmbShareRows();
  updateSmbStartButton();
});

$("#smb-config-form").addEventListener("input", updateSmbStartButton);

$("#smb-start").addEventListener("click", async () => {
  const cfg = gatherSmbConfig();
  const r = await act(null, () => api("POST", "/api/servers/smb/start", cfg), "SMB started");
  if (r) loaders.servers();
});

$("#smb-stop").addEventListener("click", async () => {
  const r = await act(null, () => api("POST", "/api/servers/smb/service", { action: "stop" }), "SMB stopped");
  if (r) loaders.servers();
});

$("#smb-restart").addEventListener("click", async () => {
  const cfg = gatherSmbConfig();
  const r = await act(null, () => api("POST", "/api/servers/smb/redeploy", cfg), "SMB redeployed");
  if (r) loaders.servers();
});

$("#smb-edit-config").addEventListener("click", async () => {
  stopSmbLogPoll();
  try {
    const d = await api("GET", "/api/servers/smb");
    const cfg = d.config || {};
    const form = $("#smb-config-form");
    form.elements.username.value = cfg.username || "";
    form.elements.password.value = cfg.password || "";
    smbShares = (cfg.shares || []).map((s) => ({ name: s.name || "", path: s.path || "" }));
    if (smbShares.length === 0) smbShares = [{ name: "", path: "" }];
    renderSmbShareRows();
    updateSmbStartButton();
    $("#smb-config-error").textContent = "";
    $("#smb-state-running").classList.add("hidden");
    $("#smb-state-configure").classList.remove("hidden");
    const badge = $("#smb-state-badge");
    badge.className = "badge ok";
    badge.textContent = "Editing";
  } catch (e) { toast(e.message, "error"); }
});

$("#smb-refresh").addEventListener("click", loaders.servers);
$("#smb-logs-refresh").addEventListener("click", fetchSmbLogs);

// Server service actions (install, start, stop, restart)
function serverAction(apiPath, action, onSuccess) {
  return async () => {
    const r = await act(null, () => api("POST", apiPath, { action }), `${action} ${apiPath.split("/").pop()}`);
    if (r) {
      if (onSuccess) onSuccess(r);
      loaders.servers();
    }
  };
}

function installAction(apiPath) {
  return async () => {
    const r = await act(null, () => api("POST", apiPath), `Install ${apiPath.split("/").pop()}`);
    if (r) { loaders.servers(); }
  };
}

function renderFtp(d) {
  const status = d.service_active ? "active" : "inactive";
  const cls = d.service_active ? "badge ok" : "badge bad";
  $("#ftp-status").className = cls;
  $("#ftp-status").textContent = d.installed ? (d.service_active ? "Active" : "Stopped") : "Not installed";

  const installed = d.installed;
  const running = d.service_active;
  $("#ftp-install").style.display = installed ? "none" : "inline-block";
  $("#ftp-start").style.display = (installed && !running) ? "inline-block" : "none";
  $("#ftp-stop").style.display = (installed && running) ? "inline-block" : "none";
  $("#ftp-restart").style.display = installed ? "inline-block" : "none";

  const body = $("#ftp-shares-body");
  body.innerHTML = d.shares.map((s) => `<tr>
    <td><b>${esc(s.name)}</b></td>
    <td class="font-mono text-xs break-all">${esc(s.path)}</td>
    <td>${s.exists ? '<span class="badge ok">yes</span>' : '<span class="badge bad">no</span>'}</td>
    <td class="whitespace-nowrap"><button class="btn btn-sm btn-danger" data-ftp-rm="${esc(s.name)}">Remove</button></td>
  </tr>`).join("");
  $("#ftp-users-list").textContent = d.users && d.users.length ? d.users.join(", ") : "(none)";
}

function renderWebdav(d) {
  const status = d.service_active ? "active" : "inactive";
  const cls = d.service_active ? "badge ok" : "badge bad";
  $("#webdav-status").className = cls;
  $("#webdav-status").textContent = d.installed ? (d.service_active ? "Active" : "Stopped") : "Not installed";

  const installed = d.installed;
  const running = d.service_active;
  $("#webdav-install").style.display = installed ? "none" : "inline-block";
  $("#webdav-start").style.display = (installed && !running) ? "inline-block" : "none";
  $("#webdav-stop").style.display = (installed && running) ? "inline-block" : "none";
  $("#webdav-restart").style.display = installed ? "inline-block" : "none";

  const body = $("#webdav-shares-body");
  body.innerHTML = d.shares.map((s) => `<tr>
    <td><b>${esc(s.name)}</b></td>
    <td class="font-mono text-xs break-all">${esc(s.path)}</td>
    <td>${s.exists ? '<span class="badge ok">yes</span>' : '<span class="badge bad">no</span>'}</td>
    <td class="whitespace-nowrap"><button class="btn btn-sm btn-danger" data-webdav-rm="${esc(s.name)}">Remove</button></td>
  </tr>`).join("");
  $("#webdav-users-list").textContent = d.users && d.users.length ? d.users.join(", ") : "(none)";
}

// FTP buttons
$("#ftp-install").addEventListener("click", installAction("/api/servers/ftp/install"));
$("#ftp-start").addEventListener("click", serverAction("/api/servers/ftp/service", "start"));
$("#ftp-stop").addEventListener("click", serverAction("/api/servers/ftp/service", "stop"));
$("#ftp-restart").addEventListener("click", serverAction("/api/servers/ftp/service", "restart"));
$("#ftp-refresh").addEventListener("click", loaders.servers);

$("#ftp-share-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const body = {
    name: fd.get("name"),
    path: fd.get("path"),
  };
  const r = await act(e.submitter, () => api("POST", "/api/servers/ftp/shares", body), "Share added");
  if (r) { e.target.reset(); loaders.servers(); }
});

$("#ftp-shares-body").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-ftp-rm]");
  if (!b) return;
  const name = b.dataset.ftpRm;
  if (!(await typedConfirm("Remove FTP share", `Remove share "${name}"?`, "REMOVE"))) return;
  const r = await act(b, () => api("DELETE", `/api/servers/ftp/shares/${encodeURIComponent(name)}`), "Share removed");
  if (r) loaders.servers();
});

// FTP users
$("#ftp-user-add").addEventListener("click", async (e) => {
  const name = $("#ftp-user-name").value.trim();
  const pass = $("#ftp-user-pass").value;
  if (!name || !pass) { toast("Username and password required", "error"); return; }
  const r = await act(e.target, () => api("POST", "/api/servers/ftp/users", { username: name, password: pass }), "User added");
  if (r) {
    $("#ftp-user-name").value = "";
    $("#ftp-user-pass").value = "";
    loaders.servers();
  }
});

// WebDAV buttons
$("#webdav-install").addEventListener("click", installAction("/api/servers/webdav/install"));
$("#webdav-start").addEventListener("click", serverAction("/api/servers/webdav/service", "start"));
$("#webdav-stop").addEventListener("click", serverAction("/api/servers/webdav/service", "stop"));
$("#webdav-restart").addEventListener("click", serverAction("/api/servers/webdav/service", "restart"));
$("#webdav-refresh").addEventListener("click", loaders.servers);

$("#webdav-share-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const body = {
    name: fd.get("name"),
    path: fd.get("path"),
  };
  const r = await act(e.submitter, () => api("POST", "/api/servers/webdav/shares", body), "Share added");
  if (r) { e.target.reset(); loaders.servers(); }
});

$("#webdav-shares-body").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-webdav-rm]");
  if (!b) return;
  const name = b.dataset.webdavRm;
  if (!(await typedConfirm("Remove WebDAV share", `Remove share "${name}"?`, "REMOVE"))) return;
  const r = await act(b, () => api("DELETE", `/api/servers/webdav/shares/${encodeURIComponent(name)}`), "Share removed");
  if (r) loaders.servers();
});

// WebDAV users
$("#webdav-user-add").addEventListener("click", async (e) => {
  const name = $("#webdav-user-name").value.trim();
  const pass = $("#webdav-user-pass").value;
  if (!name || !pass) { toast("Username and password required", "error"); return; }
  const r = await act(e.target, () => api("POST", "/api/servers/webdav/users", { username: name, password: pass }), "User added");
  if (r) {
    $("#webdav-user-name").value = "";
    $("#webdav-user-pass").value = "";
    loaders.servers();
  }
});

// ------------------------------------------------------------ theme
function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  const sel = $("#theme-select");
  if (sel) sel.value = theme;
  localStorage.setItem("dm-theme", theme);
}
function initTheme() {
  const sel = $("#theme-select");
  if (!sel) return;
  const saved = localStorage.getItem("dm-theme") || "dark";
  applyTheme(saved);
  sel.addEventListener("change", (e) => applyTheme(e.target.value));
}

// ------------------------------------------------------------ init
initTheme();
resetSyncForm();
showTab(currentTab, false);
