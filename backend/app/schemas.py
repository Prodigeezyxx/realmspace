"""
Wire shapes. Pydantic validates these before anything touches the database, so a
malformed producer gets a 422 instead of a half-written row.

On the wire these are camelCase with ms-epoch timestamps, matching the canonical
contract in dashboard/src/lib/contracts/events.ts. In Python they stay snake_case,
and producers may POST either — see EventOut.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
)


# The namespaces in event-bus-spec.md §3. Deliberately a prefix check, not an
# allow-list of full type names: §3 says the taxonomy is "namespaced and
# additive-only", so a new spatial.* type must not need a code change here.
#
# Be clear about what this does and does not buy. It rejects an unknown or
# misspelled *namespace* — "spatal.zone_enter", or a type from a system that
# has no business on this bus. It does **not** catch a typo in the suffix:
# "spatial.zone_entr" starts with "spatial." and passes.
#
# That residual gap is real. Such an event is accepted, stored forever in an
# append-only table, and then matched by no consumer's `handles`, so it is
# silently never processed. Closing it properly means an allow-list of full
# type names, which trades the additive-only property for strictness — worth
# revisiting once the taxonomy stops growing.
EVENT_NAMESPACES = (
    "perception.",
    "spatial.",
    "surface.",
    # Week 1 task 1.11 ships an RFID producer emitting rfid.read. Added ahead of
    # it because a namespace this validator doesn't know is a 422, and POD 1
    # would hit that on their first request with nothing to explain it.
    "rfid.",
    "consent.",
    "identity.",
    "rule.",
    "handoff.",
    "insight.",
    "cost.",
    "session.",
    # Pre-registered for Phase 3, for the same reason rfid. was: a namespace this
    # validator has never heard of is a 422, and the producer's author gets no
    # hint that the taxonomy is the thing refusing them. Registering the
    # namespace ahead of the producer costs a line here and saves that.
    #: intent.scored — the intent-scoring consumer (P4 lead capture).
    "intent.",
    #: drift.detected — CV drift telemetry (roadmap.md, Phase 6).
    "drift.",
    #: calibration.updated — the calibration UI (roadmap.md, Phase 6).
    "calibration.",
    #: crm.retract — the re-anonymiser on withdrawal (consent-and-identity.md §5).
    "crm.",
    #: outcome.recorded — what a lead turned into. Added with the attribution
    #: ledger; nothing in the repo had ever defined an outcome, which is why
    #: every attribution claim in the docs rested on a thing that did not exist.
    "outcome.",
    #: followup.drafted — the contextual SDR (roadmap.md Phase 5). Its own
    #: namespace rather than an `insight.` type: an insight is about the room, a
    #: draft is about one person who agreed to be contacted, and the two want
    #: different handling everywhere PII is handled.
    "followup.",
    #: erasure.requested / erasure.completed — GDPR Art. 17, the job
    #: `consent-and-identity.md` §5 asks for. Its own namespace rather than a
    #: `consent.` type, because an erasure is not a consent decision: it outranks
    #: one, it is authorised by an admin rather than given by the visitor, and it
    #: is the only thing in the system that rewrites the log.
    "erasure.",
)


def _to_camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(word.capitalize() for word in rest)


class EventIn(BaseModel):
    """What a producer POSTs.

    event_id is supplied by the *producer*, not generated here. That is what
    makes retries safe: if the POST times out and the producer resends, it sends
    the same event_id and the log dedupes it (event-bus-spec.md §2).

    Field names are snake_case in Python and camelCase on the wire — see the
    note on EventOut for why.
    """

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    event_id: uuid.UUID
    tenant_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    type: str = Field(min_length=1)
    payload: dict[str, Any]
    occurred_at: dt.datetime

    @field_validator("type")
    @classmethod
    def type_is_in_a_known_namespace(cls, value: str) -> str:
        if not value.startswith(EVENT_NAMESPACES):
            raise ValueError(
                f"unknown event namespace in {value!r}; "
                f"expected one of {', '.join(EVENT_NAMESPACES)} "
                "(taxonomy: docs/event-bus-spec.md §3)"
            )
        return value


class EventOut(EventIn):
    """What comes back out — over HTTP and over the WebSocket.

    ## Why this is camelCase with millisecond timestamps

    `dashboard/src/lib/contracts/events.ts` declares itself the canonical event
    shape, in its own words: "Both the mocked prototype and the future FastAPI +
    Postgres backend conform to THIS type." That type is:

        { seq, eventId, tenantId, sessionId, type, payload,
          occurredAt: number, recordedAt: number }   // ms epoch

    We were emitting `event_id` and ISO date strings, i.e. not conforming. Fixed
    here rather than left for the browser to paper over, because a mapper on the
    client is a permanent tax and this is the last moment it is free — nothing
    consumes the API yet except our own tests.

    `populate_by_name=True` means producers can still POST snake_case, so the
    Python side (perception, tests, curl) is unaffected.
    """

    model_config = ConfigDict(
        from_attributes=True, alias_generator=_to_camel, populate_by_name=True
    )

    seq: int
    recorded_at: dt.datetime

    @field_serializer("occurred_at", "recorded_at")
    def _as_epoch_ms(self, value: dt.datetime) -> int:
        """Timestamps go out as ms-epoch integers, per the canonical contract.

        Naive datetimes are treated as UTC. Postgres gives us TIMESTAMPTZ so in
        practice they always carry a zone, but a naive one silently interpreted
        as local time would shift every event by the UTC offset — worth being
        explicit about rather than lucky.
        """
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.timezone.utc)
        return int(value.timestamp() * 1000)


# ── session configuration ─────────────────────────────────────────────────────
#
# What an operator sets **before** the activation runs, per roi-framework.md §5:
# the zones, their weights, the funnel, the engagement threshold, the cost and
# the attribution model. Everything the report divides by.
#
# These are the wizard's shapes, not new ones. `dashboard/src/lib/session/types.ts`
# already declares `Zone { id, name, type, capacity, color, polygon }` and stores
# timestamps as ISO strings, so that is what goes on the wire here — same
# reasoning as EventOut's camelCase: the browser's contract is canonical and a
# mapper on the client would be a permanent tax.


class ZoneConfig(BaseModel):
    """One zone as the session wizard draws it, plus its measurement parameters.

    `type` is a free string rather than an enum, deliberately. There are already
    two zone vocabularies in the dashboard — `ZoneKind` in contracts/graph.ts
    (7 values) and `ZoneType` in session/types.ts (11, including `reveal` and
    `privacy_masked`) — and the wizard produces the second. An enum here would
    422 on a zone the operator can legitimately draw, and nothing in the backend
    branches on the value. Accepted as `type`, or `kind` for the graph contract's
    spelling, so either shape posts cleanly.
    """

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    type: str = Field(default="other", validation_alias=AliasChoices("type", "kind"))
    polygon: list[tuple[float, float]] | None = None
    color: str | None = None
    capacity: int | None = Field(default=None, ge=0)
    #: roi-framework.md §2 — dwell-weighted attention is Σ(dwell × weight).
    weight: float = Field(default=1.0, ge=0)
    #: Position in the entry → experience → product → capture funnel (§5).
    funnel_order: int | None = Field(default=None, ge=0)

    @field_validator("polygon")
    @classmethod
    def polygon_is_a_normalized_shape(
        cls, value: list[tuple[float, float]] | None
    ) -> list[tuple[float, float]] | None:
        """Reject polygons the tracker could only ever silently ignore.

        Two failures, both otherwise invisible. Fewer than three points is not a
        shape, and `point_in_polygon` returns False for it forever — every
        detection inside the zone the operator thinks they drew is attributed
        nowhere, and the report simply shows less traffic than there was.

        Coordinates outside 0..1 mean pixels were sent where normalized booth
        coordinates were expected. `consumers/zones.py:normalize` divides every
        detection by the frame size before comparing, so a pixel polygon matches
        nothing at all. This is the one place that mistake is cheap to catch.
        """
        if value is None:
            return None
        if len(value) < 3:
            raise ValueError(
                f"a polygon needs at least 3 points, got {len(value)} — "
                "fewer is not a shape and would contain nobody"
            )
        for x, y in value:
            if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
                raise ValueError(
                    f"polygon point ({x}, {y}) is outside 0..1 — zone polygons are "
                    "normalized booth coordinates, not pixels (data-model.md → Zone)"
                )
        return value


class TouchpointConfig(BaseModel):
    """A booth surface an operator installed — data-model.md → `(:Surface {...})`.

    Same free-string reasoning as `ZoneConfig.type`: the wizard's
    `TouchpointType` has sixteen values and the graph contract's `SurfaceNode`
    describes a different set, so an enum here would reject a touchpoint the
    operator can legitimately configure. Nothing in the backend branches on it.
    """

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    type: str = Field(default="other", validation_alias=AliasChoices("type", "kind"))
    #: Which zone it sits in, if any. Not validated against the zone list: an
    #: operator may add the touchpoint before drawing the zone around it.
    zone_id: str | None = None
    active: bool = True


class SessionConfigIn(BaseModel):
    """The wizard's output: one activation, configured for measurement."""

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    session_id: str = Field(min_length=1)
    client: str | None = None
    campaign: str | None = None
    venue: str | None = None
    city: str | None = None
    started_at: str | None = None
    ends_at: str | None = None
    booth_width_m: float | None = Field(default=None, gt=0)
    booth_depth_m: float | None = Field(default=None, gt=0)
    camera_count: int | None = Field(default=None, ge=0)

    #: Above this, a visit counts as engaged (roi-framework.md §2, Layer 2).
    #: 60s matches the default in dashboard/src/lib/roi/scorecard.ts.
    engaged_threshold_seconds: float = Field(default=60.0, gt=0)
    #: Total cost of the activation — the denominator of CPEV, CPQL and ROI.
    activation_cost: float | None = Field(default=None, ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    #: roi-framework.md §3. "influenced" by default: the simplest to defend.
    attribution_model: Literal[
        "first_touch", "last_touch", "linear", "time_decay", "influenced"
    ] = "influenced"

    #: How long after a booth touch an outcome may still be attributed to it.
    #: roi-framework.md §2 calls for "configurable 30/60/90-day windows on the
    #: outcome edge"; 90 is the default because it is the longest of the three,
    #: and a window widened after the fact to capture a deal that closed late is
    #: exactly the argument §3's design principle rules out. Narrow it before the
    #: doors open if the client's sales cycle is shorter.
    #:
    #: A closed set rather than a free integer for the same reason
    #: `attribution_model` is: these are the three windows a CFO recognises, and
    #: an arbitrary 47 is a number somebody chose to make a ratio work.
    attribution_window_days: Literal[30, 60, 90] = 90

    #: **Operator-supplied, not measured.** Influenced revenue comes from CRM
    #: attribution, which is Phase 4; until then the only honest sources are the
    #: client's own figure or nothing at all. Left null, the ROI ratio reports as
    #: unknown rather than as zero — those are different answers and the report
    #: must not conflate them. Anything displaying these states their origin.
    revenue_influenced: float | None = Field(default=None, ge=0)
    qualified_leads: int | None = Field(default=None, ge=0)

    #: Emit a `handoff.lead` for the visitors who never consented, carrying
    #: spatial intent and no contact — `integrations.md` §2's anonymous handoff.
    #:
    #: **Off unless the operator asks for it**, which is the opposite of every
    #: other flag here. A busy day is several hundred of them, each one a lead
    #: object with nobody in it; a tenant who has not asked for aggregate reach
    #: in their own stack should not find their destinations carrying it. Turned
    #: on, it is one handoff per un-consented person at `session.ended`.
    anonymous_handoffs: bool = False

    #: **Omitted leaves the zone set untouched; a list replaces it entirely**,
    #: including an empty one. The wizard always posts the whole set, so a zone
    #: the operator deleted has to disappear rather than linger and keep
    #: collecting dwell — see graph.repository.prune_zones.
    zones: list[ZoneConfig] | None = None

    #: Same replace-or-leave-alone rule as `zones`. Omitted keeps the existing
    #: touchpoints; a list replaces the set and prunes what is no longer in it.
    touchpoints: list[TouchpointConfig] | None = None

    @field_validator("touchpoints")
    @classmethod
    def touchpoint_ids_are_unique(
        cls, value: list[TouchpointConfig] | None
    ) -> list[TouchpointConfig] | None:
        """As with zones, MERGE would collapse a duplicate and lose one."""
        if value is None:
            return None
        seen = [t.id for t in value]
        duplicates = sorted({t for t in seen if seen.count(t) > 1})
        if duplicates:
            raise ValueError(f"duplicate touchpoint ids: {', '.join(duplicates)}")
        return value

    @field_validator("zones")
    @classmethod
    def zone_ids_are_unique(
        cls, value: list[ZoneConfig] | None
    ) -> list[ZoneConfig] | None:
        """Two zones with one id is a lost zone, not an error the graph reports.

        `upsert_zone` MERGEs on (tenant_id, id), so the second would overwrite
        the first and the operator would find one of their zones missing with
        nothing to explain it.
        """
        if value is None:
            return None
        seen = [z.id for z in value]
        duplicates = sorted({z for z in seen if seen.count(z) > 1})
        if duplicates:
            raise ValueError(f"duplicate zone ids: {', '.join(duplicates)}")
        return value


class TouchpointOut(TouchpointConfig):
    """A touchpoint as stored, with how many interactions it has recorded."""

    trigger_count: int = 0


class SessionConfigOut(SessionConfigIn):
    """What comes back. Both lists are always present here — never None."""

    zones: list[ZoneConfig] = []
    touchpoints: list[TouchpointOut] = []


class ZoneDwell(BaseModel):
    """Average dwell and visitor count for one zone."""

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    zone_id: str
    zone: str | None = None
    avg_dwell: float | None = None
    visitors: int = 0


class SessionGraphOut(BaseModel):
    """The graph-derived aggregates a report or a live tile starts from.

    Only what the graph knows better than the log does: unique people, and dwell
    already grouped per zone. Everything else the scorecard needs is derivable
    from the event log the caller can already read at `GET /events`, and
    computing it twice in two languages is how the two come to disagree.
    """

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    session_id: str
    unique_people: int
    zones: list[ZoneConfig]
    dwell_by_zone: list[ZoneDwell]


# ── rules ─────────────────────────────────────────────────────────────────────
#
# ADR-002's document, as a wire shape. The ADR's example is the test: every field
# below appears there, spelled the same way.
#
#   { ruleId, tenantId, name, triggerType, triggerZoneId,
#     condition: { type: "threshold", count, windowSec, zoneId, minDwellSec },
#     action:    { type: "slack", channel, message },
#     enabled, cooldownSec }
#
# Adopted verbatim from what floats-agent already ships, "so the two tracks do
# not end up with two rule languages". Two shapes here are deliberately unlike
# each other: `triggerType` is an open prefix check, and condition/action are
# closed discriminated unions. See the note on RuleIn.


class ThresholdCondition(BaseModel):
    """N of something within a window. The only condition that counts."""

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    type: Literal["threshold"]
    count: int = Field(ge=1)
    window_sec: int = Field(gt=0)
    #: Narrows the window to one zone. Distinct from `triggerZoneId`: the trigger
    #: decides which events wake the rule, this decides which are counted.
    zone_id: str | None = None
    #: For dwell triggers — ignore a dwell shorter than this. A dwell event is
    #: emitted for every stay, including a two-second one, and "5 people at the
    #: entrance" does not mean five people who walked past it.
    min_dwell_sec: float | None = Field(default=None, ge=0)


class AnyCondition(BaseModel):
    """Fire on the trigger itself, with no counting. `count: 1` in spirit, but
    named separately because "any dwell in this zone" is what an operator says,
    and a spec they can read back is the point of ADR-002."""

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    type: Literal["any"]
    zone_id: str | None = None


class NoneCondition(BaseModel):
    """Nothing happened for `windowSec`.

    ADR-002 §4: this cannot be judged when an event arrives, because that asks
    whether the window is empty at the moment something filled it. It is judged
    at a boundary — the first event past the window, or `session.ended`.
    """

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    type: Literal["none"]
    window_sec: int = Field(gt=0)
    zone_id: str | None = None


RuleCondition = ThresholdCondition | AnyCondition | NoneCondition


class SlackAction(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    type: Literal["slack"]
    channel: str = Field(min_length=1)
    message: str = Field(min_length=1)


class WebhookAction(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    type: Literal["webhook"]
    url: str = Field(min_length=1)
    #: Merged into the POST body alongside the firing. Whatever the operator's
    #: receiver needs to route it.
    payload: dict[str, Any] = Field(default_factory=dict)


class ScreenSwapAction(BaseModel):
    """Change what a screen in the room is showing."""

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    type: Literal["screen_swap"]
    screen_id: str = Field(min_length=1)
    content_id: str = Field(min_length=1)


class StaffPromptAction(BaseModel):
    """Tell a human on the floor to do something. The `< 3s` in the Phase 3
    acceptance criterion is mostly about this one — the others can be a second
    late without anybody noticing."""

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    type: Literal["staff_prompt"]
    message: str = Field(min_length=1)
    #: Where in the room the prompt is about, so a screen can place it.
    zone_id: str | None = None
    priority: Literal["low", "normal", "high"] = "normal"


class LogAction(BaseModel):
    """Do nothing but say so. The action for a rule an operator is still
    trialling, and the one every test uses."""

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    type: Literal["log"]
    message: str = ""


RuleAction = (
    SlackAction | WebhookAction | ScreenSwapAction | StaffPromptAction | LogAction
)


class RuleIn(BaseModel):
    """A rule document as an operator POSTs it.

    ## Why triggerType is validated loosely and the rest strictly

    `triggerType` reuses `EVENT_NAMESPACES` — the same prefix test the bus
    applies to an incoming event, with the same known gap (a typo in the suffix
    passes). That is ADR-002's explicit choice: "`triggerType` is any type
    registered in event-bus-spec.md §3 — **not** a closed enum … a rule spec that
    cannot name `intent.scored` or `spatial.tagged` the day those producers land
    would force a spec migration to use them, which is the tax Phase 3's
    pre-registration was meant to avoid."

    `condition.type` and `action.type` are the opposite: closed unions, because
    unlike the taxonomy they are not additive by design. An action type nothing
    dispatches is a rule that looks armed and does nothing, which is worse than
    a 422 at the moment of saving.
    """

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    rule_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    trigger_type: str = Field(min_length=1)
    trigger_zone_id: str | None = None
    condition: RuleCondition = Field(discriminator="type")
    action: RuleAction = Field(discriminator="type")
    enabled: bool = True
    #: Seconds of event time between two firings of the same rule. Zero is
    #: allowed and means "every match", which is a defensible choice for a `log`
    #: rule and a bad one for Slack.
    cooldown_sec: int = Field(default=60, ge=0)

    @field_validator("trigger_type")
    @classmethod
    def trigger_is_in_a_known_namespace(cls, value: str) -> str:
        if not value.startswith(EVENT_NAMESPACES):
            raise ValueError(
                f"unknown event namespace in triggerType {value!r}; "
                f"expected one of {', '.join(EVENT_NAMESPACES)} "
                "(taxonomy: docs/event-bus-spec.md §3)"
            )
        return value


class RuleOut(RuleIn):
    """A stored rule. `tenantId` appears here and not on RuleIn: the tenant comes
    from the authenticated principal, never from the body, or an operator could
    write a rule into somebody else's booth."""

    model_config = ConfigDict(
        from_attributes=True, alias_generator=_to_camel, populate_by_name=True
    )

    tenant_id: str
    created_at: dt.datetime
    updated_at: dt.datetime


def to_wire(row) -> dict:
    """An event_log row as the canonical RealmEvent the browser expects.

    Used by the HTTP router and by the broadcast consumer, so it lives with the
    wire shapes rather than in either caller.
    """
    return EventOut.model_validate(row).model_dump(by_alias=True, mode="json")
