"""
The rules this system can build from a sentence, and the floor that builds them
without a model.

`PRD.md` §6.5 asks for *"a plain-English composer powered by the LLM that
translates intent → rule"*, and ADR-002's first argument for rules-as-data is
that an operator writes them: *"plain-English → spec → operator confirms."* This
module is the vocabulary both halves of that share.

## How this differs from `catalogue.py`, and why

Ask's catalogue exists because a model that emits **Cypher** cannot be validated
— arbitrary query text is a parser problem nobody wins, and the dangerous output
is the one that looks right and omits `tenant_id` (`docs/adr/003-nl-query-
catalogue.md`). So there, the model picks a name and never writes a query.

A rule document is not in that position. It is **data with a closed union and a
validator that already exists** (`schemas.RuleIn`, which `PUT /v1/rules` enforces
on every rule an operator writes by hand), and the three things a model must not
decide are taken away from it structurally in `routers/rules.py` rather than
checked afterwards: the tenant and the id are never read from its output, and the
action union it is validated against does not contain a destination outside the
room.

So the model composes documents, and the shapes below are two other things:
what the **deterministic floor** can build with no provider, and what a refusal
lists so "I could not turn that into a rule" is never a dead end. Same job as
`catalogue.menu()`, which is one structure for what the model is offered and what
a person is told.

## The floor refuses rather than reaching

`stub.py::route` scores a question against each entry's own vocabulary and
refuses on a tie or a zero, because "the whole point of this file is that it does
not reach for the nearest entry when it has not understood". A rule is a stronger
version of that argument: a mis-picked measurement gives a wrong answer on a
screen, and a mis-picked rule posts to a channel or tells the floor staff to do
something.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

#: Words that carry no signal about which rule is being asked for. The same list
#: `stub.py` uses for questions, plus the ones that are noise here specifically —
#: "when", "if" and "rule" appear in nearly every phrasing an operator writes.
NOISE = frozenset(
    """a an and are as at by do does for from get give had has have how i if in
    is it me my of on or our rule set so tell that the their then there these
    this to us was we were what when whenever where which who why will with you
    your""".split()
)


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in NOISE}


def numbers(text: str) -> list[int]:
    """Every integer in the phrase, in order.

    Deliberately not "the first number is the count": "5 people for 30 seconds"
    and "30 seconds, 5 people" are the same rule, and each shape reads the ones
    it needs by unit rather than by position — see `_seconds_near`.
    """
    return [int(n) for n in re.findall(r"\b(\d{1,5})\b", text)]


def seconds_in(text: str) -> int | None:
    """A duration, if the phrase names one with a unit.

    Minutes are converted rather than refused: an operator writes "for two
    minutes" as often as "for 120 seconds", and a rule that quietly read 2 as
    seconds would fire on everybody who paused.
    """
    match = re.search(r"(\d{1,5})\s*(seconds?|secs?|s)\b", text, re.I)
    if match:
        return int(match.group(1))
    match = re.search(r"(\d{1,4})\s*(minutes?|mins?|m)\b", text, re.I)
    if match:
        return int(match.group(1)) * 60
    return None


@dataclass(frozen=True)
class Shape:
    """One kind of rule, in the operator's words and in the spec's.

    `build` returns the document's middle — trigger, condition, action and
    cooldown. The name, the id and the tenant are the caller's, for the reason
    `routers/rules.py` gives.
    """

    name: str
    #: What an operator would say they want. Shown in a refusal and in the prompt.
    asks: str
    examples: tuple[str, ...] = ()
    build: Callable[[str, dict[str, Any] | None], dict[str, Any]] = field(
        repr=False, default=None
    )  # type: ignore[assignment]


def _id(zone: dict[str, Any] | None) -> str | None:
    """The zone's id, or None for "every zone" — which is what the evaluator
    reads `triggerZoneId: null` as, and a real choice rather than a missing one."""
    return str(zone["id"]) if zone else None


def _named(template: str, zone: dict[str, Any] | None) -> str:
    """A name for the rule, since a document without one cannot be saved.

    Found by walking rather than by a test: the composer returned a document
    every unit test agreed with, and `PUT /v1/rules` refused it with
    `name: Field required` — the shapes had never named what they built. The
    operator renames it in the form; what this has to be is *armable*.

    Named by the zone's own label rather than its id, which is why the builders
    are handed the whole zone: "Entry Arch at capacity" is what an operator
    called that part of their floor, and `z_entry_arch` is what we called it.
    """
    return template.format(zone=(zone or {}).get("name") or "Every zone")


def _crowding(text: str, zone: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "name": _named("{zone} at capacity", zone),
        "triggerType": "spatial.occupancy",
        "triggerZoneId": _id(zone),
        # Both ends of a crossing are one event type, so without this the prompt
        # is raised again at the moment the zone clears. See ADR-002's
        # `payloadEquals` section.
        "condition": {"type": "any", "payloadEquals": {"status": "over"}},
        "action": {
            "type": "staff_prompt",
            "message": "This zone is at the capacity you set for it",
            "zoneId": _id(zone),
        },
        "cooldownSec": 60,
    }


def _dwell_threshold(text: str, zone: dict[str, Any] | None) -> dict[str, Any]:
    seconds = seconds_in(text) or 30
    counts = [n for n in numbers(text) if n != seconds]
    count = counts[0] if counts else 1
    return {
        "name": _named("Visitors dwelling in {zone}", zone),
        "triggerType": "spatial.dwell",
        "triggerZoneId": _id(zone),
        "condition": {
            "type": "threshold",
            "count": count,
            # The window is the operator's duration or a minute — a threshold
            # with no window counts nothing, and `presets.py` records the same
            # choice for the same reason.
            "windowSec": max(seconds, 60),
            "zoneId": _id(zone),
            # `spatial.dwell` is emitted for every stay including a two-second
            # one, so "5 people for 30 seconds" without this counts five people
            # who walked past.
            "minDwellSec": seconds,
        },
        "action": {
            "type": "staff_prompt",
            "message": "Several visitors are spending time here",
            "zoneId": _id(zone),
        },
        "cooldownSec": 60,
    }


def _group_arrives(text: str, zone: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "name": _named("A group arrives at {zone}", zone),
        "triggerType": "spatial.group",
        "triggerZoneId": _id(zone),
        # `formed`, not any: a rule that greeted an arriving group would greet
        # them again as it dissolved.
        "condition": {"type": "any", "payloadEquals": {"status": "formed"}},
        "action": {
            "type": "staff_prompt",
            "message": "A group has arrived together",
            "zoneId": _id(zone),
        },
        "cooldownSec": 60,
    }


def _quiet(text: str, zone: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "name": _named("{zone} has gone quiet", zone),
        "triggerType": "spatial.zone_enter",
        "triggerZoneId": _id(zone),
        "condition": {"type": "none", "windowSec": seconds_in(text) or 600,
                      "zoneId": _id(zone)},
        "action": {
            "type": "staff_prompt",
            "message": "Nobody has come to this zone for a while",
            "zoneId": _id(zone),
        },
        "cooldownSec": 300,
    }


def _walked_past(text: str, zone: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "name": _named("Visitors walking past {zone}", zone),
        "triggerType": "spatial.passby",
        "triggerZoneId": _id(zone),
        "condition": {
            "type": "threshold",
            "count": (numbers(text) or [3])[0],
            "windowSec": seconds_in(text) or 300,
            "zoneId": _id(zone),
        },
        "action": {
            "type": "staff_prompt",
            "message": "Visitors keep passing this zone without stopping",
            "zoneId": _id(zone),
        },
        "cooldownSec": 300,
    }


def _touched(text: str, zone: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "name": _named("A touchpoint was used", zone),
        "triggerType": "surface.interaction",
        "triggerZoneId": None,
        "condition": {"type": "any"},
        "action": {
            "type": "staff_prompt",
            "message": "Somebody is using a touchpoint",
        },
        "cooldownSec": 60,
    }


SHAPES: tuple[Shape, ...] = (
    Shape(
        name="zone_at_capacity",
        asks="a zone reaching the capacity its operator set",
        examples=(
            "tell staff when the entrance is full",
            "prompt the floor when a zone hits capacity",
        ),
        build=_crowding,
    ),
    Shape(
        name="people_dwelling",
        asks="several people staying in one zone for longer than a set time",
        examples=(
            "when 5 people dwell at the entrance for 30 seconds, prompt staff",
            "if three visitors spend a minute at the product wall",
        ),
        build=_dwell_threshold,
    ),
    Shape(
        name="group_arrives",
        asks="a group arriving together",
        examples=("greet a group when they arrive at the lounge",),
        build=_group_arrives,
    ),
    Shape(
        name="zone_gone_quiet",
        asks="nobody entering a zone for a stretch of time",
        examples=(
            "tell me when nobody has come to the demo station for 10 minutes",
        ),
        build=_quiet,
    ),
    Shape(
        name="visitors_walking_past",
        asks="people approaching a zone and not entering it",
        examples=("when three people walk past the sponsor wall without stopping",),
        build=_walked_past,
    ),
    Shape(
        name="touchpoint_used",
        asks="somebody using a touchpoint",
        examples=("when the AR mirror is used",),
        build=_touched,
    ),
)


def menu() -> list[dict[str, Any]]:
    """The shapes as a model is shown them, and as a refusal lists them.

    One structure for both, for `catalogue.menu()`'s reason: what a person is
    told this can build and what the model is offered must not drift apart.
    """
    return [
        {"shape": s.name, "asks": s.asks, "examples": list(s.examples)}
        for s in SHAPES
    ]


def match_zone(text: str, zones: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The zone an operator named, or None if they named none of them.

    Matched on the name the operator gave the zone, longest first, so "Entry
    Arch" wins over "Entry" where a session has both. **Never the nearest
    match**: a rule armed on the wrong zone is armed, plausible and silent, so an
    unrecognised zone leaves this None and the rule applies to every zone —
    which is what `triggerZoneId: null` already means and what the operator can
    see in the sentence before they press anything.
    """
    lowered = text.lower()
    for zone in sorted(zones, key=lambda z: -len(str(z.get("name") or ""))):
        name = str(zone.get("name") or "").strip().lower()
        if name and name in lowered:
            return zone
    return None


