"""HTTP-level characterization of auth + RBAC through the real middlewares/routes."""

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from pinned import EXPECTED_DEFAULT_GROUP_PERMISSIONS as GROUP_PERMS

COOKIE = "telesaved_auth_token"


def _set_cookie_headers(resp):
    return [h for h in resp.headers.getall("Set-Cookie", []) if h.startswith(COOKIE + "=")]


def _cookie_attrs(header):
    parts = [p.strip() for p in header.split(";")]
    return {p.split("=", 1)[0].lower(): (p.split("=", 1)[1] if "=" in p else True) for p in parts[1:]}


# ── required_permission_for_request: one row per branch ──────────────────────
# (method, path, required permission). Removing or reordering a branch in
# app.required_permission_for_request fails the row(s) named after the route.
PERMISSION_TABLE = [
    ("GET", "/api/auth/status", None),
    ("POST", "/api/auth/users", None),
    ("DELETE", "/api/auth/groups/Custom", None),
    ("POST", "/api/setup/save", "system.admin"),
    ("GET", "/api/setup/status", "library.view"),
    ("GET", "/api/admin/stats", "system.admin"),
    ("POST", "/api/admin/settings", "system.admin"),
    ("GET", "/api/admin/audit-log", "system.admin"),
    ("GET", "/api/jobs", "jobs.manage"),
    ("POST", "/api/jobs/1/pause", "jobs.manage"),
    ("GET", "/api/downloads", "library.view"),
    ("POST", "/api/downloads/mode", "jobs.manage"),
    ("GET", "/api/sources", "sources.manage"),
    ("POST", "/api/sources", "sources.manage"),
    ("GET", "/api/source-intelligence", "sources.manage"),
    ("POST", "/api/source-pipelines/run", "sources.manage"),
    ("GET", "/api/telegram/dialogs", "sources.manage"),
    ("GET", "/api/privacy/status", "library.view"),
    ("POST", "/api/privacy/settings", "system.admin"),
    ("POST", "/api/privacy/unlock", "system.admin"),
    ("GET", "/api/collections", "admin.view"),
    ("POST", "/api/collections", "content.manage"),
    ("DELETE", "/api/collections/1", "content.manage"),
    ("GET", "/api/personal", "admin.view"),
    ("POST", "/api/personal/x", "content.manage"),
    ("GET", "/api/watch-later", "admin.view"),
    ("POST", "/api/watch-later", "content.manage"),
    ("POST", "/api/tags/suggest", "tags.manage"),
    ("POST", "/api/tags/suggestions/1/approve", "tags.manage"),  # POST /suggest* wins
    ("POST", "/api/tags/suggestions/1/reject", "tags.manage"),
    ("GET", "/api/tags/suggestions", "system.admin"),
    ("POST", "/api/tags/migrate-labels", "system.admin"),
    ("GET", "/api/tags/labels", "system.admin"),
    ("GET", "/api/tags", "library.view"),
    ("POST", "/api/tags", "tags.manage"),
    ("POST", "/api/tags/approve/1", "tags.manage"),
    ("DELETE", "/api/tags/Funny", "tags.manage"),
    ("GET", "/api/rules", "content.manage"),
    ("POST", "/api/rules", "content.manage"),
    ("GET", "/api/review/queue", "admin.view"),
    ("POST", "/api/review/accept", "tags.manage"),
    ("GET", "/api/audio/status", "admin.view"),
    ("POST", "/api/audio/analyze", "admin.view"),
    ("GET", "/api/duplicates", "admin.view"),
    ("POST", "/api/duplicates/resolve", "content.delete"),
    ("GET", "/api/cleanup", "admin.view"),
    ("POST", "/api/cleanup/apply", "content.delete"),
    ("GET", "/api/smart-cleanup", "admin.view"),
    ("POST", "/api/smart-cleanup/apply", "content.delete"),
    ("GET", "/api/trash", "content.delete"),
    ("DELETE", "/api/trash/1", "content.delete"),
    ("POST", "/api/delete", "content.delete"),
    ("POST", "/api/bulk/tags", "tags.manage"),
    ("POST", "/api/bulk/move", "content.manage"),
    ("GET", "/api/item/a.mp4", "library.view"),
    ("POST", "/api/item/a.mp4/tags", "tags.manage"),
    ("DELETE", "/api/item/a.mp4", "content.delete"),
    ("POST", "/api/item/a.mp4/delete", "content.delete"),
    ("POST", "/api/item/a.mp4/rename", "content.manage"),
    ("POST", "/api/item/a.mp4/notes", "content.manage"),
    ("GET", "/api/export", "system.admin"),
    ("POST", "/api/import", "system.admin"),
    ("GET", "/api/library", "library.view"),
    ("GET", "/api/search", "library.view"),
    ("GET", "/api/some-future-route", "library.view"),
]
TABLE_IDS = [f"{m} {p}" for m, p, _ in PERMISSION_TABLE]


