let syncState = { initialized: false, jobs: [], schedule: null };

function resetSyncForm() {
  const form = $("#sync-form");
  if (!form) return;
  form.reset(); form.elements.id.value = "";
  $("#sj-title").textContent = "New sync job";
  syncState.schedule.set({ type: "manual" });
}

export async function init(shared) {
  if (syncState.initialized) return;
  syncState.initialized = true;
  const scheduleRoot = $("[data-schedule='sj']");
  syncState.schedule = shared.scheduleWidget(scheduleRoot);

  async function loadSyncJobs() {
    const r = await shared.api("GET", "/api/sync/jobs");
    syncState.jobs = r.jobs || [];
    const body = $("#sync-body");
    if (body) {
      body.innerHTML = syncState.jobs.map((j) => {
        const lr = j.last_run || {};
        const st = j.running ? '<span class="badge warn">running</span>' : lr.status ? (lr.status === "ok" ? '<span class="badge ok">ok</span>' : `<span class="badge bad">${lr.status}</span>`) : '<span class="badge muted">never</span>';
        return `<tr><td><b>${j.name}</b>${j.enabled ? "" : ' <span class="badge muted">disabled</span>'}</td><td class="font-mono text-xs">${j.source}<br>→ ${j.destination}</td><td>${j.mode}</td><td class="text-xs">${j.schedule_text}</td><td>${st}<div class="text-xs text-slate-500">${shared.fmtTime(lr.finished_at || lr.started_at)}</div>${lr.error ? `<div class="text-xs text-red-700">${lr.error}</div>` : ""}</td><td class="whitespace-nowrap"><button class="btn btn-sm" data-j="run" data-id="${j.id}">Run</button><button class="btn btn-sm" data-j="edit" data-id="${j.id}">Edit</button><button class="btn btn-sm" data-j="log" data-id="${j.id}">Log</button><button class="btn btn-sm btn-danger" data-j="delete" data-id="${j.id}" data-name="${j.name}">Delete</button></td></tr>`;
      }).join("") || `<tr><td colspan="6" class="text-slate-400">No sync jobs</td></tr>`;
    }
  }

  const body = $("#sync-body");
  if (body) {
    body.addEventListener("click", async (e) => {
      const b = e.target.closest("button[data-j]");
      if (!b) return;
      const id = b.dataset.id;
      if (b.dataset.j === "run") { await shared.act(b, () => shared.api("POST", `/api/sync/jobs/${id}/run`), "Started"); setTimeout(loadSyncJobs, 1500); }
      if (b.dataset.j === "edit") {
        const j = syncState.jobs.find((x) => x.id === id);
        shared.fillForm($("#sync-form"), j);
        syncState.schedule.set(j.schedule_spec);
        $("#sj-title").textContent = `Edit: ${j.name}`;
        $("#sync-form").scrollIntoView({ behavior: "smooth" });
      }
      if (b.dataset.j === "log") { window.showTab("dashboard"); }
      if (b.dataset.j === "delete") {
        if (!(await shared.typedConfirm("Delete sync job", `Delete "${b.dataset.name}"? Files are not touched.`, "DELETE"))) return;
        await shared.act(b, () => shared.api("DELETE", `/api/sync/jobs/${id}`), "Deleted"); loadSyncJobs();
      }
    });
  }

  const form = $("#sync-form");
  if (form) {
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const data = shared.formData(e.target);
      data.schedule_spec = syncState.schedule.spec();
      const id = data.id; delete data.id;
      const r = await shared.act(e.submitter, () => (id ? shared.api("PUT", `/api/sync/jobs/${id}`, data) : shared.api("POST", "/api/sync/jobs", data)), "Job saved");
      if (r && r.ok) { resetSyncForm(); loadSyncJobs(); }
    });
  }

  const resetBtn = $("#sj-reset");
  if (resetBtn) resetBtn.addEventListener("click", resetSyncForm);

  resetSyncForm();
  await loadSyncJobs();
  window.__tabRefreshers.sync = loadSyncJobs;
}

export async function teardown() {
  window.__tabRefreshers.sync = null;
}


















































































"},{