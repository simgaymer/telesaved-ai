---
id: SPEC-phase1-foundation-classification
status: done
shippedVersion: 4.5.0
shippedCommit: 7ab0c04
companions:
  - docs/ROADMAP.md
  - docs/ACCESS_CONTROL.md
  - docs/AUDIO_WHISPER.md
  - docs/ADVANCED_SEARCH.md
sources:
  - README.md
  - CHANGELOG.md
  - app.py
---

> **Canonical contract.** This SPEC and the files in `companions:` are the complete,
> preservation-validated contract for what to build, test, and validate. Source
> documents listed in frontmatter are for traceability — consult them only if you
> need narrative rationale or prose color this contract intentionally omits.
>
> **Status: SHIPPED — v4.5.0 (commit 7ab0c04). All 13 capabilities delivered.**

# Phase 1 — Foundation + Classification Core (v4.5)

## Why

TeleSaved AI is a self-hosted Telegram media vault for personal content archiving.
Its primary users download video content from Telegram and want it automatically
organized, categorized, and searchable without requiring technical expertise.
Internet connectivity is assumed at all times during normal use; the application
requires Telegram access for its primary function.

The materialized codebase (`app.py`, 10,515 lines) is now fully readable. Source
review reveals the app is already at internal version **4.4.3**. Three blockers
remain before new feature work is justified:

1. **Security** — the systemd service runs as `root` and the Debian package ships a
   broken service unit that points at the system Python instead of the venv.
2. **Onboarding** — the backend setup routes exist (`/api/setup/status`,
   `/api/setup/save`) but lack live Telegram credential validation and the frontend
   shows only a banner rather than a guided wizard flow.
3. **Classification gap** — the AI pipeline (CLIP + YOLO, lines 3060–3118) cannot
   classify adult content taxonomy. NudeNet is not yet integrated. The `ai_signals`
   DB column, `audio_language` column, and `video_tag_labels` table do not exist.

This spec closes all three before 4.5 ships. It also seeds the user-contributable
tag vocabulary, the hybrid `label_source` schema with a migration assistant, and
offline "limp mode" that gracefully degrades when internet is unavailable.

## Capabilities

- **CAP-1 — Dedicated service user**
  - **intent:** The systemd service runs as a non-root `telesaved` system user with
    write access only to `/opt/telesaved` and the configured `base_path`.
  - **success:** `systemctl show telesaved -p User` returns `telesaved`, not `root`.
    The service starts, ingests, and serves media without elevated privileges.

- **CAP-2 — Fixed Debian package venv path**
  - **intent:** The `.deb` postinst and embedded service unit reference
    `/opt/telesaved/venv/bin/python` (the venv interpreter), not `/usr/bin/python3`.
  - **success:** A clean `apt install ./telesaved-ai_4.5.x_amd64.deb` followed by
    `systemctl start telesaved` starts the app without a missing-module error.

- **CAP-3 — Setup wizard (first-run UI)**
  - **intent:** On first launch with no `config.json`, the app intercepts the browser
    session and presents a 4-step wizard: (1) Welcome + legal disclaimer, (2) Telegram
    credentials with **live API validation** before advancing, (3) vault path + free
    space check, (4) initial Admin account creation. `config.json` is written
    atomically only after successful validation. The existing `/api/setup/status` and
    `/api/setup/save` backend routes are extended; a new
    `/api/setup/validate-telegram` route performs the live credential check via
    Telethon without writing any files. Internet access and a Telegram account are
    product prerequisites — the wizard does not need to accommodate offline setup.
  - **success:** A user with zero shell experience completes setup and reaches the
    library in under 5 minutes using only a browser. Invalid credentials are rejected
    at step 2 with a clear inline error before `config.json` is written.

- **CAP-4 — AI signal fields on video records**
  - **intent:** Every video record in the database carries a structured `ai_signals`
    JSON column populated by the classification pipeline after ingestion:
    `scene_composition`, `performer_presentations`, `production_type`, `pov_type`,
    `setting`, `audio_language`.
  - **success:** After pipeline processing, querying the DB for any ingested video
    returns a non-null `ai_signals` object with at least `scene_composition` and
    `audio_language` populated.

- **CAP-5 — NudeNet classifier integration**
  - **intent:** The AI pipeline integrates NudeNet (MIT-licensed) as the scene
    composition and performer presentation classifier, replacing the stock YOLO
    object-detection pass for adult content signals. YOLO is retained for non-adult
    object tagging. The NudeNet confidence threshold defaults to **0.5** and is
    user-configurable via a setting in the Admin panel (range 0.1–0.95). Detections
    below the configured threshold are stored as `null` (unclassified) rather than
    committed as low-confidence labels.
  - **success:** After processing a video the `scene_composition` field is populated
    with a value from the defined vocabulary. The pipeline completes without error on
    CPU-only hardware. Changing the threshold setting and reprocessing a video
    reflects the new threshold without a code change.

