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

import datetime as dt
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
    camera_id: str | None = None,
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

    `camera_id` is which camera's frame the polygon was drawn in — the polygon
    is normalized within one frame, so the same numbers mean different floor in
    a different camera. None means every camera, which is what every zone drawn
    before multi-camera existed is.
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
            z.camera_id    = $camera_id,
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
        camera_id=camera_id,
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


#: Every scalar property a Session node carries, with the value a *new* session
#: gets when the caller says nothing. A create fills these in; an update leaves
#: out whatever the caller did not mention. See `upsert_session`.
SESSION_DEFAULTS: dict[str, Any] = {
    "client": None,
    "campaign": None,
    "venue": None,
    "city": None,
    "started_at": None,
    "ends_at": None,
    "booth_width_m": None,
    "booth_depth_m": None,
    "camera_count": None,
    "engaged_threshold_seconds": 60.0,
    "activation_cost": None,
    "currency": "USD",
    "attribution_model": "influenced",
    "attribution_window_days": 90,
    "revenue_influenced": None,
    "qualified_leads": None,
    "anonymous_handoffs": False,
    "insight_interval_minutes": 10,
}


async def upsert_session(
    session: AsyncSession,
    *,
    tenant_id: str,
    session_id: str,
    props: dict[str, Any] | None = None,
    **fields: Any,
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

    ## `props` carries only what the caller actually set

    This used to take one keyword per property and `SET` all eighteen on every
    call, so a partial update **nulled everything it did not mention**. That is
    not a hypothetical: `dashboard/src/lib/ops/useCalibration.ts` posts
    `{sessionId, cameras}` to declare a camera and `{sessionId, zones}` to
    assign one — so declaring a camera mid-activation silently erased the
    activation cost the ROI ratio divides by, the engagement threshold the
    engagement rate counts against, the attribution model, the client name and
    the dates. The report kept rendering, with the numbers gone. Found by the
    Phase 6 acceptance run.

    `SET s += $props` is what fixes it, and it gives the right answer to the
    question the old shape could not even ask. A key **absent** from the map is
    left alone; a key present and null is **removed**, which is how an operator
    clears a cost they had entered by mistake. The router builds the map from
    Pydantic's `model_fields_set`, so "omitted" and "explicitly null" stay
    different all the way down.

    On create, `SESSION_DEFAULTS` fills the rest — a session with no
    `currency` or no `engaged_threshold_seconds` would divide by nothing.
    """
    # Two spellings of one thing. `props` is the router's, built from the fields
    # a request actually set; the keyword form is the ergonomic one for
    # consumers and tests, where naming a property *is* setting it.
    props = {**(props or {}), **fields}

    unknown = set(props) - set(SESSION_DEFAULTS)
    if unknown:
        # A typo'd key would otherwise be written as a property nothing reads,
        # and the field it was meant to be would keep its old value — an update
        # that reports success and changes nothing.
        raise ValueError(f"not session properties: {', '.join(sorted(unknown))}")
    result = await session.run(
        """
        MERGE (s:Session {tenant_id: $tenant_id, id: $session_id})
        ON CREATE SET s += $defaults
        SET s += $props
        RETURN s
        """,
        tenant_id=tenant_id,
        session_id=session_id,
        # Applied before `$props` so an explicit value in the same call wins.
        defaults=SESSION_DEFAULTS,
        props=props,
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

    `camera_id` comes back for the same reason and is **not** filtered here.
    The tracker holds one cached zone set per session and narrows it per
    detection in memory (`consumers/tracker.py`), which is one round trip
    instead of one per camera on the <500ms path — and one cache to invalidate
    when an operator redraws, rather than a set of them that can disagree.
    """
    result = await session.run(
        """
        MATCH (z:Zone {tenant_id: $tenant_id, session_id: $session_id})
        WHERE $include_undrawn OR z.polygon IS NOT NULL
        RETURN z.id           AS id,
               z.name         AS name,
               z.type         AS type,
               z.polygon      AS polygon,
               z.camera_id    AS camera_id,
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
                # None, not [], for a zone nobody has drawn yet. An empty list
                # is a polygon with no points, and `ZoneConfig` rejects that on
                # sight — correctly, since it would contain nobody. So the
                # config endpoint, whose entire reason for passing
                # include_undrawn=True is to show the operator a half-created
                # zone, could not serialize one: the write succeeded and then
                # building the response raised, which the browser saw as a 500
                # and reported as "the bus is unreachable". Absent geometry is
                # null; [] would be a claim about a shape.
                "polygon": (
                    [[flat[i], flat[i + 1]] for i in range(0, len(flat) - 1, 2)]
                    if flat
                    else None
                ),
                # None for every zone drawn before cameras had ids, and for a
                # zone an operator deliberately left unowned. Both mean the
                # same thing to the tracker: score it against any camera.
                "camera_id": record["camera_id"],
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


async def sessions_for_tenant(
    session: AsyncSession, *, tenant_id: str, limit: int = 50
) -> list[dict[str, Any]]:
    """This tenant's activations, newest first. A listing, not a report.

    Deliberately carries **no measured figures** — no visitor count, no dwell,
    no engagement rate. `routers/sessions.py` states the rule this obeys: the
    four ROI layers are defined once, in `dashboard/src/lib/roi/scorecard.ts`,
    and computing any of them here in a second language would give the product
    two definitions of "engagement rate" with nothing to notice when they stop
    agreeing. What comes back is what an operator typed in and what the wizard
    stored — enough to say which activations exist and what each one was scored
    against.

    That is what the benchmark needs. Comparing this activation to the client's
    own history means running the *same* scorecard over each earlier session's
    log, and the only thing missing before this was knowing which sessions
    there were.

    Ordered by `started_at` descending, nulls last: a session created but never
    dated is real and belongs in the list, but it is not the most recent thing
    that happened. Bounded, because a tenant who runs weekly for two years has
    a hundred of these and a report wants the last handful.

    The plan's retention window is **not** applied here. `started_at` is a
    string on the node (see `upsert_session`), so a Cypher comparison against a
    datetime is a silent wrong answer and `datetime(s.started_at)` throws on a
    malformed one. `routers/sessions.py` filters the result instead, where an
    unparseable date can keep its session rather than break a listing.
    """
    result = await session.run(
        """
        MATCH (s:Session {tenant_id: $tenant_id})
        RETURN s.id                        AS id,
               s.client                    AS client,
               s.campaign                  AS campaign,
               s.venue                     AS venue,
               s.city                      AS city,
               s.started_at                AS started_at,
               s.ends_at                   AS ends_at,
               s.engaged_threshold_seconds AS engaged_threshold_seconds,
               s.activation_cost           AS activation_cost,
               s.currency                  AS currency,
               s.revenue_influenced        AS revenue_influenced,
               s.qualified_leads           AS qualified_leads
        ORDER BY s.started_at IS NULL, s.started_at DESC, s.id
        LIMIT $limit
        """,
        tenant_id=tenant_id,
        limit=limit,
    )
    return [dict(record) async for record in result]


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


# ── Camera (graph migration 005) ─────────────────────────────────────────────
#
# A camera exists for two reasons: `drift.detected` and `calibration.updated`
# are both keyed on a `camera_id` that previously had nowhere to live, and
# `privacy.md` §"Sensitive zones" promises a per-camera opt-out polygon whose
# pixels are masked before any model runs.
#
# The polygon is stored flattened, [x1,y1,x2,y2,…], for the same reason zones
# are: Neo4j cannot hold a nested list as a property. Callers never see that
# shape — every read here rebuilds pairs.


async def upsert_camera(
    session: AsyncSession,
    *,
    tenant_id: str,
    session_id: str,
    camera_id: str,
    label: str | None = None,
) -> dict[str, Any]:
    """Create or update a Camera. Deliberately does **not** touch the mask.

    This is the session-config path — the wizard declaring which cameras a booth
    has. The mask is set by `set_privacy_mask` and nothing else.

    Keeping the two apart is not tidiness. The wizard posts its whole camera set
    on every save, so if this wrote `privacy_mask` a save that omitted it would
    clear a mask an operator had drawn, and the next frame would go to the model
    unmasked. Silent, and a privacy failure rather than a lost setting.
    """
    result = await session.run(
        """
        MERGE (c:Camera {tenant_id: $tenant_id, session_id: $session_id, id: $camera_id})
        ON CREATE SET c.mask_revision = 0
        SET c.label = coalesce($label, c.label, $camera_id)
        RETURN c
        """,
        tenant_id=tenant_id,
        session_id=session_id,
        camera_id=camera_id,
        label=label,
    )
    record = await result.single()
    return dict(record["c"])


async def set_privacy_mask(
    session: AsyncSession,
    *,
    tenant_id: str,
    session_id: str,
    camera_id: str,
    polygon: list[list[float]] | None,
    updated_at: dt.datetime,
) -> int:
    """Set (or clear) a camera's opt-out polygon. Returns the new revision.

    The increment is **inside the same Cypher statement** as the write, for the
    reason `identify` puts its consent gate there: a read-then-write in Python
    is two round trips, and two operators saving a mask at once would both read
    revision 3 and both write revision 4. Perception decides whether its cached
    mask is stale by comparing revisions, so a duplicated revision means an edge
    box holding the older of two masks and believing it is current.

    `polygon=None` clears the mask and still increments — removing a mask is a
    calibration change like any other, and an edge box has to learn about it.
    """
    flat = None if polygon is None else [c for point in polygon for c in point]

    result = await session.run(
        """
        MERGE (c:Camera {tenant_id: $tenant_id, session_id: $session_id, id: $camera_id})
        ON CREATE SET c.label = $camera_id
        SET c.privacy_mask   = $polygon,
            c.mask_revision  = coalesce(c.mask_revision, 0) + 1,
            c.mask_updated_at = $updated_at
        RETURN c.mask_revision AS revision
        """,
        tenant_id=tenant_id,
        session_id=session_id,
        camera_id=camera_id,
        polygon=flat,
        updated_at=updated_at,
    )
    record = await result.single()
    return int(record["revision"])


def _unflatten(flat: list[float] | None) -> list[list[float]] | None:
    """[x1,y1,x2,y2,…] → [[x1,y1],[x2,y2],…]. None stays None.

    None rather than [] for a camera with no mask, matching the reasoning on
    `zones_for_session`'s polygon: an empty list is a claim about a shape, and
    a mask with no points would be a mask that masks nothing — which is exactly
    the same on-screen as "no mask", and must not be.
    """
    if not flat:
        return None
    return [[flat[i], flat[i + 1]] for i in range(0, len(flat) - 1, 2)]


async def max_cameras_for_tenant(session: AsyncSession, *, tenant_id: str) -> int:
    """The most cameras any one of this tenant's activations declares.

    The plan limit is per activation — `gtm.md` sells "a single camera kit", not
    one camera per organisation for ever — so a client who ran a one-camera
    booth in March and another in April is inside Booth, and a total would say
    otherwise.

    Both halves are counted for the reason `put_session_config` checks both:
    `camera_count` is the integer the twin and the drift panel read, the
    `(:Camera)` nodes are what zones are keyed on, and a session that set one
    and not the other is ordinary.
    """
    result = await session.run(
        """
        MATCH (s:Session {tenant_id: $tenant_id})
        OPTIONAL MATCH (c:Camera {tenant_id: $tenant_id, session_id: s.id})
        WITH s, count(c) AS declared
        RETURN coalesce(max(
            CASE WHEN coalesce(s.camera_count, 0) > declared
                 THEN s.camera_count ELSE declared END
        ), 0) AS most
        """,
        tenant_id=tenant_id,
    )
    record = await result.single()
    return int(record["most"]) if record else 0


async def cameras_for_session(
    session: AsyncSession, *, tenant_id: str, session_id: str
) -> list[dict[str, Any]]:
    """Every camera declared for a session, with its mask and revision."""
    result = await session.run(
        """
        MATCH (c:Camera {tenant_id: $tenant_id, session_id: $session_id})
        RETURN c.id            AS id,
               c.label         AS label,
               c.privacy_mask  AS privacy_mask,
               c.mask_revision AS mask_revision
        ORDER BY c.id
        """,
        tenant_id=tenant_id,
        session_id=session_id,
    )
    cameras: list[dict[str, Any]] = []
    async for record in result:
        cameras.append(
            {
                "id": record["id"],
                "label": record["label"] or record["id"],
                "privacy_mask": _unflatten(record["privacy_mask"]),
                "mask_revision": record["mask_revision"] or 0,
            }
        )
    return cameras


async def camera_mask(
    session: AsyncSession, *, tenant_id: str, session_id: str, camera_id: str
) -> dict[str, Any] | None:
    """One camera's mask, for perception to fetch. None if no such camera.

    The distinction between None here and a camera whose `privacy_mask` is None
    is the one perception acts on: no camera declared is a misconfiguration an
    operator has to fix, while a declared camera with no mask is a booth that
    genuinely has no sensitive surface. Collapsing them would make a typo in
    `--camera-id` look like a deliberate decision not to mask.
    """
    result = await session.run(
        """
        MATCH (c:Camera {tenant_id: $tenant_id, session_id: $session_id, id: $camera_id})
        RETURN c.privacy_mask  AS privacy_mask,
               c.mask_revision AS mask_revision
        """,
        tenant_id=tenant_id,
        session_id=session_id,
        camera_id=camera_id,
    )
    record = await result.single()
    if record is None:
        return None
    return {
        "polygon": _unflatten(record["privacy_mask"]),
        "revision": record["mask_revision"] or 0,
    }


async def prune_cameras(
    session: AsyncSession, *, tenant_id: str, session_id: str, keep_ids: list[str]
) -> int:
    """Delete this session's cameras that are not in `keep_ids`. Returns the count.

    Same contract as `prune_zones` — the wizard posts the whole set — with one
    consequence worth stating: deleting a camera deletes its mask. An operator
    who removes a camera and adds it back has to redraw, and that is the right
    failure direction. The alternative is a mask surviving invisibly and being
    reapplied to a camera someone has since repointed at a different wall.
    """
    result = await session.run(
        """
        MATCH (c:Camera {tenant_id: $tenant_id, session_id: $session_id})
        WHERE NOT c.id IN $keep_ids
        DETACH DELETE c
        RETURN count(c) AS deleted
        """,
        tenant_id=tenant_id,
        session_id=session_id,
        keep_ids=keep_ids,
    )
    record = await result.single()
    return record["deleted"]


# ── Group (graph migration 006) ──────────────────────────────────────────────
#
# Co-visiting people. `data-model.md` has specified this node since the start
# and `schema.py` has constrained it since migration 001 — with a comment saying
# it "is used by GROUP_MEMBER_OF and the 'Groups in Lounge' query in
# data-model.md but was never declared". Nothing wrote one until `consumers/
# grouping.py`, so that documented example query returned nothing for the whole
# life of the project.


async def upsert_group(
    session: AsyncSession,
    *,
    tenant_id: str,
    session_id: str,
    group_id: str,
    size: int,
    cohesion: float,
    first_seen: str,
    last_seen: str,
) -> dict[str, Any]:
    """Create or update a Group (data-model.md → `(:Group {...})`).

    `first_seen` is written only on create. It is the founding moment the group
    id itself derives from, so letting a later event move it would make the id
    stop matching the node it names — and every subsequent event for that group
    would derive a different id and create a second node.
    """
    result = await session.run(
        """
        MERGE (g:Group {tenant_id: $tenant_id, session_id: $session_id, id: $group_id})
        ON CREATE SET g.first_seen = $first_seen
        SET g.size      = $size,
            g.cohesion  = $cohesion,
            g.last_seen = $last_seen
        RETURN g
        """,
        tenant_id=tenant_id,
        session_id=session_id,
        group_id=group_id,
        size=size,
        cohesion=cohesion,
        first_seen=first_seen,
        last_seen=last_seen,
    )
    record = await result.single()
    return dict(record["g"])


async def set_group_members(
    session: AsyncSession,
    *,
    tenant_id: str,
    session_id: str,
    group_id: str,
    members: list[str],
) -> int:
    """Make the group's membership exactly `members`. Returns how many are linked.

    **Replaces rather than appends**, which is the whole reason this is not a
    loop of MERGEs at the call site. A `changed` event carries the membership as
    it now stands, so somebody who left has to actually lose their edge — an
    append-only writer would leave them in the group forever, and `size` would
    disagree with the number of edges under it.

    Both endpoints are matched with tenant *and* session, so an edge can never
    be drawn across either. A member with no `Person` node yet is skipped rather
    than created: the graph writer builds people from `perception.detection`,
    and inventing one here would produce a person with no position, no zone and
    no history who exists only because they were near somebody.
    """
    await session.run(
        """
        MATCH (g:Group {tenant_id: $tenant_id, session_id: $session_id, id: $group_id})
              <-[r:GROUP_MEMBER_OF]-(:Person)
        WHERE NOT r.anon_id IN $members
        DELETE r
        """,
        tenant_id=tenant_id,
        session_id=session_id,
        group_id=group_id,
        members=members,
    )
    result = await session.run(
        """
        MATCH (g:Group  {tenant_id: $tenant_id, session_id: $session_id, id: $group_id})
        MATCH (p:Person {tenant_id: $tenant_id, session_id: $session_id})
        WHERE p.anon_id IN $members
        MERGE (p)-[r:GROUP_MEMBER_OF]->(g)
        SET r.anon_id = p.anon_id
        RETURN count(r) AS linked
        """,
        tenant_id=tenant_id,
        session_id=session_id,
        group_id=group_id,
        members=members,
    )
    record = await result.single()
    return int(record["linked"])


async def groups_for_session(
    session: AsyncSession, *, tenant_id: str, session_id: str
) -> list[dict[str, Any]]:
    """Every group in a session, with its members. Ordered by size then id.

    Written for the tests and for whatever reads groups next; nothing on the
    dashboard consumes it yet, and it is small enough that adding it now costs
    less than the round trip of discovering it missing.
    """
    result = await session.run(
        """
        MATCH (g:Group {tenant_id: $tenant_id, session_id: $session_id})
        OPTIONAL MATCH (p:Person)-[:GROUP_MEMBER_OF]->(g)
        WITH g, collect(p.anon_id) AS members
        RETURN g.id         AS id,
               g.size       AS size,
               g.cohesion   AS cohesion,
               g.first_seen AS first_seen,
               g.last_seen  AS last_seen,
               members
        ORDER BY g.size DESC, g.id
        """,
        tenant_id=tenant_id,
        session_id=session_id,
    )
    groups: list[dict[str, Any]] = []
    async for record in result:
        groups.append(
            {
                "id": record["id"],
                "size": record["size"],
                "cohesion": record["cohesion"],
                "first_seen": record["first_seen"],
                "last_seen": record["last_seen"],
                "members": sorted(m for m in record["members"] if m),
            }
        )
    return groups


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


# ── consent and identity (Phase 4) ────────────────────────────────────────────
#
# The one part of this module where a *failure* to write is the safe outcome.
# Everything above records what a camera saw; these record that somebody agreed
# to be named, and the edge below is the only thing in the system that connects
# an anonymous track to a person. `consent-and-identity.md` §3: "the
# IDENTIFIED_AS edge cannot be created unless a non-withdrawn ConsentEvent of the
# required tier exists in the same transaction. This is enforced in code (the bus
# consumer), not by convention."
#
# It is enforced here instead, one layer lower than the doc says, and deliberately
# so: a consumer that forgot the check would still be able to draw the edge, and
# the check is worth nothing if it can be skipped by writing a second caller.
# Below, the MATCH on the consent *is* the gate — no consent of the right tier,
# no rows, no edge, no exception. The caller is told what happened by the return
# value rather than by a promise it kept.


async def upsert_consent_event(
    session: AsyncSession,
    *,
    tenant_id: str,
    consent_id: str,
    tier: str,
    basis: str,
    copy_version: str,
    captured_at: str,
    captured_by: str,
    source: str,
    anon_id: str,
    session_id: str,
    expires_at: str | None = None,
) -> dict[str, Any]:
    """Create or update the ConsentEvent, and point it at what it covers.

    `PERMITS` (ConsentEvent → Person) is the scope of the permission, per
    `consent-and-identity.md` §3. It is drawn here rather than with the
    `IDENTIFIED_AS` edge because it is true whether or not a Contact is ever
    created: a T1 consent with no details yet still covers this person.

    `withdrawn_at` is never set by this function, only read. A capture cannot
    un-withdraw a consent — replaying the log after a withdrawal must not resurrect
    the permission, and an ON CREATE/SET that touched it would do exactly that on
    the next replay.
    """
    result = await session.run(
        """
        MERGE (c:ConsentEvent {tenant_id: $tenant_id, id: $consent_id})
        ON CREATE SET c.captured_at = $captured_at
        SET c.tier         = $tier,
            c.basis        = $basis,
            c.copy_version = $copy_version,
            c.captured_by  = $captured_by,
            c.source       = $source,
            c.session_id   = $session_id,
            c.anon_id      = $anon_id,
            c.expires_at   = $expires_at
        WITH c
        OPTIONAL MATCH (p:Person {tenant_id: $tenant_id, session_id: $session_id,
                                  anon_id: $anon_id})
        FOREACH (_ IN CASE WHEN p IS NULL THEN [] ELSE [1] END |
            MERGE (c)-[:PERMITS]->(p)
        )
        RETURN c
        """,
        tenant_id=tenant_id,
        consent_id=consent_id,
        tier=tier,
        basis=basis,
        copy_version=copy_version,
        captured_at=captured_at,
        captured_by=captured_by,
        source=source,
        session_id=session_id,
        anon_id=anon_id,
        expires_at=expires_at,
    )
    record = await result.single()
    return dict(record["c"])


async def identify(
    session: AsyncSession,
    *,
    tenant_id: str,
    session_id: str,
    anon_id: str,
    contact_id: str,
    consent_id: str,
    minimum_tier: str,
    via: str,
    at: str,
    email: str | None = None,
    name: str | None = None,
    company: str | None = None,
    title: str | None = None,
) -> dict[str, Any] | None:
    """Create the Contact and link it to the anonymous track — **if** consent allows.

    Returns the Contact's properties, or **None** when the gate refused. None is
    the interesting answer and callers must handle it: it means a consent event
    of at least `minimum_tier`, not withdrawn, does not exist for this id.

    ## Why the gate is one statement and not an `if`

    Reading the consent, deciding, and then writing would leave a window in which
    a withdrawal lands between the two — and the write would proceed on a consent
    that no longer exists. The MATCH below is inside the same statement as the
    MERGE, so there is no gap: Neo4j either finds a valid consent and creates the
    edge, or finds nothing and returns no rows.

    ## The tier comparison

    Tiers are a strict superset chain (`consent-and-identity.md` §2: "each tier
    is a strict superset of the one above"), so `T1 <= T2 <= T3` is a string
    comparison over `'T1' | 'T2' | 'T3'` and stays correct as long as the labels
    keep sorting in permission order. A fourth tier named `T0` would break that
    silently, which is why the tier vocabulary is a closed union at the API.
    """
    result = await session.run(
        """
        MATCH (c:ConsentEvent {tenant_id: $tenant_id, id: $consent_id})
        WHERE c.withdrawn_at IS NULL AND c.tier >= $minimum_tier
        MATCH (p:Person {tenant_id: $tenant_id, session_id: $session_id, anon_id: $anon_id})
        MERGE (ct:Contact {tenant_id: $tenant_id, id: $contact_id})
        ON CREATE SET ct.created_at = $at, ct.source = $via
        SET ct.email   = coalesce($email, ct.email),
            ct.name    = coalesce($name, ct.name),
            ct.company = coalesce($company, ct.company),
            ct.title   = coalesce($title, ct.title)
        MERGE (p)-[:IDENTIFIED_AS {via: $via, at: $at}]->(ct)
        MERGE (ct)-[:GRANTED]->(c)
        RETURN ct
        """,
        tenant_id=tenant_id,
        session_id=session_id,
        anon_id=anon_id,
        contact_id=contact_id,
        consent_id=consent_id,
        minimum_tier=minimum_tier,
        via=via,
        at=at,
        email=email,
        name=name,
        company=company,
        title=title,
    )
    record = await result.single()
    return dict(record["ct"]) if record else None


async def spatial_intent_for(
    session: AsyncSession, *, tenant_id: str, session_id: str, anon_id: str
) -> dict[str, Any]:
    """One person's whole path, as the raw rows a LeadHandoff is assembled from.

    `integrations.md` §2 calls `spatial_intent` "the realmspace differentiator" —
    it is the part of a lead no CRM could have known. This returns the
    measurements; `app/attribution/spatial_intent.py` turns them into the block,
    because the shaping is arithmetic and belongs somewhere it can be tested
    without a database.

    ## Why two collections in one query

    Dwells and surface interactions are separate patterns, and matching both in
    one MATCH would give their cross product — a visitor with three dwells and
    two surfaces would report six of each, and the total dwell in a lead's
    handoff would be tripled. Collected in separate subqueries instead, which is
    the shape that returns each edge once.

    `weight` and `funnel_order` travel with the dwells because the two numbers
    the handoff needs are defined in terms of them: weighted attention is
    `Σ(dwell × weight)` per `roi-framework.md` §2, and funnel depth is the
    furthest `funnel_order` the person reached.
    """
    result = await session.run(
        """
        MATCH (p:Person {tenant_id: $tenant_id, session_id: $session_id, anon_id: $anon_id})
        CALL (p) {
            MATCH (p)-[d:DWELLED_IN]->(z:Zone)
            RETURN collect({
                zone_id:      z.id,
                zone:         z.name,
                duration:     d.duration,
                started_at:   d.started_at,
                weight:       z.weight,
                funnel_order: z.funnel_order
            }) AS dwells
        }
        CALL (p) {
            MATCH (p)-[i:INTERACTED_WITH]->(s:Surface)
            RETURN collect({
                surface_id: s.id,
                label:      s.label,
                kind:       i.kind,
                duration:   i.duration,
                at:         i.at
            }) AS surfaces
        }
        RETURN dwells, surfaces, p.first_seen AS first_seen, p.last_seen AS last_seen
        """,
        tenant_id=tenant_id,
        session_id=session_id,
        anon_id=anon_id,
    )
    record = await result.single()
    if record is None:
        # No Person at all. An empty path rather than None: the caller is
        # building a handoff for somebody the identity consumer already linked,
        # so "this person has walked nowhere yet" is the honest reading and it
        # produces a handoff with an empty spatial_intent rather than no handoff.
        return {"dwells": [], "surfaces": [], "first_seen": None, "last_seen": None}
    return {
        "dwells": list(record["dwells"]),
        "surfaces": list(record["surfaces"]),
        "first_seen": record["first_seen"],
        "last_seen": record["last_seen"],
    }


async def contacts_in_session(
    session: AsyncSession, *, tenant_id: str, session_id: str
) -> list[dict[str, Any]]:
    """Every live identification in a session — what `session.ended` fans out over.

    Only live ones: the match requires the `IDENTIFIED_AS` edge, which the
    re-anonymiser deletes on withdrawal. Somebody who consented and then changed
    their mind is simply not here, which is the behaviour the final handoff
    wants and the reason this is a graph read rather than a scan of the log.
    """
    result = await session.run(
        """
        MATCH (p:Person {tenant_id: $tenant_id, session_id: $session_id})
              -[:IDENTIFIED_AS]->(ct:Contact)
        OPTIONAL MATCH (ct)-[:GRANTED]->(c:ConsentEvent)
        WHERE c.withdrawn_at IS NULL
        RETURN p.anon_id AS anon_id, ct.id AS contact_id, ct.email AS email
        ORDER BY p.anon_id
        """,
        tenant_id=tenant_id,
        session_id=session_id,
    )
    return [dict(record) async for record in result]


async def unidentified_in_session(
    session: AsyncSession, *, tenant_id: str, session_id: str
) -> list[str]:
    """Everyone in a session who is nobody — the anonymous handoff's population.

    `integrations.md` §2 allows a handoff with no `contact`, "still valid …
    carrying spatial_intent for aggregate ROI". `contacts_in_session` is the
    other half of the room; this is the rest of it.

    The match is the absence of a live `IDENTIFIED_AS` edge, which makes two
    cases one: somebody who never consented, and somebody who consented and then
    withdrew. That is right rather than convenient — a withdrawal returns a
    person to the anonymous path, and `reanonymise.py` argues at length that the
    anonymous path was never consent-gated and survives untouched. A withdrawn
    visitor's dwell still counts toward reach, exactly as it did before they gave
    their name.
    """
    result = await session.run(
        """
        MATCH (p:Person {tenant_id: $tenant_id, session_id: $session_id})
        WHERE NOT (p)-[:IDENTIFIED_AS]->(:Contact)
        RETURN p.anon_id AS anon_id
        ORDER BY p.anon_id
        """,
        tenant_id=tenant_id,
        session_id=session_id,
    )
    return [record["anon_id"] async for record in result]


async def consent_for_contact(
    session: AsyncSession, *, tenant_id: str, contact_id: str
) -> dict[str, Any] | None:
    """The live consent a contact granted, or None if it was withdrawn.

    The handoff carries a `consent` block because `integrations.md` §2 makes it
    "required for any PII emission" — a destination receiving a name has to be
    able to see what permitted it without trusting that we checked.
    """
    result = await session.run(
        """
        MATCH (ct:Contact {tenant_id: $tenant_id, id: $contact_id})-[:GRANTED]->(c:ConsentEvent)
        WHERE c.withdrawn_at IS NULL
        RETURN c
        ORDER BY c.captured_at DESC
        LIMIT 1
        """,
        tenant_id=tenant_id,
        contact_id=contact_id,
    )
    record = await result.single()
    return dict(record["c"]) if record else None


async def upsert_outcome(
    session: AsyncSession,
    *,
    tenant_id: str,
    outcome_id: str,
    dedupe_key: str,
    stage: str,
    value: float | None,
    currency: str,
    closed_at: str | None,
    source: str,
    external_ref: str | None = None,
) -> dict[str, Any]:
    """Create or update an Outcome, and link it to the Contact it belongs to.

    The link is drawn by matching `dedupe_key` against the contact's own — the
    same key `handoff.lead` carried out to the destination, which is what lets an
    outcome name its lead in the vocabulary the CRM already speaks rather than in
    an internal id it never saw.

    ## The link is optional, and the node is not

    `OPTIONAL MATCH`: an outcome whose contact has since been re-anonymised, or
    which arrives before the handoff it refers to, is still recorded. Refusing it
    would lose a real deal because of an ordering accident, and the ledger reads
    the log rather than this node anyway — this is the queryable copy, not the
    record.
    """
    result = await session.run(
        """
        MERGE (o:Outcome {tenant_id: $tenant_id, id: $outcome_id})
        SET o.dedupe_key   = $dedupe_key,
            o.stage        = $stage,
            o.value        = $value,
            o.currency     = $currency,
            o.closed_at    = $closed_at,
            o.source       = $source,
            o.external_ref = $external_ref
        WITH o
        OPTIONAL MATCH (ct:Contact {tenant_id: $tenant_id})
        WHERE ct.dedupe_key = $dedupe_key
        FOREACH (_ IN CASE WHEN ct IS NULL THEN [] ELSE [1] END |
            MERGE (ct)-[:RESULTED_IN]->(o)
        )
        RETURN o
        """,
        tenant_id=tenant_id,
        outcome_id=outcome_id,
        dedupe_key=dedupe_key,
        stage=stage,
        value=value,
        currency=currency,
        closed_at=closed_at,
        source=source,
        external_ref=external_ref,
    )
    record = await result.single()
    return dict(record["o"])


async def set_contact_dedupe_key(
    session: AsyncSession, *, tenant_id: str, contact_id: str, dedupe_key: str
) -> None:
    """Record on the Contact the key its handoffs went out under.

    Written by the attribution consumer when it builds a handoff, so an outcome
    arriving later can find its contact. Without it the only place the mapping
    exists is inside handoff payloads on the log, and the graph could not answer
    "which deals came from this person" at all.
    """
    await session.run(
        """
        MATCH (ct:Contact {tenant_id: $tenant_id, id: $contact_id})
        SET ct.dedupe_key = $dedupe_key
        """,
        tenant_id=tenant_id,
        contact_id=contact_id,
        dedupe_key=dedupe_key,
    )


async def person_exists(
    session: AsyncSession, *, tenant_id: str, session_id: str, anon_id: str
) -> bool:
    """Whether the tracked person a consent names is in the graph yet.

    Exists so the identity consumer can tell two failures apart. `identify`
    returns None for both "consent does not permit this" and "there is no such
    Person", and they call for opposite responses: the first is a decision to
    respect, the second is usually the graph writer being a poll behind and is
    worth retrying.
    """
    result = await session.run(
        """
        MATCH (p:Person {tenant_id: $tenant_id, session_id: $session_id, anon_id: $anon_id})
        RETURN count(p) > 0 AS found
        """,
        tenant_id=tenant_id,
        session_id=session_id,
        anon_id=anon_id,
    )
    record = await result.single()
    return bool(record["found"])


async def contact_for_anon(
    session: AsyncSession, *, tenant_id: str, session_id: str, anon_id: str
) -> dict[str, Any] | None:
    """The Contact an anonymous track was identified as, if any."""
    result = await session.run(
        """
        MATCH (p:Person {tenant_id: $tenant_id, session_id: $session_id, anon_id: $anon_id})
              -[:IDENTIFIED_AS]->(ct:Contact)
        RETURN ct
        """,
        tenant_id=tenant_id,
        session_id=session_id,
        anon_id=anon_id,
    )
    record = await result.single()
    return dict(record["ct"]) if record else None


async def re_anonymise(
    session: AsyncSession,
    *,
    tenant_id: str,
    withdrawn_at: str,
    consent_id: str | None = None,
    contact_id: str | None = None,
    anon_id: str | None = None,
    session_id: str | None = None,
) -> list[str]:
    """Undo an identification. Returns the contact ids affected.

    `consent-and-identity.md` §5: "a bus consumer re-anonymises: drops the
    `IDENTIFIED_AS` edge, deletes/redacts the `Contact` per policy … The
    anonymous path survives for aggregate ROI."

    That last sentence is the constraint that shapes this whole function. The
    Person, its zone edges, its dwells — none of it is touched. It was never
    consent-gated in the first place (`privacy.md`), and deleting it would
    silently change every report already delivered from that activation. What is
    removed is the *link* and the PII, which is all consent ever granted.

    The ConsentEvent survives too, stamped with `withdrawn_at`. It is the
    evidence: a record that consent was given, and then withdrawn, and when. A
    deployment that deleted it would be unable to answer the question the
    withdrawal itself might later raise.

    Matched by whichever identifier the caller has, because a withdrawal arrives
    from a kiosk that knows only the track, an operator who knows the contact, or
    an erasure request that names the consent.
    """
    result = await session.run(
        """
        MATCH (ct:Contact {tenant_id: $tenant_id})
        OPTIONAL MATCH (p:Person)-[link:IDENTIFIED_AS]->(ct)
        OPTIONAL MATCH (ct)-[:GRANTED]->(c:ConsentEvent)
        WITH ct, p, link, c
        WHERE ($contact_id IS NOT NULL AND ct.id = $contact_id)
           OR ($consent_id IS NOT NULL AND c.id = $consent_id)
           OR ($anon_id    IS NOT NULL AND p.anon_id = $anon_id
               AND ($session_id IS NULL OR p.session_id = $session_id))
        // The consent stays, stamped. It is the evidence that permission was
        // given and then taken back — deleting it would leave nothing able to
        // answer the question a withdrawal might later raise.
        FOREACH (_ IN CASE WHEN c IS NULL THEN [] ELSE [1] END |
            SET c.withdrawn_at = $withdrawn_at
        )
        // The PII goes; the node stays as a tombstone so the ids in an already
        // pushed CRM record still resolve to something that says "retracted".
        SET ct.email = NULL, ct.name = NULL, ct.company = NULL, ct.title = NULL,
            ct.redacted_at = $withdrawn_at
        DELETE link
        RETURN DISTINCT ct.id AS contact_id
        """,
        tenant_id=tenant_id,
        withdrawn_at=withdrawn_at,
        consent_id=consent_id,
        contact_id=contact_id,
        anon_id=anon_id,
        session_id=session_id,
    )
    return [record["contact_id"] async for record in result]


async def withdraw_consent(
    session: AsyncSession, *, tenant_id: str, consent_id: str, withdrawn_at: str
) -> bool:
    """Stamp a ConsentEvent withdrawn even though no Contact was ever created.

    The T1-with-no-details case: somebody agreed, nothing was ever linked, and
    they changed their mind. `re_anonymise` matches from the Contact and so finds
    nothing to do here — but the consent must still stop being valid, or a replay
    of the capture would happily identify them afterwards.
    """
    result = await session.run(
        """
        MATCH (c:ConsentEvent {tenant_id: $tenant_id, id: $consent_id})
        SET c.withdrawn_at = coalesce(c.withdrawn_at, $withdrawn_at)
        RETURN c.id AS id
        """,
        tenant_id=tenant_id,
        consent_id=consent_id,
        withdrawn_at=withdrawn_at,
    )
    return (await result.single()) is not None


async def upsert_insight(
    session: AsyncSession,
    *,
    tenant_id: str,
    session_id: str,
    insight_id: str,
    text: str,
    generated_by: str,
    timestamp: str,
    confidence: float | None = None,
    about_zone_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Create or update an Insight (data-model.md → `(:Insight {...})`).

    `data-model.md`'s opening line is that "every LLM-generated insight is
    persisted as a first-class node in the same graph", and `graph/schema.py` has
    carried the key constraint since Phase 1 — "Insight and Frame get keys now so
    Phase 2/5 add writers without a migration". This is that writer.

    ## Why there is no `DERIVED_FROM` edge

    `data-model.md` §relationships has `(Insight)-[:DERIVED_FROM]->(Event)`, and
    there is a `(:Event)` constraint in the schema — but **nothing in this system
    writes an `(:Event)` node**. Events live on the append-only log, which is the
    record of what happened and the thing a replay reads; copying them into the
    graph to give this edge somewhere to point would duplicate the log into a
    store that holds current state, and the copy would drift.

    So the supporting event ids travel in the `insight.generated` payload
    instead, where a reader can resolve them against the log itself. The graph
    keeps `ABOUT`, which points at zones that really are nodes here.

    ## The node is a copy, not the record

    The event on the log is the record; this is the queryable copy, the same
    relationship every other node in this store has to the events that produced
    it. `MERGE` on the derived id makes a replay re-write the same node rather
    than a second one.
    """
    result = await session.run(
        """
        MERGE (i:Insight {tenant_id: $tenant_id, id: $insight_id})
        SET i.session_id   = $session_id,
            i.text         = $text,
            i.generated_by = $generated_by,
            i.timestamp    = $timestamp,
            i.confidence   = $confidence
        WITH i
        UNWIND CASE WHEN $about_zone_ids = [] THEN [null] ELSE $about_zone_ids END
               AS zone_id
        OPTIONAL MATCH (z:Zone {tenant_id: $tenant_id, session_id: $session_id,
                                id: zone_id})
        FOREACH (_ IN CASE WHEN z IS NULL THEN [] ELSE [1] END |
            MERGE (i)-[:ABOUT]->(z)
        )
        RETURN i
        """,
        tenant_id=tenant_id,
        session_id=session_id,
        insight_id=insight_id,
        text=text,
        generated_by=generated_by,
        timestamp=timestamp,
        confidence=confidence,
        about_zone_ids=about_zone_ids or [],
    )
    record = await result.single()
    return dict(record["i"]) if record else {}


async def contacts_awaiting_withdrawal(
    session: AsyncSession, *, tenant_id: str, contact_ids: list[str]
) -> list[str]:
    """Of these contacts, the ones the withdrawal has not been carried out on.

    The erasure consumer refuses to run while this is non-empty, and the reason
    is an ordering hazard rather than tidiness. `consumers/reanonymise.py` is
    what emits `crm.retract`, and it builds that list by reading the
    `IDENTIFIED_AS` edge. An erasure that deleted the Contact first would leave
    the re-anonymiser with nothing to find, no `crm.retract` would ever be
    emitted, and the copy already sitting in the client's CRM would stay there —
    an erasure that reported success and left the data where it mattered most.

    "Not carried out" is either of two states: a live link, or a Contact that
    still has its PII. The re-anonymiser sets `redacted_at` and drops the link in
    one statement, so both clear together.
    """
    result = await session.run(
        """
        MATCH (ct:Contact {tenant_id: $tenant_id})
        WHERE ct.id IN $contact_ids
          AND (ct.redacted_at IS NULL OR (:Person)-[:IDENTIFIED_AS]->(ct))
        RETURN ct.id AS id
        """,
        tenant_id=tenant_id,
        contact_ids=contact_ids,
    )
    return [record["id"] async for record in result]


async def erase_contact(
    session: AsyncSession, *, tenant_id: str, contact_id: str
) -> int:
    """Delete a Contact outright. Returns 1 if there was one, 0 if not.

    Where the withdrawal leaves a tombstone, the erasure removes the node. The
    tombstone exists so an id in an already-pushed CRM record resolves to
    something that says "retracted"; once the record has actually been retracted
    and the request is Article 17 rather than a change of mind, there is nothing
    left for it to answer.

    `DETACH DELETE` takes the `GRANTED` edge with it and leaves the ConsentEvent
    standing. That is the point: the consent, its tier, its basis, the copy the
    person read and the timestamp they withdrew are the evidence this erasure was
    lawful and asked for, and none of it names anybody. `reanonymise.py` makes
    the same argument about keeping it through a withdrawal.

    The Person, its zones and its dwells are not touched here either — they were
    never consent-gated, and deleting them would rewrite reports already
    delivered about somebody those reports never named.
    """
    result = await session.run(
        """
        MATCH (ct:Contact {tenant_id: $tenant_id, id: $contact_id})
        DETACH DELETE ct
        RETURN count(ct) AS erased
        """,
        tenant_id=tenant_id,
        contact_id=contact_id,
    )
    record = await result.single()
    return record["erased"] if record else 0


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
