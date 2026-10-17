import * as shared from "./shared.js";

const { $, $$, esc, toast } = shared;
const modules = {};
const tabMap = {
  dashboard: "./tabs/dashboard.js",
  files: "./tabs/files.js",
  mounts: "./tabs/mounts.js",
  fstab: "./tabs/fstab.js",
  sync: "./tabs/sync.js",
  rclone: "./tabs/rclone.js",
  media: "./tabs/media.js",
  servers: "./tabs/servers.js",
  settings: "./tabs/settings.js"
};

let activeTab = null;
let activeModule = null;

function getCurrentTab() {
  const main = $("main");
  return main ? main.dataset.currentTab || "dashboard" : "dashboard";
}

async function ensureModule(tab) {
  if (!tabMap[tab]) return null;
  if (!modules[tab]) {
    modules[tab] = await import(tabMap[tab]);
  }
  return modules[tab];
}

async function showTab(tab, push = true) {
  const sections = $$("section[data-section]");
  sections.forEach((s) => s.classList.toggle("hidden", s.dataset.section !== tab));
  const navLinks = $$("#nav a");
  navLinks.forEach((a) => a.classList.toggle("active", a.dataset.tab === tab));
  if (push) history.pushState({ tab }, "", tab === "dashboard" ? "/" : `/${tab}`);

  if (activeTab && activeTab !== tab && activeModule && activeModule.teardown) {
    try { await activeModule.teardown(); } catch (err) { console.error("Tab teardown error", err); }
  }

  const mod = await ensureModule(tab);
  if (!mod) return;

  activeTab = tab;
  activeModule = mod;
  shared.setActiveTab(tab);

  if (typeof mod.init === "function") {
    await mod.init(shared);
  }
}

function attachNavHandlers() {
  $$("#nav a").forEach((a) => a.addEventListener("click", (e) => {
    e.preventDefault();
    showTab(a.dataset.tab);
  }));
}

window.addEventListener("popstate", (e) => {
  showTab((e.state && e.state.tab) || "dashboard", false);
});

window.__tabRefreshers = window.__tabRefreshers || {};

attachNavHandlers();
shared.initTheme();
showTab(getCurrentTab(), false).catch((err) => toast(err.message || String(err), "error"));

export { showTab };

window.showTab = showTab;

window.addEventListener("beforeunload", async () => {
  if (activeModule && activeModule.teardown) {
    try { await activeModule.teardown(); } catch (err) { console.error("Before unload teardown failed", err); }
  }
});

window.addEventListener("DOMContentLoaded", () => {
  const main = $("main");
  if (main && main.dataset.currentTab) {
    showTab(main.dataset.currentTab, false).catch((err) => toast(err.message || String(err), "error"));
  }
});

window.__tabModules = modules;












































































































"},{