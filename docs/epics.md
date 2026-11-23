---
stepsCompleted: [step-01, step-02, step-03, step-04, step-05]
status: done
shippedVersion: 4.5.0
shippedCommit: 7ab0c04
inputDocuments:
  - docs/SPEC-phase1-foundation-classification.md
  - docs/ROADMAP.md
  - docs/ACCESS_CONTROL.md
  - docs/AUDIO_WHISPER.md
  - docs/ADVANCED_SEARCH.md
  - app.py
---

# TeleSaved AI Phase 1 — Epic Breakdown

## Overview

This document provides the complete epic and story breakdown for TeleSaved AI Phase 1
(v4.5 — Foundation + Classification Core), decomposing the 14 capabilities from
`SPEC-phase1-foundation-classification.md` into implementable stories.

Source baseline: `app.py` v4.4.3, 10,515 lines. Target version: 4.5.0.

**Status: SHIPPED — v4.5.0 (commit 7ab0c04). All 6 epics, 18 stories, and 25 FRs delivered.**

---

## Requirements Inventory

### Functional Requirements

FR1: Service runs as non-root `telesaved` system user in all shipped configurations.
FR2: Debian package `.deb` service unit references venv Python, not system Python.
FR3: Browser-based 4-step setup wizard intercepts first launch when `config.json` is absent.
FR4: Setup wizard step 2 performs live Telegram API credential validation before advancing.
FR5: Setup wizard writes `config.json` atomically only after successful validation.
FR6: Every video record carries an `ai_signals` JSON column: `scene_composition`, `performer_presentations`, `production_type`, `pov_type`, `setting`.
FR7: NudeNet (MIT) is integrated as the adult content classifier in the AI pipeline.
FR8: NudeNet confidence threshold defaults to 0.5 and is configurable in Admin (range 0.1–0.95).
FR9: Detections below threshold are stored as `null`, not committed as low-confidence labels.
FR10: Admin and Manager groups can manage the act-tag vocabulary by default; custom groups configurable via `tags.manage`.
FR11: Initial seed vocabulary includes: Oral, Anal, Vaginal, Handjob, Fingering, Bondage, BDSM, CNC, Watersports, PnP, Creampie, Squirting, Toys, Strap-on, Threesome, Gangbang, Orgy.
FR12: Users with `tags.manage` can suggest new tags from Review Studio; suggestions enter an admin approval queue.
FR13: Approving a suggested tag adds it to vocabulary and records the originating video as `label_source: user_confirmed`.
FR14: A `video_tag_labels` table `(path, tag, label_source, created_at)` is added alongside existing `tags_json`.
FR15: `label_source` values: `ai_inferred`, `user_confirmed`, `user_corrected`, `user_added`.
FR16: A migration assistant tool in Admin → Tag Management lets users bulk-map legacy tags to `label_source`.
FR17: Whisper-detected language (ISO 639-1) is stored as an indexed `audio_language` column on the `videos` table.
FR18: Users can filter the library by `language:xx` in advanced search.
FR19: Transcribed videos expose "Export SRT" and "Export VTT" buttons on the detail page.
FR20: `tag_mappings.json` in `base_path` maps `ai_signals` values to human-readable display tags.
FR21: Display tag mappings are user-editable without a code change or restart.
FR22: Legal disclaimer is present in `README.md` and rendered on the wizard welcome screen.
FR23: Advanced search panel exposes filters for all `ai_signals` fields, `audio_language`, and act tags.
FR24: When internet is unavailable, app enters limp mode: Telegram jobs auto-pause, UI shows connectivity banner, library remains accessible.
FR25: Connectivity is polled on backoff; jobs resume automatically when connectivity is restored unless manually paused.

### Non-Functional Requirements

NFR1: Service must not run as root in any shipped config (systemd unit, .deb, documentation).
NFR2: NudeNet pipeline must complete on CPU-only hardware without CUDA as a hard dependency.
NFR3: `SQLITE_SCHEMA_VERSION` increments to 2; all schema changes apply via startup migration — no manual DB surgery.
NFR4: `config.json` writes are atomic (temp file + rename); existing pattern preserved.
NFR5: All new API endpoints enforce existing RBAC — 401 for unauthenticated, 403 for unauthorized. Setup routes (`/api/setup/*`) remain public.
NFR6: `config.json` not bundled in `.deb` unless `TELESAVED_INCLUDE_CONFIG=1` (existing behavior preserved).
NFR7: Limp mode preserves full local library functionality; no Telegram operations are queued or replayed during offline period.
NFR8: `tag_mappings.json` is human-editable JSON stored in `base_path`.

