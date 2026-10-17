"""Shared helper utilities for API route modules."""
from __future__ import annotations

import os
import shutil
import time as _time
from typing import Any

_prev_cpu = None
_prev_cpu_time = 0.0


def _bad(exc: Exception, status: int = 400):
    from fastapi import HTTPException

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
    global _prev_cpu, _prev_cpu_time
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

    now = _time.monotonic()
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
    try:
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
    except OSError:
        procs = []

    procs.sort(key=lambda p: p["pid"])
    procs = procs[:15]

    return {
        "uptime": uptime_str,
        "uptime_seconds": int(uptime_s),
        "cpu": {"used_pct": cpu_pct, "user": 0, "system": 0, "idle": 0},
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