- **CAP-6 — Controlled act-tag vocabulary (admin-managed)**
  - **intent:** The Tag Management panel is accessible to **Admin and Manager groups
    by default**. Per-group access to Tag Management is configurable via the granular
    group permissions system — any custom group can be granted or denied the
    `tags.manage` permission independently. Admins can define, edit, merge, and retire
    act tags. The initial seed vocabulary includes: Oral, Anal, Vaginal, Handjob,
    Fingering, Bondage, BDSM, CNC, Watersports, PnP, Creampie, Squirting, Toys,
    Strap-on, Threesome, Gangbang, Orgy.
  - **success:** An Admin can add "Fisting" to the vocabulary, and it immediately
    appears as a selectable tag in Review Studio without a code change or restart. A
    custom group with `tags.manage` granted can also access Tag Management; one with
    it revoked cannot.

- **CAP-7 — User tag contribution flow**
  - **intent:** Any user with `tags.manage` permission can suggest a new act tag from
    the Review Studio video card. Suggestions enter a pending queue visible to Admins.
    On approval the tag joins the vocabulary; the video that triggered the suggestion
    becomes the first confirmed training example for that tag (`label_source:
    user_confirmed`).
  - **success:** A non-admin user submits "Pegging" as a suggested tag; an Admin
    approves it; the tag appears in the vocabulary; the originating video has
    `label_source: user_confirmed` for that tag.

- **CAP-8 — `label_source` via hybrid `video_tag_labels` table**
  - **intent:** A new `video_tag_labels` table `(path, tag, label_source, created_at)`
    is added alongside the existing `tags_json` column. The two coexist: legacy rows
    without a `video_tag_labels` entry are treated as `ai_inferred` by default.
    New assignments (AI or user) write a label row. `label_source` values:
    `ai_inferred`, `user_confirmed`, `user_corrected`, `user_added`. When a user
    corrects an AI tag, the old label row is updated to `user_corrected` and the
    replacement gets `user_confirmed`. A **migration assistant tool** (accessible
    from Admin → Tag Management) lets users review legacy untagged assignments and
    map them to a `label_source` in bulk or individually — the tool is a convenience,
    not a requirement for the app to function.
  - **success:** The DB schema includes `video_tag_labels`. A query joining `videos`
    and `video_tag_labels` retrieves all `user_corrected` entries for training data
    export. Existing installs upgrade without data loss; legacy tags remain accessible
    and are treated as `ai_inferred`.

- **CAP-9 — Whisper language stored as structured field**
  - **intent:** The language code returned by Whisper (ISO 639-1) is stored as a
    dedicated indexed column on the video record, not just embedded in transcript JSON.
  - **success:** `SELECT DISTINCT audio_language FROM videos` returns human-readable
    language codes. Users can filter the library by `language:es` in advanced search.

- **CAP-10 — SRT / VTT subtitle export**
  - **intent:** Any video with a completed Whisper transcript can export its transcript
    as a standard `.srt` or `.vtt` subtitle file via a download button on the video
    detail page.
  - **success:** Clicking "Export SRT" on a transcribed video downloads a valid
    `.srt` file that a media player (VLC, mpv) correctly renders as timed subtitles.

- **CAP-11 — Display tag mappings (config-driven)**
  - **intent:** Human-readable display tags (Gay, Lesbian, Straight, Trans-inclusive,
    Solo, Group, POV, Outdoor, Public) are derived from `ai_signals` via a mapping
    config file (`tag_mappings.json`) that admins can edit without a code change.
  - **success:** Editing `tag_mappings.json` to rename "Gay" to "MLM" and restarting
    the service causes the library to display "MLM" without any code change.

- **CAP-12 — Legal disclaimer on first-run and README**
  - **intent:** The first-run wizard and the README both display a one-paragraph
    disclaimer: compliance with applicable laws governing downloaded content is the
    user's sole responsibility.
  - **success:** The disclaimer text is present in `README.md` and rendered on the
    setup wizard welcome screen.

- **CAP-13 — Updated search filters for taxonomy fields**
  - **intent:** The advanced search panel exposes filters for `scene_composition`,
    `performer_presentations`, `production_type`, `pov_type`, `setting`,
    `audio_language`, and act tags — in addition to all existing 4.4.2 filters.
  - **success:** A user can filter the library to `scene_composition: duo` AND
    `act_tag: Oral` and receive only matching results, with source ACL enforced.

## Constraints

- The service **must not run as root** in any shipped configuration (systemd unit,
  Debian package, or documentation example).
- NudeNet must run on **CPU-only hardware** without requiring CUDA. GPU acceleration
  is optional and must not be a hard dependency.
- The taxonomy schema (`ai_signals` col, `audio_language` col, `video_tag_labels`
  table) must be applied by the **startup migration routine** (`init_sqlite_db`) —
  no manual DB surgery required on upgrade. `SQLITE_SCHEMA_VERSION` increments to 2.
- `tag_mappings.json` must be **human-editable JSON** stored in `base_path`, not
  compiled into the application binary or frontend bundle.
