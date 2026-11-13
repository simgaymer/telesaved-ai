"""
telesaved/sources.py — Video source helpers and download/ingest (AD-6, Tier 2).

Extracted from app.py.  All functions receive their dependencies via _bind(cfg)
at startup.  The Telethon client is injected later via _bind_client(client)
because it is created after _bind() runs.  Both calls are idempotent.

Async functions are allowed here (unlike pipeline.py).
"""
import os
import asyncio
import hashlib
import logging
import re
from datetime import datetime

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level globals owned by this module.
# app.py aliases these after _bind() so external callsites work unchanged.
# ---------------------------------------------------------------------------
sem = asyncio.Semaphore(2)

_cfg: dict = {}


def _bind(cfg: dict) -> None:
    """Inject all dependencies (except client) at startup."""
    _cfg.clear()
    _cfg.update(cfg)


def _bind_client(client) -> None:
    """Inject the Telethon TelegramClient after it has been created in MAIN."""
    _cfg["client"] = client


# ---------------------------------------------------------------------------
# Helpers used internally
# ---------------------------------------------------------------------------

def _client():
    return _cfg.get("client")


# ================= VIDEO SOURCE HELPERS =================

def message_unique_id(message):
    utils = _cfg["utils"]
    peer = getattr(message, "peer_id", None)
    try:
        peer_key = str(utils.get_peer_id(peer)) if peer is not None else "unknown"
    except Exception:
        peer_key = str(getattr(message, "chat_id", None) or "unknown")
    return f"{peer_key}:{message.id}"


def is_message_already_seen(message):
    state_ids = _cfg["state_ids"]
    return message.id in state_ids or message_unique_id(message) in state_ids


def mark_message_seen(message):
    state_ids = _cfg["state_ids"]
    state_ids.add(message_unique_id(message))


async def resolve_video_source(ref):
    client = _client()
    ref = str(ref or "").strip()
    if not ref or ref.lower() in {"me", "saved", "saved messages", "self"}:
        return await client.get_entity("me")
    try:
        if re.fullmatch(r"-?\d+", ref):
            return await client.get_entity(int(ref))
    except Exception:
        pass
    return await client.get_entity(ref)


async def list_dialog_sources(limit=250):
    client = _client()
    utils = _cfg["utils"]
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
    utils = _cfg["utils"]
    enabled_video_sources = _cfg["enabled_video_sources"]
    peer_ids = set()
    for ref in enabled_video_sources():
        try:
            entity = await resolve_video_source(ref)
            peer_ids.add(str(utils.get_peer_id(entity)))
        except Exception as e:
            logger.warning("Could not resolve configured source %s: %s", ref, e)
    return peer_ids


async def event_source_is_enabled(event):
    utils = _cfg["utils"]
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
    utils = _cfg["utils"]
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
    utils = _cfg["utils"]
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
    telegram_message_media_kind = _cfg["telegram_message_media_kind"]
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
    safe_abs_path = _cfg["safe_abs_path"]
    ensure_directory = _cfg["ensure_directory"]
    save_json = _cfg["save_json"]
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
    get_source_profile = _cfg["get_source_profile"]
    source_display_name = _cfg["source_display_name"]
    APP_VERSION = _cfg["APP_VERSION"]
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
    tags = _cfg["tags"]
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
    metadata_store = _cfg["metadata_store"]
    APP_VERSION = _cfg["APP_VERSION"]
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
    tags = _cfg["tags"]
    metadata_store = _cfg["metadata_store"]
    get_source_profiles = _cfg["get_source_profiles"]
    source_display_name = _cfg["source_display_name"]
    APP_VERSION = _cfg["APP_VERSION"]
    TELEGRAM_METADATA_PATH = _cfg["TELEGRAM_METADATA_PATH"]
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
    tags = _cfg["tags"]
    metadata_store = _cfg["metadata_store"]
    metadata_source_label = _cfg["metadata_source_label"]
    APP_VERSION = _cfg["APP_VERSION"]
    TELEGRAM_METADATA_PATH = _cfg["TELEGRAM_METADATA_PATH"]
    pct = _cfg["pct"]
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
    client = _client()
    get_source_profile = _cfg["get_source_profile"]
    source_allows_message = _cfg["source_allows_message"]
    parse_source_limit = _cfg["parse_source_limit"]
    message_matches_source_media_filter = _cfg["message_matches_source_media_filter"]
    wait_job_if_paused = _cfg["wait_job_if_paused"]
    job_cancel_requested = _cfg["job_cancel_requested"]
    update_job = _cfg["update_job"]
    finish_job = _cfg["finish_job"]
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
    client = _client()
    metadata_store = _cfg["metadata_store"]
    get_source_profile = _cfg["get_source_profile"]
    save_state = _cfg["save_state"]
    wait_job_if_paused = _cfg["wait_job_if_paused"]
    job_cancel_requested = _cfg["job_cancel_requested"]
    update_job = _cfg["update_job"]
    finish_job = _cfg["finish_job"]
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

async def process_message(message, source_ref="me", source_title="Saved Messages"):
    state_ids = _cfg["state_ids"]
    state_hashes = _cfg["state_hashes"]
    tags = _cfg["tags"]
    scores_store = _cfg["scores_store"]
    embeddings_store = _cfg["embeddings_store"]
    metadata_store = _cfg["metadata_store"]
    get_source_profile = _cfg["get_source_profile"]
    source_allows_message = _cfg["source_allows_message"]
    source_display_name = _cfg["source_display_name"]
    get_download_directory = _cfg["get_download_directory"]
    safe_source_subdir = _cfg["safe_source_subdir"]
    ensure_directory = _cfg["ensure_directory"]
    download_control = _cfg["download_control"]
    refresh_storage_download_mode = _cfg["refresh_storage_download_mode"]
    create_job = _cfg["create_job"]
    finish_job = _cfg["finish_job"]
    update_job = _cfg["update_job"]
    job_cancel_requested = _cfg["job_cancel_requested"]
    job_pause_requested = _cfg["job_pause_requested"]
    set_download_status = _cfg["set_download_status"]
    clear_download_status = _cfg["clear_download_status"]
    record_download_history = _cfg["record_download_history"]
    file_hash = _cfg["file_hash"]
    safe_rel_path = _cfg["safe_rel_path"]
    fused_tag = _cfg["fused_tag"]
    get_video_duration_seconds = _cfg["get_video_duration_seconds"]
    build_faiss_index = _cfg["build_faiss_index"]
    _persist_faiss_index = _cfg["_persist_faiss_index"]
    apply_auto_rules_to_path = _cfg["apply_auto_rules_to_path"]
    format_bytes = _cfg["format_bytes"]
    pct = _cfg["pct"]
    save_state = _cfg["save_state"]

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
            fi, fk = build_faiss_index()
            _persist_faiss_index(fi, fk)
            status["status"] = "complete"
            status["path"] = rel
            status["note"] = "Indexed successfully"
            status["updated_at"] = datetime.now().isoformat(timespec="seconds")
            finish_job(job_id, "complete", "Indexed successfully")
            return fi, fk
        except Exception as e:
            status["status"] = "failed"
            status["note"] = str(e)
            status["updated_at"] = datetime.now().isoformat(timespec="seconds")
            logger.exception("Download/processing failed for message %s", message.id)
            finish_job(job_id, "failed", str(e))
        finally:
            record_download_history(dict(status))
            clear_download_status(message.id)
