# telesaved/oidc.py — OpenID Connect (Authorization Code + PKCE) protocol layer
#
# Architectural decisions honoured:
#   AD-5: no aiohttp.web types here; functions take/return plain Python values.
#         (aiohttp's *client* is used for outbound calls to the IdP.)
#   AD-6: no import of app.py; app.py calls _bind() once at startup.
#
# Scope: discovery, JWKS, pending-login (state/nonce/PKCE) store, authorize URL,
# code exchange and ID-token validation. User provisioning, group mapping and
# HTTP routes live in app.py.
#
# Never log tokens, authorization codes or the client secret.

import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import secrets
import ssl
import time
from urllib.parse import quote, urlencode, urlsplit

import aiohttp

try:  # optional like ldap3: a missing PyJWT disables OIDC, not the whole app
    import jwt
    JWT_IMPORT_ERROR = ""
except Exception as _e:  # pragma: no cover - depends on the install
    jwt = None
    JWT_IMPORT_ERROR = str(_e)

logger = logging.getLogger("telesaved")

_cfg: dict = {}

STATE_TTL_SECONDS = 600
PENDING_LOGIN_CAP = 1_000
DISCOVERY_TTL_SECONDS = 3600
JWKS_TTL_SECONDS = 3600
JWKS_MIN_REFRESH_SECONDS = 60
HTTP_TIMEOUT_SECONDS = 10
CLOCK_SKEW_SECONDS = 60
MAX_RESPONSE_BYTES = 1_000_000

# Asymmetric algorithms only: "none" and HS* (secret-keyed) are refused.
ALLOWED_ALGORITHMS = {"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512"}

# state -> {"nonce", "verifier", "redirect_uri", "issuer", "created"}
PENDING_LOGINS: dict = {}
_DISCOVERY_CACHE: dict = {}  # (issuer, ca_file) -> (fetched_at, doc)
_JWKS_CACHE: dict = {}       # (jwks_uri, ca_file) -> (fetched_at, {kid: PyJWK}, [PyJWK])
_JWKS_LAST_FORCED: dict = {}  # (jwks_uri, ca_file) -> time of the last unknown-kid refetch

# Browser-binding cookie for a pending login: holds sha256(state), path-scoped.
STATE_COOKIE = "telesaved_oidc_state"
STATE_COOKIE_PATH = "/api/auth/oidc/"


class OIDCError(Exception):
    """Login/configuration failure.

    ``code`` is the short value sent to the browser as ``login_error``;
    ``reason`` is the operator-facing detail for logs and the Test button.
    Neither ever contains a token, code or secret.
    """

    def __init__(self, code, reason):
        super().__init__(reason)
        self.code = code
        self.reason = reason


def _bind(cfg=None):
    """Initialise the module. cfg may override ``now`` (a time.time-like callable)
    and ``http_timeout``. Clears all caches and pending logins."""
    _cfg.clear()
    _cfg.update(cfg or {})
    clear_caches()
    PENDING_LOGINS.clear()


def clear_caches():
    _DISCOVERY_CACHE.clear()
    _JWKS_CACHE.clear()
    _JWKS_LAST_FORCED.clear()


def _now():
    fn = _cfg.get("now")
    return float(fn()) if callable(fn) else time.time()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def pkce_challenge(verifier: str) -> str:
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def _is_loopback(host: str) -> bool:
    host = (host or "").strip("[]").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _require_secure_url(url: str, what: str):
    """IdP URLs must be https; plain http is accepted only for loopback hosts
    (local testing), because discovery/JWKS over http can be forged."""
    parts = urlsplit(str(url or ""))
    if parts.scheme == "https" and parts.netloc:
        return
    if parts.scheme == "http" and parts.hostname and _is_loopback(parts.hostname):
        return
    raise OIDCError("config", f"{what} must be an https:// URL")


def normalized_scopes(settings) -> str:
    scopes = [s for s in str(settings.get("scopes") or "").replace(",", " ").split() if s]
    if "openid" not in scopes:
        scopes.insert(0, "openid")
    out = []
    for s in scopes:
        if s not in out:
            out.append(s)
    return " ".join(out)


def _ssl_context(settings):
    ca_file = str(settings.get("ca_file") or "").strip()
    if not ca_file:
        return None
    if not os.path.isfile(ca_file):
        raise OIDCError("config", f"CA file not found: {ca_file}")
    try:
        return ssl.create_default_context(cafile=ca_file)
    except Exception as e:
        raise OIDCError("config", f"CA file could not be loaded: {e}")


