"""Server management via Docker containers (FTP, WebDAV, SMB/Samba)."""
from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import Any

import docker

from app import config

log = logging.getLogger(__name__)

FTP_CONTAINER = "drive-manager-ftp"
WEBDAV_CONTAINER_PREFIX = "drive-manager-webdav"
SMB_CONTAINER = "samba"

FTP_IMAGE = "ftp-server-arm64:latest"
WEBDAV_IMAGE = "sentanu/caddy-webdav:latest"
SMB_IMAGE = "dockurr/samba:latest"

_PATH_RE = re.compile(r"^/[A-Za-z0-9._/@+-]*$")
_SHARE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")

# Config keys
FTP_CONFIG_KEY = "ftp-server"
WEBDAV_CONFIG_KEY = "webdav-server"
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


def _find_container_by_label(label_key: str, label_value: str):
    for c in _docker_client().containers.list(all=True):
        if c.labels.get(label_key) == label_value:
            return c
    return None


# ---------------------------------------------------------------- Validation

def validate_export_path(path: str) -> str:
    path = os.path.normpath(path or "")
    if not _PATH_RE.match(path) or ".." in path.split("/"):
        raise ServerError("Export path must be an absolute path without ..")
    return path


def validate_share_name(name: str) -> str:
    name = name.strip()
    if not _SHARE_NAME_RE.match(name):
        raise ServerError("Share name: letters, digits, _, - only")
    return name


# ---------------------------------------------------------------- Docker image availability

def ftp_server_installed() -> bool:
    try:
        _docker_client().ping()
        return True
    except Exception:
        return False


def webdav_server_installed() -> bool:
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


def ensure_ftp_packages() -> None:
    try:
        _docker_client().images.get(FTP_IMAGE)
    except docker.errors.ImageNotFound:
        log.info("Pulling FTP image %s", FTP_IMAGE)
        _docker_client().images.pull(FTP_IMAGE)


def ensure_webdav_packages() -> None:
    try:
        _docker_client().images.get(WEBDAV_IMAGE)
    except docker.errors.ImageNotFound:
        log.info("Pulling WebDAV image %s", WEBDAV_IMAGE)
        _docker_client().images.pull(WEBDAV_IMAGE)


def ensure_smb_packages() -> None:
    try:
        _docker_client().images.get(SMB_IMAGE)
    except docker.errors.ImageNotFound:
        log.info("Pulling SMB image %s", SMB_IMAGE)
        _docker_client().images.pull(SMB_IMAGE)


# ---------------------------------------------------------------- FTP config persistence

def _ftp_config() -> dict[str, Any]:
    return config.load(FTP_CONFIG_KEY) or {"shares": [], "users": []}


def _save_ftp_config(data: dict[str, Any]) -> None:
    config.update(FTP_CONFIG_KEY, lambda d: d.update(data))


# ---------------------------------------------------------------- FTP operations

def list_ftp_shares() -> list[dict[str, Any]]:
    shares = _ftp_config().get("shares", [])
    for share in shares:
        share["exists"] = os.path.exists(share["path"])
    return shares


def write_ftp_config_to_container(shares: list[dict[str, Any]], users: list[dict[str, str]]) -> None:
    """Recreate the FTP container with updated shares and users."""
    c = _get_container(FTP_CONTAINER)
    if c:
        c.remove(force=True)

    env: dict[str, str] = {}
    volumes: dict[str, dict[str, str]] = {"/mnt": {"bind": "/mnt", "mode": "rw"}}

    for i, user in enumerate(users, start=1):
        env[f"FTP_USER_{i}"] = user["username"]
        env[f"FTP_PASS_{i}"] = user["password"]

    for i, share in enumerate(shares, start=1):
        name = share["name"]
        path = share["path"]
        env[f"FTP_SHARE_{i}"] = name
        volumes[path] = {"bind": f"/home/ftpusers/{name}", "mode": "rw"}

    cmd: list[str] | None = None if shares else ["tail", "-f", "/dev/null"]

    _docker_client().containers.run(
        FTP_IMAGE,
        name=FTP_CONTAINER,
        command=cmd,
        environment=env,
        volumes=volumes,
        network_mode="host",
        restart_policy={"Name": "unless-stopped"},
        detach=True,
    )


