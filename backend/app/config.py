"""
Settings, loaded from backend/.env (see .env.example).

One Settings object, built once and cached, so every module reads the same
config and nothing reaches for os.environ directly. Alembic's env.py imports
this too, which is what keeps the migration DB URL and the app DB URL from
drifting apart.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # postgresql+asyncpg://... — the +asyncpg part picks the async driver.
    #
    # This connects as `realmspace_app`, which is neither a superuser nor the
    # owner of anything, so row-level security applies to it (migration 0003).
    # A superuser bypasses policies unconditionally — verified — so the role
    # here is what makes the isolation real rather than decorative.
    database_url: str
    test_database_url: str | None = None

    # The owner connection, used by Alembic only. Migrations need CREATE and
    # ALTER; the app must never have them. Falls back to database_url so a
    # deployment that has not split the roles still runs — with the policies
    # inert, which the RLS tests will notice.
    admin_database_url: str | None = None

    # "local" on the edge box, "cloud" when deployed. See event-bus-spec.md §5:
    # the bus is designed to run on the edge with no network.
    env: str = "local"

    # echo SQL to stdout — useful while learning what SQLAlchemy actually emits
    sql_echo: bool = False

    @property
    def migration_url(self) -> str:
        """What Alembic connects with — the owner, or the app role if unsplit."""
        return self.admin_database_url or self.database_url

    # Neo4j holds the graph (data-model.md; PRD.md §architecture). Postgres keeps
    # the event log and timeseries — they are separate stores on purpose.
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""
    # Community edition is single-database; this exists so a future Enterprise
    # deployment can point tests or tenants at a different one without a refactor.
    neo4j_database: str = "neo4j"

    # ── consumers (event-bus-spec.md §4) ──────────────────────────────────────
    # Busy interval is small because §4 calls the tracker real-time and roadmap
    # Phase 1 acceptance is < 500ms detection → dashboard. Idle backs off so an
    # empty log isn't hammered.
    consumer_busy_interval_seconds: float = 0.1
    consumer_idle_interval_seconds: float = 0.5
    consumer_batch_size: int = 200

    # Attempts before an event is parked in dead_letter and skipped. Spec §5:
    # "after N tries it surfaces in the HITL review screen".
    consumer_max_attempts: int = 3
    # The first wait. Each subsequent attempt doubles it — a transient failure
    # (a database blip, a graph write racing a restart) usually clears within a
    # moment, and hammering it at a fixed interval is the worst thing to do to
    # something already struggling.
    consumer_retry_delay_seconds: float = 0.2
    # ...but bounded, and the bound matters more than the growth. The tracker is
    # on the real-time path with a <500ms budget from detection to dashboard;
    # an unbounded doubling would hold a whole batch behind one slow failure
    # while the room fills up.
    consumer_retry_max_delay_seconds: float = 5.0

    # Start consumer loops with the API process. Off in tests, which drive
    # run_once() directly instead of racing a background task.
    consumers_enabled: bool = True

    # Dwell threshold, matching the browser: agents/definitions/dwell.ts uses
    # thresholdSec: 30 and agent-engine.ts:81 uses 30_000ms. One definition of
    # "dwelled" across edge and browser.
    dwell_threshold_seconds: float = 30.0

    # How long the tracker may reuse cached zone polygons. Operators redraw
    # zones mid-session, so an unexpiring cache silently scores dwell against
    # stale boundaries. Short enough that a redraw takes effect quickly, long
    # enough that the hot path isn't doing a graph round trip per frame.
    tracker_zone_cache_seconds: float = 30.0

    # Cap on people the tracker holds position for, per process. Bounds memory
    # across a multi-day activation; eviction costs one spurious zone_enter.
    tracker_max_tracked_people: int = 10_000

    # ── session hygiene ───────────────────────────────────────────────────────
    # Ported from the postgres-track's browser-side deriver
    # (dashboard/src/lib/live-session/spatial-deriver.ts), whose header cites
    # CHI '26: 71% of raw sessions are invalid without these. Untreated, all
    # three failures below produce events that look perfectly ordinary and land
    # in a client's ROI report as findings.

    # A zone change must persist this long before it is believed. Somebody
    # standing on a boundary flickers between two zones at frame rate; without
    # a confirm window that is dozens of enter/exit/dwell pairs a minute, each
    # one indistinguishable from a real visit.
    #
    # Note this means a zone needs two detections spaced at least this far
    # apart to register at all. At 20fps that is 12 frames; for sparse
    # synthetic input it is the difference between events and silence.
    tracker_zone_confirm_seconds: float = 0.6

    # Dwells shorter than this are noise, not visits, and are dropped. The exit
    # is still emitted — the person did leave — but nothing claims they spent
    # time there.
    tracker_min_dwell_seconds: float = 1.0

    # A track that stops being detected while inside a zone is assumed to have
    # left at its last sighting. Without this its exit and dwell are never
    # emitted at all: anyone who walks out of frame from inside a zone simply
    # vanishes from the report, so the activation under-reports the visits that
    # ended in the least convenient place. Measured on event time, not wall
    # clock, so a replay reproduces it exactly.
    tracker_dropout_seconds: float = 20.0

    # How close counts as "passed by": normalized distance to a zone's edge.
    # roi-framework.md §2 wants the skip signal — people who came near and chose
    # not to engage — and 0.08 of the frame matches the postgres-track's
    # PASSBY_RADIUS, so a visitor is a pass-by on both tracks or on neither.
    tracker_passby_radius: float = 0.08

    # ── CV drift telemetry (roadmap.md Phase 6) ───────────────────────────────
    # How long a drift window is, in minutes of *event* time. Separate from the
    # insight interval, which is an operator-facing setting about how often they
    # want to be told something: this is a measurement window, and shortening it
    # to get faster warnings just makes each sample noisier.
    drift_window_minutes: int = 10

    # Below this many samples a window produces no reading. A mean confidence
    # over three detections is noise, and a drift event built on it is a guess
    # wearing an event type. No reading is the honest output — the panel shows
    # nothing rather than a shrug.
    drift_min_samples: int = 30

    # Relative change against the baseline, as a fraction, before we say
    # anything. Two thresholds because `drift.detected` carries a severity, and
    # one number cannot distinguish "worth a look at the next lull" from "the
    # numbers coming out of this camera are wrong now".
    #
    # Deliberately relative, not absolute: cameras differ, and an absolute
    # confidence floor would fire permanently on a hard scene and never on an
    # easy one. The event carries both sides of the comparison anyway
    # (event-bus-spec.md §3), so a human can disagree with the threshold without
    # having to re-derive the measurement.
    drift_warn_ratio: float = 0.15
    drift_critical_ratio: float = 0.30

    # ── Gaze (roadmap.md Phase 1's last unbuilt spatial signal) ──────────────
    #
    # A monocular camera gives a facing direction, not a gaze vector — no depth,
    # and looking up reads the same as looking ahead. `privacy.md` sanctions
    # exactly this much (pose keypoints kept "briefly" for a gaze vector, face
    # mesh discarded, embeddings never computed) and perception derives the
    # heading at the edge so no skeleton reaches the log.
    #
    # The risk is the one `detection_rate` drift was left unbuilt for and that
    # grouping refuses in its own way: a detector that answers on every frame
    # reports every head turn as interest, which looks like coverage. These are
    # the two floors that stop it.

    # Below this the heading is a guess about a skeleton the model could not
    # see. Perception ships its confidence rather than filtering on it, so the
    # threshold lives here, in one place, instead of at every camera.
    gaze_min_confidence: float = 0.45

    # How long the same target must be held before it is attention rather than
    # a head turn. The same shape as `tracker_zone_confirm_seconds` and for the
    # same reason: a momentary crossing is not a visit, and a momentary glance
    # is not interest.
    gaze_min_seconds: float = 1.5

    # ── Group visits (roadmap.md blind spot, data-model.md → (:Group)) ────────
    #
    # The whole difficulty here is that **proximity is not company**. Three
    # strangers queueing at a popular zone are within a metre of each other for
    # minutes, and a detector built on distance-and-time reports every queue as
    # a family. So a pair has to clear one of two harder tests, and these are
    # the knobs for both.

    # How close counts as together: normalized distance between two centroids.
    # A little wider than `tracker_passby_radius`, because that measures a
    # person against a zone edge and this measures two people who are trying to
    # stay next to each other.
    group_radius: float = 0.10

    # Test one, co-movement: how far the pair's midpoint must travel while they
    # stay together, as a fraction of the frame. This is what separates walking
    # the floor together from standing in the same spot — a queue accumulates
    # time without accumulating travel.
    group_min_travel: float = 0.25

    # Test two, joint arrival and departure: how close in time two people must
    # enter and leave a zone to count as arriving together. This is what catches
    # the family who sit at one table and never move, and what a queue fails —
    # queue members arrive and leave at different times, which is what makes it
    # a queue.
    group_joint_window_seconds: float = 8.0

    # Below this many co-observations there is nothing to be confident about,
    # whichever test they cleared. Same reasoning as `drift_min_samples`.
    group_confirm_samples: int = 20

    # Share of a pair's shared observations in which they were actually
    # together. Two people who happen to be near each other a third of the time
    # are two people, and this is the floor that says so.
    group_min_cohesion: float = 0.6

    # How long apart before the pair is no longer a pair. Measured on event time
    # like every other window here, so a replay dissolves the group at the same
    # point the original run did.
    group_break_seconds: float = 45.0

    # ── auth (multi-tenant.md §3, Week 1 tasks 1.6/1.7) ───────────────────────
    # Tokens are issued and verified locally with this secret. Deliberately not
    # Firebase-verified on the hot path: event-bus-spec.md §1 requires the edge
    # box to survive going offline, and checking a Firebase ID token needs
    # Google's JWKS. A kit that cannot authenticate when the wifi drops is not
    # fit for a conference floor.
    #
    # No default. A blank secret would sign tokens anyone could forge, and a
    # shipped default is worse than none — the app refuses to start without it.
    #: The Firebase project whose ID tokens `POST /v1/auth/token` will accept.
    #:
    #: Not a secret — a project id is public by design — which is why real
    #: authentication could be built while the AI provider and Stripe still wait
    #: on keys nobody has. Verification needs this and Google's published keys,
    #: nothing else (`app/auth/firebase.py`).
    #:
    #: **Unset outside `local` means the token endpoint refuses to issue
    #: anything.** See `routers/auth.py`: a deployment that forgets this fails
    #: closed at the door rather than shipping the email-trust hole.
    firebase_project_id: str | None = None

    jwt_secret: str
    jwt_ttl_seconds: int = 3600  # short: WS tokens travel in the query string

    # Roles from multi-tenant.md §3. "producer" is not a human role — it is what
    # a device API key carries, and it can only write.
    default_role: str = "viewer"

    # ── rule actions (roadmap Phase 3) ────────────────────────────────────────
    # A rule's `slack` action names a channel; the credential to post with is
    # deployment configuration and never part of the rule document, which an
    # operator authors in a browser and which is returned by a GET.
    #
    # None means "no Slack configured". A `slack` rule then dead-letters with
    # that as the reason, which is the honest outcome: the alternative is a
    # dispatcher that reports success for a message nobody received.
    slack_webhook_url: str | None = None

    # Signs outgoing `webhook` actions, so the receiver can tell a firing from
    # this booth from anything else that finds the URL. Same scheme
    # integrations.md specifies for Phase 4's bring-your-own delivery — and now
    # literally the same function, shared by consumers/handoff_delivery.py.
    webhook_signing_secret: str | None = None

    # Where a `handoff.lead` is delivered. integrations.md §5's "Generic Webhook
    # — POST the LeadHandoff JSON to a customer URL (HMAC-signed)", and the
    # destination every CRM adapter is checked against before it is written.
    #
    # None means no destination is configured, which is not a failure: handoffs
    # are still built and still on the log, and a destination added next week
    # reads them from seq 0. That is the opposite of `slack_webhook_url`'s
    # unset behaviour, and deliberately — a rule action names Slack explicitly,
    # so an unset URL there is a rule that cannot do what it says, whereas
    # nothing has asked for this one.
    handoff_webhook_url: str | None = None

    # ── tenant credentials (multi-tenant.md §2, Phase 4's CRM adapters) ───────
    # Encrypts what a tenant stores in `tenant_integration` — a HubSpot token, a
    # Salesforce refresh token, an enrichment key. Urlsafe base64 of 32 bytes:
    #   python -c "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
    #
    # No default, and unset is a refusal rather than plaintext storage — see
    # app/secrets.py. A shipped default here would be a published key protecting
    # somebody else's CRM.
    credential_encryption_key: str | None = None

    # How long a dispatcher waits on an outbound call before treating it as
    # failed. Short, because event-bus-spec.md §4 budgets `< 3s` end to end and
    # base.Consumer will retry twice on top of this.
    action_timeout_seconds: float = 2.0

    # After this long, a dispatch still sitting at `claimed` is stranded rather
    # than in flight, and `/ops` shows it to a human.
    #
    # A row is at `claimed` for the whole of every normal dispatch — the claim is
    # committed before the call goes out, which is the entire idempotency
    # mechanism — so this is not a tuning knob but the line between "running" and
    # "its process died holding this". An action gives up at
    # `action_timeout_seconds` and each attempt claims afresh, so the longest
    # legitimate life of a claimed row is one timeout. Thirty times that, because
    # the cost of the two errors is wildly asymmetric: too low puts healthy
    # dispatches on the operator's screen as problems and teaches them to ignore
    # the panel, while too high only delays a row nobody was going to see for
    # another minute.
    stranded_dispatch_after_seconds: float = 60.0


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
