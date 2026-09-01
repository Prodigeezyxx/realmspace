"""
Registry of consumers, plus a CLI to run one on its own.

The API process starts all of them as background tasks (app/main.py). This entry
point exists for the times you want one in isolation — watching the tracker's
logs without the API's noise, or replaying a cursor and seeing exactly what one
consumer does with it.

    python -m app.consumers.run tracker
    python -m app.consumers.run graph_writer --once
    python -m app.consumers.run --list
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from app.consumers.attribution import AttributionConsumer
from app.consumers.base import Consumer
from app.consumers.broadcast import BroadcastConsumer
from app.consumers.crm_delivery import CrmDeliveryConsumer
from app.consumers.crm_retract import CrmRetractConsumer
from app.consumers.dispatch import DispatchConsumer
from app.consumers.erasure import ErasureConsumer
from app.consumers.graph_writer import GraphWriterConsumer
from app.consumers.handoff_delivery import HandoffDeliveryConsumer
from app.consumers.outcomes import OutcomesConsumer
from app.consumers.identity import IdentityConsumer
from app.consumers.drift import DriftConsumer
from app.consumers.gaze import GazeConsumer
from app.consumers.grouping import GroupingConsumer
from app.consumers.insights import InsightsConsumer
from app.consumers.reanonymise import ReAnonymiseConsumer
from app.consumers.retention import RetentionConsumer
from app.consumers.rules import RulesConsumer
from app.consumers.sdr import SdrConsumer
from app.consumers.touch import TouchConsumer
from app.consumers.tracker import TrackerConsumer
from app.graph.driver import connect, disconnect

# Order matters when running them all in one pass: the tracker produces the
# spatial events the graph writer consumes, so running it first means a single
# pass carries a detection all the way to the graph.
#
# Phase 3 extends that chain by two. The tracker's spatial.* events are what
# rules fire on, and the evaluator's rule.fired is what the dispatcher acts on,
# so in this order one pass carries a detection all the way to a Slack post.
# Reversed, each link would wait a poll interval for the one before it — which
# would still be correct, and would spend most of the `< 3s` budget in
# event-bus-spec.md §4 waiting on nothing.
# Phase 4 adds two more, and their position is for the same reason. The identity
# consumer needs the Person the graph writer creates, so it follows it; the
# re-anonymiser undoes what identity draws, so it follows that. Out of order they
# are still correct — identity retries until the Person appears — but a capture
# would spend a poll interval and a retry backoff waiting for a node that a
# single pass could have produced first.
CONSUMER_CLASSES: list[type[Consumer]] = [
    TrackerConsumer,
    # Between the two, and both sides of that are load-bearing. It resolves a
    # tablet's tap against who the tracker says was standing in the zone, and
    # **refuses to answer until the tracker's cursor has passed the tap** — so
    # ahead of the tracker every tap would spend a retry backoff waiting for
    # output this same pass could have produced. Ahead of the graph writer so
    # one pass carries a finger all the way to `INTERACTED_WITH`.
    TouchConsumer,
    GraphWriterConsumer,
    IdentityConsumer,
    ReAnonymiseConsumer,
    # Attribution reads the link identity draws, and delivery carries what
    # attribution builds — so the lead chain sits in that order for the same
    # single-pass reason as the rule chain below it.
    AttributionConsumer,
    HandoffDeliveryConsumer,
    # Both destinations for one handoff, each claiming under its own
    # `action_type` so a CRM being down cannot hold up the webhook or the other
    # way round. The retract consumer follows the re-anonymiser's `crm.retract`
    # and reads the links delivery writes, so it sits after delivery for the
    # same single-pass reason: a withdrawal in the same batch as the push it
    # undoes finds the link already there.
    CrmDeliveryConsumer,
    CrmRetractConsumer,
    # Reads the same `handoff.lead` the deliveries do and writes only to the
    # log — no outbound call, nothing to claim. After them, because a lead
    # reaching the client's CRM is time-sensitive on a floor and a draft is read
    # after close; before the erasure, so a withdrawal and a draft arriving in
    # one batch end with the draft redacted rather than written afterwards.
    SdrConsumer,
    # After the retraction, and that order is a correctness condition rather
    # than an optimisation: the erasure refuses to run until every crm_link is
    # retracted, so ahead of it here it would spend a retry backoff on every
    # request rather than completing in the same pass.
    ErasureConsumer,
    # Watches the spatial stream for the passage of event time. Ordered after
    # the graph writer so a window it summarises has already been written to the
    # graph, and away from the lead chain, which it shares nothing with.
    # Reads the detection stream and the tracker's own zone transitions, and
    # writes `spatial.group` back. Before the graph writer would be wrong: the
    # writer links members to `Person` nodes, and a group announced ahead of the
    # people in it would find nobody to link. After the tracker for the same
    # reason — the zone enters it reads are the tracker's output.
    GroupingConsumer,
    # Last, and it reads the registry above to find out who must have caught up
    # before it may empty anything. Ordered here so that list is complete by the
    # time it runs; the check itself is by name at handle time, not by position.
    RetentionConsumer,
    # Beside grouping and for the same reason: the tracker is the <500ms path
    # the Phase 1 acceptance is measured on, and a gaze bug must not be able to
    # stop dwell being measured. It reads the detection stream and writes
    # `spatial.gaze`. Before the graph writer, which links the look to a
    # `Person` and a `Zone` and would find neither if it ran first.
    GazeConsumer,
    InsightsConsumer,
    # Reads the same detection stream on the same fixed windows as the insight
    # agent, and shares nothing else with it. Neither writes what the other
    # reads, so the order between them is arbitrary; they sit together because
    # they are the two consumers that measure a window rather than an event.
    DriftConsumer,
    # Off the ledger's critical path — the ledger reads the log. This keeps the
    # graph's queryable copy of "which deals came from this visitor" current.
    OutcomesConsumer,
    RulesConsumer,
    DispatchConsumer,
    BroadcastConsumer,
]


#: The consumer instances this process is actually running, by name.
#:
#: Registered by the FastAPI lifespan, so a route can reach the *live* instance
#: rather than building a fresh one. That distinction is the whole reason this
#: exists: the tracker's answers depend on state it has accumulated from the
#: stream, so draining a brand-new instance would not advance the real consumer
#: — it would run a different one that happens to share a cursor, and emit
#: whatever an empty state implies.
_running: dict[str, Consumer] = {}


def register_running(consumer: Consumer) -> None:
    _running[consumer.name] = consumer


def clear_running() -> None:
    _running.clear()


def running(name: str) -> Consumer | None:
    """The live instance, or None when consumers are not running here.

    None is a real answer, not a failure: `consumers_enabled` is off under test,
    and a deployment could run the API and the consumers as separate processes.
    Callers say so rather than silently building a substitute.
    """
    return _running.get(name)


def build_all() -> list[Consumer]:
    return [cls() for cls in CONSUMER_CLASSES]


def build(name: str) -> Consumer:
    for cls in CONSUMER_CLASSES:
        if cls.name == name:
            return cls()
    raise SystemExit(
        f"unknown consumer {name!r}; known: {[c.name for c in CONSUMER_CLASSES]}"
    )


async def _main(name: str | None, once: bool) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    )
    # Consumers touch the graph, and the driver is normally opened by the API's
    # lifespan — which isn't running here.
    await connect()
    try:
        consumers = [build(name)] if name else build_all()
        if once:
            for c in consumers:
                consumed = await c.run_once()
                print(f"{c.name}: consumed {consumed}")
        else:
            await asyncio.gather(*(c.run_forever() for c in consumers))
    except asyncio.CancelledError:
        pass
    finally:
        await disconnect()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="run a realmspace bus consumer")
    parser.add_argument("consumer", nargs="?", help="consumer name; omit to run all")
    parser.add_argument("--once", action="store_true", help="one pass, then exit")
    parser.add_argument("--list", action="store_true", help="list consumers and exit")
    args = parser.parse_args()

    if args.list:
        for cls in CONSUMER_CLASSES:
            print(f"{cls.name:14} handles: {', '.join(cls.handles) or 'everything'}")
        return 0

    try:
        return asyncio.run(_main(args.consumer, args.once))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