def _timeout():
    return aiohttp.ClientTimeout(total=float(_cfg.get("http_timeout") or HTTP_TIMEOUT_SECONDS))


async def _read_json(resp, what):
    chunks, size = [], 0
    async for chunk in resp.content.iter_chunked(64 * 1024):
        size += len(chunk)
        if size > MAX_RESPONSE_BYTES:
            raise OIDCError("config", f"{what} response is too large")
        chunks.append(chunk)
    body = b"".join(chunks)
    try:
        data = json.loads(body.decode("utf-8"))
    except Exception:
        raise OIDCError("config", f"{what} did not return JSON (HTTP {resp.status})")
    if not isinstance(data, dict):
        raise OIDCError("config", f"{what} did not return a JSON object")
    return data


async def _get_json(settings, url, what):
    ssl_ctx = _ssl_context(settings)
    try:
        async with aiohttp.ClientSession(timeout=_timeout()) as session:
            async with session.get(url, ssl=ssl_ctx if ssl_ctx is not None else True,
                                   headers={"Accept": "application/json"}) as resp:
                if resp.status != 200:
                    raise OIDCError("config", f"{what} returned HTTP {resp.status}")
                return await _read_json(resp, what)
    except OIDCError:
        raise
    except Exception as e:
        raise OIDCError("config", f"{what} request failed: {type(e).__name__}: {e}")


# ── Discovery + JWKS ──────────────────────────────────────────────────────────

def pyjwt_available():
    return jwt is not None


async def discover(settings, force=False):
    if jwt is None:
        raise OIDCError("config", f"PyJWT[crypto] is not installed: {JWT_IMPORT_ERROR}")
    issuer = str(settings.get("issuer") or "").strip().rstrip("/")
    if not issuer:
        raise OIDCError("config", "Issuer URL is required")
    _require_secure_url(issuer, "Issuer URL")
    key = (issuer, str(settings.get("ca_file") or ""))
    cached = _DISCOVERY_CACHE.get(key)
    if cached and not force and _now() - cached[0] < DISCOVERY_TTL_SECONDS:
        return cached[1]
    doc = await _get_json(settings, issuer + "/.well-known/openid-configuration", "Discovery")
    doc_issuer = str(doc.get("issuer") or "")
    if doc_issuer.rstrip("/") != issuer:
        raise OIDCError("config", f"Discovery issuer {doc_issuer!r} does not match configured issuer {issuer!r}")
    for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not str(doc.get(field) or "").strip():
            raise OIDCError("config", f"Discovery document has no {field}")
        _require_secure_url(doc[field], field)
    methods = doc.get("code_challenge_methods_supported")
    if isinstance(methods, list) and methods and "S256" not in methods:
        raise OIDCError("config", "Provider does not support PKCE S256")
    _DISCOVERY_CACHE[key] = (_now(), doc)
    return doc


def _parse_jwks(data):
    """Return ({kid: (PyJWK, alg)}, [(PyJWK, alg)]) for the asymmetric signing
    keys in a JWKS document. ``alg`` is the key's declared alg or None."""
    by_kid, keys = {}, []
    raw_keys = data.get("keys")
    if not isinstance(raw_keys, list):
        raise OIDCError("config", "JWKS has no keys list")
    for raw in raw_keys:
        if not isinstance(raw, dict):
            continue
        if raw.get("use") not in (None, "sig"):
            continue
        if raw.get("kty") not in ("RSA", "EC"):
            continue  # never accept symmetric ("oct") or EdDSA ("OKP") keys
        if "kid" in raw and not isinstance(raw.get("kid"), str):
            continue
        try:
            jwk = jwt.PyJWK.from_dict(raw)
        except Exception:
            continue  # unsupported key type / algorithm
        entry = (jwk, raw.get("alg"))
        keys.append(entry)
        if jwk.key_id:
            by_kid[jwk.key_id] = entry
    return by_kid, keys


async def get_jwks(settings, jwks_uri, force=False):
    key = (jwks_uri, str(settings.get("ca_file") or ""))
    cached = _JWKS_CACHE.get(key)
    if cached and not force and _now() - cached[0] < JWKS_TTL_SECONDS:
        return cached[1], cached[2]
    data = await _get_json(settings, jwks_uri, "JWKS")
    by_kid, keys = _parse_jwks(data)
    if not keys:
        raise OIDCError("config", "JWKS contains no usable signing keys")
    _JWKS_CACHE[key] = (_now(), by_kid, keys)
    return by_kid, keys


