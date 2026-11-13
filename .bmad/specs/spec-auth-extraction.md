---
title: 'Extract telesaved/auth.py — Local users, RBAC, session management'
type: 'refactor'
created: '2025-01-30'
status: 'ready-for-dev'
review_loop_iteration: 0
context:
  - ARCHITECTURE.md
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The LOCAL USERS / RBAC / SOURCE ACL section (lines 724–1046, ~323 lines) lives inside `app.py`, violating the Tier 2 extraction plan in ARCHITECTURE.md.

**Approach:** Move all 22 auth functions and module-level globals to a new `telesaved/auth.py` using the established `_bind(cfg)` + forwarding-alias pattern. `app.py` gains one import, one `_auth._bind(cfg)` call, and one forwarding alias per extracted name — no external callsite changes and no behaviour changes.

## Boundaries & Constraints

**Always:**
- Follow the `_bind(cfg)` + in-place mutation pattern from `telesaved/state.py`.
- `auth_data`, `AUTH_SESSIONS`, `_LOGIN_ATTEMPTS` are module-level dicts/sets in `auth.py`, populated in-place by `_bind()` using `.clear()/.update()` so alias references in `app.py` stay valid.
- `PERMISSION_CATALOG`, `DEFAULT_GROUPS`, `AUTH_SESSION_COOKIE`, `AUTH_SESSION_SECONDS`, `_LOGIN_MAX_ATTEMPTS`, `_LOGIN_WINDOW_SECONDS` are constants — assigned directly as module-level names in `auth.py`.
- `load_auth_data()` and `save_auth_data()` remain private to `auth.py`; `app.py` never calls them directly.
- The section also contains `ldap_default_settings()`, `identity_default_data()`, `ensure_identity_defaults()`, and `auth_default_data()` — these are part of the AUTH section and move with it.
- The LDAP section (lines 1047–1565) is **not** part of this extraction — it stays in `app.py`.
- Syntax-check both files after implementation.

**Never:**
- Do not change function signatures visible to external callers.
- Do not extract the LDAP section (`ldap_is_enabled`, `ldap_authenticate`, etc.) — those live in `telesaved/ldap_backend.py` already or remain in `app.py`.
- Do not add new logic or error handling.

</frozen-after-approval>

## Code Map

- `app.py:724–1046` — auth section to extract; 22 functions + module-level globals
- `app.py:1979` — `import telesaved.state as _state` — pattern to follow for import placement
- `app.py:2742` — `_pipeline._bind(cfg)` — `_bind` call pattern to follow
- `app.py:1047` — LDAP section start — the boundary; do NOT extract past here
- `app.py:95` — `AUTH_FILE = os.path.join(BASE_PATH, "auth.json")`
- `app.py:877` — `load_auth_data()` — loads from AUTH_FILE, calls `ensure_identity_defaults`
- `app.py:900` — `auth_data = load_auth_data()` — module-level init
- `telesaved/state.py:588` — `_bind(cfg)` reference implementation (in-place mutation)
- `app.py:6684` — AUTH / RBAC ROUTES — first route that references auth functions (verify no callsite breaks)

## Tasks & Acceptance

**Execution:**
- [ ] `telesaved/auth.py` -- CREATE with `_bind(cfg)` pattern; move all 22 functions + module-level constants and dict globals; `_bind` receives `AUTH_FILE`, `load_json`, `save_json`, `now_stamp`, `normalize_source_ref`; `_bind` loads auth data and populates `auth_data` in-place
- [ ] `app.py` -- REMOVE auth section (lines 724–1046); ADD `import telesaved.auth as _auth` near other telesaved imports (before line 724); ADD `_auth._bind(cfg)` call immediately after the section was; ADD `auth_data = _auth.auth_data` alias and forwarding defs for all 22 functions; ADD `AUTH_SESSIONS`, `PERMISSION_CATALOG`, `DEFAULT_GROUPS`, `AUTH_SESSION_COOKIE`, `AUTH_SESSION_SECONDS` as aliases pointing to `_auth.*`

**Acceptance Criteria:**
- Given `app.py` is loaded, when `python -c "import ast; ast.parse(open('app.py').read()); print('OK')"` runs, then it prints `OK`.
- Given `telesaved/auth.py` exists, when `python -c "import ast; ast.parse(open('telesaved/auth.py').read()); print('OK')"` runs, then it prints `OK`.
- Given the section is extracted, when `grep -n "^AUTH_SESSION_COOKIE\|^AUTH_SESSIONS\s*=\|^_LOGIN_ATTEMPTS\s*=\|^PERMISSION_CATALOG\s*=\|^DEFAULT_GROUPS\s*=" app.py` runs, then zero matches return (all owned by `auth.py`).
- Given auth is wired, when `grep -n "auth_current_user\|auth_initialized\|user_has_permission" app.py` runs, then all matches are either the forwarding `def` or a callsite — none are bare implementations.

## Design Notes

**`_bind` cfg keys for `auth.py`:**
```
AUTH_FILE, load_json, save_json, now_stamp, normalize_source_ref
```

**In-place mutation pattern for `auth_data`:**
```python
auth_data: dict = {}

def _bind(cfg: dict):
    _cfg.clear(); _cfg.update(cfg)
    loaded = _load_auth_data()
    auth_data.clear(); auth_data.update(loaded)
```

**`app.py` aliases:**
```python
auth_data       = _auth.auth_data       # same dict object
AUTH_SESSIONS   = _auth.AUTH_SESSIONS   # same dict object
PERMISSION_CATALOG = _auth.PERMISSION_CATALOG
DEFAULT_GROUPS  = _auth.DEFAULT_GROUPS
AUTH_SESSION_COOKIE    = _auth.AUTH_SESSION_COOKIE
AUTH_SESSION_SECONDS   = _auth.AUTH_SESSION_SECONDS
```

**Forwarding functions** — all existing call sites in `app.py` resolve through these:
```python
def auth_initialized():          return _auth.auth_initialized()
def auth_current_user(request):  return _auth.auth_current_user(request)
# ... etc for all 22 functions
```

## Verification

**Commands:**
- `python -c "import ast; ast.parse(open('app.py').read()); print('app.py OK')"` -- expected: `app.py OK`
- `python -c "import ast; ast.parse(open('telesaved/auth.py').read()); print('auth.py OK')"` -- expected: `auth.py OK`
- `grep -c "^AUTH_SESSION_COOKIE\s*=\|^AUTH_SESSIONS\s*=\|^PERMISSION_CATALOG\s*=\|^DEFAULT_GROUPS\s*=" app.py` -- expected: `0`

## Spec Change Log

## Review Triage Log
