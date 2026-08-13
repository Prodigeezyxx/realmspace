"""
The attribution consumer — a consented visitor becomes a lead somebody can act on.

`event-bus-spec.md` §4: *"build LeadHandoff, apply model+window"*. The shape is
pinned in `integrations.md` §2 and most of this is assembly; the parts worth
reading are the two triggers and what happens to a withdrawn contact.

  identity.resolved → handoff.lead (stage `identified`, the path so far)
  session.ended     → handoff.lead (stage `final`, the complete path) × contacts

## Why a lead is emitted twice

A trade-show lead is worth most while the visitor is still on the floor, and a
handoff sent at that moment carries an incomplete path — they have not finished
walking it. Sending only at `session.ended` gives a complete lead that arrives
after everyone has gone home; sending only at capture gives a fast lead that
says almost nothing.

So both, and the pairing that makes it safe:

- **the same `dedupe_key`** on each (`tenant:email|anon_id`, per
  `integrations.md` §2), because every adapter's upsert is keyed on it — the
  final handoff *updates* the lead the early one created rather than adding a
  second;
- **different derived `event_id`s**, keyed on the stage as well as the contact,
  because the bus dedupes on `event_id` and an id derived from the contact alone
  would make the second handoff vanish silently.

Get that backwards in either direction and the failure is quiet: identical ids
lose the complete path, identical-but-random ids duplicate the lead in the
client's CRM.

## A withdrawn contact produces nothing

Both triggers read the live `IDENTIFIED_AS` edge — `contact_for_anon` for one
person, `contacts_in_session` for the fan-out — and the re-anonymiser deletes
that edge on withdrawal. So somebody who consented and then changed their mind
is simply absent, including on a **replay**: re-reading their old
`identity.resolved` builds no handoff, for the same reason re-reading their
capture re-identifies nobody.

`crm.retract` handles anything already pushed. This consumer's job is only to
stop adding to it.

## Anonymous handoffs are deferred

`integrations.md` §2 allows a handoff with no `contact`, carrying spatial_intent
for aggregate ROI. It needs a different trigger — every person, not every
contact — and a different consent story, so it is not built here rather than
half-built. Noted in the roadmap.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from app import db, repository
from app.attribution import score as scoring
from app.attribution import spatial_intent
from app.config import get_settings
from app.consumers.base import Consumer
from app.consumers.ids import derive_event_id
from app.graph import repository as graph_repo
from app.graph.driver import get_driver
from app.models import EventLog
from app.schemas import EventIn

log = logging.getLogger(__name__)

RESOLVED = "identity.resolved"
SESSION_ENDED = "session.ended"
HANDOFF = "handoff.lead"

SCHEMA = "realmspace.lead_handoff/v1"

#: The two moments a handoff is built. See the module docstring — they share a
#: dedupe_key and differ in their event id.
IDENTIFIED = "identified"
FINAL = "final"


class AttributionConsumer(Consumer):
    name = "attribution"
    handles = (RESOLVED, SESSION_ENDED)

    #: Retryable. Every read is of current graph state and the emitted ids are
    #: derived, so re-running one parked event in isolation produces what the
    #: original attempt would have — including producing *nothing* if the
    #: contact has withdrawn in the meantime, which is the correct answer rather
    #: than a stale one.
    retryable = True

    async def handle(self, event: EventLog) -> None:
        if event.type == RESOLVED:
            await self._on_identified(event)
        elif event.type == SESSION_ENDED:
            await self._on_session_ended(event)

    # ── the two triggers ──────────────────────────────────────────────────────

    async def _on_identified(self, event: EventLog) -> None:
        anon_id = event.payload.get("anon_id")
        if not anon_id:
            raise ValueError(f"identity.resolved seq={event.seq} has no anon_id")

        settings = get_settings()
        async with get_driver().session(database=settings.neo4j_database) as gs:
            contact = await graph_repo.contact_for_anon(
                gs, tenant_id=event.tenant_id, session_id=event.session_id, anon_id=anon_id
            )
            if contact is None:
                # Withdrawn between the identification and now, or on a replay of
                # an identification that has since been undone. Not an error.
                log.info(
                    "attribution: %s is no longer identified; no handoff", anon_id
                )
                return

            handoff = await self._build(
                gs, event=event, anon_id=anon_id, contact=contact, stage=IDENTIFIED
            )

        await self._emit(event, [handoff])

    async def _on_session_ended(self, event: EventLog) -> None:
        """Fan out over everyone still identified when the doors shut.

        Driven off the graph rather than by replaying the session's
        `identity.resolved` events, because the graph is where a withdrawal has
        already taken effect. Reading the log would rebuild handoffs for people
        who have since been re-anonymised.
        """
        settings = get_settings()
        handoffs: list[dict[str, Any]] = []

        async with get_driver().session(database=settings.neo4j_database) as gs:
            identified = await graph_repo.contacts_in_session(
                gs, tenant_id=event.tenant_id, session_id=event.session_id
            )
            for row in identified:
                contact = await graph_repo.contact_for_anon(
                    gs,
                    tenant_id=event.tenant_id,
                    session_id=event.session_id,
                    anon_id=row["anon_id"],
                )
                if contact is None:
                    continue
                handoffs.append(
                    await self._build(
                        gs,
                        event=event,
                        anon_id=row["anon_id"],
                        contact=contact,
                        stage=FINAL,
                        lead_count=len(identified),
                    )
                )

        await self._emit(event, handoffs)

    # ── assembly ──────────────────────────────────────────────────────────────

    async def _build(
        self,
        gs,
        *,
        event: EventLog,
        anon_id: str,
        contact: dict[str, Any],
        stage: str,
        lead_count: int | None = None,
    ) -> dict[str, Any]:
        config = (
            await graph_repo.session_config(
                gs, tenant_id=event.tenant_id, session_id=event.session_id
            )
            or {}
        )
        path = await graph_repo.spatial_intent_for(
            gs, tenant_id=event.tenant_id, session_id=event.session_id, anon_id=anon_id
        )
        zones = await graph_repo.zones_for_session(
            gs, tenant_id=event.tenant_id, session_id=event.session_id
        )
        surfaces = await graph_repo.surfaces_for_session(
            gs, tenant_id=event.tenant_id, session_id=event.session_id
        )
        consent = await graph_repo.consent_for_contact(
            gs, tenant_id=event.tenant_id, contact_id=contact["id"]
        )

        intent = spatial_intent.build(
            path["dwells"],
            path["surfaces"],
            surfaces_available=len(surfaces) or None,
            max_funnel_order=max(
                (z["funnel_order"] for z in zones if z.get("funnel_order") is not None),
                default=None,
            ),
        )
        value, basis, components = scoring.lead_score(
            intent,
            engaged_threshold_seconds=config.get("engaged_threshold_seconds") or 60.0,
        )
        intent["lead_score"] = value
        intent["lead_score_basis"] = basis
        intent["lead_score_components"] = components

        email = contact.get("email")
        return {
            "schema": SCHEMA,
            "stage": stage,
            "tenant_id": event.tenant_id,
            "activation": {
                "id": event.session_id,
                "name": config.get("campaign") or config.get("client"),
                "venue": config.get("venue"),
                "city": config.get("city"),
                "started_at": config.get("started_at"),
                "ends_at": config.get("ends_at"),
            },
            # Present only because a live consent was found above. A contact whose
            # consent has been withdrawn never reaches here, and one whose PII was
            # redacted carries nothing to send.
            "contact": {
                "id": contact["id"],
                "email": email,
                "name": contact.get("name"),
                "company": contact.get("company"),
                "title": contact.get("title"),
                "source": contact.get("source"),
            },
            "spatial_intent": intent,
            "consent": {
                "tier": consent.get("tier") if consent else None,
                "basis": consent.get("basis") if consent else None,
                "copy_version": consent.get("copy_version") if consent else None,
                "captured_at": consent.get("captured_at") if consent else None,
            },
            "roi_context": {
                "attribution_model": config.get("attribution_model") or "influenced",
                "attribution_window_days": config.get("attribution_window_days") or 90,
                "activation_cost_share": self._cost_share(config, lead_count),
            },
            "dedupe_key": f"{event.tenant_id}:{email or anon_id}",
            "anon_id": anon_id,
            "emitted_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "event_seq": event.seq,
        }

    @staticmethod
    def _cost_share(config: dict[str, Any], lead_count: int | None) -> float | None:
        """What this lead cost, or None while that is unknowable.

        The denominator is how many leads the activation produced, and that is
        not known while the doors are open — a share computed against a partial
        count changes every time somebody else scans a badge, so an early handoff
        would carry a figure that is wrong by the end of the day.

        None until the session ends, then `activation_cost / leads`. Blank rather
        than estimated is the same rule the report follows for every other figure
        it cannot yet compute.
        """
        cost = config.get("activation_cost")
        if cost is None or not lead_count:
            return None
        return round(float(cost) / lead_count, 2)

    # ── emission ──────────────────────────────────────────────────────────────

    async def _emit(self, event: EventLog, handoffs: list[dict[str, Any]]) -> None:
        if not handoffs:
            return

        async with db.SessionLocal() as session:
            await db.scope_to_tenant(session, event.tenant_id)
            for handoff in handoffs:
                await repository.append_event(
                    session,
                    EventIn(
                        # Stage is in the key. Without it the final handoff would
                        # derive the same id as the early one and the bus would
                        # swallow it as a duplicate — the complete path would
                        # never leave the log, and nothing would fail.
                        event_id=derive_event_id(
                            "handoff",
                            event.tenant_id,
                            event.session_id,
                            handoff["contact"]["id"],
                            handoff["stage"],
                        ),
                        tenant_id=event.tenant_id,
                        session_id=event.session_id,
                        type=HANDOFF,
                        payload=handoff,
                        occurred_at=event.occurred_at,
                    ),
                )
            await session.commit()
