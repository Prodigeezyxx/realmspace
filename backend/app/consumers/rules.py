"""
The rules evaluator — ADR-002's "exactly one evaluator on the edge".

Reads the bus, matches events against the tenant's stored rule documents, and
appends `rule.fired`. It does **not** carry out the action: that is
`consumers/dispatch.py`, reading `rule.fired` off the same bus. The split is
deliberate and is discussed under "Why firing and dispatching are two consumers"
below.

## What this file is, precisely

`floats-agent` shipped a working evaluator on 2026-08-10. ADR-002 adopted its
rule *language* verbatim — "so the two tracks do not end up with two rule
languages" — and then recorded four places where a working evaluator and a
**replayable** one differ. This is that evaluator with the four corrections, and
each one is marked below with the ADR section it comes from. They are the reason
this file exists rather than a port.

  §1  the window is read from the bus, not held in a dict a restart empties
  §2  cooldown compares event time, never datetime.now()
  §3  `rule.fired` carries a derived event id, so a replay dedupes
  §4  a `none` condition is judged at a boundary, not on arrival

## Why firing and dispatching are two consumers

Posting to Slack takes as long as Slack takes. Evaluation is on the path that
event-bus-spec.md §4 gives a `< 3s` budget, and a rule that fires promptly and a
webhook that answers slowly are different problems with different fixes. Keeping
them apart also means a dead-lettered dispatch parks the *dispatch*, leaving the
firing itself on the log as the durable record that the rule matched — which is
what an operator asking "did my rule work?" is actually asking.

## Why `handles` is empty

A rule may name any type registered in event-bus-spec.md §3, including ones no
producer writes yet. A static filter here would have to be updated every time an
operator writes a rule about a new type, which is precisely the coupling ADR-002
removed by making rules data. The per-event cost is one indexed lookup that
returns nothing for a type no rule mentions.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from app import db, repository
from app.consumers.base import Consumer
from app.consumers.ids import derive_event_id
from app.models import EventLog, Rule
from app.schemas import EventIn

log = logging.getLogger(__name__)

FIRED = "rule.fired"
SESSION_ENDED = "session.ended"


def _payload_field(event: EventLog, *names: str) -> Any:
    """Read the first of several spellings of one payload field.

    Payloads are stored as the producer wrote them, and `event-bus-spec.md` §3
    pins them in **snake_case**: a `spatial.dwell` from the tracker carries
    `anon_id`, `zone_id`, `duration`. The rule *document* is camelCase, like every
    other wire contract here, and reading a rule's vocabulary back into the
    payload is how this file first shipped — matching `zoneId` against events
    that only ever contained `zone_id`.

    The result was a rule that was armed, correct, watching the right events, and
    silently matched nothing. No error, no dead letter, no dwell counted: exactly
    the failure the log is worst at revealing. Caught by
    `tests/test_phase3_acceptance.py`, which is the only test that runs the
    tracker and the evaluator against each other; every unit test on either side
    had been written in that side's own dialect and agreed with itself.

    Both spellings are accepted, snake_case first, on the same grounds the spec
    already gives for `anon_id` / `person_id`: "one script has to work against
    either backend or the comparison breaks." The browser has the mirror of this
    in `lib/bus/wire.ts`, which camelises on arrival.
    """
    for name in names:
        if name in event.payload:
            return event.payload[name]
    return None


class RulesConsumer(Consumer):
    name = "rules"

    #: Not retryable, and the reason is the same shape as broadcast's: a rule
    #: exists to act on a room as it is now. Re-running one parked event hours
    #: later evaluates a window that has closed, against a room that has changed,
    #: and dispatches an action about neither. The repair for a parked evaluation
    #: is a cursor rewind — an operator decision, not a button — which replays the
    #: surrounding events too and is safe because every firing is deduped by its
    #: derived id.
    retryable = False

    #: Empty on purpose — see the module docstring.
    handles: tuple[str, ...] = ()

    async def handle(self, event: EventLog) -> None:
        # `rule.fired` is on the bus like everything else, and evaluating rules
        # against it would let one rule trigger another. That is a feature
        # somebody will eventually want and a loop nobody wants first: two rules
        # each triggered by the other's firing produce events forever, at the
        # speed of the poll loop. Excluded until the spec says what a chained
        # rule means (ADR-002 leaves `triggerType` open, so a rule *can* name
        # `rule.fired`; this refuses to act on it rather than pretending it is
        # not expressible).
        if event.type == FIRED:
            return

        async with db.SessionLocal() as session:
            await db.scope_to_tenant(session, event.tenant_id)

            rules = await self._candidates(session, event)
            if not rules:
                return

            fired: list[EventIn] = []
            for rule in rules:
                built = await self._evaluate(session, rule, event)
                if built is not None:
                    fired.append(built)

            for out in fired:
                await repository.append_event(session, out)
            if fired:
                await session.commit()

    # ── which rules could this event possibly match ───────────────────────────

    async def _candidates(self, session, event: EventLog) -> list[Rule]:
        """Enabled rules this event could fire.

        Two families, and the second is the whole of §4. A rule whose trigger
        type matches is a candidate for the obvious reason. A `none` rule is a
        candidate on *any* event, because "nothing happened for 30 seconds" is
        only ever discovered by something else happening — the arrival that ends
        the quiet is by definition not of the type being waited for.
        """
        direct = await repository.list_rules(
            session,
            tenant_id=event.tenant_id,
            trigger_type=event.type,
            enabled_only=True,
        )
        # Cheap because the index is partial on `enabled` and a booth has tens of
        # rules, not thousands. If that stops being true the fix is a
        # `condition->>'type' = 'none'` partial index, not a cache — a cache here
        # would be the in-memory state §1 exists to remove.
        quiet = [
            r
            for r in await repository.list_rules(
                session, tenant_id=event.tenant_id, enabled_only=True
            )
            if r.condition.get("type") == "none" and r.trigger_type != event.type
        ]
        return [*direct, *quiet]

    # ── evaluation ────────────────────────────────────────────────────────────

    async def _evaluate(
        self, session, rule: Rule, event: EventLog
    ) -> EventIn | None:
        """Decide whether `rule` fires on `event`, and build the firing if so."""
        kind = rule.condition.get("type")

        if kind == "threshold":
            matched = await self._threshold(session, rule, event)
        elif kind == "any":
            matched = self._any(rule, event)
        elif kind == "none":
            matched = await self._none(session, rule, event)
        else:
            # Unreachable through the API, which validates against a closed
            # union — but a row written by a migration or by hand should not
            # take the consumer down.
            log.warning("rule %s has unknown condition type %r", rule.rule_id, kind)
            return None

        if matched is None:
            return None

        # §2 — cooldown, on event time. `matched["at"]` is when the condition
        # became true, which for a `none` rule is the moment the quiet window
        # closed rather than the moment the arrival that revealed it landed.
        if await self._in_cooldown(session, rule, event, at=matched["at"]):
            return None

        return self._fired(rule, event, matched)

    async def _threshold(
        self, session, rule: Rule, event: EventLog
    ) -> dict[str, Any] | None:
        """N of the trigger type within `windowSec`.

        **§1 — the window is a read, not a dict.** `floats-agent` keeps
        `_event_windows: dict[str, list[dict]]` trimmed at 200 entries. A restart
        mid-session empties it, so a rule that would have fired does not; a
        replay of the same events produces a different set of firings; and the
        trim silently drops the oldest events of a busy window. Reading the log
        has none of those properties, and the log is the thing a replay replays.

        ## Counting people, not events

        `count: 5` in "when 5 people dwell at the entrance" means five people.
        One person can emit several dwells in one window — they leave the zone
        and come back — and counting events would fire a five-person rule on one
        restless visitor. So the count is of distinct `anonId`s where the payload
        carries one, and of events where it does not (a `surface.interaction`
        from a booth with no tracker attached, say). Which of the two was used is
        recorded in the firing, because it changes what the number means.
        """
        window_sec = rule.condition["windowSec"]
        since = event.occurred_at - dt.timedelta(seconds=window_sec)

        events = await repository.read_window(
            session,
            tenant_id=event.tenant_id,
            session_id=event.session_id,
            type=rule.trigger_type,
            since=since,
            until=event.occurred_at,
            before_seq=event.seq,
        )
        events = [e for e in events if self._matches_scope(rule, e)]

        anon_ids = {
            anon
            for anon in (_payload_field(e, "anon_id", "anonId") for e in events)
            if isinstance(anon, str)
        }
        counted_by = "people" if anon_ids else "events"
        observed = len(anon_ids) if anon_ids else len(events)

        if observed < rule.condition["count"]:
            return None
        return {
            "at": event.occurred_at,
            "observed": observed,
            "countedBy": counted_by,
            "windowSec": window_sec,
        }

    def _any(self, rule: Rule, event: EventLog) -> dict[str, Any] | None:
        """The trigger itself, with no counting. Still scope-checked, because
        `any dwell` and `any dwell in the entrance` are different rules."""
        if not self._matches_scope(rule, event):
            return None
        return {"at": event.occurred_at, "observed": 1, "countedBy": "events"}

    async def _none(
        self, session, rule: Rule, event: EventLog
    ) -> dict[str, Any] | None:
        """Nothing of the trigger type for `windowSec`.

        **§4 — judged at a boundary.** Evaluated on arrival, "none" asks whether
        the window is empty at the moment something happened, which is nearly a
        contradiction. What it means is *nothing happened for windowSec*, and
        that becomes true at a moment when, by definition, no event is arriving
        to prompt the check. So it is judged when the next thing does happen —
        any event, or `session.ended` — by looking backwards.

        The firing is dated at the moment the quiet window **closed**, not at the
        arrival that revealed it. A staff prompt saying "the demo station has
        been empty for 5 minutes" is about a silence that started five minutes
        ago, and dating it now would put it after the visitor who ended it.

        ## One firing per silence

        Every event after the boundary reveals the same silence — two arrivals a
        second apart both look back to the same last occurrence and both find a
        gap longer than the window. Suppressing the second with control flow
        would mean asking "has anything already crossed this boundary?", which is
        another backwards read and another thing to get wrong.

        Instead the firing's identity is the **silence**, not the arrival: the
        dedupe key is the seq of the event the quiet started after, so every
        later arrival derives the same `event_id` and the bus's UNIQUE
        constraint refuses the second. Same mechanism as §3, applied to a
        condition whose cause is an absence. Cooldown is then a genuine cooldown
        rather than the thing holding this together.
        """
        window_sec = rule.condition["windowSec"]
        window = dt.timedelta(seconds=window_sec)

        previous = await repository.last_event_of_type(
            session,
            tenant_id=event.tenant_id,
            session_id=event.session_id,
            type=rule.trigger_type,
            before_seq=event.seq,
        )

        if previous is None:
            # Nothing of this type has *ever* happened in this session. That is
            # a silence, but it has no start, and dating it from the session's
            # first event would fire on a booth that has been open for ten
            # seconds. A `none` rule needs one occurrence to have something to
            # be quiet since; until then it waits.
            return None

        quiet_since = previous.occurred_at
        if event.occurred_at - quiet_since < window:
            return None

        closed_at = quiet_since + window
        return {
            "at": closed_at,
            # The silence, not the arrival — see the docstring.
            "dedupe": f"quiet:{previous.seq}",
            "observed": 0,
            "countedBy": "events",
            "windowSec": window_sec,
            "quietSince": quiet_since.isoformat(),
            # Which event revealed the silence. `session.ended` here means the
            # activation closed without the thing ever happening again, which
            # reads differently in a report from a visitor turning up.
            "noticedBy": event.type,
        }

    def _matches_scope(self, rule: Rule, event: EventLog) -> bool:
        """Zone and dwell-length narrowing.

        `triggerZoneId` and `condition.zoneId` are both honoured and are not the
        same thing — the trigger decides which events wake the rule, the
        condition decides which are counted. In practice a composer sets one or
        the other; both are checked so a document that sets both is not silently
        half-applied.

        `minDwellSec` matters more than it looks: `spatial.dwell` is emitted for
        every stay including a two-second one, so "5 people at the entrance for
        30 seconds" without it counts five people who walked past.
        """
        zone = _payload_field(event, "zone_id", "zoneId")
        if rule.trigger_zone_id is not None and zone != rule.trigger_zone_id:
            return False
        want_zone = rule.condition.get("zoneId")
        if want_zone is not None and zone != want_zone:
            return False

        min_dwell = rule.condition.get("minDwellSec")
        if min_dwell is not None:
            duration = _payload_field(event, "duration", "durationSec")
            if not isinstance(duration, (int, float)) or duration < min_dwell:
                return False
        return True

    # ── cooldown ──────────────────────────────────────────────────────────────

    async def _in_cooldown(
        self, session, rule: Rule, event: EventLog, *, at: dt.datetime
    ) -> bool:
        """**§2 — cooldown on event time.**

        `floats-agent` compares `datetime.now(timezone.utc)` against a
        `last_fired_at` column. Two consequences, and the second is why this is
        an ADR item rather than a nitpick:

        - a replay produces different output from the original run, because the
          wall clock has moved and the column has not been rewound. The tracker's
          dropout sweep is on event time for exactly this reason
          (event-bus-spec.md: "a wall-clock timer … would make a replay produce
          different events from the original run, which is a worse trade").
        - the answer depends on a mutable row rather than on the log, so two
          processes evaluating the same rule disagree about whether it is in
          cooldown depending on which one wrote last.

        So the last firing is read from the bus and compared on `occurred_at`.
        It is located by the `triggerSeq` it carries rather than by its own seq —
        `repository.last_rule_firing` explains why at length, and it is the
        difference between a cooldown that works and one that silently never
        applies.
        """
        if rule.cooldown_sec <= 0:
            return False

        last = await repository.last_rule_firing(
            session,
            tenant_id=event.tenant_id,
            session_id=event.session_id,
            rule_id=rule.rule_id,
            before_trigger_seq=event.seq,
        )
        if last is None:
            return False
        return at - last.occurred_at < dt.timedelta(seconds=rule.cooldown_sec)

    # ── the firing ────────────────────────────────────────────────────────────

    def _fired(self, rule: Rule, event: EventLog, matched: dict[str, Any]) -> EventIn:
        """Build the `rule.fired` event.

        **§3 — a derived event id.** From (consumer, tenant, session, rule, and
        the identity of what caused the match), via `consumers/ids.py`. A random
        id means a replayed input mints a new output the bus cannot dedupe, and —
        because the dispatcher keys its idempotency on this id — the action fires
        twice.

        The cause is the triggering `seq` for a condition about something that
        happened, and the silence for a `none` condition about something that did
        not (`matched["dedupe"]`). Either way it is unique, stable across a
        replay, and *the thing the operator would point at* when asked why the
        rule fired — which is the property that makes the id meaningful rather
        than merely deterministic.

        ## Why the action is copied into the payload

        The dispatcher could look the rule up by id. It reads it from here
        instead, so that what gets carried out is the document **as it was when
        the rule matched**. An operator who edits a rule between the firing and
        the dispatch has not retroactively changed what their booth decided to
        do, and ADR-002's second reason for rules-as-data — "a rule must be
        inspectable after it fires … reading the document as it was" — stays true
        of the log rather than only of the current row.
        """
        return EventIn(
            event_id=derive_event_id(
                self.name,
                event.tenant_id,
                event.session_id,
                rule.rule_id,
                matched.get("dedupe", event.seq),
            ),
            tenant_id=event.tenant_id,
            session_id=event.session_id,
            type=FIRED,
            payload={
                "ruleId": rule.rule_id,
                "ruleName": rule.name,
                "triggerType": rule.trigger_type,
                #: The event that caused this, so "why did it fire?" is one
                #: lookup rather than a reconstruction.
                "triggerSeq": event.seq,
                "condition": rule.condition,
                "action": rule.action,
                "matched": {
                    k: v for k, v in matched.items() if k not in ("at", "dedupe")
                },
            },
            # When the condition became true, which for a `none` rule is when the
            # quiet window closed rather than when the arrival that revealed it
            # landed. Never now(): a replay must date a firing where the original
            # run dated it.
            occurred_at=matched["at"],
        )
