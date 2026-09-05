"""
Who agreed — the consumer between a kiosk's yes and the identified funnel.

    consent.given (kiosk)  →  consent.captured (this)  →  identity, CRM, ledger

`consent.captured` has had every reader since Phase 4 — `consumers/identity.py`
draws the `IDENTIFIED_AS` edge, attribution builds the lead, five adapters
deliver it, the SDR drafts from it, the ledger audits it — and no producer that
a visitor could reach. `roadmap.md`'s line was "the surfaces themselves
(badge/QR/kiosk hardware) are not built". A phone pointed at a QR code is that
surface, and it has a camera pointed at nothing: it can attest that the person
in front of it agreed, and not which track on the floor they are.

Same split as `perception.detection` → `consumers/tracker.py` → `spatial.*`, and
the same one `consumers/touch.py` uses for a tablet: the producer emits the raw
fact, and the consumer, which is where the state is, derives the rest.

## Where this deliberately differs from `consumers/touch.py`

That file refuses a tap it cannot attribute, and this one does not refuse a
consent it cannot attribute. The two look like the same problem and are not:

  - a tablet's tap is a claim about **somebody else** — who pressed it — and the
    device cannot observe that, so attributing it by proximity or by "the last
    visitor seen" would be inventing the observation. Refusing costs nothing
    that was being measured: the tap is on the log either way and still counts
    as an interaction.
  - a consent is a claim the visitor makes about **themselves**, by typing their
    own address. The spatial path is the second half, not the first. Refusing
    the capture for want of it would discard the lead — a person who gave us
    their details and heard nothing back — which is a worse answer than a lead
    with no path.

So: the consent is always captured, and the `anon_id` is attached only when the
floor can say who. A busy stand still refuses most *attributions*, and the
report shows that the way it shows an unattributed tap — the consent counted,
the path stated as unobserved rather than invented.

Stage 1 emits only the attributed case; an unattributed consent is logged and
left for the operator to see. The unlinked path — a Contact with no `Person` —
is the follow-on, and until it lands this file is deliberately loud about what
it dropped rather than silent about it.

## Occupancy is read from the log, not the graph

`DWELLED_IN` is written from `spatial.dwell`, which the tracker emits when
somebody *leaves* a zone; at the moment somebody is filling in a form at a
plinth they are still standing at it. The graph would answer "nobody" live and
"one person" on a replay of the same log. So this reads the tracker's
`spatial.zone_enter` / `zone_exit` by event time, through
`consumers/occupancy.occupants_at` — one definition of who is in a room, shared
with the touch and crowding consumers, because two of them could disagree about
whether the room is empty and neither answer would be obviously wrong.

## Why it waits for the tracker

The `zone_enter` that puts somebody at the plinth is appended when the tracker
*processes* the detection, which can be after the consent it explains has landed
— a form takes longer to fill in than a poll interval, but a busy log does not
promise that. Resolving immediately would read an empty zone and attribute
nothing, permanently, since the cursor would have moved on. So a consent whose
`seq` the tracker has not passed raises, and the base class's retry and backoff
do the waiting: `consumers/touch.py`'s shape, and `consumers/erasure.py`'s
before it. The answer is not wrong yet, it is not available yet.
"""

from __future__ import annotations

import logging

from app import db, repository
from app.config import get_settings
from app.consumers.base import Consumer
from app.consumers.ids import derive_event_id
from app.consumers.occupancy import occupants_at
from app.graph import repository as graph_repo
from app.graph.driver import get_driver
from app.models import EventLog
from app.schemas import EventIn

log = logging.getLogger(__name__)

GIVEN = "consent.given"
CAPTURED = "consent.captured"

#: The consumer whose output this reads. Named rather than inferred, because the
#: wait below is a correctness condition and a renamed tracker should break
#: loudly here rather than silently stop waiting.
TRACKER = "tracker"


class ConsentNotResolvableYet(RuntimeError):
    """The tracker has not caught up. Retry, do not drop.

    Its own type so the wait is legible on `/ops` as waiting rather than as a
    failure — the difference between "this will work shortly" and "this needs a
    human".
    """


