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

    #: **Omitted leaves the zone set untouched; a list replaces it entirely**,
    #: including an empty one. The wizard always posts the whole set, so a zone
    #: the operator deleted has to disappear rather than linger and keep
    #: collecting dwell — see graph.repository.prune_zones.
    zones: list[ZoneConfig] | None = None

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


class SessionConfigOut(SessionConfigIn):
    """What comes back. Zones are always a list here — never None."""

    zones: list[ZoneConfig] = []


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


def to_wire(row) -> dict:
    """An event_log row as the canonical RealmEvent the browser expects.

    Used by the HTTP router and by the broadcast consumer, so it lives with the
    wire shapes rather than in either caller.
    """
    return EventOut.model_validate(row).model_dump(by_alias=True, mode="json")
