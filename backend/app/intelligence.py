"""
Intelligence — Phase 5: Insight Agent, Contextual SDR, Live Analyst.

Post-session intelligence: periodic bounded graph snapshots → LLM-generated
insights with traceable event IDs. Path-aware follow-up drafts for consented
leads. Live NL queries against the bus (reuses Phase 2 Ask templates).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from openai import OpenAI

from app import db
from app.config import get_settings


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── LLM client ──────────────────────────────────────────────────────────────

def _llm_client() -> OpenAI:
    s = get_settings()
    return OpenAI(api_key=s.openrouter_api_key, base_url=s.openrouter_base_url)


# ── Insight agent ───────────────────────────────────────────────────────────


def generate_insights(tenant_id: str, session_id: str, max_insights: int = 3) -> list[dict]:
    """Periodic bounded graph snapshot → LLM-generated insights.

    Reads current graph state + recent dwell data, sends a digest to the LLM,
    returns structured insights with supporting event IDs.
    """
    # Build a bounded digest of the session
    digest = _build_session_digest(tenant_id, session_id)
    if not digest or digest.get("eventCount", 0) == 0:
        return [{"insight": "No session data available yet.", "kind": "info", "supportingEvents": []}]

    prompt = _insight_prompt(digest)

    insights = []
    try:
        client = _llm_client()
        completion = client.chat.completions.create(
            model=get_settings().openrouter_model,
            messages=[
                {"role": "system", "content": prompt["system"]},
                {"role": "user", "content": prompt["user"]},
            ],
            temperature=0.3,
            max_tokens=600,
        )
        raw = completion.choices[0].message.content or "[]"
        # Strip markdown fences
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("\n```", 1)[0]
        insights = json.loads(raw)
        if not isinstance(insights, list):
            insights = [{"insight": str(insights), "kind": "info", "supportingEvents": []}]
    except Exception:
        # Fallback: rules-based insights
        insights = _fallback_insights(digest)

    # Persist each insight
    now = _now_iso()
    for ins in insights[:max_insights]:
        iid = f"ins_{uuid.uuid4().hex[:12]}"
        db.execute(
            """INSERT INTO insights (insight_id, tenant_id, session_id, kind,
               insight_text, supporting_event_seqs, generated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (iid, tenant_id, session_id,
             ins.get("kind", "info"),
             ins.get("insight", ""),
             json.dumps(ins.get("supportingEvents", [])),
             now),
        )
        ins["insightId"] = iid
        ins["tenantId"] = tenant_id
        ins["sessionId"] = session_id
        ins["generatedAt"] = now

    return insights[:max_insights]


def _build_session_digest(tenant_id: str, session_id: str) -> dict:
    """Build a token-bounded digest of session state for the LLM."""
    try:
        event_count = db.fetchone(
            "SELECT COUNT(*) as c FROM event_log WHERE tenant_id = ? AND session_id = ?",
            (tenant_id, session_id),
        )
        persons = db.fetchone(
            "SELECT COUNT(DISTINCT node_id) as c FROM graph_nodes WHERE tenant_id = ? AND session_id = ? AND node_kind = 'Person'",
            (tenant_id, session_id),
        )
        zones = db.fetchall(
            "SELECT node_id, props FROM graph_nodes WHERE tenant_id = ? AND session_id = ? AND node_kind = 'Zone'",
            (tenant_id, session_id),
        )
        surfaces = db.fetchone(
            "SELECT COUNT(*) as c FROM event_log WHERE tenant_id = ? AND session_id = ? AND type = 'surface.interaction'",
            (tenant_id, session_id),
        )
        dwell_avg = db.fetchone(
            """SELECT ROUND(AVG(CAST(json_extract(payload, '$.durationSec') AS REAL)), 1) as avg
               FROM event_log WHERE tenant_id = ? AND session_id = ? AND type = 'spatial.dwell'
               AND CAST(json_extract(payload, '$.durationSec') AS REAL) > 0
               AND CAST(json_extract(payload, '$.durationSec') AS REAL) <= 7200""",
            (tenant_id, session_id),
        )
        # Intents
        intents = db.fetchall(
            "SELECT * FROM intent_scores WHERE tenant_id = ? AND session_id = ? AND tier != 'cold' ORDER BY score DESC LIMIT 5",
            (tenant_id, session_id),
        )
        # Handoffs
        handoff_count = db.fetchone(
            "SELECT COUNT(*) as c FROM lead_handoffs WHERE tenant_id = ? AND session_id = ?",
            (tenant_id, session_id),
        )

        return {
            "eventCount": event_count["c"] if event_count else 0,
            "uniquePersons": persons["c"] if persons else 0,
            "zones": [
                {"id": z["node_id"], "label": json.loads(z["props"]).get("label", z["node_id"])}
                for z in zones
            ],
            "surfaceInteractions": surfaces["c"] if surfaces else 0,
            "avgDwellSec": dwell_avg["avg"] if dwell_avg and dwell_avg["avg"] else 0,
            "leads": len(intents),
            "handoffs": handoff_count["c"] if handoff_count else 0,
            "topIntents": [
                {"anonId": i["anon_id"], "score": i["score"], "tier": i["tier"]}
                for i in intents[:5]
            ],
        }
    except Exception:
        return {"eventCount": 0}


