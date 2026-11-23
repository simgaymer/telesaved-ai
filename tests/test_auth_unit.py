"""Characterization tests for telesaved/auth.py in isolation (via _bind)."""

import time

import pytest
from aiohttp.test_utils import make_mocked_request

from pinned import EXPECTED_DEFAULT_GROUP_PERMISSIONS


# ── Normalization ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    ("  Alice  ", "alice"),
    ("Bob Smith!", "bobsmith"),
    ("first.last@example.com", "first.last@example.com"),
    ("under_score-dash", "under_score-dash"),
    ("<script>", "script"),
    (None, ""),
    ("", ""),
    ("x" * 200, "x" * 80),
])
def test_normalize_username(auth_mod, raw, expected):
    assert auth_mod.normalize_username(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("  Video   Team ", "Video Team"),
    ('Bad<>:\\/*?"\'Name', "BadName"),
    ("tab\tname", "tab name"),
    (None, ""),
    ("g" * 100, "g" * 64),
])
def test_normalize_group_name(auth_mod, raw, expected):
    assert auth_mod.normalize_group_name(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ([], []),
    (None, []),
    (["bogus", "also.bogus"], []),
    (["library.view", " library.view ", "nope", "*"], ["library.view", "*"]),
    (["tags.manage", "admin.view"], ["tags.manage", "admin.view"]),
])
def test_normalize_permission_list_has_no_fallback(auth_mod, raw, expected):
    # Pinned since v4.5.9: empty / all-invalid stays [] (no library.view default).
    assert auth_mod.normalize_permission_list(raw) == expected


def test_normalize_source_grants(auth_mod):
    out = auth_mod.normalize_source_grants(["*", "", "  ", "@chan", "@chan", "Saved", None])
    assert out == ["*", "@chan", "me"]
    assert auth_mod.normalize_source_grants(None) == []


# ── Password hashing ──────────────────────────────────────────────────────────

def test_hash_password_is_salted_pbkdf2(auth_mod):
    salt1, digest1 = auth_mod.hash_password("s3cret-pass")
    salt2, digest2 = auth_mod.hash_password("s3cret-pass")
    assert len(salt1) == 32 and salt1 != salt2
    assert digest1 != digest2
    assert len(digest1) == 64  # sha256 hex
    assert auth_mod.hash_password("s3cret-pass", salt1) == (salt1, digest1)


def test_verify_password(auth_mod):
    salt, digest = auth_mod.hash_password("s3cret-pass")
    user = {"password_salt": salt, "password_hash": digest}
    assert auth_mod.verify_password("s3cret-pass", user) is True
    assert auth_mod.verify_password("wrong-pass", user) is False
    assert auth_mod.verify_password("s3cret-pass", None) is False
    assert auth_mod.verify_password("s3cret-pass", {"password_hash": digest}) is False
    assert auth_mod.verify_password("s3cret-pass", {"password_salt": salt}) is False


# ── Default schema / groups ───────────────────────────────────────────────────



@pytest.mark.parametrize("group", sorted(EXPECTED_DEFAULT_GROUP_PERMISSIONS))
def test_default_group_permissions_pinned(auth_mod, group):
    assert auth_mod.DEFAULT_GROUPS[group]["permissions"] == EXPECTED_DEFAULT_GROUP_PERMISSIONS[group], (
        f"default group {group!r} permissions changed"
    )
    assert auth_mod.auth_data["groups"][group]["permissions"] == EXPECTED_DEFAULT_GROUP_PERMISSIONS[group]
    assert auth_mod.auth_data["groups"][group]["system"] is True


def test_default_groups_are_exactly_three(auth_mod):
    assert set(auth_mod.DEFAULT_GROUPS) == {"Admin", "Manager", "Users"}


def test_permission_catalog_ids(auth_mod):
    assert [p["id"] for p in auth_mod.PERMISSION_CATALOG] == [
        "*", "admin.view", "library.view", "downloads.view", "content.manage", "content.delete",
        "tags.manage", "sources.manage", "source_acl.manage", "jobs.manage", "security.manage",
        "system.admin",
    ]


def test_fresh_auth_data_defaults(auth_mod):
    data = auth_mod.auth_data
    assert data["users"] == {}
    assert data["source_access"]["groups"] == {"Admin": ["*"], "Manager": ["*"], "Users": []}
    assert data["source_access"]["users"] == {}
    assert data["identity"]["local"] == {"enabled": True, "emergency_admin": True}
    assert data["identity"]["oidc"] == {"enabled": False}
    assert data["identity"]["ldap"]["enabled"] is False
    assert auth_mod.auth_initialized() is False


