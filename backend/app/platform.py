"""
Platform hardening — Phase 6: RBAC, tenant isolation, exports, billing, health.

Makes realmspace a real product: secure, self-serve, billable, auditable.
"""

from __future__ import annotations

import csv
import io
import json
import time as _time
from datetime import datetime, timezone
from typing import Any, Callable

from app import db
from app.config import get_settings


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── RBAC middleware ─────────────────────────────────────────────────────────

ROLES = {"admin", "operator", "analyst", "viewer"}
ROLE_PERMISSIONS: dict[str, set[str]] = {
    "admin":    {"read", "write", "delete", "admin", "export", "billing"},
    "operator": {"read", "write", "export"},
    "analyst":  {"read", "export"},
    "viewer":   {"read"},
}

# Which endpoints require which permissions
ENDPOINT_PERMISSIONS: dict[str, str] = {
    "GET":      "read",
    "POST":     "write",
    "PUT":      "write",
    "DELETE":   "delete",
    # Specific overrides
    "POST:/v1/consent":        "write",
    "POST:/v1/identity/resolve": "write",
    "POST:/v1/handoff":        "write",
    "POST:/v1/handoff/*/sync": "write",
    "POST:/v1/intent/score":   "write",
    "POST:/v1/rules":          "write",
    "PUT:/v1/rules/*":         "write",
    "DELETE:/v1/rules/*":      "delete",
    "POST:/v1/insights/generate": "write",
    "POST:/v1/sdr/draft":      "write",
    "GET:/v1/export/*":        "export",
    "GET:/v1/billing/*":       "billing",
    "POST:/v1/crm/connection": "write",
    "PUT:/v1/crm/connection":  "write",
}


def check_permission(email: str, method: str, path: str) -> dict:
    """Check if the given user has permission for this endpoint.

    Returns {"allowed": bool, "role": str, "required": str}
    """
    user = db.fetchone("SELECT * FROM auth_users WHERE email = ?", (email.lower(),))
    if not user:
        return {"allowed": False, "role": "unknown", "required": "read",
                "error": "user not found"}

    role = user["role"]
    if role not in ROLES:
        role = "viewer"

    # Determine required permission
    required = "read"
    # Check exact path match first
    path_key = f"{method}:{path}"
    if path_key in ENDPOINT_PERMISSIONS:
        required = ENDPOINT_PERMISSIONS[path_key]
    else:
        # Check wildcard matches
        for ep, perm in ENDPOINT_PERMISSIONS.items():
            if ":" in ep and "*" in ep:
                ep_method, ep_path = ep.split(":", 1)
                if ep_method == method and _path_matches(ep_path, path):
                    required = perm
                    break
            elif ":" not in ep:
                if ep == method:
                    required = ENDPOINT_PERMISSIONS[ep]

    allowed = required in ROLE_PERMISSIONS.get(role, set())
    return {"allowed": allowed, "role": role, "required": required}


def _path_matches(pattern: str, actual: str) -> bool:
    """Simple wildcard matching for paths like /v1/rules/*"""
    pattern_parts = pattern.strip("/").split("/")
    actual_parts = actual.strip("/").split("/")
    if len(pattern_parts) != len(actual_parts):
        return False
    for pp, ap in zip(pattern_parts, actual_parts):
        if pp == "*":
            continue
        if pp != ap:
            return False
    return True


def list_users() -> list[dict]:
    rows = db.fetchall("SELECT * FROM auth_users ORDER BY created_at", ())
    return [
        {"userId": r["user_id"], "email": r["email"], "displayName": r["display_name"],
         "orgId": r["org_id"], "role": r["role"], "createdAt": r["created_at"]}
        for r in rows
    ]


