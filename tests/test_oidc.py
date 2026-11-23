"""OIDC login against an in-process mock IdP.

The mock IdP is an aiohttp test server serving discovery, JWKS and a token
endpoint. ID tokens are RS256-signed with PyJWT using a key generated per test
session. The token endpoint enforces client_secret_basic, the redirect URI and
the PKCE S256 verifier, so a passing login proves TeleSaved sent all three.

Covers every row of the I/O matrix in
_bmad-output/implementation-artifacts/spec-oidc-login.md plus settings secrecy.
"""

import base64
import json
import logging
import time
from urllib.parse import parse_qs, unquote, urlsplit

import jwt
import pytest
from aiohttp import web
from cryptography.hazmat.primitives.asymmetric import rsa

import telesaved.oidc as oidc_mod

COOKIE = "telesaved_auth_token"
CLIENT_ID = "telesaved"
CLIENT_SECRET = "idp-client-secret-value-1234567890"
KID = "test-key-1"

_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PUBLIC_BASE = "https://telesaved.example.org"
REDIRECT_URI = PUBLIC_BASE + "/api/auth/oidc/callback"


class MockIdP:
    def __init__(self):
        self.issuer = ""
        self.codes = {}
        self.token_calls = 0
        self.jwks_calls = 0
        self.issued_tokens = []
        self.discovery_overrides = {}
        self.jwks_override = None
        self.sign_key = _PRIVATE_KEY
        self.kid = KID

    def app(self):
        app = web.Application()
        app.router.add_get("/.well-known/openid-configuration", self.discovery)
        app.router.add_get("/jwks", self.jwks)
        app.router.add_post("/token", self.token)
        return app

    async def discovery(self, request):
        doc = {
            "issuer": self.issuer,
            "authorization_endpoint": self.issuer + "/auth",
            "token_endpoint": self.issuer + "/token",
            "jwks_uri": self.issuer + "/jwks",
            "response_types_supported": ["code"],
            "code_challenge_methods_supported": ["plain", "S256"],
            "scopes_supported": ["openid", "profile", "email", "groups"],
        }
        doc.update(self.discovery_overrides)
        return web.json_response(doc)

    async def jwks(self, request):
        self.jwks_calls += 1
        if self.jwks_override is not None:
            return web.json_response(self.jwks_override)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.sign_key.public_key()))
        jwk.update({"kid": self.kid, "use": "sig", "alg": "RS256"})
        # Keycloak also publishes an encryption key; it must be ignored.
        enc = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(_OTHER_KEY.public_key()))
        enc.update({"kid": "enc-key", "use": "enc", "alg": "RSA-OAEP"})
        return web.json_response({"keys": [jwk, enc]})

    async def token(self, request):
        self.token_calls += 1
        form = await request.post()
        auth = request.headers.get("Authorization", "")
        expected = "Basic " + base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
        if auth != expected:
            return web.json_response({"error": "invalid_client"}, status=401)
        entry = self.codes.pop(form.get("code"), None)
        if entry is None or form.get("grant_type") != "authorization_code":
            return web.json_response({"error": "invalid_grant"}, status=400)
        if form.get("redirect_uri") != entry["redirect_uri"]:
            return web.json_response({"error": "invalid_grant"}, status=400)
        if oidc_mod.pkce_challenge(form.get("code_verifier") or "") != entry["code_challenge"]:
            return web.json_response({"error": "invalid_grant"}, status=400)
        id_token = entry["make_token"]()
        self.issued_tokens.append(id_token)
        return web.json_response({"access_token": "opaque-access", "token_type": "Bearer", "id_token": id_token})

    def issue_code(self, auth_params, claims=None, key=None, alg="RS256", headers=None, drop=()):
        now = int(time.time())
        base = {
            "iss": self.issuer,
            "aud": CLIENT_ID,
            "sub": "sub-alice",
            "iat": now,
            "exp": now + 300,
            "nonce": auth_params["nonce"],
            "preferred_username": "alice",
            "name": "Alice Example",
            "email": "alice@example.org",
            "groups": ["admins", "media"],
        }
        base.update(claims or {})
        for name in drop:
            base.pop(name, None)
        signing_key = key if key is not None else self.sign_key
        hdr = {"kid": self.kid}
        hdr.update(headers or {})

        def make_token():
            if isinstance(hdr.get("kid"), str):
                return jwt.encode(base, signing_key, algorithm=alg, headers=hdr)
            # PyJWT refuses to encode a non-string kid; build the JWS by hand.
            def b64(obj):
                return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=")
            signing_input = b64({"alg": alg, "typ": "JWT", **hdr}) + b"." + b64(base)
            algo = jwt.algorithms.RSAAlgorithm(jwt.algorithms.RSAAlgorithm.SHA256)
            sig = base64.urlsafe_b64encode(algo.sign(signing_input, signing_key)).rstrip(b"=")
            return (signing_input + b"." + sig).decode()

        code = f"code-{len(self.codes)}-{now}-{id(base)}"
        self.codes[code] = {
            "redirect_uri": auth_params["redirect_uri"],
            "code_challenge": auth_params["code_challenge"],
            "make_token": make_token,
        }
        return code


