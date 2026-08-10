"""
Ask the Room — NLQ → constrained SQL templates over the relational projection.

Per ADR-001, queries run against the SQL projection (graph_nodes / graph_edges
+ event_log), NOT a raw Cypher graph DB. The LLM's job is to parse the user's
natural-language question, select the right parameterized template, and fill in
its parameters. Templates are an allow-list — the LLM cannot generate arbitrary
SQL, only parameterize pre-vetted queries.

If no API key is configured, a stub falls back to regex-based answer matching
(mirroring the dashboard's lib/mock/ask-answers.ts).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from openai import OpenAI

from app import db
from app.config import get_settings

# ── Template definitions ───────────────────────────────────────────────────
# Each template has: id, description (for LLM matching), sql (param placeholders
# are ?), default_params, chart_type, and a format_answer function name.


@dataclass
class QueryTemplate:
    id: str
    description: str
    sql: str
    params: list[str]  # ordered param names for binding
    chart_type: str  # "number" | "bar" | "line" | "table" | "pie"
    format_answer: str  # key in FORMATTERS


QUERY_TEMPLATES: list[QueryTemplate] = [
    QueryTemplate(
        id="unique_visitors",
        description="How many unique people visited a session? (total, count, visitors)",
        sql="""SELECT COUNT(DISTINCT json_extract(payload, '$.anonId')) AS count
FROM event_log
WHERE tenant_id = ? AND session_id = ? AND type = 'perception.detection'""",
        params=["tenant_id", "session_id"],
        chart_type="number",
        format_answer="count",
    ),
    QueryTemplate(
        id="events_count",
        description="How many total events in a session? (events, total, how many things happened)",
        sql="""SELECT COUNT(*) AS count
FROM event_log
WHERE tenant_id = ? AND session_id = ?""",
        params=["tenant_id", "session_id"],
        chart_type="number",
        format_answer="count",
    ),
    QueryTemplate(
        id="avg_dwell",
        description="What is the average dwell time across all visitors? (average, mean dwell, how long do people stay)",
        sql="""SELECT AVG(CAST(json_extract(payload, '$.durationSec') AS REAL)) AS avg_dwell_sec
FROM event_log
WHERE tenant_id = ? AND session_id = ? AND type = 'spatial.dwell'
  AND CAST(json_extract(payload, '$.durationSec') AS REAL) > 0
  AND CAST(json_extract(payload, '$.durationSec') AS REAL) <= 7200""",
        params=["tenant_id", "session_id"],
        chart_type="number",
        format_answer="avg_dwell",
    ),
    QueryTemplate(
        id="dwell_by_zone",
        description="Dwell time broken down by zone (dwell per zone, zone dwell, how long in each zone)",
        sql="""SELECT
  json_extract(payload, '$.zoneId') AS zone_id,
  COUNT(*) AS dwells,
  ROUND(AVG(CAST(json_extract(payload, '$.durationSec') AS REAL)), 1) AS avg_dwell_sec,
  ROUND(SUM(CAST(json_extract(payload, '$.durationSec') AS REAL)), 1) AS total_dwell_sec
FROM event_log
WHERE tenant_id = ? AND session_id = ? AND type = 'spatial.dwell'
  AND CAST(json_extract(payload, '$.durationSec') AS REAL) > 0
  AND CAST(json_extract(payload, '$.durationSec') AS REAL) <= 7200
GROUP BY json_extract(payload, '$.zoneId')
ORDER BY total_dwell_sec DESC""",
        params=["tenant_id", "session_id"],
        chart_type="bar",
        format_answer="dwell_by_zone",
    ),
    QueryTemplate(
        id="person_dwell",
        description="How long did a specific person dwell? (person X, how long P-XXX, specific visitor dwell time)",
        sql="""SELECT
  json_extract(payload, '$.zoneId') AS zone_id,
  ROUND(CAST(json_extract(payload, '$.durationSec') AS REAL), 1) AS dwell_sec
FROM event_log
WHERE tenant_id = ? AND session_id = ? AND type = 'spatial.dwell'
  AND json_extract(payload, '$.anonId') = ?
  AND CAST(json_extract(payload, '$.durationSec') AS REAL) > 0