### Additional Requirements (from source review)

- `SQLITE_SCHEMA_VERSION = 1` at line 1822; migration routine is `init_sqlite_db()` — new columns added via `ALTER TABLE` gated on version.
- `/api/setup/status` and `/api/setup/save` exist and already write `config.json` atomically. New route `/api/setup/validate-telegram` performs live check without writing files.
- Tags stored as flat `tags_json TEXT` on `videos` table. `video_tag_labels` table is additive — no breaking change to existing rows.
- Whisper output already stored in `metadata_json`; `audio_language` extraction is a one-column migration + pipeline write.
- SRT/VTT conversion is a pure function over Whisper segments already in `metadata_json`.
- `APP_VERSION` bumps from `"4.4.3"` to `"4.5.0"` as part of this phase.
- Internet connectivity assumed available; limp mode is triggered by Telegram unreachable, not general network absence.

### UX Design Requirements

UX-DR1: Setup wizard renders as a full-screen overlay intercepting the normal app shell when `CONFIG_MISSING` is true — not a banner or a settings panel.
UX-DR2: Wizard step indicator shows current step (1 of 4) at the top of the overlay.
UX-DR3: Telegram credential validation on step 2 shows a spinner while the live check runs; inline error message on failure; green check + auto-advance on success.
UX-DR4: Vault path step shows available disk space inline.
UX-DR5: Connectivity banner is non-blocking (persists at top of UI, does not prevent library interaction).
UX-DR6: "Suggest a tag" flow in Review Studio is a single-tap inline input, not a modal navigation.
UX-DR7: Tag moderation queue in Admin is a distinct panel listing pending suggestions with approve/reject/merge actions.

### FR Coverage Map

FR1:  Epic 1 — Service runs as `telesaved` user
FR2:  Epic 1 — Debian package venv path fix
FR3:  Epic 2 — Setup wizard frontend intercepts first launch
FR4:  Epic 2 — Live Telegram validation route + wizard step 2
FR5:  Epic 2 — Atomic config write preserved
FR6:  Epic 3 — `ai_signals` DB column migration
FR7:  Epic 3 — NudeNet pipeline integration
FR8:  Epic 3 — NudeNet threshold Admin setting
FR9:  Epic 3 — Sub-threshold stored as null
FR10: Epic 4 — Tag vocabulary panel default permissions
FR11: Epic 4 — Seed vocabulary
FR12: Epic 4 — Tag suggestion flow in Review Studio
FR13: Epic 4 — Suggestion approval writes label row
FR14: Epic 4 — `video_tag_labels` table migration
FR15: Epic 4 — `label_source` values
FR16: Epic 4 — Migration assistant tool
FR17: Epic 5 — `audio_language` column migration + Whisper write
FR18: Epic 5 — Language filter in advanced search
FR19: Epic 5 — SRT/VTT export endpoint + detail page buttons
FR20: Epic 5 — `tag_mappings.json` loader
FR21: Epic 5 — Display tag derivation from `ai_signals`
FR22: Epic 2 — Legal disclaimer in README + wizard welcome screen
FR23: Epic 5 — Search filter panel extended for taxonomy fields
FR24: Epic 6 — Limp mode: auto-pause jobs + connectivity banner
FR25: Epic 6 — Connectivity poll + auto-resume

---

## Epic List

### Epic 1: Secure Deployment Foundation
Users can install TeleSaved from a `.deb` and run it as a dedicated non-root service user, with a working venv-based Python path.
**FRs covered:** FR1, FR2
**NFRs covered:** NFR1, NFR6

### Epic 2: Browser Setup Wizard
New users can complete the entire first-run setup — Telegram credentials, vault path, and Admin account — through a browser in under 5 minutes without touching a terminal.
**FRs covered:** FR3, FR4, FR5, FR22
**NFRs covered:** NFR4, NFR5
**UX-DRs covered:** UX-DR1, UX-DR2, UX-DR3, UX-DR4

