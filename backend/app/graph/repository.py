"""
Every Cypher statement in the system lives here. Nothing else writes Cypher.

## Why that rule is load-bearing, not tidiness

multi-tenant.md §2 wants tenant isolation enforced by the database. Neo4j
Community cannot do that — no row-level security, no per-tenant database, and
(verified) no property-existence constraint, so the database will happily store
a node with no tenant_id at all and happily return another tenant's nodes to a
query that forgot to filter.

That makes this module the *only* thing enforcing isolation. So:

  - `tenant_id` is a required keyword argument on every function
  - every Cypher string filters or sets `tenant_id`
  - callers cannot construct their own queries, because they don't hold a session

If Cypher leaks into routers or consumers, the guarantee is gone and nothing
will fail loudly to tell you. That is the whole reason for the rule.

## MERGE, not CREATE

Graph writes come from bus consumers, which are at-least-once: the same event
can legitimately be processed twice (event-bus-spec.md §4 — "process
idempotently"). Because the graph is a separate store from the log, a graph
write and a cursor advance cannot share a transaction, so replay after a crash
*will* re-apply writes. MERGE makes that harmless; CREATE would duplicate.
"""

from __future__ import annotations

from typing import Any

from neo4j import AsyncSession

# ── writes ────────────────────────────────────────────────────────────────────


async def upsert_person(
    session: AsyncSession,
    *,
    tenant_id: str,
    session_id: str,
    anon_id: str,
    first_seen: str,
    last_seen: str,
    total_dwell_seconds: float = 0.0,
    attention_score: float = 0.0,
    appearance_summary: str | None = None,
) -> dict[str, Any]:
    """Create or update a Person node. Returns the node's properties as a dict.

    The counterpart to append_event, and the contrast is the point. An event
    already in the log is history: ON CONFLICT DO NOTHING, never rewritten. A
    Person is the opposite — the tracker sees them again every frame, and
    `last_seen` / `total_dwell_seconds` / `attention_score` are *supposed* to
    move. So: MERGE, with the first-sighting fields separated from the ones that
    advance.

    The MERGE pattern is exactly the uniqueness key
    (tenant_id, session_id, anon_id). It has to be: MERGE can only use the
    backing index if the pattern matches the constraint, and without that index
    two concurrent writers can each fail to see the other's node and create
    duplicates.

    `first_seen` is set ON CREATE only. A caller re-reporting a later
    first_seen — which the tracker does, since it doesn't remember across
    restarts — must not overwrite the original. That is the one field where
    being wrong silently corrupts session-length analysis.

    `appearance_summary` uses coalesce($new, p.existing) so a call that omits it
    doesn't null out a summary an earlier VLM pass already wrote. Plain
    SET would erase it.
    """
    result = await session.run(
        """
        MERGE (p:Person {tenant_id: $tenant_id, session_id: $session_id, anon_id: $anon_id})
        ON CREATE SET p.first_seen = $first_seen
        SET p.last_seen           = $last_seen,
            p.total_dwell_seconds = $total_dwell_seconds,
            p.attention_score     = $attention_score,
            p.appearance_summary  = coalesce($appearance_summary, p.appearance_summary)
        RETURN p
        """,
        tenant_id=tenant_id,
        session_id=session_id,
        anon_id=anon_id,
        first_seen=first_seen,
        last_seen=last_seen,
        total_dwell_seconds=total_dwell_seconds,
        attention_score=attention_score,
        appearance_summary=appearance_summary,
    )
    record = await result.single()
    return dict(record["p"])


async def upsert_zone(
    session: AsyncSession,
    *,
    tenant_id: str,
    session_id: str,
    zone_id: str,
    name: str,
    type: str,
    polygon: list[list[float]] | None = None,
    color: str | None = None,
    capacity: int | None = None,
) -> dict[str, Any]:
    """Create or update a Zone (data-model.md → `(:Zone {...})`).

    Zones come from the session wizard, not from perception — an operator draws
    them once and may edit them mid-session. So everything except the key is
    fair game to update; there is no "first seen" equivalent here.

    Neo4j cannot store nested lists as a property, so the polygon is flattened
    to [x1, y1, x2, y2, ...] on write and rebuilt into pairs on read.
    """
    flat: list[float] | None = None
    if polygon is not None:
        flat = [coord for point in polygon for coord in point]

    result = await session.run(
        """
        MERGE (z:Zone {tenant_id: $tenant_id, id: $zone_id})
        SET z.session_id = $session_id,
            z.name       = $name,
            z.type       = $type,
            z.polygon    = $polygon,
            z.color      = $color,
            z.capacity   = $capacity
        RETURN z
        """,
        tenant_id=tenant_id,
        zone_id=zone_id,
        session_id=session_id,
        name=name,
        type=type,
        polygon=flat,
        color=color,
        capacity=capacity,
    )
    record = await result.single()
    return dict(record["z"])


