# Troubleshooting

## Service logs

```bash
sudo journalctl -u telesaved -n 200 --no-pager
sudo journalctl -u telesaved -f
```

## Application logs

Adjust `{base_path}` to your configured vault path (default `/opt/telesaved/media`):

```bash
tail -200 {base_path}/telesaved.log
tail -200 {base_path}/whisper.log
```

## Missing Python modules

Verify the service uses the venv:

```bash
sudo systemctl show telesaved -p ExecStart -p Environment
sudo /opt/telesaved/venv/bin/python -c "from aiohttp import web; import telethon, whisper; print('OK')"
```

Reinstall dependencies:

```bash
cd /opt/telesaved
sudo ./scripts/install-telesaved-venv.sh
```

## NudeNet / AI pipeline fails

Verify NudeNet is installed in the venv:

```bash
sudo /opt/telesaved/venv/bin/python -c "from nudenet import NudeDetector; print('NudeNet OK')"
```

If not installed:

```bash
sudo /opt/telesaved/venv/bin/pip install nudenet>=3.4
```

NudeNet runs on CPU only by default. To confirm no GPU is being attempted:

```bash
sudo systemctl show telesaved -p Environment
# Should include: CUDA_VISIBLE_DEVICES=
```

## Whisper fails

Verify the binary:

```bash
sudo /opt/telesaved/venv/bin/whisper --help | head
```

Use CPU mode for older or unsupported GPUs:

```ini
Environment=TELESAVED_WHISPER_DEVICE=cpu
Environment=CUDA_VISIBLE_DEVICES=
```

If the Whisper CLI is broken:

```bash
cd /opt/telesaved
sudo ./scripts/repair-whisper-cli.sh
```

## Search does not find expected files

Try fielded search syntax:

```text
name:document_2026-03-19
date:2026-03-19
source:"Saved Messages"
duration:>5m
```

For taxonomy filters (v4.5.0+), use the **⚙ Filters** panel in the Library or the **✨ Ask AI** button.

If date or duration fields are missing, open Administration → Dashboard or reprocess the affected items to fill cached values.

## Missing durations

TeleSaved uses `ffprobe`. Confirm FFmpeg is installed:

```bash
ffprobe -version
```

## Setup wizard times out on step 2 (air-gapped / offline systems)

Step 2 of the setup wizard makes a live outbound connection to Telegram to validate your API credentials. On isolated or air-gapped networks this will time out after 10 seconds.

**Workaround:** Use the offline bypass:

1. Enter your **API ID** and **API hash** on step 2 (phone number not required for skip).
2. Click **"Skip validation (offline / air-gapped) →"** — the small link beneath the "Validate & Continue" button.
3. Continue through the vault path and admin account steps as normal.

Telegram sync will be unavailable until network access is restored. Jobs requiring Telegram will fail until the service can reach the Telegram API.

## tag_mappings.json not loading

The file must be valid JSON and must exist in `base_path`. Verify:

```bash
python3 -m json.tool {base_path}/tag_mappings.json
```

If missing, restart the service — it will be recreated with defaults.

## User locked out

Use a local emergency Admin account. If LDAP is misconfigured, local login remains available unless explicitly disabled. The setup wizard created the initial Admin account; use those credentials.

## Corrupted SQLite database

If the service fails to start and logs show `database disk image is malformed` or `SQLite error`:

```bash
VAULT=/path/to/your/base_path

# Check integrity first
sqlite3 "$VAULT/telesaved.db" "PRAGMA integrity_check;"

# Recover into a new database
sqlite3 "$VAULT/telesaved.db" ".recover" | sqlite3 "$VAULT/telesaved-recovered.db"
sudo systemctl stop telesaved
mv "$VAULT/telesaved.db" "$VAULT/telesaved.db.corrupt"
mv "$VAULT/telesaved-recovered.db" "$VAULT/telesaved.db"
sudo systemctl start telesaved
```

If recovery fails, restore from a backup. See the [Backup and Restore](ADMINISTRATION.md#backups) section in the Administration Guide for the full procedure.

## Service running as root (pre-4.5.0 installs)

Verify the service user after upgrading to 4.5.0:

```bash
sudo systemctl show telesaved -p User
# Should return: User=telesaved
```

If it still shows `root`, reinstall the `.deb` or manually update the service unit and run `systemctl daemon-reload && systemctl restart telesaved`.