@pytest.fixture
async def idp(aiohttp_server):
    provider = MockIdP()
    server = await aiohttp_server(provider.app())
    provider.issuer = str(server.make_url("")).rstrip("/")
    return provider


@pytest.fixture
async def sso(client, app_module, make_user, idp):
    """TeleSaved client with OIDC enabled against the mock IdP and one local admin."""
    oidc_mod._bind({})
    admin_token = make_user("Admin", username="root")
    app_module.oidc_update_settings({
        "enabled": True,
        "issuer": idp.issuer,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "group_mappings": {"admins": ["Admin"]},
        "default_groups": ["Users"],
        "public_base_url": PUBLIC_BASE,
    })
    yield {"client": client, "idp": idp, "app": app_module, "admin_token": admin_token}
    oidc_mod._bind({})


async def begin(client, **kwargs):
    resp = await client.get("/api/auth/oidc/login", allow_redirects=False, **kwargs)
    assert resp.status == 302, await resp.text()
    location = resp.headers["Location"]
    params = {k: v[0] for k, v in parse_qs(urlsplit(location).query).items()}
    params["_location"] = location
    return params


async def callback(client, query, **kwargs):
    return await client.get("/api/auth/oidc/callback", params=query, allow_redirects=False, **kwargs)


def login_error(resp):
    assert resp.status == 302
    location = resp.headers["Location"]
    assert location.startswith("/?login_error="), location
    assert COOKIE not in resp.cookies or resp.cookies[COOKIE].value == ""
    return unquote(location.split("=", 1)[1])


async def full_login(sso, **issue_kwargs):
    client, idp = sso["client"], sso["idp"]
    params = await begin(client)
    code = idp.issue_code(params, **issue_kwargs)
    return params, await callback(client, {"code": code, "state": params["state"]})


# ── Start login ───────────────────────────────────────────────────────────────

async def test_login_redirects_to_idp_with_state_nonce_and_pkce(sso):
    client, idp = sso["client"], sso["idp"]
    params = await begin(client)
    assert params["_location"].startswith(idp.issuer + "/auth?")
    assert params["response_type"] == "code"
    assert params["client_id"] == CLIENT_ID
    assert params["code_challenge_method"] == "S256"
    assert len(params["code_challenge"]) == 43
    assert params["state"] and params["nonce"] and params["state"] != params["nonce"]
    assert params["scope"].split()[0] == "openid"
    assert params["redirect_uri"] == REDIRECT_URI
    assert params["state"] in oidc_mod.PENDING_LOGINS


async def test_login_uses_public_base_url_for_redirect_uri(sso):
    sso["app"].oidc_update_settings({"public_base_url": "https://media.example.org/"})
    params = await begin(sso["client"])
    assert params["redirect_uri"] == "https://media.example.org/api/auth/oidc/callback"


