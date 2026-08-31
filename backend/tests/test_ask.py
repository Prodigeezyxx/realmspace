"""
Ask the Room — the last open item of Phase 2, and Phase 5's Live Analyst.

The properties worth holding, in the order they matter.

**The model cannot reach another tenant's activation.** Not because a validator
catches it, but because `tenant_id` and `session_id` are not parameters any
catalogue entry declares — there is no model output that could set them. That is
`docs/adr/003-nl-query-catalogue.md`'s whole reason for existing, and the test
asserts the structural fact rather than a behaviour.

**Every figure is measured.** What this replaces returned invented numbers from
nine regexes behind a demo gate. Each answer here is checked against the graph it
came from.

**A question nobody anticipated is refused, with what it can answer instead.**
Never the nearest entry, never an empty result dressed as a zero.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from neo4j import AsyncSession as GraphSession
from sqlalchemy.ext.asyncio import AsyncSession

from app import repository
from app.llm import catalogue, fallback, prompts
from app.schemas import EventIn
from tests.test_handoff import (
    BASE,
    S,
    T,
    seed_activation,
    seed_person_with_a_path,
)


@pytest.fixture(autouse=True)
async def _scope_to_test_tenant(db_session: AsyncSession):
    from tests.conftest import as_tenant

    await as_tenant(db_session, T)
    yield


# §3 puts "query Ask" with Analyst. "reader" was never in `USER_ROLES` — the old gate admitted any
# human role by name, so an invalid one passed silently until the
# capability map made a role mean something.
async def _client(db_session: AsyncSession, role: str = "analyst") -> AsyncClient:
    from collections.abc import AsyncIterator

    from app.auth.models import AuthUser
    from app.auth.tokens import issue_token
    from app.db import get_session
    from app.main import app

    user_id = f"u_{role}_{T}"
    db_session.add(
        AuthUser(
            user_id=user_id,
            email=f"{user_id}@floats.demo",
            display_name=user_id,
            tenant_id=T,
            role=role,
        )
    )
    await db_session.commit()

    async def override() -> AsyncIterator[AsyncSession]:
        yield db_session
        await db_session.commit()

    app.dependency_overrides[get_session] = override
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={
            "Authorization": f"Bearer {issue_token(subject=user_id, tenant_id=T, role=role)}"
        },
    )


async def seed_room(db_session: AsyncSession, graph_session: GraphSession) -> None:
    """Two visitors with real paths, and the zone entries the log question needs."""
    await seed_activation(graph_session)
    for anon_id in ("P-001", "P-002"):
        await seed_person_with_a_path(graph_session, anon_id=anon_id)
        await repository.append_event(
            db_session,
            EventIn(
                event_id=uuid.uuid4(),
                tenant_id=T,
                session_id=S,
                type="spatial.zone_enter",
                payload={"anon_id": anon_id, "zone_id": "z_entry"},
                occurred_at=BASE + dt.timedelta(seconds=30),
            ),
        )
    await db_session.commit()


# ── the structural property ADR-003 exists for ────────────────────────────────


def test_no_catalogue_entry_lets_a_caller_name_the_tenant() -> None:
    """The scoping is not validated, it is unreachable.

    A model's entire output is a query name and declared parameters. If no entry
    declares `tenant_id` or `session_id`, no model output can set them — which is
    a property of the catalogue's shape rather than of any check that could be
    forgotten.
    """
    forbidden = {"tenant_id", "session_id", "tenantId", "sessionId"}
    for entry in catalogue.CATALOGUE.values():
        declared = {p.name for p in entry.params}
        assert not (declared & forbidden), entry.name

    for listed in catalogue.menu():
        assert not ({p["name"] for p in listed["params"]} & forbidden)


def test_an_entry_cannot_be_run_without_a_scope() -> None:
    """`Scope` is required positionally by every entry, so there is no call that
    forgets it and reads the whole tenant."""
    import inspect

    for entry in catalogue.CATALOGUE.values():
        signature = inspect.signature(entry.run)
        assert list(signature.parameters)[0] == "scope", entry.name


def test_a_parameter_the_entry_does_not_declare_is_refused() -> None:
    """A model that invented a filter has misunderstood the question, and running
    the unfiltered version would answer a different one convincingly."""
    entry = catalogue.get("dwell_by_zone")
    with pytest.raises(catalogue.BadParams, match="zone_id"):
        entry.coerce({"zone_id": "z_entry"})


def test_a_parameter_of_the_wrong_type_is_refused_with_the_reason() -> None:
    entry = catalogue.get("busiest_window")
    with pytest.raises(catalogue.BadParams, match="whole number"):
        entry.coerce({"minutes": "all"})
    with pytest.raises(catalogue.BadParams, match="more than"):
        entry.coerce({"minutes": 10_000})


def test_a_query_that_does_not_exist_names_the_ones_that_do() -> None:
    with pytest.raises(catalogue.UnknownQuestion, match="dwell_by_zone"):
        catalogue.get("DROP TABLE event_log")


# ── the deterministic provider ────────────────────────────────────────────────


def test_the_stub_routes_real_questions_and_refuses_the_rest() -> None:
    provider = fallback()

    assert provider.route("How many unique visitors today?")["query"] == "visitor_count"
    assert (
        provider.route("Which zone had the longest average dwell?")["query"]
        == "dwell_by_zone"
    )
    # A number the operator typed reaches the parameter it belongs to.
    assert provider.route("Show me the busiest 10 minutes.")["params"] == {
        "minutes": 10
    }

    refused = provider.route("what is the airspeed of an unladen swallow")
    assert refused["query"] is None
    assert "could not match" in refused["reason"]


def test_the_stub_reads_the_question_out_of_the_real_prompt() -> None:
    """One path through the system whichever provider is on the end of it: the
    same prompt is assembled either way."""
    provider = fallback()
    prompt = prompts.routing_prompt("How many unique visitors today?")
    assert "QUESTION:" in prompt
    assert provider.route(prompt.rsplit("QUESTION:", 1)[-1].strip())["query"] == (
        "visitor_count"
    )


async def test_the_stub_spends_nothing_and_says_so() -> None:
    """`cost.py` refuses to invent a price; zero here is a measurement, not a
    placeholder — no call was made."""
    completion = await fallback().complete(
        prompts.routing_prompt("How many unique visitors today?")
    )
    assert completion.total_tokens == 0
    assert json.loads(completion.text)["query"] == "visitor_count"


def test_the_stub_refuses_a_credential_rather_than_ignoring_one() -> None:
    """An admin storing a key against it has misunderstood what it is, and
    accepting the key would leave them believing a model is answering."""
    with pytest.raises(ValueError, match="takes no credential"):
        fallback().parse_secret("sk-something")


# ── end to end ────────────────────────────────────────────────────────────────


async def test_every_figure_comes_from_the_graph(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """The thing the nine regexes could not do."""
    await seed_room(db_session, graph_session)
    client = await _client(db_session)

    async with client:
        body = (
            await client.post(
                "/v1/ask",
                json={"question": "How many unique visitors today?", "sessionId": S},
            )
        ).json()

    from app.graph import repository as graph_repo

    measured = await graph_repo.people_in_session(
        graph_session, tenant_id=T, session_id=S
    )
    assert body["query"] == "visitor_count"
    assert body["rows"] == [{"visitors": measured}]
    assert str(measured) in body["answer"]
    assert body["basis"] == "deterministic"


async def test_the_dwell_answer_matches_the_graph(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    await seed_room(db_session, graph_session)
    client = await _client(db_session)

    async with client:
        body = (
            await client.post(
                "/v1/ask",
                json={
                    "question": "Which zone had the longest average dwell?",
                    "sessionId": S,
                },
            )
        ).json()

    from app.graph import repository as graph_repo

    measured = await graph_repo.dwell_by_zone(
        graph_session, tenant_id=T, session_id=S
    )
    assert [r["zone"] for r in body["rows"]] == [r["zone"] for r in measured]
    assert body["chart"] == "bar"


async def test_a_log_shaped_question_is_answered_from_the_log(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """"When was it busiest" is what `roadmap.md` means by "over the live bus" —
    the graph holds current state and cannot answer it."""
    await seed_room(db_session, graph_session)
    client = await _client(db_session)

    async with client:
        body = (
            await client.post(
                "/v1/ask",
                json={"question": "Show me the busiest 5 minutes.", "sessionId": S},
            )
        ).json()

    assert body["query"] == "busiest_window"
    assert body["rows"][0]["people"] == 2
    assert body["rows"][0]["window_minutes"] == 5


async def test_a_question_it_cannot_answer_says_so_and_offers_what_it_can(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """A refusal with no alternative is a dead end, and a guess is worse."""
    await seed_room(db_session, graph_session)
    client = await _client(db_session)

    async with client:
        response = await client.post(
            "/v1/ask",
            json={"question": "Which visitors are wearing red?", "sessionId": S},
        )

    # A refusal is a 200: the request succeeded and this is the answer.
    assert response.status_code == 200
    body = response.json()
    assert body["query"] is None
    assert body["rows"] == []
    assert {q["query"] for q in body["canAnswer"]} == set(catalogue.CATALOGUE)


async def test_an_empty_session_reads_as_empty_not_as_zero(
    db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """"We measured none" and "nothing has been recorded" are different answers —
    the rule `/live` and the report both follow."""
    await seed_activation(graph_session)
    client = await _client(db_session)

    async with client:
        body = (
            await client.post(
                "/v1/ask",
                json={
                    "question": "Which zone had the longest average dwell?",
                    "sessionId": S,
                },
            )
        ).json()

    assert body["rows"] == []
    assert "No dwell has been recorded" in body["answer"]


async def test_the_suggestions_come_from_the_catalogue(
    db_session: AsyncSession,
) -> None:
    """A suggested question that cannot be answered is not something this
    codebase should be able to express."""
    client = await _client(db_session)
    async with client:
        body = (await client.get("/v1/ask/catalogue")).json()

    assert {q["query"] for q in body["queries"]} == set(catalogue.CATALOGUE)
    for listed in body["queries"]:
        assert listed["examples"]


# ── with a real provider configured ───────────────────────────────────────────
#
# Open decision 2 closed on 2026-08-31: the key is OpenRouter's. What changes for
# `/ask` is exactly three things — the basis it reports, who chooses the query,
# and that the call now costs something. These pin all three, and the fourth
# property that matters: a model outage costs an operator style, not an answer.


async def _connect_openrouter(
    db_session: AsyncSession, *, model: str | None = None
) -> None:
    """Store a key the way `python -m app.llm.connect` does."""
    from app import llm, secrets

    key = "sk-or-v1-" + "0" * 8
    await repository.upsert_integration(
        db_session,
        tenant_id=T,
        provider="openrouter",
        secret_ct=secrets.encrypt(key, tenant_id=T, provider="openrouter"),
        secret_hint=secrets.hint(key),
        field_map={"model": model} if model else {},
        kind=llm.KIND,
    )
    await db_session.commit()


async def test_the_model_answers_and_the_answer_says_so(
    db_session: AsyncSession,
    graph_session: GraphSession,
    encryption_key: str,
    monkeypatch,
) -> None:
    """`basis` is what an operator reads to know which kind of thing answered.

    The figure is still the graph's. A model that routed and phrased is not a
    model that measured — ADR-003's whole point — so the count here is asserted
    against the store, exactly as it is for the deterministic provider.
    """
    from tests.llm_transport import completion, stub_transport

    await seed_room(db_session, graph_session)
    await _connect_openrouter(db_session)

    prompts_seen: list[str] = []

    def handler(request):
        import httpx as _httpx

        sent = json.loads(request.content)
        prompts_seen.append(sent["messages"][0]["content"])
        if "QUESTION:" in sent["messages"][0]["content"] and len(prompts_seen) == 1:
            return _httpx.Response(
                200, json=completion('{"query": "visitor_count", "params": {}}')
            )
        return _httpx.Response(200, json=completion("Nine people came through."))

    stub_transport(monkeypatch, handler)
    client = await _client(db_session)

    async with client:
        body = (
            await client.post(
                "/v1/ask",
                json={"question": "how many people came?", "sessionId": S},
            )
        ).json()

    from app.graph import repository as graph_repo

    measured = await graph_repo.people_in_session(
        graph_session, tenant_id=T, session_id=S
    )
    assert body["basis"] == "openrouter"
    assert body["query"] == "visitor_count"
    assert body["rows"] == [{"visitors": measured}]
    assert body["answer"] == "Nine people came through."
    assert len(prompts_seen) == 2, "one call to route, one to phrase"


async def test_the_model_never_sees_the_tenant_or_the_session(
    db_session: AsyncSession,
    graph_session: GraphSession,
    encryption_key: str,
    monkeypatch,
) -> None:
    """`base.py` states this as a fact about the architecture. This is the check.

    A provider is text-in, text-out with no access to a store; `tenant_id` and
    `session_id` are injected by the caller and are not parameters any entry
    declares. So neither should appear in anything we send a vendor — and the
    outbound body is the only place that could be falsified by a later change.
    """
    from tests.llm_transport import completion, stub_transport

    await seed_room(db_session, graph_session)
    await _connect_openrouter(db_session)

    bodies: list[str] = []

    def handler(request):
        import httpx as _httpx

        bodies.append(request.content.decode())
        return _httpx.Response(
            200, json=completion('{"query": "visitor_count", "params": {}}')
        )

    stub_transport(monkeypatch, handler)
    client = await _client(db_session)

    async with client:
        await client.post(
            "/v1/ask", json={"question": "how many people came?", "sessionId": S}
        )

    assert bodies
    for sent in bodies:
        assert T not in sent
        assert S not in sent


async def test_the_call_is_metered_with_the_vendors_own_counts(
    db_session: AsyncSession,
    graph_session: GraphSession,
    encryption_key: str,
    monkeypatch,
) -> None:
    """`app/cost.py` reserved `llm_tokens` in Phase 3 and named Ask as the
    missing spender. It is not missing any more, and the number is the vendor's."""
    from tests.llm_transport import completion, stub_transport

    await seed_room(db_session, graph_session)
    await _connect_openrouter(db_session)

    stub_transport(
        monkeypatch,
        lambda request: __import__("httpx").Response(
            200,
            json=completion(
                '{"query": "visitor_count", "params": {}}',
                prompt_tokens=100,
                completion_tokens=25,
            ),
        ),
    )
    client = await _client(db_session)

    async with client:
        await client.post(
            "/v1/ask", json={"question": "how many people came?", "sessionId": S}
        )

    metered = [
        event
        for event in await repository.read_events(db_session, tenant_id=T, limit=500)
        if event.type == "cost.metered"
    ]
    assert metered, "an Ask that called a model must appear on the ledger"
    payload = metered[-1].payload
    assert payload["kind"] == "llm_tokens"
    # 125 per call, twice: routing and phrasing.
    assert payload["amount"] == 250.0
    assert payload["detail"]["provider"] == "openrouter"


