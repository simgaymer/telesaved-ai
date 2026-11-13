---
title: 'Extract telesaved/sources.py — Video source helpers and download/ingest'
type: 'refactor'
created: '2025-01-30'
status: 'done'
review_loop_iteration: 0
baseline_commit: '431692e67885670ec5931ad45200d90eabac0cef'
context:
  - ARCHITECTURE.md
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The VIDEO SOURCE HELPERS and DOWNLOAD / INGEST sections (lines 2826–3503, ~678 lines, 25 functions) live inside `app.py`, violating the Tier 2 extraction plan in ARCHITECTURE.md.

**Approach:** Move all 25 functions and the `sem` semaphore to a new `telesaved/sources.py` using the `_bind(cfg)` + forwarding-alias pattern. `sources.py` is async-heavy (9 async functions) — async is permitted here (unlike `pipeline.py`). The `client` (Telethon TelegramClient) is injected late via a second `_bind_client(client)` call at startup after the client is created (line 9354). `process_message` updates `faiss_index`/`faiss_keys` and returns `(faiss_index, faiss_keys)`; the forwarding alias in `app.py` handles the global reassignment.

## Boundaries & Constraints

**Always:**
- Follow the `_bind(cfg)` + forwarding-alias pattern.
- `sem = asyncio.Semaphore(2)` is a module-level global owned by `sources.py`; `app.py` aliases it.
- `client` is injected via a separate `_bind_client(client)` call (not part of the main `_bind`) because it is defined after `_bind` runs. `_bind_client` simply stores `client` in `_cfg["client"]`.
- `process_message` declares `global faiss_index, faiss_keys` in the original — in `sources.py` it calls `build_faiss_index()` + `_persist_faiss_index()` via injected callables and returns `(faiss_index, faiss_keys)`. The `app.py` forwarding alias does the global update.
- All 25 functions are extracted. `sem` moves to `sources.py`.
- Syntax-check both files after implementation.

**Never:**
- Do not extract the TRASH, BACKUP, or other sections that follow line 3503.
- Do not change any function signatures visible to external callers.
- Do not make `_bind_client` part of the main `_bind` call — the ordering constraint is real.
- Do not add new logic.

</frozen-after-approval>

## Code Map

- `app.py:2826–3325` — VIDEO SOURCE HELPERS (24 functions)
- `app.py:3326–3503` — DOWNLOAD / INGEST (`sem` global + `process_message`)
- `app.py:3327` — `sem = asyncio.Semaphore(2)` — moves to `sources.py` module level
- `app.py:3328` — `process_message` — async, uses `client`, calls `fused_tag`, `build_faiss_index`, `_persist_faiss_index`, `create_job`, `finish_job`, `update_job`
- `app.py:9354` — `client = TelegramClient(...)` — defined late; passed via `_bind_client`
- `app.py:9405` — MAIN section — where `_bind_client` call goes (after client created)
- `telesaved/state.py:588` — `_bind` reference
- `app.py:2742` — `_pipeline._bind` pattern — shows where `sources._bind` slot fits in startup sequence
- `app.py:6574` — `from telesaved import audio as _audio` — import placement reference

**Key injected dependencies:**
- From `_bind`: `state_ids`, `tags`, `scores_store`, `embeddings_store`, `state_hashes`, `metadata_store`, `save_state`, `get_source_profile`, `source_allows_message`, `get_download_directory`, `source_display_name`, `safe_source_subdir`, `ensure_directory`, `refresh_storage_download_mode`, `download_control`, `create_job`, `finish_job`, `update_job`, `job_cancel_requested`, `job_pause_requested`, `wait_job_if_paused`, `set_download_status`, `clear_download_status`, `record_download_history`, `file_hash`, `fused_tag`, `get_video_duration_seconds`, `build_faiss_index`, `_persist_faiss_index`, `apply_auto_rules_to_path`, `format_bytes`, `pct`, `enabled_video_sources`, `APP_VERSION`, `safe_rel_path`, `safe_abs_path`, `utils` (telethon utils), `TELEGRAM_METADATA_PATH`
- From `_bind_client`: `client`

## Tasks & Acceptance

**Execution:**
- [x] `telesaved/sources.py` -- CREATE with `_bind(cfg)` and `_bind_client(client)` pattern; move all 25 functions + `sem`; `process_message` returns `(faiss_index, faiss_keys)` after rebuilding FAISS
- [x] `app.py` -- REMOVE sources sections (lines 2826–3503); ADD `import telesaved.sources as _sources` near other telesaved imports; ADD `_sources._bind(cfg)` call in startup sequence; ADD `_sources._bind_client(client)` call in MAIN section after `client` is created; ADD `sem = _sources.sem` alias; ADD forwarding defs for all 25 functions; `process_message` forwarding alias unpacks `(faiss_index, faiss_keys)`

**Acceptance Criteria:**
- Given `app.py` is loaded, when `python -c "import ast; ast.parse(open('app.py').read()); print('OK')"` runs, then it prints `OK`.
- Given `telesaved/sources.py` exists, when `python -c "import ast; ast.parse(open('telesaved/sources.py').read()); print('OK')"` runs, then it prints `OK`.
- Given sections are extracted, when `grep -n "^sem\s*=" app.py` runs, then zero implementation lines return (only the alias).
- Given `process_message` is called, when traced through the forwarding alias, then `faiss_index` and `faiss_keys` in `app.py` scope are updated.
- Given `_bind_client` is called after client creation in MAIN, when `_sources._cfg["client"]` is accessed, then it returns the `TelegramClient` instance.

## Design Notes

**`_bind_client` pattern:**
```python
def _bind_client(client):
    """Called after TelegramClient is created in app.py MAIN."""
    _cfg["client"] = client
```

**`process_message` return value:**
```python
async def process_message(message, source_ref="me", source_title="Saved Messages"):
    ...
    fi, fk = _cfg["build_faiss_index"]()
    _cfg["_persist_faiss_index"](fi, fk)
    ...
    return fi, fk  # caller updates app-scope globals
```

**`app.py` forwarding alias:**
```python
async def process_message(message, source_ref="me", source_title="Saved Messages"):
    global faiss_index, faiss_keys
    result = await _sources.process_message(message, source_ref=source_ref, source_title=source_title)
    if result is not None:
        faiss_index, faiss_keys = result
```

**Startup order in `app.py`:**
```python
# In the PIPELINE section (current line ~2742):
_pipeline._bind({...})
_sources._bind({...})   # ← added here; no client yet
faiss_index = _pipeline.faiss_index
faiss_keys  = _pipeline.faiss_keys
...
# In MAIN (current line ~9405), after client = TelegramClient(...):
_sources._bind_client(client)
```

## Verification

**Commands:**
- `python -c "import ast; ast.parse(open('app.py').read()); print('app.py OK')"` -- expected: `app.py OK`
- `python -c "import ast; ast.parse(open('telesaved/sources.py').read()); print('sources.py OK')"` -- expected: `sources.py OK`
- `grep -c "^sem\s*=" app.py` -- expected: `0`
- `grep -c "^import telethon\|^from telethon" telesaved/sources.py` -- expected: `0` (no direct Telethon import — uses injected `utils` and `client`)

## Spec Change Log

## Review Triage Log
