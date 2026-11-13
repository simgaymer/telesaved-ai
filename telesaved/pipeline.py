# telesaved/pipeline.py — ML/AI pipeline: models, tagging, FAISS, search
#
# Architectural decisions honoured:
#   AD-4: All AI/ML pipeline logic lives here; app.py holds no ML imports.
#   AD-5: This module is synchronous only; no async code.
#   AD-6: No import of app.py; all config + helpers injected via _bind().
#
# Usage pattern
# ─────────────
#   import telesaved.pipeline as _pipeline
#   _pipeline._bind({
#       "FRAME_PATH": ..., "THUMB_PATH": ..., "PREVIEW_PATH": ...,
#       "SCENE_PATH": ..., "BASE_PATH": ...,
#       "FAISS_INDEX_FILE": ..., "FAISS_KEYS_FILE": ...,
#       "APP_VERSION": ...,
#       "PROMPTS": ..., "TAG_WEIGHTS": ..., "TAG_THRESHOLDS": ...,
#       "DEFAULT_NUDENET_THRESHOLD": ...,
#       "embeddings_store": ..., "scores_store": ..., "tags": ..., "metadata_store": ...,
#       "save_state": ..., "save_ai_signals": ..., "write_ai_tag_labels": ...,
#       "compute_feedback_bias": ..., "get_video_duration_seconds": ...,
#       "normalize_score": ..., "get_effective_tags": ..., "get_manual_override": ...,
#       "candidate_storage_roots": ..., "safe_abs_path": ...,
#       "get_download_directory": ..., "format_duration": ..., "now_stamp": ...,
#       "ensure_directory": ...,
#       "delete_scene_thumbnails_for_file": ..., "scene_url_for_file": ...,
#       "thumb_abs_path_for_file": ..., "preview_abs_path_for_file": ...,
#       "scene_abs_path_for_file": ...,
#       "thumb_rel_path_for_file": ...,
#       "settings_data": ...,
#   })
#   # After _bind(), the FAISS globals are initialised:
#   faiss_index = _pipeline.faiss_index
#   faiss_keys  = _pipeline.faiss_keys
#
# app.py creates module-level aliases and forwarding defs so all existing
# call sites continue to work without modification.

import json
import logging
import os
import re
import shutil
import subprocess

import clip
import faiss
import numpy as np
import torch
from PIL import Image
from ultralytics import YOLO

logger = logging.getLogger("telesaved")

# ── Module-level config (populated by _bind) ──────────────────────────────────
_cfg: dict = {}

# ── ML model globals (set during _bind) ───────────────────────────────────────
device = "cpu"
clip_model = None
preprocess = None
yolo_model = None
text_tokens: dict = {}

# ── FAISS index globals (set during _bind, copied into app.py after) ─────────
faiss_index = None
faiss_keys: list = []

# ── NudeNet lazy-load state ───────────────────────────────────────────────────
_nudenet_detector = None
_nudenet_load_attempted = False


# ── _bind ─────────────────────────────────────────────────────────────────────

def _bind(cfg: dict):
    """Initialise the pipeline module.

    Loads CLIP, YOLO, tokenises prompts, and initialises the FAISS index.
    After this call, pipeline.faiss_index and pipeline.faiss_keys hold the
    initial index; app.py copies them into its own scope immediately after.
    """
    global device, clip_model, preprocess, yolo_model, text_tokens
    global faiss_index, faiss_keys

    _cfg.clear()
    _cfg.update(cfg)

    # ── MODEL SETUP ──────────────────────────────────────────────────────────
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
        for tag, prompts in _cfg["PROMPTS"].items()
    }

    # ── FAISS STARTUP INIT ───────────────────────────────────────────────────
    _n_embeddings = len(_cfg["embeddings_store"])
    if _n_embeddings > 0:
        _loaded_index, _loaded_keys = _load_faiss_index()
        if _loaded_index is not None:
            faiss_index, faiss_keys = _loaded_index, _loaded_keys
        else:
            logger.info("Building FAISS index from %d stored embeddings…", _n_embeddings)
            faiss_index, faiss_keys = build_faiss_index()
            _persist_faiss_index(faiss_index, faiss_keys)
    else:
        faiss_index, faiss_keys = None, []


