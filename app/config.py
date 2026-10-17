"""Paths, JSON config (source of truth, atomic writes), logging and per-job logs."""
from __future__ import annotations

import copy
import json
import logging
import os
import re
import secrets
import threading
from collections import deque
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable

CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/config"))
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
LOG_DIR = DATA_DIR / "logs"
STAGING_DIR = DATA_DIR / "media-staging"
RCLONE_CACHE_DIR = DATA_DIR / "rclone-cache"
RCLONE_CONF = CONFIG_DIR / "rclone.conf"
SECRET_KEY_FILE = CONFIG_DIR / "secret.key"
APP_DIR = Path(__file__).resolve().parent

DEFAULTS: dict[str, Any] = {
    "auth": {"setup_completed": False, "username": "", "password_hash": "", "session_epoch": 1},
    "app": {"browse_roots": ["/mnt", "/data"]},
    "notifications": {
        "telegram": {"enabled": False, "bot_token": "", "chat_id": ""},
        "webhook": {"enabled": False, "url": ""},
    },
    "peers": {"mounts": []},
    "sync-jobs": {"jobs": []},
    "media-jobs": {
        "jobs": [],
        "settings": {
            "use_llm": True,
            "llm_base_url": "https://api.openai.com/v1",
            "llm_model": "gpt-4o-mini",
            "default_category": "Hollywood",
            "category_rules": {
                "Telugu": "Tollywood", "Tamil": "Kollywood", "Hindi": "Bollywood",
                "Malayalam": "Mollywood", "Kannada": "Sandalwood", "English": "Hollywood",
            },
        },
    },
    "fstab": {"entries": [], "read_at": None, "error": None},
    "nfs-server": {"exports": []},
    "smb-server": {"shares": [], "users": []},
    "ftp-server": {"shares": [], "users": []},
    "webdav-server": {"shares": [], "users": []},
}

_locks: dict[str, threading.RLock] = {name: threading.RLock() for name in DEFAULTS}

log = logging.getLogger("drive_manager")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def config_path(name: str) -> Path:
    return CONFIG_DIR / f"{name}.json"


def atomic_write_text(path: Path, text: str, mode: int | None = None) -> None:
    """Write via a sibling .tmp file + fsync + os.replace so readers never see partial files."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{threading.get_ident()}.tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    if mode is not None:
        os.chmod(tmp, mode)
    os.replace(tmp, path)


def load(name: str) -> dict[str, Any]:
    with _locks[name]:
        try:
            with open(config_path(name), encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            return copy.deepcopy(DEFAULTS[name])
        except (OSError, ValueError) as exc:
            log.error("config %s unreadable (%s); using defaults", name, exc)
            return copy.deepcopy(DEFAULTS[name])
    merged = copy.deepcopy(DEFAULTS[name])
    if isinstance(data, dict):
        merged.update(data)
    return merged


def save(name: str, data: dict[str, Any]) -> None:
    with _locks[name]:
        text = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        atomic_write_text(config_path(name), text, mode=0o600 if name in ("auth", "notifications") else None)


def update(name: str, fn: Callable[[dict[str, Any]], Any]) -> dict[str, Any]:
    """Locked read-modify-write; ``fn`` mutates the dict in place."""
    with _locks[name]:
        data = load(name)
        fn(data)
        save(name, data)
        return data


def bootstrap() -> None:
    for directory in (CONFIG_DIR, DATA_DIR, LOG_DIR, STAGING_DIR, RCLONE_CACHE_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    for name in DEFAULTS:
        if not config_path(name).exists():
            save(name, copy.deepcopy(DEFAULTS[name]))
    if not RCLONE_CONF.exists():
        atomic_write_text(RCLONE_CONF, "", mode=0o600)
    secret_key()


def secret_key() -> str:
    env = os.environ.get("SECRET_KEY")
    if env:
        return env
    if not SECRET_KEY_FILE.exists():
        atomic_write_text(SECRET_KEY_FILE, secrets.token_urlsafe(48) + "\n", mode=0o600)
    return SECRET_KEY_FILE.read_text(encoding="utf-8").strip()


def browse_roots() -> list[str]:
    env = [r.strip() for r in os.environ.get("BROWSE_ROOTS", "").split(",") if r.strip()]
    return env or list(load("app").get("browse_roots") or ["/mnt", "/data"])


def new_id() -> str:
    return secrets.token_hex(6)


# ---------------------------------------------------------------- logging

_SECRET_RE = re.compile(r"(?i)(password|pass|token|secret)=([^\s,]+)")


class _RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        return True


def setup_logging() -> None:
    root = logging.getLogger()
    if getattr(root, "_dm_configured", False):
        return
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(LOG_DIR / "app.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"))
    except OSError:
        pass
    for handler in handlers:
        handler.setFormatter(fmt)
        handler.addFilter(_RedactFilter())
        root.addHandler(handler)
    root._dm_configured = True  # type: ignore[attr-defined]


_SAFE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")


def job_log_path(kind: str, job_id: str) -> Path:
    if not _SAFE.match(kind) or not _SAFE.match(job_id):
        raise ValueError("invalid log name")
    return LOG_DIR / f"{kind}-{job_id}.log"


def redact(text: str) -> str:
    return _SECRET_RE.sub(r"\1=***", text)


def job_log(kind: str, job_id: str, message: str, level: str = "INFO") -> None:
    message = redact(message)
    line = f"{utc_now()} [{level}] {message}\n"
    try:
        with open(job_log_path(kind, job_id), "a", encoding="utf-8") as handle:
            handle.write(line)
    except (OSError, ValueError):
        log.warning("cannot write %s log for %s", kind, job_id)
    getattr(log, level.lower(), log.info)("[%s:%s] %s", kind, job_id, message)


def list_logs() -> list[str]:
    if not LOG_DIR.exists():
        return []
    return sorted(p.name for p in LOG_DIR.glob("*.log"))


def tail_log(name: str, lines: int = 300, level: str = "") -> list[str]:
    if not _SAFE.match(name) or not name.endswith(".log"):
        raise ValueError("invalid log name")
    path = LOG_DIR / name
    if not path.exists():
        return []
    out: deque[str] = deque(maxlen=max(1, min(lines, 5000)))
    with open(path, encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            if level and f"[{level.upper()}]" not in raw:
                continue
            out.append(raw.rstrip("\n"))
    return list(out)
