# Troubleshooting

## Service logs

```bash
sudo journalctl -u telesaved -n 200 --no-pager
sudo journalctl -u telesaved -f
```

## Application logs

```bash
tail -200 /mnt/rda1/.vault/telesaved/telesaved.log
tail -200 /mnt/rda1/.vault/telesaved/whisper.log
```

Adjust paths if `base_path` is different.

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

## Whisper fails

Verify the binary:

```bash
sudo /opt/telesaved/venv/bin/whisper --help | head
```

Use CPU mode for older or unsupported GPUs:

```text
TELESAVED_WHISPER_DEVICE=cpu
CUDA_VISIBLE_DEVICES=
```

## Search does not find expected files

Try fielded search syntax:

```text
name:document_2026-03-19
date:2026-03-19
source:"Saved Messages"
duration:>5m
```

If date or duration fields are missing, open Administration Dashboard or reprocess metadata to fill cached duration values.

## Missing durations

TeleSaved uses `ffprobe`. Confirm FFmpeg is installed:

```bash
ffprobe -version
```

## User locked out

Use a local emergency Admin account. If LDAP is misconfigured, local login should remain available unless explicitly disabled.