def _insight_prompt(digest: dict) -> dict:
    system = """You are the Insight Agent for RealmSpace, an analytics platform for physical brand experiences.

You receive a digest of session data and produce 1-3 concise, actionable insights.
Each insight MUST be a JSON object with: "insight" (the human-readable text),
"kind" (one of: "opportunity", "warning", "info", "highlight"),
"supportingEvents" (empty array — IDs are filled by the engine).

Rules:
- Be specific: cite numbers where available (dwell times, counts, scores)
- Be actionable: tell the operator what to DO, not just what happened
- Highlight positive signals (high dwell, many leads) AND warning signals (quiet zones, low engagement)
- Keep insights under 120 characters
- Return ONLY a JSON array, no other text

Example:
[{"insight": "z_screen had 2.9x average dwell — consider adding a second screen or extending content.", "kind": "opportunity", "supportingEvents": []}]
"""

    user = f"""Session digest:
- {digest.get('eventCount', 0)} total events
- {digest.get('uniquePersons', 0)} unique visitors
- {len(digest.get('zones', []))} zones: {', '.join(z['label'] for z in digest.get('zones', []))}
- {digest.get('surfaceInteractions', 0)} surface interactions
- {digest.get('avgDwellSec', 0)}s average dwell
- {digest.get('leads', 0)} warm/hot leads, {digest.get('handoffs', 0)} CRM handoffs
{f'- Top intents: {digest.get("topIntents", [])}' if digest.get('topIntents') else '- No warm/hot leads yet'}

Generate 3 insights."""

    return {"system": system, "user": user}


def _fallback_insights(digest: dict) -> list[dict]:
    """Rules-based fallback when LLM is unavailable."""
    insights = []
    ec = digest.get("eventCount", 0)
    up = digest.get("uniquePersons", 0)
    avg = digest.get("avgDwellSec", 0)
    leads = digest.get("leads", 0)
    hoffs = digest.get("handoffs", 0)

    if ec == 0:
        return [{"insight": "No session data available yet.", "kind": "info", "supportingEvents": []}]

    if up > 0:
        insights.append({"insight": f"{up} unique visitors tracked — session is recording normally.", "kind": "info", "supportingEvents": []})
    if avg > 60:
        insights.append({"insight": f"Average dwell of {avg:.0f}s is above the 60s engagement threshold — strong engagement.", "kind": "highlight", "supportingEvents": []})
    elif avg > 0:
        insights.append({"insight": f"Average dwell of {avg:.0f}s is below the 60s engagement benchmark — consider interactive elements.", "kind": "warning", "supportingEvents": []})
    if leads > 0:
        insights.append({"insight": f"{leads} warm/hot leads detected — follow up within 24h.", "kind": "opportunity", "supportingEvents": []})
    if hoffs > 0:
        insights.append({"insight": f"{hoffs} leads handed off to CRM.", "kind": "info", "supportingEvents": []})

    return insights[:3] if insights else [{"insight": "Session running.", "kind": "info", "supportingEvents": []}]


