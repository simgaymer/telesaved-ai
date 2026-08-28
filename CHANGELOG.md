# Changelog

## 4.5.6 — Tier 1 Modular Refactor: Serializers extracted

- **`ARCHITECTURE.md` created** — Formal architecture spine documenting the layered-monolith → modular-monolith transition paradigm, 7 architectural decisions (AD-1 through AD-7), module dependency diagram, consistency conventions, current and target directory layout, Tier 1 extraction order, and deferred decisions. This is the build contract for all future module work.
- **`telesaved/` package created** — `telesaved/__init__.py` establishes the package. All subsequent module extractions land here.
- **`telesaved/serializers.py` extracted** — The entire SERIALIZERS section (350 lines of implementation) moved out of `app.py` into a purpose-built module. Functions: `serialize_item`, `score_rows`, `similar_video_items`, `tag_summary_for_paths`, `source_summary_for_paths`, `cluster_label_from_paths`, `normalized_embedding_map`, `cosine_similarity`, `build_visual_cluster_report`, `build_item_detail_payload`. Uses a `_bind()` dependency-injection pattern at startup — no circular imports, no import of `app.py`, all existing call sites in `app.py` unchanged via forwarding aliases.
- `app.py` shrinks from 11,524 → 11,232 lines (−292 lines of implementation). All forwarding aliases are 1-line pass-throughs.

## 4.5.5 — Medium-Priority Gap Fixes

- **RBAC smoke test section** — `docs/SMOKE_TEST.md` now includes a full Section 10 (RBAC and permissions) with sub-sections for Viewer, Manager, and Source ACL. Covers 15 concrete test cases: API 403 checks for unpermitted actions, Manager vs Admin boundary, source-scoped library filtering, and thumbnail/file 403 for restricted sources. Section numbering updated: Limp mode → §11, Upgrade → §12. Release gate updated to require RBAC section pass on auth-touching releases.
- **Safe-path audit complete** — `api_admin_backups_download` was the remaining route where `safe_join()` lacked a `try/except ValueError` guard. Now returns 403 on traversal attempts, matching all other static-serving routes.
- **Air-gapped / offline setup wizard** — Step 2 now has a secondary "Skip validation (offline / air-gapped) →" button (muted, below the primary Validate button). Requires API ID and hash to be filled; advances to step 3 without the Telegram round-trip. Documented in `docs/INSTALLATION.md` (new "Air-gapped or offline installation" subsection) and `docs/TROUBLESHOOTING.md` (new "Setup wizard times out on step 2" section).
- `APP_VERSION` → `4.5.5`; sidebar badge, README, ROADMAP, INSTALLATION.md updated.

## 4.5.4 — High-Priority Security + Reliability Fixes

- **SRI integrity attributes** — All three CDN scripts (React 18.3.1, ReactDOM 18.3.1, Babel Standalone 8.0.4) pinned to exact versions with `integrity="sha384-…"` and `crossorigin="anonymous"`. Browser will refuse to execute any tampered or substituted script. Closes the supply-chain / CDN-outage risk.
- **Safe-path hardening** — `/previews/` and `/thumbs/` static-file routes now wrap `safe_abs_path()` in `try/except ValueError` (matching the existing pattern on `/files/` and `/scenes/`). Path traversal attempts now return 403 instead of an unhandled 500.
- **FAISS rebuild warning** — `build_faiss_index()` now emits a `WARNING` log when the index holds >5,000 vectors, reminding operators that rebuilds are in-memory only and take longer at scale. Startup log also announces the embedding count before the rebuild begins.
- **Auth error state** — `checkAuth()` now retries twice (2-second delay between attempts) before giving up, so a backend that is still starting doesn't immediately drop the UI to login. After retries are exhausted, a new `"error"` app state renders a "Cannot connect to TeleSaved" screen with a **Retry** button and the actual error message, rather than silently falling through to the login page.

## 4.5.3 — P1 Stabilisation + Gap Fixes

- Fixed `api()` helper to throw `AuthError` on 401 responses instead of silently returning the error body; `App()` now catches `AuthError` in `checkAuth()` and the connectivity poller and redirects to the login screen.
- Added hash-based URL routing: `parseHash()` / `buildHash()` helpers; browser back/forward now works via `popstate` listener; deep-linking to `#/item/PATH`, `#/admin/TAB`, `#/watch-later`, etc. restores the correct view on refresh.
- Fixed `onError` infinite reload loop in `ListRow` image tag (same pattern as the earlier `VideoCard` fix — `setSrc("")`).
- Removed dead `speakerColors` array from `AILibrarian` component.
- Removed dead sort props (`sortBy`, `setSortBy`, `sortDir`, `setSortDir`) from `ListRow` signature and all call sites.
- Added segment count limit (max 5,000) and per-segment text length limit (max 10,000 chars) to `POST /api/item/{path}/audio/transcript` to prevent DoS via oversized payloads.
- Added per-item in-flight lock on `POST /api/item/{path}/audio/analyze` to prevent duplicate concurrent Whisper/AI jobs on the same file.
- Added exponential backoff (max 5 min) to frontend connectivity poller so a slow or restarting backend is not hammered.
- Added item-not-found / deleted-item error state in `ItemDetailPage`: if the backend returns 404, a clear "Item not found" message is shown with a back button instead of a broken panel.
- Single-sourced `APP_VERSION` — bumped `app.py` to `4.5.3`; README, INSTALLATION.md, and ROADMAP all updated to match.
- Updated `docs/ROADMAP.md`: added v4.5.3 row; replaced completed items in recommended priorities with accurate next-up backlog (FAISS persistence, CDN vendoring, backup/restore 2.0, etc.).
- Added hardware and resource requirements section to `README.md` and `docs/INSTALLATION.md`.
- Added SQLite backup and recovery procedures to `docs/ADMINISTRATION.md` and `docs/TROUBLESHOOTING.md`.

