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

from pydantic import BaseModel, ConfigDict, Field


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


class EventOut(EventIn):
    """What comes back out. Adds the two server-assigned fields."""

    model_config = ConfigDict(from_attributes=True)

    seq: int
    recorded_at: dt.datetime
