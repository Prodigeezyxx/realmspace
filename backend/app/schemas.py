"""
Wire shapes. Pydantic validates these before anything touches the database, so a
malformed producer gets a 422 instead of a half-written row.

On the wire these are camelCase with ms-epoch timestamps, matching the canonical
contract in dashboard/src/lib/contracts/events.ts. In Python they stay snake_case,
and producers may POST either — see EventOut.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from app.consumers.ids import CAMERA_SEP


# The namespaces in event-bus-spec.md §3. Deliberately a prefix check, not an
# allow-list of full type names: §3 says the taxonomy is "namespaced and
# additive-only", so a new spatial.* type must not need a code change here.
#
# Be clear about what this does and does not buy. It rejects an unknown or
# misspelled *namespace* — "spatal.zone_enter", or a type from a system that
# has no business on this bus. It does **not** catch a typo in the suffix:
# "spatial.zone_entr" starts with "spatial." and passes.
#
# That residual gap is real. Such an event is accepted, stored forever in an
# append-only table, and then matched by no consumer's `handles`, so it is
# silently never processed. Closing it properly means an allow-list of full
# type names, which trades the additive-only property for strictness — worth
# revisiting once the taxonomy stops growing.
EVENT_NAMESPACES = (
    "perception.",
    "spatial.",
    "surface.",
    # Week 1 task 1.11 ships an RFID producer emitting rfid.read. Added ahead of
    # it because a namespace this validator doesn't know is a 422, and POD 1
    # would hit that on their first request with nothing to explain it.
    "rfid.",
    "consent.",
    "identity.",
    "rule.",
    "handoff.",
    "insight.",
    "cost.",
    "session.",
    # Pre-registered for Phase 3, for the same reason rfid. was: a namespace this
    # validator has never heard of is a 422, and the producer's author gets no
    # hint that the taxonomy is the thing refusing them. Registering the
    # namespace ahead of the producer costs a line here and saves that.
    #: intent.scored — the intent-scoring consumer (P4 lead capture).
    "intent.",
    #: drift.detected — CV drift telemetry (roadmap.md, Phase 6).
    "drift.",
    #: calibration.updated — the calibration UI (roadmap.md, Phase 6).
    "calibration.",
    #: crm.retract — the re-anonymiser on withdrawal (consent-and-identity.md §5).
    "crm.",
    #: outcome.recorded — what a lead turned into. Added with the attribution
    #: ledger; nothing in the repo had ever defined an outcome, which is why
    #: every attribution claim in the docs rested on a thing that did not exist.
    "outcome.",
    #: followup.drafted — the contextual SDR (roadmap.md Phase 5). Its own
    #: namespace rather than an `insight.` type: an insight is about the room, a
    #: draft is about one person who agreed to be contacted, and the two want
    #: different handling everywhere PII is handled.
    "followup.",
    #: erasure.requested / erasure.completed — GDPR Art. 17, the job
    #: `consent-and-identity.md` §5 asks for. Its own namespace rather than a
    #: `consent.` type, because an erasure is not a consent decision: it outranks
    #: one, it is authorised by an admin rather than given by the visitor, and it
    #: is the only thing in the system that rewrites the log.
    "erasure.",
    #: retention.purge_requested / retention.purged — `gtm.md`'s "30-day data
    #: retention", enforced. Its own namespace and not an `erasure.` type: that
    #: one is a person exercising a right over their own data, this is a window
    #: expiring over everybody's, and conflating them would put rows nobody
    #: asked about into the answer an auditor reads.
    "retention.",
)


def _to_camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(word.capitalize() for word in rest)


class EventIn(BaseModel):
    """What a producer POSTs.

    event_id is supplied by the *producer*, not generated here. That is what
    makes retries safe: if the POST times out and the producer resends, it sends
    the same event_id and the log dedupes it (event-bus-spec.md §2).

    Field names are snake_case in Python and camelCase on the wire — see the
    note on EventOut for why.
    """

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    event_id: uuid.UUID
    tenant_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    type: str = Field(min_length=1)
    payload: dict[str, Any]
    occurred_at: dt.datetime

    @field_validator("type")
    @classmethod
    def type_is_in_a_known_namespace(cls, value: str) -> str:
        if not value.startswith(EVENT_NAMESPACES):
            raise ValueError(
                f"unknown event namespace in {value!r}; "
                f"expected one of {', '.join(EVENT_NAMESPACES)} "
                "(taxonomy: docs/event-bus-spec.md §3)"
            )
        return value


class ShareCreate(BaseModel):
    """What an operator asks for when they want a link for a client."""

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    #: Whose copy this is, for the operator's own list. Never shown to the
    #: person opening the link — it is a note to self, not a greeting.
    label: str | None = None
    #: Bounded by `app.share.MAX_EXPIRY_DAYS`, and `expires_at` is NOT NULL, so
    #: a link that never dies cannot be asked for.
    expires_in_days: int = Field(default=30, ge=1, le=365)


class ShareOut(BaseModel):
    """One link, as its creator sees it. **Never carries the token.**

    `hint` is the last four characters, which answers the only question a list
    needs to — "is this the link I sent on Tuesday" — without the value being
    retrievable. Same reasoning as `secretHint` on a stored credential.
    """

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    id: str
    session_id: str
    label: str | None
    hint: str
    created_by: str
    created_at: dt.datetime
    expires_at: dt.datetime
    revoked_at: dt.datetime | None
    last_viewed_at: dt.datetime | None
    view_count: int
    #: Derived rather than stored, so "is this live" cannot drift from the two
    #: columns that decide it.
    active: bool


class ShareCreated(ShareOut):
    """The one response that carries the token, at the one moment it exists.

    There is no endpoint that returns it a second time. That is not a missing
    feature: a value nobody can read back is a value that cannot leak from the
    database, and the operator's copy of the link is the URL in their hand.
    """

    token: str


class TabletCreate(BaseModel):
    """What an operator asks for when they want to hand a touchpoint a tablet.

    Its own model rather than `ShareCreate`, for one field's sake and
    deliberately: a share link's month is the life of a conversation with a
    client after the stand comes down, and a tablet's is the activation. Reusing
    `ShareCreate` here left `touch.DEFAULT_EXPIRY_DAYS` reachable by nothing —
    a constant with an argument in its comment and no call site, which is what
    `plans.py` warns about a limit nothing reads.
    """

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    #: The operator's note to self — "the plinth by the door". Never shown to
    #: whoever taps it.
    label: str | None = None
    #: Bounded by `app.touch.MAX_EXPIRY_DAYS`, and `expires_at` is NOT NULL, so
    #: a tablet that never expires cannot be asked for.
    expires_in_days: int = Field(default=14, ge=1, le=365)


class TouchpointTokenOut(BaseModel):
    """One tablet, as the operator who set it up sees it. **Never the token.**

    `ShareOut`'s shape, with `surfaceId` — which touchpoint this tablet is — and
    with usage rather than views. The counts are deliberately not the same
    number as the report's: see `touch.note_use`.
    """

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    id: str
    session_id: str
    surface_id: str
    label: str | None
    hint: str
    created_by: str
    created_at: dt.datetime
    expires_at: dt.datetime
    revoked_at: dt.datetime | None
    last_used_at: dt.datetime | None
    use_count: int
    #: Derived from `revoked_at` and `expires_at` rather than stored, so a
    #: column can never disagree with the two that decide it. `ShareOut` does
    #: the same.
    active: bool


class TouchpointTokenCreated(TouchpointTokenOut):
    """The one response that carries the token, at the one moment it exists.

    `ShareCreated`'s reasoning without a word changed: no endpoint returns it a
    second time, and the operator's copy is the URL now open on the tablet.
    """

    token: str


class CapturedContact(BaseModel):
    """The PII half of a consent, and the only PII in the payload.

    Lives here rather than in `routers/consent.py`, where it was written,
    because two capture surfaces now build one: the operator-credentialled
    endpoint and the kiosk a member of the public is standing at. Two copies of
    this model would be two answers to "what may a consent carry about
    somebody", and `erasure.CONTACT_PII` redacts against exactly one list.

    Every field optional: a badge scan may carry nothing at capture time, with
    the details arriving from the registry later. Everything outside this object
    is anonymous, which is what lets a deployment keep the consent record after
    erasing the person it was about.
    """

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    email: str | None = Field(default=None, max_length=320)
    name: str | None = Field(default=None, max_length=200)
    company: str | None = Field(default=None, max_length=200)
    title: str | None = Field(default=None, max_length=200)


class KioskCreate(BaseModel):
    """What an operator asks for when they want a consent kiosk on a plinth.

    `TabletCreate`'s shape and its expiry, for its reason — an activation, not a
    conversation. Its own model rather than a shared one because the two
    credentials are deliberately separate: see migration 0016.
    """

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    #: The operator's note to self — "the QR by the coffee". Never shown to the
    #: visitor, who sees the touchpoint's own label instead.
    label: str | None = None
    #: Bounded by `app.kiosk.MAX_EXPIRY_DAYS`, and `expires_at` is NOT NULL, so
    #: a kiosk link that never expires cannot be asked for.
    expires_in_days: int = Field(default=14, ge=1, le=365)


class KioskTokenOut(BaseModel):
    """One kiosk, as the operator who set it up sees it. **Never the token.**

    `TouchpointTokenOut`'s shape. The counts are that file's counts and not the
    report's: `kiosk.note_use` counts scans, including the visitors who read the
    copy and declined, and the log counts consents.
    """

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    id: str
    session_id: str
    surface_id: str
    label: str | None
    hint: str
    created_by: str
    created_at: dt.datetime
    expires_at: dt.datetime
    revoked_at: dt.datetime | None
    last_used_at: dt.datetime | None
    use_count: int
    #: Derived from `revoked_at` and `expires_at` rather than stored, so a
    #: column can never disagree with the two that decide it.
    active: bool


class KioskTokenCreated(KioskTokenOut):
    """The one response that carries the token, at the one moment it exists.

    Whatever an operator prints the QR code from is the last copy; no endpoint
    returns it again.
    """

    token: str


class KioskOut(BaseModel):
    """What the visitor's phone is told, and the whole of it.

    The wording they are about to agree to, the version of it, and what tier
    they are being asked for. No activation name beyond the touchpoint's own
    label, no counts, no visitors — `TabletOut` refuses the same things for the
    same reason: whoever is holding this phone is a member of the public.

    `copyText` and `copyVersion` travel together and are the point of this
    endpoint.
    `event-bus-spec.md` §3: *"a capture surface that cannot say which wording it
    displayed has not really captured consent"* — so the surface reads both from
    the activation rather than carrying its own, and an operator who corrects
    the wording in the wizard changes what the stand shows without re-minting
    anything or walking round with a laptop.
    """

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    surface_id: str
    label: str
    #: `copy_text`, not `copy`: pydantic's `BaseModel.copy` is a method, and a
    #: field that shadows one is a warning today and somebody's confusing bug
    #: later.
    copy_text: str
    copy_version: str
    tier: Literal["T1", "T2", "T3"]
    basis: Literal["explicit_optin", "contract", "legitimate_interest"]


class ConsentGivenIn(BaseModel):
    """What a kiosk knows about the consent in front of it.

    No `anonId`: a plinth has no camera. Which visitor this was is
    `consumers/kiosk_consent.py`'s question, answered from zone occupancy, and
    the consent is captured either way — see that consumer's docstring for why
    it does not refuse the way `consumers/touch.py` does.

    No `tier` or `copyVersion` either, and that is the load-bearing half: both
    come off the activation the token resolves to. A surface that could name its
    own tier could claim a T3 for somebody who was shown the T1 wording, and the
    consent record is the evidence a disputed withdrawal is settled by.
    """

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    #: Minted in the browser **before** the copy is shown, not when the request
    #: is sent. `routers/consent.py`'s module docstring has the argument and it
    #: applies here unchanged: it is what makes a retry over bad venue wifi one
    #: consent rather than two records of one conversation.
    consent_id: str = Field(min_length=1, max_length=128)
    #: When they pressed agree, on the phone's own clock. Defaults to arrival,
    #: which is wrong by exactly the length of an outage, and is why a queued
    #: consent sends its own.
    at: dt.datetime | None = None
    contact: CapturedContact | None = None


class TouchpointIn(BaseModel):
    """What the tablet knows about its own tap, which is not very much.

    No `anonId`: a tablet has no camera and cannot know who pressed it.
    Attributing the tap to a visitor is `consumers/touch.py`'s job, where zone
    occupancy is, and it refuses more often than it succeeds.
    """

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    #: Minted by the tablet when the finger lands, not when the request is sent.
    #: Same rule as `consentId` and as `perception/bus_client.py`'s event ids:
    #: it is what makes a retry over bad venue wifi one tap rather than two.
    touch_id: str = Field(min_length=1, max_length=128)
    #: What kind of interaction the touchpoint offers — "tap", "sample",
    #: "scan". The operator's word, carried through to `INTERACTED_WITH.kind`.
    kind: str = Field(default="tap", min_length=1, max_length=64)
    #: When the finger landed. Defaults to arrival, which is wrong by exactly
    #: the length of an outage, and is why a queued tap sends its own.
    at: dt.datetime | None = None


class TabletOut(BaseModel):
    """What the tablet is told about itself, and the whole of it.

    A label to put on the button and nothing else — not the activation, not the
    other touchpoints, not a count. Whoever is holding the tablet is a member of
    the public.
    """

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    surface_id: str
    label: str
    kind: str


class EventOut(EventIn):
    """What comes back out — over HTTP and over the WebSocket.

    ## Why this is camelCase with millisecond timestamps

    `dashboard/src/lib/contracts/events.ts` declares itself the canonical event
    shape, in its own words: "Both the mocked prototype and the future FastAPI +
    Postgres backend conform to THIS type." That type is:

        { seq, eventId, tenantId, sessionId, type, payload,
          occurredAt: number, recordedAt: number }   // ms epoch

    We were emitting `event_id` and ISO date strings, i.e. not conforming. Fixed
    here rather than left for the browser to paper over, because a mapper on the
    client is a permanent tax and this is the last moment it is free — nothing
    consumes the API yet except our own tests.

    `populate_by_name=True` means producers can still POST snake_case, so the
    Python side (perception, tests, curl) is unaffected.
    """

    model_config = ConfigDict(
        from_attributes=True, alias_generator=_to_camel, populate_by_name=True
    )

    seq: int
    recorded_at: dt.datetime

    @field_serializer("occurred_at", "recorded_at")
    def _as_epoch_ms(self, value: dt.datetime) -> int:
        """Timestamps go out as ms-epoch integers, per the canonical contract.

        Naive datetimes are treated as UTC. Postgres gives us TIMESTAMPTZ so in
        practice they always carry a zone, but a naive one silently interpreted
        as local time would shift every event by the UTC offset — worth being
        explicit about rather than lucky.
        """
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.timezone.utc)
        return int(value.timestamp() * 1000)


# ── session configuration ─────────────────────────────────────────────────────
#
# What an operator sets **before** the activation runs, per roi-framework.md §5:
# the zones, their weights, the funnel, the engagement threshold, the cost and
# the attribution model. Everything the report divides by.
#
# These are the wizard's shapes, not new ones. `dashboard/src/lib/session/types.ts`
# already declares `Zone { id, name, type, capacity, color, polygon }` and stores
# timestamps as ISO strings, so that is what goes on the wire here — same
# reasoning as EventOut's camelCase: the browser's contract is canonical and a
# mapper on the client would be a permanent tax.


def normalized_polygon(
    value: list[tuple[float, float]] | None,
    *,
    what: str,
    where: str,
) -> list[tuple[float, float]] | None:
    """Reject polygons that could only ever be silently ignored.

    Shared by zones and by camera privacy masks, because both are drawn by the
    same editor into the same 0..1 coordinate space and both fail the same two
    ways — and a mask validated more loosely than a zone would be a mask that
    passes validation and covers nothing.

    Two failures, both otherwise invisible. Fewer than three points is not a
    shape, and `point_in_polygon` returns False for it forever — every detection
    inside the zone the operator thinks they drew is attributed nowhere, and the
    report simply shows less traffic than there was. For a mask the same bug is
    worse: the pixels go to the model.

    Coordinates outside 0..1 mean pixels were sent where normalized booth
    coordinates were expected. `consumers/zones.py:normalize` divides every
    detection by the frame size before comparing, so a pixel polygon matches
    nothing at all. This is the one place that mistake is cheap to catch.
    """
    if value is None:
        return None
    if len(value) < 3:
        raise ValueError(
            f"a polygon needs at least 3 points, got {len(value)} — "
            "fewer is not a shape and would contain nobody"
        )
    for x, y in value:
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            raise ValueError(
                f"polygon point ({x}, {y}) is outside 0..1 — {what} are "
                f"normalized booth coordinates, not pixels ({where})"
            )
    return value


class ZoneConfig(BaseModel):
    """One zone as the session wizard draws it, plus its measurement parameters.

    `type` is a free string rather than an enum, deliberately. There are already
    two zone vocabularies in the dashboard — `ZoneKind` in contracts/graph.ts
    (7 values) and `ZoneType` in session/types.ts (11, including `reveal` and
    `privacy_masked`) — and the wizard produces the second. An enum here would
    422 on a zone the operator can legitimately draw, and nothing in the backend
    branches on the value. Accepted as `type`, or `kind` for the graph contract's
    spelling, so either shape posts cleanly.
    """

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    type: str = Field(default="other", validation_alias=AliasChoices("type", "kind"))
    polygon: list[tuple[float, float]] | None = None
    #: Which camera's frame this polygon is drawn in.
    #:
    #: A polygon is normalized 0..1 *within one frame*, so the same coordinates
    #: name different floor in different cameras. Without an owner, a detection
    #: from the camera pointing at the entrance is tested against the polygons
    #: drawn over the demo stand and lands in a zone nobody walked into.
    #:
    #: **None means every camera**, which is what every zone drawn before this
    #: existed is, so a one-camera booth is unaffected. On a multi-camera
    #: session an unowned zone is the operator saying "score this against
    #: whatever sees it" — worth allowing, because a booth may genuinely have
    #: two cameras covering one stand from either side.
    camera_id: str | None = None
    color: str | None = None
    capacity: int | None = Field(default=None, ge=0)
    #: roi-framework.md §2 — dwell-weighted attention is Σ(dwell × weight).
    weight: float = Field(default=1.0, ge=0)
    #: Position in the entry → experience → product → capture funnel (§5).
    funnel_order: int | None = Field(default=None, ge=0)

    @field_validator("polygon")
    @classmethod
    def polygon_is_a_normalized_shape(
        cls, value: list[tuple[float, float]] | None
    ) -> list[tuple[float, float]] | None:
        return normalized_polygon(value, what="zone polygons", where="data-model.md → Zone")


class TouchpointConfig(BaseModel):
    """A booth surface an operator installed — data-model.md → `(:Surface {...})`.

    Same free-string reasoning as `ZoneConfig.type`: the wizard's
    `TouchpointType` has sixteen values and the graph contract's `SurfaceNode`
    describes a different set, so an enum here would reject a touchpoint the
    operator can legitimately configure. Nothing in the backend branches on it.
    """

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    type: str = Field(default="other", validation_alias=AliasChoices("type", "kind"))
    #: Which zone it sits in, if any. Not validated against the zone list: an
    #: operator may add the touchpoint before drawing the zone around it.
    zone_id: str | None = None
    active: bool = True


class CameraConfig(BaseModel):
    """One camera a booth has. The home `camera_id` never had.

    Deliberately thin. `drift.detected` and `calibration.updated` need somewhere
    to point, and the privacy mask needs an owner; everything else about a
    camera — lens, mount, resolution — is either not ours to know or arrives on
    each detection already.

    The mask is **not** settable here. See `CameraOut` and
    `POST /v1/sessions/{id}/cameras/{camera_id}/calibration`: a mask edit is an
    audited operator action with a revision, not a field on a config save that
    could clear it by omission.
    """

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    id: str = Field(min_length=1)
    label: str | None = None

    @field_validator("id")
    @classmethod
    def id_has_no_namespace_separator(cls, value: str) -> str:
        """No `/`, because a track id is namespaced `camera_id/anon_id`.

        `consumers/ids.person_key` joins the two with `CAMERA_SEP` to keep two
        cameras' `P-001` apart. Allow the separator inside a camera id and the
        join stops being reversible — `("cam/1", "P-2")` and `("cam", "1/P-2")`
        produce the same key — which is the collision the namespace exists to
        prevent, reintroduced one level up. Refused at the only place a camera
        id is chosen, so nothing downstream has to escape anything.
        """
        if CAMERA_SEP in value:
            raise ValueError(
                f"camera id may not contain {CAMERA_SEP!r}: it separates the camera "
                "from the track id it namespaces (consumers/ids.py)"
            )
        return value


class CameraOut(CameraConfig):
    """A camera as stored, with the mask an operator drew and its revision.

    `mask_revision` is on the read side because perception polls it: a cheap
    integer comparison tells an edge box whether the mask it cached is still
    current, without shipping the polygon on every check.
    """

    label: str
    privacy_mask: list[tuple[float, float]] | None = None
    mask_revision: int = 0


class SessionConfigIn(BaseModel):
    """The wizard's output: one activation, configured for measurement."""

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    session_id: str = Field(min_length=1)
    client: str | None = None
    campaign: str | None = None
    venue: str | None = None
    city: str | None = None
    started_at: str | None = None
    ends_at: str | None = None
    booth_width_m: float | None = Field(default=None, gt=0)
    booth_depth_m: float | None = Field(default=None, gt=0)
    camera_count: int | None = Field(default=None, ge=0)

    #: Above this, a visit counts as engaged (roi-framework.md §2, Layer 2).
    #: 60s matches the default in dashboard/src/lib/roi/scorecard.ts.
    engaged_threshold_seconds: float = Field(default=60.0, gt=0)
    #: Total cost of the activation — the denominator of CPEV, CPQL and ROI.
    activation_cost: float | None = Field(default=None, ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    #: roi-framework.md §3. "influenced" by default: the simplest to defend.
    attribution_model: Literal[
        "first_touch", "last_touch", "linear", "time_decay", "influenced"
    ] = "influenced"

    #: How long after a booth touch an outcome may still be attributed to it.
    #: roi-framework.md §2 calls for "configurable 30/60/90-day windows on the
    #: outcome edge"; 90 is the default because it is the longest of the three,
    #: and a window widened after the fact to capture a deal that closed late is
    #: exactly the argument §3's design principle rules out. Narrow it before the
    #: doors open if the client's sales cycle is shorter.
    #:
    #: A closed set rather than a free integer for the same reason
    #: `attribution_model` is: these are the three windows a CFO recognises, and
    #: an arbitrary 47 is a number somebody chose to make a ratio work.
    attribution_window_days: Literal[30, 60, 90] = 90

    #: **Operator-supplied, not measured.** Influenced revenue comes from CRM
    #: attribution, which is Phase 4; until then the only honest sources are the
    #: client's own figure or nothing at all. Left null, the ROI ratio reports as
    #: unknown rather than as zero — those are different answers and the report
    #: must not conflate them. Anything displaying these states their origin.
    revenue_influenced: float | None = Field(default=None, ge=0)
    qualified_leads: int | None = Field(default=None, ge=0)

    #: Emit a `handoff.lead` for the visitors who never consented, carrying
    #: spatial intent and no contact — `integrations.md` §2's anonymous handoff.
    #:
    #: **Off unless the operator asks for it**, which is the opposite of every
    #: other flag here. A busy day is several hundred of them, each one a lead
    #: object with nobody in it; a tenant who has not asked for aggregate reach
    #: in their own stack should not find their destinations carrying it. Turned
    #: on, it is one handoff per un-consented person at `session.ended`.
    anonymous_handoffs: bool = False

    #: How often the insight agent summarises the room, in minutes of **event
    #: time**. 10 matches `PRD.md`'s "LLM-generated insights every 10 minutes".
    #:
    #: Bounded below at 1 because the window is what an insight is computed from
    #: — a sub-minute window on a busy floor says "two people entered a zone",
    #: which is a reading rather than an insight.
    insight_interval_minutes: int = Field(default=10, ge=1, le=240)

    #: The exact wording a consent kiosk displays, and its version. Both are
    #: read by `GET /v1/kiosk/{token}` and neither is carried on the token, so
    #: an operator who corrects a sentence changes what every plinth shows
    #: without re-minting a QR code.
    #:
    #: `consent_copy_version` is the load-bearing one, not the tier
    #: (`event-bus-spec.md` §3): the tier says what somebody was asked for, this
    #: says what they read before agreeing, and it is the only thing that
    #: settles a withdrawal argued after the fact. A kiosk cannot be minted
    #: until it is set — `routers/sessions.py` refuses — because a surface that
    #: cannot say which wording it displayed has not really captured consent.
    consent_copy: str | None = Field(default=None, max_length=8000)
    consent_copy_version: str | None = Field(default=None, max_length=128)

    #: What the kiosk asks for. **T2 by default**, alone among the defaults
    #: here in being the higher of two reasonable answers: below T2 the CRM gate
    #: in `app/consent_tier.py` refuses every delivery, so a kiosk set to T1
    #: would capture consents all day and deliver nothing — the whole purpose of
    #: the surface failing quietly. T1 is a deliberate choice an operator makes
    #: when the activation is measuring and not collecting.
    consent_tier: Literal["T1", "T2", "T3"] = "T2"
    consent_basis: Literal[
        "explicit_optin", "contract", "legitimate_interest"
    ] = "explicit_optin"

    #: **Omitted leaves the zone set untouched; a list replaces it entirely**,
    #: including an empty one. The wizard always posts the whole set, so a zone
    #: the operator deleted has to disappear rather than linger and keep
    #: collecting dwell — see graph.repository.prune_zones.
    zones: list[ZoneConfig] | None = None

    #: Same replace-or-leave-alone rule as `zones`. Omitted keeps the existing
    #: touchpoints; a list replaces the set and prunes what is no longer in it.
    touchpoints: list[TouchpointConfig] | None = None

    #: Same rule again. Declaring a camera here is what gives `camera_id` a
    #: meaning; a mask drawn against it is set separately and survives a save
    #: that lists the camera, because `upsert_camera` does not touch the mask.
    cameras: list[CameraConfig] | None = None

    @field_validator("touchpoints")
    @classmethod
    def touchpoint_ids_are_unique(
        cls, value: list[TouchpointConfig] | None
    ) -> list[TouchpointConfig] | None:
        """As with zones, MERGE would collapse a duplicate and lose one."""
        if value is None:
            return None
        seen = [t.id for t in value]
        duplicates = sorted({t for t in seen if seen.count(t) > 1})
        if duplicates:
            raise ValueError(f"duplicate touchpoint ids: {', '.join(duplicates)}")
        return value

    @field_validator("cameras")
    @classmethod
    def camera_ids_are_unique(
        cls, value: list[CameraConfig] | None
    ) -> list[CameraConfig] | None:
        """As with zones and touchpoints — MERGE collapses the duplicate.

        Worse here than for a zone: two cameras sharing an id share one mask
        node, so a mask drawn for the camera pointing at the payment terminal
        would also be applied to the one pointing at the entrance, blanking the
        wrong pixels on both.
        """
        if value is None:
            return None
        seen = [c.id for c in value]
        duplicates = sorted({c for c in seen if seen.count(c) > 1})
        if duplicates:
            raise ValueError(f"duplicate camera ids: {', '.join(duplicates)}")
        return value

    @field_validator("zones")
    @classmethod
    def zone_ids_are_unique(
        cls, value: list[ZoneConfig] | None
    ) -> list[ZoneConfig] | None:
        """Two zones with one id is a lost zone, not an error the graph reports.

        `upsert_zone` MERGEs on (tenant_id, id), so the second would overwrite
        the first and the operator would find one of their zones missing with
        nothing to explain it.
        """
        if value is None:
            return None
        seen = [z.id for z in value]
        duplicates = sorted({z for z in seen if seen.count(z) > 1})
        if duplicates:
            raise ValueError(f"duplicate zone ids: {', '.join(duplicates)}")
        return value

    @model_validator(mode="after")
    def zones_name_a_declared_camera(self) -> "SessionConfigIn":
        """A zone cannot belong to a camera the session does not have.

        Silently is the alternative, and it is the bad one: the tracker filters
        zones by the detection's camera, so a zone owned by a typo matches
        nothing, collects no dwell, and appears in the report as a part of the
        booth nobody visited. That is indistinguishable from a genuine finding.

        **Only checked when the same request declares both lists.** The wizard
        posts the whole configuration, so it is; a request that updates zones
        alone leaves `cameras` None, and the stored camera set is not visible
        from inside a schema. `routers/sessions.py` makes the same check against
        what is stored, which is the one that catches a zone orphaned later by
        a camera being removed.
        """
        if self.zones is None or self.cameras is None:
            return self
        declared = {c.id for c in self.cameras}
        unknown = sorted(
            {z.camera_id for z in self.zones if z.camera_id and z.camera_id not in declared}
        )
        if unknown:
            raise ValueError(
                f"zones name cameras this session does not declare: {', '.join(unknown)}"
            )
        return self


class TouchpointOut(TouchpointConfig):
    """A touchpoint as stored, with how many interactions it has recorded."""

    trigger_count: int = 0


class SessionSummaryOut(BaseModel):
    """One activation in a listing. What was set, never what was measured.

    The absence of a visitor count here is the design, not an oversight. The ROI
    layers are defined once, in the browser's `lib/roi/scorecard.ts`; a count on
    this model would be a second definition of "unique visitor" arriving by the
    back door, and the two would diverge without anything failing. A caller that
    wants figures for one of these sessions reads its log and runs that scorecard
    — which is exactly what the report's benchmark does.

    `revenue_influenced` and `qualified_leads` are the client's own numbers, as
    they are everywhere else. Anything rendering them says so.
    """

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    session_id: str
    client: str | None = None
    campaign: str | None = None
    venue: str | None = None
    city: str | None = None
    #: Strings, matching `SessionConfigIn` and what the graph actually stores.
    #: Typing them as datetimes here would 422 the whole listing over one
    #: session whose date the wizard wrote in some other shape — one bad row
    #: taking out the benchmark for every other activation.
    started_at: str | None = None
    ends_at: str | None = None
    engaged_threshold_seconds: float = 60.0
    activation_cost: float | None = None
    currency: str = "USD"
    revenue_influenced: float | None = None
    qualified_leads: int | None = None


class SessionConfigOut(SessionConfigIn):
    """What comes back. Both lists are always present here — never None."""

    zones: list[ZoneConfig] = []
    touchpoints: list[TouchpointOut] = []
    cameras: list[CameraOut] = []


#: The four calibration kinds `event-bus-spec.md` §3 pins for
#: `calibration.updated`. All four are accepted by the *contract*; only
#: `privacy_mask` is accepted by the endpoint, which refuses the other three
#: with the reason. See `routers/calibration.py`.
CALIBRATION_KINDS = ("homography", "zone_map", "reader_map", "privacy_mask")


class CalibrationIn(BaseModel):
    """An operator recalibrating one camera.

    `polygon` is required for `privacy_mask` and meaningless for the rest, which
    is not expressed as a union type because the rest are refused anyway — see
    the router. A validator here would make the refusal a 422 about a missing
    field instead of a 501 that says nothing consumes a homography, and the
    second is the message an operator can act on.
    """

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    kind: str = Field(default="privacy_mask")
    #: Normalized 0..1, same coordinate space as a zone polygon and drawn with
    #: the same editor. None clears an existing mask.
    polygon: list[tuple[float, float]] | None = None
    #: Free text — why this was recalibrated. Goes onto the event, because the
    #: audit trail this event exists to be is a record of decisions, and "the
    #: camera was knocked at 14:10" is the part a later reader needs.
    note: str | None = None

    @field_validator("polygon")
    @classmethod
    def polygon_is_a_normalized_shape(
        cls, value: list[tuple[float, float]] | None
    ) -> list[tuple[float, float]] | None:
        return normalized_polygon(
            value, what="privacy masks", where="privacy.md → Sensitive zones"
        )


class CalibrationOut(BaseModel):
    """What a recalibration produced: the new revision, and the event recording it."""

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    camera_id: str
    kind: str
    revision: int
    event_id: uuid.UUID


class MaskOut(BaseModel):
    """One camera's opt-out polygon, as perception fetches it.

    `polygon: null` is a camera with no mask — a booth with no sensitive
    surface. It is **not** the same as a 404, which means no such camera was
    declared, and perception treats the two differently: the first is a
    deliberate decision, the second is a misconfiguration it refuses to run on.
    """

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    camera_id: str
    polygon: list[tuple[float, float]] | None = None
    revision: int = 0


class ZoneDwell(BaseModel):
    """Average dwell and visitor count for one zone."""

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    zone_id: str
    zone: str | None = None
    avg_dwell: float | None = None
    visitors: int = 0


class SessionGraphOut(BaseModel):
    """The graph-derived aggregates a report or a live tile starts from.

    Only what the graph knows better than the log does: unique people, and dwell
    already grouped per zone. Everything else the scorecard needs is derivable
    from the event log the caller can already read at `GET /events`, and
    computing it twice in two languages is how the two come to disagree.
    """

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    session_id: str
    unique_people: int
    zones: list[ZoneConfig]
    dwell_by_zone: list[ZoneDwell]


# ── rules ─────────────────────────────────────────────────────────────────────
#
# ADR-002's document, as a wire shape. The ADR's example is the test: every field
# below appears there, spelled the same way.
#
#   { ruleId, tenantId, name, triggerType, triggerZoneId,
#     condition: { type: "threshold", count, windowSec, zoneId, minDwellSec },
#     action:    { type: "slack", channel, message },
#     enabled, cooldownSec }
#
# Adopted verbatim from what floats-agent already ships, "so the two tracks do
# not end up with two rule languages". Two shapes here are deliberately unlike
# each other: `triggerType` is an open prefix check, and condition/action are
# closed discriminated unions. See the note on RuleIn.


class ThresholdCondition(BaseModel):
    """N of something within a window. The only condition that counts."""

    #: Unknown keys are **refused**, on all three conditions. A condition is a
    #: filter, and a misspelled filter narrows nothing — `payloadEqals` would arm
    #: a rule that matches every event of its type and looks exactly like the one
    #: the operator wrote. That is the failure `_payload_field` records having
    #: shipped once already, in the other direction, and it took an
    #: end-to-end test to find.
    model_config = ConfigDict(
        alias_generator=_to_camel, populate_by_name=True, extra="forbid"
    )

    type: Literal["threshold"]
    count: int = Field(ge=1)
    window_sec: int = Field(gt=0)
    #: Narrows the window to one zone. Distinct from `triggerZoneId`: the trigger
    #: decides which events wake the rule, this decides which are counted.
    zone_id: str | None = None
    #: For dwell triggers — ignore a dwell shorter than this. A dwell event is
    #: emitted for every stay, including a two-second one, and "5 people at the
    #: entrance" does not mean five people who walked past it.
    min_dwell_sec: float | None = Field(default=None, ge=0)
    #: Narrows to events whose payload says this. A flat map of field → value,
    #: all of which must match. It exists because a status is not a zone: a
    #: `spatial.occupancy` says whether a room filled or emptied and a
    #: `spatial.group` whether one formed or dissolved, and a rule armed on the
    #: type alone acts on both — raising "the entrance is at capacity" at the
    #: moment it clears. Both spellings of a field are accepted when it is read
    #: (`consumers/rules._payload_field`), since the payload is snake_case and
    #: this document is camelCase.
    #:
    #: Values are strings on purpose. This narrows an event to a *kind* of event,
    #: which is what a status is; comparing numbers is what `threshold` is for,
    #: and a `>=` here would be a second, quieter way to say the same thing.
    payload_equals: dict[str, str] | None = None


class AnyCondition(BaseModel):
    """Fire on the trigger itself, with no counting. `count: 1` in spirit, but
    named separately because "any dwell in this zone" is what an operator says,
    and a spec they can read back is the point of ADR-002."""

    model_config = ConfigDict(
        alias_generator=_to_camel, populate_by_name=True, extra="forbid"
    )

    type: Literal["any"]
    zone_id: str | None = None
    #: Narrows to events whose payload says this. A flat map of field → value,
    #: all of which must match. It exists because a status is not a zone: a
    #: `spatial.occupancy` says whether a room filled or emptied and a
    #: `spatial.group` whether one formed or dissolved, and a rule armed on the
    #: type alone acts on both — raising "the entrance is at capacity" at the
    #: moment it clears. Both spellings of a field are accepted when it is read
    #: (`consumers/rules._payload_field`), since the payload is snake_case and
    #: this document is camelCase.
    #:
    #: Values are strings on purpose. This narrows an event to a *kind* of event,
    #: which is what a status is; comparing numbers is what `threshold` is for,
    #: and a `>=` here would be a second, quieter way to say the same thing.
    payload_equals: dict[str, str] | None = None


class NoneCondition(BaseModel):
    """Nothing happened for `windowSec`.

    ADR-002 §4: this cannot be judged when an event arrives, because that asks
    whether the window is empty at the moment something filled it. It is judged
    at a boundary — the first event past the window, or `session.ended`.
    """

    model_config = ConfigDict(
        alias_generator=_to_camel, populate_by_name=True, extra="forbid"
    )

    type: Literal["none"]
    window_sec: int = Field(gt=0)
    zone_id: str | None = None


RuleCondition = ThresholdCondition | AnyCondition | NoneCondition


class SlackAction(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    type: Literal["slack"]
    channel: str = Field(min_length=1)
    message: str = Field(min_length=1)


class WebhookAction(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    type: Literal["webhook"]
    url: str = Field(min_length=1)
    #: Merged into the POST body alongside the firing. Whatever the operator's
    #: receiver needs to route it.
    payload: dict[str, Any] = Field(default_factory=dict)


class ScreenSwapAction(BaseModel):
    """Change what a screen in the room is showing."""

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    type: Literal["screen_swap"]
    screen_id: str = Field(min_length=1)
    content_id: str = Field(min_length=1)


class StaffPromptAction(BaseModel):
    """Tell a human on the floor to do something. The `< 3s` in the Phase 3
    acceptance criterion is mostly about this one — the others can be a second
    late without anybody noticing."""

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    type: Literal["staff_prompt"]
    message: str = Field(min_length=1)
    #: Where in the room the prompt is about, so a screen can place it.
    zone_id: str | None = None
    priority: Literal["low", "normal", "high"] = "normal"


class LogAction(BaseModel):
    """Do nothing but say so. The action for a rule an operator is still
    trialling, and the one every test uses."""

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    type: Literal["log"]
    message: str = ""


RuleAction = (
    SlackAction | WebhookAction | ScreenSwapAction | StaffPromptAction | LogAction
)


#: What a *composed* rule may do, which is narrower than what an operator may
#: write. `log` and `staff_prompt` stay inside the room; Slack, a webhook and a
#: screen swap send a client's floor data somewhere, and a model choosing the
#: channel or the URL would be inventing where. The composer returns the
#: condition and names what the operator has to supply, and they change the
#: action by hand — which is the moment a person decides a rule should reach
#: outside. Structural rather than a check afterwards: the destination fields
#: are not in the union the model's output is validated against.
ComposableAction = StaffPromptAction | LogAction


class ComposedRule(BaseModel):
    """The document a model returns, before an operator has confirmed anything.

    `RuleIn` minus the three fields the model must not decide. `ruleId` and the
    tenant are minted by `routers/rules.py` from the credential — a model that
    picks an id can overwrite an operator's existing rule, and that is silent
    loss rather than an error — and the action union is narrowed above.

    Everything else is validated by the *same* members `PUT /v1/rules` enforces
    on a hand-written rule. There is no second validator, which is the point:
    `docs/adr/003-nl-query-catalogue.md` refuses model-written Cypher because
    arbitrary query text cannot be validated, and a rule document is data with a
    closed union that already can be.
    """

    model_config = ConfigDict(
        alias_generator=_to_camel, populate_by_name=True, extra="forbid"
    )

    name: str = Field(min_length=1, max_length=120)
    trigger_type: str = Field(min_length=1)
    trigger_zone_id: str | None = None
    condition: RuleCondition = Field(discriminator="type")
    action: ComposableAction = Field(discriminator="type")
    cooldown_sec: int = Field(default=60, ge=0)

    @field_validator("trigger_type")
    @classmethod
    def trigger_is_in_a_known_namespace(cls, value: str) -> str:
        """The same prefix test `RuleIn` applies, for the same reason.

        Not narrowed to the types that have producers: `event-bus-spec.md` §3
        registers six of those ahead of the code that emits them precisely so a
        rule can name one, and the composer says so in a warning instead.
        """
        if not value.startswith(EVENT_NAMESPACES):
            raise ValueError(
                f"unknown event namespace in triggerType {value!r}; "
                f"expected one of {', '.join(EVENT_NAMESPACES)} "
                "(taxonomy: docs/event-bus-spec.md §3)"
            )
        return value


class RuleIn(BaseModel):
    """A rule document as an operator POSTs it.

    ## Why triggerType is validated loosely and the rest strictly

    `triggerType` reuses `EVENT_NAMESPACES` — the same prefix test the bus
    applies to an incoming event, with the same known gap (a typo in the suffix
    passes). That is ADR-002's explicit choice: "`triggerType` is any type
    registered in event-bus-spec.md §3 — **not** a closed enum … a rule spec that
    cannot name `intent.scored` or `spatial.tagged` the day those producers land
    would force a spec migration to use them, which is the tax Phase 3's
    pre-registration was meant to avoid."

    `condition.type` and `action.type` are the opposite: closed unions, because
    unlike the taxonomy they are not additive by design. An action type nothing
    dispatches is a rule that looks armed and does nothing, which is worse than
    a 422 at the moment of saving.
    """

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    rule_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    trigger_type: str = Field(min_length=1)
    trigger_zone_id: str | None = None
    condition: RuleCondition = Field(discriminator="type")
    action: RuleAction = Field(discriminator="type")
    enabled: bool = True
    #: Seconds of event time between two firings of the same rule. Zero is
    #: allowed and means "every match", which is a defensible choice for a `log`
    #: rule and a bad one for Slack.
    cooldown_sec: int = Field(default=60, ge=0)

    @field_validator("trigger_type")
    @classmethod
    def trigger_is_in_a_known_namespace(cls, value: str) -> str:
        if not value.startswith(EVENT_NAMESPACES):
            raise ValueError(
                f"unknown event namespace in triggerType {value!r}; "
                f"expected one of {', '.join(EVENT_NAMESPACES)} "
                "(taxonomy: docs/event-bus-spec.md §3)"
            )
        return value


class RuleOut(RuleIn):
    """A stored rule. `tenantId` appears here and not on RuleIn: the tenant comes
    from the authenticated principal, never from the body, or an operator could
    write a rule into somebody else's booth."""

    model_config = ConfigDict(
        from_attributes=True, alias_generator=_to_camel, populate_by_name=True
    )

    tenant_id: str
    created_at: dt.datetime
    updated_at: dt.datetime


def to_wire(row) -> dict:
    """An event_log row as the canonical RealmEvent the browser expects.

    Used by the HTTP router and by the broadcast consumer, so it lives with the
    wire shapes rather than in either caller.
    """
    return EventOut.model_validate(row).model_dump(by_alias=True, mode="json")
