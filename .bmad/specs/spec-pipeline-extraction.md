---
title: 'Extract telesaved/pipeline.py — ML/AI pipeline module'
type: 'refactor'
created: '2025-01-30'
status: 'done'
review_loop_iteration: 0
context:
  - ARCHITECTURE.md
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The AI/ML pipeline (models, frame extraction, tagging, FAISS index, search) lives as a 772-line embedded block in `app.py` (lines 2743–3514), violating AD-4 which mandates pipeline logic reside in `telesaved/pipeline.py`.

**Approach:** Move all 23 pipeline functions and module-level model initialisation to a new `telesaved/pipeline.py` using the established `_bind(cfg)` + forwarding-alias pattern. `app.py` gains one `import`, one `_pipeline._bind(cfg)` call, and one forwarding alias per extracted name — no external callsite changes and no behaviour changes.

## Boundaries & Constraints

**Always:**
- Follow the `_bind(cfg)` + in-place mutation pattern from `telesaved/state.py` and `telesaved/audio.py`.
- `faiss_index` and `faiss_keys` remain owned by `app.py` module scope. `reprocess_file()` and `reprocess_all_files_job()` return the updated `(faiss_index, faiss_keys)` tuple; callers reassign. This avoids touching ~13 external callsites that use `global faiss_index, faiss_keys` outside the pipeline section.
- `reprocess_all_files_job()` is `async` and uses job primitives (`bulk_job`, `create_job`, `update_job`, `finish_job`, `wait_job_if_paused`, `job_cancel_requested`). Per AD-5, `pipeline.py` contains only sync functions. Keep `reprocess_all_files_job()` and `reprocess_selected_files_job()` in `app.py`; they call `_pipeline.reprocess_file()`.
- `reprocess_file()` in `pipeline.py` returns `(faiss_index, faiss_keys)` instead of modifying globals directly.
- Every ML import (`clip`, `torch`, `torchvision`, `PIL`, `numpy`, `faiss`, `ultralytics`) must live only in `telesaved/pipeline.py` after the extraction. Remove them from `app.py` top-level imports.
- All forwarding aliases in `app.py` must use the `def fn(*a, **kw): return _pipeline.fn(*a, **kw)` form for functions, or direct assignment for plain globals — matching existing patterns.
- Syntax-check both files after implementation.

**Never:**
- Do not change function signatures visible to external callers in `app.py`.
- Do not extract `reprocess_all_files_job()` or `reprocess_selected_files_job()` into `pipeline.py` (async + job-primitive dependency).
- Do not own `faiss_index` / `faiss_keys` in `pipeline.py` as module globals (keeps 13 external callsites unchanged).
- Do not change any route handler logic.
- Do not add new features or error handling beyond what already exists.

</frozen-after-approval>

## Code Map

- `app.py:2743–3514` — pipeline block to extract; model setup (2743–2757), NudeNet (2758–2877), frame extraction (2878–2907), preview generation (2908–2953), scene/timeline detection (2954–3123), embedding (3124–3134), tagging (3135–3235), file lookup (3236–3251), reprocess (3252–3317), migration (3318–3368), FAISS (3369–3474), search (3475–3514)
- `app.py:34–39` — ML top-level imports (`torch`, `clip`, `PIL`, `ultralytics.YOLO`, `numpy`, `faiss`) — remove after extraction
- `app.py:7212–7310` — pattern to follow: `_audio._bind()` + forwarding aliases
- `app.py:2012–2029` — `_state._bind()` + alias pattern (in-place mutation reference)
- `app.py:3267` — `reprocess_all_files_job()` (stays in `app.py`; calls `_pipeline.reprocess_file()`)
- `app.py:6584` — `reprocess_selected_files_job()` (stays in `app.py`; calls `_pipeline.reprocess_file()`)
- `app.py:6614` — external `faiss_index, faiss_keys = build_faiss_index()` callsite (uses forwarding alias)
- `telesaved/state.py:588` — `_bind(cfg)` reference implementation
- `telesaved/audio.py` — `_bind(cfg)` reference implementation for a larger module
- `telesaved/__init__.py` — package marker; no changes needed

## Tasks & Acceptance

