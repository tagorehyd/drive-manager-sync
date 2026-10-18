"""Media renamer for Jellyfin: series -> movie -> LLM detection, category rules, staged atomic moves."""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

import requests
from apscheduler.triggers.interval import IntervalTrigger

from app import config, db, mounts, notifier, scheduler

log = logging.getLogger(__name__)

EXTENSIONS = {".mkv", ".mp4", ".avi", ".mov", ".m4v"}
MIN_FILE_AGE = float(os.environ.get("MEDIA_MIN_FILE_AGE", "30"))
TMP_SUFFIX = ".dm-tmp"

SERIES_PATTERNS = [
    re.compile(r"\bS(\d{1,2})\s*E(\d{1,3})\b", re.I),
    re.compile(r"\b(\d{1,2})x(\d{1,3})\b", re.I),
    re.compile(r"\bSeason\s*(\d{1,2})\s*Episode\s*(\d{1,3})\b", re.I),
]
MOVIE_PATTERN = re.compile(r"^(?P<title>.+?)[\s(\[]+(?P<year>(?:19|20)\d{2})(?:[)\].]|\s|$)")
LANGUAGES = {
    "telugu": "Telugu", "tel": "Telugu", "tamil": "Tamil", "tam": "Tamil", "hindi": "Hindi", "hin": "Hindi",
    "malayalam": "Malayalam", "mal": "Malayalam", "kannada": "Kannada", "kan": "Kannada",
    "english": "English", "eng": "English", "korean": "Korean", "kor": "Korean", "japanese": "Japanese",
    "spanish": "Spanish", "french": "French", "german": "German",
}
NOISE = re.compile(r"\b(2160p|1080p|720p|480p|4k|uhd|hdr|x264|x265|h264|h265|hevc|web[- ]?dl|webrip|bluray|"
                   r"brrip|hdrip|dvdrip|aac|ddp?5 1|dts|10bit|proper|repack)\b.*$", re.I)


# ================================================================ parsing

def clean_name(name: str) -> str:
    stem = os.path.splitext(name)[0] if os.path.splitext(name)[1].lower() in EXTENSIONS else name
    # Strip leading site watermark (e.g. "www.5MovieRulz.company - Title ...").
    # Remove everything before the first " - " when that prefix looks like a domain.
    if " - " in stem:
        prefix = stem.split(" - ", 1)[0]
        if "." in prefix:
            stem = stem.split(" - ", 1)[1]
    stem = re.sub(r"^\s*(?:www\.)?[A-Za-z0-9-]+\.(?:com|net|org|in|me|co|tv|cc|xyz|to|ws)\s*[-_]*\s*", "", stem)
    stem = re.sub(r"^\s*\[[^\]]*\]\s*", "", stem)
    stem = stem.replace("_", " ")
    stem = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", stem)
    return re.sub(r"\s+", " ", stem).strip(" -")


