# Security Policy

TeleSaved AI is a self-hosted application and may index sensitive media and Telegram metadata.

## Do not commit secrets or runtime data

Never commit:

- `config.json`
- Telegram `.session` files
- `auth.json`
- `state.json`, `settings.json`, `feedback.json`, `jobs.json`
- `telesaved.db` and SQLite sidecar files
- logs
- downloaded media
- thumbnails, previews, scene images, audio/transcript data
- trash and backups

## Authentication

Create a strong local Admin password on first run. If LDAP is enabled, keep a local emergency Admin account available.

## Network exposure

TeleSaved is intended for trusted self-hosted environments. Place it behind a reverse proxy, VPN, or access-controlled network boundary before exposing it beyond a LAN.

## Reporting issues

For private deployments, report security issues directly to the repository owner/maintainer rather than opening a public issue containing secrets, logs, or media paths.