@pytest.mark.parametrize("method, path, expected", PERMISSION_TABLE, ids=TABLE_IDS)
def test_required_permission_for_request(app_module, method, path, expected):
    request = make_mocked_request(method, path)
    assert app_module.required_permission_for_request(request) == expected, f"{method} {path}"


# ── Middleware matrix: every route row x every default group ─────────────────

@pytest.fixture
async def probe_client(aiohttp_client, app_module, fresh_auth):
    """Real auth/privacy middlewares in front of a catch-all handler, so a 200
    means "passed the middleware" without running the production handler."""
    async def reached(request):
        return web.json_response({"ok": True, "reached": True})

    probe = web.Application(middlewares=[app_module.auth_middleware, app_module.privacy_middleware])
    probe.router.add_route("*", "/{tail:.*}", reached)
    return await aiohttp_client(probe)


@pytest.mark.parametrize("method, path, required", PERMISSION_TABLE, ids=TABLE_IDS)
async def test_middleware_enforces_route_map_per_group(probe_client, fresh_auth, headers, method, path, required):
    # Users inserted directly (no PBKDF2) to keep this 3x-wide matrix fast.
    tokens = {}
    for group in GROUP_PERMS:
        username = f"probe-{group.lower()}"
        fresh_auth.auth_data["users"][username] = {"username": username, "groups": [group]}
        tokens[group], _ = fresh_auth.create_auth_session(username)
    for group, token in tokens.items():
        perms = GROUP_PERMS[group]
        allowed = required is None or "*" in perms or required in perms
        resp = await probe_client.request(method, path, headers=headers(token))
        body = await resp.json()
        if allowed:
            assert resp.status == 200, f"{group} should pass middleware for {method} {path}: {body}"
        else:
            assert resp.status == 403, f"{group} should be forbidden for {method} {path}: {body}"
            assert body == {"ok": False, "forbidden": True, "error": f"Permission required:{required}"}


# ── Bootstrap state ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("method, path", [
    ("GET", "/api/library"),
    ("GET", "/api/admin/stats"),
    ("POST", "/api/sources"),
    ("GET", "/api/auth/admin"),
    ("POST", "/api/auth/logout"),
    ("GET", "/api/privacy/status"),
])
async def test_no_users_requires_bootstrap(client, method, path):
    resp = await client.request(method, path)
    assert resp.status == 401
    body = await resp.json()
    assert body["bootstrap_required"] is True and body["ok"] is False


@pytest.mark.parametrize("path", ["/api/auth/status", "/api/health", "/api/setup/status"])
async def test_public_paths_open_before_bootstrap(client, path):
    resp = await client.get(path)
    assert resp.status == 200


async def test_non_api_paths_not_gated_before_bootstrap(client):
    # Before any user exists, source ACL is open and /files/ reaches the handler.
    resp = await client.get("/files/missing.mp4")
    assert resp.status == 404


