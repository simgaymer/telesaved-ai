# Advanced Granular Search

TeleSaved 4.4.2 replaces the earlier search behavior with hybrid metadata, filename, filter, and semantic search.

## Searchable fields

- Filename and path
- Exact date and date ranges
- Duration minimum/maximum
- File size minimum/maximum
- Source title or source reference
- Required tags, any tags, and excluded tags
- Personal state
- Favorite status
- Rating range
- Watched/unwatched
- Watch-later status
- Existing/missing file status
- Semantic text query through CLIP/FAISS

## Fielded examples

```text
name:document_2026-03-19
filename:"document_2026-03-19"
date:2026-03-19
source:"Saved Messages"
duration:>5m
duration:<30s
duration_min:1:30 duration_max:10m
tag:Movie
anytag:Gay,Female
exclude:Archived
state:watch_next
favorite:true
rating:4
watched:false
watch_later:true
exists:true
```

## Plain-language examples

```text
videos from March 19 2026
watch next from Saved Messages
favorites rated 4 or higher
unwatched movies over 5 minutes
files starting with document_2026-03-19
```

## Sorting

Use the UI sort controls or natural phrases such as:

```text
largest first
shortest first
newest first
highest rated
```

## Notes

Advanced search is deterministic and local. It does not call an external LLM to parse the query.
