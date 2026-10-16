"""Managed mounts (NFS / SMB / Rclone): add, mount, unmount, permission validation, ownership, self-healing."""
from __future__ import annotations

import logging
import os
import re
import threading
import time
import uuid
from typing import Any, Callable

from app import config, fstab, rclone
from app.hostexec import host, host_sh

log = logging.getLogger(__name__)

MNT_ROOT = os.environ.get("MNT_ROOT", "/mnt")
NFS_OPTIONS = "hard,noatime,_netdev,nofail,x-systemd.automount"
SMB_OPTIONS = "vers=3.0,uid=1000,gid=1000,file_mode=0664,dir_mode=0775,_netdev,nofail"
CRED_DIR = "/etc/drive-manager/credentials"
HEAL_ATTEMPTS = 3
HEAL_DELAY = float(os.environ.get("MOUNT_HEAL_DELAY", "5"))

_HOST_RE = re.compile(r"^[A-Za-z0-9.\-:\[\]]{1,253}$")
_PATH_RE = re.compile(r"^/[A-Za-z0-9._/@+-]*$")
_SHARE_RE = re.compile(r"^[A-Za-z0-9._$ -]{1,80}$")
_USER_RE = re.compile(r"^[A-Za-z0-9._@\\-]{1,64}$")
_OPTS_RE = re.compile(r"^[A-Za-z0-9_.,=:/-]*$")


class MountError(ValueError):
    pass


# ---------------------------------------------------------------- mount table

def _mount_points() -> set[str]:
    points: set[str] = set()
    for path in ("/proc/self/mountinfo", "/proc/1/mountinfo"):
        try:
            with open(path, encoding="utf-8") as handle:
                for line in handle:
                    fields = line.split(" - ")[0].split()
                    if len(fields) >= 5:
                        points.add(fields[4].replace("\\040", " "))
        except OSError:
            continue
    return points


def is_mounted(path: str) -> bool:
    return os.path.normpath(path) in _mount_points()


# ---------------------------------------------------------------- permission validation

