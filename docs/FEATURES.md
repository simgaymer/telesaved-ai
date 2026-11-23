# Feature Overview

## AI Librarian

- Natural-language search over your library via the **✨ Ask AI** button in the Library topbar.
- Type a plain-English question: "Show me long movies from Saved Messages", "Find videos tagged Oral sorted by newest", "Show duplicate candidates".
- The Librarian translates the prompt into filters and semantic search, returns matching videos inline, and explains which filters it applied.
- Results open directly in the full-page video detail view.
- Pre-loaded example prompts shown on first open.
- Conversation history is maintained within the session.

## Audio Intelligence 2.0

- **Segmented transcript view** in the Audio tab: each Whisper segment displayed as timestamp · speaker · text.
- **Speaker labels** — colour-coded badges per speaker. Native speaker IDs from the engine are used when available; otherwise a turn-taking heuristic assigns `S1`, `S2`, etc.
- **Inline transcript editor** — double-click any segment to edit its text. Commit with Enter or the ✓ button. Save all edits at once.
- **Plain text toggle** — switch between segmented and plain transcript views.
- Segment count, audio duration, and language shown in a metadata row above the transcript.

## Library and ingestion

- Download videos from Telegram Saved Messages, chats, groups, and channels.
- Configure multiple Telegram sources with per-source pipeline controls.
- Track downloads and processing operations as Jobs with pause/resume/cancel.
- Store file metadata including source, duration, size, hashes, resolution, and Telegram message metadata.
- Trash/restore workflow — no immediate destructive deletes.
- Smart collections group videos by tag, source, or search query.
- Watch Later and personal states (ratings, notes, favorites, watched tracking).

## AI media intelligence

- Extract frames with FFmpeg.
- Generate thumbnails and hover preview GIFs.
- Use CLIP embeddings for semantic search and similarity.
- Use YOLO for object/person-assisted tagging.
- Use NudeNet for adult content classification: scene composition, performer presentations, production type, POV, and setting.
- Use FAISS for vector search and similar-video recommendations.
- Use Whisper for audio transcription, language detection, and SRT/VTT subtitle export.

## Classification and tagging (v4.5.0)

- Structured `ai_signals` column: `scene_composition`, `performer_presentations`, `production_type`, `pov_type`, `setting`.
- `audio_language` column stores ISO 639-1 language code from Whisper detection.
- Configurable NudeNet confidence threshold (default 0.5, range 0.1–0.95) in Admin → Scoring.
- Controlled act-tag vocabulary managed by Admin/Manager groups.
- Seed vocabulary: Oral, Anal, Vaginal, Handjob, Fingering, Bondage, BDSM, CNC, Watersports, PnP, Creampie, Squirting, Toys, Strap-on, Threesome, Gangbang, Orgy.
- User tag contribution flow: inline suggestion in Review Studio, Admin approval queue.
- `label_source` tracking on all tag assignments: `ai_inferred`, `user_confirmed`, `user_corrected`, `user_added`.
- `tag_mappings.json` in `base_path`: user-editable config maps AI signal values to human-readable display tags.
- Legacy tag migration assistant in Admin → Tag Management.

## Administration

- Dashboard: stats, storage bar, by-source breakdown.
- Sources: Telegram source management with per-source pipeline settings.
- Review Studio: single-item sequential review with accept/skip and inline tag suggestion.
- Tag Management: vocabulary management, pending suggestion queue (approve/reject/merge), display tag mappings editor, legacy migration tool.
- Scoring: AI pipeline weight and threshold configuration.
- Users & Groups: local user management, RBAC, LDAP backend.
- System: runtime settings editor.
- Logs: service logs and feedback viewer.
- Jobs: queue with pause/resume/cancel per job.
- Limp mode: when Telegram is unreachable, jobs auto-pause and a banner notifies the user; jobs auto-resume on reconnection.

## Review and cleanup

- Trash/restore instead of immediate destructive delete.
- Exact duplicate cleanup.
- Possible duplicate review with confidence and signals.
- Review Studio and Cleanup Studio.

## Search

TeleSaved 4.5.0 includes hybrid advanced search with the following filters:

- Filename, path, and prefix
- Date and date ranges
- Duration minimum/maximum
- Source
- Tags (required, optional, excluded)
- Act tags
- AI signals: scene composition, performer presentation, production type, POV, setting
- Audio language (ISO 639-1)
- Personal state, favorite, rating, watched/unwatched, watch-later
- File existence status
- Minimum score threshold