def add_ftp_share(data: dict[str, Any]) -> dict[str, Any]:
    name = validate_share_name(data["name"])
    path = validate_export_path(data["path"])

    cfg = _ftp_config()
    shares = cfg.get("shares", [])
    for share in shares:
        if share["name"] == name:
            share["path"] = path
            break
    else:
        shares.append({"name": name, "path": path})

    _save_ftp_config({"shares": shares, "users": cfg.get("users", [])})
    write_ftp_config_to_container(shares, cfg.get("users", []))
    return {"name": name, "path": path}


def remove_ftp_share(name: str) -> None:
    cfg = _ftp_config()
    shares = [s for s in cfg.get("shares", []) if s["name"] != name]
    _save_ftp_config({"shares": shares, "users": cfg.get("users", [])})
    write_ftp_config_to_container(shares, cfg.get("users", []))


def ftp_service_action(action: str) -> dict[str, Any]:
    c = _get_container(FTP_CONTAINER)
    if not c:
        ensure_ftp_packages()
        write_ftp_config_to_container(_ftp_config().get("shares", []), _ftp_config().get("users", []))
        c = _get_container(FTP_CONTAINER)
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


def ftp_service_status() -> dict[str, Any]:
    c = _get_container(FTP_CONTAINER)
    if not c:
        return {"stdout": "inactive"}
    try:
        c.reload()
    except Exception:
        return {"stdout": "inactive"}
    running = c.status == "running"
    return {"stdout": "active" if running else "inactive"}


# ---------------------------------------------------------------- FTP users

def set_ftp_password(username: str, password: str) -> dict[str, Any]:
    ensure_ftp_packages()
    c = _get_container(FTP_CONTAINER)
    if not c:
        write_ftp_config_to_container(_ftp_config().get("shares", []), [])
        c = _get_container(FTP_CONTAINER)
        if not c:
            return {"ok": False, "stderr": "FTP container could not be created"}

    for _ in range(20):
        try:
            c.reload()
        except Exception:
            pass
        if c.status == "running":
            break
        import time
        time.sleep(0.5)

    if c.status != "running":
        return {"ok": False, "stderr": "FTP container not running"}

    res = c.exec_run(f"pure-pw useradd {username} -f /etc/pure-ftpd/passwd.pdb -m -u ftpuser -d /home/ftpusers/{username}")
    return {"ok": res.exit_code == 0, "stderr": res.output.decode(errors="replace") if res.output else ""}


def delete_ftp_user(username: str) -> dict[str, Any]:
    c = _get_container(FTP_CONTAINER)
    if not c:
        return {"ok": False, "stderr": "FTP container not running"}
    res = c.exec_run(f"pure-pw userdel {username} -f /etc/pure-ftpd/passwd.pdb -m")
    return {"ok": res.exit_code == 0, "stderr": res.output.decode(errors="replace") if res.output else ""}


def list_ftp_users() -> list[str]:
    cfg = _ftp_config()
    return [u.get("username", "") for u in cfg.get("users", []) if u.get("username")]


# ---------------------------------------------------------------- WebDAV config persistence

def _webdav_config() -> dict[str, Any]:
    return config.load(WEBDAV_CONFIG_KEY) or {"shares": [], "users": []}


def _save_webdav_config(data: dict[str, Any]) -> None:
    config.update(WEBDAV_CONFIG_KEY, lambda d: d.update(data))


# ---------------------------------------------------------------- WebDAV operations

def list_webdav_shares() -> list[dict[str, Any]]:
    shares = _webdav_config().get("shares", [])
    for share in shares:
        share["exists"] = os.path.exists(share["path"])
    return shares


