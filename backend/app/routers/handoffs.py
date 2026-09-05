"""
The pull half of bring-your-own: a client reads their own leads, on their schedule.

`integrations.md` §5 lists five bring-your-own connectors. The signed webhook and
the Zapier/Make hooks push. These two pull:

- **Inbound REST API** — "customers pull handoffs from realmspace on their
  schedule", which is what an integration team behind a firewall actually wants:
  no inbound port, no endpoint to keep up, nothing to sign-verify.
- **CSV / scheduled export** — "for offline or air-gapped clients". The scheduled
  half is a cron calling this with `?format=csv`. There is deliberately no
  scheduler here: a job runner that emailed a file on Tuesdays would be a second
  place where a client's leads leave the building, with its own retry story and
  its own way of failing quietly.

## The cursor is the log's own seq, not a page number

Same cursor the WebSocket and `GET /events` use, and the reason is the same one
`event-bus-spec.md` §2 gives: seq is the single source of truth for order, so a
consumer that stops at 4,180 and comes back tomorrow asks for 4,180 and gets
every lead since with no gap and no duplicate. A page number would shift under
them the moment a new handoff landed.

## Redaction happens here, because the log keeps the name

A withdrawal does not rewrite the log — only an erasure does, and only for the
person who asked. So a `handoff.lead` for somebody who has since withdrawn is
still sitting there with their email in it, and an endpoint that read it straight
out would hand a client the data that person asked to stop being used. The
verdict comes from `attribution.ledger.withdrawn_subjects`, imported rather than
re-derived: the ledger reads the same log and has to reach the same answer, and
two implementations would disagree exactly once, in the direction that matters.

## `leads`, not plain `read`

This returns contact emails. `multi-tenant.md` §3 gives "own follow-up
sequences" to Analyst and describes Viewer as "the sponsor/brand stakeholder" —
somebody who reads the report about an activation, not the list of people who
attended it. A capability rather than a role name, so the answer to "who may
pull our leads" is one line in `CAPABILITIES` and not a condition repeated here.
"""

from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app import repository
from app.attribution.ledger import (
    is_redacted_key,
    redact_dedupe_key,
    withdrawn_subjects,
)
from app.auth.principal import Principal, require_leads
from app.db import get_session

router = APIRouter(prefix="/v1/handoffs", tags=["handoffs"])

HANDOFF = "handoff.lead"
WITHDRAWN = "consent.withdrawn"

#: One page. Matches `repository.MAX_LIMIT`, which is the real ceiling.
DEFAULT_LIMIT = 200

CSV_COLUMNS = [
    "seq",
    "occurred_at",
    "session_id",
    "stage",
    "dedupe_key",
    "contact_id",
    "contact_email",
    "contact_name",
    "contact_company",
    "contact_title",
    "anon_id",
    "withdrawn",
    "consent_tier",
    "consent_basis",
    "consent_captured_at",
    "zones_visited",
    "top_dwell_zone",
    "dwell_seconds_total",
    "surfaces_engaged",
    "attention_score",
    "lead_score",
    "lead_score_basis",
    "activation_id",
    "activation_name",
    "activation_venue",
    "attribution_window_days",
    "activation_cost_share",
]


@router.get("", summary="Pull leads from the log, from a cursor")
async def list_handoffs(
    session_id: str | None = Query(default=None, alias="sessionId"),
    since: int = Query(default=0, ge=0, description="Last seq you handled"),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=1000),
    format: str = Query("json", pattern="^(json|csv)$"),
    principal: Principal = Depends(require_leads),
    session: AsyncSession = Depends(get_session),
):
    """Every `handoff.lead` after `since`, oldest first, withdrawn ones redacted.

    `nextSince` is what to pass next time. It is the seq of the last row
    returned rather than the caller's own bookkeeping, so a client that saves it
    after a successful import cannot skip a lead by miscounting.
    """
    rows = await repository.read_events(
        session,
        tenant_id=principal.tenant_id,
        since_seq=since,
        limit=limit,
        session_id=session_id,
        type=HANDOFF,
    )

    withdrawals = await _every_withdrawal(session, tenant_id=principal.tenant_id)
    withdrawn_keys, withdrawn_contacts = withdrawn_subjects(
        withdrawals, [r.payload for r in rows]
    )

    handoffs = [
        _shape(row, withdrawn_keys=withdrawn_keys, withdrawn_contacts=withdrawn_contacts)
        for row in rows
    ]

    if format == "csv":
        return Response(
            content=_to_csv(handoffs),
            media_type="text/csv",
            headers={
                "Content-Disposition": 'attachment; filename="handoffs.csv"'
            },
        )

    return {
        "handoffs": handoffs,
        "count": len(handoffs),
        # The caller's next cursor. Unchanged when there was nothing new, which
        # is what makes an empty poll a no-op rather than a rewind.
        "nextSince": rows[-1].seq if rows else since,
        # True when this page filled — there is more to read right now, and a
        # client on a schedule should keep going rather than wait for tomorrow.
        "more": len(rows) == limit,
    }


