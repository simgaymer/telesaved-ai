# Advanced Search

TeleSaved 4.4.2 adds granular search on top of semantic CLIP/FAISS search.

## Supported filters

- Filename, path, and prefix matching
- Exact date and date ranges
- Duration minimum/maximum
- Source title or source reference
- Required, optional, and excluded tags
- Personal state: Unreviewed, Watch Next, Keep, Maybe, Delete Candidate, Archived
- Favorite status
- Minimum rating
- Watched/unwatched
- Watch later
- Existing/missing file status

## Example searches

```text
document_2026-03-19
date:2026-03-19
source:"Saved Messages" duration:>5m
favorites rating:4
watch next from March 19 2026
```

The backend enforces source access controls before returning library results. Users only see results from sources they are allowed to access.