def write_webdav_config_to_container(shares: list[dict[str, Any]], users: list[dict[str, str]]) -> None:
    """Recreate WebDAV containers: one container per share, each on its own port."""
    # Stop/remove all existing webdav containers
    for c in _docker_client().containers.list(all=True, filters={"name": WEBDAV_CONTAINER_PREFIX}):
        c.remove(force=True)

    env: dict[str, str] = {}
    volumes: dict[str, dict[str, str]] = {}

    # Single user for all webdav shares
    if users:
        env["WEBDAV_USERNAME"] = users[0]["username"]
        env["WEBDAV_PASSWORD"] = users[0]["password"]
    else:
        env["WEBDAV_USERNAME"] = "webdav"
        env["WEBDAV_PASSWORD"] = "webdav"

    base_port = 8080
    for i, share in enumerate(shares, start=1):
        name = share["name"]
        path = share["path"]
        container_port = 8000 + i  # 8001, 8002, ...
        host_port = 8000 + i
        env[f"WEBDAV_USERNAME_{i}"] = users[0]["username"] if users else "webdav"
        env[f"WEBDAV_PASSWORD_{i}"] = users[0]["password"] if users else "webdav"
        volumes[path] = {"bind": f"/srv/{i}", "mode": "rw"}

        _docker_client().containers.run(
            WEBDAV_IMAGE,
            name=f"{WEBDAV_CONTAINER_PREFIX}-{i}",
            environment=env,
            volumes=volumes,
            ports={"80/tcp": 8000 + i},
            labels={f"{WEBDAV_CONTAINER_PREFIX}-share": name},
            restart_policy={"Name": "unless-stopped"},
            detach=True,
        )

    if not shares:
        _docker_client().containers.run(
            WEBDAV_IMAGE,
            name=f"{WEBDAV_CONTAINER_PREFIX}-empty",
            environment=env,
            volumes=volumes,
            ports={"80/tcp": 8081},
            restart_policy={"Name": "unless-stopped"},
            detach=True,
        )


def add_webdav_share(data: dict[str, Any]) -> dict[str, Any]:
    name = validate_share_name(data["name"])
    path = validate_export_path(data["path"])

    cfg = _webdav_config()
    shares = cfg.get("shares", [])
    for share in shares:
        if share["name"] == name:
            share["path"] = path
            break
    else:
        shares.append({"name": name, "path": path})

    _save_webdav_config({"shares": shares, "users": cfg.get("users", [])})
    write_webdav_config_to_container(shares, cfg.get("users", []))
    return {"name": name, "path": path}


def remove_webdav_share(name: str) -> None:
    cfg = _webdav_config()
    shares = [s for s in cfg.get("shares", []) if s["name"] != name]
    _save_webdav_config({"shares": shares, "users": cfg.get("users", [])})
    write_webdav_config_to_container(shares, cfg.get("users", []))


def webdav_service_action(action: str) -> dict[str, Any]:
    # WebDAV has one container per share; start/stop all of them
    containers = _docker_client().containers.list(all=True, filters={"name": WEBDAV_CONTAINER_PREFIX})
    if not containers:
        ensure_webdav_packages()
        write_webdav_config_to_container(_webdav_config().get("shares", []), _webdav_config().get("users", []))
        containers = _docker_client().containers.list(all=True, filters={"name": WEBDAV_CONTAINER_PREFIX})
        if action == "stop":
            for c in containers:
                c.stop()
        return {"ok": True}

    if action == "start":
        for c in containers:
            c.start()
    elif action == "stop":
        for c in containers:
            c.stop()
    elif action == "restart":
        for c in containers:
            c.restart()
    return {"ok": True}


def webdav_service_status() -> dict[str, Any]:
    containers = _docker_client().containers.list(all=True, filters={"name": WEBDAV_CONTAINER_PREFIX})
    if not containers:
        return {"stdout": "inactive"}
    running = all(c.status == "running" for c in containers)
    return {"stdout": "active" if running else "inactive"}


# ---------------------------------------------------------------- WebDAV users

