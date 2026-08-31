"""
The OpenRouter adapter — what ADR-003 says an adapter owes, asserted.

Four properties, in the order they would hurt:

**A cost we did not measure is never reported.** `app/cost.py` refuses to invent
a cost per action and the stub's zero is honest because it makes no call. A
provider returning zeros for a call it actually made would put a made-up figure
on the unit-economics tile, so a reply with no `usage` is an error.

**A dead key and a rate limit are different sentences.** `retryable` is what
`/ops` renders for a human; a 401 that read as retryable would have somebody
waiting for a key to start working again.

**Reasoning is mandatory on every model this key allows**, so the caller's
`max_tokens` is the budget for the answer and the headroom is added on top. The
regression this guards is real and was measured before the adapter existed: 16
tokens of budget came back `content: null`, `finish_reason: "length"`.

**A model outside the allow-list fails here, not at the vendor.** With a sentence
naming what is permitted, rather than a 4xx an operator reads as an outage.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.llm.base import Completion, LlmError
from app.llm.openrouter import (
    ALLOWED,
    BATCH_ONLY,
    DEFAULT_MODEL,
    REASONING_HEADROOM,
    OpenRouterProvider,
)
from tests.llm_transport import completion, stub_transport

KEY = "sk-or-v1-" + "0" * 8


def _provider(**config) -> OpenRouterProvider:
    return OpenRouterProvider.for_tenant(secret=KEY, config=config)  # type: ignore[return-value]


# ── the credential ────────────────────────────────────────────────────────────


def test_a_key_for_another_vendor_is_refused_before_it_is_stored() -> None:
    """`connect` calls this, so the error names the vendor rather than 401ing later."""
    with pytest.raises(LlmError) as caught:
        OpenRouterProvider.parse_secret("sk-ant-api03-something")
    assert "sk-or-" in str(caught.value)
    assert caught.value.retryable is False


def test_an_empty_credential_is_refused() -> None:
    with pytest.raises(LlmError):
        OpenRouterProvider.parse_secret("   ")


def test_the_adapter_says_a_model_answered() -> None:
    """`capabilities()["reasons"]` is what every surface renders as the basis.

    The stub reports False. If this were False too, an operator could not tell a
    model's answer from a keyword match — which is the distinction `stub.py`
    exists to keep visible.
    """
    assert OpenRouterProvider.capabilities()["reasons"] is True


# ── the model ─────────────────────────────────────────────────────────────────


def test_a_model_outside_the_allow_list_is_refused_by_name(monkeypatch) -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json=completion("{}"))

    stub_transport(monkeypatch, handler)
    with pytest.raises(LlmError) as caught:
        _ = _provider(model="openai/gpt-4o").model

    assert "allow-list" in str(caught.value)
    assert DEFAULT_MODEL in str(caught.value)
    assert caught.value.retryable is False
    assert not called, "the refusal must happen before the request goes out"


def test_the_default_is_the_cheaper_model() -> None:
    """Measured 2026-08-31: $0.0000167 a routing call against Gemini's $0.00096.

    Cost rather than latency. `event-bus-spec.md` §4's `< 3s` belongs to the
    rules engine, not to a person typing a question, and neither model would meet
    it — both reason before answering and neither lets that be disabled.
    """
    assert _provider().model == "deepseek/deepseek-v4-flash-0731"
    assert DEFAULT_MODEL in ALLOWED


def test_a_tenant_can_choose_another_permitted_model() -> None:
    assert _provider(model="google/gemini-3.7-flash").model == "google/gemini-3.7-flash"


def test_the_batch_model_is_refused_with_the_reason_it_cannot_work() -> None:
    """Verified live 2026-08-31: `/chat/completions` answers 404 for this slug.

    It is on the key's allow-list, so "not permitted" would be a false
    explanation and would send somebody to ask the account owner for access they
    already have. The Batch API is a different protocol, not a permission.
    """
    slug = "deepseek/deepseek-v4-pro-0813:batch"
    assert slug in BATCH_ONLY and slug not in ALLOWED

    with pytest.raises(LlmError) as caught:
        _ = _provider(model=slug).model
    assert "Batch API" in str(caught.value)
    assert caught.value.retryable is False


# ── the call ──────────────────────────────────────────────────────────────────


async def test_the_answer_budget_is_the_answer_budget(monkeypatch) -> None:
    """Reasoning cannot be disabled on these models, so it is added on top.

    Live, `{"reasoning": {"enabled": false}}` answers 400 "Reasoning is mandatory
    for this endpoint and cannot be disabled." A caller asking for 512 tokens of
    answer must not receive 512 tokens of thinking and an empty string.
    """
    sent: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent.update(json.loads(request.content))
        return httpx.Response(200, json=completion('{"query": "unique_visitors"}'))

    stub_transport(monkeypatch, handler)
    await _provider().complete("who came?", max_tokens=512)

    assert sent["max_tokens"] == 512 + REASONING_HEADROOM
    assert sent["model"] == DEFAULT_MODEL
    assert sent["messages"] == [{"role": "user", "content": "who came?"}]


async def test_the_token_counts_are_the_vendors_own(monkeypatch) -> None:
    stub_transport(
        monkeypatch,
        lambda request: httpx.Response(
            200, json=completion("ok", prompt_tokens=130, completion_tokens=46)
        ),
    )
    said = await _provider().complete("anything")

    assert said == Completion(text="ok", input_tokens=130, output_tokens=46)
    assert said.total_tokens == 176


async def test_reasoning_tokens_are_inside_the_metered_count(monkeypatch) -> None:
    """They are billed as output and counted in `completion_tokens`.

    `llm_tokens` on the ledger is what the client is charged for, so the number
    we meter includes what the model spent thinking. Asserted because the
    tempting "correction" — subtracting reasoning to report only visible output —
    would understate every bill.
    """
    stub_transport(
        monkeypatch,
        lambda request: httpx.Response(
            200, json=completion("ok", completion_tokens=46)
        ),
    )
    said = await _provider().complete("anything")
    assert said.output_tokens == 46  # 34 of them reasoning, per the fixture


async def test_a_reply_with_no_usage_is_an_error_not_a_free_call(monkeypatch) -> None:
    """See `app/cost.py`: a default is a number made up on a client's behalf."""
    payload = completion("ok")
    del payload["usage"]
    stub_transport(monkeypatch, lambda request: httpx.Response(200, json=payload))

    with pytest.raises(LlmError, match="usage"):
        await _provider().complete("anything")


