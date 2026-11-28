---
status: draft
phase: 2
baseline: "4.5.15 (branch baseline/v4.5.15, PR #5)"
stepsCompleted: [step-01]
inputDocuments:
  - docs/epics.md  # Phase 1 (done, shipped 4.5.0) — numbering continues from it
  - ARCHITECTURE.md  # backend architecture spine (AD-1…)
  - docs/SPEC-phase1-foundation-classification.md
  - docs/ROADMAP.md  # next priorities: 1. SSO/OIDC, 2. CDN vendoring
  - docs/USER_MANAGEMENT.md
  - docs/ACCESS_CONTROL.md
  - docs/ADVANCED_SEARCH.md
  - "party-mode session 2026-11-13 (decisions log: _bmad-output/party-mode/memories/installed/.memlog.md)"
  - "UX mockup: https://claude.ai/artifact/T23ETNknGrs74a8Kksp5Hb (TeleSaved Phone Shelf, v6)"
---

# telesaved-ai - Epic Breakdown (Phase 2: Friends on the Internet)

## Overview

This document provides the complete epic and story breakdown for telesaved-ai, decomposing the requirements from the PRD, UX Design if it exists, and Architecture requirements into implementable stories.

**Phase 2 continues `docs/epics.md` (Phase 1, Epics 1–6, FR1–FR25, NFR1–NFR8, UX-DR1–UX-DR7, shipped 4.5.0).** Numbering continues from there: FR26+, NFR9+, UX-DR8+, Epic 7+. All `file:line` references are verified against the **4.5.15 baseline** (`baseline/v4.5.15`).

Goal: expose the 4.5.15 vault to invited friends (some technical, some not, mostly on phones) over the internet through Cloudflare Tunnel + Access. Nobody outside the invite list may reach anything, and no friend may see anything from a source they weren't granted.

## Requirements Inventory

### Functional Requirements

**A. Security baseline and internet exposure**

FR26: Restore `Secure` on the session cookie. It regressed in `1df2ce1` when `make_auth_response` moved to `telesaved/auth.py:402`. CHANGELOG 4.5.7 still claims it is set.
FR27: The server binds to a configurable `bind_host`, default `127.0.0.1` (today `web.TCPSite(runner, "0.0.0.0", 8080)`, `app.py:8334`).
FR28: The privacy-PIN token cookie is `HttpOnly` (today `httponly=False`, `app.py:6882`).
FR29: Login throttling works per IP as well as per username. Today `_login_check_and_record_failure` keys on username only (`telesaved/auth.py:211`), and `api_auth_login` is at `app.py:5630`.
FR30: Privacy PIN unlock is throttled with temporary lockout (`api_privacy_unlock`, `app.py:6868`, has none).
FR31: `/thumbs/`, `/previews/` and `/scenes/` fail closed for non-admins when the owning video can't be resolved. Today the ACL check runs only `if rel_guess:` (`app.py:8182`, `8217`, `8232`, `8247`).
FR32: Responses carry security headers: CSP, `frame-ancestors`, `Referrer-Policy: no-referrer`, `X-Content-Type-Options` (none today).
FR33: The server validates the Cloudflare Access JWT (`Cf-Access-Jwt-Assertion`): signature against the team keys, `aud`, `iss`, `exp`. It never trusts the plain email header.
FR34: Just-in-time provisioning. A verified Access identity with no account becomes a user (`auth_backend: "cloudflare_access"`) in the default group with zero sources, following `sync_ldap_user` (`app.py:984`). 4.5.9 already removed the implicit `library.view` fallback in `normalize_permission_list`, so a zero-permission start is possible.
FR35: Newly provisioned users appear in a "Pending" panel on the admin dashboard with a one-tap "assign group" action, and the action is recorded in the audit log (4.5.11).
FR36: When `access.sso_enabled` is on, a verified Access identity creates the TeleSaved session directly, with no second password. A local emergency admin login stays available.

**B. Multi-viewer backend**

