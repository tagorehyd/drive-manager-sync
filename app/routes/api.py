"""JSON API used by the single-page UI."""
from __future__ import annotations

import os
import shutil
import threading
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app import config, db, fstab, media, mounts, notifier, rclone, servers, sync

router = APIRouter(prefix="/api")

# Track previous CPU stats for real-time delta calculation
_prev_cpu = None
_prev_cpu_time = 0.0


def _bad(exc: Exception, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail=str(exc))


def disk_usage(path: str) -> dict[str, Any] | None:
    try:
        u = shutil.disk_usage(path)
        return {"path": path, "total": u.total, "free": u.free, "used_pct": round(100 * u.used / u.total, 1) if u.total else 0}
    except OSError:
        return None


def _read_proc(path: str) -> str:
    try:
        with open(path, "r", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def system_metrics() -> dict[str, Any]:
    uptime_s = 0
    uptime_line = _read_proc("/proc/uptime").split()
    if uptime_line:
        try:
            uptime_s = float(uptime_line[0])
        except ValueError:
            uptime_s = 0
    days = int(uptime_s // 86400)
    hours = int((uptime_s % 86400) // 3600)
    minutes = int((uptime_s % 3600) // 60)
    uptime_str = f"{days}d {hours}h {minutes}m"

    mem_total = mem_free = mem_avail = 0
    for line in _read_proc("/proc/meminfo").splitlines():
        parts = line.split()
        if len(parts) >= 2:
            key = parts[0].rstrip(":")
            val = int(parts[1]) if parts[1].isdigit() else 0
            if key == "MemTotal":
                mem_total = val
            elif key == "MemFree":
                mem_free = val
            elif key == "MemAvailable":
                mem_avail = val
    mem_used = mem_total - mem_avail
    mem_pct = round(100 * mem_used / mem_total, 1) if mem_total else 0
    mem_avail_pct = round(100 * mem_avail / mem_total, 1) if mem_total else 0

    cpu_user = cpu_system = cpu_idle = 0
    cpu_line = _read_proc("/proc/stat").splitlines()[0] if _read_proc("/proc/stat") else ""
    cpu_parts = cpu_line.split()
    if len(cpu_parts) >= 5:
        try:
            new_user = int(cpu_parts[1])
            new_system = int(cpu_parts[3])
            new_idle = int(cpu_parts[4])
        except ValueError:
            new_user = new_system = new_idle = 0
    else:
        new_user = new_system = new_idle = 0

    import time as _time
    now = _time.monotonic()
    global _prev_cpu, _prev_cpu_time
    if _prev_cpu is not None and now > _prev_cpu_time:
        dt = now - _prev_cpu_time
        du = new_user - _prev_cpu[0]
        ds = new_system - _prev_cpu[1]
        di = new_idle - _prev_cpu[2]
        total = du + ds + di
        cpu_pct = round(100 * (total - di) / total, 1) if total > 0 else 0
    else:
        cpu_pct = 0

    _prev_cpu = (new_user, new_system, new_idle)
    _prev_cpu_time = now

    procs = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/stat", "r", errors="replace") as fh:
                parts = fh.read().split()
            state = parts[2] if len(parts) > 2 else "?"
            name = parts[1].strip("()") if len(parts) > 1 else pid
            procs.append({"pid": int(pid), "name": name[:24], "state": state})
        except (OSError, ValueError, IndexError):
            continue
    procs.sort(key=lambda p: p["pid"])
    procs = procs[:15]

    return {
        "uptime": uptime_str,
        "uptime_seconds": int(uptime_s),
        "cpu": {"used_pct": cpu_pct, "user": cpu_user, "system": cpu_system, "idle": cpu_idle},
        "memory": {
            "total": mem_total * 1024,
            "used": mem_used * 1024,
            "free": mem_free * 1024,
            "available": mem_avail * 1024,
            "used_pct": mem_pct,
            "available_pct": mem_avail_pct,
        },
        "processes": procs,
        "process_count": len(procs),
    }


# ---------------------------------------------------------------- dashboard

@router.get("/dashboard")
def dashboard():
    mlist = mounts.list_mounts()
    mount_usage = []
    for m in mlist:
        if m.get("mounted"):
            du = disk_usage(m["mount_point"])
            if du:
                mount_usage.append({
                    "id": m.get("id"),
                    "name": m.get("name"),
                    "type": m.get("type"),
                    "mount_point": m["mount_point"],
                    "total": du["total"],
                    "free": du["free"],
                    "used_pct": du["used_pct"],
                })
    return {
        "mounts": {"total": len(mlist), "mounted": sum(1 for m in mlist if m["mounted"]),
                   "healthy": sum(1 for m in mlist if m.get("status") and mounts.healthy(m["status"]))},
        "sync_jobs": len(sync.list_jobs()),
        "media_jobs": len(media.list_jobs()),
        "fstab_entries": len(config.load("fstab")["entries"]),
        "rclone_remotes": len(rclone.list_remotes()),
        "recent_runs": db.recent_runs(15),
        "disks": [d for d in (disk_usage(p) for p in ("/config", "/data", "/mnt")) if d],
        "mount_usage": mount_usage,
        "system": system_metrics(),
    }


# ---------------------------------------------------------------- folder picker

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


# ---------------------------------------------------------------- mounts

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
        raise _bad(exc)
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
        raise _bad(exc, 404)
    raise HTTPException(404, "unknown action")


@router.delete("/mounts/{mount_id}")
def delete_mount(mount_id: str):
    try:
        mounts.delete_mount(mount_id)
    except KeyError as exc:
        raise _bad(exc, 404)
    return {"ok": True}


# ---------------------------------------------------------------- fstab

class MountPointIn(BaseModel):
    mountpoint: str
    confirm: str = ""


# ---------------------------------------------------------------- import fstab as managed mount

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
    """Convert an fstab entry to a managed mount with optional persist.
    Only supports NFS and SMB/CIFS network filesystems."""
    try:
        fstab_entry = fstab.find(body.mountpoint)
        if not fstab_entry:
            raise HTTPException(404, "fstab entry not found")

        fstype = fstab_entry["fstype"]
        device = fstab_entry["device"]

        # Validate type matches filesystem
        if body.type == "nfs" and fstype not in ("nfs", "nfs4"):
            raise ValueError(f"Cannot import {fstype} as NFS - use NFS entries only")
        if body.type == "smb" and fstype not in ("cifs", "smb"):
            raise ValueError(f"Cannot import {fstype} as SMB - use CIFS/SMB entries only")

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
            raise HTTPException(404, "unknown action")
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
        raise HTTPException(404, "fstab entry not found")
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


# ---------------------------------------------------------------- schedule builder + sync jobs

class ScheduleIn(BaseModel):
    spec: dict[str, Any] = Field(default_factory=dict)


@router.post("/schedule/preview")
def schedule_preview(body: ScheduleIn):
    try:
        cron = sync.build_cron(body.spec)
    except (ValueError, TypeError) as exc:
        raise _bad(exc)
    event = body.spec.get("type") == "event"
    return {"cron": cron, "text": sync.describe(cron, event), "next_runs": sync.next_runs(cron)}


@router.get("/sync/jobs")
def sync_jobs():
    return {"jobs": sync.list_jobs()}


@router.post("/sync/jobs")
def create_sync_job(body: dict[str, Any]):
    try:
        return {"ok": True, "job": sync.save_job(body)}
    except (ValueError, TypeError) as exc:
        raise _bad(exc)


@router.put("/sync/jobs/{job_id}")
def update_sync_job(job_id: str, body: dict[str, Any]):
    try:
        return {"ok": True, "job": sync.save_job(body, job_id)}
    except KeyError as exc:
        raise _bad(exc, 404)
    except (ValueError, TypeError) as exc:
        raise _bad(exc)


@router.delete("/sync/jobs/{job_id}")
def delete_sync_job(job_id: str):
    try:
        sync.delete_job(job_id)
    except KeyError as exc:
        raise _bad(exc, 404)
    return {"ok": True}


@router.post("/sync/jobs/{job_id}/run")
def run_sync_job(job_id: str):
    try:
        return sync.run_job(job_id, "manual")
    except KeyError as exc:
        raise _bad(exc, 404)


# ---------------------------------------------------------------- rclone

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
        raise _bad(exc)
    return {"ok": True}


@router.delete("/rclone/remotes/{name}")
def delete_remote(name: str):
    try:
        rclone.delete_remote(name)
    except ValueError as exc:
        raise _bad(exc)
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
        raise _bad(exc)


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
        raise _bad(exc)
    threading.Thread(target=rclone.transfer, args=(body.mode, body.source, body.destination, "rclone", "sync-now"),
                     daemon=True).start()
    return {"ok": True, "log": "rclone-sync-now.log"}


# ---------------------------------------------------------------- media

class PreviewIn(BaseModel):
    filename: str
    destination: str = "/mnt/media"
    use_llm: bool = False


@router.get("/media/jobs")
def media_jobs():
    return {"jobs": media.list_jobs()}


@router.post("/media/jobs")
def create_media_job(body: dict[str, Any]):
    try:
        return {"ok": True, "job": media.save_job(body)}
    except (ValueError, TypeError) as exc:
        raise _bad(exc)


@router.put("/media/jobs/{job_id}")
def update_media_job(job_id: str, body: dict[str, Any]):
    try:
        return {"ok": True, "job": media.save_job(body, job_id)}
    except KeyError as exc:
        raise _bad(exc, 404)
    except (ValueError, TypeError) as exc:
        raise _bad(exc)


@router.delete("/media/jobs/{job_id}")
def delete_media_job(job_id: str):
    try:
        media.delete_job(job_id)
    except KeyError as exc:
        raise _bad(exc, 404)
    return {"ok": True}


@router.post("/media/jobs/{job_id}/scan")
def scan_media_job(job_id: str):
    try:
        return media.start_scan(job_id)
    except KeyError as exc:
        raise _bad(exc, 404)


@router.get("/media/jobs/{job_id}/records")
def media_records(job_id: str):
    return {"records": db.media_records(job_id)}


@router.get("/media/settings")
def get_media_settings():
    return {"settings": media.settings(), "llm_key_set": bool(os.environ.get("OPENAI_COMPATIBLE_API_KEY"))}


@router.put("/media/settings")
def put_media_settings(body: dict[str, Any]):
    return {"settings": media.save_settings(body)}


@router.post("/media/preview")
def media_preview(body: PreviewIn):
    return media.preview(body.filename, body.destination, body.use_llm)


# ---------------------------------------------------------------- settings

class NotificationsIn(BaseModel):
    telegram_enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""
    webhook_enabled: bool = False
    webhook_url: str = ""


class RootsIn(BaseModel):
    roots: list[str]


@router.get("/settings/notifications")
def get_notifications():
    data = config.load("notifications")
    tg, hook = data.get("telegram") or {}, data.get("webhook") or {}
    return {"telegram_enabled": bool(tg.get("enabled")), "chat_id": tg.get("chat_id", ""),
            "bot_token_set": bool(tg.get("bot_token")), "webhook_enabled": bool(hook.get("enabled")),
            "webhook_url": hook.get("url", "")}


@router.put("/settings/notifications")
def put_notifications(body: NotificationsIn):
    if body.webhook_url and not body.webhook_url.startswith(("http://", "https://")):
        raise HTTPException(400, "webhook URL must start with http:// or https://")

    def apply(d: dict[str, Any]) -> None:
        tg = d.setdefault("telegram", {})
        tg.update(enabled=body.telegram_enabled, chat_id=body.chat_id.strip())
        if body.bot_token.strip():
            tg["bot_token"] = body.bot_token.strip()
        d["webhook"] = {"enabled": body.webhook_enabled, "url": body.webhook_url.strip()}
    config.update("notifications", apply)
    return get_notifications()


@router.post("/settings/notifications/test")
def test_notifications():
    return notifier.send("✅ *Drive Manager* test notification", {"event": "test", "time": config.utc_now()})


@router.get("/settings/browse-roots")
def get_roots():
    return {"roots": config.browse_roots(), "env_override": bool(os.environ.get("BROWSE_ROOTS"))}


@router.put("/settings/browse-roots")
def put_roots(body: RootsIn):
    roots = [os.path.normpath(r.strip()) for r in body.roots if r.strip()]
    if not roots or any(not os.path.isabs(r) or r == "/" for r in roots):
        raise HTTPException(400, "roots must be absolute paths other than /")
    config.update("app", lambda d: d.update(browse_roots=roots))
    return get_roots()


# ---------------------------------------------------------------- logs

@router.get("/logs")
def logs():
    return {"logs": config.list_logs()}


@router.get("/logs/{name}")
def log_tail(name: str, lines: int = Query(300, ge=1, le=5000), level: str = ""):
    try:
        return {"name": name, "lines": config.tail_log(name, lines, level)}
    except ValueError as exc:
        raise _bad(exc)


# ---------------------------------------------------------------- NFS server

class NfsExportIn(BaseModel):
    path: str
    client: str = "*"
    options: dict[str, Any] = Field(default_factory=dict)


class NfsServiceIn(BaseModel):
    action: str


@router.get("/servers/nfs")
def nfs_status():
    return servers.nfs_server_status()


@router.post("/servers/nfs/exports")
def add_nfs_export(body: NfsExportIn):
    try:
        return {"ok": True, "export": servers.add_nfs_export(body.model_dump())}
    except (ValueError, KeyError) as exc:
        raise _bad(exc)


@router.delete("/servers/nfs/exports")
def remove_nfs_export(path: str, client: str | None = None):
    try:
        servers.remove_nfs_export(path, client)
    except (ValueError, KeyError) as exc:
        raise _bad(exc)
    return {"ok": True}


@router.post("/servers/nfs/service")
def nfs_service(body: NfsServiceIn):
    try:
        res = servers.nfs_service_action(body.action)
    except ValueError as exc:
        raise _bad(exc)
    return {"ok": res["ok"], "stderr": res.get("stderr", "")}


@router.post("/servers/nfs/install")
def nfs_install():
    try:
        servers.ensure_nfs_packages()
    except Exception as exc:
        raise _bad(exc)
    return {"ok": True}


# ---------------------------------------------------------------- SMB server

class SmbShareIn(BaseModel):
    name: str
    path: str
    options: dict[str, Any] = Field(default_factory=dict)


class SmbServiceIn(BaseModel):
    action: str


class SmbUserIn(BaseModel):
    username: str
    password: str


@router.get("/servers/smb")
def smb_status():
    return servers.smb_server_status()


@router.post("/servers/smb/shares")
def add_smb_share(body: SmbShareIn):
    try:
        return {"ok": True, "share": servers.add_smb_share(body.model_dump())}
    except (ValueError, KeyError) as exc:
        raise _bad(exc)


@router.delete("/servers/smb/shares/{name}")
def remove_smb_share(name: str):
    try:
        servers.remove_smb_share(name)
    except (ValueError, KeyError) as exc:
        raise _bad(exc)
    return {"ok": True}


@router.post("/servers/smb/service")
def smb_service(body: SmbServiceIn):
    try:
        res = servers.smb_service_action(body.action)
    except ValueError as exc:
        raise _bad(exc)
    return {"ok": res["ok"], "stderr": res.get("stderr", "")}


@router.post("/servers/smb/install")
def smb_install():
    try:
        servers.ensure_smb_packages()
    except Exception as exc:
        raise _bad(exc)
    return {"ok": True}


@router.post("/servers/smb/users")
def add_smb_user(body: SmbUserIn):
    try:
        res = servers.set_smb_password(body.username, body.password)
    except (ValueError, KeyError) as exc:
        raise _bad(exc)
    return {"ok": res["ok"], "stderr": res.get("stderr", "")}


@router.delete("/servers/smb/users/{username}")
def delete_smb_user(username: str):
    try:
        res = servers.delete_smb_user(username)
    except (ValueError, KeyError) as exc:
        raise _bad(exc)
    return {"ok": res["ok"], "stderr": res.get("stderr", "")}