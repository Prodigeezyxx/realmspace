"""
The attribution ledger, read out — as JSON for a screen, as CSV for an auditor.

`roi-framework.md` §4: "an exportable, auditable list of every booth-touch →
outcome link with timestamps and consent basis. This is what a CFO/auditor asks
for; nobody else in the category ships it."

The arithmetic lives in `app/attribution/ledger.py`, which is a pure function
over log events; this module's whole job is to fetch those events and render the
result. That split is why the numbers a client will argue about can be tested
without a database in front of them.

## Why the page size is large and paging is deliberate

A ledger is read whole — an export missing its last page is worse than no export,
because it looks complete. So each event type is read in full, in log order, up
to a bound that is stated rather than silent: past it the response says it was
truncated instead of quietly returning a shorter ledger.

## CSV is a first-class answer, not a nicety

"Exportable" is half the sentence in `roi-framework.md`. The CSV is flattened one
row per outcome (and one row per lead with no outcome yet), because that is the
shape a spreadsheet can pivot and the nested JSON is not.
"""

from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app import repository
from app.attribution import ledger as ledger_builder
from app.auth.principal import Principal, require_reader
from app.db import get_session
from app.graph import repository as graph_repo
from app.graph.driver import get_graph_session

router = APIRouter(prefix="/v1/ledger", tags=["ledger"])

HANDOFF = "handoff.lead"
OUTCOME = "outcome.recorded"
WITHDRAWN = "consent.withdrawn"

#: Read bound per event type. Generous, because a ledger read short is a ledger
#: that lies; `truncated` says so when it is hit rather than the caller having to
#: infer it from a suspiciously round number of rows.
MAX_EVENTS = 5000

CSV_COLUMNS = [
    "dedupe_key",
    "contact_id",
    "contact_name",
    "contact_email",
    "anon_id",
    "withdrawn",
    "first_touch_at",
    "final_touch_at",
    "zones_visited",
    "dwell_seconds_total",
    "lead_score",
    "lead_score_basis",
    "consent_tier",
    "consent_basis",
    "consent_copy_version",
    "consent_captured_at",
    "outcome_id",
    "outcome_stage",
    "outcome_value",
    "outcome_currency",
    "outcome_closed_at",
    "outcome_source",
    "outcome_recorded_by",
    "external_ref",
    "days_to_close",
    "in_window",
    "attributed_value",
]


async def _payloads(
    session: AsyncSession, *, tenant_id: str, session_id: str, type: str
) -> tuple[list[dict], bool]:
    rows = await repository.read_events(
        session,
        tenant_id=tenant_id,
        session_id=session_id,
        type=type,
        limit=MAX_EVENTS,
    )
    return [row.payload for row in rows], len(rows) >= MAX_EVENTS


@router.get(
    "/{session_id}",
    summary="Every booth-touch → outcome link, with its consent basis",
)
async def get_ledger(
    session_id: str,
    format: str = Query("json", pattern="^(json|csv)$"),
    principal: Principal = Depends(require_reader),
    session: AsyncSession = Depends(get_session),
    graph=Depends(get_graph_session),
):
    """Build the ledger for one activation.

    The model and window come from the session's configuration, not from a query
    parameter. That is the point of `roi-framework.md` §5 — they are agreed with
    the client before the doors open, and a ledger whose model could be chosen at
    read time is a ledger you can shop for a better number in.
    """
    config = (
        await graph_repo.session_config(
            graph, tenant_id=principal.tenant_id, session_id=session_id
        )
        or {}
    )

    handoffs, h_trunc = await _payloads(
        session, tenant_id=principal.tenant_id, session_id=session_id, type=HANDOFF
    )
    outcomes, o_trunc = await _payloads(
        session, tenant_id=principal.tenant_id, session_id=session_id, type=OUTCOME
    )
    withdrawals, w_trunc = await _payloads(
        session, tenant_id=principal.tenant_id, session_id=session_id, type=WITHDRAWN
    )

    built = ledger_builder.build(
        handoffs,
        outcomes,
        withdrawals,
        attribution_model=config.get("attribution_model") or "influenced",
        attribution_window_days=config.get("attribution_window_days") or 90,
    )
    built["session_id"] = session_id
    built["activation_cost"] = config.get("activation_cost")
    #: The client's own typed-in figure, carried beside the measured one rather
    #: than replaced by it. Where the two disagree, that disagreement is
    #: information — see the note in the one-pager.
    built["revenue_influenced_stated"] = config.get("revenue_influenced")
    built["truncated"] = h_trunc or o_trunc or w_trunc

    if format == "csv":
        return Response(
            content=_to_csv(built),
            media_type="text/csv",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="ledger-{session_id}.csv"'
                )
            },
        )
    return built


def _to_csv(built: dict) -> str:
    """Flatten to one row per outcome, plus a row for every lead without one.

    A lead with no outcome yet is the majority of any live ledger and is not an
    empty row — it is a booth touch waiting on a sales cycle, and dropping it
    would make the export overstate the conversion rate of everything left.
    """
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()

    for row in built["rows"]:
        base = {
            column: row.get(column)
            for column in CSV_COLUMNS
            if column in row and column != "zones_visited"
        }
        base["zones_visited"] = " > ".join(row.get("zones_visited") or [])

        if not row["outcomes"]:
            writer.writerow(base)
            continue

        for outcome in row["outcomes"]:
            writer.writerow(
                {
                    **base,
                    "outcome_id": outcome["outcome_id"],
                    "outcome_stage": outcome["stage"],
                    "outcome_value": outcome["value"],
                    "outcome_currency": outcome["currency"],
                    "outcome_closed_at": outcome["closed_at"],
                    "outcome_source": outcome["source"],
                    "outcome_recorded_by": outcome["recorded_by"],
                    "external_ref": outcome["external_ref"],
                    "days_to_close": outcome["days_to_close"],
                    "in_window": outcome["in_window"],
                }
            )

    return buffer.getvalue()
