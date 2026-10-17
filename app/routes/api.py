"""Main API aggregator that includes per-area routers."""
from __future__ import annotations

from fastapi import APIRouter

from app.routes import (
    dashboard,
    browse,
    mounts,
    fstab,
    sync,
    rclone,
    media,
    settings,
    logs,
    servers,
)

router = APIRouter(prefix="/api")

# include subrouters which define their own paths (e.g. /dashboard, /mounts)
router.include_router(dashboard.router)
router.include_router(browse.router)
router.include_router(mounts.router)
router.include_router(fstab.router)
router.include_router(sync.router)
router.include_router(rclone.router)
router.include_router(media.router)
router.include_router(settings.router)
router.include_router(logs.router)
router.include_router(servers.router)
