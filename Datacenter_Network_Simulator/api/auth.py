"""
Authentication for the REST API — username + password, HS256 JWT.

Zero external dependencies: HMAC-SHA256 JWTs and PBKDF2-SHA256 password
hashing are both built from the standard library, so the PyInstaller bundle
stays free of native crypto wheels.

User store (multi-user ready)
-----------------------------
Users live in a JSON file (DCIM_AUTH_USERS_FILE, default ./auth_users.json):

    {
      "admin": {"pw_hash": "pbkdf2_sha256$...", "role": "admin"},
      "alice": {"pw_hash": "pbkdf2_sha256$...", "role": "user"}
    }

If that file does not exist, a single bootstrap user is synthesised from
environment variables — this is the original single-password setup:

    DCIM_AUTH_USERNAME       — bootstrap username (default "admin")
    DCIM_AUTH_PASSWORD_HASH  — its PBKDF2 hash

So existing deployments keep working: log in as `admin` with the old password.
Create the file (and more users) with:

    python -m api.auth add-user

Other config:
    DCIM_AUTH_SECRET     — JWT signing secret. REQUIRED (api.main refuses to
                           serve without it).
    DCIM_AUTH_TTL_HOURS  — token lifetime in hours (default 12).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Optional

from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

# ── Config ───────────────────────────────────────────────────────────────────

SECRET: str = os.environ.get("DCIM_AUTH_SECRET", "")
_ALGO = "HS256"
TTL_SECONDS: int = int(float(os.environ.get("DCIM_AUTH_TTL_HOURS", "12")) * 3600)

_PBKDF2_ROUNDS = 200_000
_PBKDF2_PREFIX = "pbkdf2_sha256"

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USERS_FILE: str = os.environ.get(
    "DCIM_AUTH_USERS_FILE", os.path.join(_PROJECT_ROOT, "auth_users.json")
)

# Precomputed hash of a random string. Verifying against it when a username is
# unknown keeps login timing roughly constant, so attackers can't enumerate
# valid usernames by response time.
_DUMMY_HASH = ""  # filled lazily on first authenticate()


# ── Password hashing (PBKDF2-SHA256) ─────────────────────────────────────────

def hash_password(plain: str, *, rounds: int = _PBKDF2_ROUNDS) -> str:
    """Return a self-describing hash string: pbkdf2_sha256$rounds$salt$hash."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt, rounds)
    return f"{_PBKDF2_PREFIX}${rounds}${salt.hex()}${digest.hex()}"


def verify_hash(plain: str, stored_hash: str) -> bool:
    """Constant-time check of *plain* against a stored PBKDF2 hash string."""
    if not stored_hash:
        return False
    try:
        prefix, rounds_s, salt_hex, hash_hex = stored_hash.split("$")
        if prefix != _PBKDF2_PREFIX:
            return False
        rounds = int(rounds_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, AttributeError):
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt, rounds)
    return hmac.compare_digest(candidate, expected)


# ── User store ───────────────────────────────────────────────────────────────

