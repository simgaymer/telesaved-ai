---
title: 'Scaling guidance document — docs/SCALING.md'
type: 'chore'
created: '2026-09-22'
status: 'done'
review_loop_iteration: 0
baseline_commit: '431692e67885670ec5931ad45200d90eabac0cef'
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** TeleSaved AI has no documentation describing resource requirements per library size, known O(n) hot paths, or operational recommendations for large deployments. Users have no guidance on what hardware to size for, when performance will degrade, or what knobs exist to tune behaviour.

**Approach:** Create `docs/SCALING.md` — a single reference document covering: in-memory data layout and growth rates, per-operation complexity, hardware sizing tables at 1 K / 10 K / 50 K / 100 K videos, known bottlenecks and their current caps, and actionable operational recommendations. Link it from `README.md` and add it to the docs list in `ARCHITECTURE.md`. No code changes.

## Boundaries & Constraints

**Always:**
- Ground every number in actual code (cite file:line where the behaviour is defined).
- Cover the six in-memory globals (`tags`, `scores_store`, `embeddings_store`, `metadata_store`, `state_ids`, `state_hashes`) and their per-entry memory cost.
- Cover the known bottlenecks: `save_state()` full-rewrite, FAISS full rebuild, `collection_counts()` O(c×n), `sweep_missing_index_entries()` O(n) filesystem scan.
- Include config knobs that already exist: `DOWNLOAD_STOP_THRESHOLD`, `INDEX_SWEEP_INTERVAL_SECONDS`, `hash_scan_limit`, `probe_missing_limit`, `max_messages_per_run`, `sem` (concurrency cap).
- Keep numbers conservative and clearly marked as estimates.

**Never:**
- Do not change any code.
- Do not recommend features or fixes that don't exist yet (flag them as "planned" at most).
- Do not invent numbers — derive them from code constants and reasonable per-entry estimates.

</frozen-after-approval>

## Code Map

- `telesaved/state.py:38-42` — six module-level globals (dict/set); `persist_state_to_sqlite` at line 251 (DELETE all + INSERT all = O(n) full rewrite)
- `telesaved/pipeline.py:731-755` — `build_faiss_index()` full rebuild from embeddings_store; 512-float32 per embedding = 2048 bytes/video
- `app.py:102` — `PER_PAGE = 24` pagination constant
- `app.py:2149-2155` — `download_control` dict; `DOWNLOAD_STOP_THRESHOLD = 5.0` at line 2150
- `app.py:2803` — `build_duplicate_report(hash_scan_limit=250)` — O(n) + up to 250 file reads
- `app.py:3867` — `build_library_items()` — O(n log n) sort + O(n) scan before pagination
- `app.py:4376` — `collection_counts()` — calls `build_library_items()` once per smart collection
- `app.py:5020` — `sweep_missing_index_entries()` — O(n) filesystem existence check every 6 h
- `app.py:5129` — `periodic_missing_index_sweep()` — `INDEX_SWEEP_INTERVAL_SECONDS = 6 * 60 * 60` at line 2216
- `telesaved/jobs.py:39` — `job_lock` (threading.Lock); `sem = asyncio.Semaphore(2)` in sources.py
- `telesaved/state.py:47` — `sqlite_lock` (threading.Lock) guards all SQLite I/O

## Tasks & Acceptance

**Execution:**
- [x] `docs/SCALING.md` -- CREATE: full scaling guidance document (sections: overview, in-memory data layout, per-operation complexity table, hardware sizing table, known bottlenecks, config knobs, operational recommendations, planned improvements callout)
- [x] `README.md` -- ADD one-line link to `docs/SCALING.md` in the docs/links section
- [x] `ARCHITECTURE.md` -- ADD `docs/SCALING.md` reference in the existing docs listing

**Acceptance Criteria:**
- Given `docs/SCALING.md` exists, when `grep -c "SCALING.md" README.md` runs, then it prints `1` or more.
- Given `docs/SCALING.md` exists, when `grep -c "SCALING" ARCHITECTURE.md` runs, then it prints `1` or more.
- Given the document, when every bottleneck section references a `file:line` anchor, then all cited line numbers are verifiable in the current codebase.

## Verification

**Commands:**
- `grep -c "SCALING.md" README.md` -- expected: `1`
- `grep -c "SCALING" ARCHITECTURE.md` -- expected: `1`
- `test -f docs/SCALING.md && echo "exists"` -- expected: `exists`
