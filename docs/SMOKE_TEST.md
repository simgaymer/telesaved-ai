# TeleSaved AI — Smoke Test Checklist

Use this checklist for every release before merging to `main`. Execute on a clean Debian/Ubuntu machine with a fresh `.deb` install. Mark each item ✅ pass / ❌ fail / ⚠️ partial.

## Environment

- **Package:** `dist/telesaved-ai_4.5.8_all.deb`
- **Tester:**
- **Date:**
- **Host OS:**
- **Hardware:** (CPU-only or GPU)

---

## 1. Package install

| # | Check | Expected | Result |
|---|-------|----------|--------|
| 1.1 | `sudo apt install ./telesaved-ai_4.5.8_all.deb` completes | Exit 0, no errors | |
| 1.2 | `id telesaved` | User exists, no login shell | |
| 1.3 | `systemctl is-active telesaved` | `active` | |
| 1.4 | `systemctl show telesaved -p User` | `User=telesaved` | |
| 1.5 | `systemctl show telesaved -p ExecStart` | Contains `/opt/telesaved/venv/bin/python` | |
| 1.6 | `/opt/telesaved/venv/bin/python -c "import aiohttp, telethon, nudenet, whisper, faiss; print('OK')"` | `OK` | |

---

## 2. First-run setup wizard

| # | Check | Expected | Result |
|---|-------|----------|--------|
| 2.1 | Open `http://SERVER:8080/` in browser | Setup wizard overlay renders (not library) | |
| 2.2 | Step indicator shows "Step 1 of 4" | Step dots visible, Welcome content shown | |
| 2.3 | Legal disclaimer visible on step 1 | Disclaimer text present before "Get Started" | |
| 2.4 | Click "Get Started" | Advances to step 2 | |
| 2.5 | Enter **invalid** Telegram credentials, click "Validate & Continue" | Spinner appears → inline error message → stays on step 2 | |
| 2.6 | Enter **valid** credentials, click "Validate & Continue" | Spinner → green/advance → moves to step 3 | |
| 2.7 | Step 3 renders with vault path field | `/opt/telesaved/media` pre-filled | |
| 2.8 | Click "Next" | Advances to step 4 | |
| 2.9 | Enter admin username + password, click "Complete Setup" | Wizard closes, library loads | |
| 2.10 | Refresh page | Library loads (not wizard) | |

---

## 3. Authentication

| # | Check | Expected | Result |
|---|-------|----------|--------|
| 3.1 | `POST /api/auth/status` after setup | `{"authenticated": false, "bootstrap_required": false}` | |
| 3.2 | Log in with wizard-created admin credentials | Library loads, username shown in sidebar | |
| 3.3 | Click "Sign Out" | Redirected to login page | |
| 3.4 | Login with wrong password | Error message inline | |
| 3.5 | Login with correct credentials | Library loads | |

---

## 4. Telegram source + download

| # | Check | Expected | Result |
|---|-------|----------|--------|
| 4.1 | Open Administration → Sources | Sources page renders | |
| 4.2 | Click "+ Add Source", fill form, submit | Source appears in list | |
| 4.3 | Open Administration → Jobs | Jobs page renders with active/queued jobs | |
| 4.4 | At least one video downloads | File appears in `base_path` | |
| 4.5 | Library page shows the downloaded video | Thumbnail renders, filename correct | |

---

## 5. AI pipeline

| # | Check | Expected | Result |
|---|-------|----------|--------|
| 5.1 | Open video detail → Info tab | `ai_signals` present (scene_composition etc.) OR `null` if below threshold | |
| 5.2 | Info tab shows `audio_language` | ISO 639-1 code OR `—` if not transcribed | |
| 5.3 | Tags tab shows AI display tags | Display tags from `tag_mappings.json` shown | |
| 5.4 | `SELECT ai_signals FROM videos LIMIT 5` via sqlite3 | Rows present, valid JSON or NULL | |

---

## 6. Audio Intelligence

| # | Check | Expected | Result |
|---|-------|----------|--------|
| 6.1 | Open video detail → Audio tab | "No audio analysis yet" + "Analyze Audio" button | |
| 6.2 | Click "Analyze Audio" | "Queued… (auto-refreshing)" appears | |
| 6.3 | Wait for completion | Transcript view renders with segments | |
| 6.4 | Timestamp column shows e.g. `0:12` | Timestamps correctly formatted | |
| 6.5 | Speaker badge visible (S1 at minimum) | Colour-coded badge present | |
| 6.6 | Click "⬇ SRT" | `.srt` file downloads, opens correctly in VLC/mpv | |
| 6.7 | Click "⬇ VTT" | `.vtt` file downloads | |
| 6.8 | Double-click a segment | Inline textarea appears | |
| 6.9 | Edit text, press Enter | Segment text updates | |
| 6.10 | Click "💾 Save edits" | "Transcript saved" toast — edits persist on page reload | |
| 6.11 | Click "Plain text" toggle | Plain transcript text renders | |
| 6.12 | Language shown in metadata row | e.g. `🗣 Language: en` | |

---

## 7. Library UX

| # | Check | Expected | Result |
|---|-------|----------|--------|
| 7.1 | Grid view renders | 16:9 cards, thumbnails, tag chips | |
| 7.2 | Hover over card | Preview GIF plays (if generated) | |
| 7.3 | Switch to list view | Table with sortable columns | |
| 7.4 | Click a column header | Sort direction arrows, results reorder | |
| 7.5 | Search for a filename fragment | Matching results returned | |
| 7.6 | Open filter panel (⚙ Filters) | All filter fields present (source, tag, act tag, language, scene, duration, score) | |
| 7.7 | Filter by `audio_language` | Only matching videos returned | |
| 7.8 | Select 2 items (checkboxes) | Bulk action bar appears | |
| 7.9 | Click "🗑 Delete selected" | Confirm modal appears | |
| 7.10 | Confirm delete | Items moved to trash, library reloads | |
| 7.11 | Empty library state | Empty state message with CTA shown | |

