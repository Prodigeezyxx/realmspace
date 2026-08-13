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

from app.consumers.base import Consumer
from app.consumers.broadcast import BroadcastConsumer
from app.consumers.dispatch import DispatchConsumer
from app.consumers.graph_writer import GraphWriterConsumer
from app.consumers.identity import IdentityConsumer
from app.consumers.reanonymise import ReAnonymiseConsumer
from app.consumers.rules import RulesConsumer
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
    GraphWriterConsumer,
    IdentityConsumer,
    ReAnonymiseConsumer,
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
