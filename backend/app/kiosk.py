"""
Consent kiosks: minting one, and turning one back into a surface.

The store half of the capture surface. `app/touch.py` is the same file for the
tablet, `app/share.py` for the read link, and the crypto is **imported from
share rather than copied** — one `hash_token` in the application, as
`app/actions/webhook.sign` is one signature for the rule action and the handoff
delivery both.

## What this exists to produce

`consent.captured` has had every reader since Phase 4 — `consumers/identity.py`
draws `IDENTIFIED_AS`, attribution builds the lead, five CRM adapters deliver
it, the SDR drafts from it, the ledger audits it — and its only producer was
`curl`. `roadmap.md` said so in one line: *"the surfaces themselves (badge/QR/
kiosk hardware) are not built"*. A phone pointed at a QR code on a plinth is
that surface.

## Why a token and not a device key

`api_key` is tenant-wide and is what the camera box presents. A kiosk's
credential is printed and stuck to furniture in a public room, which is the most
copyable thing in the system, so it gets one that can be withdrawn on its own —
and one that is **not** the tablet's, because a single leaked photograph should
not also be able to inflate a touchpoint's tally. Migration 0016 has the
argument in full.

## The three ids come off the row

`tenant_id`, `session_id` and `surface_id` are on the token and are never read
from the request. The zone that attributes a consent hangs off that Surface, so
a caller that could name its own kiosk could choose the zone its visitors are
counted in.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ConsentToken
from app.share import hash_token, mint

#: A fortnight, `touch.DEFAULT_EXPIRY_DAYS`: an activation runs for days and
#: this is an unauthenticated write path. A report share's month is for a
#: conversation that continues after the stand comes down; a kiosk's does not.
DEFAULT_EXPIRY_DAYS = 14

#: The same ceiling as everywhere else here, for the same reason: `expires_at`
#: is NOT NULL so that "forever" cannot be expressed, and this bounds how close
#: to it somebody gets by typing a large number.
MAX_EXPIRY_DAYS = 365


async def create(
    session: AsyncSession,
    *,
    tenant_id: str,
    session_id: str,
    surface_id: str,
    created_by: str,
    label: str | None,
    expires_in_days: int,
    now: dt.datetime,
) -> tuple[ConsentToken, str]:
    """Mint a kiosk's token and store its digest. Returns the row **and the
    plaintext**, whose only appearance is the response the caller is about to
    build — the QR code an operator prints from it is the last copy."""
    token, digest, hint = mint()
    days = max(1, min(int(expires_in_days), MAX_EXPIRY_DAYS))

    row = ConsentToken(
        id=f"ksk_{uuid.uuid4().hex[:12]}",
        tenant_id=tenant_id,
        session_id=session_id,
        surface_id=surface_id,
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
) -> ConsentToken | None:
    """The kiosk a token captures for, or None.

    **One `None` for every reason** — unknown, expired, revoked — which the
    caller turns into one 404. `share.resolve` has the argument: which of the
    three it was is the only thing a stranger holding a guess could act on.
    """
    row = (
        await session.execute(
            select(ConsentToken).where(ConsentToken.token_sha256 == hash_token(token))
        )
    ).scalar_one_or_none()

    if row is None:
        return None
    if row.revoked_at is not None:
        return None
    if row.expires_at <= now:
        return None
    return row


async def note_use(session: AsyncSession, *, token_id: str, now: dt.datetime) -> None:
    """Record that the kiosk is being scanned.

    Counts **requests**, like `touch.note_use` and for its reason: a retry over
    bad venue wifi counts twice here and once on the log. It answers "is the
    plinth by the door still in use", which is a question about the device. How
    many people consented is the log's answer, and the two must not be read as
    one number — this one includes every visitor who opened the page, read the
    copy, and walked away without agreeing, which is a thing we deliberately do
    not record on the bus.
    """
    await session.execute(
        update(ConsentToken)
        .where(ConsentToken.id == token_id)
        .values(last_used_at=now, use_count=ConsentToken.use_count + 1)
    )


async def revoke(
    session: AsyncSession, *, tenant_id: str, token_id: str, now: dt.datetime
) -> bool:
    """Withdraw one kiosk. Scoped to the caller's verified tenant explicitly,
    because this table is outside RLS — 0016's docstring says why."""
    result = await session.execute(
        update(ConsentToken)
        .where(
            ConsentToken.id == token_id,
            ConsentToken.tenant_id == tenant_id,
            ConsentToken.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    return bool(result.rowcount)


async def for_session(
    session: AsyncSession, *, tenant_id: str, session_id: str
) -> list[ConsentToken]:
    """Every kiosk on one activation, newest first."""
    return list(
        (
            await session.execute(
                select(ConsentToken)
                .where(
                    ConsentToken.tenant_id == tenant_id,
                    ConsentToken.session_id == session_id,
                )
                .order_by(ConsentToken.created_at.desc())
            )
        ).scalars()
    )
