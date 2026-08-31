"""
The one test that calls OpenRouter for real. Skipped unless asked for.

Everything else about this adapter is asserted against a stubbed transport, which
is the right default: a suite that needed a vendor to be up would fail for
reasons that are not about this code, and it would spend a shared key on every
run. But five CRM adapters are already in the position of being proven only
against the API their vendor documents, and `roadmap.md` says so in the open
decisions rather than pretending otherwise. This is the one place that closes the
gap for the model provider, and it is run by hand:

    OPENROUTER_LIVE_KEY=sk-or-v1-… .venv/bin/python -m pytest tests/test_openrouter_live.py -q -s

What it proves that a mock cannot: the slugs are real, the key is accepted, the
response shape is the one the adapter parses, reasoning tokens are inside
`completion_tokens`, and the whole `/ask` path — route, measure, phrase, meter —
completes against a live model within the latency the endpoint reports.
"""

from __future__ import annotations

import os

import pytest
from neo4j import AsyncSession as GraphSession
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.openrouter import ALLOWED, BATCH_ONLY, OpenRouterProvider
from tests.test_ask import _client, _connect_openrouter, seed_room
from tests.test_handoff import S, T  # noqa: F401  — S and T are the seeded ids

LIVE_KEY = os.environ.get("OPENROUTER_LIVE_KEY", "")

pytestmark = pytest.mark.skipif(
    not LIVE_KEY,
    reason="set OPENROUTER_LIVE_KEY to run the live smoke against OpenRouter",
)


@pytest.fixture(autouse=True)
async def _scope_to_test_tenant(db_session: AsyncSession):
    """RLS fails closed, so an unscoped seed is refused rather than misplaced."""
    from tests.conftest import as_tenant

    await as_tenant(db_session, T)
    yield


async def test_the_key_reaches_the_account() -> None:
    provider = OpenRouterProvider.for_tenant(secret=LIVE_KEY, config={})
    ok, detail = await provider.healthcheck()
    print(f"\n  healthcheck: {detail}")
    assert ok, detail


@pytest.mark.parametrize("slug", sorted(ALLOWED))
async def test_every_slug_on_the_allow_list_is_a_real_model(slug: str) -> None:
    """The screenshot gave display names. This is what says the slugs are right.

    A slug that has been renamed by the vendor fails here rather than at an
    operator's first question.
    """
    provider = OpenRouterProvider.for_tenant(secret=LIVE_KEY, config={"model": slug})
    said = await provider.complete(
        'Reply with exactly this JSON and nothing else: {"ok": true}', max_tokens=64
    )
    print(
        f"\n  {slug}: {said.input_tokens} in / {said.output_tokens} out — "
        f"{said.text.strip()[:80]!r}"
    )
    assert "ok" in said.text
    assert said.output_tokens > 0, "reasoning is billed as output; this cannot be zero"


@pytest.mark.parametrize("slug", sorted(BATCH_ONLY))
async def test_the_batch_model_still_cannot_be_called_synchronously(slug: str) -> None:
    """The reason `BATCH_ONLY` exists, checked against the vendor rather than
    remembered. If OpenRouter ever puts this model on `/chat/completions`, this
    fails and the slug moves up into `ALLOWED`."""
    provider = OpenRouterProvider.for_tenant(secret=LIVE_KEY, config={})
    provider._config = {"model": slug}  # past the adapter's own guard, on purpose

    import httpx

    from app.llm import http as llm_http

    async with httpx.AsyncClient(base_url="https://openrouter.ai/api/v1") as client:
        response = await client.post(
            "/chat/completions",
            headers={"Authorization": f"Bearer {LIVE_KEY}"},
            json={
                "model": slug,
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 16,
            },
            timeout=llm_http.get_settings().llm_timeout_seconds,
        )
    print(f"\n  {slug}: {response.status_code} {response.text[:160]}")
    assert response.status_code == 404
    assert "Batch API" in response.text


async def test_a_real_model_answers_a_real_activation(
    db_session: AsyncSession,
    graph_session: GraphSession,
    encryption_key: str,
    monkeypatch,
) -> None:
    """The whole path, with nothing stubbed but the floor the visitors walked."""
    from app import llm, secrets

    await seed_room(db_session, graph_session)
    # The live key, stored the way `python -m app.llm.connect` stores one.
    from app import repository

    await repository.upsert_integration(
        db_session,
        tenant_id=T,
        provider="openrouter",
        secret_ct=secrets.encrypt(LIVE_KEY, tenant_id=T, provider="openrouter"),
        secret_hint=secrets.hint(LIVE_KEY),
        field_map={},
        kind=llm.KIND,
    )
    await db_session.commit()

    client = await _client(db_session)
    async with client:
        body = (
            await client.post(
                "/v1/ask",
                json={"question": "how many people came through?", "sessionId": S},
            )
        ).json()

    print(
        f"\n  basis={body['basis']} query={body['query']} "
        f"took={body['tookMs']}ms\n  answer: {body['answer']}\n  rows: {body['rows']}"
    )

    from app.graph import repository as graph_repo

    measured = await graph_repo.people_in_session(
        graph_session, tenant_id=T, session_id=S
    )
    assert body["basis"] == "openrouter"
    assert body["query"] == "visitor_count"
    assert body["rows"] == [{"visitors": measured}]

    # The sentence is the model's and the figure in it is still the graph's —
    # but a model writes "Two visitors came through", so the check is that the
    # count is *there*, not that it is a digit. What would be a real failure is a
    # number in the sentence that is not the measurement, and that is the second
    # assertion: any digit in the answer must be the one we measured.
    import re

    words = {
        0: "zero", 1: "one", 2: "two", 3: "three", 4: "four",
        5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine",
    }
    lowered = body["answer"].lower()
    assert str(measured) in lowered or words.get(measured, "\0") in lowered
    assert set(re.findall(r"\d+", lowered)) <= {str(measured)}, (
        "a figure in the answer that is not the measurement is an invented number"
    )