def set_webdav_password(username: str, password: str) -> dict[str, Any]:
    ensure_webdav_packages()
    cfg = _webdav_config()
    if not any(u["username"] == username for u in cfg.get("users", [])):
        cfg.setdefault("users", []).append({"username": username, "password": password})
        _save_webdav_config({"shares": cfg.get("shares", []), "users": users})

    # Recreate all webdav containers with the new user
    write_webdav_config_to_container(_webdav_config().get("shares", []), cfg.get("users", []))
    return {"ok": True, "stderr": ""}


def delete_webdav_user(username: str) -> dict[str, Any]:
    cfg = _webdav_config()
    users = [u for u in cfg.get("users", []) if u["username"] != username]
    _save_webdav_config({"shares": cfg.get("shares", []), "users": users})
    write_webdav_config_to_container(_webdav_config().get("shares", []), users)
    return {"ok": True, "stderr": ""}


def list_webdav_users() -> list[str]:
    cfg = _webdav_config()
    return [u.get("username", "") for u in cfg.get("users", []) if u.get("username")]


# ---------------------------------------------------------------- SMB config persistence

SMB_COMPOSE_DIR_NAME = "smb-server"


def smb_compose_dir() -> Path:
    d = config.DATA_DIR / SMB_COMPOSE_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def smb_compose_file() -> Path:
    return smb_compose_dir() / "docker-compose.yml"


def smb_compose_exists() -> bool:
    return smb_compose_file().exists()


def smb_load_config() -> dict[str, Any]:
    return config.load(SMB_CONFIG_KEY) or {"username": "", "password": "", "shares": []}


def smb_save_config(username: str, password: str, shares: list[dict[str, Any]]) -> None:
    config.update(SMB_CONFIG_KEY, lambda d: d.update({"username": username, "password": password, "shares": shares}))


def smb_build_compose_text(username: str, password: str, shares: list[dict[str, Any]]) -> str:
    volume_lines = "\n".join(f"      - {s['path']}:/shared/{s['name']}" for s in shares)
    return (
        "services:\n"
        "  samba:\n"
        "    image: dockurr/samba\n"
        "    container_name: samba\n"
        "    environment:\n"
        '      NAME: "shared"\n'
        f'      USER: "{username}"\n'
        f'      PASS: "{password}"\n'
        "    ports:\n"
        "      - 445:445\n"
        "    volumes:\n"
        f"{volume_lines}\n"
    )


def smb_write_compose(username: str, password: str, shares: list[dict[str, Any]]) -> None:
    smb_compose_dir()
    smb_compose_file().write_text(smb_build_compose_text(username, password, shares), encoding="utf-8")


# ---------------------------------------------------------------- Validation

def validate_smb_config(username: str, password: str, shares: list[dict[str, Any]]) -> tuple[str, str, list[dict[str, str]]]:
    user = (username or "").strip()
    if not user:
        raise ServerError("Username is required")
    pwd = password or ""
    if not pwd:
        raise ServerError("Password is required")
    if not shares:
        raise ServerError("At least one share is required")
    clean_shares: list[dict[str, str]] = []
    seen_names: set[str] = set()
    seen_paths: set[str] = set()
    for s in shares:
        name = validate_share_name(s.get("name", ""))
        path = validate_export_path(s.get("path", ""))
        if name in seen_names:
            raise ServerError(f"Duplicate share name: {name}")
        if path in seen_paths:
            raise ServerError(f"Duplicate host path: {path}")
        seen_names.add(name)
        seen_paths.add(path)
        clean_shares.append({"name": name, "path": path})
    return user, pwd, clean_shares


# ---------------------------------------------------------------- SMB operations

def smb_pull_image() -> None:
    try:
        _docker_client().images.get(SMB_IMAGE)
    except docker.errors.ImageNotFound:
        log.info("Pulling SMB image %s", SMB_IMAGE)
        _docker_client().images.pull(SMB_IMAGE)


