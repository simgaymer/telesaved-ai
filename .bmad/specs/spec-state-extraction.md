---
title: 'telesaved/state.py — extract SQLite state backend + in-memory globals'
type: 'refactor'
created: '2026-09-01'
status: 'ready-for-dev'
review_loop_iteration: 0
context: ['ARCHITECTURE.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** All SQLite I/O, the six shared in-memory state dicts/sets (`tags`,
`scores_store`, `embeddings_store`, `metadata_store`, `state_ids`, `state_hashes`),
and `save_state()` live inline in `app.py` — violating AD-1 and AD-3. Every future
module extraction depends on a clean state boundary.

**Approach:** Extract the `# SQLITE STATE BACKEND` section (lines 1981–2360) and the
`# STATE` initialisation block (lines 2361–2399) into `telesaved/state.py`. The module
owns the six globals. `app.py` calls `state._bind(cfg)` at startup, then accesses the
globals via `state.tags`, `state.metadata_store`, etc. and calls `state.save_state()`.
All existing call sites in `app.py` get 1-line forwarding aliases, matching the Tier 1
extraction pattern. Six raw `sqlite_connect()` calls in route handlers are wrapped into
typed helper functions in `state.py` (AD-3 compliance).

## Boundaries & Constraints

**Always:**
- `telesaved/state.py` imports only from stdlib + `telesaved/` downward (no import of
  `app.py`). Config values (`DB_FILE`, `STATE_FILE`, `BASE_PATH`, etc.) are passed as
  arguments to `state._bind()` — not imported.
- The six state globals (`tags`, `scores_store`, `embeddings_store`, `metadata_store`,
  `state_ids`, `state_hashes`) are module-level dicts/sets in `state.py`, populated by
  `_bind()` calling `_load_state()`. All other modules access them as
  `state.tags`, `state.metadata_store`, etc. — never re-import the raw objects.
- `sqlite_lock` and all `sqlite3.connect()` calls live only in `state.py` (AD-3).
  The six raw `sqlite_connect()` calls currently in route handlers (lines 8330–8468)
  must be replaced with typed helpers added to `state.py`.
- `app.py` retains 1-line forwarding aliases for every extracted function so the rest of
  `app.py`'s ~390 state-reference call sites continue to work unchanged.
- `state._bind()` must be idempotent: calling it twice (e.g. after a backup restore)
  resets the in-memory state without leaking old references.
- `compute_library_dashboard_stats()` and `VALID_LABEL_SOURCES` remain in `app.py` for
  now — they use other `app.py` helpers and are deferred to a later tier.

**Never:**
- Do not change function signatures or return types of any extracted function.
- Do not move `compute_library_dashboard_stats()` — it calls `get_effective_tags`,
  `find_existing_file`, `collect_file_metadata`, etc., all still in `app.py`.
- Do not change the backup/restore logic in `app.py` — only update it to call
  `state._rebind(restored_state)` instead of re-assigning globals directly.
- Do not add async code to `state.py` — all DB operations are synchronous.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Normal startup | `DB_FILE` exists, data present | `state._bind()` loads all six dicts; app continues normally | — |
| First run (empty DB) | No videos in DB | All six dicts empty; `init_sqlite_db()` creates schema | — |
| Legacy JSON migration | `STATE_FILE` has data, DB empty | `persist_state_to_sqlite()` migrates it; `_bind()` reloads from DB | — |
| `_bind()` called twice (restore) | Second call after restore | In-memory globals reset to restored data; old references via `state.tags` see new data | — |
| Raw sqlite in route handler | `api_tags_suggestion_submit` etc. | Replaced with typed helpers: `state.suggest_tag()`, `state.list_suggestions()`, `state.approve_suggestion()`, `state.reject_suggestion()`, `state.export_tag_labels()` | Existing error handling preserved |

</frozen-after-approval>

## Code Map

- `app.py:1981–2399` — `# SQLITE STATE BACKEND` + `# STATE` sections: everything being extracted.
  - `SQLITE_SCHEMA_VERSION`, `sqlite_lock` — module constants in `state.py`
  - `sqlite_connect`, `_column_exists`, `init_sqlite_db`, `sqlite_state_has_data`, `sqlite_json_load` — internal helpers
  - `load_state_from_sqlite`, `persist_state_to_sqlite` — core load/persist
  - `save_ai_signals`, `get_ai_signals`, `save_audio_language`, `get_audio_language` — typed column mutators
  - `write_tag_label`, `write_ai_tag_labels`, `get_tag_labels`, `migrate_legacy_tag_labels` — label helpers
  - `write_legacy_state_snapshot`, `sqlite_backend_info` — diagnostics
  - `save_state` — the single public persist entrypoint
  - `state_ids`, `state_hashes`, `tags`, `scores_store`, `embeddings_store`, `metadata_store` — the six globals
- `app.py:8330–8468` — six raw `sqlite_connect()` in route handlers (AD-3 violations):
  - `api_tags_suggestion_submit` (line 8330): `suggest_tag()`
  - `api_tags_suggestions_list` (line 8351): `list_suggestions()`
  - `api_tags_suggestion_approve` (lines 8374, 8399): `get_suggestion()` + `set_suggestion_status()`
  - `api_tags_suggestion_reject` (line 8416): `set_suggestion_status()`
  - `api_tags_labels_export` (line 8468): `export_tag_labels()`