async def _signing_key(settings, jwks_uri, kid):
    if kid is not None and not isinstance(kid, str):
        raise OIDCError("token", "ID token kid is not a string")
    by_kid, keys = await get_jwks(settings, jwks_uri)
    if kid and kid in by_kid:
        return by_kid[kid]
    if not kid and len(keys) == 1:
        return keys[0]
    # Unknown kid: the IdP may have rotated keys. Refetch, at most once a minute.
    cache_key = (jwks_uri, str(settings.get("ca_file") or ""))
    last = _JWKS_LAST_FORCED.get(cache_key)
    if last is None or _now() - last >= JWKS_MIN_REFRESH_SECONDS:
        _JWKS_LAST_FORCED[cache_key] = _now()
        by_kid, keys = await get_jwks(settings, jwks_uri, force=True)
        if kid and kid in by_kid:
            return by_kid[kid]
        if not kid and len(keys) == 1:
            return keys[0]
    raise OIDCError("token", f"No JWKS signing key matches kid {kid!r}")


# ── Pending logins (state → nonce + PKCE verifier) ───────────────────────────

def _purge_pending():
    now = _now()
    for state in [s for s, e in PENDING_LOGINS.items() if now - e["created"] > STATE_TTL_SECONDS]:
        PENDING_LOGINS.pop(state, None)


def new_pending_login(redirect_uri, issuer=""):
    _purge_pending()
    while len(PENDING_LOGINS) >= PENDING_LOGIN_CAP:
        oldest = min(PENDING_LOGINS, key=lambda s: PENDING_LOGINS[s]["created"])
        PENDING_LOGINS.pop(oldest, None)
    state = secrets.token_urlsafe(32)
    entry = {
        "nonce": secrets.token_urlsafe(32),
        "verifier": secrets.token_urlsafe(64),
        "redirect_uri": redirect_uri,
        "issuer": issuer,
        "created": _now(),
    }
    PENDING_LOGINS[state] = entry
    return state, entry


def state_cookie_value(state):
    """Value of the browser-binding cookie for a pending login."""
    return hashlib.sha256(str(state or "").encode("utf-8")).hexdigest()


def state_cookie_matches(state, cookie_value):
    expected = state_cookie_value(state).encode("ascii")
    return hmac.compare_digest(expected, str(cookie_value or "").encode("utf-8"))


def pop_pending_login(state):
    """Single-use: the entry is removed whether or not it is still valid."""
    state = str(state or "")
    if not state:
        return None
    entry = PENDING_LOGINS.pop(state, None)
    if not entry or _now() - entry["created"] > STATE_TTL_SECONDS:
        return None
    return entry


# ── Login steps ───────────────────────────────────────────────────────────────

async def start_login(settings, redirect_uri):
    """Return (IdP authorization URL, state) for a fresh pending login."""
    if not str(settings.get("client_id") or "").strip():
        raise OIDCError("config", "Client ID is required")
    doc = await discover(settings)
    state, entry = new_pending_login(redirect_uri, issuer=doc["issuer"])
    params = {
        "response_type": "code",
        "client_id": str(settings.get("client_id")).strip(),
        "redirect_uri": redirect_uri,
        "scope": normalized_scopes(settings),
        "state": state,
        "nonce": entry["nonce"],
        "code_challenge": pkce_challenge(entry["verifier"]),
        "code_challenge_method": "S256",
    }
    endpoint = doc["authorization_endpoint"]
    sep = "&" if "?" in endpoint else "?"
    return endpoint + sep + urlencode(params, quote_via=quote), state