def add_user(email: str, display_name: str | None, org_id: str, role: str) -> dict:
    if role not in ROLES:
        return {"error": f"invalid role: {role}", "valid_roles": list(ROLES)}
    uid = f"u_{email.split('@')[0]}_{_now_iso()[:10].replace('-','')}"
    now = _now_iso()
    db.execute(
        """INSERT OR REPLACE INTO auth_users (user_id, email, display_name, org_id, role, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (uid, email.lower(), display_name, org_id, role, now),
    )
    return {"userId": uid, "email": email, "displayName": display_name, "orgId": org_id, "role": role}


# ── Multi-tenant scoping ────────────────────────────────────────────────────

def verify_tenant_access(email: str, tenant_id: str) -> bool:
    """Check if a user's org matches the requested tenant."""
    user = db.fetchone("SELECT org_id, role FROM auth_users WHERE email = ?", (email.lower(),))
    if not user:
        return False
    # Admins can access any tenant
    if user["role"] == "admin":
        return True
    return user["org_id"] == tenant_id


def tenant_stats(tenant_id: str) -> dict:
    """Aggregate stats for a tenant across all sessions."""
    sessions = db.fetchone(
        "SELECT COUNT(DISTINCT session_id) as c FROM event_log WHERE tenant_id = ?",
        (tenant_id,),
    )
    events = db.fetchone(
        "SELECT COUNT(*) as c FROM event_log WHERE tenant_id = ?",
        (tenant_id,),
    )
    rules = db.fetchone(
        "SELECT COUNT(*) as c FROM rules WHERE tenant_id = ?",
        (tenant_id,),
    )
    consents = db.fetchone(
        "SELECT COUNT(*) as c FROM consents WHERE tenant_id = ?",
        (tenant_id,),
    )
    handoffs = db.fetchone(
        "SELECT COUNT(*) as c FROM lead_handoffs WHERE tenant_id = ?",
        (tenant_id,),
    )
    return {
        "tenantId": tenant_id,
        "sessionCount": sessions["c"] if sessions else 0,
        "eventCount": events["c"] if events else 0,
        "ruleCount": rules["c"] if rules else 0,
        "consentCount": consents["c"] if consents else 0,
        "handoffCount": handoffs["c"] if handoffs else 0,
        "computedAt": _now_iso(),
    }


# ── Exports ─────────────────────────────────────────────────────────────────


def export_session_json(tenant_id: str, session_id: str) -> dict:
    """Full session export as structured JSON — all events, graph, outcomes."""
    events = db.fetchall(
        "SELECT * FROM event_log WHERE tenant_id = ? AND session_id = ? ORDER BY seq",
        (tenant_id, session_id),
    )
    graph_nodes = db.fetchall(
        "SELECT * FROM graph_nodes WHERE tenant_id = ? AND session_id = ?",
        (tenant_id, session_id),
    )
    graph_edges = db.fetchall(
        "SELECT * FROM graph_edges WHERE tenant_id = ? AND session_id = ?",
        (tenant_id, session_id),
    )
    insights = db.fetchall(
        "SELECT * FROM insights WHERE tenant_id = ? AND session_id = ?",
        (tenant_id, session_id),
    )

    return {
        "exportFormat": "json",
        "tenantId": tenant_id,
        "sessionId": session_id,
        "exportedAt": _now_iso(),
        "events": [_event_row(e) for e in events],
        "graph": {
            "nodes": [_node_row(n) for n in graph_nodes],
            "edges": [_edge_row(e) for e in graph_edges],
        },
        "insights": [_insight_row(i) for i in insights],
        "totals": {
            "events": len(events),
            "graphNodes": len(graph_nodes),
            "graphEdges": len(graph_edges),
            "insights": len(insights),
        },
    }


def export_session_csv(tenant_id: str, session_id: str) -> str:
    """Full session export as CSV (events only — graph is relational)."""
    events = db.fetchall(
        "SELECT * FROM event_log WHERE tenant_id = ? AND session_id = ? ORDER BY seq",
        (tenant_id, session_id),
    )
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["seq", "eventId", "type", "payload", "occurredAt", "recordedAt"])
    for e in events:
        writer.writerow([
            e["seq"], e["event_id"], e["type"],
            e["payload"], e["occurred_at"], e["recorded_at"],
        ])
    return buf.getvalue()


def export_attribution_ledger_csv(tenant_id: str, session_id: str | None = None) -> str:
    """CFO-ready attribution ledger: lead → spatial path → attribution model → outcome."""
    if session_id:
        rows = db.fetchall(
            "SELECT * FROM lead_handoffs WHERE tenant_id = ? AND session_id = ? ORDER BY created_at",
            (tenant_id, session_id),
        )
    else:
        rows = db.fetchall(
            "SELECT * FROM lead_handoffs WHERE tenant_id = ? ORDER BY created_at",
            (tenant_id,),
        )

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "handoffId", "sessionId", "anonId", "contactEmail", "contactName",
        "attributionModel", "intentScore", "intentTier", "status", "crmStatus", "createdAt",
    ])
    for r in rows:
        writer.writerow([
            r["handoff_id"], r["session_id"], r["anon_id"],
            r["contact_email"], r["contact_name"],
            r["attribution_model"], r["intent_score"], r["intent_tier"],
            r["status"], r["crm_status"] if "crm_status" in r.keys() else "pending",
            r["created_at"],
        ])
    return buf.getvalue()


def _event_row(e) -> dict:
    return {
        "seq": e["seq"], "eventId": e["event_id"], "type": e["type"],
        "payload": json.loads(e["payload"]) if isinstance(e["payload"], str) else e["payload"],
        "occurredAt": e["occurred_at"], "recordedAt": e["recorded_at"],
    }


