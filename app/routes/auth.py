"""Setup, login, logout and change-password."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from app import auth, config
from app.templating import templates

router = APIRouter()
log = logging.getLogger(__name__)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


@router.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request):
    if auth.is_setup_complete():
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "setup.html", {"error": None, "username": "admin"})


@router.post("/setup")
def setup_submit(request: Request, username: str = Form("admin"), password: str = Form(...), confirm: str = Form(...),
                 setup_password: str = Form("")):
    if auth.is_setup_complete():
        return RedirectResponse("/login", status_code=303)
    try:
        auth.complete_setup(username, password, confirm, setup_password)
    except TimeoutError:
        return templates.TemplateResponse(request, "setup.html", {
            "error": "Too many wrong setup passwords. Try again in 5 minutes.", "username": username}, status_code=429)
    except PermissionError as exc:
        return templates.TemplateResponse(request, "setup.html", {"error": str(exc), "username": username}, status_code=403)
    except ValueError as exc:
        return templates.TemplateResponse(request, "setup.html", {"error": str(exc), "username": username}, status_code=400)
    return RedirectResponse("/login", status_code=303)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if not auth.is_setup_complete():
        return RedirectResponse("/setup", status_code=303)
    if auth.session_user(request.cookies.get(auth.COOKIE_NAME)):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    try:
        token = auth.login(username, password)
    except TimeoutError as exc:
        minutes = max(1, int(exc.args[0]) // 60) if exc.args else 5
        return templates.TemplateResponse(request, "login.html", {
            "error": f"Too many failed logins. Account locked for about {minutes} minute(s)."}, status_code=429)
    except PermissionError:
        log.warning("failed login for %r from %s", username[:64], _client_ip(request))
        return templates.TemplateResponse(request, "login.html", {"error": "Invalid username or password"}, status_code=401)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(auth.COOKIE_NAME, token, max_age=auth.session_max_age(), httponly=True,
                        samesite=auth.cookie_samesite(), secure=auth.cookie_secure())
    return response


@router.post("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(auth.COOKIE_NAME)
    return response


class ChangePassword(BaseModel):
    old_password: str
    new_password: str
    confirm: str


@router.post("/api/auth/change-password")
def change_password(body: ChangePassword):
    try:
        auth.change_password(body.old_password, body.new_password, body.confirm)
    except PermissionError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=403)
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    data = config.load("auth")
    response = JSONResponse({"ok": True, "detail": "Password changed. Other sessions were signed out."})
    response.set_cookie(auth.COOKIE_NAME, auth.issue_session(data["username"], int(data["session_epoch"])),
                        max_age=auth.session_max_age(), httponly=True, samesite=auth.cookie_samesite(), secure=auth.cookie_secure())
    return response
