# telesaved/state.py — SQLite state backend and shared in-memory library index
#
# Architectural decisions honoured:
#   AD-1: This module owns and exclusively controls the six in-memory state dicts/sets.
#   AD-3: sqlite_lock and all sqlite3 calls live here; no other module touches SQLite.
#   AD-6: No import of app.py; config values are injected via _bind().
#
# Usage pattern
# ─────────────
#   import telesaved.state as _state
#   _state._bind({
#       "DB_FILE": ..., "STATE_FILE": ..., "BASE_PATH": ...,
#       "load_json": ..., "save_json": ..., "now_stamp": ...,
#       "format_bytes": ..., "ensure_directory": ...,
#   })
#   # After _bind(), the six globals are populated and available as:
#   _state.tags, _state.scores_store, _state.embeddings_store,
#   _state.metadata_store, _state.state_ids, _state.state_hashes
#
# app.py creates module-level aliases pointing at these objects so all
# 390+ existing call sites continue to work without modification.

import json
import logging
import os
import sqlite3
from threading import Lock

logger = logging.getLogger("telesaved")

# ── Module-level config (populated by _bind) ──────────────────────────────────
_cfg: dict = {}

# ── Shared in-memory state (the six canonical globals) ───────────────────────
# All mutations go through the functions in this module (AD-1).
# These objects are populated in-place by _bind() / _rebind(), so alias
# references held by app.py always point to the same dict/set objects.
tags: dict = {}
scores_store: dict = {}
embeddings_store: dict = {}
metadata_store: dict = {}
state_ids: set = set()
state_hashes: set = set()

# ── SQLite internals ──────────────────────────────────────────────────────────
SQLITE_SCHEMA_VERSION = 3
sqlite_lock = Lock()


def sqlite_connect():
    conn = sqlite3.connect(_cfg["DB_FILE"], timeout=30)
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
    _cfg["ensure_directory"](_cfg["BASE_PATH"])
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
            # audit_log — immutable record of significant admin actions
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_log (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor      TEXT NOT NULL,
                    action     TEXT NOT NULL,
                    detail     TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_log_created_at ON audit_log(created_at)"
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
    updated_at = _cfg["now_stamp"]()
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


def save_state():
    """Persist the current in-memory state to SQLite and write the legacy JSON snapshot."""
    persist_state_to_sqlite(state_ids, state_hashes, tags, scores_store, embeddings_store, metadata_store)
    write_legacy_state_snapshot(state_ids, state_hashes, tags, scores_store, embeddings_store, metadata_store)


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
                (str(rel_path), str(tag), label_source, _cfg["now_stamp"]()),
            )
            conn.commit()


def write_ai_tag_labels(rel_path, tag_list):
    """Write ai_inferred label rows for a list of tags assigned by the AI pipeline."""
    ts = _cfg["now_stamp"]()
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


