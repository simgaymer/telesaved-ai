# telesaved/auth.py — Local users, RBAC, session management
#
# Architectural decisions honoured:
#   AD-6: No import of app.py; config values and helpers injected via _bind().
#
# Usage pattern
# ─────────────
#   import telesaved.auth as _auth
#   _auth._bind({
#       "AUTH_FILE": ..., "APP_VERSION": ...,
#       "load_json": ..., "save_json": ..., "now_stamp": ...,
#       "normalize_source_ref": ...,
#       "ldap_is_enabled": ..., "ldap_status_payload": ...,
#       "accessible_source_refs_for_user": ...,
#       "web": ...,
#   })
#   # After _bind(), auth_data, AUTH_SESSIONS, and all helpers are available.
#
# app.py aliases the module-level dicts and constants, and holds 1-line
# forwarding defs so all ~50+ downstream callsites continue to work unchanged.

import hashlib
import hmac
import json
import logging
import re
import secrets
import time

logger = logging.getLogger("telesaved")

# ── Module-level config (populated by _bind) ──────────────────────────────────
_cfg: dict = {}

# ── Constants ─────────────────────────────────────────────────────────────────
AUTH_SESSION_COOKIE    = "telesaved_auth_token"
AUTH_SESSION_SECONDS   = 60 * 60 * 12
_LOGIN_MAX_ATTEMPTS    = 10
_LOGIN_WINDOW_SECONDS  = 300  # 5-minute sliding window

PERMISSION_CATALOG = [
    {"id": "*",                "label": "Full system access"},
    {"id": "admin.view",       "label": "Open Administration"},
    {"id": "library.view",     "label": "View library"},
    {"id": "downloads.view",   "label": "View downloads"},
    {"id": "content.manage",   "label": "Add, rename, reprocess, and manage content"},
    {"id": "content.delete",   "label": "Move content to Trash / restore content"},
    {"id": "tags.manage",      "label": "Assign, disassign, create, and remove tags"},
    {"id": "sources.manage",   "label": "Manage Telegram sources and source pipelines"},
    {"id": "source_acl.manage","label": "Grant and revoke source access"},
    {"id": "jobs.manage",      "label": "View and control jobs"},
    {"id": "security.manage",  "label": "Manage users, groups, permissions, and local authentication"},
    {"id": "system.admin",     "label": "Manage system settings, setup, logs, backups, import/export"},
]

DEFAULT_GROUPS = {
    "Admin": {
        "name": "Admin",
        "description": "Full Systems Access",
        "system": True,
        "permissions": ["*"],
    },
    "Manager": {
        "name": "Manager",
        "description": "Add/delete content, manage sources, assign/disassign tags, and grant source access",
        "system": True,
        "permissions": [
            "admin.view", "library.view", "downloads.view", "content.manage", "content.delete",
            "tags.manage", "sources.manage", "source_acl.manage", "jobs.manage"
        ],
    },
    "Users": {
        "name": "Users",
        "description": "View Only Access, No Access to Administration",
        "system": True,
        "permissions": ["library.view", "downloads.view"],
    },
}

# ── Module-level mutable globals (populated in-place by _bind) ────────────────
# These objects are populated in-place by _bind() so alias references held by
# app.py always point to the same dict objects.
auth_data:       dict = {}
AUTH_SESSIONS:   dict = {}
_LOGIN_ATTEMPTS: dict = {}   # username -> {"count": int, "window_start": float}


# ── _bind ─────────────────────────────────────────────────────────────────────

def _bind(cfg: dict):
    """Initialise the auth module.

    cfg must contain:
        AUTH_FILE, APP_VERSION,
        load_json, save_json, now_stamp,
        normalize_source_ref,
        ldap_is_enabled, ldap_status_payload,
        accessible_source_refs_for_user,
        web  (aiohttp.web for make_auth_response),
    """
    _cfg.clear()
    _cfg.update(cfg)
    loaded = _load_auth_data()
    auth_data.clear()
    auth_data.update(loaded)
    # AUTH_SESSIONS and _LOGIN_ATTEMPTS are runtime-only; clear on rebind.
    AUTH_SESSIONS.clear()
    _LOGIN_ATTEMPTS.clear()


# ── Auth data load / save ─────────────────────────────────────────────────────

def _load_auth_data() -> dict:
    data = _cfg["load_json"](_cfg["AUTH_FILE"], _auth_default_data())
    if not isinstance(data, dict):
        data = _auth_default_data()
    data.setdefault("version", 1)
    data.setdefault("enabled", True)
    data.setdefault("users", {})
    data.setdefault("groups", {})
    data.setdefault("source_access", {})
    data.setdefault("settings", {})
    data["source_access"].setdefault("users", {})
    data["source_access"].setdefault("groups", {})
    for name, group in DEFAULT_GROUPS.items():
        existing = data["groups"].get(name) or {}
        merged = {**group, **existing}
        if name in {"Admin", "Manager", "Users"}:
            merged["system"] = True
        data["groups"][name] = merged
    data["source_access"]["groups"].setdefault("Admin", ["*"])
    data["source_access"]["groups"].setdefault("Manager", ["*"])
    data["source_access"]["groups"].setdefault("Users", [])
    ensure_identity_defaults(data)
    return data


