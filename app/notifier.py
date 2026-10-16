"""Failure-only notifications: Telegram and a generic JSON webhook."""
from __future__ import annotations

import logging
import os
from typing import Any

import requests

from app import config

log = logging.getLogger(__name__)


def _md(text: Any) -> str:
    value = "" if text is None else str(text)
    for ch in ("\\", "_", "*", "[", "`"):
        value = value.replace(ch, "\\" + ch)
    return value


def format_failure(kind: str, job: str, reason: str, source: str = "", destination: str = "") -> str:
    lines = [f"🚨 *{_md(kind)} job failed*", "", f"📁 *Job:* {_md(job)}"]
    if source:
        lines.append(f"📤 *Source:* `{source.replace('`', '')}`")
    if destination:
        lines.append(f"📥 *Destination:* `{destination.replace('`', '')}`")
    lines += [f"❗ *Reason:* {_md(reason)}", f"🕒 *Time:* {config.utc_now()}", "", "Check the Drive Manager UI for details."]
    return "\n".join(lines)


def settings() -> dict[str, Any]:
    data = config.load("notifications")
    tg = data.get("telegram") or {}
    tg.setdefault("bot_token", "")
    tg.setdefault("chat_id", "")
    if not tg["bot_token"] and os.environ.get("TELEGRAM_BOT_TOKEN"):
        tg.update(bot_token=os.environ["TELEGRAM_BOT_TOKEN"], chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""), enabled=True)
    data["telegram"] = tg
    return data


def send(text: str, payload: dict[str, Any]) -> dict[str, Any]:
    cfg = settings()
    result: dict[str, Any] = {"telegram": None, "webhook": None}
    tg = cfg["telegram"]
    if tg.get("enabled") and tg.get("bot_token") and tg.get("chat_id"):
        try:
            resp = requests.post(f"https://api.telegram.org/bot{tg['bot_token']}/sendMessage",
                                 json={"chat_id": tg["chat_id"], "text": text, "parse_mode": "Markdown"}, timeout=15)
            result["telegram"] = resp.status_code == 200
        except requests.RequestException as exc:
            log.warning("telegram send failed: %s", type(exc).__name__)
            result["telegram"] = False
    hook = cfg.get("webhook") or {}
    if hook.get("enabled") and hook.get("url"):
        try:
            resp = requests.post(hook["url"], json=payload, timeout=15)
            result["webhook"] = 200 <= resp.status_code < 300
        except requests.RequestException as exc:
            log.warning("webhook send failed: %s", type(exc).__name__)
            result["webhook"] = False
    return result


def notify_failure(kind: str, job: dict[str, Any], reason: str) -> bool:
    """Send once per failure streak. Returns True when a message was sent.

    The caller persists ``job['last_failure_notified']`` and resets it on success.
    """
    if job.get("last_failure_notified"):
        return False
    text = format_failure(kind.title(), job.get("name", "?"), reason, job.get("source", ""), job.get("destination", ""))
    send(text, {"event": f"{kind}.failed", "job": job.get("name"), "job_id": job.get("id"), "reason": reason,
                "source": job.get("source"), "destination": job.get("destination"), "time": config.utc_now()})
    job["last_failure_notified"] = True
    return True
