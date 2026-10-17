let settingsState = { initialized: false };

export async function init(shared) {
  if (settingsState.initialized) return;
  settingsState.initialized = true;

  async function loadSettings() {
    const [n, roots] = await Promise.all([shared.api("GET", "/api/settings/notifications"), shared.api("GET", "/api/settings/browse-roots")]);
    const notifyForm = $("#notify-form");
    if (notifyForm) {
      shared.fillForm(notifyForm, n);
      notifyForm.elements.bot_token.value = "";
    }
    const tgToken = $("#tg-token-set");
    if (tgToken) tgToken.textContent = n.bot_token_set ? "(saved)" : "(not set)";
    const rootsForm = $("#roots-form");
    if (rootsForm) rootsForm.elements.roots.value = (roots.roots || []).join("\n");
    const rootsEnv = $("#roots-env");
    if (rootsEnv) rootsEnv.textContent = roots.env_override ? "BROWSE_ROOTS environment variable overrides this list." : "One absolute path per line.";
  }

  const notifyForm = $("#notify-form");
  if (notifyForm) notifyForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    await shared.act(e.submitter, () => shared.api("PUT", "/api/settings/notifications", shared.formData(e.target)), "Saved");
    loadSettings();
  });

  const notifyTest = $("#notify-test");
  if (notifyTest) notifyTest.addEventListener("click", (e) => shared.act(e.target, () => shared.api("POST", "/api/settings/notifications/test"), "Test sent (check Telegram / webhook)"));

  const passwordForm = $("#password-form");
  if (passwordForm) passwordForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const r = await shared.act(e.submitter, () => shared.api("POST", "/api/auth/change-password", shared.formData(e.target)), "Password changed");
    if (r && r.ok) e.target.reset();
  });

  const rootsForm = $("#roots-form");
  if (rootsForm) rootsForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const roots = e.target.elements.roots.value.split("\n").map((x) => x.trim()).filter(Boolean);
    await shared.act(e.submitter, () => shared.api("PUT", "/api/settings/browse-roots", { roots }), "Saved");
  });

  const themeSelect = $("#theme-select");
  if (themeSelect) themeSelect.addEventListener("change", (e) => shared.applyTheme(e.target.value));

  loadSettings();
  window.__tabRefreshers.settings = loadSettings;
}

export async function teardown() {
  window.__tabRefreshers.settings = null;
}




























































































































"},{