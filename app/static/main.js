export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
export const esc = (v) => String(v ?? "").replace(/[&<>\"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
export const fmtTime = (iso) => (iso ? new Date(iso).toLocaleString() : "—");
export const yes = (v, okText = "yes", badText = "no") => (v ? `<span class="badge ok">${okText}</span>` : `<span class="badge bad">${badText}</span>`);
export const tri = (v) => (v === undefined || v === null ? `<span class="badge muted">?</span>` : yes(v));

export function toast(msg, kind = "info") {
  const el = document.createElement("div");
  el.className = "toast";
  el.style.background = kind === "error" ? "#b91c1c" : kind === "ok" ? "#15803d" : "#334155";
  el.textContent = msg;
  const host = $("#toasts");
  if (host) host.appendChild(el);
  setTimeout(() => el.remove(), kind === "error" ? 9000 : 4000);
}

export async function api(method, url, body) {
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

export async function act(btn, fn, okMsg) {
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

export function formData(form) {
  const out = {};
  for (const el of form.elements) {
    if (!el.name) continue;
    out[el.name] = el.type === "checkbox" ? el.checked : el.type === "number" ? Number(el.value) : el.value;
  }
  return out;
}

export function fillForm(form, data) {
  for (const el of form.elements) {
    if (!el.name || !(el.name in data)) continue;
    if (el.type === "checkbox") el.checked = !!data[el.name];
    else el.value = data[el.name] ?? "";
  }
}

export function typedConfirm(title, text, word) {
  return new Promise((resolve) => {
    const titleEl = $("#confirm-title");
    const textEl = $("#confirm-text");
    const input = $("#confirm-input");
    const ok = $("#confirm-ok");
    if (titleEl) titleEl.textContent = title;
    if (textEl) textEl.innerHTML = `${esc(text)}<br>Type <b class="font-mono">${esc(word)}</b> to confirm.`;
    if (input) { input.value = ""; input.focus(); }
    if (ok) ok.disabled = true;
    const modal = $("#confirm");
    if (modal) modal.classList.remove("hidden");
    const onInput = () => { if (ok) ok.disabled = input.value !== word; };
    if (input) input.oninput = onInput;
    const close = (v) => { if (modal) modal.classList.add("hidden"); resolve(v); };
    const cancel = $("#confirm-cancel");
    const form = $("#confirm-form");
    if (cancel) cancel.onclick = () => close(null);
    if (form) form.onsubmit = (e) => { e.preventDefault(); if (input.value === word) close(input.value); };
  });
}

export function scheduleWidget(root) {
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
  return { spec, set(s) { s = s || { type: "manual" }; get("type").value = s.type || "manual"; for (const k of ["every", "minute", "time", "weekday", "day", "cron"]) if (s[k] !== undefined) get(k).value = s[k]; preview(); }, preview };
}

export const rootState = { activeTab: null };

export function setActiveTab(tab) {
  rootState.activeTab = tab;
}

export function getActiveTab() {
  return rootState.activeTab;
}

export function mountTypeChanged() {
  const t = $("#mf-type");
  if (!t) return;
  const field = $("#mf-type");
  const type = field.value;
  $$("#mount-form .mf").forEach((el) => el.classList.toggle("hidden", !el.classList.contains(type)));
  const placeholders = {
    nfs: "hard,noatime,_netdev,nofail,x-systemd.automount (default)",
    smb: "vers=3.0,uid=1000,gid=1000,file_mode=0664,dir_mode=0775,_netdev,nofail (default)",
    rclone: "VFS cache settings (see rclone docs), pass/password are obscured automatically"
  };
  const input = $("#mf-options");
  if (input) input.placeholder = placeholders[type] || "";
  const hints = {
    nfs: "NFSv4 with hard,noatime,_netdev,nofail,x-systemd.automount by default",
    smb: "Credentials are stored only on the host in a root-only file.",
    rclone: "Rclone mounts use a VFS write cache in /data/rclone-cache. OAuth remotes: run rclone config on desktop."
  };
  const hint = $("#mf-hint");
  if (hint) hint.textContent = hints[type] || "";
}

export function openImportModal(mountpoint, device, fstype, options) {
  let type = "nfs";
  if (fstype === "cifs" || fstype === "smb") type = "smb";
  else if (device.startsWith("//")) type = "smb";
  else if (device.startsWith("UUID=")) type = "nfs";
  const nameEl = $("#import-name");
  if (nameEl) nameEl.value = mountpoint.split("/").pop() || "imported-mount";
  const hostEl = $("#import-host");
  if (hostEl) hostEl.value = "";
  const exportEl = $("#import-export");
  if (exportEl) exportEl.value = "";
  const shareEl = $("#import-share");
  if (shareEl) shareEl.value = "";
  const usernameEl = $("#import-username");
  if (usernameEl) usernameEl.value = "";
  const passwordEl = $("#import-password");
  if (passwordEl) passwordEl.value = "";
  const optionsEl = $("#import-options");
  if (optionsEl) optionsEl.value = options || "";
  const autoEl = $("#import-auto-mount");
  if (autoEl) autoEl.checked = true;
  if (type === "nfs" && device.includes(":")) {
    const [host, exportPath] = device.split(":");
    if (hostEl) hostEl.value = host;
    if (exportEl) exportEl.value = exportPath;
  } else if (type === "smb" && device.startsWith("//")) {
    const parts = device.substring(2).split("/");
    if (hostEl) hostEl.value = parts[0];
    if (shareEl) shareEl.value = parts[1] || "";
  } else if (device.startsWith("UUID=") || device.startsWith("LABEL=")) {
    toast("Local filesystem (ext4/xfs/etc) cannot be imported as NFS/SMB mount", "error");
    return;
  }
  $$("#import-form .mf").forEach((el) => el.classList.toggle("hidden", !el.classList.contains(type)));
  const modal = $("#import-modal");
  if (modal) modal.classList.remove("hidden");
  if (nameEl) nameEl.focus();
}

export function openFstabEditModal(entry) {
  const modal = $("#fstab-edit-modal");
  const mountpoint = $("#edit-mountpoint");
  const mountpointDisplay = $("#edit-mountpoint-display");
  const noauto = $("#edit-noauto");
  const automount = $("#edit-automount");
  const nofail = $("#edit-nofail");
  const options = $("#edit-options");
  if (mountpoint) mountpoint.value = entry.mountpoint;
  if (mountpointDisplay) mountpointDisplay.textContent = entry.mountpoint;
  if (noauto) noauto.checked = entry.options.includes("noauto");
  if (automount) automount.checked = entry.automount === true;
  if (nofail) nofail.checked = entry.nofail === true;
  if (options) options.value = entry.options || "";
  if (modal) modal.classList.remove("hidden");
}

export function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  const sel = $("#theme-select");
  if (sel) sel.value = theme;
  localStorage.setItem("dm-theme", theme);
}

export function initTheme() {
  const sel = $("#theme-select");
  if (!sel) return;
  const saved = localStorage.getItem("dm-theme") || "dark";
  applyTheme(saved);
  sel.addEventListener("change", (e) => applyTheme(e.target.value));
}

export function renderStatusBadge(el, installed, active) {
  if (!el) return;
  const running = !!active;
  el.className = installed ? (running ? "badge ok" : "badge bad") : "badge muted";
  el.textContent = installed ? (running ? "Active" : "Stopped") : "Not installed";
}

export function installAction(apiPath) {
  return async () => {
    const r = await act(null, () => api("POST", apiPath), `Install ${apiPath.split("/").pop()}`);
    if (r) {
      if (window.__tabRefreshers && window.__tabRefreshers.servers) window.__tabRefreshers.servers();
    }
  };
}

export function serverAction(apiPath, action) {
  return async () => {
    const r = await act(null, () => api("POST", apiPath, { action }), `${action} ${apiPath.split("/").pop()}`);
    if (r && window.__tabRefreshers && window.__tabRefreshers.servers) window.__tabRefreshers.servers();
  };
}

export function safeParseRules(text) {
  const rules = {};
  if (!text) return rules;
  text.split("\n").forEach((line) => { const i = line.indexOf("="); if (i > 0) rules[line.slice(0, i).trim()] = line.slice(i + 1).trim(); });
  return rules;
}

export function makeServerRows(rows, kind) {
  return rows.map((s) => `<tr>
    <td><b>${esc(s.name)}</b></td>
    <td class="font-mono text-xs break-all">${esc(s.path)}</td>
    <td>${s.exists ? '<span class="badge ok">yes</span>' : '<span class="badge bad">no</span>'}</td>
    <td class="whitespace-nowrap"><button class="btn btn-sm btn-danger" data-${kind}-rm="${esc(s.name)}">Remove</button></td>
  </tr>`).join("");
}

window.__tabRefreshers = window.__tabRefreshers || {};

export const Delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export function arrayFromObjectMap(obj) { return Object.entries(obj || {}).map(([k, v]) => `${k}=${v}`).join("\n"); }

export function countDepth(path) {
  if (!path) return 0;
  return path.split("/").filter(Boolean).length;
}

export function rootIndexOf(path, roots) {
  for (let i = 0; i < roots.length; i++) {
    const r = roots[i];
    if (path === r || path.startsWith(r + "/")) return i;
  }
  return -1;
}

export function isAncestorOrSelf(anc, path) {
  return path === anc || path.startsWith(anc + "/");
}

export function childrenOf(parentPath, d) {
  const kids = [];
  const pd = countDepth(parentPath);
  for (const dir of (d.dirs || [])) {
    if (countDepth(dir.path) === pd + 1 && isAncestorOrSelf(parentPath, dir.path)) kids.push({ ...dir, type: "dir" });
  }
  for (const f of (d.files || [])) {
    if (countDepth(f.path) === pd + 1 && isAncestorOrSelf(parentPath, f.path)) kids.push({ ...f, type: "file" });
  }
  kids.sort((a, b) => {
    const ta = a.type === "dir" ? 0 : 1;
    const tb = b.type === "dir" ? 0 : 1;
    if (ta !== tb) return ta - tb;
    return a.name.localeCompare(b.name);
  });
  return kids;
}

export function fileIcon(name) {
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

export function fmtSize(bytes) {
  if (bytes == null) return "—";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0, s = bytes;
  while (s >= 1024 && i < u.length - 1) { s /= 1024; i++; }
  return `${s.toFixed(i ? 1 : 0)} ${u[i]}`;
}

export function ownershipCell(o) {
  if (!o) return "—";
  if (o.error) return `<span class="badge bad">${esc(o.error)}</span>`;
  return `<div class="text-xs">owner ${o.owner_uid}:${o.owner_gid} · app ${o.app_uid}:${o.app_gid} ${o.mode ? `· ${esc(o.mode)}` : ""}</div>
    ${o.uid_match && o.gid_match ? '<span class="badge ok">match</span>' : ''}`;
}

export function bindToggle(selector, handler) {
  const el = $(selector);
  if (el) el.addEventListener("click", handler);
}

export function getFromForm(name, fallback = "") {
  const el = $(name);
  return el ? el.value : fallback;
}

export function setText(id, text) {
  const el = $(id);
  if (el) el.textContent = text;
}

export function setHtml(id, html) {
  const el = $(id);
  if (el) el.innerHTML = html;
}

export function safeString(value, fallback = "") {
  return value == null ? fallback : String(value);
}

export const browser = window;
"use strict";
},{