FR37: Playback position, watched, favorite, rating, watch-later and personal state are stored per user (today per item in `metadata_store`, e.g. `playback_position` at `app.py:5254`). Existing values migrate to the admin user.
FR38: Viewers (`library.view`) can read and write their own personal state (today `/api/personal` and `/api/watch` GET require `admin.view`, `app.py:1190`).
FR39: Viewers can read collections through an endpoint filtered by access (today `/api/collections` GET requires `admin.view`, `app.py:1188`). The owner can mark a collection "show as home shelf".
FR40: Transcript search filters by `user_can_access_path` (`app.py:1128`) **before** matching. It returns segment index, start time and snippet from `transcript_segments` (`telesaved/audio.py:94`), and is available to `library.view`. Today `search_audio_transcripts` scans every item (`telesaved/audio.py:1181–1192`) and `/api/audio` requires `admin.view` (`app.py:1202`).
FR41: The main library search can include transcript matches, returning per result the matched segment (time, text, hit offset).
FR42: Restore granular search filters lost between 4.4.2 and 4.5.15: date / date range (`date_from`, `date_to`, `since`, `before`), watched, favorite, watch-later and personal state. 4.4.2's `parse_advanced_search_query` exists on `main:app.py` but not on 4.5.15, even though `docs/ADVANCED_SEARCH.md` still documents date ranges. Keep 4.5.x's `audio_language`, `scene_composition`, `performer_presentation`, `size_*` and `duration_*` filters.
FR43: Every video has a stable opaque ID that survives rename and move.
FR44: Deep links use `#/w/<id>` (or `/w/<id>`) instead of `#/item/<encoded path>` (today `frontend/index.html:2940–2956`, `1503`), so file paths never appear in URLs, history or shared links. Unknown, deleted or forbidden IDs all return the same generic "not shared with you" response.
FR45: The owner chooses which Details fields friends can see. Telegram post/message ID and file path are hidden from non-admins by default.
FR46: A home-shelves endpoint returns Continue Watching (per user), Recently Added, one row per accessible source, and collection shelves. All are filtered by access.

**C. Friend-facing mobile UI** (mockup: T23ETNknGrs74a8Kksp5Hb)

FR47: Role-based landing. Users without `admin.view` land on the Viewer home. Admins keep the console and can switch to the Viewer home.
FR48: The Viewer home shows horizontally scrollable shelves (FR46) under a search entry and a profile button.
FR49: Tapping a video opens it. Press-and-hold (~450 ms) opens a peek sheet with a muted preview, Play/Resume and Favorite. This replaces the hover-only preview (`onMouseEnter`, `frontend/index.html:966`, `1003`) on touch devices.
FR50: The video page has the player, Play/Resume, Favorite, Watch later and Share. Edit, Trash and selection controls are hidden without `content.manage` / `content.delete`.
FR51: The share sheet copies the opaque link (FR44), explains its privacy properties, and uses the Web Share API when available.
FR52: Search has one input, quick source chips, and a grid/list toggle saved per user.
FR53: Advanced mode is a per-user setting (switch on the You tab plus a link under search). It is off by default, and the admin can set the default per group. It changes what is shown, never what is permitted.
FR54: Advanced filters: sort; source; length; status (new, in progress, watched); only show (favorites, watch later); tag; spoken language (`audio_language`, Phase 1 FR18); Added date range (Today, 7 days, 30 days, Custom From/To) via FR42. Clear-all and an active-filter count are included.
FR55: Transcript matches appear in main search results in both modes, with timestamp and highlighted snippet. Tapping one opens the video at that moment, on the Transcript tab if advanced mode is on.
FR56: In advanced mode the video page has tabs: Overview, Transcript (tap to seek, find-in-transcript, SRT/VTT export per Phase 1 FR19), Tags, Details.
FR57: Tags tab: add and remove tags plus AI suggestions only with `tags.manage`, reusing the Phase 1 suggestion flow (FR12). Otherwise tags are read-only, and tapping one opens a filtered search.
FR58: The You tab shows identity, shared sources, the advanced-mode switch, layout preference and sign out.
FR59: A "This one isn't shared with you" screen shows no title, thumbnail or source, with a "Go to my home" action.

**D. Later / optional**

FR60: FreeIPA directory → OIDC bridge (Authentik or Keycloak) → Cloudflare Access. Group claims in the Access JWT map to TeleSaved groups on login (ROADMAP priority 1: SSO/OIDC).

### NonFunctional Requirements

NFR9: Zero exposure. No video, thumbnail, preview, scene, transcript text, title or metadata reaches a user without access to its source. This is enforced server-side, with an automated test per media and search endpoint.
NFR10: No runtime CDN or in-browser Babel. React production builds are vendored and JSX is precompiled, so the UI boots with no internet egress (ROADMAP priority 2; today unpkg React 18.3.1 **development** builds plus `@babel/standalone` 8.0.4, `frontend/index.html:8–16`).
NFR11: The Viewer home is interactive in ≤ 3 s on a mid-range phone over 4G (Lighthouse mobile).
NFR12: Accessibility: touch targets ≥ 44 px, WCAG 2.1 AA contrast, labels on every icon-only control, real buttons and inputs (today 3 `aria-`/`role` attributes in `frontend/index.html`).
NFR13: Non-technical friends: from invite to playing video with no password, and at most one sign-in step once FR36 is on.
NFR14: Admin effort: adding a friend takes ≤ 2 steps (allow the email in Access, then assign a group).
NFR15: Security regression tests cover spoofed Access headers, direct-to-origin requests, forbidden-source media and transcript search, login and PIN brute force, and opaque-ID enumeration.
NFR16: Claims match code. Every CHANGELOG security claim has a test that would fail if the fix regressed (the FR26 lesson).
NFR17: Changes are reviewable. One story per PR. No multi-release squash commits like `1df2ce1`.
NFR18: Check Cloudflare's current terms for streaming video through Tunnel/proxy before rollout to friends, and record the outcome.
NFR19: Phase 1 NFR5 still applies. All new endpoints enforce RBAC (401/403) through `required_permission_for_request` (`app.py:1169`).

