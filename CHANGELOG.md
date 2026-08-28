# Changelog

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
