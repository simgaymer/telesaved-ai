# OIDC single sign-on (Keycloak in front of FreeIPA)

TeleSaved can sign users in with OpenID Connect (Authorization Code flow with PKCE S256). The
implementation is provider-agnostic and discovery-based, so any standards-compliant provider
works. The documented topology is:

```
browser ──► TeleSaved ──(OIDC)──► Keycloak ──(LDAP user federation)──► FreeIPA
```

FreeIPA is not an OIDC provider, so Keycloak federates its users and groups and issues the ID
tokens. Local username/password login keeps working alongside OIDC; keep at least one local
Admin account as emergency access for when the IdP is down.

## What TeleSaved does

- `GET /api/auth/oidc/login` redirects to the IdP with a fresh `state`, `nonce` and PKCE
  `code_challenge`, and sets a short-lived HttpOnly cookie (path `/api/auth/oidc/`) holding a hash
  of the `state`. The callback only accepts a `state` whose cookie this browser holds, so a login
  started by someone else cannot be completed in your browser (login CSRF). Pending logins live in
  memory for 10 minutes and are single-use; a restart mid-login, or starting a second login in
  another tab, just shows "sign-in attempt expired".
- `GET /api/auth/oidc/callback` exchanges the code (`client_secret_basic`), then validates the ID
  token: signature against the provider's JWKS (asymmetric algorithms only), `iss`, `aud`
  (and `azp` for multi-audience tokens), `exp`/`iat` with 60 s leeway, and `nonce`.
- The username comes from `username_claim` (default `preferred_username`), groups from
  `groups_claim` (default `groups`). Groups map to TeleSaved groups like the LDAP backend:
  case-insensitive, and `default_groups` are always added.
- Users are stored with `auth_backend: "oidc"` and no password. A disabled TeleSaved account
  stays disabled.
- **`auto_create_users` is on by default:** every user who can sign in to the Keycloak client gets
  a TeleSaved account with `default_groups` (default `Users`, i.e. library access). Restrict who
  can use the client in Keycloak (e.g. a client role required by an authentication-flow condition,
  or a dedicated realm), or turn auto-create off and pre-provision users.
- The TeleSaved session is local (12 h default). There is no token refresh, and **signing out of
  TeleSaved does not end the Keycloak session**: the next "Sign in with SSO" may log the user
  straight back in. Sign out of Keycloak too on shared machines.

### Account linking

SSO accounts are bound to the ID token's issuer and subject (`iss` + `sub`) on first login.

| Existing TeleSaved account with that username | Result |
|---|---|
| none | created if `auto_create_users` is on, else `not_provisioned` |
| OIDC, same issuer and subject | signed in, profile and groups updated |
| OIDC, different issuer or subject | `conflict` (never taken over; changing IdP or realm needs an admin to delete or rename the old account) |
| LDAP | `conflict`, unless `link_ldap_accounts` is on: then it is converted to OIDC and its LDAP password login is refused from then on |
| local (with or without password) | `conflict`, always |

Failures redirect to `/?login_error=<code>` and the login page shows a message. Codes:
`state`, `token`, `exchange`, `access_denied` (or another IdP error), `conflict`,
`not_provisioned`, `disabled`, `claims`, `config`, `oidc_disabled`, `bootstrap`. Unknown codes
show a generic message. The reason is
logged (never tokens, codes or the client secret).

## 1. Keycloak: federate FreeIPA

In your realm, **User federation → Add LDAP provider**:

| Setting | Value |
|---|---|
| Vendor | Red Hat Directory Server |
| Connection URL | `ldaps://ipa.example.org` |
| Bind DN | `uid=keycloak,cn=sysaccounts,cn=etc,dc=example,dc=org` (a read-only system account) |
| Edit mode | `READ_ONLY` |
| Users DN | `cn=users,cn=accounts,dc=example,dc=org` |
| Username LDAP attribute | `uid` |
| RDN LDAP attribute | `uid` |
| UUID LDAP attribute | `ipaUniqueID` |
| User object classes | `inetOrgPerson, organizationalPerson` |

