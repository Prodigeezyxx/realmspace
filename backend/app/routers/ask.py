"""
Ask the Room — a question in English, an answer off measured data.

`roadmap.md` Phase 2's last open item and Phase 5's Live Analyst are the same
endpoint: *"LLM → constrained Cypher (allow-list, validated) → graph → answer
(replace regex mocks)"*, and *"Live Analyst agent (NL queries over the live
bus)"*. One path answers both, because a question about right now and a question
about this morning differ only in which catalogue entry serves them.

## What this replaces

`dashboard/src/lib/mock/ask-answers.ts`: nine regexes returning invented numbers,
behind an `isDemo` gate so a real activation asked a question and got nothing.
Same category as the report's `1,287 visitors` and `/agents`' `fired: 488`, both
deleted rather than kept. Every figure here comes from the graph or the log.

## The model never sees a database and never writes a query

It picks a name from `app/llm/catalogue.py` and fills declared parameters.
`tenant_id` and `session_id` come from the verified credential and the path, and
are not parameters any entry declares — so no model output can reach another
tenant's activation. `docs/adr/003-nl-query-catalogue.md` has the argument.

## Every answer says what produced it

`basis` is the provider name, or `deterministic` when no key is configured — which
is the ordinary case while open decision 2 stands. An operator who cannot tell a
model's answer from a keyword match cannot judge either, so the surface renders
it and this endpoint always sends it.

## Who may ask

`require_ask`, not `require_reader`. `multi-tenant.md` §3 puts "query Ask" with
Analyst and gives Operator "run activations, see live dashboard" — and this
codebase reads that table as a deny-list, because a permission matrix where
omission grants nothing is the only kind that means anything. So an operator on
the floor cannot ask the room a question, which is the most surprising line in
the matrix and is recorded in §3 as a decision rather than left to look like an
oversight.

## A refusal is a 200

"I cannot answer that yet" is an answer, and the request succeeded. A 4xx would
make the browser unable to tell "we could not reach our own backend" from "that
question is outside what we measure", which are different things for an operator
to do next — the same reasoning `POST /v1/integrations/{provider}/test` gives.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import time
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app import llm, repository
from app.auth.principal import Principal, require_ask
from app.cost import meter
from app.db import get_session
from app.graph.driver import get_graph_session
from app.llm import catalogue, prompts
from app.llm.base import LlmError
from app.schemas import _to_camel

router = APIRouter(prefix="/v1/ask", tags=["ask"])


class AskIn(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    question: str = Field(min_length=1, max_length=500)
    session_id: str = Field(min_length=1)


class AskOut(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    question: str
    #: The answer in words, or the refusal. Always populated.
    answer: str
    #: Which catalogue entry served it, or None when nothing did. Shown to the
    #: operator, because "which measurement is this" is the first thing anybody
    #: sensible asks of a number they are about to repeat to a client.
    query: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    chart: str | None = None
    #: `deterministic` or the provider's name. Never absent.
    basis: str
    #: What this system *can* answer. Sent on a refusal, because a refusal with
    #: no alternative is a dead end.
    can_answer: list[dict[str, Any]] = Field(default_factory=list)
    took_ms: int = 0


@router.post("", response_model=AskOut, summary="Ask a question about a session")
async def ask(
    body: AskIn,
    principal: Principal = Depends(require_ask),
    session: AsyncSession = Depends(get_session),
    graph=Depends(get_graph_session),
) -> AskOut:
    started = time.monotonic()
    # Wall clock as well as the monotonic one: `took_ms` is a duration and this
    # is when the spend happened. They are not the same clock and neither can do
    # the other's job.
    asked_at = dt.datetime.now(dt.timezone.utc)

    integration = await repository.get_integration_of_kind(
        session, tenant_id=principal.tenant_id, kind=llm.KIND, active_only=True
    )
    provider = llm.provider_for(integration)
    basis = provider.provider

    # ── route ────────────────────────────────────────────────────────────────
    routed, tokens = await _route(provider, body.question)
    if not routed.get("query"):
        return AskOut(
            question=body.question,
            answer=routed.get("reason")
            or "I cannot answer that from what this activation measures.",
            basis=basis,
            can_answer=catalogue.menu(),
            took_ms=_since(started),
        )

    # ── execute ──────────────────────────────────────────────────────────────
    scope = catalogue.Scope(
        # From the credential and the path. Never from the model.
        tenant_id=principal.tenant_id,
        session_id=body.session_id,
        graph=graph,
        log=session,
    )
    try:
        entry, params, rows = await catalogue.execute(
            routed["query"], routed.get("params"), scope=scope
        )
    except (catalogue.UnknownQuestion, catalogue.BadParams) as exc:
        # The model named something that does not exist or filled it in wrongly.
        # Reported as a refusal rather than a 500: it is a bad answer from the
        # model, not a broken server, and the operator's next move is to rephrase.
        return AskOut(
            question=body.question,
            answer=str(exc),
            basis=basis,
            can_answer=catalogue.menu(),
            took_ms=_since(started),
        )

    # ── phrase ───────────────────────────────────────────────────────────────
    answer = entry.phrase(rows)
    if provider.capabilities().get("reasons"):
        try:
            said = await provider.complete(
                prompts.phrasing_prompt(
                    question=body.question, query=entry.name, rows=rows
                )
            )
            tokens += said.total_tokens
            if said.text.strip():
                answer = said.text.strip()
        except LlmError as exc:
            # The measurement succeeded and only the prose failed. The entry's
            # own phrasing is the floor precisely so a model outage costs an
            # operator style rather than an answer.
            answer = f"{answer} (phrased without the model: {exc})"

    if tokens:
        await meter(
            session,
            tenant_id=principal.tenant_id,
            session_id=body.session_id,
            kind="llm_tokens",
            amount=float(tokens),
            unit="tokens",
            # The work happened when the question was asked. `cost.py` warns
            # against `now()` because a replayed cost would land at the time of
            # the replay — but this is a request, not an event a consumer
            # reprocesses, and there is no replay of it to be wrong about.
            occurred_at=asked_at,
            # For the same reason the cause carries the question and the moment.
            # `cost.py` warns against a timestamp here because a derived id is
            # what makes a *replay* idempotent; the spender here is one request,
            # and `("ask", entry.name)` alone made every later question routing
            # to the same entry in the same activation collide with the first
            # and vanish — a client asking `visitor_count` ten times was billed
            # for one.
            cause=(
                "ask",
                entry.name,
                hashlib.sha256(body.question.strip().lower().encode()).hexdigest()[:16],
                asked_at.isoformat(),
            ),
            detail={"provider": basis, "query": entry.name},
        )
        await session.commit()

    return AskOut(
        question=body.question,
        answer=answer,
        query=entry.name,
        params=params,
        rows=rows,
        chart=entry.chart,
        basis=basis,
        took_ms=_since(started),
    )


@router.get("/catalogue", summary="What this system can be asked")
async def get_catalogue(
    principal: Principal = Depends(require_ask),
) -> dict[str, Any]:
    """The menu, for a UI that should suggest real questions.

    The suggestions on `/ask` used to be a hardcoded list beside a hardcoded set
    of answers. They come from the catalogue now, so a suggestion that cannot be
    answered is not something this codebase can express.
    """
    return {"queries": catalogue.menu()}


async def _route(provider, question: str) -> tuple[dict[str, Any], int]:
    """Ask the provider which measurement answers this. Never trusts the shape."""
    try:
        completion = await provider.complete(prompts.routing_prompt(question))
    except LlmError as exc:
        return (
            {"query": None, "reason": f"the AI provider could not answer: {exc}"},
            0,
        )

    try:
        routed = json.loads(_unfenced(completion.text))
    except ValueError:
        return (
            {
                "query": None,
                "reason": "the AI provider replied with something that is not JSON",
            },
            completion.total_tokens,
        )

    if not isinstance(routed, dict):
        return (
            {"query": None, "reason": "the AI provider did not reply with an object"},
            completion.total_tokens,
        )
    return routed, completion.total_tokens


def _unfenced(text: str) -> str:
    """The JSON out of a fenced code block, if the model wrapped it in one.

    Gemini 3.7 Flash answers the routing prompt with ```json … ``` every time,
    measured 2026-08-31; DeepSeek answers with bare JSON. Both are asked for JSON
    and neither is wrong — a fence is how a chat model marks a code block — so
    this belongs here, in the one place that parses a model's output, rather than
    in an adapter that would have to re-learn it per vendor.

    Prompting harder is the alternative and it is the weaker one: it fails
    silently and only for some models, and the failure looks like "I could not
    match that to anything I can measure" — a refusal an operator reads as the
    question being wrong.
    """
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    body = stripped[3:]
    if body[:4].lower().startswith("json"):
        body = body[4:]
    return body.rsplit("```", 1)[0].strip()


def _since(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