### Additional Requirements

- **Baseline:** 4.5.15 modular monolith per `ARCHITECTURE.md`. Shared state is owned by `telesaved/state.py` (AD-1). Imports flow downward only (AD-2). New auth code goes in `telesaved/auth.py`; new search code respects the `pipeline.py` / `audio.py` boundaries.
- **Before any internet exposure:** review `1df2ce1` (21 files, +5,085/−2,589, 4.5.9–4.5.15 squashed). Its known regression is FR26.
- Auth path: `auth_middleware` (`app.py:1222`), `required_permission_for_request` (`app.py:1169`), `privacy_middleware` (`app.py:1380`). ACL helpers: `user_can_access_path` (`app.py:1128`), `require_source_access_response` (`app.py:1164`), `accessible_source_refs_for_user` (`app.py:1096`).
- The audit log (4.5.11, `telesaved/state.py` `append_audit_log`) records provisioning, group assignment and visibility changes.
- 4.4.2's search parser can be recovered from git history (`git show main:app.py`, `parse_advanced_search_query`) for FR42.
- Frontend: one `frontend/index.html` (3,136 lines) with `App()` hash router (`:2961`, `:2940`). The build step (NFR10) comes before UI stories. `build-telesaved-deb.sh` and `scripts/install-telesaved-venv.sh` must ship the built assets.
- Deployment: `cloudflared` tunnel plus an Access app with an email allowlist and one-time-PIN login. New config keys: `bind_host`, `cookie_secure`, `access.team_domain`, `access.aud`, `access.sso_enabled`.
- Packaging already ships `telesaved-telegram-login` (`build-telesaved-deb.sh:457`). `scripts/telegram-login.py` covers venv installs.
- Future infrastructure: Proxmox homelab (offline for now) with FreeIPA, Jellyfin, Stirling PDF and production TeleSaved.

### UX Design Requirements

UX-DR8: Viewer palette uses the existing tokens (`--bg #0e1621`, `--panel #17212b`, `--panel-2 #1f2c3a`, `--text #e9edf0`, `--muted #8fa3b5`, `--accent #2ea6ff`). Text on accent fills is `#0e1621` for AA contrast.
UX-DR9: Poster card: 168×100 landscape thumbnail, duration badge, 4 px accent progress bar, 1-line title, sub-line ("% left" or source). Tap opens. 450 ms hold opens peek. Touch callout and context menu are suppressed.
UX-DR10: Shelf row: heading plus hint, horizontal scroll with snap, hidden scrollbar, 16 px gutter.
UX-DR11: Viewer bottom nav with four items (Home, Search, Favorites, You), icon plus label, accent when active, safe-area padding.
UX-DR12: Bottom sheet (peek, share): backdrop tap closes, slide-up animation, `role="dialog"` with a label.
UX-DR13: Segmented controls (grid/list, video tabs) with `aria-pressed`, or `role="tab"` with `aria-selected`.
UX-DR14: Filter chip groups (single-select, `aria-pressed`) in a collapsible "Filters & sort (n)" panel showing the current sort, with Clear.
UX-DR15: Transcript line: monospace accent timestamp plus text, active highlight, find-in-transcript input.
UX-DR16: Tag chip with × remove (`aria-label="Remove tag X"`), an add input (Enter submits), and dashed AI-suggestion chips.
UX-DR17: Details key/value list. Restricted fields show "Hidden (owner only)".
UX-DR18: Search snippet: timestamp plus quoted text with the hit in `<mark>`, clamped to 2 lines in grid and 1 in list. The count says "n found in what was said".
UX-DR19: Empty and locked states (no favorites, no matches, no transcript yet, not shared with you) with mockup copy.
UX-DR20: Advanced-mode switch: `role="switch"`, `aria-checked`, 52×32.
UX-DR21: Date range: preset chips plus native From/To date inputs (`color-scheme: dark`).
UX-DR22: List row: 112×63 thumbnail with duration and progress, title, "source · added · status", plus a tag line in advanced mode.
UX-DR23: Focus-visible 2 px accent outline. All controls ≥ 44 px.

### FR Coverage Map

{{requirements_coverage_map}}

## Epic List

{{epics_list}}