async def exchange_code(settings, code, pending):
    doc = await discover(settings)
    client_id = str(settings.get("client_id") or "").strip()
    client_secret = str(settings.get("client_secret") or "")
    form = {
        "grant_type": "authorization_code",
        "code": str(code or ""),
        "redirect_uri": pending["redirect_uri"],
        "code_verifier": pending["verifier"],
    }
    headers = {"Accept": "application/json"}
    if client_secret:
        # client_secret_basic: RFC 6749 §2.3.1 form-encodes id and secret first.
        raw = f"{quote(client_id, safe='')}:{quote(client_secret, safe='')}".encode("utf-8")
        headers["Authorization"] = "Basic " + base64.b64encode(raw).decode("ascii")
    else:
        form["client_id"] = client_id
    ssl_ctx = _ssl_context(settings)
    try:
        async with aiohttp.ClientSession(timeout=_timeout()) as session:
            async with session.post(doc["token_endpoint"], data=form, headers=headers,
                                    ssl=ssl_ctx if ssl_ctx is not None else True) as resp:
                try:
                    data = await _read_json(resp, "Token endpoint")
                except OIDCError:
                    raise OIDCError("exchange", f"Token endpoint returned HTTP {resp.status} without JSON")
                if resp.status != 200:
                    err = str(data.get("error") or "unknown_error")[:80]
                    raise OIDCError("exchange", f"Token endpoint returned HTTP {resp.status}: {err}")
    except OIDCError:
        raise
    except Exception as e:
        raise OIDCError("exchange", f"Token request failed: {type(e).__name__}")
    id_token = data.get("id_token")
    if not isinstance(id_token, str) or not id_token:
        raise OIDCError("token", "Token response has no id_token")
    return id_token


async def validate_id_token(settings, id_token, nonce):
    doc = await discover(settings)
    try:
        header = jwt.get_unverified_header(id_token)
    except Exception as e:
        raise OIDCError("token", f"ID token is malformed: {type(e).__name__}")
    alg = str(header.get("alg") or "")
    if alg not in ALLOWED_ALGORITHMS:
        raise OIDCError("token", f"ID token algorithm {alg!r} is not allowed")
    jwk, key_alg = await _signing_key(settings, doc["jwks_uri"], header.get("kid"))
    if key_alg and key_alg != alg:
        raise OIDCError("token", "ID token algorithm does not match the signing key")
    client_id = str(settings.get("client_id") or "").strip()
    try:
        claims = jwt.decode(
            id_token,
            key=jwk.key,
            algorithms=[alg],
            audience=client_id,
            issuer=doc["issuer"],
            leeway=CLOCK_SKEW_SECONDS,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as e:
        raise OIDCError("token", f"ID token rejected: {type(e).__name__}: {e}")
    aud = claims.get("aud")
    if "azp" in claims and claims.get("azp") != client_id:
        raise OIDCError("token", "ID token azp does not match client ID")
    if isinstance(aud, list) and len(aud) > 1 and "azp" not in claims:
        raise OIDCError("token", "Multi-audience ID token has no azp")
    token_nonce = claims.get("nonce")
    if not isinstance(token_nonce, str) or not hmac.compare_digest(
            token_nonce.encode("utf-8"), str(nonce or "").encode("utf-8")):
        raise OIDCError("token", "ID token nonce mismatch")
    return claims


async def complete_login(settings, code, pending, iss_param=None):
    """Exchange the code and return validated ID-token claims."""
    if not code:
        raise OIDCError("token", "Callback has no authorization code")
    doc = await discover(settings)
    if pending.get("issuer") and pending["issuer"] != doc["issuer"]:
        raise OIDCError("state", "Issuer changed while the login was pending")
    if iss_param and iss_param != doc["issuer"]:
        raise OIDCError("token", "Callback iss parameter does not match issuer")
    id_token = await exchange_code(settings, code, pending)
    return await validate_id_token(settings, id_token, pending["nonce"])


async def test_configuration(settings):
    """Fetch discovery and JWKS fresh; return a plain result dict (never raises)."""
    started = time.time()
    result = {"ok": False, "discovery": False, "jwks": False, "jwks_keys": 0}
    try:
        if not str(settings.get("client_id") or "").strip():
            raise OIDCError("config", "Client ID is required")
        doc = await discover(settings, force=True)
        result["discovery"] = True
        result["issuer"] = doc["issuer"]
        result["authorization_endpoint"] = doc["authorization_endpoint"]
        result["token_endpoint"] = doc["token_endpoint"]
        result["scopes_supported"] = list(doc.get("scopes_supported") or [])
        result["scopes"] = normalized_scopes(settings)
        _, keys = await get_jwks(settings, doc["jwks_uri"], force=True)
        result["jwks"] = True
        result["jwks_keys"] = len(keys)
        result["client_secret_set"] = bool(settings.get("client_secret"))
        result["ok"] = True
    except OIDCError as e:
        result["error"] = e.reason
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
    result["elapsed_seconds"] = round(time.time() - started, 2)
    return result