def migrate_legacy_tag_labels(rel_path, label_source="ai_inferred", tag_map=None):
    """Write label rows for all tags on a video that lack one. Used by the migration tool.
    tag_map defaults to the module-level tags dict (AD-1 compliant when called from within
    this module); callers may pass an explicit dict for testing or future state.py use.
    """
    if tag_map is None:
        tag_map = tags
    existing = get_tag_labels(rel_path)
    video_tags = tag_map.get(str(rel_path), [])
    new_rows = [
        (str(rel_path), str(t), label_source, _cfg["now_stamp"]())
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
    _cfg["save_json"](
        _cfg["STATE_FILE"],
        {
            "backend": "sqlite",
            "database_file": _cfg["DB_FILE"],
            "snapshot_created_at": _cfg["now_stamp"](),
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
        "database_file": _cfg["DB_FILE"],
        "exists": os.path.exists(_cfg["DB_FILE"]),
        "size": os.path.getsize(_cfg["DB_FILE"]) if os.path.exists(_cfg["DB_FILE"]) else 0,
        "size_human": _cfg["format_bytes"](os.path.getsize(_cfg["DB_FILE"])) if os.path.exists(_cfg["DB_FILE"]) else "0 B",
        "videos": 0,
        "message_ids": 0,
        "content_hashes": 0,
        "schema_version": SQLITE_SCHEMA_VERSION,
    }
    try:
        with sqlite_connect() as conn:
            info["videos"] = int(conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0] or 0)
            info["message_ids"] = int(conn.execute("SELECT COUNT(*) FROM message_ids").fetchone()[0] or 0)
            info["content_hashes"] = int(conn.execute("SELECT COUNT(*) FROM content_hashes").fetchone()[0] or 0)
            row = conn.execute("SELECT value FROM app_meta WHERE key='updated_at'").fetchone()
            info["updated_at"] = row["value"] if row else ""
    except Exception as e:
        info["error"] = str(e)
    return info


# ── Tag-suggestion helpers (AD-3: moves raw SQL out of route handlers) ────────

def suggest_tag(tag, suggested_by, video_path, now_stamp_fn=None):
    """Insert a pending tag suggestion. Returns (already_pending: bool)."""
    ts = (now_stamp_fn or _cfg["now_stamp"])()
    with sqlite_lock:
        with sqlite_connect() as conn:
            existing = conn.execute(
                "SELECT id FROM tag_suggestions WHERE LOWER(tag)=LOWER(?) AND status='pending'",
                (tag,),
            ).fetchone()
            if existing:
                return True  # already pending
            conn.execute(
                "INSERT INTO tag_suggestions (tag, suggested_by, video_path, created_at, status) VALUES (?,?,?,?,?)",
                (tag, suggested_by, video_path, ts, "pending"),
            )
            conn.commit()
    return False


def list_suggestions(status="pending"):
    """Return pending (or filtered) tag suggestions as a list of dicts."""
    with sqlite_connect() as conn:
        rows = conn.execute(
            "SELECT id, tag, suggested_by, video_path, created_at, status FROM tag_suggestions WHERE status=? ORDER BY id DESC",
            (status,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_suggestion(suggestion_id):
    """Fetch a single tag suggestion by id. Returns a dict or None."""
    with sqlite_connect() as conn:
        row = conn.execute(
            "SELECT * FROM tag_suggestions WHERE id=? AND status='pending'",
            (suggestion_id,),
        ).fetchone()
    return dict(row) if row else None


def set_suggestion_status(suggestion_id, status):
    """Update the status of a tag suggestion. Returns rowcount (0 if not found)."""
    with sqlite_lock:
        with sqlite_connect() as conn:
            result = conn.execute(
                "UPDATE tag_suggestions SET status=? WHERE id=? AND status='pending'",
                (status, suggestion_id),
            )
            conn.commit()
            return result.rowcount


def export_tag_labels(label_source_filter=None):
    """Return all video_tag_labels rows, optionally filtered by label_source."""
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
    return [dict(r) for r in rows]


# ── Audit log helpers ────────────────────────────────────────────────────────

def append_audit_log(actor, action, detail=""):
    """Insert one row into audit_log. Best-effort: logs a warning on failure, never raises.

    Args:
        actor:  username of the authenticated user, or "system" for background actions.
        action: short dot-namespaced identifier, e.g. "user.create", "backup.restore".
        detail: human-readable one-line summary (≤ 240 chars). No passwords or tokens.
    """
    try:
        ts = _cfg["now_stamp"]()
        detail_safe = str(detail or "")[:240]
        with sqlite_lock:
            with sqlite_connect() as conn:
                conn.execute(
                    "INSERT INTO audit_log (actor, action, detail, created_at) VALUES (?, ?, ?, ?)",
                    (str(actor or "system"), str(action or ""), detail_safe, ts),
                )
                conn.commit()
    except Exception:
        logger.warning("audit_log write failed for action=%s actor=%s", action, actor, exc_info=True)


def get_audit_log(limit=200):
    """Return audit log entries newest-first, up to `limit` rows (clamped 1–1000)."""
    limit = max(1, min(int(limit), 1000))
    try:
        with sqlite_connect() as conn:
            rows = conn.execute(
                "SELECT id, actor, action, detail, created_at FROM audit_log ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        logger.warning("audit_log read failed", exc_info=True)
        return []


# ── Startup / restore ─────────────────────────────────────────────────────────

def _bind(cfg: dict):
    """Initialise the state module.

    cfg must contain:
        DB_FILE, STATE_FILE, BASE_PATH,
        load_json, save_json, now_stamp, format_bytes, ensure_directory

    Idempotent: calling _bind() a second time (e.g. in tests) resets state.
    """
    _cfg.clear()
    _cfg.update(cfg)
    init_sqlite_db()
    legacy = _cfg["load_json"](
        _cfg["STATE_FILE"],
        {"ids": [], "hashes": [], "tags": {}, "scores": {}, "embeddings": {}, "metadata": {}},
    )
    if not sqlite_state_has_data() and legacy.get("tags"):
        logger.info("Migrating legacy JSON state to SQLite database: %s", _cfg["DB_FILE"])
        persist_state_to_sqlite(
            legacy.get("ids", []),
            legacy.get("hashes", []),
            legacy.get("tags", {}),
            legacy.get("scores", {}),
            legacy.get("embeddings", {}),
            legacy.get("metadata", {}),
        )
    loaded = load_state_from_sqlite(legacy)
    _populate_globals(loaded)


def _rebind(loaded_state: dict):
    """Reset in-memory state from a pre-loaded dict (used by backup restore).

    Does NOT re-run init_sqlite_db() or legacy migration — the caller is responsible
    for having loaded a fresh state from the restored SQLite database.
    """
    loaded_state.setdefault("ids", [])
    loaded_state.setdefault("hashes", [])
    loaded_state.setdefault("tags", {})
    loaded_state.setdefault("scores", {})
    loaded_state.setdefault("embeddings", {})
    loaded_state.setdefault("metadata", {})
    _populate_globals(loaded_state)


def _populate_globals(loaded: dict):
    """Populate the six module-level globals in-place from a loaded state dict.

    Uses .clear() + .update() so alias references held by app.py continue to
    point to the same dict/set objects (not replaced by a new object).
    """
    state_ids.clear()
    state_ids.update(loaded.get("ids", []))
    state_hashes.clear()
    state_hashes.update(loaded.get("hashes", []))
    tags.clear()
    tags.update(loaded.get("tags", {}))
    scores_store.clear()
    scores_store.update(loaded.get("scores", {}))
    embeddings_store.clear()
    embeddings_store.update(loaded.get("embeddings", {}))
    metadata_store.clear()
    metadata_store.update(loaded.get("metadata", {}))
