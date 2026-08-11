"""
The cost meter, as executable claims.

One property carries the file: **metering the same spend twice costs nothing
twice.** A cost event with a random id survives every replay as a fresh row, and
a session's metered spend then grows every time the log is reprocessed — in the
direction that makes the product look more expensive than it was, in the figure
a client is most likely to argue about.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import cost
from app.models import EventLog
from tests.conftest import as_tenant

T = "t_test"
S = "s_demo"
BASE = dt.datetime(2026, 8, 11, 10, 0, tzinfo=dt.timezone.utc)


async def _count(session: AsyncSession) -> int:
    return await session.scalar(
        select(func.count()).select_from(EventLog).where(EventLog.type == "cost.metered")
    )


async def test_a_spend_lands_on_the_bus(db_session: AsyncSession) -> None:
    await as_tenant(db_session, T)
    row, created = await cost.meter(
        db_session,
        tenant_id=T,
        session_id=S,
        kind="llm_tokens",
        amount=1350,
        unit="tokens",
        occurred_at=BASE,
        cause=("ask", "q_17"),
    )

    assert created is True
    assert row.type == "cost.metered"
    assert row.payload == {"kind": "llm_tokens", "amount": 1350, "unit": "tokens"}
    # The spend is stamped when the work happened, not when the row was written.
    assert row.occurred_at.replace(tzinfo=dt.timezone.utc) == BASE


async def test_metering_the_same_spend_twice_is_one_row(db_session: AsyncSession) -> None:
    """The reason this module exists. Same cause, same derived id, one row —
    so a replay of the log does not inflate what the session cost."""
    await as_tenant(db_session, T)
    args = dict(
        tenant_id=T, session_id=S, kind="other", amount=0.4, unit="USD",
        occurred_at=BASE, cause=("rules", "r_entry", "ev-9"),
    )

    first_row, first_created = await cost.meter(db_session, **args)
    second_row, second_created = await cost.meter(db_session, **args)

    assert first_created is True
    assert second_created is False
    assert first_row.seq == second_row.seq
    assert await _count(db_session) == 1


async def test_different_causes_are_different_spends(db_session: AsyncSession) -> None:
    """Two Slack posts from the same rule are two costs, not a duplicate — the
    dispatch id is part of the cause for exactly this reason."""
    await as_tenant(db_session, T)
    for fired_event in ("ev-9", "ev-14"):
        await cost.meter(
            db_session, tenant_id=T, session_id=S, kind="other", amount=0.4,
            unit="USD", occurred_at=BASE, cause=("rules", "r_entry", fired_event),
        )

    assert await _count(db_session) == 2


async def test_detail_rides_along_when_given(db_session: AsyncSession) -> None:
    await as_tenant(db_session, T)
    row, _ = await cost.meter(
        db_session, tenant_id=T, session_id=S, kind="llm_tokens", amount=900,
        unit="tokens", occurred_at=BASE, cause=("ask", "q_1"),
        detail={"model": "claude-opus-5", "prompt": 700, "completion": 200},
    )
    assert row.payload["detail"]["model"] == "claude-opus-5"


async def test_a_reading_with_no_unit_or_a_negative_amount_is_refused(
    db_session: AsyncSession,
) -> None:
    """Both would sum into something misleading rather than fail loudly: an
    amount with no unit joins no line on the tile, and a negative spend quietly
    subtracts from a real one."""
    await as_tenant(db_session, T)

    with pytest.raises(ValueError, match="unit is required"):
        await cost.meter(
            db_session, tenant_id=T, session_id=S, kind="other", amount=1,
            unit="", occurred_at=BASE, cause=("x",),
        )

    with pytest.raises(ValueError, match="cannot be negative"):
        await cost.meter(
            db_session, tenant_id=T, session_id=S, kind="other", amount=-1,
            unit="USD", occurred_at=BASE, cause=("x",),
        )

    assert await _count(db_session) == 0