def test_load_merges_stored_groups_but_forces_system_flag(auth_mod, auth_store):
    auth_store["memory://auth.json"] = {
        "users": {"bob": {"username": "bob", "groups": ["Users"]}},
        "groups": {
            "Users": {"permissions": ["library.view"], "system": False},
            "Custom": {"name": "Custom", "permissions": ["tags.manage"]},
        },
        "source_access": {"groups": {"Users": ["@a"]}},
    }
    auth_mod._bind(dict(auth_mod._cfg))
    groups = auth_mod.auth_data["groups"]
    assert groups["Users"]["permissions"] == ["library.view"]  # stored value wins
    assert groups["Users"]["system"] is True                   # but system is forced
    assert groups["Custom"]["permissions"] == ["tags.manage"]
    assert set(groups) >= {"Admin", "Manager", "Users", "Custom"}
    assert auth_mod.auth_data["source_access"]["groups"]["Users"] == ["@a"]
    assert auth_mod.auth_data["source_access"]["groups"]["Admin"] == ["*"]
    assert auth_mod.auth_initialized() is True


def test_rebind_clears_sessions_and_login_attempts(auth_mod):
    auth_mod.AUTH_SESSIONS["t"] = {"user_id": "x", "expires": time.time() + 100}
    auth_mod._LOGIN_ATTEMPTS["x"] = {"count": 3, "window_start": time.time()}
    sessions, attempts = auth_mod.AUTH_SESSIONS, auth_mod._LOGIN_ATTEMPTS
    auth_mod._bind(dict(auth_mod._cfg))
    assert sessions is auth_mod.AUTH_SESSIONS and sessions == {}
    assert attempts is auth_mod._LOGIN_ATTEMPTS and attempts == {}


# ── Permissions ───────────────────────────────────────────────────────────────

def test_group_permissions_union_sorted(auth_mod):
    assert auth_mod.group_permissions(["Users"]) == ["downloads.view", "library.view"]
    assert auth_mod.group_permissions(["Users", "Nope"]) == ["downloads.view", "library.view"]
    assert auth_mod.group_permissions([]) == []
    assert auth_mod.group_permissions(None) == []


def test_group_permissions_star_first(auth_mod):
    perms = auth_mod.group_permissions(["Users", "Admin"])
    assert perms == ["*", "downloads.view", "library.view"]


def test_user_has_permission(auth_mod):
    viewer = {"groups": ["Users"]}
    admin = {"groups": ["Admin"]}
    assert auth_mod.user_has_permission(None, "library.view") is False
    assert auth_mod.user_has_permission({}, "library.view") is False
    assert auth_mod.user_has_permission(viewer, "library.view") is True
    assert auth_mod.user_has_permission(viewer, "admin.view") is False
    assert auth_mod.user_has_permission(admin, "system.admin") is True
    assert auth_mod.user_has_permission(admin, "anything.at.all") is True


def test_user_has_permission_prefers_enriched_list(auth_mod):
    # Precomputed "permissions" wins over groups ...
    assert auth_mod.user_has_permission({"groups": ["Admin"], "permissions": ["library.view"]}, "system.admin") is False
    # ... but an empty list falls back to group permissions.
    assert auth_mod.user_has_permission({"groups": ["Admin"], "permissions": []}, "system.admin") is True


# ── create_local_user ─────────────────────────────────────────────────────────

def test_create_local_user_happy_path(auth_mod, auth_store):
    user = auth_mod.create_local_user(" Alice ", "password123", display_name="Alice A", email="a@x.io",
                                      groups=["Manager"])
    assert user["id"] == "alice" and user["username"] == "alice"
    assert user["groups"] == ["Manager"]
    assert user["auth_backend"] == "local"
    assert "password_hash" not in user and "password_salt" not in user
    stored = auth_mod.auth_data["users"]["alice"]
    assert stored["password_hash"] and stored["password_hash"] != "password123"
    assert auth_mod.verify_password("password123", stored)
    assert auth_mod.auth_data["source_access"]["users"]["alice"] == []
    assert auth_store["memory://auth.json"]["users"]["alice"]["username"] == "alice"  # persisted
    assert auth_mod.auth_initialized() is True


