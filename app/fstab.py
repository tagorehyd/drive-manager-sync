"""Host /etc/fstab: read via nsenter, parse to /config/fstab.json, mount/unmount/remove/persist entries."""
from __future__ import annotations

import os
import re
from typing import Any

from app import config
from app.hostexec import host, host_sh

FSTAB_PATH = os.environ.get("FSTAB_PATH", "/etc/fstab")


def _unescape(value: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), value)


def _escape(value: str) -> str:
    return value.replace("\\", "\\134").replace(" ", "\\040").replace("\t", "\\011")


def parse(text: str) -> list[dict[str, Any]]:
    entries = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        options = parts[3] if len(parts) > 3 else "defaults"
        opts = options.split(",")
        entries.append({
            "device": _unescape(parts[0]),
            "mountpoint": _unescape(parts[1]),
            "fstype": parts[2],
            "options": options,
            "dump": parts[4] if len(parts) > 4 else "0",
            "pass": parts[5] if len(parts) > 5 else "0",
            "automount": "x-systemd.automount" in opts,
            "nofail": "nofail" in opts,
            "raw": raw,
        })
    return entries


def format_line(device: str, mountpoint: str, fstype: str, options: str) -> str:
    return f"{_escape(device)} {_escape(mountpoint)} {fstype} {options} 0 0"


def host_mount_table() -> dict[str, dict[str, str]]:
    """Host mounts from /proc/1/mountinfo (pid: host); falls back to this container's view."""
    for path in ("/proc/1/mountinfo", "/proc/self/mountinfo"):
        try:
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            break
        except OSError:
            continue
    else:
        return {}
    table: dict[str, dict[str, str]] = {}
    for line in text.splitlines():
        left, _, right = line.partition(" - ")
        fields, rfields = left.split(), right.split()
        if len(fields) >= 5 and rfields:
            table[_unescape(fields[4])] = {"fstype": rfields[0], "source": rfields[1] if len(rfields) > 1 else ""}
    return table


def read_host_fstab() -> dict[str, Any]:
    res = host(["cat", FSTAB_PATH], timeout=15)
    return {"ok": res["ok"], "text": res["stdout"], "error": None if res["ok"] else res["stderr"] or "cannot read fstab"}


def reload() -> dict[str, Any]:
    got = read_host_fstab()
    if not got["ok"]:
        data = config.update("fstab", lambda d: d.update(error=got["error"], read_at=config.utc_now()))
        config.log.warning("fstab read failed: %s", got["error"])
        return data
    data = {"entries": parse(got["text"]), "read_at": config.utc_now(), "error": None}
    config.save("fstab", data)
    return data


def entries_with_status() -> dict[str, Any]:
    data = config.load("fstab")
    table = host_mount_table()
    for entry in data["entries"]:
        seen = table.get(entry["mountpoint"])
        entry["mounted"] = bool(seen and seen["fstype"] != "autofs")
    return data


def find(mountpoint: str) -> dict[str, Any] | None:
    return next((e for e in config.load("fstab")["entries"] if e["mountpoint"] == mountpoint), None)


def mount_entry(mountpoint: str) -> dict[str, Any]:
    if not find(mountpoint):
        raise KeyError("not an fstab entry")
    return host(["mount", mountpoint], timeout=90)


def unmount_entry(mountpoint: str) -> dict[str, Any]:
    if not find(mountpoint):
        raise KeyError("not an fstab entry")
    res = host(["umount", mountpoint], timeout=60)
    if not res["ok"] and "busy" in res["stderr"].lower():
        res = host(["umount", "-l", mountpoint], timeout=60)
    return res


def _write_host_fstab(text: str) -> dict[str, Any]:
    """Backup, then atomic replace of the host fstab."""
    path = FSTAB_PATH
    script = (f'cp -p "{path}" "{path}.drive-manager.bak" && cat > "{path}.dm-tmp" && sync && '
              f'chmod 644 "{path}.dm-tmp" && mv "{path}.dm-tmp" "{path}"')
    return host_sh(script, timeout=30, input_text=text)


def _filtered_lines(mountpoint: str) -> tuple[list[str], bool]:
    got = read_host_fstab()
    if not got["ok"]:
        raise OSError(got["error"])
    kept, removed = [], False
    for raw in got["text"].splitlines():
        parts = raw.split()
        if parts and not raw.strip().startswith("#") and len(parts) >= 2 and _unescape(parts[1]) == mountpoint:
            removed = True
            continue
        kept.append(raw)
    return kept, removed


def remove_entry(mountpoint: str, confirm: str) -> dict[str, Any]:
    if confirm != "REMOVE":
        raise PermissionError('Type "REMOVE" to confirm')
    kept, removed = _filtered_lines(mountpoint)
    if not removed:
        raise KeyError("not an fstab entry")
    res = _write_host_fstab("\n".join(kept) + "\n")
    reload()
    return res


def ensure_line(device: str, mountpoint: str, fstype: str, options: str) -> dict[str, Any]:
    """Idempotently add/replace the fstab line for ``mountpoint``."""
    kept, _ = _filtered_lines(mountpoint)
    kept.append(format_line(device, mountpoint, fstype, options))
    res = _write_host_fstab("\n".join(kept) + "\n")
    reload()
    return res


def drop_line(mountpoint: str) -> dict[str, Any]:
    kept, removed = _filtered_lines(mountpoint)
    if not removed:
        return {"ok": True, "rc": 0, "stdout": "", "stderr": ""}
    res = _write_host_fstab("\n".join(kept) + "\n")
    reload()
    return res
