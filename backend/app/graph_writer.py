"""
Graph writer consumer — projects bus events into relational node/edge tables.

Rebuildable: wipe graph_* for a session and replay the bus from seq 0.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app import bus, db
from app.models import GraphSnapshot, RealmEvent

CONSUMER = "graph_writer"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert_node(
    tenant_id: str,
    session_id: str,
    kind: str,
    node_id: str,
    props: dict[str, Any],
) -> None:
    with db.get_conn() as conn:
        existing = conn.execute(
            """
            SELECT props FROM graph_nodes
            WHERE tenant_id = ? AND session_id = ? AND node_kind = ? AND node_id = ?
            """,
            (tenant_id, session_id, kind, node_id),
        ).fetchone()
        merged = json.loads(existing["props"]) if existing else {}
        merged.update(props)
        conn.execute(
            """
            INSERT INTO graph_nodes (tenant_id, session_id, node_kind, node_id, props, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(tenant_id, session_id, node_kind, node_id) DO UPDATE SET
              props = excluded.props,
              updated_at = excluded.updated_at
            """,
            (tenant_id, session_id, kind, node_id, json.dumps(merged), _now_iso()),
        )
        conn.commit()


def upsert_edge(
    tenant_id: str,
    session_id: str,
    kind: str,
    from_id: str,
    to_id: str,
    props: dict[str, Any] | None = None,
) -> None:
    props = props or {}
    with db.get_conn() as conn:
        existing = conn.execute(
            """
            SELECT props FROM graph_edges
            WHERE tenant_id = ? AND session_id = ? AND edge_kind = ?
              AND from_id = ? AND to_id = ?
            """,
            (tenant_id, session_id, kind, from_id, to_id),
        ).fetchone()
        merged = json.loads(existing["props"]) if existing else {}
        merged.update(props)
        conn.execute(
            """
            INSERT INTO graph_edges
              (tenant_id, session_id, edge_kind, from_id, to_id, props, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tenant_id, session_id, edge_kind, from_id, to_id) DO UPDATE SET
              props = excluded.props,
              updated_at = excluded.updated_at
            """,
            (
                tenant_id,
                session_id,
                kind,
                from_id,
                to_id,
                json.dumps(merged),
                _now_iso(),
            ),
        )
        conn.commit()


def apply_event(event: RealmEvent) -> None:
    t, s = event.tenantId, event.sessionId
    p = event.payload
    ts = event.occurredAt

    if event.type == "session.started":
        upsert_node(
            t,
            s,
            "Session",
            s,
            {
                "id": s,
                "name": p.get("name"),
                "venue": p.get("venue"),
                "startedAt": ts,
            },
        )
        return

    if event.type == "perception.detection":
        anon = p.get("anonId") or p.get("anon_id")
        if not anon:
            return
        upsert_node(
            t,
            s,
            "Person",
            anon,
            {
                "anonId": anon,
                "lastSeen": ts,
                "firstSeen": ts,  # merged; firstSeen kept if already set via merge order
                "attentionScore": p.get("confidence", 0),
            },
        )
        # Preserve firstSeen if node already existed
        with db.get_conn() as conn:
            row = conn.execute(
                """
                SELECT props FROM graph_nodes
                WHERE tenant_id=? AND session_id=? AND node_kind='Person' AND node_id=?
                """,
                (t, s, anon),
            ).fetchone()
            if row:
                props = json.loads(row["props"])
                if "firstSeen" not in props or props.get("firstSeen", ts) > ts:
                    # already handled; ensure firstSeen is min
                    pass
                props["firstSeen"] = min(props.get("firstSeen", ts), ts)
                props["lastSeen"] = max(props.get("lastSeen", ts), ts)
                conn.execute(
                    """
                    UPDATE graph_nodes SET props=?, updated_at=?
                    WHERE tenant_id=? AND session_id=? AND node_kind='Person' AND node_id=?
                    """,
                    (json.dumps(props), _now_iso(), t, s, anon),
                )
                conn.commit()
        return

    if event.type == "spatial.zone_enter":
        anon, zone = p.get("anonId"), p.get("zoneId")
        if anon and zone:
            upsert_node(t, s, "Zone", zone, {"id": zone})
            upsert_edge(t, s, "ENTERED", anon, zone, {"at": ts})
        return

    if event.type == "spatial.zone_exit":
        anon, zone = p.get("anonId"), p.get("zoneId")
        if anon and zone:
            upsert_edge(t, s, "LEFT", anon, zone, {"at": ts})
        return

    if event.type == "spatial.dwell":
        anon, zone = p.get("anonId"), p.get("zoneId")
        dur = p.get("durationSec", 0)
        if anon and zone:
            upsert_edge(t, s, "DWELLED_IN", anon, zone, {"duration": dur, "at": ts})
            # bump person total dwell
            with db.get_conn() as conn:
                row = conn.execute(
                    """
                    SELECT props FROM graph_nodes
                    WHERE tenant_id=? AND session_id=? AND node_kind='Person' AND node_id=?
                    """,
                    (t, s, anon),
                ).fetchone()
                props = json.loads(row["props"]) if row else {"anonId": anon}
                props["totalDwellSec"] = float(props.get("totalDwellSec", 0)) + float(dur)
                conn.execute(
                    """
                    INSERT INTO graph_nodes (tenant_id, session_id, node_kind, node_id, props, updated_at)
                    VALUES (?, ?, 'Person', ?, ?, ?)
                    ON CONFLICT(tenant_id, session_id, node_kind, node_id) DO UPDATE SET
                      props = excluded.props, updated_at = excluded.updated_at
                    """,
                    (t, s, anon, json.dumps(props), _now_iso()),
                )
                conn.commit()
        return

    if event.type == "spatial.gaze":
        anon, target = p.get("anonId"), p.get("targetId")
        if anon and target:
            upsert_edge(
                t,
                s,
                "LOOKED_AT",
                anon,
                target,
                {"duration": p.get("durationSec", 0), "confidence": p.get("confidence", 0)},
            )
        return

    if event.type == "surface.interaction":
        anon, surface = p.get("anonId"), p.get("surfaceId")
        if surface:
            upsert_node(
                t,
                s,
                "Surface",
                surface,
                {"id": surface, "label": surface, "kind": p.get("kind", "other"), "triggerCount": 1},
            )
        if anon and surface:
            upsert_edge(
                t,
                s,
                "INTERACTED_WITH",
                anon,
                surface,
                {"kind": p.get("kind"), "duration": p.get("durationSec")},
            )
        return

    if event.type == "consent.captured":
        contact = p.get("contactId")
        anon = p.get("anonId")
        if contact:
            upsert_node(
                t,
                s,
                "Contact",
                contact,
                {"id": contact, "source": "form", "createdAt": ts},
            )
            upsert_node(
                t,
                s,
                "Consent",
                f"consent_{contact}_{ts}",
                {
                    "id": f"consent_{contact}_{ts}",
                    "tier": p.get("tier"),
                    "basis": p.get("basis"),
                    "copyVersion": p.get("copyVersion"),
                    "capturedAt": ts,
                    "capturedBy": p.get("capturedBy"),
                },
            )
        if anon and contact:
            upsert_edge(t, s, "IDENTIFIED_AS", anon, contact, {"at": ts})
        return

    if event.type == "spatial.group":
        group_id = p.get("groupId")
        members = p.get("memberAnonIds") or []
        if group_id:
            upsert_node(t, s, "Group", group_id, {"id": group_id})
            for m in members:
                upsert_edge(t, s, "GROUP_MEMBER_OF", m, group_id)


def process_pending(tenant_id: str, batch_size: int = 200) -> int:
    """Drain bus for tenant from consumer cursor. Returns events processed."""
    last = bus.get_cursor(CONSUMER, tenant_id)
    # Read across sessions for this tenant
    rows = db.fetchall(
        """
        SELECT * FROM event_log
        WHERE tenant_id = ? AND seq > ?
        ORDER BY seq ASC LIMIT ?
        """,
        (tenant_id, last, batch_size),
    )
    if not rows:
        return 0

    processed = 0
    for row in rows:
        event = RealmEvent(
            seq=int(row["seq"]),
            eventId=row["event_id"],
            tenantId=row["tenant_id"],
            sessionId=row["session_id"],
            type=row["type"],
            payload=json.loads(row["payload"]),
            occurredAt=bus._ms_from_iso(row["occurred_at"]),
            recordedAt=bus._ms_from_iso(row["recorded_at"]),
        )
        try:
            apply_event(event)
            bus.set_cursor(CONSUMER, tenant_id, event.seq)
            processed += 1
        except Exception as exc:  # noqa: BLE001
            bus.dead_letter(CONSUMER, event.seq, str(exc))
            # still advance so one bad event doesn't stall forever in MVP
            bus.set_cursor(CONSUMER, tenant_id, event.seq)
    return processed


def on_bus_event(event: RealmEvent) -> None:
    """Realtime hook: apply immediately + advance cursor if ahead."""
    try:
        apply_event(event)
        cur = bus.get_cursor(CONSUMER, event.tenantId)
        if event.seq > cur:
            bus.set_cursor(CONSUMER, event.tenantId, event.seq)
    except Exception as exc:  # noqa: BLE001
        bus.dead_letter(CONSUMER, event.seq, str(exc))


def snapshot(tenant_id: str, session_id: str) -> GraphSnapshot:
    nodes = db.fetchall(
        """
        SELECT node_kind, node_id, props FROM graph_nodes
        WHERE tenant_id = ? AND session_id = ?
        """,
        (tenant_id, session_id),
    )
    edges = db.fetchall(
        """
        SELECT edge_kind, from_id, to_id, props FROM graph_edges
        WHERE tenant_id = ? AND session_id = ?
        """,
        (tenant_id, session_id),
    )

    persons: list[dict[str, Any]] = []
    zones: list[dict[str, Any]] = []
    surfaces: list[dict[str, Any]] = []
    contacts: list[dict[str, Any]] = []
    consents: list[dict[str, Any]] = []

    for n in nodes:
        props = json.loads(n["props"])
        kind = n["node_kind"]
        if kind == "Person":
            persons.append(
                {
                    "anonId": props.get("anonId", n["node_id"]),
                    "firstSeen": props.get("firstSeen", 0),
                    "lastSeen": props.get("lastSeen", 0),
                    "totalDwellSec": props.get("totalDwellSec", 0),
                    "attentionScore": props.get("attentionScore", 0),
                    "appearanceSummary": props.get("appearanceSummary"),
                    "contactId": props.get("contactId"),
                }
            )
        elif kind == "Zone":
            zones.append(
                {
                    "id": props.get("id", n["node_id"]),
                    "name": props.get("name", n["node_id"]),
                    "kind": props.get("kind", "other"),
                    "polygon": props.get("polygon"),
                    "color": props.get("color"),
                    "capacity": props.get("capacity"),
                    "weight": props.get("weight"),
                }
            )
        elif kind == "Surface":
            surfaces.append(
                {
                    "id": props.get("id", n["node_id"]),
                    "label": props.get("label", n["node_id"]),
                    "kind": props.get("kind", "other"),
                    "zoneId": props.get("zoneId"),
                    "triggerCount": props.get("triggerCount", 0),
                    "active": props.get("active", True),
                }
            )
        elif kind == "Contact":
            contacts.append(props)
        elif kind == "Consent":
            consents.append(props)

    edge_list = [
        {
            "kind": e["edge_kind"],
            "from": e["from_id"],
            "to": e["to_id"],
            "props": json.loads(e["props"]),
        }
        for e in edges
    ]

    return GraphSnapshot(
        tenantId=tenant_id,
        sessionId=session_id,
        persons=persons,
        zones=zones,
        surfaces=surfaces,
        contacts=contacts,
        consents=consents,
        edges=edge_list,
    )