class KioskConsentConsumer(Consumer):
    name = "kiosk_consent"
    handles = (GIVEN,)

    #: Retryable, and it has to be: the ordinary case is a consent that arrives
    #: before the tracker has explained it. A pure function of the log — the
    #: emitted id derives from the consent id, and occupancy is recomputed
    #: rather than accumulated — so re-running one parked event gives what the
    #: first attempt would have.
    retryable = True

    async def handle(self, event: EventLog) -> None:
        payload = event.payload
        consent_id = payload.get("consent_id")
        if not consent_id:
            # Not raised: the kiosk cannot produce this — `ConsentGivenIn`
            # requires it — so a row like it came from somewhere that retrying
            # will not fix.
            log.warning("kiosk_consent: seq=%s has no consent_id", event.seq)
            return

        surface_id = payload.get("surface_id")
        zone_id = None
        if surface_id:
            settings = get_settings()
            async with get_driver().session(database=settings.neo4j_database) as gs:
                surface = await graph_repo.surface(
                    gs,
                    tenant_id=event.tenant_id,
                    session_id=event.session_id,
                    surface_id=surface_id,
                )
            if surface is None:
                # The operator removed the kiosk. The consent still happened and
                # is still on the log; what is gone is the zone that would have
                # said who gave it.
                log.info(
                    "kiosk_consent: %s is not a configured surface on %s; the "
                    "consent stands and is unattributed",
                    surface_id,
                    event.session_id,
                )
            else:
                zone_id = surface.get("zone_id")

        async with db.SessionLocal() as session:
            await db.scope_to_tenant(session, event.tenant_id)

            anon_id = None
            if zone_id:
                tracker_at = await repository.get_cursor(
                    session, consumer=TRACKER, tenant_id=event.tenant_id
                )
                if tracker_at < event.seq:
                    raise ConsentNotResolvableYet(
                        f"tracker is at seq {tracker_at} and this consent is seq "
                        f"{event.seq}; who was in {zone_id} is not knowable yet"
                    )

                occupants = await occupants_at(
                    session,
                    tenant_id=event.tenant_id,
                    session_id=event.session_id,
                    zone_id=zone_id,
                    at=event.occurred_at,
                )
                if len(occupants) == 1:
                    anon_id = next(iter(occupants))
                else:
                    # Two people at the plinth, or none — a visitor who consented
                    # on their phone from across the stand, or a staff member
                    # demonstrating it. The consent is real; the path is not
                    # observed.
                    log.info(
                        "kiosk_consent: %s people in %s when consent %s was "
                        "given; the consent stands and is unattributed",
                        len(occupants),
                        zone_id,
                        consent_id,
                    )
            else:
                log.info(
                    "kiosk_consent: consent %s came from a surface with no zone, "
                    "so it is unattributed — set a zone on the kiosk to attach "
                    "the visitor's path",
                    consent_id,
                )

            if anon_id is None:
                # Stage 1 stops here: `consent.captured` pins an `anon_id` and
                # `consumers/identity.py` raises on a track that does not exist,
                # so emitting one without it would park a correct consent in the
                # dead-letter queue and ask an operator to un-refuse it. The
                # consent is on the log, in full, and the unlinked path is the
                # follow-on this file's docstring describes.
                return

            await repository.append_event(
                session,
                EventIn(
                    # **The same id `routers/consent.py` derives** for a capture
                    # of this consent — one conversation is one
                    # `consent.captured`, whether it reached us from a kiosk or
                    # from an operator's own POST, and a replay re-derives it
                    # rather than doubling the identification.
                    event_id=derive_event_id("consent", event.tenant_id, consent_id),
                    tenant_id=event.tenant_id,
                    session_id=event.session_id,
                    type=CAPTURED,
                    payload={
                        "consent_id": consent_id,
                        "anon_id": anon_id,
                        "tier": payload.get("tier"),
                        "basis": payload.get("basis"),
                        "copy_version": payload.get("copy_version"),
                        "captured_by": payload.get("captured_by"),
                        "source": payload.get("source") or "kiosk",
                        "captured_at": payload.get("at")
                        or event.occurred_at.isoformat(),
                        "expires_at": None,
                        # How we know which visitor, in the vocabulary
                        # `consumers/touch.py` established for the same claim.
                        "attributed_by": "zone_occupancy",
                        "zone_id": zone_id,
                        **(
                            {"contact": payload["contact"]}
                            if payload.get("contact")
                            else {}
                        ),
                    },
                    occurred_at=event.occurred_at,
                ),
            )
            await session.commit()
