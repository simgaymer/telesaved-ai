# TeleSaved AI

TeleSaved AI is a self-hosted Telegram video archiving, indexing, tagging, review, and media-management application.

It downloads videos from selected Telegram sources, stores them in a local vault, generates thumbnails/previews, applies AI-assisted tagging, supports semantic and granular search, and provides administrative tools for source management, cleanup, duplicates, transcripts, backups, user access control, and library maintenance.

Current source version: **4.4.2 — Advanced Granular Search**.

## Major features

- Telegram Saved Messages, chat, group, and channel video ingestion.
- Source Pipelines 2.0 with per-source controls and Telegram metadata preservation.
- AI-assisted tagging with CLIP and YOLO.
- FAISS semantic search plus advanced granular metadata search.
- Search by filename, path, prefix, date/date range, duration, source, tags, personal state, favorite, rating, watched, watch-later, and file status.
- Thumbnails, hover previews, scene timelines, and video detail pages.
- Whisper-based audio analysis and timestamped transcript generation.
- Trash/restore workflow and exact/possible duplicate management.
- Guided cleanup, review studio, smart collections, ratings, favorites, and personal states.
- Persistent jobs page with queue/status tracking.
- Local multi-user login, groups, RBAC, and source access control.
- LDAP identity backend support.
- Debian package build script and systemd deployment support.

## Repository layout

```text
app.py                            aiohttp backend and application logic
frontend/index.html               React single-page application
build-telesaved-deb.sh             Debian package builder
config.example.json                safe example configuration without credentials
requirements.txt                   Python dependency list
install-ldap-deps.sh               helper for LDAP dependency install
systemd/telesaved.service          venv-based systemd unit example
scripts/install-telesaved-venv.sh  zero-touch venv installer for deployed systems
scripts/repair-whisper-cli.sh      Whisper repair helper
scripts/push-full-source-to-github.sh  live-source sync helper
docs/                             project documentation
```

## Quick start on Debian/Ubuntu

```bash
sudo mkdir -p /opt/telesaved
sudo cp -a app.py frontend build-telesaved-deb.sh config.example.json requirements.txt systemd scripts install-ldap-deps.sh /opt/telesaved/
cd /opt/telesaved
sudo cp config.example.json config.json
sudo nano config.json
sudo ./scripts/install-telesaved-venv.sh
```

Then open:

```text
http://SERVER_IP:8080/
```

On a fresh install, TeleSaved prompts for the first local Admin account.

## Configuration

`config.json` is intentionally ignored by Git because it contains Telegram API credentials. Use `config.example.json` as a template:

```json
{
  "api_id": 123456,
  "api_hash": "replace_with_your_telegram_api_hash",
  "base_path": "/mnt/rda1/.vault/telesaved"
}
```

The `base_path` is where TeleSaved stores state, logs, thumbnails, previews, transcripts, trash, backups, and by default downloaded media.

## Build a Debian package

```bash
chmod +x build-telesaved-deb.sh
./build-telesaved-deb.sh
sudo apt install ./dist/telesaved-ai_4.4.2_amd64.deb
```

By default, `config.json` is not bundled. For a private/internal build only:

```bash
TELESAVED_INCLUDE_CONFIG=1 ./build-telesaved-deb.sh
```

## Documentation

Start with:

- [Installation](docs/INSTALLATION.md)
- [Configuration](docs/CONFIGURATION.md)
- [Advanced Search](docs/ADVANCED_SEARCH.md)
- [User Management and RBAC](docs/USER_MANAGEMENT.md)
- [Telegram Sources](docs/TELEGRAM_SOURCES.md)
- [Audio and Whisper](docs/AUDIO_WHISPER.md)
- [Packaging](docs/PACKAGING.md)
- [Source Sync](docs/SOURCE_SYNC.md)

## Security notice

Never commit runtime credentials, Telegram session files, user/auth state, databases, logs, downloaded media, generated previews, transcripts, trash, or backups.

See [SECURITY.md](SECURITY.md).

## Credits

TeleSaved AI was authored by James "Jamie" Villegas with AI-assisted development support from ChatGPT. It builds on open-source projects including aiohttp, Telethon, PyTorch, OpenAI CLIP, Ultralytics YOLO, FAISS, Pillow, FFmpeg, Whisper, React, Babel, and ldap3.

## License

AGPL-3.0-only. See [LICENSE](LICENSE).
