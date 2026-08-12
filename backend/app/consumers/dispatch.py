"""
The dispatcher — carries out what a rule decided to do.

Reads `rule.fired` off the bus and runs one handler from `app/actions/`. The
evaluator (`consumers/rules.py`) decides; this acts; the two are separate
consumers for the reasons in that module's docstring.

## The one thing this file is really about

ADR-002 §3: "**Per-dispatch idempotency keys on that `event_id`**, as Phase 3
requires, so a retry after a timeout cannot double-post."

That cannot be done with care alone. Posting to Slack is not idempotent, and a
POST that times out is genuinely ambiguous — the message may or may not have
arrived. Every mechanism already in this codebase for making replay safe (derived
event ids, MERGE on a stable key, ON CONFLICT DO NOTHING) works because the
target is a database we control. Slack is not.

So the decision is moved to a database that *is* ours, before the call goes out:
`repository.claim_dispatch` inserts a row keyed UNIQUE on (fired_event_id,
action_type). Winning the insert is permission to make the call. Losing it means
somebody already has, and this attempt returns without acting.

The residual case is honest rather than solved: a process that dies between the
claim and the outcome leaves a row reading `claimed`, and nobody can say whether
Slack got the message. `complete_dispatch` explains why that row is left alone.

## Why the failure path is deliberately plain

An action raises; `base.Consumer` retries with exponential backoff and then
dead-letters. That puts a failed Slack post in the `/ops` queue built for
precisely this, with the traceback the operator needs, and it is why no handler
in `app/actions/` catches its own exceptions.

`retryable` stays False. A dead-lettered dispatch is one whose retry button would
carry out an action about a moment that has passed — the same reason the tracker
and broadcast decline it.
"""

from __future__ import annotations

import logging

from app import actions, db, repository
from app.actions.context import DispatchContext
from app.consumers.base import Consumer
from app.cost import meter
from app.models import EventLog

log = logging.getLogger(__name__)

FIRED = "rule.fired"


class DispatchConsumer(Consumer):
    name = "dispatch"
    handles = (FIRED,)

    #: See the module docstring. An action is about the room as it is now.
    retryable = False

    async def handle(self, event: EventLog) -> None:
        firing = event.payload
        action = firing.get("action") or {}
        action_type = action.get("type")

        handler = actions.registry.get(action_type)
        if handler is None:
            # The API validates `action.type` against a closed union, so this is
            # a rule that predates an action type being removed, or a hand-written
            # row. Raising sends it to the dead-letter queue, where an operator
            # sees which rule names an action nothing can carry out — better than
            # a log line nobody reads about a rule that appears to be armed.
            raise ValueError(
                f"rule {firing.get('ruleId')!r} names action type {action_type!r}, "
                f"which nothing dispatches; known: {sorted(actions.registry)}"
            )

        async with db.SessionLocal() as session:
            await db.scope_to_tenant(session, event.tenant_id)

            claim = await repository.claim_dispatch(
                session,
                tenant_id=event.tenant_id,
                fired_event_id=event.event_id,
                rule_id=firing.get("ruleId", ""),
                action_type=action_type,
            )
            if claim is None:
                # Somebody already holds this — an earlier attempt of ours after
                # a crash mid-batch, or a replay of a firing already acted on.
                # Not an error and not worth a retry: the row it would wait for
                # is already delivered or already parked.
                log.debug(
                    "dispatch already claimed for %s/%s", event.event_id, action_type
                )
                return
            # The claim must be visible to any other attempt *before* the call
            # goes out, or two processes both win an uncommitted insert and both
            # post. This is the one commit that cannot wait for the end.
            await session.commit()
            # …and committing clears the tenant scope, because `scope_to_tenant`
            # sets it with `is_local => true` — transaction-scoped, which is what
            # stops a pooled connection carrying one request's tenant into the
            # next (see db.scope_to_tenant). Everything below runs in a *new*
            # transaction and needs the scope again.
            #
            # Getting this wrong is silent in the worst way: RLS makes the
            # dispatch row invisible rather than erroring, so `complete_dispatch`
            # updates nothing and every delivered action stays at `claimed`
            # forever. Found by test_log_action_records_a_dispatch_and_sends_nothing.
            await db.scope_to_tenant(session, event.tenant_id)

            ctx = DispatchContext(
                action=action,
                firing=firing,
                fired_event_id=event.event_id,
                tenant_id=event.tenant_id,
                session_id=event.session_id,
                occurred_at=event.occurred_at,
                session=session,
            )

            try:
                detail = await handler.deliver(ctx)
            except Exception as exc:  # noqa: BLE001 — recorded, then re-raised
                # Record the failure and commit it before letting base.Consumer
                # see the exception, so the `/ops` row and the dispatch row tell
                # the same story. Then re-raise: retry and dead-lettering are the
                # base class's job, and swallowing this here would mean a failed
                # action never reaches the queue built to catch it.
                await repository.complete_dispatch(
                    session,
                    dispatch_id=claim.id,
                    status="failed",
                    detail=f"{type(exc).__name__}: {exc}"[:2000],
                )
                await session.commit()
                raise

            await repository.complete_dispatch(
                session, dispatch_id=claim.id, status="delivered", detail=detail[:2000]
            )
            await self._meter(ctx, action_type)
            await session.commit()

    async def _meter(self, ctx: DispatchContext, action_type: str) -> None:
        """Record what the action cost, if anything.

        The first thing on this track to spend money, and therefore the first
        reading `/live`'s cost tile has ever had — until now it read "no meter
        yet", which was true and is the reason `cost.py` was written before it
        had a caller.

        `cause` is `(rule_id, fired_event_id)`, which is the exact tuple
        `cost.py` names: "for a rule dispatch, `(rule_id, rule_fired_event_id)`".
        Both are stable across a replay, which is what makes the derived event id
        safe — a duplicated cost survives every replay and lands in the direction
        that overstates what a client's activation cost.

        The append shares the caller's transaction, so a spend and the dispatch
        it paid for land together or neither does.

        ## Why the reading is `1 action` and not a sum of money

        The obvious design is a configured price per action, so the tile shows
        dollars. It would also mean shipping a default, and a default price is a
        number this repo would be inventing on a client's behalf — the same
        mistake `roi-framework.md` rules out for revenue and the report already
        refuses to make. What is actually known here is that one action went out.

        So the meter counts actions, in a unit that is not a currency.
        `lib/roi/cost.ts` groups by unit and only totals money across events
        already denominated in one, so this shows as a count on its own line and
        is never silently added to dollars. A deployment that knows what its
        Slack tier costs meters that alongside; nothing here has to guess.
        """
        if action_type not in actions.METERED:
            return
        await meter(
            ctx.session,
            tenant_id=ctx.tenant_id,
            session_id=ctx.session_id,
            kind="action_unit",
            amount=1.0,
            unit="actions",
            occurred_at=ctx.occurred_at,
            cause=(ctx.firing.get("ruleId", ""), str(ctx.fired_event_id)),
            detail={"actionType": action_type},
        )
