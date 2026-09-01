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
those were computed from. **It does not carry their name.** See `NAME_TOKEN`.

## Why the question is marked

`QUESTION:` on its own line, so `app/llm/stub.py` can read it back out of the
assembled prompt rather than being handed a different input. One path through the
system whichever provider is on the end of it: the same prompt is built, and a
provider that cannot reason keyword-matches the same text a model would read.
"""

from __future__ import annotations

import json
import re
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


#: A visitor's identity is written by us, after the model has finished.
#:
#: `privacy.md`: *"AI reasoning calls receive only structured event summaries"*.
#: Ask sends catalogue rows and the insight digest sends anonymous measurements,
#: so both keep that literally; this prompt used to send a consented visitor's
#: name and company, because it is writing an email to them. A name is not an
#: image, so the clause's letter held — and a name is not a structured event
#: summary either, which is open decision 5 in `roadmap.md` and is answered *no*.
#:
#: So the model drafts about a placeholder and `splice_identity` puts the real
#: person in locally. The vendor sees where somebody walked and never who they
#: are; the reviewer sees the letter they would have seen either way.
#:
#: **Square brackets in capitals, not braces.** It is the mail-merge convention,
#: which is the whole reason a chat model copies it through verbatim instead of
#: trying to be helpful. `{{FIRST_NAME}}` reads as a template with a hole in it,
#: and a model asked to write prose will sometimes fill the hole in with a guess
#: — which is the one outcome this exists to prevent.
NAME_TOKEN = "[FIRST_NAME]"
COMPANY_TOKEN = "[COMPANY]"

#: Anything else of that shape, which the prompt forbids and a model invents
#: anyway: `[LAST_NAME]`, `[PRODUCT]`, `[YOUR NAME]`. See `splice_identity`.
_LEFTOVER_TOKEN = re.compile(r"\[[A-Z][A-Z0-9 _-]{2,}\]")


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
- Their name and their company are given to you as the literal placeholders \
[FIRST_NAME] and [COMPANY]. Write those exactly, character for character, \
wherever the name or the company belongs. Never guess at what they stand for, \
and never introduce a placeholder of your own — [LAST_NAME], [PRODUCT] and \
[YOUR NAME] are not fields we have, and a draft containing one is discarded.
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
    already built, so this prompt cannot reach past what a consent covered — and
    the two fields that name a person are replaced by `NAME_TOKEN` and
    `COMPANY_TOKEN` before it leaves the building.

    A contact with no name gets `"unknown"` rather than a token, which is what
    the rest of this prompt already does with a missing field: the model is told
    to leave it alone, and there would be nothing to splice in afterwards.
    """
    zones = intent.get("zones_visited") or []
    surfaces = intent.get("surfaces_engaged") or []
    return SDR.format(
        # Never the real values. `splice_identity` puts those in after the model
        # has finished, and `contact` is taken here only so the signature stays
        # the one every caller and test already knows.
        name=NAME_TOKEN if contact.get("name") else "unknown",
        company=COMPANY_TOKEN if contact.get("company") else "unknown",
        activation=activation or "unknown",
        zones=" → ".join(str(z) for z in zones) if zones else "unknown",
        top_zone=intent.get("top_dwell_zone") or "unknown",
        top_seconds=int(intent.get("top_dwell_seconds") or 0),
        surfaces=", ".join(str(s) for s in surfaces) if surfaces else "none recorded",
        total_seconds=int(intent.get("dwell_seconds_total") or 0),
    )


def splice_identity(text: str, *, contact: dict[str, Any]) -> str | None:
    """The real person, put back into a draft written about a placeholder.

    Returns `None` when the model left a placeholder we cannot fill — its own
    invention (`[LAST_NAME]`, `[PRODUCT]`), or `[FIRST_NAME]` on a contact whose
    name we never had and whose prompt therefore said "unknown".

    `None` is a refusal, and the caller keeps the composed draft: the same floor
    `consumers/sdr._split` falls back to, for the same reason. A letter reaching
    a reviewer with `[LAST_NAME]` in it reads as a broken mail-merge, and a
    reviewer's job is to check what the draft claims about somebody's visit, not
    to find our bugs.

    A draft that uses **no** token is fine and passes through untouched. "Hello,"
    is a legal opening; the prompt asks for the placeholder, it does not require
    the model to address anybody by name.

    The first name only, matching `deterministic_draft` — a follow-up that opens
    "Hi Jordan Reeve," is a mail-merge announcing itself.
    """
    name = (contact.get("name") or "").split(" ")[0]
    company = contact.get("company") or ""

    if name:
        text = text.replace(NAME_TOKEN, name)
    if company:
        text = text.replace(COMPANY_TOKEN, company)

    return None if _LEFTOVER_TOKEN.search(text) else text


