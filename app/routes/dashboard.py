"""Dashboard endpoints."""
from __future__ import annotations

from fastapi import APIRouter

from app import mounts, sync, media, config, rclone, db
from app.routes._utils import disk_usage, system_metrics

router = APIRouter()


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
