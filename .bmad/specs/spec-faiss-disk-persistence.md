---
title: 'FAISS disk persistence — checkpoint on shutdown, load on startup'
type: 'feature'
created: '2026-09-01'
status: 'ready-for-dev'
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The FAISS semantic-search index is rebuilt entirely from scratch on every
restart by iterating over `embeddings_store`. At >5,000 vectors, startup is slow and the
app itself already logs a warning about this. There is no continuity between runs.

**Approach:** Persist the index to disk as two sidecar files in `BASE_PATH` — a binary
FAISS index (`faiss.write_index`) and a JSON keys file — immediately after every
`build_faiss_index()` call that succeeds and produces a non-empty result. On startup,
try to load these files before falling back to the rebuild path. Wipe the sidecar files
whenever the in-memory index is intentionally cleared (restore, trash-all, etc.) so a
stale cache is never loaded.

## Boundaries & Constraints

**Always:**
- Sidecar files live at `BASE_PATH/faiss_index.bin` and `BASE_PATH/faiss_keys.json`.
- Startup load is validated: key count must match vector count in the index, and every
  key must exist in `embeddings_store`. Fail fast to rebuild if any check fails.
- Persist is best-effort: a write failure logs a warning but does not crash or abort the
  current operation.
- The existing `build_faiss_index()` function signature is unchanged — it still returns
  `(index, keys)` and has no side effects. Persistence is a wrapper concern.
- `embeddings_store` remains the source of truth. The FAISS sidecar is purely a
  startup-time performance cache.

**Never:**
- Do not store `embeddings_store` itself in the FAISS sidecar (it already lives in
  SQLite; duplicating it on disk is redundant and wastes space).
- Do not change `build_faiss_index()` internals or its call signature.
- Do not add async I/O — all FAISS operations are synchronous/CPU-bound.
- Do not change the backup/restore pipeline beyond invalidating stale sidecar files
  after a restore.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Normal startup (cache present, valid) | `faiss_index.bin` + `faiss_keys.json` exist; all keys in `embeddings_store` | Index loaded from disk, rebuild skipped; log "FAISS index loaded from disk (N vectors)" | — |
| Normal startup (cache absent) | Sidecar files missing | Rebuild from `embeddings_store`; sidecar written afterwards | — |
| Cache corrupt / key mismatch | Key count ≠ vector count, or a key missing from `embeddings_store` | Log warning, fall back to rebuild; stale files overwritten | Warning-only, no crash |
| Empty `embeddings_store` on startup | 0 embeddings | `faiss_index = None, faiss_keys = []`; nothing written to disk | — |
| Restore from backup | Restore replaces `embeddings_store` | Stale sidecar deleted before `build_faiss_index()` call | — |
| Disk full on persist | Write fails | Log warning; in-memory index still usable | Warning-only |
| Persist called with `index = None` | `build_faiss_index()` returned `(None, [])` | Skip write; delete stale sidecar if present | — |

</frozen-after-approval>

## Code Map

- `app.py:80-101` — `BASE_PATH` and path constants; `FAISS_INDEX_FILE` / `FAISS_KEYS_FILE` go here.
- `app.py:3733-3767` — `# ===== FAISS =====` section: `build_faiss_index()` + module-level init call.
  New helpers `_persist_faiss_index()` and `_load_faiss_index()` live here.
- `app.py:3620-3631` — `reprocess_single_file()`: calls `build_faiss_index()` → add persist call.
- `app.py:3633` — `reprocess_all_files_job()`: calls `build_faiss_index()` → add persist call (line ~4470).
- `app.py:4568` — `remove_item()`: calls `build_faiss_index()` → add persist.
- `app.py:4638` — `restore_from_trash()`: calls `build_faiss_index()` → add persist.
- `app.py:4797` — `restore_from_backup()`: calls `build_faiss_index()` after restore — **delete sidecar before this call**, then persist afterwards.
- `app.py:6882` — bulk download/ingest job: calls `build_faiss_index()` → add persist.
- `app.py:7104` — `sweep_missing_index_entries()`: calls `build_faiss_index()` → add persist.
- `app.py:7186-7249` — `reindex_library()` / rebuild operations: calls `build_faiss_index()` → add persist.
- `app.py:10337` — `main()` / shutdown: add SIGTERM/SIGINT handler that persists before exit.

## Tasks & Acceptance

**Execution:**
- [ ] `app.py` -- Add `FAISS_INDEX_FILE` and `FAISS_KEYS_FILE` path constants alongside other `BASE_PATH`-relative paths (line ~95).
- [ ] `app.py` -- Add `_persist_faiss_index(index, keys)` and `_load_faiss_index()` helpers in the FAISS section (after `build_faiss_index()`). `_persist_faiss_index` writes the binary index + keys JSON atomically (temp file + `os.replace`). `_load_faiss_index` reads both files, validates key count matches vector count and all keys exist in `embeddings_store`, returns `(index, keys)` on success or `(None, None)` on any failure.
- [ ] `app.py` -- Replace the module-level `faiss_index, faiss_keys = build_faiss_index()` init block with: try `_load_faiss_index()` first; fall back to `build_faiss_index()` + `_persist_faiss_index()`.
- [ ] `app.py` -- Add a helper `_invalidate_faiss_cache()` that deletes both sidecar files if they exist (used before rebuild-from-scratch and after restore).
- [ ] `app.py` -- After every existing `faiss_index, faiss_keys = build_faiss_index()` callsite that is driven by a data change (reprocess, remove, restore-from-trash, sweep, bulk ingest, reindex), add `_persist_faiss_index(faiss_index, faiss_keys)`.
- [ ] `app.py` -- In the restore-from-backup path: call `_invalidate_faiss_cache()` before `build_faiss_index()`, then `_persist_faiss_index` after.
- [ ] `app.py` -- Update the startup log message to distinguish "loaded from disk" vs "rebuilt from embeddings".
- [ ] `app.py` -- Remove / update the old "in-memory only — rebuilt on every restart" warning log that fires at startup (line 3766) since it's no longer accurate.
- [ ] `app.py` -- Remove / update the roadmap-reference warning inside `build_faiss_index()` (line ~3759) since the feature is now implemented.

**Acceptance Criteria:**
- Given the app has previously run and built a non-empty index, when the process restarts, then `telesaved.log` contains "FAISS index loaded from disk" and does NOT contain "rebuilt" for the startup message.
- Given `faiss_index.bin` is deleted manually before restart, when the process starts, then it falls back to rebuild and re-creates the sidecar files.
- Given a restore-from-backup completes, when the app continues, then the FAISS index reflects the restored `embeddings_store` (not the pre-restore state).
- Given `embeddings_store` is empty on startup, when the process starts, then no sidecar files are written.

## Verification

**Commands:**
- `python -c "import app" 2>&1 | head -5` — expected: no import error
- `grep -n "FAISS_INDEX_FILE\|FAISS_KEYS_FILE\|_persist_faiss_index\|_load_faiss_index\|_invalidate_faiss_cache" app.py | wc -l` — expected: ≥10 references
- `grep -n "in-memory only — rebuilt on every restart" app.py` — expected: no output (old message removed)
- `grep -n "P2 roadmap item" app.py` — expected: no output (roadmap note removed)
