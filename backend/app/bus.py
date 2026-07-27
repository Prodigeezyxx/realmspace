"""Append-only, idempotent, replayable event bus (SQLite implementation)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from app import db
from app.models import PII_EVENT_TYPES, RealmEvent, RealmEventInput

Subscriber = Callable[[RealmEvent], None]
_subscribers: set[Subscriber] = set()


def subscribe(fn: Subscriber) -> Callable[[], None]:
    _subscribers.add(fn)

    def unsubscribe() -> None:
        _subscribers.discard(fn)

    return unsubscribe


def _notify(event: RealmEvent) -> None:
    for fn in list(_subscribers):
        try:
            fn(event)
        except Exception:
            # Never let a bad subscriber break the bus.
            pass


def _ms_now() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _iso_from_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _ms_from_iso(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


def _row_to_event(row: Any) -> RealmEvent:
    return RealmEvent(
        seq=int(row["seq"]),
        eventId=row["event_id"],
        tenantId=row["tenant_id"],
        sessionId=row["session_id"],
        type=row["type"],
        payload=json.loads(row["payload"]),
        occurredAt=_ms_from_iso(row["occurred_at"]),
        recordedAt=_ms_from_iso(row["recorded_at"]),
    )


def append(inp: RealmEventInput) -> RealmEvent:
    """Append one event. Duplicate eventId → return existing row (idempotent)."""
    if inp.type in PII_EVENT_TYPES and inp.type != "consent.captured":
        if not inp.consentProof:
            raise ValueError(
                f'refused to emit PII event "{inp.type}" without consentProof'
            )

    event_id = inp.eventId or str(uuid.uuid4())
    existing = db.fetchone("SELECT * FROM event_log WHERE event_id = ?", (event_id,))
    if existing:
        return _row_to_event(existing)

    occurred_ms = inp.occurredAt if inp.occurredAt is not None else _ms_now()
    recorded_ms = _ms_now()

    with db.get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO event_log
              (event_id, tenant_id, session_id, type, payload, occurred_at, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                inp.tenantId,
                inp.sessionId,
                inp.type,
                json.dumps(inp.payload),
                _iso_from_ms(occurred_ms),
                _iso_from_ms(recorded_ms),
            ),
        )
        conn.commit()
        seq = int(cur.lastrowid)

    event = RealmEvent(
        seq=seq,
        eventId=event_id,
        tenantId=inp.tenantId,
        sessionId=inp.sessionId,
        type=inp.type,
        payload=inp.payload,
        occurredAt=occurred_ms,
        recordedAt=recorded_ms,
    )
    _notify(event)
    return event


def append_many(inputs: list[RealmEventInput]) -> list[RealmEvent]:
    return [append(i) for i in inputs]


def read(
    tenant_id: str,
    session_id: str,
    *,
    after_seq: int = 0,
    limit: int = 500,
    types: list[str] | None = None,
) -> list[RealmEvent]:
    sql = """
      SELECT * FROM event_log
      WHERE tenant_id = ? AND session_id = ? AND seq > ?
    """
    params: list[Any] = [tenant_id, session_id, after_seq]
    if types:
        placeholders = ",".join("?" for _ in types)
        sql += f" AND type IN ({placeholders})"
        params.extend(types)
    sql += " ORDER BY seq ASC LIMIT ?"
    params.append(limit)
    rows = db.fetchall(sql, tuple(params))
    return [_row_to_event(r) for r in rows]


def get_cursor(consumer: str, tenant_id: str) -> int:
    row = db.fetchone(
        "SELECT last_seq FROM consumer_cursor WHERE consumer = ? AND tenant_id = ?",
        (consumer, tenant_id),
    )
    return int(row["last_seq"]) if row else 0


def set_cursor(consumer: str, tenant_id: str, last_seq: int) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with db.get_conn() as conn:
        conn.execute(
            """
            INSERT INTO consumer_cursor (consumer, tenant_id, last_seq, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(consumer, tenant_id) DO UPDATE SET
              last_seq = excluded.last_seq,
              updated_at = excluded.updated_at
            """,
            (consumer, tenant_id, last_seq, now),
        )
        conn.commit()


def dead_letter(consumer: str, event_seq: int, error: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with db.get_conn() as conn:
        existing = conn.execute(
            "SELECT id, attempts FROM dead_letter WHERE consumer = ? AND event_seq = ? AND resolved_at IS NULL",
            (consumer, event_seq),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE dead_letter SET attempts = ?, error = ? WHERE id = ?",
                (int(existing["attempts"]) + 1, error, existing["id"]),
            )
        else:
            conn.execute(
                """
                INSERT INTO dead_letter (consumer, event_seq, error, attempts, created_at)
                VALUES (?, ?, ?, 1, ?)
                """,
                (consumer, event_seq, error, now),
            )
        conn.commit()
