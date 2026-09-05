"""
Share tokens: minting one, and turning one back into an activation.

The store and the crypto live here rather than in a router, because the two
halves are written months apart by different people and the invariant is
between them: **the plaintext exists for exactly one function call**, and every
other part of the system only ever sees a digest.

## Why a stored token and not a JWT

`roadmap.md` asks for "signed, expiring", and a signed token gives both for
free. It does not give **revocation**, which the same line asks for by implying
an operator can take a link back. Revoking a JWT needs a denylist, a denylist is
a table, and once there is a table the signature is doing no work the table does
not already do. So: 32 random bytes, a row, and `revoked_at`.

## What a leaked URL is worth

One activation's report, with contact details removed — `routers/share.py`
applies `erasure.redact` to every PII-typed event on the way out. Not the live
feed, not the twin, not Ask, not the ledger, not any write path. The reader
endpoints are three, and they are the only routes in the application that accept
a token, which is what makes "what does this expose" answerable by reading one
file.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import secrets
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ReportShare

#: 32 bytes of `secrets` entropy, urlsafe-encoded. Guessing one is not a threat
#: model anybody needs to think further about; losing one is, which is what the
#: expiry and `revoked_at` are for.
TOKEN_BYTES = 32

#: A month, which is about the life of a post-activation conversation with a
#: client. Not forever: a link is an unauthenticated read path, and the default
#: should expire on its own if everybody involved forgets about it.
DEFAULT_EXPIRY_DAYS = 30

#: A year. `expires_at` is NOT NULL precisely so "forever" cannot be expressed,
#: and this is the ceiling on how close to forever somebody can get by typing a
#: large number.
MAX_EXPIRY_DAYS = 365


def hash_token(token: str) -> str:
    """The digest the row is found by.

    sha256 and not a password hash. A password is low-entropy and needs the work
    factor; this is 32 random bytes, where the only attack is a lookup and the
    cost of one is already irrelevant. What the digest buys is that a database
    dump does not contain a working link.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def mint() -> tuple[str, str, str]:
    """A new token, its digest, and its hint. The only place plaintext exists."""
    token = secrets.token_urlsafe(TOKEN_BYTES)
    return token, hash_token(token), token[-4:]


async def create(
    session: AsyncSession,
    *,
    tenant_id: str,
    session_id: str,
    created_by: str,
    label: str | None,
    expires_in_days: int,
    now: dt.datetime,
) -> tuple[ReportShare, str]:
    """Mint a link and store its digest. Returns the row **and the plaintext**.

    The plaintext is returned rather than stored, and the caller's only chance to
    show it is the response it is about to build. Reading it back later is not
    unimplemented — it is impossible, which is the property worth having.
    """
    token, digest, hint = mint()
    days = max(1, min(int(expires_in_days), MAX_EXPIRY_DAYS))

    row = ReportShare(
        id=f"shr_{uuid.uuid4().hex[:12]}",
        tenant_id=tenant_id,
        session_id=session_id,
        token_sha256=digest,
        hint=hint,
        label=label,
        created_by=created_by,
        expires_at=now + dt.timedelta(days=days),
    )
    session.add(row)
    await session.flush()
    return row, token


async def resolve(
    session: AsyncSession, *, token: str, now: dt.datetime
) -> ReportShare | None:
    """The activation a token opens, or None.

    **One `None` for every reason.** Unknown, expired and revoked are all the
    same answer, and the caller turns all three into a 404. Distinguishing them
    tells somebody holding a guess whether it was ever a real link, which is the
    only information a stranger could act on.
    """
    row = (
        await session.execute(
            select(ReportShare).where(ReportShare.token_sha256 == hash_token(token))
        )
    ).scalar_one_or_none()

    if row is None:
        return None
    if row.revoked_at is not None:
        return None
    if row.expires_at <= now:
        return None
    return row


async def note_view(session: AsyncSession, *, share_id: str, now: dt.datetime) -> None:
    """Record that somebody opened it.

    So an operator can answer "has the client looked at it yet", which is the
    first thing they ask after sending one. Deliberately not a per-viewer
    record: this counts openings, and turning an unauthenticated link into a
    thing that profiles the people who follow it would be a worse trade than the
    question is worth.
    """
    await session.execute(
        update(ReportShare)
        .where(ReportShare.id == share_id)
        .values(last_viewed_at=now, view_count=ReportShare.view_count + 1)
    )