## 4.5.2 — P0 Smoke Test + Transcript Save Endpoint

- Added `POST /api/item/{path}/audio/transcript` backend endpoint. Accepts edited `segments` list, validates each entry has a `text` field, writes back to `metadata_store`, rebuilds `transcript_text` and `transcript_script`, and immediately persists to SQLite. Transcript edits now survive restarts.
- Removed "Saved locally (server endpoint pending)" warning from `TranscriptView` — save errors now surface as proper error toasts.
- Added `docs/SMOKE_TEST.md` — comprehensive 11-section manual smoke test checklist covering install, wizard, auth, sources, AI pipeline, audio intelligence (including transcript save), library UX, AI Librarian, all pages, limp mode, and upgrade validation.

## 4.5.1 — AI Librarian + Audio Intelligence 2.0

- Added AI Librarian UI: **✨ Ask AI** button in the Library topbar opens a slide-in panel with natural-language search powered by `/api/librarian/ask`. Pre-loaded example prompts, conversation history, inline result rows, and filter explanation chips.
- Upgraded Audio tab to Audio Intelligence 2.0: segmented transcript view with timestamp · speaker · text per segment. Colour-coded speaker badges (S1–S4). Native or heuristic speaker mode labelled. Language, duration, and segment count metadata row.
- Added inline transcript editor: double-click any segment to edit text, commit with Enter, save all edits at once.
- Added plain text / segmented view toggle in the Audio tab.
- Stabilization: SetupWizard now calls `/api/auth/bootstrap` separately after `/api/setup/save` to correctly create the admin account.
- Stabilization: Connectivity banner correctly checks for `"online"` (actual backend value).
- Stabilization: `VideoCard` image `onError` clears src to prevent infinite reload loop.
- Documentation: full audit and update of all docs to v4.5.0; `ACCESS_CONTROL.md`, `USER_MANAGEMENT.md`, `TROUBLESHOOTING.md`, `TELEGRAM_SOURCES.md`, `SOURCE_SYNC.md` all updated. `epics.md` and `SPEC-phase1-foundation-classification.md` marked complete.

## 4.5.0 — Foundation + Classification Core

- Added browser-based 4-step setup wizard (Welcome, Telegram credentials with live validation, vault path, admin account). No terminal required for first-run configuration.
- Added NudeNet adult content classifier integrated into the AI pipeline. Produces structured `ai_signals`: scene composition, performer presentations, production type, POV, setting.
- Added configurable NudeNet confidence threshold (default 0.5, range 0.1–0.95) in Admin → Scoring.
- Added `ai_signals` JSON column and `audio_language` indexed column on the `videos` table (schema version 2).
- Added `video_tag_labels` table with `label_source` values: `ai_inferred`, `user_confirmed`, `user_corrected`, `user_added`.
- Added controlled act-tag vocabulary (Admin and Manager managed). Seed vocabulary: Oral, Anal, Vaginal, Handjob, Fingering, Bondage, BDSM, CNC, Watersports, PnP, Creampie, Squirting, Toys, Strap-on, Threesome, Gangbang, Orgy.
- Added user tag suggestion flow: inline single-tap input in Review Studio, Admin approval queue with approve/reject/merge actions.
- Added legacy tag migration assistant in Admin → Tag Management.
- Added `tag_mappings.json` in `base_path` for user-editable AI signal → display tag mappings.
- Added SRT and VTT subtitle export endpoints and detail-page buttons for transcribed videos.
- Stored Whisper-detected language as indexed `audio_language` column; filterable in advanced search.
- Added taxonomy search filters: scene composition, performer presentation, production type, setting, POV, language, and act tags.
- Added limp mode: auto-pauses Telegram-dependent jobs when internet is unreachable, connectivity banner in UI, auto-resumes on reconnection.
- Completed full frontend redesign: new dark palette, 240px sidebar, full-page ItemDetailPage with 5 tabs (Tags, Audio, Scenes, Similar, Info), grid/list library view, all admin pages.
- Service now runs as dedicated non-root `telesaved` system user; `.deb` service unit uses venv Python.
- Added `HOME` and `XDG_CACHE_HOME` environment variables to service unit and postinst script.
- Bumped `SQLITE_SCHEMA_VERSION` to 2. All schema changes applied by startup migration — no manual DB surgery required.

## 4.4.2 — Advanced Granular Search

- Added advanced metadata and natural-language search.
- Added filters by filename/path/prefix, date/date range, duration, source, tags, personal state, favorite, rating, watched, watch-later, and file status.
- Preserved hybrid semantic search behavior with CLIP/FAISS.

## 4.4.1 — LDAP Identity Backend

- Added LDAP login backend, LDAP test, group mapping, and user sync/import support.

## 4.4.0 — User Management, RBAC, and Source ACL

- Added local users, groups, permissions, login/logout, Admin bootstrap, and source access control.

## 4.3.x — Library Experience and Audio Quality

- Added Visual Library Map, favorites, ratings, personal states, command palette, timestamped transcript display, and Whisper quality improvements.

## 4.2.0 — Cleanup, Review, and Duplicate Intelligence

- Added Guided Cleanup Studio, Review Studio, advanced duplicate confidence scoring, and exact duplicate cleanup.

## 4.1.0 — Source and Telegram Intelligence

- Added Source Pipelines 2.0 and Telegram metadata preservation.

## 4.0.0 — Platform Foundation

- Added persistent job engine, first-run/admin foundations, and platform hardening.
