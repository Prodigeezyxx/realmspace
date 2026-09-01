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

Phase 4 adds two, which are neither — one is a secret and one is a receipt:

  tenant_integration   what this tenant authenticates to a CRM as
  crm_link             where a contact was actually pushed, so a withdrawal
                       knows which destinations to go and undo
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
    LargeBinary,
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
    in seq order reproduces exactly what happened.

    With exactly one exception, added in migration 0009: an erasure under GDPR
    Article 17 rewrites the `payload` of the events naming its subject, and
    stamps `redacted_at`. Nothing else is touched — no row is deleted, no
    sequence number is reused — so a replay after an erasure reproduces the same
    events in the same order with a name missing from three of them. See
    `app/consumers/erasure.py`, which is the only writer that ever does this.
    """

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
    # Set only by an erasure (migration 0009). NULL on every other row, forever.
    redacted_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: When retention emptied this payload (migration 0014). Its own column and
    #: never `redacted_at`, because the two are different facts: that one means
    #: a person asked to be forgotten, and an auditor reads its counts. This one
    #: means the event aged out of the window the client bought.
    purged_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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
    """One attempt at an outbound act somebody else's system will receive.

    Added in migration 0004 for rule actions; widened in 0006 to cover Phase 4's
    lead handoffs, which pose the identical problem. The name stays for the
    table it already is.

    This exists for its UNIQUE constraint. ADR-002 §3: `rule.fired` carries a
    derived event id and "per-dispatch idempotency keys on that `event_id`, as
    Phase 3 requires, so a retry after a timeout cannot double-post". Posting to
    Slack is not idempotent and cannot be made so from this side, so the claim
    has to happen in a database we control before the call goes out — and a
    `handoff.lead` reaching a customer's webhook is the same sentence with the
    nouns changed.

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
    #: The derived event_id of the event that caused this dispatch — a
    #: `rule.fired` or a `handoff.lead`.
    fired_event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    #: What caused it: `rule` or `handoff`. Added in 0006, and only for the human
    #: on `/ops` — the claim itself works without it, since a causing event id is
    #: already unique across the log. What it buys is that a stranded row is
    #: legible: a stuck Slack post and a stuck lead are different urgencies.
    kind: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'rule'"))
    #: The rule that fired, or the session a handoff belongs to.
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
    #: The operator who ruled on a stranded `claimed` row, and when. NULL on
    #: every row the dispatcher closed by itself — which is the point of them
    #: being separate from `status` and `completed_at`. A `delivered` the
    #: dispatcher wrote is a 200 from Slack; a `delivered` an operator wrote is a
    #: person saying they saw the message. Added in migration 0005.
    resolved_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ReportShare(Base):
    """A link that lets a client open one report without an account. Migration 0013.

    The token itself is not here — only its sha256, so a value nobody can read
    back cannot leak out of the database. `hint` is the last four characters,
    enough to recognise a link you are holding and not enough to rebuild one.
    The same shape `TenantIntegration` uses for a credential, and for the same
    reason.

    `revoked_at` rather than a DELETE, as `auth/models.ApiKey` and
    `TenantIntegration` both do: a link handed to a client is a fact about the
    activation, and withdrawing it should not destroy the answer to "did they
    ever see this".

    Outside RLS, and 0013's docstring has the argument in full — the reader has
    no credential at all, and the tenant it should be scoped to is a column on
    the row being looked up. What stands in for a policy is that the lookup key
    is a digest of 32 random bytes, and that every operator-side query filters on
    the caller's verified tenant explicitly.
    """

    __tablename__ = "report_share"
    __table_args__ = (
        Index("report_share_session_idx", "tenant_id", "session_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    #: One link opens one activation's report. Never a wildcard: a link that
    #: followed the tenant rather than the session would hand over every
    #: activation the client was never shown.
    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    token_sha256: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    hint: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    #: Not nullable on purpose. A link with no expiry is a permanent
    #: unauthenticated read path into a client's data, and "forever" should have
    #: to be typed as a number of days by somebody who meant it.
    expires_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: So an operator can answer "has the client opened it yet", which is the
    #: first thing they ask after sending one.
    last_viewed_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    view_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class SurfaceToken(Base):
    """What a tablet at a touchpoint holds. Migration 0015.

    The same shape as `ReportShare` and for the same reasons — a digest rather
    than the token, a `hint` so a list is legible, `revoked_at` rather than a
    DELETE — with one difference that is the whole point of a separate table:
    this one **writes**. A share token reaches three read routes; this reaches
    two, one of which appends a single `surface.touched` event.

    `surface_id` is on the row because it must not be in the request. A tablet
    that could name its own touchpoint could post as any of them on the stand.
    """

    __tablename__ = "surface_token"
    __table_args__ = (
        Index("surface_token_session_idx", "tenant_id", "session_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    surface_id: Mapped[str] = mapped_column(Text, nullable=False)
    token_sha256: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    hint: Mapped[str] = mapped_column(Text, nullable=False)
    #: What the operator called this tablet — "the plinth by the door". For
    #: their list, never shown to whoever taps it.
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    #: Not nullable, for `ReportShare`'s reason one step further: this is an
    #: unauthenticated *write* path, and an activation is over in days.
    expires_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: "Is the tablet by the door still alive?", which is the question an
    #: operator asks about a device they cannot see from where they are standing.
    last_used_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class TenantPurgeWatermark(Base):
    """How far a tenant's log has been purged. Migration 0014.

    A purge empties payloads and leaves the rows, which keeps derived event ids
    resolvable and dead letters pointing at something — and means a replay from
    seq 0 would feed consumers empty events and quietly rebuild a wrong graph.

    `repository.reset_cursor` refuses to rewind below `purged_before_seq`, so
    that replay is unwritable rather than merely wrong.
    """

    __tablename__ = "tenant_purge_watermark"

    tenant_id: Mapped[str] = mapped_column(Text, primary_key=True)
    purged_before_seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: The floor the last purge used. Kept so a receipt stays checkable later,
    #: against a plan that may since have changed tier.
    purged_through: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class TenantIntegration(Base):
    """What one tenant authenticates to one CRM as. Added in migration 0007.

    `multi-tenant.md` §2: "each tenant's CRM/enrichment credentials are stored
    encrypted, per-tenant, never shared". `secret_ct` is the whole of that
    sentence — there is no plaintext column to fall back to, and `app/secrets.py`
    refuses to produce a value for it when no encryption key is configured.

    One row per `(tenant_id, provider)`. An adapter asks "what am I HubSpot as,
    for this tenant?" and there is exactly one answer at a time; a surrogate key
    would permit two, and nothing downstream could choose between them.

    `revoked_at` rather than deleting the row, as `auth/models.ApiKey` does: a
    credential that appears in a delivery record afterwards should still be
    identifiable, and "we stopped" should not look like "we never had one".
    """

    __tablename__ = "tenant_integration"
    __table_args__ = (
        Index(
            "tenant_integration_active_idx",
            "tenant_id",
            postgresql_where=text("status = 'active'"),
        ),
    )

    tenant_id: Mapped[str] = mapped_column(Text, primary_key=True)
    #: Open string, not an enum — the next adapter should not need a migration
    #: to exist, and enrichment providers (integrations.md §7) arrive here too.
    provider: Mapped[str] = mapped_column(Text, primary_key=True)
    #: What the provider is *for* (migration 0010). `crm` is a destination a
    #: lead can be delivered to; `llm` is a model Ask and the SDR call. The
    #: delivery consumer asks for `crm` explicitly, so an AI key can never be
    #: enrolled as a place to send somebody's contact details.
    kind: Mapped[str] = mapped_column(
        Text, nullable=False, default="crm", server_default="crm"
    )

    #: nonce || AES-256-GCM ciphertext, bound to (tenant_id, provider). Only
    #: `app/secrets.py` reads it, and only an adapter's authenticate() asks.
    secret_ct: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    #: Last four characters of the secret. Never enough to use, enough for an
    #: admin to recognise which credential is stored.
    secret_hint: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''")
    )

    #: integrations.md §3 — "field mapping is per-tenant config, not code".
    field_map: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    #: active | revoked
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'active'")
    )

    #: What healthcheck() last said. Kept so a credential that has gone bad is
    #: visible before a lead strands on it rather than afterwards on /ops.
    last_check_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_check_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_check_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    revoked_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class CrmLink(Base):
    """One contact, as one CRM knows it. Added in migration 0008.

    `consumers/reanonymise.py` emits `crm.retract` with `destination: "all"`
    because nothing recorded where a handoff actually went. This is that record,
    and it is what resolves "all" into the destinations that received something.

    It cannot live on the graph Contact: a withdrawal redacts the Contact, so a
    note stored there would be erased moments before the retract consumer needed
    to read it. Where we sent somebody has to outlive who they were.

    `dedupe_key` is PII (`tenant:email`) and is redacted when the retraction
    succeeds — by `attribution.ledger.redact_dedupe_key`, the same function the
    ledger uses, because two redactions are two chances to disagree about which
    half of the key names a person. `external_id` survives: it is the CRM's
    identifier for a record we asked the CRM to remove, and an audit that cannot
    name the record cannot check that it went.
    """

    __tablename__ = "crm_link"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "provider",
            "contact_id",
            name="crm_link_tenant_id_provider_contact_id_key",
        ),
        Index("crm_link_tenant_contact_idx", "tenant_id", "contact_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    #: Ours — the Contact the re-anonymiser names in `crm.retract`.
    contact_id: Mapped[str] = mapped_column(Text, nullable=False)
    #: Theirs — what `retract()` is called with.
    external_id: Mapped[str] = mapped_column(Text, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(Text, nullable=False)
    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    retracted_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    retract_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
