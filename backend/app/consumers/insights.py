"""
The insight agent — the room summarised every N minutes, with its receipts.

`roadmap.md` Phase 5: *"LLM insight generation every N minutes →
`insight.generated`"*. The specification this implements is `floats-agent`'s,
adopted verbatim so the two tracks do not grow two insight contracts — the same
move ADR-002 made with their rule spec:

> **Insight agent**: periodic bounded graph snapshot → `insight.generated` with
> text + supporting event IDs; shown on `/live`; click-through opens the
> underlying events.

Their acceptance clause is the demanding half: *"insights on `/live` trace to
source events"*. An insight nobody can check is the same failure as the report's
invented `1,287 visitors` — it reads as authority. So every claim carries the ids
of the events it was computed from, and the surface opens them.

## Triggered on event time, never on the clock

This is ADR-002's second correction, which the rules evaluator already had to
make and which is easy to get wrong again here. "Every 10 minutes" tempts a
`sleep`, and a consumer that summarised on wall-clock time would produce a
different set of insights every time the log was replayed — different windows,
different boundaries, different text, from identical events.

So the trigger is a comparison of *event* time: has the interval elapsed between
the last `insight.generated` for this session and the event now being processed?
`repository.last_event_of_type` answers it with an exclusive `before_seq`, so a
replay cannot see an insight that, at the moment being replayed, had not been
written yet.

The consequence is worth stating plainly: **insights advance with the floor, not
with the wall.** A session with no traffic for an hour produces no insights about
that hour, which is correct — there was nothing to observe — and a replay of a
finished session regenerates exactly the same ones.

## Fixed, contiguous windows from the session's first event

Windows are `[first, first+N)`, `[first+N, first+2N)`, and so on — not "the last
N minutes before whatever event I am looking at". Every event belongs to exactly
one window, the boundaries do not move with traffic, and an activation's opening
is summarised as its own window instead of being half-swallowed by a boundary
nobody chose.

One event can close several windows. A quiet spell followed by a burst means the
windows in between are complete and unwritten, and catching them up is the same
work as writing one — the alternative is an insight per event forever until the
backlog clears, with the boundaries decided by arrival order.

## The trap this walked into, and it is ADR-002's

The first version asked `last_event_of_type(..., before_seq=event.seq)` for the
previous insight, which can **never** return one: an insight is appended at a
higher seq than every event it summarises, so a bound at the triggering event's
seq excludes it by construction. The lookup returned None forever, every event
recomputed window zero, and the derived id quietly swallowed the duplicates — so
it looked like it worked, and a two-minute interval produced exactly one insight
for a ten-minute session.

ADR-002 records the identical bug in the rules evaluator: *"a cooldown ordered on
the firing's own seq never applies at all, because a firing is always appended
after its cause."* There the fix was to order on the carried `triggerSeq`. Here
it is to drop the bound — safe, because the unbounded lookup only decides which
windows still need writing, while what an insight *contains* is still computed
under `before_seq`. A replay therefore reproduces the same text and the same
citations.

## Bounded, and anonymous by construction

`app/llm/digest.py` reads only `spatial.*` and `surface.interaction`, caps what
it reads, and reports `truncated` when it hits the cap. No consent, no handoff,
no contact reaches a prompt or a payload — which is what keeps
`insight.generated` in `ANONYMOUS_EVENT_TYPES` and on the anonymised
cloud-sync path (`event-bus-spec.md` §6) as a fact rather than an intention.

## The provider phrases; the composed sentence is the floor

The same relationship Ask's `catalogue.Entry.phrase` and the SDR's
`deterministic_draft` have. A provider outage costs an operator some prose, not
the observation.
"""

from __future__ import annotations

import datetime as dt
import logging

