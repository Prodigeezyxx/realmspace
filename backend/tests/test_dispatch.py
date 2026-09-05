"""
The dispatcher, as executable claims.

The load-bearing test is `test_a_replayed_firing_posts_once`. ADR-002 §3 asks
for "per-dispatch idempotency keys on that `event_id` … so a retry after a
timeout cannot double-post", and that is the only property here that cannot be
had by being careful — Slack is not a database we can put an ON CONFLICT on.

Outbound HTTP is stubbed at `httpx.AsyncClient.post`. Not because the network is
slow, but because these tests are about what the dispatcher decides to send and
how many times, and a real endpoint would answer a question nobody is asking.
"""

from __future__ import annotations

import datetime as dt
import uuid

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import repository
from app.config import get_settings
from app.consumers.dispatch import DispatchConsumer
from app.schemas import EventIn

T = "t_test"
S = "s_dispatch"
BASE = dt.datetime(2026, 8, 11, 10, 0, 0, tzinfo=dt.timezone.utc)

SLACK_URL = "https://hooks.slack.invalid/services/T/B/xxx"


@pytest.fixture(autouse=True)
async def _scope_to_test_tenant(db_session: AsyncSession):
    from tests.conftest import as_tenant

    await as_tenant(db_session, T)
    yield


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    """Slack and the webhook signer, configured. Settings are lru_cached, so the
    cache is cleared on the way in and out rather than mutated in place."""
    settings = get_settings()
    monkeypatch.setattr(settings, "slack_webhook_url", SLACK_URL)
    monkeypatch.setattr(settings, "webhook_signing_secret", "s3cret")
    yield