def sanitize(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", value or "")
    value = re.sub(r"\s+", " ", value).strip(" .")
    return value or "Unknown"


# ================================================================ renaming (matches Media-RenamerForJelly)

def sanitize_filename_text(value: Any) -> str:
    text = str(value or "Unknown Movie").strip()
    text = re.sub(r'[<>:"/\\|?*]', " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" .") or "Unknown Movie"


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        items = value
    else:
        items = [value]
    cleaned: list[str] = []
    for item in items:
        if item is None:
            continue
        text = str(item).strip()
        if text:
            cleaned.append(text)
    return cleaned


def normalize_language(value: Any) -> list[str]:
    normalized: list[str] = []
    for item in _as_list(value):
        text = str(item).strip()
        if not text:
            continue
        text = text.split("/")[0].split(",")[0].strip()
        cleaned = sanitize_filename_text(text).lower()
        if cleaned and cleaned != "unknown":
            normalized.append(cleaned)
    return normalized or ["unknown"]


def normalize_quality(value: Any) -> list[str]:
    normalized: list[str] = []
    for item in _as_list(value):
        text = str(item).strip()
        if not text:
            continue
        text = text.replace("-", " ").replace("_", " ")
        text = re.sub(r"\s+", " ", text)
        cleaned = sanitize_filename_text(text).lower()
        if cleaned and cleaned != "unknown":
            normalized.append(cleaned)
    return normalized or ["unknown"]


def _matches_any(candidate_values: Iterable[str], configured_values: Iterable[str]) -> bool:
    candidate_list = [str(value).strip().lower() for value in candidate_values if str(value).strip()]
    configured_list = [str(value).strip().lower() for value in configured_values if str(value).strip()]
    if not candidate_list or not configured_list:
        return False
    if "*" in configured_list:
        return True
    for candidate in candidate_list:
        for configured in configured_list:
            if configured in candidate or candidate in configured:
                return True
    return False


_CATEGORY_RULES_PATH = Path(__file__).resolve().parent.parent / "state" / "category_rules.json"


def _default_category_rules() -> dict[str, Any]:
    return {
        "rules": [
            {"category": "Temp_cam_RIPS", "language": ["*"], "quality": ["dvdscr", "cam", "ts", "telecine"]},
            {"category": "Bollywood", "language": ["hindi"], "quality": ["*"]},
            {"category": "Tollywood", "language": ["telugu", "tamil", "malayalam", "kannada"], "quality": ["*"]},
            {"category": "Hollywood", "language": ["english", "japanese", "japan", "chinese", "china", "german", "french", "spanish", "italian", "korean"], "quality": ["*"]},
        ]
    }


def load_category_rules_from_settings(settings: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Read category rules from media-jobs settings (flat {language: category} or rule list)."""
    settings = settings or config.load("media-jobs")["settings"]
    rules_cfg = settings.get("category_rules") or {}
    if isinstance(rules_cfg, list):
        return [r for r in rules_cfg if isinstance(r, dict)]
    # flat {language: category} -> convert to rule list
    rules = []
    for lang, cat in rules_cfg.items():
        if lang.strip() and cat.strip():
            rules.append({"category": cat.strip(), "language": [lang.strip()], "quality": ["*"]})
    if not rules:
        rules = _default_category_rules()["rules"]
    return rules


def resolve_category(language: Any, quality: Any, settings: dict[str, Any] | None = None) -> str:
    normalized_language = normalize_language(language)
    normalized_quality = normalize_quality(quality)
    matching_rules = []
    for index, rule in enumerate(load_category_rules_from_settings(settings)):
        rule_language = _as_list(rule.get("language"))
        rule_quality = _as_list(rule.get("quality"))
        if _matches_any(normalized_language, rule_language) and _matches_any(normalized_quality, rule_quality):
            matching_rules.append((index, rule))
    if not matching_rules:
        return "Hollywood"
    for _, rule in matching_rules:
        category = str(rule.get("category") or "Hollywood").strip()
        if category:
            return category
    return "Hollywood"


def build_target_filename(metadata: Dict[str, Any], extension: str) -> str:
    title = sanitize_filename_text(metadata.get("movie_name") or "Unknown Movie")
    year = metadata.get("year")
    year_text = f"({year})" if year else "(Unknown)"
    return f"{title} {year_text}{extension.lower()}"


def build_series_target_filename(metadata: Dict[str, Any], extension: str) -> str:
    title = sanitize_filename_text(metadata.get("movie_name") or "Unknown Series")
    season = metadata.get("season")
    episode = metadata.get("episode")
    season_text = f"S{int(season):02d}" if season is not None else "S00"
    episode_text = f"E{int(episode):02d}" if episode is not None else "E00"
    return f"{title} {season_text}{episode_text}{extension.lower()}"


def rename_series(file_path: str | Path, metadata: Dict[str, Any], out_dir: str | Path) -> str:
    source_path = Path(file_path)
    out_root = Path(out_dir)
    if not source_path.exists():
        raise FileNotFoundError(f"Source file not found: {source_path}")
    series_dir = out_root / "Series" / sanitize_filename_text(metadata.get("movie_name") or "Unknown Series")
    season_dir = series_dir / f"Season {int(metadata.get('season') or 0):02d}"
    season_dir.mkdir(parents=True, exist_ok=True)
    extension = source_path.suffix.lower()
    target_name = build_series_target_filename(metadata, extension)
    destination = season_dir / target_name
    counter = 1
    while destination.exists():
        stem = destination.stem
        destination = season_dir / f"{stem}_{counter}{extension}"
        counter += 1
    shutil.move(str(source_path), str(destination))
    return str(destination)


def rename_and_move(file_path: str | Path, metadata: Dict[str, Any], in_dir: str | Path, out_dir: str | Path, settings: dict[str, Any] | None = None) -> str:
    source_path = Path(file_path)
    out_root = Path(out_dir)
    if not source_path.exists():
        raise FileNotFoundError(f"Source file not found: {source_path}")
    if metadata.get("is_series"):
        return rename_series(file_path, metadata, out_root)
    category = resolve_category(metadata.get("language"), metadata.get("quality"), settings)
    base_dir = out_root / category
    base_dir.mkdir(parents=True, exist_ok=True)
    extension = source_path.suffix.lower()
    target_name = build_target_filename(metadata, extension)
    destination = base_dir / target_name
    counter = 1
    while destination.exists():
        stem = destination.stem
        destination = base_dir / f"{stem}_{counter}{extension}"
        counter += 1
    shutil.move(str(source_path), str(destination))
    return str(destination)


def _title(text: str) -> str:
    text = NOISE.sub("", text)
    text = re.sub(r"[\s(\[]*(?:19|20)\d{2}[)\]]?\s*$", "", text)
    text = re.sub(r"[\[\](){}]", " ", text)
    return re.sub(r"\s+", " ", text).strip(" -.")


def detect_language(name: str) -> str | None:
    for token in re.split(r"[^A-Za-z]+", name.lower()):
        if token in LANGUAGES:
            return LANGUAGES[token]
    return None


QUALITY_HINTS = (
    "2160p", "1080p", "720p", "480p", "4k", "uhd", "hdr",
    "x264", "x265", "h264", "h265", "hevc", "web-dl", "webdl",
    "webrip", "bluray", "blu-ray", "brrip", "hdrip", "dvdrip",
    "dvdscr", "cam", "ts", "tc", "telecine", "aac", "ddp5.1",
    "dts", "10bit", "proper", "repack",
)


def detect_quality(name: str) -> str | None:
    lowered = name.lower().replace("_", " ").replace("-", " ")
    for hint in sorted(QUALITY_HINTS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(hint)}\b", lowered):
            return hint.upper().replace("-", "")
    return None


def detect_series(filename: str, parent: str = "") -> dict[str, Any] | None:
    cleaned = clean_name(filename)
    for pattern in SERIES_PATTERNS:
        m = pattern.search(cleaned)
        if m:
            show = _title(cleaned[:m.start()])
            if not show and parent:
                show = _title(re.sub(r"\bSeason\s*\d+\b|\bS\d{1,2}\b", "", clean_name(parent), flags=re.I))
            return {"kind": "series", "title": show or "Unknown Show", "movie_name": show or "Unknown Show",
                    "season": int(m.group(1)), "episode": int(m.group(2)), "year": None,
                    "language": detect_language(filename), "quality": detect_quality(filename), "source": "series"}
    return None


def detect_movie(filename: str) -> dict[str, Any] | None:
    cleaned = clean_name(filename)
    m = MOVIE_PATTERN.match(cleaned)
    if not m:
        return None
    title = _title(m.group("title"))
    if not title:
        return None
    return {"kind": "movie", "title": title, "movie_name": title, "year": int(m.group("year")), "season": None, "episode": None,
            "language": detect_language(filename), "quality": detect_quality(filename), "source": "movie"}


def _extract_json(text: str) -> dict[str, Any] | None:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip(), flags=re.I)
    try:
        data = json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except ValueError:
            return None
    return data if isinstance(data, dict) else None


def llm_payload(filename: str, settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build the LLM chat-completion request body for a filename (without sending it)."""
    settings = settings or config.load("media-jobs")["settings"]
    key = os.environ.get("OPENAI_COMPATIBLE_API_KEY", "")
    base = (os.environ.get("OPENAI_COMPATIBLE_BASE_URL") or settings.get("llm_base_url") or "").rstrip("/")
    prompt = ("Identify the movie from this release file name. Reply with ONLY JSON: "
              '{"movie_name": "", "year": "", "language": "", "imdb_id": ""}. Use empty strings when unsure.')
    return {
        "available": bool(key and base and settings.get("use_llm", True)),
        "url": f"{base}/chat/completions" if base else None,
        "model": settings.get("llm_model", "gpt-4o-mini"),
        "temperature": 0,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": filename},
        ],
    }


def llm_identify(filename: str, settings: dict[str, Any] | None = None, timeout: float = 30) -> dict[str, Any] | None:
    """OpenAI-compatible chat completion returning {movie_name, year, language, imdb_id}."""
    settings = settings or config.load("media-jobs")["settings"]
    key = os.environ.get("OPENAI_COMPATIBLE_API_KEY", "")
    base = (os.environ.get("OPENAI_COMPATIBLE_BASE_URL") or settings.get("llm_base_url") or "").rstrip("/")
    if not key or not base or not settings.get("use_llm", True):
        return None
    payload = llm_payload(filename, settings)
    try:
        resp = requests.post(payload["url"], timeout=timeout,
                             headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                             json={"model": payload["model"], "temperature": payload["temperature"],
                                   "messages": payload["messages"]})
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
    except (requests.RequestException, KeyError, IndexError, ValueError) as exc:
        log.warning("LLM lookup failed for %s: %s", filename, type(exc).__name__)
        return None
    data = _extract_json(content)
    if not data or not str(data.get("movie_name") or "").strip():
        return None
    year_match = re.search(r"(19|20)\d{2}", str(data.get("year") or ""))
    return {"kind": "movie", "title": _title(str(data["movie_name"])) or str(data["movie_name"]).strip(),
            "year": int(year_match.group(0)) if year_match else None, "season": None, "episode": None,
            "language": (str(data.get("language") or "").strip().title() or None) or detect_language(filename),
            "quality": str(data.get("quality") or detect_quality(filename) or "unknown"),
            "imdb_id": str(data.get("imdb_id") or "").strip() or None, "source": "llm"}


def identify(filename: str, parent: str = "", use_llm: bool = True) -> dict[str, Any] | None:
    return detect_series(filename, parent) or detect_movie(filename) or (llm_identify(filename) if use_llm else None)


def category_for(language: str | None, quality: str | None = None, settings: dict[str, Any] | None = None) -> str:
    settings = settings or config.load("media-jobs")["settings"]
    return sanitize(resolve_category(language, quality, settings))


def target_path(info: dict[str, Any], destination: str, ext: str, settings: dict[str, Any] | None = None) -> Path:
    ext = ext.lower()
    settings = settings or config.load("media-jobs")["settings"]
    if info["kind"] == "series":
        title = sanitize_filename_text(info.get("movie_name") or "Unknown Series")
        season = int(info.get("season") or 0)
        episode = int(info.get("episode") or 0)
        season_dir = Path(destination) / "Series" / title / f"Season {season:02d}"
        target_name = f"{title} S{season:02d}E{episode:02d}{ext}"
        return season_dir / target_name
    name = sanitize_filename_text(info.get("title") or "Unknown Movie")
    year = info.get("year")
    year_text = f" ({year})" if year else ""
    category = resolve_category(info.get("language"), info.get("quality"), settings)
    target_name = f"{name}{year_text}{ext}"
    return Path(destination) / "Movies" / category / target_name


def preview(filename: str, destination: str = "/mnt/media", use_llm: bool = False) -> dict[str, Any]:
    """Preview how a filename would be renamed.

    A direct filename match (series/movie pattern) is tried first without an LLM
    call. If that matches, the rename target is returned with no LLM body.
    Otherwise the LLM fallback is considered and the LLM request body is exposed
    so the UI can preview what would be sent to the LLM.
    """
    name = os.path.basename(filename)
    parent = os.path.basename(os.path.dirname(filename) or ".")
    ext = os.path.splitext(filename)[1]

    # Step 1: direct match without any LLM call.
    info = identify(name, parent, use_llm=False)
    if info and info.get("movie_name"):
        return {
            "ok": True,
            "processing_method": "direct match",
            "source": name,
            "info": info,
            "target": str(target_path(info, destination, ext)),
            "llm_request": None,
        }

    # Step 2: no direct match -> consider LLM fallback and expose its request body.
    request = llm_payload(name)
    if not use_llm or not request.get("available") or not request.get("url"):
        return {
            "ok": False,
            "processing_method": "llm",
            "source": name,
            "info": None,
            "target": None,
            "error": "no direct match; LLM fallback not enabled",
            "llm_request": request,
        }

    llm_info = llm_identify(name)
    if llm_info and llm_info.get("movie_name"):
        return {
            "ok": True,
            "processing_method": "llm",
            "source": name,
            "info": llm_info,
            "target": str(target_path(llm_info, destination, ext)),
            "llm_request": request,
        }
    return {
        "ok": False,
        "processing_method": "llm",
        "source": name,
        "info": None,
        "target": None,
        "error": "no direct match and LLM did not identify the file",
        "llm_request": request,
    }


# ================================================================ staged atomic move

def _copy_fsync(src: Path, dst_tmp: Path) -> None:
    with open(src, "rb") as fin, open(dst_tmp, "wb") as fout:
        shutil.copyfileobj(fin, fout, 4 * 1024 * 1024)
        fout.flush()
        os.fsync(fout.fileno())


def place_file(source: Path, target: Path, staging_dir: Path, move_source: bool) -> None:
    """copy to staging -> verify -> move to destination (.tmp + fsync + os.replace) -> cleanup."""
    size = source.stat().st_size
    staging_dir.mkdir(parents=True, exist_ok=True)
    staged = staging_dir / f"{uuid.uuid4().hex[:8]}-{source.name}"
    staged_tmp = staged.with_name(staged.name + TMP_SUFFIX)
    try:
        _copy_fsync(source, staged_tmp)
        os.replace(staged_tmp, staged)
        if staged.stat().st_size != size:
            raise OSError("staging verification failed: size mismatch")
        target.parent.mkdir(parents=True, exist_ok=True)
        target_tmp = target.with_name(target.name + TMP_SUFFIX)
        if staged.stat().st_dev == target.parent.stat().st_dev:
            os.replace(staged, target)
        else:
            _copy_fsync(staged, target_tmp)
            if target_tmp.stat().st_size != size:
                target_tmp.unlink(missing_ok=True)
                raise OSError("destination verification failed: size mismatch")
            os.replace(target_tmp, target)
        if target.stat().st_size != size:
            raise OSError("final verification failed")
        if move_source:
            source.unlink()
    finally:
        staged_tmp.unlink(missing_ok=True)
        staged.unlink(missing_ok=True)


# ================================================================ jobs

class MediaError(ValueError):
    pass


def settings() -> dict[str, Any]:
    return config.load("media-jobs")["settings"]


def save_settings(data: dict[str, Any]) -> dict[str, Any]:
    def apply(d: dict[str, Any]) -> None:
        s = d["settings"]
        for key in ("llm_base_url", "llm_model", "default_category"):
            if key in data:
                s[key] = str(data[key]).strip()
        if "use_llm" in data:
            s["use_llm"] = bool(data["use_llm"])
        if isinstance(data.get("category_rules"), dict):
            s["category_rules"] = {str(k).strip(): str(v).strip() for k, v in data["category_rules"].items() if str(k).strip()}
    return config.update("media-jobs", apply)["settings"]


def list_jobs() -> list[dict[str, Any]]:
    return [{**j, "running": j["id"] in _running} for j in config.load("media-jobs")["jobs"]]


def get_job(job_id: str) -> dict[str, Any]:
    for job in config.load("media-jobs")["jobs"]:
        if job["id"] == job_id:
            return job
    raise KeyError("media job not found")


def _check_dir(path: str, label: str) -> str:
    real = os.path.normpath((path or "").strip())
    if not os.path.isabs(real) or not any(real == r or real.startswith(r.rstrip("/") + "/") for r in config.browse_roots()):
        raise MediaError(f"{label} must be inside {', '.join(config.browse_roots())}")
    return real


def save_job(data: dict[str, Any], job_id: str | None = None) -> dict[str, Any]:
    old = get_job(job_id) if job_id else {}
    name = str(data.get("name") or "").strip()
    if not name:
        raise MediaError("name is required")
    job = {**old, "id": old.get("id") or config.new_id(), "name": name,
           "source": _check_dir(data.get("source", ""), "source"),
           "destination": _check_dir(data.get("destination", ""), "destination"),
           "mode": data.get("mode", "move"), "enabled": bool(data.get("enabled", True)),
           "event_driven": bool(data.get("event_driven", True)),
           "scan_minutes": max(1, min(int(data.get("scan_minutes") or 15), 1440))}
    if job["mode"] not in ("copy", "move"):
        raise MediaError("mode must be copy or move")
    if job["source"] == job["destination"] or job["destination"].startswith(job["source"] + "/"):
        raise MediaError("destination must not be inside the source folder")
    job.setdefault("last_scan", None)
    job.setdefault("last_failure_notified", False)
    config.update("media-jobs", lambda d: d.update(jobs=[j for j in d["jobs"] if j["id"] != job["id"]] + [job]))
    register(job)
    return job


def delete_job(job_id: str) -> None:
    get_job(job_id)
    unregister(job_id)
    config.update("media-jobs", lambda d: d.update(jobs=[j for j in d["jobs"] if j["id"] != job_id]))


def _patch(job_id: str, **fields: Any) -> None:
    def apply(d: dict[str, Any]) -> None:
        for j in d["jobs"]:
            if j["id"] == job_id:
                j.update(fields)
    config.update("media-jobs", apply)


def register(job: dict[str, Any]) -> None:
    unregister(job["id"])
    if not job.get("enabled"):
        return
    scheduler.scheduler.add_job(scan_job, IntervalTrigger(minutes=job.get("scan_minutes", 15)),
                                id=f"media-{job['id']}", args=[job["id"]], replace_existing=True)
    if job.get("event_driven"):
        scheduler.watch(f"media-{job['id']}", job["source"], lambda jid=job["id"]: start_scan(jid))


def unregister(job_id: str) -> None:
    scheduler.remove(f"media-{job_id}")
    scheduler.unwatch(f"media-{job_id}")


def register_all() -> None:
    for job in config.load("media-jobs")["jobs"]:
        try:
            register(job)
        except Exception as exc:  # noqa: BLE001
            log.warning("cannot schedule media job %s: %s", job.get("name"), exc)


_running: set[str] = set()
_rlock = threading.Lock()


def start_scan(job_id: str) -> dict[str, Any]:
    get_job(job_id)
    threading.Thread(target=scan_job, args=(job_id,), daemon=True, name=f"media-{job_id}").start()
    return {"ok": True, "started": True}


def _stable(path: Path) -> bool:
    try:
        st = path.stat()
        if time.time() - st.st_mtime < MIN_FILE_AGE:
            return False
        time.sleep(0.5)
        return path.stat().st_size == st.st_size
    except OSError:
        return False


def scan_job(job_id: str) -> dict[str, Any]:
    with _rlock:
        if job_id in _running:
            return {"ok": False, "error": "already running"}
        _running.add(job_id)
    try:
        return _scan(job_id)
    except KeyError:
        return {"ok": False, "error": "job deleted"}
    finally:
        with _rlock:
            _running.discard(job_id)


def _scan(job_id: str) -> dict[str, Any]:
    job = get_job(job_id)
    say = lambda msg, level="INFO": config.job_log("media", job_id, msg, level)  # noqa: E731
    started = config.utc_now()
    counts = {"processed": 0, "skipped": 0, "failed": 0, "unrecognised": 0}
    failures: list[str] = []
    for path, need_write, label in ((job["source"], job["mode"] == "move", "source"), (job["destination"], True, "destination")):
        check = mounts.ensure_available(path, need_write, log_fn=say)
        if not check["ok"]:
            failures.append(f"{label} mount not usable: {check['error']}")
    if not failures:
        opts = settings()
        staging = config.STAGING_DIR / job_id
        for dirpath, _dirs, files in os.walk(job["source"]):
            for name in sorted(files):
                src = Path(dirpath) / name
                if src.suffix.lower() not in EXTENSIONS or name.startswith(".") or name.endswith(TMP_SUFFIX):
                    continue
                if not _stable(src):
                    counts["skipped"] += 1
                    continue
                size = src.stat().st_size
                record = db.media_record(job_id, str(src))
                if record and record["status"] == "done" and record["size"] == size and job["mode"] == "copy":
                    counts["skipped"] += 1
                    continue
                info = identify(name, Path(dirpath).name, opts.get("use_llm", True))
                if not info:
                    counts["unrecognised"] += 1
                    db.save_media_record(job_id, str(src), size, "unrecognised", message="no series/movie match and no LLM result")
                    say(f"unrecognised: {name}", "WARNING")
                    continue
                target = target_path(info, job["destination"], src.suffix, opts)
                try:
                    if target.exists():
                        if target.stat().st_size == size:
                            if job["mode"] == "move":
                                src.unlink()
                            db.save_media_record(job_id, str(src), size, "done", str(target), "already present")
                            counts["skipped"] += 1
                            continue
                        target = target.with_name(f"{target.stem} ({uuid.uuid4().hex[:4]}){target.suffix}")
                    place_file(src, target, staging, move_source=job["mode"] == "move")
                    db.save_media_record(job_id, str(src), size, "done", str(target), info["source"])
                    counts["processed"] += 1
                    say(f"{name} -> {target} [{info['source']}]")
                except OSError as exc:
                    counts["failed"] += 1
                    failures.append(f"{name}: {exc}")
                    db.save_media_record(job_id, str(src), size, "failed", str(target), str(exc))
                    say(f"failed {name}: {exc}", "ERROR")
    current = get_job(job_id)
    if failures:
        notifier.notify_failure("media", current, "; ".join(failures)[:500])
    else:
        current["last_failure_notified"] = False
    status = "failed" if failures else "ok"
    _patch(job_id, last_failure_notified=current["last_failure_notified"],
           last_scan={"status": status, "started_at": started, "finished_at": config.utc_now(), "counts": counts,
                      "errors": failures[:10]})
    db.record_run("media", job_id, status, started, "; ".join(failures)[:500])
    say(f"scan finished: {counts}" + (f" errors={len(failures)}" if failures else ""))
    return {"ok": not failures, "counts": counts, "errors": failures}
