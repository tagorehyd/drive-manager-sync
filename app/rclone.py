"""Rclone: remotes in /config/rclone.conf, FUSE mounts under /mnt/rclone/<remote> with VFS cache, sync now."""
from __future__ import annotations

import configparser
import io
import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from app import config
from app.hostexec import run

log = logging.getLogger(__name__)

RCLONE_MOUNT_ROOT = Path(os.environ.get("RCLONE_MOUNT_ROOT", "/mnt/rclone"))
SECRET_KEYS = {"pass", "password", "password2", "token", "secret_access_key", "client_secret", "key"}
OBSCURE_KEYS = {"pass", "password", "password2"}
MASK = "********"
_NAME = re.compile(r"^[A-Za-z0-9_-]{1,40}$")

_procs: dict[str, subprocess.Popen] = {}
_lock = threading.Lock()


def available() -> bool:
    return run(["rclone", "version"], timeout=10)["ok"]


def _read() -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str  # type: ignore[assignment]
    try:
        parser.read_string(config.RCLONE_CONF.read_text(encoding="utf-8"))
    except (OSError, configparser.Error) as exc:
        log.warning("rclone.conf: %s", exc)
    return parser


def _write(parser: configparser.ConfigParser) -> None:
    buf = io.StringIO()
    parser.write(buf)
    config.atomic_write_text(config.RCLONE_CONF, buf.getvalue(), mode=0o600)


def list_remotes() -> list[dict[str, Any]]:
    parser = _read()
    out = []
    for name in parser.sections():
        opts = dict(parser.items(name))
        mp = str(RCLONE_MOUNT_ROOT / name)
        out.append({"name": name, "type": opts.pop("type", ""), "mount_point": mp,
                    "options": {k: (MASK if k in SECRET_KEYS and v else v) for k, v in opts.items()}})
    return out


def validate_name(name: str) -> str:
    if not _NAME.match(name or ""):
        raise ValueError("Remote name: letters, digits, _ and - only")
    return name


def save_remote(name: str, rtype: str, options: dict[str, str]) -> None:
    validate_name(name)
    if not re.match(r"^[a-z0-9]+$", rtype or ""):
        raise ValueError("Invalid remote type")
    parser = _read()
    old = dict(parser.items(name)) if parser.has_section(name) else {}
    section = {"type": rtype}
    for key, value in options.items():
        key, value = key.strip(), str(value).strip()
        if not re.match(r"^[a-z0-9_]+$", key) or not value:
            continue
        if value == MASK:
            if key in old:
                section[key] = old[key]
            continue
        if key in OBSCURE_KEYS:
            res = run(["rclone", "obscure", "-"], timeout=15, input_text=value)
            if not res["ok"]:
                raise ValueError(f"rclone obscure failed: {res['stderr']}")
            value = res["stdout"].strip()
        section[key] = value
    if parser.has_section(name):
        parser.remove_section(name)
    parser.add_section(name)
    for key, value in section.items():
        parser.set(name, key, value)
    _write(parser)


def delete_remote(name: str) -> None:
    parser = _read()
    if parser.has_section(validate_name(name)):
        parser.remove_section(name)
        _write(parser)


def mount(remote: str, mount_point: str, subpath: str = "") -> dict[str, Any]:
    """Start ``rclone mount`` with a VFS write cache; the mount propagates to the host via /mnt:rshared."""
    from app.mounts import is_mounted

    validate_name(remote)
    if is_mounted(mount_point):
        return {"ok": True, "rc": 0, "stdout": "already mounted", "stderr": ""}
    Path(mount_point).mkdir(parents=True, exist_ok=True)
    config.RCLONE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cmd = ["rclone", "mount", f"{remote}:{subpath}", mount_point, "--config", str(config.RCLONE_CONF),
           "--vfs-cache-mode", "writes", "--cache-dir", str(config.RCLONE_CACHE_DIR),
           "--vfs-cache-max-size", "10G", "--vfs-cache-max-age", "1h", "--dir-cache-time", "5m",
           "--allow-other", "--uid", os.environ.get("RCLONE_UID", "1000"), "--gid", os.environ.get("RCLONE_GID", "1000"),
           "--umask", "002", "--log-file", str(config.LOG_DIR / f"rclone-{remote}.log"), "--log-level", "INFO"]
    with _lock:
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                                    start_new_session=True)
        except OSError as exc:
            return {"ok": False, "rc": 127, "stdout": "", "stderr": str(exc)}
        _procs[mount_point] = proc
    for _ in range(40):
        if is_mounted(mount_point):
            return {"ok": True, "rc": 0, "stdout": f"mounted {remote}: at {mount_point}", "stderr": ""}
        if proc.poll() is not None:
            err = proc.stderr.read() if proc.stderr else ""
            return {"ok": False, "rc": proc.returncode, "stdout": "", "stderr": err.strip()[-1000:]}
        time.sleep(0.5)
    return {"ok": False, "rc": 124, "stdout": "", "stderr": "rclone mount did not come up within 20s"}


def unmount(mount_point: str) -> dict[str, Any]:
    res = run(["fusermount3", "-uz", mount_point], timeout=30)
    if not res["ok"]:
        res = run(["umount", "-l", mount_point], timeout=30)
    with _lock:
        proc = _procs.pop(mount_point, None)
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
    return res


def unmount_all() -> None:
    for mp in list(_procs):
        unmount(mp)


def is_remote_path(path: str) -> bool:
    return bool(re.match(r"^[A-Za-z0-9_-]+:", path or ""))


def transfer(mode: str, source: str, destination: str, log_kind: str, log_id: str, timeout: float = 12 * 3600) -> dict[str, Any]:
    """Blocking ``rclone copy|sync`` (used by Sync Now and by sync jobs with remote endpoints)."""
    if mode not in ("copy", "sync"):
        raise ValueError("mode must be copy or sync")
    config.job_log(log_kind, log_id, f"rclone {mode} {source} -> {destination}")
    res = run(["rclone", mode, source, destination, "--config", str(config.RCLONE_CONF),
               "--stats-one-line", "--stats", "0", "-v"], timeout=timeout)
    for line in (res["stderr"] or "").splitlines()[-40:]:
        config.job_log(log_kind, log_id, "  " + line)
    return res