async def test_forwarded_host_is_never_used_for_redirect_uri(sso):
    params = await begin(sso["client"], headers={"X-Forwarded-Proto": "http", "X-Forwarded-Host": "evil.example"})
    assert params["redirect_uri"] == REDIRECT_URI


async def test_public_base_url_is_required(sso, headers):
    app, client = sso["app"], sso["client"]
    h = headers(sso["admin_token"])
    resp = await client.put("/api/auth/oidc/settings", headers=h, json={"public_base_url": ""})
    assert resp.status == 400
    assert app.oidc_settings_current()["public_base_url"] == PUBLIC_BASE
    # Legacy stored settings without it: no SSO button, login refused.
    app.oidc_settings_current()["public_base_url"] = ""
    status = await (await client.get("/api/auth/status")).json()
    assert status["oidc"]["enabled"] is False
    resp = await client.get("/api/auth/oidc/login", allow_redirects=False)
    assert login_error(resp) == "config"


async def test_login_sets_browser_binding_state_cookie(sso):
    resp = await sso["client"].get("/api/auth/oidc/login", allow_redirects=False)
    params = {k: v[0] for k, v in parse_qs(urlsplit(resp.headers["Location"]).query).items()}
    morsel = resp.cookies[oidc_mod.STATE_COOKIE]
    assert morsel.value == oidc_mod.state_cookie_value(params["state"])
    assert morsel["httponly"] and morsel["path"] == "/api/auth/oidc/"
    assert morsel["samesite"] == "Lax" and str(morsel["max-age"]) == "600"
    assert params["state"] not in morsel.value


async def test_login_disabled_redirects_with_error(sso):
    sso["app"].oidc_update_settings({"enabled": False})
    resp = await sso["client"].get("/api/auth/oidc/login", allow_redirects=False)
    assert login_error(resp) == "oidc_disabled"


async def test_login_misconfigured_redirects_with_error(sso):
    sso["app"].oidc_update_settings({"issuer": "https://127.0.0.1:1/unreachable"})
    resp = await sso["client"].get("/api/auth/oidc/login", allow_redirects=False)
    assert login_error(resp) == "config"


async def test_plain_http_issuer_is_refused_unless_loopback(sso, headers):
    resp = await sso["client"].put("/api/auth/oidc/settings", headers=headers(sso["admin_token"]),
                                   json={"issuer": "http://idp.example.org/realms/x"})
    assert resp.status == 400
    assert sso["app"].oidc_settings_current()["issuer"] == sso["idp"].issuer


async def test_sso_button_hidden_without_pyjwt(sso, monkeypatch):
    monkeypatch.setattr(oidc_mod, "jwt", None)
    status = await (await sso["client"].get("/api/auth/status")).json()
    assert status["oidc"]["enabled"] is False
    resp = await sso["client"].get("/api/auth/oidc/login", allow_redirects=False)
    assert login_error(resp) == "config"


# ── Callback OK + group mapping ───────────────────────────────────────────────

async def test_callback_creates_user_and_sets_session(sso, headers):
    app, client = sso["app"], sso["client"]
    _, resp = await full_login(sso)
    assert resp.status == 302 and resp.headers["Location"] == "/"
    token = resp.cookies[COOKIE].value
    assert token
    morsel = resp.cookies[COOKIE]
    assert morsel["httponly"] and not morsel["secure"]  # plain http test server

    user = app.auth_data["users"]["alice"]
    assert user["auth_backend"] == "oidc"
    assert "password_hash" not in user and "password_salt" not in user
    assert user["groups"] == ["Admin", "Users"]
    assert user["oidc_sub"] == "sub-alice"
    assert user["email"] == "alice@example.org"

    status = await (await client.get("/api/auth/status", headers=headers(token))).json()
    assert status["authenticated"] is True
    assert status["user"]["auth_backend"] == "oidc"
    assert status["user"]["groups"] == ["Admin", "Users"]


