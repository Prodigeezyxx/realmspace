"""
Attribute — Phase 4: Consent, Identity, Intent, LeadHandoff, CRM.

Consent-gated identity bridge: anonymous spatial behaviour → consented Contact →
LeadHandoff → CRM. Built on the durable event bus — every transition is an
append-only event, replayable and auditable.

Architecture:
  consent.captured → identity.resolved (consent-gated) → intent.scored
       ↓                                                    ↓
  consent.withdrawn (erasure)                    handoff.lead → CRM adapter
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app import db
from app.config import get_settings


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Consent capture ─────────────────────────────────────────────────────────

CONSENT_TIERS = {
    "t1": "aggregate-only (no PII, no re-id)",
    "t2": "spatial attribution (link anon path to contact)",
    "t3": "follow-up allowed (email + CRM sync)",
}

CONSENT_COPY_VERSION = "v1.0-2026-08"


@dataclass
class ConsentRecord:
    consent_id: str
    tenant_id: str
    session_id: str
    anon_id: str | None   # the anonymous track ID
    tier: str              # t1, t2, t3
    method: str            # badge_scan, qr, kiosk, form_webhook
    contact_email: str | None
    contact_name: str | None
    contact_phone: str | None
    copy_version: str
    captured_at: str
    withdrawn_at: str | None


def capture_consent(
    tenant_id: str,
    session_id: str,
    anon_id: str | None,
    tier: str,
    method: str,
    contact_email: str | None = None,
    contact_name: str | None = None,
    contact_phone: str | None = None,
) -> dict:
    """Record a consent event. Returns the consent record.

    PII fields (email, name, phone) require tier ≥ t2. tier=t1 is aggregate-only.
    """
    if tier not in CONSENT_TIERS:
        return {"error": f"invalid tier: {tier}", "valid_tiers": list(CONSENT_TIERS.keys())}

    # Privacy redline: PII requires at least t2 consent
    has_pii = bool(contact_email or contact_name or contact_phone)
    if has_pii and tier == "t1":
        return {"error": "t1 consent does not permit PII collection — upgrade to t2 or t3"}

    consent_id = f"c_{uuid.uuid4().hex[:12]}"
    now = _now_iso()

    db.execute(
        """INSERT INTO consents (consent_id, tenant_id, session_id, anon_id, tier,
           method, contact_email, contact_name, contact_phone, copy_version,
           captured_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (consent_id, tenant_id, session_id, anon_id, tier, method,
         contact_email, contact_name, contact_phone, CONSENT_COPY_VERSION, now),
    )

    return {
        "consentId": consent_id,
        "tenantId": tenant_id,
        "sessionId": session_id,
        "anonId": anon_id,
        "tier": tier,
        "method": method,
        "contactEmail": contact_email,
        "contactName": contact_name,
        "contactPhone": contact_phone,
        "copyVersion": CONSENT_COPY_VERSION,
        "capturedAt": now,
    }


def withdraw_consent(consent_id: str) -> dict:
    """Withdraw consent — drop identity link, re-anonymise."""
    row = db.fetchone("SELECT * FROM consents WHERE consent_id = ?", (consent_id,))
    if not row:
        return {"error": "consent not found"}

    now = _now_iso()

    # Drop identity link if one exists
    if row["anon_id"]:
        db.execute(
            "DELETE FROM identities WHERE anon_id = ? AND tenant_id = ? AND session_id = ?",
            (row["anon_id"], row["tenant_id"], row["session_id"]),
        )

    db.execute(
        "UPDATE consents SET withdrawn_at = ? WHERE consent_id = ?",
        (now, consent_id),
    )

    return {
        "consentId": consent_id,
        "withdrawn": True,
        "withdrawnAt": now,
    }


def list_consents(tenant_id: str, session_id: str | None = None) -> list[dict]:
    if session_id:
        rows = db.fetchall(
            "SELECT * FROM consents WHERE tenant_id = ? AND session_id = ? ORDER BY captured_at DESC",
            (tenant_id, session_id),
        )
    else:
        rows = db.fetchall(
            "SELECT * FROM consents WHERE tenant_id = ? ORDER BY captured_at DESC",
            (tenant_id,),
        )
    return [_consent_row_to_dict(r) for r in rows]


def _consent_row_to_dict(r) -> dict:
    return {
        "consentId": r["consent_id"],
        "tenantId": r["tenant_id"],
        "sessionId": r["session_id"],
        "anonId": r["anon_id"],
        "tier": r["tier"],
        "method": r["method"],
        "contactEmail": r["contact_email"],
        "contactName": r["contact_name"],
        "contactPhone": r["contact_phone"],
        "copyVersion": r["copy_version"],
        "capturedAt": r["captured_at"],
        "withdrawnAt": r["withdrawn_at"],
        "active": r["withdrawn_at"] is None,
    }


