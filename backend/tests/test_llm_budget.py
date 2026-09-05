"""
The ceiling on what this deployment spends with a model vendor.

The properties, in the order they would hurt:

**Unset changes nothing.** The default is `None` and no tier states a token
number, so a deployment that has not configured a budget must behave exactly as
it did before this existed. Every other test in the suite asserts that by
passing; this file asserts it directly, because "the default ships nothing" is
the claim that made it safe to add.

**A spent budget is not an error.** All three spenders already fall back to a
deterministic answer when a provider raises, and an exhausted budget is that same
situation. A consumer that failed here would stall its cursor over a sentence.

**The answer stops claiming a model wrote it.** `basis` is what every surface
renders, so it has to follow the fallback. An answer that said `openrouter` while
a keyword matcher answered would be false in the one field that exists to prevent
exactly that.

**The sum is the log's.** No usage table: a derived event id means a replayed
meter cannot inflate it, so counting rows is counting spend.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import repository
from app.config import get_settings
from app.cost import meter, spent_tokens
from app.llm import budget
from app.schemas import EventIn

T = "t_budget"
S = "s_budget"
BASE = dt.datetime(2026, 8, 15, 10, 0, tzinfo=dt.timezone.utc)


@pytest.fixture(autouse=True)
async def _scope_to_test_tenant(db_session: AsyncSession):
    from tests.conftest import as_tenant

    await as_tenant(db_session, T)
    yield


@pytest.fixture
def budget_of():
    """Set the deployment budget for one test and put it back.

    On the cached Settings object, for the reason `conftest.encryption_key`
    gives: `get_settings` is `lru_cache`d and an env var written afterwards would
    be read by nothing.
    """
    settings = get_settings()
    before = settings.llm_monthly_token_budget

    def _set(value: int | None) -> None:
        settings.llm_monthly_token_budget = value

    yield _set
    settings.llm_monthly_token_budget = before


async def _spend(
    db_session: AsyncSession,
    *,
    tokens: int,
    spender: str = "ask",
    at: dt.datetime | None = None,
    cause: str = "q1",
) -> None:
    await meter(
        db_session,
        tenant_id=T,
        session_id=S,
        kind="llm_tokens",
        amount=float(tokens),
        unit="tokens",
        occurred_at=at or BASE,
        cause=(spender, cause),
        detail={"provider": "openrouter", "spender": spender},
    )
    await db_session.commit()


# ── the sum ───────────────────────────────────────────────────────────────────


async def test_nothing_spent_is_zero_not_none(db_session: AsyncSession) -> None:
    """A tenant who has never asked anything reads zero, so the caller does not
    have to know the difference between "no rows" and "no spend"."""
    assert await spent_tokens(db_session, tenant_id=T, since=BASE) == 0


async def test_the_sum_is_every_llm_spend_in_the_window(
    db_session: AsyncSession,
) -> None:
    await _spend(db_session, tokens=100, cause="q1")
    await _spend(db_session, tokens=250, cause="q2")
    assert await spent_tokens(db_session, tenant_id=T, since=BASE) == 350


async def test_spend_before_the_window_does_not_count(
    db_session: AsyncSession,
) -> None:
    """On `occurred_at`, like every other window here — a batch replayed late
    counts against the month it was spent in."""
    await _spend(db_session, tokens=999, at=BASE - dt.timedelta(days=40), cause="old")
    await _spend(db_session, tokens=10, cause="new")
    assert await spent_tokens(db_session, tenant_id=T, since=BASE) == 10


async def test_action_units_are_not_tokens(db_session: AsyncSession) -> None:
    """`roi-framework.md`'s rule, in the meter: adding a Slack post to a token
    count produces a number that looks like unit economics and is not."""
    await meter(
        db_session,
        tenant_id=T,
        session_id=S,
        kind="action_unit",
        amount=5000.0,
        unit="actions",
        occurred_at=BASE,
        cause=("rule", "r1"),
    )
    await db_session.commit()
    assert await spent_tokens(db_session, tenant_id=T, since=BASE) == 0


async def test_the_sum_can_be_asked_of_one_spender(db_session: AsyncSession) -> None:
    """"What is the timer costing us" is a different question from "what are
    operators asking", and the timer is the one this budget exists for."""
    await _spend(db_session, tokens=100, spender="ask", cause="q1")
    await _spend(db_session, tokens=40, spender="insight", cause="w1")
    await _spend(db_session, tokens=7, spender="sdr", cause="c1")

    assert await spent_tokens(db_session, tenant_id=T, since=BASE) == 147
    assert (
        await spent_tokens(db_session, tenant_id=T, since=BASE, spender="insight") == 40
    )