from app import db, llm, repository
from app.config import get_settings
from app.consumers.base import Consumer
from app.consumers.ids import derive_event_id
from app.cost import meter
from app.graph import repository as graph_repo
from app.graph.driver import get_driver
from app.llm import digest as digest_builder
from app.llm import budget, prompts
from app.llm.base import LlmError
from app.models import EventLog
from app.schemas import EventIn

log = logging.getLogger(__name__)

GENERATED = "insight.generated"

#: Used when a session has no configuration of its own. `PRD.md`: "LLM-generated
#: insights every 10 minutes".
DEFAULT_INTERVAL_MINUTES = 10

#: How many windows one event may close. A bound rather than a limit anybody
#: should hit: it stops a single late event from summarising a whole day inside
#: one handler, and the next event picks the rest up.
MAX_CATCHUP = 24


class InsightsConsumer(Consumer):
    name = "insights"

    #: The stream it watches for the passage of event time. Deliberately the same
    #: types `digest.py` summarises: a window is worth summarising when something
    #: happened in it, and these are the somethings.
    handles = digest_builder.SOURCE_TYPES

    #: Retryable. The emitted id is derived from the window, the digest is a pure
    #: function of the log within a seq bound, and nothing leaves the building —
    #: so re-running one parked event produces exactly what the first attempt
    #: would have.
    retryable = True

    async def handle(self, event: EventLog) -> None:
        async with db.SessionLocal() as session:
            await db.scope_to_tenant(session, event.tenant_id)

            first_at = await self._first_event_at(session, event)
            if first_at is None:
                return

            interval = dt.timedelta(minutes=await self._interval(event))
            # Windows 0 … complete-1 have ended by the time of this event.
            complete = int((event.occurred_at - first_at) / interval)
            if complete <= 0:
                # Still filling the first window. Saying anything now would mean
                # the boundary moved with whoever asked.
                return

            # No seq bound — see the module docstring. An insight is always at a
            # higher seq than the events it summarises.
            previous = await repository.last_event_of_type(
                session,
                tenant_id=event.tenant_id,
                session_id=event.session_id,
                type=GENERATED,
            )
            start = 0
            if previous is not None:
                start = self._index_of(previous, first_at, interval) + 1

            for index in range(start, min(complete, start + MAX_CATCHUP)):
                await self._summarise(
                    session,
                    event=event,
                    window_from=first_at + index * interval,
                    window_to=first_at + (index + 1) * interval,
                    minutes=int(interval.total_seconds() // 60),
                )

    async def _summarise(
        self,
        session,
        *,
        event: EventLog,
        window_from: dt.datetime,
        window_to: dt.datetime,
        minutes: int,
    ) -> None:
        built = await digest_builder.build(
            session,
            tenant_id=event.tenant_id,
            session_id=event.session_id,
            window_from=window_from,
            window_to=window_to,
            before_seq=event.seq,
        )
        if built.empty:
            # A quiet window. No insight rather than "nothing happened", which is
            # a row an operator learns to scroll past.
            return

        measurements = built.measurements()
        text = prompts.deterministic_insight(
            measurements=measurements, minutes=minutes
        )
        basis = "deterministic"
        tokens = 0

        integration = await repository.get_integration_of_kind(
            session, tenant_id=event.tenant_id, kind=llm.KIND, active_only=True
        )
        provider = llm.provider_for(integration)

        # The spender this budget exists for: it fires on a timer, so it is the
        # one that can run up a bill while nobody is watching.
        may_call, refusal = await budget.within_budget(
            session, tenant_id=event.tenant_id, spender="insight"
        )
        if not may_call:
            log.info("insights: %s", refusal)

        if may_call and provider.capabilities().get("reasons"):
            try:
                written = await provider.complete(
                    prompts.insight_prompt(
                        measurements=measurements, minutes=minutes
                    ),
                    max_tokens=200,
                )
                tokens = written.total_tokens
                if written.text.strip():
                    text = written.text.strip()
                    basis = provider.provider
            except LlmError as exc:
                log.warning("insights: %s could not write: %s", provider.provider, exc)

        insight_id = derive_event_id(
            "insight", event.tenant_id, event.session_id, window_from.isoformat()
        )

        await repository.append_event(
            session,
            EventIn(
                event_id=insight_id,
                tenant_id=event.tenant_id,
                session_id=event.session_id,
                type=GENERATED,
                payload={
                    "insight_id": str(insight_id),
                    "text": text,
                    # The whole point of the contract: what this claim rests on,
                    # resolvable against the log by anybody who doubts it.
                    "refs": built.refs(),
                    "measurements": measurements,
                    "window": {
                        "from": window_from.isoformat(),
                        "to": window_to.isoformat(),
                        "minutes": minutes,
                    },
                    # `data-model.md`'s `(:Insight).generated_by`.
                    "generated_by": "llm" if basis != "deterministic" else "rule",
                    "basis": basis,
                    "truncated": built.truncated,
                },
                # The window's end, not the triggering event's time. An insight is
                # about a period, and dating it by whichever event happened to
                # cross the boundary would make its position in the log depend on
                # traffic.
                occurred_at=window_to,
            ),
        )

        if tokens:
            await meter(
                session,
                tenant_id=event.tenant_id,
                session_id=event.session_id,
                kind="llm_tokens",
                amount=float(tokens),
                unit="tokens",
                occurred_at=window_to,
                cause=("insight", str(insight_id)),
                detail={"provider": basis, "spender": "insight"},
            )

        await session.commit()
        await self._write_node(event, insight_id, text, basis, window_to, built)

    # ── the pieces ────────────────────────────────────────────────────────────

    @staticmethod
    def _index_of(
        insight: EventLog, first_at: dt.datetime, interval: dt.timedelta
    ) -> int:
        """Which window an existing insight covered.

        Read from its own `window.from` rather than from `occurred_at`, so the
        chain is stated by the insights themselves and does not shift if the
        dating rule above ever changes.
        """
        window = (insight.payload or {}).get("window") or {}
        starts = window.get("from")
        at = dt.datetime.fromisoformat(starts) if starts else insight.occurred_at
        return int((at - first_at) / interval)

    async def _interval(self, event: EventLog) -> int:
        settings = get_settings()
        async with get_driver().session(database=settings.neo4j_database) as gs:
            config = (
                await graph_repo.session_config(
                    gs, tenant_id=event.tenant_id, session_id=event.session_id
                )
                or {}
            )
        return int(config.get("insight_interval_minutes") or DEFAULT_INTERVAL_MINUTES)

    async def _first_event_at(self, session, event: EventLog) -> dt.datetime | None:
        """When this session's first summarisable event happened.

        The first window opens there rather than at the triggering event minus an
        interval, so an activation's opening minutes are summarised as their own
        window instead of being cut by a boundary nobody chose.
        """
        for type in digest_builder.SOURCE_TYPES:
            rows = await repository.read_events(
                session,
                tenant_id=event.tenant_id,
                session_id=event.session_id,
                type=type,
                limit=1,
            )
            if rows:
                return rows[0].occurred_at
        return None

    async def _write_node(
        self,
        event: EventLog,
        insight_id,
        text: str,
        basis: str,
        at: dt.datetime,
        built: digest_builder.Digest,
    ) -> None:
        """The queryable copy. `data-model.md`: "every LLM-generated insight is
        persisted as a first-class node in the same graph"."""
        settings = get_settings()
        top = built.top_zone()
        async with get_driver().session(database=settings.neo4j_database) as gs:
            await graph_repo.upsert_insight(
                gs,
                tenant_id=event.tenant_id,
                session_id=event.session_id,
                insight_id=str(insight_id),
                text=text,
                generated_by="llm" if basis != "deterministic" else "rule",
                timestamp=at.isoformat(),
                about_zone_ids=[top["zone_id"]] if top else [],
            )

