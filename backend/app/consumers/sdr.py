"""
The contextual SDR — a follow-up that names where somebody actually stood.

`roadmap.md` Phase 5: *"Contextual SDR draft agent (path-aware follow-up emails,
consent ≥ T2)"*, and the phase acceptance: *"post-session, a consented lead
receives a draft follow-up referencing the exact zones/surfaces they engaged"*.

  handoff.lead (stage `final`) → consent ≥ T2 → followup.drafted

## It drafts, and it does not send

The roadmap calls it a *draft* agent and that is the whole design, not a
staging post. Two reasons, and the second is the real one.

There is no email provider — the same absence open decision 2 describes for the
model. And an unreviewed, model-written email to a real person who consented to
be contacted is not something to put on a cron. The draft goes on the bus and to
a review list; a human sends it, or does not. If sending is ever built it wants
its own consent question, its own suppression list and its own audit, and none of
those are this consumer.

## Why `final`, and only `final`

`consumers/attribution.py` emits a handoff twice: at the badge scan with the path
so far, and at `session.ended` with the complete one. A draft built from the
first would name the entry zone and nothing else, because the visitor has not
finished walking yet — a follow-up whose one specific detail is "you came in
through the door" is worse than a generic one. So this waits for the complete
path, which is also when the acceptance says it should happen ("post-session").

The anonymous stage drafts nothing, for the obvious reason.

## The consent gate is the shared one

`app/consent_tier.py`, the same gate `crm_delivery` uses.
`consent-and-identity.md` §2 puts "personalised follow-up (SDR)" in T2's own
column, so this is that clause's literal subject. T1 is "take my details"; a
visitor who chose it is not somebody to write to.

## What the draft may reference, and what it cannot

Only the `spatial_intent` the handoff already carries — zones, dwell, surfaces.
Those are measurements the visitor's consent covers. The prompt says so in
words a model has to work to disobey, and the emitted event carries `groundedIn`:
the exact set the draft was allowed to use, so a reviewer can check a sentence
against it rather than trusting it.

Nothing here reads a raw event, a frame or a bounding box — `privacy.md`: "AI
reasoning calls receive only structured event summaries".

## A withdrawn contact drafts nothing, checked against the graph rather than the
## event

The handoff on the log is a fact about a moment that has passed, and it keeps the
name forever. Whether that person still consents is current state, and current
state is the graph — the `IDENTIFIED_AS` edge the re-anonymiser deletes on
withdrawal and the Contact an erasure removes outright. So this re-reads it,
exactly as `consumers/attribution.py` does before building a handoff at all.

Reading only the event would draft for somebody who withdrew an hour ago, and on
a replay it would draft for somebody erased last month — from a payload that has
had their name taken out of it, producing a letter addressed to nobody about a
person who asked to be forgotten.

A draft already on the log is PII like any other: `app/erasure.py` redacts
`followup.drafted`, and redacts the **body** as well as the contact, because
redacting the envelope and leaving the letter is not redaction.
"""

from __future__ import annotations

import datetime as dt
import logging
import re

from app import consent_tier, db, llm, repository
from app.config import get_settings
from app.consumers.base import Consumer
from app.consumers.ids import derive_event_id
from app.cost import meter
from app.llm import budget, prompts
from app.graph import repository as graph_repo
from app.graph.driver import get_driver
from app.llm.base import LlmError
from app.models import EventLog
from app.schemas import EventIn

log = logging.getLogger(__name__)

HANDOFF = "handoff.lead"
DRAFTED = "followup.drafted"

#: The complete path. See the module docstring.
FINAL = "final"