### Epic 3: AI Classification Pipeline
Every ingested video is automatically classified by NudeNet — scene composition, performer presentation, production type, POV, and setting — with results stored in a structured `ai_signals` column and a user-configurable confidence threshold.
**FRs covered:** FR6, FR7, FR8, FR9
**NFRs covered:** NFR2, NFR3

### Epic 4: Contributable Tag Vocabulary
Admins and Managers can manage a controlled act-tag vocabulary. Users can suggest new tags from Review Studio. All tag assignments carry a `label_source` for future classifier training. Legacy data is migrated non-destructively via an assistant tool.
**FRs covered:** FR10, FR11, FR12, FR13, FR14, FR15, FR16
**NFRs covered:** NFR3
**UX-DRs covered:** UX-DR6, UX-DR7

### Epic 5: Transcript Intelligence & Taxonomy Search
Whisper language is stored as a structured field. Transcribed videos can export SRT/VTT subtitle files. AI-derived display tags are driven by a user-editable config. Advanced search exposes all taxonomy fields.
**FRs covered:** FR17, FR18, FR19, FR20, FR21, FR23
**NFRs covered:** NFR3, NFR8

### Epic 6: Limp Mode (Connectivity Resilience)
When internet is unavailable, Telegram-dependent jobs pause automatically and a non-blocking banner informs the user. The library remains fully browsable. Jobs resume automatically when connectivity is restored.
**FRs covered:** FR24, FR25
**NFRs covered:** NFR7
**UX-DRs covered:** UX-DR5

---

## Epic 1: Secure Deployment Foundation

Users can install TeleSaved from a `.deb` package, have it run as a dedicated non-root `telesaved` system user, and have the venv Python path correctly wired so the service starts cleanly.

### Story 1.1: Create `telesaved` system user in package install

As a system administrator,
I want the Debian package to create a `telesaved` system user automatically on install,
So that the service never runs as root.

**Acceptance Criteria:**

**Given** a system with no `telesaved` user
**When** `apt install ./telesaved-ai_4.5.0_amd64.deb` completes
**Then** a `telesaved` system user exists (`id telesaved` succeeds)
**And** the user has no login shell (`/bin/false` or `/usr/sbin/nologin`)
**And** the user has no home directory created

**Given** a system where `telesaved` user already exists
**When** the package is installed or upgraded
**Then** the existing user is left unchanged (idempotent)

### Story 1.2: Update service units to run as `telesaved` user

As a system administrator,
I want both the repo `systemd/telesaved.service` and the `.deb`-embedded service unit to specify `User=telesaved`,
So that the process never runs with root privileges.

**Acceptance Criteria:**

**Given** the installed service unit
**When** `systemctl show telesaved -p User` is run
**Then** the output is `User=telesaved`, not `User=root`

**Given** the service is started after install
**When** `ps aux | grep app.py` is run
**Then** the process owner is `telesaved`, not `root`

**Given** the `base_path` directory
**When** the service starts
**Then** `telesaved` user has read/write access to `/opt/telesaved` and `base_path`
**And** the postinst script runs `chown -R telesaved:telesaved /opt/telesaved`

### Story 1.3: Fix Debian package venv Python path

As a user installing from the `.deb`,
I want the embedded service unit to invoke the venv Python interpreter,
So that all ML dependencies are available and the service starts without missing-module errors.

**Acceptance Criteria:**

**Given** a clean `.deb` install with venv at `/opt/telesaved/venv`
**When** `systemctl start telesaved` is run
**Then** the service starts successfully with exit code 0

**Given** the embedded service unit in the `.deb`
**When** `ExecStart` is inspected
**Then** it reads `/opt/telesaved/venv/bin/python /opt/telesaved/app.py`, not `/usr/bin/python3 /opt/telesaved/app.py`

**Given** `aiohttp`, `telethon`, `clip`, `torch` are installed only in the venv
**When** the service starts
**Then** all imports succeed and no `ModuleNotFoundError` appears in `telesaved.log`

---

## Epic 2: Browser Setup Wizard

New users can complete the entire first-run setup through a 4-step browser wizard without touching a terminal. The wizard validates Telegram credentials live before writing any config.

### Story 2.1: Legal disclaimer in README and wizard welcome screen

As a new user,
I want to see a clear legal disclaimer before I begin setup,
So that I understand compliance with applicable laws is my responsibility.

