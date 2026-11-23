# Advanced Search

TeleSaved 4.5.0 includes hybrid advanced search combining CLIP/FAISS semantic search with granular metadata filters.

## Supported filters

### Core metadata
- Filename, path, and prefix matching
- Exact date and date ranges
- Duration minimum/maximum
- Source title or source reference
- File existence status (existing / missing)

### Tags
- Required, optional, and excluded tags
- Act tags from the controlled vocabulary (e.g. `Oral`, `Anal`)

### AI taxonomy (v4.5.0)
- Scene composition (`scene_composition`)
- Performer presentation (`performer_presentation`)
- Audio language (ISO 639-1 code, e.g. `en`, `es`)

### Personal state
- Personal state: Unreviewed, Watch Next, Keep, Maybe, Delete Candidate, Archived
- Favorite status
- Minimum rating
- Watched / unwatched
- Watch later

### Scoring
- Minimum score threshold (`min`)

## Example searches

```text
document_2026-03-19
date:2026-03-19
source:"Saved Messages" duration:>5m
favorites rating:4
watch next from March 19 2026
language:es
act_tag:Oral scene_composition:duo
```

## Filter panel

Open the filter panel in the Library by clicking **⚙ Filters**. All taxonomy and metadata filters are available there. Click **Apply** to execute the filtered search.

## Source access control

The backend enforces source access controls before returning library results. Users only see results from sources they are allowed to access.
