# Administration Guide

The Administration section contains operational views and controls accessible from the sidebar under Administration.

## Dashboard

Shows library statistics, source distribution, storage usage, and job status counts.

## Sources

Manage Telegram sources. Each source can be a channel, group, saved messages, or chat. Per-source settings include auto-download, pipeline controls, lookback window, and metadata preservation.

## Jobs

Tracks all background operations: downloads, index sweeps, audio analysis, duplicate scans, cleanup actions, backups, and reprocessing. Each job shows status and supports pause, resume, and cancel.

### Limp mode

When TeleSaved detects that Telegram is unreachable, it enters limp mode automatically:

- All active download and sync jobs are paused with reason `connectivity_lost`.
- A banner appears in the UI: "No internet connection — Telegram sync paused. Your library is still available."
- Connectivity is polled on a backoff schedule.
- When connectivity is restored, `connectivity_lost`-paused jobs resume automatically. Jobs manually paused by the user are not affected.

## Review Studio

Single-item sequential review workflow. Each video is presented one at a time with thumbnail, metadata, and AI tags. Actions:

- **Accept** — marks the item as reviewed.
- **Skip** — defers it without accepting.
- **+ Suggest Tag** — submits a new tag suggestion to the Admin approval queue (available to users with `tags.manage` permission).

## Tag Management

### Pending Suggestions

Lists tag suggestions submitted by users with `tags.manage` permission. For each suggestion:

- **Approve** — adds the tag to the vocabulary and records the originating video as `label_source: user_confirmed`.
- **Reject** — removes the suggestion; vocabulary unchanged.
- **Merge** — maps the suggestion to an existing canonical tag; originating video tagged with the canonical name.

### Display Mappings

JSON editor for `tag_mappings.json`. Maps `ai_signals` field values to human-readable display tags. Changes take effect after saving and reloading the library. File is also editable directly at `{base_path}/tag_mappings.json`.

### Legacy Migration

Bulk-assigns `label_source` values to legacy video tag assignments that predate v4.5.0. This is a one-time convenience tool — the app functions correctly without running it.

## Scoring

Configure AI pipeline weights and thresholds, including the NudeNet confidence threshold (default 0.5, range 0.1–0.95). Detections below the threshold are stored as `null`. A reset-to-defaults action is available.

## Duplicates

- Exact duplicates are SHA256 byte-for-byte matches.
- Possible duplicates use size/duration and optional visual similarity signals.
- Delete All Exact Duplicates keeps one copy per exact duplicate group and moves extras to Trash.

## Audio Intelligence

Whisper runs through the configured CLI path and writes diagnostics to `whisper.log`. Analysis is triggered per-video from the detail page Audio tab. Results include:

- Timestamped transcript viewable in the Audio tab.
- Detected language stored as `audio_language` (ISO 639-1), filterable in advanced search.
- SRT and VTT subtitle export buttons on the Audio tab.

## Users & Access

Create local users and groups, assign permissions, grant source access, configure LDAP, and test identity backend settings. See [User Management](USER_MANAGEMENT.md) and [Access Control](ACCESS_CONTROL.md).

## System

Runtime settings editor. Displays and edits all configurable values without editing `config.json` directly.

## Logs

System log viewer (live tail) and feedback viewer. Logs are also available at `{base_path}/telesaved.log`.

## Backups

Metadata backups include state/config-related data, not the original media files. Maintain independent media backups.

### What is backed up

- `telesaved.db` — all video metadata, tags, transcripts, ratings, watch-later, personal states.
- `config.json` — Telegram credentials and vault path.
- `settings.json` — runtime configuration overrides.
- `auth.json` — local user accounts and groups.
- `tag_mappings.json` — AI signal → display tag mappings.

Media files (the videos themselves) are **not** backed up — they live at `{base_path}` and must be backed up independently.

### Manual backup procedure

```bash
# Stop the service (optional but safe)
sudo systemctl stop telesaved

# Copy the critical metadata files
VAULT=/path/to/your/base_path
BACKUP=/path/to/backup/destination/$(date +%Y%m%d-%H%M%S)
mkdir -p "$BACKUP"
cp "$VAULT/telesaved.db"       "$BACKUP/"
cp /opt/telesaved/config.json  "$BACKUP/"
cp "$VAULT/settings.json"      "$BACKUP/" 2>/dev/null || true
cp "$VAULT/auth.json"          "$BACKUP/" 2>/dev/null || true
cp "$VAULT/tag_mappings.json"  "$BACKUP/" 2>/dev/null || true

sudo systemctl start telesaved
```

### Restore procedure

```bash
sudo systemctl stop telesaved

VAULT=/path/to/your/base_path
BACKUP=/path/to/backup/destination/20240101-120000  # your backup folder

cp "$BACKUP/telesaved.db"      "$VAULT/"
cp "$BACKUP/config.json"       /opt/telesaved/
cp "$BACKUP/settings.json"     "$VAULT/" 2>/dev/null || true
cp "$BACKUP/auth.json"         "$VAULT/" 2>/dev/null || true
cp "$BACKUP/tag_mappings.json" "$VAULT/" 2>/dev/null || true

sudo systemctl start telesaved
```

### Corrupted SQLite database

If `telesaved.db` becomes corrupted (service fails to start, logs show `database disk image is malformed`):

```bash
sudo systemctl stop telesaved

VAULT=/path/to/your/base_path

# Check integrity
sqlite3 "$VAULT/telesaved.db" "PRAGMA integrity_check;"

# Attempt recovery into a new database
sqlite3 "$VAULT/telesaved.db" ".recover" | sqlite3 "$VAULT/telesaved-recovered.db"
mv "$VAULT/telesaved.db" "$VAULT/telesaved.db.corrupt"
mv "$VAULT/telesaved-recovered.db" "$VAULT/telesaved.db"

sudo systemctl start telesaved
```

If recovery fails, restore from your last backup. Transcripts, tags, ratings, and watch-later history since the last backup will be lost; media files are unaffected.
