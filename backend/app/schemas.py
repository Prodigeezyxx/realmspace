"""
Wire shapes. Pydantic validates these before anything touches the database, so a
malformed producer gets a 422 instead of a half-written row.

Field names are snake_case versions of EventContext in
dashboard/src/lib/event-context.ts (tenantId, sessionId). When the WebSocket
bridge lands in a later Phase-1 item, that's a rename, not a reshape.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    "consent.",
    "identity.",
    "rule.",
    "handoff.",
    "insight.",
    "cost.",
    "session.",
)


class EventIn(BaseModel):
    """What a producer POSTs.

    event_id is supplied by the *producer*, not generated here. That is what
    makes retries safe: if the POST times out and the producer resends, it sends
    the same event_id and the log dedupes it (event-bus-spec.md §2).
    """

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
    """What comes back out. Adds the two server-assigned fields."""

    model_config = ConfigDict(from_attributes=True)

    seq: int
    recorded_at: dt.datetime
