# Telegram Sources

TeleSaved can ingest videos from Saved Messages, chats, groups, and channels that the configured Telegram account can access.

## Adding a source

Sources are managed in Administration → Sources. Click **+ Add Source** and provide:

- **Name** — a display name for the source.
- **Chat ID / Username** — the Telegram chat ID, username (`@channelname`), or phone number.
- **Type** — `channel`, `group`, `saved` (Saved Messages), or `chat`.
- **Auto-download** — whether new messages are automatically downloaded.

## Source Profiles

Each source can define:

- Enabled / sync enabled flags
- Video download toggle
- Auto-tag toggle
- Destination subdirectory within `base_path`
- Start date (only sync messages after this date)
- Min / max file size filters
- Pipeline enabled flag and pipeline mode
- Max messages per run
- Lookback days
- Media filter
- Telegram metadata preservation settings

## Preserved metadata

When enabled, TeleSaved stores Telegram captions, sender identity, chat/source identity, forwards, replies, reactions, views, grouped message IDs, message links, and media details.

## Limp mode

When Telegram is unreachable, all active download and sync jobs are automatically paused. A banner appears in the UI. Jobs resume automatically when connectivity is restored. See [Administration Guide](ADMINISTRATION.md#limp-mode) for details.

## Authorizing Telegram

After installing from the `.deb`, authorize the Telegram session:

```bash
sudo telesaved-telegram-login
```

This step is required once per install before downloads can begin.
