"""Fstab management endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import fstab
from app.routes._utils import _bad

router = APIRouter()


class MountPointIn(BaseModel):
    mountpoint: str
    confirm: str = ""


class ImportFstabIn(BaseModel):
    mountpoint: str
    name: str
    type: str  # nfs or smb
    host: str = ""
    export: str = ""
    share: str = ""
    username: str = ""
    password: str = ""
    options: str = ""
    auto_mount: bool = True


@router.post("/fstab/import")
def import_fstab_as_mount(body: ImportFstabIn):
    try:
        fstab_entry = fstab.find(body.mountpoint)
        if not fstab_entry:
            raise HTTPException(status_code=404, detail="fstab entry not found")

        fstype = fstab_entry["fstype"]
        device = fstab_entry["device"]

        # Validate type matches filesystem
        if body.type == "nfs" and fstype not in ("nfs", "nfs4"):
            raise ValueError(f"Cannot import {fstype} as NFS - use NFS entries only")
        if body.type == "smb" and fstype not in ("cifs", "smb"):
            raise ValueError(f"Cannot import {fstype} as SMB - use CIFS/SMB entries only")

        from app import mounts

        m = mounts.add_mount({
            "type": body.type,
            "name": body.name,
            "host": body.host,
            "export": body.export,
            "share": body.share,
            "username": body.username,
            "password": body.password,
            "mount_point": body.mountpoint,
            "options": body.options or fstab_entry["options"],
            "persist": True,  # already in fstab
            "auto_mount": body.auto_mount,
        })
        # remove from fstab since it's now managed
        fstab.drop_line(body.mountpoint)
        return {"ok": True, "mount": mounts.get_mount(m["id"])}
    except (ValueError, KeyError, OSError) as exc:
        raise _bad(exc)


@router.get("/fstab")
def get_fstab():
    return fstab.entries_with_status()


@router.post("/fstab/reload")
def reload_fstab():
    fstab.reload()
    return fstab.entries_with_status()


@router.post("/fstab/{action}")
def fstab_action(action: str, body: MountPointIn):
    try:
        if action == "mount":
            res = fstab.mount_entry(body.mountpoint)
        elif action == "unmount":
            res = fstab.unmount_entry(body.mountpoint)
        elif action == "remove":
            res = fstab.remove_entry(body.mountpoint, body.confirm)
        else:
            raise HTTPException(status_code=404, detail="unknown action")
    except PermissionError as exc:
        raise _bad(exc, 400)
    except (KeyError, OSError) as exc:
        raise _bad(exc, 404 if isinstance(exc, KeyError) else 500)
    return {"ok": res["ok"], "stderr": res.get("stderr", ""), "fstab": fstab.entries_with_status()}


class FstabEditIn(BaseModel):
    mountpoint: str
    automount: bool | None = None
    nofail: bool | None = None
    noauto: bool | None = None
    options: str | None = None


@router.post("/fstab/edit")
def edit_fstab_entry(body: FstabEditIn):
    entry = fstab.find(body.mountpoint)
    if not entry:
        raise HTTPException(status_code=404, detail="fstab entry not found")
    opts = set(entry.get("options", "defaults").split(","))
    if body.automount is not None:
        if body.automount:
            opts.add("x-systemd.automount")
        else:
            opts.discard("x-systemd.automount")
    if body.nofail is not None:
        if body.nofail:
            opts.add("nofail")
        else:
            opts.discard("nofail")
    if body.noauto is not None:
        if body.noauto:
            opts.add("noauto")
        else:
            opts.discard("noauto")
    if body.options is not None:
        opts = set(body.options.split(","))
    new_options = ",".join(sorted(opts))
    res = fstab.ensure_line(entry["device"], entry["mountpoint"], entry["fstype"], new_options)
    return {"ok": res["ok"], "stderr": res.get("stderr", ""), "fstab": fstab.entries_with_status()}