ORDER BY dwell_sec DESC""",
        params=["tenant_id", "session_id", "person_id"],
        chart_type="table",
        format_answer="person_dwell",
    ),
    QueryTemplate(
        id="zone_entries",
        description="How many people entered each zone? (entries per zone, zone entry counts, which zones are popular)",
        sql="""SELECT
  json_extract(payload, '$.zoneId') AS zone_id,
  COUNT(*) AS entries
FROM event_log
WHERE tenant_id = ? AND session_id = ? AND type = 'spatial.zone_enter'
GROUP BY json_extract(payload, '$.zoneId')
ORDER BY entries DESC""",
        params=["tenant_id", "session_id"],
        chart_type="bar",
        format_answer="zone_entries",
    ),
    QueryTemplate(
        id="engaged_visitors",
        description="How many people were engaged (dwelled over threshold)? (engaged, engaged visitors, people who stayed)",
        sql="""SELECT COUNT(DISTINCT json_extract(payload, '$.anonId')) AS count
FROM event_log
WHERE tenant_id = ? AND session_id = ?
  AND type = 'spatial.dwell'
  AND CAST(json_extract(payload, '$.durationSec') AS REAL) >= ?
  AND CAST(json_extract(payload, '$.durationSec') AS REAL) <= 7200""",
        params=["tenant_id", "session_id", "threshold_sec"],
        chart_type="number",
        format_answer="count",
    ),
    QueryTemplate(
        id="busiest_hour",
        description="When was the busiest time? (peak, busiest hour, most active time, traffic over time)",
        sql="""SELECT
  strftime('%Y-%m-%dT%H:%M', recorded_at) AS minute_bucket,
  COUNT(*) AS event_count,
  COUNT(DISTINCT json_extract(payload, '$.anonId')) AS unique_people
FROM event_log
WHERE tenant_id = ? AND session_id = ?
  AND json_extract(payload, '$.anonId') IS NOT NULL
  AND json_extract(payload, '$.anonId') != ''
GROUP BY minute_bucket
ORDER BY minute_bucket""",
        params=["tenant_id", "session_id"],
        chart_type="line",
        format_answer="traffic_over_time",
    ),
    QueryTemplate(
        id="surface_interactions",
        description="How many surface interactions? (touchpoints, surfaces, interactions, screens)",
        sql="""SELECT
  json_extract(payload, '$.surfaceId') AS surface_id,
  COUNT(*) AS interactions
FROM event_log
WHERE tenant_id = ? AND session_id = ? AND type = 'surface.interaction'
GROUP BY json_extract(payload, '$.surfaceId')
ORDER BY interactions DESC""",
        params=["tenant_id", "session_id"],
        chart_type="bar",
        format_answer="surface_interactions",
    ),
    QueryTemplate(
        id="session_span",
        description="How long did the session last? (session length, duration, span, time range)",
        sql="""SELECT
  MIN(recorded_at) AS first_event,
  MAX(recorded_at) AS last_event,
  (julianday(MAX(recorded_at)) - julianday(MIN(recorded_at))) * 86400.0 AS duration_sec
FROM event_log
WHERE tenant_id = ? AND session_id = ?""",
        params=["tenant_id", "session_id"],
        chart_type="number",
        format_answer="session_span",
    ),
    QueryTemplate(
        id="event_types_breakdown",
        description="Breakdown of event types (what kinds of events, event type counts, types)",
        sql="""SELECT type, COUNT(*) AS count
FROM event_log
WHERE tenant_id = ? AND session_id = ?
GROUP BY type
ORDER BY count DESC""",
        params=["tenant_id", "session_id"],
        chart_type="pie",
        format_answer="event_types",
    ),
    QueryTemplate(
        id="zone_passby",
        description="How many passbys per zone? (passby, walked past, didn't enter)",
        sql="""SELECT
  json_extract(payload, '$.adjacentZoneId') AS zone_id,
  COUNT(*) AS passbys
