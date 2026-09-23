# TeleSaved AI

TeleSaved AI is a self-hosted Telegram video archiving, indexing, tagging, review, and media-management application.

It downloads videos from selected Telegram sources, stores them in a local vault, generates thumbnails and previews, applies AI-assisted classification and tagging, supports semantic and granular search, and provides administrative tools for source management, cleanup, duplicates, transcripts, backups, user access control, and library maintenance.

Current source version: **4.5.15 — Tier 2 Modular Refactor: COMPLETE (auth.py, jobs.py, sources.py extracted)**.

> **Legal notice:** TeleSaved AI is a personal media archiving tool. Compliance with all applicable laws governing the downloading, possession, and use of content — including copyright, obscenity, and any other relevant statutes in your jurisdiction — is solely the responsibility of the user.

## Major features

- Telegram Saved Messages, chat, group, and channel video ingestion.
- Source Pipelines 2.0 with per-source controls and Telegram metadata preservation.
- Browser-based 4-step setup wizard — no terminal required for first-run configuration.
- AI-assisted tagging with CLIP, YOLO, and NudeNet adult content classification.
- Structured AI signals: scene composition, performer presentations, production type, POV, setting.
- Controlled act-tag vocabulary (Admin-managed) with user contribution and approval workflow.
- FAISS semantic search plus advanced granular metadata search.
- Search by filename, date, duration, source, tags, AI signals, language, personal state, favorite, rating, watched, watch-later, and file status.
- Thumbnails, hover previews, scene timelines, and full-page video detail with tabs.
- Whisper-based audio analysis, timestamped transcripts, SRT/VTT subtitle export, and language detection.
- Trash/restore workflow and exact/possible duplicate management.
- Guided cleanup, review studio, smart collections, ratings, favorites, and personal states.
- Persistent jobs page with queue/status tracking and per-job pause/resume/cancel.
- Limp mode: library stays accessible when Telegram is unreachable; jobs auto-pause and auto-resume.
- Local multi-user login, groups, RBAC, and source access control.
- LDAP identity backend support.
- Debian package build script and systemd deployment support (runs as dedicated non-root `telesaved` user).

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
docs/                             project documentation
```

## Quick start (Debian package — recommended)

```bash
# Build the package
chmod +x build-telesaved-deb.sh
./build-telesaved-deb.sh

# Install on target system
sudo apt install ./dist/telesaved-ai_4.5.15_all.deb
```

After installation:

1. Open `http://SERVER_IP:8080/` in a browser.
2. Complete the 4-step setup wizard: Welcome → Telegram credentials (validated live) → vault path → admin account.
3. Add a Telegram source under Administration → Sources.
4. Videos download and are processed automatically.

## Quick start (from source)

```bash
sudo mkdir -p /opt/telesaved
sudo cp -a app.py frontend build-telesaved-deb.sh config.example.json requirements.txt systemd scripts install-ldap-deps.sh /opt/telesaved/
cd /opt/telesaved
sudo ./scripts/install-telesaved-venv.sh
sudo systemctl start telesaved
```

Then open `http://SERVER_IP:8080/` — the setup wizard will guide you through configuration.

## Configuration

`config.json` is written by the setup wizard and is intentionally ignored by Git. Use `config.example.json` as a template if configuring manually:

```json
{
  "api_id": 123456,
  "api_hash": "replace_with_your_telegram_api_hash",
  "base_path": "/mnt/rda1/.vault/telesaved"
}
```

The `base_path` is where TeleSaved stores state, logs, thumbnails, previews, transcripts, trash, backups, and downloaded media.

## Build a Debian package

```bash
chmod +x build-telesaved-deb.sh
./build-telesaved-deb.sh
sudo apt install ./dist/telesaved-ai_4.5.15_all.deb
```

The package creates a dedicated non-root `telesaved` system user, installs the Python venv on first install, and registers a systemd service.

## System requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| OS | Debian 11 / Ubuntu 22.04 (x86\_64) | Debian 12 / Ubuntu 24.04 |
| Python | 3.10 | 3.11+ |
| RAM | 4 GB | 8 GB+ |
| CPU | 2 cores | 4+ cores |
| Disk | 20 GB free (OS + app + models) | 100 GB+ (media vault separate) |
| FFmpeg | 4.x | 6.x |
| GPU | Not required | CUDA GPU speeds up CLIP, NudeNet, and Whisper significantly |

**AI model disk footprint (one-time download on first use):**

| Model | Size |
|-------|------|
| CLIP ViT-B/32 | ~350 MB |
| YOLOv8n | ~6 MB |
| NudeNet | ~90 MB |
| Whisper `base` (default) | ~145 MB |
| Whisper `small` | ~465 MB |
| Whisper `medium` | ~1.5 GB |
| Whisper `large-v2` | ~2.9 GB |

Models are cached in `$XDG_CACHE_HOME` (defaults to `/home/telesaved/.cache` when running as the `telesaved` system user).

## Documentation

See the [`docs/`](docs/) directory for:

- [Installation](docs/INSTALLATION.md)
- [Configuration](docs/CONFIGURATION.md)
- [Features](docs/FEATURES.md)
- [Advanced Search](docs/ADVANCED_SEARCH.md)
- [Audio and Whisper](docs/AUDIO_WHISPER.md)
- [Administration](docs/ADMINISTRATION.md)
- [User Management and RBAC](docs/USER_MANAGEMENT.md)
- [Access Control](docs/ACCESS_CONTROL.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Packaging](docs/PACKAGING.md)
- [Scaling and Performance](docs/SCALING.md)
- [Roadmap](docs/ROADMAP.md)
