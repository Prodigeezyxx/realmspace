"""
Encryption at rest for a tenant's credentials.

`multi-tenant.md` §2 states the requirement in one line — "each tenant's
CRM/enrichment credentials are stored encrypted, per-tenant, never shared" — and
until now nothing in the repo could hold one at all, which is why the CRM
adapters were blocked and why `routers/outcomes.py` says outcomes are typed in by
a human.

## Why an unset key refuses rather than storing plaintext

Same decision `actions/webhook.py` takes about its signing secret, and for a
sharper reason. A deployment with no key that quietly stored the token anyway
would put a client's CRM credential in a database column, and nobody would find
out until the database did. Refusing is loud, happens at configuration time
rather than at breach time, and the operator's fix is one environment variable.

There is deliberately no default key. A shipped default is worse than none: it
looks encrypted, reads as a solved problem, and is public.

## Why the tenant and provider are authenticated, not just the secret

AES-GCM takes *associated data* — bytes that are not encrypted but that the
ciphertext is bound to. `f"{tenant_id}:{provider}"` goes in there, so a row
copied from one tenant to another (a bad restore, a careless fixture, a
compromised admin moving rows sideways) fails to decrypt rather than silently
handing tenant A's HubSpot token to tenant B's delivery consumer. The failure
mode we want is a loud one, and RLS alone cannot give it — a policy stops a
*query* crossing tenants, not a row that was written into the wrong one.

## Why AES-GCM rather than Fernet

Fernet is fine and would be one line shorter. It has no associated-data
parameter, which is the property above, and its ciphertext carries a timestamp
this has no use for. Nothing else separates them here.

The nonce is 96 bits from `os.urandom`, prepended to the ciphertext, because a
GCM nonce must never repeat under one key and the only way to be sure across
processes and restarts is to draw it randomly each time rather than count.
"""

from __future__ import annotations

import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import get_settings

#: 96 bits, the size AES-GCM is specified and fastest for.
NONCE_BYTES = 12

#: What `secret_hint` holds — enough to answer "is this the key I pasted?" and
#: not enough to be worth stealing. Four characters of a token nobody can
#: reconstruct the rest of.
HINT_CHARS = 4


class EncryptionUnavailable(RuntimeError):
    """No usable `credential_encryption_key`. Raised instead of storing plaintext."""


def _cipher() -> AESGCM:
    key = get_settings().credential_encryption_key
    if not key:
        raise EncryptionUnavailable(
            "storing a tenant credential needs settings.credential_encryption_key, "
            "which is unset — refusing to write a CRM token unencrypted. Generate "
            'one with: python -c "import base64,os; '
            'print(base64.urlsafe_b64encode(os.urandom(32)).decode())"'
        )
    try:
        raw = base64.urlsafe_b64decode(key)
    except Exception as exc:  # noqa: BLE001 — any decode failure is the same problem
        raise EncryptionUnavailable(
            "credential_encryption_key is not valid urlsafe base64"
        ) from exc
    if len(raw) != 32:
        raise EncryptionUnavailable(
            f"credential_encryption_key decodes to {len(raw)} bytes; AES-256-GCM "
            "needs exactly 32"
        )
    return AESGCM(raw)


def _aad(tenant_id: str, provider: str) -> bytes:
    """What the ciphertext is bound to. See the module docstring."""
    return f"{tenant_id}:{provider}".encode()


def encrypt(secret: str, *, tenant_id: str, provider: str) -> bytes:
    """`nonce || ciphertext`, ready for the `secret_ct` column."""
    nonce = os.urandom(NONCE_BYTES)
    ct = _cipher().encrypt(nonce, secret.encode(), _aad(tenant_id, provider))
    return nonce + ct


def decrypt(blob: bytes, *, tenant_id: str, provider: str) -> str:
    """The plaintext, or a loud failure.

    An `InvalidTag` here means one of three things and all of them are serious:
    the key changed, the row was tampered with, or the row belongs to a
    different tenant/provider than the caller claims. None of them should
    degrade into an empty string or a `None` that a caller might send to a CRM
    as an empty bearer token.
    """
    if len(blob) <= NONCE_BYTES:
        raise EncryptionUnavailable(
            f"stored credential is {len(blob)} bytes, too short to contain a nonce "
            "and a ciphertext"
        )
    nonce, ct = bytes(blob[:NONCE_BYTES]), bytes(blob[NONCE_BYTES:])
    try:
        return _cipher().decrypt(nonce, ct, _aad(tenant_id, provider)).decode()
    except InvalidTag as exc:
        raise EncryptionUnavailable(
            f"stored credential for {tenant_id}/{provider} did not decrypt — the "
            "encryption key has changed, or this row was written for a different "
            "tenant or provider"
        ) from exc


def hint(secret: str) -> str:
    """The last few characters, for a UI showing which credential is stored.

    Short secrets get nothing rather than most of themselves: a four-character
    hint of a six-character token is the token.
    """
    return secret[-HINT_CHARS:] if len(secret) > HINT_CHARS * 2 else ""


def generate_key() -> str:
    """A key in the format the setting wants. For docs, tests and first setup."""
    return base64.urlsafe_b64encode(os.urandom(32)).decode()