async def test_callback_cookie_is_secure_behind_tls_proxy(sso):
    client, idp = sso["client"], sso["idp"]
    params = await begin(client)
    code = idp.issue_code(params)
    resp = await callback(client, {"code": code, "state": params["state"]}, headers={"X-Forwarded-Proto": "https"})
    assert resp.status == 302 and resp.headers["Location"] == "/"
    assert resp.cookies[COOKIE]["secure"]


async def test_group_mapping_no_match_gets_default_groups(sso):
    _, resp = await full_login(sso, claims={"groups": ["media"]})
    assert resp.headers["Location"] == "/"
    assert sso["app"].auth_data["users"]["alice"]["groups"] == ["Users"]


async def test_group_mapping_accepts_keycloak_full_path(sso):
    _, resp = await full_login(sso, claims={"groups": ["/admins"]})
    assert resp.headers["Location"] == "/"
    assert sso["app"].auth_data["users"]["alice"]["groups"] == ["Admin", "Users"]


async def test_group_mapping_nested_full_path_does_not_match_short_key(sso):
    _, resp = await full_login(sso, claims={"groups": ["/other/admins"]})
    assert resp.headers["Location"] == "/"
    assert sso["app"].auth_data["users"]["alice"]["groups"] == ["Users"]


async def test_group_mapping_missing_claim_gets_default_groups(sso):
    _, resp = await full_login(sso, drop=("groups",))
    assert resp.headers["Location"] == "/"
    assert sso["app"].auth_data["users"]["alice"]["groups"] == ["Users"]


async def test_groups_resync_on_next_login(sso):
    await full_login(sso)
    assert sso["app"].auth_data["users"]["alice"]["groups"] == ["Admin", "Users"]
    await full_login(sso, claims={"groups": []})
    assert sso["app"].auth_data["users"]["alice"]["groups"] == ["Users"]


async def test_groups_kept_when_sync_on_login_off(sso):
    await full_login(sso)
    sso["app"].oidc_update_settings({"sync_groups_on_login": False})
    _, resp = await full_login(sso, claims={"groups": []})
    assert resp.headers["Location"] == "/"
    assert sso["app"].auth_data["users"]["alice"]["groups"] == ["Admin", "Users"]


# ── Bad state ─────────────────────────────────────────────────────────────────

async def test_unknown_state_is_refused(sso):
    client, idp = sso["client"], sso["idp"]
    params = await begin(client)
    code = idp.issue_code(params)
    resp = await callback(client, {"code": code, "state": "not-a-real-state"})
    assert login_error(resp) == "state"
    assert idp.token_calls == 0
    assert "alice" not in sso["app"].auth_data["users"]


async def test_reused_state_is_refused(sso):
    client, idp = sso["client"], sso["idp"]
    params, first = await full_login(sso)
    assert first.headers["Location"] == "/"
    code = idp.issue_code(params)
    resp = await callback(client, {"code": code, "state": params["state"]})
    assert login_error(resp) == "state"


async def test_expired_state_is_refused(sso):
    client, idp = sso["client"], sso["idp"]
    params = await begin(client)
    oidc_mod.PENDING_LOGINS[params["state"]]["created"] -= oidc_mod.STATE_TTL_SECONDS + 1
    code = idp.issue_code(params)
    resp = await callback(client, {"code": code, "state": params["state"]})
    assert login_error(resp) == "state"
    assert params["state"] not in oidc_mod.PENDING_LOGINS


async def test_callback_without_state_cookie_is_refused(sso):
    client, idp = sso["client"], sso["idp"]
    params = await begin(client)
    code = idp.issue_code(params)
    client.session.cookie_jar.clear()  # e.g. an attacker's link opened in the victim's browser
    resp = await callback(client, {"code": code, "state": params["state"]})
    assert login_error(resp) == "state"
    assert idp.token_calls == 0
    assert "alice" not in sso["app"].auth_data["users"]


