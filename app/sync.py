"""Sync jobs: schedule builder, triggers (APScheduler / watchdog), mount validation + self-heal, retries."""
from __future__ import annotations

import logging
import os
import re
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from apscheduler.triggers.cron import CronTrigger
from croniter import croniter

from app import config, db, mounts, notifier, rclone, scheduler

log = logging.getLogger(__name__)

RETRY_DELAY = float(os.environ.get("SYNC_RETRY_DELAY", "30"))
TMP_SUFFIX = ".dm-tmp"
WEEKDAYS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]
WEEKDAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


class SyncError(ValueError):
    pass


# ================================================================ schedule builder

def _time(value: str | None) -> tuple[int, int]:
    m = re.match(r"^(\d{1,2}):(\d{2})$", (value or "").strip())
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise SyncError("time must be HH:MM (24h)")
    return int(m.group(1)), int(m.group(2))


def validate_cron(expr: str) -> str:
    expr = " ".join((expr or "").split())
    if len(expr.split()) != 5 or not croniter.is_valid(expr):
        raise SyncError("cron must have 5 valid fields: minute hour day-of-month month day-of-week")
    try:
        CronTrigger.from_crontab(expr)
    except ValueError as exc:
        raise SyncError(f"invalid cron: {exc}") from exc
    return expr


def build_cron(spec: dict[str, Any]) -> str:
    """Schedule-builder spec -> cron ('' for manual / event-driven).

    spec.type: manual | event | minutes{every} | hourly{minute} | daily{time} | weekly{weekday,time}
               | monthly{day,time} | cron{cron}
    Weekdays use names (sun..sat) because APScheduler and cron number weekdays differently.
    """
    kind = spec.get("type") or "manual"
    if kind in ("manual", "event"):
        return ""
    if kind == "minutes":
        n = int(spec.get("every") or 15)
        if not 1 <= n <= 59:
            raise SyncError("every N minutes: N must be 1-59")
        return "* * * * *" if n == 1 else f"*/{n} * * * *"
    if kind == "hourly":
        minute = int(spec.get("minute") or 0)
        if not 0 <= minute <= 59:
            raise SyncError("minute must be 0-59")
        return f"{minute} * * * *"
    if kind == "daily":
        h, m = _time(spec.get("time"))
        return f"{m} {h} * * *"
    if kind == "weekly":
        h, m = _time(spec.get("time"))
        wd = int(spec.get("weekday") or 0)
        if not 0 <= wd <= 6:
            raise SyncError("weekday must be 0 (Sunday) - 6 (Saturday)")
        return f"{m} {h} * * {WEEKDAYS[wd]}"
    if kind == "monthly":
        h, m = _time(spec.get("time"))
        day = int(spec.get("day") or 1)
        if not 1 <= day <= 31:
            raise SyncError("day must be 1-31")
        return f"{m} {h} {day} * *"
    if kind == "cron":
        return validate_cron(spec.get("cron", ""))
    raise SyncError(f"unknown schedule type {kind}")


def parse_cron(expr: str, event_driven: bool = False) -> dict[str, Any]:
    """Inverse of build_cron so editing a job restores the builder."""
    if not expr:
        return {"type": "event" if event_driven else "manual"}
    f = expr.split()
    if len(f) == 5:
        mi, h, dom, mon, dow = f
        if expr == "* * * * *":
            return {"type": "minutes", "every": 1}
        m = re.match(r"^\*/(\d+)$", mi)
        if m and h == dom == mon == dow == "*":
            return {"type": "minutes", "every": int(m.group(1))}
        if mi.isdigit() and h == dom == mon == dow == "*":
            return {"type": "hourly", "minute": int(mi)}
        if mi.isdigit() and h.isdigit() and mon == "*":
            t = f"{int(h):02d}:{int(mi):02d}"
            if dom == "*" and dow == "*":
                return {"type": "daily", "time": t}
            if dom == "*" and dow.lower() in WEEKDAYS:
                return {"type": "weekly", "weekday": WEEKDAYS.index(dow.lower()), "time": t}
            if dom.isdigit() and dow == "*":
                return {"type": "monthly", "day": int(dom), "time": t}
    return {"type": "cron", "cron": expr}


def describe(expr: str, event_driven: bool = False) -> str:
    spec = parse_cron(expr, event_driven)
    kind = spec["type"]
    base = {
        "manual": "Manual only",
        "event": "When files change (event driven)",
        "minutes": "Every minute" if spec.get("every") == 1 else f"Every {spec.get('every')} minutes",
        "hourly": f"Every hour at minute {spec.get('minute')}",
        "daily": f"Every day at {spec.get('time')}",
        "weekly": f"Every {WEEKDAY_NAMES[spec['weekday']]} at {spec.get('time')}" if kind == "weekly" else "",
        "monthly": f"Every month on day {spec.get('day')} at {spec.get('time')}",
        "cron": f"Custom cron: {expr}",
    }[kind]
    if event_driven and kind not in ("event", "manual"):
        base += " + on file changes"
    return base


