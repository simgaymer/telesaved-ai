---
title: 'Extract telesaved/jobs.py — Job/queue control system'
type: 'refactor'
created: '2025-01-30'
status: 'ready-for-dev'
review_loop_iteration: 0
context:
  - ARCHITECTURE.md
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The JOB / QUEUE CONTROL section (lines 2441–2704, ~264 lines) lives inside `app.py`, violating the Tier 2 extraction plan in ARCHITECTURE.md.

**Approach:** Move all 16 job functions and module-level globals to a new `telesaved/jobs.py` using the `_bind(cfg)` + forwarding-alias pattern. `run_queued_system_job` references external task functions (`reprocess_file`, `sweep_missing_index_entries`, `build_duplicate_report`, `analyze_audio_for_path`) — these are injected via `_bind`. `app.py` gains one import, one `_jobs._bind(cfg)` call, and forwarding aliases.

## Boundaries & Constraints

**Always:**
- Follow the `_bind(cfg)` + in-place mutation pattern.
- `jobs`, `job_lock`, `queued_job_ids`, `job_engine_state` are module globals in `jobs.py`; `app.py` aliases `jobs` and `job_engine_state` by reference, `job_lock` by direct assignment, `queued_job_ids` by reference.
- `JOB_HISTORY_LIMIT` is a constant — defined in `jobs.py`, aliased in `app.py`.
- The startup recovery loop (marking interrupted jobs) runs inside `_bind()` after loading.
- Task dispatch functions (`reprocess_file`, `sweep_missing_index_entries`, `build_duplicate_report`, `analyze_audio_for_path`, `reprocess_all_files_job`) are injected via `_bind` under `"task_handlers"` dict key so `run_queued_system_job` can dispatch by name.
- `wait_job_if_paused` and `job_worker_loop` are async — `jobs.py` may contain async functions (unlike `pipeline.py`, the async-only restriction does not apply here).
- Syntax-check both files after implementation.

**Never:**
- Do not change any job function signatures.
- Do not extract `reprocess_all_files_job` or `reprocess_selected_files_job` — they stay in `app.py` as async orchestrators.
- Do not add new logic.

</frozen-after-approval>

## Code Map

- `app.py:2441–2704` — jobs section to extract; 16 functions + 5 module-level globals
- `app.py:94` — `JOB_FILE = os.path.join(BASE_PATH, "jobs.json")` — injected via `_bind`
- `app.py:2442` — `job_lock = Lock()` — module global owned by `jobs.py`
- `app.py:2443` — `jobs = load_json(JOB_FILE, {})` — loaded in `_bind`
- `app.py:2446–2451` — startup recovery loop (marks interrupted jobs) — runs inside `_bind`
- `app.py:2582` — `queued_job_ids = deque(...)` — rebuilt from loaded `jobs` dict in `_bind`
- `app.py:2583` — `job_engine_state = {...}` — initialised in `_bind`
- `app.py:2611` — `run_queued_system_job` — dispatches to injected task handlers
- `app.py:2681` — `job_worker_loop` — async background worker; stays in `jobs.py`
- `telesaved/state.py:588` — `_bind` reference implementation
- `app.py:9407` — `asyncio.create_task(job_worker_loop())` — startup call that must still work via forwarding alias

## Tasks & Acceptance

**Execution:**
- [ ] `telesaved/jobs.py` -- CREATE with `_bind(cfg)` pattern; move all 16 functions + globals; startup recovery loop runs inside `_bind`; task dispatch in `run_queued_system_job` uses `_cfg["task_handlers"][task]` dict
- [ ] `app.py` -- REMOVE jobs section (lines 2441–2704); ADD `import telesaved.jobs as _jobs` near other telesaved imports; ADD `_jobs._bind(cfg)` call (must occur AFTER `_pipeline._bind` since `reprocess_file` is a task handler); ADD `jobs = _jobs.jobs`, `job_lock = _jobs.job_lock`, `queued_job_ids = _jobs.queued_job_ids`, `job_engine_state = _jobs.job_engine_state`, `JOB_HISTORY_LIMIT = _jobs.JOB_HISTORY_LIMIT` aliases; ADD forwarding defs for all 16 functions

**Acceptance Criteria:**
- Given `app.py` is loaded, when `python -c "import ast; ast.parse(open('app.py').read()); print('OK')"` runs, then it prints `OK`.
- Given `telesaved/jobs.py` exists, when `python -c "import ast; ast.parse(open('telesaved/jobs.py').read()); print('OK')"` runs, then it prints `OK`.
- Given the section is extracted, when `grep -n "^job_lock\s*=\|^jobs\s*=\|^queued_job_ids\s*=\|^job_engine_state\s*=" app.py` runs, then zero implementation lines return (only aliases).
- Given `asyncio.create_task(job_worker_loop())` is called at startup, when traced, then it resolves through the `app.py` forwarding alias to `_jobs.job_worker_loop`.

## Design Notes

**`_bind` cfg keys:**
```
JOB_FILE, load_json, save_json, datetime, logger,
task_handlers: {
    "index_sweep":    sweep_missing_index_entries,
    "duplicate_scan": build_duplicate_report,
    "audio_missing":  analyze_audio_for_path,
    "reprocess_all":  reprocess_file,   # single-file version
}
```

**Startup recovery in `_bind`:**
```python
def _bind(cfg: dict):
    _cfg.clear(); _cfg.update(cfg)
    loaded = cfg["load_json"](cfg["JOB_FILE"], {})
    jobs.clear(); jobs.update(loaded if isinstance(loaded, dict) else {})
    for _job in list(jobs.values()):
        if _job.get("status") in {"running", "paused", "cancel_requested"}:
            _job["status"] = "interrupted"
            ...
    queued_job_ids.clear()
    queued_job_ids.extend(job_id for job_id, job in jobs.items() if job.get("status") == "queued")
    job_engine_state.clear(); job_engine_state.update({...})
```

**Note:** `queued_job_ids` is a `deque` — use `.clear()` + `.extend()` for in-place update.

**`app.py` forwarding alias for `job_worker_loop`:**
```python
async def job_worker_loop():
    return await _jobs.job_worker_loop()
```

## Verification

**Commands:**
- `python -c "import ast; ast.parse(open('app.py').read()); print('app.py OK')"` -- expected: `app.py OK`
- `python -c "import ast; ast.parse(open('telesaved/jobs.py').read()); print('jobs.py OK')"` -- expected: `jobs.py OK`
- `grep -c "^job_lock\s*=" app.py` -- expected: `0`

## Spec Change Log

## Review Triage Log