async def test_bootstrap_creates_admin_and_sets_cookie(client, app_module):
    resp = await client.post("/api/auth/bootstrap", json={"username": "root", "password": "password123"})
    assert resp.status == 200
    body = await resp.json()
    assert body["user"]["groups"] == ["Admin"]
    assert body["authenticated"] is False  # payload is computed from the request, which had no cookie yet
    cookies = _set_cookie_headers(resp)
    assert len(cookies) == 1
    attrs = _cookie_attrs(cookies[0])
    assert attrs.get("httponly") is True
    assert "secure" not in attrs  # plain HTTP: browsers would drop a Secure cookie
    assert attrs.get("samesite") == "Lax"
    assert attrs.get("path") == "/"
    token = cookies[0].split(";", 1)[0].split("=", 1)[1]
    assert token in app_module.AUTH_SESSIONS

    again = await client.post("/api/auth/bootstrap", json={"username": "x", "password": "password123"})
    assert again.status == 409


async def test_bootstrap_behind_tls_proxy_sets_secure_cookie(client):
    resp = await client.post("/api/auth/bootstrap", json={"username": "root", "password": "password123"},
                             headers={"X-Forwarded-Proto": "https"})
    assert resp.status == 200
    assert _cookie_attrs(_set_cookie_headers(resp)[0]).get("secure") is True


async def test_bootstrap_validation_error(client):
    resp = await client.post("/api/auth/bootstrap", json={"password": "short"})
    assert resp.status == 400


# ── Unauthenticated ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "/api/library", "/api/admin/stats", "/files/x", "/thumbs/x.jpg", "/previews/x.gif", "/scenes/x.jpg",
])
async def test_unauthenticated_requires_login(client, make_user, path):
    make_user("Admin")
    resp = await client.get(path)
    assert resp.status == 401
    body = await resp.json()
    assert body["auth_required"] is True and body["ok"] is False


async def test_bogus_token_is_unauthenticated(client, make_user, headers):
    make_user("Admin")
    resp = await client.get("/api/library", headers=headers("not-a-real-token"))
    assert resp.status == 401


async def test_auth_status_public_after_bootstrap(client, make_user, headers):
    token = make_user("Users")
    anon = await (await client.get("/api/auth/status")).json()
    assert anon["authenticated"] is False and anon["bootstrap_required"] is False
    me = await (await client.get("/api/auth/status", headers=headers(token))).json()
    assert me["authenticated"] is True
    assert me["permissions"] == ["downloads.view", "library.view"]


# ── Viewer (Users) ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("method, path", [
    ("GET", "/api/admin/stats"),
    ("POST", "/api/sources"),
    ("DELETE", "/api/item/x"),
    ("POST", "/api/tags/approve/1"),
])
async def test_viewer_forbidden_by_middleware(client, make_user, headers, method, path):
    token = make_user("Users")
    resp = await client.request(method, path, headers=headers(token), json={})
    assert resp.status == 403
    assert (await resp.json())["forbidden"] is True


async def test_viewer_forbidden_user_management(client, make_user, headers):
    # SMOKE_TEST 10A.6 says POST /api/users; the real route is /api/auth/users,
    # which the middleware lets through and the handler rejects.
    token = make_user("Users")
    resp = await client.post("/api/auth/users", headers=headers(token),
                             json={"username": "evil", "password": "password123"})
    assert resp.status == 403


async def test_viewer_can_list_library(client, make_user, headers):
    token = make_user("Users")
    resp = await client.get("/api/library", headers=headers(token))
    assert resp.status == 200


# ── Manager ───────────────────────────────────────────────────────────────────

async def test_manager_can_post_sources(client, make_user, headers):
    token = make_user("Manager")
    resp = await client.post("/api/sources", headers=headers(token), json={"sources": ["me"]})
    assert resp.status == 200
    assert (await resp.json())["ok"] is True


