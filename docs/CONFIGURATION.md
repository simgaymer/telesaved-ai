# Configuration

TeleSaved reads `/opt/telesaved/config.json` by default. On first launch, the browser-based setup wizard writes this file automatically. The safe template is `config.example.json`.

```json
{
  "api_id": 123456,
  "api_hash": "replace_with_your_telegram_api_hash",
  "base_path": "/mnt/rda1/.vault/telesaved"
}
```

## Important paths

| Path | Purpose |
|------|---------|
| `/opt/telesaved/` | Application root |
| `/opt/telesaved/frontend/index.html` | React SPA |
| `/opt/telesaved/venv/` | Python virtual environment |
| `{base_path}/telesaved.db` | SQLite metadata database |
| `{base_path}/telesaved.log` | Application log |
| `{base_path}/whisper.log` | Whisper analysis log |
| `{base_path}/auth.json` | User and session data |
| `{base_path}/jobs.json` | Job queue state |
| `{base_path}/tag_mappings.json` | AI signal → display tag mappings (user-editable) |
| `{base_path}/trash/` | Trashed items |
| `{base_path}/backups/` | Metadata backups |

## tag_mappings.json

`tag_mappings.json` is created on first run in `base_path` with default display tag mappings. It maps `ai_signals` field values to human-readable display tags shown in the library and detail pages.

Edit this file and restart the service to change display tag labels without any code changes.

Default mappings include: Gay, Lesbian, Straight, Trans-inclusive, Solo, Group, POV, Outdoor, Public.

## NudeNet threshold

The NudeNet confidence threshold controls how certain the classifier must be before writing an AI signal. The default is `0.5`. Detections below the threshold are stored as `null` rather than committed as low-confidence labels.

Change this value in Administration → Scoring without editing any files.

## Database schema

`SQLITE_SCHEMA_VERSION = 2`. Schema migrations run automatically at startup:

- `ai_signals` TEXT column on `videos` table
- `audio_language` TEXT indexed column on `videos` table
- `video_tag_labels (path, tag, label_source, created_at)` table

## Environment overrides

Useful systemd overrides include:

```ini
Environment=TELESAVED_WHISPER_BIN=/opt/telesaved/venv/bin/whisper
Environment=TELESAVED_WHISPER_DEVICE=cpu
Environment=TELESAVED_WHISPER_MODEL=tiny
Environment=TELESAVED_WHISPER_TIMEOUT=3600
Environment=CUDA_VISIBLE_DEVICES=
```

Keep `config.json`, session files, databases, logs, media, and runtime state out of Git.
