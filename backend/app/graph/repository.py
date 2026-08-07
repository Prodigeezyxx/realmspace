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
    weight: float = 1.0,
    funnel_order: int | None = None,
) -> dict[str, Any]:
    """Create or update a Zone (data-model.md → `(:Zone {...})`).

    Zones come from the session wizard, not from perception — an operator draws
    them once and may edit them mid-session. So everything except the key is
    fair game to update; there is no "first seen" equivalent here.

    Neo4j cannot store nested lists as a property, so the polygon is flattened
    to [x1, y1, x2, y2, ...] on write and rebuilt into pairs on read.

    `weight` and `funnel_order` are measurement parameters, not geometry.
    roi-framework.md §2 defines dwell-weighted attention as `Σ(dwell × weight)`
    — "not all dwell is equal" — and §5 says the funnel is defined at session
    setup, before the activation runs. They live on the Zone because that is
    where the operator sets them and where the report reads them; storing them
    anywhere else would let the two drift apart mid-session.

    `weight` defaults to 1.0 rather than 0, so a zone whose weight was never set
    counts as ordinary dwell instead of silently contributing nothing.
    """
    flat: list[float] | None = None
    if polygon is not None:
        flat = [coord for point in polygon for coord in point]

    result = await session.run(
        """
        MERGE (z:Zone {tenant_id: $tenant_id, session_id: $session_id, id: $zone_id})
        SET z.name         = $name,
            z.type         = $type,
            z.polygon      = $polygon,
            z.color        = $color,
            z.capacity     = $capacity,
            z.weight       = $weight,
            z.funnel_order = $funnel_order
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
        weight=weight,
        funnel_order=funnel_order,
    )
    record = await result.single()
    return dict(record["z"])


async def upsert_surface(
    session: AsyncSession,
    *,
    tenant_id: str,
    session_id: str,
    surface_id: str,
    label: str,
    type: str,
    zone_id: str | None = None,
    active: bool = True,
) -> dict[str, Any]:
    """Create or update a Surface (data-model.md → `(:Surface {...})`).

    A booth touchpoint: an AR mirror, a scent station, an RFID wall. Like Zone
    it comes from the session wizard rather than from perception, so everything
    but the key is updatable.

    `trigger_count` is deliberately **not** set here. It is a running total
    maintained by `link_interacted_with` as interactions arrive, and writing it
    from the config would reset an activation's tally every time an operator
    renamed a touchpoint mid-session.
    """
    result = await session.run(
        """
        MERGE (s:Surface {tenant_id: $tenant_id, session_id: $session_id, id: $surface_id})
        ON CREATE SET s.trigger_count = 0
        SET s.label      = $label,
            s.type       = $type,
            s.zone_id    = $zone_id,
            s.active     = $active
        RETURN s
        """,
        tenant_id=tenant_id,
        session_id=session_id,
        surface_id=surface_id,
        label=label,
        type=type,
        zone_id=zone_id,
        active=active,
    )
    record = await result.single()
    return dict(record["s"])


async def link_interacted_with(
    session: AsyncSession,
    *,
    tenant_id: str,
    session_id: str,
    anon_id: str,
    surface_id: str,
    at: str,
    kind: str | None = None,
    duration: float | None = None,
) -> None:
    """(Person)-[:INTERACTED_WITH {duration, kind}]->(Surface) — data-model.md.

    Keyed on `at`, so one person can use the same touchpoint repeatedly and each
    use is its own edge, while replaying the same event merges onto the one it
    already wrote.

    `trigger_count` is incremented inside the same MERGE, which is what keeps it
    honest under replay: the counter only moves when the relationship is newly
    created, so re-processing an event cannot inflate a sponsor's usage figure.
    """
    await session.run(
        """
        MATCH (p:Person  {tenant_id: $tenant_id, session_id: $session_id, anon_id: $anon_id})
        MATCH (s:Surface {tenant_id: $tenant_id, session_id: $session_id, id: $surface_id})
        MERGE (p)-[r:INTERACTED_WITH {at: $at}]->(s)
        ON CREATE SET s.trigger_count = coalesce(s.trigger_count, 0) + 1
        SET r.kind = $kind, r.duration = $duration
        """,
        tenant_id=tenant_id,
        session_id=session_id,
        anon_id=anon_id,
        surface_id=surface_id,
        at=at,
        kind=kind,
        duration=duration,
    )


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
    engaged_threshold_seconds: float = 60.0,
    activation_cost: float | None = None,
    currency: str = "USD",
    attribution_model: str = "influenced",
    revenue_influenced: float | None = None,
    qualified_leads: int | None = None,
) -> dict[str, Any]:
    """Create or update a Session node (data-model.md → `(:Session {...})`).

    The trailing properties are the activation's **measurement parameters**,
    added for roi-framework.md §5 — "the strongest ROI comes from planning it
    into the activation, not bolting it on". Every Layer-4 metric is defined in
    terms of them: CPEV and CPQL divide by `activation_cost`, the engagement
    rate counts anyone past `engaged_threshold_seconds`, and the ROI ratio is
    only defensible if the `attribution_model` was agreed before doors opened
    rather than chosen afterwards to flatter the number.

    They are stored, not computed, for that last reason: a parameter picked
    after the fact is an argument, and §3's design principle is that we never
    inflate.

    ## revenue_influenced and qualified_leads are operator-supplied

    Those two are different in kind from the rest and the difference matters.
    Everything else here is a *setting*; these two are *the client's own
    figures*, typed in by a human, because realmspace cannot yet measure them —
    influenced revenue comes from CRM attribution, which is Phase 4. Until then
    the honest options are to take the client's number or to show nothing, and
    showing nothing is what happens when these are left null: the ROI ratio
    reports as unknown rather than as zero.

    They must never be presented as measured. Anything rendering them says where
    they came from.
    """
    result = await session.run(
        """
        MERGE (s:Session {tenant_id: $tenant_id, id: $session_id})
        SET s.client                    = $client,
            s.campaign                  = $campaign,
            s.venue                     = $venue,
            s.city                      = $city,
            s.started_at                = $started_at,
            s.ends_at                   = $ends_at,
            s.booth_width_m             = $booth_width_m,
            s.booth_depth_m             = $booth_depth_m,
            s.camera_count              = $camera_count,
            s.engaged_threshold_seconds = $engaged_threshold_seconds,
            s.activation_cost           = $activation_cost,
            s.currency                  = $currency,
            s.attribution_model         = $attribution_model,
            s.revenue_influenced        = $revenue_influenced,
            s.qualified_leads           = $qualified_leads
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
        engaged_threshold_seconds=engaged_threshold_seconds,
        activation_cost=activation_cost,
        currency=currency,
        attribution_model=attribution_model,
        revenue_influenced=revenue_influenced,
        qualified_leads=qualified_leads,
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
        MATCH (z:Zone   {tenant_id: $tenant_id, session_id: $session_id, id: $zone_id})
        MERGE (p)-[r:ENTERED {at: $at}]->(z)
        """,
        tenant_id=tenant_id,
        session_id=session_id,
        anon_id=anon_id,
        zone_id=zone_id,
        at=at,
    )


async def link_left(
    session: AsyncSession,
    *,
    tenant_id: str,
    session_id: str,
    anon_id: str,
    zone_id: str,
    at: str,
) -> None:
    """(Person)-[:LEFT]->(Zone) — data-model.md relationships.

    Declared in the doc from the start but never implemented until the tracker
    needed somewhere to record a zone exit. Same MERGE-on-timestamp shape as
    link_entered, so replaying the exit event does not draw a second edge.
    """
    await session.run(
        """
        MATCH (p:Person {tenant_id: $tenant_id, session_id: $session_id, anon_id: $anon_id})
        MATCH (z:Zone   {tenant_id: $tenant_id, session_id: $session_id, id: $zone_id})
        MERGE (p)-[r:LEFT {at: $at}]->(z)
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
        MATCH (z:Zone   {tenant_id: $tenant_id, session_id: $session_id, id: $zone_id})
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


async def zones_for_session(
    session: AsyncSession,
    *,
    tenant_id: str,
    session_id: str,
    include_undrawn: bool = False,
) -> list[dict[str, Any]]:
    """Zones with their polygons, for the tracker to test detections against.

    Rebuilds the flat [x1,y1,x2,y2,…] stored by upsert_zone back into
    [[x1,y1],[x2,y2],…] pairs, so callers never see the storage shape.

    Zones without a polygon are skipped by default — an operator can create a
    zone before drawing it, and a zone with no boundary can't contain anyone.
    `include_undrawn` is for the config endpoint, which has to show the operator
    the zone they half-created rather than pretending it doesn't exist.

    `weight` and `funnel_order` come back with the geometry because the report
    needs them alongside the dwell they weight, and a second round trip to fetch
    them is a second chance for the two to disagree about which zones exist.
    """
    result = await session.run(
        """
        MATCH (z:Zone {tenant_id: $tenant_id, session_id: $session_id})
        WHERE $include_undrawn OR z.polygon IS NOT NULL
        RETURN z.id           AS id,
               z.name         AS name,
               z.type         AS type,
               z.polygon      AS polygon,
               z.color        AS color,
               z.capacity     AS capacity,
               z.weight       AS weight,
               z.funnel_order AS funnel_order
        ORDER BY coalesce(z.funnel_order, 2147483647), z.id
        """,
        tenant_id=tenant_id,
        session_id=session_id,
        include_undrawn=include_undrawn,
    )
    zones: list[dict[str, Any]] = []
    async for record in result:
        flat = record["polygon"] or []
        zones.append(
            {
                "id": record["id"],
                "name": record["name"],
                "type": record["type"],
                "polygon": [
                    [flat[i], flat[i + 1]] for i in range(0, len(flat) - 1, 2)
                ],
                "color": record["color"],
                "capacity": record["capacity"],
                # A zone written before weights existed has no weight property.
                # Defaulting here rather than at each call site keeps the "no
                # weight means ordinary dwell" rule in one place.
                "weight": record["weight"] if record["weight"] is not None else 1.0,
                "funnel_order": record["funnel_order"],
            }
        )
    return zones


async def surfaces_for_session(
    session: AsyncSession, *, tenant_id: str, session_id: str
) -> list[dict[str, Any]]:
    """A session's touchpoints, with how many times each has been used."""
    result = await session.run(
        """
        MATCH (s:Surface {tenant_id: $tenant_id, session_id: $session_id})
        RETURN s.id            AS id,
               s.label         AS label,
               s.type          AS type,
               s.zone_id       AS zone_id,
               s.active        AS active,
               s.trigger_count AS trigger_count
        ORDER BY s.label, s.id
        """,
        tenant_id=tenant_id,
        session_id=session_id,
    )
    return [
        {
            "id": r["id"],
            "label": r["label"],
            "type": r["type"],
            "zone_id": r["zone_id"],
            "active": r["active"] if r["active"] is not None else True,
            "trigger_count": r["trigger_count"] or 0,
        }
        async for r in result
    ]


async def prune_surfaces(
    session: AsyncSession, *, tenant_id: str, session_id: str, keep_ids: list[str]
) -> int:
    """Delete this session's surfaces not in `keep_ids`. Returns the count.

    The counterpart to prune_zones, for the same reason: a touchpoint the
    operator removed must stop appearing in the report rather than linger with
    a stale trigger count nobody can account for.
    """
    result = await session.run(
        """
        MATCH (s:Surface {tenant_id: $tenant_id, session_id: $session_id})
        WHERE NOT s.id IN $keep_ids
        DETACH DELETE s
        RETURN count(s) AS deleted
        """,
        tenant_id=tenant_id,
        session_id=session_id,
        keep_ids=keep_ids,
    )
    record = await result.single()
    return record["deleted"]


async def session_config(
    session: AsyncSession, *, tenant_id: str, session_id: str
) -> dict[str, Any] | None:
    """A Session node's properties, or None if it was never created.

    None rather than an empty dict on purpose: "this session does not exist" and
    "this session exists with nothing set" are different answers, and the report
    must be able to tell them apart before it claims a number.
    """
    result = await session.run(
        """
        MATCH (s:Session {tenant_id: $tenant_id, id: $session_id})
        RETURN s
        """,
        tenant_id=tenant_id,
        session_id=session_id,
    )
    record = await result.single()
    return dict(record["s"]) if record is not None else None


async def prune_zones(
    session: AsyncSession, *, tenant_id: str, session_id: str, keep_ids: list[str]
) -> int:
    """Delete this session's zones that are not in `keep_ids`. Returns the count.

    The session wizard posts the whole zone set, so a zone the operator deleted
    has to actually go. Without this it survives as an orphan the tracker keeps
    scoring detections against — dwell attributed to a zone that is no longer on
    anybody's screen, which is worse than no zone at all because it still shows
    up in the report.

    DETACH so the ENTERED/DWELLED_IN edges go with it. That does destroy history
    for the removed zone, which is the correct reading of "the operator deleted
    this zone": a report cannot show dwell in a zone that the client is told
    does not exist.
    """
    result = await session.run(
        """
        MATCH (z:Zone {tenant_id: $tenant_id, session_id: $session_id})
        WHERE NOT z.id IN $keep_ids
        DETACH DELETE z
        RETURN count(z) AS deleted
        """,
        tenant_id=tenant_id,
        session_id=session_id,
        keep_ids=keep_ids,
    )
    record = await result.single()
    return record["deleted"]


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
