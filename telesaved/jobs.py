# telesaved/jobs.py — Job and queue control system
#
# Architectural decisions honoured:
#   AD-6: No import of app.py; config values and helpers injected via _bind().
#
# Usage pattern
# ─────────────
#   import telesaved.jobs as _jobs
#   _jobs._bind({
#       "JOB_FILE": ..., "load_json": ..., "save_json": ...,
#       "datetime": ..., "hashlib": ...,
#       "timestamp_for_filename": ...,
#       "tags": ..., "metadata_store": ...,
#       "task_handlers": {
#           "index_sweep":    sweep_missing_index_entries,
#           "duplicate_scan": build_duplicate_report,
#           "audio_missing":  analyze_audio_for_path,
#           "reprocess_all":  reprocess_file,
#       },
#   })
#   # After _bind(), job globals are available:
#   jobs             = _jobs.jobs
#   job_lock         = _jobs.job_lock
#   queued_job_ids   = _jobs.queued_job_ids
#   job_engine_state = _jobs.job_engine_state

import asyncio
import json
import logging
from collections import deque
from threading import Lock

logger = logging.getLogger("telesaved")

# ── Module-level config (populated by _bind) ──────────────────────────────────
_cfg: dict = {}

# ── Module-level globals (populated in-place by _bind) ────────────────────────
job_lock:        Lock  = Lock()
jobs:            dict  = {}
queued_job_ids:  deque = deque()
job_engine_state: dict = {}

JOB_HISTORY_LIMIT = 500


# ── _bind ─────────────────────────────────────────────────────────────────────

def _bind(cfg: dict):
    """Initialise the jobs module.

    cfg must contain:
        JOB_FILE, load_json, save_json,
        datetime, hashlib, timestamp_for_filename,
        tags, metadata_store,
        task_handlers: dict mapping task name → callable
    """
    _cfg.clear()
    _cfg.update(cfg)

    # Load persisted jobs and mark any that were running as interrupted.
    loaded = cfg["load_json"](cfg["JOB_FILE"], {})
    if not isinstance(loaded, dict):
        loaded = {}
    dt = cfg["datetime"]
    for _job in list(loaded.values()):
        if _job.get("status") in {"running", "paused", "cancel_requested"}:
            _job["status"] = "interrupted"
            _job["control"] = "finished"
            _job["finished_at"] = dt.now().isoformat(timespec="seconds")
            _job["note"] = "Marked interrupted during application startup"

    jobs.clear()
    jobs.update(loaded)

    # Rebuild queue from persisted jobs.
    queued_job_ids.clear()
    queued_job_ids.extend(
        job_id for job_id, job in jobs.items() if job.get("status") == "queued"
    )

    # Initialise engine state.
    job_engine_state.clear()
    job_engine_state.update({
        "enabled": True,
        "worker_running": False,
        "started_at": "",
        "last_tick": "",
        "current_job_id": "",
        "queued": len(queued_job_ids),
    })


# ── Core job operations ───────────────────────────────────────────────────────

def persist_jobs():
    try:
        with job_lock:
            snapshot = json.loads(json.dumps(jobs))
        _cfg["save_json"](_cfg["JOB_FILE"], snapshot)
    except Exception as e:
        logger.warning("Failed to persist job state: %s", e)


def create_job(kind, title, total=0, source_ref="", metadata=None, status="running"):
    dt = _cfg["datetime"]
    hashlib = _cfg["hashlib"]
    timestamp_for_filename = _cfg["timestamp_for_filename"]
    job_id = f"{kind}-{timestamp_for_filename()}-{hashlib.sha1((title + dt.now().isoformat()).encode('utf-8')).hexdigest()[:8]}"
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
        "started_at": dt.now().isoformat(timespec="seconds"),
        "updated_at": dt.now().isoformat(timespec="seconds"),
        "finished_at": "",
        "metadata": metadata or {},
    }
    with job_lock:
        jobs[job_id] = job
        if len(jobs) > JOB_HISTORY_LIMIT:
            inactive = [j for j in jobs.values() if j.get("status") not in {"running", "paused", "cancel_requested"}]
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
    dt = _cfg["datetime"]
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
        job["updated_at"] = dt.now().isoformat(timespec="seconds")
        snapshot = dict(job)
    persist_jobs()
    return snapshot


def finish_job(job_id, status="complete", note=""):
    if not job_id:
        return None
    dt = _cfg["datetime"]
    return update_job(job_id, status=status, control="finished",
                      finished_at=dt.now().isoformat(timespec="seconds"), note=note)


def job_cancel_requested(job_id):
    job = get_job(job_id)
    return bool(job and job.get("control") == "cancel_requested")


def job_pause_requested(job_id):
    job = get_job(job_id)
    return bool(job and job.get("control") == "paused")


def request_job_control(job_id, control):
    if control not in {"paused", "running", "cancel_requested"}:
        raise ValueError("Invalid job control state")
    job = update_job(
        job_id,
        control=control,
        status="paused" if control == "paused" else (
            "cancel_requested" if control == "cancel_requested" else "running"
        ),
    )
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
    """Run a blocking operation while recording it in the Jobs page."""
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
        all_jobs = sorted((dict(j) for j in jobs.values()), key=lambda x: x.get("updated_at", ""), reverse=True)
    active = [j for j in all_jobs if j.get("status") in {"queued", "running", "paused", "cancel_requested"}]
    recent = all_jobs[:100]
    counts = {}
    for job in all_jobs:
        counts[job.get("status", "unknown")] = counts.get(job.get("status", "unknown"), 0) + 1
    return {"active": active, "recent": recent, "counts": counts, "engine": job_engine_summary()}


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
    tags = _cfg["tags"]
    metadata_store = _cfg["metadata_store"]
    task_handlers = _cfg["task_handlers"]
    update_job(job_id, status="running", control="running", note=f"Running {task}")
    try:
        if task == "index_sweep":
            update_job(job_id, current="Checking indexed files", total=len(tags), processed=0)
            result = await asyncio.to_thread(task_handlers["index_sweep"], "job_engine")
            update_job(job_id, processed=result.get("checked", 0), total=result.get("checked", len(tags)),
                       errors=result.get("errors", 0), metadata={**metadata, "result": result})
            finish_job(job_id, "complete", f"Sweep complete: removed {result.get('removed', 0)} missing records")
        elif task == "duplicate_scan":
            limit = int(payload.get("limit", 1000) or 1000)
            update_job(job_id, current="Building duplicate report", total=limit, processed=0)
            result = await asyncio.to_thread(task_handlers["duplicate_scan"], True, max(0, min(limit, 5000)))
            group_count = len(result.get("groups", [])) if isinstance(result, dict) else 0
            update_job(job_id, processed=limit, total=limit, metadata={**metadata, "result_summary": {"groups": group_count}})
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
                    await asyncio.to_thread(task_handlers["audio_missing"], rel_path, False, transcribe)
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
                    await asyncio.to_thread(task_handlers["reprocess_all"], rel_path)
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
    dt = _cfg["datetime"]
    job_engine_state["worker_running"] = True
    job_engine_state["started_at"] = dt.now().isoformat(timespec="seconds")
    logger.info("Persistent job worker started")
    while True:
        job_engine_state["last_tick"] = dt.now().isoformat(timespec="seconds")
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