Then **Mappers → Add mapper**, type **group-ldap-mapper**:

| Setting | Value |
|---|---|
| LDAP Groups DN | `cn=groups,cn=accounts,dc=example,dc=org` |
| Group name LDAP attribute | `cn` |
| Group object classes | `groupOfNames` |
| Membership LDAP attribute | `member` |
| Membership attribute type | `DN` |
| Mode | `READ_ONLY` |

Click **Sync all users** and check that users and their groups appear.

## 2. Keycloak: TeleSaved client

**Clients → Create client**:

- Client type `OpenID Connect`, Client ID `telesaved`.
- **Client authentication: On** (confidential client), Standard flow on, Direct access grants off,
  Implicit flow off.
- Valid redirect URIs: `https://<telesaved-host>/api/auth/oidc/callback`
- Advanced → Proof Key for Code Exchange Code Challenge Method: `S256`.
- Copy the secret from the **Credentials** tab.

Add the groups claim: **Client scopes → telesaved-dedicated → Add mapper → By configuration →
Group Membership**:

- Token Claim Name: `groups`
- Full group path: **Off** (TeleSaved also accepts `/admins` full paths, but short names keep
  mappings readable)
- Add to ID token: **On**

The issuer is `https://<keycloak-host>/realms/<realm>`.

## 3. TeleSaved settings

As an Admin, open **Administration → Identity**, fill in the fields, click **Test** (it fetches
discovery and JWKS and shows the redirect URI to register), then **Save** with **Enable OIDC
login** ticked. The same settings can be set through the API (requires `security.manage`):

```bash
curl -X PUT https://telesaved.example.org/api/auth/oidc/settings \
  -H 'Content-Type: application/json' -H "X-Telesaved-Auth-Token: $TOKEN" \
  -d '{
    "enabled": true,
    "issuer": "https://sso.example.org/realms/example",
    "client_id": "telesaved",
    "client_secret": "<from Keycloak>",
    "scopes": "openid profile email groups",
    "username_claim": "preferred_username",
    "groups_claim": "groups",
    "group_mappings": {"admins": ["Admin"], "media-managers": ["Manager"]},
    "default_groups": ["Users"],
    "auto_create_users": true,
    "sync_groups_on_login": true,
    "link_ldap_accounts": false,
    "button_label": "Sign in with FreeIPA",
    "public_base_url": "https://telesaved.example.org",
    "ca_file": ""
  }'
```

`client_secret` is write-only: responses return `"client_secret": ""` and
`"client_secret_set": true`. Send `"__KEEP__"` (or omit the field) to keep it, or
`"client_secret": "", "clear_client_secret": true` to remove it.

Other routes: `GET /api/auth/oidc/settings`, `POST /api/auth/oidc/test` (optionally with
`{"settings": {...}}` to test unsaved values).

If `groups` is not a scope your provider knows, drop it from `scopes`; with the Keycloak mapper
above the claim is added regardless.

## TLS and reverse proxies

- The issuer, and every endpoint discovery returns, must be `https://`. Plain `http://` is only
  accepted for loopback hosts (local testing).
- If Keycloak uses the FreeIPA CA (or another private CA), set `ca_file` to a PEM bundle readable
  by the TeleSaved service, e.g. `/etc/ipa/ca.crt`.
- `public_base_url` is **required** when OIDC is enabled: it is the address users open TeleSaved
  at (e.g. `https://telesaved.example.org`), and the redirect URI is always
  `public_base_url + /api/auth/oidc/callback`. It is never derived from `Host` or
  `X-Forwarded-Host`, which clients control. Register exactly that URI in Keycloak.
- The session and state cookies are `Secure` when the request arrived over HTTPS (directly or
  with `X-Forwarded-Proto: https` from the proxy).

## Dependency

OIDC needs `PyJWT[crypto]>=2.8` (in `requirements.txt`). If it is missing, TeleSaved still starts;
OIDC login and **Test** report that PyJWT is not installed.