@pytest.mark.parametrize("method, path", [("GET", "/api/admin/stats"), ("GET", "/api/admin/audit-log")])
async def test_manager_forbidden_admin(client, make_user, headers, method, path):
    token = make_user("Manager")
    resp = await client.request(method, path, headers=headers(token))
    assert resp.status == 403


async def test_manager_forbidden_user_management(client, make_user, headers):
    token = make_user("Manager")
    resp = await client.post("/api/auth/users", headers=headers(token),
                             json={"username": "evil", "password": "password123"})
    assert resp.status == 403


async def test_manager_can_read_auth_admin_via_source_acl_manage(client, make_user, headers):
    token = make_user("Manager")
    resp = await client.get("/api/auth/admin", headers=headers(token))
    assert resp.status == 200
    body = await resp.json()
    assert body["can_manage_security"] is False and body["can_manage_source_acl"] is True


# ── Admin ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("method, path", [
    ("GET", "/api/admin/stats"),
    ("GET", "/api/admin/audit-log"),
    ("GET", "/api/library"),
    ("GET", "/api/sources"),
    ("GET", "/api/auth/admin"),
])
async def test_admin_passes(client, make_user, headers, method, path):
    token = make_user("Admin")
    resp = await client.request(method, path, headers=headers(token))
    assert resp.status == 200, await resp.text()


async def test_admin_can_create_user(client, make_user, headers):
    token = make_user("Admin")
    resp = await client.post("/api/auth/users", headers=headers(token),
                             json={"username": "newbie", "password": "password123", "groups": ["Users"]})
    assert resp.status == 200
    assert (await resp.json())["user"]["groups"] == ["Users"]


# ── Login / logout ────────────────────────────────────────────────────────────

def test_login_rate_limit_names_aliased_from_auth(app_module):
    # Regression: lost in the v4.5.13 extraction, making every login a NameError -> 500.
    assert app_module._LOGIN_ATTEMPTS is app_module._auth._LOGIN_ATTEMPTS
    assert app_module._LOGIN_MAX_ATTEMPTS == app_module._auth._LOGIN_MAX_ATTEMPTS
    assert app_module._LOGIN_WINDOW_SECONDS == app_module._auth._LOGIN_WINDOW_SECONDS


async def test_login_ok_sets_cookie(client, make_user, headers, app_module):
    make_user("Users", username="olga", password="password123")
    resp = await client.post("/api/auth/login", json={"username": "Olga", "password": "password123"})
    assert resp.status == 200
    body = await resp.json()
    assert body["ok"] is True
    cookies = _set_cookie_headers(resp)
    assert len(cookies) == 1
    attrs = _cookie_attrs(cookies[0])
    assert attrs.get("httponly") is True
    assert "secure" not in attrs
    assert attrs.get("samesite") == "Lax"
    token = cookies[0].split(";", 1)[0].split("=", 1)[1]
    assert app_module.AUTH_SESSIONS[token]["user_id"] == "olga"
    assert app_module.auth_data["users"]["olga"]["last_login"]
    assert (await client.get("/api/library", headers=headers(token))).status == 200


async def test_login_behind_tls_proxy_sets_secure_cookie(client, make_user):
    make_user("Users", username="olga", password="password123")
    resp = await client.post("/api/auth/login", json={"username": "olga", "password": "password123"},
                             headers={"X-Forwarded-Proto": "https"})
    assert resp.status == 200
    assert _cookie_attrs(_set_cookie_headers(resp)[0]).get("secure") is True


async def test_cookie_only_session_over_plain_http(client, make_user):
    # The real frontend authenticates only with the cookie (no X-Telesaved-Auth-Token).
    make_user("Users", username="olga", password="password123")
    client.session.cookie_jar.clear()
    assert (await client.get("/api/library")).status == 401
    resp = await client.post("/api/auth/login", json={"username": "olga", "password": "password123"})
    assert resp.status == 200
    assert (await client.get("/api/library")).status == 200
    assert (await client.get("/api/auth/status")).status == 200
    assert (await (await client.get("/api/auth/status")).json())["authenticated"] is True
    assert (await client.post("/api/auth/logout")).status == 200
    assert (await client.get("/api/library")).status == 401