def smb_image_present() -> bool:
    try:
        _docker_client().images.get(SMB_IMAGE)
        return True
    except docker.errors.ImageNotFound:
        return False
    except Exception:
        return False


def smb_status() -> dict[str, Any]:
    if not smb_image_present():
        return {"state": "not_installed", "installed": False, "running": False, "config": smb_load_config()}
    cfg = smb_load_config()
    c = _get_container(SMB_CONTAINER)
    if not c:
        return {"state": "configured", "installed": True, "running": False, "config": cfg, "container_status": None}
    try:
        c.reload()
    except Exception:
        return {"state": "configured", "installed": True, "running": False, "config": cfg, "container_status": None}
    running = c.status == "running"
    if running:
        return {"state": "running", "installed": True, "running": True, "config": cfg, "container_status": c.status}
    return {"state": "configured", "installed": True, "running": False, "config": cfg, "container_status": c.status}


def smb_start(username: str, password: str, shares: list[dict[str, Any]]) -> None:
    user, pwd, clean_shares = validate_smb_config(username, password, shares)
    smb_save_config(user, pwd, clean_shares)
    smb_write_compose(user, pwd, clean_shares)
    c = _get_container(SMB_CONTAINER)
    if c:
        c.remove(force=True)
    volumes = {s["path"]: {"bind": f"/shared/{s['name']}", "mode": "rw"} for s in clean_shares}
    _docker_client().containers.run(
        SMB_IMAGE,
        name=SMB_CONTAINER,
        environment={"NAME": "shared", "USER": user, "PASS": pwd},
        volumes=volumes,
        detach=True,
    )


def smb_stop() -> None:
    c = _get_container(SMB_CONTAINER)
    if c:
        try:
            c.stop()
        except Exception:
            pass
        try:
            c.remove(force=True)
        except Exception:
            pass


def smb_redeploy(username: str, password: str, shares: list[dict[str, Any]]) -> None:
    user, pwd, clean_shares = validate_smb_config(username, password, shares)
    smb_save_config(user, pwd, clean_shares)
    smb_write_compose(user, pwd, clean_shares)
    smb_stop()
    smb_start(user, pwd, clean_shares)


def smb_logs() -> str:
    c = _get_container(SMB_CONTAINER)
    if not c:
        return ""
    try:
        return c.logs(tail=200).decode(errors="replace")
    except Exception:
        return ""


# ---------------------------------------------------------------- Status aggregation

def ftp_server_status() -> dict[str, Any]:
    c = _get_container(FTP_CONTAINER)
    installed = c is not None
    if not c:
        return {"stdout": "inactive", "installed": False, "shares": [], "users": []}
    try:
        c.reload()
    except Exception:
        return {"stdout": "inactive", "installed": True, "shares": [], "users": []}
    running = c.status == "running"
    return {
        "stdout": "active" if running else "inactive",
        "installed": True,
        "service_active": running,
        "shares": list_ftp_shares(),
        "users": list_ftp_users(),
    }


def webdav_server_status() -> dict[str, Any]:
    containers = _docker_client().containers.list(all=True, filters={"name": WEBDAV_CONTAINER_PREFIX})
    if not containers:
        return {"stdout": "inactive", "installed": False, "shares": [], "users": []}
    running = all(c.status == "running" for c in containers)
    return {
        "stdout": "active" if running else "inactive",
        "installed": True,
        "service_active": running,
        "shares": list_webdav_shares(),
        "users": list_webdav_users(),
    }


def smb_server_status() -> dict[str, Any]:
    c = _get_container(SMB_CONTAINER)
    installed = c is not None
    if not c:
        return {"stdout": "inactive", "installed": False, "shares": [], "users": []}
    try:
        c.reload()
    except Exception:
        return {"stdout": "inactive", "installed": True, "shares": [], "users": []}
    running = c.status == "running"
    return {
        "stdout": "active" if running else "inactive",
        "installed": True,
        "service_active": running,
        "shares": list_smb_shares(),
        "users": list_smb_users(),
    }