async def test_callback_with_another_logins_state_cookie_is_refused(sso):
    client, idp = sso["client"], sso["idp"]
    first = await begin(client)
    await begin(client)  # overwrites the cookie with the second login's state hash
    code = idp.issue_code(first)
    resp = await callback(client, {"code": code, "state": first["state"]})
    assert login_error(resp) == "state"


async def test_callback_clears_state_cookie(sso):
    _, resp = await full_login(sso)
    assert resp.headers["Location"] == "/"
    assert resp.cookies[oidc_mod.STATE_COOKIE].value == ""


async def test_oidc_disabled_between_login_and_callback(sso):
    client, idp = sso["client"], sso["idp"]
    params = await begin(client)
    code = idp.issue_code(params)
    sso["app"].oidc_update_settings({"enabled": False})
    resp = await callback(client, {"code": code, "state": params["state"]})
    assert login_error(resp) == "oidc_disabled"
    assert idp.token_calls == 0


def test_pending_login_store_is_capped():
    oidc_mod._bind({})
    try:
        for _ in range(oidc_mod.PENDING_LOGIN_CAP + 25):
            oidc_mod.new_pending_login("https://x/api/auth/oidc/callback")
        assert len(oidc_mod.PENDING_LOGINS) == oidc_mod.PENDING_LOGIN_CAP
    finally:
        oidc_mod._bind({})


# ── Bad token ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("case", [
    {"claims": {"aud": "someone-else"}},
    {"claims": {"iss": "https://evil.example.org"}},
    {"claims": {"exp": int(time.time()) - 3600, "iat": int(time.time()) - 7200}},
    {"claims": {"nonce": "wrong-nonce"}},
    {"drop": ("nonce",)},
    {"key": _OTHER_KEY},
    {"key": CLIENT_SECRET, "alg": "HS256"},
    {"claims": {"aud": [CLIENT_ID, "other"], "azp": "other"}},
    {"claims": {"azp": "other"}},
    {"claims": {"aud": [CLIENT_ID, "other"]}},
    {"claims": {"nonce": "nonce-\u00e9\u4e2d"}},
    {"claims": {"nonce": 12345}},
    {"headers": {"kid": 7}},
    {"headers": {"kid": "unknown-kid"}},
], ids=["aud", "iss", "expired", "nonce", "no-nonce", "bad-signature", "hs256", "azp-multi",
        "azp-single", "multi-aud-no-azp", "non-ascii-nonce", "non-string-nonce", "non-string-kid", "unknown-kid"])
async def test_bad_id_token_is_refused(sso, case, caplog):
    logger = logging.getLogger("telesaved")
    logger.addHandler(caplog.handler)
    try:
        params, resp = await full_login(sso, **case)
    finally:
        logger.removeHandler(caplog.handler)
    assert login_error(resp) == "token"
    assert "alice" not in sso["app"].auth_data["users"]
    assert "OIDC login failed (token)" in caplog.text
    for token in sso["idp"].issued_tokens:
        assert token not in caplog.text
    assert CLIENT_SECRET not in caplog.text


async def test_jwks_key_rotation_refetches_once(sso):
    client, idp = sso["client"], sso["idp"]
    _, resp = await full_login(sso)
    assert resp.headers["Location"] == "/"
    calls = idp.jwks_calls
    # IdP rotates its signing key; TeleSaved still has the old JWKS cached.
    idp.sign_key, idp.kid = _OTHER_KEY, "test-key-2"
    _, resp = await full_login(sso)
    assert resp.headers["Location"] == "/"
    assert idp.jwks_calls == calls + 1
    # A further unknown kid within the refresh window is refused without refetching.
    _, resp = await full_login(sso, headers={"kid": "test-key-3"})
    assert login_error(resp) == "token"
    assert idp.jwks_calls == calls + 1


async def test_jwks_without_keys_list_is_refused(sso):
    sso["idp"].jwks_override = {"keys": {"not": "a list"}}
    _, resp = await full_login(sso)
    assert login_error(resp) == "config"