**Acceptance Criteria:**

**Given** the project README
**When** it is read
**Then** a paragraph stating that compliance with applicable laws governing downloaded content is the user's sole responsibility is present

**Given** the wizard welcome screen (step 1)
**When** it renders
**Then** the same disclaimer text is visible before the "Get Started" button

### Story 2.2: Wizard intercepts first launch when config is missing

As a new user with no `config.json`,
I want the app to show a full-screen setup wizard when I open the browser,
So that I am guided through setup rather than seeing a broken or empty library.

**Acceptance Criteria:**

**Given** `config.json` does not exist
**When** the user navigates to `http://server:8080`
**Then** a full-screen 4-step wizard overlay renders, not the normal library shell

**Given** the existing `/api/setup/status` endpoint
**When** it returns `{ "setup_required": true }`
**Then** the frontend renders the wizard overlay

**Given** `config.json` already exists
**When** the user navigates to the app
**Then** the wizard does not appear and the normal library shell loads

### Story 2.3: Live Telegram credential validation route

As a developer / as the wizard,
I want a `/api/setup/validate-telegram` POST endpoint that tests credentials against the Telegram API without writing any files,
So that the wizard can gate step 2 on confirmed-working credentials.

**Acceptance Criteria:**

**Given** valid `api_id` and `api_hash` are posted to `/api/setup/validate-telegram`
**When** the endpoint attempts a Telethon connection
**Then** it returns `{ "ok": true }`
**And** no `config.json` is written

**Given** invalid credentials are posted
**When** the endpoint attempts connection
**Then** it returns `{ "ok": false, "error": "<reason>" }` with status 400
**And** no `config.json` is written

**Given** an unauthenticated caller
**When** the endpoint is called
**Then** it returns a response without requiring a session token (public route, consistent with `/api/setup/*`)

### Story 2.4: Wizard steps 1–4 frontend UI

As a new user,
I want to walk through a 4-step wizard (Welcome, Credentials, Vault Path, Admin Account) entirely in the browser,
So that I can complete setup without editing files or running commands.

**Acceptance Criteria:**

**Given** the wizard is open on step 1 (Welcome)
**When** the user clicks "Get Started"
**Then** the wizard advances to step 2
**And** a step indicator shows "Step 1 of 4" / "Step 2 of 4" etc. at each stage

**Given** the user is on step 2 (Credentials) and enters valid credentials
**When** they click "Validate & Continue"
**Then** a spinner appears, live validation runs against `/api/setup/validate-telegram`
**And** on success a green check appears and the wizard auto-advances to step 3
**And** on failure an inline error message appears and the wizard stays on step 2

**Given** the user is on step 3 (Vault Path)
**When** the step renders
**Then** available disk space for the entered path is shown inline
**And** a path writability check runs before allowing advance

**Given** the user is on step 4 (Admin Account)
**When** they submit a valid username and password
**Then** `config.json` is written atomically via `/api/setup/save`
**And** the Admin account is created via `/api/auth/bootstrap`
**And** the wizard closes and the library shell loads

**Given** the user clicks "Back" on any step
**When** they return to the previous step
**Then** previously entered values are preserved

---

## Epic 3: AI Classification Pipeline

Every ingested video is automatically classified for scene composition, performer presentation, production type, POV, and setting using NudeNet, with results in a structured DB column and a configurable confidence threshold.

### Story 3.1: DB migration — `ai_signals` column on `videos` table

As a developer,
I want the `videos` table to gain an `ai_signals` TEXT column via the startup migration,
So that classification results have a structured home without manual DB surgery.

**Acceptance Criteria:**

**Given** a database at schema version 1
**When** the app starts with the updated code
**Then** `SQLITE_SCHEMA_VERSION` is 2
**And** `SELECT ai_signals FROM videos LIMIT 1` executes without error
**And** existing rows have `ai_signals = NULL` (not lost or corrupted)

**Given** a fresh install with no database
**When** `init_sqlite_db()` runs
**Then** the `videos` table is created with the `ai_signals` column present from the start

### Story 3.2: NudeNet integration into AI pipeline

As a user whose videos are processed by the AI pipeline,
I want NudeNet to classify each video's scene composition and performer presentation,
So that videos are automatically tagged with meaningful content signals.

