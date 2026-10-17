"""Logs endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app import config

router = APIRouter()


@router.get("/logs")
def logs():
    return {"logs": config.list_logs()}


@router.get("/logs/{name}")
def log_tail(name: str, lines: int = Query(300, ge=1, le=5000), level: str = ""):
    try:
        return {"name": name, "lines": config.tail_log(name, lines, level)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
