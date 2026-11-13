---
title: 'Admin audit log — who changed what and when'
type: 'feature'
created: '2026-09-01'
status: 'ready-for-dev'
review_loop_iteration: 0
context: ['ARCHITECTURE.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Multi-user instances have no record of who performed significant
administrative actions (user/group changes, backup/restore, scoring resets, source
management, system settings). Admins cannot investigate incidents or verify that
permissions are being used as intended.

**Approach:** Add a `audit_log` SQLite table (in `state.py`). Instrument ~15 high-value
admin routes in `app.py` with an `append_audit_log(actor, action, detail)` call after
each successful mutation. Add `GET /api/admin/audit-log` to expose the log. Add an
"Audit Log" tab to the existing `AdminLogs` frontend component.

## Boundaries & Constraints

**Always:**
- All SQLite writes go through `telesaved/state.py` (AD-3). `append_audit_log` lives there.
- `audit_log` table uses `CREATE TABLE IF NOT EXISTS` — no schema version bump needed.
- Only **successful** mutations are logged (after `return web.json_response({"ok": True, ...})`
  is about to fire, not on error paths).
- Actor is `auth_current_user(request).get("username") or "system"`.
- `detail` is a short plain-English string (≤ 240 chars), not a JSON dump of entire payloads.
- The endpoint is read-only and requires `system.admin` permission.
- Log is append-only; no delete/clear endpoint is provided.

**Never:**
- Do not log read-only (GET) routes.
- Do not log per-item tag edits, playback, or library browsing — only admin-tier actions.
- Do not store passwords, tokens, or credential values in `detail`.
- Do not change any existing route's error handling or return value.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Action succeeds | `append_audit_log` called after `ok: True` | Row inserted; `GET /api/admin/audit-log` returns it | — |
| `append_audit_log` DB write fails | SQLite error | Warning logged; route response unchanged (best-effort) | Warning-only |
| Actor not authenticated | `auth_current_user` returns None | `actor = "system"` used | — |
| Audit log query with `limit` | `?limit=50` | Returns last 50 rows newest-first | Clamped to 1–1000 |
| Empty audit log | No rows | Returns `{"ok": true, "entries": [], "total": 0}` | — |

</frozen-after-approval>

## Code Map

- `telesaved/state.py:152-168` — `init_sqlite_db()` `tag_suggestions` block + commit; `audit_log` table goes after `tag_suggestions`, before the `conn.commit()`.
- `telesaved/state.py:517+` — after `export_tag_labels()`; add `append_audit_log()` and `get_audit_log()` helpers here.
- `app.py:1981+` — `# SQLITE STATE BACKEND` forwarding aliases; add `append_audit_log` and `get_audit_log` aliases.
- `app.py:8699` — `api_admin_logs` GET route; add `GET /api/admin/audit-log` route nearby.
- `app.py:7504–7651` — user/group/source-access routes; instrument successful paths.
- `app.py:8641–8695` — system/scoring/scoring-reset routes; instrument successful paths.
- `app.py:8734–8755` — reprocess-all, migrate-library, index-sweep routes.
- `app.py:9044–9095` — backup create/restore routes.
- `app.py:7861–7920` — tag create/delete routes.
- `frontend/index.html:2833–2872` — `AdminLogs` component; add "Audit Log" tab.

## Tasks & Acceptance

**Execution:**
- [ ] `telesaved/state.py` -- Add `audit_log` table (`CREATE TABLE IF NOT EXISTS`) to `init_sqlite_db()` after the `tag_suggestions` block. Schema: `id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL`. Add `CREATE INDEX IF NOT EXISTS idx_audit_log_created_at ON audit_log(created_at)`.
- [ ] `telesaved/state.py` -- Add `append_audit_log(actor, action, detail="")` — best-effort INSERT with `try/except` (warning on failure, never raises). Add `get_audit_log(limit=200)` — SELECT newest-first with LIMIT.
- [ ] `app.py` -- Add forwarding aliases `append_audit_log` and `get_audit_log` in the `# SQLITE STATE BACKEND` alias block.
- [ ] `app.py` -- Add `GET /api/admin/audit-log` route (requires `system.admin`): returns `{"ok": true, "entries": [...], "total": N}`.
- [ ] `app.py` -- Instrument these 15 routes (on success only, using `actor = (auth_current_user(request) or {}).get("username") or "system"`):
  - `api_auth_user_create` → `"user.create"`, `f"Created user {username}"`
  - `api_auth_user_update` → `"user.update"`, `f"Updated user {user_id}"`
  - `api_auth_user_delete` → `"user.delete"`, `f"Deleted user {user_id}"`
  - `api_auth_group_create` → `"group.create"`, `f"Created group {name}"`
  - `api_auth_group_update` → `"group.update"`, `f"Updated group {group_name}"`
  - `api_auth_group_delete` → `"group.delete"`, `f"Deleted group {group_name}"`
  - `api_auth_source_access_update` → `"source_acl.update"`, `f"Updated ACL for {target_type}:{target}"`
  - `api_admin_system_post` → `"system.settings"`, `f"Updated system settings"`
  - `api_admin_scoring_post` → `"scoring.update"`, `f"Updated tag scoring weights"`
  - `api_admin_scoring_reset` → `"scoring.reset"`, `"Reset scoring to defaults"`
  - `api_admin_reprocess_all` → `"library.reprocess"`, `"Triggered full library reprocess"`
  - `api_admin_backups_create` → `"backup.create"`, `f"Created backup {filename}"`
  - `api_admin_backups_restore` → `"backup.restore"`, `f"Restored backup"`
  - `api_tags` (POST — tag create) → `"tag.create"`, `f"Created tag {tag_name}"`
  - `api_tags_delete` (tag delete, if exists) → `"tag.delete"`, `f"Deleted tag {tag}"`
- [ ] `frontend/index.html` -- Add "Audit Log" tab to `AdminLogs`: fetch `GET /api/admin/audit-log`, render as a table with columns `When | Actor | Action | Detail`. Add the tab button alongside "System Logs" and "Feedback". Default tab stays "logs".

**Acceptance Criteria:**
- Given an admin creates a user, when `GET /api/admin/audit-log` is called, then an entry with `action: "user.create"` appears.
- Given a non-admin calls `GET /api/admin/audit-log`, then a 403 is returned.
- Given `append_audit_log` fails (DB error), when the admin action is performed, then the action still returns `{"ok": true}` (audit failure is non-fatal).
- Given the Logs page is open, when the "Audit Log" tab is clicked, then a table of entries appears (or "No audit events yet." if empty).

## Verification

**Commands:**
- `python3 -c "import ast; ast.parse(open('app.py').read()); print('OK')"` — expected: `OK`
- `python3 -c "import ast; ast.parse(open('telesaved/state.py').read()); print('OK')"` — expected: `OK`
- `grep -n "append_audit_log" app.py | wc -l` — expected: `≥16` (1 alias + 15 callsites)
- `grep -n "audit_log" telesaved/state.py | wc -l` — expected: `≥5`