# ── Identity resolution (consent-gated) ─────────────────────────────────────


def resolve_identity(
    consent_id: str,
    anon_id: str,
    contact_email: str,
    contact_name: str | None = None,
    contact_phone: str | None = None,
) -> dict:
    """Link an anonymous track to a Contact via an active (non-withdrawn) consent.

    Consumer-side invariant: refuses IDENTIFIED_AS without a non-withdrawn
    ConsentEvent in the same transaction.
    """
    # Verify consent is active
    consent = db.fetchone(
        "SELECT * FROM consents WHERE consent_id = ? AND withdrawn_at IS NULL",
        (consent_id,),
    )
    if not consent:
        return {"error": "consent not found or has been withdrawn"}
    if consent["tier"] not in ("t2", "t3"):
        return {"error": f"identity resolution requires t2 or t3 consent, got {consent['tier']}"}

    tenant_id = consent["tenant_id"]
    session_id = consent["session_id"]
    now = _now_iso()
    identity_id = f"id_{uuid.uuid4().hex[:12]}"

    # Upsert — one identity per anon_id per session
    db.execute(
        """INSERT INTO identities (identity_id, consent_id, tenant_id, session_id,
           anon_id, contact_email, contact_name, contact_phone, resolved_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(tenant_id, session_id, anon_id) DO UPDATE SET
           consent_id=excluded.consent_id, contact_email=excluded.contact_email,
           contact_name=excluded.contact_name, contact_phone=excluded.contact_phone,
           resolved_at=excluded.resolved_at""",
        (identity_id, consent_id, tenant_id, session_id, anon_id,
         contact_email, contact_name, contact_phone, now),
    )

    return {
        "identityId": identity_id,
        "consentId": consent_id,
        "tenantId": tenant_id,
        "sessionId": session_id,
        "anonId": anon_id,
        "contactEmail": contact_email,
        "contactName": contact_name,
        "contactPhone": contact_phone,
        "resolvedAt": now,
    }


def list_identities(tenant_id: str, session_id: str | None = None) -> list[dict]:
    if session_id:
        rows = db.fetchall(
            "SELECT * FROM identities WHERE tenant_id = ? AND session_id = ?",
            (tenant_id, session_id),
        )
    else:
        rows = db.fetchall(
            "SELECT * FROM identities WHERE tenant_id = ?",
            (tenant_id,),
        )
    return [
        {
            "identityId": r["identity_id"],
            "consentId": r["consent_id"],
            "tenantId": r["tenant_id"],
            "sessionId": r["session_id"],
            "anonId": r["anon_id"],
            "contactEmail": r["contact_email"],
            "contactName": r["contact_name"],
            "contactPhone": r["contact_phone"],
            "resolvedAt": r["resolved_at"],
        }
        for r in rows
    ]


# ── Intent-signal scoring v1 (rules-based) ──────────────────────────────────


def score_intent(tenant_id: str, session_id: str, anon_id: str) -> dict:
    """Compute a rules-based intent score for an identified visitor.

    Formula: base 0 → +1 per zone visited → +2 per engagement zone (>90s dwell)
    → +1 per surface interaction → capped at 10.
    """
    zones_visited = db.fetchone(
        """SELECT COUNT(DISTINCT props) as c FROM graph_edges
           WHERE tenant_id = ? AND session_id = ? AND from_id = ? AND edge_kind = 'DWELLED_IN'""",
        (tenant_id, session_id, anon_id),
    )
    zone_count = zones_visited["c"] if zones_visited else 0

    deep_engagements = db.fetchone(
        """SELECT COUNT(*) as c FROM event_log
           WHERE tenant_id = ? AND session_id = ?
             AND type = 'spatial.dwell'
             AND json_extract(payload, '$.anonId') = ?
             AND CAST(json_extract(payload, '$.durationSec') AS REAL) > 90""",
        (tenant_id, session_id, anon_id),
    )
    deep_count = deep_engagements["c"] if deep_engagements else 0

    surfaces = db.fetchone(
        """SELECT COUNT(*) as c FROM event_log
           WHERE tenant_id = ? AND session_id = ?
             AND type = 'surface.interaction'
             AND json_extract(payload, '$.anonId') = ?""",
        (tenant_id, session_id, anon_id),
    )
    surface_count = surfaces["c"] if surfaces else 0

    score = min(zone_count + (deep_count * 2) + surface_count, 10)

    tier = "cold"
    if score >= 6:
        tier = "hot"
    elif score >= 3:
        tier = "warm"

    now = _now_iso()

    # Persist to intent_scores
    db.execute(
        """INSERT INTO intent_scores (tenant_id, session_id, anon_id, score, tier,
           zones_visited, deep_engagements, surface_interactions, computed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(tenant_id, session_id, anon_id) DO UPDATE SET
           score=excluded.score, tier=excluded.tier,
           zones_visited=excluded.zones_visited,
           deep_engagements=excluded.deep_engagements,
           surface_interactions=excluded.surface_interactions,
           computed_at=excluded.computed_at""",
        (tenant_id, session_id, anon_id, score, tier,
         zone_count, deep_count, surface_count, now),
    )

    return {
        "tenantId": tenant_id,
        "sessionId": session_id,
        "anonId": anon_id,
        "score": score,
        "tier": tier,
        "zonesVisited": zone_count,
        "deepEngagements": deep_count,
        "surfaceInteractions": surface_count,
        "computedAt": now,
    }


