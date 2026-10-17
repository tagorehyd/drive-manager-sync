"""NFS and SMB server management via Docker containers."""
from __future__ import annotations

import logging
import os
import re
import time
from typing import Any

import docker

from app import config

log = logging.getLogger(__name__)

NFS_CONTAINER = "drive-manager-nfs"
SMB_CONTAINER = "drive-manager-smb"
NFS_IMAGE = "itsthenetwork/nfs-server-alpine:latest"
SMB_IMAGE = "dockurr/samba:latest"

_PATH_RE = re.compile(r"^/[A-Za-z0-9._/@+-]*$")
_HOST_RE = re.compile(r"^[A-Za-z0-9.\-:\[\]]{1,253}$")
_SHARE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")

# Config keys for persisting server state
NFS_CONFIG_KEY = "nfs-server"
SMB_CONFIG_KEY = "smb-server"


class ServerError(ValueError):
    pass


# ---------------------------------------------------------------- Docker helpers

def _docker_client():
    return docker.DockerClient(base_url="unix://var/run/docker.sock")


def _get_container(name: str):
    try:
        return _docker_client().containers.get(name)
    except docker.errors.NotFound:
        return None


# ---------------------------------------------------------------- Validation

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


# ---------------------------------------------------------------- Docker image availability

def nfs_server_installed() -> bool:
    try:
        _docker_client().ping()
        return True
    except Exception:
        return False


def smb_server_installed() -> bool:
    try:
        _docker_client().ping()
        return True
    except Exception:
        return False


def ensure_nfs_packages() -> None:
    try:
        _docker_client().images.get(NFS_IMAGE)
    except docker.errors.ImageNotFound:
        log.info("Pulling NFS image %s", NFS_IMAGE)
        _docker_client().images.pull(NFS_IMAGE)


def ensure_smb_packages() -> None:
    try:
        _docker_client().images.get(SMB_IMAGE)
    except docker.errors.ImageNotFound:
        log.info("Pulling SMB image %s", SMB_IMAGE)
        _docker_client().images.pull(SMB_IMAGE)


# ---------------------------------------------------------------- NFS config persistence

def _nfs_config() -> dict[str, Any]:
    return config.load(NFS_CONFIG_KEY) or {"exports": []}


def _save_nfs_config(data: dict[str, Any]) -> None:
    config.update(NFS_CONFIG_KEY, lambda d: d.update(data))


# ---------------------------------------------------------------- NFS operations

def list_nfs_exports() -> list[dict[str, Any]]:
    exports = _nfs_config().get("exports", [])
    for exp in exports:
        exp["mounted"] = os.path.ismount(exp["path"])
        exp["exists"] = os.path.exists(exp["path"])
    return exports


def write_nfs_config_to_container(exports: list[dict[str, Any]]) -> None:
    """Recreate the NFS container with updated SHARED_DIRECTORIES."""
    c = _get_container(NFS_CONTAINER)
    if c:
        c.remove(force=True)

    dirs = []
    for exp in exports:
        path = exp["path"]
        client_opts = []
        for client, opts in exp.get("clients", {}).items():
            if opts:
                client_opts.append(f"{client}({opts})")
            else:
                client_opts.append(client)
        dirs.append(f"{path}:{path}:{','.join(client_opts) if client_opts else 'rw'}")

    env = {"SHARED_DIRECTORIES": ",".join(dirs)} if dirs else {}

    _docker_client().containers.run(
        NFS_IMAGE,
        name=NFS_CONTAINER,
        environment=env,
        volumes={"/mnt": {"bind": "/mnt", "mode": "rw"}},
        cap_add=["SYS_ADMIN", "NET_ADMIN"],
        network_mode="host",
        restart_policy={"Name": "unless-stopped"},
        detach=True,
    )