**Acceptance Criteria:**

**Given** a video that has been ingested and processed
**When** the AI pipeline completes
**Then** `ai_signals` on that video's DB row is a non-null JSON object
**And** it contains at least `scene_composition` and `performer_presentations` fields
**And** the pipeline completes without error on CPU-only hardware (no CUDA required)

**Given** NudeNet returns a detection with confidence below the configured threshold (default 0.5)
**When** the pipeline writes results
**Then** the corresponding `ai_signals` field is stored as `null`, not a low-confidence value

**Given** YOLO is still active for non-adult object tagging
**When** the pipeline runs
**Then** YOLO continues to contribute `people_count` and non-adult object signals
**And** NudeNet provides adult content signals independently

### Story 3.3: NudeNet confidence threshold setting in Admin

As an Admin,
I want to configure the NudeNet confidence threshold from the Admin settings panel,
So that I can tune classification sensitivity to my library without editing code.

**Acceptance Criteria:**

**Given** the Admin settings panel
**When** it loads
**Then** a NudeNet confidence threshold field is present, showing the current value (default 0.5)
**And** the field accepts values in the range 0.1–0.95

**Given** the threshold is changed and saved
**When** a video is reprocessed
**Then** NudeNet uses the new threshold value
**And** detections below the new threshold are stored as `null`

**Given** an invalid value (e.g. 1.5 or "abc") is submitted
**When** the save is attempted
**Then** an inline validation error is shown and the setting is not saved

---

## Epic 4: Contributable Tag Vocabulary

Admins and Managers manage a controlled act-tag vocabulary. Users suggest new tags from Review Studio. All tag assignments record their `label_source`. Legacy data migrates gracefully via an assistant tool.

### Story 4.1: DB migration — `video_tag_labels` table

As a developer,
I want a `video_tag_labels` table added to the database at startup migration,
So that tag assignments can carry `label_source` metadata without breaking existing `tags_json` data.

**Acceptance Criteria:**

**Given** a database at schema version 1 with existing video rows
**When** the app starts with updated code
**Then** a `video_tag_labels (path TEXT, tag TEXT, label_source TEXT, created_at TEXT)` table exists
**And** all existing `videos` rows are intact and `tags_json` is unchanged
**And** `SQLITE_SCHEMA_VERSION` is 2 (shared migration step with Story 3.1)

**Given** a query joining `videos` and `video_tag_labels` on `path`
**When** run against a database with legacy rows
**Then** legacy rows return no matching `video_tag_labels` rows (treated as `ai_inferred` by application logic)

### Story 4.2: Act-tag vocabulary management panel

As an Admin or Manager,
I want a Tag Management panel where I can add, edit, merge, and retire act tags,
So that the vocabulary stays clean and consistent across the library.

**Acceptance Criteria:**

**Given** the Admin panel
**When** a user with `tags.manage` permission opens Tag Management
**Then** the current vocabulary list is shown
**And** a form to add a new tag is available

**Given** a new tag name "Fisting" is submitted
**When** the form is saved
**Then** "Fisting" immediately appears in the vocabulary
**And** it is available as a selectable tag in Review Studio without restarting the service

**Given** a user without `tags.manage` permission
**When** they attempt to access Tag Management
**Then** they receive a 403 response and the panel is not rendered

**Given** an Admin merges two tags (e.g. "Blowjob" → "Oral")
**When** the merge is confirmed
**Then** all videos tagged "Blowjob" are updated to "Oral"
**And** "Blowjob" is removed from the vocabulary

### Story 4.3: Seed act-tag vocabulary on first run

As a new user,
I want the default act-tag vocabulary pre-loaded on first run,
So that I don't have to manually enter common tags before I can start using the library.

**Acceptance Criteria:**

**Given** a fresh install with no existing custom tags
**When** the app starts for the first time
**Then** the vocabulary contains at minimum: Oral, Anal, Vaginal, Handjob, Fingering, Bondage, BDSM, CNC, Watersports, PnP, Creampie, Squirting, Toys, Strap-on, Threesome, Gangbang, Orgy

**Given** an existing install with a custom vocabulary
**When** the app upgrades to 4.5.0
**Then** the existing vocabulary is preserved and seed tags are not duplicated

### Story 4.4: `label_source` written on new tag assignments