async def test_a_replayed_meter_does_not_inflate_the_sum(
    db_session: AsyncSession,
) -> None:
    """The whole reason `cost.py` derives its ids. Counting rows is counting
    spend only if the same spend cannot be written twice."""
    await _spend(db_session, tokens=100, cause="q1")
    await _spend(db_session, tokens=100, cause="q1")
    assert await spent_tokens(db_session, tenant_id=T, since=BASE) == 100


async def test_a_purged_row_leaves_the_sum(db_session: AsyncSession) -> None:
    """Documented in `cost.py`, and correct rather than unfortunate.

    Retention empties a payload past the client's window. A budget counting
    spend the client may no longer see would enforce against a number nobody
    can check.
    """
    await _spend(db_session, tokens=100, cause="q1")
    rows = await repository.read_events(
        db_session, tenant_id=T, session_id=S, type="cost.metered", limit=10
    )
    await repository.redact_event(
        db_session, seq=rows[0].seq, payload={}, at=BASE
    )
    await db_session.commit()

    assert await spent_tokens(db_session, tenant_id=T, since=BASE) == 0


async def test_another_tenants_spend_is_invisible(db_session: AsyncSession) -> None:
    """RLS, asserted where it would be most expensive to get wrong: one tenant
    could otherwise exhaust another's budget."""
    await _spend(db_session, tokens=100, cause="q1")
    assert await spent_tokens(db_session, tenant_id="t_someone_else", since=BASE) == 0


# ── the ceiling ───────────────────────────────────────────────────────────────


async def test_an_unset_budget_permits_everything(
    db_session: AsyncSession, budget_of
) -> None:
    """The default. It is also why this could be added without deciding anything
    about pricing."""
    budget_of(None)
    await _spend(db_session, tokens=10_000_000, cause="q1")

    may_call, reason = await budget.within_budget(
        db_session, tenant_id=T, spender="ask"
    )
    assert may_call is True
    assert reason == ""


async def test_under_the_budget_permits_the_call(
    db_session: AsyncSession, budget_of
) -> None:
    budget_of(1000)
    await _spend(db_session, tokens=999, at=budget.month_start(), cause="q1")

    may_call, _ = await budget.within_budget(db_session, tenant_id=T, spender="ask")
    assert may_call is True


async def test_reaching_the_budget_refuses_and_says_both_numbers(
    db_session: AsyncSession, budget_of
) -> None:
    """A person reads this sentence. "Budget exceeded" tells them nothing about
    whether it was nearly spent or wildly over."""
    budget_of(1000)
    await _spend(db_session, tokens=1000, at=budget.month_start(), cause="q1")

    may_call, reason = await budget.within_budget(
        db_session, tenant_id=T, spender="insight"
    )
    assert may_call is False
    assert "1,000" in reason
    assert "insight" in reason


async def test_last_months_spend_does_not_count_against_this_month(
    db_session: AsyncSession, budget_of
) -> None:
    """Calendar month, because that is the unit the vendor invoices in."""
    budget_of(100)
    last_month = budget.month_start() - dt.timedelta(days=1)
    await _spend(db_session, tokens=5000, at=last_month, cause="old")

    may_call, _ = await budget.within_budget(db_session, tenant_id=T, spender="ask")
    assert may_call is True


def test_the_month_starts_at_its_first_instant() -> None:
    at = dt.datetime(2026, 8, 31, 23, 59, 59, tzinfo=dt.timezone.utc)
    assert budget.month_start(at) == dt.datetime(
        2026, 8, 1, tzinfo=dt.timezone.utc
    )
