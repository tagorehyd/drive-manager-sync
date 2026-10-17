let mountsState = { initialized: false };

async function loadMounts() {
  const [d, r] = await Promise.all([shared.api("GET", "/api/mounts"), shared.api("GET", "/api/rclone/remotes")]);
  const mfRemote = $("#mf-remote");
  if (mfRemote) mfRemote.innerHTML = (r.remotes || []).map((x) => `<option>${x.name}</option>`).join("") || "<option value=''></option>";
  const mountsBody = $("#mounts-body");
  if (mountsBody) {
    mountsBody.innerHTML = (d.mounts || []).map((m) => {
      const s = m.status || {};
      const src = m.type === "nfs" ? `${m.host}:${m.export}` : m.type === "smb" ? `//${m.host}/${m.share}` : `${m.remote}:${m.subpath || ""}`;
      return `<tr><td><b>${m.name}</b><div class="text-xs text-slate-500">${m.type}${m.persist ? " · fstab" : ""}${m.auto_mount ? " · auto" : ""}</div></td>
        <td class="font-mono text-xs">${src}</td><td class="font-mono text-xs">${m.mount_point}</td>
        <td>${m.mounted ? '<span class="badge ok">yes</span>' : '<span class="badge bad">no</span>'}</td><td>${m.mounted ? '<span class="badge ok">yes</span>' : '<span class="badge muted">?</span>'}</td><td>${m.mounted ? '<span class="badge ok">yes</span>' : '<span class="badge muted">?</span>'}</td><td>${m.mounted ? '<span class="badge ok">yes</span>' : '<span class="badge muted">?</span>'}</td><td>${m.mounted ? '<span class="badge ok">yes</span>' : '<span class="badge muted">?</span>'}</td><td>${m.mounted ? '<span class="badge ok">yes</span>' : '<span class="badge bad">no</span>'}</td>
        <td class="whitespace-nowrap">${!m.mounted ? `<button class="btn btn-sm" data-m="mount" data-id="${m.id}">Mount</button>` : `<button class="btn btn-sm" data-m="unmount" data-id="${m.id}">Unmount</button>`} <button class="btn btn-sm" data-m="check" data-id="${m.id}">Check</button> <button class="btn btn-sm btn-danger" data-m="delete" data-id="${m.id}" data-name="${m.name}">Delete</button></td></tr>`;
    }).join("") || `<tr><td colspan="10" class="text-slate-400">No managed mounts yet</td></tr>`;
  }
}

async function exportMountsState() {
  await loadMounts();
}

export async function init(shared) {
  if (mountsState.initialized) return;
  mountsState.initialized = true;
  const mountType = $("#mf-type");
  if (mountType) mountType.addEventListener("change", () => shared.mountTypeChanged());
  const form = $("#mount-form");
  if (form) {
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const r = await shared.act(e.submitter, () => shared.api("POST", "/api/mounts", shared.formData(e.target)));
      if (r && r.ok) {
        shared.toast(r.result && !r.result.ok ? `Saved, but mount failed: ${r.result.result.stderr || "see status"}` : "Mount added", r.result && !r.result.ok ? "error" : "ok");
        e.target.reset(); await loadMounts();
      }
    });
  }

  const body = $("#mounts-body");
  if (body) {
    body.addEventListener("click", async (e) => {
      const b = e.target.closest("button[data-m]");
      if (!b) return;
      const id = b.dataset.id; const action = b.dataset.m;
      if (action === "delete") {
        if (!(await shared.typedConfirm("Delete mount", `Unmount and remove "${b.dataset.name}" (and its fstab line if persisted).`, "DELETE"))) return;
        await shared.act(b, () => shared.api("DELETE", `/api/mounts/${id}`), "Mount removed");
      } else {
        await shared.act(b, () => shared.api("POST", `/api/mounts/${id}/${action}`), action === "check" ? "Checked" : `${action} done`);
      }
      await loadMounts();
    });
  }

  const checkAll = $("#mounts-check-all");
  if (checkAll) {
    checkAll.addEventListener("click", async (e) => {
      await shared.act(e.target, () => shared.api("POST", "/api/mounts/check-all"), "All mounts checked");
      await loadMounts();
    });
  }

  if (shared.mountTypeChanged) shared.mountTypeChanged();
  await loadMounts();
  window.__tabRefreshers.mounts = exportMountsState;
}

export async function teardown() {
  window.__tabRefreshers.mounts = null;
}















































































































"},{