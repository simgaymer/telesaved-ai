# TeleSaved AI

TeleSaved AI is a self-hosted Telegram video archiving, indexing, tagging, review, and media-management application.

It downloads videos from selected Telegram sources, stores them in a local vault, generates thumbnails/previews, applies AI-assisted tagging, supports semantic and granular search, and provides administrative tools for source management, cleanup, duplicates, transcripts, backups, user access control, and library maintenance.

Current packaged version: **4.4.2**.

## Major features

- Telegram Saved Messages, chat, group, and channel video ingestion.
- Source Pipelines 2.0 with per-source controls and Telegram metadata preservation.
- AI-assisted tagging with CLIP and YOLO.
- FAISS semantic search plus advanced granular metadata search.
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
app.py                         aiohttp backend and application logic
frontend/index.html            React single-page application
build-telesaved-deb.sh          Debian package builder
config.example.json             safe example configuration without credentials
requirements.txt                Python dependency list
install-ldap-deps.sh            helper for LDAP dependency install
systemd/telesaved.service       venv-based systemd unit example
scripts/install-telesaved-venv.sh  zero-touch venv installer for deployed systems
scripts/repair-whisper-cli.sh   Whisper repair helper, when available
docs/                          project documentation
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

- [Installation](docs/INSTALL.md)
- [Configuration](docs/CONFIGURATION.md)
- [Feature overview](docs/FEATURES.md)
- [Advanced granular search](docs/ADVANCED_SEARCH.md)
- [User management and RBAC](docs/ACCESS_CONTROL.md)
- [Administration guide](docs/ADMINISTRATION.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Roadmap](docs/ROADMAP.md)

## License and notices

TeleSaved AI is intended to be released under **AGPL-3.0-or-later** because it is a network-accessible application and currently uses Ultralytics YOLO, which is AGPL-3.0 unless separately licensed.

This repository contains a license notice. Before broad public redistribution, confirm dependency versions and preserve upstream notices and full license texts.

## Authorship

Primary author and product owner: **Jamie Villegas**.

OpenAI ChatGPT assisted with iterative architecture planning, Python/aiohttp backend code, React SPA UI code, feature design, debugging, packaging guidance, systemd/deployment workflows, and UX refinement. Jamie Villegas directed the application goals, accepted/rejected implementation choices, tested updates, and retains project stewardship.