async def test_callback_iss_param_mismatch_is_refused(sso):
    client, idp = sso["client"], sso["idp"]
    params = await begin(client)
    code = idp.issue_code(params)
    resp = await callback(client, {"code": code, "state": params["state"], "iss": "https://evil.example.org"})
    assert login_error(resp) == "token"
    assert idp.token_calls == 0


async def test_discovery_issuer_mismatch_is_config_error(sso):
    sso["idp"].discovery_overrides = {"issuer": "https://other.example.org"}
    resp = await sso["client"].get("/api/auth/oidc/login", allow_redirects=False)
    assert login_error(resp) == "config"


async def test_provider_without_s256_is_config_error(sso):
    sso["idp"].discovery_overrides = {"code_challenge_methods_supported": ["plain"]}
    resp = await sso["client"].get("/api/auth/oidc/login", allow_redirects=False)
    assert login_error(resp) == "config"


async def test_wrong_pkce_verifier_fails_exchange(sso):
    client, idp = sso["client"], sso["idp"]
    params = await begin(client)
    params = dict(params, code_challenge="A" * 43)
    code = idp.issue_code(params)
    resp = await callback(client, {"code": code, "state": params["state"]})
    assert login_error(resp) == "exchange"


async def test_callback_logs_never_contain_code_or_secret(sso, caplog):
    logger = logging.getLogger("telesaved")
    logger.addHandler(caplog.handler)
    caplog.set_level(logging.DEBUG, logger="telesaved")
    try:
        client, idp = sso["client"], sso["idp"]
        params = await begin(client)
        code = idp.issue_code(params)
        resp = await callback(client, {"code": code, "state": params["state"]})
    finally:
        logger.removeHandler(caplog.handler)
    assert resp.headers["Location"] == "/"
    assert "succeeded" in caplog.text
    for secret in [code, CLIENT_SECRET, params["nonce"], *idp.issued_tokens]:
        assert secret not in caplog.text


# ── IdP error / refusals ──────────────────────────────────────────────────────

async def test_idp_error_is_reported(sso):
    client = sso["client"]
    params = await begin(client)
    resp = await callback(client, {"error": "access_denied", "state": params["state"]})
    assert login_error(resp) == "access_denied"
    assert params["state"] not in oidc_mod.PENDING_LOGINS


async def test_idp_error_value_is_sanitized(sso):
    resp = await callback(sso["client"], {"error": "<script>alert(1)</script>"})
    assert login_error(resp) == "scriptalert1script"


async def test_local_password_user_is_never_merged(sso, make_user):
    app = sso["app"]
    make_user("Users", username="alice")
    before = dict(app.auth_data["users"]["alice"])
    _, resp = await full_login(sso)
    assert login_error(resp) == "conflict"
    assert app.auth_data["users"]["alice"] == before
    assert app.auth_data["users"]["alice"]["auth_backend"] == "local"


async def test_different_subject_with_same_username_is_refused(sso):
    await full_login(sso)
    _, resp = await full_login(sso, claims={"sub": "someone-else"})
    assert login_error(resp) == "conflict"
    assert sso["app"].auth_data["users"]["alice"]["oidc_sub"] == "sub-alice"


async def test_different_issuer_with_same_username_is_refused(sso):
    app = sso["app"]
    await full_login(sso)
    app.auth_data["users"]["alice"]["oidc_issuer"] = "https://old-idp.example.org"
    _, resp = await full_login(sso)
    assert login_error(resp) == "conflict"
    assert app.auth_data["users"]["alice"]["oidc_issuer"] == "https://old-idp.example.org"


def _ldap_alice(app):
    app.sync_ldap_user("alice", {"dn": "uid=alice,cn=users,cn=accounts,dc=x", "display_name": "Alice",
                                 "ldap_groups": ["admins"]}, ["Manager"])
    return app.auth_data["users"]["alice"]


