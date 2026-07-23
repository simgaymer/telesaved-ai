# Audio and Whisper

TeleSaved can analyze audio and generate timestamped transcript segments through the Whisper CLI.

Recommended runtime configuration:

```ini
Environment=TELESAVED_WHISPER_BIN=/opt/telesaved/venv/bin/whisper
Environment=TELESAVED_WHISPER_DEVICE=cpu
Environment=TELESAVED_WHISPER_MODEL=tiny
Environment=TELESAVED_WHISPER_TIMEOUT=3600
Environment=CUDA_VISIBLE_DEVICES=
```

## Troubleshooting

Check app logs:

```bash
sudo journalctl -u telesaved -f
```

Check Whisper diagnostics:

```bash
tail -200 /mnt/rda1/.vault/telesaved/whisper.log
```

If the Whisper CLI is broken:

```bash
cd /opt/telesaved
sudo ./scripts/repair-whisper-cli.sh
```