---

## 8. AI Librarian

| # | Check | Expected | Result |
|---|-------|----------|--------|
| 8.1 | Click "✨ Ask AI" in topbar | Slide-in panel opens | |
| 8.2 | Example prompts visible on first open | Pre-loaded suggestions shown | |
| 8.3 | Click an example prompt | Question fires, assistant responds | |
| 8.4 | Response contains answer text | Non-empty answer string | |
| 8.5 | Response contains result rows | Video thumbnails + filenames shown | |
| 8.6 | Click a result row | Panel closes, ItemDetailPage opens | |
| 8.7 | Type custom question, press Enter | New question fires | |
| 8.8 | Click overlay background | Panel closes | |

---

## 9. Remaining pages

| # | Check | Expected | Result |
|---|-------|----------|--------|
| 9.1 | Watch Later → add item → check page | Item appears | |
| 9.2 | Personal → rate + favorite an item → check page | Item appears | |
| 9.3 | Collections → create → delete | CRUD works | |
| 9.4 | Trash → item visible → Restore | Item returns to library | |
| 9.5 | Downloads → storage bar visible | Progress bars and history shown | |
| 9.6 | Jobs → pause a job | Job status changes to paused | |
| 9.7 | Admin → Dashboard → stats visible | Counts and storage bar render | |
| 9.8 | Admin → Sources → add/view | Source list works | |
| 9.9 | Admin → Review Studio → accept/skip | Sequential item flow works | |
| 9.10 | Admin → Tag Management → approve suggestion | Suggestion moves to vocabulary | |
| 9.11 | Admin → Scoring → change threshold → save | Toast confirms save | |
| 9.12 | Admin → Users → create user → log in as them | New user can authenticate | |
| 9.13 | Admin → Logs → view | Log text renders | |


## 10. RBAC and permissions

> Log in as each role in turn. Use the Admin account to create each test user first (Administration → Users → Create User).

### 10A. Viewer (Users group)

| # | Check | Expected | Result |
|---|-------|----------|--------|
| 10A.1 | Log in as Viewer | Library loads | |
| 10A.2 | Sidebar shows no Administration link | Admin section hidden | |
| 10A.3 | `GET /api/admin/stats` while logged in as Viewer | `403 Forbidden` | |
| 10A.4 | `POST /api/tags/approve/{id}` while logged in as Viewer | `403 Forbidden` | |
| 10A.5 | `DELETE /api/item/{path}` while logged in as Viewer | `403 Forbidden` | |
| 10A.6 | `POST /api/users` while logged in as Viewer | `403 Forbidden` | |
| 10A.7 | `GET /api/library` while logged in as Viewer | `200 OK`, items from allowed sources only | |

### 10B. Manager group

| # | Check | Expected | Result |
|---|-------|----------|--------|
| 10B.1 | Log in as Manager | Library and Administration (limited) both accessible | |
| 10B.2 | Manager can access Administration → Tag Management | Page renders, suggestions approvable | |
| 10B.3 | Manager can approve a tag suggestion | Tag moves to vocabulary | |
| 10B.4 | `POST /api/users` while logged in as Manager | `403 Forbidden` (user management is Admin-only) | |
| 10B.5 | `POST /api/sources` while logged in as Manager | `200 OK` (sources.manage granted) | |

### 10C. Source ACL

| # | Check | Expected | Result |
|---|-------|----------|--------|
| 10C.1 | Create a source, grant access to Viewer only | Admin can see source; Viewer can also see it | |
| 10C.2 | Create a second source, grant to Admin only | Viewer library shows no items from second source | |
| 10C.3 | `GET /files/{path-from-restricted-source}` as Viewer | `403 Forbidden` | |
| 10C.4 | `GET /thumbs/{thumb-from-restricted-source}` as Viewer | `403 Forbidden` | |

---

## 11. Limp mode

| # | Check | Expected | Result |
|---|-------|----------|--------|
| 11.1 | Block outbound traffic (firewall or disconnect) | Connectivity banner appears within 60s | |
| 11.2 | Active jobs show as paused | Jobs page shows `paused` status | |
| 11.3 | Library remains browsable | Search, detail, playback all work | |
| 11.4 | Restore connectivity | Banner disappears, jobs resume | |

---

## 12. Upgrade from 4.4.x

| # | Check | Expected | Result |
|---|-------|----------|--------|
| 12.1 | Install over existing 4.4.x deployment | Service restarts cleanly | |
| 12.2 | `SELECT audio_language FROM videos LIMIT 1` | No SQL error (column migrated) | |
| 12.3 | `SELECT ai_signals FROM videos LIMIT 1` | No SQL error (column migrated) | |
| 12.4 | `SELECT * FROM video_tag_labels LIMIT 1` | No SQL error (table migrated) | |
| 12.5 | Existing library items still visible | No data loss | |

---

## Sign-off

| Role | Name | Date | Result |
|------|------|------|--------|
| Developer | | | |
| QA / Owner | | | |

**Release gate:** All P0 items (sections 1–3, 6.10, 7.9) must pass before merging to `main`. Section 10 (RBAC) must pass before any release that touches auth, permissions, or user management.
