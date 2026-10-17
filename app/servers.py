"""NFS and SMB server management: exports, shares, services, permissions."""
from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
from typing import Any

from app import config
from app.hostexec import host, host_sh

log = logging.getLogger(__name__)

NFS_EXPORTS_FILE = "/etc/exports"
SMB_CONF_FILE = "/etc/samba/smb.conf"
SMB_PASSWD_DIR = "/etc/samba/private"

_PATH_RE = re.compile(r"^/[A-Za-z0-9._/@+-]*$")
_HOST_RE = re.compile(r"^[A-Za-z0-9.\-:\[\]]{1,253}$")
_SHARE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")

class ServerError(ValueError):
    pass


def _nfs_opts_to_str(opts: dict[str, Any]) -> str:
    parts = []
    if opts.get("readonly"):
        parts.append("ro")
    else:
        parts.append("rw")
    if opts.get("sync", True):
        parts.append("sync")
    else:
        parts.append("async")
    if opts.get("no_root_squash"):
        parts.append("no_root_squash")
    else:
        parts.append("root_squash")
    if opts.get("no_subtree_check", True):
        parts.append("no_subtree_check")
    if opts.get("insecure"):
        parts.append("insecure")
    if opts.get("fsid"):
        parts.append(f"fsid={opts['fsid']}")
    return ",".join(parts)


def _smb_opts_to_str(opts: dict[str, Any]) -> str:
    lines = []
    if opts.get("readonly"):
        lines.append("   read only = yes")
    else:
        lines.append("   read only = no")
    if opts.get("guest_ok"):
        lines.append("   guest ok = yes")
        lines.append("   map to guest = Bad User")
    else:
        lines.append("   guest ok = no")
    if opts.get("browseable", True):
        lines.append("   browseable = yes")
    else:
        lines.append("   browseable = no")
    if opts.get("create_mask"):
        lines.append(f"   create mask = {opts['create_mask']}")
    if opts.get("directory_mask"):
        lines.append(f"   directory mask = {opts['directory_mask']}")
    if opts.get("force_user"):
        lines.append(f"   force user = {opts['force_user']}")
    if opts.get("force_group"):
        lines.append(f"   force group = {opts['force_group']}")
    if opts.get("valid_users"):
        lines.append(f"   valid users = {opts['valid_users']}")
    if opts.get("invalid_users"):
        lines.append(f"   invalid users = {opts['invalid_users']}")
    if opts.get("write_list"):
        lines.append(f"   write list = {opts['write_list']}")
    if opts.get("read_list"):
        lines.append(f"   read list = {opts['read_list']}")
    if opts.get("veto_files"):
        lines.append(f"   veto files = {opts['veto_files']}")
    if opts.get("hide_files"):
        lines.append(f"   hide files = {opts['hide_files']}")
    return "\n".join(lines)


def validate_export_path(path: str) -> str:
    path = os.path.normpath(path or "")
    if not _PATH_RE.match(path) or ".." in path.split("/"):
        raise ServerError("Export path must be an absolute path without ..")
    return path


def validate_host(host_str: str) -> str:
    host_str = host_str.strip()
    if host_str == "*" or host_str == "0.0.0.0/0":
        return "*"
    if _HOST_RE.match(host_str) or "/" in host_str:
        return host_str
    raise ServerError("Invalid host/network specification")


def validate_share_name(name: str) -> str:
    name = name.strip()
    if not _SHARE_NAME_RE.match(name):
        raise ServerError("Share name: letters, digits, _, - only")
    return name


def nfs_server_installed() -> bool:
    res = host_sh("command -v rpc.nfsd >/dev/null 2>&1 && command -v exportfs >/dev/null 2>&1 && echo ok", timeout=10)
    return res["ok"]


def smb_server_installed() -> bool:
    res = host_sh("command -v smbd >/dev/null 2>&1 && command -v nmbd >/dev/null 2>&1 && echo ok", timeout=10)
    return res["ok"]


def ensure_nfs_packages() -> None:
    host_sh(
        "command -v exportfs >/dev/null 2>&1 || "
        "(DEBIAN_FRONTEND=noninteractive apt-get update -qq && "
        "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nfs-kernel-server)",
        timeout=600
    )


def ensure_smb_packages() -> None:
    host_sh(
        "command -v smbd >/dev/null 2>&1 || "
        "(DEBIAN_FRONTEND=noninteractive apt-get update -qq && "
        "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq samba)",
        timeout=600
    )