@pytest.mark.parametrize("username, password, message", [
    ("", "password123", "Username is required"),
    ("!!!", "password123", "Username is required"),
    ("carol", "short", "Password must be at least 8 characters"),
    ("carol", "", "Password must be at least 8 characters"),
])
def test_create_local_user_validation(auth_mod, username, password, message):
    with pytest.raises(ValueError, match=message):
        auth_mod.create_local_user(username, password)


def test_create_local_user_duplicate(auth_mod):
    auth_mod.create_local_user("dave", "password123")
    with pytest.raises(ValueError, match="Username already exists"):
        auth_mod.create_local_user("DAVE", "password123")


@pytest.mark.parametrize("groups, expected", [
    (None, ["Users"]),
    ([], ["Users"]),
    (["Ghost"], ["Users"]),
    (["Ghost", "Manager"], ["Manager"]),
    (["Admin", "Users"], ["Admin", "Users"]),
])
def test_create_local_user_group_sanitizing(auth_mod, groups, expected):
    assert auth_mod.create_local_user("erin", "password123", groups=groups)["groups"] == expected


def test_create_local_user_disabled_flag(auth_mod):
    assert auth_mod.create_local_user("frank", "password123", disabled=True)["disabled"] is True


# ── Sessions ──────────────────────────────────────────────────────────────────

def _req(token=None, cookie=False):
    if token is None:
        return make_mocked_request("GET", "/api/library")
    if cookie:
        return make_mocked_request("GET", "/api/library", headers={"Cookie": f"telesaved_auth_token={token}"})
    return make_mocked_request("GET", "/api/library", headers={"X-Telesaved-Auth-Token": token})


def test_session_roundtrip_header_and_cookie(auth_mod):
    auth_mod.create_local_user("gina", "password123", groups=["Users"])
    token, timeout = auth_mod.create_auth_session("gina")
    assert timeout == 720 * 60
    for request in (_req(token), _req(token, cookie=True)):
        user = auth_mod.auth_current_user(request)
        assert user["id"] == "gina"
        assert user["permissions"] == ["downloads.view", "library.view"]
    assert auth_mod.auth_current_user(_req()) is None
    assert auth_mod.auth_current_user(_req("not-a-token")) is None


@pytest.mark.parametrize("minutes, expected_seconds", [
    (1, 300),                      # clamped to 5 min floor
    (60, 3600),
    (None, 720 * 60),              # default 12 h
    (0, 720 * 60),                 # falsy -> default
    (10 ** 6, 60 * 60 * 24 * 14),  # clamped to 14 day ceiling
])
def test_session_timeout_clamp(auth_mod, minutes, expected_seconds):
    auth_mod.auth_data["settings"]["session_timeout_minutes"] = minutes
    before = time.time()
    token, timeout = auth_mod.create_auth_session("someone")
    assert timeout == expected_seconds
    assert auth_mod.AUTH_SESSIONS[token]["expires"] >= before + expected_seconds - 1


def test_expired_session_rejected_and_dropped(auth_mod):
    auth_mod.create_local_user("hank", "password123")
    token, _ = auth_mod.create_auth_session("hank")
    auth_mod.AUTH_SESSIONS[token]["expires"] = time.time() - 1
    assert auth_mod.auth_current_user(_req(token)) is None
    assert token not in auth_mod.AUTH_SESSIONS


def test_session_is_sliding(auth_mod):
    auth_mod.create_local_user("ivy", "password123")
    token, _ = auth_mod.create_auth_session("ivy")
    auth_mod.AUTH_SESSIONS[token]["expires"] = time.time() + 10
    auth_mod.auth_current_user(_req(token))
    assert auth_mod.AUTH_SESSIONS[token]["expires"] > time.time() + 600


@pytest.mark.parametrize("mutation", ["disable", "delete"])
def test_disabled_or_deleted_user_drops_session(auth_mod, mutation):
    auth_mod.create_local_user("jack", "password123")
    token, _ = auth_mod.create_auth_session("jack")
    assert auth_mod.auth_current_user(_req(token)) is not None
    if mutation == "disable":
        auth_mod.auth_data["users"]["jack"]["disabled"] = True
    else:
        del auth_mod.auth_data["users"]["jack"]
    assert auth_mod.auth_current_user(_req(token)) is None
    assert token not in auth_mod.AUTH_SESSIONS