async def test_ldap_account_is_not_linked_by_default(sso):
    app = sso["app"]
    before = dict(_ldap_alice(app))
    _, resp = await full_login(sso)
    assert login_error(resp) == "conflict"
    assert app.auth_data["users"]["alice"] == before


async def test_ldap_account_links_when_enabled_and_ldap_login_is_then_refused(sso):
    app = sso["app"]
    _ldap_alice(app)
    app.oidc_update_settings({"link_ldap_accounts": True})
    _, resp = await full_login(sso)
    assert resp.headers["Location"] == "/"
    user = app.auth_data["users"]["alice"]
    assert user["auth_backend"] == "oidc" and user["oidc_sub"] == "sub-alice"
    assert "ldap_dn" not in user and "ldap_groups" not in user
    assert user["groups"] == ["Admin", "Users"]
    with pytest.raises(RuntimeError, match="single sign-on"):
        _ldap_alice(app)
    assert app.auth_data["users"]["alice"]["auth_backend"] == "oidc"


async def test_local_account_without_password_is_refused(sso):
    app = sso["app"]
    app.auth_data["users"]["alice"] = {"username": "alice", "groups": ["Users"], "auth_backend": "local"}
    _, resp = await full_login(sso)
    assert login_error(resp) == "conflict"
    assert app.auth_data["users"]["alice"]["auth_backend"] == "local"


async def test_auto_create_off_refuses_unknown_user(sso):
    sso["app"].oidc_update_settings({"auto_create_users": False})
    _, resp = await full_login(sso)
    assert login_error(resp) == "not_provisioned"
    assert "alice" not in sso["app"].auth_data["users"]


async def test_disabled_oidc_user_is_refused_and_stays_disabled(sso):
    app = sso["app"]
    await full_login(sso)
    app.auth_data["users"]["alice"]["disabled"] = True
    _, resp = await full_login(sso)
    assert login_error(resp) == "disabled"
    assert app.auth_data["users"]["alice"]["disabled"] is True


async def test_missing_username_claim_is_refused(sso):
    _, resp = await full_login(sso, drop=("preferred_username",))
    assert login_error(resp) == "claims"


# ── Local login alongside OIDC ────────────────────────────────────────────────

async def test_local_login_still_works_with_oidc_enabled(sso, make_user, headers):
    make_user("Users", username="bob", password="bob-password-1")
    client = sso["client"]
    resp = await client.post("/api/auth/login", json={"username": "bob", "password": "bob-password-1"})
    assert resp.status == 200
    token = resp.cookies[COOKIE].value
    status = await (await client.get("/api/auth/status", headers=headers(token))).json()
    assert status["authenticated"] is True
    assert status["user"]["auth_backend"] == "local"
    assert sso["app"].auth_data["users"]["bob"]["password_hash"]


async def test_status_exposes_sso_button_for_login_page(sso):
    body = await (await sso["client"].get("/api/auth/status")).json()
    assert body["oidc"] == {"enabled": True, "button_label": "Sign in with SSO"}
    assert body["identity_backends"]["oidc"] is True
    sso["app"].oidc_update_settings({"enabled": False})
    body = await (await sso["client"].get("/api/auth/status")).json()
    assert body["oidc"]["enabled"] is False


# ── Settings API ──────────────────────────────────────────────────────────────