**Execution:**
- [ ] `telesaved/pipeline.py` -- CREATE with `_bind(cfg)` pattern; move all 23 functions + model-setup block; `reprocess_file()` returns `(faiss_index, faiss_keys)` instead of mutating app globals; `_cfg` dict holds injected dependencies
- [ ] `app.py` -- REMOVE ML imports (lines 34–39: `torch`, `clip`, `PIL.Image`, `ultralytics.YOLO`, `numpy`, `faiss`); REMOVE pipeline block (lines 2743–3514); ADD `import telesaved.pipeline as _pipeline` near the other telesaved imports; ADD `_pipeline._bind(cfg)` call with all required dependencies; ADD forwarding aliases for all 23 extracted names
- [ ] `app.py` -- UPDATE `reprocess_all_files_job()` to unpack `faiss_index, faiss_keys = await asyncio.to_thread(_pipeline.reprocess_file, rel_path)` and update the final rebuild to use `_pipeline.build_faiss_index()` + `_pipeline._persist_faiss_index()`; same for `reprocess_selected_files_job()`
- [ ] `app.py` -- VERIFY all `global faiss_index, faiss_keys` declarations outside the pipeline section (lines 3254, 4018, 4213, 4283, 4442, 4664, 4672, 6585, 6743, 6891) still work correctly via forwarding aliases

**Acceptance Criteria:**
- Given the application starts, when `_pipeline._bind(cfg)` runs, then CLIP/YOLO models load, FAISS index initialises, and the startup log shows "FAISS index loaded/rebuilt" as before.
- Given `app.py` is loaded, when `grep -n "^import torch\|^import clip\|^import faiss\|^import numpy\|^from PIL\|^from ultralytics" app.py` is run, then zero matches are returned.
- Given `telesaved/pipeline.py` exists, when `python -c "import ast; ast.parse(open('telesaved/pipeline.py').read()); print('OK')"` is run, then it prints `OK`.
- Given `app.py` is modified, when `python -c "import ast; ast.parse(open('app.py').read()); print('OK')"` is run, then it prints `OK`.
- Given a search query is sent to the application, when `search()` is called, then it returns results identical to the pre-extraction behaviour.
- Given `reprocess_file()` is called, when the function completes, then `faiss_index` and `faiss_keys` in `app.py` are updated.

## Design Notes

**`reprocess_file()` return-value change:** The extracted version returns `(faiss_index, faiss_keys)`. The forwarding alias in `app.py` must reassign the globals:
```python
def reprocess_file(rel_path):
    global faiss_index, faiss_keys
    faiss_index, faiss_keys = _pipeline.reprocess_file(rel_path)
```

**`_bind` cfg keys for pipeline.py:**
```
FRAME_PATH, THUMB_PATH, PREVIEW_PATH, SCENE_PATH, BASE_PATH,
FAISS_INDEX_FILE, FAISS_KEYS_FILE, APP_VERSION,
PROMPTS, TAG_WEIGHTS, TAG_THRESHOLDS, DEFAULT_NUDENET_THRESHOLD,
embeddings_store, scores_store, tags, metadata_store,
save_state, save_ai_signals, write_ai_tag_labels,
compute_feedback_bias, get_video_duration_seconds, normalize_score,
get_effective_tags, get_manual_override, candidate_storage_roots,
safe_abs_path, get_download_directory, format_duration, now_stamp,
delete_scene_thumbnails_for_file, scene_url_for_file,
thumb_rel_path_for_file, preview_rel_path_for_file, settings_data
```

**Startup FAISS init block** (lines 3464–3474) must move into `pipeline._bind()` so it runs at bind time. It assigns `faiss_index, faiss_keys` which after extraction belong to `app.py`. Solution: `_bind()` stores them in `_cfg["_faiss_index"]` / `_cfg["_faiss_keys"]`, and `app.py` retrieves them after `_bind()` returns:
```python
_pipeline._bind(cfg)
faiss_index = _pipeline.faiss_index   # module-level in pipeline.py (set during _bind)
faiss_keys  = _pipeline.faiss_keys
```
This is the one case where `pipeline.py` does own the initial values of these globals — as module-level attributes set by `_bind()` — which `app.py` copies into its own scope immediately after.

## Verification

**Commands:**
- `python -c "import ast; ast.parse(open('app.py').read()); print('app.py OK')"` -- expected: `app.py OK`
- `python -c "import ast; ast.parse(open('telesaved/pipeline.py').read()); print('pipeline.py OK')"` -- expected: `pipeline.py OK`
- `grep -c "^import torch\|^import clip\|^import faiss\|^import numpy\|^from PIL\|^from ultralytics" app.py` -- expected: `0`
- `grep -n "sqlite3.connect" app.py` -- expected: zero matches in route handler functions (AD-3)

## Spec Change Log

## Review Triage Log