def save_auth_data():
    _cfg["save_json"](_cfg["AUTH_FILE"], auth_data)


# ── LDAP default settings (used by auth schema defaults) ─────────────────────

def ldap_default_settings():
    return {
        "enabled": False,
        "server_uri": "ldap://localhost:389",
        "start_tls": False,
        "bind_dn": "",
        "bind_password": "",
        "user_search_base": "",
        "user_filter": "(uid={username})",
        "username_attribute": "uid",
        "display_name_attribute": "cn",
        "email_attribute": "mail",
        "memberof_attribute": "memberOf",
        "group_search_base": "",
        "group_filter": "(|(member={user_dn})(uniqueMember={user_dn})(memberUid={username}))",
        "group_name_attribute": "cn",
        "default_groups": ["Users"],
        "group_mappings": {},
        "auto_create_users": True,
        "sync_groups_on_login": True,
        "timeout_seconds": 8,
    }


def identity_default_data():
    return {
        "local": {"enabled": True, "emergency_admin": True},
        "ldap": ldap_default_settings(),
        "oidc": {"enabled": False},
    }


def ensure_identity_defaults(data):
    identity = data.setdefault("identity", {})
    identity.setdefault("local", {"enabled": True, "emergency_admin": True})
    ldap_settings = identity.setdefault("ldap", {})
    defaults = ldap_default_settings()
    for key, value in defaults.items():
        ldap_settings.setdefault(key, json.loads(json.dumps(value)))
    identity.setdefault("oidc", {"enabled": False})
    return data


def _auth_default_data():
    now = _cfg["now_stamp"]() if _cfg else ""
    return {
        "version": 1,
        "enabled": True,
        "created_at": now,
        "users": {},
        "groups": json.loads(json.dumps(DEFAULT_GROUPS)),
        "source_access": {
            "users": {},
            "groups": {
                "Admin": ["*"],
                "Manager": ["*"],
                "Users": [],
            },
        },
        "settings": {
            "session_timeout_minutes": 720,
            "allow_local_login": True,
        },
        "identity": identity_default_data(),
    }


# ── Login rate limiting ───────────────────────────────────────────────────────

def _login_check_and_record_failure(username: str) -> bool:
    """Return True if the account is locked (too many recent failures).
    Record a new failure unconditionally (call after a failed attempt).
    """
    now = time.time()
    entry = _LOGIN_ATTEMPTS.get(username)
    if entry and (now - entry["window_start"]) >= _LOGIN_WINDOW_SECONDS:
        entry = None  # window expired — reset
    if entry is None:
        _LOGIN_ATTEMPTS[username] = {"count": 1, "window_start": now}
        return False
    entry["count"] += 1
    return entry["count"] > _LOGIN_MAX_ATTEMPTS


def _login_clear_failures(username: str) -> None:
    """Clear failure record on successful login."""
    _LOGIN_ATTEMPTS.pop(username, None)


# ── Normalisation helpers ─────────────────────────────────────────────────────

def normalize_group_name(value):
    name = re.sub(r"\s+", " ", str(value or "").strip())
    name = "".join(ch for ch in name if ord(ch) >= 32 and ch not in "<>:\\/*?\"'").strip()
    return name[:64]


def normalize_username(value):
    username = str(value or "").strip().lower()
    username = re.sub(r"[^a-z0-9_.@-]+", "", username)
    return username[:80]


def normalize_permission_list(values):
    valid = {item["id"] for item in PERMISSION_CATALOG}
    out = []
    for value in values or []:
        perm = str(value or "").strip()
        if perm in valid and perm not in out:
            out.append(perm)
    # NOTE: intentionally returns [] for an empty or all-invalid input.
    # Callers that need a minimum grant must add it explicitly. Do NOT add a
    # fallback default here — an explicit empty list means "no access" and must
    # be preserved as such (e.g. SSO/OIDC group sync with zero-permission sets).
    return out


def normalize_source_grants(values):
    out = []
    for value in values or []:
        raw = str(value or "").strip()
        if not raw:
            continue
        ref = "*" if raw == "*" else _cfg["normalize_source_ref"](raw)
        if ref and ref not in out:
            out.append(ref)
    return out


# ── Auth core ─────────────────────────────────────────────────────────────────

def auth_initialized():
    return bool((auth_data.get("users") or {}))


def hash_password(password, salt=None):
    password = str(password or "")
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 240_000)
    return salt, digest.hex()


def verify_password(password, user):
    if not user or not user.get("password_hash") or not user.get("password_salt"):
        return False
    try:
        _, digest = hash_password(password, user.get("password_salt"))
        return hmac.compare_digest(digest, str(user.get("password_hash") or ""))
    except Exception:
        return False


