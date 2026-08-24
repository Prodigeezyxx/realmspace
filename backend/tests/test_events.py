"""
What "the event bus works" actually means, as executable claims.

Tests 2 and 3 are the ones that fail until append_event is implemented — they
are the definition of the idempotency guarantee in docs/event-bus-spec.md §2.
"""

from __future__ import annotations

import uuid

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def make_event(**overrides) -> dict:
    """A perception.detection event, the first thing the real producer will send
    (see perception/realmspace.py)."""
    event = {
        "event_id": str(uuid.uuid4()),
        "tenant_id": "t_floats",
        "session_id": "s_demo",
        "type": "perception.detection",
        "payload": {"person_id": "P-001", "bbox": [10, 10, 90, 180], "confidence": 0.91},
        "occurred_at": "2026-07-28T12:00:00Z",
    }
    event.update(overrides)
    return event


async def test_append_returns_a_seq(client: AsyncClient) -> None:
    r = await client.post("/events", json=make_event())
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["seq"] == 1
    # camelCase + ms epoch, per dashboard/src/lib/contracts/events.ts
    assert isinstance(body["recordedAt"], int)
    assert body["eventId"] and body["tenantId"] and body["occurredAt"]


async def test_duplicate_event_id_is_a_no_op(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The core guarantee: a producer retrying after a timeout must not create a
    second row, and must get the same seq back."""
    event = make_event()

    first = await client.post("/events", json=event)
    assert first.status_code == 201, first.text

    second = await client.post("/events", json=event)
    assert second.status_code == 200, second.text  # 200, not 201 — nothing created
    assert second.json()["seq"] == first.json()["seq"]

    count = await db_session.scalar(
        text("SELECT count(*) FROM event_log WHERE event_id = :eid"),
        {"eid": event["event_id"]},
    )
    assert count == 1


async def test_duplicate_does_not_overwrite_payload(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Append-only means the log never rewrites history. Re-sending a used
    event_id with different data must leave the original row untouched."""
    event = make_event()
    await client.post("/events", json=event)

    tampered = {**event, "payload": {"person_id": "P-999", "confidence": 0.0}}
    r = await client.post("/events", json=tampered)
    assert r.status_code == 200

    stored = await db_session.scalar(
        text("SELECT payload FROM event_log WHERE event_id = :eid"),
        {"eid": event["event_id"]},
    )
    assert stored["person_id"] == "P-001"


async def test_read_forward_from_cursor(client: AsyncClient) -> None:
    """The shape every consumer polls with: everything after the last seq I
    handled, in seq order."""
    for i in range(3):
        r = await client.post("/events", json=make_event(payload={"i": i}))
        assert r.status_code == 201, r.text

    r = await client.get("/events", params={"since_seq": 0})
    rows = r.json()
    assert [row["payload"]["i"] for row in rows] == [0, 1, 2]
    assert [row["seq"] for row in rows] == sorted(row["seq"] for row in rows)

    # since_seq is exclusive — the cursor is "the last seq I already handled"
    r = await client.get("/events", params={"since_seq": rows[0]["seq"]})
    assert [row["payload"]["i"] for row in r.json()] == [1, 2]


async def test_reads_are_tenant_scoped(client: AsyncClient) -> None:
    """multi-tenant.md §2 — one tenant's events are never visible to another.

    Since auth landed this is enforced twice over: writing for another tenant is
    refused outright, and reads take their tenant from the credential rather
    than a parameter. See test_auth.py for the enforcement itself.
    """
    assert (await client.post("/events", json=make_event(tenant_id="t_floats"))).status_code == 201

    # the credential is for t_floats, so this write is refused rather than
    # landing somewhere it should not
    assert (await client.post("/events", json=make_event(tenant_id="t_other"))).status_code == 403

    rows = (await client.get("/events")).json()
    assert len(rows) == 1
    assert all(row["tenantId"] == "t_floats" for row in rows)


async def test_filters_by_type_and_session(client: AsyncClient) -> None:
    await client.post("/events", json=make_event(type="perception.detection"))
    await client.post("/events", json=make_event(type="spatial.zone_enter"))
    await client.post("/events", json=make_event(session_id="s_other"))

    r = await client.get("/events", params={"type": "spatial.zone_enter"})
    assert len(r.json()) == 1

    r = await client.get("/events", params={"session_id": "s_demo"})
    assert len(r.json()) == 2


async def test_several_types_at_once_returns_their_union(client: AsyncClient) -> None:
    """`?type=a&type=b` is a or b, not a and b.

    A row has one type, so an AND would return an empty page — and an empty page
    is indistinguishable from "nothing happened", which is the shape of bug that
    makes a report show a quiet day instead of a broken query.

    The reader this exists for is the report's benchmark: it computes a scorecard
    over several past activations and needs the spatial events without dragging
    a day of `perception.detection` along, which is almost the whole log.
    """
    await client.post("/events", json=make_event(type="perception.detection"))
    await client.post("/events", json=make_event(type="spatial.zone_enter"))
    await client.post("/events", json=make_event(type="spatial.dwell"))

    r = await client.get(
        "/events", params=[("type", "spatial.zone_enter"), ("type", "spatial.dwell")]
    )
    assert sorted(row["type"] for row in r.json()) == [
        "spatial.dwell",
        "spatial.zone_enter",
    ]

    # One type still behaves exactly as it did, which every existing caller
    # depends on.
    r = await client.get("/events", params={"type": "perception.detection"})
    assert [row["type"] for row in r.json()] == ["perception.detection"]

    # And no type at all is still everything, rather than nothing.
    assert len((await client.get("/events")).json()) == 3


async def test_phase_3_types_post_over_http(client: AsyncClient) -> None:
    """The six types Phase 3 pre-registers reach the log over the real endpoint.

    The unit test in test_consumers.py proves the validator accepts them; this
    proves the thing a producer's author will actually do — POST it — returns
    201 rather than the 422 four of these got before the namespaces were added.
    """
    for event_type in (
        "rfid.read",
        "spatial.tagged",
        "intent.scored",
        "drift.detected",
        "calibration.updated",
        "crm.retract",
    ):
        r = await client.post("/events", json=make_event(type=event_type))
        assert r.status_code == 201, f"{event_type}: {r.text}"


async def test_a_misspelled_namespace_is_still_refused(client: AsyncClient) -> None:
    """Registering four new namespaces widened what the bus accepts. It must not
    have widened it to anything, and the 422 must still say where the taxonomy
    lives — an error naming the doc is the difference between a producer author
    fixing this in a minute and filing a bug."""
    r = await client.post("/events", json=make_event(type="calibraton.updated"))
    assert r.status_code == 422
    assert "docs/event-bus-spec.md" in r.text
