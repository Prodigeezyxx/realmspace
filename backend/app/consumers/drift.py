"""
CV drift telemetry — the camera degrading, said out loud instead of showing up
as a quieter room.

`roadmap.md`'s blind-spot table has had "CV model drift → calibration UI + drift
telemetry — P6" since the founder architecture dump, and `event-bus-spec.md` §3
pinned `drift.detected` in Phase 3 so this producer would not arrive to a 422.
This is that producer.

## Two metrics, not the three the spec lists

§3 names `detection_rate`, `confidence_mean` and `track_length`. **This emits the
second and the third and deliberately not the first**, and the reason is the same
one that got the invented figures deleted from the report.

`detection_rate` — detections per unit time — falls for two completely different
reasons: the model got worse, or the room emptied. There is no way to tell them
apart from inside this consumer, and a booth is empty most of the time. A
detector that fires every lunchtime is a detector an operator learns to ignore,
and then it is worth less than nothing, because the panel now looks staffed.

The other two are **per-detection statistics**: they are computed from samples,
so an empty window contributes no samples rather than a low reading.

- `confidence_mean` — the model's own confidence in the people it did find. It
  falls when the scene degrades (a light moved, a lens fogged, someone hung a
  banner) and is flat when the room is simply quiet.
- `track_length` — measured here as the **share of visits that ended in a
  dropout** rather than in a move. Rising fragmentation means the tracker is
  losing people mid-visit, which is what drift looks like downstream, and the
  tracker already stamps `reason` on every `spatial.zone_exit` and `spatial.dwell`
  (`consumers/tracker.py`), so it needs no new producer.

If a producer for detection rate ever exists that can separate the two causes —
occupancy from a second sensor, say — this is where it goes.

## The baseline is the session's own first window

Not a configured number and not a cross-session average. A configured baseline
is a guess made before the camera was pointed anywhere, and it is wrong in one
of two silent directions for the whole activation. A cross-session average needs
the benchmark dataset that is a different, unbuilt Phase 6 bullet.

Window 0 therefore emits nothing — it is being measured, not judged — and every
later window compares against it. Recomputed from the log at each evaluation
rather than carried in memory, so a restart and a replay produce the same
answer.

**A `calibration.updated` resets it.** Masking pixels changes what the model
sees and therefore what it is confident about, so a mask edit measured against
the pre-mask baseline reads as drift the instant it is applied — the system
alarming about the operator's own correct action. After a calibration the
baseline becomes the first complete window that follows it. This is why the two
halves of this roadmap bullet are one piece of work.

## Event time, fixed contiguous windows, derived ids

All three copied from `consumers/insights.py`, including the trap it records:
a derived event is always appended at a higher seq than its cause, so the
"have I already written this window?" lookup must **not** be bounded by the
triggering event's seq or it can never see its own output. What a window
*contains* is still read under `before_seq`, which is what makes a replay
reproduce the run exactly.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from app import db, repository
from app.config import get_settings
from app.consumers.base import Consumer
from app.consumers.ids import derive_event_id
from app.models import EventLog
from app.schemas import EventIn

log = logging.getLogger(__name__)

DETECTED = "drift.detected"
DETECTION = "perception.detection"
ZONE_EXIT = "spatial.zone_exit"
DWELL = "spatial.dwell"
CALIBRATION_UPDATED = "calibration.updated"

#: What the tracker stamps on a visit that ended because it lost the person.
REASON_DROPOUT = "dropout"

#: Attributed to detections that carry no `camera_id`. Every event logged before
#: Phase 6 is one of those, and a consumer that dead-lettered its way through a
#: season of history would bury the queue `/ops` exists to surface.
UNATTRIBUTED_CAMERA = "unattributed"

#: As in `consumers/insights.py`: a bound on how much one late event may catch
#: up, so a single arrival cannot summarise a whole day inside one handler.
MAX_CATCHUP = 24

#: Metrics where a *fall* is the bad direction, versus where a *rise* is.
#: Confidence dropping is degradation; dropouts rising is degradation. Without
#: this the comparison would have to be written twice and the second copy would
#: eventually disagree with the first.
WORSE_WHEN_LOWER = ("confidence_mean",)


class DriftConsumer(Consumer):
    name = "drift"

    handles = (DETECTION, ZONE_EXIT, DWELL, CALIBRATION_UPDATED)

    #: Retryable. Everything it emits is derived from the log under a seq bound,
    #: the id is a pure function of the window, and nothing leaves the building —
    #: so re-running one parked event produces exactly what the first attempt
    #: would have. Same reasoning as the graph writer's.
    retryable = True

    async def handle(self, event: EventLog) -> None:
        settings = get_settings()
        interval = dt.timedelta(minutes=settings.drift_window_minutes)

        async with db.SessionLocal() as session:
            await db.scope_to_tenant(session, event.tenant_id)

            origin = await self._origin(session, event)
            if origin is None:
                return

            complete = int((event.occurred_at - origin) / interval)
            if complete <= 0:
                # Still inside window 0. Which is the baseline window, so there
                # would be nothing to compare against even if it had closed.
                return

            # Unbounded, per the module docstring — a drift event is written
            # after the detections it measures, so a seq bound at the triggering
            # event can never see one.
            previous = await repository.last_event_of_type(
                session,
                tenant_id=event.tenant_id,
                session_id=event.session_id,
                type=DETECTED,
            )
            start = 1  # window 0 is the baseline and is never emitted
            if previous is not None:
                start = max(start, self._index_of(previous, origin, interval) + 1)

            baseline = await self._measure(
                session, event, window_from=origin, window_to=origin + interval
            )
            if not baseline:
                # No baseline, nothing to compare against, and inventing one
                # would be inventing the finding. Later windows will find one
                # once a window with enough samples has closed.
                return

            for index in range(start, min(complete, start + MAX_CATCHUP)):
                await self._compare(
                    session,
                    event=event,
                    baseline=baseline,
                    window_from=origin + index * interval,
                    window_to=origin + (index + 1) * interval,
                )

            # This session is the consumer's own, not the batch's — `handle`
            # opens it above — so nothing appended here exists for anyone until
            # it is committed. Once per handled event rather than per emitted
            # event, so a catch-up over several windows is one transaction and
            # either lands whole or not at all.
            await session.commit()

    # ── where the windows start ──────────────────────────────────────────────

    async def _origin(self, session, event: EventLog) -> dt.datetime | None:
        """When the current measurement epoch began.

        The session's first detection, or the last `calibration.updated` if
        there has been one — a recalibration starts a new epoch, because the
        camera after a mask edit is not the camera the old baseline described.

        Bounded by the triggering event's seq, unlike the "have I written this
        window?" lookup above. This one decides what a window *contains*, and a
        replay must not see a calibration that had not happened yet.
        """
        calibration = await repository.last_event_of_type(
            session,
            tenant_id=event.tenant_id,
            session_id=event.session_id,
            type=CALIBRATION_UPDATED,
            before_seq=event.seq + 1,
        )
        if calibration is not None:
            return calibration.occurred_at

        rows = await repository.read_events(
            session,
            tenant_id=event.tenant_id,
            session_id=event.session_id,
            type=DETECTION,
            limit=1,
        )
        return rows[0].occurred_at if rows else None

    def _index_of(
        self, previous: EventLog, origin: dt.datetime, interval: dt.timedelta
    ) -> int:
        """Which window an already-written drift event covered.

        Read from its own recorded window start rather than from `occurred_at`,
        for the reason `insights.py` gives: the two are the same today and would
        stop being the same the moment the interval changed mid-session.

        A drift event from *before* the current epoch (one written before the
        last recalibration) lands at a negative index, which `max(start, 1)`
        above turns back into "start at window 1" — correct, since the epoch is
        new and no window in it has been written yet.
        """
        window = (previous.payload or {}).get("window") or {}
        starts = window.get("from")
        at = dt.datetime.fromisoformat(starts) if starts else previous.occurred_at
        return int((at - origin) / interval)

    # ── measuring one window ─────────────────────────────────────────────────

    async def _measure(
        self,
        session,
        event: EventLog,
        *,
        window_from: dt.datetime,
        window_to: dt.datetime,
    ) -> dict[str, dict[str, float]]:
        """`{camera_id: {metric: value}}` for one window. Cameras with too few
        samples are absent rather than present with a shaky number.

        `[from, to)` — start inclusive, end exclusive — so every event belongs to
        exactly one window. `repository.read_window`'s docstring argues this at
        length; the short version is that the other combination either drops the
        session's very first event or counts a boundary event twice.
        """
        settings = get_settings()

        confidence: dict[str, list[float]] = {}
        for row in await self._window(session, event, DETECTION, window_from, window_to):
            payload = row.payload or {}
            value = payload.get("confidence")
            if isinstance(value, (int, float)):
                confidence.setdefault(self._camera(payload), []).append(float(value))

        # Dropout share. Both types carry `reason`, and a visit that ends by
        # moving on produces one of each — so counting both would weight moves
        # double against dropouts and understate fragmentation. Exits alone are
        # the complete set of visit endings.
        ends: dict[str, list[bool]] = {}
        for row in await self._window(session, event, ZONE_EXIT, window_from, window_to):
            payload = row.payload or {}
            ends.setdefault(self._camera(payload), []).append(
                payload.get("reason") == REASON_DROPOUT
            )

        out: dict[str, dict[str, float]] = {}
        for camera, values in confidence.items():
            if len(values) >= settings.drift_min_samples:
                out.setdefault(camera, {})["confidence_mean"] = sum(values) / len(values)
        for camera, flags in ends.items():
            if len(flags) >= settings.drift_min_samples:
                out.setdefault(camera, {})["track_length"] = sum(flags) / len(flags)
        return out

    async def _window(
        self,
        session,
        event: EventLog,
        type: str,
        window_from: dt.datetime,
        window_to: dt.datetime,
    ) -> list[EventLog]:
        return await repository.read_window(
            session,
            tenant_id=event.tenant_id,
            session_id=event.session_id,
            type=type,
            since=window_from,
            until=window_to,
            before_seq=event.seq + 1,
            start_inclusive=True,
            end_inclusive=False,
        )

    @staticmethod
    def _camera(payload: dict[str, Any]) -> str:
        """Which camera an event is about.

        `spatial.*` events are derived by the tracker from a detection, and the
        tracker does not currently carry the camera through — so exits fall to
        `unattributed` today. That is honest rather than convenient: attributing
        them to the only camera in the payload set would be a guess that becomes
        wrong the first time a booth has two.
        """
        value = payload.get("camera_id")
        return str(value) if value else UNATTRIBUTED_CAMERA

    # ── comparing, and saying so ─────────────────────────────────────────────

    async def _compare(
        self,
        session,
        *,
        event: EventLog,
        baseline: dict[str, dict[str, float]],
        window_from: dt.datetime,
        window_to: dt.datetime,
    ) -> None:
        settings = get_settings()
        observed = await self._measure(
            session, event, window_from=window_from, window_to=window_to
        )

        for camera, metrics in sorted(observed.items()):
            for metric, value in sorted(metrics.items()):
                base = baseline.get(camera, {}).get(metric)
                if base is None or base == 0:
                    # No baseline for this camera and metric — a camera that came
                    # online after window 0, or a metric that had too few samples
                    # then. Comparing against nothing would mean comparing
                    # against zero, which reads as total collapse.
                    continue

                severity = self._severity(metric, observed=value, baseline=base, settings=settings)
                if severity is None:
                    continue

                await self._emit(
                    session,
                    event=event,
                    camera_id=camera,
                    metric=metric,
                    observed=value,
                    baseline=base,
                    severity=severity,
                    window_from=window_from,
                    window_to=window_to,
                )

    @staticmethod
    def _severity(
        metric: str, *, observed: float, baseline: float, settings: Any
    ) -> str | None:
        """`"warn"`, `"critical"`, or None for a change not worth an event.

        Only degradation is reported. A camera whose confidence *improved* has
        not drifted in any sense an operator needs to act on, and an event
        saying so would make the panel a feed rather than a queue.
        """
        change = (observed - baseline) / abs(baseline)
        worsened = -change if metric in WORSE_WHEN_LOWER else change
        if worsened >= settings.drift_critical_ratio:
            return "critical"
        if worsened >= settings.drift_warn_ratio:
            return "warn"
        return None

    async def _emit(
        self,
        session,
        *,
        event: EventLog,
        camera_id: str,
        metric: str,
        observed: float,
        baseline: float,
        severity: str,
        window_from: dt.datetime,
        window_to: dt.datetime,
    ) -> None:
        window_seconds = int((window_to - window_from).total_seconds())
        await repository.append_event(
            session,
            EventIn(
                event_id=derive_event_id(
                    "drift",
                    event.tenant_id,
                    event.session_id,
                    camera_id,
                    metric,
                    window_from.isoformat(),
                ),
                tenant_id=event.tenant_id,
                session_id=event.session_id,
                type=DETECTED,
                payload={
                    "camera_id": camera_id,
                    "metric": metric,
                    # Both sides, always. event-bus-spec.md §3: the event states
                    # the comparison it is making rather than asserting a verdict
                    # somebody later cannot check.
                    "observed": round(observed, 4),
                    "baseline": round(baseline, 4),
                    "window_seconds": window_seconds,
                    "severity": severity,
                    # Not in §3's payload, and additive. `_index_of` reads it
                    # back to know which windows are already written, and it has
                    # to survive an interval change mid-session — deriving the
                    # window from occurred_at would silently renumber every
                    # earlier event when the interval moved.
                    "window": {
                        "from": window_from.isoformat(),
                        "to": window_to.isoformat(),
                    },
                },
                # The window's end, not the triggering event's time. The finding
                # is about the window, and dating it by whichever event happened
                # to close it would put it out of order with its own evidence.
                occurred_at=window_to,
            ),
        )
