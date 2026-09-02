"""
Plain English → a rule document, and everything the model is not allowed to
decide.

ADR-002's first argument for rules being data is that *"an operator writes rules,
not an engineer. The composer UI is plain-English → spec → operator confirms."*
`PRD.md` §6.5 names the model half. This is the translation step; the confirming
is the browser's and the arming is `PUT /v1/rules`, which is the same request the
form makes for a hand-written rule — one write path, one place a role and a plan
limit are enforced.

The properties, in the order they matter:

**It composes and does not arm.** `consumers/sdr.py`'s rule for a draft. A
composer that saved would be a model arming an action in a room.

**Three things are taken from the model structurally, not checked afterwards.**
The tenant and the id are never read from its output, and the action union its
document is validated against (`schemas.ComposableAction`) does not contain a
destination outside the room — so a Slack channel or a webhook URL invented by a
model is unrepresentable rather than caught. `docs/adr/003-nl-query-catalogue.md`
makes the same move for `tenant_id` in a query.

**A zone it did not get from us is a refusal.** Never the nearest match: a rule
armed on the wrong zone is armed, plausible and silent.

**The floor refuses rather than reaching**, and says which kind of thing
answered. `basis` is the whole of an operator's ability to tell a model's draft
from a keyword match.
"""

from __future__ import annotations

import json

import pytest
from httpx import AsyncClient
from neo4j import AsyncSession as GraphSession
from sqlalchemy.ext.asyncio import AsyncSession

from app import repository
from app.llm import rule_shapes
from app.schemas import RuleIn
from tests.test_ask import _client, _connect_openrouter
from tests.test_handoff import S, T, seed_activation

pytestmark = pytest.mark.usefixtures("encryption_key")


@pytest.fixture(autouse=True)
async def _scope_to_test_tenant(db_session: AsyncSession):
    from tests.conftest import as_tenant

    await as_tenant(db_session, T)
    yield


async def compose(client: AsyncClient, instruction: str) -> dict:
    res = await client.post(
        "/v1/rules/compose", json={"instruction": instruction, "sessionId": S}
    )
    assert res.status_code == 200, res.text
    return res.json()


def model_replies(text: str):
    """A transport that answers every completion with `text`."""
    from tests.llm_transport import completion

    def handler(request):
        import httpx

        return httpx.Response(200, json=completion(text))

    return handler


# ── every shape, derived ──────────────────────────────────────────────────────


@pytest.mark.parametrize("shape", rule_shapes.SHAPES, ids=lambda s: s.name)
@pytest.mark.parametrize("zone", [None, {"id": "z_entry", "name": "Entry Arch"}])
def test_every_shape_builds_something_the_api_would_accept(shape, zone) -> None:
    """Derived over `SHAPES`, so a shape added later cannot skip this.

    Found by walking: the floor returned a document every other test agreed
    with, and `PUT /v1/rules` refused it — `name: Field required`. The shapes had
    never named what they built, and no test had put a *whole* composed document
    through the validator that actually guards the write. The one test that did
    used the model path, where the model supplies the name.
    """
    document = shape.build("when 5 people dwell for 30 seconds", zone)

    RuleIn.model_validate({**document, "ruleId": "r_x_000000", "enabled": True})

    # And it names the zone the way the operator does, not the way we do.
    if zone:
        assert "z_entry" not in document["name"]


# ── the deterministic floor ───────────────────────────────────────────────────


