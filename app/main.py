"""FastAPI entrypoint: auth gate, pages, /healthz, startup/shutdown wiring."""
from __future__ import annotations

import logging
import os
import shutil
import threading
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException as FastAPIHTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import auth, config, db, fstab, media, mounts, rclone, scheduler, sync
from app.routes import api, auth as auth_routes
from app.templating import templates

log = logging.getLogger("drive_manager")

TABS = ["dashboard", "files", "mounts", "fstab", "sync", "rclone", "media", "servers", "settings"]
PUBLIC_PATHS = {"/setup", "/login", "/healthz"}


def _startup_background() -> None:
    """Slow host work (fstab read, auto-mounts, permission checks) must not block the web server."""
    try:
        fstab.reload()
        mounts.auto_mount_all()
        mounts.check_all()
    except Exception:  # noqa: BLE001
        log.exception("startup mount tasks failed")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    config.setup_logging()
    config.bootstrap()
    if os.environ.get("RESET_AUTH", "").lower() in ("1", "true", "yes"):
        auth.reset_auth()
        log.warning("RESET_AUTH=true: login reset, /setup is required. Remove RESET_AUTH after setup.")
    if not auth.is_setup_complete():
        log.warning("FIRST BOOT: setup password is %s (also in %s). Enter it on /setup.",
                    auth.setup_password(), auth.SETUP_PASSWORD_FILE)
    db.init()
    scheduler.start()
    sync.register_all()
    media.register_all()
    if os.environ.get("DM_SKIP_STARTUP_TASKS") != "1":
        threading.Thread(target=_startup_background, daemon=True, name="startup").start()
    log.info("Drive Manager started")
    yield
    scheduler.shutdown()
    rclone.unmount_all()
    log.info("Drive Manager stopped")


app = FastAPI(title="Drive Manager", lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(config.APP_DIR / "static")), name="static")
app.include_router(auth_routes.router)
app.include_router(api.router)


def _cors_kwargs() -> dict:
    origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "*").split(",") if o.strip()]
    if not origins or "*" in origins:
        # Credentialed CORS cannot use a literal "*", so reflect any origin instead.
        return {"allow_origin_regex": ".*"}
    return {"allow_origins": origins}


@app.middleware("http")
async def gate(request: Request, call_next):
    path = request.url.path
    if path.startswith("/static/") or path == "/healthz":
        return await call_next(request)
    is_api = path.startswith("/api/")
    if not auth.is_setup_complete():
        if path == "/setup":
            return await call_next(request)
        return JSONResponse({"detail": "setup required"}, 401) if is_api else RedirectResponse("/setup", 303)
    if path in PUBLIC_PATHS:
        return await call_next(request)
    if not auth.session_user(request.cookies.get(auth.COOKIE_NAME)):
        return JSONResponse({"detail": "not authenticated"}, 401) if is_api else RedirectResponse("/login", 303)
    return await call_next(request)


# Added after the auth gate so it wraps it: CORS preflights are answered before authentication.
app.add_middleware(CORSMiddleware, allow_credentials=True, allow_methods=["*"], allow_headers=["*"], **_cors_kwargs())


@app.exception_handler(ValueError)
async def value_error(_request: Request, exc: ValueError):
    return JSONResponse({"detail": str(exc)}, 400)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    if isinstance(exc, FastAPIHTTPException):
        return JSONResponse({"detail": exc.detail}, exc.status_code)
    ref = uuid.uuid4().hex[:8]
    log.exception("unhandled error ref=%s on %s %s", ref, request.method, request.url.path)
    return JSONResponse({"detail": f"Internal error (ref {ref}); see /data/logs/app.log"}, 500)


@app.get("/healthz")
def healthz():
    config_ok = config.CONFIG_DIR.is_dir() and os.access(config.CONFIG_DIR, os.W_OK)
    database_ok = db.healthy()
    rclone_ok = shutil.which("rclone") is not None
    managed = mounts.list_mounts()
    mounts_ok = all(m["mounted"] for m in managed if m.get("auto_mount"))
    try:
        free = shutil.disk_usage(str(config.DATA_DIR)).free
        disk_free = f"{free / 1024 ** 3:.1f} GiB"
    except OSError:
        disk_free = "unknown"
    ok = config_ok and database_ok
    return {"status": "ok" if ok and mounts_ok else ("degraded" if ok else "error"), "config": config_ok,
            "database": database_ok, "rclone": rclone_ok, "mounts": mounts_ok, "disk_free": disk_free}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {"tab": "dashboard", "tabs": TABS})


@app.get("/{tab}", response_class=HTMLResponse)
def tab_page(request: Request, tab: str):
    if tab not in TABS:
        return HTMLResponse("Not found", status_code=404)
    return templates.TemplateResponse(request, "index.html", {"tab": tab, "tabs": TABS})
