"""
Issuing and checking credentials. No network, ever.

`event-bus-spec.md` §1: the edge box must keep working when the conference wifi
does not. So tokens are signed and verified with a local secret, and API keys are
checked against a local table. Nothing here can be slowed down or broken by an
unreachable identity provider.

Firebase still has a place — the dashboard already uses it to establish *who
someone is*. The intended flow is that a Firebase login is exchanged once at
`POST /v1/auth/token` for one of these, and the hot path never touches Google
again. That exchange is not built yet; email lookup stands in for it.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import secrets

import jwt

from app.config import get_settings

ALGORITHM = "HS256"

#: Prefix on every issued key. Purely so a leaked string is recognisable as a
#: realmspace credential in a log or a paste — secret scanners key off patterns
#: like this, and a bare random string tells nobody anything.
KEY_PREFIX = "rsk_"


class AuthError(Exception):
    """Credential missing, malformed, expired or revoked.

    One exception for every failure on purpose. Distinguishing "no such user"
    from "wrong signature" in the response tells an attacker which half of a
    guess was right; the caller turns all of these into the same 401.
    """


# ── JWTs, for humans ──────────────────────────────────────────────────────────


def issue_token(*, subject: str, tenant_id: str, role: str) -> str:
    """Sign a token for a user.

    `tenant_id` is inside the signed payload, which is the entire point of this
    task: it stops being something the caller can assert and becomes something
    the server stated and can verify.
    """
    settings = get_settings()
    now = dt.datetime.now(dt.timezone.utc)
    payload = {
        "sub": subject,
        "tenant_id": tenant_id,
        "role": role,
        "kind": "user",
        "iat": now,
        "exp": now + dt.timedelta(seconds=settings.jwt_ttl_seconds),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def verify_token(token: str) -> dict:
    """Decode and validate, or raise AuthError.

    `algorithms=[ALGORITHM]` is not boilerplate. Accepting whatever algorithm
    the token names is the classic JWT vulnerability — a forged header saying
    `alg: none`, or an RS256 key confusion attack. Pinning it means the only
    token that verifies is one signed the way we sign them.
    """
    settings = get_settings()
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError("invalid token") from exc


# ── API keys, for devices ─────────────────────────────────────────────────────


def generate_api_key() -> tuple[str, str, str]:
    """Mint a key. Returns (key_id, plaintext, hash).

    The plaintext is returned once, to be shown to whoever is provisioning the
    device, and then forgotten. Only the hash is stored — see ApiKey's docstring
    for why SHA-256 rather than bcrypt is the right call for a random token.
    """
    key_id = f"k_{secrets.token_hex(8)}"
    plaintext = f"{KEY_PREFIX}{secrets.token_urlsafe(32)}"
    return key_id, plaintext, hash_api_key(plaintext)


def hash_api_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


def api_keys_match(candidate_hash: str, stored_hash: str) -> bool:
    """Constant-time comparison.

    A plain `==` on a hash leaks, through timing, how many leading characters
    were right — enough to reconstruct a value byte by byte given enough
    attempts. The cost of avoiding that is one function call.
    """
    return hmac.compare_digest(candidate_hash, stored_hash)
