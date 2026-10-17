"""Rclone endpoints."""
from __future__ import annotations

import threading
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import mounts, rclone, sync

router = APIRouter()


class RemoteIn(BaseModel):
    name: str
    type: str
    options: dict[str, str] = Field(default_factory=dict)


class RcloneSyncIn(BaseModel):
    source: str
    destination: str
    mode: str = "copy"


@router.get("/rclone/remotes")
def rclone_remotes():
    managed = {m["mount_point"]: m for m in mounts.list_mounts() if m["type"] == "rclone"}
    remotes = rclone.list_remotes()
    for r in remotes:
        m = managed.get(r["mount_point"])
        r["mounted"] = bool(m and m["mounted"]) or mounts.is_mounted(r["mount_point"])
        r["mount_id"] = m["id"] if m else None
    return {"remotes": remotes, "available": rclone.available()}


@router.post("/rclone/remotes")
def save_remote(body: RemoteIn):
    try:
        rclone.save_remote(body.name, body.type, body.options)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@router.delete("/rclone/remotes/{name}")
def delete_remote(name: str):
    try:
        rclone.delete_remote(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@router.post("/rclone/remotes/{name}/mount")
def mount_remote(name: str):
    try:
        rclone.validate_name(name)
        mp = str(rclone.RCLONE_MOUNT_ROOT / name)
        existing = next((m for m in mounts.list_mounts() if m["mount_point"] == mp), None)
        mount_id = existing["id"] if existing else mounts.add_mount({"type": "rclone", "name": f"rclone-{name}", "remote": name,
                                                                      "mount_point": mp, "auto_mount": True})["id"]
        return mounts.mount(mount_id)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/rclone/remotes/{name}/unmount")
def unmount_remote(name: str):
    mp = str(rclone.RCLONE_MOUNT_ROOT / rclone.validate_name(name))
    existing = next((m for m in mounts.list_mounts() if m["mount_point"] == mp), None)
    if existing:
        return mounts.unmount(existing["id"])
    return {"ok": rclone.unmount(mp)["ok"]}


@router.post("/rclone/sync")
def rclone_sync(body: RcloneSyncIn):
    try:
        for p in (body.source, body.destination):
            if not rclone.is_remote_path(p):
                sync._check_path(p, "path")
        if body.mode not in ("copy", "sync"):
            raise ValueError("mode must be copy or sync")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    threading.Thread(target=rclone.transfer, args=(body.mode, body.source, body.destination, "rclone", "sync-now"),
                     daemon=True).start()
    return {"ok": True, "log": "rclone-sync-now.log"}