async def test_a_model_outage_costs_style_and_not_the_answer(
    db_session: AsyncSession,
    graph_session: GraphSession,
    encryption_key: str,
    monkeypatch,
) -> None:
    """ADR-003: the entry's own `phrase()` is the floor for the phrasing job.

    The measurement succeeded; only the prose failed. An operator gets the
    number and a sentence saying why it reads plainly.
    """
    from tests.llm_transport import completion, stub_transport

    await seed_room(db_session, graph_session)
    await _connect_openrouter(db_session)

    calls = {"n": 0}

    def handler(request):
        import httpx as _httpx

        calls["n"] += 1
        if calls["n"] == 1:
            return _httpx.Response(
                200, json=completion('{"query": "visitor_count", "params": {}}')
            )
        return _httpx.Response(503, text="upstream unavailable")

    stub_transport(monkeypatch, handler)
    client = await _client(db_session)

    async with client:
        body = (
            await client.post(
                "/v1/ask",
                json={"question": "how many people came?", "sessionId": S},
            )
        ).json()

    from app.graph import repository as graph_repo

    measured = await graph_repo.people_in_session(
        graph_session, tenant_id=T, session_id=S
    )
    assert body["rows"] == [{"visitors": measured}]
    assert str(measured) in body["answer"]
    assert "phrased without the model" in body["answer"]


