"""
The other four Tier 1 CRMs: Salesforce, Pipedrive, Zoho, Dynamics.

`tests/test_crm_hubspot.py` proves the reference adapter in full. This file
proves the same properties for the four that followed, parametrised rather than
copied four times — the properties are the contract in `app/crm/base.py`, they
are identical by design, and four near-identical files would be four places for
one of them to quietly stop being checked.

The four:

- **one visitor is one record**, across both handoff stages;
- **a replay does not push twice**, because the claim is taken before the call;
- **a withdrawal reaches the copy that already left**, and the row we keep to
  prove it happened no longer names the person;
- **a revoked integration can still retract**, because a withdrawal arriving
  after an admin disconnects a CRM still has to be carried out.

Each vendor then has its own section for the part that is genuinely its own:
Salesforce's External ID field and its bodiless update, Pipedrive's missing
upsert, Zoho's failures that arrive inside an HTTP 200, Dynamics' id in a header.

Every vendor is stubbed at the transport (`tests/crm_transport.py`), so these run
with no network and no credentials. What they prove is that the request we send
is the request the vendor documents — they cannot prove a live account accepts
it, and `docs/roadmap.md` says so.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid

import httpx
import pytest
from neo4j import AsyncSession as GraphSession
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import repository, secrets
from app.consumers.attribution import AttributionConsumer
from app.consumers.crm_delivery import CrmDeliveryConsumer
from app.consumers.crm_retract import CrmRetractConsumer
from app.consumers.identity import IdentityConsumer
from app.consumers.reanonymise import ReAnonymiseConsumer
from app.crm import dynamics, pipedrive, salesforce, zoho
from app.crm.base import AdapterError
from app.models import CrmLink, RuleDispatch
from app.schemas import EventIn
from tests.crm_transport import stub_transport
from tests.test_handoff import (
    BASE,
    S,
    T,
    consent,
    end_the_session,
    seed_activation,
    seed_person_with_a_path,
)

EMAIL = "sam@example.com"
DEDUPE = f"{T}:{EMAIL}"

#: One handoff, built by hand, for the mapping tests that want no database.
HANDOFF = {
    "contact": {
        "email": EMAIL,
        "name": "Sam Rivera",
        "company": "Acme",
        "title": "Head of Brand",
    },
    "activation": {"id": S, "name": "Pavilion No.7", "venue": "Eko"},
    "spatial_intent": {
        "lead_score": 72,
        "zones_visited": ["Entry", "Pod"],
        "dwell_seconds_total": 160.0,
    },
    "consent": {"tier": "T2"},
    "dedupe_key": DEDUPE,
}


@pytest.fixture(autouse=True)
async def _scope_to_test_tenant(db_session: AsyncSession):
    from tests.conftest import as_tenant

    await as_tenant(db_session, T)
    yield


# ── the vendors, at the wire ──────────────────────────────────────────────────


class FakeVendor:
    """A CRM at the wire. Records requests, returns what it is told to.

    Subclasses answer for one vendor. The shared surface is what the
    parametrised tests below use: what was sent, what came back, and what the
    stored credential looks like.
    """

    provider: str
    secret: str
    field_map: dict[str, str]
    record_id: str

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        #: status → for every call, unless a path fragment override matches.
        self.status = 200
        self.overrides: dict[str, int] = {}

    # what each vendor answers
    def respond(self, request: httpx.Request) -> httpx.Response:
        raise NotImplementedError

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status = self.status
        for fragment, override in self.overrides.items():
            if fragment in str(request.url):
                status = override
        if status >= 400:
            return httpx.Response(status, json={"message": "no"})
        return self.respond(request)

    # what the tests ask about
    def is_upsert(self, request: httpx.Request) -> bool:
        raise NotImplementedError

    def sent_fields(self, request: httpx.Request) -> dict:
        """Everything we put on the wire for this record, flattened."""
        raise NotImplementedError

    @property
    def upserts(self) -> list[httpx.Request]:
        return [r for r in self.requests if self.is_upsert(r)]

    @property
    def deletes(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == "DELETE"]

    @property
    def token_calls(self) -> list[httpx.Request]:
        return [r for r in self.requests if "token" in str(r.url)]


def _body(request: httpx.Request) -> dict:
    return json.loads(request.content or b"{}")


class FakeSalesforce(FakeVendor):
    provider = "salesforce"
    secret = json.dumps(
        {
            "instance_url": "https://acme.my.salesforce.com",
            "client_id": "c",
            "client_secret": "s",
            "refresh_token": "r",
        }
    )
    field_map = {
        "spatial_intent.lead_score": "rs_lead_score__c",
        "spatial_intent.zones_visited": "rs_zones__c",
        "spatial_intent.nothing_here": "rs_absent__c",
    }
    record_id = "00Q5f000004abcd"

    #: 204 with no body is what an *update* answers; the tests that want the
    #: bodiless path set this.
    bodiless_update = False

    def respond(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/services/oauth2/token"):
            return httpx.Response(
                200,
                json={
                    "access_token": "tok",
                    "instance_url": "https://acme.my.salesforce.com",
                    "expires_in": 3600,
                },
            )
        if request.method == "DELETE":
            return httpx.Response(204)
        if path.endswith("/describe"):
            return httpx.Response(
                200,
                json={
                    "fields": [
                        {"name": "Email"},
                        {"name": "realmspace_dedupe_key__c"},
                    ]
                },
            )
        if request.method == "PATCH":
            if self.bodiless_update:
                return httpx.Response(204)
            return httpx.Response(
                201, json={"id": self.record_id, "success": True, "created": True}
            )
        if request.method == "GET":
            return httpx.Response(200, json={"Id": self.record_id})
        return httpx.Response(200, json={})

    def is_upsert(self, request: httpx.Request) -> bool:
        return request.method == "PATCH"

    def sent_fields(self, request: httpx.Request) -> dict:
        return _body(request)


class FakePipedrive(FakeVendor):
    provider = "pipedrive"
    secret = "pd-api-token"
    field_map = {
        "spatial_intent.lead_score": "abc123leadscore",
        "spatial_intent.zones_visited": "abc123zones",
        "spatial_intent.nothing_here": "abc123absent",
    }
    record_id = "77"

    #: What a search finds. None = this email is new to the client's CRM.
    existing_person: str | None = None

    def respond(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/v1/users/me"):
            return httpx.Response(200, json={"data": {"id": 1}})
        if request.method == "DELETE":
            return httpx.Response(200, json={"data": {"id": int(self.record_id)}})
        if path.endswith("/persons/search"):
            items = (
                [{"item": {"id": int(self.existing_person)}}]
                if self.existing_person
                else []
            )
            return httpx.Response(200, json={"data": {"items": items}})
        # POST creates, PATCH updates; both answer with the person.
        return httpx.Response(200, json={"data": {"id": int(self.record_id)}})

    def is_upsert(self, request: httpx.Request) -> bool:
        return request.method in ("POST", "PATCH") and "/persons" in request.url.path

    def sent_fields(self, request: httpx.Request) -> dict:
        body = _body(request)
        flat = {k: v for k, v in body.items() if k != "custom_fields"}
        flat.update(body.get("custom_fields") or {})
        # The email lives in a list of objects; lift it so one assertion works
        # across all four vendors.
        for entry in body.get("emails") or []:
            if entry.get("primary"):
                flat["email"] = entry["value"]
        return flat


class FakeZoho(FakeVendor):
    provider = "zoho"
    secret = json.dumps(
        {"client_id": "c", "client_secret": "s", "refresh_token": "r", "region": "eu"}
    )
    field_map = {
        "spatial_intent.lead_score": "Lead_Score_RS",
        "spatial_intent.zones_visited": "Zones_RS",
        "spatial_intent.nothing_here": "Absent_RS",
    }
    record_id = "554000000123456"

    #: A per-record failure inside an HTTP 200, which is Zoho's own shape.
    record_code = "SUCCESS"

    def respond(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/oauth/v2/token"):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        record = {
            "code": self.record_code,
            "message": "record added" if self.record_code == "SUCCESS" else "no",
            "details": {"id": self.record_id},
        }
        if request.method == "DELETE":
            return httpx.Response(200, json={"data": [record]})
        if path.endswith("/Leads/upsert"):
            return httpx.Response(200, json={"data": [record]})
        return httpx.Response(200, json={"data": []})

    def is_upsert(self, request: httpx.Request) -> bool:
        return request.url.path.endswith("/Leads/upsert")

    def sent_fields(self, request: httpx.Request) -> dict:
        return (_body(request).get("data") or [{}])[0]


class FakeDynamics(FakeVendor):
    provider = "dynamics"
    secret = json.dumps(
        {
            "resource_url": "https://acme.crm.dynamics.com",
            "tenant_id": "aad-tenant",
            "client_id": "c",
            "client_secret": "s",
        }
    )
    field_map = {
        "spatial_intent.lead_score": "rs_leadscore",
        "spatial_intent.zones_visited": "rs_zones",
        "spatial_intent.nothing_here": "rs_absent",
    }
    record_id = "11111111-2222-3333-4444-555555555555"

    #: An org that refuses `return=representation` answers 204 with the id in a
    #: header instead.
    header_only = False

    def respond(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/oauth2/v2.0/token"):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        if request.method == "DELETE":
            return httpx.Response(204)
        if path.endswith("/WhoAmI"):
            return httpx.Response(200, json={"UserId": "u"})
        if "/Keys" in path:
            return httpx.Response(
                200, json={"value": [{"KeyAttributes": ["emailaddress1"]}]}
            )
        if request.method == "PATCH":
            if self.header_only:
                return httpx.Response(
                    204,
                    headers={
                        "OData-EntityId": (
                            f"https://acme.crm.dynamics.com/api/data/v9.2/"
                            f"leads({self.record_id})"
                        )
                    },
                )
            return httpx.Response(200, json={"leadid": self.record_id})
        return httpx.Response(200, json={})

    def is_upsert(self, request: httpx.Request) -> bool:
        return request.method == "PATCH"

    def sent_fields(self, request: httpx.Request) -> dict:
        return _body(request)


VENDORS = [FakeSalesforce, FakePipedrive, FakeZoho, FakeDynamics]
VENDOR_IDS = [v.provider for v in VENDORS]


# ── scaffolding ───────────────────────────────────────────────────────────────


@pytest.fixture(params=VENDORS, ids=VENDOR_IDS)
def vendor(request, monkeypatch) -> FakeVendor:
    """One vendor, answering every adapter call, with no network."""
    fake = request.param()
    stub_transport(monkeypatch, fake.handle)
    return fake


@pytest.fixture
async def connected(
    vendor: FakeVendor, db_session: AsyncSession, encryption_key: str
) -> FakeVendor:
    """A tenant with this CRM connected, as an admin would have left it."""
    await repository.upsert_integration(
        db_session,
        tenant_id=T,
        provider=vendor.provider,
        secret_ct=secrets.encrypt(
            vendor.secret, tenant_id=T, provider=vendor.provider
        ),
        secret_hint="cret",
        field_map=vendor.field_map,
    )
    await db_session.commit()
    return vendor


async def run_chain() -> None:
    """The lead chain, in the order `run.py` registers it."""
    await IdentityConsumer().run_once()
    await ReAnonymiseConsumer().run_once()
    await AttributionConsumer().run_once()
    await CrmDeliveryConsumer().run_once()
    await CrmRetractConsumer().run_once()


async def withdraw(db_session: AsyncSession) -> None:
    await repository.append_event(
        db_session,
        EventIn(
            event_id=uuid.uuid4(),
            tenant_id=T,
            session_id=S,
            type="consent.withdrawn",
            payload={
                "consent_id": "c_0001",
                "anon_id": "P-012",
                "reason": "visitor_request",
                "withdrawn_at": (BASE + dt.timedelta(minutes=10)).isoformat(),
            },
            occurred_at=BASE + dt.timedelta(minutes=10),
        ),
    )
    await db_session.commit()


async def links(db_session: AsyncSession) -> list[CrmLink]:
    return list(
        (await db_session.execute(select(CrmLink).order_by(CrmLink.id))).scalars().all()
    )


async def dispatches(db_session: AsyncSession) -> list[RuleDispatch]:
    return list(
        (await db_session.execute(select(RuleDispatch).order_by(RuleDispatch.id)))
        .scalars()
        .all()
    )


def adapter_for(vendor: FakeVendor):
    from app import crm

    return crm.registry[vendor.provider].for_tenant(
        secret=vendor.secret, field_map=vendor.field_map
    )


# ── the four properties, for every vendor ─────────────────────────────────────


async def test_a_consented_visitor_lands_in_the_crm_with_their_path(
    connected: FakeVendor, db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """Phase 4's acceptance, for the four adapters after the reference one."""
    await seed_activation(graph_session)
    await seed_person_with_a_path(graph_session)
    await consent(db_session)
    await run_chain()

    assert len(connected.upserts) == 1
    sent = connected.sent_fields(connected.upserts[0])
    assert EMAIL in json.dumps(sent)

    # The mapped spatial fields went where the tenant said, carrying the figure
    # the attribution consumer computed rather than one this test made up. The
    # field that is mapped but absent from this handoff was dropped rather than
    # sent as null.
    built = (
        await repository.read_events(
            db_session, tenant_id=T, session_id=S, type="handoff.lead", limit=5
        )
    )[0].payload
    mapped = connected.field_map
    assert sent[mapped["spatial_intent.lead_score"]] == built["spatial_intent"]["lead_score"]
    assert sent[mapped["spatial_intent.zones_visited"]] == "Entry;Pod"
    assert mapped["spatial_intent.nothing_here"] not in sent

    stored = await links(db_session)
    assert [(row.provider, row.external_id) for row in stored] == [
        (connected.provider, connected.record_id)
    ]


