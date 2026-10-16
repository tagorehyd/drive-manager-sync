"""Optional SQLite cache under /data/app.db (run history + processed media). JSON config stays authoritative."""
from __future__ import annotations

import logging
import sqlite3
import threading
from typing import Any

from app import config

log = logging.getLogger(__name__)
_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, job_id TEXT, status TEXT,
    started_at TEXT, finished_at TEXT, message TEXT);
CREATE TABLE IF NOT EXISTS media_files (
    job_id TEXT, source_path TEXT, size INTEGER, status TEXT, target TEXT, message TEXT, updated_at TEXT,
    PRIMARY KEY (job_id, source_path));
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DATA_DIR / "app.db", timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def execute(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    """Best effort: returns [] and logs on failure so a broken cache never breaks a job."""
    with _lock:
        try:
            conn = _connect()
            try:
                with conn:
                    return [dict(r) for r in conn.execute(sql, params).fetchall()]
            finally:
                conn.close()
        except sqlite3.Error as exc:
            log.warning("sqlite: %s", exc)
            return []


def init() -> bool:
    with _lock:
        try:
            conn = _connect()
            try:
                conn.executescript(SCHEMA)
            finally:
                conn.close()
        except sqlite3.Error as exc:
            log.warning("sqlite init: %s", exc)
    return healthy()


def healthy() -> bool:
    try:
        conn = _connect()
        try:
            conn.execute("SELECT 1 FROM runs LIMIT 1")
            return True
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def record_run(kind: str, job_id: str, status: str, started_at: str, message: str = "") -> None:
    execute("INSERT INTO runs (kind, job_id, status, started_at, finished_at, message) VALUES (?,?,?,?,?,?)",
            (kind, job_id, status, started_at, config.utc_now(), message[:1000]))


def recent_runs(limit: int = 20) -> list[dict[str, Any]]:
    return execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))


def media_record(job_id: str, source_path: str) -> dict[str, Any] | None:
    rows = execute("SELECT * FROM media_files WHERE job_id=? AND source_path=?", (job_id, source_path))
    return rows[0] if rows else None


def save_media_record(job_id: str, source_path: str, size: int, status: str, target: str = "", message: str = "") -> None:
    execute("INSERT OR REPLACE INTO media_files VALUES (?,?,?,?,?,?,?)",
            (job_id, source_path, size, status, target, message[:500], config.utc_now()))


def media_records(job_id: str, limit: int = 200) -> list[dict[str, Any]]:
    return execute("SELECT * FROM media_files WHERE job_id=? ORDER BY updated_at DESC LIMIT ?", (job_id, limit))
