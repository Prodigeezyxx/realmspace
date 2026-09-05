"""
A tablet at a touchpoint, and the question it cannot answer.

`surface.interaction` has had every reader since Phase 2 — `graph_writer` draws
`INTERACTED_WITH`, `llm/digest.py` reads it, `scorecard.ts` scores the
Engagement layer of the four the report is sold on — and no producer at all, so
that layer rendered `0*` on every real activation. This is the producer.

Most of this file is about what the chain **refuses**, because that is where the
design is. A tablet has no camera; it can attest that its button was pressed and
not to who pressed it, and every way of guessing (the nearest visitor, the last
one seen, the one who dwelled longest) puts an observation on an append-only log
that nothing made.

The properties worth stating plainly:

  - the three ids come off the token row, so one tablet cannot post as another
    touchpoint however the request is written;
  - a tap is attributed only when **exactly one** person was in the zone, and is
    counted either way;
  - unknown, expired and revoked are one answer;
  - a tap that arrives before the tracker has explained it is retried, not
    dropped — the answer is not wrong yet, it is not available yet.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from neo4j import AsyncSession as GraphSession
from sqlalchemy.ext.asyncio import AsyncSession

from app import repository
from app.auth.models import AuthUser
from app.auth.tokens import issue_token
from app.consumers.touch import TouchConsumer, TouchNotResolvableYet
from app.db import get_session
from app.graph import repository as graph_repo
from app.main import app
from app.schemas import EventIn
from tests.conftest import as_tenant

T = "t_test"
OTHER = "t_other"
S = "s_touch"

BASE = dt.datetime(2026, 8, 31, 10, 0, 0, tzinfo=dt.timezone.utc)
POLY = [[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0]]

TOUCHED = "surface.touched"
INTERACTION = "surface.interaction"


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
async def tablet(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """No credential at all — which is the whole point of a touchpoint link."""
    async with _make_client(db_session, None) as ac:
        yield ac
    app.dependency_overrides.clear()


async def seed(
    graph_session: GraphSession, *, zone_id: str | None = "z_pod"
) -> None:
    await graph_repo.upsert_session(
        graph_session, tenant_id=T, session_id=S, venue="Touch Hall"
    )
    await graph_repo.upsert_zone(
        graph_session, tenant_id=T, session_id=S,
        zone_id="z_pod", name="Pod", type="feature", polygon=POLY,
    )
    await graph_repo.upsert_surface(
        graph_session, tenant_id=T, session_id=S,
        surface_id="sf_mirror", label="AR Mirror", type="mirror", zone_id=zone_id,
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


async def standing_in_the_zone(
    db_session: AsyncSession, *anon_ids: str, at: dt.datetime = BASE
) -> None:
    for anon_id in anon_ids:
        await emit(
            db_session,
            type="spatial.zone_enter",
            payload={"anon_id": anon_id, "zone_id": "z_pod", "at": at.isoformat()},
            at=at,
        )


async def left_the_zone(
    db_session: AsyncSession, anon_id: str, *, at: dt.datetime
) -> None:
    await emit(
        db_session,
        type="spatial.zone_exit",
        payload={"anon_id": anon_id, "zone_id": "z_pod", "at": at.isoformat()},
        at=at,
    )


async def mint(operator: AsyncClient, surface_id: str = "sf_mirror", **body) -> str:
    res = await operator.post(
        f"/v1/sessions/{S}/surfaces/{surface_id}/tablet", json=body
    )
    assert res.status_code == 201, res.text
    return res.json()["token"]


async def let_the_tracker_catch_up(db_session: AsyncSession) -> None:
    """Stand in for the tracker having read everything appended so far.

    The consumer refuses to attribute a tap the tracker has not reached, and
    these tests seed the tracker's *output* directly rather than running it, so
    the cursor has to be moved by hand or every attribution test would assert
    the wait instead of the answer.
    """
    rows = await repository.read_events(db_session, tenant_id=T, limit=1000)
    await repository.advance_cursor(
        db_session, consumer="tracker", tenant_id=T, last_seq=rows[-1].seq
    )
    await db_session.commit()


async def taps(db_session: AsyncSession) -> list[dict]:
    rows = await repository.read_events(
        db_session, tenant_id=T, session_id=S, type=TOUCHED, limit=50
    )
    return [row.payload for row in rows]


async def interactions(db_session: AsyncSession) -> list[dict]:
    rows = await repository.read_events(
        db_session, tenant_id=T, session_id=S, type=INTERACTION, limit=50
    )
    return [row.payload for row in rows]


# ── the tablet ───────────────────────────────────────────────────────────────


async def test_a_tap_becomes_an_event_with_no_credential(
    operator: AsyncClient, tablet: AsyncClient,
    graph_session: GraphSession, db_session: AsyncSession,
) -> None:
    await seed(graph_session)
    token = await mint(operator)

    res = await tablet.post(f"/v1/touch/{token}", json={"touchId": "tap-1"})
    assert res.status_code == 201, res.text

    recorded = await taps(db_session)
    assert len(recorded) == 1
    assert recorded[0]["surface_id"] == "sf_mirror"
    assert recorded[0]["touch_id"] == "tap-1"
    # The name travels with the tap, as `zone_name` does with every spatial
    # event: `llm/digest.py` has no graph to look one up in, and without this an
    # insight reads "sf_mirror was used 3 times" at a client.
    assert recorded[0]["surface_label"] == "AR Mirror"
    # The thing a tablet cannot know, absent rather than blank.
    assert "anon_id" not in recorded[0]


async def test_a_tablet_cannot_post_as_another_touchpoint(
    operator: AsyncClient, tablet: AsyncClient,
    graph_session: GraphSession, db_session: AsyncSession,
) -> None:
    """The surface comes off the token row. There is no request field for it,
    and adding one is what this test exists to make somebody argue for."""
    await seed(graph_session)
    await graph_repo.upsert_surface(
        graph_session, tenant_id=T, session_id=S,
        surface_id="sf_other", label="Scent Station", type="scent", zone_id="z_pod",
    )
    token = await mint(operator, "sf_mirror")

    res = await tablet.post(
        f"/v1/touch/{token}",
        json={"touchId": "tap-1", "surfaceId": "sf_other", "surface_id": "sf_other"},
    )
    assert res.status_code == 201, res.text
    assert (await taps(db_session))[0]["surface_id"] == "sf_mirror"


async def test_a_retry_over_bad_wifi_is_one_tap(
    operator: AsyncClient, tablet: AsyncClient,
    graph_session: GraphSession, db_session: AsyncSession,
) -> None:
    """The id is minted when the finger lands, not when the request is sent."""
    await seed(graph_session)
    token = await mint(operator)

    for _ in range(3):
        res = await tablet.post(f"/v1/touch/{token}", json={"touchId": "tap-1"})
        assert res.status_code == 201, res.text

    assert len(await taps(db_session)) == 1


async def test_the_tablet_renders_itself_from_the_operators_config(
    operator: AsyncClient, tablet: AsyncClient, graph_session: GraphSession
) -> None:
    """Renaming the touchpoint renames the button, with nothing re-minted and
    nobody walking round the stand with a laptop."""
    await seed(graph_session)
    token = await mint(operator)

    first = await tablet.get(f"/v1/touch/{token}")
    assert first.status_code == 200
    assert first.json()["label"] == "AR Mirror"

    await graph_repo.upsert_surface(
        graph_session, tenant_id=T, session_id=S,
        surface_id="sf_mirror", label="Mirror, Bay 2", type="mirror", zone_id="z_pod",
    )
    again = await tablet.get(f"/v1/touch/{token}")
    assert again.json()["label"] == "Mirror, Bay 2"


@pytest.mark.parametrize("bad", ["not-a-token", ""])
async def test_an_unknown_token_is_a_404(tablet: AsyncClient, bad: str) -> None:
    res = await tablet.post(f"/v1/touch/{bad}", json={"touchId": "t"})
    assert res.status_code == 404


async def test_a_revoked_tablet_stops_posting(
    operator: AsyncClient, tablet: AsyncClient,
    graph_session: GraphSession, db_session: AsyncSession,
) -> None:
    await seed(graph_session)
    token = await mint(operator)
    listed = (await operator.get(f"/v1/sessions/{S}/tablets")).json()
    assert len(listed) == 1 and listed[0]["active"] is True

    assert (
        await tablet.post(f"/v1/touch/{token}", json={"touchId": "tap-1"})
    ).status_code == 201

    gone = await operator.delete(f"/v1/sessions/{S}/tablets/{listed[0]['id']}")
    assert gone.status_code == 200 and gone.json()["active"] is False

    after = await tablet.post(f"/v1/touch/{token}", json={"touchId": "tap-2"})
    assert after.status_code == 404
    # The taps it already recorded stay: withdrawing a credential does not
    # unmake the measurements it produced.
    assert len(await taps(db_session)) == 1


async def test_the_token_is_returned_once_and_never_again(
    operator: AsyncClient, graph_session: GraphSession
) -> None:
    await seed(graph_session)
    created = await operator.post(
        f"/v1/sessions/{S}/surfaces/sf_mirror/tablet", json={}
    )
    token = created.json()["token"]

    listed = (await operator.get(f"/v1/sessions/{S}/tablets")).json()
    assert "token" not in listed[0]
    assert token[-4:] == listed[0]["hint"]


async def test_a_tablet_expires_sooner_than_a_share_link(
    operator: AsyncClient, graph_session: GraphSession
) -> None:
    """Fourteen days, not the share link's thirty, and it needs its own request
    model to be reachable at all — the first version of this endpoint took
    `ShareCreate` and `touch.DEFAULT_EXPIRY_DAYS` was a constant nothing read.

    A share link's month is the life of a conversation with a client after the
    stand comes down. A tablet's is the activation, and this is an
    unauthenticated **write** path.
    """
    import datetime as dtmod

    await seed(graph_session)
    created = (
        await operator.post(f"/v1/sessions/{S}/surfaces/sf_mirror/tablet", json={})
    ).json()

    expires = dtmod.datetime.fromisoformat(created["expiresAt"])
    days = (expires - dtmod.datetime.now(dtmod.timezone.utc)).days
    assert 13 <= days <= 14, created["expiresAt"]


async def test_minting_for_a_touchpoint_nobody_configured_is_refused(
    operator: AsyncClient, graph_session: GraphSession
) -> None:
    """A URL that renders nothing and posts taps the resolver then declines is
    something an operator would discover at the stand, in front of the client."""
    await seed(graph_session)
    res = await operator.post(f"/v1/sessions/{S}/surfaces/sf_ghost/tablet", json={})
    assert res.status_code == 404
    assert "sf_ghost" in res.text


async def test_another_organisation_can_neither_mint_list_nor_revoke(
    operator: AsyncClient, outsider: AsyncClient, graph_session: GraphSession
) -> None:
    """`surface_token` is outside RLS (migration 0015), so the tenant match is
    written out by hand — which is exactly the kind of check that needs a test
    rather than a policy to rely on."""
    await seed(graph_session)
    await mint(operator)
    mine = (await operator.get(f"/v1/sessions/{S}/tablets")).json()

    assert (await outsider.get(f"/v1/sessions/{S}/tablets")).json() == []
    assert (
        await outsider.delete(f"/v1/sessions/{S}/tablets/{mine[0]['id']}")
    ).status_code == 404
    assert (
        await outsider.post(f"/v1/sessions/{S}/surfaces/sf_mirror/tablet", json={})
    ).status_code == 404


# ── who pressed it ───────────────────────────────────────────────────────────


async def test_one_person_in_the_zone_is_the_person_who_pressed_it(
    operator: AsyncClient, tablet: AsyncClient,
    graph_session: GraphSession, db_session: AsyncSession,
) -> None:
    await seed(graph_session)
    token = await mint(operator)
    await standing_in_the_zone(db_session, "P-001")
    await tablet.post(f"/v1/touch/{token}", json={"touchId": "tap-1"})
    await let_the_tracker_catch_up(db_session)

    await TouchConsumer().run_once()

    built = await interactions(db_session)
    assert len(built) == 1
    assert built[0]["anon_id"] == "P-001"
    assert built[0]["surface_id"] == "sf_mirror"
    assert built[0]["attributed_by"] == "zone_occupancy"
    assert built[0]["zone_id"] == "z_pod"
    assert built[0]["surface_label"] == "AR Mirror"


async def test_two_people_in_the_zone_is_counted_and_not_attributed(
    operator: AsyncClient, tablet: AsyncClient,
    graph_session: GraphSession, db_session: AsyncSession,
) -> None:
    """The tap is real and the person is a guess. Picking the closest, the
    newest or the longest-dwelling one would put an observation on an
    append-only log that nothing made."""
    await seed(graph_session)
    token = await mint(operator)
    await standing_in_the_zone(db_session, "P-001", "P-002")
    await tablet.post(f"/v1/touch/{token}", json={"touchId": "tap-1"})
    await let_the_tracker_catch_up(db_session)

    await TouchConsumer().run_once()

    assert len(await taps(db_session)) == 1, "the tap is measured either way"
    assert await interactions(db_session) == []


async def test_an_empty_zone_is_counted_and_not_attributed(
    operator: AsyncClient, tablet: AsyncClient,
    graph_session: GraphSession, db_session: AsyncSession,
) -> None:
    """Real rather than theoretical: a member of staff demonstrating the
    touchpoint is somebody the cameras may not have as a visitor at all."""
    await seed(graph_session)
    token = await mint(operator)
    await tablet.post(f"/v1/touch/{token}", json={"touchId": "tap-1"})
    await let_the_tracker_catch_up(db_session)

    await TouchConsumer().run_once()

    assert len(await taps(db_session)) == 1
    assert await interactions(db_session) == []


async def test_somebody_who_has_left_is_not_still_standing_there(
    operator: AsyncClient, tablet: AsyncClient,
    graph_session: GraphSession, db_session: AsyncSession,
) -> None:
    await seed(graph_session)
    token = await mint(operator)
    await standing_in_the_zone(db_session, "P-001", "P-002")
    await left_the_zone(db_session, "P-002", at=BASE + dt.timedelta(seconds=30))

    at = BASE + dt.timedelta(seconds=60)
    await tablet.post(
        f"/v1/touch/{token}", json={"touchId": "tap-1", "at": at.isoformat()}
    )
    await let_the_tracker_catch_up(db_session)

    await TouchConsumer().run_once()

    built = await interactions(db_session)
    assert len(built) == 1 and built[0]["anon_id"] == "P-001"


async def test_occupancy_is_judged_on_event_time_not_log_order(
    operator: AsyncClient, tablet: AsyncClient,
    graph_session: GraphSession, db_session: AsyncSession,
) -> None:
    """The shape of the peak-occupancy bug the 2026-08-25 walk found: a running
    tally over the log answers "who was here" only if the log happens to be
    sorted by when things happened, which a batch replayed after an outage is
    not. Here the second visitor's *arrival* is appended after the tap and
    happened before it, so a seq-ordered reader would attribute the tap to one
    person when two were standing there."""
    await seed(graph_session)
    token = await mint(operator)
    await standing_in_the_zone(db_session, "P-001")

    at = BASE + dt.timedelta(seconds=60)
    await tablet.post(
        f"/v1/touch/{token}", json={"touchId": "tap-1", "at": at.isoformat()}
    )
    # Buffered through an outage and replayed: happened before the tap, appended
    # after it.
    await standing_in_the_zone(
        db_session, "P-002", at=BASE + dt.timedelta(seconds=10)
    )
    await let_the_tracker_catch_up(db_session)

    await TouchConsumer().run_once()

    assert await interactions(db_session) == []


async def test_a_touchpoint_in_no_zone_is_counted_and_not_attributed(
    operator: AsyncClient, tablet: AsyncClient,
    graph_session: GraphSession, db_session: AsyncSession,
) -> None:
    """The operator never said where it is, and the nearest visitor is not an
    answer to a question nobody asked them."""
    await seed(graph_session, zone_id=None)
    token = await mint(operator)
    await standing_in_the_zone(db_session, "P-001")
    await tablet.post(f"/v1/touch/{token}", json={"touchId": "tap-1"})
    await let_the_tracker_catch_up(db_session)

    await TouchConsumer().run_once()

    assert len(await taps(db_session)) == 1
    assert await interactions(db_session) == []


async def test_a_tap_the_tracker_has_not_reached_waits_rather_than_refusing(
    operator: AsyncClient, tablet: AsyncClient,
    graph_session: GraphSession, db_session: AsyncSession,
) -> None:
    """The ordinary case, not an edge one: the zone_enter that explains a tap is
    appended when the tracker *processes* the detection, which can be after the
    tap has already landed. Resolving immediately would read an empty zone and
    refuse a perfectly attributable tap — permanently, because the cursor would
    have moved on."""
    await seed(graph_session)
    token = await mint(operator)
    await standing_in_the_zone(db_session, "P-001")
    await tablet.post(f"/v1/touch/{token}", json={"touchId": "tap-1"})
    # The tracker's cursor is left where it is: at zero.

    rows = await repository.read_events(
        db_session, tenant_id=T, session_id=S, type=TOUCHED, limit=5
    )
    with pytest.raises(TouchNotResolvableYet):
        await TouchConsumer().handle(rows[0])

    # And once it has caught up, the same event resolves.
    await let_the_tracker_catch_up(db_session)
    await TouchConsumer().run_once()
    assert (await interactions(db_session))[0]["anon_id"] == "P-001"


async def test_a_replay_does_not_double_a_touchpoints_tally(
    operator: AsyncClient, tablet: AsyncClient,
    graph_session: GraphSession, db_session: AsyncSession,
) -> None:
    await seed(graph_session)
    token = await mint(operator)
    await standing_in_the_zone(db_session, "P-001")
    await tablet.post(f"/v1/touch/{token}", json={"touchId": "tap-1"})
    await let_the_tracker_catch_up(db_session)

    await TouchConsumer().run_once()
    await repository.reset_cursor(db_session, consumer="touch", tenant_id=T)
    await db_session.commit()
    await TouchConsumer().run_once()

    assert len(await interactions(db_session)) == 1
