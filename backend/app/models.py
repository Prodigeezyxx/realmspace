"""Pydantic models matching dashboard/src/lib/contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

RealmEventType = Literal[
    "perception.detection",
    "spatial.zone_enter",
    "spatial.zone_exit",
    "spatial.dwell",
    "spatial.gaze",
    "spatial.group",
    "spatial.passby",
    "surface.interaction",
    "consent.captured",
    "consent.withdrawn",
    "identity.resolved",
    "rule.fired",
    "insight.generated",
    "handoff.lead",
    "cost.metered",
    "session.started",
    "session.ended",
]

PII_EVENT_TYPES = frozenset(
    {
        "consent.captured",
        "consent.withdrawn",
        "identity.resolved",
        "handoff.lead",
    }
)


class RealmEventInput(BaseModel):
    tenantId: str
    sessionId: str
    type: RealmEventType
    payload: dict[str, Any] = Field(default_factory=dict)
    eventId: str | None = None
    occurredAt: int | None = None  # ms epoch
    consentProof: dict[str, Any] | None = None


class RealmEvent(BaseModel):
    seq: int
    eventId: str
    tenantId: str
    sessionId: str
    type: RealmEventType
    payload: dict[str, Any]
    occurredAt: int
    recordedAt: int


class EventBatch(BaseModel):
    events: list[RealmEventInput]


class AuthResolveResponse(BaseModel):
    userId: str
    email: str
    displayName: str | None
    orgId: str
    role: Literal["admin", "operator", "analyst", "viewer"]


class GraphSnapshot(BaseModel):
    tenantId: str
    sessionId: str
    persons: list[dict[str, Any]]
    zones: list[dict[str, Any]]
    surfaces: list[dict[str, Any]]
    contacts: list[dict[str, Any]]
    consents: list[dict[str, Any]]
    edges: list[dict[str, Any]]


class HealthResponse(BaseModel):
    status: str
    version: str
    database: str