async def test_the_floor_builds_a_crowding_rule_and_says_it_was_the_floor(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """No provider configured is the ordinary case on a laptop, and it has to
    produce something an operator can actually arm rather than a placeholder —
    `deterministic_draft`'s rule, applied to a document."""
    await seed_activation(graph_session)
    client = await _client(db_session, role="analyst")

    out = await compose(client, "tell staff when the Entry is at capacity")

    assert out["basis"] == "deterministic"
    rule = out["rule"]
    assert rule["triggerType"] == "spatial.occupancy"
    assert rule["triggerZoneId"] == "z_entry"
    # Both ends of a crossing are one type; without this the prompt is raised
    # again as the zone empties.
    assert rule["condition"]["payloadEquals"] == {"status": "over"}
    assert rule["action"]["type"] == "staff_prompt"


async def test_the_floor_refuses_rather_than_reaching_for_the_nearest_shape(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """`stub.py::route`'s rule, and a rule is the stronger case for it: a
    mis-picked measurement shows a wrong number, a mis-picked rule tells the
    floor staff to do something."""
    await seed_activation(graph_session)
    client = await _client(db_session, role="analyst")

    out = await compose(client, "what is the airspeed of an unladen swallow")

    assert out["rule"] is None
    assert out["reason"]
    # A refusal with no alternative is a dead end — `catalogue.menu()`'s reason.
    assert [s["shape"] for s in out["canBuild"]] == [
        s["shape"] for s in rule_shapes.menu()
    ]


async def test_the_floor_reads_the_duration_and_the_count_apart(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """"5 people for 30 seconds" and "30 seconds, 5 people" are one rule. Read by
    position the second is five-second dwells by thirty people."""
    await seed_activation(graph_session)
    client = await _client(db_session, role="analyst")

    for phrasing in (
        "when 5 people dwell at the Entry for 30 seconds prompt staff",
        "prompt staff after 30 seconds of dwell by 5 people at the Entry",
    ):
        rule = (await compose(client, phrasing))["rule"]
        assert rule["condition"]["count"] == 5, phrasing
        assert rule["condition"]["minDwellSec"] == 30, phrasing


# ── with a model ──────────────────────────────────────────────────────────────


async def test_a_model_composes_and_the_document_is_one_the_api_accepts(
    db_session: AsyncSession, graph_session: GraphSession, monkeypatch
) -> None:
    """The document is validated by the same members `PUT /v1/rules` enforces,
    so "the model composed it" and "an operator could have written it" are the
    same claim."""
    from tests.llm_transport import stub_transport

    await seed_activation(graph_session)
    await _connect_openrouter(db_session)
    stub_transport(
        monkeypatch,
        model_replies(
            json.dumps(
                {
                    "rule": {
                        "name": "Entry crowding",
                        "triggerType": "spatial.dwell",
                        "triggerZoneId": "z_entry",
                        "condition": {
                            "type": "threshold",
                            "count": 5,
                            "windowSec": 60,
                            "zoneId": "z_entry",
                            "minDwellSec": 30,
                        },
                        "action": {
                            "type": "staff_prompt",
                            "message": "Five people are waiting at the entrance",
                            "zoneId": "z_entry",
                        },
                        "cooldownSec": 60,
                    }
                }
            )
        ),
    )
    client = await _client(db_session, role="analyst")

    out = await compose(client, "tell the floor when five people wait at the entrance")

    assert out["basis"] == "openrouter"
    # The API's own validator, on the composer's output. If this raises, the
    # composer built something the operator could not have saved.
    RuleIn.model_validate(out["rule"])


async def test_composing_saves_nothing(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """The whole point of a confirm step. `consumers/sdr.py` drafts and does not
    send for the same reason: what leaves the building is a human's decision."""
    await seed_activation(graph_session)
    client = await _client(db_session, role="analyst")

    out = await compose(client, "tell staff when the Entry is at capacity")
    assert out["rule"] is not None

    assert (await client.get("/v1/rules")).json() == []


async def test_a_zone_the_activation_does_not_have_is_refused(
    db_session: AsyncSession, graph_session: GraphSession, monkeypatch
) -> None:
    """Never corrected to the nearest zone. A rule armed on a zone the operator
    did not mean is armed, plausible and silent."""
    from tests.llm_transport import stub_transport

    await seed_activation(graph_session)
    await _connect_openrouter(db_session)
    stub_transport(
        monkeypatch,
        model_replies(
            json.dumps(
                {
                    "rule": {
                        "name": "Lounge crowding",
                        "triggerType": "spatial.occupancy",
                        "triggerZoneId": "z_lounge",
                        "condition": {"type": "any"},
                        "action": {"type": "log", "message": "busy"},
                        "cooldownSec": 60,
                    }
                }
            )
        ),
    )
    client = await _client(db_session, role="analyst")

    out = await compose(client, "when the lounge fills up")

    assert out["rule"] is None
    assert "z_lounge" in out["reason"]
    assert "z_entry" in out["reason"]


async def test_a_model_cannot_choose_a_slack_channel_or_a_webhook(
    db_session: AsyncSession, graph_session: GraphSession, monkeypatch
) -> None:
    """The structural half. `ComposableAction` has no Slack, webhook or screen
    member, so a channel is not something a composed document can carry.

    The condition survives, because it is the part the operator described and
    the hard part to get right; the destination was always going to be theirs to
    enter. What must not survive is the address the model proposed — naming it
    back would put somewhere nobody chose in front of an operator as though it
    were real."""
    from tests.llm_transport import stub_transport

    await seed_activation(graph_session)
    await _connect_openrouter(db_session)
    stub_transport(
        monkeypatch,
        model_replies(
            json.dumps(
                {
                    "rule": {
                        "name": "Ping ops",
                        "triggerType": "spatial.dwell",
                        "triggerZoneId": "z_entry",
                        "condition": {
                            "type": "threshold",
                            "count": 5,
                            "windowSec": 60,
                            "zoneId": "z_entry",
                            "minDwellSec": 30,
                        },
                        "action": {
                            "type": "slack",
                            "channel": "#ops",
                            "message": "busy",
                        },
                        "cooldownSec": 60,
                    }
                }
            )
        ),
    )
    client = await _client(db_session, role="analyst")

    out = await compose(client, "post to ops when the entrance is busy")

    # The condition is kept…
    assert out["rule"]["condition"]["count"] == 5
    # …the action is not, and no address the model invented is anywhere in it.
    assert out["rule"]["action"]["type"] == "log"
    assert "#ops" not in json.dumps(out["rule"])
    assert "#ops" not in json.dumps(out["warnings"])
    assert any("Slack" in w for w in out["warnings"])
    # And the marker the router uses to carry that between two functions is not
    # part of the document an operator is about to arm.
    assert "_outward" not in out["rule"]


async def test_two_composes_are_two_ids(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """A composed rule that reused an id would replace the first on the first
    press of Arm. The id is minted here and never taken from the model."""
    await seed_activation(graph_session)
    client = await _client(db_session, role="analyst")

    first = await compose(client, "tell staff when the Entry is at capacity")
    second = await compose(client, "tell staff when the Entry is at capacity")

    assert first["rule"]["ruleId"] != second["rule"]["ruleId"]
    assert first["rule"]["ruleId"].startswith("r_")


async def test_a_trigger_nothing_produces_is_composed_with_a_warning(
    db_session: AsyncSession, graph_session: GraphSession, monkeypatch
) -> None:
    """Not refused: `event-bus-spec.md` §3 registers six types ahead of their
    producers precisely so a rule can name one. Not silent either — a rule that
    is armed, correct and can never fire is the failure this codebase keeps
    finding."""
    from tests.llm_transport import stub_transport

    await seed_activation(graph_session)
    await _connect_openrouter(db_session)
    stub_transport(
        monkeypatch,
        model_replies(
            json.dumps(
                {
                    "rule": {
                        "name": "Hot lead",
                        "triggerType": "intent.scored",
                        "triggerZoneId": None,
                        "condition": {"type": "any"},
                        "action": {"type": "log", "message": "hot"},
                        "cooldownSec": 60,
                    }
                }
            )
        ),
    )
    client = await _client(db_session, role="analyst")

    out = await compose(client, "log every scored intent")

    assert out["rule"]["triggerType"] == "intent.scored"
    assert any("intent.scored" in w for w in out["warnings"])


async def test_a_reply_that_is_not_json_is_a_refusal_not_a_500(
    db_session: AsyncSession, graph_session: GraphSession, monkeypatch
) -> None:
    """The shape of the two bugs the SDR walk found: a model behaving
    reasonably, a parser accepting one form. A refusal an operator can act on,
    and the request succeeded."""
    from tests.llm_transport import stub_transport

    await seed_activation(graph_session)
    await _connect_openrouter(db_session)
    stub_transport(monkeypatch, model_replies("Sure! Here is a rule for you."))
    client = await _client(db_session, role="analyst")

    out = await compose(client, "tell staff when the Entry is at capacity")

    assert out["rule"] is None
    assert "not JSON" in out["reason"]


async def test_a_fenced_reply_is_read(
    db_session: AsyncSession, graph_session: GraphSession, monkeypatch
) -> None:
    """Gemini fences its JSON every time and DeepSeek does not; both are asked
    for JSON and neither is wrong. `llm.base.unfenced` is the one place that
    knows, shared with `/ask` rather than copied."""
    from tests.llm_transport import stub_transport

    await seed_activation(graph_session)
    await _connect_openrouter(db_session)
    document = {
        "rule": {
            "name": "Quiet pod",
            "triggerType": "spatial.zone_enter",
            "triggerZoneId": "z_pod",
            "condition": {"type": "none", "windowSec": 600, "zoneId": "z_pod"},
            "action": {"type": "log", "message": "quiet"},
            "cooldownSec": 300,
        }
    }
    stub_transport(
        monkeypatch, model_replies("```json\n" + json.dumps(document) + "\n```")
    )
    client = await _client(db_session, role="analyst")

    out = await compose(client, "tell me when nobody comes to the pod for ten minutes")

    assert out["rule"]["condition"]["type"] == "none"


async def test_the_model_call_is_metered(
    db_session: AsyncSession, graph_session: GraphSession, monkeypatch
) -> None:
    """The SDR spent tokens for a phase and metered none of them, because with
    no provider the absence looked like nothing to record. Not again."""
    from tests.llm_transport import stub_transport

    await seed_activation(graph_session)
    await _connect_openrouter(db_session)
    stub_transport(monkeypatch, model_replies('{"rule": null, "reason": "no"}'))
    client = await _client(db_session, role="analyst")

    await compose(client, "something the model declines")

    metered = await repository.read_events(
        db_session, tenant_id=T, session_id=S, type="cost.metered", limit=10
    )
    assert [e.payload["detail"]["spender"] for e in metered] == ["compose"]
    assert metered[0].payload["unit"] == "tokens"


# ── who may ───────────────────────────────────────────────────────────────────


async def test_a_role_that_cannot_author_rules_cannot_compose_one(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """Handing a document to somebody who cannot save it is a dead end, and this
    spends tokens. Same gate as the PUT."""
    await seed_activation(graph_session)
    client = await _client(db_session, role="viewer")

    res = await client.post(
        "/v1/rules/compose", json={"instruction": "anything", "sessionId": S}
    )

    assert res.status_code == 403