# ── NudeNet ───────────────────────────────────────────────────────────────────

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
        threshold = float(_cfg["settings_data"].get("nudenet_threshold", _cfg["DEFAULT_NUDENET_THRESHOLD"]))
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


# ── Frame extraction ──────────────────────────────────────────────────────────

def extract_frames(video_path):
    folder = os.path.join(_cfg["FRAME_PATH"], os.path.basename(video_path))
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


# ── Preview generation ────────────────────────────────────────────────────────

def generate_thumbnail(frame_paths, rel_path):
    if not frame_paths:
        return None
    thumb_path = _cfg["thumb_abs_path_for_file"](rel_path)
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
    preview_path = _cfg["preview_abs_path_for_file"](rel_path)
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


# ── Scene / timeline detection ────────────────────────────────────────────────

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
    duration = _cfg["get_video_duration_seconds"](video_path)
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
    thumb_path = _cfg["scene_abs_path_for_file"](rel_path, scene_index)
    _cfg["ensure_directory"](os.path.dirname(thumb_path))
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
        logger.warning("Could not extract scene thumbnail for %s at %.2fs: %s", rel_path, float(timestamp or 0.0), e)
    return None


def build_scene_timeline(rel_path, force=False, threshold=0.35, max_scenes=32, min_gap=6.0):
    tags = _cfg["tags"]
    metadata_store = _cfg["metadata_store"]
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
        _cfg["delete_scene_thumbnails_for_file"](rel_path)
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
            "time_human": _cfg["format_duration"](timestamp),
            "end_time": float(next_time or 0.0),
            "end_time_human": _cfg["format_duration"](next_time),
            "duration": max(0.0, float(next_time or 0.0) - float(timestamp or 0.0)) if next_time else 0.0,
            "duration_human": _cfg["format_duration"](max(0.0, float(next_time or 0.0) - float(timestamp or 0.0))) if next_time else "—",
            "title": "Opening" if idx == 1 and float(timestamp or 0.0) <= 0.5 else f"Scene {idx}",
            "thumbnail_url": _cfg["scene_url_for_file"](rel_path, idx) if thumb else f"/thumbs/{_cfg['thumb_rel_path_for_file'](rel_path)}",
            "thumbnail_file": os.path.basename(thumb) if thumb else "",
            "source": "scene-detect" if len(scene_times) > 1 else "fallback",
        })
    timeline = {
        "generated_at": _cfg["now_stamp"](),
        "version": _cfg["APP_VERSION"],
        "threshold": float(threshold),
        "min_gap": float(min_gap),
        "max_scenes": int(max_scenes),
        "duration": float(duration or 0.0),
        "duration_human": _cfg["format_duration"](duration),
        "scene_count": len(scenes),
        "scenes": scenes,
    }
    meta["scene_timeline"] = timeline
    meta["scene_timeline_generated_at"] = timeline["generated_at"]
    _cfg["save_state"]()
    logger.info("Scene timeline generated for %s scenes=%d", rel_path, len(scenes))
    return timeline


def cached_scene_timeline(rel_path):
    metadata_store = _cfg["metadata_store"]
    meta = metadata_store.get(rel_path, {}) or {}
    timeline = meta.get("scene_timeline")
    if isinstance(timeline, dict):
        return timeline
    return {
        "generated_at": "",
        "version": _cfg["APP_VERSION"],
        "threshold": 0.35,
        "min_gap": 6.0,
        "max_scenes": 32,
        "duration": float(meta.get("duration") or 0.0),
        "duration_human": _cfg["format_duration"](meta.get("duration") or 0.0),
        "scene_count": 0,
        "scenes": [],
    }


