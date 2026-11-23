# Source Sync

The repository contains the full application source. Runtime state, credentials, and media are never committed.

## Source files in this repository

```text
app.py                            backend (aiohttp monolith)
frontend/index.html               React SPA
build-telesaved-deb.sh            Debian package builder
config.example.json               safe example config (no credentials)
requirements.txt                  Python dependencies
install-ldap-deps.sh              LDAP dependency helper
systemd/telesaved.service         venv-based systemd unit
scripts/install-telesaved-venv.sh zero-touch venv installer
scripts/repair-whisper-cli.sh     Whisper repair helper
docs/*.md                         project documentation
README.md
CHANGELOG.md
SECURITY.md
NOTICE
LICENSE
```

## Files that must never be committed

```text
config.json               Telegram credentials
*.session                 Telethon session files
auth.json                 user accounts and sessions
state.json / jobs.json    runtime queue state
telesaved.db              SQLite database
*.db-wal / *.db-shm       SQLite WAL files
telesaved.log             application log
whisper.log               Whisper analysis log
media/                    downloaded video files
thumbnails/               generated thumbnails
previews/                 hover preview GIFs
frames/                   extracted frame images
transcripts/              audio transcript files
trash/                    trashed items
backups/                  metadata backups
tag_mappings.json         user-edited AI tag mappings (runtime config)
```

## Working branch

Active development is on `agent/restore-telesaved-source`. Merge to `main` for releases.
