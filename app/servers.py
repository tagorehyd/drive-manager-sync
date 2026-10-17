"""FTP and WebDAV server management via Docker containers."""
from __future__ import annotations

import logging
import os
import re
from typing import Any

import docker

from app import config

log = logging.getLogger(__name__)

FTP_CONTAINER = "drive-manager-ftp"
WEBDAV_CONTAINER = "drive-manager-webdav"
FTP_IMAGE = "stilliard/pure-ftpd:latest"   # ARM64-compatible FTP server
WEBDAV_IMAGE = "sentanu/caddy-webdav:latest"  # ARM64-compatible WebDAV

_PATH_RE = re.compile(r"^/[A-Za-z0-9._/@+-]*$")
_SHARE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")

# Config keys for persisting server state
FTP_CONFIG_KEY = "ftp-server"
WEBDAV_CONFIG_KEY = "webdav-server"


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

    if not shares:
        cmd: list[str] | None = ["tail", "-f", "/dev/null"]
    else:
        cmd = None

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

    # Wait for container to be running
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

    # Add user via pure-pw inside container
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
    """Recreate the WebDAV container with sentanu/caddy-webdav."""
    c = _get_container(WEBDAV_CONTAINER)
    if c:
        c.remove(force=True)

    env: dict[str, str] = {}
    volumes: dict[str, dict[str, str]] = {}

    # Single user (taken before starting the container)
    if users:
        env["WEBDAV_USERNAME"] = users[0]["username"]
        env["WEBDAV_PASSWORD"] = users[0]["password"]
    else:
        env["WEBDAV_USERNAME"] = "webdav"
        env["WEBDAV_PASSWORD"] = "webdav"

    # Mount shares at /srv/1, /srv/2, etc.
    for i, share in enumerate(shares, start=1):
        volumes[share["path"]] = {"bind": f"/srv/{i}", "mode": "rw"}

    _docker_client().containers.run(
        WEBDAV_IMAGE,
        name=WEBDAV_CONTAINER,
        environment=env,
        volumes=volumes,
        network_mode="host",
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
    c = _get_container(WEBDAV_CONTAINER)
    if not c:
        ensure_webdav_packages()
        write_webdav_config_to_container(_webdav_config().get("shares", []), _webdav_config().get("users", []))
        c = _get_container(WEBDAV_CONTAINER)
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


def webdav_service_status() -> dict[str, Any]:
    c = _get_container(WEBDAV_CONTAINER)
    if not c:
        return {"stdout": "inactive"}
    try:
        c.reload()
    except Exception:
        return {"stdout": "inactive"}
    running = c.status == "running"
    return {"stdout": "active" if running else "inactive"}


# ---------------------------------------------------------------- WebDAV users

def set_webdav_password(username: str, password: str) -> dict[str, Any]:
    ensure_webdav_packages()
    c = _get_container(WEBDAV_CONTAINER)
    if not c:
        write_webdav_config_to_container(_webdav_config().get("shares", []), [])
        c = _get_container(WEBDAV_CONTAINER)
        if not c:
            return {"ok": False, "stderr": "WebDAV container could not be created"}

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
        return {"ok": False, "stderr": "WebDAV container not running"}

    # scality/docker-standalone-webdav uses htpasswd for auth
    res = c.exec_run(f"htpasswd -cb /etc/nginx/htpasswd {username} {password}")
    if res.exit_code == 0:
        cfg = _webdav_config()
        users = cfg.get("users", [])
        if not any(u["username"] == username for u in users):
            users.append({"username": username, "password": password})
            _save_webdav_config({"shares": cfg.get("shares", []), "users": users})
    return {"ok": res.exit_code == 0, "stderr": res.output.decode(errors="replace") if res.output else ""}


def delete_webdav_user(username: str) -> dict[str, Any]:
    c = _get_container(WEBDAV_CONTAINER)
    if not c:
        return {"ok": False, "stderr": "WebDAV container not running"}
    res = c.exec_run(f"htpasswd -D /etc/nginx/htpasswd {username}")
    return {"ok": res.exit_code == 0, "stderr": res.output.decode(errors="replace") if res.output else ""}


def list_webdav_users() -> list[str]:
    cfg = _webdav_config()
    return [u.get("username", "") for u in cfg.get("users", []) if u.get("username")]


# ---------------------------------------------------------------- Status aggregation

def ftp_server_status() -> dict[str, Any]:
    c = _get_container(FTP_CONTAINER)
    installed = c is not None
    if not c:
        return {"stdout": "inactive", "installed": False}
    try:
        c.reload()
    except Exception:
        return {"stdout": "inactive", "installed": True}
    running = c.status == "running"
    return {"stdout": "active" if running else "inactive", "installed": True}


def webdav_server_status() -> dict[str, Any]:
    c = _get_container(WEBDAV_CONTAINER)
    installed = c is not None
    if not c:
        return {"stdout": "inactive", "installed": False}
    try:
        c.reload()
    except Exception:
        return {"stdout": "inactive", "installed": True}
    running = c.status == "running"
    return {"stdout": "active" if running else "inactive", "installed": True}