# ── Embedding ─────────────────────────────────────────────────────────────────

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


# ── Tagging ───────────────────────────────────────────────────────────────────

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
            _cfg["save_ai_signals"](rel_path, ai_signals)
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
    PROMPTS = _cfg["PROMPTS"]
    TAG_WEIGHTS = _cfg["TAG_WEIGHTS"]
    TAG_THRESHOLDS = _cfg["TAG_THRESHOLDS"]
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
    feedback_bias = _cfg["compute_feedback_bias"]()
    weighted_scores = {}
    for tag in averaged_scores:
        weighted = averaged_scores[tag] * TAG_WEIGHTS.get(tag, 1.0)
        weighted += feedback_bias.get(tag, 0.0)
        weighted_scores[tag] = _cfg["normalize_score"](weighted)
    duration = _cfg["get_video_duration_seconds"](video_path)
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
            _cfg["write_ai_tag_labels"](rel_path, selected)
        except Exception as _lbl_err:
            logger.warning("Failed to write ai tag labels for %s: %s", rel_path, _lbl_err)
    return selected, weighted_scores, emb.tolist()


# ── File lookup ───────────────────────────────────────────────────────────────

def find_existing_file(rel_path):
    rel_path = str(rel_path or "").strip()
    if not rel_path:
        return None
    # Absolute paths may appear in older state files or during migrations.
    if os.path.isabs(rel_path) and os.path.exists(rel_path):
        return rel_path
    for root in _cfg["candidate_storage_roots"]():
        try:
            candidate = _cfg["safe_abs_path"](rel_path, root)
            if os.path.exists(candidate):
                return candidate
        except Exception:
            pass
    return None


# ── Reprocess ─────────────────────────────────────────────────────────────────

def reprocess_file(rel_path):
    """Re-tag a single file and return the rebuilt FAISS index.

    Returns (faiss_index, faiss_keys) so the caller can update its own globals
    and persist the index at the right time (e.g. once after a bulk loop, not
    once per file).  Persistence is intentionally left to the caller.
    """
    embeddings_store = _cfg["embeddings_store"]
    scores_store = _cfg["scores_store"]
    tags = _cfg["tags"]
    abs_path = find_existing_file(rel_path)
    if not abs_path:
        raise FileNotFoundError(rel_path)
    logger.info("Manual reprocess started for %s", rel_path)
    t, s, emb = fused_tag(abs_path, rel_path=rel_path)
    tags[rel_path] = t
    scores_store[rel_path] = s
    embeddings_store[rel_path] = emb
    _cfg["save_state"]()
    new_index, new_keys = build_faiss_index()
    logger.info("Manual reprocess finished for %s", rel_path)
    return new_index, new_keys


# ── Migration ─────────────────────────────────────────────────────────────────

def migrate_library_files():
    tags = _cfg["tags"]
    source_root = os.path.abspath(_cfg["BASE_PATH"])
    target_root = os.path.abspath(_cfg["get_download_directory"]())
    if source_root == target_root:
        return {
            "moved": 0,
            "skipped": 0,
            "missing": 0,
            "failed": 0,
            "details": ["Source and destination are the same; nothing to migrate."]
        }
    _cfg["ensure_directory"](target_root)
    moved = 0
    skipped = 0
    missing = 0
    failed = 0
    details = []
    indexed_paths = sorted(tags.keys())
    for rel_path in indexed_paths:
        try:
            src = _cfg["safe_abs_path"](rel_path, source_root)
            dst = _cfg["safe_abs_path"](rel_path, target_root)
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


# ── FAISS ─────────────────────────────────────────────────────────────────────

def build_faiss_index():
    embeddings_store = _cfg["embeddings_store"]
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
    return index, valid_keys