def deterministic_draft(
    *, contact: dict[str, Any], intent: dict[str, Any], activation: str
) -> tuple[str, str]:
    """`(subject, body)` composed from the measurements, with no model.

    The same relationship `catalogue.Entry.phrase` has to a model's prose: this
    is the floor, and a provider that can reason improves on it. With open
    decision 2 still open it *is* the draft, so it has to be something an
    operator could actually send after reading it — not a placeholder.

    It states only what was measured, and it says where the detail came from.
    A follow-up that implied a conversation nobody had would be worse than no
    follow-up: the visitor knows whether they spoke to anybody.
    """
    name = (contact.get("name") or "").split(" ")[0]
    greeting = f"Hi {name}," if name else "Hello,"

    zones = [str(z) for z in (intent.get("zones_visited") or [])]
    surfaces = [str(s) for s in (intent.get("surfaces_engaged") or [])]
    top = intent.get("top_dwell_zone")
    total = int(intent.get("dwell_seconds_total") or 0)

    lines = [greeting, ""]
    if activation:
        lines.append(f"Thank you for visiting us at {activation}.")
    else:
        lines.append("Thank you for visiting our stand.")

    if top:
        lines.append(
            f"We noticed you spent time at {top}"
            + (f", across about {total // 60} minutes with us" if total >= 60 else "")
            + "."
        )
    elif zones:
        lines.append("We noticed you looked around " + ", ".join(zones) + ".")

    if surfaces:
        lines.append("You also tried " + ", ".join(surfaces) + ".")

    lines += [
        "",
        "If you would like to know more about what you saw, just reply to this "
        "message and we will pick it up from there.",
        "",
        "Best regards",
    ]

    subject = (
        f"Following up from {activation}" if activation else "Following up from our stand"
    )
    return subject, "\n".join(lines)


INSIGHT = """You are writing one short observation for an operator running a \
retail activation, about the last {minutes} minutes on their floor.

What was measured in that window, and the only thing you may use:

{measurements}

Rules, and they matter more than the prose:
- Use only the numbers above. Never add one, never round to a rounder-sounding \
figure, and never compare to a period you were not given.
- No recommendations and no causes. "Product Pod held attention longest" is an \
observation; "because the lighting draws people in" is a story we did not measure.
- One or two sentences. No preamble.
- If the numbers are unremarkable, say something unremarkable. An operator \
learns more from a quiet hour reported quietly than from a flat one dressed up.

QUESTION:
Write the observation."""


def insight_prompt(*, measurements: dict[str, Any], minutes: int) -> str:
    return INSIGHT.format(
        minutes=minutes,
        measurements=json.dumps(measurements, indent=2, default=str),
    )


def deterministic_insight(*, measurements: dict[str, Any], minutes: int) -> str:
    """The observation composed from the measurements, with no model.

    The floor, in the same sense `catalogue.Entry.phrase` is Ask's and
    `deterministic_draft` is the SDR's: with open decision 2 open this *is* the
    insight, so it has to be worth an operator's glance rather than a placeholder.

    It states what happened and stops. Everything a model might add — a cause, a
    comparison, a recommendation — is exactly what was not measured.
    """
    people = measurements.get("people") or 0
    entries = measurements.get("zone_entries") or 0
    top = measurements.get("top_zone")
    top_seconds = measurements.get("top_zone_seconds") or 0
    surfaces = measurements.get("surfaces") or []
    passbys = measurements.get("passbys") or 0

    if not people and not entries:
        return f"Nothing was recorded on the floor in the last {minutes} minutes."

    parts = [
        f"{people} {'person' if people == 1 else 'people'} moved through "
        f"{entries} zone {'entry' if entries == 1 else 'entries'} in the last "
        f"{minutes} minutes."
    ]

    if top and top_seconds:
        parts.append(
            f"{top} held attention longest, at {round(top_seconds)}s of dwell."
        )

    if surfaces:
        best = surfaces[0]
        parts.append(
            f"{best['surface']} was used {best['interactions']} "
            f"{'time' if best['interactions'] == 1 else 'times'}."
        )

    if passbys:
        parts.append(
            f"{passbys} {'person' if passbys == 1 else 'people'} came close to a "
            "zone without entering it."
        )

    return " ".join(parts)
