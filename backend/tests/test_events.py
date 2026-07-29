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
    assert body["recorded_at"] is not None


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

    r = await client.get("/events", params={"tenant_id": "t_floats", "since_seq": 0})
    rows = r.json()
    assert [row["payload"]["i"] for row in rows] == [0, 1, 2]
    assert [row["seq"] for row in rows] == sorted(row["seq"] for row in rows)

    # since_seq is exclusive — the cursor is "the last seq I already handled"
    r = await client.get(
        "/events", params={"tenant_id": "t_floats", "since_seq": rows[0]["seq"]}
    )
    assert [row["payload"]["i"] for row in r.json()] == [1, 2]


async def test_reads_are_tenant_scoped(client: AsyncClient) -> None:
    """multi-tenant.md §2 — one tenant's events are never visible to another."""
    await client.post("/events", json=make_event(tenant_id="t_floats"))
    await client.post("/events", json=make_event(tenant_id="t_other"))

    r = await client.get("/events", params={"tenant_id": "t_floats"})
    rows = r.json()
    assert len(rows) == 1
    assert all(row["tenant_id"] == "t_floats" for row in rows)


async def test_filters_by_type_and_session(client: AsyncClient) -> None:
    await client.post("/events", json=make_event(type="perception.detection"))
    await client.post("/events", json=make_event(type="spatial.zone_enter"))
    await client.post("/events", json=make_event(session_id="s_other"))

    r = await client.get(
        "/events", params={"tenant_id": "t_floats", "type": "spatial.zone_enter"}
    )
    assert len(r.json()) == 1

    r = await client.get("/events", params={"tenant_id": "t_floats", "session_id": "s_demo"})
    assert len(r.json()) == 2