def _persist_faiss_index(index, keys):
    """Write the FAISS index and its key list to disk as sidecar files.

    Called after every build_faiss_index() that produces a non-empty result.
    Write failures are best-effort: a warning is logged but nothing is raised.
    When index is None (empty embeddings_store), any stale sidecar files are
    removed so a subsequent startup does not load an outdated cache.
    """
    FAISS_INDEX_FILE = _cfg["FAISS_INDEX_FILE"]
    FAISS_KEYS_FILE = _cfg["FAISS_KEYS_FILE"]
    if index is None or not keys:
        _invalidate_faiss_cache()
        return
    try:
        tmp_index = FAISS_INDEX_FILE + ".tmp"
        tmp_keys = FAISS_KEYS_FILE + ".tmp"
        faiss.write_index(index, tmp_index)
        with open(tmp_keys, "w", encoding="utf-8") as fh:
            json.dump(keys, fh)
        os.replace(tmp_index, FAISS_INDEX_FILE)
        os.replace(tmp_keys, FAISS_KEYS_FILE)
        logger.debug("FAISS index persisted to disk (%d vectors)", len(keys))
    except Exception:
        logger.warning("FAISS index could not be persisted to disk", exc_info=True)
        # Clean up any partial temp files.
        for tmp in (FAISS_INDEX_FILE + ".tmp", FAISS_KEYS_FILE + ".tmp"):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _load_faiss_index():
    """Attempt to load the FAISS index from the sidecar files on disk.

    Returns (index, keys) on success, or (None, None) if the files are absent,
    corrupt, or inconsistent with the current embeddings_store.
    A (None, None) return means the caller should fall back to build_faiss_index().
    """
    embeddings_store = _cfg["embeddings_store"]
    FAISS_INDEX_FILE = _cfg["FAISS_INDEX_FILE"]
    FAISS_KEYS_FILE = _cfg["FAISS_KEYS_FILE"]
    if not os.path.exists(FAISS_INDEX_FILE) or not os.path.exists(FAISS_KEYS_FILE):
        return None, None
    try:
        index = faiss.read_index(FAISS_INDEX_FILE)
        with open(FAISS_KEYS_FILE, "r", encoding="utf-8") as fh:
            keys = json.load(fh)
        if not isinstance(keys, list):
            raise ValueError("keys file is not a list")
        if index.ntotal != len(keys):
            raise ValueError(
                f"FAISS index has {index.ntotal} vectors but keys file has {len(keys)} entries"
            )
        # Every key must still exist in embeddings_store; if embeddings were
        # added or removed since the last persist, the cache is stale.
        missing = [k for k in keys if k not in embeddings_store]
        if missing:
            raise ValueError(
                f"{len(missing)} key(s) in FAISS cache not found in embeddings_store "
                f"(e.g. {missing[0]!r})"
            )
        logger.info("FAISS index loaded from disk (%d vectors)", len(keys))
        return index, keys
    except Exception as exc:
        logger.warning("FAISS disk cache could not be loaded (%s); rebuilding from embeddings", exc)
        _invalidate_faiss_cache()
        return None, None


def _invalidate_faiss_cache():
    """Delete the FAISS sidecar files so the next startup does a clean rebuild."""
    FAISS_INDEX_FILE = _cfg["FAISS_INDEX_FILE"]
    FAISS_KEYS_FILE = _cfg["FAISS_KEYS_FILE"]
    for path in (FAISS_INDEX_FILE, FAISS_KEYS_FILE):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except Exception:
            logger.warning("Could not remove FAISS cache file %s", path, exc_info=True)


# ── Search ────────────────────────────────────────────────────────────────────

def search(faiss_index, faiss_keys, query, filter_tag=None, min_score=0.0, top_k=250):
    """Search the FAISS index.

    Takes faiss_index and faiss_keys as explicit parameters because they are
    owned by app.py scope (updated by reprocess_file and ingest operations).
    """
    scores_store = _cfg["scores_store"]
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
        file_tags = _cfg["get_effective_tags"](path)
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
                "manual_override": _cfg["get_manual_override"](path) is not None,
            }
        )
    results.sort(key=lambda x: x["final_score"], reverse=True)
    return results[:top_k]
