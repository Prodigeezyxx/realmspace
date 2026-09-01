"""
Touchpoint tokens: minting one, and turning one back into a surface.

The store half of the tablet producer. `app/share.py` is the same file for the
read side, and the crypto is **imported from it rather than copied** — one
`hash_token` in the application, as `app/actions/webhook.sign` is one signature
for the rule action and the handoff delivery both.

## What this exists to produce

`surface.interaction` has had every reader since Phase 2 — `graph_writer` draws
`INTERACTED_WITH`, `llm/digest.py` reads it, `scorecard.ts` scores the
Engagement layer of the four the report is sold on — and no producer. The
roadmap's reason was "real interactions need booth hardware", which is true of
an RFID plinth and not true of a tablet running a browser page.

## Why a token and not a device key

`api_key` is tenant-wide and cannot be revoked without rotating the camera's
too. A tablet on a stand is the device most likely to be picked up and carried
off, so it gets a credential that can be withdrawn on its own — migration 0015
has the argument in full.

## The three ids come off the row

`tenant_id`, `session_id` and `surface_id` are on the token and are never read
from the request. A tablet that could name its own touchpoint could post as any
of them, and `routers/events.py` already records what "validation you have to
remember to write" costs.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SurfaceToken
from app.share import hash_token, mint

#: A fortnight. An activation runs for days, and this is an unauthenticated
#: write path — `report_share`'s month is for a conversation with a client that
#: continues after the stand comes down, and a tablet's does not.
DEFAULT_EXPIRY_DAYS = 14

#: `report_share`'s ceiling, for the same reason: `expires_at` is NOT NULL so
#: that "forever" cannot be expressed, and this bounds how close to it somebody
#: gets by typing a large number.
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
) -> tuple[SurfaceToken, str]:
    """Mint a tablet's token and store its digest. Returns the row **and the
    plaintext**, whose only appearance is the response the caller is about to
    build. Reading it back later is impossible rather than unimplemented."""
    token, digest, hint = mint()
    days = max(1, min(int(expires_in_days), MAX_EXPIRY_DAYS))

    row = SurfaceToken(
        id=f"tch_{uuid.uuid4().hex[:12]}",
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
) -> SurfaceToken | None:
    """The touchpoint a token posts to, or None.

    **One `None` for every reason** — unknown, expired, revoked — which the
    caller turns into one 404. `share.resolve` has the argument: telling
    somebody holding a guess which of the three it was is the only thing they
    could act on.
    """
    row = (
        await session.execute(
            select(SurfaceToken).where(SurfaceToken.token_sha256 == hash_token(token))
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
    """Record that the tablet is alive.

    Not a duplicate of the tally, and **not the same number as the report's**.
    This counts *requests* — so a tablet's retry over bad wifi counts twice here
    and once on the log, which is the point: it answers "is the tablet by the
    door still alive", a question about the device rather than about the floor.

    The floor's numbers are the log's: `surface.touched` is every tap, and
    `Surface.trigger_count` is the subset `consumers/touch.py` could name
    somebody for. An operator reading this row is asking whether to go and look
    at the tablet.
    """
    await session.execute(
        update(SurfaceToken)
        .where(SurfaceToken.id == token_id)
        .values(last_used_at=now, use_count=SurfaceToken.use_count + 1)
    )


async def revoke(
    session: AsyncSession, *, tenant_id: str, token_id: str, now: dt.datetime
) -> bool:
    """Withdraw one tablet. Scoped to the caller's verified tenant explicitly,
    because this table is outside RLS — 0015's docstring says why."""
    result = await session.execute(
        update(SurfaceToken)
        .where(
            SurfaceToken.id == token_id,
            SurfaceToken.tenant_id == tenant_id,
            SurfaceToken.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    return bool(result.rowcount)


async def for_session(
    session: AsyncSession, *, tenant_id: str, session_id: str
) -> list[SurfaceToken]:
    """Every tablet on one activation, newest first."""
    return list(
        (
            await session.execute(
                select(SurfaceToken)
                .where(
                    SurfaceToken.tenant_id == tenant_id,
                    SurfaceToken.session_id == session_id,
                )
                .order_by(SurfaceToken.created_at.desc())
            )
        ).scalars()
    )
