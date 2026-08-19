"""
The drafts, for the human who decides whether any of them is sent.

`consumers/sdr.py` writes `followup.drafted` onto the log and stops there. A
draft nobody can read is not a deliverable, so this is the read side.

## Read-only, and that is the feature

There is no send endpoint, no approve, no edit. `roadmap.md` calls this a *draft*
agent; sending wants an email provider that does not exist, a suppression list, a
bounce story and an audit of who pressed the button, and shipping half of that
behind a button labelled "Send" would be worse than shipping none of it. The page
says so rather than leaving an operator to discover it.

## Withdrawn and erased people are redacted at read time

The same rule `routers/handoffs.py` and the ledger follow, for the same reason: a
withdrawal does not rewrite the log — only an erasure does — so a draft written
before somebody withdrew is still sitting there with their name in it. The draft
body is redacted too, not just the contact: the body says "Hi Sam".
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app import repository
from app.attribution.ledger import is_redacted_key, withdrawn_subjects
from app.auth.principal import Principal, require_leads
from app.db import get_session
from app.routers.handoffs import _every_withdrawal

router = APIRouter(prefix="/v1/followups", tags=["followups"])

DRAFTED = "followup.drafted"

#: What a redacted draft reads as. The row survives because it is the record that
#: a draft existed and was never sent; what it says about the person does not.
REDACTED = "[withdrawn]"


@router.get("", summary="Follow-up drafts awaiting a human")
async def list_followups(
    session_id: str | None = Query(default=None, alias="sessionId"),
    since: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=1000),
    principal: Principal = Depends(require_leads),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Every draft, newest cursor last, withdrawn ones stripped of their person.

    `require_leads`: these are somebody's name beside a letter written about
    them, which is not something everyone with a dashboard login should see.
    `multi-tenant.md` §3 puts "own follow-up sequences" with Analyst — the person
    whose job the drafts are — rather than with the operator running the room.
    """
    rows = await repository.read_events(
        session,
        tenant_id=principal.tenant_id,
        since_seq=since,
        limit=limit,
        session_id=session_id,
        type=DRAFTED,
    )

    withdrawals = await _every_withdrawal(session, tenant_id=principal.tenant_id)
    withdrawn_keys, withdrawn_contacts = withdrawn_subjects(
        withdrawals, [row.payload for row in rows]
    )

    drafts = []
    for row in rows:
        payload = dict(row.payload)
        contact = dict(payload.get("contact") or {})
        key = payload.get("dedupe_key") or ""

        withdrawn = (
            key in withdrawn_keys
            or contact.get("id") in withdrawn_contacts
            or is_redacted_key(key)
            or row.redacted_at is not None
        )
        if withdrawn:
            payload["contact"] = {"id": contact.get("id")}
            payload["subject"] = REDACTED
            payload["body"] = REDACTED

        drafts.append(
            {
                "seq": row.seq,
                "sessionId": row.session_id,
                "draftedAt": payload.get("drafted_at"),
                "withdrawn": withdrawn,
                #: True once somebody sends it. Nothing in this build sets it —
                #: see the module docstring — and it is on the wire so a surface
                #: never has to infer "not sent" from an absent field.
                "sent": bool(payload.get("sent")),
                "basis": payload.get("basis"),
                "draft": payload,
            }
        )

    return {
        "followups": drafts,
        "count": len(drafts),
        "nextSince": rows[-1].seq if rows else since,
        "more": len(rows) == limit,
        #: Stated on every response rather than in documentation somebody has to
        #: go and find.
        "sendingSupported": False,
    }
