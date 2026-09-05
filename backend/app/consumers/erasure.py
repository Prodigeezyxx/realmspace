"""
The erasure job — `consent-and-identity.md` §5's second bullet, and the only
thing in this system that writes to the event log twice.

  erasure.requested → wait for the retraction, redact the log, delete the
                      Contact → erasure.completed

## Erasure is withdrawal, plus the part withdrawal cannot reach

`POST /v1/erasure` appends a `consent.withdrawn` alongside the request, so the
whole existing path runs first: `reanonymise.py` drops the `IDENTIFIED_AS` edge
and redacts the graph Contact, and `crm_retract.py` removes the record from every
CRM that received it. None of that is rebuilt here, and reusing it is the point —
an erasure that took its own route to the CRMs would be a second implementation
of the thing a person's rights depend on.

What is left after all that is the log, and the log is where the email actually
is: inside `consent.captured`'s contact object and inside every `handoff.lead`
built from it. Withdrawal cannot touch those because the log is append-only. This
can, and `app/erasure.py` says exactly what it removes.

## It refuses to run before the retraction has happened, and that ordering is the
## whole reason this is a consumer rather than an endpoint

Two checks, both of which raise and let `base.Consumer` retry with backoff:

  1. **the graph Contact still has its PII or a live link** — the re-anonymiser
     has not processed yet. Erasing first would delete the node the re-anonymiser
     reads to build `crm.retract`, so no retraction would ever be emitted and the
     copy in the client's CRM would stay there. An erasure that reported success
     and left the data where it mattered most is the worst failure available
     here, and it is silent.
  2. **a `crm_link` is still outstanding** — the retraction was emitted and has
     not landed. Same reasoning one step later.

If either keeps failing, the request lands on `/ops` as a parked event with the
reason on it, which is correct: somebody has to know that a person asked to be
erased and it has not finished.

## Retryable, unlike the delivery consumers

Every write is a MERGE, a DELETE or an idempotent payload rewrite, and the
`erasure.completed` id is derived — so re-running one parked request in isolation
produces what the original attempt would have. It is also the consumer where a
stuck event is least acceptable, for the reason `reanonymise.py` gives about
itself: the parked event is a person's rights, and it should be one click.

## The receipt names ids and counts, and nothing else

`erasure.completed` is on the same append-only log this just rewrote, so a
receipt that quoted what it removed would put the PII straight back. It carries
the contact id, the counts, and when — enough to prove the work happened, and
nothing that would undo it.
"""

from __future__ import annotations

import datetime as dt
import logging

from app import db, erasure, repository
from app.config import get_settings
from app.consumers.base import Consumer
from app.consumers.ids import derive_event_id
from app.graph import repository as graph_repo
from app.graph.driver import get_driver
from app.models import EventLog
from app.schemas import EventIn

log = logging.getLogger(__name__)

REQUESTED = "erasure.requested"
COMPLETED = "erasure.completed"

#: Types read to resolve the subject and to redact. Everything else on the log
#: about this person is ids and timestamps and stays — `app/erasure.py` says why.
READ_TYPES = tuple(dict.fromkeys(erasure.LINKING_TYPES + erasure.PII_TYPES))

#: One page of the log per read. The scan is tenant-wide rather than
#: session-scoped because a Contact id derives from the email and so spans
#: activations — which is the property that makes one visitor at two events one
#: person, and the same property that means an erasure has to cover both.
PAGE = 1000