def load_users() -> dict:
    """
    Return {username: {"pw_hash": str, "role": str}}.

    Prefers the JSON users file; falls back to a single env-defined bootstrap
    user when the file is absent.
    """
    if os.path.isfile(USERS_FILE):
        try:
            with open(USERS_FILE, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass
        return {}
    # Env bootstrap (legacy single-password mode)
    env_hash = os.environ.get("DCIM_AUTH_PASSWORD_HASH", "")
    if env_hash:
        username = os.environ.get("DCIM_AUTH_USERNAME", "admin")
        return {username: {"pw_hash": env_hash, "role": "admin"}}
    return {}


def save_users(users: dict) -> None:
    tmp = USERS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(users, fh, indent=2)
    os.replace(tmp, USERS_FILE)


def add_user(username: str, password: str, role: str = "user") -> None:
    """Create or update a user in the JSON store."""
    users = {}
    if os.path.isfile(USERS_FILE):
        try:
            with open(USERS_FILE, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                users = loaded
        except (OSError, ValueError):
            users = {}
    users[username] = {"pw_hash": hash_password(password), "role": role}
    save_users(users)


def authenticate(username: str, password: str) -> Optional[dict]:
    """Return claims {username, role} on success, else None. Timing-hardened."""
    global _DUMMY_HASH
    if not _DUMMY_HASH:
        _DUMMY_HASH = hash_password(secrets.token_urlsafe(16))
    users = load_users()
    rec = users.get(username)
    stored = rec.get("pw_hash", "") if rec else _DUMMY_HASH
    ok = verify_hash(password, stored)
    if rec and ok:
        return {"username": username, "role": rec.get("role", "user")}
    return None


# ── JWT (HS256) ──────────────────────────────────────────────────────────────

def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(seg: str) -> bytes:
    pad = "=" * (-len(seg) % 4)
    return base64.urlsafe_b64decode(seg + pad)


def issue_token(username: str, role: str = "user") -> str:
    now = int(time.time())
    header = {"alg": _ALGO, "typ": "JWT"}
    payload = {"sub": username, "role": role, "iat": now, "exp": now + TTL_SECONDS}
    h = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    p = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{h}.{p}".encode("ascii")
    sig = hmac.new(SECRET.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{h}.{p}.{_b64url_encode(sig)}"


def _decode_token(token: str) -> dict:
    """Validate signature + expiry. Raise 401 on any problem."""
    if not SECRET:
        # Should never happen — main.py refuses to serve without a secret.
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Auth not configured")
    try:
        h, p, s = token.split(".")
    except ValueError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Malformed token")
    signing_input = f"{h}.{p}".encode("ascii")
    expected_sig = hmac.new(SECRET.encode("utf-8"), signing_input, hashlib.sha256).digest()
    try:
        got_sig = _b64url_decode(s)
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Malformed token")
    if not hmac.compare_digest(got_sig, expected_sig):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token signature")
    try:
        payload = json.loads(_b64url_decode(p))
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Malformed token payload")
    if int(payload.get("exp", 0)) < int(time.time()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expired")
    return payload


# ── FastAPI dependencies ─────────────────────────────────────────────────────

_bearer = HTTPBearer(auto_error=False)


def require_auth(cred: HTTPAuthorizationCredentials = Depends(_bearer)) -> dict:
    """Protect normal routes — expects `Authorization: Bearer <jwt>`."""
    if cred is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _decode_token(cred.credentials)


def require_auth_query(token: str = Query(..., description="JWT (EventSource cannot send headers)")) -> dict:
    """Protect SSE — the browser EventSource API can only pass a query param."""
    return _decode_token(token)


# ── CLI: python -m api.auth add-user | set-password ──────────────────────────

def _cli_add_user() -> None:
    import getpass

    username = input("Username: ").strip()
    if not username:
        print("Username required.")
        raise SystemExit(1)
    pw1 = getpass.getpass("Password: ")
    pw2 = getpass.getpass("Confirm password: ")
    if pw1 != pw2:
        print("Passwords do not match.")
        raise SystemExit(1)
    if len(pw1) < 8:
        print("Password must be at least 8 characters.")
        raise SystemExit(1)
    role = input("Role [user]: ").strip() or "user"
    add_user(username, pw1, role)
    print(f"\nUser '{username}' (role={role}) written to {USERS_FILE}")
    if not SECRET and not os.environ.get("DCIM_AUTH_SECRET"):
        new_secret = secrets.token_urlsafe(48)
        print("\nDCIM_AUTH_SECRET is not set yet. Set this (User env var) and restart:\n")
        print(f'DCIM_AUTH_SECRET="{new_secret}"')


def _cli_set_password() -> None:
    """Legacy single-user mode — print env vars instead of writing the file."""
    import getpass

    username = input("Username [admin]: ").strip() or "admin"
    pw1 = getpass.getpass("New API password: ")
    pw2 = getpass.getpass("Confirm password: ")
    if pw1 != pw2:
        print("Passwords do not match.")
        raise SystemExit(1)
    if len(pw1) < 8:
        print("Password must be at least 8 characters.")
        raise SystemExit(1)
    pw_hash = hash_password(pw1)
    new_secret = secrets.token_urlsafe(48)
    print("\nSet these environment variables (User scope) and restart:\n")
    print(f'DCIM_AUTH_SECRET="{new_secret}"')
    print(f'DCIM_AUTH_PASSWORD_HASH="{pw_hash}"')
    if username != "admin":
        print(f'DCIM_AUTH_USERNAME="{username}"')
    print(
        "\nKeep DCIM_AUTH_SECRET secret — anyone who has it can mint valid tokens.\n"
        "Reuse the SAME secret across restarts or existing logins are invalidated.\n"
        "For multiple users, use `python -m api.auth add-user` instead."
    )


if __name__ == "__main__":
    import sys

    cmd = sys.argv[1] if len(sys.argv) >= 2 else ""
    if cmd == "add-user":
        _cli_add_user()
    elif cmd == "set-password":
        _cli_set_password()
    else:
        print("usage: python -m api.auth [add-user | set-password]")
        raise SystemExit(2)
