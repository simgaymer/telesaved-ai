# Deferred work

- source_spec: `_bmad-output/implementation-artifacts/spec-auth-test-harness.md`
  summary: Move the login lockout check into a `telesaved.auth` helper and drop the `_LOGIN_*` aliases from app.py.
  evidence: app.py:api_auth_login re-implements the check inline; that split let the v4.5.13 extraction break login silently.
- source_spec: `_bmad-output/implementation-artifacts/spec-auth-test-harness.md`
  summary: Bound `_LOGIN_ATTEMPTS` growth and reconsider per-username lockout; test the LDAP login branch's failure accounting.
  evidence: every failed username adds an entry; anyone can lock out a known account for 5 minutes.
- source_spec: `_bmad-output/implementation-artifacts/spec-auth-test-harness.md`
  summary: Add a test that every registered `/api/*` route has an explicit expected permission.
  evidence: unmapped routes fall through to `library.view`, so a new admin-only route could ship open to all users.
- source_spec: `_bmad-output/implementation-artifacts/spec-auth-test-harness.md`
  summary: Give app.py a config-path seam (e.g. TELESAVED_CONFIG_FILE) so tests stop patching os.path.exists.
  evidence: tests/conftest.py hides config.json only because read_app_config uses os.path.exists.
- source_spec: `_bmad-output/implementation-artifacts/spec-auth-test-harness.md`
  summary: Apply the same HTTPS-aware Secure flag to the privacy-lock cookie.
  evidence: app.py privacy cookie is set without Secure.
