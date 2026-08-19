"""
What a model is actually sent, and what it is deliberately not sent.

Two prompts, because Ask is two questions and only one of them is about language:

1. **route** — which catalogue entry answers this, and with what parameters.
   The model chooses from a menu and returns JSON. It never writes a query.
2. **phrase** — turn these rows into a sentence. The model sees the numbers we
   measured and nothing else.

## The privacy rule this file exists to keep

`privacy.md`: *"AI reasoning calls (Claude / GPT-4o) receive only structured
event summaries — never images"*, and `consent-and-identity.md`'s redlines say no
raw video leaves the edge. Nothing here assembles a frame, a bounding box or a
raw event. The routing prompt carries the catalogue and the operator's question;
the phrasing prompt carries aggregate rows that have already been through a
catalogue entry.

The SDR's prompt is the one that touches a person, and it carries what the
visitor consented to us holding — zones, dwell, surfaces — never the log rows
those were computed from.

## Why the question is marked

`QUESTION:` on its own line, so `app/llm/stub.py` can read it back out of the
assembled prompt rather than being handed a different input. One path through the
system whichever provider is on the end of it: the same prompt is built, and a
provider that cannot reason keyword-matches the same text a model would read.
"""

from __future__ import annotations

import json
from typing import Any

from app.llm.catalogue import menu

ROUTING = """You translate an operator's question about a live retail activation \
into one of a fixed set of measurements. You do not write queries.

Reply with JSON only, in one of these two shapes:

  {{"query": "<name>", "params": {{...}}}}
  {{"query": null, "reason": "<why you cannot answer>"}}

Rules:
- `query` must be one of the names below, exactly. Never invent one.
- `params` may only contain the parameters that measurement declares.
- If the question is not answerable by one of these, say so with `query: null`
  and a reason the operator can act on. Do not pick the closest one.
- Never guess a number that is not in the data. You are choosing a measurement,
  not producing an answer.

The measurements you may choose from:

{menu}

QUESTION:
{question}"""


PHRASING = """You are writing one short, factual sentence for an operator \
standing on a trade-show floor.

The measurement `{query}` returned these rows:

{rows}

Write one or two sentences stating what they show. Rules:
- Use only the numbers in the rows. Never round to a rounder-sounding figure and \
never add a figure that is not there.
- No preamble, no "based on the data", no recommendations.
- If the rows are empty, say plainly that nothing has been recorded yet.

QUESTION:
{question}"""


SDR = """You are drafting a short follow-up email to somebody who visited a \
client's stand and agreed to be contacted.

What we know, and the only thing you may use:

  Their name:        {name}
  Their company:     {company}
  The activation:    {activation}
  Zones they stood in, in order: {zones}
  Where they spent longest: {top_zone} ({top_seconds} seconds)
  Surfaces they engaged with: {surfaces}
  Total dwell: {total_seconds} seconds

Rules, and they matter more than the prose:
- Reference only what is listed above. If a field says "unknown", do not mention \
it and do not work around it with a guess.
- Never invent a conversation, a product interest, a promise, a discount, or \
anything somebody said. We measured where they walked. We did not hear them.
- No pressure, no false familiarity, no "as we discussed".
- Under 120 words. Plain sentences. A subject line, then the body.
- This is a draft a human will read before anything is sent.

QUESTION:
Draft the follow-up."""


def routing_prompt(question: str) -> str:
    """The menu and the question. See the module docstring on the marker."""
    return ROUTING.format(
        menu=json.dumps(menu(), indent=2), question=question.strip()
    )


def phrasing_prompt(
    *, question: str, query: str, rows: list[dict[str, Any]]
) -> str:
    return PHRASING.format(
        query=query,
        rows=json.dumps(rows, indent=2, default=str),
        question=question.strip(),
    )


def sdr_prompt(*, contact: dict[str, Any], intent: dict[str, Any], activation: str) -> str:
    """The follow-up draft, from spatial intent and nothing else.

    Every field is filled from a `LeadHandoff/v1` the attribution consumer
    already built, so this prompt cannot reach past what a consent covered.
    """
    zones = intent.get("zones_visited") or []
    surfaces = intent.get("surfaces_engaged") or []
    return SDR.format(
        name=contact.get("name") or "unknown",
        company=contact.get("company") or "unknown",
        activation=activation or "unknown",
        zones=" → ".join(str(z) for z in zones) if zones else "unknown",
        top_zone=intent.get("top_dwell_zone") or "unknown",
        top_seconds=int(intent.get("top_dwell_seconds") or 0),
        surfaces=", ".join(str(s) for s in surfaces) if surfaces else "none recorded",
        total_seconds=int(intent.get("dwell_seconds_total") or 0),
    )