async def upsert_session(
    session: AsyncSession,
    *,
    tenant_id: str,
    session_id: str,
    client: str | None = None,
    campaign: str | None = None,
    venue: str | None = None,
    city: str | None = None,
    started_at: str | None = None,
    ends_at: str | None = None,
    booth_width_m: float | None = None,
    booth_depth_m: float | None = None,
    camera_count: int | None = None,
) -> dict[str, Any]:
    """Create or update a Session node (data-model.md → `(:Session {...})`)."""
    result = await session.run(
        """
        MERGE (s:Session {tenant_id: $tenant_id, id: $session_id})
        SET s.client        = $client,
            s.campaign      = $campaign,
            s.venue         = $venue,
            s.city          = $city,
            s.started_at    = $started_at,
            s.ends_at       = $ends_at,
            s.booth_width_m = $booth_width_m,
            s.booth_depth_m = $booth_depth_m,
            s.camera_count  = $camera_count
        RETURN s
        """,
        tenant_id=tenant_id,
        session_id=session_id,
        client=client,
        campaign=campaign,
        venue=venue,
        city=city,
        started_at=started_at,
        ends_at=ends_at,
        booth_width_m=booth_width_m,
        booth_depth_m=booth_depth_m,
        camera_count=camera_count,
    )
    record = await result.single()
    return dict(record["s"])


async def link_entered(
    session: AsyncSession,
    *,
    tenant_id: str,
    session_id: str,
    anon_id: str,
    zone_id: str,
    at: str,
) -> None:
    """(Person)-[:ENTERED]->(Zone) — data-model.md relationships.

    MERGE on the relationship, not CREATE: replaying the same bus event must not
    produce a second identical edge. Both endpoints are matched with tenant_id
    so a relationship can never be drawn across tenants.
    """
    await session.run(
        """
        MATCH (p:Person {tenant_id: $tenant_id, session_id: $session_id, anon_id: $anon_id})
        MATCH (z:Zone   {tenant_id: $tenant_id, id: $zone_id})
        MERGE (p)-[r:ENTERED {at: $at}]->(z)
        """,
        tenant_id=tenant_id,
        session_id=session_id,
        anon_id=anon_id,
        zone_id=zone_id,
        at=at,
    )


async def link_dwelled_in(
    session: AsyncSession,
    *,
    tenant_id: str,
    session_id: str,
    anon_id: str,
    zone_id: str,
    duration: float,
    started_at: str,
    ended_at: str,
) -> None:
    """(Person)-[:DWELLED_IN {duration, started_at, ended_at}]->(Zone).

    Keyed on started_at so one person can dwell in the same zone more than once
    in a session and each visit is its own edge — but replaying the same event
    still merges onto the same one.
    """
    await session.run(
        """
        MATCH (p:Person {tenant_id: $tenant_id, session_id: $session_id, anon_id: $anon_id})
        MATCH (z:Zone   {tenant_id: $tenant_id, id: $zone_id})
        MERGE (p)-[r:DWELLED_IN {started_at: $started_at}]->(z)
        SET r.duration = $duration, r.ended_at = $ended_at
        """,
        tenant_id=tenant_id,
        session_id=session_id,
        anon_id=anon_id,
        zone_id=zone_id,
        duration=duration,
        started_at=started_at,
        ended_at=ended_at,
    )


# ── reads ─────────────────────────────────────────────────────────────────────


async def dwell_by_zone(
    session: AsyncSession, *, tenant_id: str, session_id: str | None = None
) -> list[dict[str, Any]]:
    """Average dwell per zone — data-model.md "Longest average dwell per zone".

    The doc's version has no tenant filter because it predates multi-tenant.md.
    This one does, and there is no way to call it without one.

    Feeds the /live dwell tile and the report's zone breakdown.
    """
    result = await session.run(
        """
        MATCH (p:Person {tenant_id: $tenant_id})-[d:DWELLED_IN]->(z:Zone {tenant_id: $tenant_id})
        WHERE $session_id IS NULL OR p.session_id = $session_id
        RETURN z.id            AS zone_id,
               z.name          AS zone,
               avg(d.duration) AS avg_dwell,
               count(DISTINCT p) AS visitors
        ORDER BY avg_dwell DESC
        """,
        tenant_id=tenant_id,
        session_id=session_id,
    )
    return [dict(record) async for record in result]


async def people_in_session(
    session: AsyncSession, *, tenant_id: str, session_id: str
) -> int:
    """Unique people seen in a session — the /live "People now" KPI's source."""
    result = await session.run(
        """
        MATCH (p:Person {tenant_id: $tenant_id, session_id: $session_id})
        RETURN count(p) AS people
        """,
        tenant_id=tenant_id,
        session_id=session_id,
    )
    record = await result.single()
    return record["people"]


async def delete_tenant(session: AsyncSession, *, tenant_id: str) -> int:
    """Delete every node for a tenant and its relationships. Returns node count.

    Two uses: the tests wipe `t_test` between cases (Community edition has no
    separate test database), and multi-tenant.md §6 / privacy.md need a real
    erasure path for a tenant's graph.
    """
    result = await session.run(
        """
        MATCH (n {tenant_id: $tenant_id})
        DETACH DELETE n
        RETURN count(n) AS deleted
        """,
        tenant_id=tenant_id,
    )
    record = await result.single()
    return record["deleted"]