def deterministic_compose(
    text: str, zones: list[dict[str, Any]]
) -> tuple[dict[str, Any] | None, str]:
    """`(document body, reason)` — the floor, with no model.

    Scored on how much of a shape's own vocabulary the phrase uses, exactly as
    `stub.py::route` scores a question against a catalogue entry, and refusing on
    the same two conditions: nothing matched, or two matched equally. A rule is
    the stronger case for that rule — a mis-picked measurement shows a wrong
    number, a mis-picked rule tells the floor staff to do something.
    """
    asked = words(text)
    if not asked:
        return None, "there is nothing to build a rule from"

    scores = {
        s.name: len(asked & words(" ".join((s.name.replace("_", " "), s.asks, *s.examples))))
        for s in SHAPES
    }
    best = max(scores.values())
    if best == 0:
        return None, (
            "I could not turn that into a rule. Without an AI provider configured "
            "I match your wording against the shapes I know, so try one of the "
            "examples — or build it with the fields below, which can express more "
            "than this can"
        )

    winners = sorted(name for name, score in scores.items() if score == best)
    if len(winners) > 1:
        return None, (
            "that could be "
            + " or ".join(winners)
            + ", and I would rather ask than guess"
        )

    shape = next(s for s in SHAPES if s.name == winners[0])
    return shape.build(text, match_zone(text, zones)), ""
