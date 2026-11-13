"""telesaved/audio.py — Audio Intelligence helpers.

Extracted from app.py (# ===== AUDIO INTELLIGENCE HELPERS ===== section,
lines 7456–8407, 952 lines).

Architecture rules that apply here (from ARCHITECTURE.md):
- AD-5: No aiohttp/HTTP types. All functions receive and return plain Python.
- AD-6: No import of app.py. State and helpers are injected via _bind().
- All reads of metadata_store / tags / scores_store go through _deps refs
  so the module holds no long-lived reference to the raw globals.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("telesaved")

# ---------------------------------------------------------------------------
# Dependency injection — populated by app.py calling audio._bind() at startup.
# ---------------------------------------------------------------------------
_deps: Dict[str, Any] = {}


def _bind(
    *,
    metadata_store: dict,
    tags: dict,
    scores_store: dict,
    APP_VERSION: str,
    APP_DIR: str,
    WHISPER_LOG_FILE: str,
    find_existing_file: Callable,
    get_video_duration_seconds: Callable,
    get_effective_tags: Callable,
    get_manual_override: Callable,
    save_state: Callable,
    save_audio_language: Callable,
    serialize_item: Callable,
    format_duration: Callable,
    now_stamp: Callable,
    short_name: Callable,
) -> None:
    """Bind shared state and helper callables from app.py. Called once at startup."""
    _deps.update(locals())


# ---------------------------------------------------------------------------
# Internal accessors
# ---------------------------------------------------------------------------

def _meta() -> dict:
    return _deps["metadata_store"]

def _tags() -> dict:
    return _deps["tags"]

def _scores() -> dict:
    return _deps["scores_store"]


# ---------------------------------------------------------------------------
# Default payload
# ---------------------------------------------------------------------------

def audio_default_payload(rel_path: str = "") -> dict:
    return {
        "path": rel_path,
        "status": "not_analyzed",
        "has_audio": False,
        "audio_streams": [],
        "codec": "",
        "channels": 0,
        "sample_rate": 0,
        "bit_rate": 0,
        "duration": 0.0,
        "duration_human": "—",
        "silence_seconds": 0.0,
        "silence_percent": 0.0,
        "active_audio_percent": 0.0,
        "segments": [],
        "transcript_text": "",
        "transcript_script": "",
        "transcript_segments": [],
        "transcript_speaker_mode": "",
        "transcript_status": "not_requested",
        "transcript_engine": "",
        "transcript_language": "",
        "transcript_detected_language": "",
        "transcript_requested_language": "",
        "transcript_quality_warnings": [],
        "transcript_filtered_segment_count": 0,
        "transcript_raw_segment_count": 0,
        "transcript_error": "",
        "analyzed_at": "",
        "error": "",
    }


def audio_payload_for_path(rel_path: str) -> dict:
    meta = _meta().get(rel_path, {}) or {}
    audio = meta.get("audio") or {}
    payload = audio_default_payload(rel_path)
    if isinstance(audio, dict):
        payload.update(audio)
    try:
        payload["duration_human"] = _deps["format_duration"](float(payload.get("duration") or 0.0))
    except Exception:
        payload["duration_human"] = "—"
    transcript_segments = payload.get("transcript_segments") or []
    if transcript_segments:
        enriched = enrich_transcript_segments(transcript_segments)
        payload["transcript_segments"] = enriched
        payload["transcript_speaker_mode"] = (
            "native"
            if any(str(seg.get("speaker_mode") or "") == "native" for seg in enriched)
            else "heuristic"
        )
        if not payload.get("transcript_script"):
            payload["transcript_script"] = transcript_script_from_segments(
                enriched, payload.get("transcript_text") or ""
            )
    elif payload.get("transcript_text") and not payload.get("transcript_script"):
        payload["transcript_script"] = str(payload.get("transcript_text") or "").strip()
    return payload


# ---------------------------------------------------------------------------
# Transcript helpers
# ---------------------------------------------------------------------------

def transcript_timestamp(seconds) -> str:
    """Format transcript timestamps as 0:00 or 1:02:03."""
    try:
        total = max(0, int(round(float(seconds or 0.0))))
    except Exception:
        total = 0
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def normalize_speaker_name(value, fallback: str = "Speaker 1") -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    lower = text.lower().replace("_", " ").replace("-", " ")
    if lower.startswith("speaker"):
        parts = lower.split()
        if len(parts) >= 2:
            token = parts[-1]
            if token.isdigit():
                return f"Speaker {int(token)}"
            if token in {"one", "1"}:
                return "Speaker 1"
            if token in {"two", "2"}:
                return "Speaker 2"
            if token in {"three", "3"}:
                return "Speaker 3"
            if token in {"four", "4"}:
                return "Speaker 4"
        return text[:32]
    if text.isdigit():
        return f"Speaker {int(text) + 1 if int(text) == 0 else int(text)}"
    return text[:32]


def transcript_speaker_count() -> int:
    try:
        count = int(os.environ.get("TELESAVED_TRANSCRIPT_SPEAKER_COUNT", "2") or "2")
    except Exception:
        count = 2
    return max(1, min(count, 6))


def enrich_transcript_segments(segments) -> list:
    """Add timestamp, duration, and best-effort speaker labels to Whisper segments."""
    if not isinstance(segments, list):
        return []
    enriched = []
    speaker_count = transcript_speaker_count()
    has_native_speakers = any(
        isinstance(seg, dict)
        and (seg.get("speaker") or seg.get("speaker_label") or seg.get("speaker_id"))
        for seg in segments
    )
    current_speaker_index = 0
    previous_end = None
    previous_text = ""
    previous_start = 0.0
    for idx, seg in enumerate(segments):
        if not isinstance(seg, dict):
            continue
        try:
            start = float(seg.get("start") or 0.0)
        except Exception:
            start = 0.0
        try:
            end = float(seg.get("end") or start)
        except Exception:
            end = start
        if end < start:
            end = start
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        native_speaker = (
            seg.get("speaker") or seg.get("speaker_label") or seg.get("speaker_id")
        )
        if has_native_speakers and native_speaker is not None:
            speaker = normalize_speaker_name(
                native_speaker, f"Speaker {current_speaker_index + 1}"
            )
            speaker_mode = "native"
        else:
            if idx > 0 and speaker_count > 1:
                gap = max(
                    0.0,
                    start - float(previous_end if previous_end is not None else start),
                )
                previous_duration = max(
                    0.0,
                    float(previous_end if previous_end is not None else start)
                    - previous_start,
                )
                previous_words = len(previous_text.split())
                current_words = len(text.split())
                turn_break = False
                if gap >= 1.8:
                    turn_break = True
                elif gap >= 1.0 and previous_text.endswith(("?", "!")):
                    turn_break = True
                elif previous_duration >= 12.0 and previous_words >= 18 and current_words <= 18:
                    turn_break = True
                elif previous_words >= 35 and text[:1].isupper():
                    turn_break = True
                if turn_break:
                    current_speaker_index = (current_speaker_index + 1) % speaker_count
            speaker = f"Speaker {current_speaker_index + 1}"
            speaker_mode = "heuristic"
        row = dict(seg)
        row.update({
            "start": round(start, 3),
            "end": round(end, 3),
            "start_human": transcript_timestamp(start),
            "end_human": transcript_timestamp(end),
            "timestamp": transcript_timestamp(start),
            "end_timestamp": transcript_timestamp(end),
            "duration": round(max(0.0, end - start), 3),
            "duration_human": transcript_timestamp(max(0.0, end - start)),
            "speaker": speaker,
            "speaker_mode": speaker_mode,
            "text": text,
        })
        enriched.append(row)
        previous_end = end
        previous_start = start
        previous_text = text
    return enriched


def transcript_script_from_segments(segments, fallback_text: str = "") -> str:
    if not isinstance(segments, list):
        segments = []
    lines = []
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        timestamp = seg.get("timestamp") or seg.get("start_human") or transcript_timestamp(
            seg.get("start") or 0
        )
        speaker = normalize_speaker_name(seg.get("speaker"), "Speaker 1")
        quote_text = text.replace('"', "\u201c")
        lines.append(f'{timestamp} {speaker}: "{quote_text}"')
    if lines:
        return "\n".join(lines)
    return str(fallback_text or "").strip()


# ---------------------------------------------------------------------------
# FFprobe / FFmpeg helpers
# ---------------------------------------------------------------------------

def ffprobe_audio_streams(abs_path: str) -> list:
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "a",
        "-show_entries", "stream=index,codec_name,channels,sample_rate,bit_rate,duration",
        "-of", "json",
        abs_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "ffprobe failed").strip())
    data = json.loads(result.stdout or "{}")
    streams = data.get("streams") or []
    clean = []
    for stream in streams:
        clean.append({
            "index": stream.get("index"),
            "codec": str(stream.get("codec_name") or ""),
            "channels": int(float(stream.get("channels") or 0)),
            "sample_rate": int(float(stream.get("sample_rate") or 0)),
            "bit_rate": int(float(stream.get("bit_rate") or 0)),
            "duration": float(stream.get("duration") or 0.0),
        })
    return clean


def ffmpeg_silence_detect(
    abs_path: str,
    duration: float = 0.0,
    noise_db: str = "-35dB",
    min_duration: float = 0.5,
) -> dict:
    cmd = [
        "ffmpeg", "-hide_banner", "-nostdin", "-i", abs_path,
        "-af", f"silencedetect=n={noise_db}:d={float(min_duration)}",
        "-f", "null", "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return {
            "segments": [],
            "silence_seconds": 0.0,
            "silence_percent": 0.0,
            "active_audio_percent": 0.0,
            "error": "Silence detection timed out",
        }
    format_duration = _deps["format_duration"]
    text = (result.stderr or "") + "\n" + (result.stdout or "")
    starts: list = []
    segments = []
    for line in text.splitlines():
        m_start = re.search(r"silence_start:\s*([0-9.]+)", line)
        if m_start:
            try:
                starts.append(float(m_start.group(1)))
            except Exception:
                pass
            continue
        m_end = re.search(
            r"silence_end:\s*([0-9.]+)\s*\|\s*silence_duration:\s*([0-9.]+)", line
        )
        if m_end:
            try:
                end = float(m_end.group(1))
                dur = float(m_end.group(2))
                start = starts.pop(0) if starts else max(0.0, end - dur)
                segments.append({
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "duration": round(dur, 3),
                    "start_human": format_duration(start),
                    "end_human": format_duration(end),
                    "duration_human": format_duration(dur),
                })
            except Exception:
                pass
    total_silence = sum(float(seg.get("duration") or 0.0) for seg in segments)
    try:
        duration = float(duration or 0.0)
    except Exception:
        duration = 0.0
    if duration > 0:
        total_silence = min(total_silence, duration)
        silence_percent = round((total_silence / duration) * 100.0, 2)
        active_percent = round(max(0.0, 100.0 - silence_percent), 2)
    else:
        silence_percent = 0.0
        active_percent = 0.0
    return {
        "segments": segments[:500],
        "silence_seconds": round(total_silence, 3),
        "silence_percent": silence_percent,
        "active_audio_percent": active_percent,
        "error": (
            ""
            if result.returncode == 0
            else (result.stderr or "ffmpeg silence detection returned non-zero status")[-500:]
        ),
    }


# ---------------------------------------------------------------------------
# Whisper helpers
# ---------------------------------------------------------------------------

def compact_log_text(value, limit: int = 6000) -> str:
    try:
        text = str(value or "")
    except Exception:
        text = ""
    text = text.replace("\r", "\n")
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    if len(text) > limit:
        return text[-limit:]
    return text


def append_whisper_log(
    event: str,
    detail: str = "",
    rel_path: str = "",
    abs_path: str = "",
    level: str = "INFO",
) -> None:
    """Write Whisper CLI diagnostics to whisper.log and the main TeleSaved log."""
    WHISPER_LOG_FILE = _deps["WHISPER_LOG_FILE"]
    timestamp = datetime.now().isoformat(timespec="seconds")
    header = f"{timestamp} [{level}] {event}"
    if rel_path:
        header += f" rel_path={rel_path}"
    if abs_path:
        header += f" abs_path={abs_path}"
    detail = compact_log_text(detail, 12000)
    block = header + ("\n" + detail if detail else "") + "\n"
    try:
        os.makedirs(os.path.dirname(WHISPER_LOG_FILE), exist_ok=True)
        with open(WHISPER_LOG_FILE, "a", encoding="utf-8", errors="replace") as f:
            f.write(block)
    except Exception as e:
        logger.warning("Unable to write Whisper diagnostic log: %s", e)
    main_line = f"Whisper CLI {event}"
    if rel_path:
        main_line += f" for {rel_path}"
    if detail:
        main_line += f": {compact_log_text(detail, 900).replace(chr(10), ' | ')}"
    lvl = str(level).upper()
    if lvl in {"ERROR", "FAILED"}:
        logger.error(main_line)
    elif lvl in {"WARNING", "WARN"}:
        logger.warning(main_line)
    else:
        logger.info(main_line)


_WHISPER_BINARY_CACHE: dict = {"path": None, "checked_at": 0.0, "details": []}


def whisper_candidate_paths() -> list:
    APP_DIR = _deps["APP_DIR"]
    candidates: list = []

    def add(path):
        if not path:
            return
        path = os.path.abspath(os.path.expanduser(str(path).strip()))
        if path and path not in candidates:
            candidates.append(path)

    add(os.environ.get("TELESAVED_WHISPER_BIN", ""))
    add(os.path.join(APP_DIR, "venv", "bin", "whisper"))
    add("/usr/local/bin/whisper")
    add("/usr/bin/whisper")
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        if folder:
            add(os.path.join(folder, "whisper"))
    return candidates


def check_whisper_binary(path: str) -> Tuple[bool, str]:
    if not path:
        return False, "empty path"
    if not os.path.exists(path):
        return False, "does not exist"
    if not os.access(path, os.X_OK):
        return False, "not executable"
    env = os.environ.copy()
    env.setdefault("CUDA_VISIBLE_DEVICES", "")
    try:
        result = subprocess.run(
            [path, "--help"],
            capture_output=True,
            text=True,
            timeout=20,
            env=env,
        )
    except Exception as e:
        return False, f"launch failed: {type(e).__name__}: {e}"
    combined = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
    if result.returncode == 0:
        return True, "ok"
    return False, f"returncode={result.returncode}; tail={compact_log_text(combined, 800)}"


def whisper_binary_path(force_refresh: bool = False) -> Optional[str]:
    now = time.time()
    if (
        not force_refresh
        and _WHISPER_BINARY_CACHE.get("path")
        and now - float(_WHISPER_BINARY_CACHE.get("checked_at") or 0) < 300
    ):
        return _WHISPER_BINARY_CACHE["path"]
    details = []
    for candidate in whisper_candidate_paths():
        ok, detail = check_whisper_binary(candidate)
        details.append({"path": candidate, "ok": ok, "detail": detail})
        if ok:
            _WHISPER_BINARY_CACHE.update({"path": candidate, "checked_at": now, "details": details})
            configured = os.environ.get("TELESAVED_WHISPER_BIN", "").strip()
            if configured and os.path.abspath(os.path.expanduser(configured)) != candidate:
                append_whisper_log(
                    "binary_fallback",
                    "Configured Whisper binary failed validation; using fallback.\n"
                    f"configured={configured}\nselected={candidate}\n"
                    f"candidates={json.dumps(details, indent=2)}",
                    level="WARNING",
                )
            return candidate
    _WHISPER_BINARY_CACHE.update({"path": None, "checked_at": now, "details": details})
    append_whisper_log(
        "binary_unavailable",
        "No working Whisper CLI candidate was found.\n"
        f"candidates={json.dumps(details, indent=2)}\n"
        f"PATH={os.environ.get('PATH', '')}\n"
        f"TELESAVED_WHISPER_BIN={os.environ.get('TELESAVED_WHISPER_BIN', '')}",
        level="ERROR",
    )
    return None


def whisper_available() -> bool:
    return whisper_binary_path() is not None


def whisper_language() -> str:
    value = (os.environ.get("TELESAVED_WHISPER_LANGUAGE", "en") or "en").strip()
    if value.lower() in {"", "auto", "detect", "detected", "none", "null"}:
        return ""
    aliases = {
        "english": "en", "spanish": "es", "french": "fr", "german": "de",
        "italian": "it", "portuguese": "pt", "chinese": "zh",
        "japanese": "ja", "korean": "ko",
    }
    return aliases.get(value.lower(), value)


def whisper_option(name: str, default: str) -> str:
    return str(os.environ.get(name, default) if os.environ.get(name) is not None else default)


def whisper_bool_option(name: str, default: str) -> str:
    value = os.environ.get(name, default)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return "True"
    if text in {"0", "false", "no", "off"}:
        return "False"
    return str(default)


def whisper_quality_guard_enabled() -> bool:
    value = str(
        os.environ.get("TELESAVED_WHISPER_HALLUCINATION_GUARD", "1") or "1"
    ).strip().lower()
    return value not in {"0", "false", "no", "off"}


def whisper_device() -> str:
    return (os.environ.get("TELESAVED_WHISPER_DEVICE", "cpu") or "cpu").strip().lower()


def whisper_diagnostics_payload() -> dict:
    whisper_binary_path(force_refresh=True)
    return {
        "selected": _WHISPER_BINARY_CACHE.get("path") or "",
        "candidates": _WHISPER_BINARY_CACHE.get("details") or [],
        "path": os.environ.get("PATH", ""),
        "configured": os.environ.get("TELESAVED_WHISPER_BIN", ""),
        "device": whisper_device(),
        "model": os.environ.get("TELESAVED_WHISPER_MODEL", "tiny"),
        "language": whisper_language() or "auto",
        "condition_on_previous_text": whisper_bool_option(
            "TELESAVED_WHISPER_CONDITION_ON_PREVIOUS_TEXT", "False"
        ),
        "temperature": whisper_option("TELESAVED_WHISPER_TEMPERATURE", "0"),
        "compression_ratio_threshold": whisper_option(
            "TELESAVED_WHISPER_COMPRESSION_RATIO_THRESHOLD", "2.4"
        ),
        "logprob_threshold": whisper_option("TELESAVED_WHISPER_LOGPROB_THRESHOLD", "-1.0"),
        "no_speech_threshold": whisper_option(
            "TELESAVED_WHISPER_NO_SPEECH_THRESHOLD", "0.60"
        ),
        "hallucination_guard": whisper_quality_guard_enabled(),
    }


# ---------------------------------------------------------------------------
# Whisper quality filtering
# ---------------------------------------------------------------------------

def normalize_transcript_text_for_quality(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w' ]+", " ", str(text or "").lower())).strip()


def repeated_phrase_score(text: str) -> float:
    words = normalize_transcript_text_for_quality(text).split()
    if len(words) < 8:
        return 0.0
    best = 0.0
    for n in (1, 2, 3, 4):
        if len(words) < n * 2:
            continue
        grams = [tuple(words[i: i + n]) for i in range(0, len(words) - n + 1)]
        if not grams:
            continue
        counts: dict = {}
        for gram in grams:
            counts[gram] = counts.get(gram, 0) + 1
        top = max(counts.values())
        best = max(best, top / max(1, len(grams)))
    unique_ratio = len(set(words)) / max(1, len(words))
    if unique_ratio < 0.28:
        best = max(best, 1.0 - unique_ratio)
    return round(best, 3)


def whisper_segment_quality(seg: dict) -> Tuple[bool, list]:
    warnings: list = []
    text = str(seg.get("text") or "").strip()
    if not text:
        return False, ["empty text"]
    try:
        no_speech_prob = (
            float(seg["no_speech_prob"]) if seg.get("no_speech_prob") is not None else None
        )
    except Exception:
        no_speech_prob = None
    try:
        avg_logprob = (
            float(seg["avg_logprob"]) if seg.get("avg_logprob") is not None else None
        )
    except Exception:
        avg_logprob = None
    try:
        compression_ratio = (
            float(seg["compression_ratio"])
            if seg.get("compression_ratio") is not None
            else None
        )
    except Exception:
        compression_ratio = None
    try:
        no_speech_threshold = float(
            os.environ.get("TELESAVED_WHISPER_FILTER_NO_SPEECH_PROB", "0.72") or "0.72"
        )
    except Exception:
        no_speech_threshold = 0.72
    try:
        logprob_threshold = float(
            os.environ.get("TELESAVED_WHISPER_FILTER_AVG_LOGPROB", "-1.25") or "-1.25"
        )
    except Exception:
        logprob_threshold = -1.25
    try:
        compression_threshold = float(
            os.environ.get("TELESAVED_WHISPER_FILTER_COMPRESSION_RATIO", "3.0") or "3.0"
        )
    except Exception:
        compression_threshold = 3.0
    try:
        repeat_threshold = float(
            os.environ.get("TELESAVED_WHISPER_FILTER_REPEAT_SCORE", "0.62") or "0.62"
        )
    except Exception:
        repeat_threshold = 0.62
    repeat_score = repeated_phrase_score(text)
    word_count = len(normalize_transcript_text_for_quality(text).split())
    if no_speech_prob is not None and no_speech_prob >= no_speech_threshold:
        warnings.append(f"high no_speech_prob={no_speech_prob:.2f}")
    if avg_logprob is not None and avg_logprob <= logprob_threshold:
        warnings.append(f"low avg_logprob={avg_logprob:.2f}")
    if compression_ratio is not None and compression_ratio >= compression_threshold:
        warnings.append(f"high compression_ratio={compression_ratio:.2f}")
    if word_count >= 8 and repeat_score >= repeat_threshold:
        warnings.append(f"repetitive text score={repeat_score:.2f}")
    if not whisper_quality_guard_enabled():
        return True, warnings
    return not warnings, warnings


def filter_whisper_segments(raw_segments) -> Tuple[list, list, list]:
    kept: list = []
    filtered: list = []
    warning_counts: dict = {}
    if not isinstance(raw_segments, list):
        return [], [], []
    for seg in raw_segments:
        if not isinstance(seg, dict):
            continue
        keep, warnings = whisper_segment_quality(seg)
        row = dict(seg)
        if warnings:
            row["quality_warnings"] = warnings
        for warning in warnings:
            key = warning.split("=")[0].strip()
            warning_counts[key] = warning_counts.get(key, 0) + 1
        if keep:
            kept.append(row)
        else:
            filtered.append(row)
    summary = []
    if filtered:
        summary.append(
            f"Filtered {len(filtered)} likely hallucinated/low-confidence Whisper segment(s)."
        )
    for key, count in sorted(warning_counts.items(), key=lambda item: (-item[1], item[0])):
        summary.append(f"{count} segment(s): {key}.")
    return kept, filtered, summary


# ---------------------------------------------------------------------------
# Whisper transcription subprocess
# ---------------------------------------------------------------------------

def run_whisper_transcription(abs_path: str, rel_path: str = "") -> dict:
    whisper_bin = whisper_binary_path()
    if not whisper_bin:
        diagnostic = (
            "Whisper CLI is not installed or not visible to TeleSaved.\n"
            f"TELESAVED_WHISPER_BIN={os.environ.get('TELESAVED_WHISPER_BIN', '')}\n"
            f"PATH={os.environ.get('PATH', '')}\n"
            f"shutil.which('whisper')={shutil.which('whisper')}\n"
            f"candidates={json.dumps(_WHISPER_BINARY_CACHE.get('details') or [], indent=2)}"
        )
        append_whisper_log("unavailable", diagnostic, rel_path=rel_path, abs_path=abs_path, level="ERROR")
        return {
            "status": "unavailable",
            "engine": "whisper-cli",
            "text": "",
            "segments": [],
            "error": (
                "Whisper CLI is not installed or not visible to TeleSaved. "
                "Set TELESAVED_WHISPER_BIN or add whisper to the telesaved.service PATH."
            ),
        }
    timeout = int(os.environ.get("TELESAVED_WHISPER_TIMEOUT", "3600") or "3600")
    model = os.environ.get("TELESAVED_WHISPER_MODEL", "tiny") or "tiny"
    device_name = whisper_device()
    language = whisper_language()
    verbose = os.environ.get("TELESAVED_WHISPER_VERBOSE", "True") or "True"
    temperature = whisper_option("TELESAVED_WHISPER_TEMPERATURE", "0")
    condition_on_previous_text = whisper_bool_option(
        "TELESAVED_WHISPER_CONDITION_ON_PREVIOUS_TEXT", "False"
    )
    compression_ratio_threshold = whisper_option(
        "TELESAVED_WHISPER_COMPRESSION_RATIO_THRESHOLD", "2.4"
    )
    logprob_threshold = whisper_option("TELESAVED_WHISPER_LOGPROB_THRESHOLD", "-1.0")
    no_speech_threshold = whisper_option("TELESAVED_WHISPER_NO_SPEECH_THRESHOLD", "0.60")
    env = os.environ.copy()
    if device_name == "cpu":
        env["CUDA_VISIBLE_DEVICES"] = ""
    with tempfile.TemporaryDirectory(prefix="telesaved-whisper-") as tmpdir:
        cmd = [
            whisper_bin, abs_path,
            "--model", model,
            "--device", device_name,
            "--output_format", "json",
            "--output_dir", tmpdir,
            "--fp16", "False",
            "--verbose", verbose,
            "--temperature", temperature,
            "--condition_on_previous_text", condition_on_previous_text,
            "--compression_ratio_threshold", compression_ratio_threshold,
            "--logprob_threshold", logprob_threshold,
            "--no_speech_threshold", no_speech_threshold,
        ]
        if language:
            cmd.extend(["--language", language])
        command_text = shlex.join(cmd)
        start_time = time.time()
        start_detail = "\n".join([
            f"command={command_text}",
            f"whisper_bin={whisper_bin}",
            f"model={model}",
            f"device={device_name}",
            f"language={language or 'auto'}",
            f"timeout={timeout}",
            f"verbose={verbose}",
            f"temperature={temperature}",
            f"condition_on_previous_text={condition_on_previous_text}",
            f"compression_ratio_threshold={compression_ratio_threshold}",
            f"logprob_threshold={logprob_threshold}",
            f"no_speech_threshold={no_speech_threshold}",
            f"hallucination_guard={whisper_quality_guard_enabled()}",
            f"tmpdir={tmpdir}",
            f"CUDA_VISIBLE_DEVICES={env.get('CUDA_VISIBLE_DEVICES', '')}",
            f"PATH={env.get('PATH', '')}",
        ])
        append_whisper_log("start", start_detail, rel_path=rel_path, abs_path=abs_path)
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout, env=env
            )
        except subprocess.TimeoutExpired as e:
            elapsed = round(time.time() - start_time, 2)
            detail = "\n".join([
                f"command={command_text}",
                f"elapsed_seconds={elapsed}",
                f"timeout={timeout}",
                "stdout_tail:",
                compact_log_text(getattr(e, "stdout", ""), 4000),
                "stderr_tail:",
                compact_log_text(getattr(e, "stderr", ""), 4000),
            ])
            append_whisper_log("timeout", detail, rel_path=rel_path, abs_path=abs_path, level="ERROR")
            return {
                "status": "failed",
                "engine": f"whisper-cli:{model}:{device_name}",
                "text": "",
                "segments": [],
                "error": "Whisper transcription timed out. See Administration > Logs for Whisper CLI diagnostics.",
            }
        except Exception as e:
            elapsed = round(time.time() - start_time, 2)
            detail = "\n".join([
                f"command={command_text}",
                f"elapsed_seconds={elapsed}",
                f"exception_type={type(e).__name__}",
                f"exception={e}",
            ])
            append_whisper_log(
                "subprocess_exception", detail, rel_path=rel_path, abs_path=abs_path, level="ERROR"
            )
            return {
                "status": "failed",
                "engine": f"whisper-cli:{model}:{device_name}",
                "text": "",
                "segments": [],
                "error": f"Unable to launch Whisper CLI: {e}. See Administration > Logs for Whisper CLI diagnostics.",
            }
        elapsed = round(time.time() - start_time, 2)
        stderr_tail = compact_log_text(result.stderr, 8000)
        json_files = sorted(Path(tmpdir).glob("*.json"))
        result_detail = "\n".join([
            f"command={command_text}",
            f"returncode={result.returncode}",
            f"elapsed_seconds={elapsed}",
            f"json_files={[p.name for p in json_files]}",
            "stderr_tail:",
            stderr_tail,
        ])
        if result.returncode != 0:
            append_whisper_log("failed", result_detail, rel_path=rel_path, abs_path=abs_path, level="ERROR")
            return {
                "status": "failed",
                "engine": f"whisper-cli:{model}:{device_name}",
                "text": "",
                "segments": [],
                "error": (
                    (result.stderr or result.stdout or "Whisper failed")[-1600:]
                    + "\n\nSee Administration > Logs for full Whisper CLI diagnostics."
                ),
            }
        append_whisper_log("process_complete", result_detail, rel_path=rel_path, abs_path=abs_path)
        if not json_files:
            append_whisper_log(
                "missing_json", result_detail, rel_path=rel_path, abs_path=abs_path, level="ERROR"
            )
            return {
                "status": "failed",
                "engine": f"whisper-cli:{model}:{device_name}",
                "text": "",
                "segments": [],
                "error": "Whisper did not produce a JSON transcript. See Administration > Logs for Whisper CLI diagnostics.",
            }
        try:
            data = json.loads(json_files[0].read_text(encoding="utf-8", errors="replace"))
        except Exception as e:
            detail = result_detail + f"\njson_read_error={e}\njson_path={json_files[0]}"
            append_whisper_log(
                "json_read_failed", detail, rel_path=rel_path, abs_path=abs_path, level="ERROR"
            )
            return {
                "status": "failed",
                "engine": f"whisper-cli:{model}:{device_name}",
                "text": "",
                "segments": [],
                "error": f"Unable to read Whisper output: {e}. See Administration > Logs for Whisper CLI diagnostics.",
            }
        raw_segments = data.get("segments") or []
        filtered_input_segments, filtered_out_segments, quality_warnings = filter_whisper_segments(
            raw_segments
        )
        segments = []
        for seg in filtered_input_segments:
            try:
                start = float(seg.get("start") or 0.0)
                end = float(seg.get("end") or 0.0)
            except Exception:
                start = 0.0
                end = 0.0
            text = str(seg.get("text") or "").strip()
            if not text:
                continue
            segments.append({
                "start": round(start, 3),
                "end": round(end, 3),
                "start_human": transcript_timestamp(start),
                "end_human": transcript_timestamp(end),
                "timestamp": transcript_timestamp(start),
                "end_timestamp": transcript_timestamp(end),
                "duration": round(max(0.0, end - start), 3),
                "duration_human": transcript_timestamp(max(0.0, end - start)),
                "text": text,
                "avg_logprob": seg.get("avg_logprob"),
                "compression_ratio": seg.get("compression_ratio"),
                "no_speech_prob": seg.get("no_speech_prob"),
                "quality_warnings": seg.get("quality_warnings", []),
            })
        segments = enrich_transcript_segments(segments)
        raw_text_value = str(data.get("text") or "").strip()
        text_value = " ".join(
            str(seg.get("text") or "").strip()
            for seg in segments
            if str(seg.get("text") or "").strip()
        ).strip()
        if not segments and raw_text_value and not filtered_out_segments:
            text_value = raw_text_value
        script_value = transcript_script_from_segments(segments, text_value)
        detected_language = str(data.get("language") or "").strip()
        if (
            language
            and detected_language
            and detected_language.lower()
            not in {language.lower(), "english" if language.lower() == "en" else language.lower()}
        ):
            quality_warnings.append(
                f"Whisper reported language '{detected_language}' while TeleSaved requested '{language}'."
            )
        append_whisper_log(
            "success",
            "\n".join([
                f"raw_segments={len(raw_segments)}",
                f"kept_segments={len(segments)}",
                f"filtered_segments={len(filtered_out_segments)}",
                f"text_chars={len(text_value)}",
                f"detected_language={detected_language}",
                f"requested_language={language or 'auto'}",
                f"quality_warnings={json.dumps(quality_warnings[:20])}",
                f"json_file={json_files[0].name}",
            ]),
            rel_path=rel_path,
            abs_path=abs_path,
            level="WARNING" if quality_warnings else "INFO",
        )
        return {
            "status": "complete",
            "engine": f"whisper-cli:{model}:{device_name}",
            "text": text_value,
            "script": script_value,
            "segments": segments[:1000],
            "language": detected_language,
            "requested_language": language or "auto",
            "quality_warnings": quality_warnings[:50],
            "filtered_segment_count": len(filtered_out_segments),
            "raw_segment_count": len(raw_segments),
            "speaker_mode": (
                "native"
                if any(str(seg.get("speaker_mode") or "") == "native" for seg in segments)
                else "heuristic"
            ),
            "error": "",
        }


# ---------------------------------------------------------------------------
# Main analysis entry point
# ---------------------------------------------------------------------------

def analyze_audio_for_path(rel_path: str, force: bool = False, transcribe: bool = False) -> dict:
    tags = _tags()
    metadata_store = _meta()
    if rel_path not in tags:
        raise FileNotFoundError("File not found in index")
    meta = metadata_store.setdefault(rel_path, {})
    current = meta.get("audio") if isinstance(meta.get("audio"), dict) else None
    if current and current.get("status") == "complete" and not force and not transcribe:
        return audio_payload_for_path(rel_path)
    find_existing_file = _deps["find_existing_file"]
    get_video_duration_seconds = _deps["get_video_duration_seconds"]
    format_duration = _deps["format_duration"]
    now_stamp = _deps["now_stamp"]
    save_state = _deps["save_state"]
    save_audio_language = _deps["save_audio_language"]
    abs_path = find_existing_file(rel_path)
    if not abs_path or not os.path.exists(abs_path):
        payload = audio_default_payload(rel_path)
        payload.update({"status": "missing_file", "error": "Video file does not exist", "analyzed_at": now_stamp()})
        meta["audio"] = payload
        save_state()
        return payload
    payload = audio_default_payload(rel_path)
    payload["status"] = "analyzing"
    payload["analyzed_at"] = now_stamp()
    try:
        duration = float(meta.get("duration") or get_video_duration_seconds(abs_path) or 0.0)
    except Exception:
        duration = 0.0
    payload["duration"] = duration
    payload["duration_human"] = format_duration(duration)
    try:
        streams = ffprobe_audio_streams(abs_path)
        payload["audio_streams"] = streams
        payload["has_audio"] = bool(streams)
        if streams:
            first = streams[0]
            payload["codec"] = first.get("codec", "")
            payload["channels"] = first.get("channels", 0)
            payload["sample_rate"] = first.get("sample_rate", 0)
            payload["bit_rate"] = first.get("bit_rate", 0)
    except Exception as e:
        payload["status"] = "failed"
        payload["error"] = f"Audio stream probe failed: {e}"
        meta["audio"] = payload
        save_state()
        return payload
    if not payload["has_audio"]:
        payload["status"] = "complete"
        payload["transcript_status"] = "no_audio"
        meta["audio"] = payload
        save_state()
        return payload
    silence = ffmpeg_silence_detect(abs_path, duration=duration)
    payload.update({
        "segments": silence.get("segments", []),
        "silence_seconds": silence.get("silence_seconds", 0.0),
        "silence_percent": silence.get("silence_percent", 0.0),
        "active_audio_percent": silence.get("active_audio_percent", 0.0),
    })
    if silence.get("error"):
        payload["silence_error"] = silence.get("error")
    if transcribe:
        transcript = run_whisper_transcription(abs_path, rel_path=rel_path)
        payload["transcript_status"] = transcript.get("status", "failed")
        payload["transcript_engine"] = transcript.get("engine", "")
        payload["transcript_text"] = transcript.get("text", "")
        payload["transcript_script"] = transcript.get("script", "")
        payload["transcript_segments"] = transcript.get("segments", [])
        payload["transcript_language"] = transcript.get("language", "")
        payload["transcript_detected_language"] = transcript.get("language", "")
        payload["transcript_requested_language"] = transcript.get("requested_language", "")
        payload["transcript_quality_warnings"] = transcript.get("quality_warnings", [])
        payload["transcript_filtered_segment_count"] = transcript.get("filtered_segment_count", 0)
        payload["transcript_raw_segment_count"] = transcript.get("raw_segment_count", 0)
        payload["transcript_speaker_mode"] = transcript.get("speaker_mode", "")
        payload["transcript_error"] = transcript.get("error", "")
        detected_lang = transcript.get("language", "")
        if detected_lang:
            try:
                save_audio_language(rel_path, detected_lang)
            except Exception:
                logger.warning("Could not persist audio_language for %s", rel_path)
    else:
        existing = current or {}
        payload["transcript_status"] = existing.get("transcript_status") or "not_requested"
        payload["transcript_engine"] = existing.get("transcript_engine") or ""
        payload["transcript_text"] = existing.get("transcript_text") or ""
        payload["transcript_script"] = existing.get("transcript_script") or ""
        payload["transcript_segments"] = existing.get("transcript_segments") or []
        payload["transcript_language"] = (
            existing.get("transcript_language")
            or existing.get("transcript_detected_language") or ""
        )
        payload["transcript_detected_language"] = (
            existing.get("transcript_detected_language")
            or existing.get("transcript_language") or ""
        )
        payload["transcript_requested_language"] = existing.get("transcript_requested_language") or ""
        payload["transcript_quality_warnings"] = existing.get("transcript_quality_warnings") or []
        payload["transcript_filtered_segment_count"] = existing.get("transcript_filtered_segment_count") or 0
        payload["transcript_raw_segment_count"] = existing.get("transcript_raw_segment_count") or 0
        payload["transcript_speaker_mode"] = existing.get("transcript_speaker_mode") or ""
        payload["transcript_error"] = existing.get("transcript_error") or ""
    payload["status"] = "complete"
    payload["analyzed_at"] = now_stamp()
    meta["audio"] = payload
    save_state()
    return payload


# ---------------------------------------------------------------------------
# Overview and search
# ---------------------------------------------------------------------------

def build_audio_overview() -> dict:
    tags = _tags()
    metadata_store = _meta()
    short_name = _deps["short_name"]
    APP_VERSION = _deps["APP_VERSION"]
    total = len(tags)
    analyzed = 0
    with_audio = 0
    without_audio = 0
    transcripts = 0
    failed = 0
    silence_total = 0.0
    active_total = 0.0
    active_count = 0
    recent: list = []
    for rel_path in tags.keys():
        audio = (metadata_store.get(rel_path, {}) or {}).get("audio") or {}
        if not isinstance(audio, dict) or not audio:
            continue
        status = audio.get("status") or ""
        if status:
            analyzed += 1
        if status == "failed" or audio.get("error"):
            failed += 1
        if audio.get("has_audio"):
            with_audio += 1
        elif status == "complete":
            without_audio += 1
        if audio.get("transcript_text"):
            transcripts += 1
        if audio.get("active_audio_percent") is not None:
            try:
                active_total += float(audio.get("active_audio_percent") or 0.0)
                silence_total += float(audio.get("silence_percent") or 0.0)
                active_count += 1
            except Exception:
                pass
        if audio.get("analyzed_at"):
            recent.append({
                "path": rel_path,
                "filename": short_name(rel_path),
                "status": status,
                "analyzed_at": audio.get("analyzed_at"),
                "has_audio": bool(audio.get("has_audio")),
                "transcript_status": audio.get("transcript_status", ""),
            })
    recent.sort(key=lambda row: row.get("analyzed_at") or "", reverse=True)
    return {
        "ok": True,
        "version": APP_VERSION,
        "total_videos": total,
        "analyzed": analyzed,
        "not_analyzed": max(0, total - analyzed),
        "with_audio": with_audio,
        "without_audio": without_audio,
        "transcripts": transcripts,
        "failed": failed,
        "average_active_audio_percent": (
            round(active_total / active_count, 2) if active_count else 0.0
        ),
        "average_silence_percent": (
            round(silence_total / active_count, 2) if active_count else 0.0
        ),
        "whisper_available": whisper_available(),
        "whisper_path": whisper_binary_path() or "",
        "whisper_model": os.environ.get("TELESAVED_WHISPER_MODEL", "tiny"),
        "whisper_device": whisper_device(),
        "whisper_language": whisper_language() or "auto",
        "whisper_hallucination_guard": whisper_quality_guard_enabled(),
        "whisper_condition_on_previous_text": whisper_bool_option(
            "TELESAVED_WHISPER_CONDITION_ON_PREVIOUS_TEXT", "False"
        ),
        "recent": recent[:25],
    }


def search_audio_transcripts(query: str, limit: int = 100) -> list:
    tags = _tags()
    metadata_store = _meta()
    scores_store = _scores()
    serialize_item = _deps["serialize_item"]
    get_effective_tags = _deps["get_effective_tags"]
    get_manual_override = _deps["get_manual_override"]
    q = str(query or "").strip().lower()
    if not q:
        return []
    results = []
    for rel_path in tags.keys():
        audio = (metadata_store.get(rel_path, {}) or {}).get("audio") or {}
        text = str(audio.get("transcript_text") or "")
        if q not in text.lower():
            continue
        idx = text.lower().find(q)
        start = max(0, idx - 90)
        end = min(len(text), idx + len(q) + 160)
        snippet = text[start:end].strip()
        if start > 0:
            snippet = "…" + snippet
        if end < len(text):
            snippet += "…"
        item = serialize_item({
            "path": rel_path,
            "tags": get_effective_tags(rel_path),
            "score_map": scores_store.get(rel_path, {}),
            "manual_override": get_manual_override(rel_path) is not None,
        })
        item["audio_match"] = snippet
        item["transcript_engine"] = audio.get("transcript_engine", "")
        item["analyzed_at"] = audio.get("analyzed_at", "")
        results.append(item)
        if len(results) >= int(limit or 100):
            break
    return results
