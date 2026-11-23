---
title: 'OIDC single sign-on (Keycloak federating FreeIPA)'
type: 'feature'
created: '2026-11-23'
status: 'done'
baseline_commit: 'f33278d831ee9e7096c6b31d29fc5d59826afb95'
route: 'dispatch'
review_loop_iteration: 1
context: ['{project-root}/ARCHITECTURE.md', '{project-root}/docs/ACCESS_CONTROL.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** TeleSaved users authenticate with a local password or LDAP; the organization runs FreeIPA and wants real single sign-on. FreeIPA is not an OIDC provider, so the target topology is Keycloak (user federation from FreeIPA) as the OIDC provider.

**Approach:** Add a standard OIDC Authorization Code + PKCE login, provider-agnostic (discovery-based), with IdP-group → TeleSaved-group mapping modeled on the existing LDAP backend. Local login stays available alongside OIDC.

**Decisions:** (1) User chose Keycloak-in-front-of-FreeIPA over plain LDAP or Kerberos. (2) Local username/password login remains enabled when OIDC is on (emergency access if the IdP is down). (3) Admins configure OIDC in a new Administration → Identity tab (issuer, client ID/secret, claims, group mappings, default groups, Test button); the API stays usable directly. (4) OIDC ships as its own PR after PR #7 merges. (5) Decided after review: SSO accounts are bound to issuer + subject; a different issuer or subject for an existing SSO username is `conflict`, never a takeover. Existing LDAP accounts link to SSO only when the admin setting `link_ldap_accounts` (default off) is on; once linked, LDAP password login is refused for that account. Local password accounts are always refused.

## Boundaries & Constraints

**Always:** Validate the ID token fully (signature via JWKS, `iss`, `aud`, `exp`, `nonce`) and bind each login to a single-use, expiring `state` + PKCE verifier. Never log tokens, codes or the client secret; the secret is write-only in the settings API (same `__KEEP__` pattern as LDAP `bind_password`). OIDC users get `auth_backend: "oidc"` and no password hash. A username that already belongs to a local password account is refused, never merged (same rule as `sync_ldap_user`). Session cookie goes through `make_auth_response(..., request=request)` so the HTTPS-aware Secure flag applies.

**Never:** No implicit/hybrid flow, no access-token-only login, no trusting claims from the userinfo endpoint without a validated ID token. No IdP-initiated logout or token refresh (TeleSaved sessions stay local, 12 h default). No changes to LDAP behavior. No dependency beyond `PyJWT[crypto]`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Start login | `GET /api/auth/oidc/login`, OIDC enabled | 302 to IdP `authorization_endpoint` with `state`, `nonce`, `code_challenge` (S256) | disabled/misconfigured → 302 `/?login_error=...` |
| Callback OK | valid `code`+`state` | token exchange, ID token validated, user created/updated, session cookie set, 302 `/` | N/A |
| Group mapping | `groups` claim `["admins","media"]`, mappings `admins→Admin` | user groups `Admin` + `default_groups` | no match → `default_groups` (default `Users`) |
| Bad state | unknown, reused or >10 min old `state` | no session | 302 `/?login_error=state` |
| Bad token | wrong `aud`/`iss`, expired, bad signature, nonce mismatch | no session | 302 `/?login_error=token`; reason logged without token |
| IdP error | callback with `error=access_denied` | no session | 302 `/?login_error=access_denied` |
| Local conflict | `preferred_username` equals a local password user | no session, local user untouched | 302 `/?login_error=conflict` |
| Auto-create off | unknown user, `auto_create_users=false` | no session | 302 `/?login_error=not_provisioned` |
| Disabled user | existing OIDC user with `disabled=true` | no session | 302 `/?login_error=disabled` |

</frozen-after-approval>

## Code Map

- `telesaved/auth.py:168-185` -- `identity_default_data`/`ensure_identity_defaults`: add OIDC defaults (`enabled, issuer, client_id, client_secret, scopes="openid profile email groups", username_claim="preferred_username", groups_claim="groups", group_mappings, default_groups=["Users"], auto_create_users, sync_groups_on_login, button_label="Sign in with SSO", public_base_url, ca_file`).
- `telesaved/auth.py:379` `auth_status_payload` -- set `identity_backends.oidc` from settings; add `oidc: {enabled, button_label}` for the login page.
- New `telesaved/oidc.py` -- discovery + JWKS fetch (cached, aiohttp `ClientSession`, optional `ca_file`), pending-login store (state → nonce, PKCE verifier, created; TTL 600 s, cap 1,000), authorize-URL build, code exchange (`client_secret_basic`), ID-token validation (PyJWT). `_bind(cfg)` per AD-6; no import of `app.py`.
- `app.py:781-1060` -- LDAP patterns to mirror: `ldap_sanitized_settings`, `ldap_update_settings` (write-only secret), `ldap_map_groups` (reuse logic, generalize to a shared helper), `sync_ldap_user` (model `sync_oidc_user` on it).
- `app.py:5702-5760` -- LDAP settings/test routes; add `GET/PUT /api/auth/oidc/settings` and `POST /api/auth/oidc/test` (`security.manage`), plus `GET /api/auth/oidc/login` and `GET /api/auth/oidc/callback`.
- `app.py:1227` `auth_middleware.public_paths` -- add the two OIDC login paths.
- `frontend/index.html:2913` `AdminPage` (tabs) and `:2722` `AdminUsers` -- add an `AdminIdentity` tab in the same style; uses the settings/test routes; secret field shows "set" and sends `__KEEP__` unless changed.
- `frontend/index.html:1188` `LoginPage` -- show "Sign in with SSO" button linking to `/api/auth/oidc/login` when `auth.oidc.enabled`; show `login_error` query param as a message. `checkAuth` at `:3002` already loads `/api/auth/status`.
- `tests/test_route_coverage.py` -- every new route must be added to `EXPECTED` (CI fails otherwise).
- `requirements.txt`, `requirements-dev.txt` -- add `PyJWT[crypto]>=2.8`.

## Tasks & Acceptance

**Execution:**
- [x] `requirements.txt`, `requirements-dev.txt` -- add `PyJWT[crypto]>=2.8` -- ID-token signature validation.
- [x] `telesaved/auth.py` -- OIDC defaults; status payload exposes `oidc.enabled` and `button_label` -- login page needs them.
- [x] `telesaved/oidc.py` -- module per Code Map -- isolates protocol code from `app.py`.
- [x] `app.py` -- bind `oidc` module; settings get/put/test; login + callback routes; `sync_oidc_user`; shared group-mapping helper used by LDAP and OIDC; public paths -- wiring.
- [x] `frontend/index.html` -- SSO button and `login_error` display on `LoginPage`; new Administration → Identity tab for OIDC settings with Test -- user and admin entry points.
- [x] `tests/test_oidc.py` -- in-process mock IdP (aiohttp test server serving discovery, JWKS, token endpoint; RSA key; tokens signed with PyJWT) covering every I/O matrix row plus settings secrecy -- protocol regression net.
- [x] `tests/test_route_coverage.py` -- add the five routes -- keeps coverage test green.
- [x] `docs/OIDC.md` -- Keycloak setup: FreeIPA LDAP user federation (`uid`, `cn=users,cn=accounts,…`, group mapper from `cn=groups,cn=accounts,…`), confidential client, redirect URI `https://<host>/api/auth/oidc/callback`, "Group Membership" mapper (claim `groups`, full path off), TeleSaved settings JSON, TLS-proxy note -- operator guide.
- [x] `CHANGELOG.md`, `docs/ACCESS_CONTROL.md` -- document OIDC backend -- release notes.

**Acceptance Criteria:**
- Given OIDC is enabled and configured, when a user completes login at the IdP, then they land on `/` authenticated with groups from the mapping, and `GET /api/auth/status` reports `auth_backend: "oidc"`.
- Given OIDC is enabled, when a local user logs in with username and password, then login works exactly as before.
- Given any response from the OIDC settings API, then it never contains the stored client secret.

## Implementation Notes

- `make_auth_response` gained `redirect_to=` so the callback's 302 sets the cookie through the same HTTPS-aware path.
- Extra `login_error` codes beyond the matrix: `exchange` (token endpoint refused the code, e.g. PKCE mismatch), `claims` (no usable username claim), `config` / `oidc_disabled` (login start), `bootstrap` (no local admin yet). IdP `error` values are reduced to `[a-z0-9_]`.
- Hardening: issuer and discovered endpoints must be https (http only for loopback), checked on save and at login; only RSA/EC JWS algs (no HS*, no EdDSA); JWKS `use=enc`, `oct` and `OKP` keys ignored; `azp`, when present, must equal client_id; disabled users are never re-enabled.
- Review round 1: account linking per decision (5) (`link_ldap_accounts`, default off; `sync_ldap_user` refuses `auth_backend: "oidc"`); login CSRF state cookie (`telesaved_oidc_state`, sha256(state), path `/api/auth/oidc/`, 600 s); `public_base_url` required when enabled and the only redirect-URI source (Design Notes' request-derived fallback dropped); unknown-kid JWKS refetch limited to once per 60 s per JWKS URI.
- PyJWT is imported optionally (like ldap3) so a missing package disables OIDC instead of breaking startup.
- LDAP: `ldap_map_groups` / `ldap_normalize_group_mapping` now delegate to shared `map_identity_groups` / `normalize_identity_group_mapping` (same logic).

## Spec Change Log

- **Iteration 1:** intent_gap (finding 1) resolved by the user as decision (5). bad_spec (finding 2): the spec required single-use expiring state but not browser binding; Always now implies it via decision 5 patches. KEEP: protocol module structure, mock-IdP test harness, route wiring, shared group-mapping helper, Identity tab.

## Review Triage Log

| # | Finding | Verdict | Evidence / route |
|---|---------|---------|------------------|
| 1 | Username-only matching: LDAP user silently converted to SSO and back; issuer change lets new IdP take over accounts | high | `sync_oidc_user` checks sub only when issuer matches; `sync_ldap_user` converts OIDC users. intent_gap → user chose opt-in LDAP linking + issuer/sub binding (decision 5); patched. |
| 2 | Login CSRF: `state` not bound to the browser | high | Callback pops any valid state; no cookie. Spec gap (bad_spec, non-frozen); patched with hashed-state cookie. |
| 3 | Redirect URI built from client-supplied X-Forwarded-Host | medium | Spoofable; IdP exact-match limits impact. patch: require `public_base_url`. |
| 4 | `_read_json` uses `content.read(n)`, may return a partial body | medium | aiohttp StreamReader.read(n) returns available bytes; patch: chunked read. |
| 5 | Save accepts http issuer that login rejects | low | patch. |
| 6 | SSO button shown when PyJWT missing | low | patch. |
| 7 | Raw `login_error` text shown on login page | low | Content spoofing (React escapes); patch. |
| 8 | `/oidc/test` fetches arbitrary URLs, echoes errors | low | Admin-only (`security.manage`) by design for a Test button; reject. |
| 9 | `azp` unchecked for single aud; OKP accepted w/o EdDSA; TypeErrors on odd JWKS/kid/nonce | low–medium | patch. |
| 10 | Full-path group `/other/admins` matches key `admins` | medium | Privilege via leaf collision; patch. |
| 11 | Non-dict bodies / non-list default_groups → 500 | low | patch. |
| 12 | Bool fields accept strings as truthy | low | Same as LDAP; UI sends booleans; reject. |
| 13 | Pending-login store FIFO eviction by unauthenticated flood | medium | Real; with browser-bound cookie only DoS remains; defer. |
| 14 | `login_error` not shown when a session already exists | low | reject. |
| 15 | Identity nav visible to all users | low | patch. |
| 16 | Mapping textarea breaks DN-style keys | medium | patch. |
| 17 | Logout doesn't end IdP session; sessions survive OIDC disable | medium | Spec excludes IdP logout; document; defer session revocation. |
| 18 | PyJWT unbounded in requirements vs <3 in .deb | low | patch. |
| 19 | `phase` string stale | low | reject. |
| 20 | Missing tests: legacy record upgrade, JWKS rotation, iss/discovery/S256 checks, disabled mid-login, LDAP mapping | medium | verification-gap (pre-verified); patch. |
| 21 | No frontend test harness | low | defer; manual browser check. |

## Design Notes

Redirect URI: built only from the required `public_base_url` + `/api/auth/oidc/callback` (review iteration 1 removed the request/X-Forwarded-* fallback, which any client could spoof). Each login is bound to the starting browser by a hashed-state cookie scoped to `/api/auth/oidc/`. Pending-login state lives in memory like `AUTH_SESSIONS`; a restart mid-login just yields `login_error=state`.

## Verification

**Commands:**
- `pytest -q` -- expected: all pass, including `tests/test_oidc.py` and route coverage.
