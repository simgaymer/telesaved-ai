# Changelog

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
