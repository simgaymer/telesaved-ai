# Security Notes

TeleSaved handles Telegram credentials, local media, transcript data, and access-control data. Treat deployments as sensitive.

## Secrets

Do not commit:

- `config.json`
- Telethon session files
- `auth.json`
- runtime SQLite/JSON state
- logs
- downloaded media

## Network exposure

Run TeleSaved behind a trusted reverse proxy/VPN when possible. If exposing it outside a LAN, use HTTPS and strong authentication.

## Accounts

Keep at least one local emergency Admin account even when using LDAP. Review group mappings before enabling LDAP login broadly.

## Reporting issues

For private deployments, report security issues directly to the repository owner/maintainer rather than opening public issues.