async def test_login_wrong_password(client, make_user, app_module):
    make_user("Users", username="olga", password="password123")
    resp = await client.post("/api/auth/login", json={"username": "olga", "password": "nope-nope"})
    assert resp.status == 401
    assert not _set_cookie_headers(resp)
    assert app_module._auth._LOGIN_ATTEMPTS["olga"]["count"] == 1


async def test_login_disabled_user_rejected(client, make_user):
    make_user("Users", username="olga", password="password123", disabled=True)
    resp = await client.post("/api/auth/login", json={"username": "olga", "password": "password123"})
    assert resp.status == 401


async def test_login_brute_force_lockout(client, make_user, app_module):
    make_user("Users", username="olga", password="password123")
    for i in range(10):
        resp = await client.post("/api/auth/login", json={"username": "olga", "password": f"wrong{i}xx"})
        assert resp.status == 401, f"attempt {i + 1}"
    resp = await client.post("/api/auth/login", json={"username": "olga", "password": "password123"})
    assert resp.status == 429
    # Lockout lifts once the 300 s window has passed.
    app_module._auth._LOGIN_ATTEMPTS["olga"]["window_start"] -= 300
    resp = await client.post("/api/auth/login", json={"username": "olga", "password": "password123"})
    assert resp.status == 200
    assert "olga" not in app_module._auth._LOGIN_ATTEMPTS


async def test_logout_drops_session_and_clears_cookie(client, make_user, headers, app_module):
    token = make_user("Users")
    resp = await client.post("/api/auth/logout", headers=headers(token))
    assert resp.status == 200
    assert token not in app_module.AUTH_SESSIONS
    cleared = _set_cookie_headers(resp)
    assert cleared and "max-age=0" in cleared[0].lower()
    assert (await client.get("/api/library", headers=headers(token))).status == 401


async def test_disabled_user_session_dropped(client, make_user, headers, app_module):
    token = make_user("Users", username="pete")
    assert (await client.get("/api/library", headers=headers(token))).status == 200
    app_module.auth_data["users"]["pete"]["disabled"] = True
    resp = await client.get("/api/library", headers=headers(token))
    assert resp.status == 401
    assert (await resp.json())["auth_required"] is True
    assert token not in app_module.AUTH_SESSIONS


# ── Permission lists through the group routes ─────────────────────────────────

@pytest.mark.parametrize("permissions", [[], ["bogus", "nope"]])
async def test_group_update_keeps_empty_permission_list(client, make_user, headers, app_module, permissions):
    token = make_user("Admin")
    created = await client.post("/api/auth/groups", headers=headers(token),
                                json={"name": "Auditors", "permissions": ["admin.view"]})
    assert created.status == 200
    resp = await client.put("/api/auth/groups/Auditors", headers=headers(token), json={"permissions": permissions})
    assert resp.status == 200
    assert (await resp.json())["group"]["permissions"] == []
    assert app_module.auth_data["groups"]["Auditors"]["permissions"] == []


async def test_group_create_route_defaults_empty_to_library_view(client, make_user, headers):
    # Characterization: the create route (not normalize_permission_list) adds a
    # library.view default for an empty list; an all-invalid list stays [].
    token = make_user("Admin")
    empty = await client.post("/api/auth/groups", headers=headers(token), json={"name": "A1", "permissions": []})
    assert (await empty.json())["group"]["permissions"] == ["library.view"]
    invalid = await client.post("/api/auth/groups", headers=headers(token), json={"name": "A2", "permissions": ["x"]})
    assert (await invalid.json())["group"]["permissions"] == []


async def test_admin_group_must_keep_star(client, make_user, headers):
    token = make_user("Admin")
    resp = await client.put("/api/auth/groups/Admin", headers=headers(token), json={"permissions": ["admin.view"]})
    assert resp.status == 400
