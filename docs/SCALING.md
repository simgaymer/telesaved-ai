# Scaling Guide

This document describes how TeleSaved AI's resource usage grows with library size, identifies known performance
hotspots, lists the configuration knobs available today, and gives concrete hardware sizing recommendations.

All numbers are **conservative estimates** derived from the actual code. Where a behaviour is tied to a specific line,
the source anchor is given.

---

## Contents

1. [In-Memory Data Layout](#1-in-memory-data-layout)
2. [Memory Sizing Table](#2-memory-sizing-table)
3. [Per-Operation Complexity](#3-per-operation-complexity)
4. [Hardware Sizing Recommendations](#4-hardware-sizing-recommendations)
5. [Known Bottlenecks](#5-known-bottlenecks)
6. [Configuration Knobs](#6-configuration-knobs)
7. [Operational Recommendations](#7-operational-recommendations)
8. [Planned Improvements](#8-planned-improvements)

---

## 1. In-Memory Data Layout

TeleSaved AI keeps the entire library index in six module-level Python objects
([`telesaved/state.py:38-43`](../telesaved/state.py)).
Every indexed video contributes one entry to each structure.

| Global | Type | Per-video cost (estimate) | Purpose |
|---|---|---|---|
| `tags` | `dict[str, list[str]]` | ~200 B (8–20 tag strings) | AI and manual tags |
| `scores_store` | `dict[str, dict[str, float]]` | ~500 B (~15 label→float pairs) | Tag confidence scores |
| `embeddings_store` | `dict[str, list[float]]` | **2,048 B** (512 × 4-byte float32) | CLIP semantic embedding |
| `metadata_store` | `dict[str, dict]` | ~3–15 KB (rich Telegram + file metadata) | Source, message, file metadata |
| `state_ids` | `set[str]` | ~80 B (Telegram message key string) | Seen-message deduplication |
| `state_hashes` | `set[str]` | ~80 B (64-char hex SHA-256 string) | Content-hash deduplication |

> **Dominant cost:** `embeddings_store` and `metadata_store`. A library of 10,000 videos requires
> ~20 MB for embeddings alone and up to 150 MB for metadata.

### SQLite persistence

State is persisted to `telesaved.db` (path: `BASE_PATH/telesaved.db`). The `videos` table stores all six
fields as JSON blobs per row. A legacy `state.json` snapshot is also written on every save
([`telesaved/state.py:292-295`](../telesaved/state.py)).

The save is a **full DELETE + INSERT** transaction — not incremental updates
([`telesaved/state.py:251-289`](../telesaved/state.py)). Cost scales O(n) with library size.

### FAISS semantic index

The FAISS index ([`telesaved/pipeline.py:731-755`](../telesaved/pipeline.py)) is an
`IndexFlatIP` (flat inner-product) over all 512-dim embeddings:

- Memory: `n × 512 × 4 bytes` → **2 MB per 1,000 videos**
- Loaded from disk sidecar (`faiss_index.bin` + `faiss_keys.json`) at startup
- Rebuilt from scratch after any delete, reprocess, or index sweep

---

## 2. Memory Sizing Table

RAM figures are **process RSS** estimates — Python interpreter overhead (~80 MB) plus all in-memory structures.
Metadata estimate assumes ~5 KB average per video (moderate Telegram metadata).

| Library size | `embeddings_store` | `metadata_store` (est.) | Other globals | FAISS index | **Total RAM** |
|---|---|---|---|---|---|
| 1,000 videos | 2 MB | 5 MB | ~1 MB | 2 MB | **~170 MB** |
| 10,000 videos | 20 MB | 50 MB | ~5 MB | 20 MB | **~255 MB** |
| 50,000 videos | 100 MB | 250 MB | ~15 MB | 100 MB | **~625 MB** |
| 100,000 videos | 200 MB | 500 MB | ~25 MB | 200 MB | **~1.1 GB** |

> Add ~500 MB–2 GB to these figures if the CLIP, YOLO, and NudeNet models are loaded in the same process
> (they are — see [`telesaved/pipeline.py:78-115`](../telesaved/pipeline.py)).

---

## 3. Per-Operation Complexity

| Operation | Where | Complexity | Typical latency (10 K videos) | Notes |
|---|---|---|---|---|
| List library (one page) | `app.py:3867` | O(n log n) sort + O(n) scan | ~500 ms | Full scan before paginating; 24 items returned |
| Dashboard stats | `app.py:1755` | O(n) + up to 25 file probes | ~1–2 s | `probe_missing_limit=25` caps disk I/O |
| Smart-collection counts | `app.py:4376` | O(c × n) | ~1–5 s (c collections) | Calls `build_library_items()` once per collection |
| Duplicate report (no hashing) | `app.py:2803` | O(n) | ~200 ms | Groups by size+duration signature |
| Duplicate report (with hashing) | `app.py:2803` | O(n) + O(min(250, missing)) file reads | ~1–60 s | `hash_scan_limit=250` caps new reads |
| Index sweep | `app.py:5020` | O(n) filesystem `exists()` checks | ~1–10 min | Runs every 6 h; non-blocking acquire |
| FAISS index rebuild | `telesaved/pipeline.py:731` | O(n) + `np.vstack` | ~0.5–5 s | Triggered by delete, reprocess, sweep |
| `save_state()` | `telesaved/state.py:292` | O(n) DELETE+INSERT SQLite | ~1–10 s | Called ~30+ times per user session |
| Reprocess single file | `telesaved/pipeline.py:650` | O(1) CLIP + YOLO + NudeNet | ~3–8 s (CPU) | Triggers FAISS rebuild after |
| Reprocess all files | `app.py:4812` | O(n) sequential reprocess | Hours at large scale | Rate limited by CPU throughput |
| Rules engine (on ingest) | `app.py:5359` | O(r) per video (r = rule count) | Negligible for <100 rules | Runs per download |
| Semantic search | `telesaved/pipeline.py` | O(n) FAISS flat scan | <50 ms (CPU FAISS) | `IndexFlatIP` — no approximation |

---

## 4. Hardware Sizing Recommendations

### Minimum (up to ~5,000 videos)

| Resource | Minimum |
|---|---|
| CPU | 2 cores (x86-64) |
| RAM | 4 GB |
| Storage (vault) | Size of your video collection + 10% thumbnails/previews |
| Storage (OS/DB) | 20 GB |

### Comfortable (5,000–30,000 videos)

| Resource | Recommended |
|---|---|
| CPU | 4 cores |
| RAM | 8 GB |
| Storage (vault) | Video collection + 15% derived assets |
| Storage (OS/DB) | 40 GB |

### Large deployment (30,000–100,000 videos)

| Resource | Recommended |
|---|---|
| CPU | 8+ cores |
| RAM | 16–32 GB |
| Storage (vault) | NAS or separate volume; high sequential I/O throughput preferred |
| Storage (OS/DB) | 80 GB SSD (SQLite write throughput matters at this scale) |

> **GPU acceleration:** CLIP and YOLO inference runs on CPU by default. A CUDA GPU dramatically
> reduces reprocess time (10× typical) but is not required for normal operation.
> See [`telesaved/pipeline.py:78-90`](../telesaved/pipeline.py) for model-load logic.

---

## 5. Known Bottlenecks

### B-1 — `save_state()`: full table rewrite on every change

**File:** [`telesaved/state.py:251-289`](../telesaved/state.py)

Every call to `save_state()` executes three `DELETE FROM` statements followed by bulk `INSERT OR REPLACE`
for all rows. There is no incremental row-level update. It is called from ~30+ code paths including single-video
tag edits, rule applications, downloads, and audio analysis.

**Impact:** At 10,000 videos, each `save_state()` call serialises ~50 MB of JSON and rewrites the SQLite tables.
Latency is typically 1–10 s depending on disk speed. Under concurrent user activity this serialises
behind `sqlite_lock` ([`telesaved/state.py:47`](../telesaved/state.py)).

**Workaround (today):** Use an SSD for `BASE_PATH`. Reduce concurrent edit operations.

---

### B-2 — `build_faiss_index()`: full rebuild on every mutation

**File:** [`telesaved/pipeline.py:731-755`](../telesaved/pipeline.py)

The FAISS index is rebuilt from scratch after every delete, reprocess, or index sweep. It calls
`np.vstack()` over all embeddings and adds all vectors to a new `IndexFlatIP`. There is no incremental add/remove.

**Impact:** At 100,000 videos the rebuild allocates ~200 MB and takes 2–5 s. Bulk operations
(reprocess all, mass delete) trigger one rebuild per video.

**Workaround (today):** Avoid mass reprocess during active download sessions.

---

### B-3 — `collection_counts()`: O(c × n) unbounded scan

**File:** [`app.py:4376-4392`](../app.py)

Smart-collection badge counts are computed by calling `build_library_items()` once per configured collection
on every request that needs them. `build_library_items()` always performs a full O(n log n) sort and O(n)
filter scan before returning the count. With 10 collections and 50,000 videos this is 500,000 filter evaluations
per request.

**Impact:** Dashboard loads that include collection counts become noticeably slow (5–30 s) at large library
sizes with many smart collections.

**Workaround (today):** Keep the number of smart collections small (≤ 5 for libraries > 20,000 videos).

---

### B-4 — `sweep_missing_index_entries()`: O(n) filesystem scan, runs every 6 hours

**File:** [`app.py:5020-5128`](../app.py), interval constant [`app.py:2217`](../app.py)

The background sweep checks `os.path.exists()` for every indexed path. At 100,000 videos over a network
share this can take 10–30 minutes and generates significant I/O.

The sweep holds `index_sweep_lock` for its full duration (non-blocking acquire, so a second sweep cannot
overlap), but the first sweep still blocks that lock for the full duration.

**Impact:** On network storage or HDDs the sweep dominates I/O during its run window.

**Workaround (today):** The sweep interval is `INDEX_SWEEP_INTERVAL_SECONDS = 6 * 60 * 60`
([`app.py:2217`](../app.py)). This constant is not currently user-configurable. Avoid running manual sweeps
during active download sessions.

---

### B-5 — `build_library_items()`: full in-memory sort before pagination

**File:** [`app.py:3867`](../app.py)

Every page request sorts and scans the entire library in memory before returning 24 items
(`PER_PAGE = 24`, [`app.py:102`](../app.py)). Filtering (tag, source, duration, size) happens during the
scan, not at the database layer.

**Impact:** At 100,000 videos each page request allocates a sorted list of all matching items before
discarding all but 24. Peak memory during a list request may be 2–3× the normal metadata footprint.

---

## 6. Configuration Knobs

These are the tuneable constants available in the current release. None require recompilation — they are either
code constants or per-source settings in `config.json`.

### Storage safety threshold

```
DOWNLOAD_STOP_THRESHOLD = 5.0   # app.py:2151
```

Downloads pause automatically when free storage drops below this percentage.
Increase to stop downloads earlier and preserve headroom.

### Index sweep interval

```
INDEX_SWEEP_INTERVAL_SECONDS = 6 * 60 * 60   # app.py:2217
```

How often the background sweep checks for missing files. Not currently user-configurable via `config.json`
(requires code change). Reduce to catch orphaned entries faster; increase to reduce I/O on large libraries.

### Download semaphore (concurrent downloads)

```
sem = asyncio.Semaphore(2)   # telesaved/sources.py
```

Maximum number of simultaneous Telegram downloads. The default of 2 prevents Telegram rate-limiting.
Not currently exposed as a config option.

### Hash scan limit (duplicate report)

```
hash_scan_limit=250   # app.py:2803 (build_duplicate_report default)
hash_scan_limit=1000  # app.py:3225 (build_advanced_duplicate_report default)
```

Maximum number of files for which a new SHA-256 is computed during a duplicate scan. Files already
in `metadata_store` with a recorded hash are not re-read. Increase for more complete results at the
cost of additional I/O; decrease if the report is too slow.

### Per-source message limit

Per-source profile in `config.json`:
```json
"max_messages_per_run": 500
```

Caps the number of Telegram messages scanned per pipeline run. Controls how much history is
pulled per source sync. See [`docs/SOURCE_SYNC.md`](SOURCE_SYNC.md).

### Dashboard stat probe limit

```
probe_missing_limit=25   # app.py:1755
```

During dashboard stats computation, at most this many files with missing duration metadata are
probed from disk. Prevents the dashboard from blocking on large numbers of unprobed files.

---

## 7. Operational Recommendations

### General

- **Use an SSD for `BASE_PATH`** — `save_state()` rewrites the SQLite database on every change. SSD write
  latency (0.1–1 ms) vs. HDD (10–50 ms) makes a 10–50× difference at large library sizes.
- **Keep video files on the same storage volume as `BASE_PATH`** — the index sweep, duplicate report, and
  metadata probes all call `os.path.exists()` and `os.path.getsize()` for every indexed file. Network
  round-trips multiply these costs significantly.
- **Monitor `telesaved.log`** for `"Index sweep"` and `"save_state"` lines to measure real-world latency
  in your environment.

### For libraries > 20,000 videos

- Keep smart collections to **5 or fewer** to avoid O(c × n) slowdowns on every dashboard load.
- Schedule **manual reprocess operations** (reprocess all, bulk rules apply) during off-peak hours.
- Avoid **triggering manual index sweeps** during active download sessions — the sweep holds the sweep
  lock for its full duration.
- **Disable `auto_tag`** on high-volume sources if tagging latency becomes a bottleneck; the source profile
  setting `"auto_tag": false` stores a basic `Movie`/`Clip` format tag instead of running CLIP/YOLO.

### For libraries > 50,000 videos

- Allocate **16+ GB RAM** — the combined embeddings, metadata, and FAISS index for 50,000 videos
  approaches 600 MB; Python overhead and aiohttp request buffers add significant headroom requirements.
- Consider **periodic full backups** (Administration → Backup) before large batch operations. The backup
  includes the SQLite database and all sidecar files. See [`docs/ADMINISTRATION.md`](ADMINISTRATION.md).
- **FAISS sidecar files** (`faiss_index.bin`, `faiss_keys.json`) are loaded at startup to avoid rebuild cost.
  Do not delete them between restarts unless you intend a clean index rebuild.

---

## 8. Planned Improvements

The following optimisations are identified but not yet implemented. They are tracked in the performance
optimisation work item (Priority 4B in [`docs/ROADMAP.md`](ROADMAP.md)).

| Improvement | Bottleneck addressed | Impact |
|---|---|---|
| Incremental `save_state()` — row-level `UPDATE` for changed rows only | B-1 | 10–100× faster for single-video edits |
| FAISS incremental add/remove instead of full rebuild | B-2 | Eliminates full rebuild cost on single-video mutations |
| Cache `collection_counts()` per request or on a short TTL | B-3 | Removes O(c × n) from dashboard hot path |
| Configurable `INDEX_SWEEP_INTERVAL_SECONDS` in `config.json` | B-4 | Lets operators reduce I/O on large network libraries |
| Paginated `build_library_items()` with early-exit after page fill | B-5 | Eliminates full-sort allocation for large filtered sets |