class ErasureConsumer(Consumer):
    name = "erasure"
    handles = (REQUESTED,)

    #: See the module docstring — the same exception `crm_retract` is.
    retryable = True

    async def handle(self, event: EventLog) -> None:
        payload = event.payload
        seed = erasure.Subject()
        if payload.get("contact_id"):
            seed.contact_ids.add(payload["contact_id"])
        if payload.get("consent_id"):
            seed.consent_ids.add(payload["consent_id"])
        if payload.get("anon_id"):
            seed.tracks.add((event.session_id, payload["anon_id"]))

        if not seed:
            raise ValueError(
                f"erasure.requested seq={event.seq} names nobody — it needs at "
                "least one of contact_id, consent_id, anon_id"
            )

        async with db.SessionLocal() as session:
            await db.scope_to_tenant(session, event.tenant_id)
            rows = await self._read_log(session, tenant_id=event.tenant_id)

        subject = erasure.resolve(
            [(row.type, row.session_id, row.payload) for row in rows], seed
        )

        await self._refuse_until_retracted(event, subject)

        redacted = await self._redact(event, subject, rows)
        erased = await self._erase(event, subject)

        await self._complete(event, subject, redacted=redacted, erased=erased)

    # ── the two refusals ──────────────────────────────────────────────────────

    async def _refuse_until_retracted(
        self, event: EventLog, subject: erasure.Subject
    ) -> None:
        """Both checks from the module docstring. Raising is the whole point."""
        contact_ids = sorted(subject.contact_ids)
        if not contact_ids:
            # Nobody was ever identified — a consent captured and never acted on,
            # or a track that never consented. There is no CRM copy and no
            # Contact, so there is nothing to wait for.
            return

        settings = get_settings()
        async with get_driver().session(database=settings.neo4j_database) as gs:
            pending = await graph_repo.contacts_awaiting_withdrawal(
                gs, tenant_id=event.tenant_id, contact_ids=contact_ids
            )
        if pending:
            raise RuntimeError(
                f"erasure for {pending} cannot run yet: the withdrawal has not "
                "been carried out, and erasing now would delete the record the "
                "re-anonymiser reads to retract them from the client's CRM"
            )

        async with db.SessionLocal() as session:
            await db.scope_to_tenant(session, event.tenant_id)
            outstanding = [
                f"{link.provider}:{link.external_id}"
                for contact_id in contact_ids
                for link in await repository.links_for_contact(
                    session, tenant_id=event.tenant_id, contact_id=contact_id
                )
            ]
        if outstanding:
            raise RuntimeError(
                f"erasure cannot run yet: {outstanding} have not been retracted "
                "from the destinations that received them"
            )

    # ── the work ──────────────────────────────────────────────────────────────

    async def _read_log(self, session, *, tenant_id: str) -> list[EventLog]:
        """Every event of the types that can name somebody, tenant-wide."""
        rows: list[EventLog] = []
        for type in READ_TYPES:
            since = 0
            while True:
                page = await repository.read_events(
                    session, tenant_id=tenant_id, since_seq=since, limit=PAGE, type=type
                )
                rows.extend(page)
                if len(page) < PAGE:
                    break
                since = page[-1].seq
        return rows

    async def _redact(
        self, event: EventLog, subject: erasure.Subject, rows: list[EventLog]
    ) -> int:
        """Rewrite the payloads that name this person. Returns how many."""
        at = dt.datetime.now(dt.timezone.utc)
        count = 0
        discriminator = _discriminator(event, subject)

        async with db.SessionLocal() as session:
            await db.scope_to_tenant(session, event.tenant_id)
            for row in rows:
                if not subject.covers(
                    type=row.type, session_id=row.session_id, payload=row.payload
                ):
                    continue
                cleaned = erasure.redact(
                    row.type, row.payload, discriminator=discriminator
                )
                if cleaned is None:
                    # Already redacted, or an event of a type that never carried
                    # a name. Both leave `redacted_at` unset, which keeps "what
                    # did this erasure touch" an honest answer.
                    continue
                await repository.redact_event(
                    session, seq=row.seq, payload=cleaned, at=at
                )
                count += 1
            await session.commit()
        return count

    async def _erase(self, event: EventLog, subject: erasure.Subject) -> int:
        settings = get_settings()
        erased = 0
        async with get_driver().session(database=settings.neo4j_database) as gs:
            for contact_id in sorted(subject.contact_ids):
                erased += await graph_repo.erase_contact(
                    gs, tenant_id=event.tenant_id, contact_id=contact_id
                )
        return erased

    async def _complete(
        self,
        event: EventLog,
        subject: erasure.Subject,
        *,
        redacted: int,
        erased: int,
    ) -> None:
        async with db.SessionLocal() as session:
            await db.scope_to_tenant(session, event.tenant_id)
            await repository.append_event(
                session,
                EventIn(
                    # Derived from the request, so a replay reports the same
                    # completion rather than appending a second one.
                    event_id=derive_event_id(
                        "erasure_completed", event.tenant_id, str(event.event_id)
                    ),
                    tenant_id=event.tenant_id,
                    session_id=event.session_id,
                    type=COMPLETED,
                    payload={
                        "request_event_id": str(event.event_id),
                        "contact_ids": sorted(subject.contact_ids),
                        "events_redacted": redacted,
                        "contacts_erased": erased,
                        "completed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                    },
                    occurred_at=event.occurred_at,
                ),
            )
            await session.commit()


def _discriminator(event: EventLog, subject: erasure.Subject) -> str:
    """One stable, anonymous value per erasure, for the rewritten dedupe keys.

    Without it every erased person in a tenant ends up under the single key
    `tenant:[withdrawn]`, and `attribution.ledger.build` groups on that key — so
    two people erased from one activation would come back as one ledger row with
    one of their contact ids and both of their outcomes. `redact_dedupe_key` has
    the full argument.

    The contact id first, because it is already on the log — `erasure.CONTACT_PII`
    keeps it deliberately — and it names nobody on its own. Sorted, so a subject
    that resolved to more than one contact still picks the same one every time.
    The request's own event id stands in for a subject that never had a contact,
    and it is derived rather than random (`routers/erasure.py`), so a replay
    rewrites the same rows to the same values instead of a second set.
    """
    return sorted(subject.contact_ids)[0] if subject.contact_ids else str(event.event_id)