def add_nfs_export(data: dict[str, Any]) -> dict[str, Any]:
    path = validate_export_path(data["path"])
    client = validate_host(data.get("client", "*"))
    opts = data.get("options", {})
    opts_str = _nfs_opts_to_str(opts)

    exports = _nfs_config().get("exports", [])
    for exp in exports:
        if exp["path"] == path:
            exp["clients"][client] = opts_str
            break
    else:
        exports.append({"path": path, "clients": {client: opts_str}})

    _save_nfs_config({"exports": exports})
    write_nfs_config_to_container(exports)
    return {"path": path, "client": client, "options": opts_str}


def remove_nfs_export(path: str, client: str | None = None) -> None:
    exports = _nfs_config().get("exports", [])
    if client:
        exports = [e for e in exports if not (e["path"] == path and client in e["clients"])]
    else:
        exports = [e for e in exports if e["path"] != path]
    _save_nfs_config({"exports": exports})
    write_nfs_config_to_container(exports)


def nfs_service_action(action: str) -> dict[str, Any]:
    c = _get_container(NFS_CONTAINER)
    if not c:
        ensure_nfs_packages()
        write_nfs_config_to_container(_nfs_config().get("exports", []))
        c = _get_container(NFS_CONTAINER)
        if action == "stop":
            c.stop()
        return {"ok": True}

    if action == "start":
        c.start()
    elif action == "stop":
        c.stop()
    elif action == "restart":
        c.restart()
    return {"ok": True}


def nfs_service_status() -> dict[str, Any]:
    c = _get_container(NFS_CONTAINER)
    if not c:
        return {"stdout": "inactive"}
    c.reload()
    return {"stdout": "active" if c.status == "running" else "inactive"}


# ---------------------------------------------------------------- SMB config persistence

def _smb_config() -> dict[str, Any]:
    return config.load(SMB_CONFIG_KEY) or {"shares": [], "users": []}


def _save_smb_config(data: dict[str, Any]) -> None:
    config.update(SMB_CONFIG_KEY, lambda d: d.update(data))


# ---------------------------------------------------------------- SMB operations

def list_smb_shares() -> list[dict[str, Any]]:
    shares = _smb_config().get("shares", [])
    for share in shares:
        share["exists"] = os.path.exists(share["path"])
    return shares


def write_smb_config_to_container(shares: list[dict[str, Any]], users: list[dict[str, str]]) -> None:
    """Recreate the SMB container with updated shares and users."""
    c = _get_container(SMB_CONTAINER)
    if c:
        c.remove(force=True)

    env: dict[str, str] = {}
    if shares:
        share = shares[0]
        env["NAME"] = share["name"]
        if users:
            env["USER"] = users[0]["username"]
            env["PASS"] = users[0]["password"]
        else:
            env["USER"] = "samba"
            env["PASS"] = "samba"
    else:
        env["NAME"] = "unused"
        env["USER"] = "unused"
        env["PASS"] = "unused"

    _docker_client().containers.run(
        SMB_IMAGE,
        name=SMB_CONTAINER,
        environment=env,
        volumes={"/mnt": {"bind": "/shared", "mode": "rw"}},
        network_mode="host",
        restart_policy={"Name": "unless-stopped"},
        detach=True,
    )


def add_smb_share(data: dict[str, Any]) -> dict[str, Any]:
    name = validate_share_name(data["name"])
    path = validate_export_path(data["path"])
    opts = data.get("options", {})

    cfg = _smb_config()
    shares = cfg.get("shares", [])
    for share in shares:
        if share["name"] == name:
            share["path"] = path
            share["options"] = opts
            break
    else:
        shares.append({"name": name, "path": path, "options": opts})

    _save_smb_config({"shares": shares, "users": cfg.get("users", [])})
    write_smb_config_to_container(shares, cfg.get("users", []))
    return {"name": name, "path": path, "options": opts}


def remove_smb_share(name: str) -> None:
    cfg = _smb_config()
    shares = [s for s in cfg.get("shares", []) if s["name"] != name]
    _save_smb_config({"shares": shares, "users": cfg.get("users", [])})
    write_smb_config_to_container(shares, cfg.get("users", []))