def sanitized_user(user_id, user=None):
    user = user or (auth_data.get("users") or {}).get(user_id) or {}
    return {
        "id": user_id,
        "username": user.get("username", user_id),
        "display_name": user.get("display_name") or user.get("username") or user_id,
        "email": user.get("email", ""),
        "groups": list(user.get("groups") or []),
        "disabled": bool(user.get("disabled", False)),
        "created_at": user.get("created_at", ""),
        "updated_at": user.get("updated_at", ""),
        "last_login": user.get("last_login", ""),
        "auth_backend": user.get("auth_backend", "local"),
    }


def group_permissions(group_names):
    perms = set()
    for group_name in group_names or []:
        group = (auth_data.get("groups") or {}).get(group_name) or {}
        for perm in group.get("permissions") or []:
            perms.add(str(perm))
    if "*" in perms:
        return ["*"] + sorted(p for p in perms if p != "*")
    return sorted(perms)


def user_permissions(user):
    return group_permissions((user or {}).get("groups") or [])


def user_has_permission(user, permission):
    if not user:
        return False
    perms = set(user.get("permissions") or user_permissions(user))
    return "*" in perms or permission in perms


# ── Session management ────────────────────────────────────────────────────────

def auth_cleanup_sessions():
    now = time.time()
    expired = [token for token, session in AUTH_SESSIONS.items() if float(session.get("expires", 0)) <= now]
    for token in expired:
        AUTH_SESSIONS.pop(token, None)


def create_auth_session(user_id):
    # Eviction cap: prevent unbounded dict growth under load or during a DoS.
    if len(AUTH_SESSIONS) > 1_000:
        auth_cleanup_sessions()
    timeout = int((auth_data.get("settings") or {}).get("session_timeout_minutes") or 720) * 60
    timeout = max(300, min(timeout, 60 * 60 * 24 * 14))
    token = secrets.token_urlsafe(36)
    AUTH_SESSIONS[token] = {"user_id": user_id, "created": time.time(), "expires": time.time() + timeout}
    return token, timeout


def auth_token_from_request(request):
    return request.cookies.get(AUTH_SESSION_COOKIE) or request.headers.get("X-Telesaved-Auth-Token") or ""


def auth_current_user(request):
    token = auth_token_from_request(request)
    if not token:
        return None
    auth_cleanup_sessions()
    session = AUTH_SESSIONS.get(token)
    if not session:
        return None
    user_id = session.get("user_id")
    user = (auth_data.get("users") or {}).get(user_id)
    if not user or user.get("disabled"):
        AUTH_SESSIONS.pop(token, None)
        return None
    timeout = int((auth_data.get("settings") or {}).get("session_timeout_minutes") or 720) * 60
    session["expires"] = time.time() + max(300, min(timeout, 60 * 60 * 24 * 14))
    enriched = dict(user)
    enriched["id"] = user_id
    enriched["permissions"] = user_permissions(user)
    return enriched


def auth_status_payload(request=None):
    user = auth_current_user(request) if request is not None else None
    payload = {
        "ok": True,
        "version": _cfg["APP_VERSION"],
        "auth_enabled": True,
        "bootstrap_required": not auth_initialized(),
        "authenticated": bool(user),
        "permissions_catalog": PERMISSION_CATALOG,
        "identity_backends": {"local": True, "ldap": _cfg["ldap_is_enabled"](), "oidc": False, "sso": False},
        "ldap_status": _cfg["ldap_status_payload"](),
        "phase": "local-users-groups-source-acl-ldap",
    }
    if user:
        payload["user"] = sanitized_user(user.get("id"), user)
        payload["permissions"] = user.get("permissions") or []
        payload["source_grants"] = _cfg["accessible_source_refs_for_user"](user)
    else:
        payload["permissions"] = []
        payload["source_grants"] = []
    return payload


def make_auth_response(payload, token=None, max_age=None, clear=False):
    web = _cfg["web"]
    response = web.json_response(payload)
    if clear:
        response.del_cookie(AUTH_SESSION_COOKIE, path="/")
    elif token:
        response.set_cookie(
            AUTH_SESSION_COOKIE,
            token,
            max_age=max_age or AUTH_SESSION_SECONDS,
            httponly=True,
            samesite="Lax",
            path="/",
        )
    return response


def create_local_user(username, password, display_name="", email="", groups=None, disabled=False):
    username = normalize_username(username)
    if not username:
        raise ValueError("Username is required")
    if len(str(password or "")) < 8:
        raise ValueError("Password must be at least 8 characters")
    users = auth_data.setdefault("users", {})
    if username in users:
        raise ValueError("Username already exists")
    clean_groups = [g for g in (groups or ["Users"]) if g in auth_data.get("groups", {})]
    if not clean_groups:
        clean_groups = ["Users"]
    salt, digest = hash_password(password)
    users[username] = {
        "username": username,
        "display_name": str(display_name or username).strip()[:120],
        "email": str(email or "").strip()[:160],
        "groups": clean_groups,
        "disabled": bool(disabled),
        "password_salt": salt,
        "password_hash": digest,
        "created_at": _cfg["now_stamp"](),
        "updated_at": _cfg["now_stamp"](),
        "auth_backend": "local",
    }
    auth_data.setdefault("source_access", {}).setdefault("users", {}).setdefault(username, [])
    save_auth_data()
    return sanitized_user(username, users[username])
