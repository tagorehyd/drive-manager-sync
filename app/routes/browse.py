"""Folder browsing / picker endpoints."""
from __future__ import annotations

import os
from fastapi import APIRouter, HTTPException

from app import config, mounts

router = APIRouter()


@router.get("/browse")
def browse(path: str = ""):
    roots = config.browse_roots()
    if not path:
        return {"path": "", "parent": None, "roots": roots,
                "dirs": [{"name": r, "path": r} for r in roots if os.path.isdir(r)],
                "files": []}
    real = os.path.realpath(path)
    root = next((r for r in roots if real == os.path.realpath(r) or real.startswith(os.path.realpath(r).rstrip("/") + "/")), None)
    if root is None:
        raise HTTPException(403, "path is outside the allowed browse roots")
    if not os.path.isdir(real):
        raise HTTPException(404, "not a directory")
    ok, entries = mounts._with_timeout(lambda: sorted(os.scandir(real), key=lambda e: (not e.is_dir(follow_symlinks=True), e.name.lower())), 10)
    if not ok:
        raise HTTPException(504, f"directory did not respond: {entries}")
    dirs = []
    files = []
    for entry in entries:
        try:
            if entry.name.startswith("."):
                continue
            if entry.is_dir(follow_symlinks=True):
                dirs.append({"name": entry.name, "path": os.path.join(real, entry.name)})
            else:
                try:
                    st = entry.stat(follow_symlinks=True)
                    files.append({"name": entry.name, "path": os.path.join(real, entry.name), "size": st.st_size})
                except OSError:
                    files.append({"name": entry.name, "path": os.path.join(real, entry.name), "size": 0})
        except OSError:
            continue
    parent = None if real == os.path.realpath(root) else os.path.dirname(real)
    return {"path": real, "parent": parent, "roots": roots, "dirs": dirs, "files": files}