def _node_row(n) -> dict:
    return {
        "kind": n["node_kind"], "id": n["node_id"],
        "props": json.loads(n["props"]) if isinstance(n["props"], str) else n["props"],
    }


def _edge_row(e) -> dict:
    return {
        "kind": e["edge_kind"], "from": e["from_id"], "to": e["to_id"],
        "props": json.loads(e["props"]) if isinstance(e["props"], str) else e["props"],
    }


def _insight_row(i) -> dict:
    return {
        "insightId": i["insight_id"], "kind": i["kind"],
        "text": i["insight_text"],
        "supportingEvents": json.loads(i["supporting_event_seqs"]),
        "generatedAt": i["generated_at"],
    }


# ── Billing hooks ───────────────────────────────────────────────────────────


def usage_summary(tenant_id: str, period_days: int = 30) -> dict:
    """Usage counters for billing: events, LLM calls, insights, handoffs, exports."""
    cutoff = datetime.now(timezone.utc).isoformat()
    # Event count (last N days)
    events = db.fetchone(
        "SELECT COUNT(*) as c FROM event_log WHERE tenant_id = ? AND recorded_at >= datetime('now', ?)",
        (tenant_id, f"-{period_days} days"),
    )
    # LLM calls (action_log with action_type containing 'llm' or from insights/sdr)
    llm_calls = db.fetchall(
        "SELECT COUNT(*) as c FROM insights WHERE tenant_id = ? AND generated_at >= datetime('now', ?)",
        (tenant_id, f"-{period_days} days"),
    )
    sdr_calls = db.fetchall(
        "SELECT COUNT(*) as c FROM sdr_drafts WHERE tenant_id = ? AND generated_at >= datetime('now', ?)",
        (tenant_id, f"-{period_days} days"),
    )
    handoffs = db.fetchone(
        "SELECT COUNT(*) as c FROM lead_handoffs WHERE tenant_id = ? AND created_at >= datetime('now', ?)",
        (tenant_id, f"-{period_days} days"),
    )

    total_llm = (llm_calls[0]["c"] if llm_calls else 0) + (sdr_calls[0]["c"] if sdr_calls else 0)

    return {
        "tenantId": tenant_id,
        "periodDays": period_days,
        "computedAt": _now_iso(),
        "usage": {
            "events": events["c"] if events else 0,
            "llmCalls": total_llm,
            "handoffs": handoffs["c"] if handoffs else 0,
        },
        "limits": {
            "events": 100_000,      # per period
            "llmCalls": 1_000,
            "handoffs": 5_000,
        },
        "pctUsed": {
            "events": round(((events["c"] if events else 0) / 100_000) * 100, 1),
            "llmCalls": round((total_llm / 1_000) * 100, 1),
            "handoffs": round(((handoffs["c"] if handoffs else 0) / 5_000) * 100, 1),
        },
    }


# ── System health ───────────────────────────────────────────────────────────


def system_health() -> dict:
    """Comprehensive system health check."""
    settings = get_settings()
    db_size = _db_size()

    # Check key tables
    tables = db.fetchall("SELECT name FROM sqlite_master WHERE type='table'", ())
    table_names = [t["name"] for t in tables]

    # Check bus lag
    last_seq = db.fetchone("SELECT MAX(seq) as m FROM event_log", ())
    graph_cursor = db.fetchone(
        "SELECT last_seq FROM consumer_cursor WHERE consumer = 'graph_writer' AND tenant_id = ?",
        (settings.default_tenant_id,),
    )

    lag = 0
    if last_seq and graph_cursor:
        lag = (last_seq["m"] or 0) - (graph_cursor["last_seq"] or 0)

    return {
        "status": "healthy" if lag < 100 else "degraded",
        "version": "0.1.0-p6",
        "database": f"sqlite:{db_size}",
        "uptime": "unknown",  # track via a started_at global
        "tables": len(table_names),
        "tableList": sorted(table_names),
        "busLag": lag,
        "lastEventSeq": last_seq["m"] if last_seq else 0,
        "checks": {
            "event_log": "event_log" in table_names,
            "graph": "graph_nodes" in table_names,
            "rules": "rules" in table_names,
            "consent": "consents" in table_names,
            "handoff": "lead_handoffs" in table_names,
            "insights": "insights" in table_names,
            "sdr": "sdr_drafts" in table_names,
        },
        "computedAt": _now_iso(),
    }


def _db_size() -> str:
    try:
        row = db.fetchone("SELECT page_count * page_size as size FROM pragma_page_count(), pragma_page_size()", ())
        if row:
            size_bytes = row["size"] or 0
            if size_bytes > 1024 * 1024:
                return f"{size_bytes / (1024*1024):.1f}MB"
            return f"{size_bytes / 1024:.0f}KB"
    except Exception:
        pass
    return "unknown"