def list_insights(tenant_id: str, session_id: str | None = None, limit: int = 20) -> list[dict]:
    if session_id:
        rows = db.fetchall(
            "SELECT * FROM insights WHERE tenant_id = ? AND session_id = ? ORDER BY generated_at DESC LIMIT ?",
            (tenant_id, session_id, limit),
        )
    else:
        rows = db.fetchall(
            "SELECT * FROM insights WHERE tenant_id = ? ORDER BY generated_at DESC LIMIT ?",
            (tenant_id, limit),
        )
    return [
        {
            "insightId": r["insight_id"],
            "tenantId": r["tenant_id"],
            "sessionId": r["session_id"],
            "kind": r["kind"],
            "insight": r["insight_text"],
            "supportingEvents": json.loads(r["supporting_event_seqs"]),
            "generatedAt": r["generated_at"],
        }
        for r in rows
    ]


# ── Contextual SDR (follow-up drafts) ──────────────────────────────────────


def generate_follow_up(anon_id: str, tenant_id: str, session_id: str) -> dict:
    """Generate a path-aware follow-up email draft for a consented lead.

    Requires identity resolution and ≥T2 consent.
    """
    # Check consent
    identity = db.fetchone(
        "SELECT * FROM identities WHERE tenant_id = ? AND session_id = ? AND anon_id = ?",
        (tenant_id, session_id, anon_id),
    )
    if not identity:
        return {"error": "no identity resolved for this visitor — resolve identity first"}

    consent = db.fetchone(
        "SELECT * FROM consents WHERE consent_id = ? AND withdrawn_at IS NULL AND tier IN ('t2','t3')",
        (identity["consent_id"],),
    )
    if not consent:
        return {"error": "no active t2/t3 consent for this visitor"}

    # Build spatial path context
    path_context = _build_path_context(tenant_id, session_id, anon_id)

    # Try LLM draft
    try:
        draft = _llm_draft_follow_up(
            contact_name=identity["contact_name"] or identity["contact_email"],
            path_context=path_context,
        )
    except Exception:
        draft = _fallback_draft(identity, path_context)

    now = _now_iso()
    draft_id = f"sdr_{uuid.uuid4().hex[:12]}"

    db.execute(
        """INSERT INTO sdr_drafts (draft_id, tenant_id, session_id, anon_id,
           contact_email, contact_name, path_context, draft_text, generated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (draft_id, tenant_id, session_id, anon_id,
         identity["contact_email"], identity["contact_name"],
         json.dumps(path_context), draft, now),
    )

    return {
        "draftId": draft_id,
        "tenantId": tenant_id,
        "sessionId": session_id,
        "anonId": anon_id,
        "contactEmail": identity["contact_email"],
        "contactName": identity["contact_name"],
        "subject": f"Thanks for visiting — here's what you experienced",
        "body": draft,
        "pathContext": path_context,
        "generatedAt": now,
    }


def _build_path_context(tenant_id: str, session_id: str, anon_id: str) -> dict:
    """Build contextual summary of the visitor's path for the draft."""
    zones = db.fetchall(
        """SELECT json_extract(payload, '$.zoneId') as z, COUNT(*) as c,
                  ROUND(AVG(CAST(json_extract(payload, '$.durationSec') AS REAL)), 1) as avg_d
           FROM event_log WHERE tenant_id = ? AND session_id = ?
             AND type = 'spatial.dwell' AND json_extract(payload, '$.anonId') = ?
             AND CAST(json_extract(payload, '$.durationSec') AS REAL) > 0
           GROUP BY z ORDER BY avg_d DESC""",
        (tenant_id, session_id, anon_id),
    )
    surfaces = db.fetchall(
        """SELECT json_extract(payload, '$.surfaceId') as s, COUNT(*) as c
           FROM event_log WHERE tenant_id = ? AND session_id = ?
             AND type = 'surface.interaction' AND json_extract(payload, '$.anonId') = ?
           GROUP BY s""",
        (tenant_id, session_id, anon_id),
    )

    intent = db.fetchone(
        "SELECT * FROM intent_scores WHERE tenant_id = ? AND session_id = ? AND anon_id = ?",
        (tenant_id, session_id, anon_id),
    )

    return {
        "zonesVisited": [{"zone": z["z"], "dwells": z["c"], "avgSec": z["avg_d"]} for z in zones],
        "surfacesInteracted": [{"surface": s["s"], "count": s["c"]} for s in surfaces],
        "intentScore": intent["score"] if intent else 0,
        "intentTier": intent["tier"] if intent else "cold",
        "totalZones": len(zones),
        "totalDwellSec": sum(z["c"] * z["avg_d"] for z in zones),
    }


def _llm_draft_follow_up(contact_name: str, path_context: dict) -> str:
    client = _llm_client()
    zones_list = ", ".join(z["zone"] for z in path_context.get("zonesVisited", []))
    total_sec = path_context.get("totalDwellSec", 0)
    mins = int(total_sec // 60)

    prompt = f"""Write a warm, personalised follow-up email draft for {contact_name} who visited a brand activation.

Their experience:
- They visited {path_context.get('totalZones', 0)} zones: {zones_list or 'the activation'}
- They spent approximately {mins} minutes total in the experience
- Their engagement score is {path_context.get('intentScore', 0)}/10 ({path_context.get('intentTier', 'standard')} tier)

Rules:
- Be warm and genuine, not salesy
- Reference their actual path through the experience
- Keep it under 120 words
- Include a natural next step (not "schedule a demo")
- Sign as "The Floats XR Team"
- Return ONLY the email body, no subject line, no greeting/signature header"""

    completion = client.chat.completions.create(
        model=get_settings().openrouter_model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.6,
        max_tokens=400,
    )
    return completion.choices[0].message.content or ""


def _fallback_draft(identity, path_context: dict) -> str:
    zones = path_context.get("zonesVisited", [])
    zone_names = ", ".join(z["zone"] for z in zones[:3]) if zones else "our activation"
    total_sec = path_context.get("totalDwellSec", 0)
    mins = int(total_sec // 60)
    contact = identity["contact_name"] or identity["contact_email"] or "there"

    return (
        f"Hi {contact},\n\n"
        f"It was great having you at the activation. We noticed you spent time with {zone_names}"
        f"{f' — about {mins} minutes exploring' if mins > 0 else ''}.\n\n"
        f"We'd love to hear what you thought. If you have any questions or want to learn more "
        f"about what we're building, just reply to this email.\n\n"
        f"Best,\nThe Floats XR Team"
    )


def list_drafts(tenant_id: str, session_id: str | None = None) -> list[dict]:
    if session_id:
        rows = db.fetchall(
            "SELECT * FROM sdr_drafts WHERE tenant_id = ? AND session_id = ? ORDER BY generated_at DESC",
            (tenant_id, session_id),
        )
    else:
        rows = db.fetchall(
            "SELECT * FROM sdr_drafts WHERE tenant_id = ? ORDER BY generated_at DESC",
            (tenant_id,),
        )
    return [
        {
            "draftId": r["draft_id"],
            "tenantId": r["tenant_id"],
            "sessionId": r["session_id"],
            "anonId": r["anon_id"],
            "contactEmail": r["contact_email"],
            "contactName": r["contact_name"],
            "subject": "Thanks for visiting — here's what you experienced",
            "body": r["draft_text"],
            "pathContext": json.loads(r["path_context"]),
            "generatedAt": r["generated_at"],
        }
        for r in rows
    ]


# ── Live Analyst (NLQ over live bus, reuses Phase 2 Ask) ──────────────────

# Already covered by /v1/ask endpoint — this is a thin wrapper for in-session queries.


def live_analyst_query(question: str, tenant_id: str, session_id: str) -> dict:
    """Thin wrapper over the Ask engine — answers NL questions against live bus data."""
    from app import ask
    return ask.ask(question, tenant_id, session_id)
