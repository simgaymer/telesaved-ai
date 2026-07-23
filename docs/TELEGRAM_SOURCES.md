# Telegram Sources

TeleSaved can ingest videos from Saved Messages, chats, groups, and channels that the configured Telegram account can access.

## Source Profiles

Each source can define:

- enabled/sync enabled flags
- video download toggle
- auto-tag toggle
- destination subdirectory
- start date
- min/max size
- pipeline enabled flag
- pipeline mode
- max messages per run
- lookback days
- media filter
- Telegram metadata preservation settings

## Preserved metadata

When enabled, TeleSaved stores Telegram captions, sender identity, chat/source identity, forwards, replies, reactions, views, grouped message IDs, message links, and media details.