def smb_service_action(action: str) -> dict[str, Any]:
    c = _get_container(SMB_CONTAINER)
    if not c:
        ensure_smb_packages()
        write_smb_config_to_container(_smb_config().get("shares", []), _smb_config().get("users", []))
        c = _get_container(SMB_CONTAINER)
        if action == "stop":
            c.stop()
        return {"ok": True}

    if action == "start":
        c.start()
    elif action == "stop":
        c.stop()
    elif action == "restart":
        c.restart()
    return {"ok": True}


def smb_service_status() -> dict[str, Any]:
    c = _get_container(SMB_CONTAINER)
    if not c:
        return {"smbd": {"stdout": "inactive"}, "nmbd": {"stdout": "inactive"}}
    try:
        c.reload()
    except Exception:
        return {"smbd": {"stdout": "inactive"}, "nmbd": {"stdout": "inactive"}}
    running = c.status == "running"
    has_shares = len(_smb_config().get("shares", [])) > 0
    active = running and has_shares
    return {
        "smbd": {"stdout": "active" if active else "inactive"},
        "nmbd": {"stdout": "active" if active else "inactive"},
    }


# ---------------------------------------------------------------- SMB users

def set_smb_password(username: str, password: str) -> dict[str, Any]:
    ensure_smb_packages()
    c = _get_container(SMB_CONTAINER)
    if not c:
        write_smb_config_to_container(_smb_config().get("shares", []), [])
        c = _get_container(SMB_CONTAINER)
        if not c:
            return {"ok": False, "stderr": "SMB container could not be created"}

    # Wait for container to be running
    for _ in range(20):
        try:
            c.reload()
        except Exception:
            pass
        if c.status == "running":
            break
        time.sleep(0.5)

    if c.status != "running":
        return {"ok": False, "stderr": "SMB container not running"}

    # smbpasswd -a needs a TTY for password prompts; run Python pty script inside container
    # Use base64 to avoid shell/escaping issues
    py = (
        "import pty,os,time\n"
        "m,s=pty.openpty()\n"
        "p=os.fork()\n"
        "if p==0:\n"
        "    os.setsid(); os.close(m)\n"
        "    os.dup2(s,0); os.dup2(s,1); os.dup2(s,2); os.close(s)\n"
        "    os.execvp('smbpasswd',['smbpasswd','-a',USR])\n"
        "else:\n"
        "    os.close(s)\n"
        "    time.sleep(0.3)\n"
        "    os.write(m,('%s\\n'%PWD).encode())\n"
        "    time.sleep(0.3)\n"
        "    os.write(m,('%s\\n'%PWD).encode())\n"
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
    )
    py = py.replace("USR", repr(username)).replace("PWD", repr(password))
    import base64
    encoded = base64.b64encode(py.encode()).decode()
    res = c.exec_run(f"echo {encoded} | base64 -d | python3")
    if res.exit_code == 0:
        # Also track in config for display
        cfg = _smb_config()
        users = cfg.get("users", [])
        if not any(u["username"] == username for u in users):
            users.append({"username": username, "password": password})
            _save_smb_config({"shares": cfg.get("shares", []), "users": users})
    return {"ok": res.exit_code == 0, "stderr": res.output.decode(errors="replace") if res.output else ""}


def delete_smb_user(username: str) -> dict[str, Any]:
    c = _get_container(SMB_CONTAINER)
    if not c:
        return {"ok": False, "stderr": "SMB container not running"}
    res = c.exec_run(f"smbpasswd -x {username}")
    return {"ok": res.exit_code == 0, "stderr": res.output.decode(errors="replace") if res.output else ""}


def list_smb_users() -> list[str]:
    cfg = _smb_config()
    return [u.get("username", "") for u in cfg.get("users", []) if u.get("username")]


# ---------------------------------------------------------------- Status aggregation

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


# ---------------------------------------------------------------- Compatibility wrappers (old host-based API)

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
