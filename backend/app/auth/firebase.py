"""
Proving who somebody is, rather than taking their word for it.

`POST /v1/auth/token` used to accept an email address and issue that user's
token. Its own docstring said what that was: *"anyone who knows an address can
get that user's token… scoped to local development."* Everything else in this API
verifies a signed token correctly; that one endpoint was the door left open, and
this module is what closes it.

## Why Firebase, and why no secret is needed

The dashboard already signs users in with Firebase
(`dashboard/src/lib/firebase/auth-actions.ts`), so the identity exists — it was
simply never carried to the backend. A Firebase ID token is an RS256 JWT signed
by Google, and verifying one needs only Google's **public** keys and the project
id. No service account, no private credential, nothing to leak: a project id is
public by design.

That is why this could be built now, while the AI provider and Stripe still wait
on keys nobody has.

## What is checked, and why each one matters

- **Signature**, against Google's published keys.
- **`algorithms=["RS256"]`, hard-coded.** The classic JWT forgery is an
  attacker choosing the algorithm — `alg: none`, or `HS256` verified against the
  RSA *public* key as if it were a shared secret. A single-valued list is the
  whole defence and it belongs here, in the file where it matters most.
- **Audience** equals our project id, so a token minted for somebody else's
  Firebase project cannot be replayed at us. Firebase is a shared service; this
  check is what makes "signed by Google" mean "signed for us".
- **Issuer** equals `https://securetoken.google.com/{project}`, which rules out
  the other token types Google signs.
- **Expiry**, by PyJWT, which is why the leeway is left at zero.

`email_verified` is returned rather than enforced here. Whether an unproven
address may hold an account is a decision about *this product*, and it belongs at
the exchange point where it can be refused with a sentence a UI can show.

## The key cache

`PyJWKClient` fetches and caches Google's keys, so this is not a network call per
login. Built once at module scope: the keys are Google's, not a tenant's, and a
client per request would mean a fetch per request.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import jwt
from jwt import PyJWKClient

log = logging.getLogger(__name__)

#: Google's published signing keys for Firebase ID tokens, in JWKS form.
#: (The x509 endpoint carries the same keys as certificates; this one is JWKS,
#: which is what PyJWKClient reads.)
JWKS_URL = (
    "https://www.googleapis.com/service_accounts/v1/jwk/"
    "securetoken@system.gserviceaccount.com"
)

#: Firebase ID tokens are RS256 and nothing else. See the module docstring.
ALGORITHMS = ["RS256"]

_jwks: PyJWKClient | None = None


class FirebaseError(RuntimeError):
    """An ID token could not be verified, with a reason worth showing.

    Carries something a UI can put in front of a person — "your session has
    expired, sign in again" is actionable and "auth failed" is not.
    """


@dataclass(frozen=True)
class FirebaseIdentity:
    """Who Google says this is."""

    #: The Firebase uid. Stable for the life of the account, and the thing to key
    #: on if `auth_user` ever stops being keyed on email.
    uid: str
    email: str
    #: False for an email/password account whose address has never been proven.
    #: The exchange point decides what to do about it.
    email_verified: bool


def _client() -> PyJWKClient:
    global _jwks
    if _jwks is None:
        _jwks = PyJWKClient(JWKS_URL, cache_keys=True)
    return _jwks


def verify_id_token(raw: str, *, project_id: str) -> FirebaseIdentity:
    """Verify a Firebase ID token. Raises `FirebaseError` with the reason."""
    if not raw or not raw.strip():
        raise FirebaseError("no ID token was sent")

    try:
        signing_key = _client().get_signing_key_from_jwt(raw)
    except Exception as exc:  # noqa: BLE001 — every failure here is the same answer
        # Two quite different things land here: a token naming a key that does
        # not exist (a forgery), and Google's key endpoint being unreachable (an
        # outage, or a host with no CA bundle — which is exactly what a local run
        # of this hit). Both mean "not verified", and both **refuse**: a
        # verifier that fell back to trusting the token when it could not check
        # it would be worse than no verifier at all.
        #
        # The reason goes to the log and not to the caller. It carries a URL and
        # a TLS error, which tells somebody probing this endpoint about our
        # infrastructure and tells the person signing in nothing they can act on.
        log.warning("could not verify a Firebase ID token: %s", exc)
        raise FirebaseError(
            "we could not verify that sign-in just now — try again in a moment"
        ) from exc

    try:
        claims = jwt.decode(
            raw,
            signing_key.key,
            algorithms=ALGORITHMS,
            audience=project_id,
            issuer=f"https://securetoken.google.com/{project_id}",
            options={"require": ["exp", "iat", "sub", "aud", "iss"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise FirebaseError("that sign-in has expired — sign in again") from exc
    except jwt.InvalidAudienceError as exc:
        raise FirebaseError(
            "that token was issued for a different Firebase project"
        ) from exc
    except jwt.InvalidIssuerError as exc:
        raise FirebaseError("that token was not issued by Firebase") from exc
    except jwt.InvalidTokenError as exc:
        raise FirebaseError(f"that token is not valid: {exc}") from exc

    email = claims.get("email")
    if not email:
        # An anonymous or phone-number sign-in. Real Firebase identities, and
        # useless here: `auth_user` maps an address to a tenant and a role, so an
        # identity without one cannot be resolved to an organisation.
        raise FirebaseError(
            "that sign-in carries no email address, so it cannot be matched to "
            "an account here"
        )

    return FirebaseIdentity(
        uid=str(claims["sub"]),
        email=str(email).lower(),
        email_verified=bool(claims.get("email_verified")),
    )