As a developer,
I want all new tag assignments to write a corresponding row to `video_tag_labels`,
So that every tag created after 4.5.0 has a recorded origin for training data use.

**Acceptance Criteria:**

**Given** the AI pipeline assigns a tag to a video
**When** the assignment is written to the DB
**Then** a row in `video_tag_labels` is created with `label_source = 'ai_inferred'`

**Given** a user manually adds a tag to a video in Review Studio
**When** the assignment is saved
**Then** a row is created with `label_source = 'user_added'`

**Given** a user corrects an AI-inferred tag (removes "Gay", adds "Straight")
**When** the correction is saved
**Then** the old `video_tag_labels` row for "Gay" is updated to `label_source = 'user_corrected'`
**And** a new row for "Straight" is created with `label_source = 'user_confirmed'`

### Story 4.5: User tag suggestion flow in Review Studio

As a user with `tags.manage` permission,
I want to suggest a new act tag directly from a video card in Review Studio,
So that I can contribute to the vocabulary without leaving my review workflow.

**Acceptance Criteria:**

**Given** a video card in Review Studio
**When** the user taps/clicks "+ Suggest tag"
**Then** an inline single-line input appears (not a modal navigation)

**Given** the user types "Pegging" and submits
**When** the suggestion is sent
**Then** it enters a pending queue visible to Admins
**And** a confirmation appears inline ("Suggestion submitted")
**And** the video card returns to its normal state

**Given** an Admin has not yet approved "Pegging"
**When** any user browses the tag vocabulary
**Then** "Pegging" does not appear as a selectable tag

### Story 4.6: Admin tag suggestion moderation queue

As an Admin,
I want a moderation queue showing all pending tag suggestions,
So that I can approve, reject, or merge them to maintain vocabulary quality.

**Acceptance Criteria:**

**Given** the Admin Tag Management panel
**When** pending suggestions exist
**Then** a "Pending Suggestions" section lists each suggestion with the suggesting user and originating video

**Given** an Admin approves "Pegging"
**When** approved
**Then** "Pegging" is added to the vocabulary
**And** the originating video gets a `video_tag_labels` row: `label_source = 'user_confirmed'`
**And** the suggestion is removed from the pending queue

**Given** an Admin rejects a suggestion
**When** rejected
**Then** the suggestion is removed from the queue
**And** the vocabulary is unchanged

**Given** an Admin merges a suggestion (e.g. "BJ" → existing "Oral")
**When** merged
**Then** the originating video is tagged with "Oral" with `label_source = 'user_confirmed'`
**And** "BJ" is not added to the vocabulary

### Story 4.7: Legacy tag migration assistant tool

As an Admin with an existing library pre-dating 4.5.0,
I want a migration assistant in Tag Management that lets me bulk-assign `label_source` values to untagged legacy assignments,
So that historical data can be promoted into the training dataset at my convenience.

**Acceptance Criteria:**

**Given** the Admin Tag Management panel
**When** legacy videos exist with no `video_tag_labels` rows
**Then** a "Legacy Tag Migration" section shows the count of untagged assignments

**Given** the Admin selects "Mark all legacy as ai_inferred"
**When** confirmed
**Then** all `videos` rows with tags in `tags_json` but no `video_tag_labels` row get rows written with `label_source = 'ai_inferred'`

**Given** the Admin selects individual tags to mark as `user_confirmed`
**When** confirmed
**Then** only those specific tag/video pairs get `label_source = 'user_confirmed'` rows

**Given** the migration tool has never been run
**When** the app functions normally
**Then** the app works correctly — the tool is a convenience, not a prerequisite

---

## Epic 5: Transcript Intelligence & Taxonomy Search

Whisper language is stored as a structured field. Transcribed videos export SRT/VTT. AI signals map to human-readable display tags via user-editable config. Advanced search covers all taxonomy dimensions.

### Story 5.1: `audio_language` column on `videos` table and pipeline write

As a user,
I want the language of a video's audio (detected by Whisper) stored as a searchable field,
So that I can filter my library by spoken language.

**Acceptance Criteria:**

**Given** the database migration runs on upgrade
**When** `SELECT audio_language FROM videos LIMIT 1` is executed
**Then** it succeeds without error and returns NULL for unprocessed videos

