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
    consumer_retry_delay_seconds: float = 0.2

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

    # ── auth (multi-tenant.md §3, Week 1 tasks 1.6/1.7) ───────────────────────
    # Tokens are issued and verified locally with this secret. Deliberately not
    # Firebase-verified on the hot path: event-bus-spec.md §1 requires the edge
    # box to survive going offline, and checking a Firebase ID token needs
    # Google's JWKS. A kit that cannot authenticate when the wifi drops is not
    # fit for a conference floor.
    #
    # No default. A blank secret would sign tokens anyone could forge, and a
    # shipped default is worse than none — the app refuses to start without it.
    jwt_secret: str
    jwt_ttl_seconds: int = 3600  # short: WS tokens travel in the query string

    # Roles from multi-tenant.md §3. "producer" is not a human role — it is what
    # a device API key carries, and it can only write.
    default_role: str = "viewer"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
