"""
A consent kiosk, and the two things it can and cannot say.

`consent.captured` has had every reader since Phase 4 — the identity consumer,
attribution, five CRM adapters, the follow-up drafter, the ledger — and no
producer a visitor could reach, so the whole identified half of the funnel was
only ever exercised by `curl`. This is the producer.

Most of this file is about the split between what the surface attests and what
the consumer derives, because that is where the design is:

  - the three ids, the tier and the copy version come off the token row and the
    activation, so a kiosk cannot record a T3 for somebody shown the T1 wording
    however the request is written;
  - a consent is **always recorded** and its *path* is attached only when
    exactly one person was in the surface's zone — the deliberate difference
    from `consumers/touch.py`, argued in that consumer's docstring;
  - unknown, expired and revoked are one answer, and a missing consent copy is
    a different one, because that is the operator's to fix;
  - a consent that arrives before the tracker has explained it is retried, not
    dropped.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from neo4j import AsyncSession as GraphSession
from sqlalchemy.ext.asyncio import AsyncSession

from app import erasure, repository
from app.auth.models import AuthUser
from app.auth.tokens import issue_token
from app.consumers.kiosk_consent import (
    ConsentNotResolvableYet,
    KioskConsentConsumer,
)
from app.db import get_session
from app.graph import repository as graph_repo
from app.main import app
from app.schemas import EventIn
from tests.conftest import as_tenant

T = "t_test"
OTHER = "t_other"
S = "s_kiosk"

BASE = dt.datetime(2026, 9, 3, 10, 0, 0, tzinfo=dt.timezone.utc)
#: Somebody walks up to the desk a minute before they fill the form in. The
#: consent carries its own `at` in these tests rather than defaulting to the
#: request's arrival, because occupancy is judged at the moment the person
#: agreed — and a default of "now" would make every attribution assertion
#: depend on where today's date happens to fall relative to `BASE`.
ARRIVED = BASE - dt.timedelta(minutes=1)
POLY = [[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0]]

GIVEN = "consent.given"
CAPTURED = "consent.captured"

COPY = "We keep your details with this visit and may contact you about it."
VERSION = "consent-2026-09-abcd1234"


@pytest.fixture(autouse=True)
async def _scope_to_test_tenant(db_session: AsyncSession) -> AsyncIterator[None]:
    await as_tenant(db_session, T)
    yield


def _make_client(db_session: AsyncSession, token: str | None) -> AsyncClient:
    async def override_get_session() -> AsyncIterator[AsyncSession]:
        yield db_session
        await db_session.commit()

    app.dependency_overrides[get_session] = override_get_session
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=headers
    )


async def _user(db_session: AsyncSession, user_id: str, role: str, tenant: str) -> str:
    db_session.add(
        AuthUser(
            user_id=user_id,
            email=f"{user_id}@floats.demo",
            display_name=user_id,
            tenant_id=tenant,
            role=role,
        )
    )
    await db_session.commit()
    return issue_token(subject=user_id, tenant_id=tenant, role=role)


@pytest.fixture
async def operator(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    token = await _user(db_session, "u_op", "operator", T)
    async with _make_client(db_session, token) as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
async def outsider(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """An operator in another organisation. Exists only to be unable to."""
    token = await _user(db_session, "u_other", "operator", OTHER)
    async with _make_client(db_session, token) as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
async def visitor(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """No credential at all — which is the whole point of a kiosk link."""
    async with _make_client(db_session, None) as ac:
        yield ac
    app.dependency_overrides.clear()


async def seed(
    graph_session: GraphSession,
    *,
    zone_id: str | None = "z_desk",
    copy: str | None = COPY,
    version: str | None = VERSION,
    tier: str = "T2",
) -> None:
    await graph_repo.upsert_session(
        graph_session,
        tenant_id=T,
        session_id=S,
        venue="Consent Hall",
        consent_copy=copy,
        consent_copy_version=version,
        consent_tier=tier,
    )
    await graph_repo.upsert_zone(
        graph_session, tenant_id=T, session_id=S,
        zone_id="z_desk", name="Welcome Desk", type="entry", polygon=POLY,
    )
    await graph_repo.upsert_surface(
        graph_session, tenant_id=T, session_id=S,
        surface_id="sf_kiosk", label="Welcome Kiosk", type="lead_form",
        zone_id=zone_id,
    )


async def emit(
    db_session: AsyncSession, *, type: str, payload: dict, at: dt.datetime = BASE
) -> None:
    await repository.append_event(
        db_session,
        EventIn(
            event_id=uuid.uuid4(), tenant_id=T, session_id=S,
            type=type, payload=payload, occurred_at=at,
        ),
    )
    await db_session.commit()


async def standing_at_the_desk(
    db_session: AsyncSession, *anon_ids: str, at: dt.datetime = ARRIVED
) -> None:
    for anon_id in anon_ids:
        await emit(
            db_session,
            type="spatial.zone_enter",
            payload={"anon_id": anon_id, "zone_id": "z_desk", "at": at.isoformat()},
            at=at,
        )


async def mint(operator: AsyncClient, surface_id: str = "sf_kiosk", **body) -> str:
    res = await operator.post(
        f"/v1/sessions/{S}/surfaces/{surface_id}/kiosk", json=body
    )
    assert res.status_code == 201, res.text
    return res.json()["token"]


async def let_the_tracker_catch_up(db_session: AsyncSession) -> None:
    """Stand in for the tracker having read everything appended so far.

    These tests seed the tracker's *output* directly rather than running it, so
    the cursor is moved by hand — otherwise every attribution test would assert
    the wait instead of the answer. `test_touch.py` does the same and for the
    same reason.
    """
    rows = await repository.read_events(db_session, tenant_id=T, limit=1000)
    await repository.advance_cursor(
        db_session, consumer="tracker", tenant_id=T, last_seq=rows[-1].seq
    )
    await db_session.commit()


async def given(db_session: AsyncSession) -> list[dict]:
    rows = await repository.read_events(
        db_session, tenant_id=T, session_id=S, type=GIVEN, limit=50
    )
    return [row.payload for row in rows]


async def captured(db_session: AsyncSession) -> list[dict]:
    rows = await repository.read_events(
        db_session, tenant_id=T, session_id=S, type=CAPTURED, limit=50
    )
    return [row.payload for row in rows]


# ── the kiosk ────────────────────────────────────────────────────────────────


async def test_a_consent_becomes_an_event_with_no_credential(
    operator: AsyncClient, visitor: AsyncClient,
    graph_session: GraphSession, db_session: AsyncSession,
) -> None:
    await seed(graph_session)
    token = await mint(operator)

    res = await visitor.post(
        f"/v1/kiosk/{token}/consent",
        json={"consentId": "c-1", "contact": {"email": "sam@example.test"}},
    )
    assert res.status_code == 201, res.text

    recorded = await given(db_session)
    assert len(recorded) == 1
    assert recorded[0]["consent_id"] == "c-1"
    assert recorded[0]["surface_id"] == "sf_kiosk"
    assert recorded[0]["source"] == "kiosk"
    assert recorded[0]["contact"]["email"] == "sam@example.test"
    # The thing a kiosk cannot know, absent rather than blank.
    assert "anon_id" not in recorded[0]


async def test_the_tier_and_the_wording_come_off_the_activation(
    operator: AsyncClient, visitor: AsyncClient,
    graph_session: GraphSession, db_session: AsyncSession,
) -> None:
    """A surface that could name its own tier could record a T3 for somebody
    shown the T1 wording, and the consent record is the evidence a disputed
    withdrawal is settled by. There is no request field for either, and adding
    one is what this test exists to make somebody argue for."""
    await seed(graph_session, tier="T1")
    token = await mint(operator)

    res = await visitor.post(
        f"/v1/kiosk/{token}/consent",
        json={
            "consentId": "c-1",
            "tier": "T3",
            "copyVersion": "something-else",
            "copy_version": "something-else",
        },
    )
    assert res.status_code == 201, res.text

    recorded = (await given(db_session))[0]
    assert recorded["tier"] == "T1"
    assert recorded["copy_version"] == VERSION


async def test_a_kiosk_cannot_post_as_another_surface(
    operator: AsyncClient, visitor: AsyncClient,
    graph_session: GraphSession, db_session: AsyncSession,
) -> None:
    await seed(graph_session)
    await graph_repo.upsert_surface(
        graph_session, tenant_id=T, session_id=S,
        surface_id="sf_other", label="Other Desk", type="lead_form",
        zone_id="z_desk",
    )
    token = await mint(operator, "sf_kiosk")

    res = await visitor.post(
        f"/v1/kiosk/{token}/consent",
        json={"consentId": "c-1", "surfaceId": "sf_other", "surface_id": "sf_other"},
    )
    assert res.status_code == 201, res.text
    assert (await given(db_session))[0]["surface_id"] == "sf_kiosk"


async def test_a_retry_over_bad_wifi_is_one_consent(
    operator: AsyncClient, visitor: AsyncClient,
    graph_session: GraphSession, db_session: AsyncSession,
) -> None:
    """The id is minted before the copy is shown, not when the request is sent.

    Two consent records for one conversation, differing only in id, is the
    failure `routers/consent.py`'s docstring names: afterwards nothing can say
    which wording the person actually read.
    """
    await seed(graph_session)
    token = await mint(operator)

    for _ in range(3):
        res = await visitor.post(
            f"/v1/kiosk/{token}/consent", json={"consentId": "c-1"}
        )
        assert res.status_code == 201, res.text

    assert len(await given(db_session)) == 1


async def test_the_kiosk_renders_the_operators_wording(
    operator: AsyncClient, visitor: AsyncClient, graph_session: GraphSession
) -> None:
    """Correcting a sentence changes every plinth, with nothing re-minted."""
    await seed(graph_session)
    token = await mint(operator)

    first = (await visitor.get(f"/v1/kiosk/{token}")).json()
    assert first["copyText"] == COPY
    assert first["copyVersion"] == VERSION
    assert first["label"] == "Welcome Kiosk"
    assert first["tier"] == "T2"

    await graph_repo.upsert_session(
        graph_session, tenant_id=T, session_id=S,
        consent_copy="New wording.", consent_copy_version="consent-2026-10-ffff",
    )
    again = (await visitor.get(f"/v1/kiosk/{token}")).json()
    assert again["copyText"] == "New wording."
    assert again["copyVersion"] == "consent-2026-10-ffff"


@pytest.mark.parametrize("bad", ["not-a-token", ""])
async def test_an_unknown_token_is_a_404(visitor: AsyncClient, bad: str) -> None:
    res = await visitor.post(f"/v1/kiosk/{bad}/consent", json={"consentId": "c"})
    assert res.status_code == 404


async def test_a_revoked_kiosk_stops_recording(
    operator: AsyncClient, visitor: AsyncClient,
    graph_session: GraphSession, db_session: AsyncSession,
) -> None:
    await seed(graph_session)
    token = await mint(operator)
    listed = (await operator.get(f"/v1/sessions/{S}/kiosks")).json()
    assert len(listed) == 1 and listed[0]["active"] is True

    res = await operator.delete(f"/v1/sessions/{S}/kiosks/{listed[0]['id']}")
    assert res.status_code == 200, res.text

    after = await visitor.post(f"/v1/kiosk/{token}/consent", json={"consentId": "c-1"})
    assert after.status_code == 404
    assert await given(db_session) == []

    # Revoked ones stay listed: "was this kiosk ever live" is a question about
    # the activation, and hiding them answers it wrongly by omission.
    still = (await operator.get(f"/v1/sessions/{S}/kiosks")).json()
    assert len(still) == 1 and still[0]["active"] is False


async def test_another_organisation_cannot_revoke_a_kiosk(
    operator: AsyncClient, outsider: AsyncClient, graph_session: GraphSession
) -> None:
    """`consent_token` is outside RLS (migration 0016), so the tenant match is
    written out in the handler rather than enforced by a policy."""
    await seed(graph_session)
    await mint(operator)
    mine = (await operator.get(f"/v1/sessions/{S}/kiosks")).json()[0]

    res = await outsider.delete(f"/v1/sessions/{S}/kiosks/{mine['id']}")
    assert res.status_code == 404

    assert (await operator.get(f"/v1/sessions/{S}/kiosks")).json()[0]["active"] is True


# ── the two refusals at minting time ─────────────────────────────────────────


async def test_a_kiosk_needs_a_surface(
    operator: AsyncClient, graph_session: GraphSession
) -> None:
    await seed(graph_session)
    res = await operator.post(f"/v1/sessions/{S}/surfaces/sf_ghost/kiosk", json={})
    assert res.status_code == 404
    assert "sf_ghost" in res.text


async def test_a_kiosk_needs_the_wording_before_it_is_minted(
    operator: AsyncClient, graph_session: GraphSession
) -> None:
    """The failure this prevents is the operator finding out at the stand: a
    minted link with no copy renders a refusal to every visitor who scans it."""
    await seed(graph_session, copy=None, version=None)
    res = await operator.post(f"/v1/sessions/{S}/surfaces/sf_kiosk/kiosk", json={})
    assert res.status_code == 409
    assert "consentCopy" in res.text


async def test_a_kiosk_whose_wording_was_removed_says_so_to_the_visitor(
    operator: AsyncClient, visitor: AsyncClient, graph_session: GraphSession
) -> None:
    """Not the 404. "This link is dead" and "nobody set the wording" are
    different sentences, and only one of them sends a visitor away for good."""
    await seed(graph_session)
    token = await mint(operator)
    await graph_repo.upsert_session(
        graph_session, tenant_id=T, session_id=S,
        consent_copy=None, consent_copy_version=None,
    )

    res = await visitor.get(f"/v1/kiosk/{token}")
    assert res.status_code == 409
    assert "wording" in res.text


# ── who gave it ──────────────────────────────────────────────────────────────


async def test_one_person_at_the_desk_is_the_person_who_consented(
    operator: AsyncClient, visitor: AsyncClient,
    graph_session: GraphSession, db_session: AsyncSession,
) -> None:
    await seed(graph_session)
    token = await mint(operator)
    await standing_at_the_desk(db_session, "cam-1/P-001")
    await visitor.post(
        f"/v1/kiosk/{token}/consent",
        json={
            "consentId": "c-1",
            "at": BASE.isoformat(),
            "contact": {"email": "sam@example.test"},
        },
    )
    await let_the_tracker_catch_up(db_session)

    await KioskConsentConsumer().run_once()

    built = await captured(db_session)
    assert len(built) == 1
    assert built[0]["anon_id"] == "cam-1/P-001"
    assert built[0]["attributed_by"] == "zone_occupancy"
    assert built[0]["zone_id"] == "z_desk"
    assert built[0]["tier"] == "T2"
    assert built[0]["copy_version"] == VERSION
    # The PII travels through, because the whole point of the surface is the
    # lead at the end of it.
    assert built[0]["contact"]["email"] == "sam@example.test"


@pytest.mark.parametrize(
    "occupants",
    [
        pytest.param((), id="nobody-at-the-desk"),
        pytest.param(("cam-1/P-001", "cam-1/P-002"), id="two-at-the-desk"),
    ],
)
async def test_an_unattributable_consent_is_still_recorded(
    operator: AsyncClient, visitor: AsyncClient,
    graph_session: GraphSession, db_session: AsyncSession,
    occupants: tuple[str, ...],
) -> None:
    """**The deliberate difference from `consumers/touch.py`.**

    A tap with two people in the zone is a claim about somebody the tablet
    cannot observe, so it is refused. A consent is the visitor's own claim about
    themselves — what an ambiguous zone costs is the path, not the permission,
    and discarding it would mean a person handed over their details and heard
    nothing back.

    Stage 1 stops at the log: `consent.captured` pins an `anon_id` and
    `consumers/identity.py` raises on a track that does not exist, so emitting
    one without it would park a correct consent in the dead-letter queue.
    """
    await seed(graph_session)
    token = await mint(operator)
    if occupants:
        await standing_at_the_desk(db_session, *occupants)
    await visitor.post(
        f"/v1/kiosk/{token}/consent",
        json={
            "consentId": "c-1",
            "at": BASE.isoformat(),
            "contact": {"email": "sam@example.test"},
        },
    )
    await let_the_tracker_catch_up(db_session)

    await KioskConsentConsumer().run_once()

    recorded = await given(db_session)
    assert len(recorded) == 1, "the consent is on the log either way"
    assert recorded[0]["contact"]["email"] == "sam@example.test"
    assert await captured(db_session) == [], "no path was observed, so none is claimed"


async def test_a_kiosk_in_no_zone_records_without_a_path(
    operator: AsyncClient, visitor: AsyncClient,
    graph_session: GraphSession, db_session: AsyncSession,
) -> None:
    """The operator never said where the plinth is, and the nearest visitor is
    not an answer to a question nobody asked them."""
    await seed(graph_session, zone_id=None)
    token = await mint(operator)
    await standing_at_the_desk(db_session, "cam-1/P-001")
    await visitor.post(
        f"/v1/kiosk/{token}/consent",
        json={"consentId": "c-1", "at": BASE.isoformat()},
    )
    await let_the_tracker_catch_up(db_session)

    await KioskConsentConsumer().run_once()

    assert len(await given(db_session)) == 1
    assert await captured(db_session) == []


async def test_a_consent_ahead_of_the_tracker_waits_rather_than_guessing(
    operator: AsyncClient, visitor: AsyncClient,
    graph_session: GraphSession, db_session: AsyncSession,
) -> None:
    """A form takes longer to fill in than a poll interval — until the day the
    log is busy. Resolving immediately would read an empty desk and attribute
    nothing, permanently, because the cursor would have moved on."""
    await seed(graph_session)
    token = await mint(operator)
    await standing_at_the_desk(db_session, "cam-1/P-001")
    await visitor.post(
        f"/v1/kiosk/{token}/consent",
        json={"consentId": "c-1", "at": BASE.isoformat()},
    )
    # The tracker's cursor is deliberately left behind.

    consumer = KioskConsentConsumer()
    rows = await repository.read_events(
        db_session, tenant_id=T, session_id=S, type=GIVEN, limit=1
    )
    with pytest.raises(ConsentNotResolvableYet):
        await consumer.handle(rows[0])

    # And it lands once the tracker has caught up, from the same event.
    await let_the_tracker_catch_up(db_session)
    await consumer.run_once()
    assert len(await captured(db_session)) == 1


async def test_a_replay_re_derives_one_capture(
    operator: AsyncClient, visitor: AsyncClient,
    graph_session: GraphSession, db_session: AsyncSession,
) -> None:
    """The id derives from the consent, so rewinding the cursor reproduces the
    same `consent.captured` rather than doubling the identification — which
    would double every count of identified visitors on each replay."""
    await seed(graph_session)
    token = await mint(operator)
    await standing_at_the_desk(db_session, "cam-1/P-001")
    await visitor.post(
        f"/v1/kiosk/{token}/consent",
        json={"consentId": "c-1", "at": BASE.isoformat()},
    )
    await let_the_tracker_catch_up(db_session)

    await KioskConsentConsumer().run_once()
    first = await captured(db_session)

    await repository.reset_cursor(db_session, consumer="kiosk_consent", tenant_id=T)
    await db_session.commit()
    await KioskConsentConsumer().run_once()

    assert len(await captured(db_session)) == len(first) == 1


# ── the part erasure has to reach ────────────────────────────────────────────


def test_the_raw_kiosk_event_is_classified_as_pii() -> None:
    """`consent.given` is where a kiosk consent's email first appears — and for
    the consents no zone could attribute, the only place it appears. An erasure
    that walked only from `consent.captured` would leave the name behind."""
    assert "consent.given" in erasure.PII_TYPES
    assert "consent.given" in erasure.LINKING_TYPES


def test_a_kiosk_consent_is_redacted_by_the_shared_vocabulary() -> None:
    payload = {
        "consent_id": "c-1",
        "copy_version": VERSION,
        "contact": {"email": "sam@example.test", "name": "Sam"},
    }
    redacted = erasure.redact("consent.given", payload)
    assert redacted is not None
    assert redacted["contact"] == {}
    # The evidence survives: what was agreed, and to which wording.
    assert redacted["consent_id"] == "c-1"
    assert redacted["copy_version"] == VERSION