async def test_both_stages_are_one_record_and_two_claims(
    connected: FakeVendor, db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """`integrations.md` §2: a create-only implementation duplicates every lead."""
    await seed_activation(graph_session)
    await seed_person_with_a_path(graph_session)
    await consent(db_session)
    await run_chain()
    await end_the_session(db_session)
    await run_chain()

    assert len(connected.upserts) == 2
    assert len({row.id for row in await links(db_session)}) == 1

    claims = [d for d in await dispatches(db_session) if d.kind == "handoff"]
    assert len(claims) == 2
    assert {d.status for d in claims} == {"delivered"}


async def test_a_replay_does_not_push_twice(
    connected: FakeVendor, db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """The claim is taken before the call, so a rewound cursor sends nothing."""
    await seed_activation(graph_session)
    await seed_person_with_a_path(graph_session)
    await consent(db_session)
    await run_chain()
    assert len(connected.upserts) == 1

    await repository.reset_cursor(db_session, tenant_id=T, consumer="crm_delivery")
    await db_session.commit()
    await CrmDeliveryConsumer().run_once()

    assert len(connected.upserts) == 1


async def test_a_withdrawal_removes_the_record_and_unnames_the_row(
    connected: FakeVendor, db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """The half of `consent-and-identity.md` §5 that leaves the building."""
    await seed_activation(graph_session)
    await seed_person_with_a_path(graph_session)
    await consent(db_session)
    await run_chain()
    await withdraw(db_session)
    await run_chain()

    assert [connected.record_id in str(r.url) for r in connected.deletes] == [True]

    row = (await links(db_session))[0]
    assert row.retracted_at is not None
    # The receipt survives; the person's name does not.
    assert EMAIL not in row.dedupe_key
    assert row.external_id == connected.record_id


async def test_a_revoked_integration_can_still_retract(
    connected: FakeVendor, db_session: AsyncSession, graph_session: GraphSession
) -> None:
    """A withdrawal the day after an admin disconnects still has to be honoured."""
    await seed_activation(graph_session)
    await seed_person_with_a_path(graph_session)
    await consent(db_session)
    await run_chain()

    await repository.revoke_integration(
        db_session, tenant_id=T, provider=connected.provider
    )
    await db_session.commit()

    await withdraw(db_session)
    await run_chain()

    assert connected.deletes
    assert (await links(db_session))[0].retracted_at is not None


# ── the declines and the failures, for every vendor ───────────────────────────


def test_a_handoff_with_no_email_is_declined_not_invented(vendor: FakeVendor) -> None:
    """`base.py`: `None` is "nothing here to send", not a failure."""
    assert (
        adapter_for(vendor).map({"dedupe_key": f"{T}:P-012", "spatial_intent": {}})
        is None
    )


async def test_a_rate_limit_is_raised_for_the_consumer_to_retry(
    vendor: FakeVendor,
) -> None:
    """`integrations.md` §8's first named failure. One retry policy, not two."""
    vendor.status = 429
    adapter = adapter_for(vendor)
    with pytest.raises(AdapterError) as raised:
        await adapter.upsert(adapter.map(HANDOFF))
    assert raised.value.retryable is True
    assert "429" in str(raised.value)


async def test_a_dead_token_says_so_and_is_not_retryable(vendor: FakeVendor) -> None:
    """A 401 needs somebody to reconnect the account; retrying cannot help."""
    vendor.status = 401
    adapter = adapter_for(vendor)
    with pytest.raises(AdapterError) as raised:
        await adapter.upsert(adapter.map(HANDOFF))
    assert raised.value.retryable is False
    assert "credential" in str(raised.value)


async def test_retracting_something_already_gone_succeeds_quietly(
    vendor: FakeVendor,
) -> None:
    """A replayed withdrawal finds the record gone, which is the right answer."""
    vendor.overrides = {f"leads({vendor.record_id})": 404}
    vendor.overrides.update(
        {
            f"/{vendor.record_id}": 404,
            f"({vendor.record_id})": 404,
        }
    )
    detail = await adapter_for(vendor).retract(vendor.record_id)
    assert "already gone" in detail


async def test_the_healthcheck_answers_for_a_good_credential(
    vendor: FakeVendor,
) -> None:
    ok, detail = await adapter_for(vendor).healthcheck()
    assert ok, detail


async def test_a_mandatory_name_nobody_gave_us_says_so(vendor: FakeVendor) -> None:
    """Three of these CRMs refuse a lead with no surname; none of them get a guess.

    Pipedrive has no mandatory name and is included anyway — the point is that
    no adapter derives "Rivera" from an email address.
    """
    handoff = {**HANDOFF, "contact": {"email": EMAIL}}
    sent = json.dumps(adapter_for(vendor).map(handoff))
    assert "sam" not in sent.replace(EMAIL, "")


# ── Salesforce ────────────────────────────────────────────────────────────────


async def test_salesforce_upserts_on_the_external_id_field() -> None:
    """The key is our dedupe key, not their email — see the module docstring."""
    fake = FakeSalesforce()
    adapter = salesforce.SalesforceAdapter.for_tenant(
        secret=fake.secret, field_map=fake.field_map
    )
    with pytest.MonkeyPatch.context() as mp:
        stub_transport(mp, fake.handle)
        assert await adapter.upsert(adapter.map(HANDOFF)) == fake.record_id

    # `str(url)` rather than `url.path`, which httpx hands back decoded — the
    # point of this assertion is that the key is escaped into the path.
    url = str(fake.upserts[0].url)
    assert "/sobjects/Lead/realmspace_dedupe_key__c/" in url
    assert "sam%40example.com" in url


async def test_salesforce_asks_for_the_id_when_an_update_answers_with_no_body() -> None:
    """A `crm_link` with no `external_id` is a withdrawal that cannot be carried
    out, so the id is worth one extra call on the update path."""
    fake = FakeSalesforce()
    fake.bodiless_update = True
    adapter = salesforce.SalesforceAdapter.for_tenant(
        secret=fake.secret, field_map={}
    )
    with pytest.MonkeyPatch.context() as mp:
        stub_transport(mp, fake.handle)
        assert await adapter.upsert(adapter.map(HANDOFF)) == fake.record_id

    assert [r.method for r in fake.requests if "sobjects/Lead" in r.url.path] == [
        "PATCH",
        "GET",
    ]


async def test_salesforce_healthcheck_fails_when_the_org_lacks_the_field() -> None:
    """A connection that tests green and fails on every lead is worse than one
    that fails at setup."""
    fake = FakeSalesforce()
    adapter = salesforce.SalesforceAdapter.for_tenant(
        secret=json.dumps(
            {
                "instance_url": "https://acme.my.salesforce.com",
                "client_id": "c",
                "client_secret": "s",
                "refresh_token": "r",
                "external_id_field": "not_there__c",
            }
        ),
        field_map={},
    )
    with pytest.MonkeyPatch.context() as mp:
        stub_transport(mp, fake.handle)
        ok, detail = await adapter.healthcheck()

    assert ok is False
    assert "not_there__c" in detail


async def test_salesforce_takes_the_instance_url_the_token_returns() -> None:
    """A My Domain change or a sandbox refresh moves the host, and the token
    response is the only thing that knows."""
    fake = FakeSalesforce()
    adapter = salesforce.SalesforceAdapter.for_tenant(
        secret=json.dumps(
            {
                "instance_url": "https://stale.my.salesforce.com",
                "client_id": "c",
                "client_secret": "s",
                "refresh_token": "r",
            }
        ),
        field_map={},
    )
    with pytest.MonkeyPatch.context() as mp:
        stub_transport(mp, fake.handle)
        await adapter.upsert(adapter.map(HANDOFF))

    assert adapter._base_url() == "https://acme.my.salesforce.com"


# ── Pipedrive ─────────────────────────────────────────────────────────────────


async def test_pipedrive_updates_the_person_we_already_pushed() -> None:
    """The id from `crm_link` is what stands in for the upsert Pipedrive lacks."""
    fake = FakePipedrive()
    adapter = pipedrive.PipedriveAdapter.for_tenant(secret=fake.secret, field_map={})
    with pytest.MonkeyPatch.context() as mp:
        stub_transport(mp, fake.handle)
        assert await adapter.upsert(adapter.map(HANDOFF), external_id="77") == "77"

    assert [r.method for r in fake.requests] == ["PATCH"]
    assert "/persons/search" not in " ".join(str(r.url) for r in fake.requests)


async def test_pipedrive_updates_a_person_the_client_already_had() -> None:
    """Creating a second Person for an email their CRM already knows is the
    duplication we are trying to avoid."""
    fake = FakePipedrive()
    fake.existing_person = "99"
    adapter = pipedrive.PipedriveAdapter.for_tenant(secret=fake.secret, field_map={})
    with pytest.MonkeyPatch.context() as mp:
        stub_transport(mp, fake.handle)
        assert await adapter.upsert(adapter.map(HANDOFF)) == "99"

    assert [r.method for r in fake.requests] == ["GET", "PATCH"]


async def test_pipedrive_recreates_a_person_the_client_deleted() -> None:
    """They removed a record; they did not ask us to stop sending leads."""
    fake = FakePipedrive()
    fake.overrides = {"/persons/77": 404}
    adapter = pipedrive.PipedriveAdapter.for_tenant(secret=fake.secret, field_map={})
    with pytest.MonkeyPatch.context() as mp:
        stub_transport(mp, fake.handle)
        assert await adapter.upsert(adapter.map(HANDOFF), external_id="77") == "77"

    assert [r.method for r in fake.requests] == ["PATCH", "GET", "POST"]


def test_pipedrive_creates_no_organisation_for_a_company() -> None:
    """Inventing an Organization per booth visitor would litter a client's CRM
    with companies their sales team never agreed to."""
    fake = FakePipedrive()
    adapter = pipedrive.PipedriveAdapter.for_tenant(secret=fake.secret, field_map={})
    payload = adapter.map(HANDOFF)
    assert "org_id" not in payload["person"]
    assert "organization" not in json.dumps(payload["person"]).lower()


# ── Zoho ──────────────────────────────────────────────────────────────────────


async def test_zoho_refuses_a_failure_that_arrives_inside_a_200() -> None:
    """A destination that reported success because the transport succeeded would
    put leads on the delivered pile that Zoho refused."""
    fake = FakeZoho()
    fake.record_code = "MANDATORY_NOT_FOUND"
    adapter = zoho.ZohoAdapter.for_tenant(secret=fake.secret, field_map={})
    with pytest.MonkeyPatch.context() as mp:
        stub_transport(mp, fake.handle)
        with pytest.raises(AdapterError) as raised:
            await adapter.upsert(adapter.map(HANDOFF))

    assert raised.value.retryable is False
    assert "MANDATORY_NOT_FOUND" in str(raised.value)


async def test_zoho_talks_to_the_datacentre_the_credential_names() -> None:
    """A token issued in the EU sent to the .com host fails as a bad token,
    which sends an admin off to rotate a credential that was fine."""
    fake = FakeZoho()
    adapter = zoho.ZohoAdapter.for_tenant(secret=fake.secret, field_map={})
    with pytest.MonkeyPatch.context() as mp:
        stub_transport(mp, fake.handle)
        await adapter.upsert(adapter.map(HANDOFF))

    hosts = {r.url.host for r in fake.requests}
    assert hosts == {"accounts.zoho.eu", "www.zohoapis.eu"}


def test_zoho_keys_the_upsert_on_the_email() -> None:
    fake = FakeZoho()
    adapter = zoho.ZohoAdapter.for_tenant(secret=fake.secret, field_map={})
    assert adapter.map(HANDOFF)["duplicate_check_fields"] == ["Email"]


# ── Dynamics ──────────────────────────────────────────────────────────────────


async def test_dynamics_upserts_against_the_alternate_key() -> None:
    fake = FakeDynamics()
    adapter = dynamics.DynamicsAdapter.for_tenant(secret=fake.secret, field_map={})
    with pytest.MonkeyPatch.context() as mp:
        stub_transport(mp, fake.handle)
        assert await adapter.upsert(adapter.map(HANDOFF)) == fake.record_id

    assert "leads(emailaddress1='sam%40example.com')" in str(fake.upserts[0].url)


async def test_dynamics_reads_the_id_from_the_header_when_there_is_no_body() -> None:
    """The fallback for an org that refuses `return=representation`."""
    fake = FakeDynamics()
    fake.header_only = True
    adapter = dynamics.DynamicsAdapter.for_tenant(secret=fake.secret, field_map={})
    with pytest.MonkeyPatch.context() as mp:
        stub_transport(mp, fake.handle)
        assert await adapter.upsert(adapter.map(HANDOFF)) == fake.record_id


async def test_dynamics_healthcheck_fails_without_the_alternate_key() -> None:
    fake = FakeDynamics()

    def handle(request: httpx.Request) -> httpx.Response:
        if "/Keys" in request.url.path:
            return httpx.Response(200, json={"value": []})
        return fake.handle(request)

    adapter = dynamics.DynamicsAdapter.for_tenant(secret=fake.secret, field_map={})
    with pytest.MonkeyPatch.context() as mp:
        stub_transport(mp, handle)
        ok, detail = await adapter.healthcheck()

    assert ok is False
    assert "alternate key" in detail


# ── OAuth ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "fake_cls", [FakeSalesforce, FakeZoho, FakeDynamics], ids=["salesforce", "zoho", "dynamics"]
)
async def test_the_access_token_is_fetched_once_per_lead(fake_cls) -> None:
    """One refresh per delivery, not one per call — and never a process-wide
    cache, for the reasons in `app/crm/oauth.py`."""
    fake = fake_cls()
    adapter = adapter_for(fake)
    with pytest.MonkeyPatch.context() as mp:
        stub_transport(mp, fake.handle)
        await adapter.upsert(adapter.map(HANDOFF))
        await adapter.retract(fake.record_id)

    assert len(fake.token_calls) == 1


async def test_zoho_treats_a_record_it_cannot_find_as_already_gone() -> None:
    """Zoho reports a missing record in the body of an HTTP 200, not as a 404.

    So the `status == 404` arm every other adapter relies on never fires here,
    and a replayed withdrawal for a lead already deleted would retry to
    exhaustion and park on `/ops` claiming a retraction failed that had in fact
    already succeeded — against what `repository.links_for_contact` says a
    replayed withdrawal should find.
    """
    fake = FakeZoho()
    fake.record_code = "RESOURCE_NOT_FOUND"
    adapter = zoho.ZohoAdapter.for_tenant(secret=fake.secret, field_map={})

    with pytest.MonkeyPatch.context() as mp:
        stub_transport(mp, fake.handle)
        detail = await adapter.retract(fake.record_id)

    assert "already gone" in detail


async def test_a_short_lived_token_is_not_cached_past_its_life() -> None:
    """The margin exists to refresh *early*; it must never extend the window.

    `max(lifetime - margin, margin)` did the second thing, so a token good for
    45 seconds was held for 60 and the last 15 were guaranteed 401s on a lead.
    """
    fake = FakeZoho()

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth/v2/token"):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 45})
        return fake.handle(request)

    adapter = zoho.ZohoAdapter.for_tenant(secret=fake.secret, field_map={})
    with pytest.MonkeyPatch.context() as mp:
        stub_transport(mp, handle)
        await adapter._access_token()

    now = dt.datetime.now(dt.timezone.utc)
    assert adapter._token_expires_at <= now + dt.timedelta(seconds=45)