async def test_settings_never_return_client_secret(sso, headers):
    client, app = sso["client"], sso["app"]
    h = headers(sso["admin_token"])

    resp = await client.get("/api/auth/oidc/settings", headers=h)
    text = await resp.text()
    assert resp.status == 200 and CLIENT_SECRET not in text
    body = json.loads(text)
    assert body["settings"]["client_secret"] == ""
    assert body["settings"]["client_secret_set"] is True
    assert body["redirect_uri"] == REDIRECT_URI

    # __KEEP__ and a missing field both keep the stored secret.
    resp = await client.put("/api/auth/oidc/settings", headers=h,
                            json={"settings": {"client_secret": "__KEEP__", "button_label": "Company SSO"}})
    assert resp.status == 200 and CLIENT_SECRET not in await resp.text()
    assert app.oidc_settings_current()["client_secret"] == CLIENT_SECRET
    resp = await client.put("/api/auth/oidc/settings", headers=h, json={"scopes": "openid profile"})
    assert app.oidc_settings_current()["client_secret"] == CLIENT_SECRET

    # A new value replaces it and is not echoed back.
    resp = await client.put("/api/auth/oidc/settings", headers=h, json={"client_secret": "rotated-secret-456"})
    assert resp.status == 200 and "rotated-secret-456" not in await resp.text()
    assert app.oidc_settings_current()["client_secret"] == "rotated-secret-456"

    for path in ("/api/auth/admin", "/api/auth/status"):
        text = await (await client.get(path, headers=h)).text()
        assert "rotated-secret-456" not in text

    resp = await client.post("/api/auth/oidc/test", headers=h, json={})
    text = await resp.text()
    assert "rotated-secret-456" not in text

    resp = await client.put("/api/auth/oidc/settings", headers=h, json={"client_secret": "", "clear_client_secret": True})
    assert app.oidc_settings_current()["client_secret"] == ""


async def test_settings_validation_and_group_filtering(sso, headers):
    client, app = sso["client"], sso["app"]
    h = headers(sso["admin_token"])
    resp = await client.put("/api/auth/oidc/settings", headers=h, json={"public_base_url": "javascript:alert(1)"})
    assert resp.status == 400
    for bad in ([1, 2], "text", {"settings": [1]}, {"default_groups": "Admin"}, {"group_mappings": ["x"]}):
        resp = await client.put("/api/auth/oidc/settings", headers=h, json=bad)
        assert resp.status == 400, bad
        resp = await client.post("/api/auth/oidc/test", headers=h, json=bad if not isinstance(bad, dict) or "settings" in bad else {"settings": bad})
        assert resp.status == 400, bad
    resp = await client.put("/api/auth/oidc/settings", headers=h, json={
        "group_mappings": {"admins": ["Admin", "NoSuchGroup"], "ghosts": ["NoSuchGroup"]},
        "default_groups": ["NoSuchGroup"],
        "scopes": "profile email",
    })
    assert resp.status == 200
    settings = app.oidc_settings_current()
    assert settings["group_mappings"] == {"admins": ["Admin"]}
    assert settings["default_groups"] == ["Users"]
    assert settings["scopes"].split()[0] == "openid"


async def test_settings_routes_require_security_manage(sso, make_user, headers):
    client = sso["client"]
    h = headers(make_user("Manager"))
    assert (await client.get("/api/auth/oidc/settings", headers=h)).status == 403
    assert (await client.put("/api/auth/oidc/settings", headers=h, json={"enabled": False})).status == 403
    assert (await client.post("/api/auth/oidc/test", headers=h, json={})).status == 403
    assert (await client.get("/api/auth/oidc/settings")).status == 401
    assert sso["app"].oidc_settings_current()["enabled"] is True


async def test_test_button_checks_discovery_and_jwks(sso, headers):
    client, idp = sso["client"], sso["idp"]
    h = headers(sso["admin_token"])
    resp = await client.post("/api/auth/oidc/test", headers=h, json={})
    body = await resp.json()
    assert resp.status == 200, body
    assert body["result"]["discovery"] and body["result"]["jwks"]
    assert body["result"]["jwks_keys"] == 1  # the enc key is ignored
    assert body["result"]["issuer"] == idp.issuer

    # Unsaved settings are tested without being stored.
    resp = await client.post("/api/auth/oidc/test", headers=h,
                             json={"settings": {"issuer": "https://127.0.0.1:1/nowhere"}})
    assert resp.status == 400
    assert (await resp.json())["result"]["error"]
    assert sso["app"].oidc_settings_current()["issuer"] == idp.issuer
