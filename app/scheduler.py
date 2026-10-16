"""Shared APScheduler instance and debounced watchdog observers (used by sync and media jobs)."""
from __future__ import annotations

import logging
import os
import threading
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.schedulers.background import BackgroundScheduler
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

log = logging.getLogger(__name__)


def local_tz() -> ZoneInfo:
    try:
        return ZoneInfo(os.environ.get("TZ") or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


scheduler = BackgroundScheduler(timezone=local_tz(),
                                job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 600})


def start() -> None:
    if not scheduler.running:
        scheduler.start()


def remove(job_key: str) -> None:
    if scheduler.get_job(job_key):
        scheduler.remove_job(job_key)


class _Debounced(FileSystemEventHandler):
    def __init__(self, callback: Callable[[], None], delay: float):
        self.callback, self.delay = callback, delay
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def on_any_event(self, event) -> None:  # type: ignore[no-untyped-def]
        name = os.path.basename(str(getattr(event, "dest_path", "") or event.src_path))
        if event.event_type in ("opened", "closed_no_write") or name.startswith(".") or name.endswith((".tmp", ".part")):
            return
        with self._lock:
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self.delay, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self) -> None:
        try:
            self.callback()
        except Exception:  # noqa: BLE001
            log.exception("watch callback failed")


_observer: Observer | None = None
_watches: dict[str, object] = {}
_wlock = threading.Lock()


def watch(key: str, path: str, callback: Callable[[], None], delay: float = 5.0) -> bool:
    global _observer
    unwatch(key)
    if not os.path.isdir(path):
        return False
    with _wlock:
        if _observer is None:
            _observer = Observer()
            _observer.daemon = True
            _observer.start()
        try:
            _watches[key] = _observer.schedule(_Debounced(callback, delay), path, recursive=True)
            return True
        except OSError as exc:
            log.warning("cannot watch %s: %s", path, exc)
            return False


def unwatch(key: str) -> None:
    with _wlock:
        handle = _watches.pop(key, None)
        if handle is not None and _observer is not None:
            try:
                _observer.unschedule(handle)
            except (KeyError, ValueError):
                pass


def shutdown() -> None:
    global _observer
    if scheduler.running:
        scheduler.shutdown(wait=False)
    with _wlock:
        if _observer is not None:
            _observer.stop()
            _observer = None
        _watches.clear()
