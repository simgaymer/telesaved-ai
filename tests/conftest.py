"""Shared pytest harness for TeleSaved.

Import-time contract (runs before any test module is collected):

* ``TELESAVED_BASE_PATH`` points at a per-session temp dir, so every state file
  (auth.json, telesaved.db, settings.json, logs, ...) lands there and never in
  ``/mnt/rda1`` or the repo.
* The repo's ``config.json`` is hidden from ``app.read_app_config`` while ``app``
  is imported, and the ``TELESAVED_API_ID``/``TELESAVED_API_HASH`` env vars are
  removed, so ``app.client`` is always ``None`` (no Telegram, no network).
* The ML stack imported by ``telesaved/pipeline.py`` (clip, faiss, torch, PIL,
  ultralytics) is stubbed in ``sys.modules`` so ``app`` imports in well under a
  second with only ``requirements-dev.txt`` installed.

``app`` is imported exactly once per session; tests reset auth state between
runs by re-calling ``telesaved.auth._bind``.
"""

import atexit
import os
import shutil
import sys
import tempfile
import types

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# ── Per-session state dir ─────────────────────────────────────────────────────
BASE_DIR = tempfile.mkdtemp(prefix="telesaved-tests-")
atexit.register(shutil.rmtree, BASE_DIR, ignore_errors=True)
os.environ["TELESAVED_BASE_PATH"] = BASE_DIR
os.environ.pop("TELESAVED_API_ID", None)
os.environ.pop("TELESAVED_API_HASH", None)


# ── ML stubs ──────────────────────────────────────────────────────────────────
class _Chainable:
    """Stands in for tensors / models: every method call returns itself."""

    def __getattr__(self, name):
        return lambda *a, **k: self


def _stub_module(name, **attrs):
    mod = types.ModuleType(name)
    mod.__dict__.update(attrs)
    mod.__telesaved_test_stub__ = True
    sys.modules[name] = mod
    return mod


_stub_module("clip", load=lambda *a, **k: (_Chainable(), _Chainable()),
             tokenize=lambda *a, **k: _Chainable())
_stub_module("faiss")
_stub_module("torch")
_pil_image = _stub_module("PIL.Image")
_stub_module("PIL", Image=_pil_image)
_stub_module("ultralytics", YOLO=lambda *a, **k: _Chainable())


# ── Import app once, with the repo config.json hidden ─────────────────────────
_REPO_CONFIG = os.path.join(REPO_ROOT, "config.json")
_real_exists = os.path.exists


def _exists_without_repo_config(path):
    try:
        if os.path.abspath(os.fspath(path)) == _REPO_CONFIG:
            return False
    except TypeError:
        pass
    return _real_exists(path)


os.path.exists = _exists_without_repo_config
try:
    import app as _app  # noqa: E402
finally:
    os.path.exists = _real_exists

import telesaved.auth as _auth  # noqa: E402

assert _app.BASE_PATH == BASE_DIR, f"app.BASE_PATH escaped the tmp dir: {_app.BASE_PATH}"
assert _app.client is None, "Telegram client must not be created under test"

# The real wiring app.py passed to _auth._bind at import; restored per test.
APP_AUTH_CFG = dict(_auth._cfg)


def reset_app_auth():
    """Drop persisted auth.json and rebind auth with app.py's real wiring."""
    try:
        os.remove(_app.AUTH_FILE)
    except FileNotFoundError:
        pass
    _auth._bind(APP_AUTH_CFG)


# ── Fixtures ──────────────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def app_module():
    return _app


@pytest.fixture
def auth_store():
    """In-memory backing store for auth_mod's load_json/save_json."""
    return {}


@pytest.fixture
def auth_mod(auth_store):
    """telesaved.auth bound in isolation (no app.py helpers, no disk I/O)."""
    from aiohttp import web

    def load_json(path, default):
        return auth_store.get(path, default)

    def save_json(path, data):
        import json
        auth_store[path] = json.loads(json.dumps(data))

    def normalize_source_ref(value):
        raw = str(value or "").strip()
        return "me" if raw.lower() in {"me", "saved", "self"} else raw

    _auth._bind({
        "AUTH_FILE": "memory://auth.json",
        "APP_VERSION": "test",
        "load_json": load_json,
        "save_json": save_json,
        "now_stamp": lambda: "2026-01-01T00:00:00",
        "normalize_source_ref": normalize_source_ref,
        "ldap_is_enabled": lambda: False,
        "ldap_status_payload": lambda: {"enabled": False},
        "accessible_source_refs_for_user": lambda user: [],
        "web": web,
    })
    yield _auth
    reset_app_auth()


@pytest.fixture
def fresh_auth():
    """Reset app-level auth state (users, sessions, login attempts) per test."""
    reset_app_auth()
    yield _auth
    reset_app_auth()


@pytest.fixture
def file_meta(app_module):
    """Register metadata_store entries for a test; removed afterwards."""
    added = []

    def _set(rel_path, **meta):
        app_module.metadata_store[rel_path] = dict(meta)
        added.append(rel_path)
        return rel_path

    yield _set
    for rel_path in added:
        app_module.metadata_store.pop(rel_path, None)


def build_web_app(app_module):
    """Replicate app.main()'s assembly without starting servers or tasks."""
    from aiohttp import web

    web_app = web.Application(middlewares=[app_module.auth_middleware, app_module.privacy_middleware])
    web_app.add_routes(app_module.routes)
    return web_app


@pytest.fixture
async def client(aiohttp_client, app_module, fresh_auth):
    """aiohttp test client on the real middlewares + routes."""
    return await aiohttp_client(build_web_app(app_module))


@pytest.fixture
def make_user(fresh_auth):
    """make_user(group, username=None, **kw) -> session token for a new local user.

    The session is minted directly with create_auth_session so RBAC tests do
    not depend on the login route; login itself is tested in test_rbac_http.py.
    """
    counter = {"n": 0}

    def _make(group, username=None, password="correct-horse-1", **kwargs):
        counter["n"] += 1
        username = username or f"{str(group).lower()}{counter['n']}"
        groups = group if isinstance(group, (list, tuple)) else [group]
        user = _auth.create_local_user(username, password, groups=list(groups), **kwargs)
        token, _ = _auth.create_auth_session(user["id"])
        return token

    return _make


def auth_headers(token):
    return {"X-Telesaved-Auth-Token": token}


@pytest.fixture
def headers():
    return auth_headers
