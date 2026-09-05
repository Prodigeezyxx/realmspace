"""
CV drift telemetry, and the four properties that make a drift warning worth
acting on.

**It does not fire when the room empties.** The one failure this whole consumer
is arranged to avoid. `detection_rate` is not emitted at all, and the two metrics
that are emitted are per-detection statistics, so a quiet window contributes no
samples rather than a low reading. A panel that alarms every lunchtime is a panel
an operator learns to ignore, and then it is worth less than nothing.

**It states its comparison.** Every event carries `observed` and `baseline`
(`event-bus-spec.md` §3), so a human can disagree with the threshold without
re-deriving the measurement.

**A replay reproduces it exactly.** Fixed contiguous windows on event time,
derived event ids. Rewinding the cursor regenerates the same events and the bus
dedupes them.

**A recalibration resets the baseline.** Masking pixels changes what the model
sees, so the pre-mask baseline would make the operator's own correct action look
like drift. This is the property that ties the two halves of the roadmap bullet
together.

Runs against real Postgres, in `t_test`.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import repository
from app.config import get_settings
from app.consumers.drift import DriftConsumer
from app.schemas import EventIn
from tests.conftest import as_tenant

T = "t_test"
S = "s_drift"
CAM = "cam-1"

BASE = dt.datetime(2026, 8, 19, 9, 0, 0, tzinfo=dt.timezone.utc)

#: A window's worth of samples, comfortably over `drift_min_samples` (30).
SAMPLES = 40


@pytest.fixture(autouse=True)
async def _scope_to_test_tenant(db_session: AsyncSession) -> AsyncIterator[None]:
    await as_tenant(db_session, T)
    yield


async def emit(
    db_session: AsyncSession, *, type: str, payload: dict, minutes: float
) -> None:
    await repository.append_event(
        db_session,
        EventIn(
            event_id=uuid.uuid4(),
            tenant_id=T,
            session_id=S,
            type=type,
            payload=payload,
            occurred_at=BASE + dt.timedelta(minutes=minutes),
        ),
    )


async def detections(
    db_session: AsyncSession,
    *,
    window: int,
    confidence: float,
    count: int = SAMPLES,
    camera: str | None = CAM,
) -> None:
    """`count` detections spread inside window `window`, all at one confidence.

    Spread rather than stacked on one timestamp so the window boundaries are
    actually exercised — an off-by-one in `[from, to)` would show up as a
    sample landing in the neighbouring window.
    """
    for i in range(count):
        payload: dict = {
            "anon_id": f"P-{i:03d}",
            "bbox": [0, 0, 10, 10],
            "confidence": confidence,
            "frame_id": i,
            "frame_width": 1000,
            "frame_height": 1000,
        }
        if camera is not None:
            payload["camera_id"] = camera
        await emit(
            db_session,
            type="perception.detection",
            payload=payload,
            minutes=window * 10 + (i * 9.0 / count),
        )
    # The consumer reads on its own connection — nothing uncommitted exists
    # for it. Committing per batch rather than per event keeps the suite quick.
    await db_session.commit()


async def exits(
    db_session: AsyncSession, *, window: int, dropouts: int, moves: int
) -> None:
    for i in range(dropouts + moves):
        await emit(
            db_session,
            type="spatial.zone_exit",
            payload={
                "anon_id": f"P-{i:03d}",
                "zone_id": "z_entry",
                "camera_id": CAM,
                "reason": "dropout" if i < dropouts else "move",
            },
            minutes=window * 10 + (i * 9.0 / (dropouts + moves)),
        )
    await db_session.commit()


async def close_after(db_session: AsyncSession, *, window: int) -> None:
    """One detection past the end of `window`, so event time has moved on.

    A window is only judged once something after it arrives — that is what makes
    the trigger replayable rather than a function of when somebody looked.
    """
    await emit(
        db_session,
        type="perception.detection",
        payload={
            "anon_id": "P-late",
            "bbox": [0, 0, 10, 10],
            "confidence": 0.9,
            "frame_id": 9999,
            "frame_width": 1000,
            "frame_height": 1000,
            "camera_id": CAM,
        },
        minutes=(window + 1) * 10 + 1,
    )
    await db_session.commit()


async def drifts(db_session: AsyncSession) -> list[dict]:
    # Commit first so this read starts a new transaction and therefore sees what
    # the consumer committed on its own connection.
    await db_session.commit()
    rows = await repository.read_events(
        db_session, tenant_id=T, session_id=S, type="drift.detected", limit=100
    )
    return [r.payload for r in rows]


# ── the reading ───────────────────────────────────────────────────────────────


async def test_confidence_falling_against_the_first_window_is_reported(
    db_session: AsyncSession,
) -> None:
    """Window 0 is the baseline; window 1 is judged against it."""
    await detections(db_session, window=0, confidence=0.90)
    await detections(db_session, window=1, confidence=0.55)
    await close_after(db_session, window=1)

    await DriftConsumer().run_once()

    found = [d for d in await drifts(db_session) if d["metric"] == "confidence_mean"]
    assert len(found) == 1
    assert found[0]["camera_id"] == CAM
    assert found[0]["observed"] == 0.55
    assert found[0]["baseline"] == 0.90
    assert found[0]["severity"] == "critical"
    assert found[0]["window_seconds"] == 600


async def test_the_event_carries_both_sides_of_the_comparison(
    db_session: AsyncSession,
) -> None:
    """event-bus-spec.md §3: it states the comparison rather than the verdict.

    A severity on its own is unarguable, which sounds like a virtue and is not —
    an operator who cannot see the numbers cannot tell a real degradation from a
    threshold set badly.
    """
    await detections(db_session, window=0, confidence=0.90)
    await detections(db_session, window=1, confidence=0.74)
    await close_after(db_session, window=1)

    await DriftConsumer().run_once()

    found = (await drifts(db_session))[0]
    assert {"observed", "baseline", "severity", "window_seconds"} <= set(found)
    assert found["severity"] == "warn"  # ~18% — over warn (15%), under critical (30%)


async def test_rising_dropouts_are_reported_as_track_length(
    db_session: AsyncSession,
) -> None:
    """Track fragmentation, measured from the `reason` the tracker already stamps.

    No new producer: `consumers/tracker.py` has carried `reason` on every exit
    since Phase 2's session-hygiene work.
    """
    await detections(db_session, window=0, confidence=0.9)
    await exits(db_session, window=0, dropouts=4, moves=36)
    await detections(db_session, window=1, confidence=0.9)
    await exits(db_session, window=1, dropouts=24, moves=16)
    await close_after(db_session, window=1)

    await DriftConsumer().run_once()

    found = [d for d in await drifts(db_session) if d["metric"] == "track_length"]
    assert len(found) == 1
    assert found[0]["baseline"] == 0.1
    assert found[0]["observed"] == 0.6
    assert found[0]["severity"] == "critical"


# ── the failures it refuses to invent ─────────────────────────────────────────


async def test_an_empty_window_says_nothing(db_session: AsyncSession) -> None:
    """**The property this consumer exists to have.**

    The room emptied. Detection rate collapsed to zero. A drift detector built
    on rate would fire here, every lunchtime, and the panel would be furniture
    by the second day. Both emitted metrics are per-detection, so no detections
    means no samples means no claim.
    """
    await detections(db_session, window=0, confidence=0.9)
    # Window 1: nobody in the booth at all.
    await close_after(db_session, window=1)

    await DriftConsumer().run_once()

    assert await drifts(db_session) == []


async def test_a_window_below_the_sample_floor_says_nothing(
    db_session: AsyncSession,
) -> None:
    """A mean over five detections is noise wearing an event type."""
    await detections(db_session, window=0, confidence=0.9)
    await detections(db_session, window=1, confidence=0.2, count=5)
    await close_after(db_session, window=1)

    await DriftConsumer().run_once()

    assert await drifts(db_session) == []


async def test_the_baseline_window_is_never_judged(db_session: AsyncSession) -> None:
    """Window 0 is being measured, not compared. There is nothing to compare to."""
    await detections(db_session, window=0, confidence=0.9)
    await close_after(db_session, window=0)

    await DriftConsumer().run_once()

    assert await drifts(db_session) == []


async def test_improvement_is_not_reported(db_session: AsyncSession) -> None:
    """A camera that got *better* has not drifted in any sense worth a queue entry."""
    await detections(db_session, window=0, confidence=0.50)
    await detections(db_session, window=1, confidence=0.95)
    await close_after(db_session, window=1)

    await DriftConsumer().run_once()

    assert await drifts(db_session) == []


async def test_a_small_change_is_not_reported(db_session: AsyncSession) -> None:
    """Under the warn ratio. Cameras vary; a feed of 3% wobbles is not telemetry."""
    await detections(db_session, window=0, confidence=0.90)
    await detections(db_session, window=1, confidence=0.87)
    await close_after(db_session, window=1)

    await DriftConsumer().run_once()

    assert await drifts(db_session) == []


async def test_detection_rate_is_never_emitted(db_session: AsyncSession) -> None:
    """§3 lists it; this consumer refuses it, and the refusal is the design.

    Written as a test rather than left to the docstring because the obvious
    "completeness" change — adding the third metric §3 names — is exactly the
    change that breaks the panel.
    """
    await detections(db_session, window=0, confidence=0.90, count=SAMPLES)
    await detections(db_session, window=1, confidence=0.50, count=SAMPLES)
    await close_after(db_session, window=1)

    await DriftConsumer().run_once()

    assert all(d["metric"] != "detection_rate" for d in await drifts(db_session))


# ── per camera ────────────────────────────────────────────────────────────────


async def test_two_cameras_are_measured_apart(db_session: AsyncSession) -> None:
    """A mean averaged across two cameras describes neither.

    The one that degraded would be pulled up by the one that did not, which is
    how a real fault stays under the threshold until both cameras have it.
    """
    for window, (good, bad) in enumerate([(0.90, 0.90), (0.90, 0.40)]):
        await detections(db_session, window=window, confidence=good, camera="cam-a")
        await detections(db_session, window=window, confidence=bad, camera="cam-b")
    await close_after(db_session, window=1)

    await DriftConsumer().run_once()

    found = await drifts(db_session)
    assert [d["camera_id"] for d in found] == ["cam-b"]


async def test_detections_with_no_camera_are_attributed_rather_than_dropped(
    db_session: AsyncSession,
) -> None:
    """Every event logged before Phase 6 has no `camera_id`.

    Dead-lettering through a season of history would bury the queue `/ops` exists
    to surface, so they group under one name that is visibly not a camera.
    """
    await detections(db_session, window=0, confidence=0.90, camera=None)
    await detections(db_session, window=1, confidence=0.40, camera=None)
    await close_after(db_session, window=1)

    await DriftConsumer().run_once()

    assert [d["camera_id"] for d in await drifts(db_session)] == ["unattributed"]


# ── replay ────────────────────────────────────────────────────────────────────


async def test_a_replay_reproduces_the_same_events(db_session: AsyncSession) -> None:
    """Rewind the cursor to zero and run the whole log again.

    The derived id makes the second pass a no-op at the bus. Without it the
    duplicate would land silently and `/ops` would show one fault twice — the
    same class of bug `consumers/ids.py` exists to prevent for dwell.
    """
    await detections(db_session, window=0, confidence=0.90)
    await detections(db_session, window=1, confidence=0.50)
    await detections(db_session, window=2, confidence=0.45)
    await close_after(db_session, window=2)

    await DriftConsumer().run_once()
    first = await drifts(db_session)
    assert len(first) == 2

    await repository.reset_cursor(db_session, tenant_id=T, consumer="drift", to_seq=0)
    # Committed before the consumer runs, or it blocks: the consumer polls on its
    # own connection and would wait on the row lock this session still holds.
    await db_session.commit()

    await DriftConsumer().run_once()

    assert await drifts(db_session) == first


async def test_windows_are_contiguous_and_each_is_written_once(
    db_session: AsyncSession,
) -> None:
    """Three degraded windows in a row produce three events, not nine.

    The catch-up loop starts after the last window already written, and it reads
    that **without** a seq bound — a drift event is appended above the detections
    it measures, so a bound at the triggering event could never see one and every
    pass would recompute from window 1.
    """
    await detections(db_session, window=0, confidence=0.90)
    for window in (1, 2, 3):
        await detections(db_session, window=window, confidence=0.50)
    await close_after(db_session, window=3)

    await DriftConsumer().run_once()

    found = [d for d in await drifts(db_session) if d["metric"] == "confidence_mean"]
    starts = [d["window"]["from"] for d in found]
    assert len(starts) == 3
    assert len(set(starts)) == 3


# ── the two halves, joined ────────────────────────────────────────────────────


async def test_a_recalibration_resets_the_baseline(db_session: AsyncSession) -> None:
    """**The property that makes calibration and drift one piece of work.**

    Masking pixels changes what the model sees and therefore what it is
    confident about. Measured against the pre-mask baseline, an operator drawing
    a mask would set off the alarm — the system reporting the operator's own
    correct action as a fault.

    After the calibration, windows are measured from it: the post-mask level is
    the new normal, and nothing is reported for holding it.
    """
    settings = get_settings()
    assert settings.drift_window_minutes == 10

    await detections(db_session, window=0, confidence=0.90)
    await emit(
        db_session,
        type="calibration.updated",
        payload={"camera_id": CAM, "kind": "privacy_mask", "revision": 1, "masked": True},
        minutes=10,
    )
    await db_session.commit()
    # Both windows after the mask sit at the lower post-mask level.
    await detections(db_session, window=1, confidence=0.60)
    await detections(db_session, window=2, confidence=0.60)
    await close_after(db_session, window=2)

    await DriftConsumer().run_once()

    assert await drifts(db_session) == []


async def test_drift_after_a_recalibration_is_still_caught(
    db_session: AsyncSession,
) -> None:
    """Resetting the baseline must not amount to switching the detector off.

    The camera settles at its post-mask level, then genuinely degrades. That is
    a real fault after a real recalibration, and it has to be reported.
    """
    await emit(
        db_session,
        type="calibration.updated",
        payload={"camera_id": CAM, "kind": "privacy_mask", "revision": 1, "masked": True},
        minutes=0,
    )
    await db_session.commit()
    await detections(db_session, window=0, confidence=0.80)
    await detections(db_session, window=1, confidence=0.80)
    await detections(db_session, window=2, confidence=0.35)
    await close_after(db_session, window=2)

    await DriftConsumer().run_once()

    found = [d for d in await drifts(db_session) if d["metric"] == "confidence_mean"]
    assert len(found) == 1
    assert found[0]["baseline"] == 0.80
    assert found[0]["observed"] == 0.35


