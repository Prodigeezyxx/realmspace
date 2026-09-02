"""
The rule composer, against a real model. Skipped unless asked for.

    OPENROUTER_LIVE_KEY=sk-or-v1-… .venv/bin/python -m pytest tests/test_compose_live.py -q -s

`test_sdr_live.py` records why this file exists at all: *"sharing a code path is
not the same as having walked one"* — that rule has now found two bugs in
`/ask`, a missing meter in the SDR, a reasoning ceiling tuned for the wrong
prompt, and a subject line rejected for being bold. Every one of them was a model
doing something reasonable that the code accepted exactly one form of.

## What is specific to this walk

`/ask` asks a model to pick a name from a list. The SDR asks it for prose. This
asks it for a **document with a nested discriminated union in it** — the largest
structured output anything here requests — and the ways that goes wrong are its
own: a member spelled differently, a field on the wrong level, an id invented for
a zone, a Slack channel proposed because the instruction asked for one.

`basis == "openrouter"` is the load-bearing assertion in every test here, as it
is in the SDR walk. The deterministic floor composes a perfectly good rule for
most of these instructions, so without that line a completely broken model path
passes this file.
"""

from __future__ import annotations

import json
import os

import pytest
from neo4j import AsyncSession as GraphSession
from sqlalchemy.ext.asyncio import AsyncSession

from app import llm, repository, secrets
from app.config import get_settings
from app.schemas import RuleIn
from tests.test_ask import _client
from tests.test_handoff import S, T, seed_activation

LIVE_KEY = os.environ.get("OPENROUTER_LIVE_KEY", "")

pytestmark = pytest.mark.skipif(
    not LIVE_KEY,
    reason="set OPENROUTER_LIVE_KEY to run the live composer walk",
)


@pytest.fixture(autouse=True)
async def _scope_to_test_tenant(db_session: AsyncSession):
    from tests.conftest import as_tenant

    await as_tenant(db_session, T)
    yield


@pytest.fixture
async def connected(db_session: AsyncSession):
    """The live key, stored the way `python -m app.llm.connect` stores one."""
    settings = get_settings()
    before = settings.credential_encryption_key
    settings.credential_encryption_key = secrets.generate_key()

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
    yield
    settings.credential_encryption_key = before


async def compose(client, instruction: str) -> dict:
    res = await client.post(
        "/v1/rules/compose", json={"instruction": instruction, "sessionId": S}
    )
    assert res.status_code == 200, res.text
    out = res.json()
    print(f"\n--- {instruction}\n{json.dumps(out, indent=2)}")
    return out


async def test_a_real_model_composes_rules_the_api_would_accept(
    db_session: AsyncSession, graph_session: GraphSession, connected
) -> None:
    """Three instructions of different shapes, because one is a coincidence.

    A counting rule, a silence, and a status crossing — the three condition
    types — so a model that only ever produces `any` fails here rather than
    looking like it works.
    """
    await seed_activation(graph_session)  # zones: z_entry "Entry", z_pod "Pod"
    client = await _client(db_session, role="analyst")

    instructions = (
        "when five people dwell at the Entry for thirty seconds, tell staff to open a second door",
        "tell me when nobody has come to the Pod for ten minutes",
        "prompt the floor when the Entry reaches the capacity we set",
    )

    for instruction in instructions:
        out = await compose(client, instruction)

        # The whole walk turns on this. Without it the deterministic floor's
        # perfectly good rule passes every assertion below.
        assert out["basis"] == "openrouter", out
        assert out["rule"] is not None, out["reason"]

        # The API's own validator, on the model's document. If this raises, the
        # composer produced something an operator could not have saved by hand.
        RuleIn.model_validate(out["rule"])

        # Only zones this activation has. A model that invents an id is refused
        # rather than corrected, so reaching here means it used ours.
        for zone in (
            out["rule"]["triggerZoneId"],
            out["rule"]["condition"].get("zoneId"),
            out["rule"]["action"].get("zoneId"),
        ):
            assert zone in (None, "z_entry", "z_pod"), out["rule"]

        # And it stays inside the room, whatever it was asked for.
        assert out["rule"]["action"]["type"] in ("staff_prompt", "log"), out["rule"]


async def test_a_real_model_asked_for_slack_does_not_get_to_choose_a_channel(
    db_session: AsyncSession, graph_session: GraphSession, connected
) -> None:
    """The instruction names a destination, which is the case the narrowing
    exists for. What must not come back is an address nobody chose."""
    await seed_activation(graph_session)
    client = await _client(db_session, role="analyst")

    out = await compose(
        client, "post to the #floor-ops Slack channel when the Entry gets busy"
    )

    assert out["basis"] == "openrouter", out
    assert out["rule"] is not None, out["reason"]
    assert out["rule"]["action"]["type"] in ("staff_prompt", "log")
    assert "#floor-ops" not in json.dumps(out["rule"])
    assert any("Slack" in w for w in out["warnings"]), out["warnings"]


async def test_a_real_model_declines_something_that_is_not_a_rule(
    db_session: AsyncSession, graph_session: GraphSession, connected
) -> None:
    """The refusal half, which is the one a model is worst at.

    Asked for a report it cannot build, the wrong answer is a plausible rule —
    the same failure `stub.py` refuses to make by reaching for the nearest
    catalogue entry.
    """
    await seed_activation(graph_session)
    client = await _client(db_session, role="analyst")

    out = await compose(
        client, "email me a PDF summary of last week's activations every Monday"
    )

    assert out["basis"] == "openrouter", out
    assert out["rule"] is None, out["rule"]
    assert out["reason"].strip()
    assert out["canBuild"], "a refusal with no alternative is a dead end"


async def test_the_live_call_is_metered(
    db_session: AsyncSession, graph_session: GraphSession, connected
) -> None:
    """The vendor's own token count, on the meter — the absence the SDR walk
    found in a consumer that had been spending since it was written."""
    await seed_activation(graph_session)
    client = await _client(db_session, role="analyst")

    await compose(client, "tell staff when the Entry reaches capacity")

    metered = await repository.read_events(
        db_session, tenant_id=T, session_id=S, type="cost.metered", limit=10
    )
    assert metered, "a model call that spent nothing was not a model call"
    assert metered[0].payload["detail"]["spender"] == "compose"
    assert metered[0].payload["amount"] > 0