async def test_a_budget_spent_reasoning_says_so(monkeypatch) -> None:
    """The measured failure, before the headroom existed: `content: null`."""
    payload = completion("ok")
    payload["choices"][0]["message"]["content"] = None
    payload["choices"][0]["finish_reason"] = "length"
    stub_transport(monkeypatch, lambda request: httpx.Response(200, json=payload))

    with pytest.raises(LlmError) as caught:
        await _provider().complete("anything")
    assert "reasoning" in str(caught.value)
    assert caught.value.retryable is True


# ── failure ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("status", "retryable"),
    [(429, True), (500, True), (503, True), (401, False), (403, False), (402, False), (400, False)],
)
async def test_failures_carry_the_right_sentence(monkeypatch, status, retryable) -> None:
    stub_transport(
        monkeypatch, lambda request: httpx.Response(status, text="upstream said no")
    )
    with pytest.raises(LlmError) as caught:
        await _provider().complete("anything")
    assert caught.value.retryable is retryable


async def test_a_402_names_the_credit_rather_than_the_key(monkeypatch) -> None:
    """The one an operator fixes without touching the key."""
    stub_transport(monkeypatch, lambda request: httpx.Response(402, text="insufficient"))
    with pytest.raises(LlmError, match="credit"):
        await _provider().complete("anything")


async def test_an_error_inside_a_200_is_still_an_error(monkeypatch) -> None:
    """OpenRouter answers this way when an upstream model fails mid-request.

    A caller checking only the status would read the absent `choices` as a crash
    here rather than as the vendor's own reported failure.
    """
    stub_transport(
        monkeypatch,
        lambda request: httpx.Response(
            200, json={"error": {"code": 502, "message": "upstream is down"}}
        ),
    )
    with pytest.raises(LlmError) as caught:
        await _provider().complete("anything")
    assert "upstream is down" in str(caught.value)
    assert caught.value.retryable is True


async def test_the_network_failing_is_retryable(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    stub_transport(monkeypatch, handler)
    with pytest.raises(LlmError) as caught:
        await _provider().complete("anything")
    assert caught.value.retryable is True


# ── healthcheck ───────────────────────────────────────────────────────────────


async def test_the_healthcheck_reports_what_a_shared_key_has_spent(monkeypatch) -> None:
    """Costs no tokens, and answers the question a three-person key raises."""
    stub_transport(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            json={"data": {"label": "sk-or-v1-1c0...c58", "usage": 0.42, "limit": None}},
        ),
    )
    ok, detail = await _provider().healthcheck()

    assert ok is True
    assert "0.42" in detail
    assert "no spend limit set" in detail


async def test_a_failing_healthcheck_returns_the_reason_rather_than_raising(
    monkeypatch,
) -> None:
    """`(ok, detail)` is the contract — a raise here would break `connect check`."""
    stub_transport(monkeypatch, lambda request: httpx.Response(401, text="revoked"))
    ok, detail = await _provider().healthcheck()

    assert ok is False
    assert "rejected the credential" in detail
