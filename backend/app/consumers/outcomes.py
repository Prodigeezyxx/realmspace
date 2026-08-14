"""
The outcomes consumer — writes what a lead turned into into the graph.

  outcome.recorded → (:Outcome), and (Contact)-[:RESULTED_IN]->(:Outcome)

## Why this is so small, and why the ledger does not use it

Everything this writes is a queryable copy. The attribution ledger is built from
the **log** — `app/attribution/ledger.py` — because an auditor asks what was
known and when, and the graph is current state: a withdrawal redacts a Contact,
so a graph-built ledger could not show that the touch ever happened.

What the graph is for is the other question, the one the log answers badly:
"which deals came from this person, and which zones did they walk". That is a
traversal, and it is what `data-model.md` exists for.

So this consumer is not on the ledger's critical path. If it fails, the ledger is
still correct and still exportable, and the dead letter says the graph copy is
behind — which is the right blast radius for a convenience index.
"""

from __future__ import annotations

import logging

from app.config import get_settings
from app.consumers.base import Consumer
from app.graph import repository as graph_repo
from app.graph.driver import get_driver
from app.models import EventLog

log = logging.getLogger(__name__)

RECORDED = "outcome.recorded"


class OutcomesConsumer(Consumer):
    name = "outcomes"
    handles = (RECORDED,)

    #: A pure function of the event — it reads nothing but the payload and
    #: MERGEs on a stable id — so a human retrying one parked event in isolation
    #: gets exactly what the original attempt would have produced. Same grounds
    #: as the graph writer.
    retryable = True

    async def handle(self, event: EventLog) -> None:
        payload = event.payload
        outcome_id = payload.get("outcome_id")
        dedupe_key = payload.get("dedupe_key")

        if not outcome_id or not dedupe_key:
            raise ValueError(
                f"outcome.recorded seq={event.seq} needs outcome_id and "
                f"dedupe_key; got {sorted(payload)}"
            )

        settings = get_settings()
        async with get_driver().session(database=settings.neo4j_database) as gs:
            await graph_repo.upsert_outcome(
                gs,
                tenant_id=event.tenant_id,
                outcome_id=outcome_id,
                dedupe_key=dedupe_key,
                stage=payload.get("stage", "open"),
                value=payload.get("value"),
                currency=payload.get("currency", "USD"),
                closed_at=payload.get("closed_at"),
                source=payload.get("source", "operator"),
                external_ref=payload.get("external_ref"),
            )