FROM event_log
WHERE tenant_id = ? AND session_id = ? AND type = 'spatial.passby'
GROUP BY json_extract(payload, '$.adjacentZoneId')
ORDER BY passbys DESC""",
        params=["tenant_id", "session_id"],
        chart_type="bar",
        format_answer="zone_passby",
    ),
    QueryTemplate(
        id="person_path",
        description="What path did a specific person take through zones? (person X path, where did P-XXX go, journey, route)",
        sql="""SELECT
  recorded_at,
  CASE
    WHEN type = 'spatial.zone_enter' THEN 'ENTERED ' || json_extract(payload, '$.zoneId')
    WHEN type = 'spatial.zone_exit' THEN 'EXITED ' || json_extract(payload, '$.zoneId')
    WHEN type = 'spatial.dwell' THEN 'DWELLED in ' || json_extract(payload, '$.zoneId') || ' for ' || json_extract(payload, '$.durationSec') || 's'
    ELSE type
  END AS event_desc
FROM event_log
WHERE tenant_id = ? AND session_id = ?
  AND type IN ('spatial.zone_enter', 'spatial.zone_exit', 'spatial.dwell')
  AND json_extract(payload, '$.anonId') = ?
ORDER BY recorded_at""",
        params=["tenant_id", "session_id", "person_id"],
        chart_type="table",
        format_answer="person_path",
    ),
    QueryTemplate(
        id="concurrent_people",
        description="What was the peak number of people present? (concurrent, peak, maximum people at once, crowd)",
        sql="""SELECT MAX(cnt) AS peak_concurrency FROM (
  SELECT COUNT(DISTINCT json_extract(payload, '$.anonId')) AS cnt
  FROM event_log
  WHERE tenant_id = ? AND session_id = ?
    AND json_extract(payload, '$.anonId') IS NOT NULL
    AND json_extract(payload, '$.anonId') != ''
  GROUP BY strftime('%Y-%m-%dT%H:%M', recorded_at)
)""",
        params=["tenant_id", "session_id"],
        chart_type="number",
        format_answer="count",
    ),
]


# ── Answer formatters ──────────────────────────────────────────────────────


def _fmt_count(rows: list[dict], _params: dict) -> dict:
    val = rows[0].get("count", 0) if rows else 0
    return {"answer": str(val), "value": val}


def _fmt_avg_dwell(rows: list[dict], _params: dict) -> dict:
    val = rows[0].get("avg_dwell_sec", 0) if rows else 0
    if val is None:
        val = 0
    mins = int(val // 60)
    secs = int(val % 60)
    return {"answer": f"{mins}m {secs}s average", "value": round(val, 1)}


def _fmt_dwell_by_zone(rows: list[dict], _params: dict) -> dict:
    return {
        "answer": f"{len(rows)} zones with dwell data",
        "table": rows,
        "labels": [r["zone_id"] for r in rows],
        "values": [r["total_dwell_sec"] for r in rows],
    }


def _fmt_person_dwell(rows: list[dict], params: dict) -> dict:
    pid = params.get("person_id", "unknown")
    if not rows:
        return {"answer": f"No dwell data found for {pid}"}
    total = sum(r["dwell_sec"] for r in rows)
    return {
        "answer": f"{pid} dwelled {total:.0f}s total across {len(rows)} zones",
        "table": rows,
    }


def _fmt_zone_entries(rows: list[dict], _params: dict) -> dict:
    return {
        "answer": f"{sum(r['entries'] for r in rows)} total entries across {len(rows)} zones",
        "table": rows,
        "labels": [r["zone_id"] for r in rows],
        "values": [r["entries"] for r in rows],
    }


def _fmt_traffic_over_time(rows: list[dict], _params: dict) -> dict:
    if not rows:
        return {"answer": "No traffic data"}
    busiest = max(rows, key=lambda r: r["event_count"])
    return {
        "answer": f"Traffic data for {len(rows)} time buckets",
        "table": rows[:20],
        "labels": [r["minute_bucket"] for r in rows],
        "values": [r["unique_people"] for r in rows],
    }


def _fmt_surface_interactions(rows: list[dict], _params: dict) -> dict:
    return {
        "answer": f"{sum(r['interactions'] for r in rows)} interactions across {len(rows)} surfaces",
        "table": rows,
        "labels": [r["surface_id"] for r in rows],
        "values": [r["interactions"] for r in rows],
    }


def _fmt_session_span(rows: list[dict], _params: dict) -> dict:
    if not rows or rows[0].get("duration_sec") is None:
        return {"answer": "No duration data"}
    d = rows[0]
    mins = int(d["duration_sec"] // 60)
    secs = int(d["duration_sec"] % 60)
    return {"answer": f"{mins}m {secs}s", "value": round(d["duration_sec"], 1)}


def _fmt_event_types(rows: list[dict], _params: dict) -> dict:
    return {
        "answer": f"{sum(r['count'] for r in rows)} events of {len(rows)} types",
        "table": rows,
        "labels": [r["type"] for r in rows],
        "values": [r["count"] for r in rows],
    }


def _fmt_zone_passby(rows: list[dict], _params: dict) -> dict:
    if not rows:
        return {"answer": "No passby data"}
    return {
        "answer": f"{sum(r['passbys'] for r in rows)} passbys across {len(rows)} zones",
        "table": rows,
        "labels": [r["zone_id"] for r in rows],
        "values": [r["passbys"] for r in rows],
    }


def _fmt_person_path(rows: list[dict], params: dict) -> dict:
    pid = params.get("person_id", "unknown")
    if not rows:
        return {"answer": f"No path data for {pid}"}
    steps = [r["event_desc"] for r in rows]
    return {"answer": f"{pid}: {' → '.join(steps)}", "table": rows}


FORMATTERS: dict[str, Any] = {
    "count": _fmt_count,
    "avg_dwell": _fmt_avg_dwell,
    "dwell_by_zone": _fmt_dwell_by_zone,
    "person_dwell": _fmt_person_dwell,
    "zone_entries": _fmt_zone_entries,
    "traffic_over_time": _fmt_traffic_over_time,
    "surface_interactions": _fmt_surface_interactions,
    "session_span": _fmt_session_span,
    "event_types": _fmt_event_types,
    "zone_passby": _fmt_zone_passby,
    "person_path": _fmt_person_path,
}


# ── LLM prompt builder ──────────────────────────────────────────────────────


def _build_system_prompt() -> str:
    templates_desc = "\n".join(
        f"- {t.id}: {t.description} (params: {', '.join(t.params)}, chart: {t.chart_type})"
        for t in QUERY_TEMPLATES
    )
    return f"""You are the "Ask the Room" query engine for RealmSpace, an analytics platform for physical brand experiences.

