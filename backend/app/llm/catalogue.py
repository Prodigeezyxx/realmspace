"""
The questions this system can answer, and the only queries it will run.

`roadmap.md` Phase 2 asks for *"LLM → constrained Cypher (allow-list,
validated) → graph → answer"*. This module is the allow-list, and what it allows
is **named questions, not query text** — see `docs/adr/003-nl-query-catalogue.md`
for the whole argument. In short:

A model that emits Cypher can emit `MATCH (n) DETACH DELETE n`, reach `apoc`,
`LOAD CSV` or a subquery, or simply omit `tenant_id` — and the last one is the
dangerous one, because it looks like a correct answer about somebody else's
activation. Validating arbitrary Cypher is a parser problem nobody wins.

So the model's entire output is `{"query": "dwell_by_zone", "params": {…}}`. The
Cypher is written here, by hand, and reviewed like any other code. `tenant_id`
and `session_id` are supplied by the caller from the verified credential and are
**not parameters an entry declares**, so no model output can set them and no
entry can be executed without them. That makes tenant scoping a structural
property rather than something a validator has to catch.

## What a question nobody wrote gets

"I cannot answer that yet", plus the list of what it can. Never a guess, and
never an empty result presented as a zero — the same rule the report follows for
a figure it cannot compute (`roi-framework.md` §3, and `/live`'s "we don't know
and zero are different answers").

## Adding an entry

Write the function, declare the parameters, write the phrasing. The phrasing
matters as much as the query: with no provider configured it *is* the answer
(`app/llm/stub.py`), and with one it is the floor a model's prose has to beat.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app import repository
from app.graph import repository as graph_repo


class UnknownQuestion(LookupError):
    """The model named a query that does not exist, or none did."""


class BadParams(ValueError):
    """The model named a real query and filled it in wrongly."""


@dataclass(frozen=True)
class Param:
    """One parameter an entry accepts.

    Deliberately a tiny type system rather than none. The values arrive from a
    model — the least trustworthy caller in the system — and "limit" arriving as
    the string `"all"` or as `1e9` should be a refusal with a reason, not a
    stack trace or a query that reads the whole log.
    """

    name: str
    type: type
    default: Any = None
    minimum: int | None = None
    maximum: int | None = None
    description: str = ""

    def coerce(self, value: Any) -> Any:
        if value is None:
            return self.default
        if self.type is int:
            try:
                value = int(value)
            except (TypeError, ValueError) as exc:
                raise BadParams(
                    f"{self.name} has to be a whole number, and {value!r} is not"
                ) from exc
            if self.minimum is not None and value < self.minimum:
                raise BadParams(f"{self.name} has to be at least {self.minimum}")
            if self.maximum is not None and value > self.maximum:
                raise BadParams(f"{self.name} cannot be more than {self.maximum}")
            return value
        if self.type is str:
            return str(value)
        raise BadParams(f"{self.name} has a type this catalogue cannot check")


@dataclass(frozen=True)
class Entry:
    """One answerable question."""

    name: str
    #: What this answers, in the words an operator would use. Shown to the model
    #: as the menu it picks from, and shown to a person when nothing matched.
    asks: str
    #: Example phrasings. The stub matches on these; a model reads them as
    #: examples. Both benefit from them being real questions rather than keywords.
    examples: tuple[str, ...]
    #: `(scope, params) -> rows`. Scope carries tenant and session; the entry
    #: cannot obtain them any other way.
    run: Callable[..., Awaitable[list[dict[str, Any]]]]
    #: How the rows read as a sentence when no model is phrasing them.
    phrase: Callable[[list[dict[str, Any]]], str]
    params: tuple[Param, ...] = ()
    #: Rows that are worth charting, and how. `None` means a number or a list
    #: that a chart would flatter rather than clarify.
    chart: str | None = None

    def coerce(self, given: dict[str, Any] | None) -> dict[str, Any]:
        """Validate what the model filled in. Unknown keys are refused.

        Refused rather than dropped: a model that invented `zone_id` for a query
        that has no such parameter has misunderstood the question, and silently
        running the unfiltered version would answer a different question
        convincingly.
        """
        given = given or {}
        declared = {p.name for p in self.params}
        unknown = sorted(set(given) - declared)
        if unknown:
            raise BadParams(
                f"{self.name} takes {sorted(declared) or 'no parameters'} and was "
                f"given {unknown}"
            )
        return {p.name: p.coerce(given.get(p.name)) for p in self.params}


@dataclass(frozen=True)
class Scope:
    """Who is asking and about what. Never model-supplied.

    Both fields come from the verified credential and the request path. An entry
    receives this and cannot reach a database without it, which is what makes
    "the model cannot read another tenant" structural.
    """

    tenant_id: str
    session_id: str
    graph: Any
    log: Any


# ── the queries ───────────────────────────────────────────────────────────────


async def _visitors(scope: Scope, params: dict[str, Any]) -> list[dict[str, Any]]:
    count = await graph_repo.people_in_session(
        scope.graph, tenant_id=scope.tenant_id, session_id=scope.session_id
    )
    return [{"visitors": count}]


async def _dwell_by_zone(scope: Scope, params: dict[str, Any]) -> list[dict[str, Any]]:
    rows = await graph_repo.dwell_by_zone(
        scope.graph, tenant_id=scope.tenant_id, session_id=scope.session_id
    )
    return rows[: params["limit"]]


async def _zones(scope: Scope, params: dict[str, Any]) -> list[dict[str, Any]]:
    return await graph_repo.zones_for_session(
        scope.graph, tenant_id=scope.tenant_id, session_id=scope.session_id
    )


async def _surfaces(scope: Scope, params: dict[str, Any]) -> list[dict[str, Any]]:
    return await graph_repo.surfaces_for_session(
        scope.graph, tenant_id=scope.tenant_id, session_id=scope.session_id
    )


async def _busiest_window(
    scope: Scope, params: dict[str, Any]
) -> list[dict[str, Any]]:
    """The busiest N-minute bucket, from the log rather than the graph.

    The graph holds current state and has no notion of "when was it busy"; the
    log is ordered by event time and does. This is the shape of question the
    Analyst exists for — `roadmap.md` calls it "NL queries over the live bus".
    """
    minutes = params["minutes"]
    rows = await repository.read_events(
        scope.log,
        tenant_id=scope.tenant_id,
        session_id=scope.session_id,
        type="spatial.zone_enter",
        limit=repository.MAX_LIMIT,
    )
    if not rows:
        return []

    buckets: dict[dt.datetime, set[str]] = {}
    size = dt.timedelta(minutes=minutes)
    origin = rows[0].occurred_at
    for row in rows:
        index = int((row.occurred_at - origin) / size)
        start = origin + index * size
        buckets.setdefault(start, set()).add(
            str(row.payload.get("anon_id") or row.seq)
        )

    best = max(buckets.items(), key=lambda item: len(item[1]))
    return [
        {
            "window_start": best[0].isoformat(),
            "window_minutes": minutes,
            "people": len(best[1]),
        }
    ]


async def _funnel(scope: Scope, params: dict[str, Any]) -> list[dict[str, Any]]:
    """Zones in the operator's declared order, with who reached each.

    In `funnel_order`, never sorted by traffic — the same correction Phase 2 made
    to the report. A funnel sorted by popularity is not a funnel.
    """
    zones = await graph_repo.zones_for_session(
        scope.graph, tenant_id=scope.tenant_id, session_id=scope.session_id
    )
    dwell = {
        row["zone_id"]: row
        for row in await graph_repo.dwell_by_zone(
            scope.graph, tenant_id=scope.tenant_id, session_id=scope.session_id
        )
    }
    ordered = [z for z in zones if z.get("funnel_order") is not None]
    ordered.sort(key=lambda z: z["funnel_order"])
    return [
        {
            "zone": z.get("name") or z["id"],
            "funnel_order": z["funnel_order"],
            "visitors": (dwell.get(z["id"]) or {}).get("visitors", 0),
        }
        for z in ordered
    ]


# ── the phrasings ─────────────────────────────────────────────────────────────


def _say_visitors(rows: list[dict[str, Any]]) -> str:
    count = rows[0]["visitors"] if rows else 0
    return f"{count} unique {'visitor' if count == 1 else 'visitors'} in this session."


def _say_dwell(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No dwell has been recorded in this session yet."
    parts = [
        f"{row['zone'] or row['zone_id']} at {row['avg_dwell']:.0f}s average "
        f"across {row['visitors']} visitors"
        for row in rows
    ]
    return "Longest average dwell: " + "; ".join(parts) + "."


def _say_zones(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No zones are configured for this session."
    return f"{len(rows)} zones configured: " + ", ".join(
        str(r.get("name") or r["id"]) for r in rows
    ) + "."


def _say_surfaces(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No interactive surfaces are configured for this session."
    return f"{len(rows)} surfaces: " + ", ".join(
        str(r.get("label") or r["id"]) for r in rows
    ) + "."


def _say_busiest(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No zone entries have been recorded yet, so there is no busiest window."
    row = rows[0]
    return (
        f"The busiest {row['window_minutes']}-minute window began at "
        f"{row['window_start']}, with {row['people']} people entering a zone."
    )


def _say_funnel(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return (
            "No funnel order is configured for this session, so there is no funnel "
            "to report. Set one on the zones and this becomes answerable."
        )
    return "In the configured order: " + " → ".join(
        f"{r['zone']} ({r['visitors']})" for r in rows
    ) + "."


CATALOGUE: dict[str, Entry] = {
    entry.name: entry
    for entry in (
        Entry(
            name="visitor_count",
            asks="how many unique visitors this session has seen",
            examples=("How many unique visitors today?", "How many people came?"),
            run=_visitors,
            phrase=_say_visitors,
        ),
        Entry(
            name="dwell_by_zone",
            asks="which zones held attention longest, by average dwell",
            examples=(
                "Which zone had the longest average dwell?",
                "Where did people spend the most time?",
            ),
            params=(
                Param("limit", int, default=5, minimum=1, maximum=50,
                      description="how many zones to report"),
            ),
            run=_dwell_by_zone,
            phrase=_say_dwell,
            chart="bar",
        ),
        Entry(
            name="zones",
            asks="which zones are configured for this activation",
            examples=("What zones are set up?", "List the zones."),
            run=_zones,
            phrase=_say_zones,
        ),
        Entry(
            name="surfaces",
            asks="which interactive surfaces are configured",
            examples=("What surfaces are there?", "List the touchpoints."),
            run=_surfaces,
            phrase=_say_surfaces,
        ),
        Entry(
            name="busiest_window",
            asks="when the activation was busiest",
            examples=("Show me the busiest 5 minutes.", "When was it busiest?"),
            params=(
                Param("minutes", int, default=5, minimum=1, maximum=120,
                      description="the width of the window in minutes"),
            ),
            run=_busiest_window,
            phrase=_say_busiest,
        ),
        Entry(
            name="funnel",
            asks="how far visitors got through the configured funnel",
            examples=(
                "Where are people losing interest?",
                "What's the funnel drop-off?",
            ),
            run=_funnel,
            phrase=_say_funnel,
            chart="bar",
        ),
    )
}


def menu() -> list[dict[str, Any]]:
    """The catalogue as the model sees it, and as a refusal lists it.

    One structure for both, because the thing a person is told this system can
    answer and the thing the model is offered must not drift apart.
    """
    return [
        {
            "query": entry.name,
            "asks": entry.asks,
            "examples": list(entry.examples),
            "params": [
                {
                    "name": p.name,
                    "type": p.type.__name__,
                    "default": p.default,
                    "description": p.description,
                }
                for p in entry.params
            ],
        }
        for entry in CATALOGUE.values()
    ]


def get(name: str) -> Entry:
    entry = CATALOGUE.get(name)
    if entry is None:
        raise UnknownQuestion(
            f"{name!r} is not a question this system can answer. It can answer: "
            + ", ".join(sorted(CATALOGUE))
        )
    return entry


async def execute(
    name: str, params: dict[str, Any] | None, *, scope: Scope
) -> tuple[Entry, dict[str, Any], list[dict[str, Any]]]:
    """Validate and run one catalogue entry. The only path to the stores.

    Returns the entry as well as its rows, because every caller needs the
    phrasing and the chart hint that travel with it.
    """
    entry = get(name)
    checked = entry.coerce(params)
    rows = await entry.run(scope, checked)
    return entry, checked, rows