- `app.py:7607–7663` — `audio._bind()` and `serializers._bind()` calls at startup; `state._bind()` added here.
- `telesaved/__init__.py` — add `from telesaved import state` comment note (no import needed; `app.py` imports the module directly).
- `telesaved/state.py` — new file; the extracted module.

## Tasks & Acceptance

**Execution:**
- [ ] `telesaved/state.py` -- Create module. Copy the full `# SQLITE STATE BACKEND` section (all functions: `sqlite_connect` through `sqlite_backend_info`) verbatim. Add module-level `tags`, `scores_store`, `embeddings_store`, `metadata_store` dicts and `state_ids`, `state_hashes` sets (initially empty). Add `_cfg = {}` module-level config dict. Add `save_state()` that calls `persist_state_to_sqlite` + `write_legacy_state_snapshot` using the module-level globals. Add `_bind(cfg: dict)` that stores cfg keys (`DB_FILE`, `STATE_FILE`, `BASE_PATH`) in `_cfg`, calls `init_sqlite_db()`, does legacy JSON migration if needed, loads state from SQLite, and populates the six module-level globals via `.clear()` + `.update()`. Add `_rebind(restored_state: dict)` for use after backup restore. Add the five typed tag-suggestion helpers: `suggest_tag()`, `list_suggestions()`, `get_suggestion()`, `set_suggestion_status()`, `export_tag_labels()` — moving the raw SQL from the route handlers.
- [ ] `app.py` -- At startup (after `audio._bind()` call), add `import telesaved.state as _state` and call `_state._bind({...})` passing all required config keys.
- [ ] `app.py` -- Replace the `# SQLITE STATE BACKEND` section body (keep the section header comment) with 1-line forwarding aliases for every extracted function. Keep `VALID_LABEL_SOURCES` in `app.py` (it's used by route handlers directly).
- [ ] `app.py` -- Replace the `# STATE` init block (lines 2361–2399) with aliases pointing to the `_state` module's globals and `save_state`. The six globals (`tags`, `scores_store`, etc.) become module-level names in `app.py` that reference `_state.tags`, etc. — so all downstream code continues to work via the same names.
- [ ] `app.py` -- Replace the six raw `sqlite_connect()` blocks in route handlers (lines ~8330–8468) with calls to the new typed helpers in `_state`.
- [ ] `app.py` -- Update the backup restore block to call `_state._rebind(restored_state)` instead of manually reassigning the six globals.

**Acceptance Criteria:**
- Given the app starts normally, when `telesaved.log` is inspected, then no `ImportError` or `AttributeError` appears.
- Given `grep -n "sqlite3.connect\|sqlite_connect" app.py`, then zero results appear outside of the forwarding alias definitions.
- Given `grep -n "sqlite_lock\|sqlite_connect\|sqlite3" telesaved/serializers.py telesaved/audio.py`, then zero results appear (those modules do not touch SQLite).
- Given a backup restore completes, when the library is accessed, then it reflects the restored state (not pre-restore data).
- Given `python3 -c "import ast; ast.parse(open('app.py').read())"`, then it exits with code 0.

## Design Notes

### Global alias pattern (matching Tier 1)

In `app.py`, after `_state._bind(cfg)`, the six globals are aliased at module level so
all 390 downstream call sites are unchanged:

```python
tags            = _state.tags
scores_store    = _state.scores_store
embeddings_store= _state.embeddings_store
metadata_store  = _state.metadata_store
state_ids       = _state.state_ids
state_hashes    = _state.state_hashes
```

This works because Python module-level names are just dict entries — `tags` in `app.py`
becomes a reference to `_state.tags` (the same dict object). All mutations via
`tags[path] = ...` go through the same object. The only constraint: if `_state._rebind()`
replaces the dict objects (via `.clear() + .update()`), the aliases in `app.py` still
point to the same objects (because `.clear()` + `.update()` mutates in-place, not replaces).

### `_bind()` must use `.clear() + .update()` not reassignment

`_bind()` must populate the module-level dicts/sets in-place:
```python
tags.clear(); tags.update(loaded["tags"])
```
NOT `tags = loaded["tags"]` — that would replace the module-level object and break the
aliases already held by `app.py`.

## Verification

**Commands:**
- `python3 -c "import ast; ast.parse(open('app.py').read()); print('SYNTAX OK')"` — expected: `SYNTAX OK`
- `python3 -c "import ast; ast.parse(open('telesaved/state.py').read()); print('SYNTAX OK')"` — expected: `SYNTAX OK`
- `grep -n "sqlite3.connect\|sqlite_connect(" app.py | grep -v "^.*def \|^.*#\|_state\." | wc -l` — expected: `0`
- `grep -n "sqlite_lock\|sqlite_connect\|sqlite3" telesaved/audio.py telesaved/serializers.py | wc -l` — expected: `0`
- `grep -n "_state\._bind\|_state\.save_state\|_state\.tags" app.py | wc -l` — expected: `≥5`
