"""telesaved/serializers.py — Item serialisation helpers.

Extracted from app.py (# ===== SERIALIZERS ====== section, lines 8408–8757).

Design rules (AD-7):
- All functions are pure: they receive explicit arguments and return plain
  dicts / lists. No global mutations, no I/O, no side effects.
- Reads from shared state (embeddings_store, tags, scores_store,
  metadata_store) are done via the callables passed in from app.py's
  module-level globals. This avoids a direct import of app.py while keeping
  the transition additive and non-breaking.

During the transition period functions here still call helpers that live in
app.py (find_existing_file, collect_file_metadata, get_effective_tags, etc.)
via the module-level references injected by _bind(). Once those helpers are
also extracted into telesaved.*, the injection layer goes away.
"""
from __future__ import annotations

import os
import logging
from typing import Any, Callable, Dict, List, Optional

import numpy as np

logger = logging.getLogger("telesaved")

# ---------------------------------------------------------------------------
# Dependency injection — populated by app.py calling serializers._bind(...)
# at startup.  Every function below uses these callables / dicts rather than
# importing directly from app.py.
# ---------------------------------------------------------------------------
_deps: Dict[str, Any] = {}


def _bind(
    *,
    embeddings_store: dict,
    tags: dict,
    scores_store: dict,
    metadata_store: dict,
    APP_VERSION: str,
    find_existing_file: Callable,
    collect_file_metadata: Callable,
    get_effective_tags: Callable,
    get_manual_override: Callable,
    get_ai_signals: Callable,
    get_audio_language: Callable,
    get_display_tags_for_signals: Callable,
    watch_state_for_path: Callable,
    personal_state_for_path: Callable,
    thumb_rel_path_for_file: Callable,
    preview_rel_path_for_file: Callable,
    frame_preview_urls_for_file: Callable,
    cached_scene_timeline: Callable,
    audio_payload_for_path: Callable,
    score_rows_fn: Callable,
    short_name: Callable,
    human_date_from_path: Callable,
    format_bytes: Callable,
    format_duration: Callable,
    now_stamp: Callable,
    metadata_source_label: Callable,
) -> None:
    """Bind shared state and helper callables from app.py.

    Called once during application startup, before any request is served.
    """
    _deps.update(locals())
    _deps.pop("__class__", None)


