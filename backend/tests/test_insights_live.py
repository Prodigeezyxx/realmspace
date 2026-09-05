"""
The insight consumer, against a real model. Skipped unless asked for.

    OPENROUTER_LIVE_KEY=sk-or-v1-… .venv/bin/python -m pytest tests/test_insights_live.py -q -s

`roadmap.md` Phase 5 kept this bullet 🟡 after the provider landed, and the reason
was precise: the consumer reaches the model through the same seam `/ask` does and
is tested against it, but nobody had watched a real model write one. Sharing a
code path is not the same as having walked it — `/ask` proved that by exposing
two bugs that every mocked test had passed over.

## What it has to hold that a mock cannot

The insight is a *sentence about measurements*, and the risk a real model brings
is the one `roi-framework.md` rules out everywhere else: a number in the prose
that is not in the data. So the assertions are about the numbers, not the style —
the measurements are the digest's own, the citations still resolve to the events
they claim, and no figure appears in the text that the window did not measure.
"""

from __future__ import annotations

import os
import re

import pytest
from neo4j import AsyncSession as GraphSession
from sqlalchemy.ext.asyncio import AsyncSession

from app import llm, repository, secrets
from app.config import get_settings
from app.consumers.insights import InsightsConsumer
from tests.test_handoff import S, T, seed_activation
from tests.test_insights import a_busy_ten_minutes, insights

LIVE_KEY = os.environ.get("OPENROUTER_LIVE_KEY", "")

pytestmark = pytest.mark.skipif(
    not LIVE_KEY,
    reason="set OPENROUTER_LIVE_KEY to run the live insight walk",
)


@pytest.fixture(autouse=True)
async def _scope_to_test_tenant(db_session: AsyncSession):
    from tests.conftest import as_tenant

    await as_tenant(db_session, T)
    yield


@pytest.fixture
def encrypted():
    settings = get_settings()
    before = settings.credential_encryption_key
    settings.credential_encryption_key = secrets.generate_key()
    yield
    settings.credential_encryption_key = before


async def test_a_real_model_writes_the_insight_and_invents_no_figure(
    db_session: AsyncSession, graph_session: GraphSession, encrypted
) -> None:
    await repository.upsert_integration(
        db_session,
        tenant_id=T,
        provider="openrouter",
        secret_ct=secrets.encrypt(LIVE_KEY, tenant_id=T, provider="openrouter"),
        secret_hint=secrets.hint(LIVE_KEY),
        field_map={},
        kind=llm.KIND,
    )
    await db_session.commit()

    await seed_activation(graph_session)
    await a_busy_ten_minutes(db_session)
    await InsightsConsumer().run_once()

    built = await insights(db_session)
    assert len(built) == 1
    insight = built[0]
    print(f"\n  basis={insight['basis']}\n  text: {insight['text']}")

    # The model wrote it.
    assert insight["basis"] == "openrouter"

    # And the digest still measured it. `a_busy_ten_minutes` seeds two visitors,
    # four zone entries, one surface touch, and 400s of dwell in the Pod against
    # Entry's 60 — the same numbers `test_insights.py` asserts for the
    # deterministic path, because the model's job is the sentence, not the sum.
    measured = insight["measurements"]
    assert measured["people"] == 2
    assert measured["zone_entries"] == 4
    assert measured["top_zone"] == "Pod"
    assert measured["top_zone_seconds"] == 400.0

    # Every citation still opens to the event it rests on.
    assert insight["refs"]
    for ref in insight["refs"]:
        row = await repository.get_event_by_seq(
            db_session, tenant_id=T, seq=ref["seq"]
        )
        assert row is not None
        assert str(row.event_id) == ref["event_id"]

    # The assertion this file exists for: no number in the prose that the window
    # did not measure. A model writing "roughly 15 visitors" over a digest of two
    # is the failure `roi-framework.md` rules out, and it is invisible to a mock
    # that returns a fixed string.
    #
    # The permitted set is derived from the digest rather than hand-listed. The
    # first version of this test listed the figures it expected and failed on a
    # correct sentence — the model said "the Entry zone for 60 seconds", which is
    # measured and was simply not in the list. A hand-written allow-list tests
    # the author's memory of the digest, not the model.
    def figures(value) -> set[str]:
        """Every number a truthful sentence could contain, in every form."""
        found: set[str] = set()
        if isinstance(value, dict):
            for inner in value.values():
                found |= figures(inner)
        elif isinstance(value, list):
            for inner in value:
                found |= figures(inner)
        elif isinstance(value, bool):
            pass
        elif isinstance(value, (int, float)):
            found.add(str(int(value)) if float(value).is_integer() else str(value))
            # Seconds are often written as minutes, and a decimal splits into
            # two runs of digits — "6.7 minutes" is 6 and 7.
            for derived in (value / 60, value * 60):
                if float(derived).is_integer():
                    found.add(str(int(derived)))
            found |= {part for part in re.findall(r"\d+", str(value))}
        return found

    allowed = figures(measured) | {"10"}  # the window's own length in minutes
    printed = set(re.findall(r"\d+", insight["text"]))
    assert printed <= allowed, (
        f"figures in the sentence that the window did not measure: "
        f"{sorted(printed - allowed)} — text was {insight['text']!r}"
    )


async def test_the_spend_lands_on_the_ledger(
    db_session: AsyncSession, graph_session: GraphSession, encrypted
) -> None:
    """A real call bills, and the cost tile has to see it."""
    await repository.upsert_integration(
        db_session,
        tenant_id=T,
        provider="openrouter",
        secret_ct=secrets.encrypt(LIVE_KEY, tenant_id=T, provider="openrouter"),
        secret_hint=secrets.hint(LIVE_KEY),
        field_map={},
        kind=llm.KIND,
    )
    await db_session.commit()

    await seed_activation(graph_session)
    await a_busy_ten_minutes(db_session)
    await InsightsConsumer().run_once()

    metered = [
        row.payload
        for row in await repository.read_events(
            db_session, tenant_id=T, session_id=S, type="cost.metered", limit=20
        )
        if row.payload.get("kind") == "llm_tokens"
    ]
    assert len(metered) == 1
    print(f"\n  metered: {metered[0]['amount']:.0f} tokens")
    assert metered[0]["amount"] > 0, "the vendor's own count, never an estimate"
    assert metered[0]["detail"]["spender"] == "insight"
    assert metered[0]["detail"]["provider"] == "openrouter"
