---
title: 'pytest harness with auth, RBAC and source-ACL characterization tests'
type: 'chore'
created: '2026-11-23'
status: 'draft'
route: 'dispatch'
review_loop_iteration: 0
context: ['{project-root}/ARCHITECTURE.md', '{project-root}/docs/SMOKE_TEST.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** TeleSaved has no automated tests; the only safety net for auth, RBAC and source ACL is the manual `docs/SMOKE_TEST.md` §10. SSO/OIDC will add a third identity backend into that path, and the v4.5.13 auth extraction already lost a shipped fix unnoticed (session cookie `secure=True` from v4.5.7, dropped in commit `1df2ce1`).

**Approach:** Add a pytest harness that runs without ML deps or Telegram, and characterization tests that pin today's auth/RBAC/ACL behavior at two levels: `telesaved/auth.py` in isolation via `_bind()`, and HTTP-level through the real `auth_middleware` + `privacy_middleware` + `routes` using aiohttp's test client.

## Boundaries & Constraints

**Always:** Tests assert current code behavior (characterization), not docs; where docs disagree, the test follows code and the discrepancy is recorded in Implementation Notes. Every test runs with `TELESAVED_BASE_PATH` pointed at a per-session tmp dir and never touches `/mnt/rda1`, the repo's `config.json`, or the network. Full suite runs in under 30 s on CPU with only `requirements-dev.txt` installed.

**Decisions:** (1) Restore `secure=True` on the auth session cookie in `telesaved/auth.py:make_auth_response` in this change, pinned by a test, with a CHANGELOG entry. (2) Add a GitHub Actions workflow running the suite on push and PRs to `main`. (3) Keep as one spec despite ~1,800 tokens.

**Never:** No production behavior changes other than the cookie fix above. No refactor of `app.py` to make it testable. No installing torch/CLIP/YOLO/NudeNet/whisper for tests. No tests outside auth/RBAC/ACL scope (pipeline, audio, jobs are follow-ups).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| No users yet | any `/api/*` except public paths | 401 `bootstrap_required` | N/A |
| Unauthenticated | `/api/library`, `/files/x` | 401 `auth_required` | N/A |
| Viewer (Users) | `GET /api/admin/stats`, `POST /api/sources`, `DELETE /api/item/x` | 403 `forbidden` | N/A |
| Manager | `POST /api/sources` → allowed; `/api/admin/*` → 403 | per `required_permission_for_request` | N/A |
| Admin (`*`) | any route | passes middleware | N/A |
| Login OK | valid local creds | 200, cookie `telesaved_auth_token` HttpOnly, Secure, SameSite=Lax | N/A |
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
- [ ] `telesaved/auth.py` -- add `secure=True` to `set_cookie` in `make_auth_response` -- restore the v4.5.7 fix lost in `1df2ce1`.
- [ ] `CHANGELOG.md` -- note the restored Secure flag and the new test suite under an unreleased heading -- traceability.
- [ ] `.github/workflows/tests.yml` -- on push/PR to `main`: Python 3.11, `pip install -r requirements-dev.txt`, `pytest -q` -- automatic gate.
- [ ] `requirements-dev.txt` -- pytest, pytest-aiohttp, aiohttp, telethon, numpy (pinned lower bounds matching `requirements.txt`) -- minimal deps to import `app`.
- [ ] `pytest.ini` -- `testpaths = tests`, `asyncio_mode = auto` -- single config.
- [ ] `tests/conftest.py` -- set `TELESAVED_BASE_PATH` to a session tmp dir, stub ML modules, import `app` once; fixtures: `auth_mod` (fresh `_bind` with in-memory load/save), `client` (aiohttp test client on real middlewares+routes, auth state reset per test), `make_user(group)` returning a logged-in token -- shared harness.
- [ ] `tests/test_auth_unit.py` -- normalization, hashing/verify, group/user permissions, sessions (expiry, eviction cap, disabled user), rate-limit helper, `create_local_user` validation -- module-level coverage.
- [ ] `tests/test_rbac_http.py` -- I/O matrix rows over HTTP, plus a parametrized table over `required_permission_for_request` for every prefix branch -- route-map regression net.
- [ ] `tests/test_source_acl.py` -- grants via user and group, `*`, `sources.manage` override, legacy `me` fallback, `/files/` 403 -- ACL coverage.
- [ ] `README.md` -- short "Running tests" section -- discoverability.

**Acceptance Criteria:**
- Given a clean checkout and `pip install -r requirements-dev.txt`, when `pytest` runs, then all tests pass with no network access and no files written outside the tmp dir.
- Given someone removes a branch from `required_permission_for_request` or changes a default group's permissions, when `pytest` runs, then at least one test fails naming the route or group.

## Implementation Notes

## Spec Change Log

## Review Triage Log

## Design Notes

Two layers on purpose: unit tests on `telesaved/auth.py` survive further refactors of `app.py`; HTTP tests catch wiring mistakes (like the lost cookie flag) that unit tests miss. Known doc/code mismatch to record, not fix: `SMOKE_TEST.md` 10B.2–10B.3 say Manager approves tag suggestions, but `/api/tags/suggestions*` maps to `system.admin`.

## Verification

**Commands:**
- `pytest -q` -- expected: all pass, runtime < 30 s.
- `git status --porcelain` after the run -- expected: no new untracked files outside `tests/` caches.