def next_runs(expr: str, count: int = 3) -> list[str]:
    if not expr:
        return []
    it = croniter(expr, datetime.now(scheduler.local_tz()))
    return [it.get_next(datetime).isoformat() for _ in range(count)]


# ================================================================ jobs

def list_jobs() -> list[dict[str, Any]]:
    running = set(_running)
    return [{**j, "running": j["id"] in running} for j in config.load("sync-jobs")["jobs"]]


def get_job(job_id: str) -> dict[str, Any]:
    for job in config.load("sync-jobs")["jobs"]:
        if job["id"] == job_id:
            return job
    raise KeyError("job not found")


def _check_path(path: str, label: str) -> str:
    path = (path or "").strip()
    if rclone.is_remote_path(path):
        return path
    real = os.path.normpath(path)
    if not os.path.isabs(real) or not any(real == r or real.startswith(r.rstrip("/") + "/") for r in config.browse_roots()):
        raise SyncError(f"{label} must be inside {', '.join(config.browse_roots())} or an rclone remote (name:path)")
    return real


def save_job(data: dict[str, Any], job_id: str | None = None) -> dict[str, Any]:
    old = get_job(job_id) if job_id else {}
    spec = data.get("schedule_spec") or {"type": "manual"}
    name = str(data.get("name") or "").strip()
    if not name:
        raise SyncError("name is required")
    job = {
        **old,
        "id": old.get("id") or config.new_id(),
        "name": name,
        "source": _check_path(data.get("source", ""), "source"),
        "destination": _check_path(data.get("destination", ""), "destination"),
        "mode": data.get("mode", "copy"),
        "enabled": bool(data.get("enabled", True)),
        "schedule": build_cron(spec),
        "event_driven": spec.get("type") == "event" or bool(data.get("event_driven")),
        "retry_count": max(0, min(int(data.get("retry_count", 3)), 10)),
    }
    if job["mode"] not in ("copy", "sync"):
        raise SyncError("mode must be copy or sync")
    if job["source"] == job["destination"]:
        raise SyncError("source and destination must differ")
    if job["event_driven"] and rclone.is_remote_path(job["source"]):
        raise SyncError("event-driven jobs need a local source folder")
    job["schedule_spec"] = parse_cron(job["schedule"], job["event_driven"])
    job["schedule_text"] = describe(job["schedule"], job["event_driven"])
    job.setdefault("last_run", None)
    job.setdefault("last_failure_notified", False)

    def apply(d: dict[str, Any]) -> None:
        d["jobs"] = [j for j in d["jobs"] if j["id"] != job["id"]] + [job]
    config.update("sync-jobs", apply)
    register(job)
    return job


def delete_job(job_id: str) -> None:
    get_job(job_id)
    unregister(job_id)
    config.update("sync-jobs", lambda d: d.update(jobs=[j for j in d["jobs"] if j["id"] != job_id]))


def _patch(job_id: str, **fields: Any) -> None:
    def apply(d: dict[str, Any]) -> None:
        for j in d["jobs"]:
            if j["id"] == job_id:
                j.update(fields)
    config.update("sync-jobs", apply)


# ================================================================ triggers

def register(job: dict[str, Any]) -> None:
    unregister(job["id"])
    if not job.get("enabled"):
        return
    if job.get("schedule"):
        scheduler.scheduler.add_job(run_job, CronTrigger.from_crontab(job["schedule"], timezone=scheduler.local_tz()),
                                    id=f"sync-{job['id']}", args=[job["id"], "schedule"], replace_existing=True)
    if job.get("event_driven"):
        scheduler.watch(f"sync-{job['id']}", job["source"], lambda jid=job["id"]: run_job(jid, "event"))


def unregister(job_id: str) -> None:
    scheduler.remove(f"sync-{job_id}")
    scheduler.unwatch(f"sync-{job_id}")


def register_all() -> None:
    for job in config.load("sync-jobs")["jobs"]:
        try:
            register(job)
        except Exception as exc:  # noqa: BLE001
            log.warning("cannot schedule %s: %s", job.get("name"), exc)


# ================================================================ execution

_running: set[str] = set()
_rlock = threading.Lock()


def run_job(job_id: str, trigger: str = "manual", wait: bool = False) -> dict[str, Any]:
    job = get_job(job_id)
    if trigger != "manual" and not job.get("enabled"):
        return {"ok": False, "error": "job disabled"}
    with _rlock:
        if job_id in _running:
            return {"ok": False, "error": "already running"}
        _running.add(job_id)
    thread = threading.Thread(target=_execute, args=(job_id, trigger), daemon=True, name=f"sync-{job_id}")
    thread.start()
    if wait:
        thread.join()
    return {"ok": True, "started": True}


def _execute(job_id: str, trigger: str) -> None:
    try:
        _execute_inner(job_id, trigger)
    except KeyError:
        log.info("sync job %s deleted during run", job_id)
    finally:
        with _rlock:
            _running.discard(job_id)


