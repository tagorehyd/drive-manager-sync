let mediaState = { initialized: false, jobs: [] };

function resetMediaForm() {
  const form = $("#media-form");
  if (!form) return;
  form.reset(); form.elements.id.value = ""; $("#mj-title").textContent = "New media job";
}

export async function init(shared) {
  if (mediaState.initialized) return;
  mediaState.initialized = true;

  async function loadMedia() {
    const [j, s] = await Promise.all([shared.api("GET", "/api/media/jobs"), shared.api("GET", "/api/media/settings")]);
    mediaState.jobs = j.jobs || [];
    const form = $("#media-settings");
    if (form) {
      shared.fillForm(form, s.settings || {});
      form.elements.category_rules.value = Object.entries(s.settings.category_rules || {}).map(([k, v]) => `${k}=${v}`).join("\n");
    }
    const llm = $("#llm-key");
    if (llm) { llm.textContent = s.settings.llm_key_set ? "API key set" : "no API key"; llm.className = `badge ml-1 ${s.settings.llm_key_set ? "ok" : "warn"}`; }
    const body = $("#media-body");
    if (body) {
      body.innerHTML = mediaState.jobs.map((m) => {
        const ls = m.last_scan || {};
        const c = ls.counts || {};
        return `<tr><td><b>${m.name}</b>${m.enabled ? "" : ' <span class="badge muted">disabled</span>'}</td><td class="font-mono text-xs">${m.source}<br>→ ${m.destination}</td><td>${m.mode}</td><td>${m.running ? '<span class="badge warn">scanning</span>' : ls.status ? (ls.status === "ok" ? '<span class="badge ok">ok</span>' : '<span class="badge bad">failed</span>') : '<span class="badge muted">never</span>'}<div class="text-xs text-slate-500">${shared.fmtTime(ls.finished_at)} · ${c.processed ?? 0} processed · ${c.unrecognised ?? 0} unrecognised · ${c.failed ?? 0} failed</div>${(ls.errors || []).map((x) => `<div class="text-xs text-red-700">${x}</div>`).join("")}</td><td class="whitespace-nowrap"><button class="btn btn-sm" data-mj="scan" data-id="${m.id}">Scan now</button><button class="btn btn-sm" data-mj="records" data-id="${m.id}">Files</button><button class="btn btn-sm" data-mj="edit" data-id="${m.id}">Edit</button><button class="btn btn-sm" data-mj="log" data-id="${m.id}">Log</button><button class="btn btn-sm btn-danger" data-mj="delete" data-id="${m.id}" data-name="${m.name}">Delete</button></td></tr>`;
      }).join("") || `<tr><td colspan="5" class="text-slate-400">No media jobs</td></tr>`;
    }
  }

  const body = $("#media-body");
  if (body) body.addEventListener("click", async (e) => {
    const b = e.target.closest("button[data-mj]");
    if (!b) return;
    const id = b.dataset.id;
    if (b.dataset.mj === "scan") { await shared.act(b, () => shared.api("POST", `/api/media/jobs/${id}/scan`), "Scan started"); setTimeout(loadMedia, 2000); }
    if (b.dataset.mj === "edit") { const m = mediaState.jobs.find((x) => x.id === id); shared.fillForm($("#media-form"), m); $("#mj-title").textContent = `Edit: ${m.name}`; }
    if (b.dataset.mj === "log") { window.showTab("dashboard"); }
    if (b.dataset.mj === "records") {
      const r = await shared.api("GET", `/api/media/jobs/${id}/records`);
      const card = $("#media-records-card");
      if (card) card.classList.remove("hidden");
      const job = $("#media-records-job"); if (job) job.textContent = `(${mediaState.jobs.find((x) => x.id === id).name})`;
      const records = $("#media-records");
      if (records) records.innerHTML = (r.records || []).map((x) => `<tr><td class="font-mono text-xs break-all">${x.source_path}</td><td>${x.status === "done" ? '<span class="badge ok">done</span>' : `<span class="badge bad">${x.status}</span>`}<div class="text-xs">${x.message}</div></td><td class="font-mono text-xs break-all">${x.target}</td><td>${shared.fmtTime(x.updated_at)}</td></tr>`).join("") || `<tr><td colspan="4" class="text-slate-400">Nothing processed yet</td></tr>`;
    }
    if (b.dataset.mj === "delete") {
      if (!(await shared.typedConfirm("Delete media job", `Delete "${b.dataset.name}"? Files are not touched.`, "DELETE"))) return;
      await shared.act(b, () => shared.api("DELETE", `/api/media/jobs/${id}`), "Deleted"); loadMedia();
    }
  });

  const form = $("#media-form");
  if (form) form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const d = shared.formData(e.target); const id = d.id; delete d.id;
    const r = await shared.act(e.submitter, () => (id ? shared.api("PUT", `/api/media/jobs/${id}`, d) : shared.api("POST", "/api/media/jobs", d)), "Media job saved");
    if (r && r.ok) { resetMediaForm(); loadMedia(); }
  });

  const resetBtn = $("#mj-reset");
  if (resetBtn) resetBtn.addEventListener("click", resetMediaForm);

  const settingsForm = $("#media-settings");
  if (settingsForm) settingsForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const d = shared.formData(e.target);
    d.category_rules = shared.safeParseRules(d.category_rules);
    await shared.act(e.submitter, () => shared.api("PUT", "/api/media/settings", d), "Settings saved");
  });

  const previewForm = $("#media-preview");
  if (previewForm) previewForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const r = await shared.act(e.submitter, () => shared.api("POST", "/api/media/preview", { filename: e.target.elements.filename.value, use_llm: true }));
    if (r) { const out = $("#media-preview-out"); if (out) { out.classList.remove("hidden"); out.textContent = JSON.stringify(r, null, 2);} }
  });

  resetMediaForm();
  await loadMedia();
  window.__tabRefreshers.media = loadMedia;
}

export async function teardown() {
  window.__tabRefreshers.media = null;
}







































































































"},{