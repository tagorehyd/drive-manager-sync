let filesState = { initialized: false, fbPath: "", fbExpanded: new Set(), fbCache: new Map() };

function renderTreeHtml(curPath, roots) {
  const html = [];
  const visibleRoots = roots.filter((r) => r === roots[0] || rootIndexOf(r, roots) >= 0);
  for (const root of visibleRoots) {
    const rootData = filesState.fbCache.get(root) || { dirs: [], files: [], roots: [] };
    const kids = shared.childrenOf(root, rootData);
    renderTreeNode(html, root, curPath, 0, kids);
  }
  return html.join("");
}

function renderTreeNode(html, nodePath, curPath, level, kids) {
  const nodeName = nodePath.split("/").pop() || nodePath;
  const pad = level * 18;
  const selected = nodePath === curPath ? "bg-indigo-50" : "";
  const isExpanded = filesState.fbExpanded.has(nodePath) || nodePath === curPath || (curPath && curPath.startsWith(nodePath + "/"));
  const expander = `<span class="w-4 text-center cursor-pointer hover:text-indigo-600 shrink-0" data-fb-expand="${nodePath}">${isExpanded ? "▼" : "▶"}</span>`;
  html.push(`<div class="flex items-center gap-1 px-3 py-1.5 text-sm rounded ${selected} border-transparent hover:bg-slate-100 cursor-pointer" data-fb-navdir="" data-fb-path="${nodePath}" style="padding-left:${pad + 8}px"><div class="flex items-center gap-1 min-w-0 flex-1">${expander}<span class="w-4 text-center">📁</span><div class="flex-1 min-w-0 break-all text-slate-800">${nodeName}</div><span class="w-16 text-right text-xs font-mono text-slate-500">—</span><span class="text-xs text-slate-400">dir</span></div></div>`);
  if (isExpanded && kids && kids.length > 0) {
    for (const kid of kids) {
      if (kid.type === "dir") {
        const kidData = filesState.fbCache.get(kid.path) || { dirs: [], files: [] };
        renderTreeNode(html, kid.path, curPath, level + 1, shared.childrenOf(kid.path, kidData));
      } else {
        const kpad = (level + 1) * 18;
        html.push(`<div class="flex items-center gap-1 px-3 py-1.5 text-sm rounded hover:bg-slate-50" data-fb-item="" data-fb-path="${kid.path}" data-fb-type="file" style="padding-left:${kpad + 8}px"><span class="w-4 text-center">${shared.fileIcon(kid.name)}</span><div class="flex-1 min-w-0 break-all text-slate-600">${kid.name}</div><span class="w-16 text-right text-xs font-mono text-slate-500">${shared.fmtSize(kid.size)}</span><span class="text-xs text-slate-400">file</span></div>`);
      }
    }
  }
}

function rootIndexOf(path, roots) {
  for (let i = 0; i < roots.length; i++) {
    const r = roots[i];
    if (path === r || path.startsWith(r + "/")) return i;
  }
  return -1;
}

async function loadDirIntoCache(path) {
  if (filesState.fbCache.has(path)) return filesState.fbCache.get(path);
  try {
    const d = await shared.api("GET", `/api/browse?path=${encodeURIComponent(path)}`);
    filesState.fbCache.set(path, d);
    return d;
  } catch (err) {
    shared.toast(err.message, "error");
    return null;
  }
}

async function ensurePathLoaded(curPath) {
  if (!curPath) { await loadDirIntoCache(""); return; }
  const parts = curPath.split("/").filter(Boolean);
  let acc = "";
  for (const p of parts) {
    acc = acc + "/" + p;
    if (!filesState.fbCache.has(acc)) await loadDirIntoCache(acc);
  }
}

