"""
Smoke test for the edge bus + graph writer (no server required).

Run from backend/:
  python -m app.smoke_test
"""

from __future__ import annotations

import uuid

from app import bus, db, graph_writer
from app.models import RealmEventInput


def main() -> None:
    db.init_db()
    tenant = "t_floats"
    session = f"s_smoke_{uuid.uuid4().hex[:8]}"

    bus.append(
        RealmEventInput(
            tenantId=tenant,
            sessionId=session,
            type="session.started",
            payload={"name": "Smoke Test Booth", "venue": "Local"},
        )
    )
    for i in range(3):
        bus.append(
            RealmEventInput(
                tenantId=tenant,
                sessionId=session,
                type="perception.detection",
                payload={
                    "anonId": f"P-{i+1}",
                    "bbox": [0.1, 0.1, 0.2, 0.4],
                    "confidence": 0.9,
                    "frameId": i,
                },
            )
        )
    bus.append(
        RealmEventInput(
            tenantId=tenant,
            sessionId=session,
            type="spatial.zone_enter",
            payload={"anonId": "P-1", "zoneId": "z_entry"},
        )
    )
    bus.append(
        RealmEventInput(
            tenantId=tenant,
            sessionId=session,
            type="spatial.dwell",
            payload={"anonId": "P-1", "zoneId": "z_entry", "durationSec": 12.5},
        )
    )

    graph_writer.process_pending(tenant)
    snap = graph_writer.snapshot(tenant, session)
    assert len(snap.persons) >= 3, snap
    assert any(e["kind"] == "DWELLED_IN" for e in snap.edges), snap.edges
    events = bus.read(tenant, session)
    assert len(events) >= 5

    print("OK smoke_test")
    print(f"  session={session}")
    print(f"  events={len(events)} persons={len(snap.persons)} edges={len(snap.edges)}")
    print(f"  db={db.backend_name()}")


if __name__ == "__main__":
    main()
