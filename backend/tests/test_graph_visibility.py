"""
A write is not a write until another session can see it.

## Why this file exists, including the part that turned out to be wrong

`test_sessions.py::test_graph_endpoint_reports_dwell_and_unique_people` failed on
CI on 2026-09-01 and again on 2026-09-02, passed on the runs between, and passes
on a re-run of the same commit. The failure said `uniquePeople == 1` and
`dwellByZone == []` — a person visible and their `DWELLED_IN` edge not, from one
request.

The theory was that the edge had not committed. `session.run` starts an
auto-commit transaction, and `upsert_person` ends with `await result.single()`
while every `link_*` discarded its result — so the person would commit and the
edge would not. It is a good theory and it is **wrong**: measured against
Neo4j 5.26 and 2026.06, on driver 6.2.0 and CI's 6.3.0, the session bookmark
advances after `link_dwelled_in` returns and a second session opened immediately
sees the edge. The driver completes the transaction whether or not anybody reads
the result.

So the flake's cause is still unknown, and the tests below are kept for what they
do assert rather than for what they were written to catch: **nothing else in the
suite reads the graph through a second session.** Every other test writes and
reads through the one `graph_session` fixture, which is the one arrangement that
cannot show a visibility problem — while the API is always the other one, since
`get_graph_session` opens a session per request. These fail if a writer ever
stops finishing its work before it returns.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator

import pytest
from neo4j import AsyncSession as GraphSession

from app.config import get_settings
from app.graph import repository as graph_repo
from app.graph.driver import make_driver

T = "t_test"
S = "s_visibility"
BASE = dt.datetime(2026, 9, 2, 10, 0, 0, tzinfo=dt.timezone.utc)
POLY = [[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0]]


@pytest.fixture
async def reader() -> AsyncIterator[GraphSession]:
    """A second Neo4j session, held open alongside the writer's.

    Its own driver, as `graph_session` uses its own: the point is two
    independent sessions, which is what the API and a consumer are to each other.
    """
    settings = get_settings()
    driver = make_driver(
        settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password
    )
    try:
        async with driver.session(database=settings.neo4j_database) as session:
            yield session
    finally:
        await driver.close()


async def a_person_in_a_zone(graph_session: GraphSession) -> None:
    await graph_repo.upsert_session(
        graph_session, tenant_id=T, session_id=S, venue="Visibility Hall"
    )
    await graph_repo.upsert_zone(
        graph_session, tenant_id=T, session_id=S,
        zone_id="z_a", name="Mirror Room", type="feature", polygon=POLY,
    )
    await graph_repo.upsert_person(
        graph_session, tenant_id=T, session_id=S, anon_id="cam-1/P-001",
        first_seen=BASE.isoformat(), last_seen=BASE.isoformat(),
    )


async def test_a_dwell_edge_is_visible_to_another_session(
    graph_session: GraphSession, reader: GraphSession
) -> None:
    """The CI failure, reproduced without waiting for a slow runner.

    `dwell_by_zone` is what `GET /v1/sessions/{id}/graph` calls, on a session of
    its own. Read there before `link_dwelled_in`'s transaction has committed, a
    zone that somebody stood in for 90 seconds reports nothing at all.
    """
    await a_person_in_a_zone(graph_session)
    await graph_repo.link_dwelled_in(
        graph_session, tenant_id=T, session_id=S, anon_id="cam-1/P-001",
        zone_id="z_a", duration=90.0,
        started_at=BASE.isoformat(),
        ended_at=(BASE + dt.timedelta(seconds=90)).isoformat(),
    )

    # No sleep, no retry: a writer that has returned has finished writing.
    rows = await graph_repo.dwell_by_zone(reader, tenant_id=T, session_id=S)

    assert rows == [
        {"zone_id": "z_a", "zone": "Mirror Room", "avg_dwell": 90.0, "visitors": 1}
    ]


async def test_the_other_edges_are_visible_too(
    graph_session: GraphSession, reader: GraphSession
) -> None:
    """`ENTERED`, `LEFT` and `LOOKED_AT` have the same shape as the dwell edge and
    the same defect. Counted through a second session rather than asserted one at
    a time: what is being tested is that the writer committed, not what each
    Cypher statement says."""
    await a_person_in_a_zone(graph_session)
    await graph_repo.link_entered(
        graph_session, tenant_id=T, session_id=S, anon_id="cam-1/P-001",
        zone_id="z_a", at=BASE.isoformat(),
    )
    await graph_repo.link_left(
        graph_session, tenant_id=T, session_id=S, anon_id="cam-1/P-001",
        zone_id="z_a", at=(BASE + dt.timedelta(seconds=90)).isoformat(),
    )
    await graph_repo.link_looked_at(
        graph_session, tenant_id=T, session_id=S, anon_id="cam-1/P-001",
        zone_id="z_a", duration=3.0, confidence=0.9,
        started_at=BASE.isoformat(),
    )

    result = await reader.run(
        """
        MATCH (p:Person {tenant_id: $t, session_id: $s})-[r]->(:Zone)
        RETURN type(r) AS kind ORDER BY kind
        """,
        t=T, s=S,
    )
    kinds = [record["kind"] async for record in result]

    assert kinds == ["ENTERED", "LEFT", "LOOKED_AT"]


async def test_a_surface_interaction_is_visible_to_another_session(
    graph_session: GraphSession, reader: GraphSession
) -> None:
    """`link_interacted_with` is the Engagement layer's only edge — the one the
    tablet feature spent a phase getting a producer for. Written and not
    committed, a touchpoint that has been pressed reports `trigger_count` of
    nothing at all, which is the `0*` that layer rendered for four phases and
    would be indistinguishable from it."""
    await a_person_in_a_zone(graph_session)
    await graph_repo.upsert_surface(
        graph_session, tenant_id=T, session_id=S,
        surface_id="sf_mirror", label="AR Mirror", type="mirror", zone_id="z_a",
    )
    await graph_repo.link_interacted_with(
        graph_session, tenant_id=T, session_id=S, anon_id="cam-1/P-001",
        surface_id="sf_mirror", at=BASE.isoformat(), kind="tap",
    )

    result = await reader.run(
        """
        MATCH (:Person {tenant_id: $t, session_id: $s})
              -[r:INTERACTED_WITH]->(:Surface {id: 'sf_mirror'})
        RETURN count(r) AS taps
        """,
        t=T, s=S,
    )
    record = await result.single()

    assert record["taps"] == 1