- The setup wizard must write `config.json` **atomically** (write to temp, rename).
  The existing `api/setup/save` pattern already does this — it must be preserved.
- All new API endpoints must enforce **existing RBAC** — unauthenticated callers
  receive 401; callers without required permission receive 403. Setup routes
  (`/api/setup/*`) remain public (no auth) consistent with existing behaviour.
- The Debian package **must not bundle** `config.json` unless `TELESAVED_INCLUDE_CONFIG=1`
  is set (existing behavior, must be preserved).
- **Limp mode** — when the app detects no internet connectivity (Telegram
  unreachable), all download, sync, and Telegram-dependent jobs are auto-paused
  and the UI surfaces a connectivity banner. Existing library browsing, search,
  playback, and local admin functions remain fully operational. Connectivity is
  polled on a backoff schedule; jobs resume automatically when connectivity is
  restored unless manually paused by the user.

## Non-goals

- Mobile-responsive UI redesign — deferred to Phase 2.
- CLIP fine-tuning or custom classifier training pipeline — deferred to Phase 3.
- SSO / OIDC integration — deferred per roadmap.
- Age-presentation detection or any automated content safety classifier — explicitly
  out of scope; compliance is the user's responsibility (see CAP-12).
- GPU-accelerated NudeNet inference as a required feature — optional enhancement only.
- Diarization or speaker identification in transcripts — deferred to Audio
  Intelligence 2.0.
- Backup/restore of media files — metadata backup only, unchanged from 4.4.2.
- Tag synonym search (e.g. "blowjob" matching "Oral") — Admin merges aliases via
  Tag Management; synonym auto-expansion is not in scope for this phase.
- Offline-first or disconnected operation — limp mode preserves local access but
  does not attempt to queue or replay Telegram operations made while offline.
- Inlining or self-hosting CDN assets (React, Babel from unpkg) — internet is
  assumed available; CDN bundling is a Phase 2 polish item.

## Success signal

A non-technical user installs the `.deb`, navigates to `http://server:8080`,
completes the 4-step browser wizard without touching a terminal, adds a Telegram
source, and within one processing cycle sees at least one video in the library with
`scene_composition`, `audio_language`, and at least one AI-inferred display tag
populated — all while the service runs as the `telesaved` system user. If the
server loses internet mid-session, a connectivity banner appears and the library
remains fully browsable; Telegram sync resumes automatically when connectivity
returns.

## Assumptions

- NudeNet (MIT) is legally and practically suitable for integration; evaluation of
  its classification accuracy against the target taxonomy is part of CAP-5 delivery.
- The aiohttp backend can serve the wizard from the existing public `/api/setup/*`
  routes without exposing authenticated data or bypassing RBAC.
- `SQLITE_SCHEMA_VERSION` bump to 2 and `ALTER TABLE`/`CREATE TABLE` statements
  in `init_sqlite_db()` are sufficient for the migration — consistent with existing
  app patterns.
- `tag_mappings.json` is seeded with default display tag mappings on first run if
  it does not exist, via the same `load_json` / `save_json` pattern used elsewhere.
- Internet connectivity is available during all normal app operations. Limp mode
  is an edge-case graceful degradation, not a primary use case.
- The existing `APP_VERSION = "4.4.3"` in the materialized source supersedes the
  `4.4.2` referenced in the README; version will be bumped to `4.5.0` as part of
  this phase.

## Decisions

All open questions resolved by project owner:

- **OQ-1 → RESOLVED:** Setup wizard performs a **live Telegram API validation call**.
  Internet access and a Telegram account are prerequisites for the product; deferred
  validation provides no meaningful benefit.

- **OQ-2 → RESOLVED:** Tag Management is accessible to **Admin and Manager by
  default**. Group-level access is controlled by the granular `tags.manage` permission
  which can be granted to or revoked from any custom group. See CAP-6.

- **OQ-3 → RESOLVED:** NudeNet confidence threshold **defaults to 0.5**, is
  user-configurable in Admin settings (range 0.1–0.95), and is applied per-detection
  at pipeline runtime. See CAP-5.

- **OQ-4 → RESOLVED (live source review):** `label_source` uses a **hybrid approach**
  — new non-breaking `video_tag_labels` table alongside existing `tags_json`. Legacy
  rows without label entries are treated as `ai_inferred`. A migration assistant tool
  in Admin → Tag Management lets users bulk-assign `label_source` values to legacy
  data at their convenience. No forced migration on upgrade.

- **OQ-5 → RESOLVED (live source review):** CDN assets (React, Babel from unpkg)
  remain as-is. Internet is assumed available during all app use. Self-hosting CDN
  assets is deferred to Phase 2 polish.

- **OQ-6 → RESOLVED:** When internet connectivity is lost, the app enters **limp
  mode**: all Telegram-dependent jobs auto-pause, the UI shows a connectivity banner,
  and local library functions (browse, search, play, admin) continue uninterrupted.
  Connectivity is polled on backoff; jobs resume automatically on restoration unless
  manually paused.
