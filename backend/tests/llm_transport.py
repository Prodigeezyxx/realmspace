"""
One seam for stubbing an outbound model call.

`app/llm/http.py` is the only module in the LLM package that imports `httpx`, so
replacing its `httpx` intercepts every provider's traffic. Patched at that module
rather than at `httpx.AsyncClient`, so nothing else in the suite — the webhook
delivery, the rule actions, the CRM adapters — sees a changed httpx.

The same file as `tests/crm_transport.py`, pointed at the other package. Kept
separate rather than parameterised: a test that stubbed both at once would hide
which seam it was exercising.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx

from app.llm import http as llm_http


def stub_transport(monkeypatch, handler: Callable[[httpx.Request], httpx.Response]) -> None:
    """Route every provider request through `handler`, with no network."""

    class _Httpx:
        HTTPError = httpx.HTTPError

        @staticmethod
        def AsyncClient(**kwargs):  # noqa: N802 — mirrors httpx's own name
            return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(llm_http, "httpx", _Httpx)


def completion(
    text: str, *, prompt_tokens: int = 11, completion_tokens: int = 16
) -> dict:
    """An OpenRouter chat completion, in the shape the live API returns one.

    Copied from a real 2026-08-31 response rather than invented, including
    `completion_tokens_details.reasoning_tokens` — reasoning is mandatory on
    every model this key allows, and a fixture without it would let a change that
    mishandles the reasoning budget pass.
    """
    return {
        "id": "gen-test",
        "model": "deepseek/deepseek-v4-flash-0731",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": text},
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "cost": 2.89e-06,
            "completion_tokens_details": {"reasoning_tokens": 34},
        },
    }
