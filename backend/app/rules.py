"""
Rules Engine — Phase 3: Act.

Edge-local evaluator that subscribes to the event bus, evaluates persisted
tenant-scoped rules against spatial.* / surface.* events, and dispatches
actions (Slack, webhook, screen swap, staff prompt, log) with idempotency,
cooldown, dead-letter retry, and cost telemetry.

Architecture:
  Event Bus ──> on_bus_event() ──> evaluate_rules() ──> dispatch_action()
                   │                     │
              rules DB table       action_log table + dead_letter
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from app import db
from app.config import get_settings


# ── Types ───────────────────────────────────────────────────────────────────


@dataclass
class RuleRecord:
    rule_id: str
    tenant_id: str
    name: str
    trigger_type: str
    trigger_config: dict
    condition_config: dict
    action_type: str
    action_config: dict
    enabled: bool
    cooldown_sec: int
    last_fired_at: str | None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_ms() -> int:
    return int(time.time() * 1000)


# ── Rule CRUD ───────────────────────────────────────────────────────────────


def load_rules(tenant_id: str) -> list[RuleRecord]:
    rows = db.fetchall(
        "SELECT * FROM rules WHERE tenant_id = ? AND enabled = 1",
        (tenant_id,),
    )
    return [
        RuleRecord(
            rule_id=r["rule_id"],
            tenant_id=r["tenant_id"],
            name=r["name"],
            trigger_type=r["trigger_type"],
            trigger_config=json.loads(r["trigger_config"]),
            condition_config=json.loads(r["condition_config"]),
            action_type=r["action_type"],
            action_config=json.loads(r["action_config"]),
            enabled=bool(r["enabled"]),
            cooldown_sec=r["cooldown_sec"] if r["cooldown_sec"] is not None else 60,
            last_fired_at=r["last_fired_at"],
        )
        for r in rows
    ]


def save_rule(rule_id: str, tenant_id: str, name: str, trigger_type: str,
              trigger_config: dict, condition_config: dict,
              action_type: str, action_config: dict,
              cooldown_sec: int = 60) -> str:
    now = _now_iso()
    db.execute(
        """INSERT INTO rules (rule_id, tenant_id, name, trigger_type, trigger_config,
           condition_config, action_type, action_config, cooldown_sec, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(rule_id) DO UPDATE SET
           name=excluded.name, trigger_type=excluded.trigger_type,
           trigger_config=excluded.trigger_config, condition_config=excluded.condition_config,
           action_type=excluded.action_type, action_config=excluded.action_config,
           cooldown_sec=excluded.cooldown_sec, updated_at=excluded.updated_at""",
        (rule_id, tenant_id, name, trigger_type, json.dumps(trigger_config),
         json.dumps(condition_config), action_type, json.dumps(action_config),
         cooldown_sec, now, now),
    )
    return rule_id


def update_rule(rule_id: str, updates: dict) -> bool:
    """Update specific fields on a rule. Returns True if found."""
    row = db.fetchone("SELECT 1 FROM rules WHERE rule_id = ?", (rule_id,))
    if not row:
        return False
    set_clauses = []
    params: list[Any] = []
    field_map = {
        "name": "name", "trigger_type": "trigger_type",
        "trigger_config": "trigger_config", "condition_config": "condition_config",
        "action_type": "action_type", "action_config": "action_config",
        "enabled": "enabled", "cooldown_sec": "cooldown_sec",
    }
    for key, col in field_map.items():
        if key in updates:
            val = updates[key]
            if col in ("trigger_config", "condition_config", "action_config"):
                val = json.dumps(val)
            set_clauses.append(f"{col} = ?")
            params.append(val)
    if not set_clauses:
        return True
    set_clauses.append("updated_at = ?")
    params.append(_now_iso())
    params.append(rule_id)
    db.execute(f"UPDATE rules SET {', '.join(set_clauses)} WHERE rule_id = ?", tuple(params))
    return True


def delete_rule(rule_id: str) -> bool:
    cur = db.execute("DELETE FROM rules WHERE rule_id = ?", (rule_id,))
    return cur.rowcount > 0 if hasattr(cur, 'rowcount') else True


def get_rule(rule_id: str) -> dict | None:
    r = db.fetchone("SELECT * FROM rules WHERE rule_id = ?", (rule_id,))
    if not r:
        return None
    return {
        "ruleId": r["rule_id"],
        "tenantId": r["tenant_id"],
        "name": r["name"],
        "triggerType": r["trigger_type"],
        "triggerZoneId": json.loads(r["trigger_config"]).get("zoneId"),
        "condition": json.loads(r["condition_config"]),
        "action": json.loads(r["action_config"]),
        "enabled": bool(r["enabled"]),
        "cooldownSec": r["cooldown_sec"],
    }


# ── Rule evaluation ─────────────────────────────────────────────────────────


def _check_cooldown(rule: RuleRecord) -> bool:
    """True if the rule is still in cooldown (should NOT fire)."""
    if not rule.last_fired_at:
        return False
    try:
        last = datetime.fromisoformat(rule.last_fired_at.replace("Z", "+00:00"))
        elapsed = (datetime.now(timezone.utc) - last).total_seconds()
        return elapsed < rule.cooldown_sec
    except (ValueError, TypeError):
        return False


def _match_event(rule: RuleRecord, event_type: str, payload: dict) -> bool:
    """Check if an event type matches the rule's trigger."""
    if rule.trigger_type != event_type:
        return False
    zone_id = rule.trigger_config.get("zoneId")
    if zone_id and payload.get("zoneId") != zone_id and payload.get("adjacentZoneId") != zone_id:
        return False
    return True


def _check_condition(rule: RuleRecord, session_events: list[dict]) -> bool:
    """Evaluate the condition against recent matching events."""
    cond = rule.condition_config
    cond_type = cond.get("type", "any")

    if cond_type == "any":
        return len(session_events) > 0
    elif cond_type == "none":
        return len(session_events) == 0

    # threshold: count events within window
    count = cond.get("count", 1)
    window_sec = cond.get("windowSec", 30)
    now = _now_ms()
    recent = [e for e in session_events
              if e.get("occurredAt", 0) > now - window_sec * 1000]

    # If min_dwell_sec is set, filter by dwell duration
    min_dwell = cond.get("minDwellSec")
    if min_dwell is not None:
        recent = [e for e in recent
                  if (e.get("payload", {}).get("durationSec", 0) or 0) >= min_dwell]

    if cond_type == "threshold":
        return len(recent) >= count

    return False


def _mark_fired(rule: RuleRecord) -> None:
    now = _now_iso()
    db.execute(
        "UPDATE rules SET last_fired_at = ? WHERE rule_id = ?",
        (now, rule.rule_id),
    )
    rule.last_fired_at = now


# ── In-memory sliding windows (per-rule, per-session) ───────────────────────

# Simple store: key = f"{rule_id}:{session_id}" → list of events
_event_windows: dict[str, list[dict]] = {}
MAX_WINDOW_EVENTS = 200


def _add_to_window(rule_id: str, session_id: str, event: dict) -> None:
    key = f"{rule_id}:{session_id}"
    if key not in _event_windows:
        _event_windows[key] = []
    _event_windows[key].append(event)
    # Trim old entries
    if len(_event_windows[key]) > MAX_WINDOW_EVENTS:
        _event_windows[key] = _event_windows[key][-MAX_WINDOW_EVENTS:]


def _window_for(rule_id: str, session_id: str) -> list[dict]:
    return _event_windows.get(f"{rule_id}:{session_id}", [])


# ── Action dispatchers ──────────────────────────────────────────────────────


def _dispatch_slack(action_config: dict, rule_name: str, event_count: int,
                    _tenant_id: str, _session_id: str) -> dict:
    """Post to Slack webhook or channel. Stub for now — logs the intent."""
    channel = action_config.get("channel", "#ops")
    message = action_config.get("message", f"Rule '{rule_name}' fired! ({event_count} matching events)")

    # If a SLACK_WEBHOOK_URL is configured, actually POST
    settings = get_settings()
    webhook_url = getattr(settings, 'slack_webhook_url', None)

    if webhook_url:
        try:
            r = httpx.post(webhook_url, json={"text": f"[{channel}] {message}"}, timeout=5)
            return {"channel": channel, "message": message, "status": r.status_code, "sent": True}
        except Exception as e:
            return {"channel": channel, "message": message, "status": "error", "error": str(e), "sent": False}

    # Stub: log intent
    return {"channel": channel, "message": message, "stub": True, "sent": False}


def _dispatch_webhook(action_config: dict, rule_name: str, event_count: int,
                      _tenant_id: str, _session_id: str) -> dict:
    """POST to a configured webhook URL."""
    url = action_config.get("url", "")
    if not url:
        return {"status": "skipped", "reason": "no url configured"}

    payload = {
        "rule": rule_name,
        "eventCount": event_count,
        "timestamp": _now_iso(),
    }
    try:
        r = httpx.post(url, json=payload, timeout=5)
        return {"url": url, "status": r.status_code, "sent": True}
    except Exception as e:
        return {"url": url, "status": "error", "error": str(e), "sent": False}


def _dispatch_screen_swap(action_config: dict, rule_name: str, _event_count: int,
                          _tenant_id: str, _session_id: str) -> dict:
    """Swap a screen to show specific content. Stub — emits intent."""
    screen_id = action_config.get("screenId", "main")
    return {
        "screenId": screen_id,
        "content": action_config.get("message", f"Rule '{rule_name}' triggered screen swap"),
        "stub": True,
    }


def _dispatch_staff_prompt(action_config: dict, rule_name: str, _event_count: int,
                           _tenant_id: str, _session_id: str) -> dict:
    """Send a human-readable prompt to staff. Stub."""
    return {
        "message": action_config.get("message", f"Staff action needed: {rule_name}"),
        "channel": action_config.get("channel", "staff"),
        "stub": True,
    }


def _dispatch_log(action_config: dict, rule_name: str, event_count: int,
                  _tenant_id: str, _session_id: str) -> dict:
    """Log an insight to the system."""
    return {
        "message": action_config.get("message", f"Rule '{rule_name}' logged ({event_count} events)"),
        "logged": True,
    }


DISPATCHERS: dict[str, Callable] = {
    "slack": _dispatch_slack,
    "webhook": _dispatch_webhook,
    "screen_swap": _dispatch_screen_swap,
    "staff_prompt": _dispatch_staff_prompt,
    "log": _dispatch_log,
}


def _dispatch(rule: RuleRecord, event_count: int, tenant_id: str, session_id: str) -> dict:
    """Route to the correct dispatcher, log to action_log, handle dead-letter."""
    dispatcher = DISPATCHERS.get(rule.action_type)
    if not dispatcher:
        return {"error": f"unknown action type: {rule.action_type}"}

    action_log_id = _log_action_start(rule, tenant_id, session_id, event_count)

    try:
        result = dispatcher(rule.action_config, rule.name, event_count, tenant_id, session_id)
        _log_action_complete(action_log_id, result)
        return result
    except Exception as e:
        error = str(e)
        _log_action_fail(action_log_id, error)
        _send_to_dead_letter(rule, tenant_id, session_id, event_count, error)
        return {"error": error, "dead_letter": True}


def _log_action_start(rule: RuleRecord, tenant_id: str, session_id: str,
                      event_count: int) -> int:
    now = _now_iso()
    cur = db.execute(
        """INSERT INTO action_log (rule_id, tenant_id, session_id, event_seq,
           action_type, payload, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (rule.rule_id, tenant_id, session_id, event_count,
         rule.action_type, json.dumps({"eventCount": event_count, "ruleName": rule.name}), now),
    )
    return cur.lastrowid  # type: ignore[return-value]


def _log_action_complete(action_log_id: int, result: dict) -> None:
    db.execute(
        "UPDATE action_log SET status = ?, completed_at = ?, payload = json_set(payload, '$.result', json(?)) WHERE id = ?",
        ("dispatched", _now_iso(), json.dumps(result), action_log_id),
    )


def _log_action_fail(action_log_id: int, error: str) -> None:
    db.execute(
        "UPDATE action_log SET attempts = attempts + 1, last_error = ? WHERE id = ?",
        (error, action_log_id),
    )


def _send_to_dead_letter(rule: RuleRecord, tenant_id: str, session_id: str,
                         event_count: int, error: str) -> None:
    """Write to dead_letter for HITL retry."""
    now = _now_iso()
    db.execute(
        """INSERT INTO dead_letter (consumer, event_seq, error, created_at)
           VALUES (?, ?, ?, ?)""",
        (f"rule:{rule.rule_id}", event_count, error, now),
    )


# ── Main evaluator ──────────────────────────────────────────────────────────


def evaluate_rules(event_type: str, payload: dict, tenant_id: str, session_id: str,
                   occurred_at: int, event_seq: int) -> list[dict]:
    """Called on every bus event. Evaluates all enabled rules for this tenant.

    Returns list of dispatch results for rules that fired.
    """
    rules = load_rules(tenant_id)
    if not rules:
        return []

    results: list[dict] = []
    event_wrapped = {
        "type": event_type,
        "payload": payload,
        "occurredAt": occurred_at,
        "seq": event_seq,
    }

    for rule in rules:
        if not _match_event(rule, event_type, payload):
            continue
        if _check_cooldown(rule):
            continue

        # Add to sliding window
        _add_to_window(rule.rule_id, session_id, event_wrapped)
        window = _window_for(rule.rule_id, session_id)

        if _check_condition(rule, window):
            _mark_fired(rule)
            dispatch_result = _dispatch(rule, len(window), tenant_id, session_id)
            results.append({
                "ruleId": rule.rule_id,
                "ruleName": rule.name,
                "actionType": rule.action_type,
                "result": dispatch_result,
            })

    return results


# ── Rule testing (dry-run) ──────────────────────────────────────────────────


def test_rule(tenant_id: str, session_id: str, trigger_type: str,
              trigger_zone_id: str | None, condition: dict) -> dict:
    """Dry-run: would this rule fire against real session data?"""
    # Build a temporary RuleRecord
    temp_rule = RuleRecord(
        rule_id="__test__",
        tenant_id=tenant_id,
        name="Test Rule",
        trigger_type=trigger_type,
        trigger_config={"zoneId": trigger_zone_id} if trigger_zone_id else {},
        condition_config=condition,
        action_type="log",
        action_config={},
        enabled=True,
        cooldown_sec=0,
        last_fired_at=None,
    )

    # Read matching events from the bus
    events = db.fetchall(
        "SELECT type, payload, occurred_at FROM event_log WHERE tenant_id = ? AND session_id = ? AND type = ?",
        (tenant_id, session_id, trigger_type),
    )

    matching = []
    for e in events:
        payload = json.loads(e["payload"]) if isinstance(e["payload"], str) else e["payload"]
        if _match_event(temp_rule, trigger_type, payload):
            matching.append({
                "type": e["type"],
                "payload": payload,
                "occurredAt": int(datetime.fromisoformat(e["occurred_at"].replace("Z", "+00:00")).timestamp() * 1000) if e["occurred_at"] else 0,
            })

    # Push into window — use the last event's timestamp as "now" so windows work
    max_ts = max((m["occurredAt"] for m in matching), default=_now_ms())
    # Temporarily shift _check_condition's time reference by patching _now_ms
    import app.rules as rules_mod
    original_now = rules_mod._now_ms
    rules_mod._now_ms = lambda: max_ts + 1  # 1ms after last event
    try:
        for m in matching:
            _add_to_window("__test__", session_id, m)
        window = _window_for("__test__", session_id)
        would_fire = _check_condition(temp_rule, window)
    finally:
        rules_mod._now_ms = original_now
        _event_windows.pop("__test__:" + session_id, None)

    return {
        "wouldFire": would_fire,
        "matchingEvents": len(matching),
        "reason": (f"{len(matching)} matching events found, condition {'met' if would_fire else 'not met'} "
                   f"({condition.get('type', 'any')}, count={condition.get('count')}, window={condition.get('windowSec', 30)}s)")
    }
