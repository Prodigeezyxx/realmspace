"""
Consent capture, as executable claims.

Two properties carry the whole endpoint. The first is that a retry cannot
produce a second consent record — a kiosk on conference wifi will resend, and
two records for one conversation differing only in id is precisely the state
that makes "what did they actually agree to?" unanswerable. The second is that
capture writes to the log and nowhere else: no Contact, no graph, no identity.

The rest is shape, and shape matters here more than usual because the payload is
the audit trail.
"""

from __future__ import annotations

import datetime as dt

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app import repository
from tests.conftest import TENANT

S = "s_consent"


def capture_body(**over) -> dict:
    body = {
        "consentId": "c_0001",
        "sessionId": S,
        "anonId": "P-012",
        "tier": "T1",
        "basis": "explicit_optin",
        "copyVersion": "consent-en-2026-08",
        "capturedBy": "kiosk-entrance",
        "source": "qr",
    }
    body.update(over)
    return body


async def events_of(db_session: AsyncSession, type: str) -> list:
    return await repository.read_events(
        db_session, tenant_id=TENANT, session_id=S, type=type, limit=100
    )


async def test_a_capture_lands_on_the_log_exactly_as_given(
    client: AsyncClient, db_session: AsyncSession
):
    response = await client.post("/v1/consent", json=capture_body())

    assert response.status_code == 201, response.text
    events = await events_of(db_session, "consent.captured")
    assert len(events) == 1

    payload = events[0].payload
    assert payload["consent_id"] == "c_0001"
    assert payload["tier"] == "T1"
    assert payload["copy_version"] == "consent-en-2026-08"
    # snake_case on the log, camelCase on the wire — the bus's own convention,
    # and the mismatch that silently broke dwell scoring once already.
    assert "copyVersion" not in payload


async def test_capture_writes_nothing_but_the_event(
    client: AsyncClient, db_session: AsyncSession
):
    """No Contact, no graph, no identity — those are the consumer's decisions.

    Asserted by the absence of any other event: `identity.resolved` is what the
    identity consumer emits when it draws the link, and nothing here has run it.
    """
    await client.post("/v1/consent", json=capture_body())

    assert await events_of(db_session, "identity.resolved") == []


async def test_a_retried_capture_is_one_consent(
    client: AsyncClient, db_session: AsyncSession
):
    """The property the whole `consentId` argument is about.

    A kiosk whose POST times out resends with the same id. Two consent records
    for one conversation, differing only in id, is the state that makes "which
    wording did they see?" unanswerable — so the second POST must be the same
    event, not a new one.
    """
    first = await client.post("/v1/consent", json=capture_body())
    second = await client.post("/v1/consent", json=capture_body())

    assert first.status_code == 201
    # 201 both times is the wrong answer here; the log deduped, so this is the
    # row that was already there.
    assert second.json()["eventId"] == first.json()["eventId"]
    assert second.json()["seq"] == first.json()["seq"]
    assert len(await events_of(db_session, "consent.captured")) == 1


async def test_the_contact_is_omitted_rather_than_written_as_blanks(
    client: AsyncClient, db_session: AsyncSession
):
    """"No details were given" and "these details are blank" are different
    statements about what a person handed over."""
    await client.post("/v1/consent", json=capture_body())

    payload = (await events_of(db_session, "consent.captured"))[0].payload
    assert "contact" not in payload


async def test_the_pii_travels_only_inside_contact(
    client: AsyncClient, db_session: AsyncSession
):
    await client.post(
        "/v1/consent",
        json=capture_body(
            consentId="c_0002",
            tier="T2",
            contact={"email": "sam@example.com", "name": "Sam Rivera"},
        ),
    )

    payload = (await events_of(db_session, "consent.captured"))[0].payload
    assert payload["contact"] == {"email": "sam@example.com", "name": "Sam Rivera"}
    # Everything outside `contact` stays anonymous, which is what lets a
    # deployment keep the consent record after erasing the person.
    assert payload["anon_id"] == "P-012"
    assert "email" not in payload


@pytest.mark.parametrize(
    "missing", ["copyVersion", "tier", "basis", "consentId", "source"]
)
async def test_a_capture_missing_its_audit_fields_is_refused(
    client: AsyncClient, missing: str
):
    """None of these are conveniences. Without `copyVersion` there is no record
    of what was read; without `consentId` a retry duplicates the consent; without
    `tier` nothing downstream can decide what the consent permits."""
    body = capture_body()
    del body[missing]

    assert (await client.post("/v1/consent", json=body)).status_code == 422


async def test_an_unknown_tier_is_refused(client: AsyncClient):
    """The tiers are a closed set (`consent-and-identity.md` §2). A rule that
    accepted `T4` would arm a permission nothing downstream knows how to check."""
    response = await client.post("/v1/consent", json=capture_body(tier="T4"))
    assert response.status_code == 422


async def test_a_capture_dates_itself_when_the_surface_does_not(
    client: AsyncClient, db_session: AsyncSession
):
    before = dt.datetime.now(dt.timezone.utc)

    await client.post("/v1/consent", json=capture_body())

    event = (await events_of(db_session, "consent.captured"))[0]
    assert event.occurred_at >= before - dt.timedelta(seconds=5)
    assert event.payload["captured_at"] is not None


async def test_a_withdrawal_lands_on_the_log(
    client: AsyncClient, db_session: AsyncSession
):
    await client.post("/v1/consent", json=capture_body())

    response = await client.post(
        "/v1/consent/withdraw",
        json={"sessionId": S, "consentId": "c_0001", "reason": "visitor_request"},
    )

    assert response.status_code == 201, response.text
    events = await events_of(db_session, "consent.withdrawn")
    assert len(events) == 1
    assert events[0].payload["consent_id"] == "c_0001"
    assert events[0].payload["reason"] == "visitor_request"


async def test_a_withdrawal_naming_nobody_is_refused_with_the_reason(
    client: AsyncClient,
):
    response = await client.post(
        "/v1/consent/withdraw", json={"sessionId": S, "reason": "operator"}
    )

    assert response.status_code == 422
    assert "consentId" in response.json()["detail"]


async def test_withdrawing_twice_is_one_withdrawal(
    client: AsyncClient, db_session: AsyncSession
):
    """Same subject, same reason, stated twice — one fact, so one event. The
    re-anonymiser is idempotent anyway; this stops it being asked twice."""
    payload = {"sessionId": S, "anonId": "P-012", "reason": "visitor_request"}

    await client.post("/v1/consent/withdraw", json=payload)
    await client.post("/v1/consent/withdraw", json=payload)

    assert len(await events_of(db_session, "consent.withdrawn")) == 1


async def test_an_erasure_request_is_not_a_repeat_of_a_change_of_mind(
    client: AsyncClient, db_session: AsyncSession
):
    """Different reason, different act. An erasure request is wider in scope
    than a withdrawal, so it must not be deduped onto one."""
    await client.post(
        "/v1/consent/withdraw",
        json={"sessionId": S, "anonId": "P-012", "reason": "visitor_request"},
    )
    await client.post(
        "/v1/consent/withdraw",
        json={"sessionId": S, "anonId": "P-012", "reason": "erasure_request"},
    )

    assert len(await events_of(db_session, "consent.withdrawn")) == 2
