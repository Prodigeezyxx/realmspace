"""
The log. A direct transcription of docs/event-bus-spec.md §2 — if this file and
that section ever disagree, the spec wins.

Three tables, and only the first one is written to in this checklist item:

  event_log        the append-only log itself
  consumer_cursor  where each consumer has read up to (per tenant)
  dead_letter      events a consumer failed on, for the HITL retry screen

The last two are created now but stay empty until the tracker / graph-writer
consumers land, so adding those is a pure addition with no migration churn.

Phase 3 adds two more, which are configuration rather than log:

  rules            the rule documents of ADR-002, one row per document
  rule_dispatch    the idempotency key for carrying out a rule's action
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
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
        # Added in 0004 for the rules evaluator, whose window read selects by
        # type and event time rather than by seq: "spatial.dwell in this session
        # in the last 30 seconds" (consumers/rules.py).
        Index(
            "event_log_tenant_session_type_occurred_idx",
            "tenant_id",
            "session_id",
            "type",
            "occurred_at",
        ),
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
    __table_args__ = (Index("dead_letter_tenant_id_idx", "tenant_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # Added in migration 0003 so dead letters can be tenant-scoped by RLS. The
    # error column holds a traceback, which can quote the event payload, so an
    # unscoped dead_letter leaks across tenants.
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
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


class Rule(Base):
    """One rule document, per ADR-002. Added in migration 0004.

    The split between columns and JSONB is the evaluator's query, written down:
    `trigger_type`, `tenant_id` and `enabled` are what it filters on once per
    event, so they are indexed columns. `condition` and `action` are read whole
    by a rule that has already matched, so they stay documents — and a spec
    change that adds a condition field then needs no migration.

    There is deliberately **no `last_fired_at`**. Cooldown is derived from the
    most recent `rule.fired` on the bus, compared on `occurred_at` (ADR-002 §2).
    A column holding wall time would make a replay produce different firings
    from the original run.
    """

    __tablename__ = "rules"
    __table_args__ = (
        Index(
            "rules_tenant_id_trigger_type_idx",
            "tenant_id",
            "trigger_type",
            postgresql_where=text("enabled"),
        ),
    )

    # Operator-supplied, not generated: ADR-002's example is `r_entry_crowd`, and
    # a rule an operator can name is a rule they can find again in a log line.
    rule_id: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)

    # Any type registered in event-bus-spec.md §3 — validated as a namespace
    # prefix, never a closed enum (ADR-002: a rule that cannot name
    # `intent.scored` the day that producer lands forces a spec migration).
    trigger_type: Mapped[str] = mapped_column(Text, nullable=False)
    trigger_zone_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    condition: Mapped[dict] = mapped_column(JSONB, nullable=False)
    action: Mapped[dict] = mapped_column(JSONB, nullable=False)

    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    cooldown_sec: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("60")
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class RuleDispatch(Base):
    """One attempt to carry out a rule's action. Added in migration 0004.

    This table exists for its UNIQUE constraint. ADR-002 §3: `rule.fired` carries
    a derived event id and "per-dispatch idempotency keys on that `event_id`, as
    Phase 3 requires, so a retry after a timeout cannot double-post". Posting to
    Slack is not idempotent and cannot be made so from this side, so the claim
    has to happen in the database before the call goes out.

    The other columns are for the human reading `/ops` afterwards, not for the
    dispatcher.
    """

    __tablename__ = "rule_dispatch"
    __table_args__ = (
        UniqueConstraint("fired_event_id", "action_type", name="rule_dispatch_fired_event_id_action_type_key"),
        Index("rule_dispatch_tenant_id_rule_id_idx", "tenant_id", "rule_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    #: The derived event_id of the `rule.fired` that caused this dispatch.
    fired_event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    rule_id: Mapped[str] = mapped_column(Text, nullable=False)
    action_type: Mapped[str] = mapped_column(Text, nullable=False)
    #: claimed | delivered | failed
    status: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    completed_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
