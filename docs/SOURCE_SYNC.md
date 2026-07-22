# Source Sync

The repository documentation and deployment scaffolding are initialized. The full runtime source files are:

```text
/opt/telesaved/app.py
/opt/telesaved/frontend/index.html
```

Use `scripts/push-full-source-to-github.sh` from the deployed TeleSaved host to synchronize the live source tree to this repository.

The script intentionally excludes local credentials, sessions, state databases, logs, media, thumbnails, previews, transcripts, backups, and trash.

## Expected source files

```text
app.py
frontend/index.html
build-telesaved-deb.sh
config.example.json
requirements.txt
install-ldap-deps.sh
systemd/telesaved.service
scripts/install-telesaved-venv.sh
scripts/repair-whisper-cli.sh
docs/*.md
README.md
CHANGELOG.md
SECURITY.md
NOTICE
LICENSE
```

## Important

Never commit `/opt/telesaved/config.json`, Telethon session files, media, runtime state, logs, or authentication data.