def _with_timeout(fn: Callable[[], Any], timeout: float) -> tuple[bool, Any]:
    """Run blocking filesystem probes in a thread: a dead NFS server must not hang the app."""
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["value"] = fn()
        except Exception as exc:  # noqa: BLE001
            box["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        return False, TimeoutError(f"filesystem did not respond within {timeout}s")
    if "error" in box:
        return False, box["error"]
    return True, box.get("value")


def _run_on_host(path: str) -> bool:
    """Check if path is a host mount point not accessible in container."""
    # Paths under /mnt are volume-mounted into container
    # Paths like /srv/... are host-only
    return not path.startswith("/mnt/")


def validate_path(path: str, require_mount: bool = True, timeout: float = 15) -> dict[str, Any]:
    """Read, write, rename and delete test. Existence alone never counts as healthy."""
    from app.hostexec import host_sh
    import json

    status: dict[str, Any] = {"path": path, "mounted": is_mounted(path) if require_mount else os.path.isdir(path),
                              "readable": False, "writable": False, "rename_ok": False, "delete_ok": False,
                              "error": None, "checked_at": config.utc_now()}
    if require_mount and not status["mounted"]:
        status["error"] = "not mounted"
        return status

    use_host = _run_on_host(path)
    token = uuid.uuid4().hex[:10]
    probe = os.path.join(path, f".drive-manager-check-{token}.tmp")
    renamed = os.path.join(path, f".drive-manager-check-{token}.renamed")

    def read() -> bool:
        if use_host:
            res = host_sh(f'ls "{path}" >/dev/null 2>&1 && echo ok', timeout=10)
            return res["ok"]
        os.listdir(path)
        return True

    def write() -> bool:
        if use_host:
            script = f'printf "drive-manager permission check\\n" > "{probe}" && sync && [ -f "{probe}" ] && grep -q "drive-manager" "{probe}" && rm -f "{probe}" && echo ok'
            res = host_sh(script, timeout=10)
            return res["ok"]
        with open(probe, "wb") as handle:
            handle.write(b"drive-manager permission check\n")
            handle.flush()
            os.fsync(handle.fileno())
        with open(probe, "rb") as handle:
            if not handle.read().startswith(b"drive-manager"):
                raise OSError("read-back mismatch")
        return True

    def rename_test() -> bool:
        if use_host:
            script = f'printf "drive-manager permission check\\n" > "{probe}" && sync && mv "{probe}" "{renamed}" && [ -f "{renamed}" ] && echo ok'
            res = host_sh(script, timeout=10)
            return res["ok"]
        with open(probe, "wb") as handle:
            handle.write(b"drive-manager permission check\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.rename(probe, renamed)
        return True

    def delete_test() -> bool:
        if use_host:
            res = host_sh(f'[ -f "{renamed}" ] && rm -f "{renamed}" && echo ok', timeout=10)
            return res["ok"]
        os.unlink(renamed)
        return True

    steps = [("readable", read), ("writable", write), ("rename_ok", rename_test), ("delete_ok", delete_test)]
    for key, fn in steps:
        ok, result = _with_timeout(fn, timeout)
        if not ok:
            status["error"] = f"{key.replace('_ok', '')} test failed: {result}"
            break
        status[key] = True
    return status


def ownership(path: str) -> dict[str, Any]:
    from app.hostexec import host_sh
    import json
    app_uid, app_gid = os.getuid(), os.getgid()
    use_host = _run_on_host(path)

    if use_host:
        script = f'stat -c "%u %g %a" "{path}" 2>/dev/null || echo error'
        res = host_sh(script, timeout=10)
        if not res["ok"] or not res["stdout"].strip() or res["stdout"].strip() == "error":
            return {"owner_uid": None, "owner_gid": None, "app_uid": app_uid, "app_gid": app_gid,
                    "uid_match": False, "gid_match": False, "error": "stat failed"}
        try:
            owner_uid, owner_gid, mode = res["stdout"].strip().split()
            owner_uid = int(owner_uid)
            owner_gid = int(owner_gid)
        except (ValueError, IndexError):
            return {"owner_uid": None, "owner_gid": None, "app_uid": app_uid, "app_gid": app_gid,
                    "uid_match": False, "gid_match": False, "error": "parse failed"}
        return {"owner_uid": owner_uid, "owner_gid": owner_gid, "app_uid": app_uid, "app_gid": app_gid,
                "uid_match": owner_uid == app_uid, "gid_match": owner_gid == app_gid,
                "mode": oct(int(mode, 8))}
    ok, st = _with_timeout(lambda: os.stat(path), 10)
    if not ok:
        return {"owner_uid": None, "owner_gid": None, "app_uid": app_uid, "app_gid": app_gid,
                "uid_match": False, "gid_match": False, "error": str(st)}
    return {"owner_uid": st.st_uid, "owner_gid": st.st_gid, "app_uid": app_uid, "app_gid": app_gid,
            "uid_match": st.st_uid == app_uid, "gid_match": st.st_gid == app_gid,
            "mode": oct(st.st_mode & 0o7777)}


def healthy(status: dict[str, Any], need_write: bool = True) -> bool:
    if not status.get("mounted") or not status.get("readable"):
        return False
    return not need_write or all(status.get(k) for k in ("writable", "rename_ok", "delete_ok"))


# ---------------------------------------------------------------- managed mounts (config/peers.json)

def list_mounts() -> list[dict[str, Any]]:
    points = _mount_points()
    out = []
    for m in config.load("peers")["mounts"]:
        out.append({**m, "mounted": m["mount_point"] in points})
    return out


def get_mount(mount_id: str) -> dict[str, Any]:
    for m in config.load("peers")["mounts"]:
        if m["id"] == mount_id:
            return m
    raise KeyError("mount not found")


def _check_mount_point(path: str) -> str:
    path = os.path.normpath(path or "")
    if not _PATH_RE.match(path) or ".." in path.split("/"):
        raise MountError(f"Mount point must be an absolute path without ..")
    if not path.startswith(MNT_ROOT + "/"):
        # Allow non-/mnt paths for imported fstab entries (they already exist)
        pass
    return path


def build_mount_command(m: dict[str, Any]) -> list[str]:
    if m["type"] == "nfs":
        return ["mount", "-t", "nfs4", "-o", m.get("options") or NFS_OPTIONS, f"{m['host']}:{m['export']}", m["mount_point"]]
    if m["type"] == "smb":
        opts = (m.get("options") or SMB_OPTIONS) + f",credentials={CRED_DIR}/{m['id']}"
        return ["mount", "-t", "cifs", "-o", opts, f"//{m['host']}/{m['share']}", m["mount_point"]]
    raise MountError("rclone mounts are not mounted with mount(8)")


def fstab_line(m: dict[str, Any]) -> tuple[str, str, str, str]:
    if m["type"] == "nfs":
        return f"{m['host']}:{m['export']}", m["mount_point"], "nfs4", m.get("options") or NFS_OPTIONS
    if m["type"] == "smb":
        opts = (m.get("options") or SMB_OPTIONS) + f",credentials={CRED_DIR}/{m['id']}"
        return f"//{m['host']}/{m['share']}", m["mount_point"], "cifs", opts
    raise MountError("only NFS and SMB mounts can be persisted to fstab")


def add_mount(data: dict[str, Any]) -> dict[str, Any]:
    mtype = data.get("type")
    name = str(data.get("name") or "").strip()
    if not re.match(r"^[A-Za-z0-9_-]{1,40}$", name):
        raise MountError("Name: letters, digits, _ and - only")
    m: dict[str, Any] = {"id": config.new_id(), "name": name, "type": mtype, "persist": bool(data.get("persist")),
                         "auto_mount": bool(data.get("auto_mount", True)), "options": str(data.get("options") or "").strip(),
                         "created_at": config.utc_now(), "status": None}
    if m["options"] and not _OPTS_RE.match(m["options"]):
        raise MountError("Invalid mount options")
    if mtype == "nfs":
        m["host"], m["export"] = str(data.get("host", "")).strip(), str(data.get("export", "")).strip()
        if not _HOST_RE.match(m["host"]) or not _PATH_RE.match(m["export"]):
            raise MountError("NFS needs a valid host and an absolute export path")
        m["mount_point"] = _check_mount_point(data.get("mount_point") or f"{MNT_ROOT}/{name}")
    elif mtype == "smb":
        m["host"], m["share"] = str(data.get("host", "")).strip(), str(data.get("share", "")).strip()
        m["username"] = str(data.get("username", "")).strip()
        if not _HOST_RE.match(m["host"]) or not _SHARE_RE.match(m["share"]) or not _USER_RE.match(m["username"]):
            raise MountError("SMB needs a valid server, share and username")
        if not data.get("password"):
            raise MountError("SMB password is required")
        m["mount_point"] = _check_mount_point(data.get("mount_point") or f"{MNT_ROOT}/{name}")
    elif mtype == "rclone":
        m["remote"] = rclone.validate_name(str(data.get("remote", "")).strip())
        m["subpath"] = str(data.get("subpath", "")).strip().lstrip("/")
        m["mount_point"] = _check_mount_point(data.get("mount_point") or f"{rclone.RCLONE_MOUNT_ROOT}/{m['remote']}")
        m["persist"] = False
    else:
        raise MountError("type must be nfs, smb or rclone")
    if any(x["mount_point"] == m["mount_point"] for x in config.load("peers")["mounts"]):
        raise MountError("Another managed mount already uses that mount point")
    if mtype == "smb":
        _write_credentials(m, str(data["password"]))
    config.update("peers", lambda d: d["mounts"].append(m))
    if m["persist"]:
        res = fstab.ensure_line(*fstab_line(m))
        if not res["ok"]:
            log.warning("fstab persist failed for %s: %s", name, res["stderr"])
    return m


def _write_credentials(m: dict[str, Any], password: str) -> None:
    """SMB credentials live only on the host in a root-owned 0600 file, never in JSON."""
    if "\n" in password or "\n" in m["username"]:
        raise MountError("invalid credentials")
    body = f"username={m['username']}\npassword={password}\n"
    res = host_sh(f'umask 077 && mkdir -p {CRED_DIR} && cat > {CRED_DIR}/{m["id"]} && chmod 600 {CRED_DIR}/{m["id"]}',
                  timeout=15, input_text=body)
    if not res["ok"]:
        raise MountError(f"cannot store SMB credentials on host: {res['stderr']}")


def _ensure_helpers(mtype: str) -> None:
    """Debian/Ubuntu hosts: install nfs-common / cifs-utils on first use."""
    pkg, binary = {"nfs": ("nfs-common", "mount.nfs4"), "smb": ("cifs-utils", "mount.cifs")}[mtype]
    host_sh(f"command -v {binary} >/dev/null 2>&1 || (DEBIAN_FRONTEND=noninteractive apt-get update -qq && "
            f"DEBIAN_FRONTEND=noninteractive apt-get install -y -qq {pkg})", timeout=600)


def mount(mount_id: str) -> dict[str, Any]:
    m = get_mount(mount_id)
    if m["type"] == "rclone":
        res = rclone.mount(m["remote"], m["mount_point"], m.get("subpath", ""))
    elif is_mounted(m["mount_point"]):
        res = {"ok": True, "rc": 0, "stdout": "already mounted", "stderr": ""}
    else:
        _ensure_helpers(m["type"])
        host(["mkdir", "-p", m["mount_point"]], timeout=15)
        res = host(build_mount_command(m), timeout=90)
    status = check(mount_id)
    return {"ok": res["ok"] and status["mounted"], "result": {k: res.get(k) for k in ("rc", "stdout", "stderr")},
            "status": status}


def unmount(mount_id: str) -> dict[str, Any]:
    m = get_mount(mount_id)
    if m["type"] == "rclone":
        res = rclone.unmount(m["mount_point"])
    else:
        res = host(["umount", m["mount_point"]], timeout=60)
        if not res["ok"] and "busy" in res["stderr"].lower():
            res = host(["umount", "-l", m["mount_point"]], timeout=60)
    check(mount_id)
    return {"ok": res["ok"] or not is_mounted(m["mount_point"]), "result": {k: res.get(k) for k in ("rc", "stdout", "stderr")}}


def delete_mount(mount_id: str) -> None:
    m = get_mount(mount_id)
    if is_mounted(m["mount_point"]):
        unmount(mount_id)
    if m.get("persist"):
        fstab.drop_line(m["mount_point"])
    if m["type"] == "smb":
        host(["rm", "-f", f"{CRED_DIR}/{m['id']}"], timeout=15)
    config.update("peers", lambda d: d.update(mounts=[x for x in d["mounts"] if x["id"] != mount_id]))


def check(mount_id: str) -> dict[str, Any]:
    m = get_mount(mount_id)
    status = validate_path(m["mount_point"])
    status["ownership"] = ownership(m["mount_point"]) if status["mounted"] else None

    def apply(d: dict[str, Any]) -> None:
        for x in d["mounts"]:
            if x["id"] == mount_id:
                x["status"] = status
    config.update("peers", apply)
    return status


def check_all() -> list[dict[str, Any]]:
    return [check(m["id"]) for m in config.load("peers")["mounts"]]


def auto_mount_all() -> None:
    for m in config.load("peers")["mounts"]:
        if m.get("auto_mount") and not is_mounted(m["mount_point"]):
            try:
                res = mount(m["id"])
                log.info("auto-mount %s: ok=%s", m["name"], res["ok"])
            except Exception as exc:  # noqa: BLE001 - startup must continue
                log.warning("auto-mount %s failed: %s", m["name"], exc)


# ---------------------------------------------------------------- self-healing for jobs

def owning_mount(path: str) -> dict[str, Any] | None:
    real = os.path.normpath(path)
    best = None
    for m in config.load("peers")["mounts"]:
        mp = m["mount_point"]
        if real == mp or real.startswith(mp + "/"):
            if best is None or len(mp) > len(best["mount_point"]):
                best = m
    return best


def _fstab_mount_for(path: str) -> str | None:
    real = os.path.normpath(path)
    best = None
    for e in config.load("fstab")["entries"]:
        mp = e["mountpoint"]
        if mp.startswith(MNT_ROOT + "/") and (real == mp or real.startswith(mp + "/")):
            if best is None or len(mp) > len(best):
                best = mp
    return best


def ensure_available(path: str, need_write: bool, log_fn: Callable[[str], None] | None = None,
                     attempts: int = HEAL_ATTEMPTS, delay: float | None = None) -> dict[str, Any]:
    """Validate the mount backing ``path``; if unhealthy, remount up to ``attempts`` times before failing."""
    say = log_fn or (lambda msg: log.info(msg))
    delay = HEAL_DELAY if delay is None else delay
    managed = owning_mount(path)
    fstab_mp = None if managed else _fstab_mount_for(path)
    mount_point = managed["mount_point"] if managed else fstab_mp

    def probe() -> dict[str, Any]:
        if mount_point:
            st = validate_path(mount_point)
            if st["mounted"] and not os.path.isdir(path):
                st.update(readable=False, error=f"{path} does not exist on the mount")
            return st
        return validate_path(path, require_mount=False)

    status = probe()
    if healthy(status, need_write):
        return {"ok": True, "attempts": 0, "status": status}
    if not mount_point:
        return {"ok": False, "attempts": 0, "status": status,
                "error": f"{path}: {status.get('error') or 'unavailable'} (not on a remountable mount)"}
    for attempt in range(1, attempts + 1):
        say(f"mount for {path} unhealthy ({status.get('error')}); remount attempt {attempt}/{attempts}")
        if managed:
            if is_mounted(managed["mount_point"]):
                unmount(managed["id"])
            mount(managed["id"])
        elif fstab_mp:
            host(["umount", "-l", fstab_mp], timeout=30)
            host(["mount", fstab_mp], timeout=90)
        status = probe()
        if healthy(status, need_write):
            say(f"mount for {path} healthy after attempt {attempt}")
            return {"ok": True, "attempts": attempt, "status": status}
        if attempt < attempts:
            time.sleep(delay)
    return {"ok": False, "attempts": attempts, "status": status,
            "error": f"{path}: {status.get('error') or 'unhealthy'} after {attempts} remount attempts"}
