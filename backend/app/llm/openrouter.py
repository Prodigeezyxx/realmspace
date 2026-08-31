"""
OpenRouter — the provider `roadmap.md` open decision 2 waited for.

Chosen 2026-08-31 because it is what we were given: one key, three models on an
account-level allow-list, and one OpenAI-shaped API in front of the two of them
this system can actually call. That last part is why it closes the decision
rather than postponing it — swapping the model behind Ask is a string in
`field_map`, not another adapter.

**Two of the three, not three.** `deepseek/deepseek-v4-pro-0813:batch` is on the
key's list and answers 404 on `/chat/completions`: it exists only behind
OpenRouter's Batch API. See `BATCH_ONLY` below.

## What the allow-list here is for

The key's own allow-list is enforced by OpenRouter, so a model outside it fails
anyway. `ALLOWED` restates it so the failure happens **before** the request, with
a sentence naming what is permitted, rather than as a 4xx a tenant reads as an
outage. It is a copy of somebody else's list and will drift; `healthcheck` reads
the account's own limits so the drift is visible rather than assumed.

## Every model on this list reasons, and it cannot be turned off

`{"reasoning": {"enabled": false}}` answers **400 "Reasoning is mandatory for
this endpoint and cannot be disabled."** on both Flash models. That is not a
detail about billing — it decides whether a call returns anything at all. A
`max_tokens` the caller meant for the answer is spent thinking first, and the
call comes back `finish_reason: "length"` with `content: null`.

Measured 2026-08-31 on `routing_prompt`: 233 reasoning tokens on Gemini 3.7
Flash, 34 on DeepSeek V4 Flash 0731, against a `complete()` default of 512 for
the whole completion. So the caller's `max_tokens` is treated as the budget for
the *answer*, and `REASONING_HEADROOM` is added on top of it before the request
goes out. A caller asking for 512 tokens of answer gets 512 tokens of answer.

How much reasoning a prompt provokes is not predictable, and not even stable for
one prompt — `REASONING_HEADROOM` records six samples of one input spanning 0 to
3906 tokens. The headroom is a ceiling, not an estimate.

## Why DeepSeek V4 Flash 0731 is the default

Measured 2026-08-31 on the real `routing_prompt`, both routing correctly:

| model | route | phrase | cost per routing call |
|---|---|---|---|
| `deepseek/deepseek-v4-flash-0731` | 5.9s | 3.6s | $0.0000167 |
| `google/gemini-3.7-flash` | 2.1s | 3.7s | $0.00096 |

**Cost is the deciding number, not latency.** `event-bus-spec.md` §4's `< 3s` is
the *rules engine's* budget — a detection reaching a staff prompt — and `/ask` is
not on it: it is a person who typed a question and is watching a spinner. Neither
model would meet 3s anyway, because both reason before answering and neither lets
that be turned off.

So the default is the one that costs 58× less for the same routing decision, and
`took_ms` on every answer is what says how long it actually took. A tenant who
prefers Gemini sets `field_map.model`.

## Token counts are the vendor's, or the call failed

`base.py` and ADR-003 both say it: a provider that cannot report what a call cost
leaves `app/cost.py` metering a made-up number on a unit-economics tile. A reply
with no `usage` is therefore an error rather than a `Completion` with zeros —
the stub's zero is honest because it makes no call, and this one would not be.

Reasoning tokens are billed as output and are counted in `completion_tokens`, so
`llm_tokens` on the ledger already includes what the model spent thinking. That
is the number the client is charged for, and it is the number we meter.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from app.llm import register
from app.llm.base import Completion, LlmError, LlmProvider
from app.llm.http import LlmHttp

log = logging.getLogger(__name__)

BASE_URL = "https://openrouter.ai/api/v1"

#: What this key permits *and this system can call*, as OpenRouter's own slugs —
#: the screenshot's names are display names and none of them is the slug.
ALLOWED: dict[str, str] = {
    "deepseek/deepseek-v4-flash-0731": "DeepSeek V4 Flash 0731",
    "google/gemini-3.7-flash": "Gemini 3.7 Flash",
}

#: Permitted by the key and unreachable from here. Asked for on
#: `/chat/completions`, `deepseek/deepseek-v4-pro-0813:batch` answers **404 "This
#: model is only available through the Batch API. Use the /api/beta/batches
#: endpoint instead."** (verified live, 2026-08-31).
#:
#: That is not a slug to correct — it is a different protocol: a job is
#: submitted, and the answer is collected later. Nothing in this system is shaped
#: that way. `/ask` is a person waiting for a sentence, and even the insight
#: consumer wants its completion inline so the `insight.generated` event carries
#: the text. So the third of the three models we were given is listed here, by
#: name, with the reason — rather than silently dropped, which would leave
#: somebody re-deriving this from a 404.
BATCH_ONLY: dict[str, str] = {
    "deepseek/deepseek-v4-pro-0813:batch": "DeepSeek V4 Pro 0813 (batch)",
}

DEFAULT_MODEL = "deepseek/deepseek-v4-flash-0731"

#: Added to the caller's `max_tokens`, because reasoning cannot be disabled and
#: is drawn from the same budget.
#:
#: **8000, and the number is large on purpose.** It was 1024 — four times the
#: largest trace measured on the routing prompt — until the SDR walk on
#: 2026-08-31 found a draft that returned nothing at all. Sampling the *same*
#: prompt six times explains why a headroom cannot be tuned:
#:
#:     deepseek/deepseek-v4-flash-0731   0, 178, 311, 846, 851, 3906
#:     google/gemini-3.7-flash           617, 629, 673, 688, 618, 626
#:
#: Identical input, and DeepSeek's reasoning spans nought to nearly four
#: thousand. There is no headroom that is both tight and safe, so this is not
#: tuned to that distribution — it is a ceiling well above it.
#:
#: **A ceiling is not a reservation.** `max_tokens` bills for tokens actually
#: produced, so a generous one costs nothing on the calls that do not need it;
#: on the calls that do, the alternative was paying for the reasoning and
#: receiving no answer, which is the worst of both. What stops a runaway is this
#: ceiling together with `llm_monthly_token_budget`, which counts what was
#: actually spent.
REASONING_HEADROOM = 8000

#: OpenRouter attributes traffic by these and shows them on the account's
#: activity page. A shared key with three people on it is exactly the case where
#: "which app spent this" is worth being able to answer.
ATTRIBUTION = {
    "HTTP-Referer": "https://realmspace.io",
    "X-Title": "realmspace",
}


@register
class OpenRouterProvider(LlmHttp, LlmProvider):
    """One tenant's key, one model, text in and text out."""

    provider: ClassVar[str] = "openrouter"

    @classmethod
    def capabilities(cls) -> dict[str, Any]:
        """`reasons` is True, and it is what every surface renders as the basis.

        `streams` stays False: OpenRouter supports SSE, but nothing here reads a
        stream — `/ask` needs the whole JSON routing object before it can do
        anything at all, and a half-parsed one is not a partial answer.
        """
        return {"reasons": True, "streams": False}

    @classmethod
    def parse_secret(cls, raw: str) -> dict[str, Any]:
        """Refuse a key that is not one, at `connect` time rather than at ask time."""
        raw = raw.strip()
        if not raw:
            raise LlmError("the credential is empty", retryable=False)
        if not raw.startswith("sk-or-"):
            raise LlmError(
                "an OpenRouter key starts with 'sk-or-' — this looks like a key "
                "for a different vendor",
                retryable=False,
            )
        return {"token": raw}

    def _base_url(self) -> str:
        return BASE_URL

    async def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._secret}", **ATTRIBUTION}

    @property
    def model(self) -> str:
        """The tenant's model, checked before it reaches the wire.

        `field_map` is a JSONB blob a tenant controls, so this is where a typo
        becomes a named refusal instead of a 400 from a vendor that an operator
        reads as us being down.
        """
        chosen = (self._config.get("model") or DEFAULT_MODEL).strip()
        if chosen in BATCH_ONLY:
            raise LlmError(
                f"{chosen!r} is on this key's allow-list but is reachable only "
                "through OpenRouter's Batch API (/api/beta/batches), which submits "
                "a job and collects the answer later. Nothing here is shaped that "
                "way — Ask is somebody waiting for a sentence. Use "
                + " or ".join(sorted(ALLOWED)),
                retryable=False,
            )
        if chosen not in ALLOWED:
            raise LlmError(
                f"{chosen!r} is not on this key's allow-list. Permitted: "
                + ", ".join(f"{slug} ({name})" for slug, name in ALLOWED.items()),
                retryable=False,
            )
        return chosen

    async def complete(self, prompt: str, *, max_tokens: int = 512) -> Completion:
        payload = await self._call(
            "POST",
            "/chat/completions",
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens + REASONING_HEADROOM,
            },
        )

        choices = payload.get("choices") or []
        if not choices:
            raise LlmError(
                f"{self.provider} returned no choices for {self.model}", retryable=True
            )
        choice = choices[0]
        text = (choice.get("message") or {}).get("content") or ""

        if not text.strip():
            # The mandatory-reasoning failure, named as itself. Retryable because
            # a longer budget or a second attempt usually answers, and because
            # `routers/ask.py` turns this into a sentence rather than a 500.
            raise LlmError(
                f"{self.model} spent its budget reasoning and returned no answer "
                f"(finish_reason: {choice.get('finish_reason')})",
                retryable=True,
            )

        usage = payload.get("usage")
        if not isinstance(usage, dict):
            raise LlmError(
                f"{self.provider} answered without usage counts, so this call "
                "cannot be metered — see app/cost.py on invented figures",
                retryable=True,
            )

        return Completion(
            text=text,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
        )

    async def healthcheck(self) -> tuple[bool, str]:
        """`GET /key` — the account's own view of this credential.

        Costs no tokens, and reports the two things worth knowing about a key
        that three people share: what it has spent, and whether anybody has put
        a ceiling on it.
        """
        try:
            payload = await self._call("GET", "/key")
        except LlmError as exc:
            return False, str(exc)

        data = payload.get("data") or {}
        limit = data.get("limit")
        ceiling = f"${limit}" if limit is not None else "no spend limit set"
        return True, (
            f"{data.get('label', 'this key')} reaches OpenRouter; "
            f"${data.get('usage', 0)} spent, {ceiling}. Model: {self.model}."
        )


__all__ = ["ALLOWED", "BATCH_ONLY", "DEFAULT_MODEL", "OpenRouterProvider"]