def _check_bound() -> None:
    """Raise RuntimeError if _bind() has not been called yet.

    Guards against importing a serializer function in tests or tools without
    going through app.py startup, which would produce a confusing KeyError.
    """
    if not _deps:
        raise RuntimeError(
            "telesaved.serializers has not been initialised — call _bind() first. "
            "If you are writing a test, call serializers._bind(**mock_deps) in setUp."
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _emb() -> dict:
    return _deps["embeddings_store"]

def _tags() -> dict:
    return _deps["tags"]

def _scores() -> dict:
    return _deps["scores_store"]

def _meta() -> dict:
    return _deps["metadata_store"]


# ---------------------------------------------------------------------------
# Public serialisers
# ---------------------------------------------------------------------------

def serialize_item(item: dict) -> dict:
    """Serialise a search-result item dict into a full API payload."""
    _check_bound()
    rel_path = item["path"]
    find = _deps["find_existing_file"]
    abs_path = find(rel_path)
    file_meta = _deps["collect_file_metadata"](rel_path, abs_path=abs_path, ensure_duration=True)
    ai_signals = _deps["get_ai_signals"](rel_path)
    audio_language = _deps["get_audio_language"](rel_path)
    return {
        "path": rel_path,
        "directory": os.path.dirname(rel_path),
        "filename": _deps["short_name"](rel_path),
        "date": _deps["human_date_from_path"](rel_path),
        "tags": item.get("tags", []),
        "score_map": item.get("score_map", {}),
        "final_score": item.get("final_score"),
        "semantic_score": item.get("semantic_score"),
        "manual_override": item.get("manual_override", False),
        "exists": file_meta["exists"],
        "size": file_meta["size"],
        "size_human": file_meta["size_human"],
        "modified": file_meta["modified"],
        "duration": file_meta["duration"],
        "duration_human": file_meta["duration_human"],
        "source": file_meta["source"],
        "source_ref": file_meta["source_ref"],
        **_deps["watch_state_for_path"](rel_path),
        **_deps["personal_state_for_path"](rel_path),
        "thumb_url": f"/thumbs/{_deps['thumb_rel_path_for_file'](rel_path)}",
        "preview_url": f"/previews/{_deps['preview_rel_path_for_file'](rel_path)}",
        "file_url": f"/files/{rel_path}",
        "ai_signals": ai_signals,
        "audio_language": audio_language,
        "display_tags": _deps["get_display_tags_for_signals"](ai_signals),
    }


def score_rows(score_map: dict) -> list:
    """Convert a tag→score dict to a sorted list of display rows."""
    _check_bound()
    rows = []
    if not isinstance(score_map, dict):
        return rows
    for tag, value in score_map.items():
        try:
            numeric = float(value)
        except Exception:
            numeric = 0.0
        rows.append({
            "tag": tag,
            "score": numeric,
            "score_percent": round(max(0.0, min(numeric, 1.0)) * 100.0, 2),
        })
    rows.sort(key=lambda row: row["score"], reverse=True)
    return rows


def similar_video_items(rel_path: str, limit: int = 8) -> list:
    """Return visually/semantically similar videos using stored CLIP embeddings."""
    _check_bound()
    embeddings_store = _emb()
    tags = _tags()
    scores_store = _scores()

    base_vec = embeddings_store.get(rel_path)
    if not isinstance(base_vec, list) or not base_vec:
        return []
    try:
        base = np.array(base_vec, dtype="float32")
        base_norm = float(np.linalg.norm(base))
        if base_norm <= 0:
            return []
        base = base / base_norm
    except Exception:
        return []
    candidates = []
    for other_path, other_vec in embeddings_store.items():
        if other_path == rel_path:
            continue
        if other_path not in tags:
            continue
        if not isinstance(other_vec, list) or not other_vec:
            continue
        try:
            vec = np.array(other_vec, dtype="float32")
            norm = float(np.linalg.norm(vec))
            if norm <= 0:
                continue
            similarity = float(np.dot(base, vec / norm))
            candidates.append((similarity, other_path))
        except Exception:
            continue
    candidates.sort(key=lambda item: item[0], reverse=True)
    results = []
    get_effective_tags = _deps["get_effective_tags"]
    get_manual_override = _deps["get_manual_override"]
    for similarity, path in candidates[: max(0, int(limit))]:
        payload = serialize_item({
            "path": path,
            "tags": get_effective_tags(path),
            "score_map": scores_store.get(path, {}),
            "final_score": similarity,
            "semantic_score": similarity,
            "manual_override": get_manual_override(path) is not None,
        })
        payload["similarity"] = similarity
        results.append(payload)
    return results


def tag_summary_for_paths(paths: list, max_items: int = 6) -> list:
    _check_bound()
    get_effective_tags = _deps["get_effective_tags"]
    counts: dict = {}
    for path in paths:
        for tag in get_effective_tags(path):
            counts[tag] = counts.get(tag, 0) + 1
    rows = sorted(counts.items(), key=lambda item: item[1], reverse=True)
    return [{"tag": tag, "count": count} for tag, count in rows[:max_items]]


def source_summary_for_paths(paths: list, max_items: int = 6) -> list:
    _check_bound()
    metadata_store = _meta()
    metadata_source_label = _deps["metadata_source_label"]
    counts: dict = {}
    for path in paths:
        source = metadata_source_label(metadata_store.get(path, {}) or {})
        counts[source] = counts.get(source, 0) + 1
    rows = sorted(counts.items(), key=lambda item: item[1], reverse=True)
    return [{"source": source, "count": count} for source, count in rows[:max_items]]


def cluster_label_from_paths(paths: list) -> str:
    _check_bound()
    tags_part = tag_summary_for_paths(paths, max_items=3)
    source_part = source_summary_for_paths(paths, max_items=1)
    label_bits = []
    if tags_part:
        label_bits.append(" / ".join(row["tag"] for row in tags_part[:2]))
    if source_part:
        label_bits.append(source_part[0]["source"])
    if not label_bits:
        return "Visual Cluster"
    return " • ".join(label_bits)


def normalized_embedding_map(limit: int = 1000) -> list:
    """Return normalised (path, vector) pairs for indexed files that have embeddings."""
    _check_bound()
    embeddings_store = _emb()
    tags = _tags()
    rows = []
    for path, vec in embeddings_store.items():
        if path not in tags:
            continue
        if not isinstance(vec, list) or not vec:
            continue
        try:
            arr = np.array(vec, dtype="float32")
            norm = float(np.linalg.norm(arr))
            if norm <= 0:
                continue
            rows.append((path, arr / norm))
        except Exception:
            continue
        if limit and len(rows) >= int(limit):
            break
    return rows


def cosine_similarity(a: "np.ndarray", b: "np.ndarray") -> float:
    try:
        return float(np.dot(a, b))
    except Exception:
        return 0.0


def build_visual_cluster_report(
    cluster_count: int = 8,
    limit: int = 800,
    representative_count: int = 8,
    iterations: int = 10,
) -> dict:
    """Build a lightweight visual cluster report from stored CLIP embeddings.

    Uses deterministic k-means over normalised CLIP vectors — no scikit-learn
    dependency.
    """
    _check_bound()
    embeddings_store = _emb()
    tags = _tags()
    scores_store = _scores()
    APP_VERSION = _deps["APP_VERSION"]
    get_effective_tags = _deps["get_effective_tags"]
    get_manual_override = _deps["get_manual_override"]
    collect_file_metadata = _deps["collect_file_metadata"]
    format_duration = _deps["format_duration"]
    format_bytes = _deps["format_bytes"]
    now_stamp = _deps["now_stamp"]

    try:
        cluster_count = max(2, min(int(cluster_count), 24))
    except Exception:
        cluster_count = 8
    try:
        limit = max(25, min(int(limit), 5000))
    except Exception:
        limit = 800
    try:
        representative_count = max(3, min(int(representative_count), 24))
    except Exception:
        representative_count = 8
    try:
        iterations = max(2, min(int(iterations), 25))
    except Exception:
        iterations = 10

    rows = normalized_embedding_map(limit=limit)
    total_embeddings = len([
        1 for path, vec in embeddings_store.items()
        if path in tags and isinstance(vec, list) and vec
    ])
    if len(rows) < 2:
        return {
            "ok": True,
            "version": APP_VERSION,
            "clusters": [],
            "summary": {
                "indexed_embeddings": total_embeddings,
                "used_embeddings": len(rows),
                "cluster_count": 0,
                "limit": limit,
                "message": "Not enough stored embeddings are available for clustering.",
            },
        }

    paths = [row[0] for row in rows]
    matrix = np.vstack([row[1] for row in rows]).astype("float32")
    n, dim = matrix.shape
    k = min(cluster_count, n)

    seed_positions = np.linspace(0, n - 1, k, dtype=int)
    centroids = matrix[seed_positions].copy()
    assignments = np.zeros(n, dtype=int)

    for _ in range(iterations):
        sims = matrix @ centroids.T
        new_assignments = np.argmax(sims, axis=1)
        if np.array_equal(assignments, new_assignments):
            assignments = new_assignments
            break
        assignments = new_assignments
        for cluster_idx in range(k):
            members = matrix[assignments == cluster_idx]
            if len(members) == 0:
                continue
            centroid = members.mean(axis=0)
            norm = np.linalg.norm(centroid)
            if norm > 0:
                centroids[cluster_idx] = centroid / norm

    clusters = []
    for cluster_idx in range(k):
        member_indexes = np.where(assignments == cluster_idx)[0]
        if len(member_indexes) == 0:
            continue
        centroid = centroids[cluster_idx]
        scored_members = []
        total_size = 0
        duration_total = 0.0
        duration_count = 0
        existing_count = 0
        missing_count = 0
        member_paths = [paths[int(i)] for i in member_indexes]

        for i in member_indexes:
            path = paths[int(i)]
            sim = cosine_similarity(matrix[int(i)], centroid)
            scored_members.append((sim, path))
            file_meta = collect_file_metadata(path, ensure_duration=False)
            if file_meta.get("exists"):
                existing_count += 1
            else:
                missing_count += 1
            try:
                total_size += int(file_meta.get("size") or 0)
            except Exception:
                pass
            try:
                dur = float(file_meta.get("duration") or 0.0)
                if dur > 0:
                    duration_total += dur
                    duration_count += 1
            except Exception:
                pass

        scored_members.sort(key=lambda item: item[0], reverse=True)
        representatives = []
        for sim, path in scored_members[:representative_count]:
            payload = serialize_item({
                "path": path,
                "tags": get_effective_tags(path),
                "score_map": scores_store.get(path, {}),
                "final_score": sim,
                "semantic_score": sim,
                "manual_override": get_manual_override(path) is not None,
            })
            payload["cluster_similarity"] = sim
            representatives.append(payload)

        avg_duration = duration_total / duration_count if duration_count else 0.0
        clusters.append({
            "id": f"cluster-{cluster_idx + 1}",
            "title": cluster_label_from_paths(member_paths),
            "count": int(len(member_paths)),
            "representatives": representatives,
            "tags": tag_summary_for_paths(member_paths, max_items=8),
            "sources": source_summary_for_paths(member_paths, max_items=8),
            "average_duration": avg_duration,
            "average_duration_human": format_duration(avg_duration),
            "total_size": int(total_size),
            "total_size_human": format_bytes(total_size),
            "existing_count": existing_count,
            "missing_count": missing_count,
            "cohesion": round(
                float(np.mean([s for s, _ in scored_members])) if scored_members else 0.0,
                4,
            ),
        })

    clusters.sort(key=lambda row: row["count"], reverse=True)
    return {
        "ok": True,
        "version": APP_VERSION,
        "clusters": clusters,
        "summary": {
            "indexed_embeddings": total_embeddings,
            "used_embeddings": n,
            "cluster_count": len(clusters),
            "requested_clusters": cluster_count,
            "limit": limit,
            "representative_count": representative_count,
            "iterations": iterations,
            "generated_at": now_stamp(),
        },
    }


def build_item_detail_payload(rel_path: str, include_similar: bool = True) -> dict:
    """Build the full item detail payload for a given library path."""
    _check_bound()
    tags = _tags()
    scores_store = _scores()
    metadata_store = _meta()

    if rel_path not in tags:
        raise FileNotFoundError("File not found in index")

    find_existing_file = _deps["find_existing_file"]
    collect_file_metadata = _deps["collect_file_metadata"]
    get_effective_tags = _deps["get_effective_tags"]
    get_manual_override = _deps["get_manual_override"]
    watch_state_for_path = _deps["watch_state_for_path"]
    personal_state_for_path = _deps["personal_state_for_path"]
    frame_preview_urls_for_file = _deps["frame_preview_urls_for_file"]
    cached_scene_timeline = _deps["cached_scene_timeline"]
    audio_payload_for_path = _deps["audio_payload_for_path"]
    score_rows_fn = _deps["score_rows_fn"]
    short_name = _deps["short_name"]

    abs_path = find_existing_file(rel_path)
    file_meta = collect_file_metadata(rel_path, abs_path=abs_path, ensure_duration=True)
    meta = metadata_store.setdefault(rel_path, {})
    effective_tags = get_effective_tags(rel_path)
    manual_override = get_manual_override(rel_path)
    score_map = scores_store.get(rel_path, {})

    item = serialize_item({
        "path": rel_path,
        "tags": effective_tags,
        "score_map": score_map,
        "manual_override": manual_override is not None,
    })
    item.update({
        "ok": True,
        "effective_tags": effective_tags,
        "stored_tags": tags.get(rel_path, []),
        "manual_override_detail": manual_override,
        "score_rows": score_rows_fn(score_map),
        **watch_state_for_path(rel_path),
        **personal_state_for_path(rel_path),
        "metadata": meta,
        "notes": str(meta.get("notes") or ""),
        "absolute_path": abs_path or "",
        "exists": file_meta.get("exists", False),
        "size": file_meta.get("size", 0),
        "size_human": file_meta.get("size_human", "0 B"),
        "modified": file_meta.get("modified", ""),
        "duration": file_meta.get("duration", 0.0),
        "duration_human": file_meta.get("duration_human", "—"),
        "source": file_meta.get("source", "Saved Messages"),
        "source_ref": file_meta.get("source_ref", ""),
        "frame_urls": frame_preview_urls_for_file(rel_path),
        "scene_timeline": cached_scene_timeline(rel_path),
        "audio": audio_payload_for_path(rel_path),
        "telegram": meta.get("telegram") if isinstance(meta.get("telegram"), dict) else {},
        "telegram_link": meta.get("telegram_link", ""),
        "telegram_metadata_file": meta.get("telegram_metadata_file", ""),
        "telegram_metadata_captured_at": meta.get("telegram_metadata_captured_at", ""),
        "telegram_sender_name": meta.get("telegram_sender_name", ""),
        "telegram_sender_username": meta.get("telegram_sender_username", ""),
        "telegram_sender_id": meta.get("telegram_sender_id", ""),
        "telegram_caption": meta.get("telegram_caption", ""),
        "telegram_forward_from": meta.get("telegram_forward_from", ""),
        "telegram_reply_to_msg_id": meta.get("telegram_reply_to_msg_id", ""),
        "telegram_grouped_id": meta.get("telegram_grouped_id", ""),
        "telegram_views": meta.get("telegram_views", 0),
        "telegram_forwards": meta.get("telegram_forwards", 0),
        "telegram_reaction_count": meta.get("telegram_reaction_count", 0),
        "message_id": meta.get("message_id", ""),
        "message_key": meta.get("message_key", ""),
        "message_date": meta.get("message_date", ""),
        "downloaded_at": meta.get("downloaded_at", ""),
        "original_filename": meta.get("original_filename", short_name(rel_path)),
        "sha256": meta.get("sha256", ""),
        "renamed_at": meta.get("renamed_at", ""),
        "restored_at": meta.get("restored_at", ""),
    })
    item["similar"] = similar_video_items(rel_path, limit=8) if include_similar else []
    return item