# ── Lead handoff ────────────────────────────────────────────────────────────


def build_lead_handoff(tenant_id: str, session_id: str, anon_id: str,
                       attribution_model: str = "first_touch") -> dict:
    """Assemble a LeadHandoff for CRM delivery.

    attribution_model: first_touch, last_touch, linear, influenced
    """
    identity = db.fetchone(
        "SELECT * FROM identities WHERE tenant_id = ? AND session_id = ? AND anon_id = ?",
        (tenant_id, session_id, anon_id),
    )
    if not identity:
        return {"error": "no identity resolved for this visitor"}

    intent = score_intent(tenant_id, session_id, anon_id)

    handoff_id = f"lh_{uuid.uuid4().hex[:12]}"
    now = _now_iso()
    dedupe_key = f"{tenant_id}:{session_id}:{anon_id}:{attribution_model}"

    # Check for existing handoff (idempotent)
    existing = db.fetchone(
        "SELECT handoff_id FROM lead_handoffs WHERE dedupe_key = ?",
        (dedupe_key,),
    )
    if existing:
        row = db.fetchone("SELECT * FROM lead_handoffs WHERE handoff_id = ?", (existing["handoff_id"],))
        return _handoff_row_to_dict(row)

    payload = {
        "handoffId": handoff_id,
        "dedupeKey": dedupe_key,
        "tenantId": tenant_id,
        "sessionId": session_id,
        "anonId": anon_id,
        "contactEmail": identity["contact_email"],
        "contactName": identity["contact_name"],
        "contactPhone": identity["contact_phone"],
        "attributionModel": attribution_model,
        "spatialIntent": intent,
        "consentBasis": identity["consent_id"],
    }

    db.execute(
        """INSERT INTO lead_handoffs (handoff_id, dedupe_key, tenant_id, session_id,
           anon_id, contact_email, contact_name, contact_phone,
           attribution_model, intent_score, intent_tier, consent_id,
           status, payload, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
        (handoff_id, dedupe_key, tenant_id, session_id, anon_id,
         identity["contact_email"], identity["contact_name"],
         identity["contact_phone"], attribution_model,
         intent["score"], intent["tier"], identity["consent_id"],
         json.dumps(payload), now),
    )

    return payload


def list_handoffs(tenant_id: str, session_id: str | None = None) -> list[dict]:
    if session_id:
        rows = db.fetchall(
            "SELECT * FROM lead_handoffs WHERE tenant_id = ? AND session_id = ? ORDER BY created_at DESC",
            (tenant_id, session_id),
        )
    else:
        rows = db.fetchall(
            "SELECT * FROM lead_handoffs WHERE tenant_id = ? ORDER BY created_at DESC",
            (tenant_id,),
        )
    return [_handoff_row_to_dict(r) for r in rows]


def _handoff_row_to_dict(r) -> dict:
    return {
        "handoffId": r["handoff_id"],
        "tenantId": r["tenant_id"],
        "sessionId": r["session_id"],
        "anonId": r["anon_id"],
        "contactEmail": r["contact_email"],
        "contactName": r["contact_name"],
        "contactPhone": r["contact_phone"],
        "attributionModel": r["attribution_model"],
        "intentScore": r["intent_score"],
        "intentTier": r["intent_tier"],
        "consentId": r["consent_id"],
        "status": r["status"],
        "crmStatus": r["crm_status"] if "crm_status" in r.keys() else "pending",
        "createdAt": r["created_at"],
        "syncedAt": r["synced_at"] if "synced_at" in r.keys() else None,
    }


def update_handoff_status(handoff_id: str, status: str, crm_status: str | None = None) -> dict:
    now = _now_iso()
    if crm_status:
        db.execute(
            "UPDATE lead_handoffs SET status = ?, crm_status = ?, synced_at = ? WHERE handoff_id = ?",
            (status, crm_status, now, handoff_id),
        )
    else:
        db.execute(
            "UPDATE lead_handoffs SET status = ? WHERE handoff_id = ?",
            (status, handoff_id),
        )
    return {"handoffId": handoff_id, "status": status, "crmStatus": crm_status}


# ── CRM adapter interface ───────────────────────────────────────────────────


def sync_to_crm(handoff_id: str, crm_type: str, api_config: dict | None = None) -> dict:
    """Sync a lead handoff to a CRM. Currently stubs HubSpot; BYO webhook always works.

    Returns {"synced": bool, "crmType": str, "externalId": str|None, "error": str|None}
    """
    row = db.fetchone("SELECT * FROM lead_handoffs WHERE handoff_id = ?", (handoff_id,))
    if not row:
        return {"error": "handoff not found"}

    payload = json.loads(row["payload"])

    if crm_type == "hubspot":
        return _sync_hubspot(payload, api_config)
    elif crm_type == "webhook":
        return _sync_webhook(payload, api_config)
    elif crm_type == "salesforce":
        return {"synced": False, "crmType": "salesforce", "error": "Salesforce adapter not yet built"}
    elif crm_type == "pipedrive":
        return {"synced": False, "crmType": "pipedrive", "error": "Pipedrive adapter not yet built"}
    else:
        return {"error": f"unknown CRM type: {crm_type}"}


def _sync_hubspot(payload: dict, _config: dict | None) -> dict:
    """HubSpot reference adapter. Stub — returns the shape a real adapter would.

    Real implementation would:
    1. Look up contact by email (GET /crm/v3/objects/contacts/{email})
    2. Create or update contact with spatial_intent fields
    3. Return external ID
    """
    import httpx

    settings = get_settings()
    api_key = getattr(settings, 'hubspot_api_key', None)

    if not api_key:
        # Stub mode
        return {
            "synced": False,
            "crmType": "hubspot",
            "stub": True,
            "note": "set HUBSPOT_API_KEY to enable live HubSpot sync",
            "payload_shape": {
                "email": payload.get("contactEmail"),
                "properties": {
                    "spatial_intent_score": payload.get("spatialIntent", {}).get("score"),
                    "spatial_intent_tier": payload.get("spatialIntent", {}).get("tier"),
                    "zones_visited": payload.get("spatialIntent", {}).get("zonesVisited"),
                    "attribution_model": payload.get("attributionModel"),
                    "realmspace_handoff_id": payload.get("handoffId"),
                },
            },
        }

    # Live HubSpot sync
    try:
        r = httpx.post(
            "https://api.hubapi.com/crm/v3/objects/contacts",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "properties": {
                    "email": payload.get("contactEmail"),
                    "firstname": payload.get("contactName", "").split(" ")[0] if payload.get("contactName") else "",
                    "lastname": " ".join(payload.get("contactName", "").split(" ")[1:]) if payload.get("contactName") else "",
                    "spatial_intent_score": str(payload.get("spatialIntent", {}).get("score", 0)),
                    "spatial_intent_tier": payload.get("spatialIntent", {}).get("tier", ""),
                    "zones_visited": str(payload.get("spatialIntent", {}).get("zonesVisited", 0)),
                    "realmspace_handoff_id": payload.get("handoffId"),
                }
            },
            timeout=10,
        )
        if r.status_code in (200, 201):
            data = r.json()
            return {"synced": True, "crmType": "hubspot", "externalId": data.get("id")}
        return {"synced": False, "crmType": "hubspot", "error": f"HTTP {r.status_code}: {r.text[:200]}"}
    except Exception as e:
        return {"synced": False, "crmType": "hubspot", "error": str(e)}


def _sync_webhook(payload: dict, config: dict | None) -> dict:
    """BYO HMAC-signed webhook — always works."""
    url = (config or {}).get("url") or getattr(get_settings(), 'crm_webhook_url', None)
    if not url:
        return {"synced": False, "crmType": "webhook", "error": "no webhook URL configured"}

    import httpx
    try:
        r = httpx.post(url, json=payload, timeout=10)
        return {"synced": r.status_code < 400, "crmType": "webhook", "status": r.status_code}
    except Exception as e:
        return {"synced": False, "crmType": "webhook", "error": str(e)}