async def _every_withdrawal(session: AsyncSession, *, tenant_id: str) -> list[dict]:
    """Every `consent.withdrawn` this tenant has, paged to the end.

    Two things this deliberately does not do, both of which it did once and both
    of which failed in the same direction — a withdrawn person's email going out
    to a client's own systems.

    **It does not stop at one page.** `repository.read_events` caps at
    `MAX_LIMIT` and returns the *oldest* rows from the cursor, so a single read
    hands back the first thousand withdrawals a tenant ever recorded and silently
    drops the rest. The dropped ones are the recent ones — the people who withdrew
    most recently are exactly the people most likely to still be in the page being
    exported. `routers/ledger.py` meets the same cap and answers it with a
    `truncated` flag, which is honest for a report and useless here: there is no
    partially-correct redaction, so this reads to the end instead.

    **It does not filter by session.** A withdrawal is filed under whichever
    session recorded it (`WithdrawalIn.session_id` is required), while a Contact
    id is stable across activations — so a visitor who attended two and withdrew
    at the second has their withdrawal filed under the second alone. Scoping this
    read to the caller's `sessionId` would leave the first activation's handoff
    un-redacted for the one person who had asked, most explicitly, that it not be.
    """
    withdrawals: list[dict] = []
    since = 0
    while True:
        page = await repository.read_events(
            session,
            tenant_id=tenant_id,
            since_seq=since,
            limit=repository.MAX_LIMIT,
            type=WITHDRAWN,
        )
        withdrawals.extend(row.payload for row in page)
        if len(page) < repository.MAX_LIMIT:
            return withdrawals
        since = page[-1].seq


def _shape(row, *, withdrawn_keys: set[str], withdrawn_contacts: set[str]) -> dict:
    """One log row as a handoff a client can import, redacted if it has to be."""
    payload = dict(row.payload)
    contact = payload.get("contact")
    key = payload.get("dedupe_key") or ""

    withdrawn = (
        key in withdrawn_keys
        or (isinstance(contact, dict) and contact.get("id") in withdrawn_contacts)
        # An erasure has already rewritten this row, and the request that caused
        # it may have named only a consent id — which neither of the two checks
        # above can see. See `ledger.is_redacted_key`.
        or is_redacted_key(key)
    )
    if withdrawn:
        # The touch, its timestamps and its consent basis survive; the name does
        # not. Exactly what the ledger does with the same row, for the same
        # reason — a client importing this should be able to see that a lead was
        # here and has been withdrawn, and should not receive them again.
        if isinstance(contact, dict):
            payload["contact"] = {"id": contact.get("id")}
        payload["dedupe_key"] = redact_dedupe_key(key)

    return {
        "seq": row.seq,
        "eventId": str(row.event_id),
        "occurredAt": row.occurred_at.isoformat(),
        "sessionId": row.session_id,
        "withdrawn": withdrawn,
        #: Set when an erasure has rewritten this row (migration 0009), so a
        #: client re-reading their history can tell "this lead was never named"
        #: from "we removed the name at their request".
        "redactedAt": row.redacted_at.isoformat() if row.redacted_at else None,
        "handoff": payload,
    }


def _to_csv(handoffs: list[dict]) -> str:
    """Flatten to one row per handoff.

    One row per handoff rather than per lead: both stages of one visitor are
    separate rows here, because this is the log and the whole point of pulling it
    is to see what arrived and when. The ledger is where the two are folded into
    one lead; an export that quietly did that would be a second, disagreeing
    ledger.
    """
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()

    for entry in handoffs:
        payload = entry["handoff"]
        contact = payload.get("contact") or {}
        intent = payload.get("spatial_intent") or {}
        consent = payload.get("consent") or {}
        activation = payload.get("activation") or {}
        roi = payload.get("roi_context") or {}

        writer.writerow(
            {
                "seq": entry["seq"],
                "occurred_at": entry["occurredAt"],
                "session_id": entry["sessionId"],
                "stage": payload.get("stage"),
                "dedupe_key": payload.get("dedupe_key"),
                "contact_id": contact.get("id"),
                "contact_email": contact.get("email"),
                "contact_name": contact.get("name"),
                "contact_company": contact.get("company"),
                "contact_title": contact.get("title"),
                "anon_id": payload.get("anon_id"),
                "withdrawn": entry["withdrawn"],
                "consent_tier": consent.get("tier"),
                "consent_basis": consent.get("basis"),
                "consent_captured_at": consent.get("captured_at"),
                "zones_visited": " > ".join(intent.get("zones_visited") or []),
                "top_dwell_zone": intent.get("top_dwell_zone"),
                "dwell_seconds_total": intent.get("dwell_seconds_total"),
                "surfaces_engaged": ";".join(intent.get("surfaces_engaged") or []),
                "attention_score": intent.get("attention_score"),
                "lead_score": intent.get("lead_score"),
                "lead_score_basis": intent.get("lead_score_basis"),
                "activation_id": activation.get("id"),
                "activation_name": activation.get("name"),
                "activation_venue": activation.get("venue"),
                "attribution_window_days": roi.get("attribution_window_days"),
                "activation_cost_share": roi.get("activation_cost_share"),
            }
        )

    return buffer.getvalue()