class Posted:
    """Records every outbound POST, and answers however the test asked."""

    def __init__(self, status: int = 200) -> None:
        self.calls: list[dict] = []
        self.status = status

    async def __call__(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return httpx.Response(
            self.status, request=httpx.Request("POST", str(url)), text="ok"
        )


@pytest.fixture
def posted(monkeypatch) -> Posted:
    recorder = Posted()
    monkeypatch.setattr(httpx.AsyncClient, "post", recorder)
    return recorder


async def fire(
    db_session: AsyncSession,
    *,
    action: dict,
    rule_id: str = "r_entry_crowd",
    trigger_seq: int = 1,
    event_id: uuid.UUID | None = None,
    at: dt.datetime = BASE,
) -> uuid.UUID:
    """Append one `rule.fired`, as the evaluator emits it.

    Hand-written rather than driven through the evaluator on purpose: this file
    is about what happens *after* a rule fires, and coupling it to the
    evaluator's own correctness would make a dispatch test fail for a reason that
    has nothing to do with dispatch.
    """
    event_id = event_id or uuid.uuid4()
    await repository.append_event(
        db_session,
        EventIn(
            event_id=event_id,
            tenant_id=T,
            session_id=S,
            type="rule.fired",
            payload={
                "ruleId": rule_id,
                "ruleName": "Entrance crowding",
                "triggerType": "spatial.dwell",
                "triggerSeq": trigger_seq,
                "condition": {"type": "threshold", "count": 5, "windowSec": 30},
                "action": action,
                "matched": {"observed": 5, "countedBy": "people"},
            },
            occurred_at=at,
        ),
    )
    await db_session.commit()
    return event_id


async def events_of(db_session: AsyncSession, type: str) -> list:
    return await repository.read_events(
        db_session, tenant_id=T, session_id=S, type=type, limit=100
    )


# ── the four actions ──────────────────────────────────────────────────────────


async def test_slack_posts_the_message(db_session: AsyncSession, posted: Posted):
    """The Phase 3 acceptance criterion, end of the chain."""
    await fire(db_session, action={"type": "slack", "channel": "#ops", "message": "5 at entrance"})

    await DispatchConsumer().run_once()

    assert len(posted.calls) == 1
    body = posted.calls[0]["json"]
    assert body["channel"] == "#ops"
    assert body["text"] == "5 at entrance"


async def test_webhook_is_signed_over_the_bytes_that_are_sent(
    db_session: AsyncSession, posted: Posted
):
    """A signature computed over a re-serialised dict is the classic way a
    webhook signature "randomly" fails, so the test verifies the header against
    the exact `content` bytes rather than against a dict."""
    from app.actions.webhook import sign

    await fire(
        db_session,
        action={"type": "webhook", "url": "https://ops.invalid/hook", "payload": {"team": "floor"}},
    )

    await DispatchConsumer().run_once()

    call = posted.calls[0]
    assert call["headers"]["X-Realmspace-Signature"] == sign("s3cret", call["content"])
    # The operator's own payload rides along, and cannot overwrite the fields
    # the receiver needs to identify the firing.
    assert b'"team":"floor"' in call["content"]
    assert b'"ruleId":"r_entry_crowd"' in call["content"]


async def test_staff_prompt_lands_on_the_bus(db_session: AsyncSession):
    """Not pushed at the hub: a tablet that reconnects ten seconds later has to
    be able to catch up from its cursor, and it can only do that if the prompt
    is in the log."""
    await fire(
        db_session,
        action={"type": "staff_prompt", "message": "Greet the group at the entrance", "priority": "high"},
    )

    await DispatchConsumer().run_once()

    prompts = await events_of(db_session, "rule.staff_prompt")
    assert len(prompts) == 1
    assert prompts[0].payload["message"] == "Greet the group at the entrance"
    assert prompts[0].payload["priority"] == "high"
    assert prompts[0].payload["ruleId"] == "r_entry_crowd"


async def test_screen_swap_lands_on_the_bus(db_session: AsyncSession):
    await fire(
        db_session,
        action={"type": "screen_swap", "screenId": "scr_main", "contentId": "reel_b"},
    )

    await DispatchConsumer().run_once()

    swaps = await events_of(db_session, "rule.screen_swap")
    assert len(swaps) == 1
    assert swaps[0].payload["screenId"] == "scr_main"
    assert swaps[0].payload["contentId"] == "reel_b"


async def test_log_action_records_a_dispatch_and_sends_nothing(
    db_session: AsyncSession, posted: Posted
):
    fired_id = await fire(db_session, action={"type": "log", "message": "noted"})

    await DispatchConsumer().run_once()

    assert posted.calls == []
    row = await repository.get_dispatch(
        db_session, tenant_id=T, fired_event_id=fired_id, action_type="log"
    )
    assert row is not None and row.status == "delivered"


# ── ADR-002 §3: one dispatch per firing ───────────────────────────────────────


async def test_a_replayed_firing_posts_once(db_session: AsyncSession, posted: Posted):
    """**The test this file exists for.**

    A crash mid-batch redelivers the firing. The evaluator's derived id means
    the same `rule.fired` comes back rather than a new one, and the UNIQUE on
    (fired_event_id, action_type) is what turns the second delivery into a
    no-op. Without the claim, an operator's Slack channel gets one message per
    replay — and a replay is a routine operation on this bus, not an accident.
    """
    await fire(db_session, action={"type": "slack", "channel": "#ops", "message": "5 at entrance"})

    consumer = DispatchConsumer()
    await consumer.run_once()
    await repository.reset_cursor(
        db_session, consumer="dispatch", tenant_id=T, to_seq=0
    )
    await db_session.commit()
    await consumer.run_once()

    assert len(posted.calls) == 1


async def test_two_firings_of_one_rule_are_two_dispatches(
    db_session: AsyncSession, posted: Posted
):
    """The other half of the claim: idempotency must not collapse two genuine
    firings of the same rule into one. They differ by the event id the evaluator
    derived from their triggers."""
    await fire(db_session, action={"type": "slack", "channel": "#ops", "message": "a"}, trigger_seq=1)
    await fire(db_session, action={"type": "slack", "channel": "#ops", "message": "b"}, trigger_seq=9)

    await DispatchConsumer().run_once()

    assert len(posted.calls) == 2


# ── failure ───────────────────────────────────────────────────────────────────


async def test_a_failing_slack_post_lands_in_the_dead_letter_queue(
    db_session: AsyncSession, monkeypatch
):
    """Phase 3's second acceptance criterion — *a forced failure lands in the
    HITL queue* — for the dispatcher specifically. No handler in `app/actions/`
    catches its own exceptions, so retry, backoff and parking are the base
    consumer's, already built and already surfaced at `/ops`."""
    monkeypatch.setattr(httpx.AsyncClient, "post", Posted(status=500))
    fired_id = await fire(
        db_session, action={"type": "slack", "channel": "#ops", "message": "5 at entrance"}
    )

    await DispatchConsumer().run_once()

    parked = await repository.list_dead_letters(db_session, consumer="dispatch")
    assert len(parked) == 1
    assert "500" in parked[0].error

    row = await repository.get_dispatch(
        db_session, tenant_id=T, fired_event_id=fired_id, action_type="slack"
    )
    assert row is not None and row.status == "failed"


async def test_an_unconfigured_slack_parks_rather_than_reporting_success(
    db_session: AsyncSession, monkeypatch, posted: Posted
):
    """Nothing is more useless than a dispatcher that reports success for a
    message nobody received."""
    monkeypatch.setattr(get_settings(), "slack_webhook_url", None)
    await fire(db_session, action={"type": "slack", "channel": "#ops", "message": "x"})

    await DispatchConsumer().run_once()

    assert posted.calls == []
    parked = await repository.list_dead_letters(db_session, consumer="dispatch")
    assert len(parked) == 1
    assert "slack_webhook_url" in parked[0].error


async def test_an_unsigned_webhook_is_refused(
    db_session: AsyncSession, monkeypatch, posted: Posted
):
    """Falling back to sending unsigned would silently downgrade the only
    protection the receiver has, and the receiver cannot see this config."""
    monkeypatch.setattr(get_settings(), "webhook_signing_secret", None)
    await fire(
        db_session, action={"type": "webhook", "url": "https://ops.invalid/hook"}
    )

    await DispatchConsumer().run_once()

    assert posted.calls == []
    parked = await repository.list_dead_letters(db_session, consumer="dispatch")
    assert len(parked) == 1


async def test_an_unknown_action_type_parks(db_session: AsyncSession):
    """The API validates against a closed union, so this is a hand-written row
    or a rule that predates an action type being removed. Parking it puts the
    rule in front of an operator; a log line would leave it looking armed."""
    await fire(db_session, action={"type": "carrier_pigeon", "message": "x"})

    await DispatchConsumer().run_once()

    parked = await repository.list_dead_letters(db_session, consumer="dispatch")
    assert len(parked) == 1
    assert "carrier_pigeon" in parked[0].error


# ── cost telemetry ────────────────────────────────────────────────────────────


async def test_a_dispatch_meters_an_action(db_session: AsyncSession, posted: Posted):
    """The first thing on this track that spends anything, and therefore the
    first reading `/live`'s cost tile has ever had."""
    await fire(db_session, action={"type": "slack", "channel": "#ops", "message": "x"})

    await DispatchConsumer().run_once()

    costs = await events_of(db_session, "cost.metered")
    assert len(costs) == 1
    assert costs[0].payload["kind"] == "action_unit"
    # One dispatched action, in a unit that is not a currency — so the reader
    # shows it on its own line and never adds it to dollars.
    assert costs[0].payload["amount"] == 1.0
    assert costs[0].payload["unit"] == "actions"
    # Dated when the rule fired, not when the meter ran: a replayed cost must not
    # be attributed to the session that replayed it.
    assert costs[0].occurred_at == BASE


async def test_a_replayed_dispatch_does_not_inflate_the_bill(
    db_session: AsyncSession, posted: Posted
):
    """`cost.py`'s whole reason for taking a *cause* rather than an id: a
    duplicated cost survives every replay, and it lands in the direction that
    overstates what a client's activation cost."""
    await fire(db_session, action={"type": "slack", "channel": "#ops", "message": "x"})

    consumer = DispatchConsumer()
    await consumer.run_once()
    await repository.reset_cursor(
        db_session, consumer="dispatch", tenant_id=T, to_seq=0
    )
    await db_session.commit()
    await consumer.run_once()

    assert len(await events_of(db_session, "cost.metered")) == 1


async def test_a_prompt_is_not_metered(db_session: AsyncSession):
    """`staff_prompt`, `screen_swap` and `log` go out over a socket this process
    is already running. Metering them at zero would put three lines reading "0"
    on an operator's cost tile."""
    await fire(db_session, action={"type": "staff_prompt", "message": "greet them"})

    await DispatchConsumer().run_once()

    assert await events_of(db_session, "cost.metered") == []
