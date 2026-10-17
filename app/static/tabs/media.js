let rcloneState = { initialized: false, data: [] };

export async function init(shared) {
  if (rcloneState.initialized) return;
  rcloneState.initialized = true;

  async function loadRemotes() {
    const d = await shared.api("GET", "/api/rclone/remotes");
    const meta = $("#rclone-meta");
    if (meta) meta.textContent = d.available ? `${d.remotes.length} remote(s) in /config/rclone.conf` : "rclone binary not available!";
    rcloneState.data = d.remotes || [];
    const body = $("#rclone-body");
    if (body) {
      body.innerHTML = rcloneState.data.map((r) => `<tr><td><b>${r.name}</b><div class="text-xs text-slate-500 font-mono">${Object.entries(r.options || {}).map(([k, v]) => `${k}=${v}`).join(", ")}</div></td><td>${r.type}</td><td class="font-mono text-xs">${r.mount_point || "—"}</td><td>${r.mounted ? '<span class="badge ok">yes</span>' : '<span class="badge bad">no</span>'}</td><td class="whitespace-nowrap"><button class="btn btn-sm" data-r="mount" data-n="${r.name}">Mount</button><button class="btn btn-sm" data-r="unmount" data-n="${r.name}">Unmount</button><button class="btn btn-sm" data-r="edit" data-n="${r.name}">Edit</button><button class="btn btn-sm btn-danger" data-r="delete" data-n="${r.name}">Delete</button></td></tr>`).join("") || `<tr><td colspan="5" class="text-slate-400">No remotes</td></tr>`;
    }
  }

  const body = $("#rclone-body");
  if (body) {
    body.addEventListener("click", async (e) => {
      const b = e.target.closest("button[data-r]");
      if (!b) return;
      const n = encodeURIComponent(b.dataset.n);
      if (b.dataset.r === "edit") {
        const r = rcloneState.data.find((x) => x.name === b.dataset.n);
        const form = $("#remote-form");
        if (form) shared.fillForm(form, { name: r.name, type: r.type, options: Object.entries(r.options || {}).map(([k, v]) => `${k}=${v}`).join("\n") });
        return;
      }
      if (b.dataset.r === "delete") {
        if (!(await shared.typedConfirm("Delete remote", `Delete rclone remote "${b.dataset.n}"?`, "DELETE"))) return;
        await shared.act(b, () => shared.api("DELETE", `/api/rclone/remotes/${n}`), "Remote deleted");
      } else {
        await shared.act(b, () => shared.api("POST", `/api/rclone/remotes/${n}/${b.dataset.r}`), `${b.dataset.r} done`);
      }
      await loadRemotes();
    });
  }

  const remoteForm = $("#remote-form");
  if (remoteForm) remoteForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const d = shared.formData(e.target);
    const options = {};
    (d.options || "").split("\n").forEach((line) => { const i = line.indexOf("="); if (i > 0) options[line.slice(0, i).trim()] = line.slice(i + 1).trim(); });
    const r = await shared.act(e.submitter, () => shared.api("POST", "/api/rclone/remotes", { name: d.name, type: d.type, options }), "Remote saved");
    if (r && r.ok) { e.target.reset(); await loadRemotes(); }
  });

  const syncForm = $("#rsync-form");
  if (syncForm) syncForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    await shared.act(e.submitter, () => shared.api("POST", "/api/rclone/sync", shared.formData(e.target)), "Sync started");
  });

  await loadRemotes();
  window.__tabRefreshers.rclone = loadRemotes;
}

export async function teardown() {
  window.__tabRefreshers.rclone = null;
}



























































































"},{