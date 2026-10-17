"""Media endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Any
import os

from app import media, db

router = APIRouter()


class PreviewIn(BaseModel):
    filename: str
    destination: str = "/mnt/media"
    use_llm: bool = False


@router.get("/media/jobs")
def media_jobs():
    return {"jobs": media.list_jobs()}


@router.post("/media/jobs")
def create_media_job(body: dict[str, Any]):
    try:
        return {"ok": True, "job": media.save_job(body)}
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.put("/media/jobs/{job_id}")
def update_media_job(job_id: str, body: dict[str, Any]):
    try:
        return {"ok": True, "job": media.save_job(body, job_id)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/media/jobs/{job_id}")
def delete_media_job(job_id: str):
    try:
        media.delete_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"ok": True}


@router.post("/media/jobs/{job_id}/scan")
def scan_media_job(job_id: str):
    try:
        return media.start_scan(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/media/jobs/{job_id}/records")
def media_records(job_id: str):
    return {"records": db.media_records(job_id)}


@router.get("/media/settings")
def get_media_settings():
    return {"settings": media.settings(), "llm_key_set": bool(os.environ.get("OPENAI_COMPATIBLE_API_KEY"))}


@router.put("/media/settings")
def put_media_settings(body: dict[str, Any]):
    return {"settings": media.save_settings(body)}


@router.post("/media/preview")
def media_preview(body: PreviewIn):
    return media.preview(body.filename, body.destination, body.use_llm)