async function renderTree(curPath) {
  const container = $("#fb-tree");
  const empty = $("#fb-empty");
  const loading = $("#fb-loading");
  const pathBar = $("#fb-path-bar");
  const upBtn = $("#fb-up");
  const rootBtn = $("#fb-root");
  if (!container || !empty || !loading || !pathBar || !upBtn || !rootBtn) return;

  await ensurePathLoaded(curPath || "");
  const data = filesState.fbCache.get(curPath || "") || { dirs: [], files: [], roots: [] };
  const roots = data.roots || [];
  const html = [];
  for (const root of roots) {
    const rootData = filesState.fbCache.get(root) || { dirs: [], files: [], roots: [] };
    const kids = shared.childrenOf(root, rootData);
    renderTreeNode(html, root, curPath, 0, kids);
  }
  container.innerHTML = html.join("") || '<div class="px-3 py-2 text-slate-500 text-sm">Empty</div>';
  pathBar.innerHTML = filesState.fbPath ? `Roots${filesState.fbPath.split("/").filter(Boolean).map((seg) => ` / <span class="cursor-pointer text-indigo-600 hover:underline" data-fb-nav="" data-fb-path="${filesState.fbPath.split("/").slice(0, filesState.fbPath.split("/").indexOf(seg)+1).join("/")}">${seg}</span>`).join("")}` : "Roots" : "Roots";
  upBtn.classList.toggle("opacity-50", !curPath);
  rootBtn.classList.toggle("opacity-50", !curPath);
  empty.classList.add("hidden");
  loading.classList.add("hidden");
}

export async function init(sharedRef) {
  if (filesState.initialized) return;
  filesState.initialized = true;

  const render = async () => {
    filesState.fbPath = filesState.fbPath || "";
    await renderTree(filesState.fbPath);
  };

  const upBtn = $("#fb-up");
  if (upBtn) upBtn.addEventListener("click", async () => {
    if (!filesState.fbPath) return;
    const parent = filesState.fbPath.substring(0, filesState.fbPath.lastIndexOf("/"));
    filesState.fbPath = parent || "";
    await renderTree(filesState.fbPath);
  });

  const rootBtn = $("#fb-root");
  if (rootBtn) rootBtn.addEventListener("click", async () => {
    filesState.fbPath = "";
    filesState.fbExpanded = new Set();
    filesState.fbCache = new Map();
    await renderTree("");
  });

  const tree = $("#fb-tree");
  if (tree) {
    tree.addEventListener("click", async (e) => {
      const exp = e.target.closest("[data-fb-expand]");
      if (exp) {
        const p = exp.dataset.fbExpand;
        if (!filesState.fbCache.has(p)) await loadDirIntoCache(p);
        if (filesState.fbExpanded.has(p)) filesState.fbExpanded.delete(p); else filesState.fbExpanded.add(p);
        await renderTree(filesState.fbPath);
      }
      const dirLink = e.target.closest("[data-fb-navdir]");
      if (dirLink) {
        const p = dirLink.dataset.fbPath;
        filesState.fbPath = p;
        let pp = p;
        while (pp) {
          filesState.fbExpanded.add(pp);
          const idx = pp.lastIndexOf("/");
          if (idx <= 0) break;
          pp = pp.substring(0, idx);
        }
        await renderTree(p);
      }
    });
  }

  const pathNav = $("#fb-path-bar");
  if (pathNav) {
    pathNav.addEventListener("click", async (e) => {
      const nav = e.target.closest("[data-fb-nav]");
      if (!nav) return;
      const targetPath = nav.dataset.fbPath;
      if (!targetPath) {
        filesState.fbPath = ""; filesState.fbExpanded = new Set(); filesState.fbCache = new Map();
        await renderTree("");
        return;
      }
      filesState.fbPath = targetPath;
      let pp = targetPath;
      while (pp) {
        filesState.fbExpanded.add(pp);
        const idx = pp.lastIndexOf("/");
        if (idx <= 0) break;
        pp = pp.substring(0, idx);
      }
      await renderTree(targetPath);
    });
  }

  filesState.fbPath = "";
  filesState.fbExpanded = new Set();
  filesState.fbCache = new Map();
  await renderTree("");
}

export async function teardown() {
  filesState.fbPath = "";
  filesState.fbExpanded = new Set();
  filesState.fbCache = new Map();
}

let shared;












































































"},{