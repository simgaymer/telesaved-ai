# Audio and Whisper

TeleSaved can analyze audio and generate timestamped transcripts through the Whisper CLI.

## Triggering analysis

From any video's detail page, open the **Audio** tab and click **Analyze Audio**. The analysis is queued as a background job. The tab auto-refreshes while processing.

## Results

Once analysis completes, the Audio tab shows:

- **Detected language** — ISO 639-1 code (e.g. `en`, `es`) stored as `audio_language` and filterable in advanced search.
- **Transcript** — full timestamped text in a scrollable view.
- **Export SRT** — download a `.srt` subtitle file for use in VLC, mpv, or any media player.
- **Export VTT** — download a `.vtt` subtitle file for web video players.

Re-analysis can be triggered at any time by clicking **Re-analyze**.

## Configuration

Recommended runtime configuration in the systemd unit or override:

```ini
Environment=TELESAVED_WHISPER_BIN=/opt/telesaved/venv/bin/whisper
Environment=TELESAVED_WHISPER_DEVICE=cpu
Environment=TELESAVED_WHISPER_MODEL=tiny
Environment=TELESAVED_WHISPER_TIMEOUT=3600
Environment=CUDA_VISIBLE_DEVICES=
```

Whisper model choices (in ascending quality/speed tradeoff): `tiny`, `base`, `small`, `medium`, `large`.

## Troubleshooting

Check app logs:

```bash
sudo journalctl -u telesaved -f
```

Check Whisper diagnostics:

```bash
tail -200 {base_path}/whisper.log
```

If the Whisper CLI is broken:

```bash
cd /opt/telesaved
sudo ./scripts/repair-whisper-cli.sh
```

Verify the binary directly:

```bash
sudo /opt/telesaved/venv/bin/whisper --help | head
```
