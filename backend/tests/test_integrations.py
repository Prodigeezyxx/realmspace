"""
The credential store: what it refuses, what it never gives back, and who may ask.

`multi-tenant.md` §2 asks for one sentence — "each tenant's CRM/enrichment
credentials are stored encrypted, per-tenant, never shared" — and each clause of
it is a test here.

The tests use a fake adapter registered into `app.crm.registry` rather than
HubSpot. The registry is the seam, and exercising it with something inert proves
the store works without a network and without pinning these tests to one CRM's
API shape.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import crm, repository, secrets
from app.auth.models import AuthUser
from app.auth.tokens import issue_token
from app.crm.base import AdapterError, CrmAdapter
from app.db import get_session
from app.main import app
from tests.conftest import TENANT, as_tenant

OTHER = "t_integrations_other"


class FakeCrm(CrmAdapter):
    """An adapter that talks to nothing. Records what it was handed."""

    provider = "fakecrm"

    #: Set by a test to make healthcheck() fail the way an expired token does.
    healthy = True

    def map(self, handoff: dict[str, Any]) -> dict[str, Any] | None:
        return {"email": handoff.get("contact", {}).get("email")}

    async def upsert(self, payload: dict[str, Any]) -> str | None:
        return "ext_1"

    async def retract(self, external_id: str) -> str:
        return f"retracted {external_id}"

    async def healthcheck(self) -> tuple[bool, str]:
        if not type(self).healthy:
            return False, "401 from fakecrm: token expired"
        # Proves the secret arrived decrypted and intact, which is the whole
        # round trip this module exists for.
        return True, f"authenticated with {self._secret}"

    @classmethod
    def capabilities(cls) -> dict[str, Any]:
        return {"custom_fields": True, "retract": True, "activities": False}


@pytest.fixture(autouse=True)
def fake_adapter() -> AsyncIterator[type[FakeCrm]]:
    """Register the fake for one test and take it out again.

    Registered rather than monkeypatched onto the registry dict wholesale, so
    these tests exercise `crm.register` — the function every real adapter uses.
    """
    crm.register(FakeCrm)
    FakeCrm.healthy = True
    yield FakeCrm
    crm.registry.pop(FakeCrm.provider, None)


async def _client(
    db_session: AsyncSession, role: str = "admin", tenant: str = TENANT
) -> AsyncClient:
    user_id = f"u_{role}_{tenant}"
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

    async def override() -> AsyncIterator[AsyncSession]:
        yield db_session
        await db_session.commit()

    app.dependency_overrides[get_session] = override
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={
            "Authorization": f"Bearer {issue_token(subject=user_id, tenant_id=tenant, role=role)}"
        },
    )


# ── encryption ────────────────────────────────────────────────────────────────


def test_a_credential_survives_a_round_trip(encryption_key: str) -> None:
    blob = secrets.encrypt("pat-na1-secret", tenant_id=TENANT, provider="fakecrm")
    assert b"pat-na1-secret" not in blob
    assert (
        secrets.decrypt(blob, tenant_id=TENANT, provider="fakecrm")
        == "pat-na1-secret"
    )


def test_no_key_refuses_rather_than_storing_plaintext() -> None:
    """The decision `app/secrets.py` exists to make.

    Deliberately does not take the `encryption_key` fixture: this is the
    unconfigured deployment, and what it must not do is return something
    storable.
    """
    from app.config import get_settings

    settings = get_settings()
    before = settings.credential_encryption_key
    settings.credential_encryption_key = None
    try:
        with pytest.raises(secrets.EncryptionUnavailable):
            secrets.encrypt("pat-na1-secret", tenant_id=TENANT, provider="fakecrm")
    finally:
        settings.credential_encryption_key = before


def test_a_row_moved_between_tenants_does_not_decrypt(encryption_key: str) -> None:
    """The associated-data property, and the reason for choosing AES-GCM.

    A ciphertext written for one tenant is not readable as another's, so a bad
    restore or a row copied sideways fails loudly instead of handing tenant A's
    token to tenant B's delivery consumer.
    """
    blob = secrets.encrypt("pat-na1-secret", tenant_id=TENANT, provider="fakecrm")

    with pytest.raises(secrets.EncryptionUnavailable):
        secrets.decrypt(blob, tenant_id=OTHER, provider="fakecrm")
    with pytest.raises(secrets.EncryptionUnavailable):
        secrets.decrypt(blob, tenant_id=TENANT, provider="hubspot")


def test_a_changed_key_fails_loudly(encryption_key: str) -> None:
    """Rotation without re-entry is not silently lossy — it is an error.

    There is no re-encryption path yet, and the honest failure is one somebody
    reads, not a credential that quietly stops working at the CRM.
    """
    from app.config import get_settings

    blob = secrets.encrypt("pat-na1-secret", tenant_id=TENANT, provider="fakecrm")
    get_settings().credential_encryption_key = secrets.generate_key()

    with pytest.raises(secrets.EncryptionUnavailable):
        secrets.decrypt(blob, tenant_id=TENANT, provider="fakecrm")


def test_the_hint_is_not_the_secret() -> None:
    assert secrets.hint("pat-na1-abcd1234") == "1234"
    # Short enough that four characters would be most of it.
    assert secrets.hint("abc123") == ""


# ── the endpoints ─────────────────────────────────────────────────────────────


async def test_storing_a_credential_never_returns_it(
    db_session: AsyncSession, encryption_key: str
) -> None:
    client = await _client(db_session)
    async with client:
        put = await client.put(
            "/v1/integrations/fakecrm",
            json={"secret": "pat-na1-abcd1234", "fieldMap": {"lead_score": "rs_score"}},
        )
        assert put.status_code == 200, put.text
        body = put.json()
        assert "pat-na1-abcd1234" not in put.text
        assert body["secretHint"] == "1234"
        assert body["fieldMap"] == {"lead_score": "rs_score"}
        # From the adapter class, not the row: capabilities are a property of
        # the CRM and this build, not of the credential.
        assert body["capabilities"]["retract"] is True

        got = await client.get("/v1/integrations/fakecrm")
        assert got.status_code == 200
        assert "pat-na1-abcd1234" not in got.text

        listed = await client.get("/v1/integrations")
        assert [row["provider"] for row in listed.json()] == ["fakecrm"]
        assert "pat-na1-abcd1234" not in listed.text

    # And the column itself holds ciphertext, not the token.
    row = await repository.get_integration(
        db_session, tenant_id=TENANT, provider="fakecrm"
    )
    assert row is not None
    assert b"pat-na1-abcd1234" not in bytes(row.secret_ct)


async def test_no_encryption_key_is_a_503_not_a_stored_token(
    db_session: AsyncSession
) -> None:
    from app.config import get_settings

    settings = get_settings()
    before = settings.credential_encryption_key
    settings.credential_encryption_key = None
    try:
        client = await _client(db_session)
        async with client:
            put = await client.put(
                "/v1/integrations/fakecrm", json={"secret": "pat-na1-abcd1234"}
            )
        assert put.status_code == 503
        assert "credential_encryption_key" in put.json()["detail"]
    finally:
        settings.credential_encryption_key = before

    assert (
        await repository.get_integration(
            db_session, tenant_id=TENANT, provider="fakecrm"
        )
        is None
    )


async def test_a_provider_with_no_adapter_is_refused(
    db_session: AsyncSession, encryption_key: str
) -> None:
    """A stored credential nothing can dispatch is an admin who believes their
    CRM is connected."""
    client = await _client(db_session)
    async with client:
        put = await client.put(
            "/v1/integrations/sugarcrm", json={"secret": "whatever"}
        )
    assert put.status_code == 422
    assert "no adapter" in put.json()["detail"]


async def test_a_credential_of_the_wrong_shape_is_refused_at_the_door(
    db_session: AsyncSession, encryption_key: str
) -> None:
    """An admin pasting a bearer token into the Salesforce slot finds out now.

    The alternative is a credential that stores cleanly, tests green if nobody
    presses test, and fails on the first lead of a three-day activation. The
    message names the fields, because "invalid credential" tells an admin
    nothing they can act on.
    """
    client = await _client(db_session)
    async with client:
        put = await client.put(
            "/v1/integrations/salesforce", json={"secret": "pat-na1-token"}
        )
        assert put.status_code == 422
        assert "refresh_token" in put.json()["detail"]

        missing = await client.put(
            "/v1/integrations/salesforce",
            json={"secret": '{"instance_url": "https://x.my.salesforce.com"}'},
        )
        assert missing.status_code == 422
        assert "client_id" in missing.json()["detail"]

    # And nothing was stored — a refused credential must not leave a row an
    # admin can see and believe in.
    assert (
        await repository.get_integration(
            db_session, tenant_id=TENANT, provider="salesforce"
        )
        is None
    )


async def test_a_zoho_credential_in_the_wrong_datacentre_is_refused(
    db_session: AsyncSession, encryption_key: str
) -> None:
    """Zoho's regions are separate accounts, and the wrong one reads as a bad
    token rather than as a wrong region — so it is caught here instead."""
    client = await _client(db_session)
    async with client:
        put = await client.put(
            "/v1/integrations/zoho",
            json={
                "secret": (
                    '{"client_id": "c", "client_secret": "s", '
                    '"refresh_token": "r", "region": "co.uk"}'
                )
            },
        )
    assert put.status_code == 422
    assert "co.uk" in put.json()["detail"]


async def test_replacing_a_credential_clears_the_old_verdict(
    db_session: AsyncSession, encryption_key: str
) -> None:
    """A new token has not been tested yet, and must not inherit a red status —
    or a green one."""
    client = await _client(db_session)
    async with client:
        await client.put("/v1/integrations/fakecrm", json={"secret": "pat-na1-old1"})

        FakeCrm.healthy = False
        failed = await client.post("/v1/integrations/fakecrm/test")
        assert failed.status_code == 200
        assert failed.json()["ok"] is False

        FakeCrm.healthy = True
        replaced = await client.put(
            "/v1/integrations/fakecrm", json={"secret": "pat-na1-new2"}
        )
        assert replaced.json()["lastCheckAt"] is None
        assert replaced.json()["lastCheckOk"] is None

        # ...and the new secret is the one an adapter now authenticates with.
        checked = await client.post("/v1/integrations/fakecrm/test")
        assert checked.json()["detail"].endswith("pat-na1-new2")


async def test_a_failed_check_is_a_200_that_says_no(
    db_session: AsyncSession, encryption_key: str
) -> None:
    """An expired token is an answer, not a backend error — and it is stored, so
    the admin screen shows it tomorrow without anybody pressing test."""
    client = await _client(db_session)
    async with client:
        await client.put("/v1/integrations/fakecrm", json={"secret": "pat-na1-abcd"})
        FakeCrm.healthy = False

        checked = await client.post("/v1/integrations/fakecrm/test")
        assert checked.status_code == 200
        assert checked.json() == {
            "provider": "fakecrm",
            "ok": False,
            "detail": "401 from fakecrm: token expired",
        }

        got = await client.get("/v1/integrations/fakecrm")
        assert got.json()["lastCheckOk"] is False
        assert got.json()["lastCheckDetail"] == "401 from fakecrm: token expired"
        assert got.json()["lastCheckAt"] is not None


async def test_revoking_keeps_the_row_and_the_ciphertext(
    db_session: AsyncSession, encryption_key: str
) -> None:
    """`crm.retract` can arrive the day after an admin disconnects HubSpot, and
    retracting a contact still needs to authenticate to it."""
    client = await _client(db_session)
    async with client:
        await client.put("/v1/integrations/fakecrm", json={"secret": "pat-na1-abcd"})
        deleted = await client.delete("/v1/integrations/fakecrm")
        assert deleted.status_code == 200
        assert deleted.json()["status"] == "revoked"
        assert deleted.json()["revokedAt"] is not None

        # Still listed: "we disconnected in March" and "we never connected" are
        # different answers.
        listed = await client.get("/v1/integrations")
        assert [row["status"] for row in listed.json()] == ["revoked"]

    row = await repository.get_integration(
        db_session, tenant_id=TENANT, provider="fakecrm"
    )
    assert row is not None and row.secret_ct
    assert secrets.decrypt(
        row.secret_ct, tenant_id=TENANT, provider="fakecrm"
    ) == "pat-na1-abcd"

    # And a delivery consumer asking "where does a lead go?" no longer sees it.
    assert (
        await repository.list_integrations(
            db_session, tenant_id=TENANT, active_only=True
        )
        == []
    )


async def test_an_operator_may_not_manage_integrations(
    db_session: AsyncSession, encryption_key: str
) -> None:
    """multi-tenant.md §RBAC: integrations are the Admin's, not the Operator's.

    An operator arms a rule that posts to a room; a credential writes into the
    client's system of record and outlives the activation.
    """
    operator = await _client(db_session, role="operator")
    async with operator:
        assert (
            await operator.put(
                "/v1/integrations/fakecrm", json={"secret": "pat-na1-abcd"}
            )
        ).status_code == 403
        assert (await operator.get("/v1/integrations")).status_code == 403

    admin = await _client(db_session, role="admin")
    async with admin:
        assert (
            await admin.put(
                "/v1/integrations/fakecrm", json={"secret": "pat-na1-abcd"}
            )
        ).status_code == 200


async def test_a_device_key_cannot_reach_the_credential_store(
    db_session: AsyncSession, encryption_key: str, device_key: str
) -> None:
    """The credential most likely to walk out of a venue is the one taped inside
    the kit."""

    async def override() -> AsyncIterator[AsyncSession]:
        yield db_session
        await db_session.commit()

    app.dependency_overrides[get_session] = override
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": device_key},
    ) as device:
        assert (await device.get("/v1/integrations")).status_code == 403


# ── isolation ─────────────────────────────────────────────────────────────────


async def test_another_tenants_credential_is_invisible_to_the_database(
    db_session: AsyncSession, encryption_key: str
) -> None:
    """The mechanism, not the outcome — raw SQL, as `test_rls.py` does, because
    the failure this guards against is a query somebody adds later and forgets
    to scope."""
    await as_tenant(db_session, OTHER)
    await repository.upsert_integration(
        db_session,
        tenant_id=OTHER,
        provider="fakecrm",
        secret_ct=secrets.encrypt(
            "theirs", tenant_id=OTHER, provider="fakecrm"
        ),
        secret_hint="eirs",
        field_map={},
    )
    await db_session.commit()

    await as_tenant(db_session, TENANT)
    rows = (
        await db_session.execute(text("SELECT tenant_id FROM tenant_integration"))
    ).scalars().all()
    assert rows == []


async def test_the_api_cannot_read_another_tenants_integration(
    db_session: AsyncSession, encryption_key: str
) -> None:
    await as_tenant(db_session, OTHER)
    await repository.upsert_integration(
        db_session,
        tenant_id=OTHER,
        provider="fakecrm",
        secret_ct=secrets.encrypt("theirs", tenant_id=OTHER, provider="fakecrm"),
        secret_hint="eirs",
        field_map={},
    )
    await db_session.commit()
    await as_tenant(db_session, TENANT)

    client = await _client(db_session)
    async with client:
        assert (await client.get("/v1/integrations/fakecrm")).status_code == 404


# ── the seam ──────────────────────────────────────────────────────────────────


async def test_an_adapter_receives_the_secret_already_open(
    db_session: AsyncSession, encryption_key: str
) -> None:
    """`crm.adapter_for` is the only place a credential is decrypted."""
    row = await repository.upsert_integration(
        db_session,
        tenant_id=TENANT,
        provider="fakecrm",
        secret_ct=secrets.encrypt(
            "pat-na1-abcd", tenant_id=TENANT, provider="fakecrm"
        ),
        secret_hint="abcd",
        field_map={"lead_score": "rs_score"},
    )
    await db_session.commit()

    adapter = crm.adapter_for(row)
    assert isinstance(adapter, FakeCrm)
    assert (await adapter.healthcheck()) == (True, "authenticated with pat-na1-abcd")


async def test_a_provider_dropped_from_a_build_reads_as_such(
    db_session: AsyncSession, encryption_key: str
) -> None:
    """A stored row whose adapter no longer exists is a real state — an adapter
    withdrawn between deployments — and it should not crash a consumer."""
    row = await repository.upsert_integration(
        db_session,
        tenant_id=TENANT,
        provider="fakecrm",
        secret_ct=secrets.encrypt(
            "pat-na1-abcd", tenant_id=TENANT, provider="fakecrm"
        ),
        secret_hint="abcd",
        field_map={},
    )
    await db_session.commit()
    crm.registry.pop("fakecrm")

    with pytest.raises(AdapterError) as caught:
        crm.adapter_for(row)
    assert caught.value.retryable is False
