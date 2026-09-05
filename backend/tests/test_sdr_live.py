"""
The contextual SDR, against a real model. Skipped unless asked for.

    OPENROUTER_LIVE_KEY=sk-or-v1-… .venv/bin/python -m pytest tests/test_sdr_live.py -q -s

The last Phase 5 bullet still 🟡, and the reason `roadmap.md` gives is that
nobody had watched a real model draft a real follow-up from a real handoff.
Sharing a code path with `/ask` is not the same as having walked one — that rule
has now paid twice, turning up two bugs in `/ask` and a missing meter in this
consumer.

## Why this one is not the insight walk again

Ask returns JSON and the insight returns a sentence about anonymous
measurements. This returns **prose addressed to a named person**, and the
assertions are about what the prompt already forbids: a figure nobody measured,
a zone they never stood in, a conversation that never happened.

`consumers/sdr._split` is the specific thing to watch. It requires the reply's
first line to start with `Subject:`; anything else returns `None`, the composed
draft quietly stands, and `basis` stays `deterministic`. That is the same shape
as the Gemini JSON-fence bug — a model behaving reasonably, a parser that accepts
one form, and a failure that reads as the feature simply not working. Which is
why `basis == "openrouter"` is the load-bearing assertion here: it is the only
thing that distinguishes "a model wrote this" from "the fallback did, and
nothing said so".
"""

from __future__ import annotations

import os
import re

import pytest
from neo4j import AsyncSession as GraphSession
from sqlalchemy.ext.asyncio import AsyncSession

from app import llm, repository, secrets
from app.llm import prompts
from app.config import get_settings
from app.graph import repository as graph_repo
from tests.test_handoff import ENTRY_POLY, S, T
from tests.test_sdr import a_consented_visitor, drafts, run_chain

LIVE_KEY = os.environ.get("OPENROUTER_LIVE_KEY", "")

pytestmark = pytest.mark.skipif(
    not LIVE_KEY,
    reason="set OPENROUTER_LIVE_KEY to run the live SDR walk",
)