def load_nfs_exports() -> list[dict[str, Any]]:
    exports = []
    try:
        res = host(["cat", NFS_EXPORTS_FILE], timeout=10)
        if not res["ok"]:
            return exports
        for line_num, line in enumerate(res["stdout"].splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            path = parts[0]
            clients = {}
            for p in parts[1:]:
                if "(" in p and p.endswith(")"):
                    client, opts = p.split("(", 1)
                    opts = opts[:-1]
                else:
                    client, opts = p, ""
                clients[client] = opts
            exports.append({"path": path, "clients": clients, "line_num": line_num})
    except Exception:
        pass
    return exports


def load_smb_shares() -> list[dict[str, Any]]:
    shares = []
    current = None
    try:
        res = host(["cat", SMB_CONF_FILE], timeout=10)
        if not res["ok"]:
            return shares
        for line in res["stdout"].splitlines():
            line = line.rstrip("\n")
            if line.strip().startswith("[") and line.strip().endswith("]"):
                if current:
                    shares.append(current)
                name = line.strip()[1:-1]
                if name == "global":
                    continue
                current = {"name": name, "path": "", "options": {}}
            elif current and "=" in line:
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip()
                if key == "path":
                    current["path"] = val
                else:
                    current["options"][key] = val
        if current:
            shares.append(current)
    except Exception:
        pass
    return shares


def write_nfs_exports(exports: list[dict[str, Any]]) -> None:
    lines = ["# Drive Manager managed exports - do not edit manually\n"]
    for exp in exports:
        path = exp["path"]
        client_opts = []
        for client, opts in exp["clients"].items():
            if opts:
                client_opts.append(f"{client}({opts})")
            else:
                client_opts.append(client)
        lines.append(f"{path} {' '.join(client_opts)}")
    content = "\n".join(lines) + "\n"
    host_sh(f"cat > {NFS_EXPORTS_FILE}", timeout=10, input_text=content)


def write_smb_conf(shares: list[dict[str, Any]]) -> None:
    lines = [
        "[global]",
        "   workgroup = WORKGROUP",
        "   server string = Drive Manager SMB Server",
        "   security = user",
        "   map to guest = Bad User",
        "   log file = /var/log/samba/log.%m",
        "   max log size = 1000",
        "   logging = file",
        "   panic action = /usr/share/samba/panic-action %d",
        "",
    ]
    for share in shares:
        lines.append(f"[{share['name']}]")
        lines.append(f"   path = {share['path']}")
        lines.append(_smb_opts_to_str(share.get("options", {})))
        lines.append("")
    content = "\n".join(lines) + "\n"
    host_sh(f"cat > {SMB_CONF_FILE}", timeout=10, input_text=content)


def exportfs_ra() -> dict[str, Any]:
    return host(["exportfs", "-ra"], timeout=30)


def exportfs_u(path: str) -> dict[str, Any]:
    return host(["exportfs", "-u", path], timeout=30)


def test_smb_conf() -> dict[str, Any]:
    return host(["testparm", "-s"], timeout=30)


def nfs_service_action(action: str) -> dict[str, Any]:
    return host(["systemctl", action, "nfs-kernel-server"], timeout=30)


def smb_service_action(action: str) -> dict[str, Any]:
    res = host(["systemctl", action, "smbd"], timeout=30)
    if action in ("start", "restart"):
        host(["systemctl", action, "nmbd"], timeout=30)
    return res


def nfs_service_status() -> dict[str, Any]:
    return host(["systemctl", "is-active", "nfs-kernel-server"], timeout=10)


def smb_service_status() -> dict[str, Any]:
    smbd = host(["systemctl", "is-active", "smbd"], timeout=10)
    nmbd = host(["systemctl", "is-active", "nmbd"], timeout=10)
    return {"smbd": smbd, "nmbd": nmbd}


def list_nfs_exports() -> list[dict[str, Any]]:
    exports = load_nfs_exports()
    for exp in exports:
        exp["mounted"] = os.path.ismount(exp["path"])
        exp["exists"] = os.path.exists(exp["path"])
    return exports


def list_smb_shares() -> list[dict[str, Any]]:
    shares = load_smb_shares()
    for share in shares:
        share["exists"] = os.path.exists(share["path"])
    return shares


def add_nfs_export(data: dict[str, Any]) -> dict[str, Any]:
    path = validate_export_path(data["path"])
    client = validate_host(data.get("client", "*"))
    opts = data.get("options", {})
    opts_str = _nfs_opts_to_str(opts)

    exports = load_nfs_exports()
    for exp in exports:
        if exp["path"] == path:
            exp["clients"][client] = opts_str
            break
    else:
        exports.append({"path": path, "clients": {client: opts_str}})

    write_nfs_exports(exports)
    exportfs_ra()
    return {"path": path, "client": client, "options": opts_str}


def remove_nfs_export(path: str, client: str | None = None) -> None:
    exports = load_nfs_exports()
    if client:
        exports = [e for e in exports if not (e["path"] == path and client in e["clients"])]
    else:
        exports = [e for e in exports if e["path"] != path]
    write_nfs_exports(exports)
    exportfs_ra()


def add_smb_share(data: dict[str, Any]) -> dict[str, Any]:
    name = validate_share_name(data["name"])
    path = validate_export_path(data["path"])
    opts = data.get("options", {})

    shares = load_smb_shares()
    for share in shares:
        if share["name"] == name:
            share["path"] = path
            share["options"] = opts
            break
    else:
        shares.append({"name": name, "path": path, "options": opts})

    write_smb_conf(shares)
    test_smb_conf()
    return {"name": name, "path": path, "options": opts}


def remove_smb_share(name: str) -> None:
    shares = load_smb_shares()
    shares = [s for s in shares if s["name"] != name]
    write_smb_conf(shares)
    test_smb_conf()


def set_smb_password(username: str, password: str) -> dict[str, Any]:
    ensure_smb_packages()
    # smbpasswd -a needs a TTY for password prompts; run Python pty script on host via nsenter
    py = (
        "import pty,os,time\n"
        "m,s=pty.openpty()\n"
        "p=os.fork()\n"
        "if p==0:\n"
        "    os.setsid(); os.close(m)\n"
        "    os.dup2(s,0); os.dup2(s,1); os.dup2(s,2); os.close(s)\n"
        "    os.execvp('smbpasswd',['smbpasswd','-a','%s'])\n"
        "else:\n"
        "    os.close(s)\n"
        "    time.sleep(0.3)\n"
        "    os.write(m,('%s\\n'%password).encode())\n"
        "    time.sleep(0.3)\n"
        "    os.write(m,('%s\\n'%password).encode())\n"
        "    time.sleep(1.0)\n"
        "    out=b''\n"
        "    while True:\n"
        "        try:\n"
        "            c=os.read(m,4096)\n"
        "            if not c: break\n"
        "            out+=c\n"
        "        except OSError: break\n"
        "    os.close(m)\n"
        "    _,st=os.waitpid(p,0)\n"
        "    print('RC:%d'%os.WEXITSTATUS(st))\n"
        "    print('OUT:%s'%out.decode(errors='replace'))\n"
    ) % (username, username, password, password)
    # Write script to temp file on host to avoid shell escaping issues
    write_res = host_sh("cat > /tmp/dm_smbpasswd.py", timeout=10, input_text=py)
    if not write_res["ok"]:
        return write_res
    res = host_sh("python3 /tmp/dm_smbpasswd.py && rm -f /tmp/dm_smbpasswd.py", timeout=30)
    return res


def delete_smb_user(username: str) -> dict[str, Any]:
    return host(["smbpasswd", "-x", username], timeout=30)


def list_smb_users() -> list[str]:
    res = host(["pdbedit", "-L"], timeout=10)
    if not res["ok"]:
        return []
    users = []
    for line in res["stdout"].splitlines():
        if ":" in line:
            users.append(line.split(":")[0])
    return users


def nfs_server_status() -> dict[str, Any]:
    svc = nfs_service_status()
    exports = list_nfs_exports()
    return {
        "installed": nfs_server_installed(),
        "service_active": svc.get("stdout", "").strip() == "active",
        "exports": exports,
    }


def smb_server_status() -> dict[str, Any]:
    svc = smb_service_status()
    shares = list_smb_shares()
    users = list_smb_users()
    return {
        "installed": smb_server_installed(),
        "smbd_active": svc.get("smbd", {}).get("stdout", "").strip() == "active",
        "nmbd_active": svc.get("nmbd", {}).get("stdout", "").strip() == "active",
        "shares": shares,
        "users": users,
    }