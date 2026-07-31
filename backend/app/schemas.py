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
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator


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


def to_wire(row) -> dict:
    """An event_log row as the canonical RealmEvent the browser expects.

    Used by the HTTP router and by the broadcast consumer, so it lives with the
    wire shapes rather than in either caller.
    """
    return EventOut.model_validate(row).model_dump(by_alias=True, mode="json")