class SdrConsumer(Consumer):
    name = "sdr"
    handles = (HANDOFF,)

    #: Retryable. The emitted id is derived from the contact, the draft is
    #: rebuilt from the handoff payload rather than from accumulated state, and
    #: nothing leaves the building — so re-running one parked event produces what
    #: the original attempt would have.
    retryable = True

    async def handle(self, event: EventLog) -> None:
        payload = event.payload

        if payload.get("stage") != FINAL:
            return

        contact = payload.get("contact") or {}
        if not contact.get("id"):
            # An anonymous handoff. Nobody to write to.
            return

        tier = consent_tier.tier_of(payload)
        if not consent_tier.permits(tier):
            log.info(
                "sdr: %s — %s",
                event.event_id,
                consent_tier.refusal(tier, act="a follow-up draft"),
            )
            return

        anon_id = payload.get("anon_id")
        if anon_id and not await self._still_identified(event, anon_id):
            # Withdrawn since the handoff was built, or erased entirely. The
            # handoff keeps their name forever; whether they still consent is
            # current state, and current state is the graph.
            log.info(
                "sdr: %s is no longer identified; no draft", contact.get("id")
            )
            return

        intent = payload.get("spatial_intent") or {}
        activation = (payload.get("activation") or {}).get("name") or ""

        subject, body = prompts.deterministic_draft(
            contact=contact, intent=intent, activation=activation
        )
        basis = "deterministic"

        async with db.SessionLocal() as session:
            await db.scope_to_tenant(session, event.tenant_id)
            integration = await repository.get_integration_of_kind(
                session, tenant_id=event.tenant_id, kind=llm.KIND, active_only=True
            )
            provider = llm.provider_for(integration)
            tokens = 0

            may_call, refusal = await budget.within_budget(
                session, tenant_id=event.tenant_id, spender="sdr"
            )
            if not may_call:
                log.info("sdr: %s", refusal)

            if may_call and provider.capabilities().get("reasons"):
                try:
                    written = await provider.complete(
                        prompts.sdr_prompt(
                            contact=contact, intent=intent, activation=activation
                        ),
                        max_tokens=400,
                    )
                    tokens = written.total_tokens
                    drafted = _split(written.text)
                    if drafted:
                        subject, body = drafted
                        basis = provider.provider
                except LlmError as exc:
                    # The composed draft stands. A provider outage should cost an
                    # operator some polish, not the follow-up — the same floor
                    # `catalogue.Entry.phrase` gives Ask.
                    log.warning("sdr: %s could not draft: %s", provider.provider, exc)

            if tokens:
                # **This consumer has spent tokens since the day it was written
                # and metered none of them.** Nothing showed on the cost tile,
                # because with no provider `tokens` was always zero and the
                # absence looked like nothing to record. A real provider bills
                # for drafts, and `roi-framework.md`'s unit economics cannot be
                # short one spender.
                #
                # Keyed on the contact, like the draft itself: one draft per
                # contact per session, so one spend, and a replay derives the
                # same id rather than billing twice for the same follow-up.
                await meter(
                    session,
                    tenant_id=event.tenant_id,
                    session_id=event.session_id,
                    kind="llm_tokens",
                    amount=float(tokens),
                    unit="tokens",
                    occurred_at=event.occurred_at,
                    cause=("sdr", contact["id"]),
                    detail={"provider": basis, "spender": "sdr"},
                )

            await repository.append_event(
                session,
                EventIn(
                    # One draft per contact per session. A replay re-derives the
                    # same id and the log dedupes it, so a rewound cursor cannot
                    # fill a reviewer's queue with copies of one follow-up.
                    event_id=derive_event_id(
                        "followup", event.tenant_id, event.session_id, contact["id"]
                    ),
                    tenant_id=event.tenant_id,
                    session_id=event.session_id,
                    type=DRAFTED,
                    payload={
                        "contact": {
                            "id": contact["id"],
                            "email": contact.get("email"),
                            "name": contact.get("name"),
                        },
                        "dedupe_key": payload.get("dedupe_key"),
                        "anon_id": payload.get("anon_id"),
                        "subject": subject,
                        "body": body,
                        "basis": basis,
                        "consent": {"tier": tier},
                        # What the draft was allowed to reference. A reviewer
                        # checks a sentence against this rather than trusting it,
                        # which is the only way to catch a model that invented a
                        # conversation.
                        "grounded_in": {
                            "zones_visited": intent.get("zones_visited") or [],
                            "top_dwell_zone": intent.get("top_dwell_zone"),
                            "dwell_seconds_total": intent.get("dwell_seconds_total"),
                            "surfaces_engaged": intent.get("surfaces_engaged") or [],
                        },
                        # Stated on the row, not implied by its absence elsewhere.
                        "sent": False,
                        "drafted_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                    },
                    occurred_at=event.occurred_at,
                ),
            )
            await session.commit()


    async def _still_identified(self, event: EventLog, anon_id: str) -> bool:
        """Is this person still linked to a live, non-withdrawn consent?"""
        settings = get_settings()
        async with get_driver().session(database=settings.neo4j_database) as gs:
            contact = await graph_repo.contact_for_anon(
                gs,
                tenant_id=event.tenant_id,
                session_id=event.session_id,
                anon_id=anon_id,
            )
        return contact is not None


#: A subject line, however a chat model chose to decorate it. Markdown emphasis
#: around the label is the common one — DeepSeek writes `**Subject:** …` some of
#: the time and a bare `Subject: …` the rest — and a leading `#` shows up too.
#:
#: This is the same lesson as the JSON fence in `routers/ask.py`: a model asked
#: for a shape gives that shape *with the formatting a chat model uses*, and a
#: parser that accepts exactly one rendering rejects a correct answer. The
#: failure is the quiet kind — `None` keeps the composed draft and `basis` still
#: reads `deterministic`, so the feature looks switched off rather than broken.
_SUBJECT_LINE = re.compile(
    r"^\s*[#*_\s]*subject[*_\s]*[:\-–][*_\s]*", re.IGNORECASE
)

#: Trailing emphasis left over once the label is gone: `**Subject:** Your visit**`
#: is rare, but `**Your visit**` after a bolded label is not.
_EMPHASIS = re.compile(r"^[*_]+|[*_]+$")


def _split(text: str) -> tuple[str, str] | None:
    """A model's draft into `(subject, body)`.

    The prompt asks for a subject line then the body. A reply that does not offer
    one is not forced into the shape — `None` keeps the composed draft, which is
    a worse email than a good model's and a much better one than a mangled reply.

    What *is* accepted is any reasonable rendering of a subject line, because
    refusing a bolded one throws away a perfectly good draft. See `_SUBJECT_LINE`.
    """
    text = (text or "").strip()
    if not text:
        return None

    first, _, rest = text.partition("\n")
    match = _SUBJECT_LINE.match(first)
    if not match:
        # No subject line offered. Rather than promoting the first sentence of
        # the body into one, keep ours.
        return None

    subject = _EMPHASIS.sub("", first[match.end():].strip()).strip()
    body = rest.strip()
    return (subject, body) if subject and body else None
