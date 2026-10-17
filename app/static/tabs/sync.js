let fstabState = { initialized: false, data: { entries: [] } };

function renderFstab() {
  const q = $("#fstab-filter");
  const rows = (fstabState.data.entries || []).filter((x) => !q || !q.value || `${x.device} ${x.mountpoint} ${x.fstype} ${x.options}`.toLowerCase().includes(q.value.toLowerCase()));
  const body = $("#fstab-body");
  if (body) {
    body.innerHTML = rows.map((x) => `<tr><td class="font-mono text-xs break-all">${x.device}</td><td class="font-mono text-xs">${x.mountpoint}</td><td>${x.fstype}</td><td class="font-mono text-xs break-all">${x.options}</td><td>${x.mounted ? '<span class="badge ok">yes</span>' : '<span class="badge bad">no</span>'}</td><td>${x.automount ? '<span class="badge ok">yes</span>' : '<span class="badge muted">no</span>'}</td><td>${x.nofail ? '<span class="badge ok">yes</span>' : '<span class="badge muted">no</span>'}</td><td class="whitespace-nowrap"><button class="btn btn-sm" data-f="mount" data-mp="${x.mountpoint}">Mount</button> <button class="btn btn-sm" data-f="unmount" data-mp="${x.mountpoint}">Unmount</button> <button class="btn btn-sm btn-indigo" data-f="edit" data-mp="${x.mountpoint}">Edit</button> <button class="btn btn-sm" data-f="import" data-mp="${x.mountpoint}" data-device="${x.device}" data-fstype="${x.fstype}" data-options="${x.options}">Import</button> <button class="btn btn-sm btn-danger" data-f="remove" data-mp="${x.mountpoint}">Remove</button></td></tr>`).join("") || `<tr><td colspan="8" class="text-slate-400">No entries</td></tr>`;
  }
  const meta = $("#fstab-meta");
  if (meta) meta.textContent = `${fstabState.data.entries.length} entries · read ${new Date(fstabState.data.read_at || Date.now()).toLocaleString()}${fstabState.data.error ? " · ERROR: " + fstabState.data.error : ""}`;
}

export async function init(shared) {
  if (fstabState.initialized) return;
  fstabState.initialized = true;

  async function reloadFstab() {
    fstabState.data = await shared.api("GET", "/api/fstab");
    renderFstab();
  }

  const filter = $("#fstab-filter");
  if (filter) filter.addEventListener("input", renderFstab);

  const reload = $("#fstab-reload");
  if (reload) reload.addEventListener("click", async (e) => {
    const r = await shared.act(e.target, () => shared.api("POST", "/api/fstab/reload"), "fstab reloaded");
    if (r) { fstabState.data = r; renderFstab(); }
  });

  const body = $("#fstab-body");
  if (body) {
    body.addEventListener("click", async (e) => {
      const b = e.target.closest("button[data-f]");
      if (!b) return;
      const action = b.dataset.f;
      const bodyType = { mountpoint: b.dataset.mp };
      if (action === "remove") {
        const word = await shared.typedConfirm("Remove fstab entry", `Remove ${b.dataset.mp} from the host /etc/fstab (a backup is written to /etc/fstab.drive-manager.bak).`, "REMOVE");
        if (!word) return;
        bodyType.confirm = word;
      } else if (action === "import") {
        shared.openImportModal(b.dataset.mp, b.dataset.device, b.dataset.fstype, b.dataset.options);
        return;
      } else if (action === "edit") {
        const entry = fstabState.data.entries.find((x) => x.mountpoint === b.dataset.mp);
        if (!entry) return;
        shared.openFstabEditModal(entry);
        return;
      }
      const r = await shared.act(b, () => shared.api("POST", `/api/fstab/${action}`, bodyType), `${action} done`);
      if (r && r.fstab) { fstabState.data = r.fstab; renderFstab(); }
    });
  }

  const cancel = $("#fstab-edit-cancel");
  if (cancel) cancel.addEventListener("click", () => { const modal = $("#fstab-edit-modal"); if (modal) modal.classList.add("hidden"); });

  const ok = $("#fstab-edit-ok");
  if (ok) ok.addEventListener("click", async () => {
    const bodyData = {
      mountpoint: $("#edit-mountpoint").value,
      automount: $("#edit-automount").checked,
      nofail: $("#edit-nofail").checked,
      noauto: !$("#edit-noauto").checked,
      options: $("#edit-options").value || undefined,
    };
    const r = await shared.api("POST", "/api/fstab/edit", bodyData);
    if (r) {
      const modal = $("#fstab-edit-modal"); if (modal) modal.classList.add("hidden");
      shared.toast("fstab entry updated", "ok");
      fstabState.data = r.fstab;
      renderFstab();
    }
  });

  await reloadFstab();
  window.__tabRefreshers.fstab = reloadFstab;
}

export async function teardown() {
  window.__tabRefreshers.fstab = null;
}
































































































"},{