async def test_a_model_that_fences_its_json_is_still_understood(
    db_session: AsyncSession,
    graph_session: GraphSession,
    encryption_key: str,
    monkeypatch,
) -> None:
    """Gemini 3.7 Flash wraps the routing object in ```json every time.

    Measured live 2026-08-31. Without `_unfenced` the answer is "the AI provider
    replied with something that is not JSON" — a refusal the operator reads as
    their question being wrong, for a model that routed it correctly.
    """
    from tests.llm_transport import completion, stub_transport

    await seed_room(db_session, graph_session)
    await _connect_openrouter(db_session, model="google/gemini-3.7-flash")

    fenced = '```json\n{\n  "query": "visitor_count",\n  "params": {}\n}\n```'
    stub_transport(
        monkeypatch,
        lambda request: __import__("httpx").Response(200, json=completion(fenced)),
    )
    client = await _client(db_session)

    async with client:
        body = (
            await client.post(
                "/v1/ask",
                json={"question": "how many people came?", "sessionId": S},
            )
        ).json()

    assert body["query"] == "visitor_count"
    assert body["rows"], "a fenced answer must measure, not refuse"


async def test_a_dead_key_refuses_rather_than_falling_back_to_the_matcher(
    db_session: AsyncSession,
    graph_session: GraphSession,
    encryption_key: str,
    monkeypatch,
) -> None:
    """The failure mode `stub.py` exists to prevent, from the other direction.

    A tenant who configured a provider must not be silently downgraded to
    keyword matching: `basis` would still read `openrouter` while a matcher
    answered, and the answer's stated basis would be false.
    """
    from tests.llm_transport import stub_transport

    await seed_room(db_session, graph_session)
    await _connect_openrouter(db_session)

    stub_transport(
        monkeypatch,
        lambda request: __import__("httpx").Response(401, text="key revoked"),
    )
    client = await _client(db_session)

    async with client:
        body = (
            await client.post(
                "/v1/ask",
                json={"question": "how many people came?", "sessionId": S},
            )
        ).json()

    assert body["basis"] == "openrouter"
    assert body["query"] is None
    assert "rejected the credential" in body["answer"]
    assert body["canAnswer"], "a refusal still says what it could answer"
