"""
The rules evaluator, as executable claims.

Structured around ADR-002's "four things the spec pins that the shipped
evaluator does differently", because each of those is a specific way a rule
engine that works becomes a rule engine that cannot be replayed. In order:

  §1  test_window_survives_a_restart          — the window is read, not held
  §2  test_cooldown_is_on_event_time          — and its replay twin
  §3  test_replay_produces_one_firing         — derived event ids
  §4  test_none_fires_when_the_quiet_closes   — and fires exactly once

`test_window_survives_a_restart` is the load-bearing one. Every other test here
would pass against an in-memory sliding window; that one is the difference.

Rules are stored through the repository rather than the API so the evaluator is
under test on its own — the router has its own tests in test_rules_api.py.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import repository
from app.consumers.rules import RulesConsumer
from app.schemas import EventIn

T = "t_test"
S = "s_rules"
BASE = dt.datetime(2026, 8, 11, 10, 0, 0, tzinfo=dt.timezone.utc)

DWELL = "spatial.dwell"
FIRED = "rule.fired"


@pytest.fixture(autouse=True)
async def _scope_to_test_tenant(db_session: AsyncSession):
    from tests.conftest import as_tenant

    await as_tenant(db_session, T)
    yield


async def make_rule(
    db_session: AsyncSession,
    *,
    rule_id: str = "r_entry_crowd",
    trigger_type: str = DWELL,
    trigger_zone_id: str | None = None,
    condition: dict | None = None,
    action: dict | None = None,
    cooldown_sec: int = 60,
    enabled: bool = True,
) -> None:
    """Store a rule document. Defaults to ADR-002's own example: five people
    dwelling at the entrance for 30 seconds inside a 30-second window."""
    await repository.upsert_rule(
        db_session,
        tenant_id=T,
        rule_id=rule_id,
        name="Entrance crowding",
        trigger_type=trigger_type,
        trigger_zone_id=trigger_zone_id,
        condition=condition
        or {
            "type": "threshold",
            "count": 5,
            "windowSec": 30,
            "zoneId": "z_entry",
            "minDwellSec": 30,
        },
        action=action or {"type": "log", "message": "5 at entrance"},
        enabled=enabled,
        cooldown_sec=cooldown_sec,
    )
    await db_session.commit()


async def dwell(
    db_session: AsyncSession,
    *,
    at: dt.datetime,
    anon_id: str,
    zone_id: str = "z_entry",
    duration_sec: float = 45.0,
) -> None:
    """One spatial.dwell, as the tracker emits it.

    **snake_case, because that is what is actually in the log.** Payloads are
    stored as the producer wrote them and `event-bus-spec.md` §3 pins this one as
    `{ anon_id, zone_id, duration, … }`. The first version of this file used the
    rule document's camelCase instead, which meant every test here passed against
    events no producer will ever emit — and the evaluator's real bug, that it
    matched none of the tracker's output, survived a green suite.
    """
    await repository.append_event(
        db_session,
        EventIn(
            event_id=uuid.uuid4(),
            tenant_id=T,
            session_id=S,
            type=DWELL,
            payload={
                "anon_id": anon_id,
                "zone_id": zone_id,
                "duration": duration_sec,
            },
            occurred_at=at,
        ),
    )
    await db_session.commit()


async def crowd(
    db_session: AsyncSession,
    *,
    people: int = 5,
    start: dt.datetime = BASE,
    zone_id: str = "z_entry",
    duration_sec: float = 45.0,
) -> dt.datetime:
    """`people` different visitors dwelling, one second apart. Returns the time
    of the last one, which is the event the rule is evaluated on."""
    at = start
    for i in range(people):
        at = start + dt.timedelta(seconds=i)
        await dwell(
            db_session,
            at=at,
            anon_id=f"P-{i:03d}",
            zone_id=zone_id,
            duration_sec=duration_sec,
        )
    return at


async def firings(db_session: AsyncSession, rule_id: str | None = None) -> list:
    rows = await repository.read_events(
        db_session, tenant_id=T, session_id=S, type=FIRED, limit=100
    )
    if rule_id is not None:
        rows = [r for r in rows if r.payload["ruleId"] == rule_id]
    return rows


# ── the basics ────────────────────────────────────────────────────────────────


async def test_threshold_fires_when_the_count_is_reached(db_session: AsyncSession):
    """The Phase 3 acceptance criterion, minus the Slack post: five people
    dwelling at the entrance inside the window fires the rule once."""
    await make_rule(db_session)
    await crowd(db_session)

    await RulesConsumer().run_once()

    rows = await firings(db_session)
    assert len(rows) == 1
    assert rows[0].payload["ruleId"] == "r_entry_crowd"
    assert rows[0].payload["matched"]["observed"] == 5
    # The action travels with the firing so the dispatcher acts on the document
    # as it was, not as it is now.
    assert rows[0].payload["action"] == {"type": "log", "message": "5 at entrance"}


async def test_four_people_do_not_fire_a_five_person_rule(db_session: AsyncSession):
    await make_rule(db_session)
    await crowd(db_session, people=4)

    await RulesConsumer().run_once()

    assert await firings(db_session) == []


async def test_one_restless_visitor_is_not_five_people(db_session: AsyncSession):
    """Counting events rather than people is the obvious implementation and the
    wrong one: `spatial.dwell` is emitted per stay, so one visitor leaving and
    returning five times would fire a five-person rule on their own."""
    await make_rule(db_session)
    for i in range(6):
        await dwell(db_session, at=BASE + dt.timedelta(seconds=i), anon_id="P-001")

    await RulesConsumer().run_once()

    assert await firings(db_session) == []


async def test_a_short_dwell_does_not_count_towards_a_long_one(
    db_session: AsyncSession,
):
    """`minDwellSec` is what separates "5 people at the entrance for 30 seconds"
    from five people who walked past it."""
    await make_rule(db_session)
    await crowd(db_session, duration_sec=2.0)

    await RulesConsumer().run_once()

    assert await firings(db_session) == []


async def test_another_zone_does_not_fire_the_rule(db_session: AsyncSession):
    await make_rule(db_session)
    await crowd(db_session, zone_id="z_lounge")

    await RulesConsumer().run_once()

    assert await firings(db_session) == []


async def test_a_disabled_rule_does_not_fire(db_session: AsyncSession):
    await make_rule(db_session, enabled=False)
    await crowd(db_session)

    await RulesConsumer().run_once()

    assert await firings(db_session) == []


async def test_events_outside_the_window_do_not_count(db_session: AsyncSession):
    """Four people at the start, a fifth a full window later. Five dwells exist
    in the log; never five inside any 30-second window."""
    await make_rule(db_session)
    await crowd(db_session, people=4)
    await dwell(db_session, at=BASE + dt.timedelta(seconds=90), anon_id="P-004")

    await RulesConsumer().run_once()

    assert await firings(db_session) == []


# ── ADR-002 §1: the window is a read, not a dict ──────────────────────────────


async def test_window_survives_a_restart(db_session: AsyncSession):
    """**The test this file exists for.**

    Four people arrive, the consumer processes them, and then the process dies —
    modelled by discarding the consumer instance entirely. A fifth person arrives
    and a *new* instance handles them.

    An evaluator holding its window in memory (`floats-agent`'s
    `_event_windows: dict[str, list[dict]]`) sees a window containing one person
    and does not fire. The rule an operator wrote is silently not in force after
    any restart, which on a conference floor means after any deploy, crash or
    laptop lid. Reading the window from the log makes the restart invisible.
    """
    await make_rule(db_session)
    await crowd(db_session, people=4)

    await RulesConsumer().run_once()
    assert await firings(db_session) == []

    # The process dies here. Nothing of the first instance survives.
    await dwell(db_session, at=BASE + dt.timedelta(seconds=5), anon_id="P-004")
    await RulesConsumer().run_once()

    rows = await firings(db_session)
    assert len(rows) == 1
    assert rows[0].payload["matched"]["observed"] == 5


# ── ADR-002 §2: cooldown on event time ────────────────────────────────────────


async def test_cooldown_suppresses_a_second_firing(db_session: AsyncSession):
    await make_rule(db_session, cooldown_sec=300)
    await crowd(db_session)
    await RulesConsumer().run_once()

    # Another five people, well inside the cooldown.
    await crowd(db_session, start=BASE + dt.timedelta(seconds=10))
    await RulesConsumer().run_once()

    assert len(await firings(db_session)) == 1


async def test_cooldown_expires_on_event_time(db_session: AsyncSession):
    """The cooldown is 60 seconds of *event* time. The second crowd is 120
    seconds later by the edge clock and milliseconds later by the wall clock,
    and it is the edge clock that decides."""
    await make_rule(db_session, cooldown_sec=60)
    await crowd(db_session)
    await RulesConsumer().run_once()

    await crowd(db_session, start=BASE + dt.timedelta(seconds=120))
    await RulesConsumer().run_once()

    assert len(await firings(db_session)) == 2


async def test_a_replay_produces_the_same_firings(db_session: AsyncSession):
    """§2's real consequence, and the reason wall-clock cooldown is an ADR item
    rather than a nitpick.

    A wall-clock evaluator replayed later sees a `last_fired_at` that is now far
    in the past, decides the cooldown has expired, and fires again — so a replay
    of an afternoon produces more firings than the afternoon did, and every
    figure derived from `rule.fired` is wrong in a way nothing flags. Comparing
    `occurred_at` to `occurred_at` makes the replay reproduce the original run.
    """
    await make_rule(db_session, cooldown_sec=300)
    await crowd(db_session)
    await crowd(db_session, start=BASE + dt.timedelta(seconds=10))
    await RulesConsumer().run_once()
    before = len(await firings(db_session))
    assert before == 1

    await repository.reset_cursor(db_session, consumer="rules", tenant_id=T, to_seq=0)
    await db_session.commit()
    await RulesConsumer().run_once()

    assert len(await firings(db_session)) == before


# ── ADR-002 §3: derived event ids ─────────────────────────────────────────────


async def test_replay_produces_one_firing(db_session: AsyncSession):
    """A crash mid-batch redelivers events. With a random `event_id` the bus has
    nothing to dedupe on, so the second delivery appends a second `rule.fired` —
    and the dispatcher, which keys its idempotency on that id, posts to Slack
    twice for one crowd."""
    await make_rule(db_session, cooldown_sec=0)
    await crowd(db_session)

    consumer = RulesConsumer()
    await consumer.run_once()

    await repository.reset_cursor(db_session, consumer="rules", tenant_id=T, to_seq=0)
    await db_session.commit()
    await consumer.run_once()

    rows = await firings(db_session)
    assert len(rows) == 1


async def test_the_firing_id_is_derived_from_the_trigger(db_session: AsyncSession):
    """Same cause, same id, on any machine — the property `consumers/ids.py`
    exists to provide, asserted here rather than inferred from the dedupe."""
    from app.consumers.ids import derive_event_id

    await make_rule(db_session)
    last_at = await crowd(db_session)
    await RulesConsumer().run_once()

    rows = await firings(db_session)
    trigger_seq = rows[0].payload["triggerSeq"]
    assert rows[0].event_id == derive_event_id(
        "rules", T, S, "r_entry_crowd", trigger_seq
    )
    assert rows[0].occurred_at == last_at


# ── ADR-002 §4: `none` is judged at a boundary ────────────────────────────────


def quiet_rule(window_sec: int = 60) -> dict:
    return {"type": "none", "windowSec": window_sec}


async def test_none_does_not_fire_while_things_are_happening(
    db_session: AsyncSession,
):
    await make_rule(db_session, rule_id="r_quiet", condition=quiet_rule())
    await dwell(db_session, at=BASE, anon_id="P-000")
    await dwell(db_session, at=BASE + dt.timedelta(seconds=10), anon_id="P-001")

    await RulesConsumer().run_once()

    assert await firings(db_session, "r_quiet") == []


async def test_none_fires_when_the_quiet_closes(db_session: AsyncSession):
    """Evaluated on arrival, a `none` condition asks whether the window is empty
    at the moment something filled it — nearly a contradiction. It is judged
    when the next thing happens, by looking back, and dated at the moment the
    silence became long enough rather than at the arrival that revealed it."""
    await make_rule(db_session, rule_id="r_quiet", condition=quiet_rule(60))
    await dwell(db_session, at=BASE, anon_id="P-000")
    # Two minutes of nothing, then somebody arrives.
    await dwell(db_session, at=BASE + dt.timedelta(seconds=120), anon_id="P-001")

    await RulesConsumer().run_once()

    rows = await firings(db_session, "r_quiet")
    assert len(rows) == 1
    assert rows[0].occurred_at == BASE + dt.timedelta(seconds=60)
    assert rows[0].payload["matched"]["observed"] == 0


async def test_none_fires_once_per_silence(db_session: AsyncSession):
    """Every arrival after the boundary looks back at the same silence. Two of
    them a second apart would each find a gap longer than the window, so the
    firing's identity has to be the silence rather than the arrival — otherwise
    a busy minute after a quiet one produces a burst of "nothing is happening"
    alerts."""
    await make_rule(
        db_session, rule_id="r_quiet", condition=quiet_rule(60), cooldown_sec=0
    )
    await dwell(db_session, at=BASE, anon_id="P-000")
    await dwell(db_session, at=BASE + dt.timedelta(seconds=120), anon_id="P-001")
    await dwell(db_session, at=BASE + dt.timedelta(seconds=121), anon_id="P-002")
    await dwell(db_session, at=BASE + dt.timedelta(seconds=122), anon_id="P-003")

    await RulesConsumer().run_once()

    assert len(await firings(db_session, "r_quiet")) == 1


async def test_none_needs_something_to_be_quiet_since(db_session: AsyncSession):
    """A booth that has been open for ten seconds has not "gone quiet". Without
    one prior occurrence there is no silence to measure, and firing from the
    session's first event would alert on every activation at its start."""
    await make_rule(db_session, rule_id="r_quiet", condition=quiet_rule(60))
    await repository.append_event(
        db_session,
        EventIn(
            event_id=uuid.uuid4(),
            tenant_id=T,
            session_id=S,
            type="session.started",
            payload={},
            occurred_at=BASE,
        ),
    )
    await db_session.commit()

    await RulesConsumer().run_once()

    assert await firings(db_session, "r_quiet") == []


