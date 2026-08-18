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

## Reader, not operator

`require_reader`: this is the client's own data going to the client's own
systems, and the role that may read a report may read the leads in it.
"""

from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app import repository
from app.attribution.ledger import redact_dedupe_key, withdrawn_subjects
from app.auth.principal import Principal, require_reader
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
    principal: Principal = Depends(require_reader),
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

    withdrawals = await repository.read_events(
        session,
        tenant_id=principal.tenant_id,
        limit=1000,
        session_id=session_id,
        type=WITHDRAWN,
    )
    withdrawn_keys, withdrawn_contacts = withdrawn_subjects(
        [w.payload for w in withdrawals], [r.payload for r in rows]
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


def _shape(row, *, withdrawn_keys: set[str], withdrawn_contacts: set[str]) -> dict:
    """One log row as a handoff a client can import, redacted if it has to be."""
    payload = dict(row.payload)
    contact = payload.get("contact")
    key = payload.get("dedupe_key") or ""

    withdrawn = key in withdrawn_keys or (
        isinstance(contact, dict) and contact.get("id") in withdrawn_contacts
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
