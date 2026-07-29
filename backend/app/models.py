"""
The log. A direct transcription of docs/event-bus-spec.md §2 — if this file and
that section ever disagree, the spec wins.

Three tables, and only the first one is written to in this checklist item:

  event_log        the append-only log itself
  consumer_cursor  where each consumer has read up to (per tenant)
  dead_letter      events a consumer failed on, for the HITL retry screen

The last two are created now but stay empty until the tracker / graph-writer
consumers land, so adding those is a pure addition with no migration churn.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import BigInteger, DateTime, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class EventLog(Base):
    """One row per event. Nothing ever updates or deletes a row here — that is
    what "append-only" means, and it is why replay works: re-reading the table
    in seq order reproduces exactly what happened."""

    __tablename__ = "event_log"

    # BIGSERIAL. Assigned by Postgres, monotonic, the replay cursor. Consumers
    # order by this and remember the last one they handled.
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # Assigned by the *producer*, not the server. That is the whole trick: if a
    # producer's POST times out it can retry with the same event_id, and the
    # UNIQUE constraint turns the retry into a no-op instead of a duplicate.
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True, nullable=False)

    # multi-tenant.md §2: every table carries tenant_id, every query is scoped.
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    session_id: Mapped[str] = mapped_column(Text, nullable=False)

    # Namespaced event type, e.g. "perception.detection" — taxonomy in spec §3.
    type: Mapped[str] = mapped_column(Text, nullable=False)

    # JSONB not JSON: binary, indexable, and Postgres can query inside it.
    # Privacy invariant (spec §6): frames/video never go in here, only structure.
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # When it happened, by the edge device's clock.
    occurred_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # When the log accepted it. Differs from occurred_at after an offline replay.
    recorded_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        # Serves the consumer poll: "events for this tenant/session after seq N".
        Index("event_log_tenant_id_session_id_seq_idx", "tenant_id", "session_id", "seq"),
        Index("event_log_type_idx", "type"),
    )


class ConsumerCursor(Base):
    """How far each consumer has read. Replay = set last_seq back and let the
    consumer re-read; idempotent consumers make that safe."""

    __tablename__ = "consumer_cursor"

    consumer: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(Text, primary_key=True)
    last_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class DeadLetter(Base):
    """A consumer failed on an event N times. Surfaces in the HITL review screen
    (roadmap Phase 3) for a human to retry or fix."""

    __tablename__ = "dead_letter"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    consumer: Mapped[str] = mapped_column(Text, nullable=False)
    event_seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    error: Mapped[str] = mapped_column(Text, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    resolved_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
