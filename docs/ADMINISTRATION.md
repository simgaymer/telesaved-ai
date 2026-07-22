# Administration Guide

The Administration section contains operational views and controls.

## Dashboard

Shows library statistics, source distribution, storage status, file existence, and duration coverage.

## Sources

Manage Telegram sources, per-source pipeline settings, media filters, lookback windows, metadata preservation, and source-level tags.

## Jobs

Tracks downloads, index sweeps, audio analysis, duplicate scans, cleanup actions, backups, rules, and reprocessing operations.

## Duplicates

- Exact duplicates are SHA256 byte-for-byte matches.
- Possible duplicates use size/duration and optional visual similarity signals.
- Delete All Exact Duplicates keeps one copy per exact duplicate group and moves extras to Trash.

## Cleanup Studio and Review Studio

Use these pages to prioritize review work, accept/edit tags, skip reviewed items, and handle cleanup candidates.

## Audio Intelligence

Whisper runs through the configured CLI path and writes diagnostics to `whisper.log`. Transcripts are stored in metadata and rendered on the detail page as timestamped script lines.

## Users & Access

Create local users/groups, assign permissions, grant source access, configure LDAP, and test identity backend settings.

## Backups

Metadata backups include state/config-related data, not the original media files. Maintain independent media backups.
