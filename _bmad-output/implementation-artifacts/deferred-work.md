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
- source_spec: `_bmad-output/implementation-artifacts/spec-oidc-login.md`
  summary: Make the pending-login store flood-resistant (per-client rate limit or stateless signed state).
  evidence: 1,000-entry FIFO can be filled by unauthenticated requests, failing real users' callbacks with `state`.
- source_spec: `_bmad-output/implementation-artifacts/spec-oidc-login.md`
  summary: RP-initiated logout (end_session_endpoint) and revoking OIDC sessions when SSO is disabled or a user is removed at the IdP.
  evidence: TeleSaved logout leaves the IdP session, so the SSO button signs straight back in; sessions last up to 12 h after IdP-side removal.
- source_spec: `_bmad-output/implementation-artifacts/spec-oidc-login.md`
  summary: Add a frontend test harness for the login page and Identity tab.
  evidence: repo has no JS tests; SSO button, error messages and settings form are verified manually only.
