"""Settings, notifications and browse-roots endpoints."""
from __future__ import annotations

import os
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Any

from app import config, notifier

router = APIRouter()


class NotificationsIn(BaseModel):
    telegram_enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""
    webhook_enabled: bool = False
    webhook_url: str = ""


class RootsIn(BaseModel):
    roots: list[str]


@router.get("/settings/notifications")
def get_notifications():
    data = config.load("notifications")
    tg, hook = data.get("telegram") or {}, data.get("webhook") or {}
    return {"telegram_enabled": bool(tg.get("enabled")), "chat_id": tg.get("chat_id", ""),
            "bot_token_set": bool(tg.get("bot_token")), "webhook_enabled": bool(hook.get("enabled")),
            "webhook_url": hook.get("url", "")}


@router.put("/settings/notifications")
def put_notifications(body: NotificationsIn):
    if body.webhook_url and not body.webhook_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="webhook URL must start with http:// or https://")

    def apply(d: dict[str, Any]) -> None:
        tg = d.setdefault("telegram", {})
        tg.update(enabled=body.telegram_enabled, chat_id=body.chat_id.strip())
        if body.bot_token.strip():
            tg["bot_token"] = body.bot_token.strip()
        d["webhook"] = {"enabled": body.webhook_enabled, "url": body.webhook_url.strip()}
    config.update("notifications", apply)
    return get_notifications()


@router.post("/settings/notifications/test")
def test_notifications():
    return notifier.send("✅ *Drive Manager* test notification", {"event": "test", "time": config.utc_now()})


@router.get("/settings/browse-roots")
def get_roots():
    return {"roots": config.browse_roots(), "env_override": bool(os.environ.get("BROWSE_ROOTS"))}


@router.put("/settings/browse-roots")
def put_roots(body: RootsIn):
    roots = [os.path.normpath(r.strip()) for r in body.roots if r.strip()]
    if not roots or any(not os.path.isabs(r) or r == "/" for r in roots):
        raise HTTPException(status_code=400, detail="roots must be absolute paths other than /")
    config.update("app", lambda d: d.update(browse_roots=roots))
    return get_roots()