async def test_session_ended_closes_a_quiet_window(db_session: AsyncSession):
    """ADR-002 names `session.ended` as the other boundary: an activation that
    closed without the thing ever happening again is exactly the case a `none`
    rule is about, and there is no arrival to reveal it."""
    await make_rule(db_session, rule_id="r_quiet", condition=quiet_rule(60))
    await dwell(db_session, at=BASE, anon_id="P-000")
    await repository.append_event(
        db_session,
        EventIn(
            event_id=uuid.uuid4(),
            tenant_id=T,
            session_id=S,
            type="session.ended",
            payload={},
            occurred_at=BASE + dt.timedelta(seconds=300),
        ),
    )
    await db_session.commit()

    await RulesConsumer().run_once()

    rows = await firings(db_session, "r_quiet")
    assert len(rows) == 1
    assert rows[0].payload["matched"]["noticedBy"] == "session.ended"


# ── loops ─────────────────────────────────────────────────────────────────────


async def test_a_rule_cannot_be_triggered_by_a_firing(db_session: AsyncSession):
    """`triggerType` is an open prefix check, so `rule.fired` is expressible. Two
    rules each triggered by the other's firing would produce events forever at
    the speed of the poll loop, so firings are not evaluated — refused
    explicitly rather than left as a shape nobody tried."""
    await make_rule(db_session, rule_id="r_a", condition={"type": "any"})
    await make_rule(
        db_session, rule_id="r_b", trigger_type=FIRED, condition={"type": "any"}
    )
    await dwell(db_session, at=BASE, anon_id="P-000")

    consumer = RulesConsumer()
    await consumer.run_once()
    await consumer.run_once()

    assert len(await firings(db_session, "r_a")) == 1
    assert await firings(db_session, "r_b") == []


async def test_a_camelcase_payload_is_also_matched(db_session: AsyncSession):
    """Both spellings, snake_case first.

    The tracker writes `zone_id`; a producer built against the browser's
    contract would write `zoneId`, and `perception/realmspace.py` is a file both
    tracks share. The spec already concedes this for `anon_id` / `person_id` —
    "one script has to work against either backend or the comparison breaks" —
    and the same tolerance applies here rather than making a rule silently match
    nothing depending on which producer wrote the event.
    """
    await make_rule(db_session)
    for i in range(5):
        await repository.append_event(
            db_session,
            EventIn(
                event_id=uuid.uuid4(),
                tenant_id=T,
                session_id=S,
                type=DWELL,
                payload={
                    "anonId": f"P-{i:03d}",
                    "zoneId": "z_entry",
                    "durationSec": 45.0,
                },
                occurred_at=BASE + dt.timedelta(seconds=i),
            ),
        )
    await db_session.commit()

    await RulesConsumer().run_once()

    rows = await firings(db_session)
    assert len(rows) == 1
    assert rows[0].payload["matched"]["countedBy"] == "people"
