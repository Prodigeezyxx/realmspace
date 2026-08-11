"""realmspace edge API — FastAPI entrypoint."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app import __version__, ask, attribute, bus, db, graph_writer, rules, scorecard
from app.config import get_settings
from app.hub import hub
from app.models import (
    AskRequest,
    AskResponse,
    AuthResolveResponse,
    ConsentCaptureRequest,
    ConsentResponse,
    CrmConnectionConfig,
    CrmSyncRequest,
    EventBatch,
    GraphSnapshot,
    HealthResponse,
    IdentityResolveRequest,
    IntentScoreRequest,
    LeadHandoffRequest,
    RealmEvent,
    RealmEventInput,
    RuleCreateRequest,
    RuleDefinition,
    RulesListResponse,
    RuleTestRequest,
    RuleTestResponse,
    RuleUpdateRequest,
    SessionMeta,
    SessionOutcome,
)

_loop: asyncio.AbstractEventLoop | None = None


def _on_bus_event(event: RealmEvent) -> None:
    """Sync bus subscriber → graph writer + rules engine + schedule WS broadcast."""
    graph_writer.on_bus_event(event)
    rules.evaluate_rules(
        event.type,
        event.payload,
        event.tenantId,
        event.sessionId,
        event.occurredAt,
        event.seq,
    )
    if _loop and _loop.is_running():
        asyncio.run_coroutine_threadsafe(hub.broadcast(event), _loop)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _loop
    _loop = asyncio.get_running_loop()
    db.init_db()
    bus.subscribe(_on_bus_event)
    # Catch up any backlog for the default tenant
    settings = get_settings()
    graph_writer.process_pending(settings.default_tenant_id)
    yield


app = FastAPI(
    title="realmspace edge API",
    version=__version__,
    description="Append-only event bus + relational graph projection for the edge kit.",
    lifespan=lifespan,
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__, database=db.backend_name())


@app.post("/v1/events", response_model=RealmEvent)
def post_event(body: RealmEventInput) -> RealmEvent:
    try:
        return bus.append(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/v1/events/batch", response_model=list[RealmEvent])
def post_events(body: EventBatch) -> list[RealmEvent]:
    try:
        return bus.append_many(body.events)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/v1/events", response_model=list[RealmEvent])
def get_events(
    tenant_id: str = Query(..., alias="tenantId"),
    session_id: str = Query(..., alias="sessionId"),
    after_seq: int = Query(0, alias="afterSeq"),
    limit: int = Query(500, ge=1, le=5000),
    type: list[str] | None = Query(None),
) -> list[RealmEvent]:
    return bus.read(
        tenant_id,
        session_id,
        after_seq=after_seq,
        limit=limit,
        types=type,
    )


@app.get("/v1/graph/{tenant_id}/{session_id}", response_model=GraphSnapshot)
def get_graph(tenant_id: str, session_id: str) -> GraphSnapshot:
    graph_writer.process_pending(tenant_id)
    return graph_writer.snapshot(tenant_id, session_id)


@app.get("/v1/sessions/{tenant_id}", response_model=list[SessionMeta])
def list_sessions(tenant_id: str) -> list[SessionMeta]:
    """Recorded sessions for a tenant (most recent first) — for replay pickers."""
    return [SessionMeta(**meta) for meta in bus.list_sessions(tenant_id)]


def _parse_json_query(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"invalid JSON query param: {exc}") from exc


@app.get("/v1/sessions/{tenant_id}/{session_id}/outcome", response_model=SessionOutcome)
def session_outcome(
    tenant_id: str,
    session_id: str,
    activationCost: float | None = Query(None),
    revenueInfluenced: float | None = Query(None),
    qualifiedLeads: int | None = Query(None),
    engagedThresholdSec: float = Query(60.0, gt=0),
    maxDwellSec: float = Query(scorecard.MAX_DWELL_SEC, gt=0),
    zoneConfig: str | None = Query(None, description='JSON list of {"id","kind","weight"}'),
    expectedDwellSecByKind: str | None = Query(None, description="JSON map kind -> expected dwell seconds"),
) -> SessionOutcome:
    """4-layer ROI scorecard for one session, computed over the durable bus.

    Zone weights/kinds are optional config (JSON) for dwell-weighted
    attention + per-touchpoint-type normalization; until a session.started
    producer carries them, callers pass the activation's zone config.
    """
    zones = _parse_json_query(zoneConfig, None)
    if zones is not None and not isinstance(zones, list):
        raise HTTPException(status_code=400, detail="zoneConfig must be a JSON list")
    expected = _parse_json_query(expectedDwellSecByKind, None)
    if expected is not None and not isinstance(expected, dict):
        raise HTTPException(status_code=400, detail="expectedDwellSecByKind must be a JSON object")

    params = scorecard.OutcomeParams(
        activation_cost=activationCost,
        revenue_influenced=revenueInfluenced,
        qualified_leads=qualifiedLeads,
        engaged_threshold_sec=engagedThresholdSec,
        max_dwell_sec=maxDwellSec,
        zone_config={z["id"]: {"kind": z.get("kind", "other"), "weight": z.get("weight", 1.0)} for z in (zones or []) if z.get("id")},
        expected_dwell_sec_by_kind=expected or {},
    )
    result = scorecard.compute_outcome(tenant_id, session_id, params)
    return SessionOutcome(**result)


@app.post("/v1/consumers/graph_writer/drain")
def drain_graph_writer(tenant_id: str = Query(..., alias="tenantId")) -> dict[str, Any]:
    n = graph_writer.process_pending(tenant_id)
    return {"processed": n, "consumer": "graph_writer", "tenantId": tenant_id}


@app.get("/v1/auth/resolve", response_model=AuthResolveResponse)
def auth_resolve(email: str = Query(...)) -> AuthResolveResponse:
    """RBAC skeleton: resolve email → org → role (local seed users)."""
    row = db.fetchone("SELECT * FROM auth_users WHERE email = ?", (email.lower(),))
    if not row:
        # Unknown users land as viewer on default tenant for local demos
        return AuthResolveResponse(
            userId="u_anonymous",
            email=email.lower(),
            displayName=None,
            orgId=get_settings().default_tenant_id,
            role="viewer",
        )
    role = row["role"]
    if role not in ("admin", "operator", "analyst", "viewer"):
        role = "viewer"
    return AuthResolveResponse(
        userId=row["user_id"],
        email=row["email"],
        displayName=row["display_name"],
        orgId=row["org_id"],
        role=role,  # type: ignore[arg-type]
    )


@app.post("/v1/ask", response_model=AskResponse)
def ask_room(body: AskRequest) -> AskResponse:
    """Ask the Room — NL query against the session's event data.

    Uses constrained SQL templates (ADR-001: relational projection, not Cypher).
    When OPENROUTER_API_KEY is configured, an LLM selects the best template;
    otherwise a regex stub maps questions to templates.
    """
    result = ask.ask(body.question, body.tenantId, body.sessionId)
    return AskResponse(
        question=body.question,
        answer=result.get("answer", ""),
        chartType=result.get("chart_type"),
        template=result.get("template"),
        fallback=result.get("fallback", False),
        table=result.get("table"),
        labels=result.get("labels"),
        values=result.get("values"),
        value=result.get("value"),
    )


# ── RULES ENGINE (Phase 3) ──────────────────────────────────────────────────
# Persisted rules, CRUD, dry-run testing.


@app.get("/v1/rules/{tenant_id}", response_model=RulesListResponse)
def list_rules(tenant_id: str) -> RulesListResponse:
    recs = rules.load_rules(tenant_id)
    return RulesListResponse(rules=[
        RuleDefinition(
            ruleId=r.rule_id,
            tenantId=r.tenant_id,
            name=r.name,
            triggerType=r.trigger_type,
            triggerZoneId=r.trigger_config.get("zoneId") if r.trigger_config else None,
            condition=r.condition_config,
            action=r.action_config,
            enabled=r.enabled,
            cooldownSec=r.cooldown_sec,
        )
        for r in recs
    ])


@app.post("/v1/rules", response_model=RuleDefinition, status_code=201)
def create_rule(body: RuleCreateRequest) -> RuleDefinition:
    rule_id = f"r_{body.tenantId}_{body.triggerType}_{int(time.time())}"
    trigger_config = {"zoneId": body.triggerZoneId} if body.triggerZoneId else {}
    rules.save_rule(
        rule_id=rule_id,
        tenant_id=body.tenantId,
        name=body.name,
        trigger_type=body.triggerType,
        trigger_config=trigger_config,
        condition_config=body.condition.model_dump(),
        action_type=body.action.type,
        action_config=body.action.model_dump(),
        cooldown_sec=body.cooldownSec,
    )
    return RuleDefinition(
        ruleId=rule_id,
        tenantId=body.tenantId,
        name=body.name,
        triggerType=body.triggerType,
        triggerZoneId=body.triggerZoneId,
        condition=body.condition,
        action=body.action,
        enabled=True,
        cooldownSec=body.cooldownSec,
    )


@app.put("/v1/rules/{rule_id}", response_model=dict)
def update_rule_endpoint(rule_id: str, body: RuleUpdateRequest) -> dict:
    updates = {}
    if body.name is not None:
        updates["name"] = body.name
    if body.condition is not None:
        updates["condition_config"] = body.condition.model_dump()
    if body.action is not None:
        updates["action_type"] = body.action.type
        updates["action_config"] = body.action.model_dump()
    if body.enabled is not None:
        updates["enabled"] = 1 if body.enabled else 0
    if body.cooldownSec is not None:
        updates["cooldown_sec"] = body.cooldownSec

    ok = rules.update_rule(rule_id, updates)
    if not ok:
        raise HTTPException(status_code=404, detail="rule not found")
    return {"ok": True, "ruleId": rule_id}


@app.delete("/v1/rules/{rule_id}", response_model=dict)
def delete_rule_endpoint(rule_id: str) -> dict:
    ok = rules.delete_rule(rule_id)
    if not ok:
        raise HTTPException(status_code=404, detail="rule not found")
    return {"ok": True, "ruleId": rule_id}


@app.post("/v1/rules/test", response_model=RuleTestResponse)
def test_rule_endpoint(body: RuleTestRequest) -> RuleTestResponse:
    result = rules.test_rule(
        tenant_id=body.tenantId,
        session_id=body.sessionId,
        trigger_type=body.rule.triggerType,
        trigger_zone_id=body.rule.triggerZoneId,
        condition=body.rule.condition.model_dump(),
    )
    return RuleTestResponse(**result)


import time  # noqa: E402 — needed for create_rule timestamp


# ── ATTRIBUTE (Phase 4): Consent, Identity, Intent, Handoff, CRM ────────────


@app.post("/v1/consent", response_model=ConsentResponse)
def capture_consent(body: ConsentCaptureRequest) -> ConsentResponse:
    result = attribute.capture_consent(
        tenant_id=body.tenantId,
        session_id=body.sessionId,
        anon_id=body.anonId,
        tier=body.tier,
        method=body.method,
        contact_email=body.contactEmail,
        contact_name=body.contactName,
        contact_phone=body.contactPhone,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return ConsentResponse(**result)


@app.post("/v1/consent/{consent_id}/withdraw")
def withdraw_consent(consent_id: str) -> dict:
    result = attribute.withdraw_consent(consent_id)
    if "error" in result:
        raise HTTPException(status_code=404 if "not found" in result["error"] else 400, detail=result["error"])
    return result


@app.get("/v1/consent/{tenant_id}")
def list_consents(tenant_id: str, sessionId: str | None = None) -> list[dict]:
    return attribute.list_consents(tenant_id, sessionId)


@app.post("/v1/identity/resolve")
def resolve_identity(body: IdentityResolveRequest) -> dict:
    result = attribute.resolve_identity(
        consent_id=body.consentId,
        anon_id=body.anonId,
        contact_email=body.contactEmail,
        contact_name=body.contactName,
        contact_phone=body.contactPhone,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.get("/v1/identity/{tenant_id}")
def list_identities(tenant_id: str, sessionId: str | None = None) -> list[dict]:
    return attribute.list_identities(tenant_id, sessionId)


@app.post("/v1/intent/score")
def score_intent(body: IntentScoreRequest) -> dict:
    return attribute.score_intent(body.tenantId, body.sessionId, body.anonId)


@app.post("/v1/handoff")
def create_handoff(body: LeadHandoffRequest) -> dict:
    result = attribute.build_lead_handoff(
        tenant_id=body.tenantId,
        session_id=body.sessionId,
        anon_id=body.anonId,
        attribution_model=body.attributionModel,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.get("/v1/handoff/{tenant_id}")
def list_handoffs(tenant_id: str, sessionId: str | None = None) -> list[dict]:
    return attribute.list_handoffs(tenant_id, sessionId)


@app.post("/v1/handoff/{handoff_id}/sync")
def sync_handoff(handoff_id: str, body: CrmSyncRequest) -> dict:
    result = attribute.sync_to_crm(handoff_id, body.crmType, body.apiConfig)
    if result.get("synced") or result.get("stub"):
        attribute.update_handoff_status(handoff_id, "synced" if result["synced"] else "stub",
                                         f"{body.crmType}:{'ok' if result['synced'] else 'stub' if result.get('stub') else 'error'}")
    else:
        attribute.update_handoff_status(handoff_id, "failed", f"{body.crmType}:error")
    return result


@app.put("/v1/crm/connection")
def upsert_crm_connection(body: CrmConnectionConfig) -> dict:
    now = attribute._now_iso()
    db.execute(
        """INSERT INTO crm_connections (tenant_id, crm_type, api_config, field_mapping, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(tenant_id, crm_type) DO UPDATE SET
           api_config=excluded.api_config, field_mapping=excluded.field_mapping,
           updated_at=excluded.updated_at""",
        (body.tenantId, body.crmType, json.dumps(body.apiConfig),
         json.dumps(body.fieldMapping), now, now),
    )
    return {"ok": True, "tenantId": body.tenantId, "crmType": body.crmType}


@app.get("/v1/crm/connection/{tenant_id}")
def get_crm_connections(tenant_id: str) -> list[dict]:
    rows = db.fetchall("SELECT * FROM crm_connections WHERE tenant_id = ?", (tenant_id,))
    return [
        {
            "tenantId": r["tenant_id"], "crmType": r["crm_type"],
            "apiConfig": json.loads(r["api_config"]),
            "fieldMapping": json.loads(r["field_mapping"]),
            "healthStatus": r["health_status"], "lastCheckedAt": r["last_checked_at"],
        }
        for r in rows
    ]


@app.websocket("/v1/ws/{tenant_id}/{session_id}")
async def ws_session(websocket: WebSocket, tenant_id: str, session_id: str) -> None:
    await hub.connect(tenant_id, session_id, websocket)
    # Catch-up: send recent events so the client can hydrate
    recent = bus.read(tenant_id, session_id, after_seq=0, limit=200)
    await websocket.send_json(
        {
            "type": "hello",
            "tenantId": tenant_id,
            "sessionId": session_id,
            "replay": [e.model_dump() for e in recent],
        }
    )
    try:
        while True:
            # Keepalive / client pings; we ignore payload content for now
            await websocket.receive_text()
    except WebSocketDisconnect:
        await hub.disconnect(tenant_id, session_id, websocket)
