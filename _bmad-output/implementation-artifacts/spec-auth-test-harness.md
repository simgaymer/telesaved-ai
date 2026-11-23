---
title: 'pytest harness with auth, RBAC and source-ACL characterization tests'
type: 'chore'
created: '2026-11-23'
status: 'done'
baseline_commit: 'd6dc2d4dedf1b3bd3a2098efeed3c463e783a972'
route: 'dispatch'
review_loop_iteration: 1
context: ['{project-root}/ARCHITECTURE.md', '{project-root}/docs/SMOKE_TEST.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** TeleSaved has no automated tests; the only safety net for auth, RBAC and source ACL is the manual `docs/SMOKE_TEST.md` §10. SSO/OIDC will add a third identity backend into that path, and the v4.5.13 auth extraction already lost a shipped fix unnoticed (session cookie `secure=True` from v4.5.7, dropped in commit `1df2ce1`).

**Approach:** Add a pytest harness that runs without ML deps or Telegram, and characterization tests that pin today's auth/RBAC/ACL behavior at two levels: `telesaved/auth.py` in isolation via `_bind()`, and HTTP-level through the real `auth_middleware` + `privacy_middleware` + `routes` using aiohttp's test client.

## Boundaries & Constraints

**Always:** Tests assert current code behavior (characterization), not docs; where docs disagree, the test follows code and the discrepancy is recorded in Implementation Notes. Every test runs with `TELESAVED_BASE_PATH` pointed at a per-session tmp dir and never touches `/mnt/rda1`, the repo's `config.json`, or the network. Full suite runs in under 30 s on CPU with only `requirements-dev.txt` installed.

**Decisions:** (1) Revised by the user after review: set `Secure` on the auth session cookie only when the request came over HTTPS (`request.secure` or `X-Forwarded-Proto: https`), so the documented plain-HTTP install keeps working; pinned by unit tests, HTTP tests, and a cookie-only end-to-end login test; CHANGELOG entry. (2) Add a GitHub Actions workflow running the suite on push and PRs to `main`. (3) Keep as one spec despite ~1,800 tokens. (4) Added after implementation, approved by the user: fix the local-login 500 found by the suite (`app.py` missing `_LOGIN_*` aliases from `telesaved.auth`) in this change.

**Never:** No production behavior changes other than the cookie fix and login-alias fix above. No refactor of `app.py` to make it testable. No installing torch/CLIP/YOLO/NudeNet/whisper for tests. No tests outside auth/RBAC/ACL scope (pipeline, audio, jobs are follow-ups).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| No users yet | any `/api/*` except public paths | 401 `bootstrap_required` | N/A |
| Unauthenticated | `/api/library`, `/files/x` | 401 `auth_required` | N/A |
| Viewer (Users) | `GET /api/admin/stats`, `POST /api/sources`, `DELETE /api/item/x` | 403 `forbidden` | N/A |
| Manager | `POST /api/sources` → allowed; `/api/admin/*` → 403 | per `required_permission_for_request` | N/A |
| Admin (`*`) | any route | passes middleware | N/A |
| Login OK | valid local creds | 200, cookie `telesaved_auth_token` HttpOnly, SameSite=Lax; Secure only when HTTPS or `X-Forwarded-Proto: https` | cookie alone (no header) authenticates follow-up requests over plain HTTP |
| Login brute force | 10 wrong passwords, then 11th | 11th → 429 even with correct password | lockout window 300 s |
| Disabled user | valid session, user then disabled | next request 401, session dropped | N/A |
| Source ACL | Viewer granted source A only | path in A allowed; path in B → 403; `sources.manage` or `*` grant → all | legacy file with no `source_ref` maps to `me` |
| Permission list | `[]` / all-invalid | stays `[]` (no `library.view` fallback) | N/A |

</frozen-after-approval>

## Code Map

- `telesaved/auth.py` -- unit under test; `_bind(cfg)` needs `AUTH_FILE, APP_VERSION, load_json, save_json, now_stamp, normalize_source_ref, ldap_is_enabled, ldap_status_payload, accessible_source_refs_for_user, web`. Module dicts `auth_data`, `AUTH_SESSIONS`, `_LOGIN_ATTEMPTS` are mutated in place — reset between tests by re-calling `_bind`.
- `app.py:1096-1168` -- `accessible_source_refs_for_user`, `source_ref_for_path`, `user_can_access_source/path`, `require_*_response`, `required_permission_for_request` (route→permission map).
- `app.py:1214` `auth_middleware`, `app.py:1380` `privacy_middleware`, `app.py:8330` app assembly (`web.Application(middlewares=[auth_middleware, privacy_middleware]); add_routes(routes)`) — replicate in a fixture; do not call `main()`.
- `app.py:5608` bootstrap, `5630` login (rate-limit check reads `_LOGIN_ATTEMPTS` directly), `5673` logout.
- `telesaved/pipeline.py:45-52` -- top-level `import clip, faiss, torch, PIL.Image, ultralytics`; `_bind` calls `clip.load()` expecting a 2-tuple. Importing `app` works when these are stubbed in `sys.modules` before import (verified: import takes ~0.3 s, `client is None` without `config.json`).
- `app.py:44` -- `TELESAVED_BASE_PATH` env var redirects all state files; must be set before `import app`.

## Tasks & Acceptance

**Execution:**
- [x] `telesaved/auth.py` -- add `secure=True` to `set_cookie` in `make_auth_response` -- restore the v4.5.7 fix lost in `1df2ce1`.
- [x] `CHANGELOG.md` -- note the restored Secure flag and the new test suite under an unreleased heading -- traceability.
- [x] `.github/workflows/tests.yml` -- on push/PR to `main`: Python 3.11, `pip install -r requirements-dev.txt`, `pytest -q` -- automatic gate.
- [x] `requirements-dev.txt` -- pytest, pytest-aiohttp, aiohttp, telethon, numpy (pinned lower bounds matching `requirements.txt`) -- minimal deps to import `app`.
- [x] `pytest.ini` -- `testpaths = tests`, `asyncio_mode = auto` -- single config.
- [x] `tests/conftest.py` -- set `TELESAVED_BASE_PATH` to a session tmp dir, stub ML modules, import `app` once; fixtures: `auth_mod` (fresh `_bind` with in-memory load/save), `client` (aiohttp test client on real middlewares+routes, auth state reset per test), `make_user(group)` returning a logged-in token -- shared harness.
- [x] `tests/test_auth_unit.py` -- normalization, hashing/verify, group/user permissions, sessions (expiry, eviction cap, disabled user), rate-limit helper, `create_local_user` validation -- module-level coverage.
- [x] `tests/test_rbac_http.py` -- I/O matrix rows over HTTP, plus a parametrized table over `required_permission_for_request` for every prefix branch -- route-map regression net.
- [x] `tests/test_source_acl.py` -- grants via user and group, `*`, `sources.manage` override, legacy `me` fallback, `/files/` 403 -- ACL coverage.
- [x] `README.md` -- short "Running tests" section -- discoverability.

**Acceptance Criteria:**
- Given a clean checkout and `pip install -r requirements-dev.txt`, when `pytest` runs, then all tests pass with no network access and no files written outside the tmp dir.
- Given someone removes a branch from `required_permission_for_request` or changes a default group's permissions, when `pytest` runs, then at least one test fails naming the route or group.

## Implementation Notes

- **Pre-existing bug found — local login is broken (HTTP 500).** `app.py:api_auth_login` (~line 5639) reads `_LOGIN_ATTEMPTS`, `_LOGIN_MAX_ATTEMPTS`, `_LOGIN_WINDOW_SECONDS`, but `app.py` never aliases them from `telesaved.auth` (lost in the v4.5.13 extraction, alongside the cookie flag). Every `POST /api/auth/login` with a non-empty username raises `NameError` → 500, so after a session expires or the user logs out nobody can log back in (bootstrap still works). Per **Never** (no production changes beyond the cookie fix) this is *not* fixed here. Handling: `test_rbac_http.py::test_login_route_is_currently_broken` is a strict `xfail` asserting 200 — it XPASSes (fails the suite) once fixed, forcing removal of the marker. The I/O-matrix rows "Login OK" and "Login brute force" are exercised via the `login_route_names` fixture, which monkeypatches the three missing names onto `app` (test-only). Suggested fix: add `_LOGIN_ATTEMPTS = _auth._LOGIN_ATTEMPTS`, `_LOGIN_MAX_ATTEMPTS = _auth._LOGIN_MAX_ATTEMPTS`, `_LOGIN_WINDOW_SECONDS = _auth._LOGIN_WINDOW_SECONDS` to the alias block in `app.py`, then delete the xfail marker and fixture.
- `make_user` mints sessions with `create_auth_session` rather than HTTP login (consequence of the above).
- The middleware matrix test runs every `required_permission_for_request` row against a catch-all probe app behind the real `auth_middleware` + `privacy_middleware`, so a 200 means "passed the middleware" without executing production handlers. Real-route tests cover the explicit matrix rows.
- `conftest.py` hides the repo `config.json` from `app.read_app_config` during import (temporary `os.path.exists` wrapper) and unsets `TELESAVED_API_ID/HASH`, so a developer's local config can't redirect `BASE_PATH` to `/mnt/rda1` or create a Telegram client.
- Doc/code mismatches recorded (tests follow code):
  - `SMOKE_TEST.md` 10B.2–10B.3: `GET /api/tags/suggestions`, `/api/tags/labels`, `/api/tags/migrate-labels` map to `system.admin` (Manager cannot list suggestions). However `POST /api/tags/suggestions/{id}/approve|reject` hits the earlier `POST /api/tags/suggest*` branch → `tags.manage`, so a Manager *can* approve/reject if given an id. The spec's Design Notes statement "`/api/tags/suggestions*` maps to `system.admin`" is only true for non-POST.
  - `SMOKE_TEST.md` 10A.6 says `POST /api/users`; that route does not exist (middleware maps it to `library.view`, then 404). The real route is `POST /api/auth/users`, which the middleware lets through (`/api/auth/` → no permission) and the handler rejects with 403 for Viewer and Manager.
  - Permission-list row: `normalize_permission_list` and `PUT /api/auth/groups/{name}` keep `[]`; but `POST /api/auth/groups` substitutes `["library.view"]` for an empty list *before* normalizing (all-invalid still → `[]`). Pinned as-is.
  - `/api/downloads` `GET` falls through to `library.view`; `/api/privacy/*` other than `/status` (including `/unlock`) requires `system.admin` in the RBAC map.
  - Before bootstrap, non-API paths (`/files/…`) are not gated and source ACL is open (`user_can_access_source` returns True when no users exist).
  - A custom group with `system.admin` but not `sources.manage` can open any source, while `accessible_source_refs_for_user` (and `/api/auth/status.source_grants`) reports only its real grants.
- Default `Users` group has no source grants, so a fresh Viewer cannot read legacy (`me`) files until granted.
- Verified locally on Python 3.11 and 3.13 in clean venvs with only `requirements-dev.txt`: 267 passed, 1 xfailed, ~6–10 s. Mutation checks: removing the `/api/rules` branch fails 4 tests named `…[GET /api/rules]`/`…[POST /api/rules]`; adding `tags.manage` to `Users` fails `test_default_group_permissions_pinned[Users]` plus matrix rows; dropping `secure=True` fails 3 cookie tests.

## Spec Change Log

- **Iteration 1 (intent_gap):** Review found unconditional `secure=True` locks out the documented `http://SERVER_IP:8080` install (frontend authenticates by cookie only; browsers drop Secure cookies on plain HTTP). User chose "Secure only on HTTPS". Amended decision (1) and the Login OK matrix row. Avoids: every standard install losing login after upgrade. KEEP: the harness, fixtures, permission table, ACL tests, and the login-alias fix were unaffected and kept as-is; only the cookie logic and its tests were re-derived.

## Review Triage Log

| # | Finding | Verdict | Evidence / route |
|---|---------|---------|------------------|
| 1 | Unconditional Secure cookie locks out plain-HTTP installs (blind, edge) | high | `frontend/index.html:848` cookie-only; docs say `http://SERVER_IP:8080`. intent_gap → user chose HTTPS-only Secure; fixed. |
| 2 | No cookie-only end-to-end auth test (blind, edge) | medium | All HTTP tests used the header. Same root as 1; added `test_cookie_only_session_over_plain_http`, which fails if Secure is forced. |
| 3 | `api_auth_login` duplicates the lockout check instead of calling an auth helper | medium | Real: the split is why extraction broke login. Pre-existing design; defer. |
| 4 | Alias identity relies on `_bind` clearing dicts in place | low | Pinned by `test_rebind_clears_sessions_and_login_attempts` and the alias test; reject. |
| 5 | Unbounded `_LOGIN_ATTEMPTS` growth / per-username lockout DoS; LDAP branch untested | medium | Pre-existing behavior, not caused by this change; defer. |
| 6 | No completeness check that every registered `/api/*` route is in the permission table | medium | Real: new routes silently fall through to `library.view`. Pre-existing; defer. |
| 7 | requirements-dev comment overstates what conftest stubs | low | Correct; patched. |
| 8 | `config.json` hiding depends on `os.path.exists` | medium | Real fragility; the `client is None` assert limits harm. defer (needs a config-path seam in app.py). |
| 9 | SMOKE_TEST 10A.6/10B.4 name `POST /api/users` | low | Real route is `/api/auth/users`; patched doc. |
| 10 | pytest-asyncio not pinned though pytest.ini needs >=0.24 | low | Patched requirements-dev. Action SHA pinning / lint step: reject (not needed for a test gate). |
| 11 | Source-ACL fixture cleanup hard-coded; implicit `fresh_auth` | low | Session tmp dir is deleted at end; reject. |
| 12 | Stray blank lines; no `pythonpath` | low | Patched. |
| 13 | Privacy cookie (`app.py:6881`) not Secure (verification-gap, noted) | low | Out of the auth-cookie scope; defer with item 3 batch. |

## Design Notes

Two layers on purpose: unit tests on `telesaved/auth.py` survive further refactors of `app.py`; HTTP tests catch wiring mistakes (like the lost cookie flag) that unit tests miss. Known doc/code mismatch to record, not fix: `SMOKE_TEST.md` 10B.2–10B.3 say Manager approves tag suggestions, but `/api/tags/suggestions*` maps to `system.admin`.

## Verification

**Commands:**
- `pytest -q` -- expected: all pass, runtime < 30 s.
- `git status --porcelain` after the run -- expected: no new untracked files outside `tests/` caches.
