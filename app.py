import os
import re
import json
import shutil
import sqlite3
import hashlib
import hmac
import secrets
import asyncio
import logging
import subprocess
import tempfile
import zipfile
import csv
import io
from collections import deque
from datetime import datetime
from logging.handlers import RotatingFileHandler
from threading import Lock
from pathlib import Path
import shlex
import time
from urllib.parse import unquote, quote
from aiohttp import web
from telethon import TelegramClient, events, utils
try:
    import ldap3
    from ldap3.utils.conv import escape_filter_chars as ldap_escape_filter_chars
except Exception as e:
    ldap3 = None
    LDAP3_IMPORT_ERROR = str(e)
    def ldap_escape_filter_chars(value):
        return str(value or "")
import torch
import clip
from PIL import Image
from ultralytics import YOLO
import numpy as np
import faiss
# ================= CONFIG =================
APP_DIR = os.path.dirname(__file__)
CONFIG_FILE = os.path.join(APP_DIR, "config.json")
CONFIG_EXAMPLE_FILE = os.path.join(APP_DIR, "config.example.json")
FRONTEND_DIR = os.path.join(APP_DIR, "frontend")
INDEX_FILE = os.path.join(FRONTEND_DIR, "index.html")
DEFAULT_BASE_PATH = os.environ.get("TELESAVED_BASE_PATH", "/mnt/rda1/.vault/telesaved")
CONFIG_MISSING = False
CONFIG_ERROR = ""
def read_app_config():
    global CONFIG_MISSING, CONFIG_ERROR
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                loaded = json.load(f)
            loaded.setdefault("base_path", DEFAULT_BASE_PATH)
            return loaded
        except Exception as e:
            CONFIG_MISSING = True
            CONFIG_ERROR = str(e)
            return {"api_id": 0, "api_hash": "", "base_path": DEFAULT_BASE_PATH}
    env_api_id = os.environ.get("TELESAVED_API_ID", "").strip()
    env_api_hash = os.environ.get("TELESAVED_API_HASH", "").strip()
    env_base_path = os.environ.get("TELESAVED_BASE_PATH", DEFAULT_BASE_PATH).strip() or DEFAULT_BASE_PATH
    if env_api_id and env_api_hash:
        try:
            return {"api_id": int(env_api_id), "api_hash": env_api_hash, "base_path": env_base_path}
        except Exception as e:
            CONFIG_MISSING = True
            CONFIG_ERROR = f"Invalid TELESAVED_API_ID: {e}"
            return {"api_id": 0, "api_hash": "", "base_path": env_base_path}
    CONFIG_MISSING = True
    CONFIG_ERROR = "config.json is missing. Complete the first-run setup wizard or create /opt/telesaved/config.json."
    return {"api_id": 0, "api_hash": "", "base_path": env_base_path}
cfg = read_app_config()
try:
    api_id = int(cfg.get("api_id") or 0)
except Exception:
    api_id = 0
api_hash = str(cfg.get("api_hash") or "").strip()
BASE_PATH = str(cfg.get("base_path") or DEFAULT_BASE_PATH).strip() or DEFAULT_BASE_PATH
TELEGRAM_CONFIGURED = bool(api_id and api_hash and not CONFIG_MISSING)
STATE_FILE = os.path.join(BASE_PATH, "state.json")
DB_FILE = os.path.join(BASE_PATH, "telesaved.db")
STATE_BACKEND = "sqlite"
SETTINGS_FILE = os.path.join(BASE_PATH, "settings.json")
FEEDBACK_FILE = os.path.join(BASE_PATH, "feedback.json")
TAG_MAPPINGS_FILE = os.path.join(BASE_PATH, "tag_mappings.json")
FRAME_PATH = os.path.join(BASE_PATH, "frames")
PREVIEW_PATH = os.path.join(BASE_PATH, "previews")
THUMB_PATH = os.path.join(BASE_PATH, "thumbs")
SCENE_PATH = os.path.join(BASE_PATH, "scenes")
AUDIO_PATH = os.path.join(BASE_PATH, "audio")
TRASH_PATH = os.path.join(BASE_PATH, "trash")
BACKUP_PATH = os.path.join(BASE_PATH, "backups")
TRASH_INDEX_FILE = os.path.join(TRASH_PATH, "trash_index.json")
SESSION_FILE = os.path.join(APP_DIR, "telesaved_session")
LOG_FILE = os.path.join(BASE_PATH, "telesaved.log")
WHISPER_LOG_FILE = os.path.join(BASE_PATH, "whisper.log")
JOB_FILE = os.path.join(BASE_PATH, "jobs.json")
AUTH_FILE = os.path.join(BASE_PATH, "auth.json")
TELEGRAM_METADATA_PATH = os.path.join(BASE_PATH, "telegram_metadata")
PER_PAGE = 24
APP_VERSION = "4.5.8"
os.makedirs(BASE_PATH, exist_ok=True)
os.makedirs(FRAME_PATH, exist_ok=True)
os.makedirs(PREVIEW_PATH, exist_ok=True)
os.makedirs(THUMB_PATH, exist_ok=True)
os.makedirs(SCENE_PATH, exist_ok=True)
os.makedirs(AUDIO_PATH, exist_ok=True)
os.makedirs(TRASH_PATH, exist_ok=True)
os.makedirs(BACKUP_PATH, exist_ok=True)
os.makedirs(TELEGRAM_METADATA_PATH, exist_ok=True)
os.makedirs(FRONTEND_DIR, exist_ok=True)
# ================= LOGGING =================
logger = logging.getLogger("telesaved")
logger.setLevel(logging.INFO)
logger.handlers.clear()
formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3)
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)
logger.info("TeleSaved SPA backend starting up.")
# ================= HELPERS =================
def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.exception("Failed to load JSON from %s: %s", path, e)
            return default
    return default
def save_json(path, data):
    # Ensure the destination directory exists before writing the temporary file.
    # This prevents background jobs from failing when the base metadata directory
    # has not been created yet or was recreated after mount timing changes.
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, path)
def now_stamp():
    return datetime.now().isoformat(timespec="seconds")
def timestamp_for_filename():
    return datetime.now().strftime("%Y%m%d-%H%M%S")
def safe_id(value):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(value or ""))[:180]
def safe_join(base_path, *parts):
    base_abs = os.path.abspath(base_path)
    target = os.path.abspath(os.path.join(base_abs, *parts))
    if not target.startswith(base_abs):
        raise ValueError("Path traversal blocked")
    return target
def ensure_directory(path):
    os.makedirs(path, exist_ok=True)
    return path
def safe_rel_path(abs_path, base_path=None):
    if base_path is None:
        base_path = BASE_PATH
    return os.path.relpath(abs_path, base_path)
def safe_abs_path(rel_path, base_path=None):
    if base_path is None:
        base_path = BASE_PATH
    base_abs = os.path.abspath(base_path)
    abs_path = os.path.abspath(os.path.join(base_path, rel_path))
    if not abs_path.startswith(base_abs):
        raise ValueError("Path traversal blocked")
    return abs_path
def candidate_storage_roots():
    """Return all storage roots worth checking for existing indexed files.
    Older builds sometimes indexed paths relative to BASE_PATH, while later builds
    indexed relative to the configured download directory. If that setting changed
    between builds, file size lookups can incorrectly return 0 B. This root list
    lets the app find files created under either convention.
    """
    roots = []
    for root in (get_download_directory() if "get_download_directory" in globals() else BASE_PATH, BASE_PATH):
        if root:
            roots.append(root)
            parent = os.path.dirname(os.path.abspath(root))
            grandparent = os.path.dirname(parent)
            roots.extend([parent, grandparent])
    out = []
    seen = set()
    for root in roots:
        try:
            root_abs = os.path.abspath(root)
        except Exception:
            continue
        if root_abs and root_abs not in seen:
            out.append(root_abs)
            seen.add(root_abs)
    return out
def file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()
def short_name(path):
    return os.path.basename(path)
def human_date_from_path(path):
    parts = path.split(os.sep)
    if parts:
        maybe_date = parts[0]
        if len(maybe_date) == 10 and maybe_date.count("-") == 2:
            return maybe_date
    return "Unknown"
def normalize_score(value):
    return max(0.0, min(float(value), 1.5))
def pct(value, total):
    if not total:
        return 0
    try:
        return int((value / total) * 100)
    except Exception:
        return 0
def format_bytes(num):
    try:
        num = float(num)
    except Exception:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    idx = 0
    while num >= 1024 and idx < len(units) - 1:
        num /= 1024
        idx += 1
    return f"{num:.1f} {units[idx]}"
def get_storage_stats():
    """Return disk usage for the configured download directory."""
    try:
        path = get_download_directory() if "get_download_directory" in globals() else BASE_PATH
        ensure_directory(path)
        total, used, free = shutil.disk_usage(path)
        percent_free = (free / total) * 100 if total else 0.0
        percent_used = (used / total) * 100 if total else 0.0
        return {
            "total": int(total),
            "used": int(used),
            "free": int(free),
            "percent_free": round(percent_free, 2),
            "percent_used": round(percent_used, 2),
            "total_human": format_bytes(total),
            "used_human": format_bytes(used),
            "free_human": format_bytes(free),
        }
    except Exception as e:
        logger.exception("Failed to get storage stats: %s", e)
        return {
            "total": 0,
            "used": 0,
            "free": 0,
            "percent_free": 0.0,
            "percent_used": 0.0,
            "total_human": "0 B",
            "used_human": "0 B",
            "free_human": "0 B",
        }
def storage_is_low():
    return get_storage_stats().get("percent_free", 0.0) < DOWNLOAD_STOP_THRESHOLD
def set_download_mode(mode, reason=""):
    if mode not in {"running", "paused_manual", "paused_storage"}:
        raise ValueError(f"Invalid download mode: {mode}")
    download_control["mode"] = mode
    download_control["reason"] = reason
    download_control["updated"] = datetime.now().isoformat(timespec="seconds")
    logger.warning("Download mode changed: %s (%s)", mode, reason)
def refresh_storage_download_mode():
    """Auto-pause on low storage. Storage pauses stay paused until the user resumes."""
    if storage_is_low():
        if download_control.get("mode") != "paused_storage":
            set_download_mode("paused_storage", f"Storage below {DOWNLOAD_STOP_THRESHOLD}%")
        return
    if download_control.get("mode") == "paused_storage":
        download_control["reason"] = "Storage recovered; resume manually"
        download_control["updated"] = datetime.now().isoformat(timespec="seconds")
def parse_page(value):
    try:
        return max(1, int(value))
    except Exception:
        return 1
def paginate_items(items, page, per_page=PER_PAGE):
    total = len(items)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    start = (page - 1) * per_page
    end = start + per_page
    return items[start:end], page, total_pages, total
def preview_key(rel_path):
    return rel_path.replace(os.sep, "__")
def preview_rel_path_for_file(rel_path):
    return f"{preview_key(rel_path)}.gif"
def thumb_rel_path_for_file(rel_path):
    return f"{preview_key(rel_path)}.jpg"
def preview_abs_path_for_file(rel_path):
    return os.path.join(PREVIEW_PATH, preview_rel_path_for_file(rel_path))
def thumb_abs_path_for_file(rel_path):
    return os.path.join(THUMB_PATH, thumb_rel_path_for_file(rel_path))
def scene_rel_path_for_file(rel_path, scene_index):
    return f"{preview_key(rel_path)}__scene_{int(scene_index):03d}.jpg"
def scene_abs_path_for_file(rel_path, scene_index):
    return os.path.join(SCENE_PATH, scene_rel_path_for_file(rel_path, scene_index))
def scene_url_for_file(rel_path, scene_index):
    return f"/scenes/{quote(scene_rel_path_for_file(rel_path, scene_index))}"
def scene_thumbnail_paths_for_file(rel_path):
    paths = []
    prefix = f"{preview_key(rel_path)}__scene_"
    try:
        meta = metadata_store.get(rel_path, {}) if "metadata_store" in globals() else {}
        timeline = meta.get("scene_timeline") or {}
        for scene in timeline.get("scenes", []) if isinstance(timeline, dict) else []:
            thumb_name = scene.get("thumbnail_file") or ""
            if thumb_name:
                paths.append(os.path.join(SCENE_PATH, thumb_name))
    except Exception:
        pass
    try:
        if os.path.isdir(SCENE_PATH):
            for name in os.listdir(SCENE_PATH):
                if name.startswith(prefix) and name.lower().endswith((".jpg", ".jpeg", ".png")):
                    paths.append(os.path.join(SCENE_PATH, name))
    except Exception:
        pass
    deduped = []
    seen = set()
    for path in paths:
        if path not in seen:
            seen.add(path)
            deduped.append(path)
    return deduped
def delete_scene_thumbnails_for_file(rel_path):
    removed = 0
    for path in scene_thumbnail_paths_for_file(rel_path):
        try:
            if os.path.exists(path):
                os.remove(path)
                removed += 1
        except Exception:
            logger.warning("Could not delete scene thumbnail %s", path)
    return removed
def move_scene_thumbnails_for_file(rel_path, target_dir):
    moved = []
    ensure_directory(target_dir)
    for path in scene_thumbnail_paths_for_file(rel_path):
        try:
            if os.path.exists(path):
                dst = safe_join(target_dir, os.path.basename(path))
                move_if_exists(path, dst) if "move_if_exists" in globals() else shutil.move(path, dst)
                moved.append(os.path.relpath(dst, TRASH_PATH) if target_dir.startswith(TRASH_PATH) else dst)
        except Exception:
            logger.warning("Could not move scene thumbnail %s", path)
    return moved
def restore_scene_thumbnails_for_file(rel_path, trash_rels):
    restored = []
    for rel in trash_rels or []:
        try:
            src = safe_join(TRASH_PATH, rel)
            dst = os.path.join(SCENE_PATH, os.path.basename(rel))
            if os.path.exists(src):
                ensure_directory(os.path.dirname(dst))
                shutil.move(src, dst)
                restored.append(dst)
        except Exception:
            logger.warning("Could not restore scene thumbnail %s", rel)
    return restored
def frame_preview_urls_for_file(rel_path, limit=14):
    """Return lightweight frame-strip preview URLs for the detail player."""
    try:
        abs_path = find_existing_file(rel_path) if "find_existing_file" in globals() else None
        basename = os.path.basename(abs_path or rel_path)
        folder = safe_join(FRAME_PATH, basename)
        if not os.path.isdir(folder):
            return []
        frames = [f for f in sorted(os.listdir(folder)) if f.lower().endswith(".jpg")]
        out = []
        for idx, name in enumerate(frames[:max(0, int(limit))]):
            time_value = round(idx * 0.5, 2)
            out.append({
                "url": f"/frame-preview/{quote(basename)}/{quote(name)}",
                "name": name,
                "time": time_value,
                "label": format_duration(time_value),
            })
        return out
    except Exception as e:
        logger.warning("Could not build frame preview URLs for %s: %s", rel_path, e)
        return []
def get_video_duration_seconds(video_path):
    try:
        cmd = [
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        return float(result.stdout.strip())
    except Exception as e:
        logger.warning("Failed to get duration for %s: %s", video_path, e)
        return 0.0
def format_duration(seconds):
    try:
        seconds = int(round(float(seconds or 0)))
    except Exception:
        seconds = 0
    if seconds <= 0:
        return "—"
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"
def metadata_source_label(meta=None):
    meta = meta or {}
    source = str(meta.get("source") or meta.get("source_title") or "").strip()
    source_ref = str(meta.get("source_ref") or "").strip()
    if source:
        return source
    if source_ref:
        return source_display_name(source_ref) if "source_display_name" in globals() else source_ref
    # Legacy indexed files pre-date source metadata and were originally pulled from Saved Messages.
    return "Saved Messages"
def collect_file_metadata(rel_path, abs_path=None, ensure_duration=False):
    """Return display/sort metadata for an indexed video.
    Size and modified time are read live from disk. Duration/source are cached in state.json under metadata so the list view can sort without probing every
    file on every request. Missing duration is filled lazily for visible rows.
    """
    rel_path = str(rel_path or "")
    meta = metadata_store.setdefault(rel_path, {})
    if abs_path is None:
        abs_path = find_existing_file(rel_path) if "find_existing_file" in globals() else None
    exists = False
    file_size = 0
    modified = ""
    modified_ts = 0.0
    if abs_path and os.path.exists(abs_path):
        exists = True
        try:
            file_size = os.path.getsize(abs_path)
            modified_ts = os.path.getmtime(abs_path)
            modified = datetime.fromtimestamp(modified_ts).isoformat(timespec="seconds")
        except Exception:
            pass
        if ensure_duration and not meta.get("duration"):
            try:
                meta["duration"] = float(get_video_duration_seconds(abs_path))
                meta["duration_scanned_at"] = datetime.now().isoformat(timespec="seconds")
                # NOTE: save_state() deliberately omitted here.
                # Duration is written into the in-memory metadata_store dict above
                # and will be persisted by the next save_state() call triggered by
                # a real write operation (download, tag edit, audio analysis, etc.).
                # Calling save_state() here caused a full-library SQLite rewrite on
                # every sort request when any item lacked a cached duration.
            except Exception:
                pass
    duration = 0.0
    try:
        duration = float(meta.get("duration") or 0.0)
    except Exception:
        duration = 0.0
    source = metadata_source_label(meta)
    source_ref = str(meta.get("source_ref") or "").strip()
    return {
        "exists": exists,
        "size": int(file_size),
        "size_human": format_bytes(file_size),
        "modified": modified,
        "modified_ts": float(modified_ts or 0.0),
        "duration": duration,
        "duration_human": format_duration(duration),
        "source": source,
        "source_ref": source_ref,
    }
def sort_score_value(item):
    if item.get("final_score") is not None:
        return float(item.get("final_score") or 0.0)
    if item.get("semantic_score") is not None:
        return float(item.get("semantic_score") or 0.0)
    score_map = item.get("score_map") or {}
    if score_map:
        try:
            return max(float(v) for v in score_map.values())
        except Exception:
            return 0.0
    return 0.0
def sort_library_items(items, sort_by="path", sort_dir="asc"):
    allowed = {
        "path", "filename", "directory", "date", "size", "duration",
        "source", "tags", "score", "manual", "modified", "relevance"
    }
    sort_by = str(sort_by or "path").lower()
    if sort_by not in allowed:
        sort_by = "path"
    sort_dir = str(sort_dir or "asc").lower()
    reverse = sort_dir == "desc"
    def key(item):
        rel_path = item.get("path", "")
        meta = collect_file_metadata(rel_path, ensure_duration=False)
        if sort_by == "filename":
            return short_name(rel_path).lower()
        if sort_by == "directory":
            return os.path.dirname(rel_path).lower()
        if sort_by == "date":
            return human_date_from_path(rel_path)
        if sort_by == "size":
            return meta.get("size", 0)
        if sort_by == "duration":
            return meta.get("duration", 0.0)
        if sort_by == "source":
            return str(meta.get("source", "")).lower()
        if sort_by == "tags":
            return ",".join(item.get("tags") or []).lower()
        if sort_by == "score" or sort_by == "relevance":
            return sort_score_value(item)
        if sort_by == "manual":
            return 1 if item.get("manual_override") else 0
        if sort_by == "modified":
            return meta.get("modified_ts", 0.0)
        return rel_path.lower()
    return sorted(items, key=key, reverse=reverse), sort_by, ("desc" if reverse else "asc")
# ================= TAG CONFIG =================
SYSTEM_TAGS = [
    "Male",
    "Female",
    "Trans",
    "Gay",
    "Straight",
    "Bisexual",
    "Adults",
    "Clip",
    "Movie",
]
PROMPTS = {
    "Male": ["a man"],
    "Female": ["a woman"],
    "Trans": ["a transgender person"],
    "Gay": ["two men together", "intimate scene between men"],
    "Straight": ["a man and a woman together"],
    "Bisexual": ["multiple genders together"],
    "Adults": ["an adult person", "grown man", "grown woman"],
    "Clip": ["a short clip", "a casual video clip"],
    "Movie": ["a movie scene", "a cinematic scene"],
}
DEFAULT_TAG_WEIGHTS = {
    "Male": 1.05,
    "Female": 1.05,
    "Trans": 0.92,
    "Gay": 0.93,
    "Straight": 0.95,
    "Bisexual": 0.90,
    "Adults": 1.10,
    "Clip": 1.08,
    "Movie": 1.08,
}
DEFAULT_TAG_THRESHOLDS = {
    "Male": 0.24,
    "Female": 0.24,
    "Trans": 0.30,
    "Gay": 0.31,
    "Straight": 0.30,
    "Bisexual": 0.32,
    "Adults": 0.25,
    "Clip": 0.23,
    "Movie": 0.23,
}
DEFAULT_NUDENET_THRESHOLD = 0.5

# Seed act-tag vocabulary — added to custom_tags on first run if absent.
ACT_TAG_SEED = [
    "Oral", "Anal", "Vaginal", "Handjob", "Fingering",
    "Bondage", "BDSM", "CNC", "Watersports", "PnP",
    "Creampie", "Squirting", "Toys", "Strap-on",
    "Threesome", "Gangbang", "Orgy",
]

DEFAULT_SETTINGS = {
    "tag_weights": DEFAULT_TAG_WEIGHTS,
    "tag_thresholds": DEFAULT_TAG_THRESHOLDS,
    "auto_refresh": "10",
    "download_directory": BASE_PATH,
    "video_sources": ["me"],
    "source_profiles": {},
    "custom_tags": list(ACT_TAG_SEED),
    "tag_metadata": {},
    "smart_collections": [],
    "automation_rules": [],
    "nudenet_threshold": DEFAULT_NUDENET_THRESHOLD,
    "privacy": {
        "enabled": False,
        "pin_hash": "",
        "pin_salt": "",
        "session_timeout_minutes": 15,
        "privacy_blur": True,
        "hide_filenames": False,
        "require_unlock_for_delete": True,
    },
}
settings_data = load_json(SETTINGS_FILE, DEFAULT_SETTINGS)
settings_data.setdefault("tag_weights", {})
settings_data.setdefault("tag_thresholds", {})
settings_data.setdefault("auto_refresh", "10")
settings_data.setdefault("download_directory", BASE_PATH)
settings_data.setdefault("video_sources", ["me"])
settings_data.setdefault("source_profiles", {})
settings_data.setdefault("custom_tags", [])
settings_data.setdefault("tag_metadata", {})
settings_data.setdefault("smart_collections", [])
settings_data.setdefault("automation_rules", [])
settings_data.setdefault("nudenet_threshold", DEFAULT_NUDENET_THRESHOLD)
settings_data.setdefault("privacy", {})
# Seed act-tag vocabulary for existing installs upgrading to 4.5.0.
_existing_custom = {t.lower() for t in settings_data.get("custom_tags", [])}
for _seed_tag in ACT_TAG_SEED:
    if _seed_tag.lower() not in _existing_custom:
        settings_data["custom_tags"].append(_seed_tag)
        _existing_custom.add(_seed_tag.lower())
del _existing_custom, _seed_tag
settings_data["privacy"].setdefault("enabled", False)
settings_data["privacy"].setdefault("pin_hash", "")
settings_data["privacy"].setdefault("pin_salt", "")
settings_data["privacy"].setdefault("session_timeout_minutes", 15)
settings_data["privacy"].setdefault("privacy_blur", True)
settings_data["privacy"].setdefault("hide_filenames", False)
settings_data["privacy"].setdefault("require_unlock_for_delete", True)

# ================= TAG MAPPINGS =================
DEFAULT_TAG_MAPPINGS = {
    "scene_composition": {
        "duo_mm":      "Gay",
        "duo_ff":      "Lesbian",
        "duo_mf":      "Straight",
        "group_all_m": "Gay Group",
        "group_all_f": "Lesbian Group",
        "group_mixed": "Group",
        "solo_m":      "Solo Male",
        "solo_f":      "Solo Female",
    },
    "pov_type": {
        "pov": "POV",
    },
    "production_type": {
        "amateur": "Amateur",
        "professional": "Professional",
    },
    "setting": {
        "outdoor": "Outdoor",
        "public":  "Public",
        "indoor":  "Indoor",
    },
    "performer_presentations": {
        "trans": "Trans-inclusive",
    },
}

def _load_tag_mappings():
    """Load tag_mappings.json from BASE_PATH, creating it with defaults if absent."""
    try:
        if os.path.exists(TAG_MAPPINGS_FILE):
            with open(TAG_MAPPINGS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    # Seed the file with defaults
    try:
        with open(TAG_MAPPINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_TAG_MAPPINGS, f, indent=2, ensure_ascii=False)
    except Exception:
        pass
    return dict(DEFAULT_TAG_MAPPINGS)

def _save_tag_mappings(mappings):
    """Persist tag mappings dict to TAG_MAPPINGS_FILE."""
    with open(TAG_MAPPINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(mappings, f, indent=2, ensure_ascii=False)

tag_mappings = _load_tag_mappings()

def get_display_tags_for_signals(ai_signals):
    """Derive a list of human-readable display tag strings from an ai_signals dict.
    Uses the loaded tag_mappings; returns an empty list for None/missing signals.
    """
    if not isinstance(ai_signals, dict):
        return []
    result = []
    mappings = tag_mappings
    # scene_composition: single string value
    sc = ai_signals.get("scene_composition")
    if sc and isinstance(sc, str):
        label = mappings.get("scene_composition", {}).get(sc)
        if label:
            result.append(label)
    # pov_type
    pov = ai_signals.get("pov_type")
    if pov and isinstance(pov, str):
        label = mappings.get("pov_type", {}).get(pov)
        if label:
            result.append(label)
    # production_type
    pt = ai_signals.get("production_type")
    if pt and isinstance(pt, str):
        label = mappings.get("production_type", {}).get(pt)
        if label:
            result.append(label)
    # setting
    setting = ai_signals.get("setting")
    if setting and isinstance(setting, str):
        label = mappings.get("setting", {}).get(setting)
        if label:
            result.append(label)
    # performer_presentations: list of strings
    pp = ai_signals.get("performer_presentations")
    if isinstance(pp, list):
        pp_map = mappings.get("performer_presentations", {})
        for p in pp:
            label = pp_map.get(str(p))
            if label and label not in result:
                result.append(label)
    return result

# ================= LOCAL USERS / RBAC / SOURCE ACL =================
AUTH_SESSION_COOKIE = "telesaved_auth_token"
AUTH_SESSIONS = {}
# Login rate limiting: track failed attempts per normalised username.
# After _LOGIN_MAX_ATTEMPTS failures within _LOGIN_WINDOW_SECONDS the account
# is temporarily locked until the window expires (no persistent storage needed).
_LOGIN_ATTEMPTS: dict = {}   # username -> {"count": int, "window_start": float}
_LOGIN_MAX_ATTEMPTS = 10
_LOGIN_WINDOW_SECONDS = 300  # 5-minute sliding window

def _login_check_and_record_failure(username: str) -> bool:
    """Return True if the account is locked (too many recent failures).
    Record a new failure unconditionally (call after a failed attempt).
    """
    now = time.time()
    entry = _LOGIN_ATTEMPTS.get(username)
    if entry and (now - entry["window_start"]) >= _LOGIN_WINDOW_SECONDS:
        entry = None  # window expired — reset
    if entry is None:
        _LOGIN_ATTEMPTS[username] = {"count": 1, "window_start": now}
        return False
    entry["count"] += 1
    return entry["count"] > _LOGIN_MAX_ATTEMPTS

def _login_clear_failures(username: str) -> None:
    """Clear failure record on successful login."""
    _LOGIN_ATTEMPTS.pop(username, None)
AUTH_SESSION_SECONDS = 60 * 60 * 12
PERMISSION_CATALOG = [
    {"id": "*", "label": "Full system access"},
    {"id": "admin.view", "label": "Open Administration"},
    {"id": "library.view", "label": "View library"},
    {"id": "downloads.view", "label": "View downloads"},
    {"id": "content.manage", "label": "Add, rename, reprocess, and manage content"},
    {"id": "content.delete", "label": "Move content to Trash / restore content"},
    {"id": "tags.manage", "label": "Assign, disassign, create, and remove tags"},
    {"id": "sources.manage", "label": "Manage Telegram sources and source pipelines"},
    {"id": "source_acl.manage", "label": "Grant and revoke source access"},
    {"id": "jobs.manage", "label": "View and control jobs"},
    {"id": "security.manage", "label": "Manage users, groups, permissions, and local authentication"},
    {"id": "system.admin", "label": "Manage system settings, setup, logs, backups, import/export"},
]
DEFAULT_GROUPS = {
    "Admin": {
        "name": "Admin",
        "description": "Full Systems Access",
        "system": True,
        "permissions": ["*"],
    },
    "Manager": {
        "name": "Manager",
        "description": "Add/delete content, manage sources, assign/disassign tags, and grant source access",
        "system": True,
        "permissions": [
            "admin.view", "library.view", "downloads.view", "content.manage", "content.delete",
            "tags.manage", "sources.manage", "source_acl.manage", "jobs.manage"
        ],
    },
    "Users": {
        "name": "Users",
        "description": "View Only Access, No Access to Administration",
        "system": True,
        "permissions": ["library.view", "downloads.view"],
    },
}
def ldap_default_settings():
    return {
        "enabled": False,
        "server_uri": "ldap://localhost:389",
        "start_tls": False,
        "bind_dn": "",
        "bind_password": "",
        "user_search_base": "",
        "user_filter": "(uid={username})",
        "username_attribute": "uid",
        "display_name_attribute": "cn",
        "email_attribute": "mail",
        "memberof_attribute": "memberOf",
        "group_search_base": "",
        "group_filter": "(|(member={user_dn})(uniqueMember={user_dn})(memberUid={username}))",
        "group_name_attribute": "cn",
        "default_groups": ["Users"],
        "group_mappings": {},
        "auto_create_users": True,
        "sync_groups_on_login": True,
        "timeout_seconds": 8,
    }
def identity_default_data():
    return {
        "local": {"enabled": True, "emergency_admin": True},
        "ldap": ldap_default_settings(),
        "oidc": {"enabled": False},
    }
def ensure_identity_defaults(data):
    identity = data.setdefault("identity", {})
    identity.setdefault("local", {"enabled": True, "emergency_admin": True})
    ldap_settings = identity.setdefault("ldap", {})
    defaults = ldap_default_settings()
    for key, value in defaults.items():
        ldap_settings.setdefault(key, json.loads(json.dumps(value)))
    identity.setdefault("oidc", {"enabled": False})
    return data
def auth_default_data():
    return {
        "version": 1,
        "enabled": True,
        "created_at": now_stamp() if "now_stamp" in globals() else datetime.now().isoformat(timespec="seconds"),
        "users": {},
        "groups": json.loads(json.dumps(DEFAULT_GROUPS)),
        "source_access": {
            "users": {},
            "groups": {
                "Admin": ["*"],
                "Manager": ["*"],
                "Users": [],
            },
        },
        "settings": {
            "session_timeout_minutes": 720,
            "allow_local_login": True,
        },
        "identity": identity_default_data(),
    }
def normalize_group_name(value):
    name = re.sub(r"\s+", " ", str(value or "").strip())
    name = "".join(ch for ch in name if ord(ch) >= 32 and ch not in "<>:\\/*?\"'").strip()
    return name[:64]
def normalize_username(value):
    username = str(value or "").strip().lower()
    username = re.sub(r"[^a-z0-9_.@-]+", "", username)
    return username[:80]
def normalize_permission_list(values):
    valid = {item["id"] for item in PERMISSION_CATALOG}
    out = []
    for value in values or []:
        perm = str(value or "").strip()
        if perm in valid and perm not in out:
            out.append(perm)
    return out or ["library.view"]
def normalize_source_grants(values):
    out = []
    for value in values or []:
        raw = str(value or "").strip()
        if not raw:
            continue
        ref = "*" if raw == "*" else normalize_source_ref(raw)
        if ref and ref not in out:
            out.append(ref)
    return out
def load_auth_data():
    data = load_json(AUTH_FILE, auth_default_data())
    if not isinstance(data, dict):
        data = auth_default_data()
    data.setdefault("version", 1)
    data.setdefault("enabled", True)
    data.setdefault("users", {})
    data.setdefault("groups", {})
    data.setdefault("source_access", {})
    data.setdefault("settings", {})
    data["source_access"].setdefault("users", {})
    data["source_access"].setdefault("groups", {})
    for name, group in DEFAULT_GROUPS.items():
        existing = data["groups"].get(name) or {}
        merged = {**group, **existing}
        if name in {"Admin", "Manager", "Users"}:
            merged["system"] = True
        data["groups"][name] = merged
    data["source_access"]["groups"].setdefault("Admin", ["*"])
    data["source_access"]["groups"].setdefault("Manager", ["*"])
    data["source_access"]["groups"].setdefault("Users", [])
    ensure_identity_defaults(data)
    return data
auth_data = load_auth_data()
def save_auth_data():
    save_json(AUTH_FILE, auth_data)
def auth_initialized():
    return bool((auth_data.get("users") or {}))
def hash_password(password, salt=None):
    password = str(password or "")
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 240_000)
    return salt, digest.hex()
def verify_password(password, user):
    if not user or not user.get("password_hash") or not user.get("password_salt"):
        return False
    try:
        _, digest = hash_password(password, user.get("password_salt"))
        return hmac.compare_digest(digest, str(user.get("password_hash") or ""))
    except Exception:
        return False
def sanitized_user(user_id, user=None):
    user = user or (auth_data.get("users") or {}).get(user_id) or {}
    return {
        "id": user_id,
        "username": user.get("username", user_id),
        "display_name": user.get("display_name") or user.get("username") or user_id,
        "email": user.get("email", ""),
        "groups": list(user.get("groups") or []),
        "disabled": bool(user.get("disabled", False)),
        "created_at": user.get("created_at", ""),
        "updated_at": user.get("updated_at", ""),
        "last_login": user.get("last_login", ""),
        "auth_backend": user.get("auth_backend", "local"),
    }
def group_permissions(group_names):
    perms = set()
    for group_name in group_names or []:
        group = (auth_data.get("groups") or {}).get(group_name) or {}
        for perm in group.get("permissions") or []:
            perms.add(str(perm))
    if "*" in perms:
        return ["*"] + sorted(p for p in perms if p != "*")
    return sorted(perms)
def user_permissions(user):
    return group_permissions((user or {}).get("groups") or [])
def user_has_permission(user, permission):
    if not user:
        return False
    perms = set(user.get("permissions") or user_permissions(user))
    return "*" in perms or permission in perms
def auth_cleanup_sessions():
    now = time.time()
    expired = [token for token, session in AUTH_SESSIONS.items() if float(session.get("expires", 0)) <= now]
    for token in expired:
        AUTH_SESSIONS.pop(token, None)
def create_auth_session(user_id):
    auth_cleanup_sessions()
    timeout = int((auth_data.get("settings") or {}).get("session_timeout_minutes") or 720) * 60
    timeout = max(300, min(timeout, 60 * 60 * 24 * 14))
    token = secrets.token_urlsafe(36)
    AUTH_SESSIONS[token] = {"user_id": user_id, "created": time.time(), "expires": time.time() + timeout}
    return token, timeout
def auth_token_from_request(request):
    return request.cookies.get(AUTH_SESSION_COOKIE) or request.headers.get("X-Telesaved-Auth-Token") or ""
def auth_current_user(request):
    token = auth_token_from_request(request)
    if not token:
        return None
    auth_cleanup_sessions()
    session = AUTH_SESSIONS.get(token)
    if not session:
        return None
    user_id = session.get("user_id")
    user = (auth_data.get("users") or {}).get(user_id)
    if not user or user.get("disabled"):
        AUTH_SESSIONS.pop(token, None)
        return None
    timeout = int((auth_data.get("settings") or {}).get("session_timeout_minutes") or 720) * 60
    session["expires"] = time.time() + max(300, min(timeout, 60 * 60 * 24 * 14))
    enriched = dict(user)
    enriched["id"] = user_id
    enriched["permissions"] = user_permissions(user)
    return enriched
def auth_status_payload(request=None):
    user = auth_current_user(request) if request is not None else None
    payload = {
        "ok": True,
        "version": APP_VERSION,
        "auth_enabled": True,
        "bootstrap_required": not auth_initialized(),
        "authenticated": bool(user),
        "permissions_catalog": PERMISSION_CATALOG,
        "identity_backends": {"local": True, "ldap": ldap_is_enabled(), "oidc": False, "sso": False},
        "ldap_status": ldap_status_payload(),
        "phase": "local-users-groups-source-acl-ldap",
    }
    if user:
        payload["user"] = sanitized_user(user.get("id"), user)
        payload["permissions"] = user.get("permissions") or []
        payload["source_grants"] = accessible_source_refs_for_user(user)
    else:
        payload["permissions"] = []
        payload["source_grants"] = []
    return payload
def make_auth_response(payload, token=None, max_age=None, clear=False):
    response = web.json_response(payload)
    if clear:
        response.del_cookie(AUTH_SESSION_COOKIE, path="/")
    elif token:
        response.set_cookie(
            AUTH_SESSION_COOKIE,
            token,
            max_age=max_age or AUTH_SESSION_SECONDS,
            httponly=True,
            samesite="Lax",
            secure=True,
            path="/",
        )
    return response
def create_local_user(username, password, display_name="", email="", groups=None, disabled=False):
    username = normalize_username(username)
    if not username:
        raise ValueError("Username is required")
    if len(str(password or "")) < 8:
        raise ValueError("Password must be at least 8 characters")
    users = auth_data.setdefault("users", {})
    if username in users:
        raise ValueError("Username already exists")
    clean_groups = [g for g in (groups or ["Users"]) if g in auth_data.get("groups", {})]
    if not clean_groups:
        clean_groups = ["Users"]
    salt, digest = hash_password(password)
    users[username] = {
        "username": username,
        "display_name": str(display_name or username).strip()[:120],
        "email": str(email or "").strip()[:160],
        "groups": clean_groups,
        "disabled": bool(disabled),
        "password_salt": salt,
        "password_hash": digest,
        "created_at": now_stamp(),
        "updated_at": now_stamp(),
        "auth_backend": "local",
    }
    auth_data.setdefault("source_access", {}).setdefault("users", {}).setdefault(username, [])
    save_auth_data()
    return sanitized_user(username, users[username])
# ================= LDAP IDENTITY BACKEND =================
def ldap_settings_current():
    ensure_identity_defaults(auth_data)
    return auth_data.setdefault("identity", {}).setdefault("ldap", ldap_default_settings())
def ldap_is_enabled(settings=None):
    settings = settings or ldap_settings_current()
    return bool(settings.get("enabled"))
def ldap_status_payload(settings=None):
    settings = settings or ldap_settings_current()
    configured = bool(str(settings.get("server_uri") or "").strip() and str(settings.get("user_search_base") or "").strip())
    return {
        "enabled": bool(settings.get("enabled")),
        "configured": configured,
        "ldap3_available": ldap3 is not None,
        "ldap3_error": "" if ldap3 is not None else LDAP3_IMPORT_ERROR,
        "server_uri": str(settings.get("server_uri") or ""),
        "start_tls": bool(settings.get("start_tls")),
        "user_search_base": str(settings.get("user_search_base") or ""),
        "group_search_base": str(settings.get("group_search_base") or ""),
    }
def ldap_sanitized_settings(settings=None):
    settings = settings or ldap_settings_current()
    clean = json.loads(json.dumps(settings))
    clean["bind_password"] = ""
    clean["bind_password_set"] = bool(settings.get("bind_password"))
    clean["ldap3_available"] = ldap3 is not None
    clean["ldap3_error"] = "" if ldap3 is not None else LDAP3_IMPORT_ERROR
    return clean
def ldap_normalize_group_mapping(value):
    out = {}
    groups = auth_data.get("groups") or {}
    if not isinstance(value, dict):
        return out
    for ldap_group, targets in value.items():
        key = str(ldap_group or "").strip()
        if not key:
            continue
        if isinstance(targets, str):
            raw_targets = [item.strip() for item in targets.split(",")]
        else:
            raw_targets = [str(item or "").strip() for item in (targets or [])]
        clean_targets = [name for name in raw_targets if name in groups]
        if clean_targets:
            out[key] = clean_targets
    return out
def ldap_update_settings(incoming):
    settings = ldap_settings_current()
    if not isinstance(incoming, dict):
        incoming = {}
    bool_fields = {"enabled", "start_tls", "auto_create_users", "sync_groups_on_login"}
    str_fields = {
        "server_uri", "bind_dn", "user_search_base", "user_filter", "username_attribute",
        "display_name_attribute", "email_attribute", "memberof_attribute", "group_search_base",
        "group_filter", "group_name_attribute"
    }
    for field in bool_fields:
        if field in incoming:
            settings[field] = bool(incoming.get(field))
    for field in str_fields:
        if field in incoming:
            settings[field] = str(incoming.get(field) or "").strip()
    if "timeout_seconds" in incoming:
        try:
            settings["timeout_seconds"] = max(2, min(60, int(incoming.get("timeout_seconds") or 8)))
        except Exception:
            settings["timeout_seconds"] = 8
    if "bind_password" in incoming:
        value = str(incoming.get("bind_password") or "")
        if value and value != "__KEEP__":
            settings["bind_password"] = value
        elif not value and incoming.get("clear_bind_password"):
            settings["bind_password"] = ""
    if "default_groups" in incoming:
        groups = auth_data.get("groups") or {}
        selected = [str(g or "").strip() for g in (incoming.get("default_groups") or [])]
        settings["default_groups"] = [g for g in selected if g in groups] or ["Users"]
    if "group_mappings" in incoming:
        settings["group_mappings"] = ldap_normalize_group_mapping(incoming.get("group_mappings") or {})
    save_auth_data()
    return settings
def ldap_entry_attr(entry, attr, default=""):
    attr = str(attr or "").strip()
    if not entry or not attr:
        return default
    try:
        if attr not in entry:
            return default
        value = entry[attr].value
        if isinstance(value, (list, tuple)):
            return str(value[0]) if value else default
        return str(value or default)
    except Exception:
        return default
def ldap_entry_values(entry, attr):
    attr = str(attr or "").strip()
    if not entry or not attr:
        return []
    try:
        if attr not in entry:
            return []
        value = entry[attr].value
        if isinstance(value, (list, tuple, set)):
            return [str(v) for v in value if str(v or "").strip()]
        if value:
            return [str(value)]
    except Exception:
        pass
    return []
def ldap_cn_from_dn(value):
    text = str(value or "")
    match = re.search(r"(?:^|,)\s*cn=([^,]+)", text, re.I)
    if match:
        return match.group(1).replace("\\,", ",").strip()
    return text.strip()
def ldap_connection(settings, user=None, password=None):
    if ldap3 is None:
        raise RuntimeError(f"ldap3 is not installed: {LDAP3_IMPORT_ERROR}")
    server_uri = str(settings.get("server_uri") or "").strip()
    if not server_uri:
        raise RuntimeError("LDAP server URI is required")
    timeout = max(2, min(60, int(settings.get("timeout_seconds") or 8)))
    server = ldap3.Server(server_uri, get_info=ldap3.NONE, connect_timeout=timeout)
    conn = ldap3.Connection(server, user=user or None, password=password or None, auto_bind=False,
    receive_timeout=timeout)
    if settings.get("start_tls"):
        if not conn.open():
            raise RuntimeError(f"LDAP connection failed: {conn.result}")
        if not conn.start_tls():
            raise RuntimeError(f"LDAP StartTLS failed: {conn.result}")
    if not conn.bind():
        raise RuntimeError(f"LDAP bind failed: {conn.result}")
    return conn
def ldap_service_connection(settings):
    return ldap_connection(settings, settings.get("bind_dn") or None, settings.get("bind_password") or None)
def ldap_user_search_filter(settings, username):
    template = str(settings.get("user_filter") or "(uid={username})")
    safe_username = ldap_escape_filter_chars(str(username or ""))
    return template.replace("{username}", safe_username)
def ldap_group_search_filter(settings, username, user_dn):
    template = str(settings.get("group_filter") or "(|(member={user_dn})(uniqueMember={user_dn})(memberUid={username}))")
    return (template
        .replace("{username}", ldap_escape_filter_chars(str(username or "")))
        .replace("{user_dn}", ldap_escape_filter_chars(str(user_dn or ""))))
def ldap_find_user_entry(conn, settings, username):
    search_base = str(settings.get("user_search_base") or "").strip()
    if not search_base:
        raise RuntimeError("LDAP user search base is required")
    attrs = []
    for attr in [
        settings.get("username_attribute"), settings.get("display_name_attribute"),
        settings.get("email_attribute"),
        settings.get("memberof_attribute"), "dn", "cn", "uid", "mail", "memberOf"
    ]:
        attr = str(attr or "").strip()
        if attr and attr not in attrs and attr.lower() != "dn":
            attrs.append(attr)
    search_filter = ldap_user_search_filter(settings, username)
    ok = conn.search(search_base, search_filter, search_scope=ldap3.SUBTREE, attributes=attrs or
    ldap3.ALL_ATTRIBUTES, size_limit=2)
    if not ok or not conn.entries:
        raise RuntimeError("LDAP user was not found")
    if len(conn.entries) > 1:
        logger.warning("LDAP user search for %s returned more than one entry; using first result", username)
    return conn.entries[0]
def ldap_group_names_for_user(conn, settings, username, user_dn, user_entry):
    names = []
    dns = []
    for value in ldap_entry_values(user_entry, settings.get("memberof_attribute") or "memberOf"):
        if value not in dns:
            dns.append(value)
        cn = ldap_cn_from_dn(value)
        if cn and cn not in names:
            names.append(cn)
    search_base = str(settings.get("group_search_base") or "").strip()
    if search_base:
        group_attr = str(settings.get("group_name_attribute") or "cn").strip() or "cn"
        group_filter = ldap_group_search_filter(settings, username, user_dn)
        try:
            if conn.search(search_base, group_filter, search_scope=ldap3.SUBTREE, attributes=[group_attr,
            "cn"], size_limit=500):
                for entry in conn.entries:
                    entry_dn = getattr(entry, "entry_dn", "")
                    if entry_dn and entry_dn not in dns:
                        dns.append(entry_dn)
                    value = ldap_entry_attr(entry, group_attr) or ldap_entry_attr(entry, "cn") or ldap_cn_from_dn(entry_dn)
                    if value and value not in names:
                        names.append(value)
        except Exception as e:
            logger.warning("LDAP group search failed for %s: %s", username, e)
    return names, dns
def ldap_map_groups(ldap_names, ldap_dns, settings):
    mapped = []
    lookup_values = list(ldap_names or []) + list(ldap_dns or [])
    lookup_lower = {str(v).strip().lower(): str(v).strip() for v in lookup_values if str(v or "").strip()}
    mappings = settings.get("group_mappings") or {}
    for ldap_key, telesaved_groups in mappings.items():
        key = str(ldap_key or "").strip()
        if not key:
            continue
        if key.lower() in lookup_lower:
            for group_name in telesaved_groups or []:
                if group_name in (auth_data.get("groups") or {}) and group_name not in mapped:
                    mapped.append(group_name)
    for group_name in settings.get("default_groups") or ["Users"]:
        if group_name in (auth_data.get("groups") or {}) and group_name not in mapped:
            mapped.append(group_name)
    return mapped or ["Users"]
def sync_ldap_user(username, user_info, groups):
    username = normalize_username(username)
    users = auth_data.setdefault("users", {})
    existing = users.get(username)
    if existing and existing.get("auth_backend", "local") == "local" and existing.get("password_hash"):
        raise RuntimeError("A local account already exists with this username; rename the LDAP user or local user before enabling LDAP login")
    now = now_stamp()
    record = existing or {"created_at": now}
    record.update({
        "username": username,
        "display_name": str(user_info.get("display_name") or username).strip()[:120],
        "email": str(user_info.get("email") or "").strip()[:160],
        "groups": [g for g in groups if g in (auth_data.get("groups") or {})] or ["Users"],
        "disabled": False,
        "auth_backend": "ldap",
        "ldap_dn": str(user_info.get("dn") or ""),
        "ldap_groups": list(user_info.get("ldap_groups") or []),
        "updated_at": now,
    })
    record.pop("password_salt", None)
    record.pop("password_hash", None)
    users[username] = record
    auth_data.setdefault("source_access", {}).setdefault("users", {}).setdefault(username, [])
    save_auth_data()
    return sanitized_user(username, record)
def ldap_authenticate(username, password):
    settings = ldap_settings_current()
    if not ldap_is_enabled(settings):
        raise RuntimeError("LDAP is disabled")
    if not str(password or ""):
        raise RuntimeError("Password is required")
    service = None
    try:
        service = ldap_service_connection(settings)
        entry = ldap_find_user_entry(service, settings, username)
        user_dn = getattr(entry, "entry_dn", "")
        if not user_dn:
            raise RuntimeError("LDAP user DN could not be determined")
        # Verify the supplied user password by binding as the user.
        user_conn = None
        try:
            user_conn = ldap_connection(settings, user_dn, password)
        finally:
            try:
                if user_conn:
                    user_conn.unbind()
            except Exception:
                pass
        ldap_names, ldap_dns = ldap_group_names_for_user(service, settings, username, user_dn, entry)
        mapped_groups = ldap_map_groups(ldap_names, ldap_dns, settings)
        existing_user = (auth_data.get("users") or {}).get(normalize_username(username))
        if not existing_user and not bool(settings.get("auto_create_users", True)):
            raise RuntimeError("LDAP login succeeded, but auto-create users is disabled and this user has not been synced")
        if existing_user and existing_user.get("auth_backend") == "ldap" and not bool(settings.get("sync_groups_on_login", True)):
            mapped_groups = existing_user.get("groups") or mapped_groups
        user_info = {
            "dn": user_dn,
            "display_name": ldap_entry_attr(entry, settings.get("display_name_attribute") or "cn") or username,
            "email": ldap_entry_attr(entry, settings.get("email_attribute") or "mail"),
            "ldap_groups": ldap_names,
            "ldap_group_dns": ldap_dns,
        }
        user = sync_ldap_user(username, user_info, mapped_groups)
        return {"ok": True, "user": user, "ldap_groups": ldap_names, "ldap_group_dns": ldap_dns,
        "mapped_groups": mapped_groups}
    finally:
        try:
            if service:
                service.unbind()
        except Exception:
            pass
def ldap_test(settings=None, username="", password=""):
    settings = settings or ldap_settings_current()
    started = time.time()
    result = {"ok": False, "elapsed_seconds": 0, "service_bind": False, "user_search": False, "user_bind":
    False, "ldap_groups": [], "mapped_groups": []}
    service = None
    try:
        service = ldap_service_connection(settings)
        result["service_bind"] = True
        if username:
            entry = ldap_find_user_entry(service, settings, username)
            result["user_search"] = True
            result["user_dn"] = getattr(entry, "entry_dn", "")
            if password:
                user_conn = None
                try:
                    user_conn = ldap_connection(settings, result["user_dn"], password)
                    result["user_bind"] = True
                finally:
                    try:
                        if user_conn:
                            user_conn.unbind()
                    except Exception:
                        pass
            ldap_names, ldap_dns = ldap_group_names_for_user(service, settings, username, result["user_dn"],
            entry)
            result["ldap_groups"] = ldap_names
            result["ldap_group_dns"] = ldap_dns
            result["mapped_groups"] = ldap_map_groups(ldap_names, ldap_dns, settings)
        result["ok"] = True
        return result
    except Exception as e:
        result["error"] = str(e)
        return result
    finally:
        result["elapsed_seconds"] = round(time.time() - started, 2)
        try:
            if service:
                service.unbind()
        except Exception:
            pass
def accessible_source_refs_for_user(user):
    if not user:
        return []
    grants = []
    source_access = auth_data.get("source_access") or {}
    user_grants = (source_access.get("users") or {}).get(user.get("id") or user.get("username"), [])
    grants.extend(user_grants or [])
    for group_name in user.get("groups") or []:
        grants.extend((source_access.get("groups") or {}).get(group_name, []) or [])
    grants = normalize_source_grants(grants)
    if "*" in grants or user_has_permission(user, "sources.manage"):
        return ["*"]
    return grants
def source_ref_for_path(rel_path):
    meta = metadata_store.get(str(rel_path or ""), {}) if "metadata_store" in globals() else {}
    ref = str((meta or {}).get("source_ref") or "").strip()
    if not ref:
        # Legacy files without metadata came from Saved Messages in early builds.
        ref = "me"
    return normalize_source_ref(ref) or ref or "me"
def user_can_access_source(user, source_ref):
    if not auth_initialized():
        return True
    if not user:
        return False
    if user_has_permission(user, "*") or user_has_permission(user, "system.admin"):
        return True
    grants = accessible_source_refs_for_user(user)
    if "*" in grants:
        return True
    ref = normalize_source_ref(source_ref) or str(source_ref or "")
    return ref in grants
def user_can_access_path(user, rel_path):
    return user_can_access_source(user, source_ref_for_path(rel_path))
def rel_path_from_derived_name(name):
    base = os.path.basename(unquote(str(name or "")))
    base = re.sub(r"__scene_\d+\.jpg$", "", base)
    if base.endswith(".gif") or base.endswith(".jpg"):
        base = base.rsplit(".", 1)[0]
    if not base:
        return ""
    return base.replace("__", os.sep)
def source_options_payload():
    refs = {"me"}
    try:
        refs.update(get_video_sources())
    except Exception:
        pass
    try:
        for path in tags.keys():
            refs.add(source_ref_for_path(path))
    except Exception:
        pass
    profiles = get_source_profiles() if "get_source_profiles" in globals() else {}
    rows = []
    for ref in sorted(normalize_source_ref(r) or r for r in refs if r):
        profile = profiles.get(ref) or {}
        title = profile.get("title") or (source_display_name(ref) if "source_display_name" in globals() else
        ref)
        rows.append({"ref": ref, "title": title})
    return rows
def require_permission_response(request, permission):
    user = auth_current_user(request)
    if not user:
        return web.json_response({"ok": False, "auth_required": True, "error": "Login required"}, status=401)
    if permission and not user_has_permission(user, permission):
        return web.json_response({"ok": False, "forbidden": True, "error": f"Permission required:{permission}"}, status=403)
    return None
def require_source_access_response(request, rel_path):
    user = auth_current_user(request)
    if not user_can_access_path(user, rel_path):
        return web.json_response({"ok": False, "forbidden": True, "error": "You do not have access to this source"}, status=403)
    return None
def required_permission_for_request(request):
    path = request.path or "/"
    method = request.method.upper()
    if path.startswith("/api/auth/"):
        return None
    if path == "/api/setup/save":
        return "system.admin"
    if path.startswith("/api/admin"):
        return "system.admin"
    if path.startswith("/api/jobs"):
        return "jobs.manage"
    if path.startswith("/api/downloads") and method != "GET":
        return "jobs.manage"
    if path.startswith("/api/sources"):
        return "sources.manage"
    if path.startswith("/api/source-intelligence") or path.startswith("/api/source-pipelines") or path.startswith("/api/telegram"):
        return "sources.manage"
    if path.startswith("/api/privacy"):
        return "system.admin" if path != "/api/privacy/status" else "library.view"
    if path.startswith("/api/collections"):
        return "content.manage" if method != "GET" else "admin.view"
    if path.startswith("/api/personal") or path.startswith("/api/watch"):
        return "admin.view" if method == "GET" else "content.manage"
    if path.startswith("/api/tags/suggest") and method == "POST":
        return "tags.manage"
    if path.startswith("/api/tags/suggestions") or path.startswith("/api/tags/migrate-labels") or path.startswith("/api/tags/labels"):
        return "system.admin"
    if path.startswith("/api/tags") and method != "GET":
        return "tags.manage"
    if path.startswith("/api/rules"):
        return "content.manage"
    if path.startswith("/api/review"):
        return "tags.manage" if method != "GET" else "admin.view"
    if path.startswith("/api/audio"):
        return "admin.view"
    if path.startswith("/api/duplicates") or path.startswith("/api/cleanup") or path.startswith("/api/smart-cleanup"):
        return "content.delete" if method != "GET" else "admin.view"
    if path.startswith("/api/trash") or path.startswith("/api/delete"):
        return "content.delete"
    if path.startswith("/api/bulk/tags"):
        return "tags.manage"
    if path.startswith("/api/bulk"):
        return "content.manage"
    if path.startswith("/api/item") and method != "GET":
        if "/tags" in path:
            return "tags.manage"
        if method == "DELETE" or "/delete" in path:
            return "content.delete"
        return "content.manage"
    if path.startswith("/api/export") or path.startswith("/api/import"):
        return "system.admin"
    return "library.view"
@web.middleware
async def auth_middleware(request, handler):
    path = request.path or "/"
    public_paths = {"/", "/favicon.ico", "/api/health", "/api/setup/status", "/api/setup/validate-telegram", "/api/auth/status", "/api/auth/login", "/api/auth/bootstrap"}
    if path in public_paths:
        return await handler(request)
    if not auth_initialized():
        if path.startswith("/api/"):
            return web.json_response({"ok": False, "bootstrap_required": True, "error": "Create the first Admin account to continue"}, status=401)
        return await handler(request)
    user = auth_current_user(request)
    if not user:
        if path.startswith("/api/") or path.startswith("/files/") or path.startswith("/thumbs/") or path.startswith("/previews/") or path.startswith("/scenes/"):
            return web.json_response({"ok": False, "auth_required": True, "error": "Login required"},
            status=401)
        return await handler(request)
    required = required_permission_for_request(request) if path.startswith("/api/") else "library.view"
    if required and not user_has_permission(user, required):
        return web.json_response({"ok": False, "forbidden": True, "error": f"Permission required:{required}"}, status=403)
    return await handler(request)
def normalize_tag_name(value):
    tag = str(value or "").strip()
    tag = re.sub(r"\s+", " ", tag)
    tag = tag.strip(" ,;|/")
    if not tag:
        return ""
    # Keep display case, but remove problematic path/control characters.
    tag = "".join(ch for ch in tag if ord(ch) >= 32 and ord(ch) != 127 and ch not in "<>:\\/*?\"\'").strip()
    return tag[:48]
def tag_key(value):
    return normalize_tag_name(value).lower()
def get_custom_tags():
    cleaned = []
    seen = {tag.lower() for tag in SYSTEM_TAGS}
    for item in settings_data.get("custom_tags", []):
        tag = normalize_tag_name(item)
        if not tag:
            continue
        key = tag.lower()
        if key in seen:
            continue
        cleaned.append(tag)
        seen.add(key)
    return cleaned
def get_all_tags():
    return list(SYSTEM_TAGS) + get_custom_tags()
PREDEFINED_TAGS = get_all_tags()
def refresh_tag_globals():
    global PREDEFINED_TAGS, TAG_WEIGHTS, TAG_THRESHOLDS
    PREDEFINED_TAGS = get_all_tags()
    settings_data["custom_tags"] = get_custom_tags()
    metadata = settings_data.setdefault("tag_metadata", {})
    for tag in PREDEFINED_TAGS:
        settings_data["tag_weights"].setdefault(tag, DEFAULT_TAG_WEIGHTS.get(tag, 1.0))
        settings_data["tag_thresholds"].setdefault(tag, DEFAULT_TAG_THRESHOLDS.get(tag, 0.99))
        metadata.setdefault(tag, {
            "label": tag,
            "group": "System" if tag in SYSTEM_TAGS else "Custom",
            "color": "",
            "locked": False,
            "hidden": False,
        })
        metadata[tag].setdefault("label", tag)
        metadata[tag].setdefault("group", "System" if tag in SYSTEM_TAGS else "Custom")
        metadata[tag].setdefault("color", "")
        metadata[tag].setdefault("locked", False)
        metadata[tag].setdefault("hidden", False)
    TAG_WEIGHTS = settings_data["tag_weights"]
    TAG_THRESHOLDS = settings_data["tag_thresholds"]
def sanitize_tags(selected):
    if not isinstance(selected, list):
        selected = [selected]
    wanted = {tag_key(tag) for tag in selected if normalize_tag_name(tag)}
    return [tag for tag in PREDEFINED_TAGS if tag.lower() in wanted]
refresh_tag_globals()
# ================= PRIVACY / PIN LOCK =================
PRIVACY_SESSION_TOKENS = {}
PRIVACY_MIN_PIN_LENGTH = 4
def get_privacy_settings():
    raw = settings_data.setdefault("privacy", {})
    raw.setdefault("enabled", False)
    raw.setdefault("pin_hash", "")
    raw.setdefault("pin_salt", "")
    raw.setdefault("session_timeout_minutes", 15)
    raw.setdefault("privacy_blur", True)
    raw.setdefault("hide_filenames", False)
    raw.setdefault("require_unlock_for_delete", True)
    try:
        raw["session_timeout_minutes"] = max(1, min(int(raw.get("session_timeout_minutes") or 15), 1440))
    except Exception:
        raw["session_timeout_minutes"] = 15
    raw["enabled"] = bool(raw.get("enabled", False))
    raw["privacy_blur"] = bool(raw.get("privacy_blur", True))
    raw["hide_filenames"] = bool(raw.get("hide_filenames", False))
    raw["require_unlock_for_delete"] = bool(raw.get("require_unlock_for_delete", True))
    return raw
def privacy_pin_configured():
    p = get_privacy_settings()
    return bool(p.get("pin_hash") and p.get("pin_salt"))
def privacy_is_enabled():
    return bool(get_privacy_settings().get("enabled") and privacy_pin_configured())
def privacy_public_settings():
    p = get_privacy_settings()
    return {
        "enabled": bool(p.get("enabled") and privacy_pin_configured()),
        "configured": privacy_pin_configured(),
        "session_timeout_minutes": int(p.get("session_timeout_minutes") or 15),
        "privacy_blur": bool(p.get("privacy_blur", True)),
        "hide_filenames": bool(p.get("hide_filenames", False)),
        "require_unlock_for_delete": bool(p.get("require_unlock_for_delete", True)),
    }
def privacy_hash_pin(pin, salt=None):
    pin = str(pin or "")
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt.encode("utf-8"), 200_000)
    return salt, digest.hex()
def privacy_verify_pin(pin):
    p = get_privacy_settings()
    if not p.get("pin_hash") or not p.get("pin_salt"):
        return False
    try:
        _, digest = privacy_hash_pin(str(pin or ""), p.get("pin_salt"))
        return hmac.compare_digest(digest, str(p.get("pin_hash") or ""))
    except Exception:
        return False
def privacy_expiry_seconds():
    return max(60, int(get_privacy_settings().get("session_timeout_minutes") or 15) * 60)
def cleanup_privacy_sessions():
    now = datetime.now().timestamp()
    expired = [token for token, expiry in PRIVACY_SESSION_TOKENS.items() if float(expiry or 0) <= now]
    for token in expired:
        PRIVACY_SESSION_TOKENS.pop(token, None)
def create_privacy_session():
    cleanup_privacy_sessions()
    token = secrets.token_urlsafe(32)
    PRIVACY_SESSION_TOKENS[token] = datetime.now().timestamp() + privacy_expiry_seconds()
    return token
def privacy_token_from_request(request):
    # NOTE: query-string branch intentionally removed — tokens in URLs leak via
    # server logs, browser history, and Referer headers. Header + cookie only.
    return (
        request.headers.get("X-Telesaved-Privacy-Token") or request.cookies.get("telesaved_privacy_token") or ""
    )
def is_privacy_unlocked(request):
    if not privacy_is_enabled():
        return True
    cleanup_privacy_sessions()
    token = privacy_token_from_request(request)
    if not token:
        return False
    expiry = PRIVACY_SESSION_TOKENS.get(token)
    if not expiry or float(expiry) <= datetime.now().timestamp():
        PRIVACY_SESSION_TOKENS.pop(token, None)
        return False
    PRIVACY_SESSION_TOKENS[token] = datetime.now().timestamp() + privacy_expiry_seconds()
    return True
@web.middleware
async def privacy_middleware(request, handler):
    path = request.path or "/"
    if path in {"/", "/favicon.ico", "/api/health", "/api/setup/status"} or path.startswith("/api/privacy") or path.startswith("/api/auth/"):
        return await handler(request)
    if not privacy_is_enabled() or is_privacy_unlocked(request):
        return await handler(request)
    if path.startswith("/api/"):
        return web.json_response({"ok": False, "locked": True, "error": "TeleSaved is locked"}, status=423)
    return web.Response(status=423, text="TeleSaved is locked")
def get_auto_refresh():
    value = str(settings_data.get("auto_refresh", "10"))
    return value if value in {"10", "15", "30", "60", "never"} else "10"
def get_download_directory():
    path = settings_data.get("download_directory", BASE_PATH)
    return path or BASE_PATH
def normalize_source_ref(value):
    raw = str(value or "").strip()
    if not raw:
        return ""
    lower = raw.lower()
    if lower in {"me", "saved", "saved messages", "saved_messages", "self"}:
        return "me"
    # Convert private Telegram post links such as https://t.me/c/1234567890/42
    # into the channel peer ID format Telethon can often resolve.
    m = re.search(r"t\.me/c/(\d+)(?:/\d+)?", raw, flags=re.IGNORECASE)
    if m:
        return f"-100{m.group(1)}"
    # Convert public Telegram links into @username references.
    m = re.search(r"t\.me/(?!c/)([A-Za-z0-9_]+)", raw, flags=re.IGNORECASE)
    if m:
        return f"@{m.group(1)}"
    return raw
def normalize_video_sources(sources):
    cleaned = []
    seen = set()
    if not isinstance(sources, list):
        sources = [sources]
    for source in sources:
        value = normalize_source_ref(source)
        if not value:
            continue
        key = value.lower()
        if key not in seen:
            cleaned.append(value)
            seen.add(key)
    return cleaned or ["me"]
def get_video_sources():
    return normalize_video_sources(settings_data.get("video_sources", ["me"]))
def set_video_sources(values):
    settings_data["video_sources"] = normalize_video_sources(values)
    save_settings()
    return settings_data["video_sources"]
def source_display_name(ref):
    ref = str(ref or "").strip()
    if ref.lower() in {"me", "saved", "saved messages", "self"}:
        return "Saved Messages"
    return ref
def default_source_profile(ref, title=None):
    ref = normalize_source_ref(ref) or "me"
    safe_title = title or source_display_name(ref)
    return {
        "ref": ref,
        "title": safe_title,
        "enabled": True,
        "sync_enabled": True,
        "download_videos": True,
        "auto_tag": True,
        "destination_subdir": "",
        "start_date": "",
        "min_size_mb": "",
        "max_size_mb": "",
        "notes": "",
        # Bundle 2 / Source Pipelines 2.0
        "pipeline_enabled": True,
        "pipeline_mode": "incremental",
        "max_messages_per_run": "",
        "lookback_days": "",
        "media_filter": "videos",
        "preserve_telegram_metadata": True,
        "preserve_captions": True,
        "preserve_sender": True,
        "preserve_forwards": True,
        "preserve_replies": True,
        "preserve_reactions": True,
        "apply_source_tag": False,
        "source_tag": re.sub(r"[^A-Za-z0-9_ -]+", "_", str(safe_title or ref)).strip()[:40],
        "pipeline_notes": "",
    }
def get_source_profiles():
    raw = settings_data.setdefault("source_profiles", {})
    profiles = {}
    for ref in get_video_sources():
        clean_ref = normalize_source_ref(ref) or "me"
        existing = raw.get(clean_ref) or raw.get(ref) or {}
        profile = default_source_profile(clean_ref, existing.get("title") or source_display_name(clean_ref))
        if isinstance(existing, dict):
            for key in profile.keys():
                if key in existing:
                    profile[key] = existing[key]
        profile["ref"] = clean_ref
        profile["enabled"] = bool(profile.get("enabled", True))
        profile["sync_enabled"] = bool(profile.get("sync_enabled", True))
        profile["download_videos"] = bool(profile.get("download_videos", True))
        profile["auto_tag"] = bool(profile.get("auto_tag", True))
        profiles[clean_ref] = profile
    return profiles
def get_source_profile(ref, title=None):
    clean_ref = normalize_source_ref(ref) or "me"
    profiles = get_source_profiles()
    profile = profiles.get(clean_ref) or default_source_profile(clean_ref, title)
    if title and (not profile.get("title") or profile.get("title") == clean_ref):
        profile["title"] = title
    return profile
def save_source_profiles(profiles):
    cleaned = {}
    sources = []
    if isinstance(profiles, dict):
        iterable = profiles.values()
    else:
        iterable = profiles or []
    for item in iterable:
        if isinstance(item, str):
            profile = default_source_profile(item)
        elif isinstance(item, dict):
            profile = default_source_profile(item.get("ref") or item.get("source") or "me", item.get("title"))
            for key in profile.keys():
                if key in item:
                    profile[key] = item[key]
        else:
            continue
        ref = normalize_source_ref(profile.get("ref")) or "me"
        profile["ref"] = ref
        profile["enabled"] = bool(profile.get("enabled", True))
        profile["sync_enabled"] = bool(profile.get("sync_enabled", True))
        profile["download_videos"] = bool(profile.get("download_videos", True))
        profile["auto_tag"] = bool(profile.get("auto_tag", True))
        cleaned[ref] = profile
        sources.append(ref)
    settings_data["video_sources"] = normalize_video_sources(sources)
    settings_data["source_profiles"] = cleaned
    save_settings()
    return get_source_profiles()
def enabled_video_sources():
    profiles = get_source_profiles()
    enabled = [ref for ref, profile in profiles.items() if profile.get("enabled", True) and
    profile.get("sync_enabled", True)]
    return enabled or ["me"]
def safe_source_subdir(value):
    value = str(value or "").strip().strip("/")
    if not value:
        return ""
    parts = []
    for part in value.replace("\\", "/").split("/"):
        part = re.sub(r"[^A-Za-z0-9._ -]+", "_", part).strip(" .")
        if part and part not in {".", ".."}:
            parts.append(part[:80])
    return os.path.join(*parts) if parts else ""
def message_size_bytes(message):
    try:
        return int(getattr(getattr(message, "file", None), "size", 0) or 0)
    except Exception:
        return 0
def source_allows_message(message, source_profile):
    if not source_profile.get("enabled", True) or not source_profile.get("download_videos", True):
        return False, "Source disabled"
    if not source_profile.get("pipeline_enabled", True):
        return False, "Source pipeline disabled"
    start_date = str(source_profile.get("start_date") or "").strip()
    if start_date:
        try:
            msg_date = getattr(message, "date", None)
            if msg_date and msg_date.date().isoformat() < start_date:
                return False, f"Message before source start date {start_date}"
        except Exception:
            pass
    lookback_days = str(source_profile.get("lookback_days") or "").strip()
    if lookback_days:
        try:
            msg_date = getattr(message, "date", None)
            if msg_date:
                age_days = (datetime.now(msg_date.tzinfo) - msg_date).days if msg_date.tzinfo else (datetime.now() - msg_date.replace(tzinfo=None)).days
                if age_days > int(float(lookback_days)):
                    return False, f"Message older than source lookback window ({lookback_days} days)"
        except Exception:
            pass
    size = message_size_bytes(message)
    min_mb = str(source_profile.get("min_size_mb") or "").strip()
    max_mb = str(source_profile.get("max_size_mb") or "").strip()
    try:
        if min_mb and size and size < float(min_mb) * 1024 * 1024:
            return False, f"File below source minimum size {min_mb} MB"
    except Exception:
        pass
    try:
        if max_mb and size and size > float(max_mb) * 1024 * 1024:
            return False, f"File above source maximum size {max_mb} MB"
    except Exception:
        pass
    return True, ""
def parse_source_limit(value, default=0, minimum=0, maximum=100000):
    try:
        text = str(value or "").strip()
        if not text:
            return int(default or 0)
        parsed = int(float(text))
        if parsed <= 0:
            return int(default or 0)
        return max(minimum, min(parsed, maximum))
    except Exception:
        return int(default or 0)
def telegram_message_media_kind(message):
    try:
        if getattr(message, "video", None):
            return "video"
        file_obj = getattr(message, "file", None)
        mime = str(getattr(file_obj, "mime_type", "") or getattr(getattr(message, "document", None),
        "mime_type", "") or "").lower()
        if mime.startswith("video/"):
            return "video"
        if getattr(message, "document", None) and mime.startswith("video/"):
            return "video"
        if getattr(message, "photo", None):
            return "photo"
        if getattr(message, "audio", None) or mime.startswith("audio/"):
            return "audio"
        if getattr(message, "document", None):
            return "document"
    except Exception:
        pass
    return "other"
def message_matches_source_media_filter(message, source_profile):
    media_filter = str(source_profile.get("media_filter") or "videos").strip().lower()
    kind = telegram_message_media_kind(message)
    if media_filter in {"video", "videos", "video_documents"}:
        return kind == "video"
    if media_filter in {"all_media", "media"}:
        return kind in {"video", "document", "audio", "photo"}
    return kind == "video"
def save_settings():
    save_json(
        SETTINGS_FILE,
        {
            "tag_weights": TAG_WEIGHTS,
            "tag_thresholds": TAG_THRESHOLDS,
            "auto_refresh": settings_data.get("auto_refresh", "10"),
            "download_directory": settings_data.get("download_directory", BASE_PATH),
            "video_sources": get_video_sources(),
            "source_profiles": get_source_profiles(),
            "custom_tags": get_custom_tags(),
            "tag_metadata": settings_data.get("tag_metadata", {}),
            "smart_collections": get_smart_collections() if "get_smart_collections" in globals() else settings_data.get("smart_collections", []),
            "automation_rules": get_rules() if "get_rules" in globals() else settings_data.get("automation_rules", []),
            "nudenet_threshold": settings_data.get("nudenet_threshold", DEFAULT_NUDENET_THRESHOLD),
            "privacy": get_privacy_settings(),
        }
    )
    logger.info("Settings saved to %s", SETTINGS_FILE)
# ================= FEEDBACK / MANUAL OVERRIDES =================
feedback_data = load_json(
    FEEDBACK_FILE,
    {
        "manual_overrides": {},
        "history": []
    }
)
feedback_data.setdefault("manual_overrides", {})
feedback_data.setdefault("history", [])
feedback_data.setdefault("review_skips", {})
# ================= TRASH / BACKUP STATE =================
trash_index = load_json(TRASH_INDEX_FILE, {"items": []})
trash_index.setdefault("items", [])
def save_trash_index():
    ensure_directory(TRASH_PATH)
    save_json(TRASH_INDEX_FILE, trash_index)
def trash_items():
    items = trash_index.setdefault("items", [])
    items.sort(key=lambda x: x.get("trashed_at", ""), reverse=True)
    return items
def save_feedback():
    save_json(FEEDBACK_FILE, feedback_data)
def get_manual_override(path):
    return feedback_data["manual_overrides"].get(path)
def get_effective_tags(path):
    override = get_manual_override(path)
    if override and isinstance(override.get("tags"), list):
        return override["tags"]
    return tags.get(path, [])
def set_manual_override(path, selected_tags, original_tags):
    feedback_data["manual_overrides"][path] = {
        "tags": selected_tags,
        "edited_at": datetime.now().isoformat(timespec="seconds"),
        "original_tags": original_tags
    }
    feedback_data["history"].insert(
        0,
        {
            "path": path,
            "edited_at": datetime.now().isoformat(timespec="seconds"),
            "original_tags": original_tags,
            "new_tags": selected_tags
        }
    )
    feedback_data["history"] = feedback_data["history"][:500]
    save_feedback()
    logger.info("Manual override saved for %s => %s", path, selected_tags)
def clear_manual_override(path):
    if path in feedback_data["manual_overrides"]:
        del feedback_data["manual_overrides"][path]
        save_feedback()
        logger.info("Manual override cleared for %s", path)
def compute_feedback_bias():
    adds = {tag: 0 for tag in PREDEFINED_TAGS}
    removes = {tag: 0 for tag in PREDEFINED_TAGS}
    for item in feedback_data.get("history", []):
        original = set(item.get("original_tags", []))
        new = set(item.get("new_tags", []))
        for tag in PREDEFINED_TAGS:
            if tag in new and tag not in original:
                adds[tag] += 1
            if tag in original and tag not in new:
                removes[tag] += 1
    bias = {}
    for tag in PREDEFINED_TAGS:
        net = adds[tag] - removes[tag]
        bias[tag] = max(-0.08, min(net * 0.01, 0.08))
    return bias
# ================= SQLITE STATE BACKEND =================
SQLITE_SCHEMA_VERSION = 3
sqlite_lock = Lock()
def sqlite_connect():
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn
def _column_exists(conn, table, column):
    """Return True if `column` exists on `table` in the connected SQLite db."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r["name"] == column for r in rows)
def init_sqlite_db():
    """Create and migrate the SQLite metadata store.
    Schema versions:
      1 — original schema (videos, message_ids, content_hashes, app_meta)
      2 — adds ai_signals TEXT column to videos
      3 — adds audio_language TEXT column to videos (indexed)
    """
    ensure_directory(BASE_PATH)
    with sqlite_lock:
        with sqlite_connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS app_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS message_ids (
                    message_id TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS content_hashes (
                    hash TEXT PRIMARY KEY
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS videos (
                    path TEXT PRIMARY KEY,
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    scores_json TEXT NOT NULL DEFAULT '{}',
                    embedding_json TEXT NOT NULL DEFAULT '[]',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_videos_updated_at ON videos(updated_at)")
            # ── Schema migrations (additive, column-existence-guarded) ────────
            existing_version = 1
            row = conn.execute("SELECT value FROM app_meta WHERE key='schema_version'").fetchone()
            if row:
                try:
                    existing_version = int(row["value"])
                except Exception:
                    existing_version = 1
            # v2 — ai_signals column
            if not _column_exists(conn, "videos", "ai_signals"):
                try:
                    conn.execute("ALTER TABLE videos ADD COLUMN ai_signals TEXT")
                    logger.info("DB migration: added ai_signals column")
                except Exception:
                    pass
            # v3 — audio_language column
            if not _column_exists(conn, "videos", "audio_language"):
                try:
                    conn.execute("ALTER TABLE videos ADD COLUMN audio_language TEXT")
                    logger.info("DB migration: added audio_language column")
                except Exception:
                    pass
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_videos_audio_language ON videos(audio_language)"
            )
            # video_tag_labels — always CREATE IF NOT EXISTS (safe for any version)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS video_tag_labels (
                    path TEXT NOT NULL,
                    tag  TEXT NOT NULL,
                    label_source TEXT NOT NULL DEFAULT 'ai_inferred',
                    created_at   TEXT NOT NULL,
                    PRIMARY KEY (path, tag)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_vtl_label_source ON video_tag_labels(label_source)"
            )
            # tag_suggestions — pending user-contributed tag requests
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tag_suggestions (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    tag         TEXT NOT NULL,
                    suggested_by TEXT NOT NULL,
                    video_path  TEXT NOT NULL,
                    created_at  TEXT NOT NULL,
                    status      TEXT NOT NULL DEFAULT 'pending'
                )
                """
            )
            conn.execute(
                "INSERT OR REPLACE INTO app_meta(key, value) VALUES (?, ?)",
                ("schema_version", str(SQLITE_SCHEMA_VERSION)),
            )
            conn.commit()
def sqlite_state_has_data():
    try:
        with sqlite_connect() as conn:
            cur = conn.execute("SELECT COUNT(*) AS count FROM videos")
            if int(cur.fetchone()["count"] or 0) > 0:
                return True
            cur = conn.execute("SELECT COUNT(*) AS count FROM message_ids")
            if int(cur.fetchone()["count"] or 0) > 0:
                return True
            cur = conn.execute("SELECT COUNT(*) AS count FROM content_hashes")
            return int(cur.fetchone()["count"] or 0) > 0
    except Exception as e:
        logger.exception("Unable to inspect SQLite state: %s", e)
        return False
def sqlite_json_load(value, default):
    try:
        if value is None or value == "":
            return default
        return json.loads(value)
    except Exception:
        return default
def load_state_from_sqlite(default_state=None):
    default_state = default_state or {
        "ids": [],
        "hashes": [],
        "tags": {},
        "scores": {},
        "embeddings": {},
        "metadata": {},
    }
    try:
        with sqlite_connect() as conn:
            ids = []
            for row in conn.execute("SELECT value_json FROM message_ids ORDER BY rowid"):
                ids.append(sqlite_json_load(row["value_json"], row["value_json"]))
            hashes = []
            for row in conn.execute("SELECT hash FROM content_hashes ORDER BY rowid"):
                hashes.append(row["hash"])
            tags_out = {}
            scores_out = {}
            embeddings_out = {}
            metadata_out = {}
            for row in conn.execute("SELECT path, tags_json, scores_json, embedding_json, metadata_json FROM videos ORDER BY path"):
                path = row["path"]
                tags_out[path] = sqlite_json_load(row["tags_json"], [])
                scores_out[path] = sqlite_json_load(row["scores_json"], {})
                embeddings_out[path] = sqlite_json_load(row["embedding_json"], [])
                metadata_out[path] = sqlite_json_load(row["metadata_json"], {})
            return {
                "ids": ids,
                "hashes": hashes,
                "tags": tags_out,
                "scores": scores_out,
                "embeddings": embeddings_out,
                "metadata": metadata_out,
            }
    except Exception as e:
        logger.exception("Failed to load SQLite state; using JSON fallback: %s", e)
        return default_state
def persist_state_to_sqlite(ids, hashes, tag_map, score_map, embedding_map, metadata_map):
    """Persist the in-memory library index to SQLite transactionally."""
    updated_at = now_stamp()
    with sqlite_lock:
        with sqlite_connect() as conn:
            conn.execute("BEGIN")
            conn.execute("DELETE FROM message_ids")
            conn.execute("DELETE FROM content_hashes")
            conn.execute("DELETE FROM videos")
            conn.executemany(
                "INSERT OR REPLACE INTO message_ids(message_id, value_json) VALUES (?, ?)",
                [(str(item), json.dumps(item)) for item in ids],
            )
            conn.executemany(
                "INSERT OR REPLACE INTO content_hashes(hash) VALUES (?)",
                [(str(item),) for item in hashes if str(item)],
            )
            rows = []
            for path in sorted(tag_map.keys()):
                rows.append((
                    path,
                    json.dumps(tag_map.get(path, [])),
                    json.dumps(score_map.get(path, {})),
                    json.dumps(embedding_map.get(path, [])),
                    json.dumps(metadata_map.get(path, {})),
                    updated_at,
                ))
            conn.executemany(
                """
                INSERT OR REPLACE INTO videos(path, tags_json, scores_json, embedding_json, metadata_json,
                updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.execute("INSERT OR REPLACE INTO app_meta(key, value) VALUES (?, ?)", ("updated_at",
            updated_at))
            conn.execute("INSERT OR REPLACE INTO app_meta(key, value) VALUES (?, ?)", ("backend", "sqlite"))
            conn.commit()
def save_ai_signals(rel_path, signals):
    """Write the ai_signals JSON for a single video row.
    Called by the pipeline after NudeNet classification.
    signals is a dict or None; None clears the column.
    """
    value = json.dumps(signals) if signals is not None else None
    with sqlite_lock:
        with sqlite_connect() as conn:
            conn.execute(
                "UPDATE videos SET ai_signals = ? WHERE path = ?",
                (value, str(rel_path)),
            )
            conn.commit()

def get_ai_signals(rel_path):
    """Return the ai_signals dict for a video, or None if not yet classified."""
    try:
        with sqlite_connect() as conn:
            row = conn.execute("SELECT ai_signals FROM videos WHERE path = ?", (str(rel_path),)).fetchone()
            if row and row["ai_signals"]:
                return json.loads(row["ai_signals"])
    except Exception:
        pass
    return None

def save_audio_language(rel_path, language):
    """Write the ISO 639-1 audio_language for a single video row.
    Called after Whisper transcription. Empty string clears the column to NULL.
    """
    value = str(language).strip() or None
    with sqlite_lock:
        with sqlite_connect() as conn:
            conn.execute(
                "UPDATE videos SET audio_language = ? WHERE path = ?",
                (value, str(rel_path)),
            )
            conn.commit()

def get_audio_language(rel_path):
    """Return the stored audio_language code for a video, or None."""
    try:
        with sqlite_connect() as conn:
            row = conn.execute(
                "SELECT audio_language FROM videos WHERE path = ?", (str(rel_path),)
            ).fetchone()
            if row:
                return row["audio_language"]
    except Exception:
        pass
    return None

# ── label_source helpers ──────────────────────────────────────────────────────

VALID_LABEL_SOURCES = {"ai_inferred", "user_confirmed", "user_corrected", "user_added"}

def write_tag_label(rel_path, tag, label_source):
    """Upsert a single (path, tag) row in video_tag_labels."""
    if label_source not in VALID_LABEL_SOURCES:
        label_source = "ai_inferred"
    with sqlite_lock:
        with sqlite_connect() as conn:
            conn.execute(
                """
                INSERT INTO video_tag_labels (path, tag, label_source, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(path, tag) DO UPDATE SET
                    label_source = excluded.label_source,
                    created_at   = excluded.created_at
                """,
                (str(rel_path), str(tag), label_source, now_stamp()),
            )
            conn.commit()

def write_ai_tag_labels(rel_path, tag_list):
    """Write ai_inferred label rows for a list of tags assigned by the AI pipeline."""
    ts = now_stamp()
    rows = [(str(rel_path), str(t), "ai_inferred", ts) for t in tag_list]
    with sqlite_lock:
        with sqlite_connect() as conn:
            conn.executemany(
                """
                INSERT INTO video_tag_labels (path, tag, label_source, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(path, tag) DO NOTHING
                """,
                rows,
            )
            conn.commit()

def get_tag_labels(rel_path):
    """Return {tag: label_source} for a video. Tags not in the table are not returned."""
    try:
        with sqlite_connect() as conn:
            rows = conn.execute(
                "SELECT tag, label_source FROM video_tag_labels WHERE path = ?",
                (str(rel_path),),
            ).fetchall()
            return {r["tag"]: r["label_source"] for r in rows}
    except Exception:
        return {}

def migrate_legacy_tag_labels(rel_path, label_source="ai_inferred"):
    """Write label rows for all tags on a video that lack one. Used by the migration tool."""
    existing = get_tag_labels(rel_path)
    video_tags = tags.get(str(rel_path), [])
    new_rows = [
        (str(rel_path), str(t), label_source, now_stamp())
        for t in video_tags
        if t not in existing
    ]
    if not new_rows:
        return 0
    with sqlite_lock:
        with sqlite_connect() as conn:
            conn.executemany(
                """
                INSERT INTO video_tag_labels (path, tag, label_source, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(path, tag) DO NOTHING
                """,
                new_rows,
            )
            conn.commit()
    return len(new_rows)

def write_legacy_state_snapshot(ids, hashes, tag_map, score_map, embedding_map, metadata_map):
    """Write a JSON snapshot for backups, diagnostics, and rollback tools."""
    save_json(
        STATE_FILE,
        {
            "backend": "sqlite",
            "database_file": DB_FILE,
            "snapshot_created_at": now_stamp(),
            "ids": list(ids),
            "hashes": list(hashes),
            "tags": tag_map,
            "scores": score_map,
            "embeddings": embedding_map,
            "metadata": metadata_map,
        }
    )
def sqlite_backend_info():
    info = {
        "backend": "sqlite",
        "database_file": DB_FILE,
        "exists": os.path.exists(DB_FILE),
        "size": os.path.getsize(DB_FILE) if os.path.exists(DB_FILE) else 0,
        "size_human": format_bytes(os.path.getsize(DB_FILE)) if os.path.exists(DB_FILE) else "0 B",
        "videos": 0,
        "message_ids": 0,
        "content_hashes": 0,
        "schema_version": SQLITE_SCHEMA_VERSION,
    }
    try:
        with sqlite_connect() as conn:
            info["videos"] = int(conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0] or 0)
            info["message_ids"] = int(conn.execute("SELECT COUNT(*) FROM message_ids").fetchone()[0] or 0)
            info["content_hashes"] = int(conn.execute("SELECT COUNT(*) FROM content_hashes").fetchone()[0] or
            0)
            row = conn.execute("SELECT value FROM app_meta WHERE key='updated_at'").fetchone()
            info["updated_at"] = row["value"] if row else ""
    except Exception as e:
        info["error"] = str(e)
    return info
# ================= STATE =================
init_sqlite_db()
legacy_state = load_json(
    STATE_FILE,
    {
        "ids": [],
        "hashes": [],
        "tags": {},
        "scores": {},
        "embeddings": {},
        "metadata": {}
    }
)
if not sqlite_state_has_data() and legacy_state.get("tags"):
    logger.info("Migrating legacy JSON state to SQLite database: %s", DB_FILE)
    persist_state_to_sqlite(
        legacy_state.get("ids", []),
        legacy_state.get("hashes", []),
        legacy_state.get("tags", {}),
        legacy_state.get("scores", {}),
        legacy_state.get("embeddings", {}),
        legacy_state.get("metadata", {}),
    )
state = load_state_from_sqlite(legacy_state)
state.setdefault("ids", [])
state.setdefault("hashes", [])
state.setdefault("tags", {})
state.setdefault("scores", {})
state.setdefault("embeddings", {})
state.setdefault("metadata", {})
state_ids = set(state.get("ids", []))
state_hashes = set(state.get("hashes", []))
tags = state.get("tags", {})
scores_store = state.get("scores", {})
embeddings_store = state.get("embeddings", {})
metadata_store = state.get("metadata", {})
def save_state():
    persist_state_to_sqlite(state_ids, state_hashes, tags, scores_store, embeddings_store, metadata_store)
    write_legacy_state_snapshot(state_ids, state_hashes, tags, scores_store, embeddings_store, metadata_store)
def compute_library_dashboard_stats(probe_missing_limit=25):
    """Build Administration dashboard statistics for the indexed library.
    The dashboard uses cached duration metadata where available. To avoid a long
    blocking dashboard load on large libraries, it only probes a small number of
    missing durations per request and caches those values in state.json.
    """
    total_videos = len(tags)
    tag_counts = {tag: 0 for tag in PREDEFINED_TAGS}
    source_counts = {}
    source_duration_totals = {}
    source_duration_counts = {}
    duration_total = 0.0
    duration_count = 0
    missing_duration_count = 0
    total_size = 0
    existing_files = 0
    missing_files = 0
    probed = 0
    changed = False
    for rel_path in list(tags.keys()):
        effective = get_effective_tags(rel_path)
        for tag in effective:
            if tag not in tag_counts:
                tag_counts[tag] = 0
            tag_counts[tag] += 1
        abs_path = find_existing_file(rel_path) if "find_existing_file" in globals() else None
        meta = metadata_store.setdefault(rel_path, {})
        file_meta = collect_file_metadata(rel_path, abs_path=abs_path, ensure_duration=False)
        if file_meta.get("exists"):
            existing_files += 1
        else:
            missing_files += 1
        try:
            total_size += int(file_meta.get("size") or 0)
        except Exception:
            pass
        duration = 0.0
        try:
            duration = float(file_meta.get("duration") or 0.0)
        except Exception:
            duration = 0.0
        if duration <= 0 and abs_path and os.path.exists(abs_path) and probed < probe_missing_limit:
            probed += 1
            try:
                duration = float(get_video_duration_seconds(abs_path) or 0.0)
                if duration > 0:
                    meta["duration"] = duration
                    meta["duration_scanned_at"] = datetime.now().isoformat(timespec="seconds")
                    changed = True
                    file_meta = collect_file_metadata(rel_path, abs_path=abs_path, ensure_duration=False)
            except Exception:
                duration = 0.0
        source = file_meta.get("source") or "Saved Messages"
        source_counts[source] = source_counts.get(source, 0) + 1
        if duration > 0:
            duration_total += duration
            duration_count += 1
            source_duration_totals[source] = source_duration_totals.get(source, 0.0) + duration
            source_duration_counts[source] = source_duration_counts.get(source, 0) + 1
        else:
            missing_duration_count += 1
    if changed:
        save_state()
    def percent(count):
        if not total_videos:
            return 0.0
        return round((float(count) / float(total_videos)) * 100.0, 2)
    by_tag = []
    for label, count in tag_counts.items():
        by_tag.append({
            "label": label,
            "count": int(count),
            "percent": percent(count),
        })
    by_tag.sort(key=lambda row: (-row["count"], row["label"].lower()))
    by_source = []
    for label, count in source_counts.items():
        src_duration_count = source_duration_counts.get(label, 0)
        src_total_duration = source_duration_totals.get(label, 0.0)
        src_average = src_total_duration / src_duration_count if src_duration_count else 0.0
        by_source.append({
            "label": label,
            "count": int(count),
            "percent": percent(count),
            "average_duration": round(src_average, 2),
            "average_duration_human": format_duration(src_average),
            "duration_count": int(src_duration_count),
        })
    by_source.sort(key=lambda row: (-row["count"], row["label"].lower()))
    average_duration = duration_total / duration_count if duration_count else 0.0
    return {
        "total_videos": int(total_videos),
        "existing_files": int(existing_files),
        "missing_files": int(missing_files),
        "total_size": int(total_size),
        "total_size_human": format_bytes(total_size),
        "by_tag": by_tag,
        "by_source": by_source,
        "duration": {
            "average": round(average_duration, 2),
            "average_human": format_duration(average_duration),
            "total": round(duration_total, 2),
            "total_human": format_duration(duration_total),
            "count": int(duration_count),
            "missing_count": int(missing_duration_count),
            "probed_this_request": int(probed),
        },
    }
# ================= SOURCE INTELLIGENCE =================
def source_intelligence_key(ref="", title=""):
    clean_ref = normalize_source_ref(ref) if "normalize_source_ref" in globals() else str(ref or "").strip()
    if clean_ref:
        return clean_ref
    clean_title = str(title or "").strip()
    if clean_title.lower() in {"saved messages", "saved", "me"}:
        return "me"
    return clean_title or "unknown"
def source_health_grade(score):
    try:
        score = float(score)
    except Exception:
        score = 0.0
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 65:
        return "C"
    if score >= 50:
        return "D"
    return "F"
def source_recommendations(row):
    recs = []
    count = int(row.get("count") or 0)
    if count <= 0:
        recs.append("No indexed videos yet. Confirm the source is enabled and has completed a sync.")
        return recs
    duplicate_rate = float(row.get("duplicate_rate") or 0.0)
    missing_rate = float(row.get("missing_rate") or 0.0)
    failed_downloads = int(row.get("failed_downloads") or 0)
    missing_duration_rate = float(row.get("missing_duration_rate") or 0.0)
    avg_duration = float(row.get("average_duration") or 0.0)
    if duplicate_rate >= 25:
        recs.append("High duplicate rate. Review duplicates for this source before expanding sync.")
    elif duplicate_rate >= 10:
        recs.append("Moderate duplicate rate. Consider periodic duplicate cleanup for this source.")
    if missing_rate > 0:
        recs.append("Some indexed files are missing on disk. Run the missing-file index sweep.")
    if failed_downloads:
        recs.append("Recent failed downloads detected. Check Jobs and Downloads for source-specific errors.")
    if missing_duration_rate >= 20:
        recs.append("Many videos are missing duration metadata. Run metadata rebuild or open the dashboard to cache durations gradually.")
    if avg_duration and avg_duration < 20:
        recs.append("This source mostly produces very short clips. Consider a minimum-size or minimum-duration rule if noise is high.")
    elif avg_duration and avg_duration > 1800:
        recs.append("This source tends to produce long videos. Monitor storage growth and duplicate rate.")
    if not recs:
        recs.append("Source looks healthy based on current metadata.")
    return recs
def build_source_intelligence_report(probe_missing_limit=20):
    """Return per-source health, quality, storage, duration, tag, and duplicate statistics."""
    profiles = get_source_profiles() if "get_source_profiles" in globals() else {}
    configured_refs = set(profiles.keys())
    rows = {}
    def ensure_row(ref="me", title="Saved Messages"):
        key = source_intelligence_key(ref, title)
        profile = profiles.get(key) or profiles.get(ref) or {}
        label = profile.get("title") or title or source_display_name(key) if "source_display_name" in globals() else (title or key)
        if key not in rows:
            rows[key] = {
                "ref": key,
                "title": label,
                "configured": key in configured_refs,
                "enabled": profile.get("enabled", True),
                "sync_enabled": profile.get("sync_enabled", True),
                "download_videos": profile.get("download_videos", True),
                "auto_tag": profile.get("auto_tag", True),
                "destination_subdir": profile.get("destination_subdir", ""),
                "start_date": profile.get("start_date", ""),
                "min_size_mb": profile.get("min_size_mb", ""),
                "max_size_mb": profile.get("max_size_mb", ""),
                "notes": profile.get("notes", ""),
                "count": 0,
                "existing": 0,
                "missing": 0,
                "size": 0,
                "duration_total": 0.0,
                "duration_count": 0,
                "duration_missing": 0,
                "tags": {},
                "format_counts": {"Clip": 0, "Movie": 0},
                "duplicate_members": 0,
                "duplicate_groups": 0,
                "failed_downloads": 0,
                "recent_failures": [],
                "latest_message_date": "",
                "latest_downloaded_at": "",
                "latest_modified": "",
                "latest_video": None,
                "sample_items": [],
            }
        return rows[key]
    ensure_row("me", "Saved Messages")
    for ref, profile in profiles.items():
        ensure_row(ref, profile.get("title") or source_display_name(ref))
    hash_groups = {}
    signature_groups = {}
    changed = False
    probed = 0
    for rel_path in list(tags.keys()):
        meta = metadata_store.setdefault(rel_path, {})
        src_ref = meta.get("source_ref") or ("me" if metadata_source_label(meta) == "Saved Messages" else "")
        title = metadata_source_label(meta)
        row = ensure_row(src_ref or title, title)
        row["count"] += 1
        abs_path = find_existing_file(rel_path)
        file_meta = collect_file_metadata(rel_path, abs_path=abs_path, ensure_duration=False)
        exists = bool(file_meta.get("exists"))
        if exists:
            row["existing"] += 1
        else:
            row["missing"] += 1
        try:
            size = int(file_meta.get("size") or meta.get("size") or 0)
        except Exception:
            size = 0
        row["size"] += max(0, size)
        duration = 0.0
        try:
            duration = float(file_meta.get("duration") or meta.get("duration") or 0.0)
        except Exception:
            duration = 0.0
        if duration <= 0 and abs_path and os.path.exists(abs_path) and probed < probe_missing_limit:
            try:
                duration = float(get_video_duration_seconds(abs_path) or 0.0)
                probed += 1
                if duration > 0:
                    meta["duration"] = duration
                    meta["duration_scanned_at"] = datetime.now().isoformat(timespec="seconds")
                    changed = True
            except Exception:
                duration = 0.0
        if duration > 0:
            row["duration_total"] += duration
            row["duration_count"] += 1
        else:
            row["duration_missing"] += 1
        effective = get_effective_tags(rel_path)
        for tag in effective:
            row["tags"][tag] = row["tags"].get(tag, 0) + 1
            if tag in row["format_counts"]:
                row["format_counts"][tag] += 1
        sha = str(meta.get("sha256") or meta.get("hash") or "").strip()
        if sha:
            hash_groups.setdefault(sha, []).append((row["ref"], rel_path))
        if size > 0 and duration > 0:
            signature = f"{size}:{round(duration, 1)}"
            signature_groups.setdefault(signature, []).append((row["ref"], rel_path))
        for date_key, row_key in (("message_date", "latest_message_date"), ("downloaded_at",
        "latest_downloaded_at"), ("modified", "latest_modified")):
            value = str(meta.get(date_key) or file_meta.get(date_key) or "")
            if value and value > str(row.get(row_key) or ""):
                row[row_key] = value
        latest_marker = str(meta.get("downloaded_at") or meta.get("message_date") or file_meta.get("modified") or "")
        current_marker = str((row.get("latest_video") or {}).get("sort_marker") or "")
        if latest_marker and latest_marker >= current_marker:
            row["latest_video"] = {
                "path": rel_path,
                "filename": short_name(rel_path),
                "sort_marker": latest_marker,
                "file_url": f"/files/{quote(rel_path)}",
            }
        if len(row["sample_items"]) < 6:
            try:
                row["sample_items"].append(serialize_item({
                    "path": rel_path,
                    "tags": effective,
                    "score_map": scores_store.get(rel_path, {}),
                    "manual_override": get_manual_override(rel_path) is not None,
                }))
            except Exception:
                pass
    if changed:
        save_state()
    def apply_duplicate_group(group):
        if len(group) < 2:
            return
        seen_sources = set()
        for ref, _path in group:
            row = ensure_row(ref, source_display_name(ref) if "source_display_name" in globals() else ref)
            row["duplicate_members"] += 1
            seen_sources.add(row["ref"])
        for ref in seen_sources:
            ensure_row(ref)["duplicate_groups"] += 1
    for group in hash_groups.values():
        apply_duplicate_group(group)
    for group in signature_groups.values():
        # Only count signature groups that were not already counted by exact hash.
        apply_duplicate_group(group)
    for item in list(download_history):
        status = str(item.get("status") or "").lower()
        if status != "failed":
            continue
        ref = item.get("source_ref") or item.get("source") or "me"
        title = item.get("source") or source_display_name(ref) if "source_display_name" in globals() else str(ref)
        row = ensure_row(ref, title)
        row["failed_downloads"] += 1
        if len(row["recent_failures"]) < 5:
            row["recent_failures"].append({
                "message_id": item.get("message_id"),
                "filename": item.get("filename"),
                "updated_at": item.get("updated_at"),
                "note": item.get("note"),
            })
    total_count = sum(int(row.get("count") or 0) for row in rows.values())
    total_size = sum(int(row.get("size") or 0) for row in rows.values())
    total_duration = sum(float(row.get("duration_total") or 0.0) for row in rows.values())
    out_rows = []
    for key, row in rows.items():
        count = int(row.get("count") or 0)
        existing = int(row.get("existing") or 0)
        missing = int(row.get("missing") or 0)
        duplicate_members = min(int(row.get("duplicate_members") or 0), count)
        duration_count = int(row.get("duration_count") or 0)
        duration_total = float(row.get("duration_total") or 0.0)
        avg_duration = duration_total / duration_count if duration_count else 0.0
        missing_duration = int(row.get("duration_missing") or 0)
        duplicate_rate = (duplicate_members / count * 100.0) if count else 0.0
        missing_rate = (missing / count * 100.0) if count else 0.0
        missing_duration_rate = (missing_duration / count * 100.0) if count else 0.0
        failed_downloads = int(row.get("failed_downloads") or 0)
        quality_score = 100.0
        quality_score -= min(missing_rate * 0.8, 40.0)
        quality_score -= min(duplicate_rate * 0.45, 30.0)
        quality_score -= min(failed_downloads * 3.0, 18.0)
        quality_score -= min(missing_duration_rate * 0.25, 12.0)
        if row.get("enabled") is False:
            quality_score -= 5.0
        quality_score = max(0.0, min(100.0, quality_score))
        top_tags = [
            {"label": tag, "count": int(value), "percent": round((int(value) / count * 100.0) if count else
            0.0, 2)}
            for tag, value in sorted(row.get("tags", {}).items(), key=lambda item: (-item[1],
            item[0].lower()))[:8]
        ]
        final_row = {
            **row,
            "size_human": format_bytes(row.get("size") or 0),
            "percent_of_library": round((count / total_count * 100.0) if total_count else 0.0, 2),
            "duration_total": round(duration_total, 2),
            "duration_total_human": format_duration(duration_total),
            "average_duration": round(avg_duration, 2),
            "average_duration_human": format_duration(avg_duration),
            "duplicate_rate": round(duplicate_rate, 2),
            "missing_rate": round(missing_rate, 2),
            "missing_duration_rate": round(missing_duration_rate, 2),
            "quality_score": round(quality_score, 2),
            "grade": source_health_grade(quality_score),
            "top_tags": top_tags,
        }
        final_row["recommendations"] = source_recommendations(final_row)
        out_rows.append(final_row)
    out_rows.sort(key=lambda row: (-int(row.get("count") or 0), -float(row.get("quality_score") or 0.0),
    str(row.get("title") or "").lower()))
    active_rows = [row for row in out_rows if int(row.get("count") or 0) > 0]
    best = max(active_rows, key=lambda row: row.get("quality_score", 0), default=None)
    attention = [row for row in sorted(active_rows, key=lambda row: row.get("quality_score", 100))[:5] if
    row.get("quality_score", 100) < 85]
    duplicate_hotspots = [row for row in sorted(active_rows, key=lambda row: row.get("duplicate_rate", 0),
    reverse=True)[:5] if row.get("duplicate_rate", 0) > 0]
    storage_hotspots = [row for row in sorted(active_rows, key=lambda row: row.get("size", 0), reverse=True)
    [:5] if row.get("size", 0) > 0]
    return {
        "ok": True,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "version": APP_VERSION,
        "summary": {
            "source_count": len(out_rows),
            "active_source_count": len(active_rows),
            "configured_source_count": len(configured_refs),
            "total_videos": total_count,
            "total_size": total_size,
            "total_size_human": format_bytes(total_size),
            "total_runtime": round(total_duration, 2),
            "total_runtime_human": format_duration(total_duration),
            "best_source": {"ref": best.get("ref"), "title": best.get("title"), "grade": best.get("grade"),
            "quality_score": best.get("quality_score")} if best else None,
        },
        "attention_sources": attention,
        "duplicate_hotspots": duplicate_hotspots,
        "storage_hotspots": storage_hotspots,
        "sources": out_rows,
    }
# ================= DOWNLOAD STATUS =================
download_status = {}
download_history = deque(maxlen=50)
DOWNLOAD_STOP_THRESHOLD = 5.0
download_control = {
    "mode": "running",
    "reason": "",
    "updated": datetime.now().isoformat(timespec="seconds"),
}
# ================= BULK JOB STATUS =================
bulk_job = {
    "running": False,
    "status": "idle",
    "processed": 0,
    "total": 0,
    "current": "",
    "started_at": "",
    "finished_at": "",
    "errors": 0,
    "mode": "",
}
# ================= JOB / QUEUE CONTROL =================
job_lock = Lock()
jobs = load_json(JOB_FILE, {})
if not isinstance(jobs, dict):
    jobs = {}
for _job in list(jobs.values()):
    if _job.get("status") in {"running", "paused", "cancel_requested"}:
        _job["status"] = "interrupted"
        _job["control"] = "finished"
        _job["finished_at"] = datetime.now().isoformat(timespec="seconds")
        _job["note"] = "Marked interrupted during application startup"
JOB_HISTORY_LIMIT = 500
def persist_jobs():
    try:
        with job_lock:
            snapshot = json.loads(json.dumps(jobs))
        save_json(JOB_FILE, snapshot)
    except Exception as e:
        logger.warning("Failed to persist job state: %s", e)
def create_job(kind, title, total=0, source_ref="", metadata=None, status="running"):
    job_id = f"{kind}-{timestamp_for_filename()}-{hashlib.sha1((title +datetime.now().isoformat()).encode('utf-8')).hexdigest()[:8]}"
    job = {
        "id": job_id,
        "kind": kind,
        "title": title,
        "status": status,
        "control": "queued" if status == "queued" else "running",
        "current": "",
        "processed": 0,
        "total": int(total or 0),
        "percent": 0,
        "errors": 0,
        "source_ref": source_ref,
        "note": "",
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "finished_at": "",
        "metadata": metadata or {},
    }
    with job_lock:
        jobs[job_id] = job
        if len(jobs) > JOB_HISTORY_LIMIT:
            inactive = [j for j in jobs.values() if j.get("status") not in {"running", "paused",
            "cancel_requested"}]
            inactive.sort(key=lambda x: x.get("updated_at", ""))
            for old in inactive[: max(0, len(jobs) - JOB_HISTORY_LIMIT)]:
                jobs.pop(old["id"], None)
    logger.info("Job created: %s %s", job_id, title)
    persist_jobs()
    return job_id
def get_job(job_id):
    with job_lock:
        job = jobs.get(job_id)
        return dict(job) if job else None
def update_job(job_id, **updates):
    if not job_id:
        return None
    with job_lock:
        job = jobs.get(job_id)
        if not job:
            return None
        job.update(updates)
        if job.get("total"):
            try:
                job["percent"] = int((float(job.get("processed", 0)) / float(job.get("total", 1))) * 100)
            except Exception:
                pass
        job["updated_at"] = datetime.now().isoformat(timespec="seconds")
        snapshot = dict(job)
    persist_jobs()
    return snapshot
def finish_job(job_id, status="complete", note=""):
    if not job_id:
        return None
    return update_job(job_id, status=status, control="finished",
    finished_at=datetime.now().isoformat(timespec="seconds"), note=note)
def job_cancel_requested(job_id):
    job = get_job(job_id)
    return bool(job and job.get("control") == "cancel_requested")
def job_pause_requested(job_id):
    job = get_job(job_id)
    return bool(job and job.get("control") == "paused")
def request_job_control(job_id, control):
    if control not in {"paused", "running", "cancel_requested"}:
        raise ValueError("Invalid job control state")
    job = update_job(job_id, control=control, status="paused" if control == "paused" else ("cancel_requested"
    if control == "cancel_requested" else "running"))
    if not job:
        raise KeyError("Job not found")
    logger.warning("Job control changed: %s -> %s", job_id, control)
    return job
def wait_job_if_paused_sync(job_id):
    import time
    while job_pause_requested(job_id):
        update_job(job_id, status="paused", note="Paused by user")
        time.sleep(1)
    if job_id and get_job(job_id) and get_job(job_id).get("status") == "paused":
        update_job(job_id, status="running", note="Resumed")
async def wait_job_if_paused(job_id):
    while job_pause_requested(job_id):
        update_job(job_id, status="paused", note="Paused by user")
        await asyncio.sleep(1)
    if job_id and get_job(job_id) and get_job(job_id).get("status") == "paused":
        update_job(job_id, status="running", note="Resumed")
async def run_tracked_blocking_job(kind, title, func, *args, total=1, current="", source_ref="",
metadata=None, **kwargs):
    """Run a blocking operation while recording it in the Jobs page.
    This is intentionally lightweight: the work still happens in the request that
    started it, but the Jobs screen can now show the operation while it is
    running and retain it in recent history after it completes.
    """
    job_id = create_job(kind, title, total=total, source_ref=source_ref, metadata=metadata or {})
    update_job(job_id, status="running", control="running", current=current or title, processed=0,
    total=total, note="Started")
    try:
        result = await asyncio.to_thread(func, *args, **kwargs)
        update_job(job_id, processed=total or 1, total=total or 1, current=current or title, errors=0)
        finish_job(job_id, "complete", "Complete")
        return result, get_job(job_id)
    except Exception as e:
        logger.exception("Tracked job failed: %s", title)
        update_job(job_id, errors=1, current=current or title)
        finish_job(job_id, "failed", str(e))
        raise
def attach_job_to_result(result, job):
    if isinstance(result, dict):
        result = dict(result)
        result["job"] = job
        result.setdefault("job_id", job.get("id") if job else "")
        return result
    return {"ok": True, "result": result, "job": job, "job_id": job.get("id") if job else ""}
def job_summary():
    with job_lock:
        all_jobs = sorted((dict(j) for j in jobs.values()), key=lambda x: x.get("updated_at", ""),
        reverse=True)
    active = [j for j in all_jobs if j.get("status") in {"queued", "running", "paused", "cancel_requested"}]
    recent = all_jobs[:100]
    counts = {}
    for job in all_jobs:
        counts[job.get("status", "unknown")] = counts.get(job.get("status", "unknown"), 0) + 1
    return {"active": active, "recent": recent, "counts": counts, "engine": job_engine_summary()}
queued_job_ids = deque([job_id for job_id, job in jobs.items() if job.get("status") == "queued"])
job_engine_state = {
    "enabled": True,
    "worker_running": False,
    "started_at": "",
    "last_tick": "",
    "current_job_id": "",
    "queued": len(queued_job_ids),
}
def job_engine_summary():
    job_engine_state["queued"] = len(queued_job_ids)
    return dict(job_engine_state)
def enqueue_system_job(task, title=None, payload=None):
    payload = payload or {}
    titles = {
        "index_sweep": "Sweep missing index entries",
        "duplicate_scan": "Scan duplicate candidates",
        "audio_missing": "Analyze missing audio metadata",
        "reprocess_all": "Reprocess all indexed files",
    }
    if task not in titles:
        raise ValueError("Unsupported queued job task")
    job = create_job("system", title or titles[task], total=0, metadata={"task": task, "payload": payload},
    status="queued")
    queued_job_ids.append(job)
    job_engine_state["queued"] = len(queued_job_ids)
    persist_jobs()
    logger.info("Queued system job %s: %s", job, task)
    return get_job(job)
async def run_queued_system_job(job_id):
    job = get_job(job_id)
    if not job:
        return
    metadata = job.get("metadata") or {}
    task = metadata.get("task")
    payload = metadata.get("payload") or {}
    update_job(job_id, status="running", control="running", note=f"Running {task}")
    try:
        if task == "index_sweep":
            update_job(job_id, current="Checking indexed files", total=len(tags), processed=0)
            result = await asyncio.to_thread(sweep_missing_index_entries, "job_engine")
            update_job(job_id, processed=result.get("checked", 0), total=result.get("checked", len(tags)),
            errors=result.get("errors", 0), metadata={**metadata, "result": result})
            finish_job(job_id, "complete", f"Sweep complete: removed {result.get('removed', 0)} missing records")
        elif task == "duplicate_scan":
            limit = int(payload.get("limit", 1000) or 1000)
            update_job(job_id, current="Building duplicate report", total=limit, processed=0)
            result = await asyncio.to_thread(build_duplicate_report, True, max(0, min(limit, 5000)))
            group_count = len(result.get("groups", [])) if isinstance(result, dict) else 0
            update_job(job_id, processed=limit, total=limit, metadata={**metadata, "result_summary":
            {"groups": group_count}})
            finish_job(job_id, "complete", f"Duplicate scan complete: {group_count} groups")
        elif task == "audio_missing":
            limit = max(1, min(int(payload.get("limit", 50) or 50), 500))
            transcribe = bool(payload.get("transcribe", False))
            processed = 0
            errors = 0
            update_job(job_id, total=limit, processed=0, current="Scanning missing audio metadata")
            for rel_path in list(tags.keys()):
                await wait_job_if_paused(job_id)
                if job_cancel_requested(job_id):
                    finish_job(job_id, "cancelled", "Cancelled by user")
                    return
                audio = (metadata_store.get(rel_path, {}) or {}).get("audio") or {}
                if audio and audio.get("status") == "complete":
                    continue
                try:
                    update_job(job_id, current=rel_path, processed=processed, errors=errors)
                    await asyncio.to_thread(analyze_audio_for_path, rel_path, False, transcribe)
                    processed += 1
                except Exception:
                    errors += 1
                    logger.exception("Queued audio analysis failed for %s", rel_path)
                if processed >= limit:
                    break
            update_job(job_id, processed=processed, total=max(limit, processed), errors=errors)
            finish_job(job_id, "complete", f"Audio analysis complete: {processed} processed, {errors} errors")
        elif task == "reprocess_all":
            paths = sorted(tags.keys())
            update_job(job_id, total=len(paths), processed=0, current="Preparing reprocess")
            errors = 0
            for idx, rel_path in enumerate(paths, start=1):
                await wait_job_if_paused(job_id)
                if job_cancel_requested(job_id):
                    finish_job(job_id, "cancelled", "Cancelled by user")
                    return
                try:
                    update_job(job_id, current=rel_path, processed=idx - 1, errors=errors, note="Reprocessing file")
                    await asyncio.to_thread(reprocess_file, rel_path)
                except Exception:
                    errors += 1
                    logger.exception("Queued reprocess failed for %s", rel_path)
                update_job(job_id, processed=idx, errors=errors)
            finish_job(job_id, "complete", f"Reprocess complete with {errors} errors")
        else:
            finish_job(job_id, "failed", f"Unsupported task: {task}")
    except Exception as e:
        logger.exception("Queued system job failed: %s", job_id)
        finish_job(job_id, "failed", str(e))
async def job_worker_loop():
    if job_engine_state.get("worker_running"):
        return
    job_engine_state["worker_running"] = True
    job_engine_state["started_at"] = datetime.now().isoformat(timespec="seconds")
    logger.info("Persistent job worker started")
    while True:
        job_engine_state["last_tick"] = datetime.now().isoformat(timespec="seconds")
        try:
            if queued_job_ids:
                job_id = queued_job_ids.popleft()
                job_engine_state["current_job_id"] = job_id
                job_engine_state["queued"] = len(queued_job_ids)
                job = get_job(job_id)
                if job and job.get("control") != "cancel_requested" and job.get("status") == "queued":
                    await run_queued_system_job(job_id)
                elif job and job.get("control") == "cancel_requested":
                    finish_job(job_id, "cancelled", "Cancelled before start")
                job_engine_state["current_job_id"] = ""
            else:
                await asyncio.sleep(2)
        except Exception:
            logger.exception("Job worker loop error")
            await asyncio.sleep(5)
# ================= INDEX SWEEP STATUS =================
# Runs automatically every six hours (four times daily) and can also be
# triggered manually from Administration -> System.
INDEX_SWEEP_INTERVAL_SECONDS = 6 * 60 * 60
index_sweep_lock = Lock()
index_sweep_status = {
    "running": False,
    "status": "idle",
    "mode": "",
    "started_at": "",
    "finished_at": "",
    "last_run_at": "",
    "next_run_at": "",
    "total_indexed_before": 0,
    "checked": 0,
    "removed": 0,
    "kept": 0,
    "errors": 0,
    "current": "",
    "details": [],
}
def update_next_index_sweep_time(delay_seconds=INDEX_SWEEP_INTERVAL_SECONDS):
    try:
        next_ts = datetime.now().timestamp() + float(delay_seconds)
        index_sweep_status["next_run_at"] = datetime.fromtimestamp(next_ts).isoformat(timespec="seconds")
    except Exception:
        index_sweep_status["next_run_at"] = ""
def set_download_status(message_id, payload):
    download_status[str(message_id)] = payload
def clear_download_status(message_id):
    download_status.pop(str(message_id), None)
def record_download_history(item):
    download_history.appendleft(item)
# ================= MODEL SETUP =================
device = "cpu"
logger.info("Using device: %s", device)
logger.info("Loading CLIP model...")
clip_model, preprocess = clip.load("ViT-B/32", device=device)
logger.info("Loading YOLO model...")
yolo_model = YOLO("yolov8n.pt")
try:
    yolo_model.to("cpu")
except Exception:
    pass
text_tokens = {
    tag: clip.tokenize(prompts).to(device)
    for tag, prompts in PROMPTS.items()
}
# ================= NUDENET CLASSIFIER =================
_nudenet_detector = None
_nudenet_load_attempted = False

def _get_nudenet():
    """Lazy-load NudeNet detector. Returns None if NudeNet is not installed."""
    global _nudenet_detector, _nudenet_load_attempted
    if _nudenet_load_attempted:
        return _nudenet_detector
    _nudenet_load_attempted = True
    try:
        from nudenet import NudeDetector
        _nudenet_detector = NudeDetector()
        logger.info("NudeNet detector loaded successfully.")
    except Exception as e:
        logger.warning("NudeNet not available — adult content classification disabled: %s", e)
        _nudenet_detector = None
    return _nudenet_detector

# Map NudeNet labels to our taxonomy vocabulary.
_NUDENET_SCENE_COMPOSITION_RULES = [
    # (label_substring, performer count signal)
    ("MALE_GENITALIA",    "male_detected"),
    ("FEMALE_GENITALIA",  "female_detected"),
    ("FEMALE_BREAST",     "female_detected"),
    ("MALE_BREAST",       "male_detected"),
    ("BUTTOCKS",          "body_detected"),
    ("ANUS",              "body_detected"),
    ("FEET",              "body_detected"),
    ("ARMPITS",           "body_detected"),
    ("BELLY",             "body_detected"),
    ("FACE_FEMALE",       "female_face"),
    ("FACE_MALE",         "male_face"),
]

def _nudenet_signals_from_detections(detections, threshold):
    """Convert raw NudeNet detections into a structured ai_signals dict.

    Args:
        detections: list of dicts from NudeDetector.detect(), each with 'class' and 'score' keys.
        threshold: float confidence threshold; detections below this are ignored.

    Returns:
        dict with scene_composition, performer_presentations, production_type, pov_type, setting keys,
        or None if no detections exceed the threshold.
    """
    if not detections:
        return None

    male_signals   = 0
    female_signals = 0
    body_signals   = 0

    for det in detections:
        score = float(det.get("score", 0.0))
        if score < threshold:
            continue
        label = str(det.get("class", "")).upper()
        if "MALE_GENITALIA" in label or "FACE_MALE" in label:
            male_signals += 1
        elif "FEMALE_GENITALIA" in label or "FEMALE_BREAST" in label or "FACE_FEMALE" in label:
            female_signals += 1
        else:
            body_signals += 1

    if male_signals == 0 and female_signals == 0 and body_signals == 0:
        return None  # nothing above threshold

    # Derive scene_composition
    if male_signals > 0 and female_signals > 0:
        scene_comp = "group_mixed" if (male_signals + female_signals) > 2 else "duo_mf"
    elif male_signals > 1:
        scene_comp = "group_all_m" if male_signals > 2 else "duo_mm"
    elif female_signals > 1:
        scene_comp = "group_all_f" if female_signals > 2 else "duo_ff"
    elif male_signals == 1 and female_signals == 0:
        scene_comp = "solo_m"
    elif female_signals == 1 and male_signals == 0:
        scene_comp = "solo_f"
    else:
        scene_comp = "unknown"

    # Derive performer_presentations list
    presentations = []
    if male_signals > 0:
        presentations.append("male")
    if female_signals > 0:
        presentations.append("female")

    return {
        "scene_composition": scene_comp,
        "performer_presentations": presentations,
        "production_type": None,   # not classifiable from NudeNet alone
        "pov_type": None,          # not classifiable from NudeNet alone
        "setting": None,           # not classifiable from NudeNet alone
        "nudenet_detections_above_threshold": male_signals + female_signals + body_signals,
    }

def run_nudenet_analysis(frame_paths, threshold=None):
    """Run NudeNet on a sample of extracted frame paths.

    Returns an ai_signals dict, or None if NudeNet is unavailable or no
    detections exceed the configured threshold.
    """
    if threshold is None:
        threshold = float(settings_data.get("nudenet_threshold", DEFAULT_NUDENET_THRESHOLD))
    detector = _get_nudenet()
    if detector is None:
        return None
    # Sample up to 6 evenly-spaced frames to keep CPU cost reasonable.
    sample = frame_paths[::max(1, len(frame_paths) // 6)][:6]
    all_detections = []
    for frame_path in sample:
        try:
            result = detector.detect(frame_path)
            if isinstance(result, list):
                all_detections.extend(result)
        except Exception as e:
            logger.warning("NudeNet detection failed for frame %s: %s", frame_path, e)
    return _nudenet_signals_from_detections(all_detections, threshold)
# ================= FRAME EXTRACTION =================
def extract_frames(video_path):
    folder = os.path.join(FRAME_PATH, os.path.basename(video_path))
    os.makedirs(folder, exist_ok=True)
    for existing in os.listdir(folder):
        if existing.lower().endswith(".jpg"):
            try:
                os.remove(os.path.join(folder, existing))
            except Exception:
                pass
    pattern = os.path.join(folder, "frame_%03d.jpg")
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        video_path,
        "-vf",
        "fps=2",
        "-t",
        "6",
        pattern,
    ]
    logger.info("Extracting frames from %s", video_path)
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    frames = sorted(
        os.path.join(folder, f)
        for f in os.listdir(folder)
        if f.lower().endswith(".jpg")
    )
    return frames
# ================= PREVIEW GENERATION =================
def generate_thumbnail(frame_paths, rel_path):
    if not frame_paths:
        return None
    thumb_path = thumb_abs_path_for_file(rel_path)
    os.makedirs(os.path.dirname(thumb_path), exist_ok=True)
    try:
        img = Image.open(frame_paths[0]).convert("RGB")
        img.thumbnail((480, 270))
        img.save(thumb_path, format="JPEG", quality=82, optimize=True)
        logger.info("Generated thumbnail %s", thumb_path)
        return thumb_path
    except Exception as e:
        logger.exception("Failed to generate thumbnail for %s: %s", rel_path, e)
        return None
def generate_preview_gif(frame_paths, rel_path):
    if not frame_paths:
        return None
    preview_path = preview_abs_path_for_file(rel_path)
    os.makedirs(os.path.dirname(preview_path), exist_ok=True)
    frames = []
    for p in frame_paths[:8]:
        try:
            img = Image.open(p).convert("RGB")
            img.thumbnail((320, 180))
            frames.append(img)
        except Exception as e:
            logger.warning("Failed to use frame %s in preview GIF: %s", p, e)
    if not frames:
        return None
    try:
        first, rest = frames[0], frames[1:]
        first.save(
            preview_path,
            format="GIF",
            save_all=True,
            append_images=rest,
            duration=350,
            loop=0,
            optimize=True,
        )
        logger.info("Generated preview GIF %s", preview_path)
        return preview_path
    except Exception as e:
        logger.exception("Failed to generate preview GIF for %s: %s", rel_path, e)
        return None
# ================= SCENE / TIMELINE DETECTION =================
def parse_scene_times_from_ffmpeg(stderr_text):
    times = []
    if not stderr_text:
        return times
    for match in re.finditer(r"pts_time:([0-9]+(?:\.[0-9]+)?)", stderr_text):
        try:
            times.append(float(match.group(1)))
        except Exception:
            pass
    return times
def normalize_scene_times(times, duration, min_gap=6.0, max_scenes=32):
    cleaned = []
    try:
        duration = float(duration or 0.0)
    except Exception:
        duration = 0.0
    try:
        min_gap = max(1.0, float(min_gap or 6.0))
    except Exception:
        min_gap = 6.0
    try:
        max_scenes = max(2, min(int(max_scenes or 32), 120))
    except Exception:
        max_scenes = 32
    for value in sorted(set(round(float(t), 2) for t in times if t is not None)):
        if value < 0:
            continue
        if duration > 0 and value > duration:
            continue
        if cleaned and value - cleaned[-1] < min_gap:
            continue
        cleaned.append(value)
        if len(cleaned) >= max_scenes:
            break
    if not cleaned or cleaned[0] > 1.0:
        cleaned.insert(0, 0.0)
    # For videos where scene detection finds almost nothing, create useful
    # chapter-like markers so the detail page still becomes navigable.
    if duration >= 120 and len(cleaned) < 4:
        target_count = min(max_scenes, 8 if duration < 1800 else 12)
        uniform = [round((duration * idx) / target_count, 2) for idx in range(target_count)]
        merged = sorted(set(cleaned + uniform))
        cleaned = []
        for value in merged:
            if cleaned and value - cleaned[-1] < min_gap:
                continue
            cleaned.append(value)
            if len(cleaned) >= max_scenes:
                break
    return cleaned[:max_scenes]
def extract_scene_detection_times(video_path, threshold=0.35, max_scenes=32, min_gap=6.0):
    try:
        threshold = max(0.05, min(float(threshold or 0.35), 0.95))
    except Exception:
        threshold = 0.35
    try:
        max_scenes = max(2, min(int(max_scenes or 32), 120))
    except Exception:
        max_scenes = 32
    duration = get_video_duration_seconds(video_path)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-i", video_path,
        "-vf", f"select='gt(scene,{threshold})',showinfo",
        "-an",
        "-f", "null",
        "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        times = parse_scene_times_from_ffmpeg(result.stderr or "")
    except subprocess.TimeoutExpired:
        logger.warning("Scene detection timed out for %s", video_path)
        times = []
    except Exception as e:
        logger.warning("Scene detection failed for %s: %s", video_path, e)
        times = []
    return normalize_scene_times(times, duration, min_gap=min_gap, max_scenes=max_scenes), duration
def extract_scene_thumbnail(video_path, rel_path, scene_index, timestamp):
    thumb_path = scene_abs_path_for_file(rel_path, scene_index)
    ensure_directory(os.path.dirname(thumb_path))
    cmd = [
        "ffmpeg",
        "-y",
        "-ss", str(max(0.0, float(timestamp or 0.0))),
        "-i", video_path,
        "-frames:v", "1",
        "-vf", "scale=360:-2",
        "-q:v", "4",
        thumb_path,
    ]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
        if os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 0:
            return thumb_path
    except Exception as e:
        logger.warning("Could not extract scene thumbnail for %s at %.2fs: %s", rel_path, float(timestamp or
        0.0), e)
    return None
def build_scene_timeline(rel_path, force=False, threshold=0.35, max_scenes=32, min_gap=6.0):
    if rel_path not in tags:
        raise FileNotFoundError("File not found in index")
    meta = metadata_store.setdefault(rel_path, {})
    existing = meta.get("scene_timeline")
    if existing and isinstance(existing, dict) and existing.get("scenes") and not force:
        return existing
    abs_path = find_existing_file(rel_path)
    if not abs_path or not os.path.exists(abs_path):
        raise FileNotFoundError("Video file not found on disk")
    # Clear stale thumbnails before regenerating.
    if force:
        delete_scene_thumbnails_for_file(rel_path)
    scene_times, duration = extract_scene_detection_times(
        abs_path,
        threshold=threshold,
        max_scenes=max_scenes,
        min_gap=min_gap,
    )
    scenes = []
    for idx, timestamp in enumerate(scene_times, start=1):
        next_time = scene_times[idx] if idx < len(scene_times) else duration
        thumb = extract_scene_thumbnail(abs_path, rel_path, idx, timestamp)
        scenes.append({
            "index": idx,
            "time": float(timestamp),
            "time_human": format_duration(timestamp),
            "end_time": float(next_time or 0.0),
            "end_time_human": format_duration(next_time),
            "duration": max(0.0, float(next_time or 0.0) - float(timestamp or 0.0)) if next_time else 0.0,
            "duration_human": format_duration(max(0.0, float(next_time or 0.0) - float(timestamp or 0.0))) if
            next_time else "—",
            "title": "Opening" if idx == 1 and float(timestamp or 0.0) <= 0.5 else f"Scene {idx}",
            "thumbnail_url": scene_url_for_file(rel_path, idx) if thumb else f"/thumbs/{thumb_rel_path_for_file(rel_path)}",
            "thumbnail_file": os.path.basename(thumb) if thumb else "",
            "source": "scene-detect" if len(scene_times) > 1 else "fallback",
        })
    timeline = {
        "generated_at": now_stamp(),
        "version": APP_VERSION,
        "threshold": float(threshold),
        "min_gap": float(min_gap),
        "max_scenes": int(max_scenes),
        "duration": float(duration or 0.0),
        "duration_human": format_duration(duration),
        "scene_count": len(scenes),
        "scenes": scenes,
    }
    meta["scene_timeline"] = timeline
    meta["scene_timeline_generated_at"] = timeline["generated_at"]
    save_state()
    logger.info("Scene timeline generated for %s scenes=%d", rel_path, len(scenes))
    return timeline
def cached_scene_timeline(rel_path):
    meta = metadata_store.get(rel_path, {}) or {}
    timeline = meta.get("scene_timeline")
    if isinstance(timeline, dict):
        return timeline
    return {
        "generated_at": "",
        "version": APP_VERSION,
        "threshold": 0.35,
        "min_gap": 6.0,
        "max_scenes": 32,
        "duration": float(meta.get("duration") or 0.0),
        "duration_human": format_duration(meta.get("duration") or 0.0),
        "scene_count": 0,
        "scenes": [],
    }
# ================= EMBEDDING =================
def compute_embedding(images):
    if not images:
        raise RuntimeError("No frames available for embedding generation")
    tensors = torch.cat([preprocess(img).unsqueeze(0) for img in images]).to(device)
    with torch.no_grad():
        feats = clip_model.encode_image(tensors)
    feats = feats / feats.norm(dim=-1, keepdim=True)
    mean_feat = feats.mean(dim=0)
    mean_feat = mean_feat / mean_feat.norm()
    return mean_feat.cpu().numpy().astype("float32")
# ================= TAGGING =================
def fused_tag(video_path, rel_path=None):
    frame_paths = extract_frames(video_path)
    if not frame_paths:
        raise RuntimeError(f"No frames could be extracted from {video_path}")
    if rel_path:
        generate_thumbnail(frame_paths, rel_path)
        generate_preview_gif(frame_paths, rel_path)
    # ── NudeNet classification ───────────────────────────────────────────────
    if rel_path:
        try:
            ai_signals = run_nudenet_analysis(frame_paths)
            save_ai_signals(rel_path, ai_signals)
            if ai_signals:
                logger.info(
                    "NudeNet classification for %s => scene_composition=%s presentations=%s",
                    rel_path,
                    ai_signals.get("scene_composition"),
                    ai_signals.get("performer_presentations"),
                )
            else:
                logger.info("NudeNet classification for %s => no detections above threshold", rel_path)
        except Exception as e:
            logger.warning("NudeNet analysis failed for %s: %s", rel_path, e)
    images = []
    for p in frame_paths:
        try:
            images.append(Image.open(p).convert("RGB"))
        except Exception as e:
            logger.warning("Failed to load frame %s: %s", p, e)
    if not images:
        raise RuntimeError(f"No readable frame images for {video_path}")
    emb = compute_embedding(images)
    raw_score_map = {tag: 0.0 for tag in PROMPTS}
    for img in images:
        image_tensor = preprocess(img).unsqueeze(0).to(device)
        with torch.no_grad():
            image_features = clip_model.encode_image(image_tensor)
            image_features = image_features / image_features.norm(dim=-1, keepdim=True)
            for tag, tokens in text_tokens.items():
                text_features = clip_model.encode_text(tokens)
                text_features = text_features / text_features.norm(dim=-1, keepdim=True)
                similarity = (image_features @ text_features.T).squeeze(0)
                raw_score_map[tag] += float(similarity.mean().item())
        try:
            results = yolo_model.predict(img, verbose=False, device="cpu")
            people_count = 0
            for r in results:
                if hasattr(r, "boxes") and r.boxes is not None and r.boxes.cls is not None:
                    for c in r.boxes.cls:
                        if int(c) == 0:
                            people_count += 1
            if people_count > 0:
                raw_score_map["Adults"] += min(0.05 * people_count, 0.20)
            if people_count >= 2:
                raw_score_map["Gay"] += 0.015
                raw_score_map["Straight"] += 0.015
                raw_score_map["Bisexual"] += 0.015
        except Exception as e:
            logger.warning("YOLO analysis warning for %s: %s", video_path, e)
    frame_count = max(len(images), 1)
    averaged_scores = {tag: (raw_score_map[tag] / frame_count) for tag in raw_score_map}
    feedback_bias = compute_feedback_bias()
    weighted_scores = {}
    for tag in averaged_scores:
        weighted = averaged_scores[tag] * TAG_WEIGHTS.get(tag, 1.0)
        weighted += feedback_bias.get(tag, 0.0)
        weighted_scores[tag] = normalize_score(weighted)
    duration = get_video_duration_seconds(video_path)
    is_clip = duration <= 299.0
    is_movie = duration >= 300.0
    weighted_scores["Clip"] = 1.0 if is_clip else 0.0
    weighted_scores["Movie"] = 1.0 if is_movie else 0.0
    selected = []
    for tag, score in weighted_scores.items():
        if tag in ("Clip", "Movie"):
            continue
        threshold = TAG_THRESHOLDS.get(tag, 0.22)
        if score >= threshold:
            selected.append(tag)
    if is_clip:
        selected.append("Clip")
    elif is_movie:
        selected.append("Movie")
    if not selected:
        selected = ["Clip"]
    logger.info(
        "Tagging complete for %s => tags=%s duration=%.2fs clip=%s movie=%s",
        video_path,
        selected,
        duration,
        is_clip,
        is_movie,
    )
    # Write ai_inferred label rows for every tag the pipeline assigned.
    if rel_path and selected:
        try:
            write_ai_tag_labels(rel_path, selected)
        except Exception as _lbl_err:
            logger.warning("Failed to write ai tag labels for %s: %s", rel_path, _lbl_err)
    return selected, weighted_scores, emb.tolist()
# ================= FILE LOOKUP =================
def find_existing_file(rel_path):
    rel_path = str(rel_path or "").strip()
    if not rel_path:
        return None
    # Absolute paths may appear in older state files or during migrations.
    if os.path.isabs(rel_path) and os.path.exists(rel_path):
        return rel_path
    for root in candidate_storage_roots():
        try:
            candidate = safe_abs_path(rel_path, root)
            if os.path.exists(candidate):
                return candidate
        except Exception:
            pass
    return None
# ================= REPROCESS =================
def reprocess_file(rel_path):
    global faiss_index, faiss_keys
    abs_path = find_existing_file(rel_path)
    if not abs_path:
        raise FileNotFoundError(rel_path)
    logger.info("Manual reprocess started for %s", rel_path)
    t, s, emb = fused_tag(abs_path, rel_path=rel_path)
    tags[rel_path] = t
    scores_store[rel_path] = s
    embeddings_store[rel_path] = emb
    save_state()
    faiss_index, faiss_keys = build_faiss_index()
    logger.info("Manual reprocess finished for %s", rel_path)
async def reprocess_all_files_job():
    global bulk_job, faiss_index, faiss_keys
    if bulk_job["running"]:
        return
    paths = sorted(tags.keys())
    job_id = create_job("reprocess", "Reprocess all indexed files", total=len(paths))
    bulk_job = {
        "running": True,
        "status": "running",
        "processed": 0,
        "total": len(paths),
        "current": "",
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "finished_at": "",
        "errors": 0,
        "mode": "reprocess_all",
        "job_id": job_id,
    }
    logger.info("Bulk reprocess job started for %d files", len(paths))
    try:
        for rel_path in paths:
            await wait_job_if_paused(job_id)
            if job_cancel_requested(job_id):
                bulk_job["status"] = "cancelled"
                bulk_job["finished_at"] = datetime.now().isoformat(timespec="seconds")
                finish_job(job_id, "cancelled", "Cancelled by user")
                logger.warning("Bulk reprocess job cancelled")
                return
            bulk_job["current"] = rel_path
            update_job(job_id, current=rel_path, processed=bulk_job["processed"], errors=bulk_job["errors"],
            note="Reprocessing file")
            try:
                await asyncio.to_thread(reprocess_file, rel_path)
            except Exception:
                bulk_job["errors"] += 1
                logger.exception("Bulk reprocess failed for %s", rel_path)
            bulk_job["processed"] += 1
            update_job(job_id, processed=bulk_job["processed"], errors=bulk_job["errors"], current=rel_path)
        faiss_index, faiss_keys = build_faiss_index()
        bulk_job["status"] = "complete"
        bulk_job["finished_at"] = datetime.now().isoformat(timespec="seconds")
        finish_job(job_id, "complete", "Reprocess complete")
        logger.info("Bulk reprocess job completed")
    except Exception as e:
        bulk_job["status"] = "failed"
        bulk_job["finished_at"] = datetime.now().isoformat(timespec="seconds")
        finish_job(job_id, "failed", str(e))
        raise
    finally:
        bulk_job["running"] = False
# ================= MIGRATION =================
def migrate_library_files():
    source_root = os.path.abspath(BASE_PATH)
    target_root = os.path.abspath(get_download_directory())
    if source_root == target_root:
        return {
            "moved": 0,
            "skipped": 0,
            "missing": 0,
            "failed": 0,
            "details": ["Source and destination are the same; nothing to migrate."]
        }
    ensure_directory(target_root)
    moved = 0
    skipped = 0
    missing = 0
    failed = 0
    details = []
    indexed_paths = sorted(tags.keys())
    for rel_path in indexed_paths:
        try:
            src = safe_abs_path(rel_path, source_root)
            dst = safe_abs_path(rel_path, target_root)
            if not os.path.exists(src):
                if os.path.exists(dst):
                    skipped += 1
                    details.append(f"Skipped already present in destination: {rel_path}")
                else:
                    missing += 1
                    details.append(f"Missing in source and destination: {rel_path}")
                continue
            if os.path.exists(dst):
                skipped += 1
                details.append(f"Skipped destination already exists: {rel_path}")
                continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(src, dst)
            moved += 1
            details.append(f"Moved: {rel_path}")
            logger.info("Migrated file %s -> %s", src, dst)
        except Exception as e:
            failed += 1
            details.append(f"Failed: {rel_path} ({e})")
            logger.exception("Migration failed for %s", rel_path)
    return {
        "moved": moved,
        "skipped": skipped,
        "missing": missing,
        "failed": failed,
        "details": details[:200]
    }
# ================= FAISS =================
def build_faiss_index():
    keys = list(embeddings_store.keys())
    if not keys:
        return None, []
    valid_keys = []
    vectors = []
    for key in keys:
        vec = embeddings_store.get(key)
        if not isinstance(vec, list) or not vec:
            continue
        try:
            arr = np.array(vec, dtype="float32")
            valid_keys.append(key)
            vectors.append(arr)
        except Exception:
            logger.warning("Skipping invalid embedding for %s", key)
    if not vectors:
        return None, []
    vecs = np.vstack(vectors).astype("float32")
    index = faiss.IndexFlatIP(vecs.shape[1])
    index.add(vecs)
    n = len(valid_keys)
    logger.info("FAISS index rebuilt with %d vectors", n)
    if n > 5000:
        logger.warning(
            "FAISS index holds %d vectors — rebuild time may be noticeable on restart. "
            "Consider enabling FAISS disk persistence (P2 roadmap item) to skip the rebuild.",
            n,
        )
    return index, valid_keys
_n_embeddings = len(embeddings_store)
if _n_embeddings > 0:
    logger.info("Building FAISS index from %d stored embeddings (in-memory only — rebuilt on every restart)…", _n_embeddings)
faiss_index, faiss_keys = build_faiss_index()
# ================= SEARCH =================
def search(query, filter_tag=None, min_score=0.0, top_k=250):
    global faiss_index, faiss_keys
    if not query or not query.strip():
        raise RuntimeError("Empty search query")
    if faiss_index is None or not faiss_keys:
        return []
    tokens = clip.tokenize([query]).to(device)
    with torch.no_grad():
        q_vec = clip_model.encode_text(tokens)
        q_vec = q_vec / q_vec.norm(dim=-1, keepdim=True)
    distances, indices = faiss_index.search(
        q_vec.cpu().numpy().astype("float32"),
        min(top_k * 3, len(faiss_keys))
    )
    results = []
    for idx, semantic_score in zip(indices[0], distances[0]):
        if idx < 0 or idx >= len(faiss_keys):
            continue
        path = faiss_keys[idx]
        file_tags = get_effective_tags(path)
        if filter_tag and filter_tag not in file_tags:
            continue
        if float(semantic_score) < float(min_score):
            continue
        tag_scores = scores_store.get(path, {})
        boost = (sum(tag_scores.values()) / len(tag_scores) if tag_scores else 0.0)
        final_score = float(semantic_score) * 0.8 + float(boost) * 0.2
        results.append(
            {
                "path": path,
                "semantic_score": float(semantic_score),
                "final_score": float(final_score),
                "tags": file_tags,
                "score_map": tag_scores,
                "manual_override": get_manual_override(path) is not None,
            }
        )
    results.sort(key=lambda x: x["final_score"], reverse=True)
    return results[:top_k]
# ================= VIDEO SOURCE HELPERS =================
def message_unique_id(message):
    peer = getattr(message, "peer_id", None)
    try:
        peer_key = str(utils.get_peer_id(peer)) if peer is not None else "unknown"
    except Exception:
        peer_key = str(getattr(message, "chat_id", None) or "unknown")
    return f"{peer_key}:{message.id}"
def is_message_already_seen(message):
    return message.id in state_ids or message_unique_id(message) in state_ids
def mark_message_seen(message):
    state_ids.add(message_unique_id(message))
async def resolve_video_source(ref):
    ref = str(ref or "").strip()
    if not ref or ref.lower() in {"me", "saved", "saved messages", "self"}:
        return await client.get_entity("me")
    # Accept numeric peer IDs as well as @usernames, channel links, and names Telethon can resolve.
    try:
        if re.fullmatch(r"-?\d+", ref):
            return await client.get_entity(int(ref))
    except Exception:
        pass
    return await client.get_entity(ref)
async def list_dialog_sources(limit=250):
    dialogs = [{
        "id": "me",
        "ref": "me",
        "title": "Saved Messages",
        "username": None,
        "kind": "saved",
    }]
    seen_refs = {"me"}
    async for dialog in client.iter_dialogs(limit=limit):
        entity = dialog.entity
        try:
            peer_id = utils.get_peer_id(entity)
        except Exception:
            peer_id = getattr(entity, "id", None)
        username = getattr(entity, "username", None)
        title = dialog.name or username or str(peer_id)
        ref = f"@{username}" if username else str(peer_id)
        if ref.lower() in seen_refs:
            continue
        seen_refs.add(ref.lower())
        kind = "chat"
        if getattr(dialog, "is_channel", False):
            kind = "channel"
        elif getattr(dialog, "is_group", False):
            kind = "group"
        elif getattr(dialog, "is_user", False):
            kind = "user"
        dialogs.append({
            "id": str(peer_id),
            "ref": ref,
            "title": title,
            "username": username,
            "kind": kind,
        })
    dialogs.sort(key=lambda x: (x.get("kind", ""), x.get("title", "").lower()))
    return dialogs
async def configured_source_peer_ids():
    peer_ids = set()
    for ref in enabled_video_sources():
        try:
            entity = await resolve_video_source(ref)
            peer_ids.add(str(utils.get_peer_id(entity)))
        except Exception as e:
            logger.warning("Could not resolve configured source %s: %s", ref, e)
    return peer_ids
async def event_source_is_enabled(event):
    peer = getattr(event.message, "peer_id", None)
    try:
        event_peer_id = str(utils.get_peer_id(peer))
    except Exception:
        event_peer_id = str(getattr(event.message, "chat_id", ""))
    peer_ids = await configured_source_peer_ids()
    return event_peer_id in peer_ids
async def entity_title(entity, fallback):
    if str(fallback).lower() in {"me", "saved", "saved messages", "self"}:
        return "Saved Messages"
    return (
        getattr(entity, "title", None) or " ".join(filter(None, [getattr(entity, "first_name", None), getattr(entity, "last_name",
        None)])).strip() or getattr(entity, "username", None)
        or str(fallback)
    )
def iso_or_blank(value):
    try:
        if value:
            return value.isoformat(timespec="seconds")
    except TypeError:
        try:
            return value.isoformat()
        except Exception:
            return ""
    except Exception:
        return ""
    return ""
def entity_identity(entity):
    if not entity:
        return {}
    try:
        entity_id = utils.get_peer_id(entity)
    except Exception:
        entity_id = getattr(entity, "id", "")
    title = (
        getattr(entity, "title", None) or " ".join(filter(None, [getattr(entity, "first_name", None), getattr(entity, "last_name",
        None)])).strip() or getattr(entity, "username", None)
        or str(entity_id or "")
    )
    return {
        "id": str(entity_id or ""),
        "raw_id": str(getattr(entity, "id", "") or ""),
        "title": title,
        "username": getattr(entity, "username", None) or "",
        "first_name": getattr(entity, "first_name", None) or "",
        "last_name": getattr(entity, "last_name", None) or "",
        "bot": bool(getattr(entity, "bot", False)),
        "verified": bool(getattr(entity, "verified", False)),
        "scam": bool(getattr(entity, "scam", False)),
        "fake": bool(getattr(entity, "fake", False)),
    }
def telegram_message_link(source_ref, chat_entity, message_id):
    try:
        username = getattr(chat_entity, "username", None) or ""
        if username and message_id:
            return f"https://t.me/{username}/{message_id}"
    except Exception:
        pass
    try:
        peer_id = str(utils.get_peer_id(chat_entity))
        if peer_id.startswith("-100") and message_id:
            return f"https://t.me/c/{peer_id[4:]}/{message_id}"
    except Exception:
        pass
    raw = str(source_ref or "").strip()
    if raw.startswith("@") and message_id:
        return f"https://t.me/{raw[1:]}/{message_id}"
    if raw.startswith("-100") and message_id:
        return f"https://t.me/c/{raw[4:]}/{message_id}"
    return ""
def reaction_value_to_text(reaction):
    if reaction is None:
        return ""
    for attr in ("emoticon", "document_id"):
        val = getattr(reaction, attr, None)
        if val:
            return str(val)
    return str(reaction)
def reaction_summary(message):
    out = []
    reactions = getattr(message, "reactions", None)
    results = getattr(reactions, "results", None) or []
    for item in results:
        out.append({
            "reaction": reaction_value_to_text(getattr(item, "reaction", None)),
            "count": int(getattr(item, "count", 0) or 0),
            "chosen": bool(getattr(item, "chosen", False)),
        })
    return out
def forward_summary(message):
    fwd = getattr(message, "fwd_from", None)
    if not fwd:
        return {}
    return {
        "date": iso_or_blank(getattr(fwd, "date", None)),
        "from_id": str(getattr(fwd, "from_id", "") or ""),
        "from_name": getattr(fwd, "from_name", "") or "",
        "channel_id": str(getattr(fwd, "channel_id", "") or ""),
        "channel_post": str(getattr(fwd, "channel_post", "") or ""),
        "post_author": getattr(fwd, "post_author", "") or "",
        "saved_from_peer": str(getattr(fwd, "saved_from_peer", "") or ""),
        "saved_from_msg_id": str(getattr(fwd, "saved_from_msg_id", "") or ""),
    }
def message_file_summary(message):
    f = getattr(message, "file", None)
    doc = getattr(message, "document", None)
    attrs = []
    for attr in getattr(doc, "attributes", []) or []:
        attrs.append({
            "type": attr.__class__.__name__,
            "duration": getattr(attr, "duration", None),
            "w": getattr(attr, "w", None),
            "h": getattr(attr, "h", None),
            "file_name": getattr(attr, "file_name", None),
        })
    return {
        "kind": telegram_message_media_kind(message),
        "name": getattr(f, "name", "") or "",
        "ext": getattr(f, "ext", "") or "",
        "mime_type": getattr(f, "mime_type", "") or getattr(doc, "mime_type", "") or "",
        "size": int(getattr(f, "size", 0) or 0),
        "duration": getattr(f, "duration", None),
        "width": getattr(f, "width", None),
        "height": getattr(f, "height", None),
        "attributes": attrs,
    }
def telegram_metadata_sidecar_rel(rel_path):
    digest = hashlib.sha1(str(rel_path).encode("utf-8")).hexdigest()[:20]
    return os.path.join("telegram_metadata", f"{digest}.json")
def write_telegram_metadata_sidecar(rel_path, payload):
    try:
        rel = telegram_metadata_sidecar_rel(rel_path)
        abs_path = safe_abs_path(rel)
        ensure_directory(os.path.dirname(abs_path))
        save_json(abs_path, payload)
        return rel
    except Exception as e:
        logger.warning("Could not write Telegram metadata sidecar for %s: %s", rel_path, e)
        return ""
async def build_telegram_metadata_payload(message, source_ref="", source_title="", source_profile=None):
    source_profile = source_profile or get_source_profile(source_ref, source_title)
    chat_entity = None
    sender_entity = None
    try:
        chat_entity = await message.get_chat()
    except Exception:
        try:
            chat_entity = await resolve_video_source(source_ref)
        except Exception:
            chat_entity = None
    if source_profile.get("preserve_sender", True):
        try:
            sender_entity = await message.get_sender()
        except Exception:
            sender_entity = None
    text = str(getattr(message, "message", "") or getattr(message, "raw_text", "") or "")
    reply_to = getattr(message, "reply_to", None)
    replies = getattr(message, "replies", None)
    payload = {
        "schema": "telesaved.telegram_metadata.v1",
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "app_version": APP_VERSION,
        "source_ref": source_ref,
        "source_title": source_title or source_profile.get("title") or source_display_name(source_ref),
        "source_profile": {
            "destination_subdir": source_profile.get("destination_subdir", ""),
            "pipeline_mode": source_profile.get("pipeline_mode", "incremental"),
            "media_filter": source_profile.get("media_filter", "videos"),
        },
        "message_id": getattr(message, "id", "") or "",
        "message_key": message_unique_id(message),
        "message_date": iso_or_blank(getattr(message, "date", None)),
        "edit_date": iso_or_blank(getattr(message, "edit_date", None)),
        "post_author": getattr(message, "post_author", "") or "",
        "grouped_id": str(getattr(message, "grouped_id", "") or ""),
        "reply_to_msg_id": str(getattr(reply_to, "reply_to_msg_id", "") or getattr(message, "reply_to_msg_id",
        "") or ""),
        "views": int(getattr(message, "views", 0) or 0),
        "forwards": int(getattr(message, "forwards", 0) or 0),
        "reply_count": int(getattr(replies, "replies", 0) or 0),
        "pinned": bool(getattr(message, "pinned", False)),
        "mentioned": bool(getattr(message, "mentioned", False)),
        "silent": bool(getattr(message, "silent", False)),
        "telegram_link": telegram_message_link(source_ref, chat_entity, getattr(message, "id", "")),
        "chat": entity_identity(chat_entity),
        "sender": entity_identity(sender_entity) if source_profile.get("preserve_sender", True) else {},
        "media": message_file_summary(message),
    }
    if source_profile.get("preserve_captions", True):
        payload["caption"] = text
        payload["caption_length"] = len(text)
    if source_profile.get("preserve_forwards", True):
        payload["forward"] = forward_summary(message)
    if source_profile.get("preserve_replies", True):
        payload["reply"] = {
            "reply_to_msg_id": payload.get("reply_to_msg_id", ""),
            "reply_count": payload.get("reply_count", 0),
        }
    if source_profile.get("preserve_reactions", True):
        payload["reactions"] = reaction_summary(message)
    return payload
def apply_source_pipeline_tags(rel_path, source_profile):
    if not source_profile.get("apply_source_tag", False):
        return []
    tag = str(source_profile.get("source_tag") or "").strip()
    if not tag:
        tag = f"Source: {source_profile.get('title') or source_profile.get('ref') or 'Telegram'}"
    tag = tag[:60]
    current = list(tags.get(rel_path, []))
    if tag not in current:
        current.append(tag)
        tags[rel_path] = current
    return current
def merge_telegram_metadata_into_item(rel_path, metadata_payload, source_profile):
    meta = metadata_store.setdefault(rel_path, {})
    sidecar_rel = write_telegram_metadata_sidecar(rel_path, metadata_payload)
    sender = metadata_payload.get("sender") or {}
    forward = metadata_payload.get("forward") or {}
    caption = metadata_payload.get("caption", "")
    meta.update({
        "telegram": metadata_payload,
        "telegram_metadata_file": sidecar_rel,
        "telegram_metadata_captured_at": metadata_payload.get("captured_at", ""),
        "telegram_link": metadata_payload.get("telegram_link", ""),
        "telegram_chat_id": (metadata_payload.get("chat") or {}).get("id", ""),
        "telegram_sender_id": sender.get("id", ""),
        "telegram_sender_name": sender.get("title", ""),
        "telegram_sender_username": sender.get("username", ""),
        "telegram_caption": caption,
        "telegram_caption_preview": caption[:240],
        "telegram_forward_from": forward.get("from_name") or forward.get("post_author") or forward.get("channel_id") or "",
        "telegram_reply_to_msg_id": metadata_payload.get("reply_to_msg_id", ""),
        "telegram_grouped_id": metadata_payload.get("grouped_id", ""),
        "telegram_views": metadata_payload.get("views", 0),
        "telegram_forwards": metadata_payload.get("forwards", 0),
        "telegram_reaction_count": sum(int(x.get("count", 0) or 0) for x in metadata_payload.get("reactions",
        []) if isinstance(x, dict)),
        "source_pipeline_version": APP_VERSION,
    })
    apply_source_pipeline_tags(rel_path, source_profile)
    return meta
def build_source_pipeline_report():
    profiles = get_source_profiles()
    rows = []
    total_metadata = 0
    for ref, profile in profiles.items():
        source_items = []
        for path, meta in metadata_store.items():
            meta = meta or {}
            if str(meta.get("source_ref") or "").lower() == str(ref).lower() or str(meta.get("source") or
            "").lower() == str(profile.get("title") or "").lower():
                source_items.append((path, meta))
        with_tg = [x for x in source_items if isinstance(x[1].get("telegram"), dict)]
        total_metadata += len(with_tg)
        latest = ""
        for _, meta in source_items:
            latest = max(latest, str(meta.get("message_date") or ""))
        rows.append({
            "ref": ref,
            "title": profile.get("title") or source_display_name(ref),
            "enabled": bool(profile.get("enabled", True)),
            "sync_enabled": bool(profile.get("sync_enabled", True)),
            "pipeline_enabled": bool(profile.get("pipeline_enabled", True)),
            "pipeline_mode": profile.get("pipeline_mode", "incremental"),
            "media_filter": profile.get("media_filter", "videos"),
            "max_messages_per_run": profile.get("max_messages_per_run", ""),
            "lookback_days": profile.get("lookback_days", ""),
            "videos": len(source_items),
            "telegram_metadata": len(with_tg),
            "caption_count": sum(1 for _, meta in with_tg if (meta.get("telegram") or {}).get("caption")),
            "sender_count": sum(1 for _, meta in with_tg if ((meta.get("telegram") or {}).get("sender") or
            {}).get("id")),
            "forward_count": sum(1 for _, meta in with_tg if (meta.get("telegram") or {}).get("forward")),
            "sidecar_count": sum(1 for _, meta in with_tg if meta.get("telegram_metadata_file")),
            "latest_message_date": latest,
            "preservation": {
                "captions": bool(profile.get("preserve_captions", True)),
                "sender": bool(profile.get("preserve_sender", True)),
                "forwards": bool(profile.get("preserve_forwards", True)),
                "replies": bool(profile.get("preserve_replies", True)),
                "reactions": bool(profile.get("preserve_reactions", True)),
            },
        })
    rows.sort(key=lambda r: (not r.get("enabled", True), str(r.get("title", "")).lower()))
    return {
        "ok": True,
        "version": APP_VERSION,
        "summary": {
            "source_count": len(rows),
            "pipeline_enabled": sum(1 for r in rows if r.get("pipeline_enabled")),
            "videos": len(tags),
            "telegram_metadata": total_metadata,
            "metadata_directory": TELEGRAM_METADATA_PATH,
        },
        "pipelines": rows,
    }
def build_telegram_metadata_summary(limit=20):
    total = len(tags)
    with_tg = []
    missing = []
    with_caption = 0
    with_sender = 0
    with_link = 0
    with_sidecar = 0
    by_source = {}
    for path in sorted(tags.keys()):
        meta = metadata_store.get(path) or {}
        src = metadata_source_label(meta)
        row = by_source.setdefault(src, {"source": src, "videos": 0, "telegram_metadata": 0, "missing": 0})
        row["videos"] += 1
        tg = meta.get("telegram")
        if isinstance(tg, dict):
            with_tg.append(path)
            row["telegram_metadata"] += 1
            if tg.get("caption"):
                with_caption += 1
            if (tg.get("sender") or {}).get("id"):
                with_sender += 1
            if tg.get("telegram_link"):
                with_link += 1
            if meta.get("telegram_metadata_file"):
                with_sidecar += 1
        else:
            row["missing"] += 1
            if len(missing) < int(limit or 20):
                missing.append({"path": path, "source": src, "message_id": meta.get("message_id", ""),
                "message_key": meta.get("message_key", "")})
    source_rows = list(by_source.values())
    source_rows.sort(key=lambda x: x.get("missing", 0), reverse=True)
    return {
        "ok": True,
        "version": APP_VERSION,
        "directory": TELEGRAM_METADATA_PATH,
        "summary": {
            "videos": total,
            "with_telegram_metadata": len(with_tg),
            "missing_telegram_metadata": max(0, total - len(with_tg)),
            "with_caption": with_caption,
            "with_sender": with_sender,
            "with_link": with_link,
            "with_sidecar": with_sidecar,
            "coverage_percent": pct(len(with_tg), total),
        },
        "by_source": source_rows,
        "missing_examples": missing,
    }
async def run_source_pipeline_job(job_id, source_ref, options=None):
    options = options or {}
    if client is None:
        finish_job(job_id, "failed", "Telegram is not configured")
        return
    try:
        entity = await resolve_video_source(source_ref)
        title = await entity_title(entity, source_ref)
        profile = get_source_profile(source_ref, title)
        if not profile.get("pipeline_enabled", True):
            finish_job(job_id, "skipped", "Source pipeline disabled")
            return
        max_messages = parse_source_limit(options.get("max_messages") or profile.get("max_messages_per_run"),
        default=500, maximum=100000)
        update_job(job_id, total=max_messages, current=title, note="Scanning Telegram source")
        scanned = 0
        candidates = 0
        async for msg in client.iter_messages(entity, limit=max_messages):
            scanned += 1
            await wait_job_if_paused(job_id)
            if job_cancel_requested(job_id):
                finish_job(job_id, "cancelled", f"Cancelled after {scanned} scanned")
                return
            update_job(job_id, processed=scanned, total=max_messages, current=f"{title} message {getattr(msg, 'id', '')}", note=f"Scanned {scanned}; candidates {candidates}")
            if not message_matches_source_media_filter(msg, profile):
                continue
            allowed, _ = source_allows_message(msg, profile)
            if not allowed:
                continue
            candidates += 1
            await process_message(msg, source_ref=source_ref, source_title=title)
        finish_job(job_id, "complete", f"Pipeline complete. Scanned {scanned}; video candidates{candidates}.")
    except Exception as e:
        logger.exception("Source pipeline failed for %s: %s", source_ref, e)
        finish_job(job_id, "failed", str(e))
async def run_telegram_metadata_backfill_job(job_id, limit=500):
    if client is None:
        finish_job(job_id, "failed", "Telegram is not configured")
        return
    candidates = []
    for path, meta in metadata_store.items():
        meta = meta or {}
        if isinstance(meta.get("telegram"), dict):
            continue
        if meta.get("message_id") and (meta.get("source_ref") or meta.get("message_key")):
            candidates.append((path, meta))
    if limit:
        candidates = candidates[: int(limit)]
    update_job(job_id, total=len(candidates), note="Backfilling Telegram metadata")
    updated = 0
    errors = 0
    skipped = 0
    entity_cache = {}
    for idx, (path, meta) in enumerate(candidates, start=1):
        await wait_job_if_paused(job_id)
        if job_cancel_requested(job_id):
            finish_job(job_id, "cancelled", f"Cancelled after {idx-1} items")
            return
        update_job(job_id, processed=idx, total=len(candidates), current=path, note=f"Updated {updated}; skipped {skipped}; errors {errors}")
        source_ref = str(meta.get("source_ref") or "").strip()
        if not source_ref:
            key = str(meta.get("message_key") or "")
            source_ref = key.split(":", 1)[0] if ":" in key else "me"
        try:
            if source_ref not in entity_cache:
                entity_cache[source_ref] = await resolve_video_source(source_ref)
            entity = entity_cache[source_ref]
            msg = await client.get_messages(entity, ids=int(meta.get("message_id")))
            if not msg:
                skipped += 1
                continue
            title = await entity_title(entity, source_ref)
            profile = get_source_profile(source_ref, title)
            payload = await build_telegram_metadata_payload(msg, source_ref=source_ref, source_title=title,
            source_profile=profile)
            merge_telegram_metadata_into_item(path, payload, profile)
            updated += 1
            if updated % 20 == 0:
                save_state()
        except Exception as e:
            errors += 1
            logger.warning("Telegram metadata backfill failed for %s: %s", path, e)
    save_state()
    finish_job(job_id, "complete" if errors == 0 else "complete_with_errors", f"Metadata backfill complete. Updated {updated}; skipped {skipped}; errors {errors}.")
# ================= DOWNLOAD / INGEST =================
sem = asyncio.Semaphore(2)
async def process_message(message, source_ref="me", source_title="Saved Messages"):
    global faiss_index, faiss_keys
    message_key = message_unique_id(message)
    if is_message_already_seen(message):
        return
    source_profile = get_source_profile(source_ref, source_title)
    allowed, skip_reason = source_allows_message(message, source_profile)
    if not allowed:
        logger.info("Skipping message %s from source %s: %s", message.id, source_ref, skip_reason)
        return
    job_id = create_job("download", f"Download message {message.id}", total=100, source_ref=source_ref,
    metadata={"message_key": message_key, "source": source_profile.get("title")})
    refresh_storage_download_mode()
    if download_control.get("mode") != "running":
        logger.info(
            "Download skipped for message %s because mode=%s reason=%s",
            message.id,
            download_control.get("mode"),
            download_control.get("reason"),
        )
        finish_job(job_id, "skipped", download_control.get("reason", "Downloads paused"))
        return
    async with sem:
        refresh_storage_download_mode()
        if download_control.get("mode") != "running":
            logger.info(
                "Download skipped inside worker for message %s because mode=%s reason=%s",
                message.id,
                download_control.get("mode"),
                download_control.get("reason"),
            )
            finish_job(job_id, "skipped", download_control.get("reason", "Downloads paused"))
            return
        download_root = ensure_directory(get_download_directory())
        source_subdir = safe_source_subdir(source_profile.get("destination_subdir"))
        source_root = ensure_directory(os.path.join(download_root, source_subdir)) if source_subdir else download_root
        folder = os.path.join(source_root, datetime.now().strftime("%Y-%m-%d"))
        os.makedirs(folder, exist_ok=True)
        status = {
            "message_id": message.id,
            "message_key": message_key,
            "source": source_profile.get("title") or source_title or source_display_name(source_ref),
            "source_ref": source_ref,
            "job_id": job_id,
            "status": "starting",
            "filename": None,
            "path": None,
            "current": 0,
            "total": 0,
            "percent": 0,
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "note": f"Preparing download to {folder}"
        }
        set_download_status(message.id, status)
        logger.info("Starting download for message %s into %s", message.id, folder)
        def progress_callback(current, total):
            status["status"] = "downloading"
            status["current"] = int(current)
            status["total"] = int(total)
            status["percent"] = pct(current, total)
            status["updated_at"] = datetime.now().isoformat(timespec="seconds")
            status["note"] = f"{format_bytes(current)} / {format_bytes(total)}"
            update_job(job_id, processed=status["percent"], total=100, current=status.get("filename") or
            f"message {message.id}", note=status["note"])
            if job_cancel_requested(job_id) or job_pause_requested(job_id):
                raise RuntimeError("Download cancelled or paused by user")
        try:
            refresh_storage_download_mode()
            if download_control.get("mode") != "running":
                status["status"] = "paused"
                status["note"] = download_control.get("reason", "Downloads paused")
                status["updated_at"] = datetime.now().isoformat(timespec="seconds")
                record_download_history(dict(status))
                clear_download_status(message.id)
                finish_job(job_id, "paused", status["note"])
                return
            update_job(job_id, current=f"message {message.id}", note="Starting Telegram download")
            file_path = await message.download_media(file=folder, progress_callback=progress_callback)
            if not file_path:
                status["status"] = "failed"
                status["note"] = "No file downloaded"
                record_download_history(dict(status))
                clear_download_status(message.id)
                finish_job(job_id, "failed", status["note"])
                return
            refresh_storage_download_mode()
            rel = safe_rel_path(file_path, download_root)
            status["filename"] = os.path.basename(file_path)
            status["path"] = rel
            status["status"] = "processing"
            status["note"] = "Download complete, analyzing file"
            status["updated_at"] = datetime.now().isoformat(timespec="seconds")
            update_job(job_id, processed=100, total=100, current=rel, note="Download complete; analyzing")
            h = file_hash(file_path)
            if h in state_hashes:
                try:
                    os.remove(file_path)
                except Exception:
                    pass
                mark_message_seen(message)
                save_state()
                status["status"] = "duplicate"
                status["note"] = "Duplicate file removed"
                record_download_history(dict(status))
                clear_download_status(message.id)
                finish_job(job_id, "duplicate", "Duplicate file removed")
                return
            try:
                if source_profile.get("auto_tag", True):
                    t, s, emb = fused_tag(file_path, rel_path=rel)
                else:
                    duration_tmp = get_video_duration_seconds(file_path)
                    t = ["Movie" if duration_tmp >= 300.0 else "Clip"]
                    s = {"Clip": 1.0 if duration_tmp < 300.0 else 0.0, "Movie": 1.0 if duration_tmp >= 300.0 else 0.0}
                    emb = None
                    logger.info("Auto-tag disabled for source %s; stored basic format tag for %s", source_ref,
                    rel)
            except Exception as e:
                logger.exception("Tagging failed for %s", file_path)
                t = ["Clip"]
                s = {"Clip": 1.0, "Movie": 0.0}
                emb = None
                status["note"] = f"Tagging fallback used: {e}"
            tags[rel] = t
            scores_store[rel] = s
            if emb is not None:
                embeddings_store[rel] = emb
            try:
                metadata_store[rel] = {
                    **metadata_store.get(rel, {}),
                    "source": source_profile.get("title") or source_title or source_display_name(source_ref),
                    "source_ref": source_ref,
                    "source_destination_subdir": source_subdir,
                    "message_id": message.id,
                    "message_key": message_key,
                    "message_date": iso_or_blank(getattr(message, "date", None)),
                    "downloaded_at": datetime.now().isoformat(timespec="seconds"),
                    "original_filename": os.path.basename(file_path),
                    "duration": float(get_video_duration_seconds(file_path)),
                    "size": int(os.path.getsize(file_path)),
                    "sha256": h,
                    "modified":
                    datetime.fromtimestamp(os.path.getmtime(file_path)).isoformat(timespec="seconds"),
                }
                if source_profile.get("preserve_telegram_metadata", True):
                    telegram_payload = await build_telegram_metadata_payload(message, source_ref=source_ref,
                    source_title=source_title, source_profile=source_profile)
                    merge_telegram_metadata_into_item(rel, telegram_payload, source_profile)
            except Exception as e:
                logger.warning("Could not store metadata for %s: %s", rel, e)
            try:
                rule_result = apply_auto_rules_to_path(rel, trigger="ingest")
                if rule_result.get("changed"):
                    status["note"] = f"Indexed successfully; {rule_result.get('changed')} rule change(s)applied"
            except Exception as e:
                logger.warning("Auto rules failed for %s: %s", rel, e)
            mark_message_seen(message)
            state_hashes.add(h)
            save_state()
            faiss_index, faiss_keys = build_faiss_index()
            status["status"] = "complete"
            status["path"] = rel
            status["note"] = "Indexed successfully"
            status["updated_at"] = datetime.now().isoformat(timespec="seconds")
            finish_job(job_id, "complete", "Indexed successfully")
        except Exception as e:
            status["status"] = "failed"
            status["note"] = str(e)
            status["updated_at"] = datetime.now().isoformat(timespec="seconds")
            logger.exception("Download/processing failed for message %s", message.id)
            finish_job(job_id, "failed", str(e))
        finally:
            record_download_history(dict(status))
            clear_download_status(message.id)
# ================= TRASH HELPERS =================
def unique_trash_id(rel_path):
    digest = hashlib.sha1(str(rel_path).encode("utf-8")).hexdigest()[:10]
    return f"{timestamp_for_filename()}-{digest}"
def move_if_exists(src, dst):
    if not src or not os.path.exists(src):
        return False
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.move(src, dst)
    return True
def active_record_snapshot(rel_path):
    return {
        "tags": list(tags.get(rel_path, [])),
        "scores": scores_store.get(rel_path, {}),
        "embedding": embeddings_store.get(rel_path),
        "metadata": metadata_store.get(rel_path, {}),
        "manual_override": feedback_data.get("manual_overrides", {}).get(rel_path),
    }
def soft_delete_indexed_file(rel_path):
    """Move an indexed video into Trash and remove it from the active library."""
    global faiss_index, faiss_keys
    if rel_path not in tags:
        raise FileNotFoundError("File not found in index")
    abs_path, storage_root = find_existing_file_with_root(rel_path)
    trash_id = unique_trash_id(rel_path)
    trash_dir = safe_join(TRASH_PATH, trash_id)
    ensure_directory(trash_dir)
    snapshot = active_record_snapshot(rel_path)
    deleted_video = False
    trash_video_rel = ""
    original_size = 0
    original_hash = snapshot.get("metadata", {}).get("sha256") or snapshot.get("metadata", {}).get("hash") or ""
    if abs_path and os.path.exists(abs_path):
        try:
            original_size = int(os.path.getsize(abs_path))
        except Exception:
            original_size = 0
        try:
            original_hash = file_hash(abs_path)
            state_hashes.discard(original_hash)
        except Exception:
            pass
        trash_video_name = os.path.basename(abs_path)
        trash_video_abs = safe_join(trash_dir, trash_video_name)
        move_if_exists(abs_path, trash_video_abs)
        trash_video_rel = os.path.relpath(trash_video_abs, TRASH_PATH)
        deleted_video = True
    derived = {}
    for label, src in (("thumb", thumb_abs_path_for_file(rel_path)), ("preview",
    preview_abs_path_for_file(rel_path))):
        try:
            if os.path.exists(src):
                dst = safe_join(trash_dir, os.path.basename(src))
                move_if_exists(src, dst)
                derived[label] = os.path.relpath(dst, TRASH_PATH)
        except Exception:
            logger.warning("Could not move derived file to trash: %s", src)
    scene_derived = move_scene_thumbnails_for_file(rel_path, trash_dir)
    if scene_derived:
        derived["scenes"] = scene_derived
    trash_entry = {
        "trash_id": trash_id,
        "original_path": rel_path,
        "original_root": storage_root or get_download_directory(),
        "filename": short_name(rel_path),
        "size": int(original_size),
        "size_human": format_bytes(original_size),
        "sha256": original_hash,
        "trashed_at": now_stamp(),
        "video_deleted": deleted_video,
        "trash_video_rel": trash_video_rel,
        "derived": derived,
        "snapshot": snapshot,
    }
    trash_index.setdefault("items", []).insert(0, trash_entry)
    tags.pop(rel_path, None)
    scores_store.pop(rel_path, None)
    embeddings_store.pop(rel_path, None)
    metadata_store.pop(rel_path, None)
    feedback_data.get("manual_overrides", {}).pop(rel_path, None)
    save_state()
    save_feedback()
    save_trash_index()
    faiss_index, faiss_keys = build_faiss_index()
    logger.info("Moved indexed file to trash %s video_deleted=%s", rel_path, deleted_video)
    refresh_storage_download_mode()
    return {"path": rel_path, "trash_id": trash_id, "video_deleted": deleted_video, "trashed": True}
def restore_trash_item(trash_id):
    """Restore one trashed video and its index data."""
    global faiss_index, faiss_keys
    trash_id = safe_id(trash_id)
    items = trash_index.setdefault("items", [])
    entry = next((item for item in items if item.get("trash_id") == trash_id), None)
    if not entry:
        raise FileNotFoundError("Trash item not found")
    rel_path = entry.get("original_path") or ""
    if not rel_path:
        raise ValueError("Trash entry is missing original path")
    if rel_path in tags:
        raise FileExistsError("An active library item already exists at this path")
    root = entry.get("original_root") or get_download_directory()
    restore_abs = safe_abs_path(rel_path, root)
    if os.path.exists(restore_abs):
        raise FileExistsError("A file already exists at the restore location")
    video_rel = entry.get("trash_video_rel") or ""
    if video_rel:
        trash_video_abs = safe_join(TRASH_PATH, video_rel)
        if os.path.exists(trash_video_abs):
            os.makedirs(os.path.dirname(restore_abs), exist_ok=True)
            shutil.move(trash_video_abs, restore_abs)
        elif entry.get("video_deleted"):
            raise FileNotFoundError("The trashed video file is missing")
    for label, dst_func in (("thumb", thumb_abs_path_for_file), ("preview", preview_abs_path_for_file)):
        rel = (entry.get("derived") or {}).get(label)
        if not rel:
            continue
        src = safe_join(TRASH_PATH, rel)
        dst = dst_func(rel_path)
        try:
            if os.path.exists(src):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.move(src, dst)
        except Exception:
            logger.warning("Could not restore derived file %s", src)
    restore_scene_thumbnails_for_file(rel_path, (entry.get("derived") or {}).get("scenes", []))
    snapshot = entry.get("snapshot") or {}
    tags[rel_path] = snapshot.get("tags", [])
    scores_store[rel_path] = snapshot.get("scores", {})
    if snapshot.get("embedding") is not None:
        embeddings_store[rel_path] = snapshot.get("embedding")
    if snapshot.get("metadata") is not None:
        metadata_store[rel_path] = snapshot.get("metadata") or {}
        metadata_store[rel_path]["restored_at"] = now_stamp()
    if snapshot.get("manual_override") is not None:
        feedback_data.setdefault("manual_overrides", {})[rel_path] = snapshot.get("manual_override")
    try:
        if os.path.exists(restore_abs):
            h = file_hash(restore_abs)
            state_hashes.add(h)
            metadata_store.setdefault(rel_path, {})["sha256"] = h
            metadata_store.setdefault(rel_path, {})["size"] = int(os.path.getsize(restore_abs))
    except Exception:
        pass
    items.remove(entry)
    try:
        trash_dir = safe_join(TRASH_PATH, trash_id)
        if os.path.isdir(trash_dir) and not os.listdir(trash_dir):
            os.rmdir(trash_dir)
    except Exception:
        pass
    save_state()
    save_feedback()
    save_trash_index()
    faiss_index, faiss_keys = build_faiss_index()
    refresh_storage_download_mode()
    logger.info("Restored trash item %s -> %s", trash_id, rel_path)
    return {"trash_id": trash_id, "path": rel_path, "restored": True}
def permanently_delete_trash_item(trash_id):
    trash_id = safe_id(trash_id)
    items = trash_index.setdefault("items", [])
    entry = next((item for item in items if item.get("trash_id") == trash_id), None)
    if not entry:
        raise FileNotFoundError("Trash item not found")
    trash_dir = safe_join(TRASH_PATH, trash_id)
    if os.path.isdir(trash_dir):
        shutil.rmtree(trash_dir, ignore_errors=True)
    items.remove(entry)
    save_trash_index()
    refresh_storage_download_mode()
    return {"trash_id": trash_id, "deleted": True}
def empty_trash():
    items = list(trash_index.setdefault("items", []))
    deleted = 0
    failed = []
    for entry in items:
        try:
            permanently_delete_trash_item(entry.get("trash_id"))
            deleted += 1
        except Exception as e:
            failed.append({"trash_id": entry.get("trash_id"), "error": str(e)})
    return {"deleted": deleted, "failed": failed}
# ================= BACKUP HELPERS =================
def backup_manifest():
    return {
        "app": "TeleSaved AI",
        "created_at": now_stamp(),
        "type": "metadata_backup",
        "includes_media": False,
        "files": ["telesaved.db", "state.json", "settings.json", "feedback.json", "trash_index.json"],
    }
def backup_file_info(path):
    st = os.stat(path)
    return {
        "filename": os.path.basename(path),
        "size": int(st.st_size),
        "size_human": format_bytes(st.st_size),
        "created_at": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
        "download_url": f"/api/admin/backups/{os.path.basename(path)}",
    }
def list_backups():
    ensure_directory(BACKUP_PATH)
    files = []
    for name in os.listdir(BACKUP_PATH):
        if not name.endswith(".zip"):
            continue
        path = safe_join(BACKUP_PATH, name)
        if os.path.isfile(path):
            files.append(backup_file_info(path))
    files.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return files
def create_metadata_backup(label="manual"):
    ensure_directory(BACKUP_PATH)
    safe_label = safe_id(label or "manual") or "manual"
    filename = f"telesaved-backup-{timestamp_for_filename()}-{safe_label}.zip"
    backup_path = safe_join(BACKUP_PATH, filename)
    # Flush current in-memory state first.
    save_state()
    save_settings()
    save_feedback()
    save_trash_index()
    with zipfile.ZipFile(backup_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(backup_manifest(), indent=2))
        for src, arcname in ((DB_FILE, "telesaved.db"), (STATE_FILE, "state.json"), (SETTINGS_FILE,
        "settings.json"), (FEEDBACK_FILE, "feedback.json"), (TRASH_INDEX_FILE, "trash_index.json"),
        (CONFIG_FILE, "config.json")):
            if os.path.exists(src):
                zf.write(src, arcname)
    logger.info("Created metadata backup %s", backup_path)
    return backup_file_info(backup_path)
def load_json_from_backup(zf, name, default):
    if name not in zf.namelist():
        return default
    with zf.open(name) as f:
        return json.loads(f.read().decode("utf-8"))
def validate_backup_zip(path):
    allowed = {"manifest.json", "telesaved.db", "state.json", "settings.json", "feedback.json",
    "trash_index.json", "config.json"}
    with zipfile.ZipFile(path, "r") as zf:
        for info in zf.infolist():
            name = info.filename
            if name.startswith("/") or ".." in name.split("/"):
                raise ValueError("Unsafe backup archive path")
            if name not in allowed:
                raise ValueError(f"Unexpected file in backup: {name}")
        manifest = load_json_from_backup(zf, "manifest.json", {})
    return manifest
def restore_metadata_backup(path):
    global faiss_index, faiss_keys, TAG_WEIGHTS, TAG_THRESHOLDS
    validate_backup_zip(path)
    safety_backup = create_metadata_backup("pre-restore")
    restored_db_bytes = None
    with zipfile.ZipFile(path, "r") as zf:
        restored_state = load_json_from_backup(zf, "state.json", None)
        restored_settings = load_json_from_backup(zf, "settings.json", None)
        restored_feedback = load_json_from_backup(zf, "feedback.json", None)
        restored_trash = load_json_from_backup(zf, "trash_index.json", {"items": []})
        if "telesaved.db" in zf.namelist():
            restored_db_bytes = zf.read("telesaved.db")
    if restored_db_bytes:
        db_restore_tmp = f"{DB_FILE}.restore-tmp"
        with open(db_restore_tmp, "wb") as f:
            f.write(restored_db_bytes)
        if os.path.exists(DB_FILE):
            shutil.copy2(DB_FILE, f"{DB_FILE}.pre-restore-{timestamp_for_filename()}")
        os.replace(db_restore_tmp, DB_FILE)
        init_sqlite_db()
        restored_state = load_state_from_sqlite(restored_state or {})
    if not isinstance(restored_state, dict):
        raise ValueError("Backup does not contain a valid state.json or telesaved.db")
    state.clear()
    state.update(restored_state)
    state.setdefault("ids", [])
    state.setdefault("hashes", [])
    state.setdefault("tags", {})
    state.setdefault("scores", {})
    state.setdefault("embeddings", {})
    state.setdefault("metadata", {})
    state_ids.clear(); state_ids.update(state.get("ids", []))
    state_hashes.clear(); state_hashes.update(state.get("hashes", []))
    tags.clear(); tags.update(state.get("tags", {}))
    scores_store.clear(); scores_store.update(state.get("scores", {}))
    embeddings_store.clear(); embeddings_store.update(state.get("embeddings", {}))
    metadata_store.clear(); metadata_store.update(state.get("metadata", {}))
    if isinstance(restored_feedback, dict):
        feedback_data.clear()
        feedback_data.update(restored_feedback)
    feedback_data.setdefault("manual_overrides", {})
    feedback_data.setdefault("history", [])
    if isinstance(restored_settings, dict):
        settings_data.clear()
        settings_data.update(restored_settings)
    settings_data.setdefault("tag_weights", {})
    settings_data.setdefault("tag_thresholds", {})
    settings_data.setdefault("auto_refresh", "10")
    settings_data.setdefault("download_directory", BASE_PATH)
    settings_data.setdefault("video_sources", ["me"])
    settings_data.setdefault("source_profiles", {})
    settings_data.setdefault("custom_tags", [])
    settings_data.setdefault("tag_metadata", {})
    settings_data.setdefault("smart_collections", [])
    settings_data.setdefault("automation_rules", [])
    settings_data.setdefault("privacy", {})
    get_privacy_settings()
    refresh_tag_globals()
    if isinstance(restored_trash, dict):
        trash_index.clear()
        trash_index.update(restored_trash)
    trash_index.setdefault("items", [])
    save_state()
    save_settings()
    save_feedback()
    save_trash_index()
    faiss_index, faiss_keys = build_faiss_index()
    refresh_storage_download_mode()
    logger.warning("Restored metadata backup from %s", path)
    return {"restored": True, "safety_backup": safety_backup}
# ================= DELETE HELPERS =================
def delete_indexed_file(rel_path):
    """Move a video to Trash and remove its active index data.
    This intentionally behaves like a recycle bin: existing delete APIs and UI
    actions are non-destructive by default. Use the Trash admin page to restore or permanently delete trashed videos.
    """
    return soft_delete_indexed_file(rel_path)
def bulk_delete_paths(paths):
    deleted = 0
    failed = []
    for rel_path in paths:
        try:
            delete_indexed_file(rel_path)
            deleted += 1
        except Exception as e:
            logger.exception("Bulk delete failed for %s", rel_path)
            failed.append({"path": rel_path, "error": str(e)})
    refresh_storage_download_mode()
    return {"deleted": deleted, "failed": failed}
# ================= DUPLICATE DETECTION HELPERS =================
def path_sha256(rel_path, compute_missing=False):
    meta = metadata_store.setdefault(rel_path, {})
    existing = str(meta.get("sha256") or meta.get("hash") or "").strip()
    if existing:
        return existing
    if not compute_missing:
        return ""
    abs_path = find_existing_file(rel_path)
    if not abs_path or not os.path.exists(abs_path):
        return ""
    try:
        h = file_hash(abs_path)
        meta["sha256"] = h
        meta["hash_scanned_at"] = now_stamp()
        try:
            meta["size"] = int(os.path.getsize(abs_path))
            meta["modified"] = datetime.fromtimestamp(os.path.getmtime(abs_path)).isoformat(timespec="seconds")
        except Exception:
            pass
        return h
    except Exception as e:
        logger.warning("Could not hash %s for duplicate detection: %s", rel_path, e)
        return ""
def duplicate_group_payload(group_id, group_type, key, paths):
    items = []
    total_size = 0
    for rel_path in paths:
        try:
            payload = serialize_item({
                "path": rel_path,
                "tags": get_effective_tags(rel_path),
                "score_map": scores_store.get(rel_path, {}),
                "manual_override": get_manual_override(rel_path) is not None,
            })
            total_size += int(payload.get("size") or 0)
            items.append(payload)
        except Exception as e:
            logger.warning("Could not serialize duplicate item %s: %s", rel_path, e)
    items.sort(key=lambda item: (item.get("source", ""), item.get("filename", ""), item.get("path", "")))
    return {
        "id": group_id,
        "type": group_type,
        "key": key,
        "count": len(items),
        "total_size": total_size,
        "total_size_human": format_bytes(total_size),
        "reclaimable_size": max(0, total_size - max([int(i.get("size") or 0) for i in items] or [0])),
        "reclaimable_size_human": format_bytes(max(0, total_size - max([int(i.get("size") or 0) for i in
        items] or [0]))),
        "items": items,
    }
def build_duplicate_report(compute_hashes=False, hash_scan_limit=250):
    hash_groups = {}
    signature_groups = {}
    scanned_hashes = 0
    missing_hashes = 0
    for rel_path in sorted(tags.keys()):
        abs_path = find_existing_file(rel_path)
        file_meta = collect_file_metadata(rel_path, abs_path=abs_path, ensure_duration=False)
        if not file_meta.get("exists"):
            continue
        had_sha = bool(str(metadata_store.get(rel_path, {}).get("sha256") or metadata_store.get(rel_path,
        {}).get("hash") or "").strip())
        allow_compute = bool(compute_hashes and not had_sha and scanned_hashes < int(hash_scan_limit or 0))
        sha = path_sha256(rel_path, compute_missing=allow_compute)
        if sha:
            hash_groups.setdefault(sha, []).append(rel_path)
            if allow_compute and not had_sha:
                scanned_hashes += 1
        else:
            missing_hashes += 1
        try:
            size = int(file_meta.get("size") or metadata_store.get(rel_path, {}).get("size") or 0)
        except Exception:
            size = 0
        try:
            duration = float(file_meta.get("duration") or metadata_store.get(rel_path, {}).get("duration") or
            0.0)
        except Exception:
            duration = 0.0
        if size > 0 and duration > 0:
            signature = f"{size}:{int(round(duration))}"
            signature_groups.setdefault(signature, []).append(rel_path)
    exact = []
    for idx, (sha, paths) in enumerate(sorted(hash_groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))):
        if len(paths) > 1:
            exact.append(duplicate_group_payload(f"hash-{idx+1}", "exact_hash", sha, paths))
    exact_paths = {path for group in exact for path in [item.get("path") for item in group.get("items", [])]}
    possible = []
    for idx, (signature, paths) in enumerate(sorted(signature_groups.items(), key=lambda kv: (-len(kv[1]),
    kv[0]))):
        filtered = [path for path in paths if path not in exact_paths]
        if len(filtered) > 1:
            possible.append(duplicate_group_payload(f"sig-{idx+1}", "same_size_duration", signature,
            filtered))
    exact.sort(key=lambda group: (group.get("count", 0), group.get("reclaimable_size", 0)), reverse=True)
    possible.sort(key=lambda group: (group.get("count", 0), group.get("reclaimable_size", 0)), reverse=True)
    if compute_hashes and scanned_hashes:
        save_state()
    return {
        "ok": True,
        "generated_at": now_stamp(),
        "library_items": len(tags),
        "hashes_scanned": scanned_hashes,
        "missing_hashes": missing_hashes,
        "exact_groups": exact,
        "possible_groups": possible,
        "exact_group_count": len(exact),
        "possible_group_count": len(possible),
        "total_group_count": len(exact) + len(possible),
    }
def choose_exact_duplicate_keeper(items):
    """Choose the one file to keep from an exact-hash duplicate group.
    Exact duplicates are byte-for-byte identical, so content quality is the same.
    The keeper choice is deterministic and slightly favors richer metadata before
    falling back to path/name ordering.
    """
    candidates = [item for item in items if item.get("path")]
    if not candidates:
        return None
    def sort_key(item):
        path = str(item.get("path") or "")
        meta = metadata_store.get(path, {}) if isinstance(metadata_store, dict) else {}
        source = str(item.get("source") or meta.get("source_title") or meta.get("source_ref") or "")
        added = str(meta.get("added_at") or meta.get("message_date") or item.get("date") or "")
        try:
            size = int(item.get("size") or meta.get("size") or 0)
        except Exception:
            size = 0
        # Sort ascending, so negative values mean preferred.
        return (
            0 if item.get("exists", True) else 1,
            0 if source and source not in {"Unknown", "Unknown Source"} else 1,
            -size,
            added,
            path.lower(),
        )
    return sorted(candidates, key=sort_key)[0]
def delete_all_exact_duplicates(compute_hashes=True, hash_scan_limit=None):
    """Move all byte-for-byte exact duplicate copies to Trash, keeping one per group."""
    global faiss_index, faiss_keys
    try:
        limit = int(hash_scan_limit if hash_scan_limit is not None else len(tags))
    except Exception:
        limit = len(tags)
    limit = max(0, min(limit, max(len(tags), 1)))
    report = build_duplicate_report(bool(compute_hashes), limit)
    exact_groups = report.get("exact_groups", []) or []
    targets = []
    keepers = []
    planned_groups = []
    keeper_hashes = set()
    estimated_reclaimable = 0
    for group in exact_groups:
        items = [item for item in (group.get("items") or []) if item.get("path")]
        if len(items) < 2:
            continue
        keeper = choose_exact_duplicate_keeper(items)
        if not keeper:
            continue
        keeper_path = keeper.get("path")
        delete_items = [item for item in items if item.get("path") and item.get("path") != keeper_path]
        delete_paths = [item.get("path") for item in delete_items]
        if not delete_paths:
            continue
        targets.extend(delete_paths)
        keepers.append(keeper_path)
        sha = str(group.get("key") or "").strip()
        if sha:
            keeper_hashes.add(sha)
        try:
            estimated_reclaimable += sum(int(item.get("size") or 0) for item in delete_items)
        except Exception:
            pass
        planned_groups.append({
            "group_id": group.get("id"),
            "hash": sha,
            "kept": keeper_path,
            "deleted": delete_paths,
            "deleted_count": len(delete_paths),
            "reclaimable_size": sum(int(item.get("size") or 0) for item in delete_items),
            "reclaimable_size_human": format_bytes(sum(int(item.get("size") or 0) for item in delete_items)),
        })
    # De-duplicate paths while preserving order. A path should only be trashed once.
    seen = set()
    unique_targets = []
    for path in targets:
        if path and path not in seen:
            seen.add(path)
            unique_targets.append(path)
    result = bulk_delete_paths(unique_targets) if unique_targets else {"deleted": 0, "failed": []}
    # Preserve exact hashes for the remaining keeper copies. soft_delete_indexed_file()
    # removes a hash from state_hashes when it moves a duplicate to Trash; for exact
    # duplicate groups, at least one active file with that same hash remains.
    for sha in keeper_hashes:
        state_hashes.add(sha)
    if keeper_hashes:
        save_state()
    refreshed = build_duplicate_report(False, 0)
    return {
        "ok": True,
        "generated_at": now_stamp(),
        "groups_processed": len(planned_groups),
        "keepers": keepers,
        "deleted_paths": unique_targets,
        "deleted": int(result.get("deleted") or 0),
        "failed": result.get("failed", []),
        "failed_count": len(result.get("failed", []) or []),
        "estimated_reclaimable_size": estimated_reclaimable,
        "estimated_reclaimable_size_human": format_bytes(estimated_reclaimable),
        "hashes_scanned": report.get("hashes_scanned", 0),
        "missing_hashes": report.get("missing_hashes", 0),
        "before_exact_group_count": len(exact_groups),
        "after_exact_group_count": refreshed.get("exact_group_count", 0),
        "remaining_possible_group_count": refreshed.get("possible_group_count", 0),
        "groups": planned_groups,
    }
# ================= BUNDLE 3: CLEANUP / REVIEW / DUPLICATE INTELLIGENCE HELPERS =================
def score_map_confidence(score_map):
    """Return best AI score and a normalized confidence percentage."""
    best_tag = ""
    best_score = 0.0
    if isinstance(score_map, dict):
        for tag, value in score_map.items():
            try:
                numeric = float(value)
            except Exception:
                continue
            if tag in {"Clip", "Movie"}:
                continue
            if numeric > best_score:
                best_score = numeric
                best_tag = str(tag)
    return best_tag, round(max(0.0, min(best_score, 1.0)) * 100.0, 1)
def review_category_from_reasons(reasons):
    text = " ".join(str(r) for r in reasons or []).lower()
    if "missing file" in text:
        return "Missing File"
    if "rule flagged" in text:
        return "Rule Flag"
    if "conflicting" in text or "multiple orientation" in text:
        return "Conflict"
    if "no tags" in text:
        return "No Tags"
    if "no ai score" in text:
        return "No AI Data"
    if "low confidence" in text:
        return "Low Confidence"
    return "General Review"
def review_priority_from_category(category, reasons):
    category = str(category or "")
    if category == "Missing File":
        return {"label": "Critical", "score": 100, "severity": "critical"}
    if category == "Conflict":
        return {"label": "High", "score": 85, "severity": "high"}
    if category == "Rule Flag":
        return {"label": "High", "score": 80, "severity": "high"}
    if category in {"No Tags", "No AI Data"}:
        return {"label": "Medium", "score": 60, "severity": "medium"}
    if category == "Low Confidence":
        return {"label": "Medium", "score": 50, "severity": "medium"}
    return {"label": "Low", "score": 25, "severity": "low"}
def enrich_review_item_for_studio(item):
    rel_path = item.get("path") or ""
    reasons = item.get("review_reasons") or review_reason_for_path(rel_path)
    category = review_category_from_reasons(reasons)
    priority = review_priority_from_category(category, reasons)
    best_tag, confidence = score_map_confidence(scores_store.get(rel_path, {}))
    suggested_action = "Open Details"
    if category == "Missing File":
        suggested_action = "Sweep missing index entry"
    elif category in {"No Tags", "No AI Data", "Low Confidence"}:
        suggested_action = "Review and accept/edit tags"
    elif category == "Conflict":
        suggested_action = "Resolve conflicting tags"
    elif category == "Rule Flag":
        suggested_action = "Review rule flag"
    item["review_category"] = category
    item["review_priority"] = priority
    item["best_ai_tag"] = best_tag
    item["best_ai_confidence"] = confidence
    item["suggested_action"] = suggested_action
    item["reviewed_before"] = bool(get_manual_override(rel_path))
    return item
def build_review_studio(limit=200, include_skipped=False, category_filter=""):
    payload = build_review_queue(limit=max(1, int(limit or 200)), include_skipped=bool(include_skipped))
    items = [enrich_review_item_for_studio(dict(item)) for item in payload.get("items", [])]
    if category_filter:
        wanted = str(category_filter).strip().lower()
        items = [item for item in items if str(item.get("review_category") or "").lower() == wanted]
    categories = {}
    priorities = {"Critical": 0, "High": 0, "Medium": 0, "Low": 0}
    for item in items:
        cat = item.get("review_category") or "General Review"
        categories[cat] = categories.get(cat, 0) + 1
        label = (item.get("review_priority") or {}).get("label") or "Low"
        priorities[label] = priorities.get(label, 0) + 1
    items.sort(key=lambda item: (-(item.get("review_priority") or {}).get("score", 0), item.get("filename") or
    ""))
    return {
        "ok": True,
        "version": APP_VERSION,
        "generated_at": now_stamp(),
        "items": items,
        "count": len(items),
        "checked": payload.get("checked", 0),
        "skipped_count": payload.get("skipped_count", 0),
        "include_skipped": bool(include_skipped),
        "categories": categories,
        "priorities": priorities,
        "recommended_workflow": [
            "Resolve missing-file and rule-flag items first.",
            "Accept obvious correct tags in batches.",
            "Open conflicts and low-confidence items in Details before changing tags.",
            "Skip items only when you intentionally want them hidden from the queue."
        ],
    }
def apply_review_studio_batch(paths, action, selected_tags=None, note=""):
    selected_tags = sanitize_tags(selected_tags or []) if "sanitize_tags" in globals() else list(selected_tags or [])
    clean_paths = [str(path) for path in paths or [] if str(path) in tags]
    changed = 0
    failed = []
    for rel_path in clean_paths:
        try:
            if action == "accept":
                current = list(get_effective_tags(rel_path))
                set_manual_override(rel_path, current, tags.get(rel_path, []))
                review_skip_map().pop(rel_path, None)
                changed += 1
            elif action == "skip":
                review_skip_map()[rel_path] = {
                    "skipped_at": now_stamp(),
                    "reasons": review_reason_for_path(rel_path),
                    "source": "Review Studio batch",
                }
                changed += 1
            elif action == "unskip":
                review_skip_map().pop(rel_path, None)
                changed += 1
            elif action == "add_tags":
                current = list(get_effective_tags(rel_path))
                merged = sanitize_tags(current + [tag for tag in selected_tags if tag not in current])
                set_manual_override(rel_path, merged, tags.get(rel_path, []))
                review_skip_map().pop(rel_path, None)
                changed += 1
            elif action == "set_tags":
                set_manual_override(rel_path, selected_tags, tags.get(rel_path, []))
                review_skip_map().pop(rel_path, None)
                changed += 1
            elif action == "remove_tags":
                current = [tag for tag in get_effective_tags(rel_path) if tag not in selected_tags]
                set_manual_override(rel_path, current, tags.get(rel_path, []))
                changed += 1
            elif action == "note":
                metadata_store.setdefault(rel_path, {})["review_studio_note"] = str(note or "")[:2000]
                metadata_store.setdefault(rel_path, {})["review_studio_note_at"] = now_stamp()
                changed += 1
            elif action == "trash":
                delete_indexed_file(rel_path)
                changed += 1
            else:
                raise ValueError(f"Unsupported review batch action: {action}")
        except Exception as e:
            logger.exception("Review Studio batch action failed for %s", rel_path)
            failed.append({"path": rel_path, "error": str(e)})
    save_feedback()
    if action in {"note", "trash"}:
        save_state()
    return {"ok": True, "action": action, "requested": len(paths or []), "processed": len(clean_paths),
    "changed": changed, "failed": failed, "failed_count": len(failed)}
def normalized_duplicate_name(rel_path):
    name = os.path.splitext(os.path.basename(str(rel_path or "")))[0].lower()
    name = re.sub(r"\s*\(\d+\)\s*$", "", name)
    name = re.sub(r"[-_ ]?copy$", "", name)
    name = re.sub(r"[^a-z0-9]+", "", name)
    return name
def embedding_cosine_similarity(path_a, path_b):
    vec_a = embeddings_store.get(path_a)
    vec_b = embeddings_store.get(path_b)
    if not isinstance(vec_a, list) or not isinstance(vec_b, list) or not vec_a or not vec_b:
        return None
    try:
        a = np.array(vec_a, dtype="float32")
        b = np.array(vec_b, dtype="float32")
        denom = float(np.linalg.norm(a) * np.linalg.norm(b))
        if denom <= 0:
            return None
        return float(np.dot(a, b) / denom)
    except Exception:
        return None
def duplicate_pair_confidence(group_type, item_a, item_b):
    path_a = item_a.get("path") or ""
    path_b = item_b.get("path") or ""
    if group_type == "exact_hash":
        return {"score": 100, "signals": ["Byte-for-byte SHA256 match"]}
    signals = []
    score = 0.0
    try:
        size_a = int(item_a.get("size") or 0)
        size_b = int(item_b.get("size") or 0)
        if size_a > 0 and size_a == size_b:
            score += 38
            signals.append("Same file size")
    except Exception:
        pass
    try:
        dur_a = float(item_a.get("duration") or 0)
        dur_b = float(item_b.get("duration") or 0)
        if dur_a > 0 and dur_b > 0 and abs(dur_a - dur_b) <= 1.0:
            score += 32
            signals.append("Same rounded duration")
    except Exception:
        pass
    if normalized_duplicate_name(path_a) and normalized_duplicate_name(path_a) == normalized_duplicate_name(path_b):
        score += 10
        signals.append("Very similar filename")
    source_a = str(item_a.get("source") or "")
    source_b = str(item_b.get("source") or "")
    if source_a and source_b and source_a == source_b:
        score += 5
        signals.append("Same source")
    sim = embedding_cosine_similarity(path_a, path_b)
    if sim is not None:
        if sim >= 0.98:
            score += 15
            signals.append(f"Near-identical visual embedding ({sim:.3f})")
        elif sim >= 0.92:
            score += 9
            signals.append(f"Strong visual embedding similarity ({sim:.3f})")
        elif sim < 0.78:
            score -= 15
            signals.append(f"Visual embedding differs ({sim:.3f})")
    return {"score": int(max(0, min(round(score), 99))), "signals": signals or ["Same size/duration signature"]}
def advanced_duplicate_group_payload(group):
    items = [dict(item) for item in (group.get("items") or []) if item.get("path")]
    keeper = choose_exact_duplicate_keeper(items)
    keeper_path = keeper.get("path") if keeper else (items[0].get("path") if items else "")
    group_type = group.get("type") or "possible"
    pair_scores = []
    signals = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            pair = duplicate_pair_confidence(group_type, items[i], items[j])
            pair_scores.append(pair.get("score", 0))
            for signal in pair.get("signals", []):
                if signal not in signals:
                    signals.append(signal)
    confidence = 100 if group_type == "exact_hash" else (int(round(sum(pair_scores) / len(pair_scores))) if
    pair_scores else 0)
    confidence_label = "Exact" if confidence >= 100 else ("Very High" if confidence >= 90 else "High" if
    confidence >= 75 else "Medium" if confidence >= 55 else "Review Carefully")
    for item in items:
        item["duplicate_keeper"] = item.get("path") == keeper_path
        item["duplicate_recommended_action"] = "Keep" if item.get("path") == keeper_path else ("Trash Candidate" if confidence >= 85 else "Manual Review")
        item["duplicate_confidence"] = confidence
    out = dict(group)
    out["items"] = items
    out["recommended_keeper"] = keeper_path
    out["confidence"] = confidence
    out["confidence_label"] = confidence_label
    out["signals"] = signals[:12]
    out["recommended_delete_paths"] = [item.get("path") for item in items if item.get("path") and
    item.get("path") != keeper_path and (group_type == "exact_hash" or confidence >= 85)]
    out["decision"] = "safe_to_delete_extras" if group_type == "exact_hash" else ("likely_duplicate_review_then_delete" if confidence >= 85 else "manual_watch_required")
    return out
def build_advanced_duplicate_report(compute_hashes=False, hash_scan_limit=1000):
    base = build_duplicate_report(compute_hashes=bool(compute_hashes), hash_scan_limit=int(hash_scan_limit or
    0))
    exact = [advanced_duplicate_group_payload(group) for group in base.get("exact_groups", [])]
    possible = [advanced_duplicate_group_payload(group) for group in base.get("possible_groups", [])]
    all_groups = exact + possible
    very_high = sum(1 for group in all_groups if int(group.get("confidence") or 0) >= 90)
    high = sum(1 for group in all_groups if 75 <= int(group.get("confidence") or 0) < 90)
    review = sum(1 for group in all_groups if int(group.get("confidence") or 0) < 75)
    recommended_delete_paths = []
    for group in exact:
        recommended_delete_paths.extend(group.get("recommended_delete_paths") or [])
    return {
        **base,
        "ok": True,
        "version": APP_VERSION,
        "advanced": True,
        "exact_groups": exact,
        "possible_groups": possible,
        "confidence_summary": {"very_high": very_high, "high": high, "manual_review": review},
        "recommended_exact_delete_paths": recommended_delete_paths,
        "recommended_exact_delete_count": len(recommended_delete_paths),
        "recommended_exact_reclaimable_size": sum(int(item.get("size") or 0) for group in exact for item in
        group.get("items", []) if item.get("path") in set(recommended_delete_paths)),
    }
def build_cleanup_studio(deep=False):
    plan = build_smart_cleanup_plan(deep=bool(deep))
    duplicate_report = build_advanced_duplicate_report(compute_hashes=bool(deep), hash_scan_limit=1500 if deep else 0)
    review_payload = build_review_studio(limit=250, include_skipped=False)
    health = compute_admin_health() if "compute_admin_health" in globals() else {}
    exact_delete_count = int(duplicate_report.get("recommended_exact_delete_count") or 0)
    possible_count = int(duplicate_report.get("possible_group_count") or 0)
    review_count = int(review_payload.get("count") or 0)
    stages = []
    stages.append({
        "id": "backup",
        "title": "Create safety backup",
        "risk": "safe",
        "description": "Create a metadata backup before cleanup, review, or duplicate removal.",
        "action": "create_backup",
        "action_label": "Create Backup",
        "recommended": True,
        "count": 1,
    })
    if exact_delete_count:
        stages.append({
            "id": "exact-duplicates",
            "title": "Remove exact duplicate copies",
            "risk": "low",
            "description": "Byte-for-byte SHA256 matches can safely keep one copy and move the extras to Trash.",
            "admin_tab": "duplicates",
            "action_label": "Open Advanced Duplicates",
            "recommended": True,
            "count": exact_delete_count,
            "reclaimable_size_human": duplicate_report.get("recommended_exact_reclaimable_size_human") or format_bytes(duplicate_report.get("recommended_exact_reclaimable_size", 0)),
        })
    if plan.get("summary"):
        stages.append({
            "id": "smart-cleanup",
            "title": "Run guided cleanup actions",
            "risk": "mixed",
            "description": plan.get("summary"),
            "admin_tab": "cleanupStudio",
            "action_label": "Review Cleanup Issues",
            "recommended": True,
            "count": len(plan.get("issues", []) or []),
        })
    if review_count:
        stages.append({
            "id": "review-studio",
            "title": "Review uncertain metadata",
            "risk": "manual",
            "description": "Use Review Studio to resolve low-confidence, conflicting, untagged, and rule-flagged videos.",
            "admin_tab": "review",
            "action_label": "Open Review Studio",
            "recommended": True,
            "count": review_count,
        })
    if possible_count:
        stages.append({
            "id": "possible-duplicates",
            "title": "Watch likely duplicates",
            "risk": "manual",
            "description": "Possible duplicates are not deleted automatically. Watch and compare them before deciding what to keep.",
            "admin_tab": "duplicates",
            "action_label": "Open Duplicate Review",
            "recommended": True,
            "count": possible_count,
        })
    return {
        "ok": True,
        "version": APP_VERSION,
        "generated_at": now_stamp(),
        "deep": bool(deep),
        "summary": {
            "issues": len(plan.get("issues", []) or []),
            "review_items": review_count,
            "exact_duplicate_delete_candidates": exact_delete_count,
            "possible_duplicate_groups": possible_count,
            "storage_free_human": (health.get("storage") or {}).get("free_human", ""),
        },
        "stages": stages,
        "cleanup_plan": plan,
        "review_summary": {"categories": review_payload.get("categories", {}), "priorities":
        review_payload.get("priorities", {})},
        "duplicate_summary": {
            "exact_groups": duplicate_report.get("exact_group_count", 0),
            "possible_groups": duplicate_report.get("possible_group_count", 0),
            "confidence_summary": duplicate_report.get("confidence_summary", {}),
        },
    }
# ================= SMART CLEANUP ASSISTANT HELPERS =================
def cleanup_sample_item(rel_path):
    try:
        return serialize_item({
            "path": rel_path,
            "tags": get_effective_tags(rel_path),
            "score_map": scores_store.get(rel_path, {}),
            "manual_override": get_manual_override(rel_path) is not None,
        })
    except Exception:
        return {
            "path": rel_path,
            "filename": short_name(rel_path),
            "tags": get_effective_tags(rel_path) if rel_path in tags else [],
        }
def cleanup_issue(issue_id, title, severity, description, count=0, action="", action_label="", samples=None,
reclaimable_size=0, metadata=None):
    severity = severity if severity in {"info", "low", "medium", "high", "critical"} else "info"
    return {
        "id": issue_id,
        "title": title,
        "severity": severity,
        "description": description,
        "count": int(count or 0),
        "action": action,
        "action_label": action_label,
        "samples": samples or [],
        "reclaimable_size": int(reclaimable_size or 0),
        "reclaimable_size_human": format_bytes(reclaimable_size or 0),
        "metadata": metadata or {},
    }
def collect_cleanup_candidates(deep=False, sample_limit=10):
    missing_paths = []
    missing_duration = []
    missing_hash = []
    broken_thumb = []
    broken_preview = []
    very_short = []
    very_large = []
    empty_tags = []
    missing_source = []
    no_embedding = []
    total_existing_size = 0
    for rel_path in sorted(tags.keys()):
        abs_path = find_existing_file(rel_path)
        file_meta = collect_file_metadata(rel_path, abs_path=abs_path, ensure_duration=False)
        meta = metadata_store.get(rel_path, {}) or {}
        effective_tags = get_effective_tags(rel_path)
        if not file_meta.get("exists"):
            missing_paths.append(rel_path)
            continue
        try:
            size = int(file_meta.get("size") or 0)
        except Exception:
            size = 0
        total_existing_size += size
        try:
            duration = float(file_meta.get("duration") or meta.get("duration") or 0.0)
        except Exception:
            duration = 0.0
        if duration <= 0:
            missing_duration.append(rel_path)
        elif duration < 10:
            very_short.append(rel_path)
        if not str(meta.get("sha256") or meta.get("hash") or meta.get("file_hash") or "").strip():
            missing_hash.append(rel_path)
        if not os.path.exists(thumb_abs_path_for_file(rel_path)):
            broken_thumb.append(rel_path)
        if not os.path.exists(preview_abs_path_for_file(rel_path)):
            broken_preview.append(rel_path)
        if size >= 1024 * 1024 * 1024:
            very_large.append((size, rel_path))
        if not effective_tags:
            empty_tags.append(rel_path)
        if not str(meta.get("source") or meta.get("source_title") or meta.get("source_ref") or "").strip():
            missing_source.append(rel_path)
        if not embeddings_store.get(rel_path):
            no_embedding.append(rel_path)
    very_large.sort(reverse=True)
    def sample(paths):
        return [cleanup_sample_item(path) for path in list(paths)[:sample_limit]]
    return {
        "missing_paths": missing_paths,
        "missing_duration": missing_duration,
        "missing_hash": missing_hash,
        "broken_thumb": broken_thumb,
        "broken_preview": broken_preview,
        "very_short": very_short,
        "very_large": [path for size, path in very_large],
        "very_large_size": sum(size for size, path in very_large),
        "empty_tags": empty_tags,
        "missing_source": missing_source,
        "no_embedding": no_embedding,
        "total_existing_size": total_existing_size,
        "sample": sample,
    }
def build_smart_cleanup_plan(deep=False):
    """Create a safe, explainable cleanup plan without deleting media files.
    The plan focuses on issues that affect trust, storage safety, and library
    quality. Destructive cleanup still routes through Trash, and automatic
    actions are deliberately conservative.
    """
    started = now_stamp()
    storage = get_storage_stats()
    candidates = collect_cleanup_candidates(deep=deep)
    issues = []
    missing = candidates["missing_paths"]
    if missing:
        issues.append(cleanup_issue(
            "missing-index-entries",
            "Missing file index entries",
            "high",
            "These videos are still indexed, but the media file no longer exists on disk. Sweep them out of the active index.",
            len(missing),
            "sweep_missing",
            "Sweep missing files",
            candidates["sample"](missing),
        ))
    if storage_is_low():
        issues.append(cleanup_issue(
            "low-storage",
            "Low storage protection is active",
            "critical",
            f"Only {storage.get('percent_free')}% storage is free. Downloads should stay paused until files are deleted, moved, or storage is expanded.",
            1,
            "scan_duplicates",
            "Find reclaimable duplicates",
            [],
            metadata={"storage": storage},
        ))
    duplicate_report = build_duplicate_report(bool(deep), 1000 if deep else 0)
    duplicate_reclaimable = 0
    duplicate_group_count = int(duplicate_report.get("total_group_count") or 0)
    for group in duplicate_report.get("exact_groups", []) + duplicate_report.get("possible_groups", []):
        duplicate_reclaimable += int(group.get("reclaimable_size") or 0)
    if duplicate_group_count:
        first_samples = []
        for group in duplicate_report.get("exact_groups", []) + duplicate_report.get("possible_groups", []):
            for item in group.get("items", [])[:3]:
                first_samples.append(item)
                if len(first_samples) >= 8:
                    break
            if len(first_samples) >= 8:
                break
        issues.append(cleanup_issue(
            "duplicates",
            "Likely duplicate videos",
            "medium",
            "TeleSaved found exact or likely duplicate groups. Review them before moving anything to Trash.",
            duplicate_group_count,
            "scan_duplicates",
            "Refresh duplicate scan",
            first_samples,
            reclaimable_size=duplicate_reclaimable,
            metadata={
                "exact_groups": duplicate_report.get("exact_group_count", 0),
                "possible_groups": duplicate_report.get("possible_group_count", 0),
            },
        ))
    review_count = review_queue_count(limit=100000)
    if review_count:
        issues.append(cleanup_issue(
            "review-queue",
            "Videos need tag review",
            "medium",
            "These items have uncertain, conflicting, missing, or otherwise review-worthy metadata. Review improves future AI tag quality.",
            review_count,
            "open_review",
            "Open Review Queue",
        ))
    broken_assets = sorted(set(candidates["broken_thumb"] + candidates["broken_preview"]))
    if broken_assets:
        issues.append(cleanup_issue(
            "broken-previews",
            "Missing thumbnails or hover previews",
            "low",
            "Some existing videos are missing thumbnails or animated previews. Reprocess a small batch to repair browsing quality.",
            len(broken_assets),
            "repair_previews",
            "Repair preview batch",
            candidates["sample"](broken_assets),
        ))
    if candidates["missing_duration"]:
        issues.append(cleanup_issue(
            "missing-duration",
            "Missing duration metadata",
            "low",
            "Duration is missing for some existing files, which affects sorting, statistics, and smart collections.",
            len(candidates["missing_duration"]),
            "scan_duration",
            "Scan duration batch",
            candidates["sample"](candidates["missing_duration"]),
        ))
    if candidates["missing_hash"]:
        issues.append(cleanup_issue(
            "missing-hashes",
            "Missing duplicate fingerprints",
            "low",
            "Some existing files have no saved SHA256 fingerprint. Hashing improves exact duplicate detection.",
            len(candidates["missing_hash"]),
            "hash_scan",
            "Hash fingerprint batch",
            candidates["sample"](candidates["missing_hash"]),
        ))
    if candidates["no_embedding"]:
        issues.append(cleanup_issue(
            "missing-embeddings",
            "Missing semantic embeddings",
            "medium",
            "Some videos cannot participate in semantic search or similar-video matching until reprocessed.",
            len(candidates["no_embedding"]),
            "reprocess_metadata",
            "Reprocess metadata batch",
            candidates["sample"](candidates["no_embedding"]),
        ))
    if candidates["very_large"]:
        issues.append(cleanup_issue(
            "large-files",
            "Very large files",
            "info",
            "These files are at least 1 GB. They are not necessarily a problem, but reviewing them can quickly recover storage.",
            len(candidates["very_large"]),
            "open_large_files",
            "Review large files",
            candidates["sample"](candidates["very_large"]),
            reclaimable_size=candidates["very_large_size"],
        ))
    if candidates["very_short"]:
        issues.append(cleanup_issue(
            "very-short-clips",
            "Very short clips",
            "info",
            "These videos are under 10 seconds. They may be intentional, but they are good candidates for manual cleanup.",
            len(candidates["very_short"]),
            "open_short_clips",
            "Review short clips",
            candidates["sample"](candidates["very_short"]),
        ))
    recent_failed_downloads = [item for item in list(download_history)[:50] if str(item.get("status",
    "")).lower() == "failed"]
    if recent_failed_downloads:
        issues.append(cleanup_issue(
            "failed-downloads",
            "Recent failed downloads",
            "medium",
            "Recent Telegram downloads failed. Review the Downloads or Jobs page before expanding source sync.",
            len(recent_failed_downloads),
            "open_downloads",
            "Open Downloads",
            [],
        ))
    backup_count = len(list_backups())
    if backup_count == 0:
        issues.append(cleanup_issue(
            "no-backup",
            "No metadata backups found",
            "high",
            "Create a backup before large cleanup, duplicate removal, imports, or restore operations.",
            1,
            "create_backup",
            "Create backup",
        ))
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    issues.sort(key=lambda row: (severity_order.get(row.get("severity", "info"), 9), -int(row.get("count") or
    0), row.get("title", "")))
    total_reclaimable = sum(int(issue.get("reclaimable_size") or 0) for issue in issues)
    safe_actions = [issue for issue in issues if issue.get("action") in {"sweep_missing", "create_backup",
    "scan_duration", "hash_scan"}]
    if not issues:
        summary = "No major cleanup issues were found. Your active library index looks healthy."
    else:
        summary = f"TeleSaved found {len(issues)} cleanup opportunity group(s). Start with high-severity items, then review duplicates and metadata repairs."
    return {
        "ok": True,
        "version": APP_VERSION,
        "generated_at": started,
        "deep": bool(deep),
        "summary": summary,
        "issue_count": len(issues),
        "issues": issues,
        "safe_action_count": len(safe_actions),
        "total_reclaimable_size": total_reclaimable,
        "total_reclaimable_size_human": format_bytes(total_reclaimable),
        "storage": storage,
        "download_control": download_control,
        "library": {
            "indexed": len(tags),
            "existing_size": candidates["total_existing_size"],
            "existing_size_human": format_bytes(candidates["total_existing_size"]),
            "missing_index_entries": len(candidates["missing_paths"]),
            "review_queue": review_count,
            "duplicate_groups": duplicate_group_count,
            "backups": backup_count,
        },
    }
def scan_missing_durations(limit=100):
    changed = False
    scanned = 0
    updated = 0
    failed = 0
    for rel_path in sorted(tags.keys()):
        if scanned >= int(limit or 0):
            break
        meta = metadata_store.setdefault(rel_path, {})
        try:
            current = float(meta.get("duration") or 0.0)
        except Exception:
            current = 0.0
        if current > 0:
            continue
        abs_path = find_existing_file(rel_path)
        if not abs_path or not os.path.exists(abs_path):
            continue
        scanned += 1
        try:
            duration = float(get_video_duration_seconds(abs_path) or 0.0)
            if duration > 0:
                meta["duration"] = duration
                meta["duration_scanned_at"] = now_stamp()
                updated += 1
                changed = True
        except Exception:
            failed += 1
    if changed:
        save_state()
    return {"ok": True, "scanned": scanned, "updated": updated, "failed": failed}
def scan_missing_hashes(limit=100):
    changed = False
    scanned = 0
    updated = 0
    failed = 0
    for rel_path in sorted(tags.keys()):
        if scanned >= int(limit or 0):
            break
        meta = metadata_store.setdefault(rel_path, {})
        if str(meta.get("sha256") or meta.get("hash") or meta.get("file_hash") or "").strip():
            continue
        abs_path = find_existing_file(rel_path)
        if not abs_path or not os.path.exists(abs_path):
            continue
        scanned += 1
        try:
            h = file_hash(abs_path)
            meta["sha256"] = h
            meta["hash_scanned_at"] = now_stamp()
            state_hashes.add(h)
            updated += 1
            changed = True
        except Exception:
            failed += 1
    if changed:
        save_state()
    return {"ok": True, "scanned": scanned, "updated": updated, "failed": failed}
def repair_preview_batch(limit=25):
    repaired = 0
    failed = []
    checked = 0
    for rel_path in sorted(tags.keys()):
        if checked >= int(limit or 0):
            break
        abs_path = find_existing_file(rel_path)
        if not abs_path or not os.path.exists(abs_path):
            continue
        thumb_missing = not os.path.exists(thumb_abs_path_for_file(rel_path))
        preview_missing = not os.path.exists(preview_abs_path_for_file(rel_path))
        if not (thumb_missing or preview_missing):
            continue
        checked += 1
        try:
            reprocess_file(rel_path)
            repaired += 1
        except Exception as e:
            logger.exception("Cleanup preview repair failed for %s", rel_path)
            failed.append({"path": rel_path, "error": str(e)})
    return {"ok": True, "checked": checked, "repaired": repaired, "failed": failed}
def reprocess_metadata_batch(limit=25):
    processed = 0
    failed = []
    for rel_path in sorted(tags.keys()):
        if processed >= int(limit or 0):
            break
        if embeddings_store.get(rel_path):
            continue
        abs_path = find_existing_file(rel_path)
        if not abs_path or not os.path.exists(abs_path):
            continue
        try:
            reprocess_file(rel_path)
            processed += 1
        except Exception as e:
            logger.exception("Cleanup metadata reprocess failed for %s", rel_path)
            failed.append({"path": rel_path, "error": str(e)})
    return {"ok": True, "processed": processed, "failed": failed}
def run_cleanup_action(action, limit=100):
    action = str(action or "").strip()
    try:
        limit = max(1, min(int(limit or 100), 1000))
    except Exception:
        limit = 100
    if action == "sweep_missing":
        return sweep_missing_index_entries("cleanup_assistant")
    if action == "scan_duplicates":
        result = build_duplicate_report(True, limit)
        save_state()
        return result
    if action == "create_backup":
        return {"ok": True, "backup": create_metadata_backup("cleanup-assistant")}
    if action == "scan_duration":
        return scan_missing_durations(limit)
    if action == "hash_scan":
        return scan_missing_hashes(limit)
    if action == "repair_previews":
        return repair_preview_batch(min(limit, 50))
    if action == "reprocess_metadata":
        return reprocess_metadata_batch(min(limit, 50))
    return {"ok": False, "error": f"Unsupported cleanup action: {action}"}
# ================= SMART COLLECTION HELPERS =================
def normalize_collection_id(value=None):
    value = safe_id(value or "")
    return value or f"collection-{timestamp_for_filename()}"
def normalize_collection_payload(data, existing=None):
    existing = existing or {}
    data = data or {}
    name = str(data.get("name") or existing.get("name") or "Untitled Collection").strip()[:120] or "Untitled Collection"
    collection_id = normalize_collection_id(data.get("id") or existing.get("id") or name.lower().replace(" ",
    "-"))
    def text_field(key, default=""):
        return str(data.get(key, existing.get(key, default)) or "").strip()
    def float_field(key, default=""):
        value = data.get(key, existing.get(key, default))
        if value in (None, ""):
            return ""
        try:
            return str(float(value))
        except Exception:
            return ""
    return {
        "id": collection_id,
        "name": name,
        "description": str(data.get("description") or existing.get("description") or "").strip()[:1000],
        "q": text_field("q"),
        "tag": text_field("tag"),
        "source": text_field("source"),
        "min": float_field("min", "0") or "0",
        "duration_min": float_field("duration_min"),
        "duration_max": float_field("duration_max"),
        "size_min_mb": float_field("size_min_mb"),
        "size_max_mb": float_field("size_max_mb"),
        "sort": text_field("sort", "path") or "path",
        "dir": text_field("dir", "asc") or "asc",
        "created_at": existing.get("created_at") or now_stamp(),
        "updated_at": now_stamp(),
    }
def get_smart_collections():
    raw = settings_data.setdefault("smart_collections", [])
    if not isinstance(raw, list):
        raw = []
    out = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        col = normalize_collection_payload(item, item)
        key = col["id"]
        if key in seen:
            col["id"] = f"{key}-{len(out)+1}"
        seen.add(col["id"])
        out.append(col)
    settings_data["smart_collections"] = out
    return out
def save_smart_collections(collections):
    settings_data["smart_collections"] = collections
    save_settings()
    return get_smart_collections()
def parse_float_or_none(value):
    try:
        if value in (None, ""):
            return None
        return float(value)
    except Exception:
        return None
def item_matches_extended_filters(
    item,
    source_filter="",
    duration_min=None,
    duration_max=None,
    size_min_mb=None,
    size_max_mb=None,
    audio_language=None,
    scene_composition=None,
    performer_presentation=None,
    act_tag=None,
):
    rel_path = item.get("path", "")
    file_meta = collect_file_metadata(rel_path, ensure_duration=False)
    if source_filter:
        wanted = str(source_filter).strip().lower()
        source = str(file_meta.get("source") or "").lower()
        source_ref = str(file_meta.get("source_ref") or "").lower()
        if wanted not in source and wanted not in source_ref:
            return False
    duration = float(file_meta.get("duration") or 0.0)
    size_mb = float(file_meta.get("size") or 0) / (1024 * 1024)
    if duration_min is not None and duration < duration_min:
        return False
    if duration_max is not None and duration > duration_max:
        return False
    if size_min_mb is not None and size_mb < size_min_mb:
        return False
    if size_max_mb is not None and size_mb > size_max_mb:
        return False
    # ── Taxonomy filters ──────────────────────────────────────────────────────
    if audio_language:
        stored_lang = get_audio_language(rel_path) or ""
        if stored_lang.lower() != str(audio_language).strip().lower():
            return False
    if scene_composition:
        signals = get_ai_signals(rel_path) or {}
        if str(signals.get("scene_composition") or "").lower() != str(scene_composition).strip().lower():
            return False
    if performer_presentation:
        signals = get_ai_signals(rel_path) or {}
        pp_list = [str(p).lower() for p in (signals.get("performer_presentations") or [])]
        if str(performer_presentation).strip().lower() not in pp_list:
            return False
    if act_tag:
        item_tags = item.get("tags") or get_effective_tags(rel_path)
        if str(act_tag).strip() not in item_tags:
            return False
    return True

def build_library_items(
    q="",
    tag_filter="",
    min_score=0.0,
    source_filter="",
    duration_min=None,
    duration_max=None,
    size_min_mb=None,
    size_max_mb=None,
    audio_language=None,
    scene_composition=None,
    performer_presentation=None,
    act_tag=None,
):
    q = str(q or "").strip()
    tag_filter = str(tag_filter or "").strip()
    if not q:
        items = []
        for path in sorted(tags.keys()):
            file_tags = get_effective_tags(path)
            if tag_filter and tag_filter not in file_tags:
                continue
            item = {
                "path": path,
                "tags": file_tags,
                "score_map": scores_store.get(path, {}),
                "final_score": None,
                "semantic_score": None,
                "manual_override": get_manual_override(path) is not None,
            }
            items.append(item)
    else:
        items = search(query=q, filter_tag=tag_filter if tag_filter else None, min_score=float(min_score or
        0.0), top_k=500)
    return [
        item for item in items
        if item_matches_extended_filters(
            item,
            source_filter=source_filter,
            duration_min=duration_min,
            duration_max=duration_max,
            size_min_mb=size_min_mb,
            size_max_mb=size_max_mb,
            audio_language=audio_language,
            scene_composition=scene_composition,
            performer_presentation=performer_presentation,
            act_tag=act_tag,
        )
    ]
# ================= AI LIBRARIAN =================
def normalize_librarian_text(text):
    return re.sub(r"\s+", " ", str(text or "").strip())[:2000]
def parse_number_unit_to_seconds(number, unit):
    try:
        value = float(number)
    except Exception:
        return None
    unit = str(unit or "").lower()
    if unit.startswith("hour") or unit in {"h", "hr", "hrs"}:
        return value * 3600
    if unit.startswith("min") or unit in {"m"}:
        return value * 60
    return value
def parse_number_unit_to_mb(number, unit):
    try:
        value = float(number)
    except Exception:
        return None
    unit = str(unit or "").lower()
    if unit in {"gb", "g", "gib"} or unit.startswith("gig"):
        return value * 1024
    if unit in {"tb", "t", "tib"} or unit.startswith("tera"):
        return value * 1024 * 1024
    if unit in {"kb", "k", "kib"} or unit.startswith("kilo"):
        return value / 1024
    return value
def librarian_available_tags():
    return list(PREDEFINED_TAGS)
def librarian_parse_prompt(prompt):
    """Translate a natural-language library request into safe local filters.
    This intentionally uses deterministic parsing plus the existing CLIP/FAISS
    semantic index. It does not call an external LLM, so it stays private and works offline after models are available locally.
    """
    original = normalize_librarian_text(prompt)
    lower = original.lower()
    working = lower
    filters = {
        "q": "",
        "tags": [],
        "tag_filter": "",
        "source_filter": "",
        "min_score": 0.0,
        "duration_min": None,
        "duration_max": None,
        "size_min_mb": None,
        "size_max_mb": None,
        "sort_by": "relevance",
        "sort_dir": "desc",
        "exists": None,
        "limit": 24,
        "mode": "search",
    }
    reasons = []
    # Recognize explicit tags and common tag plurals.
    tag_aliases = {tag.lower(): tag for tag in PREDEFINED_TAGS}
    tag_aliases.update({
        "men": "Male",
        "man": "Male",
        "male": "Male",
        "women": "Female",
        "woman": "Female",
        "female": "Female",
        "transgender": "Trans",
        "trans": "Trans",
        "adults": "Adults",
        "adult": "Adults",
        "clips": "Clip",
        "clip": "Clip",
        "movies": "Movie",
        "movie": "Movie",
    })
    found_tags = []
    for alias, canonical in sorted(tag_aliases.items(), key=lambda x: -len(x[0])):
        if re.search(r"\b" + re.escape(alias) + r"\b", lower):
            if canonical not in found_tags:
                found_tags.append(canonical)
            working = re.sub(r"\b" + re.escape(alias) + r"\b", " ", working)
    filters["tags"] = found_tags
    filters["tag_filter"] = found_tags[0] if found_tags else ""
    if found_tags:
        reasons.append("Matched requested tag filters: " + ", ".join(found_tags))
    # Source parsing: @source, t.me links, Saved Messages, or phrases like "from X".
    source = ""
    at_match = re.search(r"@([A-Za-z0-9_]{3,})", original)
    if at_match:
        source = "@" + at_match.group(1)
    elif "saved messages" in lower or "saved message" in lower:
        source = "Saved Messages"
    else:
        m = re.search(r"\b(?:from|source|channel|group|chat)\s+([@A-Za-z0-9_.:/-]{3,80})", original,
        flags=re.IGNORECASE)
        if m:
            source = m.group(1).strip().rstrip(".,;)")
    if source:
        filters["source_filter"] = source
        reasons.append(f"Filtered by source containing: {source}")
    # Duration intent.
    if re.search(r"\b(long|longer|lengthy|full length|full-length)\b", lower):
        filters["duration_min"] = max(filters["duration_min"] or 0, 300.0)
        filters["sort_by"] = "duration"
        filters["sort_dir"] = "desc"
        reasons.append("Interpreted 'long' as videos 5 minutes or longer")
    if re.search(r"\b(short|shorter|quick|tiny)\b", lower):
        filters["duration_max"] = min(filters["duration_max"] or 10**9, 300.0)
        filters["sort_by"] = "duration"
        filters["sort_dir"] = "asc"
        reasons.append("Interpreted 'short' as videos under 5 minutes")
    for m in re.finditer(r"\b(?:over|longer than|more than|at least|above)\s+(\d+(?:\.\d+)?)\s*(seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h)\b", lower):
        seconds = parse_number_unit_to_seconds(m.group(1), m.group(2))
        if seconds is not None:
            filters["duration_min"] = max(filters["duration_min"] or 0, seconds)
            reasons.append(f"Minimum duration: {format_duration(seconds)}")
    for m in re.finditer(r"\b(?:under|less than|shorter than|below|max(?:imum)?)\s+(\d+(?:\.\d+)?)\s*(seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h)\b", lower):
        seconds = parse_number_unit_to_seconds(m.group(1), m.group(2))
        if seconds is not None:
            filters["duration_max"] = min(filters["duration_max"] or 10**9, seconds)
            reasons.append(f"Maximum duration: {format_duration(seconds)}")
    # Size intent.
    if re.search(r"\b(big|large|largest|huge)\b", lower):
        filters["sort_by"] = "size"
        filters["sort_dir"] = "desc"
        reasons.append("Sorted by largest file size")
    if re.search(r"\b(small|smallest)\b", lower):
        filters["sort_by"] = "size"
        filters["sort_dir"] = "asc"
        reasons.append("Sorted by smallest file size")
    for m in re.finditer(r"\b(?:over|larger than|more than|at least|above)\s+(\d+(?:\.\d+)?)\s*(kb|mb|gb|tb|kib|mib|gib|tib)\b", lower):
        mb = parse_number_unit_to_mb(m.group(1), m.group(2))
        if mb is not None:
            filters["size_min_mb"] = max(filters["size_min_mb"] or 0, mb)
            reasons.append(f"Minimum size: {format_bytes(mb * 1024 * 1024)}")
    for m in re.finditer(r"\b(?:under|less than|smaller than|below|max(?:imum)?)\s+(\d+(?:\.\d+)?)\s*(kb|mb|gb|tb|kib|mib|gib|tib)\b", lower):
        mb = parse_number_unit_to_mb(m.group(1), m.group(2))
        if mb is not None:
            filters["size_max_mb"] = min(filters["size_max_mb"] or 10**12, mb)
            reasons.append(f"Maximum size: {format_bytes(mb * 1024 * 1024)}")
    # Existence / cleanup intent.
    if re.search(r"\b(missing|broken|not exist|doesn.?t exist|orphan|orphaned)\b", lower):
        filters["exists"] = False
        filters["mode"] = "cleanup"
        reasons.append("Showing indexed entries whose media file is missing")
    if re.search(r"\b(existing|exists|available files|present files)\b", lower):
        filters["exists"] = True
        reasons.append("Showing only entries with media files present")
    # Review intent.
    if re.search(r"\b(review|uncertain|low confidence|forgot to tag|untagged|needs tags?|needs review)\b",
    lower):
        filters["mode"] = "review"
        reasons.append("Interpreted request as a review/quality-control query")
    # Duplicate intent is surfaced as guidance; duplicate groups remain handled in Admin > Duplicates.
    if re.search(r"\b(duplicate|duplicates|repost|reposts)\b", lower):
        filters["mode"] = "duplicates"
        reasons.append("Duplicate-focused request detected")
    # Sort intent.
    sort_map = [
        (r"\b(newest|recent|latest|recently added)\b", "modified", "desc", "Sorted newest first"),
        (r"\b(oldest|earliest)\b", "modified", "asc", "Sorted oldest first"),
        (r"\b(best|highest score|most relevant|top)\b", "score", "desc", "Sorted by strongest score"),
    ]
    for pattern, col, direction, why in sort_map:
        if re.search(pattern, lower):
            filters["sort_by"] = col
            filters["sort_dir"] = direction
            reasons.append(why)
            break
    # Limit intent.
    m = re.search(r"\b(?:top|first|show|limit)\s+(\d{1,3})\b", lower)
    if m:
        try:
            filters["limit"] = max(1, min(int(m.group(1)), 100))
        except Exception:
            pass
    # Build semantic query from leftover meaningful words.
    remove_patterns = [
        r"\b(show|find|get|list|search|me|videos?|files?|that|are|is|the|a|an|all|please|probably|i|forgot|to|tag|with|and|or|by|of|in|on|for|only)\b",
        r"\b(from|source|channel|group|chat|saved messages?)\b",
        r"@\w+",
        r"https?://\S+",
        r"\b(over|under|less than|more than|larger than|smaller than|longer than|shorter than|at least|above|below|max(?:imum)?)\s+\d+(?:\.\d+)?\s*\w+\b",
        r"\b(long|longer|lengthy|full length|full-length|short|shorter|quick|tiny|big|large|largest|huge|small|smallest|newest|recent|latest|oldest|earliest|best|top|highest score|most relevant)\b",
    ]
    semantic = working
    for pattern in remove_patterns:
        semantic = re.sub(pattern, " ", semantic, flags=re.IGNORECASE)
    semantic = re.sub(r"[^A-Za-z0-9_ .'-]", " ", semantic)
    semantic = re.sub(r"\s+", " ", semantic).strip()
    if len(semantic) >= 3 and filters["mode"] not in {"cleanup", "duplicates"}:
        filters["q"] = semantic
        reasons.append(f"Used semantic search text: {semantic}")
    return filters, reasons
def librarian_item_pool(filters):
    if filters.get("mode") == "review":
        review_payload = build_review_queue(limit=500, include_skipped=False) if "build_review_queue" in globals() else {"items": []}
        review_items = review_payload.get("items", [])
        items = []
        for review in review_items:
            rel_path = review.get("path")
            if rel_path in tags:
                items.append({
                    "path": rel_path,
                    "tags": get_effective_tags(rel_path),
                    "score_map": scores_store.get(rel_path, {}),
                    "final_score": None,
                    "semantic_score": None,
                    "manual_override": get_manual_override(rel_path) is not None,
                    "review_reasons": review.get("reasons", []),
                })
    elif filters.get("mode") == "duplicates":
        report = build_duplicate_report(compute_hashes=False) if "build_duplicate_report" in globals() else {}
        groups = (report.get("exact_groups") or []) + (report.get("possible_groups") or [])
        paths = []
        for group in groups:
            for item in group.get("items", []):
                path = item.get("path")
                if path and path not in paths:
                    paths.append(path)
        items = [{
            "path": path,
            "tags": get_effective_tags(path),
            "score_map": scores_store.get(path, {}),
            "final_score": None,
            "semantic_score": None,
            "manual_override": get_manual_override(path) is not None,
        } for path in paths if path in tags]
    else:
        items = build_library_items(
            q=filters.get("q", ""),
            tag_filter=filters.get("tag_filter", ""),
            min_score=float(filters.get("min_score") or 0.0),
            source_filter=filters.get("source_filter", ""),
            duration_min=filters.get("duration_min"),
            duration_max=filters.get("duration_max"),
            size_min_mb=filters.get("size_min_mb"),
            size_max_mb=filters.get("size_max_mb"),
        )
    # Additional tag AND filtering when the prompt named multiple tags.
    wanted_tags = filters.get("tags") or []
    if len(wanted_tags) > 1:
        wanted = set(wanted_tags)
        items = [item for item in items if wanted.issubset(set(item.get("tags") or
        get_effective_tags(item.get("path"))))]
    # Existing/missing filter.
    if filters.get("exists") is not None:
        desired = bool(filters.get("exists"))
        items = [item for item in items if bool(collect_file_metadata(item.get("path", ""),
        ensure_duration=False).get("exists")) == desired]
    items, sort_by, sort_dir = sort_library_items(items, sort_by=filters.get("sort_by", "relevance"),
    sort_dir=filters.get("sort_dir", "desc"))
    filters["sort_by"] = sort_by
    filters["sort_dir"] = sort_dir
    return items
def librarian_summary(prompt, filters, reasons, total, items):
    parts = []
    if filters.get("mode") == "review":
        parts.append("I treated this as a review-queue request and returned videos that likely need human attention.")
    elif filters.get("mode") == "duplicates":
        parts.append("I treated this as a duplicate-cleanup request and returned videos from likely duplicate groups.")
    elif filters.get("mode") == "cleanup":
        parts.append("I treated this as a cleanup request and focused on missing or broken index entries.")
    else:
        parts.append("I translated your request into library filters and semantic search.")
    if total:
        parts.append(f"I found {total} matching video{'s' if total != 1 else ''}.")
    else:
        parts.append("I did not find matching videos. Try a broader request or remove one filter.")
    if filters.get("tags"):
        parts.append("Tags: " + ", ".join(filters["tags"]) + ".")
    if filters.get("source_filter"):
        parts.append("Source filter: " + str(filters["source_filter"]) + ".")
    if filters.get("duration_min") is not None or filters.get("duration_max") is not None:
        lo = format_duration(filters.get("duration_min") or 0) if filters.get("duration_min") else "any"
        hi = format_duration(filters.get("duration_max") or 0) if filters.get("duration_max") else "any"
        parts.append(f"Duration range: {lo} to {hi}.")
    if filters.get("size_min_mb") is not None or filters.get("size_max_mb") is not None:
        lo = format_bytes((filters.get("size_min_mb") or 0) * 1024 * 1024) if filters.get("size_min_mb") else "any"
        hi = format_bytes((filters.get("size_max_mb") or 0) * 1024 * 1024) if filters.get("size_max_mb") else "any"
        parts.append(f"Size range: {lo} to {hi}.")
    parts.append(f"Sorted by {filters.get('sort_by')} {filters.get('sort_dir')}.")
    return " ".join(parts)
def librarian_suggestions_payload():
    return {
        "ok": True,
        "version": APP_VERSION,
        "suggestions": [
            "Show me long movies from Saved Messages",
            "Find short clips tagged Gay sorted by newest",
            "Show me large files over 1 GB",
            "Find videos from @channelname longer than 10 minutes",
            "Show me videos that probably need review",
            "Find missing files in the index",
            "Show duplicate candidates",
            "Find recent high scoring movies",
        ],
        "capabilities": [
            "Natural-language search over tags, source, duration, size, and sort order",
            "Private local parsing with the existing semantic CLIP/FAISS index",
            "Review, duplicate, and cleanup intent detection",
            "Direct result links into the existing video detail page",
        ],
    }
EXPORT_FIELDS = [
    "path", "filename", "directory", "date", "exists", "size", "size_human",
    "duration", "duration_human", "source", "source_ref", "modified",
    "tags", "manual_override", "final_score", "semantic_score", "notes",
    "message_id", "message_key", "message_date", "downloaded_at", "sha256",
]
def export_request_params(request):
    q = request.query.get("q", "").strip()
    sort_by = request.query.get("sort", "relevance" if q else "path")
    sort_dir = request.query.get("dir", "desc" if sort_by in {"relevance", "score", "size", "duration",
    "modified"} else "asc")
    try:
        min_score = float(request.query.get("min", "0") or 0)
    except Exception:
        min_score = 0.0
    return {
        "q": q,
        "tag_filter": request.query.get("tag", "").strip(),
        "source_filter": request.query.get("source", "").strip(),
        "min_score": min_score,
        "duration_min": parse_float_or_none(request.query.get("duration_min", "")),
        "duration_max": parse_float_or_none(request.query.get("duration_max", "")),
        "size_min_mb": parse_float_or_none(request.query.get("size_min_mb", "")),
        "size_max_mb": parse_float_or_none(request.query.get("size_max_mb", "")),
        "sort_by": sort_by,
        "sort_dir": sort_dir,
    }
def library_export_rows(params):
    items = build_library_items(
        q=params.get("q", ""),
        tag_filter=params.get("tag_filter", ""),
        min_score=float(params.get("min_score") or 0),
        source_filter=params.get("source_filter", ""),
        duration_min=params.get("duration_min"),
        duration_max=params.get("duration_max"),
        size_min_mb=params.get("size_min_mb"),
        size_max_mb=params.get("size_max_mb"),
    )
    items, sort_by, sort_dir = sort_library_items(items, sort_by=params.get("sort_by", "path"),
    sort_dir=params.get("sort_dir", "asc"))
    rows = []
    for item in items:
        payload = serialize_item(item)
        meta = metadata_store.get(payload["path"], {}) or {}
        payload["tags"] = "|".join(payload.get("tags") or [])
        payload["notes"] = str(meta.get("notes") or "")
        payload["message_id"] = meta.get("message_id", "")
        payload["message_key"] = meta.get("message_key", "")
        payload["message_date"] = meta.get("message_date", "")
        payload["downloaded_at"] = meta.get("downloaded_at", "")
        payload["sha256"] = meta.get("sha256", "")
        rows.append({field: payload.get(field, "") for field in EXPORT_FIELDS})
    return rows, sort_by, sort_dir
def library_export_csv(rows):
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=EXPORT_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return out.getvalue()
def split_import_tags(value):
    if isinstance(value, list):
        raw = value
    else:
        raw = re.split(r"[|,]", str(value or ""))
    cleaned = []
    known = {tag.lower(): tag for tag in PREDEFINED_TAGS}
    for tag in raw:
        tag = normalize_tag_name(tag)
        if not tag:
            continue
        cleaned.append(known.get(tag.lower(), tag))
    # Preserve order while deduping.
    out = []
    seen = set()
    for tag in cleaned:
        key = tag.lower()
        if key not in seen:
            out.append(tag)
            seen.add(key)
    return out
def parse_library_import_text(text, filename=""):
    text = text or ""
    name = str(filename or "").lower()
    stripped = text.lstrip()
    if name.endswith(".csv") or (stripped and not stripped.startswith(("{", "["))):
        reader = csv.DictReader(io.StringIO(text))
        return [dict(row) for row in reader]
    parsed = json.loads(text)
    if isinstance(parsed, dict):
        return parsed.get("items") or parsed.get("rows") or []
    if isinstance(parsed, list):
        return parsed
    return []
def apply_library_import_rows(rows, import_tags=True, import_notes=True, dry_run=False):
    result = {"checked": 0, "updated": 0, "skipped": 0, "missing": 0, "errors": []}
    for raw in rows:
        result["checked"] += 1
        try:
            if not isinstance(raw, dict):
                result["skipped"] += 1
                continue
            rel_path = str(raw.get("path") or "").strip()
            if not rel_path:
                result["skipped"] += 1
                continue
            if rel_path not in tags:
                result["missing"] += 1
                continue
            changed = False
            if import_tags and "tags" in raw:
                new_tags = [tag for tag in split_import_tags(raw.get("tags")) if tag in PREDEFINED_TAGS]
                if not dry_run:
                    set_manual_override(rel_path, new_tags, tags.get(rel_path, []))
                changed = True
            if import_notes and "notes" in raw:
                notes = str(raw.get("notes") or "")[:20000]
                if not dry_run:
                    metadata_store.setdefault(rel_path, {})["notes"] = notes
                    metadata_store.setdefault(rel_path, {})["notes_updated_at"] = now_stamp()
                changed = True
            if changed:
                result["updated"] += 1
            else:
                result["skipped"] += 1
        except Exception as e:
            result["errors"].append(str(e))
    if not dry_run and result["updated"]:
        save_state()
        save_feedback()
    result["ok"] = True
    result["dry_run"] = bool(dry_run)
    return result
def collection_items(collection, page=1, sort_override=None, dir_override=None):
    collection = normalize_collection_payload(collection, collection)
    items = build_library_items(
        q=collection.get("q", ""),
        tag_filter=collection.get("tag", ""),
        min_score=float(collection.get("min") or 0.0),
        source_filter=collection.get("source", ""),
        duration_min=parse_float_or_none(collection.get("duration_min")),
        duration_max=parse_float_or_none(collection.get("duration_max")),
        size_min_mb=parse_float_or_none(collection.get("size_min_mb")),
        size_max_mb=parse_float_or_none(collection.get("size_max_mb")),
    )
    sort_by = sort_override or collection.get("sort") or "path"
    sort_dir = dir_override or collection.get("dir") or "asc"
    current_user = auth_current_user(request)
    items = [item for item in items if user_can_access_path(current_user, item.get("path", ""))]
    items, sort_by, sort_dir = sort_library_items(items, sort_by=sort_by, sort_dir=sort_dir)
    paged_items, page, total_pages, total_items = paginate_items(items, page)
    return {
        "items": [serialize_item(i) for i in paged_items],
        "page": page,
        "total_pages": total_pages,
        "total_items": total_items,
        "sort": sort_by,
        "dir": sort_dir,
    }
def collection_counts():
    counts = {}
    for col in get_smart_collections():
        try:
            counts[col["id"]] = len(build_library_items(
                q=col.get("q", ""),
                tag_filter=col.get("tag", ""),
                min_score=float(col.get("min") or 0.0),
                source_filter=col.get("source", ""),
                duration_min=parse_float_or_none(col.get("duration_min")),
                duration_max=parse_float_or_none(col.get("duration_max")),
                size_min_mb=parse_float_or_none(col.get("size_min_mb")),
                size_max_mb=parse_float_or_none(col.get("size_max_mb")),
            ))
        except Exception:
            counts[col["id"]] = 0
    return counts
# ================= RULES ENGINE HELPERS =================
RULE_CONDITION_FIELDS = [
    "source_contains",
    "path_contains",
    "tags_all",
    "tags_any",
    "missing_tags",
    "duration_min",
    "duration_max",
    "size_min_mb",
    "size_max_mb",
    "exists",
]
RULE_ACTION_FIELDS = [
    "add_tags",
    "remove_tags",
    "set_tags",
    "append_note",
    "flag_review",
]
def rule_id_from_name(name):
    return safe_id(str(name or "rule").lower().replace(" ", "-")) or f"rule-{timestamp_for_filename()}"
def rule_list_field(value):
    if value is None:
        return []
    if isinstance(value, list):
        raw = value
    else:
        raw = re.split(r"[,;|\n]+", str(value))
    out = []
    seen = set()
    for item in raw:
        tag = normalize_tag_name(item)
        if not tag:
            continue
        key = tag.lower()
        if key in seen:
            continue
        # Keep only known tags for tag action/condition fields.
        matched = next((known for known in PREDEFINED_TAGS if known.lower() == key), None)
        if matched:
            out.append(matched)
            seen.add(key)
    return out
def rule_text_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        raw = value
    else:
        raw = re.split(r"[,;|\n]+", str(value))
    out = []
    seen = set()
    for item in raw:
        text = str(item or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        out.append(text[:120])
        seen.add(key)
    return out
def rule_float(value, default=""):
    if value in (None, ""):
        return default
    try:
        return str(float(value))
    except Exception:
        return default
def normalize_rule_payload(data, existing=None):
    existing = existing or {}
    data = data or {}
    name = str(data.get("name") or existing.get("name") or "Untitled Rule").strip()[:120] or "Untitled Rule"
    rule_id = safe_id(data.get("id") or existing.get("id") or rule_id_from_name(name))
    conditions_in = data.get("conditions") if isinstance(data.get("conditions"), dict) else {}
    conditions_existing = existing.get("conditions") if isinstance(existing.get("conditions"), dict) else {}
    actions_in = data.get("actions") if isinstance(data.get("actions"), dict) else {}
    actions_existing = existing.get("actions") if isinstance(existing.get("actions"), dict) else {}
    def cval(key, default=""):
        return conditions_in.get(key, data.get(key, conditions_existing.get(key, existing.get(key, default))))
    def aval(key, default=""):
        return actions_in.get(key, data.get(key, actions_existing.get(key, existing.get(key, default))))
    exists_raw = cval("exists", "")
    if exists_raw in (True, "true", "1", "yes", "exists", "present"):
        exists_value = "true"
    elif exists_raw in (False, "false", "0", "no", "missing"):
        exists_value = "false"
    else:
        exists_value = ""
    return {
        "id": rule_id,
        "name": name,
        "description": str(data.get("description") or existing.get("description") or "").strip()[:1000],
        "enabled": bool(data.get("enabled", existing.get("enabled", True))),
        "auto_apply": bool(data.get("auto_apply", existing.get("auto_apply", False))),
        "stop_after_match": bool(data.get("stop_after_match", existing.get("stop_after_match", False))),
        "conditions": {
            "source_contains": str(cval("source_contains", "") or "").strip()[:160],
            "path_contains": str(cval("path_contains", "") or "").strip()[:160],
            "tags_all": rule_list_field(cval("tags_all", [])),
            "tags_any": rule_list_field(cval("tags_any", [])),
            "missing_tags": rule_list_field(cval("missing_tags", [])),
            "duration_min": rule_float(cval("duration_min", "")),
            "duration_max": rule_float(cval("duration_max", "")),
            "size_min_mb": rule_float(cval("size_min_mb", "")),
            "size_max_mb": rule_float(cval("size_max_mb", "")),
            "exists": exists_value,
        },
        "actions": {
            "add_tags": rule_list_field(aval("add_tags", [])),
            "remove_tags": rule_list_field(aval("remove_tags", [])),
            "set_tags": rule_list_field(aval("set_tags", [])),
            "append_note": str(aval("append_note", "") or "").strip()[:2000],
            "flag_review": bool(aval("flag_review", False)),
        },
        "created_at": existing.get("created_at") or now_stamp(),
        "updated_at": now_stamp(),
        "last_run_at": existing.get("last_run_at", ""),
        "last_match_count": int(existing.get("last_match_count", 0) or 0),
        "last_change_count": int(existing.get("last_change_count", 0) or 0),
    }
def get_rules():
    raw = settings_data.setdefault("automation_rules", [])
    if not isinstance(raw, list):
        raw = []
    out = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        rule = normalize_rule_payload(item, item)
        if rule["id"] in seen:
            rule["id"] = f"{rule['id']}-{len(out)+1}"
        seen.add(rule["id"])
        out.append(rule)
    settings_data["automation_rules"] = out
    return out
def save_rules(rules):
    settings_data["automation_rules"] = [normalize_rule_payload(rule, rule) for rule in rules]
    save_settings()
    return get_rules()
def rule_conditions_summary(rule):
    c = rule.get("conditions", {}) or {}
    parts = []
    if c.get("source_contains"):
        parts.append(f"source contains {c['source_contains']}")
    if c.get("path_contains"):
        parts.append(f"path contains {c['path_contains']}")
    if c.get("tags_all"):
        parts.append("has all tags: " + ", ".join(c["tags_all"]))
    if c.get("tags_any"):
        parts.append("has any tag: " + ", ".join(c["tags_any"]))
    if c.get("missing_tags"):
        parts.append("missing tags: " + ", ".join(c["missing_tags"]))
    if c.get("duration_min"):
        parts.append(f"duration >= {format_duration(float(c['duration_min']))}")
    if c.get("duration_max"):
        parts.append(f"duration <= {format_duration(float(c['duration_max']))}")
    if c.get("size_min_mb"):
        parts.append(f"size >= {format_bytes(float(c['size_min_mb']) * 1024 * 1024)}")
    if c.get("size_max_mb"):
        parts.append(f"size <= {format_bytes(float(c['size_max_mb']) * 1024 * 1024)}")
    if c.get("exists") == "true":
        parts.append("file exists")
    elif c.get("exists") == "false":
        parts.append("file missing")
    return parts or ["all videos"]
def rule_actions_summary(rule):
    a = rule.get("actions", {}) or {}
    parts = []
    if a.get("set_tags"):
        parts.append("set tags: " + ", ".join(a["set_tags"]))
    if a.get("add_tags"):
        parts.append("add tags: " + ", ".join(a["add_tags"]))
    if a.get("remove_tags"):
        parts.append("remove tags: " + ", ".join(a["remove_tags"]))
    if a.get("append_note"):
        parts.append("append note")
    if a.get("flag_review"):
        parts.append("flag for review")
    return parts or ["no changes"]
def serialize_rule(rule):
    rule = normalize_rule_payload(rule, rule)
    rule["condition_summary"] = rule_conditions_summary(rule)
    rule["action_summary"] = rule_actions_summary(rule)
    return rule
def rule_matches_path(rule, rel_path):
    if rel_path not in tags:
        return False, "not indexed"
    c = rule.get("conditions", {}) or {}
    effective = set(get_effective_tags(rel_path))
    meta = collect_file_metadata(rel_path, ensure_duration=False)
    if c.get("source_contains"):
        wanted = str(c["source_contains"]).lower()
        source_text = " ".join([
            str(meta.get("source") or ""),
            str(meta.get("source_ref") or ""),
            str((metadata_store.get(rel_path) or {}).get("source") or ""),
            str((metadata_store.get(rel_path) or {}).get("source_ref") or ""),
        ]).lower()
        if wanted not in source_text:
            return False, "source mismatch"
    if c.get("path_contains") and str(c["path_contains"]).lower() not in rel_path.lower():
        return False, "path mismatch"
    tags_all = set(c.get("tags_all") or [])
    if tags_all and not tags_all.issubset(effective):
        return False, "missing required tag"
    tags_any = set(c.get("tags_any") or [])
    if tags_any and not tags_any.intersection(effective):
        return False, "missing any-tag match"
    missing_tags = set(c.get("missing_tags") or [])
    if missing_tags and missing_tags.intersection(effective):
        return False, "tag not missing"
    duration = float(meta.get("duration") or 0.0)
    if c.get("duration_min") and duration < float(c["duration_min"]):
        return False, "duration too short"
    if c.get("duration_max") and duration > float(c["duration_max"]):
        return False, "duration too long"
    size_mb = float(meta.get("size") or 0) / (1024 * 1024)
    if c.get("size_min_mb") and size_mb < float(c["size_min_mb"]):
        return False, "size too small"
    if c.get("size_max_mb") and size_mb > float(c["size_max_mb"]):
        return False, "size too large"
    if c.get("exists") == "true" and not bool(meta.get("exists")):
        return False, "file missing"
    if c.get("exists") == "false" and bool(meta.get("exists")):
        return False, "file exists"
    return True, "matched"
def update_tags_from_rule(rel_path, new_tags):
    new_tags = sanitize_tags(new_tags)
    override = get_manual_override(rel_path)
    if override and isinstance(override, dict):
        override["tags"] = new_tags
        override["edited_at"] = now_stamp()
        override["rule_updated"] = True
    else:
        tags[rel_path] = new_tags
    return new_tags
def apply_rule_to_path(rule, rel_path, dry_run=False, trigger="manual"):
    matched, reason = rule_matches_path(rule, rel_path)
    result = {
        "path": rel_path,
        "matched": bool(matched),
        "changed": False,
        "reason": reason,
        "before_tags": list(get_effective_tags(rel_path)) if rel_path in tags else [],
        "after_tags": list(get_effective_tags(rel_path)) if rel_path in tags else [],
        "actions": [],
    }
    if not matched:
        return result
    actions = rule.get("actions", {}) or {}
    current = list(get_effective_tags(rel_path))
    new_tags = list(current)
    if actions.get("set_tags"):
        new_tags = list(actions.get("set_tags") or [])
        result["actions"].append("set_tags")
    else:
        for tag in actions.get("remove_tags") or []:
            if tag in new_tags:
                new_tags = [t for t in new_tags if t != tag]
                result["actions"].append(f"remove:{tag}")
        for tag in actions.get("add_tags") or []:
            if tag not in new_tags:
                new_tags.append(tag)
                result["actions"].append(f"add:{tag}")
    new_tags = sanitize_tags(new_tags)
    if new_tags != current:
        result["changed"] = True
        result["after_tags"] = new_tags
        if not dry_run:
            update_tags_from_rule(rel_path, new_tags)
    note = str(actions.get("append_note") or "").strip()
    if note:
        result["actions"].append("append_note")
        result["changed"] = True
        if not dry_run:
            meta = metadata_store.setdefault(rel_path, {})
            notes = str(meta.get("notes") or "")
            stamp = now_stamp()
            block = f"[{stamp}] Rule '{rule.get('name')}' ({trigger}): {note}"
            meta["notes"] = (notes + "\n" + block).strip()[-20000:]
            meta["notes_updated_at"] = stamp
    if actions.get("flag_review"):
        result["actions"].append("flag_review")
        result["changed"] = True
        if not dry_run:
            meta = metadata_store.setdefault(rel_path, {})
            flags = meta.setdefault("rule_review_flags", [])
            entry = {
                "rule_id": rule.get("id"),
                "rule_name": rule.get("name"),
                "trigger": trigger,
                "flagged_at": now_stamp(),
            }
            flags = [f for f in flags if f.get("rule_id") != rule.get("id")]
            flags.insert(0, entry)
            meta["rule_review_flags"] = flags[:20]
    if result["changed"] and not dry_run:
        meta = metadata_store.setdefault(rel_path, {})
        hits = meta.setdefault("rule_hits", [])
        hits.insert(0, {
            "rule_id": rule.get("id"),
            "rule_name": rule.get("name"),
            "trigger": trigger,
            "applied_at": now_stamp(),
            "actions": result["actions"],
        })
        meta["rule_hits"] = hits[:50]
        meta["last_rule_applied_at"] = now_stamp()
    return result
def run_rules(rule_ids=None, dry_run=False, limit=0, only_auto=False, trigger="manual"):
    rules = [rule for rule in get_rules() if rule.get("enabled", True)]
    if only_auto:
        rules = [rule for rule in rules if rule.get("auto_apply")]
    if rule_ids:
        wanted = {str(item) for item in rule_ids}
        rules = [rule for rule in rules if rule.get("id") in wanted]
    paths = sorted(tags.keys())
    if limit:
        try:
            paths = paths[:max(1, min(int(limit), len(paths)))]
        except Exception:
            pass
    job_id = create_job("rules", "Rules Engine", total=len(paths), metadata={"dry_run": bool(dry_run),
    "rules": [r.get("id") for r in rules]})
    matched = 0
    changed = 0
    rule_counts = {rule.get("id"): {"matched": 0, "changed": 0} for rule in rules}
    samples = []
    errors = []
    try:
        for idx, rel_path in enumerate(paths, start=1):
            update_job(job_id, processed=idx - 1, current=rel_path)
            if job_cancel_requested(job_id):
                finish_job(job_id, "cancelled", "Rules run cancelled")
                return {"ok": False, "cancelled": True, "job_id": job_id, "matched": matched, "changed":
                changed, "samples": samples, "errors": errors}
            for rule in rules:
                try:
                    result = apply_rule_to_path(rule, rel_path, dry_run=dry_run, trigger=trigger)
                    if result.get("matched"):
                        matched += 1
                        rule_counts[rule.get("id")]["matched"] += 1
                        if len(samples) < 50:
                            samples.append({"rule": rule.get("name"), **result})
                    if result.get("changed"):
                        changed += 1
                        rule_counts[rule.get("id")]["changed"] += 1
                    if result.get("matched") and rule.get("stop_after_match"):
                        break
                except Exception as e:
                    logger.exception("Rule %s failed for %s", rule.get("id"), rel_path)
                    errors.append({"rule_id": rule.get("id"), "path": rel_path, "error": str(e)})
        if not dry_run and changed:
            save_state()
            save_feedback()
        now = now_stamp()
        if not dry_run:
            current = get_rules()
            for rule in current:
                stats = rule_counts.get(rule.get("id"))
                if stats:
                    rule["last_run_at"] = now
                    rule["last_match_count"] = stats["matched"]
                    rule["last_change_count"] = stats["changed"]
            settings_data["automation_rules"] = current
            save_settings()
        finish_job(job_id, "complete", f"Rules complete: {changed} change(s)")
        return {"ok": True, "dry_run": bool(dry_run), "job_id": job_id, "matched": matched, "changed":
        changed, "rules": rule_counts, "samples": samples, "errors": errors}
    except Exception as e:
        finish_job(job_id, "failed", str(e))
        raise
def apply_auto_rules_to_path(rel_path, trigger="ingest"):
    changed = 0
    matched = 0
    for rule in get_rules():
        if not rule.get("enabled", True) or not rule.get("auto_apply"):
            continue
        result = apply_rule_to_path(rule, rel_path, dry_run=False, trigger=trigger)
        if result.get("matched"):
            matched += 1
        if result.get("changed"):
            changed += 1
        if result.get("matched") and rule.get("stop_after_match"):
            break
    if changed:
        save_feedback()
        logger.info("Auto rules applied to %s: matched=%d changed=%d", rel_path, matched, changed)
    return {"matched": matched, "changed": changed}
# ================= BULK EDIT / REVIEW HELPERS =================
def bulk_update_tags(paths, selected_tags, action="add"):
    valid_paths = [str(path) for path in paths if str(path) in tags]
    edit_tags = sanitize_tags(selected_tags)
    action = str(action or "add").lower()
    if action not in {"add", "remove", "set"}:
        raise ValueError("Invalid bulk tag action")
    if not edit_tags and action in {"add", "remove", "set"}:
        raise ValueError("Choose at least one valid tag")
    updated = 0
    failed = []
    for rel_path in valid_paths:
        try:
            current = list(get_effective_tags(rel_path))
            if action == "add":
                new_tags = current + [tag for tag in edit_tags if tag not in current]
            elif action == "remove":
                new_tags = [tag for tag in current if tag not in edit_tags]
            else:
                new_tags = list(edit_tags)
            new_tags = sanitize_tags(new_tags)
            set_manual_override(rel_path, new_tags, tags.get(rel_path, []))
            updated += 1
        except Exception as e:
            logger.exception("Bulk tag update failed for %s", rel_path)
            failed.append({"path": rel_path, "error": str(e)})
    return {"updated": updated, "failed": failed, "action": action, "tags": edit_tags}
async def reprocess_selected_files_job(paths):
    global bulk_job, faiss_index, faiss_keys
    if bulk_job.get("running"):
        return
    clean_paths = [str(path) for path in paths if str(path) in tags]
    bulk_job = {
        "running": True,
        "status": "running",
        "processed": 0,
        "total": len(clean_paths),
        "current": "",
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "finished_at": "",
        "errors": 0,
        "mode": "reprocess_selected",
        "cancel_requested": False,
    }
    try:
        for rel_path in clean_paths:
            if bulk_job.get("cancel_requested"):
                bulk_job["status"] = "cancelled"
                break
            bulk_job["current"] = rel_path
            try:
                await asyncio.to_thread(reprocess_file, rel_path)
            except Exception:
                bulk_job["errors"] += 1
                logger.exception("Selected reprocess failed for %s", rel_path)
            bulk_job["processed"] += 1
        if bulk_job.get("status") != "cancelled":
            faiss_index, faiss_keys = build_faiss_index()
            bulk_job["status"] = "complete"
    finally:
        bulk_job["running"] = False
        bulk_job["finished_at"] = datetime.now().isoformat(timespec="seconds")
        bulk_job["current"] = ""
def review_skip_map():
    skips = feedback_data.setdefault("review_skips", {})
    return skips if isinstance(skips, dict) else {}
def review_reason_for_path(rel_path):
    if rel_path not in tags:
        return []
    reasons = []
    rule_flags = (metadata_store.get(rel_path, {}) or {}).get("rule_review_flags") or []
    for flag in rule_flags[:3]:
        name = flag.get("rule_name") or flag.get("rule_id") or "rule"
        reasons.append(f"Rule flagged for review: {name}")
    if reasons:
        return reasons
    if get_manual_override(rel_path):
        return []
    abs_path = find_existing_file(rel_path)
    file_meta = collect_file_metadata(rel_path, abs_path=abs_path, ensure_duration=False)
    if not file_meta.get("exists"):
        reasons.append("Missing file")
    effective = list(get_effective_tags(rel_path))
    if not effective:
        reasons.append("No tags")
    score_map = scores_store.get(rel_path, {}) or {}
    if not score_map:
        reasons.append("No AI score data")
    else:
        scored = []
        for tag, value in score_map.items():
            if tag in {"Clip", "Movie"}:
                continue
            try:
                scored.append(float(value))
            except Exception:
                pass
        max_score = max(scored) if scored else 0.0
        if max_score and max_score < 0.34:
            reasons.append(f"Low confidence max score {max_score:.2f}")
    format_tags = {"Clip", "Movie"}.intersection(effective)
    if len(format_tags) > 1:
        reasons.append("Conflicting Clip/Movie tags")
    orientation_tags = {"Gay", "Straight", "Bisexual"}.intersection(effective)
    if len(orientation_tags) > 1:
        reasons.append("Multiple orientation tags")
    return reasons
def build_review_queue(limit=50, include_skipped=False):
    skips = review_skip_map()
    items = []
    checked = 0
    skipped_count = 0
    for rel_path in sorted(tags.keys()):
        checked += 1
        is_skipped = rel_path in skips
        if is_skipped and not include_skipped:
            skipped_count += 1
            continue
        reasons = review_reason_for_path(rel_path)
        if not reasons:
            continue
        payload = serialize_item({
            "path": rel_path,
            "tags": get_effective_tags(rel_path),
            "score_map": scores_store.get(rel_path, {}),
            "final_score": None,
            "semantic_score": None,
            "manual_override": False,
        })
        payload["review_reasons"] = reasons
        payload["review_skipped"] = is_skipped
        payload["review_skip_detail"] = skips.get(rel_path)
        items.append(payload)
        if len(items) >= limit:
            break
    return {
        "items": items,
        "count": len(items),
        "checked": checked,
        "skipped_count": skipped_count,
        "include_skipped": include_skipped,
    }
def review_queue_count(limit=10000):
    skips = review_skip_map()
    count = 0
    for rel_path in sorted(tags.keys()):
        if rel_path in skips:
            continue
        if review_reason_for_path(rel_path):
            count += 1
            if count >= limit:
                break
    return count
def remove_index_entry_for_missing_file(rel_path):
    """Remove an indexed record whose media file is no longer on disk.
    This intentionally does not delete a video file because the sweep is for
    records where the video is already missing. It removes thumbnails/previews,
    cached metadata, manual override state, and FAISS/AI index data.
    """
    rel_path = str(rel_path or "").strip()
    if not rel_path:
        return {"path": rel_path, "removed": False, "reason": "blank path"}
    removed_hash = None
    meta = metadata_store.get(rel_path) or {}
    for key in ("sha256", "hash", "file_hash"):
        value = meta.get(key)
        if value:
            removed_hash = str(value)
            state_hashes.discard(removed_hash)
            break
    for derived in (thumb_abs_path_for_file(rel_path), preview_abs_path_for_file(rel_path)):
        try:
            if os.path.exists(derived):
                os.remove(derived)
        except Exception:
            logger.warning("Could not delete orphaned derived file %s", derived)
    delete_scene_thumbnails_for_file(rel_path)
    tags.pop(rel_path, None)
    scores_store.pop(rel_path, None)
    embeddings_store.pop(rel_path, None)
    metadata_store.pop(rel_path, None)
    feedback_data.get("manual_overrides", {}).pop(rel_path, None)
    return {"path": rel_path, "removed": True, "removed_hash": removed_hash}
def sweep_missing_index_entries(mode="manual"):
    """Remove index records for videos that no longer exist on disk."""
    global faiss_index, faiss_keys
    acquired = index_sweep_lock.acquire(blocking=False)
    if not acquired:
        return {
            "ok": False,
            "already_running": True,
            "error": "Index sweep is already running",
            "status": dict(index_sweep_status),
        }
    started = datetime.now().isoformat(timespec="seconds")
    indexed_paths = list(tags.keys())
    job_id = create_job("index_sweep", f"Index sweep ({mode})", total=len(indexed_paths), metadata={"mode":
    mode})
    removed_items = []
    errors = 0
    checked = 0
    kept = 0
    index_sweep_status.update({
        "running": True,
        "status": "running",
        "mode": mode,
        "started_at": started,
        "finished_at": "",
        "last_run_at": started,
        "total_indexed_before": len(indexed_paths),
        "checked": 0,
        "removed": 0,
        "kept": 0,
        "errors": 0,
        "current": "",
        "details": [],
        "job_id": job_id,
    })
    logger.info("Index sweep started mode=%s indexed=%d", mode, len(indexed_paths))
    try:
        for rel_path in indexed_paths:
            wait_job_if_paused_sync(job_id)
            if job_cancel_requested(job_id):
                index_sweep_status["status"] = "cancelled"
                finish_job(job_id, "cancelled", "Cancelled by user")
                return {"ok": False, "cancelled": True, "checked": checked, "removed": len(removed_items),
                "kept": kept, "errors": errors, "status": dict(index_sweep_status)}
            checked += 1
            index_sweep_status["checked"] = checked
            index_sweep_status["current"] = rel_path
            update_job(job_id, current=rel_path, processed=checked, errors=errors)
            try:
                abs_path = find_existing_file(rel_path)
                if abs_path and os.path.exists(abs_path):
                    kept += 1
                    index_sweep_status["kept"] = kept
                    continue
                result = remove_index_entry_for_missing_file(rel_path)
                if result.get("removed"):
                    removed_items.append(rel_path)
                    index_sweep_status["removed"] = len(removed_items)
                    if len(index_sweep_status["details"]) < 100:
                        index_sweep_status["details"].append(rel_path)
            except Exception:
                errors += 1
                index_sweep_status["errors"] = errors
                logger.exception("Index sweep failed for %s", rel_path)
        if removed_items:
            save_state()
            save_feedback()
            faiss_index, faiss_keys = build_faiss_index()
        finished = datetime.now().isoformat(timespec="seconds")
        index_sweep_status.update({
            "running": False,
            "status": "complete",
            "finished_at": finished,
            "current": "",
            "checked": checked,
            "removed": len(removed_items),
            "kept": kept,
            "errors": errors,
        })
        update_next_index_sweep_time()
        finish_job(job_id, "complete", f"Checked {checked}; removed {len(removed_items)}; errors {errors}")
        logger.info(
            "Index sweep complete mode=%s checked=%d removed=%d kept=%d errors=%d",
            mode,
            checked,
            len(removed_items),
            kept,
            errors,
        )
        return {
            "ok": True,
            "checked": checked,
            "removed": len(removed_items),
            "kept": kept,
            "errors": errors,
            "removed_paths": removed_items[:250],
            "status": dict(index_sweep_status),
        }
    finally:
        if index_sweep_status.get("running"):
            index_sweep_status["running"] = False
            index_sweep_status["status"] = "failed" if errors else "complete"
            index_sweep_status["finished_at"] = datetime.now().isoformat(timespec="seconds")
            index_sweep_status["current"] = ""
            update_next_index_sweep_time()
            if 'job_id' in locals() and get_job(job_id) and get_job(job_id).get("status") == "running":
                finish_job(job_id, "failed" if errors else "complete", "Index sweep ended unexpectedly")
        index_sweep_lock.release()
async def periodic_missing_index_sweep():
    """Background maintenance loop: four automatic index sweeps per day."""
    # Give startup, HTTP routing, and initial sync a few minutes before the
    # first maintenance pass. Manual sweep is available immediately in Admin.
    initial_delay = 10 * 60
    update_next_index_sweep_time(initial_delay)
    await asyncio.sleep(initial_delay)
    while True:
        try:
            await asyncio.to_thread(sweep_missing_index_entries, "scheduled")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Scheduled missing-file index sweep failed")
        update_next_index_sweep_time(INDEX_SWEEP_INTERVAL_SECONDS)
        await asyncio.sleep(INDEX_SWEEP_INTERVAL_SECONDS)
def sanitize_video_filename(name, old_filename):
    name = str(name or "").strip()
    name = os.path.basename(name)
    name = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        raise ValueError("Filename cannot be blank")
    old_ext = os.path.splitext(old_filename)[1]
    new_root, new_ext = os.path.splitext(name)
    if not new_ext and old_ext:
        name = f"{name}{old_ext}"
    return name
def find_existing_file_with_root(rel_path):
    rel_path = str(rel_path or "").strip()
    if os.path.isabs(rel_path) and os.path.exists(rel_path):
        return rel_path, os.path.dirname(os.path.abspath(rel_path))
    for root in candidate_storage_roots():
        try:
            candidate = safe_abs_path(rel_path, root)
            if os.path.exists(candidate):
                return candidate, root
        except Exception:
            pass
    return None, None
def rename_indexed_file(rel_path, requested_name):
    global faiss_index, faiss_keys
    if rel_path not in tags:
        raise FileNotFoundError("File not found in index")
    abs_path, root = find_existing_file_with_root(rel_path)
    if not abs_path or not root:
        raise FileNotFoundError("Video file not found on disk")
    old_filename = os.path.basename(rel_path)
    new_filename = sanitize_video_filename(requested_name, old_filename)
    old_dir_rel = os.path.dirname(rel_path)
    new_rel_path = os.path.join(old_dir_rel, new_filename) if old_dir_rel else new_filename
    new_abs_path = safe_abs_path(new_rel_path, root)
    if os.path.abspath(abs_path) == os.path.abspath(new_abs_path):
        return {"old_path": rel_path, "new_path": rel_path, "renamed": False}
    if os.path.exists(new_abs_path):
        raise FileExistsError(f"A file named {new_filename} already exists in this folder")
    os.makedirs(os.path.dirname(new_abs_path), exist_ok=True)
    os.rename(abs_path, new_abs_path)
    old_thumb = thumb_abs_path_for_file(rel_path)
    new_thumb = thumb_abs_path_for_file(new_rel_path)
    old_preview = preview_abs_path_for_file(rel_path)
    new_preview = preview_abs_path_for_file(new_rel_path)
    for old_derived, new_derived in ((old_thumb, new_thumb), (old_preview, new_preview)):
        try:
            if os.path.exists(old_derived):
                os.makedirs(os.path.dirname(new_derived), exist_ok=True)
                os.replace(old_derived, new_derived)
        except Exception:
            logger.warning("Could not rename derived file %s -> %s", old_derived, new_derived)
    old_scene_files = sorted(scene_thumbnail_paths_for_file(rel_path))
    for idx, old_scene in enumerate(old_scene_files, start=1):
        try:
            if os.path.exists(old_scene):
                new_scene = scene_abs_path_for_file(new_rel_path, idx)
                os.makedirs(os.path.dirname(new_scene), exist_ok=True)
                os.replace(old_scene, new_scene)
        except Exception:
            logger.warning("Could not rename scene thumbnail %s", old_scene)
    tags[new_rel_path] = tags.pop(rel_path)
    if rel_path in scores_store:
        scores_store[new_rel_path] = scores_store.pop(rel_path)
    if rel_path in embeddings_store:
        embeddings_store[new_rel_path] = embeddings_store.pop(rel_path)
    if rel_path in metadata_store:
        metadata_store[new_rel_path] = metadata_store.pop(rel_path)
        metadata_store[new_rel_path]["renamed_at"] = datetime.now().isoformat(timespec="seconds")
        timeline = metadata_store[new_rel_path].get("scene_timeline")
        if isinstance(timeline, dict):
            for scene in timeline.get("scenes", []):
                try:
                    idx = int(scene.get("index") or 0)
                    if idx:
                        scene["thumbnail_file"] = scene_rel_path_for_file(new_rel_path, idx)
                        scene["thumbnail_url"] = scene_url_for_file(new_rel_path, idx)
                except Exception:
                    pass
    manual_overrides = feedback_data.get("manual_overrides", {})
    if rel_path in manual_overrides:
        manual_overrides[new_rel_path] = manual_overrides.pop(rel_path)
    for item in feedback_data.get("history", []):
        if item.get("path") == rel_path:
            item["path"] = new_rel_path
    save_state()
    save_feedback()
    faiss_index, faiss_keys = build_faiss_index()
    logger.info("Renamed indexed file %s -> %s", rel_path, new_rel_path)
    return {"old_path": rel_path, "new_path": new_rel_path, "renamed": True}
# ================= WATCH / PLAYBACK HELPERS =================
def playback_percent(position, duration):
    try:
        position = float(position or 0.0)
        duration = float(duration or 0.0)
        if duration <= 0:
            return 0.0
        return round(max(0.0, min((position / duration) * 100.0, 100.0)), 2)
    except Exception:
        return 0.0
def watch_state_for_path(rel_path):
    meta = metadata_store.get(rel_path, {}) or {}
    position = 0.0
    duration = 0.0
    try:
        position = float(meta.get("playback_position") or 0.0)
    except Exception:
        position = 0.0
    try:
        duration = float(meta.get("playback_duration") or meta.get("duration") or 0.0)
    except Exception:
        duration = 0.0
    return {
        "watch_later": bool(meta.get("watch_later")),
        "watched": bool(meta.get("watched")),
        "playback_position": position,
        "playback_position_human": format_duration(position),
        "playback_duration": duration,
        "playback_duration_human": format_duration(duration),
        "playback_percent": playback_percent(position, duration),
        "last_watched_at": str(meta.get("last_watched_at") or ""),
        "watch_later_added_at": str(meta.get("watch_later_added_at") or ""),
        "watched_at": str(meta.get("watched_at") or ""),
    }
def apply_watch_state_to_payload(payload):
    state = watch_state_for_path(payload.get("path"))
    payload.update(state)
    return payload
def personal_state_for_path(rel_path):
    meta = metadata_store.get(rel_path, {}) or {}
    try:
        rating = int(meta.get("rating") or 0)
    except Exception:
        rating = 0
    rating = max(0, min(rating, 5))
    state_name = str(meta.get("personal_state") or "unreviewed").strip() or "unreviewed"
    allowed_states = {"unreviewed", "watch_next", "keep", "maybe", "delete_candidate", "archived"}
    if state_name not in allowed_states:
        state_name = "unreviewed"
    return {
        "favorite": bool(meta.get("favorite")),
        "rating": rating,
        "personal_state": state_name,
        "personal_state_label": personal_state_label(state_name),
        "personal_updated_at": str(meta.get("personal_updated_at") or ""),
    }
def personal_state_label(value):
    labels = {
        "unreviewed": "Unreviewed",
        "watch_next": "Watch Next",
        "keep": "Keep",
        "maybe": "Maybe",
        "delete_candidate": "Delete Candidate",
        "archived": "Archived",
    }
    return labels.get(str(value or ""), "Unreviewed")
def apply_personal_state_to_payload(payload):
    payload.update(personal_state_for_path(payload.get("path")))
    return payload
def personal_library_items(kind="all", limit=200):
    kind = str(kind or "all").strip().lower()
    rows = []
    for rel_path in list(tags.keys()):
        personal = personal_state_for_path(rel_path)
        include = False
        if kind == "favorites":
            include = personal.get("favorite")
        elif kind == "rated":
            include = int(personal.get("rating") or 0) > 0
        elif kind in {"watch_next", "keep", "maybe", "delete_candidate", "archived", "unreviewed"}:
            include = personal.get("personal_state") == kind
        else:
            include = personal.get("favorite") or int(personal.get("rating") or 0) > 0 or personal.get("personal_state") != "unreviewed"
        if include:
            row = serialize_item({
                "path": rel_path,
                "tags": get_effective_tags(rel_path),
                "score_map": scores_store.get(rel_path, {}),
                "manual_override": get_manual_override(rel_path) is not None,
            })
            rows.append(row)
    rows.sort(key=lambda row: str(row.get("personal_updated_at") or row.get("last_watched_at") or
    row.get("modified") or ""), reverse=True)
    return rows[:max(1, min(int(limit or 200), 1000))]
def library_map_payload(limit=25):
    """Return visual library grouping data for the Library Map UI."""
    rows = []
    totals = {
        "videos": 0,
        "existing": 0,
        "missing": 0,
        "size": 0,
        "duration": 0.0,
        "favorites": 0,
        "rated": 0,
        "watch_next": 0,
        "watched": 0,
    }
    by_directory = {}
    by_source = {}
    by_tag = {}
    by_month = {}
    by_state = {}
    by_rating = {str(i): {"label": f"{i} star" if i == 1 else f"{i} stars", "rating": i, "count": 0, "size":
    0, "duration": 0.0} for i in range(1, 6)}
    for rel_path in sorted(tags.keys()):
        file_meta = collect_file_metadata(rel_path, ensure_duration=False)
        personal = personal_state_for_path(rel_path)
        watch = watch_state_for_path(rel_path)
        file_tags = get_effective_tags(rel_path)
        size = int(file_meta.get("size") or 0)
        duration = float(file_meta.get("duration") or 0.0)
        source = file_meta.get("source") or "Unknown source"
        directory = os.path.dirname(rel_path) or "Root"
        month = "Unknown"
        meta = metadata_store.get(rel_path, {}) or {}
        date_value = str(meta.get("message_date") or meta.get("downloaded_at") or file_meta.get("modified") or
        "")
        if len(date_value) >= 7 and re.match(r"^\d{4}-\d{2}", date_value):
            month = date_value[:7]
        else:
            path_match = re.search(r"(20\d{2})[-_/](\d{2})", rel_path)
            if path_match:
                month = f"{path_match.group(1)}-{path_match.group(2)}"
        totals["videos"] += 1
        totals["existing"] += 1 if file_meta.get("exists") else 0
        totals["missing"] += 0 if file_meta.get("exists") else 1
        totals["size"] += size
        totals["duration"] += duration
        totals["favorites"] += 1 if personal.get("favorite") else 0
        totals["rated"] += 1 if personal.get("rating") else 0
        totals["watch_next"] += 1 if personal.get("personal_state") == "watch_next" else 0
        totals["watched"] += 1 if watch.get("watched") else 0
        def add_group(bucket, key, label=None, extra=None):
            label = label or key
            row = bucket.setdefault(key, {"key": key, "label": label, "count": 0, "size": 0, "duration": 0.0,
            "favorites": 0, "watched": 0})
            row["count"] += 1
            row["size"] += size
            row["duration"] += duration
            row["favorites"] += 1 if personal.get("favorite") else 0
            row["watched"] += 1 if watch.get("watched") else 0
            if extra:
                row.update(extra)
            return row
        add_group(by_directory, directory)
        add_group(by_source, source)
        add_group(by_month, month)
        add_group(by_state, personal.get("personal_state"), personal.get("personal_state_label"))
        if personal.get("rating"):
            rkey = str(personal.get("rating"))
            by_rating[rkey]["count"] += 1
            by_rating[rkey]["size"] += size
            by_rating[rkey]["duration"] += duration
        for tag in file_tags:
            add_group(by_tag, tag)
    def finish_rows(bucket, limit_count=limit, reverse=True):
        out = []
        for row in bucket.values():
            count = int(row.get("count") or 0)
            total_size = int(row.get("size") or 0)
            total_duration = float(row.get("duration") or 0.0)
            out.append({
                **row,
                "count": count,
                "size": total_size,
                "size_human": format_bytes(total_size),
                "duration": total_duration,
                "duration_human": format_duration(total_duration),
                "average_duration": total_duration / count if count else 0.0,
                "average_duration_human": format_duration(total_duration / count) if count else "—",
                "percent": round((count / totals["videos"]) * 100.0, 2) if totals["videos"] else 0.0,
            })
        out.sort(key=lambda r: (r.get("count", 0), r.get("size", 0)), reverse=reverse)
        if limit_count and len(out) > int(limit_count):
            return out[:int(limit_count)]
        return out
    rating_rows = [row for row in finish_rows(by_rating, limit_count=0) if row.get("count")]
    rating_rows.sort(key=lambda row: int(row.get("rating") or 0), reverse=True)
    totals["size_human"] = format_bytes(totals["size"])
    totals["duration_human"] = format_duration(totals["duration"])
    return {
        "ok": True,
        "version": APP_VERSION,
        "generated_at": now_stamp(),
        "totals": totals,
        "directories": finish_rows(by_directory),
        "sources": finish_rows(by_source),
        "tags": finish_rows(by_tag),
        "months": finish_rows(by_month, limit_count=36),
        "states": finish_rows(by_state, limit_count=0),
        "ratings": rating_rows,
    }
def watch_item_payload(rel_path):
    return serialize_item({
        "path": rel_path,
        "tags": get_effective_tags(rel_path),
        "score_map": scores_store.get(rel_path, {}),
        "manual_override": get_manual_override(rel_path) is not None,
    })
def build_watch_lists(limit=100):
    rows = []
    for rel_path in list(tags.keys()):
        if rel_path not in metadata_store:
            continue
        meta = metadata_store.get(rel_path, {}) or {}
        ws = watch_state_for_path(rel_path)
        if ws["watch_later"] or ws["watched"] or ws["playback_position"] > 0:
            payload = watch_item_payload(rel_path)
            payload.update(ws)
            rows.append(payload)
    def stamp(row, key):
        return str(row.get(key) or "")
    continue_watching = [
        row for row in rows
        if not row.get("watched") and float(row.get("playback_position") or 0.0) >= 5.0 and float(row.get("playback_percent") or 0.0) < 95.0
    ]
    continue_watching.sort(key=lambda row: stamp(row, "last_watched_at"), reverse=True)
    watch_later = [row for row in rows if row.get("watch_later") and not row.get("watched")]
    watch_later.sort(key=lambda row: stamp(row, "watch_later_added_at") or stamp(row, "last_watched_at"),
    reverse=True)
    watched = [row for row in rows if row.get("watched")]
    watched.sort(key=lambda row: stamp(row, "watched_at") or stamp(row, "last_watched_at"), reverse=True)
    recent = [row for row in rows if row.get("last_watched_at")]
    recent.sort(key=lambda row: stamp(row, "last_watched_at"), reverse=True)
    limit = max(1, min(int(limit or 100), 500))
    return {
        "ok": True,
        "version": APP_VERSION,
        "summary": {
            "continue_watching": len(continue_watching),
            "watch_later": len(watch_later),
            "watched": len(watched),
            "tracked": len(rows),
        },
        "continue_watching": continue_watching[:limit],
        "watch_later": watch_later[:limit],
        "watched": watched[:limit],
        "recent": recent[:limit],
    }
# ================= AUDIO INTELLIGENCE HELPERS =================
# Implementation lives in telesaved/audio.py (AD-5, AD-6).
# _bind() and forwarding aliases follow after SERIALIZERS below
# (serialize_item must be defined before _audio._bind() is called).
from telesaved import audio as _audio
# ================= SERIALIZERS =================
# Implementation lives in telesaved/serializers.py (AD-7).
# Bind shared state + helpers, then re-export as module-level names so all
# existing call sites in app.py continue to work without modification.
from telesaved import serializers as _serializers
_serializers._bind(
    embeddings_store=embeddings_store,
    tags=tags,
    scores_store=scores_store,
    metadata_store=metadata_store,
    APP_VERSION=APP_VERSION,
    find_existing_file=find_existing_file,
    collect_file_metadata=collect_file_metadata,
    get_effective_tags=get_effective_tags,
    get_manual_override=get_manual_override,
    get_ai_signals=get_ai_signals,
    get_audio_language=get_audio_language,
    get_display_tags_for_signals=get_display_tags_for_signals,
    watch_state_for_path=watch_state_for_path,
    personal_state_for_path=personal_state_for_path,
    thumb_rel_path_for_file=thumb_rel_path_for_file,
    preview_rel_path_for_file=preview_rel_path_for_file,
    frame_preview_urls_for_file=frame_preview_urls_for_file,
    cached_scene_timeline=cached_scene_timeline,
    audio_payload_for_path=audio_payload_for_path,
    score_rows_fn=score_rows,
    short_name=short_name,
    human_date_from_path=human_date_from_path,
    format_bytes=format_bytes,
    format_duration=format_duration,
    now_stamp=now_stamp,
    metadata_source_label=metadata_source_label,
)
# Forwarding aliases — all existing call sites resolve through these names.
def serialize_item(item):
    return _serializers.serialize_item(item)
# Forwarding aliases continued — all call sites in app.py resolve unchanged.
def score_rows(score_map):
    return _serializers.score_rows(score_map)
def similar_video_items(rel_path, limit=8):
    return _serializers.similar_video_items(rel_path, limit=limit)
def tag_summary_for_paths(paths, max_items=6):
    return _serializers.tag_summary_for_paths(paths, max_items=max_items)
def source_summary_for_paths(paths, max_items=6):
    return _serializers.source_summary_for_paths(paths, max_items=max_items)
def cluster_label_from_paths(paths):
    return _serializers.cluster_label_from_paths(paths)
def normalized_embedding_map(limit=1000):
    return _serializers.normalized_embedding_map(limit=limit)
def cosine_similarity(a, b):
    return _serializers.cosine_similarity(a, b)
def build_visual_cluster_report(cluster_count=8, limit=800, representative_count=8, iterations=10):
    return _serializers.build_visual_cluster_report(
        cluster_count=cluster_count, limit=limit,
        representative_count=representative_count, iterations=iterations,
    )
def build_item_detail_payload(rel_path, include_similar=True):
    return _serializers.build_item_detail_payload(rel_path, include_similar=include_similar)
# ---- audio._bind() + forwarding aliases (placed after serialize_item is defined) ----
_audio._bind(
    metadata_store=metadata_store,
    tags=tags,
    scores_store=scores_store,
    APP_VERSION=APP_VERSION,
    APP_DIR=APP_DIR,
    WHISPER_LOG_FILE=WHISPER_LOG_FILE,
    find_existing_file=find_existing_file,
    get_video_duration_seconds=get_video_duration_seconds,
    get_effective_tags=get_effective_tags,
    get_manual_override=get_manual_override,
    save_state=save_state,
    save_audio_language=save_audio_language,
    serialize_item=serialize_item,
    format_duration=format_duration,
    now_stamp=now_stamp,
    short_name=short_name,
)
# Forwarding aliases — all existing call sites in app.py resolve unchanged.
def audio_default_payload(rel_path=""): return _audio.audio_default_payload(rel_path)
def audio_payload_for_path(rel_path): return _audio.audio_payload_for_path(rel_path)
def transcript_timestamp(seconds): return _audio.transcript_timestamp(seconds)
def normalize_speaker_name(value, fallback="Speaker 1"): return _audio.normalize_speaker_name(value, fallback)
def transcript_speaker_count(): return _audio.transcript_speaker_count()
def enrich_transcript_segments(segments): return _audio.enrich_transcript_segments(segments)
def transcript_script_from_segments(segments, fallback_text=""): return _audio.transcript_script_from_segments(segments, fallback_text)
def ffprobe_audio_streams(abs_path): return _audio.ffprobe_audio_streams(abs_path)
def ffmpeg_silence_detect(abs_path, duration=0.0, noise_db="-35dB", min_duration=0.5): return _audio.ffmpeg_silence_detect(abs_path, duration=duration, noise_db=noise_db, min_duration=min_duration)
def compact_log_text(value, limit=6000): return _audio.compact_log_text(value, limit)
def append_whisper_log(event, detail="", rel_path="", abs_path="", level="INFO"): return _audio.append_whisper_log(event, detail=detail, rel_path=rel_path, abs_path=abs_path, level=level)
def whisper_candidate_paths(): return _audio.whisper_candidate_paths()
def check_whisper_binary(path): return _audio.check_whisper_binary(path)
def whisper_binary_path(force_refresh=False): return _audio.whisper_binary_path(force_refresh)
def whisper_available(): return _audio.whisper_available()
def whisper_language(): return _audio.whisper_language()
def whisper_option(name, default): return _audio.whisper_option(name, default)
def whisper_bool_option(name, default): return _audio.whisper_bool_option(name, default)
def whisper_quality_guard_enabled(): return _audio.whisper_quality_guard_enabled()
def whisper_device(): return _audio.whisper_device()
def whisper_diagnostics_payload(): return _audio.whisper_diagnostics_payload()
def normalize_transcript_text_for_quality(text): return _audio.normalize_transcript_text_for_quality(text)
def repeated_phrase_score(text): return _audio.repeated_phrase_score(text)
def whisper_segment_quality(seg): return _audio.whisper_segment_quality(seg)
def filter_whisper_segments(raw_segments): return _audio.filter_whisper_segments(raw_segments)
def run_whisper_transcription(abs_path, rel_path=""): return _audio.run_whisper_transcription(abs_path, rel_path)
def analyze_audio_for_path(rel_path, force=False, transcribe=False): return _audio.analyze_audio_for_path(rel_path, force=force, transcribe=transcribe)
def build_audio_overview(): return _audio.build_audio_overview()
def search_audio_transcripts(query, limit=100): return _audio.search_audio_transcripts(query, limit=limit)
# ================= API ROUTES =================
routes = web.RouteTableDef()
# ================= AUTH / RBAC ROUTES =================
@routes.get("/api/auth/status")
async def api_auth_status(request):
    return web.json_response(auth_status_payload(request))
@routes.post("/api/auth/bootstrap")
async def api_auth_bootstrap(request):
    if auth_initialized():
        return web.json_response({"ok": False, "error": "Bootstrap is already complete"}, status=409)
    try:
        data = await request.json()
    except Exception:
        data = {}
    try:
        user = create_local_user(
            data.get("username") or "admin",
            data.get("password") or "",
            display_name=data.get("display_name") or "TeleSaved Administrator",
            email=data.get("email") or "",
            groups=["Admin"],
        )
        token, max_age = create_auth_session(user["id"])
        logger.info("Initial TeleSaved Admin account created: %s", user.get("username"))
        return make_auth_response({"ok": True, "user": user, **auth_status_payload(request)}, token=token,
        max_age=max_age)
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)
@routes.post("/api/auth/login")
async def api_auth_login(request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    username = normalize_username(data.get("username"))
    password = str(data.get("password") or "")
    # Check rate limit before touching any credentials
    if username:
        entry = _LOGIN_ATTEMPTS.get(username, {})
        if entry.get("count", 0) >= _LOGIN_MAX_ATTEMPTS and (time.time() - entry.get("window_start", 0)) < _LOGIN_WINDOW_SECONDS:
            logger.warning("TeleSaved login rate-limited for username=%s", username)
            return web.json_response({"ok": False, "error": "Too many login attempts. Please try again later."}, status=429)
    user = (auth_data.get("users") or {}).get(username)
    local_allowed = bool((auth_data.get("settings") or {}).get("allow_local_login", True))
    if local_allowed and user and not user.get("disabled") and user.get("auth_backend", "local") == "local" and verify_password(password, user):
        _login_clear_failures(username)
        user["last_login"] = now_stamp()
        save_auth_data()
        token, max_age = create_auth_session(username)
        logger.info("TeleSaved local login succeeded for username=%s", username)
        return make_auth_response({"ok": True, **auth_status_payload(request)}, token=token, max_age=max_age)
    if ldap_is_enabled():
        try:
            ldap_result = ldap_authenticate(username, password)
            ldap_user = (auth_data.get("users") or {}).get(username)
            if ldap_user and not ldap_user.get("disabled"):
                _login_clear_failures(username)
                ldap_user["last_login"] = now_stamp()
                save_auth_data()
                token, max_age = create_auth_session(username)
                logger.info("TeleSaved LDAP login succeeded for username=%s mapped_groups=%s", username,
                ldap_result.get("mapped_groups"))
                return make_auth_response({"ok": True, **auth_status_payload(request)}, token=token,
                max_age=max_age)
        except Exception as e:
            logger.warning("TeleSaved LDAP login failed for username=%s: %s", username, e)
    # Record failure (also handles first-failure init via the helper)
    if username:
        _login_check_and_record_failure(username)
    logger.warning("Failed TeleSaved login for username=%s", username)
    return web.json_response({"ok": False, "error": "Invalid username or password"}, status=401)
@routes.post("/api/auth/logout")
async def api_auth_logout(request):
    token = auth_token_from_request(request)
    if token:
        AUTH_SESSIONS.pop(token, None)
    return make_auth_response({"ok": True}, clear=True)
@routes.get("/api/auth/admin")
async def api_auth_admin_payload(request):
    user = auth_current_user(request)
    if not (user_has_permission(user, "security.manage") or user_has_permission(user, "source_acl.manage")):
        return web.json_response({"ok": False, "error": "Permission required: security.manage or source_acl.manage"}, status=403)
    return web.json_response({
        "ok": True,
        "users": [sanitized_user(uid, u) for uid, u in sorted((auth_data.get("users") or {}).items())],
        "groups": auth_data.get("groups") or {},
        "permissions_catalog": PERMISSION_CATALOG,
        "source_access": auth_data.get("source_access") or {"users": {}, "groups": {}},
        "sources": source_options_payload(),
        "can_manage_security": user_has_permission(user, "security.manage"),
        "can_manage_source_acl": user_has_permission(user, "source_acl.manage") or user_has_permission(user,
        "security.manage"),
        "identity_backends": {"local": True, "ldap": ldap_is_enabled(), "oidc": False, "sso": False},
        "ldap": {"settings": ldap_sanitized_settings(), "status": ldap_status_payload()},
    })
@routes.get("/api/auth/ldap/settings")
async def api_auth_ldap_settings_get(request):
    denied = require_permission_response(request, "security.manage")
    if denied:
        return denied
    return web.json_response({"ok": True, "settings": ldap_sanitized_settings(), "status":
    ldap_status_payload()})
@routes.put("/api/auth/ldap/settings")
async def api_auth_ldap_settings_put(request):
    denied = require_permission_response(request, "security.manage")
    if denied:
        return denied
    try:
        data = await request.json()
    except Exception:
        data = {}
    settings = ldap_update_settings(data.get("settings") if isinstance(data.get("settings"), dict) else data)
    logger.info("LDAP settings updated enabled=%s server=%s user_base=%s", settings.get("enabled"),
    settings.get("server_uri"), settings.get("user_search_base"))
    return web.json_response({"ok": True, "settings": ldap_sanitized_settings(settings), "status":
    ldap_status_payload(settings)})
@routes.post("/api/auth/ldap/test")
async def api_auth_ldap_test(request):
    denied = require_permission_response(request, "security.manage")
    if denied:
        return denied
    try:
        data = await request.json()
    except Exception:
        data = {}
    settings = json.loads(json.dumps(ldap_settings_current()))
    if isinstance(data.get("settings"), dict):
        # Test unsaved settings without writing them. Preserve stored bind password unless a new one was supplied.
        incoming = data.get("settings") or {}
        stored_password = settings.get("bind_password", "")
        for key, value in incoming.items():
            if key == "bind_password" and not value:
                continue
            settings[key] = value
        if not settings.get("bind_password"):
            settings["bind_password"] = stored_password
        settings["group_mappings"] = ldap_normalize_group_mapping(settings.get("group_mappings") or {})
    result = ldap_test(settings, normalize_username(data.get("test_username") or ""),
    str(data.get("test_password") or ""))
    status = 200 if result.get("ok") else 400
    if result.get("ok"):
        logger.info("LDAP test succeeded service_bind=%s user_search=%s user_bind=%s",
        result.get("service_bind"), result.get("user_search"), result.get("user_bind"))
    else:
        logger.warning("LDAP test failed: %s", result.get("error"))
    return web.json_response({"ok": bool(result.get("ok")), "result": result, "status":
    ldap_status_payload(settings)}, status=status)
@routes.post("/api/auth/ldap/sync-user")
async def api_auth_ldap_sync_user(request):
    denied = require_permission_response(request, "security.manage")
    if denied:
        return denied
    try:
        data = await request.json()
    except Exception:
        data = {}
    username = normalize_username(data.get("username") or "")
    if not username:
        return web.json_response({"ok": False, "error": "username is required"}, status=400)
    settings = ldap_settings_current()
    service = None
    try:
        service = ldap_service_connection(settings)
        entry = ldap_find_user_entry(service, settings, username)
        user_dn = getattr(entry, "entry_dn", "")
        ldap_names, ldap_dns = ldap_group_names_for_user(service, settings, username, user_dn, entry)
        mapped_groups = ldap_map_groups(ldap_names, ldap_dns, settings)
        user_info = {
            "dn": user_dn,
            "display_name": ldap_entry_attr(entry, settings.get("display_name_attribute") or "cn") or username,
            "email": ldap_entry_attr(entry, settings.get("email_attribute") or "mail"),
            "ldap_groups": ldap_names,
            "ldap_group_dns": ldap_dns,
        }
        user = sync_ldap_user(username, user_info, mapped_groups)
        return web.json_response({"ok": True, "user": user, "ldap_groups": ldap_names, "mapped_groups":
        mapped_groups})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)
    finally:
        try:
            if service:
                service.unbind()
        except Exception:
            pass
@routes.post("/api/auth/users")
async def api_auth_user_create(request):
    denied = require_permission_response(request, "security.manage")
    if denied:
        return denied
    data = await request.json()
    try:
        user = create_local_user(
            data.get("username"),
            data.get("password"),
            display_name=data.get("display_name") or "",
            email=data.get("email") or "",
            groups=data.get("groups") or ["Users"],
            disabled=bool(data.get("disabled", False)),
        )
        return web.json_response({"ok": True, "user": user})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)
@routes.put("/api/auth/users/{user_id}")
async def api_auth_user_update(request):
    denied = require_permission_response(request, "security.manage")
    if denied:
        return denied
    user_id = normalize_username(request.match_info["user_id"])
    users = auth_data.setdefault("users", {})
    if user_id not in users:
        return web.json_response({"ok": False, "error": "User not found"}, status=404)
    data = await request.json()
    user = users[user_id]
    if "display_name" in data:
        user["display_name"] = str(data.get("display_name") or user_id).strip()[:120]
    if "email" in data:
        user["email"] = str(data.get("email") or "").strip()[:160]
    if "groups" in data:
        groups = [g for g in (data.get("groups") or []) if g in auth_data.get("groups", {})]
        user["groups"] = groups or ["Users"]
    if "disabled" in data:
        user["disabled"] = bool(data.get("disabled"))
    if data.get("password"):
        if len(str(data.get("password") or "")) < 8:
            return web.json_response({"ok": False, "error": "Password must be at least 8 characters"},
            status=400)
        salt, digest = hash_password(data.get("password"))
        user["password_salt"] = salt
        user["password_hash"] = digest
    user["updated_at"] = now_stamp()
    save_auth_data()
    return web.json_response({"ok": True, "user": sanitized_user(user_id, user)})
@routes.delete("/api/auth/users/{user_id}")
async def api_auth_user_delete(request):
    denied = require_permission_response(request, "security.manage")
    if denied:
        return denied
    user_id = normalize_username(request.match_info["user_id"])
    users = auth_data.setdefault("users", {})
    current = auth_current_user(request)
    if current and current.get("id") == user_id:
        return web.json_response({"ok": False, "error": "You cannot delete your own active account"},
        status=400)
    if user_id not in users:
        return web.json_response({"ok": False, "error": "User not found"}, status=404)
    users.pop(user_id, None)
    (auth_data.setdefault("source_access", {}).setdefault("users", {})).pop(user_id, None)
    save_auth_data()
    return web.json_response({"ok": True, "deleted": user_id})
@routes.post("/api/auth/groups")
async def api_auth_group_create(request):
    denied = require_permission_response(request, "security.manage")
    if denied:
        return denied
    data = await request.json()
    name = normalize_group_name(data.get("name"))
    if not name:
        return web.json_response({"ok": False, "error": "Group name is required"}, status=400)
    groups = auth_data.setdefault("groups", {})
    if name in groups:
        return web.json_response({"ok": False, "error": "Group already exists"}, status=409)
    groups[name] = {
        "name": name,
        "description": str(data.get("description") or "Custom group").strip()[:240],
        "system": False,
        "permissions": normalize_permission_list(data.get("permissions") or ["library.view"]),
        "created_at": now_stamp(),
    }
    auth_data.setdefault("source_access", {}).setdefault("groups", {}).setdefault(name, [])
    save_auth_data()
    return web.json_response({"ok": True, "group": groups[name]})
@routes.put("/api/auth/groups/{group_name}")
async def api_auth_group_update(request):
    denied = require_permission_response(request, "security.manage")
    if denied:
        return denied
    group_name = normalize_group_name(request.match_info["group_name"])
    groups = auth_data.setdefault("groups", {})
    if group_name not in groups:
        return web.json_response({"ok": False, "error": "Group not found"}, status=404)
    data = await request.json()
    group = groups[group_name]
    if "description" in data:
        group["description"] = str(data.get("description") or "").strip()[:240]
    if "permissions" in data:
        if group_name == "Admin" and "*" not in (data.get("permissions") or []):
            return web.json_response({"ok": False, "error": "Admin group must retain full access"},
            status=400)
        group["permissions"] = normalize_permission_list(data.get("permissions") or [])
    group["updated_at"] = now_stamp()
    save_auth_data()
    return web.json_response({"ok": True, "group": group})
@routes.delete("/api/auth/groups/{group_name}")
async def api_auth_group_delete(request):
    denied = require_permission_response(request, "security.manage")
    if denied:
        return denied
    group_name = normalize_group_name(request.match_info["group_name"])
    groups = auth_data.setdefault("groups", {})
    if group_name not in groups:
        return web.json_response({"ok": False, "error": "Group not found"}, status=404)
    if groups[group_name].get("system"):
        return web.json_response({"ok": False, "error": "System groups cannot be removed"}, status=400)
    groups.pop(group_name, None)
    auth_data.setdefault("source_access", {}).setdefault("groups", {}).pop(group_name, None)
    for user in (auth_data.get("users") or {}).values():
        user["groups"] = [g for g in (user.get("groups") or []) if g != group_name] or ["Users"]
    save_auth_data()
    return web.json_response({"ok": True, "deleted": group_name})
@routes.post("/api/auth/source-access")
async def api_auth_source_access_update(request):
    user = auth_current_user(request)
    if not (user_has_permission(user, "source_acl.manage") or user_has_permission(user, "security.manage")):
        return web.json_response({"ok": False, "error": "Permission required: source_acl.manage"}, status=403)
    data = await request.json()
    target_type = str(data.get("target_type") or "").strip().lower()
    target = str(data.get("target") or "").strip()
    refs = normalize_source_grants(data.get("source_refs") or [])
    access = auth_data.setdefault("source_access", {})
    if target_type == "user":
        target = normalize_username(target)
        if target not in (auth_data.get("users") or {}):
            return web.json_response({"ok": False, "error": "User not found"}, status=404)
        access.setdefault("users", {})[target] = refs
    elif target_type == "group":
        target = normalize_group_name(target)
        if target not in (auth_data.get("groups") or {}):
            return web.json_response({"ok": False, "error": "Group not found"}, status=404)
        access.setdefault("groups", {})[target] = refs
    else:
        return web.json_response({"ok": False, "error": "target_type must be user or group"}, status=400)
    save_auth_data()
    logger.info("Source ACL updated by %s for %s:%s -> %s", user.get("username"), target_type, target, refs)
    return web.json_response({"ok": True, "source_access": access, "target_type": target_type, "target":
    target, "source_refs": refs})
def setup_status_payload():
    return {
        "ok": True,
        "version": APP_VERSION,
        "setup_required": not TELEGRAM_CONFIGURED,
        "telegram_configured": TELEGRAM_CONFIGURED,
        "config_file": CONFIG_FILE,
        "config_example_file": CONFIG_EXAMPLE_FILE,
        "config_missing": CONFIG_MISSING,
        "config_error": CONFIG_ERROR,
        "base_path": BASE_PATH,
        "frontend_index": INDEX_FILE,
    }
def install_check_payload():
    checks = []
    def add(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})
    add("config.json", os.path.exists(CONFIG_FILE), CONFIG_FILE if os.path.exists(CONFIG_FILE) else "Missing; first-run wizard can create it")
    add("frontend/index.html", os.path.exists(INDEX_FILE), INDEX_FILE)
    add("base path", os.path.isdir(BASE_PATH), BASE_PATH)
    add("base path writable", os.access(BASE_PATH, os.W_OK), BASE_PATH)
    add("ffmpeg", bool(shutil.which("ffmpeg")), shutil.which("ffmpeg") or "not found")
    add("ffprobe", bool(shutil.which("ffprobe")), shutil.which("ffprobe") or "not found")
    add("dpkg-deb", bool(shutil.which("dpkg-deb")), shutil.which("dpkg-deb") or "not found")
    add("ldap3", ldap3 is not None, "available" if ldap3 is not None else f"not installed:{LDAP3_IMPORT_ERROR}")
    add("python", True, os.sys.version.split()[0])
    add("docker compose files", os.path.exists(os.path.join(APP_DIR, "docker-compose.yml")),
    os.path.join(APP_DIR, "docker-compose.yml"))
    return {"ok": True, "version": APP_VERSION, "checks": checks, "setup": setup_status_payload()}
@routes.get("/api/setup/status")
async def api_setup_status(request):
    return web.json_response(setup_status_payload())
@routes.post("/api/setup/save")
async def api_setup_save(request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    try:
        new_api_id = int(str(data.get("api_id", "")).strip())
    except Exception:
        return web.json_response({"ok": False, "error": "Telegram API ID must be a number"}, status=400)
    new_api_hash = str(data.get("api_hash", "")).strip()
    new_base_path = str(data.get("base_path", BASE_PATH)).strip() or DEFAULT_BASE_PATH
    if not new_api_id or not new_api_hash:
        return web.json_response({"ok": False, "error": "Telegram API ID and API hash are required"},
        status=400)
    try:
        os.makedirs(new_base_path, exist_ok=True)
        if not os.access(new_base_path, os.W_OK):
            return web.json_response({"ok": False, "error": f"Base path is not writable: {new_base_path}"},
            status=400)
        payload = {"api_id": new_api_id, "api_hash": new_api_hash, "base_path": new_base_path}
        tmp = f"{CONFIG_FILE}.tmp"
        with open(tmp, "w") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, CONFIG_FILE)
        try:
            os.chmod(CONFIG_FILE, 0o600)
        except Exception:
            pass
        return web.json_response({"ok": True, "restart_required": True, "message": "Configuration saved. Restart telesaved.service to activate Telegram sync."})
    except Exception as e:
        logger.exception("First-run setup save failed")
        return web.json_response({"ok": False, "error": str(e)}, status=500)
@routes.post("/api/setup/validate-telegram")
async def api_setup_validate_telegram(request):
    """Live-validate Telegram API credentials without writing any files.
    Used by the setup wizard step 2 to gate advancement on confirmed-working credentials.
    This route is intentionally public (no auth required) — it is only reachable before
    config.json exists and the service has Telegram configured.
    """
    try:
        data = await request.json()
    except Exception:
        data = {}
    try:
        check_api_id = int(str(data.get("api_id", "")).strip())
    except Exception:
        return web.json_response({"ok": False, "error": "API ID must be a number"}, status=400)
    check_api_hash = str(data.get("api_hash", "")).strip()
    if not check_api_id or not check_api_hash:
        return web.json_response({"ok": False, "error": "API ID and API hash are required"}, status=400)
    import tempfile as _tempfile
    tmp_session = None
    client = None
    try:
        tmp_dir = _tempfile.mkdtemp(prefix="telesaved-validate-")
        tmp_session = os.path.join(tmp_dir, "validate_session")
        client = TelegramClient(tmp_session, check_api_id, check_api_hash)
        await asyncio.wait_for(client.connect(), timeout=10)
        if not await client.is_user_authorized():
            # Credentials are structurally valid — Telegram accepted the connection.
            # Not being authorized simply means no active session, which is expected.
            pass
        return web.json_response({"ok": True})
    except asyncio.TimeoutError:
        return web.json_response({"ok": False, "error": "Telegram connection timed out. Check your network."}, status=400)
    except Exception as e:
        msg = str(e)
        if "API_ID_INVALID" in msg or "api_id" in msg.lower():
            return web.json_response({"ok": False, "error": "Invalid API ID. Check your credentials at my.telegram.org/apps."}, status=400)
        if "AUTH_KEY" in msg or "api_hash" in msg.lower():
            return web.json_response({"ok": False, "error": "Invalid API hash. Check your credentials at my.telegram.org/apps."}, status=400)
        return web.json_response({"ok": False, "error": f"Telegram validation failed: {msg}"}, status=400)
    finally:
        try:
            if client and client.is_connected():
                await client.disconnect()
        except Exception:
            pass
        try:
            import shutil as _shutil
            _shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass

@routes.get("/api/install/checks")
async def api_install_checks(request):
    return web.json_response(install_check_payload())
@routes.post("/api/jobs/enqueue")
async def api_jobs_enqueue(request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    try:
        job = enqueue_system_job(str(data.get("task", "")).strip(), data.get("title"), data.get("payload") or
        {})
        return web.json_response({"ok": True, "job": job, "engine": job_engine_summary()})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)
# ================= CONNECTIVITY / LIMP MODE =================
# Global connectivity state — updated by the background poller.
_connectivity_status = "online"   # "online" | "offline"
_connectivity_paused_jobs = set() # job IDs auto-paused by connectivity loss

async def _connectivity_poller():
    """Background task that polls Telegram reachability and manages limp mode.
    Uses exponential backoff: checks every 30s when online, 15s when offline.
    """
    global _connectivity_status, _connectivity_paused_jobs
    backoff = 30
    while True:
        await asyncio.sleep(backoff)
        if not TELEGRAM_CONFIGURED or client is None:
            # No Telegram configured; nothing to poll.
            continue
        reachable = False
        try:
            if client.is_connected():
                reachable = True
            else:
                await asyncio.wait_for(client.connect(), timeout=10)
                reachable = client.is_connected()
        except Exception:
            reachable = False

        if not reachable and _connectivity_status == "online":
            # Transition: online → offline — auto-pause active jobs
            _connectivity_status = "offline"
            backoff = 15
            logger.warning("Connectivity lost — entering limp mode; pausing active jobs")
            with job_lock:
                active_ids = [
                    jid for jid, j in jobs.items()
                    if j.get("status") in {"running", "queued"}
                ]
            for jid in active_ids:
                update_job(jid, control="paused", status="paused", note="connectivity_lost")
                _connectivity_paused_jobs.add(jid)

        elif reachable and _connectivity_status == "offline":
            # Transition: offline → online — resume jobs we auto-paused
            _connectivity_status = "online"
            backoff = 30
            logger.info("Connectivity restored — resuming limp-mode-paused jobs")
            to_resume = list(_connectivity_paused_jobs)
            _connectivity_paused_jobs.clear()
            for jid in to_resume:
                job = get_job(jid)
                if job and job.get("note") == "connectivity_lost" and job.get("status") == "paused":
                    update_job(jid, control="running", status="running", note="Resumed after connectivity restored")

@routes.get("/api/health")
async def api_health(request):
    return web.json_response({
        "ok": True,
        "version": APP_VERSION,
        "setup_required": not TELEGRAM_CONFIGURED,
        "connectivity_status": _connectivity_status,
    })
@routes.get("/api/tags")
async def api_tags(request):
    refresh_tag_globals()
    return web.json_response({
        "tags": PREDEFINED_TAGS,
        "system_tags": list(SYSTEM_TAGS),
        "custom_tags": get_custom_tags(),
        "metadata": settings_data.get("tag_metadata", {}),
    })
@routes.post("/api/tags")
async def api_tags_create(request):
    data = await request.json()
    tag = normalize_tag_name(data.get("tag") or data.get("name"))
    if not tag:
        return web.json_response({"ok": False, "error": "Tag name cannot be blank"}, status=400)
    if tag.lower() in {t.lower() for t in PREDEFINED_TAGS}:
        return web.json_response({"ok": False, "error": "Tag already exists"}, status=409)
    custom = get_custom_tags()
    custom.append(tag)
    settings_data["custom_tags"] = custom
    metadata = settings_data.setdefault("tag_metadata", {})
    metadata[tag] = {
        "label": tag,
        "group": str(data.get("group") or "Custom").strip() or "Custom",
        "color": str(data.get("color") or "").strip(),
        "locked": bool(data.get("locked", False)),
        "hidden": bool(data.get("hidden", False)),
    }
    refresh_tag_globals()
    save_settings()
    return web.json_response({"ok": True, "tag": tag, "tags": PREDEFINED_TAGS})
@routes.post("/api/tags/metadata")
async def api_tags_metadata_update(request):
    data = await request.json()
    metadata_updates = data.get("metadata", {})
    metadata = settings_data.setdefault("tag_metadata", {})
    if not isinstance(metadata_updates, dict):
        return web.json_response({"ok": False, "error": "metadata must be an object"}, status=400)
    for tag in PREDEFINED_TAGS:
        if tag not in metadata_updates:
            continue
        incoming = metadata_updates.get(tag) or {}
        current = metadata.setdefault(tag, {})
        for key in ("label", "group", "color", "locked", "hidden"):
            if key in incoming:
                current[key] = incoming[key]
    refresh_tag_globals()
    save_settings()
    return web.json_response({"ok": True, "metadata": settings_data.get("tag_metadata", {})})
@routes.delete("/api/tags/{tag}")
async def api_tags_delete(request):
    tag = normalize_tag_name(unquote(request.match_info["tag"]))
    if not tag:
        return web.json_response({"ok": False, "error": "Invalid tag"}, status=400)
    if tag in SYSTEM_TAGS:
        return web.json_response({"ok": False, "error": "System tags cannot be deleted"}, status=400)
    custom = [t for t in get_custom_tags() if t.lower() != tag.lower()]
    if len(custom) == len(get_custom_tags()):
        return web.json_response({"ok": False, "error": "Custom tag not found"}, status=404)
    settings_data["custom_tags"] = custom
    settings_data.setdefault("tag_metadata", {}).pop(tag, None)
    removed_from = 0
    if request.query.get("remove", "1") != "0":
        for rel_path in list(tags.keys()):
            changed = False
            if tag in tags.get(rel_path, []):
                tags[rel_path] = [t for t in tags.get(rel_path, []) if t != tag]
                changed = True
            override = get_manual_override(rel_path)
            if override and tag in override.get("tags", []):
                override["tags"] = [t for t in override.get("tags", []) if t != tag]
                changed = True
            if changed:
                removed_from += 1
        save_state()
        save_feedback()
    refresh_tag_globals()
    save_settings()
    return web.json_response({"ok": True, "tag": tag, "removed_from": removed_from, "tags": PREDEFINED_TAGS})
@routes.post("/api/tags/suggest")
async def api_tags_suggest(request):
    """Submit a new tag suggestion from Review Studio.
    Any user with tags.manage permission may call this endpoint.
    The suggestion enters a pending queue; an Admin must approve it before
    the tag becomes part of the vocabulary.
    """
    user = auth_current_user(request)
    data = await request.json()
    tag = normalize_tag_name(data.get("tag") or data.get("name"))
    video_path = str(data.get("video_path") or "").strip()
    if not tag:
        return web.json_response({"ok": False, "error": "Tag name cannot be blank"}, status=400)
    if not video_path:
        return web.json_response({"ok": False, "error": "video_path is required"}, status=400)
    if tag.lower() in {t.lower() for t in PREDEFINED_TAGS}:
        return web.json_response({"ok": False, "error": "Tag already exists in vocabulary"}, status=409)
    suggested_by = (user or {}).get("username") or "unknown"
    with sqlite_lock:
        with sqlite_connect() as conn:
            # Prevent duplicate pending suggestions for the same tag.
            existing = conn.execute(
                "SELECT id FROM tag_suggestions WHERE LOWER(tag)=LOWER(?) AND status='pending'",
                (tag,),
            ).fetchone()
            if existing:
                return web.json_response({"ok": True, "queued": True, "message": "Suggestion already pending"})
            conn.execute(
                "INSERT INTO tag_suggestions (tag, suggested_by, video_path, created_at, status) VALUES (?,?,?,?,?)",
                (tag, suggested_by, video_path, now_stamp(), "pending"),
            )
            conn.commit()
    logger.info("Tag suggestion submitted: '%s' by %s for %s", tag, suggested_by, video_path)
    return web.json_response({"ok": True, "message": "Suggestion submitted for admin review"})

@routes.get("/api/tags/suggestions")
async def api_tags_suggestions_list(request):
    """List pending tag suggestions. Admin only."""
    status_filter = request.query.get("status", "pending")
    with sqlite_connect() as conn:
        rows = conn.execute(
            "SELECT id, tag, suggested_by, video_path, created_at, status FROM tag_suggestions WHERE status=? ORDER BY id DESC",
            (status_filter,),
        ).fetchall()
    return web.json_response({
        "ok": True,
        "suggestions": [dict(r) for r in rows],
    })

@routes.post("/api/tags/suggestions/{suggestion_id}/approve")
async def api_tags_suggestion_approve(request):
    """Approve a pending tag suggestion: adds it to vocabulary and records label_source."""
    try:
        suggestion_id = int(request.match_info["suggestion_id"])
    except (ValueError, KeyError):
        return web.json_response({"ok": False, "error": "Invalid suggestion id"}, status=400)
    # Optional: allow rename on approval
    data = {}
    try:
        data = await request.json()
    except Exception:
        pass
    with sqlite_connect() as conn:
        row = conn.execute(
            "SELECT * FROM tag_suggestions WHERE id=? AND status='pending'",
            (suggestion_id,),
        ).fetchone()
        if not row:
            return web.json_response({"ok": False, "error": "Suggestion not found or already actioned"}, status=404)
    approved_tag = normalize_tag_name(data.get("tag") or row["tag"])
    merge_into = normalize_tag_name(data.get("merge_into") or "")
    final_tag = merge_into if merge_into and merge_into.lower() in {t.lower() for t in PREDEFINED_TAGS} else approved_tag
    # Add to vocabulary if not merging into an existing tag.
    if final_tag.lower() not in {t.lower() for t in PREDEFINED_TAGS}:
        custom = get_custom_tags()
        custom.append(final_tag)
        settings_data["custom_tags"] = custom
        refresh_tag_globals()
        save_settings()
    # Write user_confirmed label for the originating video.
    video_path = row["video_path"]
    if video_path:
        try:
            write_tag_label(video_path, final_tag, "user_confirmed")
        except Exception as e:
            logger.warning("Could not write label for approved suggestion: %s", e)
    # Mark suggestion as approved.
    with sqlite_lock:
        with sqlite_connect() as conn:
            conn.execute(
                "UPDATE tag_suggestions SET status='approved' WHERE id=?",
                (suggestion_id,),
            )
            conn.commit()
    logger.info("Tag suggestion #%d approved: '%s' → '%s'", suggestion_id, row["tag"], final_tag)
    return web.json_response({"ok": True, "tag": final_tag, "tags": PREDEFINED_TAGS})

@routes.post("/api/tags/suggestions/{suggestion_id}/reject")
async def api_tags_suggestion_reject(request):
    """Reject a pending tag suggestion."""
    try:
        suggestion_id = int(request.match_info["suggestion_id"])
    except (ValueError, KeyError):
        return web.json_response({"ok": False, "error": "Invalid suggestion id"}, status=400)
    with sqlite_lock:
        with sqlite_connect() as conn:
            result = conn.execute(
                "UPDATE tag_suggestions SET status='rejected' WHERE id=? AND status='pending'",
                (suggestion_id,),
            )
            conn.commit()
            if result.rowcount == 0:
                return web.json_response({"ok": False, "error": "Suggestion not found or already actioned"}, status=404)
    return web.json_response({"ok": True})

@routes.post("/api/tags/migrate-labels")
async def api_tags_migrate_labels(request):
    """Migration assistant: bulk-write label_source rows for legacy tags that lack them.
    Accepts:
      { "label_source": "ai_inferred" }            — mark all legacy tags
      { "paths": [...], "label_source": "user_confirmed" }  — mark specific videos
    Admin only (system.admin permission enforced by the router).
    """
    data = {}
    try:
        data = await request.json()
    except Exception:
        pass
    label_source = str(data.get("label_source") or "ai_inferred")
    if label_source not in VALID_LABEL_SOURCES:
        return web.json_response({"ok": False, "error": f"label_source must be one of {sorted(VALID_LABEL_SOURCES)}"}, status=400)
    paths = data.get("paths")
    if paths is not None and not isinstance(paths, list):
        return web.json_response({"ok": False, "error": "paths must be a list or omitted"}, status=400)
    target_paths = paths if paths else list(tags.keys())
    total_written = 0
    for rel_path in target_paths:
        try:
            written = migrate_legacy_tag_labels(rel_path, label_source=label_source)
            total_written += written
        except Exception as e:
            logger.warning("Migration failed for %s: %s", rel_path, e)
    return web.json_response({
        "ok": True,
        "label_source": label_source,
        "paths_processed": len(target_paths),
        "labels_written": total_written,
    })

@routes.get("/api/tags/labels/export")
async def api_tags_labels_export(request):
    """Export all video_tag_labels rows, optionally filtered by label_source.
    Used for training data export.
    """
    label_source_filter = request.query.get("label_source")
    try:
        with sqlite_connect() as conn:
            if label_source_filter:
                rows = conn.execute(
                    "SELECT path, tag, label_source, created_at FROM video_tag_labels WHERE label_source=? ORDER BY path, tag",
                    (label_source_filter,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT path, tag, label_source, created_at FROM video_tag_labels ORDER BY path, tag"
                ).fetchall()
        return web.json_response({
            "ok": True,
            "count": len(rows),
            "labels": [dict(r) for r in rows],
        })
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)

@routes.get("/api/tags/mappings")
async def api_tags_mappings_get(request):
    """Return current tag mappings. Loads fresh from disk each call (hot-reload)."""
    denied = require_permission_response(request, "tags.manage")
    if denied:
        return denied
    global tag_mappings
    tag_mappings = _load_tag_mappings()
    return web.json_response({"ok": True, "mappings": tag_mappings})

@routes.post("/api/tags/mappings")
async def api_tags_mappings_save(request):
    """Replace tag mappings in memory and on disk."""
    denied = require_permission_response(request, "tags.manage")
    if denied:
        return denied
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)
    mappings = data.get("mappings")
    if not isinstance(mappings, dict):
        return web.json_response({"ok": False, "error": "mappings must be an object"}, status=400)
    global tag_mappings
    try:
        _save_tag_mappings(mappings)
        tag_mappings = mappings
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)
    return web.json_response({"ok": True, "mappings": tag_mappings})

@routes.get("/api/librarian/suggestions")
async def api_librarian_suggestions(request):
    return web.json_response(librarian_suggestions_payload())
@routes.post("/api/librarian/ask")
async def api_librarian_ask(request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    prompt = normalize_librarian_text(data.get("prompt") or data.get("q") or "")
    if not prompt:
        return web.json_response({"ok": False, "error": "Ask the AI Librarian a question first."}, status=400)
    try:
        filters, reasons = librarian_parse_prompt(prompt)
        requested_limit = data.get("limit")
        if requested_limit not in (None, ""):
            try:
                filters["limit"] = max(1, min(int(requested_limit), 100))
            except Exception:
                pass
        items = librarian_item_pool(filters)
        total = len(items)
        limit = max(1, min(int(filters.get("limit") or 24), 100))
        serialized = [serialize_item(item) for item in items[:limit]]
        answer = librarian_summary(prompt, filters, reasons, total, items)
        library_query = {
            "q": filters.get("q", ""),
            "tag": filters.get("tag_filter", ""),
            "source": filters.get("source_filter", ""),
            "min": filters.get("min_score", 0.0),
            "duration_min": filters.get("duration_min"),
            "duration_max": filters.get("duration_max"),
            "size_min_mb": filters.get("size_min_mb"),
            "size_max_mb": filters.get("size_max_mb"),
            "sort": filters.get("sort_by", "path"),
            "dir": filters.get("sort_dir", "asc"),
        }
        return web.json_response({
            "ok": True,
            "prompt": prompt,
            "answer": answer,
            "filters": filters,
            "reasons": reasons,
            "total_items": total,
            "returned_items": len(serialized),
            "items": serialized,
            "library_query": library_query,
        })
    except Exception as e:
        logger.exception("AI Librarian request failed")
        return web.json_response({"ok": False, "error": str(e)}, status=500)
@routes.get("/api/library")
async def api_library(request):
    q = request.query.get("q", "").strip()
    tag_filter = request.query.get("tag", "").strip()
    source_filter = request.query.get("source", "").strip()
    min_raw = request.query.get("min", "0")
    page = parse_page(request.query.get("page", "1"))
    sort_by = request.query.get("sort", "relevance" if q else "path")
    sort_dir = request.query.get("dir", "desc" if sort_by in {"relevance", "score", "size", "duration",
    "modified"} else "asc")
    try:
        min_score = float(min_raw)
    except ValueError:
        min_score = 0.0
    items = build_library_items(
        q=q,
        tag_filter=tag_filter,
        min_score=min_score,
        source_filter=source_filter,
        duration_min=parse_float_or_none(request.query.get("duration_min", "")),
        duration_max=parse_float_or_none(request.query.get("duration_max", "")),
        size_min_mb=parse_float_or_none(request.query.get("size_min_mb", "")),
        size_max_mb=parse_float_or_none(request.query.get("size_max_mb", "")),
        audio_language=request.query.get("audio_language", "").strip() or None,
        scene_composition=request.query.get("scene_composition", "").strip() or None,
        performer_presentation=request.query.get("performer_presentation", "").strip() or None,
        act_tag=request.query.get("act_tag", "").strip() or None,
    )
    items, sort_by, sort_dir = sort_library_items(items, sort_by=sort_by, sort_dir=sort_dir)
    paged_items, page, total_pages, total_items = paginate_items(items, page)
    return web.json_response({
        "items": [serialize_item(i) for i in paged_items],
        "page": page,
        "total_pages": total_pages,
        "total_items": total_items,
        "query": q,
        "tag": tag_filter,
        "source": source_filter,
        "min": min_score,
        "sort": sort_by,
        "dir": sort_dir,
    })
@routes.get("/api/export/library")
async def api_export_library(request):
    params = export_request_params(request)
    rows, sort_by, sort_dir = library_export_rows(params)
    fmt = str(request.query.get("format", "csv") or "csv").lower()
    stamp = timestamp_for_filename()
    if fmt == "json":
        payload = {
            "ok": True,
            "exported_at": now_stamp(),
            "count": len(rows),
            "sort": sort_by,
            "dir": sort_dir,
            "fields": EXPORT_FIELDS,
            "items": rows,
        }
        return web.Response(
            text=json.dumps(payload, indent=2),
            content_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="telesaved-library-{stamp}.json"'},
        )
    csv_text = library_export_csv(rows)
    return web.Response(
        text=csv_text,
        content_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="telesaved-library-{stamp}.csv"'},
    )
@routes.post("/api/import/library")
async def api_import_library(request):
    dry_run = request.query.get("dry_run", "0") in {"1", "true", "yes"}
    import_tags = request.query.get("tags", "1") not in {"0", "false", "no"}
    import_notes = request.query.get("notes", "1") not in {"0", "false", "no"}
    filename = ""
    text = ""
    content_type = request.headers.get("content-type", "")
    try:
        if "multipart/form-data" in content_type:
            form = await request.post()
            upload = form.get("file")
            if not upload:
                return web.json_response({"ok": False, "error": "No file uploaded"}, status=400)
            filename = getattr(upload, "filename", "") or "import"
            raw = upload.file.read()
            text = raw.decode("utf-8-sig", errors="replace")
            dry_run = str(form.get("dry_run", "1")) in {"1", "true", "yes", "on"}
            import_tags = str(form.get("tags", "1")) not in {"0", "false", "no", "off"}
            import_notes = str(form.get("notes", "1")) not in {"0", "false", "no", "off"}
        else:
            data = await request.json()
            filename = str(data.get("filename") or "import.json")
            if "items" in data:
                rows = data.get("items") or []
                return web.json_response(apply_library_import_rows(rows, import_tags=import_tags,
                import_notes=import_notes, dry_run=bool(data.get("dry_run", dry_run))))
            text = str(data.get("text") or "")
            dry_run = bool(data.get("dry_run", dry_run))
            import_tags = bool(data.get("tags", import_tags))
            import_notes = bool(data.get("notes", import_notes))
        rows = parse_library_import_text(text, filename)
        return web.json_response(apply_library_import_rows(rows, import_tags=import_tags,
        import_notes=import_notes, dry_run=dry_run))
    except Exception as e:
        logger.exception("Library import failed")
        return web.json_response({"ok": False, "error": str(e)}, status=400)
@routes.get("/api/downloads")
async def api_downloads(request):
    refresh_storage_download_mode()
    active = sorted(download_status.values(), key=lambda x: x.get("updated_at", ""), reverse=True)
    history = list(download_history)
    return web.json_response({
        "active": active,
        "history": history,
        "mode": download_control.get("mode"),
        "reason": download_control.get("reason"),
        "updated": download_control.get("updated"),
        "threshold": DOWNLOAD_STOP_THRESHOLD,
        "storage": get_storage_stats(),
    })
@routes.get("/api/storage")
async def api_storage(request):
    refresh_storage_download_mode()
    return web.json_response({
        "ok": True,
        "threshold": DOWNLOAD_STOP_THRESHOLD,
        "download_mode": download_control.get("mode"),
        "reason": download_control.get("reason"),
        "updated": download_control.get("updated"),
        **get_storage_stats(),
    })
@routes.post("/api/downloads/pause")
async def api_download_pause(request):
    set_download_mode("paused_manual", "Paused by user")
    return web.json_response({"ok": True, "mode": download_control.get("mode")})
@routes.post("/api/downloads/resume")
async def api_download_resume(request):
    if storage_is_low():
        set_download_mode("paused_storage", f"Storage below {DOWNLOAD_STOP_THRESHOLD}%")
        return web.json_response({"ok": False, "error": "Storage below 5%", "mode":
        download_control.get("mode")}, status=409)
    set_download_mode("running", "Resumed by user")
    return web.json_response({"ok": True, "mode": download_control.get("mode")})
@routes.get("/api/sources")
async def api_sources_get(request):
    configured = get_video_sources()
    profiles = get_source_profiles()
    dialogs = [
        {
            "id": "me",
            "ref": "me",
            "title": "Saved Messages",
            "kind": "saved",
            "username": None,
        }
    ]
    error = ""
    try:
        seen_refs = {"me"}
        if client is None:
            raise RuntimeError("Telegram client is not configured")
        async for dialog in client.iter_dialogs(limit=250):
            entity = dialog.entity
            try:
                peer_id = utils.get_peer_id(entity)
            except Exception:
                peer_id = getattr(entity, "id", None)
            username = getattr(entity, "username", None)
            title = dialog.name or getattr(dialog, "title", None) or username or str(peer_id or "Untitled")
            ref = f"@{username}" if username else str(peer_id or "")
            if not ref:
                continue
            key = ref.lower()
            if key in seen_refs:
                continue
            seen_refs.add(key)
            if getattr(dialog, "is_channel", False):
                kind = "channel"
            elif getattr(dialog, "is_group", False):
                kind = "group"
            elif getattr(dialog, "is_user", False):
                kind = "user"
            else:
                kind = "chat"
            dialogs.append({
                "id": str(peer_id) if peer_id is not None else ref,
                "ref": ref,
                "title": str(title),
                "username": username,
                "kind": kind,
            })
    except Exception as e:
        # Keep the source page usable even if Telegram dialog enumeration fails.
        logger.warning("Unable to enumerate Telegram dialogs for source picker: %s", e)
        error = ""
    return web.json_response({
        "ok": True,
        "sources": configured,
        "profiles": profiles,
        "dialogs": dialogs,
        "error": error,
    })
@routes.post("/api/sources")
async def api_sources_post(request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    if "profiles" in data:
        profiles = save_source_profiles(data.get("profiles", []))
        sources = list(profiles.keys())
    else:
        sources = set_video_sources(data.get("sources", ["me"]))
        profiles = get_source_profiles()
    logger.info("Telegram video sources updated: %s", sources)
    return web.json_response({"ok": True, "sources": sources, "profiles": profiles})
@routes.get("/api/jobs")
async def api_jobs(request):
    return web.json_response({"ok": True, "jobs": job_summary()})
@routes.post("/api/jobs/{job_id}/pause")
async def api_job_pause(request):
    try:
        job = request_job_control(request.match_info["job_id"], "paused")
        return web.json_response({"ok": True, "job": job})
    except KeyError:
        return web.json_response({"ok": False, "error": "Job not found"}, status=404)
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)
@routes.post("/api/jobs/{job_id}/resume")
async def api_job_resume(request):
    try:
        job = request_job_control(request.match_info["job_id"], "running")
        return web.json_response({"ok": True, "job": job})
    except KeyError:
        return web.json_response({"ok": False, "error": "Job not found"}, status=404)
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)
@routes.post("/api/jobs/{job_id}/cancel")
async def api_job_cancel(request):
    try:
        job = request_job_control(request.match_info["job_id"], "cancel_requested")
        return web.json_response({"ok": True, "job": job})
    except KeyError:
        return web.json_response({"ok": False, "error": "Job not found"}, status=404)
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)
@routes.get("/api/admin/health")
async def api_admin_health(request):
    refresh_storage_download_mode()
    existing = 0
    missing = 0
    for rel_path in list(tags.keys()):
        abs_path = find_existing_file(rel_path)
        if abs_path and os.path.exists(abs_path):
            existing += 1
        else:
            missing += 1
    telegram_connected = False
    telegram_authorized = False
    try:
        telegram_connected = bool(client.is_connected())
        telegram_authorized = bool(await client.is_user_authorized()) if telegram_connected else False
    except Exception:
        pass
    storage = get_storage_stats()
    recent_failed_downloads = [item for item in list(download_history)[:50] if str(item.get("status",
    "")).lower() == "failed"]
    health_items = [
        {"name": "Telegram session", "ok": telegram_connected and telegram_authorized, "detail": "Connected and authorized" if telegram_connected and telegram_authorized else "Not connected or not authorized"},
        {"name": "Storage", "ok": not storage_is_low(), "detail": f"{storage.get('free_human')} free({storage.get('percent_free')}%)"},
        {"name": "FAISS index", "ok": faiss_index is not None and len(faiss_keys) == len(embeddings_store),
        "detail": f"{len(faiss_keys)} vectors / {len(embeddings_store)} embeddings"},
        {"name": "Missing files", "ok": missing == 0, "detail": f"{missing} missing / {existing} existing"},
        {"name": "Downloads", "ok": len(recent_failed_downloads) == 0, "detail": f"{len(download_status)}active; {len(recent_failed_downloads)} recent failures"},
    ]
    overall_ok = all(item["ok"] for item in health_items)
    return web.json_response({
        "ok": True,
        "overall_ok": overall_ok,
        "items": health_items,
        "telegram": {"connected": telegram_connected, "authorized": telegram_authorized},
        "storage": storage,
        "download_control": download_control,
        "faiss": {"available": faiss_index is not None, "vectors": len(faiss_keys), "embeddings":
        len(embeddings_store)},
        "files": {"indexed": len(tags), "existing": existing, "missing": missing},
        "downloads": {"active": len(download_status), "recent_failed": len(recent_failed_downloads)},
        "index_sweep": index_sweep_status,
        "jobs": job_summary(),
        "sources": get_source_profiles(),
        "trash_items": len(trash_index.get("items", [])),
        "backups": len(list_backups()),
        "database": sqlite_backend_info(),
    })
@routes.get("/api/admin/overview")
async def api_admin_overview(request):
    bias = compute_feedback_bias()
    strongest_bias = sorted(bias.items(), key=lambda x: abs(x[1]), reverse=True)[:5]
    return web.json_response({
        "counts": {
            "indexed_files": len(tags),
            "manual_overrides": len(feedback_data.get("manual_overrides", {})),
            "active_downloads": len(download_status),
            "recent_activity_items": len(download_history),
            "embeddings": len(embeddings_store),
            "trash_items": len(trash_index.get("items", [])),
            "backups": len(list_backups()),
            "review_queue": review_queue_count(limit=10000),
            "custom_tags": len(get_custom_tags()),
        },
        "system": {
            "auto_refresh": get_auto_refresh(),
            "download_directory": get_download_directory(),
            "preview_directory": PREVIEW_PATH,
            "thumbnail_directory": THUMB_PATH,
            "state_file": STATE_FILE,
            "database_file": DB_FILE,
            "state_backend": STATE_BACKEND,
            "feedback_file": FEEDBACK_FILE,
            "log_file": LOG_FILE,
            "trash_directory": TRASH_PATH,
            "backup_directory": BACKUP_PATH,
            "device": device,
        },
        "bulk_job": bulk_job,
        "index_sweep": index_sweep_status,
        "download_control": download_control,
        "jobs": job_summary(),
        "storage": get_storage_stats(),
        "storage_threshold": DOWNLOAD_STOP_THRESHOLD,
        "database": sqlite_backend_info(),
        "library_stats": compute_library_dashboard_stats(),
        "strongest_bias": [{"tag": tag, "bias": value} for tag, value in strongest_bias],
    })
@routes.get("/api/admin/stats")
async def api_admin_stats(request):
    return web.json_response({
        "ok": True,
        "library_stats": compute_library_dashboard_stats(probe_missing_limit=50),
    })
@routes.get("/api/admin/library-map")
async def api_admin_library_map(request):
    try:
        limit = int(request.query.get("limit", "25"))
    except Exception:
        limit = 25
    try:
        return web.json_response(library_map_payload(limit=max(5, min(limit, 100))))
    except Exception as e:
        logger.exception("Library map failed")
        return web.json_response({"ok": False, "error": str(e)}, status=500)
@routes.get("/api/personal/library")
async def api_personal_library(request):
    try:
        limit = int(request.query.get("limit", "200"))
    except Exception:
        limit = 200
    kind = request.query.get("kind", "all")
    try:
        items = personal_library_items(kind=kind, limit=limit)
        return web.json_response({"ok": True, "kind": kind, "items": items, "total_items": len(items),
        "version": APP_VERSION})
    except Exception as e:
        logger.exception("Personal library failed")
        return web.json_response({"ok": False, "error": str(e)}, status=500)
@routes.get("/api/admin/source-intelligence")
async def api_admin_source_intelligence(request):
    try:
        probe = max(0, min(int(request.query.get("probe", "20")), 100))
    except Exception:
        probe = 20
    report = await asyncio.to_thread(build_source_intelligence_report, probe)
    return web.json_response(report)
@routes.get("/api/admin/source-pipelines")
async def api_admin_source_pipelines(request):
    return web.json_response(build_source_pipeline_report())
@routes.post("/api/admin/source-pipelines/run")
async def api_admin_source_pipeline_run(request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    source_ref = normalize_source_ref(data.get("source_ref") or data.get("ref") or "me") or "me"
    max_messages = parse_source_limit(data.get("max_messages"), default=0, maximum=100000)
    title = f"Source pipeline: {source_display_name(source_ref)}"
    job_id = create_job("source_pipeline", title, total=max_messages or 500, source_ref=source_ref,
    metadata={"source_ref": source_ref, "max_messages": max_messages})
    update_job(job_id, note="Queued source pipeline", status="running", control="running")
    asyncio.create_task(run_source_pipeline_job(job_id, source_ref, {"max_messages": max_messages}))
    return web.json_response({"ok": True, "job": get_job(job_id), "pipelines":
    build_source_pipeline_report()})
@routes.get("/api/admin/telegram-metadata")
async def api_admin_telegram_metadata(request):
    try:
        limit = max(0, min(int(request.query.get("limit", "20")), 100))
    except Exception:
        limit = 20
    return web.json_response(build_telegram_metadata_summary(limit=limit))
@routes.post("/api/admin/telegram-metadata/backfill")
async def api_admin_telegram_metadata_backfill(request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    limit = parse_source_limit(data.get("limit"), default=500, maximum=100000)
    job_id = create_job("telegram_metadata", "Backfill Telegram metadata", total=limit or 500,
    metadata={"limit": limit})
    update_job(job_id, note="Queued Telegram metadata backfill", status="running", control="running")
    asyncio.create_task(run_telegram_metadata_backfill_job(job_id, limit=limit))
    return web.json_response({"ok": True, "job": get_job(job_id), "metadata":
    build_telegram_metadata_summary(limit=20)})
@routes.get("/api/admin/database")
async def api_admin_database(request):
    return web.json_response({"ok": True, "database": sqlite_backend_info()})
@routes.get("/api/privacy/status")
async def api_privacy_status(request):
    public = privacy_public_settings()
    public["unlocked"] = is_privacy_unlocked(request)
    public["version"] = APP_VERSION
    return web.json_response({"ok": True, "privacy": public})
@routes.post("/api/privacy/unlock")
async def api_privacy_unlock(request):
    data = await request.json()
    pin = str(data.get("pin") or "")
    if not privacy_is_enabled():
        return web.json_response({"ok": True, "privacy": {**privacy_public_settings(), "unlocked": True}})
    if not privacy_verify_pin(pin):
        return web.json_response({"ok": False, "error": "Incorrect PIN"}, status=403)
    token = create_privacy_session()
    resp = web.json_response({"ok": True, "token": token, "privacy": {**privacy_public_settings(), "unlocked":
    True}})
    resp.set_cookie(
        "telesaved_privacy_token",
        token,
        max_age=privacy_expiry_seconds(),
        httponly=False,
        samesite="Strict",
        path="/",
    )
    return resp
@routes.post("/api/privacy/lock")
async def api_privacy_lock(request):
    token = privacy_token_from_request(request)
    if token:
        PRIVACY_SESSION_TOKENS.pop(token, None)
    resp = web.json_response({"ok": True, "privacy": {**privacy_public_settings(), "unlocked": not
    privacy_is_enabled()}})
    resp.del_cookie("telesaved_privacy_token", path="/")
    return resp
@routes.get("/api/privacy/settings")
async def api_privacy_settings_get(request):
    return web.json_response({"ok": True, "privacy": {**privacy_public_settings(), "unlocked":
    is_privacy_unlocked(request)}})
@routes.post("/api/privacy/settings")
async def api_privacy_settings_post(request):
    data = await request.json()
    p = get_privacy_settings()
    current_pin = str(data.get("current_pin") or "")
    new_pin = str(data.get("new_pin") or "")
    if privacy_pin_configured() and not is_privacy_unlocked(request) and not privacy_verify_pin(current_pin):
        return web.json_response({"ok": False, "error": "Current PIN is required to change privacy settings"},
        status=403)
    requested_enabled = bool(data.get("enabled", p.get("enabled", False)))
    if new_pin:
        if len(new_pin) < PRIVACY_MIN_PIN_LENGTH:
            return web.json_response({"ok": False, "error": f"PIN must be at least {PRIVACY_MIN_PIN_LENGTH}characters"}, status=400)
        salt, digest = privacy_hash_pin(new_pin)
        p["pin_salt"] = salt
        p["pin_hash"] = digest
    elif requested_enabled and not privacy_pin_configured():
        return web.json_response({"ok": False, "error": "Set a PIN before enabling privacy lock"}, status=400)
    p["enabled"] = requested_enabled
    try:
        p["session_timeout_minutes"] = max(1, min(int(data.get("session_timeout_minutes",
        p.get("session_timeout_minutes", 15))), 1440))
    except Exception:
        p["session_timeout_minutes"] = 15
    p["privacy_blur"] = bool(data.get("privacy_blur", p.get("privacy_blur", True)))
    p["hide_filenames"] = bool(data.get("hide_filenames", p.get("hide_filenames", False)))
    p["require_unlock_for_delete"] = bool(data.get("require_unlock_for_delete",
    p.get("require_unlock_for_delete", True)))
    settings_data["privacy"] = p
    save_settings()
    logger.info("Privacy settings updated: enabled=%s configured=%s timeout=%s", p["enabled"],
    privacy_pin_configured(), p["session_timeout_minutes"])
    return web.json_response({"ok": True, "privacy": {**privacy_public_settings(), "unlocked":
    is_privacy_unlocked(request)}})
@routes.get("/api/admin/system")
async def api_admin_system_get(request):
    return web.json_response({
        "auto_refresh": get_auto_refresh(),
        "download_directory": get_download_directory(),
        "video_sources": get_video_sources(),
    })
@routes.post("/api/admin/system")
async def api_admin_system_post(request):
    data = await request.json()
    auto_refresh = str(data.get("auto_refresh", "10"))
    if auto_refresh not in {"10", "15", "30", "60", "never"}:
        auto_refresh = "10"
    download_directory = str(data.get("download_directory", "")).strip()
    if not download_directory:
        return web.json_response({"ok": False, "error": "Download directory cannot be blank"}, status=400)
    try:
        ensure_directory(download_directory)
    except Exception as e:
        logger.exception("Failed to use download directory %s", download_directory)
        return web.json_response({"ok": False, "error": str(e)}, status=400)
    settings_data["auto_refresh"] = auto_refresh
    settings_data["download_directory"] = download_directory
    if "video_sources" in data:
        settings_data["video_sources"] = normalize_video_sources(data.get("video_sources", []))
    save_settings()
    return web.json_response({"ok": True})
@routes.get("/api/admin/scoring")
async def api_admin_scoring_get(request):
    return web.json_response({
        "weights": TAG_WEIGHTS,
        "thresholds": TAG_THRESHOLDS,
        "bias": compute_feedback_bias(),
        "tags": PREDEFINED_TAGS,
        "nudenet_threshold": settings_data.get("nudenet_threshold", DEFAULT_NUDENET_THRESHOLD),
    })
@routes.post("/api/admin/scoring")
async def api_admin_scoring_post(request):
    data = await request.json()
    weights = data.get("weights", {})
    thresholds = data.get("thresholds", {})
    for tag in PREDEFINED_TAGS:
        try:
            TAG_WEIGHTS[tag] = max(0.1, min(float(weights.get(tag, TAG_WEIGHTS[tag])), 3.0))
            TAG_THRESHOLDS[tag] = max(0.0, min(float(thresholds.get(tag, TAG_THRESHOLDS[tag])), 1.5))
        except Exception:
            return web.json_response({"ok": False, "error": f"Invalid numeric value for {tag}"}, status=400)
    if "nudenet_threshold" in data:
        try:
            nt = float(data["nudenet_threshold"])
            if not (0.1 <= nt <= 0.95):
                return web.json_response({"ok": False, "error": "nudenet_threshold must be between 0.1 and 0.95"}, status=400)
            settings_data["nudenet_threshold"] = round(nt, 3)
        except (TypeError, ValueError):
            return web.json_response({"ok": False, "error": "nudenet_threshold must be a number"}, status=400)
    save_settings()
    return web.json_response({"ok": True})
@routes.post("/api/admin/scoring/reset")
async def api_admin_scoring_reset(request):
    for tag in PREDEFINED_TAGS:
        TAG_WEIGHTS[tag] = DEFAULT_TAG_WEIGHTS.get(tag, 1.0)
        TAG_THRESHOLDS[tag] = DEFAULT_TAG_THRESHOLDS.get(tag, 0.99)
    settings_data["nudenet_threshold"] = DEFAULT_NUDENET_THRESHOLD
    save_settings()
    return web.json_response({"ok": True})
@routes.get("/api/admin/logs")
async def api_admin_logs(request):
    lines = []
    whisper_lines = []
    try:
        limit = max(50, min(int(request.query.get("limit", "200") or "200"), 1000))
    except Exception:
        limit = 200
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()[-limit:]
        except Exception as e:
            lines = [f"Unable to read log file: {e}\n"]
    if os.path.exists(WHISPER_LOG_FILE):
        try:
            with open(WHISPER_LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                whisper_lines = f.readlines()[-limit:]
        except Exception as e:
            whisper_lines = [f"Unable to read Whisper log file: {e}\n"]
    combined_lines = list(lines)
    if whisper_lines:
        combined_lines.extend(["\n", "===== Whisper CLI Diagnostics =====\n"])
        combined_lines.extend(whisper_lines)
    return web.json_response({
        "lines": combined_lines,
        "app_lines": lines,
        "whisper_lines": whisper_lines,
        "log_file": LOG_FILE,
        "whisper_log_file": WHISPER_LOG_FILE,
    })
@routes.get("/api/admin/feedback")
async def api_admin_feedback(request):
    return web.json_response({
        "history": feedback_data.get("history", [])[:100]
    })
@routes.post("/api/admin/reprocess-all")
async def api_admin_reprocess_all(request):
    if bulk_job["running"]:
        return web.json_response({"ok": False, "error": "A bulk job is already running"}, status=409)
    asyncio.create_task(reprocess_all_files_job())
    return web.json_response({"ok": True})
@routes.post("/api/admin/migrate-library")
async def api_admin_migrate_library(request):
    try:
        result = migrate_library_files()
        return web.json_response({"ok": True, "result": result})
    except Exception as e:
        logger.exception("Library migration failed")
        return web.json_response({"ok": False, "error": str(e)}, status=500)
@routes.get("/api/admin/index-sweep")
async def api_admin_index_sweep_status(request):
    return web.json_response({"ok": True, "index_sweep": index_sweep_status})
@routes.post("/api/admin/index-sweep")
async def api_admin_index_sweep_run(request):
    result = await asyncio.to_thread(sweep_missing_index_entries, "manual")
    status = 409 if result.get("already_running") else 200
    return web.json_response(result, status=status)
@routes.get("/api/admin/cleanup/plan")
async def api_admin_cleanup_plan(request):
    deep = request.query.get("deep", "0") in {"1", "true", "yes"}
    plan = await asyncio.to_thread(build_smart_cleanup_plan, deep)
    return web.json_response(plan)
@routes.post("/api/admin/cleanup/action")
async def api_admin_cleanup_action(request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    action = data.get("action")
    limit = data.get("limit", 100)
    try:
        result, job = await run_tracked_blocking_job(
            "cleanup",
            f"Cleanup action: {action or 'unknown'}",
            run_cleanup_action,
            action,
            limit,
            total=1,
            current=str(action or "cleanup"),
            metadata={"action": action, "limit": limit},
        )
        payload = attach_job_to_result(result, job)
        status = 200 if payload.get("ok", True) else 400
        return web.json_response(payload, status=status)
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)
@routes.get("/api/admin/duplicates")
async def api_admin_duplicates(request):
    compute = request.query.get("scan", "0") in {"1", "true", "yes"}
    try:
        limit = max(0, min(int(request.query.get("limit", "250")), 2000))
    except Exception:
        limit = 250
    result = await asyncio.to_thread(build_duplicate_report, compute, limit)
    return web.json_response(result)
@routes.post("/api/admin/duplicates/scan")
async def api_admin_duplicates_scan(request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    try:
        limit = max(0, min(int(data.get("limit", 500)), 5000))
    except Exception:
        limit = 500
    try:
        result, job = await run_tracked_blocking_job(
            "duplicates",
            "Scan duplicate candidates",
            build_duplicate_report,
            True,
            limit,
            total=max(1, limit),
            current="Building duplicate report",
            metadata={"action": "scan", "limit": limit},
        )
        return web.json_response(attach_job_to_result(result, job))
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)
@routes.post("/api/admin/duplicates/delete")
async def api_admin_duplicates_delete(request):
    data = await request.json()
    paths = data.get("paths", [])
    if not isinstance(paths, list):
        return web.json_response({"ok": False, "error": "paths must be a list"}, status=400)
    try:
        result, job = await run_tracked_blocking_job(
            "duplicates",
            "Move selected duplicate files to Trash",
            bulk_delete_paths,
            paths,
            total=max(1, len(paths)),
            current="Moving selected duplicates to Trash",
            metadata={"action": "delete_selected", "count": len(paths)},
        )
        return web.json_response({"ok": True, **result, "job": job, "job_id": job.get("id") if job else ""})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)
@routes.post("/api/admin/duplicates/delete-exact")
async def api_admin_duplicates_delete_exact(request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    compute = data.get("scan", True) not in {False, "0", "false", "no"}
    try:
        limit = int(data.get("limit", len(tags)))
    except Exception:
        limit = len(tags)
    limit = max(0, min(limit, max(len(tags), 1)))
    try:
        result, job = await run_tracked_blocking_job(
            "duplicates",
            "Delete all exact duplicates",
            delete_all_exact_duplicates,
            compute,
            limit,
            total=max(1, limit),
            current="Moving exact duplicate copies to Trash",
            metadata={"action": "delete_all_exact", "scan": compute, "limit": limit},
        )
        payload = attach_job_to_result(result, job)
        return web.json_response(payload, status=200 if payload.get("ok", True) else 400)
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)
@routes.get("/api/admin/cleanup/studio")
async def api_admin_cleanup_studio(request):
    deep = request.query.get("deep", "0") in {"1", "true", "yes"}
    result = await asyncio.to_thread(build_cleanup_studio, deep)
    return web.json_response(result)
@routes.get("/api/admin/review/studio")
async def api_admin_review_studio(request):
    try:
        limit = max(1, min(int(request.query.get("limit", "200")), 500))
    except Exception:
        limit = 200
    include_skipped = request.query.get("include_skipped", "0") in {"1", "true", "yes"}
    category = request.query.get("category", "")
    result = await asyncio.to_thread(build_review_studio, limit, include_skipped, category)
    return web.json_response(result)
@routes.post("/api/admin/review/batch")
async def api_admin_review_batch(request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    paths = data.get("paths", [])
    if not isinstance(paths, list):
        return web.json_response({"ok": False, "error": "paths must be a list"}, status=400)
    action = str(data.get("action") or "").strip()
    selected_tags = data.get("tags", [])
    note = data.get("note", "")
    try:
        result, job = await run_tracked_blocking_job(
            "review",
            f"Review Studio batch: {action or 'unknown'}",
            apply_review_studio_batch,
            paths,
            action,
            selected_tags,
            note,
            total=max(1, len(paths)),
            current=f"Review batch {action or 'unknown'}",
            metadata={"action": action, "count": len(paths), "tags": selected_tags},
        )
        return web.json_response(attach_job_to_result(result, job), status=200 if result.get("ok", True) else
        400)
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)
@routes.get("/api/admin/duplicates/advanced")
async def api_admin_duplicates_advanced(request):
    compute = request.query.get("scan", "0") in {"1", "true", "yes"}
    try:
        limit = max(0, min(int(request.query.get("limit", "1000")), 10000))
    except Exception:
        limit = 1000
    result = await asyncio.to_thread(build_advanced_duplicate_report, compute, limit)
    return web.json_response(result)
@routes.get("/api/collections")
async def api_collections_list(request):
    collections = get_smart_collections()
    counts = collection_counts() if request.query.get("counts", "1") != "0" else {}
    return web.json_response({"ok": True, "collections": collections, "counts": counts})
@routes.post("/api/collections")
async def api_collections_create(request):
    data = await request.json()
    collections = get_smart_collections()
    col = normalize_collection_payload(data)
    ids = {c["id"] for c in collections}
    base = col["id"]
    idx = 2
    while col["id"] in ids:
        col["id"] = f"{base}-{idx}"
        idx += 1
    collections.append(col)
    save_smart_collections(collections)
    return web.json_response({"ok": True, "collection": col, "collections": get_smart_collections()})
@routes.put("/api/collections/{collection_id}")
async def api_collections_update(request):
    collection_id = safe_id(request.match_info["collection_id"])
    data = await request.json()
    collections = get_smart_collections()
    updated = None
    for idx, col in enumerate(collections):
        if col.get("id") == collection_id:
            updated = normalize_collection_payload({**col, **data, "id": collection_id}, col)
            collections[idx] = updated
            break
    if not updated:
        return web.json_response({"ok": False, "error": "Collection not found"}, status=404)
    save_smart_collections(collections)
    return web.json_response({"ok": True, "collection": updated, "collections": get_smart_collections()})
@routes.delete("/api/collections/{collection_id}")
async def api_collections_delete(request):
    collection_id = safe_id(request.match_info["collection_id"])
    before = get_smart_collections()
    after = [col for col in before if col.get("id") != collection_id]
    if len(after) == len(before):
        return web.json_response({"ok": False, "error": "Collection not found"}, status=404)
    save_smart_collections(after)
    return web.json_response({"ok": True, "deleted": collection_id, "collections": get_smart_collections()})
@routes.get("/api/collections/{collection_id}/items")
async def api_collections_items(request):
    collection_id = safe_id(request.match_info["collection_id"])
    col = next((c for c in get_smart_collections() if c.get("id") == collection_id), None)
    if not col:
        return web.json_response({"ok": False, "error": "Collection not found"}, status=404)
    page = parse_page(request.query.get("page", "1"))
    sort_by = request.query.get("sort") or col.get("sort") or "path"
    sort_dir = request.query.get("dir") or col.get("dir") or "asc"
    result = collection_items(col, page=page, sort_override=sort_by, dir_override=sort_dir)
    return web.json_response({"ok": True, "collection": col, **result})
@routes.get("/api/trash")
async def api_trash_list(request):
    return web.json_response({
        "ok": True,
        "items": trash_items(),
        "count": len(trash_index.get("items", [])),
        "trash_directory": TRASH_PATH,
    })
@routes.post("/api/trash/{trash_id}/restore")
async def api_trash_restore(request):
    trash_id = request.match_info["trash_id"]
    try:
        result = await asyncio.to_thread(restore_trash_item, trash_id)
        return web.json_response({"ok": True, "result": result})
    except Exception as e:
        logger.exception("Trash restore failed for %s", trash_id)
        return web.json_response({"ok": False, "error": str(e)}, status=500)
@routes.delete("/api/trash/{trash_id}")
async def api_trash_permanent_delete(request):
    trash_id = request.match_info["trash_id"]
    try:
        result = await asyncio.to_thread(permanently_delete_trash_item, trash_id)
        return web.json_response({"ok": True, "result": result})
    except Exception as e:
        logger.exception("Permanent trash delete failed for %s", trash_id)
        return web.json_response({"ok": False, "error": str(e)}, status=500)
@routes.post("/api/trash/empty")
async def api_trash_empty(request):
    result = await asyncio.to_thread(empty_trash)
    return web.json_response({"ok": True, **result})
@routes.get("/api/admin/visual-clusters")
async def api_admin_visual_clusters(request):
    try:
        cluster_count = int(request.query.get("clusters", "8"))
    except Exception:
        cluster_count = 8
    try:
        limit = int(request.query.get("limit", "800"))
    except Exception:
        limit = 800
    try:
        representative_count = int(request.query.get("representatives", "8"))
    except Exception:
        representative_count = 8
    result = await asyncio.to_thread(build_visual_cluster_report, cluster_count, limit, representative_count)
    return web.json_response(result)
@routes.post("/api/admin/visual-clusters/scan")
async def api_admin_visual_clusters_scan(request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    cluster_count = data.get("clusters", 8)
    limit = data.get("limit", 800)
    representative_count = data.get("representatives", 8)
    try:
        result, job = await run_tracked_blocking_job(
            "visual",
            "Build visual library clusters",
            build_visual_cluster_report,
            cluster_count,
            limit,
            representative_count,
            total=max(1, int(limit or 1)),
            current="Building visual clusters",
            metadata={"clusters": cluster_count, "limit": limit, "representatives": representative_count},
        )
        return web.json_response(attach_job_to_result(result, job))
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)
@routes.get("/api/admin/backups")
async def api_admin_backups_list(request):
    return web.json_response({"ok": True, "backups": list_backups(), "backup_directory": BACKUP_PATH})
@routes.post("/api/admin/backups")
async def api_admin_backups_create(request):
    try:
        label = "manual"
        try:
            data = await request.json()
            label = data.get("label") or "manual"
        except Exception:
            pass
        info, job = await run_tracked_blocking_job(
            "backup",
            "Create metadata backup",
            create_metadata_backup,
            label,
            total=1,
            current="Creating metadata backup",
            metadata={"label": label},
        )
        return web.json_response({"ok": True, "backup": info, "job": job, "job_id": job.get("id") if job else
        ""})
    except Exception as e:
        logger.exception("Backup creation failed")
        return web.json_response({"ok": False, "error": str(e)}, status=500)
@routes.get("/api/admin/backups/{filename}")
async def api_admin_backups_download(request):
    filename = safe_id(request.match_info["filename"])
    if not filename.endswith(".zip"):
        return web.Response(status=400, text="Invalid backup filename")
    try:
        path = safe_join(BACKUP_PATH, filename)
    except ValueError:
        return web.Response(status=403, text="Forbidden")
    if not os.path.exists(path):
        return web.Response(status=404, text="Backup not found")
    return web.FileResponse(path)
@routes.post("/api/admin/backups/restore")
async def api_admin_backups_restore(request):
    tmp_path = None
    try:
        content_type = request.headers.get("Content-Type", "")
        if "multipart/form-data" in content_type:
            reader = await request.multipart()
            field = await reader.next()
            if not field:
                return web.json_response({"ok": False, "error": "No backup file uploaded"}, status=400)
            fd, tmp_path = tempfile.mkstemp(prefix="telesaved-restore-", suffix=".zip")
            os.close(fd)
            with open(tmp_path, "wb") as f:
                while True:
                    chunk = await field.read_chunk()
                    if not chunk:
                        break
                    f.write(chunk)
        else:
            data = await request.json()
            filename = safe_id(data.get("filename", ""))
            if not filename.endswith(".zip"):
                return web.json_response({"ok": False, "error": "Invalid backup filename"}, status=400)
            tmp_path = safe_join(BACKUP_PATH, filename)
        result = await asyncio.to_thread(restore_metadata_backup, tmp_path)
        return web.json_response({"ok": True, "result": result})
    except Exception as e:
        logger.exception("Backup restore failed")
        return web.json_response({"ok": False, "error": str(e)}, status=500)
    finally:
        if tmp_path and tmp_path.startswith(tempfile.gettempdir()) and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
@routes.get("/api/watch")
async def api_watch(request):
    try:
        limit = int(request.query.get("limit", "100"))
    except Exception:
        limit = 100
    return web.json_response(build_watch_lists(limit=limit))
@routes.post("/api/item/{path:.*}/watch-later")
async def api_item_watch_later(request):
    rel_path = request.match_info["path"]
    if rel_path not in tags:
        return web.json_response({"ok": False, "error": "File not found in index"}, status=404)
    try:
        data = await request.json()
    except Exception:
        data = {}
    enabled = bool(data.get("watch_later", True))
    meta = metadata_store.setdefault(rel_path, {})
    meta["watch_later"] = enabled
    if enabled:
        meta["watch_later_added_at"] = now_stamp()
    else:
        meta.pop("watch_later_added_at", None)
    save_state()
    return web.json_response({"ok": True, "path": rel_path, **watch_state_for_path(rel_path)})
@routes.post("/api/item/{path:.*}/playback")
async def api_item_playback(request):
    rel_path = request.match_info["path"]
    if rel_path not in tags:
        return web.json_response({"ok": False, "error": "File not found in index"}, status=404)
    try:
        data = await request.json()
    except Exception:
        data = {}
    meta = metadata_store.setdefault(rel_path, {})
    try:
        position = max(0.0, float(data.get("position") or 0.0))
    except Exception:
        position = 0.0
    try:
        duration = max(0.0, float(data.get("duration") or meta.get("duration") or 0.0))
    except Exception:
        duration = 0.0
    meta["playback_position"] = position
    if duration > 0:
        meta["playback_duration"] = duration
        meta.setdefault("duration", duration)
    meta["last_watched_at"] = now_stamp()
    # Treat near-end playback as watched, but do not force it when duration is unknown.
    if duration > 0 and playback_percent(position, duration) >= 95.0:
        meta["watched"] = True
        meta["watched_at"] = now_stamp()
    save_state()
    return web.json_response({"ok": True, "path": rel_path, **watch_state_for_path(rel_path)})
@routes.post("/api/item/{path:.*}/watched")
async def api_item_watched(request):
    rel_path = request.match_info["path"]
    if rel_path not in tags:
        return web.json_response({"ok": False, "error": "File not found in index"}, status=404)
    try:
        data = await request.json()
    except Exception:
        data = {}
    watched = bool(data.get("watched", True))
    meta = metadata_store.setdefault(rel_path, {})
    meta["watched"] = watched
    meta["last_watched_at"] = now_stamp()
    if watched:
        meta["watched_at"] = now_stamp()
        try:
            duration = float(meta.get("playback_duration") or meta.get("duration") or 0.0)
            if duration > 0:
                meta["playback_position"] = duration
        except Exception:
            pass
    else:
        meta.pop("watched_at", None)
    save_state()
    return web.json_response({"ok": True, "path": rel_path, **watch_state_for_path(rel_path)})
@routes.get("/api/item/{path:.*}/scenes")
async def api_item_scenes_get(request):
    rel_path = request.match_info["path"]
    denied = require_source_access_response(request, rel_path)
    if denied:
        return denied
    if rel_path not in tags:
        return web.json_response({"ok": False, "error": "File not found in index"}, status=404)
    return web.json_response({"ok": True, "timeline": cached_scene_timeline(rel_path)})
@routes.post("/api/item/{path:.*}/scenes")
async def api_item_scenes_post(request):
    rel_path = request.match_info["path"]
    if rel_path not in tags:
        return web.json_response({"ok": False, "error": "File not found in index"}, status=404)
    try:
        data = await request.json()
    except Exception:
        data = {}
    try:
        threshold = float(data.get("threshold", 0.35))
    except Exception:
        threshold = 0.35
    try:
        max_scenes = int(data.get("max_scenes", 32))
    except Exception:
        max_scenes = 32
    try:
        min_gap = float(data.get("min_gap", 6.0))
    except Exception:
        min_gap = 6.0
    force = bool(data.get("force", True))
    try:
        timeline, job = await run_tracked_blocking_job(
            "timeline",
            "Generate scene timeline",
            build_scene_timeline,
            rel_path,
            total=1,
            current=rel_path,
            source_ref=(metadata_store.get(rel_path, {}) or {}).get("source_ref", ""),
            metadata={"path": rel_path, "force": force, "threshold": threshold, "max_scenes": max_scenes,
            "min_gap": min_gap},
            force=force,
            threshold=threshold,
            max_scenes=max_scenes,
            min_gap=min_gap,
        )
        return web.json_response({"ok": True, "timeline": timeline, "job": job, "job_id": job.get("id") if job else ""})
    except Exception as e:
        logger.exception("Scene timeline generation failed for %s", rel_path)
        return web.json_response({"ok": False, "error": str(e)}, status=500)
@routes.get("/api/item/{path:.*}")
async def api_item_detail(request):
    rel_path = request.match_info["path"]
    denied = require_source_access_response(request, rel_path)
    if denied:
        return denied
    try:
        item = build_item_detail_payload(rel_path, include_similar=True)
        return web.json_response(item)
    except FileNotFoundError:
        return web.json_response({"ok": False, "error": "File not found in index"}, status=404)
    except Exception as e:
        logger.exception("Item detail failed for %s", rel_path)
        return web.json_response({"ok": False, "error": str(e)}, status=500)
def _srt_timestamp(seconds):
    """Convert float seconds to SRT timestamp format HH:MM:SS,mmm."""
    try:
        total_ms = int(round(float(seconds or 0) * 1000))
    except Exception:
        total_ms = 0
    total_ms = max(0, total_ms)
    ms = total_ms % 1000
    total_s = total_ms // 1000
    h = total_s // 3600
    m = (total_s % 3600) // 60
    s = total_s % 60
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def _vtt_timestamp(seconds):
    """Convert float seconds to WebVTT timestamp format HH:MM:SS.mmm."""
    return _srt_timestamp(seconds).replace(",", ".")

def _segments_to_srt(segments):
    """Convert Whisper transcript segments list to SRT format string."""
    lines = []
    for i, seg in enumerate(segments or [], start=1):
        start = _srt_timestamp(seg.get("start", 0))
        end = _srt_timestamp(seg.get("end", 0))
        text = str(seg.get("text") or "").strip()
        lines.append(f"{i}\n{start} --> {end}\n{text}\n")
    return "\n".join(lines)

def _segments_to_vtt(segments):
    """Convert Whisper transcript segments list to WebVTT format string."""
    lines = ["WEBVTT", ""]
    for i, seg in enumerate(segments or [], start=1):
        start = _vtt_timestamp(seg.get("start", 0))
        end = _vtt_timestamp(seg.get("end", 0))
        text = str(seg.get("text") or "").strip()
        lines.append(f"{i}\n{start} --> {end}\n{text}\n")
    return "\n".join(lines)

@routes.get("/api/item/{path:.*}/transcript.srt")
async def api_item_transcript_srt(request):
    rel_path = request.match_info["path"]
    denied = require_source_access_response(request, rel_path)
    if denied:
        return denied
    audio = audio_payload_for_path(rel_path)
    segments = audio.get("transcript_segments") or []
    if not segments:
        return web.json_response({"ok": False, "error": "No transcript available"}, status=404)
    filename = os.path.splitext(os.path.basename(rel_path))[0] + ".srt"
    return web.Response(
        text=_segments_to_srt(segments),
        content_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

@routes.get("/api/item/{path:.*}/transcript.vtt")
async def api_item_transcript_vtt(request):
    rel_path = request.match_info["path"]
    denied = require_source_access_response(request, rel_path)
    if denied:
        return denied
    audio = audio_payload_for_path(rel_path)
    segments = audio.get("transcript_segments") or []
    if not segments:
        return web.json_response({"ok": False, "error": "No transcript available"}, status=404)
    filename = os.path.splitext(os.path.basename(rel_path))[0] + ".vtt"
    return web.Response(
        text=_segments_to_vtt(segments),
        content_type="text/vtt",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

@routes.post("/api/item/{path:.*}/notes")
async def api_item_notes_save(request):
    rel_path = request.match_info["path"]
    if rel_path not in tags:
        return web.json_response({"ok": False, "error": "File not found in index"}, status=404)
    try:
        data = await request.json()
    except Exception:
        data = {}
    notes = str(data.get("notes") or "")[:10000]
    metadata_store.setdefault(rel_path, {})["notes"] = notes
    metadata_store.setdefault(rel_path, {})["notes_updated_at"] = datetime.now().isoformat(timespec="seconds")
    save_state()
    return web.json_response({"ok": True, "notes": notes})
@routes.post("/api/item/{path:.*}/personal")
async def api_item_personal_save(request):
    rel_path = request.match_info["path"]
    if rel_path not in tags:
        return web.json_response({"ok": False, "error": "File not found in index"}, status=404)
    try:
        data = await request.json()
    except Exception:
        data = {}
    meta = metadata_store.setdefault(rel_path, {})
    if "favorite" in data:
        meta["favorite"] = bool(data.get("favorite"))
    if "rating" in data:
        try:
            meta["rating"] = max(0, min(int(data.get("rating") or 0), 5))
        except Exception:
            meta["rating"] = 0
    if "personal_state" in data:
        state_name = str(data.get("personal_state") or "unreviewed").strip() or "unreviewed"
        allowed_states = {"unreviewed", "watch_next", "keep", "maybe", "delete_candidate", "archived"}
        if state_name not in allowed_states:
            state_name = "unreviewed"
        meta["personal_state"] = state_name
    meta["personal_updated_at"] = now_stamp()
    save_state()
    return web.json_response({"ok": True, "path": rel_path, **personal_state_for_path(rel_path)})
@routes.get("/api/item/{path:.*}/similar")
async def api_item_similar(request):
    rel_path = request.match_info["path"]
    denied = require_source_access_response(request, rel_path)
    if denied:
        return denied
    if rel_path not in tags:
        return web.json_response({"ok": False, "error": "File not found in index"}, status=404)
    try:
        limit = int(request.query.get("limit", "8"))
    except Exception:
        limit = 8
    return web.json_response({"ok": True, "items": similar_video_items(rel_path, limit=limit)})
@routes.post("/api/item/{path:.*}/rename")
async def api_item_rename(request):
    rel_path = request.match_info["path"]
    if rel_path not in tags:
        return web.json_response({"ok": False, "error": "File not found in index"}, status=404)
    data = await request.json()
    new_name = data.get("filename") or data.get("name") or ""
    try:
        result = await asyncio.to_thread(rename_indexed_file, rel_path, new_name)
        new_path = result.get("new_path", rel_path)
        return web.json_response({
            "ok": True,
            "result": result,
            "item": serialize_item({
                "path": new_path,
                "tags": get_effective_tags(new_path),
                "score_map": scores_store.get(new_path, {}),
                "manual_override": get_manual_override(new_path) is not None,
            })
        })
    except Exception as e:
        logger.exception("Rename failed for %s", rel_path)
        return web.json_response({"ok": False, "error": str(e)}, status=500)
@routes.post("/api/item/{path:.*}/tags")
async def api_item_tags_save(request):
    rel_path = request.match_info["path"]
    if rel_path not in tags:
        return web.json_response({"ok": False, "error": "File not found in index"}, status=404)
    data = await request.json()
    selected = data.get("tags", [])
    selected = sanitize_tags(selected)
    original_tags = list(tags.get(rel_path, []))
    set_manual_override(rel_path, selected, original_tags)
    # Write label_source rows for the user's tag changes.
    try:
        original_set = set(original_tags)
        selected_set = set(selected)
        existing_labels = get_tag_labels(rel_path)
        for t in selected_set:
            if t not in original_set:
                write_tag_label(rel_path, t, "user_added")
            elif existing_labels.get(t) == "ai_inferred":
                write_tag_label(rel_path, t, "user_confirmed")
        for t in original_set - selected_set:
            if existing_labels.get(t) == "ai_inferred":
                write_tag_label(rel_path, t, "user_corrected")
    except Exception as _lbl_err:
        logger.warning("Failed to write tag labels for %s: %s", rel_path, _lbl_err)
    return web.json_response({"ok": True})
@routes.post("/api/item/{path:.*}/tags/clear")
async def api_item_tags_clear(request):
    rel_path = request.match_info["path"]
    if rel_path not in tags:
        return web.json_response({"ok": False, "error": "File not found in index"}, status=404)
    clear_manual_override(rel_path)
    return web.json_response({"ok": True})
@routes.post("/api/item/{path:.*}/reprocess")
async def api_item_reprocess(request):
    rel_path = request.match_info["path"]
    if rel_path not in tags:
        return web.json_response({"ok": False, "error": "File not found in index"}, status=404)
    try:
        _, job = await run_tracked_blocking_job(
            "processing",
            "Reprocess video",
            reprocess_file,
            rel_path,
            total=1,
            current=rel_path,
            source_ref=(metadata_store.get(rel_path, {}) or {}).get("source_ref", ""),
            metadata={"path": rel_path, "action": "reprocess"},
        )
        return web.json_response({"ok": True, "job": job, "job_id": job.get("id") if job else ""})
    except Exception as e:
        logger.exception("Manual reprocess failed for %s", rel_path)
        return web.json_response({"ok": False, "error": str(e)}, status=500)
@routes.delete("/api/item/{path:.*}")
async def api_item_delete(request):
    rel_path = request.match_info["path"]
    if rel_path not in tags:
        return web.json_response({"ok": False, "error": "File not found in index"}, status=404)
    try:
        result, job = await run_tracked_blocking_job(
            "trash",
            "Move video to Trash",
            delete_indexed_file,
            rel_path,
            total=1,
            current=rel_path,
            source_ref=(metadata_store.get(rel_path, {}) or {}).get("source_ref", ""),
            metadata={"path": rel_path, "action": "soft_delete"},
        )
        return web.json_response({"ok": True, "result": result, "job": job, "job_id": job.get("id") if job else ""})
    except Exception as e:
        logger.exception("Delete failed for %s", rel_path)
        return web.json_response({"ok": False, "error": str(e)}, status=500)
@routes.post("/api/delete-bulk")
async def api_delete_bulk(request):
    data = await request.json()
    paths = data.get("paths", [])
    if not isinstance(paths, list):
        return web.json_response({"ok": False, "error": "paths must be a list"}, status=400)
    try:
        result, job = await run_tracked_blocking_job(
            "trash",
            "Move selected videos to Trash",
            bulk_delete_paths,
            paths,
            total=max(1, len(paths)),
            current="Moving selected videos to Trash",
            metadata={"action": "bulk_delete", "count": len(paths)},
        )
        return web.json_response({"ok": True, **result, "job": job, "job_id": job.get("id") if job else ""})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)
@routes.post("/api/delete-tag")
async def api_delete_tag(request):
    data = await request.json()
    tag = sanitize_tags([data.get("tag")])
    tag = tag[0] if tag else ""
    if not tag:
        return web.json_response({"ok": False, "error": "Invalid tag"}, status=400)
    targets = [path for path in list(tags.keys()) if tag in get_effective_tags(path)]
    try:
        result, job = await run_tracked_blocking_job(
            "trash",
            f"Move videos tagged {tag} to Trash",
            bulk_delete_paths,
            targets,
            total=max(1, len(targets)),
            current=f"Moving {tag} videos to Trash",
            metadata={"action": "delete_by_tag", "tag": tag, "count": len(targets)},
        )
        return web.json_response({"ok": True, "tag": tag, **result, "job": job, "job_id": job.get("id") if job else ""})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)
@routes.post("/api/bulk/tags")
async def api_bulk_tags(request):
    data = await request.json()
    paths = data.get("paths", [])
    selected = data.get("tags", [])
    action = data.get("action", "add")
    if not isinstance(paths, list):
        return web.json_response({"ok": False, "error": "paths must be a list"}, status=400)
    try:
        result, job = await run_tracked_blocking_job(
            "tags",
            f"Bulk {action} tags",
            bulk_update_tags,
            paths,
            selected,
            action,
            total=max(1, len(paths)),
            current="Updating tags",
            metadata={"action": action, "tags": selected, "count": len(paths)},
        )
        return web.json_response({"ok": True, **result, "job": job, "job_id": job.get("id") if job else ""})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)
@routes.post("/api/bulk/reprocess")
async def api_bulk_reprocess(request):
    if bulk_job.get("running"):
        return web.json_response({"ok": False, "error": "A bulk job is already running"}, status=409)
    data = await request.json()
    paths = data.get("paths", [])
    if not isinstance(paths, list):
        return web.json_response({"ok": False, "error": "paths must be a list"}, status=400)
    clean_paths = [str(path) for path in paths if str(path) in tags]
    asyncio.create_task(reprocess_selected_files_job(clean_paths))
    return web.json_response({"ok": True, "queued": len(clean_paths)})
@routes.get("/api/review")
async def api_review_queue(request):
    try:
        limit = max(1, min(int(request.query.get("limit", "50")), 250))
    except Exception:
        limit = 50
    include_skipped = request.query.get("include_skipped", "0") in {"1", "true", "yes"}
    return web.json_response({"ok": True, **build_review_queue(limit=limit, include_skipped=include_skipped)})
@routes.post("/api/review/{path:.*}/accept")
async def api_review_accept(request):
    rel_path = request.match_info["path"]
    if rel_path not in tags:
        return web.json_response({"ok": False, "error": "File not found in index"}, status=404)
    current = list(get_effective_tags(rel_path))
    set_manual_override(rel_path, current, tags.get(rel_path, []))
    review_skip_map().pop(rel_path, None)
    save_feedback()
    return web.json_response({"ok": True, "path": rel_path, "tags": current})
@routes.post("/api/review/{path:.*}/skip")
async def api_review_skip(request):
    rel_path = request.match_info["path"]
    if rel_path not in tags:
        return web.json_response({"ok": False, "error": "File not found in index"}, status=404)
    skips = review_skip_map()
    skips[rel_path] = {
        "skipped_at": datetime.now().isoformat(timespec="seconds"),
        "reasons": review_reason_for_path(rel_path),
    }
    save_feedback()
    return web.json_response({"ok": True, "path": rel_path})
@routes.post("/api/review/{path:.*}/unskip")
async def api_review_unskip(request):
    rel_path = request.match_info["path"]
    review_skip_map().pop(rel_path, None)
    save_feedback()
    return web.json_response({"ok": True, "path": rel_path})
@routes.get("/api/rules")
async def api_rules_list(request):
    rules = [serialize_rule(rule) for rule in get_rules()]
    return web.json_response({"ok": True, "version": APP_VERSION, "rules": rules, "tags": PREDEFINED_TAGS})
@routes.post("/api/rules")
async def api_rules_create(request):
    data = await request.json()
    rules = get_rules()
    rule = normalize_rule_payload(data)
    existing_ids = {r.get("id") for r in rules}
    base = rule["id"]
    idx = 2
    while rule["id"] in existing_ids:
        rule["id"] = f"{base}-{idx}"
        idx += 1
    rules.append(rule)
    save_rules(rules)
    return web.json_response({"ok": True, "rule": serialize_rule(rule), "rules": [serialize_rule(r) for r in
    get_rules()]})
@routes.put("/api/rules/{rule_id}")
async def api_rules_update(request):
    rule_id = safe_id(request.match_info["rule_id"])
    data = await request.json()
    rules = get_rules()
    updated = None
    for idx, existing in enumerate(rules):
        if existing.get("id") == rule_id:
            updated = normalize_rule_payload({**existing, **data, "id": rule_id}, existing)
            rules[idx] = updated
            break
    if not updated:
        return web.json_response({"ok": False, "error": "Rule not found"}, status=404)
    save_rules(rules)
    return web.json_response({"ok": True, "rule": serialize_rule(updated), "rules": [serialize_rule(r) for r
    in get_rules()]})
@routes.delete("/api/rules/{rule_id}")
async def api_rules_delete(request):
    rule_id = safe_id(request.match_info["rule_id"])
    before = get_rules()
    after = [rule for rule in before if rule.get("id") != rule_id]
    if len(after) == len(before):
        return web.json_response({"ok": False, "error": "Rule not found"}, status=404)
    save_rules(after)
    return web.json_response({"ok": True, "deleted": rule_id, "rules": [serialize_rule(r) for r in
    get_rules()]})
@routes.post("/api/rules/run")
async def api_rules_run(request):
    data = await request.json()
    dry_run = bool(data.get("dry_run", True))
    limit = int(data.get("limit", 0) or 0)
    only_auto = bool(data.get("only_auto", False))
    rule_ids = data.get("rule_ids") or None
    if rule_ids and not isinstance(rule_ids, list):
        rule_ids = [str(rule_ids)]
    try:
        result, job = await run_tracked_blocking_job(
            "rules",
            "Run rules engine",
            run_rules,
            rule_ids,
            dry_run,
            limit,
            only_auto,
            "manual",
            total=max(1, len(rule_ids or get_rules())),
            current="Applying rules" if not dry_run else "Dry-run rules",
            metadata={"rule_ids": rule_ids, "dry_run": dry_run, "limit": limit, "only_auto": only_auto},
        )
        return web.json_response(attach_job_to_result(result, job))
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)
@routes.post("/api/rules/{rule_id}/run")
async def api_rule_run_one(request):
    rule_id = safe_id(request.match_info["rule_id"])
    data = await request.json()
    dry_run = bool(data.get("dry_run", True))
    limit = int(data.get("limit", 0) or 0)
    try:
        result, job = await run_tracked_blocking_job(
            "rules",
            f"Run rule: {rule_id}",
            run_rules,
            [rule_id],
            dry_run,
            limit,
            False,
            "manual",
            total=1,
            current="Applying rule" if not dry_run else "Dry-run rule",
            metadata={"rule_id": rule_id, "dry_run": dry_run, "limit": limit},
        )
        return web.json_response(attach_job_to_result(result, job))
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)
@routes.get("/api/audio/overview")
async def api_audio_overview(request):
    return web.json_response(build_audio_overview())
@routes.get("/api/audio/search")
async def api_audio_search(request):
    query = request.query.get("q", "").strip()
    try:
        limit = int(request.query.get("limit", "100"))
    except Exception:
        limit = 100
    return web.json_response({
        "ok": True,
        "query": query,
        "results": search_audio_transcripts(query, limit=max(1, min(limit, 250))),
    })
@routes.post("/api/audio/analyze-missing")
async def api_audio_analyze_missing(request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    try:
        limit = int(data.get("limit", 25))
    except Exception:
        limit = 25
    limit = max(1, min(limit, 100))
    transcribe = bool(data.get("transcribe", False))
    job_id = create_job(
        "audio",
        "Analyze missing audio metadata" + (" and transcripts" if transcribe else ""),
        total=limit,
        metadata={"action": "analyze_missing", "limit": limit, "transcribe": transcribe},
    )
    processed = []
    errors = []
    update_job(job_id, current="Scanning library for missing audio metadata", processed=0, total=limit)
    for rel_path in list(tags.keys()):
        await wait_job_if_paused(job_id)
        if job_cancel_requested(job_id):
            finish_job(job_id, "cancelled", "Cancelled by user")
            return web.json_response({"ok": False, "cancelled": True, "processed": processed, "errors":
            errors, "job": get_job(job_id), "job_id": job_id})
        audio = (metadata_store.get(rel_path, {}) or {}).get("audio") or {}
        if audio and audio.get("status") == "complete":
            continue
        try:
            update_job(job_id, current=rel_path, processed=len(processed), errors=len(errors))
            payload = await asyncio.to_thread(analyze_audio_for_path, rel_path, False, transcribe)
            processed.append({"path": rel_path, "status": payload.get("status"), "has_audio":
            payload.get("has_audio"), "transcript_status": payload.get("transcript_status")})
        except Exception as e:
            logger.exception("Audio analysis failed for %s", rel_path)
            errors.append({"path": rel_path, "error": str(e)})
            update_job(job_id, errors=len(errors))
        if len(processed) >= limit:
            break
    update_job(job_id, processed=len(processed), total=max(limit, len(processed)), errors=len(errors),
    current="Audio analysis complete")
    finish_job(job_id, "complete", f"Audio analysis complete: {len(processed)} processed, {len(errors)}errors")
    return web.json_response({"ok": True, "processed": processed, "errors": errors, "overview":
    build_audio_overview(), "job": get_job(job_id), "job_id": job_id})
@routes.get("/api/item/{path:.*}/audio")
async def api_item_audio_get(request):
    rel_path = request.match_info["path"]
    denied = require_source_access_response(request, rel_path)
    if denied:
        return denied
    if rel_path not in tags:
        return web.json_response({"ok": False, "error": "File not found in index"}, status=404)
    return web.json_response({"ok": True, "audio": audio_payload_for_path(rel_path)})
# Per-item in-flight lock — prevents duplicate concurrent Whisper/AI jobs on the same file.
_audio_analyze_locks: dict = {}

@routes.post("/api/item/{path:.*}/audio/analyze")
async def api_item_audio_analyze(request):
    rel_path = request.match_info["path"]
    denied = require_source_access_response(request, rel_path)
    if denied:
        return denied
    if rel_path not in tags:
        return web.json_response({"ok": False, "error": "File not found in index"}, status=404)
    # Reject if a job for this exact file is already in flight
    if _audio_analyze_locks.get(rel_path):
        return web.json_response({"ok": False, "error": "Audio analysis already in progress for this item"}, status=409)
    try:
        data = await request.json()
    except Exception:
        data = {}
    force = bool(data.get("force", True))
    transcribe = bool(data.get("transcribe", False))
    _audio_analyze_locks[rel_path] = True
    try:
        audio, job = await run_tracked_blocking_job(
            "audio",
            "Analyze audio/transcript" if transcribe else "Analyze audio",
            analyze_audio_for_path,
            rel_path,
            force,
            transcribe,
            total=1,
            current=rel_path,
            source_ref=(metadata_store.get(rel_path, {}) or {}).get("source_ref", ""),
            metadata={"path": rel_path, "force": force, "transcribe": transcribe},
        )
        return web.json_response({"ok": True, "audio": audio, "job": job, "job_id": job.get("id") if job else ""})
    except Exception as e:
        logger.exception("Audio analysis failed for %s", rel_path)
        return web.json_response({"ok": False, "error": str(e)}, status=500)
    finally:
        _audio_analyze_locks.pop(rel_path, None)
@routes.post("/api/item/{path:.*}/audio/transcript")
async def api_item_audio_transcript_save(request):
    """Save user-edited transcript segments back to the audio metadata store.

    Accepts a JSON body with a ``segments`` list — each element must be a dict
    with at least a ``text`` field.  Other fields (start, end, speaker, etc.)
    are preserved as-is so the enriched payload remains intact.

    The edited segments are written to ``metadata_store[path]["audio"]``
    and immediately persisted to SQLite so edits survive a restart.
    """
    rel_path = request.match_info["path"]
    denied = require_source_access_response(request, rel_path)
    if denied:
        return denied
    if rel_path not in tags:
        return web.json_response({"ok": False, "error": "File not found in index"}, status=404)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Invalid JSON body"}, status=400)
    segments = data.get("segments")
    if not isinstance(segments, list):
        return web.json_response({"ok": False, "error": "'segments' must be a list"}, status=400)
    # Guard against oversized payloads
    MAX_SEGMENTS = 5000
    if len(segments) > MAX_SEGMENTS:
        return web.json_response({"ok": False, "error": f"Too many segments: max {MAX_SEGMENTS}"}, status=400)
    # Validate each segment has at least a text field and a sane length
    MAX_SEGMENT_TEXT = 10000
    for i, seg in enumerate(segments):
        if not isinstance(seg, dict):
            return web.json_response({"ok": False, "error": f"Segment {i} is not an object"}, status=400)
        if "text" not in seg:
            return web.json_response({"ok": False, "error": f"Segment {i} missing 'text' field"}, status=400)
        if len(str(seg["text"])) > MAX_SEGMENT_TEXT:
            return web.json_response({"ok": False, "error": f"Segment {i} text exceeds {MAX_SEGMENT_TEXT} characters"}, status=400)
    # Write into metadata_store
    meta = metadata_store.setdefault(rel_path, {})
    audio = meta.setdefault("audio", {})
    audio["transcript_segments"] = segments
    # Rebuild transcript_text and transcript_script from the edited segments
    audio["transcript_text"] = " ".join(str(seg.get("text", "")).strip() for seg in segments if seg.get("text"))
    audio["transcript_script"] = transcript_script_from_segments(segments, audio["transcript_text"])
    # Persist to SQLite immediately
    try:
        persist_state_to_sqlite(state_ids, state_hashes, tags, scores_store, embeddings_store, metadata_store)
    except Exception:
        logger.exception("Failed to persist transcript edits for %s", rel_path)
        return web.json_response({"ok": False, "error": "Transcript updated in memory but failed to persist to database"}, status=500)
    logger.info("Transcript segments saved for %s (%d segments)", rel_path, len(segments))
    return web.json_response({
        "ok": True,
        "path": rel_path,
        "segment_count": len(segments),
        "transcript_text": audio["transcript_text"],
    })

# ================= STATIC FILES =================
@routes.get("/frame-preview/{video_key}/{frame_name}")
async def serve_frame_preview(request):
    try:
        video_key = os.path.basename(unquote(request.match_info["video_key"]))
        rel_guess = video_key.replace("__", os.sep)
        if rel_guess:
            denied = require_source_access_response(request, rel_guess)
            if denied:
                return denied
        frame_name = os.path.basename(unquote(request.match_info["frame_name"]))
        if not frame_name.lower().endswith(".jpg"):
            return web.Response(status=403, text="Forbidden")
        frame_abs = safe_join(FRAME_PATH, video_key, frame_name)
    except Exception:
        return web.Response(status=403, text="Forbidden")
    if not os.path.exists(frame_abs):
        return web.Response(status=404, text="Frame not found")
    return web.FileResponse(frame_abs)
@routes.get("/files/{path:.*}")
async def serve_file(request):
    rel_path = request.match_info["path"]
    denied = require_source_access_response(request, rel_path)
    if denied:
        return denied
    try:
        abs_path = safe_abs_path(rel_path, get_download_directory())
    except ValueError:
        return web.Response(status=403, text="Forbidden")
    if not os.path.exists(abs_path):
        try:
            abs_path = safe_abs_path(rel_path, BASE_PATH)
        except ValueError:
            return web.Response(status=403, text="Forbidden")
    if not os.path.exists(abs_path):
        return web.Response(status=404, text="File not found")
    return web.FileResponse(abs_path)
@routes.get("/previews/{name:.*}")
async def serve_preview(request):
    name = request.match_info["name"]
    rel_guess = rel_path_from_derived_name(name)
    if rel_guess:
        denied = require_source_access_response(request, rel_guess)
        if denied:
            return denied
    try:
        preview_abs = safe_abs_path(name, PREVIEW_PATH)
    except ValueError:
        return web.Response(status=403, text="Forbidden")
    if not os.path.exists(preview_abs):
        return web.Response(status=404, text="Preview not found")
    return web.FileResponse(preview_abs)
@routes.get("/thumbs/{name:.*}")
async def serve_thumb(request):
    name = request.match_info["name"]
    rel_guess = rel_path_from_derived_name(name)
    if rel_guess:
        denied = require_source_access_response(request, rel_guess)
        if denied:
            return denied
    try:
        thumb_abs = safe_abs_path(name, THUMB_PATH)
    except ValueError:
        return web.Response(status=403, text="Forbidden")
    if not os.path.exists(thumb_abs):
        return web.Response(status=404, text="Thumbnail not found")
    return web.FileResponse(thumb_abs)
@routes.get("/scenes/{name:.*}")
async def serve_scene_thumb(request):
    name = request.match_info["name"]
    rel_guess = rel_path_from_derived_name(name)
    if rel_guess:
        denied = require_source_access_response(request, rel_guess)
        if denied:
            return denied
    try:
        scene_abs = safe_abs_path(name, SCENE_PATH)
    except ValueError:
        return web.Response(status=403, text="Forbidden")
    if not os.path.exists(scene_abs):
        return web.Response(status=404, text="Scene thumbnail not found")
    return web.FileResponse(scene_abs)
# ================= FRONTEND =================
@routes.get("/")
async def serve_spa_root(request):
    if not os.path.exists(INDEX_FILE):
        return web.Response(status=500, text="frontend/index.html not found")
    return web.FileResponse(INDEX_FILE)
@routes.get("/{tail:.*}")
async def serve_spa_fallback(request):
    tail = request.match_info["tail"]
    if tail.startswith("api/") or tail.startswith("files/") or tail.startswith("previews/") or tail.startswith("thumbs/") or tail.startswith("scenes/"):
        return web.Response(status=404, text="Not found")
    if not os.path.exists(INDEX_FILE):
        return web.Response(status=500, text="frontend/index.html not found")
    return web.FileResponse(INDEX_FILE)
# ================= TELEGRAM =================
client = TelegramClient(SESSION_FILE, api_id, api_hash) if TELEGRAM_CONFIGURED else None
if client is not None:
    @client.on(events.NewMessage())
    async def handler(event):
        try:
            if not await event_source_is_enabled(event):
                return
        except Exception as e:
            logger.warning("Could not evaluate event source for message %s: %s", event.message.id, e)
            return
        title = "Configured Source"
        source_ref = "me"
        try:
            chat = await event.get_chat()
            title = await entity_title(chat, getattr(event.message, "chat_id", "source"))
            try:
                source_ref = str(utils.get_peer_id(chat))
            except Exception:
                source_ref = str(getattr(event.message, "chat_id", "") or "me")
        except Exception:
            pass
        profile = get_source_profile(source_ref, title)
        if not message_matches_source_media_filter(event.message, profile):
            return
        logger.info("New media message received from %s: %s", title, event.message.id)
        await process_message(event.message, source_ref=source_ref, source_title=title)
else:
    async def handler(event):
        return None
async def initial_sync():
    if client is None:
        logger.warning("Initial sync skipped because Telegram is not configured. Complete first-run setup and restart.")
        return
    sources = enabled_video_sources()
    logger.info("Starting initial sync for sources: %s", sources)
    for source_ref in sources:
        try:
            entity = await resolve_video_source(source_ref)
            title = await entity_title(entity, source_ref)
            profile = get_source_profile(source_ref, title)
            if not profile.get("enabled", True) or not profile.get("sync_enabled", True):
                logger.info("Skipping disabled source %s", source_ref)
                continue
            logger.info("Syncing source %s (%s)", source_ref, title)
            max_messages = parse_source_limit(profile.get("max_messages_per_run"), default=0, maximum=100000)
            async for msg in client.iter_messages(entity, limit=max_messages or None):
                if message_matches_source_media_filter(msg, profile):
                    await process_message(msg, source_ref=source_ref, source_title=title)
        except Exception as e:
            logger.exception("Initial sync failed for source %s: %s", source_ref, e)
    logger.info("Initial sync complete")
# ================= MAIN =================
async def main():
    ensure_directory(get_download_directory())
    ensure_directory(PREVIEW_PATH)
    ensure_directory(THUMB_PATH)
    app = web.Application(client_max_size=1024**3, middlewares=[auth_middleware, privacy_middleware])
    app.add_routes(routes)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()
    asyncio.create_task(periodic_missing_index_sweep())
    asyncio.create_task(job_worker_loop())
    asyncio.create_task(_connectivity_poller())
    logger.info("SPA backend listening on port 8080")
    logger.info("Current download directory: %s", get_download_directory())
    logger.info("Current auto-refresh setting: %s", get_auto_refresh())
    logger.info("Current Telegram video sources: %s", get_video_sources())
    if storage_is_low():
        set_download_mode("paused_storage", f"Storage below {DOWNLOAD_STOP_THRESHOLD}% on startup")
        logger.warning("Downloads paused on startup because free storage is below %.1f%%",
        DOWNLOAD_STOP_THRESHOLD)
    if TELEGRAM_CONFIGURED and client is not None:
        await initial_sync()
        await client.run_until_disconnected()
    else:
        logger.warning("TeleSaved is running in first-run setup mode. Telegram sync is disabled until config.json is created and the service is restarted.")
        while True:
            await asyncio.sleep(3600)
async def start_client():
    global client

    if not TELEGRAM_CONFIGURED or client is None:
        await main()
        return

    await client.connect()

    if not await client.is_user_authorized():
        logger.warning(
            "Telegram credentials are configured, but the saved Telegram session is not authorized. "
            "TeleSaved will continue in web mode without Telegram synchronization. "
            "Run 'sudo telesaved-telegram-login' and then restart the service."
        )
        try:
            await client.disconnect()
        except Exception:
            logger.exception("Could not disconnect the unauthorized Telegram client")
        client = None
        await main()
        return

    await main()
if __name__ == "__main__":
    try:
        if client is not None:
            client.loop.run_until_complete(start_client())
        else:
            asyncio.run(start_client())
    except Exception:
        logger.exception("Fatal application error")
        raise