#: Phrases `prompts.SDR` forbids by name — "never invent a conversation, a
#: product interest, a promise, a discount, or anything somebody said. We
#: measured where they walked. We did not hear them."
INVENTED = (
    "as we discussed",
    "as discussed",
    "you mentioned",
    "as promised",
    "you said",
    "our conversation",
    "discount",
    "% off",
    "free trial",
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


async def a_zone_they_never_entered(graph_session: GraphSession) -> str:
    """A zone on the activation that this visitor did not stand in.

    `seed_person_with_a_path` dwells in Entry and Pod, so with only those two
    zones "the draft names no zone they did not visit" is unfalsifiable — there
    is no wrong answer available. This gives it one.
    """
    await graph_repo.upsert_zone(
        graph_session,
        tenant_id=T,
        session_id=S,
        zone_id="z_vip",
        name="VIP Lounge",
        type="feature",
        polygon=ENTRY_POLY,
        weight=1.0,
        funnel_order=3,
    )
    return "VIP Lounge"


async def test_a_real_model_drafts_the_follow_up(
    db_session: AsyncSession, graph_session: GraphSession, connected
) -> None:
    await a_consented_visitor(db_session, graph_session)
    never_entered = await a_zone_they_never_entered(graph_session)
    await run_chain()

    built = await drafts(db_session)
    assert len(built) == 1
    draft = built[0]
    print(f"\n  basis={draft['basis']}\n  subject: {draft['subject']}\n\n{draft['body']}\n")

    # The load-bearing one: `_split` accepted the reply. Without this the
    # composed draft stands in silently and every assertion below still passes.
    assert draft["basis"] == "openrouter"

    subject, body = draft["subject"], draft["body"]
    assert subject.strip() and body.strip()
    assert subject != body
    assert "\n" not in subject, "a subject line is one line"

    text = f"{subject}\n{body}"
    lowered = text.lower()

    # What the prompt says it may reference, and what it says it may not.
    grounded = draft["grounded_in"]
    assert grounded["top_dwell_zone"] == "Pod"
    for zone in grounded["zones_visited"]:
        pass  # named for the reader; the assertion that bites is the next one
    assert never_entered.lower() not in lowered, (
        f"the draft named {never_entered!r}, a zone on the activation this "
        "visitor never stood in — the prompt lists only the zones they entered"
    )

    for phrase in INVENTED:
        assert phrase not in lowered, (
            f"the draft says {phrase!r}. We measured where they walked; we did "
            "not hear them, and `prompts.SDR` forbids this by name"
        )

    # No figure the model was not given. Derived from **what the prompt was
    # actually handed** — `prompts.sdr_prompt` passes the contact, the activation
    # name, the zones, the dwells — rather than from a list written here.
    #
    # Twice now a hand-scoped version of this check has failed a correct draft:
    # the insight walk forgot a zone's own seconds, and the first run of this one
    # flagged "7", which is the 7 in "Pavilion No.7". The activation's name is in
    # the prompt, so a 7 in the draft is quotation, not invention. Scoping the
    # check to what the model was given is the only version that tests the model
    # rather than the author.
    handoff = [
        row.payload
        for row in await repository.read_events(
            db_session, tenant_id=T, session_id=S, type="handoff.lead", limit=10
        )
    ][-1]
    given = " ".join(
        str(part)
        for part in (
            (handoff.get("activation") or {}).get("name"),
            (handoff.get("contact") or {}).get("name"),
            (handoff.get("contact") or {}).get("company"),
            *(grounded["zones_visited"] or []),
            *(grounded["surfaces_engaged"] or []),
            grounded["top_dwell_zone"],
        )
        if part
    )
    allowed = set(re.findall(r"\d+", given))
    for value in (grounded["dwell_seconds_total"], 40.0, 120.0):
        if value is None:
            continue
        allowed.add(str(int(value)) if float(value).is_integer() else str(value))
        # A dwell is often written as minutes, and a decimal splits in two.
        for derived in (value / 60, value * 60):
            if float(derived).is_integer():
                allowed.add(str(int(derived)))
        allowed |= set(re.findall(r"\d+", str(value / 60)))

    printed = set(re.findall(r"\d+", text))
    assert printed <= allowed, (
        f"figures in the draft the model was never given: "
        f"{sorted(printed - allowed)} — draft was {text!r}"
    )

    # A tracking id in an email to a customer.
    assert "p-012" not in lowered, "the anon_id reached a customer-facing draft"

    # **Open decision 5, and the half only a real model can answer.** The prompt
    # carries `[FIRST_NAME]`/`[COMPANY]` and `splice_identity` puts the person
    # back afterwards, so what OpenRouter is shown is where somebody walked and
    # never who they are. A stub copies a bracket token through by construction;
    # whether a model that has been asked to write warm prose does is exactly
    # the sort of thing this walk exists to find — and if it does not, `basis`
    # above is already `deterministic` and this test has failed one line up.
    prompt = prompts.sdr_prompt(
        contact=(handoff.get("contact") or {}),
        intent=(handoff.get("spatial_intent") or {}),
        activation=(handoff.get("activation") or {}).get("name") or "",
    )
    for private in ("Sam", "Rivera"):
        assert private not in prompt, (
            f"{private!r} was sent to the model vendor — `privacy.md` says a "
            "reasoning call receives structured event summaries, and a name is "
            "not one"
        )
    assert prompts.NAME_TOKEN in prompt

    # And the reviewer still gets a letter addressed to a person.
    assert "Sam" in text, "the draft names nobody; the splice did not happen"

    # The prompt asks for under 120 words, and a longer one is an email a human
    # has to cut before sending.
    assert len(body.split()) < 120, f"{len(body.split())} words"

    # It is a draft, and nothing here sends anything.
    assert draft["sent"] is False


async def test_the_draft_is_metered_against_a_real_bill(
    db_session: AsyncSession, graph_session: GraphSession, connected
) -> None:
    """The meter this consumer did not have until today, against real counts."""
    await a_consented_visitor(db_session, graph_session)
    await run_chain()

    metered = [
        row.payload
        for row in await repository.read_events(
            db_session, tenant_id=T, session_id=S, type="cost.metered", limit=20
        )
        if row.payload.get("kind") == "llm_tokens"
    ]
    assert len(metered) == 1, "one draft, one spend"
    print(f"\n  metered: {metered[0]['amount']:.0f} tokens")
    assert metered[0]["amount"] > 0, "the vendor's own count, never an estimate"
    assert metered[0]["detail"]["spender"] == "sdr"
    assert metered[0]["detail"]["provider"] == "openrouter"
