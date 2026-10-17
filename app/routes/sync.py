"""Sync and scheduling endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Any

from app import sync
from app.routes._utils import _bad

router = APIRouter()


class ScheduleIn(BaseModel):
    spec: dict[str, Any] = Field(default_factory=dict)


@router.post("/schedule/preview")
def schedule_preview(body: ScheduleIn):
    try:
        cron = sync.build_cron(body.spec)
    except (ValueError, TypeError) as exc:
        raise _bad(exc)
    event = body.spec.get("type") == "event"
    return {"cron": cron, "text": sync.describe(cron, event), "next_runs": sync.next_runs(cron)}


@router.get("/sync/jobs")
def sync_jobs():
    return {"jobs": sync.list_jobs()}


@router.post("/sync/jobs")
def create_sync_job(body: dict[str, Any]):
    try:
        return {"ok": True, "job": sync.save_job(body)}
    except (ValueError, TypeError) as exc:
        raise _bad(exc)


@router.put("/sync/jobs/{job_id}")
def update_sync_job(job_id: str, body: dict[str, Any]):
    try:
        return {"ok": True, "job": sync.save_job(body, job_id)}
    except KeyError as exc:
        raise _bad(exc, 404)
    except (ValueError, TypeError) as exc:
        raise _bad(exc)


@router.delete("/sync/jobs/{job_id}")
def delete_sync_job(job_id: str):
    try:
        sync.delete_job(job_id)
    except KeyError as exc:
        raise _bad(exc, 404)
    return {"ok": True}


@router.post("/sync/jobs/{job_id}/run")
def run_sync_job(job_id: str):
    try:
        return sync.run_job(job_id, "manual")
    except KeyError as exc:
        raise _bad(exc, 404)
