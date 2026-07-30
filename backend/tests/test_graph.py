"""
What "the graph store works" means, as executable claims.

Tests 1–4 fail until upsert_person is implemented — they are the definition of
idempotent graph writes.

Note the shape difference from test_events.py: there is no separate test
database here. Neo4j Community is single-database (SHOW DATABASES returns only
`neo4j` and `system`), so isolation comes from tenant_id instead. That is a
constraint turned into an asset — if tenant scoping is broken anywhere, these
tests bleed into each other and fail.
"""

from __future__ import annotations

import pytest
from neo4j import AsyncSession

from app.graph import repository as graph

T = "t_test"
OTHER = "t_test_other"
S = "s_graph_demo"


async def test_upsert_person_creates_one_node(graph_session: AsyncSession) -> None:
    props = await graph.upsert_person(
        graph_session,
        tenant_id=T,
        session_id=S,
        anon_id="P-001",
        first_seen="2026-07-30T10:00:00Z",
        last_seen="2026-07-30T10:00:00Z",
    )
    assert props["anon_id"] == "P-001"
    assert props["tenant_id"] == T
    assert await graph.people_in_session(graph_session, tenant_id=T, session_id=S) == 1


async def test_upsert_person_twice_does_not_duplicate(
    graph_session: AsyncSession,
) -> None:
    """The uniqueness constraint plus MERGE: seeing the same person again must
    update, not insert."""
    for _ in range(2):
        await graph.upsert_person(
            graph_session,
            tenant_id=T,
            session_id=S,
            anon_id="P-001",
            first_seen="2026-07-30T10:00:00Z",
            last_seen="2026-07-30T10:00:05Z",
        )
    assert await graph.people_in_session(graph_session, tenant_id=T, session_id=S) == 1


async def test_first_seen_is_preserved_but_last_seen_moves(
    graph_session: AsyncSession,
) -> None:
    """The ON CREATE vs ON MATCH split — the point of the whole function.

    first_seen is true only at the first sighting and must never move.
    last_seen must move forward every time the tracker sees them again.
    """
    await graph.upsert_person(
        graph_session,
        tenant_id=T,
        session_id=S,
        anon_id="P-001",
        first_seen="2026-07-30T10:00:00Z",
        last_seen="2026-07-30T10:00:00Z",
        total_dwell_seconds=0.0,
    )
    props = await graph.upsert_person(
        graph_session,
        tenant_id=T,
        session_id=S,
        anon_id="P-001",
        first_seen="2026-07-30T10:00:30Z",  # a later value — must be ignored
        last_seen="2026-07-30T10:00:30Z",
        total_dwell_seconds=30.0,
    )
    assert props["first_seen"] == "2026-07-30T10:00:00Z", "first_seen was overwritten"
    assert props["last_seen"] == "2026-07-30T10:00:30Z", "last_seen did not advance"
    assert props["total_dwell_seconds"] == 30.0


async def test_anon_id_is_scoped_to_its_session(graph_session: AsyncSession) -> None:
    """data-model.md: anon_id is "session-scoped, never re-used across sessions".

    So P-001 in two sessions is two different people, and the constraint key
    (tenant_id, session_id, anon_id) has to make that true.
    """
    for sess in (S, "s_other_session"):
        await graph.upsert_person(
            graph_session,
            tenant_id=T,
            session_id=sess,
            anon_id="P-001",
            first_seen="2026-07-30T10:00:00Z",
            last_seen="2026-07-30T10:00:00Z",
        )
    assert await graph.people_in_session(graph_session, tenant_id=T, session_id=S) == 1
    assert (
        await graph.people_in_session(
            graph_session, tenant_id=T, session_id="s_other_session"
        )
        == 1
    )


async def test_relationships_are_idempotent(graph_session: AsyncSession) -> None:
    """Replaying a bus event must not draw the same edge twice."""
    await graph.upsert_person(
        graph_session,
        tenant_id=T,
        session_id=S,
        anon_id="P-001",
        first_seen="2026-07-30T10:00:00Z",
        last_seen="2026-07-30T10:00:00Z",
    )
    await graph.upsert_zone(
        graph_session,
        tenant_id=T,
        session_id=S,
        zone_id="z_entry",
        name="Entrance",
        type="entry",
    )
    for _ in range(3):
        await graph.link_entered(
            graph_session,
            tenant_id=T,
            session_id=S,
            anon_id="P-001",
            zone_id="z_entry",
            at="2026-07-30T10:00:01Z",
        )

    result = await graph_session.run(
        "MATCH (:Person {tenant_id: $t})-[r:ENTERED]->(:Zone {tenant_id: $t}) "
        "RETURN count(r) AS edges",
        t=T,
    )
    record = await result.single()
    assert record["edges"] == 1


async def test_reads_are_tenant_scoped(graph_session: AsyncSession) -> None:
    """multi-tenant.md §2. The database cannot enforce this (Community has no
    row-level security), so the repository must — this test is the check."""
    for tenant in (T, OTHER):
        await graph.upsert_person(
            graph_session,
            tenant_id=tenant,
            session_id=S,
            anon_id="P-001",
            first_seen="2026-07-30T10:00:00Z",
            last_seen="2026-07-30T10:00:00Z",
        )
    assert await graph.people_in_session(graph_session, tenant_id=T, session_id=S) == 1
    assert (
        await graph.people_in_session(graph_session, tenant_id=OTHER, session_id=S) == 1
    )


async def test_dwell_by_zone(graph_session: AsyncSession) -> None:
    """The query /live and the report both need, from data-model.md."""
    await graph.upsert_zone(
        graph_session,
        tenant_id=T,
        session_id=S,
        zone_id="z_lounge",
        name="Lounge",
        type="lounge",
    )
    for i, duration in enumerate([10.0, 30.0], start=1):
        anon = f"P-00{i}"
        await graph.upsert_person(
            graph_session,
            tenant_id=T,
            session_id=S,
            anon_id=anon,
            first_seen="2026-07-30T10:00:00Z",
            last_seen="2026-07-30T10:01:00Z",
        )
        await graph.link_dwelled_in(
            graph_session,
            tenant_id=T,
            session_id=S,
            anon_id=anon,
            zone_id="z_lounge",
            duration=duration,
            started_at=f"2026-07-30T10:00:0{i}Z",
            ended_at="2026-07-30T10:01:00Z",
        )

    rows = await graph.dwell_by_zone(graph_session, tenant_id=T, session_id=S)
    assert len(rows) == 1
    assert rows[0]["zone"] == "Lounge"
    assert rows[0]["avg_dwell"] == pytest.approx(20.0)
    assert rows[0]["visitors"] == 2


async def test_zone_polygon_round_trips(graph_session: AsyncSession) -> None:
    """Neo4j cannot store nested lists, so polygons are flattened on write.
    This pins that behaviour so the flatten/rebuild pair stays in sync."""
    props = await graph.upsert_zone(
        graph_session,
        tenant_id=T,
        session_id=S,
        zone_id="z_poly",
        name="Polygon Zone",
        type="engagement",
        polygon=[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]],
    )
    assert props["polygon"] == [0.0, 0.0, 1.0, 0.0, 1.0, 1.0]
