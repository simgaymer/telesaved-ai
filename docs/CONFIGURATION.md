# Configuration

TeleSaved reads `/opt/telesaved/config.json` first. If no file exists, it can also read environment variables.

## config.json

```json
{
  "api_id": 123456,
  "api_hash": "replace_with_your_telegram_api_hash",
  "base_path": "/mnt/rda1/.vault/telesaved"
}
```

## Environment variables

- `TELESAVED_API_ID`
- `TELESAVED_API_HASH`
- `TELESAVED_BASE_PATH`
- `TELESAVED_WHISPER_BIN`
- `TELESAVED_WHISPER_DEVICE`
- `TELESAVED_WHISPER_MODEL`
- `TELESAVED_WHISPER_TIMEOUT`

## Storage paths

Under `base_path`, TeleSaved maintains:

```text
telesaved.db
state.json
settings.json
feedback.json
auth.json
jobs.json
telesaved.log
whisper.log
frames/
previews/
thumbs/
scenes/
audio/
trash/
backups/
telegram_metadata/
```

Do not commit these runtime files to Git.

## Telegram setup

Create Telegram API credentials through Telegram's developer portal and place them in `config.json`. The application uses Telethon and stores the Telegram session under `/opt/telesaved`.

## Whisper setup

The recommended deployment points TeleSaved to:

```text
/opt/telesaved/venv/bin/whisper
```

The systemd unit sets:

```text
TELESAVED_WHISPER_DEVICE=cpu
CUDA_VISIBLE_DEVICES=
```

This avoids CUDA kernel incompatibilities on older NVIDIA GPUs.
