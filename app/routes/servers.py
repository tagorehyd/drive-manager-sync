"""Server control endpoints (FTP/SMB/WebDAV)"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Any

from app import servers

router = APIRouter()


class FtpShareIn(BaseModel):
    name: str
    path: str


class FtpServiceIn(BaseModel):
    action: str


class FtpUserIn(BaseModel):
    username: str
    password: str


@router.get("/servers/ftp")
def ftp_status():
    return servers.ftp_server_status()


@router.post("/servers/ftp/shares")
def add_ftp_share(body: FtpShareIn):
    try:
        return {"ok": True, "share": servers.add_ftp_share(body.model_dump())}
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/servers/ftp/shares/{name}")
def remove_ftp_share(name: str):
    try:
        servers.remove_ftp_share(name)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@router.post("/servers/ftp/service")
def ftp_service(body: FtpServiceIn):
    try:
        res = servers.ftp_service_action(body.action)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": res["ok"], "stderr": res.get("stderr", "")}


@router.post("/servers/ftp/install")
def ftp_install():
    try:
        servers.ensure_ftp_packages()
        servers.write_ftp_config_to_container(servers._ftp_config().get("shares", []), servers._ftp_config().get("users", []))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@router.post("/servers/ftp/users")
def add_ftp_user(body: FtpUserIn):
    try:
        res = servers.set_ftp_password(body.username, body.password)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": res["ok"], "stderr": res.get("stderr", "")}


@router.delete("/servers/ftp/users/{username}")
def delete_ftp_user(username: str):
    try:
        res = servers.delete_ftp_user(username)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": res["ok"], "stderr": res.get("stderr", "")}


# ---------------------------------------------------------------- SMB server

class SmbShareIn(BaseModel):
    name: str
    path: str
    options: dict[str, Any] = Field(default_factory=dict) if False else dict


class SmbServiceIn(BaseModel):
    action: str


class SmbUserIn(BaseModel):
    username: str
    password: str


@router.get("/servers/smb")
def smb_status():
    return servers.smb_server_status()


@router.post("/servers/smb/shares")
def add_smb_share(body: SmbShareIn):
    try:
        return {"ok": True, "share": servers.add_smb_share(body.model_dump())}
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/servers/smb/shares/{name}")
def remove_smb_share(name: str):
    try:
        servers.remove_smb_share(name)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@router.post("/servers/smb/service")
def smb_service(body: SmbServiceIn):
    try:
        res = servers.smb_service_action(body.action)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": res["ok"], "stderr": res.get("stderr", "")}


@router.post("/servers/smb/install")
def smb_install():
    try:
        servers.ensure_smb_packages()
        servers.write_smb_config_to_container(servers._smb_config().get("shares", []), servers._smb_config().get("users", []))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@router.post("/servers/smb/users")
def add_smb_user(body: SmbUserIn):
    try:
        res = servers.set_smb_password(body.username, body.password)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": res["ok"], "stderr": res.get("stderr", "")}


@router.delete("/servers/smb/users/{username}")
def delete_smb_user(username: str):
    try:
        res = servers.delete_smb_user(username)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": res["ok"], "stderr": res.get("stderr", "")}


# ---------------------------------------------------------------- WebDAV server

class WebdavShareIn(BaseModel):
    name: str
    path: str


class WebdavServiceIn(BaseModel):
    action: str


class WebdavUserIn(BaseModel):
    username: str
    password: str


@router.get("/servers/webdav")
def webdav_status():
    return servers.webdav_server_status()


@router.post("/servers/webdav/shares")
def add_webdav_share(body: WebdavShareIn):
    try:
        return {"ok": True, "share": servers.add_webdav_share(body.model_dump())}
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/servers/webdav/shares/{name}")
def remove_webdav_share(name: str):
    try:
        servers.remove_webdav_share(name)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@router.post("/servers/webdav/service")
def webdav_service(body: WebdavServiceIn):
    try:
        res = servers.webdav_service_action(body.action)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": res["ok"], "stderr": res.get("stderr", "")}


@router.post("/servers/webdav/install")
def webdav_install():
    try:
        servers.ensure_webdav_packages()
        servers.write_webdav_config_to_container(servers._webdav_config().get("shares", []), servers._webdav_config().get("users", []))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@router.post("/servers/webdav/users")
def add_webdav_user(body: WebdavUserIn):
    try:
        res = servers.set_webdav_password(body.username, body.password)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": res["ok"], "stderr": res.get("stderr", "")}


@router.delete("/servers/webdav/users/{username}")
def delete_webdav_user(username: str):
    try:
        res = servers.delete_webdav_user(username)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": res["ok"], "stderr": res.get("stderr", "")}
