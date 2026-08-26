---
id: SPEC-phase1-foundation-classification
companions:
  - docs/ROADMAP.md
  - docs/ACCESS_CONTROL.md
  - docs/AUDIO_WHISPER.md
  - docs/ADVANCED_SEARCH.md
sources:
  - README.md
  - CHANGELOG.md
---

> **Canonical contract.** This SPEC and the files in `companions:` are the complete,
> preservation-validated contract for what to build, test, and validate. Source
> documents listed in frontmatter are for traceability — consult them only if you
> need narrative rationale or prose color this contract intentionally omits.

# Phase 1 — Foundation + Classification Core (v4.5)

## Why

TeleSaved AI is a self-hosted Telegram media vault for personal content archiving.
Its primary users download video content from Telegram and want it automatically
organized, categorized, and searchable without requiring technical expertise.

The current codebase (v4.4.2) has three blockers before any new feature work is
justified:

1. **Security** — the systemd service runs as `root` and the Debian package ships a
   broken service unit that points at the system Python instead of the venv.
2. **Onboarding** — setup requires manual JSON editing and shell commands; there is
   no guided first-run experience for non-technical users.
3. **Classification gap** — the AI pipeline uses stock CLIP + YOLO, which cannot
   classify adult content taxonomy (scene composition, performer presentation,
   act type). The taxonomy and its data model do not exist yet.

This spec closes all three before 4.5 ships. It also seeds the user-contributable
tag vocabulary and the labeled-data pipeline that will feed future classifier
fine-tuning.

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
  - **intent:** On first launch with no `config.json`, the app presents a browser-based
    setup wizard that collects Telegram `api_id` / `api_hash`, performs a **live
    validation call against the Telegram API** before writing config, sets `base_path`,
    writes `config.json` atomically, and prompts for the initial Admin account — all
    without touching a terminal. (Users are assumed to have internet access and an
    active Telegram account as prerequisites for the product.)
  - **success:** A user with zero shell experience can complete setup and reach the
    library in under 5 minutes using only a browser. Invalid credentials are rejected
    with a clear error before `config.json` is written.

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

- **CAP-8 — `label_source` field on tag assignments**
  - **intent:** Every tag–video association carries a `label_source` value:
    `ai_inferred`, `user_confirmed`, `user_corrected`, or `user_added`. When a user
    corrects an AI-assigned tag the original is marked `user_corrected` and the
    replacement is marked `user_confirmed`.
  - **success:** The DB schema includes `label_source` on the tag-assignment join
    table. A query can retrieve all `user_corrected` assignments for export as
    training data.

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
- The taxonomy schema (`ai_signals` fields + `label_source`) must be **database-
  migrated** — no manual DB surgery required on upgrade.
- `tag_mappings.json` must be **human-editable JSON** stored in `base_path`, not
  compiled into the application binary or frontend bundle.
- The setup wizard must write `config.json` **atomically** (write to temp, rename)
  to prevent a corrupted config on crash or power loss.
- All new API endpoints must enforce **existing RBAC** — unauthenticated callers
  receive 401; callers without required permission receive 403.
- The Debian package **must not bundle** `config.json` unless `TELESAVED_INCLUDE_CONFIG=1`
  is set (existing behavior, must be preserved).

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

## Success signal

A non-technical user opens a fresh Debian install, runs `apt install
./telesaved-ai_4.5.x_amd64.deb`, navigates to `http://server:8080`, completes the
browser setup wizard without touching a terminal, adds a Telegram source, and within
one processing cycle sees at least one video in the library with `scene_composition`,
`audio_language`, and at least one AI-inferred display tag populated — all while the
service runs as the `telesaved` system user.

## Assumptions

- NudeNet (MIT) is legally and practically suitable for integration; evaluation of
  its classification accuracy against the target taxonomy is part of CAP-5 delivery.
- The aiohttp backend can serve the setup wizard from a pre-auth route without
  exposing any sensitive data or bypassing RBAC for post-setup requests.
- SQLite migration can be handled by a startup migration routine (not a separate
  migration tool) consistent with existing app.py patterns.
- `tag_mappings.json` will be seeded with the agreed default display tag mappings on
  first run if it does not already exist.

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
