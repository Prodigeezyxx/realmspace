"""
The two routes a consent kiosk reaches, and the only ones that take one of its
tokens.

## A router of its own, as `routers/touch.py` and `routers/share.py` are

`POST /v1/consent` already exists and already admits a device credential — a
kiosk may write, and requiring an operator would mean every plinth on the floor
carried a human's token. It is still the wrong door for a phone in a public
room, for the reason `routers/touch.py` gives in its sharpest form: that
endpoint takes `sessionId`, `anonId`, `tier` and `copyVersion` from the request,
so a credential that reached it could record a T3 consent, against any track, on
any activation, under wording nobody was shown.

So a token is not a credential. It reaches these two routes, which resolve it to
a `(tenant, session, surface)` triple and take **none of the three from the
request** — nor the tier, nor the copy version, both of which come off the
activation. What a visitor's phone can say is that the person in front of it
agreed, and when.

## What one POST is worth

One `consent.given`, idempotent on the browser's own `consentId`. No `anonId`: a
plinth has no camera, and `consumers/kiosk_consent.py` answers who was standing
there from zone occupancy. `GET /` returns the wording and the tier, which is
what the page needs to render and the whole of what a member of the public may
have — no counts, no visitors, no activation name beyond the touchpoint's own
label.

## Why the raw event is not `consent.captured`

Two reasons, and the second is the deciding one. The captured event pins an
`anon_id` (`event-bus-spec.md` §3) and this surface cannot supply it. And the
consumer that can — by waiting for the tracker's cursor to pass the consent and
then reading zone occupancy — is a consumer precisely because that wait needs a
retry and a backoff, which an HTTP handler holding a visitor's phone must not
do. Same split as `perception.detection` → `consumers/tracker.py` → `spatial.*`:
the producer emits the fact it can attest to.

## Everything unknown is a 404

Unknown, expired and revoked are one answer, with one message. `share.py` has
the argument: which of the three it was is the only thing a stranger holding a
guess could act on.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, status
from neo4j import AsyncSession as GraphSession
from sqlalchemy.ext.asyncio import AsyncSession

from app import kiosk, repository
from app.consumers.ids import derive_event_id
from app.db import get_session, scope_to_tenant
from app.graph import repository as graph_repo
from app.graph.driver import get_graph_session
from app.models import ConsentToken
from app.schemas import ConsentGivenIn, EventIn, EventOut, KioskOut

router = APIRouter(prefix="/v1/kiosk", tags=["kiosk"])

#: `consent.given` — the raw fact a capture surface can actually attest to.
#: Pinned in `event-bus-spec.md` §3, and PII: it carries `contact`.
GIVEN = "consent.given"

#: One message for every failure. See the module docstring.
GONE = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="this consent link is not valid — it may have expired or been revoked",
)

#: A kiosk whose activation has lost its wording. Its own answer rather than the
#: 404 above, because this one is the operator's to fix and the visitor should
#: not be told the link is dead when it is the copy that is missing — the same
#: distinction `/report` draws between "nobody came" and "nothing was
#: measuring".
UNCONFIGURED = HTTPException(
    status_code=status.HTTP_409_CONFLICT,
    detail=(
        "this activation has no consent wording set, so there is nothing to "
        "show or agree to — set it on the activation first"
    ),
)


async def _open(token: str, session: AsyncSession) -> ConsentToken:
    """Resolve a token and scope the connection to its tenant.

    The order is `touch._open`'s and matters for its reason: `consent_token` is
    outside RLS because the caller has no tenant until this lookup supplies one,
    so scoping is the first thing that happens after resolving, and the append
    below then runs under the same policy as any other producer's.
    """
    row = await kiosk.resolve(
        session, token=token, now=dt.datetime.now(dt.timezone.utc)
    )
    if row is None:
        raise GONE
    await scope_to_tenant(session, row.tenant_id)
    return row


async def _copy(graph: GraphSession, row: ConsentToken) -> dict:
    """The activation's consent wording, or the 409 above.

    Read per request rather than stored on the token: correcting a sentence
    should change every plinth at once, and the version a capture records must
    be the one the operator can point at afterwards.
    """
    props = await graph_repo.session_config(
        graph, tenant_id=row.tenant_id, session_id=row.session_id
    )
    if not props or not props.get("consent_copy") or not props.get(
        "consent_copy_version"
    ):
        raise UNCONFIGURED
    return props


@router.get("/{token}", response_model=KioskOut, summary="What this kiosk asks")
async def kiosk_page(
    token: str,
    session: AsyncSession = Depends(get_session),
    graph: GraphSession = Depends(get_graph_session),
) -> KioskOut:
    """The wording to display, read from the operator's own configuration.

    The label comes from the `Surface`, as the tablet's does, so renaming the
    kiosk in the wizard renames what the visitor sees without re-minting
    anything.
    """
    row = await _open(token, session)

    surface = await graph_repo.surface(
        graph,
        tenant_id=row.tenant_id,
        session_id=row.session_id,
        surface_id=row.surface_id,
    )
    if surface is None:
        # A token for a kiosk that has since been removed from the activation.
        # Same answer as a bad token, because from the visitor's side they are
        # the same thing: there is nothing here to agree to.
        raise GONE

    props = await _copy(graph, row)

    return KioskOut(
        surface_id=row.surface_id,
        label=surface.get("label") or row.surface_id,
        copy_text=props["consent_copy"],
        copy_version=props["consent_copy_version"],
        tier=props.get("consent_tier") or "T2",
        basis=props.get("consent_basis") or "explicit_optin",
    )


@router.post(
    "/{token}/consent",
    response_model=EventOut,
    status_code=status.HTTP_201_CREATED,
    summary="Record a consent given at this kiosk",
)
async def given(
    token: str,
    body: ConsentGivenIn,
    session: AsyncSession = Depends(get_session),
    graph: GraphSession = Depends(get_graph_session),
) -> EventOut:
    """Append `consent.given`, and nothing else.

    The division `POST /v1/consent` makes, one surface further out: recording is
    unconditional and acting on it is a consumer's job, with the bus's retries
    and dead-lettering behind it. A kiosk whose graph is unreachable still
    records the yes — which is the failure that endpoint's docstring says it
    exists to survive, and it is more true here, where the person who agreed is
    standing in front of the screen and will not be asked again.

    **The tier and the copy version are read, not received.** A surface that
    could name its own tier could claim a T3 for somebody shown the T1 wording,
    and the consent record is the evidence a disputed withdrawal is settled by.
    """
    row = await _open(token, session)
    props = await _copy(graph, row)
    at = body.at or dt.datetime.now(dt.timezone.utc)

    payload: dict = {
        "consent_id": body.consent_id,
        "tier": props.get("consent_tier") or "T2",
        "basis": props.get("consent_basis") or "explicit_optin",
        "copy_version": props["consent_copy_version"],
        # Which plinth, in the operator's own words where they gave it one. The
        # `captured_by` of `event-bus-spec.md` §3, filled from the row rather
        # than from the request for this router's whole reason.
        "captured_by": row.label or row.surface_id,
        "source": "kiosk",
        "surface_id": row.surface_id,
        "at": at.isoformat(),
    }
    if body.contact is not None:
        # Omitted entirely rather than written as an object of nulls, as
        # `routers/consent.py` does it: "no details were given" and "these
        # details are blank" are different statements about what a person handed
        # over.
        contact = body.contact.model_dump(exclude_none=True)
        if contact:
            payload["contact"] = contact

    appended, _ = await repository.append_event(
        session,
        EventIn(
            # Derived from the browser's own consent id, so its retry over bad
            # venue wifi and this endpoint agree on what "the same consent"
            # means. Distinct from the id `consumers/kiosk_consent.py` derives
            # for the `consent.captured` it produces: they are two events about
            # one conversation.
            event_id=derive_event_id("consent_given", row.tenant_id, body.consent_id),
            tenant_id=row.tenant_id,
            session_id=row.session_id,
            type=GIVEN,
            payload=payload,
            occurred_at=at,
        ),
    )

    await kiosk.note_use(session, token_id=row.id, now=dt.datetime.now(dt.timezone.utc))
    await session.commit()
    return EventOut.model_validate(appended)
