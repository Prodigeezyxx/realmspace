"""
The parts of a handoff that every adapter has to answer the same way.

Two of them: the flattened key set a tenant's field map names, and the fields
Salesforce, Zoho and Dynamics will not create a lead without.

## One spelling of a mapped field, across five destinations

A client who maps `spatial_intent.lead_score` to a custom field should not have
to learn a second spelling of it because they changed CRM, and an adapter author
should not have to rediscover which keys are on offer. `mappable_fields` is that
list, built once from the `LeadHandoff/v1` shape in `integrations.md` §2.

## The fields Salesforce, Zoho and Dynamics will not create a lead without

All three model a lead with a mandatory surname and a mandatory company; a POST
missing either is a 400, and the lead is lost. A badge scan that carries only an
email is completely ordinary, so three adapters need the same answer to the same
question, and it is written here once rather than drifting into three.

## A placeholder is not an invented fact

`roi-framework.md` rules out numbers made up on a client's behalf and this
codebase extends that to fields. The answer here is not to guess a surname — it
is to write one that says out loud that nobody gave us one. A salesperson
opening `(unknown)` knows exactly what they have: an email, a company they will
have to ask for, and a spatial path that says the person spent four minutes at
the product pod. Deriving "Smith" from `smith@acme.com` would be a guess wearing
a real name's clothes, and the CRM would never show it as one.

The alternative — declining the lead the way a missing email is declined — was
rejected because a missing email means there is nothing to key a record on at
all, while a missing surname means only that a required box needs filling. One
is unsendable; the other is merely incomplete.

`default_company` is in the credential document so a client can put their own
convention there, since this is the value their sales ops team will be filtering
on.
"""

from __future__ import annotations

#: What goes in a mandatory name field when the visitor gave no name.
UNKNOWN_NAME = "(unknown)"

#: What goes in a mandatory company field when nothing set `default_company`.
UNKNOWN_COMPANY = "Unknown (realmspace)"


def split_name(full_name: str | None) -> tuple[str | None, str]:
    """`(first, last)` from whatever the capture surface gave us.

    Last name is never empty because the three CRMs that call this will not
    accept an empty one; first name is `None` when there was only one word,
    since a person with one name has one name and padding it would be inventing.
    """
    parts = (full_name or "").split()
    if not parts:
        return None, UNKNOWN_NAME
    if len(parts) == 1:
        return None, parts[0]
    return " ".join(parts[:-1]), parts[-1]


def mappable_fields(handoff: dict) -> dict:
    """The handoff flattened into the keys a tenant's field map names.

    Only the fields a destination cannot map by itself. The standard contact
    fields — email, name, company, title — are sent by every adapter regardless,
    because every CRM has somewhere to put them and asking a client to map them
    would be asking them to configure the obvious.
    """
    intent = handoff.get("spatial_intent") or {}
    roi = handoff.get("roi_context") or {}
    consent = handoff.get("consent") or {}
    activation = handoff.get("activation") or {}
    return {
        **{f"spatial_intent.{k}": v for k, v in intent.items()},
        "activation.id": activation.get("id"),
        "activation.name": activation.get("name"),
        "activation.venue": activation.get("venue"),
        "consent.tier": consent.get("tier"),
        "consent.basis": consent.get("basis"),
        "consent.captured_at": consent.get("captured_at"),
        "roi_context.attribution_window_days": roi.get("attribution_window_days"),
        "dedupe_key": handoff.get("dedupe_key"),
    }


def flatten(value):
    """A value as a CRM will take it.

    A list (`spatial_intent.zones_visited`) is semicolon-joined: every
    destination here accepts that in a text or multi-picklist field, and a JSON
    array is rejected wholesale, taking the rest of the record with it.
    """
    return ";".join(str(v) for v in value) if isinstance(value, list) else value