def _execute_inner(job_id: str, trigger: str) -> None:
    started = config.utc_now()
    job = get_job(job_id)
    say = lambda msg, level="INFO": config.job_log("sync", job_id, msg, level)  # noqa: E731
    say(f"=== run ({trigger}) {job['source']} -> {job['destination']} [{job['mode']}]")
    _patch(job_id, last_run={"status": "running", "started_at": started, "trigger": trigger})
    error, stats = None, {}
    try:
        for path, need_write, label in ((job["source"], False, "source"), (job["destination"], True, "destination")):
            if rclone.is_remote_path(path):
                continue
            if label == "destination" and not mounts.owning_mount(path) and not os.path.exists(path):
                Path(path).mkdir(parents=True, exist_ok=True)
            check = mounts.ensure_available(path, need_write, log_fn=say)
            if not check["ok"]:
                raise RuntimeError(f"{label} mount not usable: {check['error']}")
        attempts = 1 + int(job.get("retry_count", 3))
        for attempt in range(1, attempts + 1):
            try:
                stats = _transfer(job, say)
                error = None
                break
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
                say(f"attempt {attempt}/{attempts} failed: {error}", "ERROR")
                if attempt < attempts:
                    time.sleep(RETRY_DELAY)
        if error:
            raise RuntimeError(error)
    except Exception as exc:  # noqa: BLE001
        error = str(exc)
    status = "failed" if error else "ok"
    current = get_job(job_id)
    if error:
        sent = notifier.notify_failure("sync", current, error)
        say(f"=== FAILED: {error}" + (" (notification sent)" if sent else ""), "ERROR")
    else:
        current["last_failure_notified"] = False
        say(f"=== OK {stats}")
    _patch(job_id, last_failure_notified=current["last_failure_notified"],
           last_run={"status": status, "started_at": started, "finished_at": config.utc_now(),
                     "trigger": trigger, "error": error, "stats": stats})
    db.record_run("sync", job_id, status, started, error or "")


def _transfer(job: dict[str, Any], say: Any) -> dict[str, Any]:
    if rclone.is_remote_path(job["source"]) or rclone.is_remote_path(job["destination"]):
        res = rclone.transfer(job["mode"], job["source"], job["destination"], "sync", job["id"])
        if not res["ok"]:
            raise RuntimeError(f"rclone exited {res['rc']}: {res['stderr'][-300:]}")
        return {"engine": "rclone"}
    return copy_tree(job["source"], job["destination"], delete_extra=job["mode"] == "sync")


# ================================================================ native engine

def atomic_copy(src: Path, dst: Path) -> None:
    """Copy to <dst>.dm-tmp, fsync, keep mtime, then os.replace."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + TMP_SUFFIX)
    try:
        with open(src, "rb") as fin, open(tmp, "wb") as fout:
            shutil.copyfileobj(fin, fout, 4 * 1024 * 1024)
            fout.flush()
            os.fsync(fout.fileno())
        st = src.stat()
        os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
        os.replace(tmp, dst)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _unchanged(src: Path, dst: Path) -> bool:
    try:
        s, d = src.stat(), dst.stat()
    except OSError:
        return False
    return s.st_size == d.st_size and abs(s.st_mtime - d.st_mtime) < 2


def copy_tree(source: str, destination: str, delete_extra: bool = False) -> dict[str, int]:
    """Idempotent: files with equal size+mtime are skipped, so repeated runs are cheap no-ops."""
    src_root, dst_root = Path(source), Path(destination)
    if not src_root.is_dir():
        raise FileNotFoundError(f"source folder missing: {source}")
    dst_root.mkdir(parents=True, exist_ok=True)
    stats = {"copied": 0, "skipped": 0, "deleted": 0, "errors": 0}
    seen: set[str] = set()
    first_error = None
    for dirpath, _dirs, files in os.walk(src_root):
        for name in files:
            if name.endswith(TMP_SUFFIX):
                continue
            rel = os.path.relpath(os.path.join(dirpath, name), src_root)
            seen.add(rel)
            try:
                if _unchanged(src_root / rel, dst_root / rel):
                    stats["skipped"] += 1
                else:
                    atomic_copy(src_root / rel, dst_root / rel)
                    stats["copied"] += 1
            except OSError as exc:
                stats["errors"] += 1
                first_error = first_error or f"{rel}: {exc}"
    if delete_extra and not stats["errors"]:
        for dirpath, _dirs, files in os.walk(dst_root, topdown=False):
            for name in files:
                rel = os.path.relpath(os.path.join(dirpath, name), dst_root)
                if rel not in seen:
                    (dst_root / rel).unlink(missing_ok=True)
                    stats["deleted"] += 1
            if Path(dirpath) != dst_root and not os.listdir(dirpath):
                os.rmdir(dirpath)
    if first_error:
        raise OSError(f"{stats['errors']} file(s) failed, first: {first_error}")
    return stats
