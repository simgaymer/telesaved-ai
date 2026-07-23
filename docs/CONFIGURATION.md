# Configuration

TeleSaved reads `/opt/telesaved/config.json` by default. The safe template is `config.example.json`.

```json
{
  "api_id": 123456,
  "api_hash": "replace_with_your_telegram_api_hash",
  "base_path": "/mnt/rda1/.vault/telesaved"
}
```

## Important paths

- Application: `/opt/telesaved`
- Frontend: `/opt/telesaved/frontend/index.html`
- Local venv: `/opt/telesaved/venv`
- Default vault/base path: `/mnt/rda1/.vault/telesaved`
- SQLite metadata database: `telesaved.db`
- Logs: `telesaved.log`, `whisper.log`
- Runtime auth/user data: `auth.json`
- Jobs: `jobs.json`
- Trash: `trash/`
- Backups: `backups/`

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
