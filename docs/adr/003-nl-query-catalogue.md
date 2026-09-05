# ADR 003 — The model picks a question; it never writes the query

**Status:** Accepted (2026-08-18) · **Implemented on this track (2026-08-18)**, **with a real provider since 2026-08-31** (`app/llm/openrouter.py`) —
`backend/app/llm/catalogue.py` (the catalogue), `app/llm/base.py` + `__init__.py`
(the provider seam), `app/llm/stub.py` (the deterministic stand-in),
`app/routers/ask.py` (the endpoint), `dashboard/src/app/(app)/ask/page.tsx` (the
surface).
**Context:** `roadmap.md` Phase 2 — *"**Ask the Room** real: LLM → constrained
Cypher (allow-list, validated) → graph → answer (replace regex mocks)"* — and
Phase 5's *"Live Analyst agent (NL queries over the live bus)"*. They are the
same endpoint.
**Deciders:** `antoniorobles/phase1` (`[neo4j-track]`).

## Decision

**An operator's question is mapped to the *name* of a hand-written query, never
to query text.** The model's entire output is:

```json
{ "query": "dwell_by_zone", "params": { "limit": 5 } }
```

The Cypher lives in `app/llm/catalogue.py`, is written by us, and is reviewed
like any other code. `tenant_id` and `session_id` are supplied by the caller from
the verified credential and the request path, and are **not parameters any entry
declares** — so there is no model output that can set them, and no entry that can
be executed without them.

## Why not validated free Cypher

The roadmap's phrase, "constrained Cypher (allow-list, validated)", has a reading
where the model writes Cypher and a validator approves it. That reading does not
survive contact with the language.

Cypher has `CALL`, `LOAD CSV`, procedure invocation (`apoc.*`), subqueries,
`UNION`, and predicates assembled from strings. A validator has to be a parser,
and a parser has to be right about a language that grows with each Neo4j release
while the thing it is defending against is a system explicitly good at producing
plausible text. Every allow-list of that kind is a list of the attacks somebody
thought of.

And the dangerous failure is not the loud one. `MATCH (n) DETACH DELETE n` would
at least be obvious. What actually goes wrong is:

```cypher
MATCH (p:Person)-[d:DWELLED_IN]->(z:Zone) RETURN z.name, avg(d.duration)
```

— correct Cypher, valid syntax, no forbidden keyword, and **no `tenant_id`**. It
returns a confident, well-formatted answer about every client this deployment
has ever hosted, and nobody reading the answer can tell. `multi-tenant.md` §2
puts tenant scoping on every query in the system; a validator that has to notice
its absence is a weaker guarantee than a query that cannot be written without it.

A catalogue makes the property structural. There is one code path to the stores,
it takes a `Scope`, and `Scope` carries the tenant.

## Why not "the LLM writes SQL/Cypher and we run it read-only"

A read-only credential answers the deletion case and not the disclosure one,
which is the case that matters here. It also gives up the second benefit: an
entry is a *reviewed measurement*. `funnel` returns zones in the operator's
declared order rather than sorted by traffic, because Phase 2 already made that
correction for the report and a generated query would rediscover the wrong
version. The catalogue is where "what this number means" is decided once.

## What the model is actually for

Two jobs, and only one is about language:

1. **Routing** — which measurement answers this question, with what parameters.
2. **Phrasing** — turning rows we measured into a sentence.

Neither touches a database. A provider is a text-in, text-out component with no
access to a store, which is what lets `app/llm/base.py` state that the model
cannot read another tenant's activation as a fact about the architecture rather
than a promise about the prompt.

The entry's own `phrase()` is the floor for job 2. A model outage costs an
operator some style, not an answer.

## What a question nobody wrote gets

"I cannot answer that yet", plus the list of what it can — never the nearest
entry. A near-miss answered confidently is worse than a refusal, because the
operator repeats it to a client. `/live` and the report already draw this
distinction for figures ("we don't know" and "zero" are different answers); this
extends it to questions.

## What an adapter owes — met by `app/llm/openrouter.py` (2026-08-31)

Open decision 2 closed on 2026-08-31: the provider is OpenRouter. This list was
written before the vendor was known and is unchanged by it — which was the point
of writing it then. Each item is now asserted in `tests/test_openrouter.py`:

- `complete(prompt) -> Completion` with the **vendor's own token counts**, never
  an estimate from character length. `app/cost.py` meters them, and an invented
  count is an invented figure on a unit-economics tile.
- Raise `LlmError(retryable=…)` and never return a degraded answer. The caller
  owns retry; a provider that swallowed its own 429 and guessed would present a
  guess as an answer.
- Declare `capabilities()["reasons"] = True`, which is what makes the surface say
  the model answered rather than the matcher.
- Accept that the prompt is assembled by `app/llm/prompts.py` and carries only
  structured summaries. `privacy.md`: *"AI reasoning calls receive only
  structured event summaries — never images."*

## What the vendor added to this list

Nothing about the catalogue, and two things about the adapter, both found by
calling it rather than by reading its docs:

- **Reasoning cannot be disabled** on either usable model, so the caller's
  `max_tokens` is the answer's budget and the adapter adds headroom. A routing
  call that spends its budget thinking returns `content: null` — an empty
  routing object, which reads exactly like a model that could not choose.
- **A model may fence its JSON.** Gemini wraps the routing object in a ```json
  block every time. Unfenced in `routers/ask.py`, which is the one place that
  parses model output; an adapter doing it would have to re-learn it per vendor.

## When no key is configured

`app/llm/stub.py` still answers, and it is not a fallback that was made obsolete
— it is what a tenant with no provider gets. It matches a question to an entry by
wording and then runs the real query, so every figure is measured; the only guess
is the match, and a tie or a miss is a refusal. It reports zero tokens, which is
a measurement: no call was made.

This is not the mock it replaced. `dashboard/src/lib/mock/ask-answers.ts` matched
nine regexes and returned **invented numbers**, behind a demo gate, so a real
activation asked a question and got nothing.

## Consequences

- Adding an answerable question is a code change, deliberately. That is the cost,
  and it is the same cost the report pays for every figure it shows.
- The catalogue is small, and its size is honest: it is exactly what this system
  can answer.
- A provider swap changes one module. The catalogue, the scoping, the metering
  and the surfaces do not move.