**Given** Whisper completes transcription on a video
**When** results are written to the DB
**Then** the detected ISO 639-1 language code is stored in the `audio_language` column
**And** `SELECT DISTINCT audio_language FROM videos` returns the populated codes

### Story 5.2: SRT and VTT subtitle export

As a user,
I want to download a Whisper transcript as an SRT or VTT subtitle file from the video detail page,
So that I can load subtitles in any media player or use them for other purposes.

**Acceptance Criteria:**

**Given** a video with a completed Whisper transcript
**When** the video detail page loads
**Then** "Export SRT" and "Export VTT" buttons are visible

**Given** the user clicks "Export SRT"
**When** the download response is received
**Then** a valid `.srt` file is downloaded with timestamped subtitle entries
**And** loading the file in VLC or mpv renders correctly timed subtitles

**Given** a video with no transcript
**When** the video detail page loads
**Then** the export buttons are not shown (or are disabled with tooltip "Transcript required")

### Story 5.3: `tag_mappings.json` config and display tag derivation

As an Admin,
I want AI signal values to automatically derive human-readable display tags (Gay, Lesbian, Straight, etc.) from a JSON config file I can edit,
So that display labels reflect my community's preferred terminology without code changes.

**Acceptance Criteria:**

**Given** `tag_mappings.json` does not exist in `base_path`
**When** the app starts
**Then** it is created with the default mapping set (Gay, Lesbian, Straight, Trans-inclusive, Solo, Group, POV, Outdoor, Public)

**Given** an Admin edits `tag_mappings.json` to rename "Gay" to "MLM" and restarts the service
**When** the library renders
**Then** videos previously displaying "Gay" now display "MLM"
**And** no code change was required

**Given** a video with `ai_signals.scene_composition = "duo_mm"`
**When** display tags are derived
**Then** the video shows the display tag mapped from that signal per the config

### Story 5.4: Advanced search filters for taxonomy fields

As a user,
I want the advanced search panel to expose filters for scene composition, performer presentation, production type, POV, setting, language, and act tags,
So that I can find specific content from any combination of those dimensions.

**Acceptance Criteria:**

**Given** the advanced search panel
**When** it opens
**Then** filter sections for Scene, Performers, Production, Setting, POV, Language, and Act Tags are present alongside all existing 4.4.2 filters

**Given** the user filters by `scene_composition: duo` AND `act_tag: Oral`
**When** results are returned
**Then** only videos matching both criteria are shown
**And** source ACL is enforced (users see only sources they have access to)

**Given** the user filters by `language: es`
**When** results are returned
**Then** only videos with `audio_language = 'es'` are shown

---

## Epic 6: Limp Mode (Connectivity Resilience)

When internet is unavailable, Telegram-dependent jobs pause automatically and a non-blocking banner informs the user. Local library functions remain fully operational. Jobs resume automatically on reconnection.

### Story 6.1: Connectivity detection and limp mode activation

As a user,
I want the app to detect when Telegram is unreachable and automatically pause all download and sync jobs,
So that the job queue doesn't accumulate errors while I'm offline.

**Acceptance Criteria:**

**Given** the app is running and Telegram becomes unreachable
**When** the connectivity check fires (polled on backoff schedule)
**Then** all active download and sync jobs are auto-paused with reason `"connectivity_lost"`
**And** no new Telegram-dependent jobs are started until connectivity is restored

**Given** connectivity is restored
**When** the next poll succeeds
**Then** jobs paused with reason `"connectivity_lost"` are automatically resumed
**And** jobs manually paused by the user are NOT auto-resumed

### Story 6.2: Connectivity banner in the UI

As a user,
I want a non-blocking banner to appear when the app detects no internet connectivity,
So that I know why Telegram sync has paused without losing access to my library.

**Acceptance Criteria:**

**Given** limp mode is active
**When** any page of the library is viewed
**Then** a banner at the top of the UI reads "No internet connection — Telegram sync paused. Your library is still available."
**And** the banner does not block interaction with the library, search, or playback

**Given** limp mode is active
**When** the user attempts to manually trigger a Telegram sync job
**Then** the action is rejected with an inline message "Telegram is currently unreachable"

**Given** connectivity is restored
**When** the next poll confirms connectivity
**Then** the banner disappears automatically
**And** a brief toast notification confirms "Connection restored — sync resumed"
