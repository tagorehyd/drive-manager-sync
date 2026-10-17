"""Mounts-related endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import mounts

router = APIRouter()


class MountIn(BaseModel):
    type: str
    name: str
    host: str = ""
    export: str = ""
    share: str = ""
    username: str = ""
    password: str = ""
    remote: str = ""
    subpath: str = ""
    mount_point: str = ""
    options: str = ""
    persist: bool = False
    auto_mount: bool = True
    mount_now: bool = True


@router.get("/mounts")
def list_mounts():
    return {"mounts": mounts.list_mounts(), "defaults": {"nfs": mounts.NFS_OPTIONS, "smb": mounts.SMB_OPTIONS}}


@router.post("/mounts")
def add_mount(body: MountIn):
    try:
        m = mounts.add_mount(body.model_dump())
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    result = mounts.mount(m["id"]) if body.mount_now else None
    return {"ok": True, "mount": mounts.get_mount(m["id"]), "result": result}


@router.post("/mounts/check-all")
def check_all():
    return {"statuses": mounts.check_all()}


@router.post("/mounts/{mount_id}/{action}")
def mount_action(mount_id: str, action: str):
    try:
        if action == "mount":
            return mounts.mount(mount_id)
        if action == "unmount":
            return mounts.unmount(mount_id)
        if action == "check":
            return {"ok": True, "status": mounts.check(mount_id)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    raise HTTPException(status_code=404, detail="unknown action")


@router.delete("/mounts/{mount_id}")
def delete_mount(mount_id: str):
    try:
        mounts.delete_mount(mount_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"ok": True}