def test_session_eviction_cap(auth_mod):
    now = time.time()
    for i in range(1000):
        auth_mod.AUTH_SESSIONS[f"old{i}"] = {"user_id": "x", "expires": now - 1}
    # At exactly 1,000 sessions no cleanup runs (cap is "> 1000").
    auth_mod.create_auth_session("x")
    assert len(auth_mod.AUTH_SESSIONS) == 1001
    # Above the cap, creating a session evicts the expired ones first.
    auth_mod.create_auth_session("x")
    assert len(auth_mod.AUTH_SESSIONS) == 2
    assert not any(t.startswith("old") for t in auth_mod.AUTH_SESSIONS)


def test_auth_cleanup_sessions_keeps_live(auth_mod):
    auth_mod.AUTH_SESSIONS["dead"] = {"user_id": "x", "expires": time.time() - 1}
    auth_mod.AUTH_SESSIONS["live"] = {"user_id": "x", "expires": time.time() + 100}
    auth_mod.auth_cleanup_sessions()
    assert set(auth_mod.AUTH_SESSIONS) == {"live"}


# ── Login rate-limit helper ───────────────────────────────────────────────────

def test_rate_limit_constants(auth_mod):
    assert auth_mod._LOGIN_MAX_ATTEMPTS == 10
    assert auth_mod._LOGIN_WINDOW_SECONDS == 300


def test_rate_limit_locks_on_eleventh_failure(auth_mod):
    results = [auth_mod._login_check_and_record_failure("kim") for _ in range(11)]
    assert results == [False] * 10 + [True]
    assert auth_mod._LOGIN_ATTEMPTS["kim"]["count"] == 11
    # Per-username: another account is unaffected.
    assert auth_mod._login_check_and_record_failure("lee") is False


def test_rate_limit_window_expiry_resets(auth_mod):
    for _ in range(11):
        auth_mod._login_check_and_record_failure("kim")
    auth_mod._LOGIN_ATTEMPTS["kim"]["window_start"] = time.time() - 300
    assert auth_mod._login_check_and_record_failure("kim") is False
    assert auth_mod._LOGIN_ATTEMPTS["kim"]["count"] == 1


def test_rate_limit_clear(auth_mod):
    auth_mod._login_check_and_record_failure("kim")
    auth_mod._login_clear_failures("kim")
    assert "kim" not in auth_mod._LOGIN_ATTEMPTS
    auth_mod._login_clear_failures("never-seen")  # no error


# ── Responses / payloads ──────────────────────────────────────────────────────

def test_make_auth_response_cookie_flags(auth_mod):
    response = auth_mod.make_auth_response({"ok": True}, token="tok123", max_age=600)
    morsel = response.cookies["telesaved_auth_token"]
    assert morsel.value == "tok123"
    assert morsel["httponly"] is True
    assert morsel["secure"] is True  # restored v4.5.7 fix (lost in 1df2ce1)
    assert morsel["samesite"] == "Lax"
    assert morsel["path"] == "/"
    assert str(morsel["max-age"]) == "600"


def test_make_auth_response_default_max_age(auth_mod):
    response = auth_mod.make_auth_response({"ok": True}, token="tok123")
    assert str(response.cookies["telesaved_auth_token"]["max-age"]) == str(12 * 60 * 60)


def test_make_auth_response_clear_and_no_token(auth_mod):
    cleared = auth_mod.make_auth_response({"ok": True}, token="ignored", clear=True)
    morsel = cleared.cookies["telesaved_auth_token"]
    assert morsel.value == "" and str(morsel["max-age"]) == "0"
    assert "telesaved_auth_token" not in auth_mod.make_auth_response({"ok": True}).cookies


def test_auth_status_payload_shapes(auth_mod):
    anon = auth_mod.auth_status_payload(_req())
    assert anon["bootstrap_required"] is True
    assert anon["authenticated"] is False
    assert anon["permissions"] == [] and anon["source_grants"] == []
    assert anon["identity_backends"] == {"local": True, "ldap": False, "oidc": False, "sso": False}

    auth_mod.create_local_user("mia", "password123", groups=["Admin"])
    token, _ = auth_mod.create_auth_session("mia")
    authed = auth_mod.auth_status_payload(_req(token))
    assert authed["bootstrap_required"] is False
    assert authed["authenticated"] is True
    assert authed["user"]["id"] == "mia"
    assert authed["permissions"] == ["*"]