You receive a natural-language question about a session and must select the best matching query template and extract its parameters.

Available templates:
{templates_desc}

Respond with ONLY a JSON object:
{{"template": "<template_id>", "params": {{"param_name": "value", ...}}}}

If no template matches, respond with:
{{"template": null, "fallback": "I can answer questions about: visitors, dwell time, zones, traffic, events, surfaces, session duration, person paths, and passbys."}}

The tenant_id and session_id are provided separately by the caller — you only need to extract natural-language parameters like person IDs (e.g. "P-001"), zone IDs, or numeric thresholds.

Examples:
- "How many visitors?" → {{"template": "unique_visitors", "params": {{}}}}
- "How long did P-012 stay?" → {{"template": "person_dwell", "params": {{"person_id": "P-012"}}}}
- "Which zone was busiest?" → {{"template": "zone_entries", "params": {{}}}}
- "Where did P-005 go?" → {{"template": "person_path", "params": {{"person_id": "P-005"}}}}
- "How many engaged visitors with threshold 60s?" → {{"template": "engaged_visitors", "params": {{"threshold_sec": "60"}}}}
- "What's the average dwell?" → {{"template": "avg_dwell", "params": {{}}}}
"""


def _call_llm(question: str) -> dict[str, Any]:
    """Send question to OpenRouter, get back template + params."""
    settings = get_settings()
    if not settings.openrouter_api_key:
        return _stub_match(question)

    client = OpenAI(
        api_key=settings.openrouter_api_key,
        base_url="https://openrouter.ai/api/v1",
    )

    try:
        completion = client.chat.completions.create(
            model=settings.openrouter_model,
            messages=[
                {"role": "system", "content": _build_system_prompt()},
                {"role": "user", "content": question},
            ],
            temperature=0.1,
            max_tokens=300,
        )
        raw = completion.choices[0].message.content or "{}"
        # Strip markdown code fences if present
        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        raw = re.sub(r"\s*```$", "", raw)
        return json.loads(raw)
    except Exception:
        return _stub_match(question)


def _stub_match(question: str) -> dict[str, Any]:
    """Regex-based fallback when no API key is configured."""
    q = question.lower()

    if any(w in q for w in ("concurrent", "peak", "maximum people", "crowd", "most people at once")):
        return {"template": "concurrent_people", "params": {}}
    if any(w in q for w in ("passby", "passed by", "walked past")):
        return {"template": "zone_passby", "params": {}}
    if any(w in q for w in ("surface", "touchpoint", "interaction", "screen")):
        return {"template": "surface_interactions", "params": {}}
    # Dwell checks BEFORE zone checks to avoid "dwell by zone" matching "zone"
    if any(w in q for w in ("average dwell", "avg dwell", "mean dwell", "how long do people", "how long does", "how long did people")):
        return {"template": "avg_dwell", "params": {}}
    if any(w in q for w in ("dwell by zone", "dwell per zone", "zone dwell", "dwell time by zone", "per zone dwell")):
        return {"template": "dwell_by_zone", "params": {}}
    if any(w in q for w in ("engaged", "engagement", "stayed longer")):
        threshold = "60"
        m = re.search(r"(\d+)\s*s", q)
        if m:
            threshold = m.group(1)
        return {"template": "engaged_visitors", "params": {"threshold_sec": threshold}}

    # Person-specific patterns
    person_match = re.search(r"P-\d+", question, re.IGNORECASE)
    if person_match:
        pid = person_match.group(0)
        if any(w in q for w in ("path", "route", "journey", "where did", "go", "went")):
            return {"template": "person_path", "params": {"person_id": pid}}
        if any(w in q for w in ("stay", "dwell", "how long", "duration")):
            return {"template": "person_dwell", "params": {"person_id": pid}}

    if any(w in q for w in ("how many", "count", "visitor", "people", "unique", "total people")):
        return {"template": "unique_visitors", "params": {}}
    if any(w in q for w in ("busiest time", "traffic", "busy time", "active time", "over time")):
        return {"template": "busiest_hour", "params": {}}
    if any(w in q for w in ("zone", "entry", "popular", "busiest")):
        return {"template": "zone_entries", "params": {}}
    if any(w in q for w in ("event type", "breakdown", "what kind", "types of event")):
        return {"template": "event_types_breakdown", "params": {}}
    if any(w in q for w in ("how long", "session length", "duration", "span", "lasted")):
        return {"template": "session_span", "params": {}}

    return {
        "template": None,
        "fallback": "I can answer questions about: visitors, dwell time, zones, traffic, events, surfaces, session duration, person paths, and passbys.",
    }


# ── Execute a query ────────────────────────────────────────────────────────


def _execute_template(template: QueryTemplate, params: dict[str, str], tenant_id: str, session_id: str) -> dict[str, Any]:
    """Bind params and run the template SQL."""
    # Build ordered param values matching the template's params list
    bound: list[str] = []
    for pname in template.params:
        if pname == "tenant_id":
            bound.append(tenant_id)
        elif pname == "session_id":
            bound.append(session_id)
        else:
            bound.append(params.get(pname, ""))

    rows = db.fetchall(template.sql, tuple(bound))
    rows_dicts = [dict(r) for r in rows]

    fmt = FORMATTERS.get(template.format_answer, _fmt_count)
    result = fmt(rows_dicts, {**params, "tenant_id": tenant_id, "session_id": session_id})
    result["chart_type"] = template.chart_type
    result["template"] = template.id
    return result


# ── Public API ──────────────────────────────────────────────────────────────


def ask(question: str, tenant_id: str, session_id: str) -> dict[str, Any]:
    """Main entry point: NL question → answer.

    Returns {"answer", "chart_type", "template", ...} on success,
    or {"answer": "...", "fallback": True} when no template matches.
    """
    llm_result = _call_llm(question)

    template_id = llm_result.get("template")
    if not template_id:
        return {"answer": llm_result.get("fallback", "I couldn't understand that question."), "fallback": True}

    # Find the matching template
    for t in QUERY_TEMPLATES:
        if t.id == template_id:
            return _execute_template(t, llm_result.get("params", {}), tenant_id, session_id)

    return {"answer": f"No template found for '{template_id}'", "fallback": True}
