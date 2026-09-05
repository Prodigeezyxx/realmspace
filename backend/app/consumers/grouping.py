"""
Group visits — who came with whom.

`roadmap.md`'s blind-spot table has listed "Group visits → `Group` node — P1/P2"
since the founder architecture dump. Everything around it was already built and
nothing filled the middle:

- `(:Group)` has been constrained in `graph/schema.py` since **migration 001**,
  added with a comment that it "is used by GROUP_MEMBER_OF and the 'Groups in
  Lounge' query in data-model.md but was never declared".
- `data-model.md` specifies the node — `size`, `cohesion`, `first_seen`,
  `last_seen` — and ships a worked example query that has returned nothing for
  the whole life of the project.
- `spatial.group` is in `event-bus-spec.md` §3's taxonomy and in the browser's
  `RealmEventType`.
- `PRD.md` lists "group formation" as a rules trigger, and the rule validator
  accepts any `spatial.*` prefix — so an operator could arm a rule on it today
  and it would never fire.

This is the producer.

## Proximity is not company

The whole difficulty. Three strangers queueing at a popular zone are within a
metre of each other for four minutes. A detector built on distance-and-time
calls that a family, calls every busy zone one giant group, and produces a
number an operator learns to ignore — the same failure `detection_rate` drift
was refused for, where a signal that fires on the common case is worse than no
signal because it looks like coverage.

So a pair has to clear one of two harder tests.

**Co-movement.** They stayed together *while the scene changed*: their shared
midpoint travelled a real distance while they remained within `group_radius`.
This is what separates walking the floor together from standing in the same
spot. A queue accumulates time without accumulating travel, and fails.

**Joint arrival and departure.** They entered a zone within
`group_joint_window_seconds` of each other and left within it too. This catches
the family who sit at one table for twenty minutes and never move — the case
co-movement alone would miss — and a queue fails it for the reason it is a
queue: its members arrive and leave at different times.

Either test, plus a cohesion floor, makes a **pair**. Groups are the connected
components over confirmed pairs, because two people together and one of them
with a third is a group of three, which is how people actually walk.

## Never across cameras

Both positions in the proximity test are normalized 0..1 within their own
camera's frame, so two people in different frames can measure a hand's width
apart while standing in different rooms — and `group_radius` would call them a
pair for the length of the session. There is no transform between the two
frames (zones are normalized image coordinates end to end, which is why
`homography` calibration is refused), so this cannot be fixed by converting,
only by declining to compare.

The cost is real and worth stating: a couple who split across two camera views
stop registering as a group until they are both in one view again. The
alternative is reporting strangers as families, which is the failure this whole
consumer exists to avoid.

## Zone transitions come from the tracker, not from here

This consumer subscribes to `spatial.zone_enter` / `spatial.zone_exit` rather
than deciding for itself who is in a zone. Those events have already been
through the tracker's confirm window, dropout sweep and minimum dwell — the
session-hygiene work the blind-spot table cites CHI '26 for ("71% of raw
sessions invalid without them"). A second opinion about who is in a zone would
eventually disagree with the report, and then the same visitor is in a group in
one place and not in another.

## Its own consumer, not part of the tracker

Same call `consumers/drift.py` made about the same stream. The tracker is the
real-time path with a <500ms detection-to-dashboard budget (Phase 1 acceptance),
it is already six hundred lines, and a bug in group detection must not be able
to stop dwell being measured.

## Event time, derived ids, and no wall clock

Every window here is measured on `occurred_at`, and every emitted `event_id` is
derived from what caused it, so a replay reproduces the run and the bus dedupes
it. `on_replay` clears state for the same reason the tracker's does: idempotency
protects against reprocessing the same input, not against processing it in a
different state.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any, Iterable

from app import db, repository
from app.config import get_settings
from app.consumers.base import Consumer
from app.consumers.ids import derive_event_id, person_key
from app.consumers.zones import centroid, normalize
from app.models import EventLog
from app.schemas import EventIn

log = logging.getLogger(__name__)

DETECTION = "perception.detection"
ZONE_ENTER = "spatial.zone_enter"
ZONE_EXIT = "spatial.zone_exit"
SESSION_ENDED = "session.ended"
GROUP = "spatial.group"

#: What a `spatial.group` is saying about the group it names.
FORMED = "formed"
CHANGED = "changed"
DISSOLVED = "dissolved"


def _hypot(ax: float, ay: float, bx: float, by: float) -> float:
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


@dataclass
class Person:
    """The little this needs to remember about one visitor."""

    x: float = 0.0
    y: float = 0.0
    last_seen: dt.datetime | None = None
    #: zone_id -> when they entered it, from the tracker's own events.
    entered_at: dict[str, dt.datetime] = field(default_factory=dict)
    #: zone_id -> when they left it.
    exited_at: dict[str, dt.datetime] = field(default_factory=dict)
    #: Where they were standing when last seen, so the panel can name a zone.
    zone: str | None = None
    #: Which camera saw them. None on a session that declares one camera or
    #: none, and on every detection logged before cameras had ids.
    camera: str | None = None


@dataclass
class Pair:
    """What two people have done in each other's company.

    `together` over `observed` is cohesion. `travel` accumulates only while they
    are together, which is what makes it a measure of going somewhere *with*
    somebody rather than of going somewhere.
    """

    observed: int = 0
    together: int = 0
    travel: float = 0.0
    #: Midpoint at the last observation in which they were together, so travel
    #: measures the pair rather than either person's own wandering.
    mid: tuple[float, float] | None = None
    last_together: dt.datetime | None = None
    first_together: dt.datetime | None = None
    #: Set once they have arrived and departed a zone in step. Sticky: having
    #: done it once is evidence, and losing it because they later used
    #: different doors would be forgetting something true.
    joint_transit: bool = False

    @property
    def cohesion(self) -> float:
        return self.together / self.observed if self.observed else 0.0


@dataclass
class Group:
    """A group as last announced, so a change can be recognised as one."""

    id: str
    members: frozenset[str]
    first_seen: dt.datetime
    cohesion: float


class GroupingConsumer(Consumer):
    name = "grouping"

    handles = (DETECTION, ZONE_ENTER, ZONE_EXIT, SESSION_ENDED)

    #: Not retryable in isolation, and for the reason the tracker states about
    #: itself: this carries state built from the events *before* the parked one
    #: — who was standing where, which pairs have travelled together, who
    #: arrived with whom. Replaying one detection against an empty state does
    #: not reproduce the original attempt, it produces a confident wrong answer.
    #: The repair is a cursor rewind, which is an operator action.
    retryable = False

    def __init__(self) -> None:
        super().__init__()
        # (tenant, session) -> anon_id -> Person
        self._people: dict[tuple[str, str], dict[str, Person]] = {}
        # (tenant, session) -> frozenset{a, b} -> Pair
        self._pairs: dict[tuple[str, str], dict[frozenset[str], Pair]] = {}
        # (tenant, session) -> group_id -> Group, as last announced
        self._groups: dict[tuple[str, str], dict[str, Group]] = {}

    async def on_replay(self, tenant_id: str) -> None:
        """Forget everybody, so the replay rebuilds them from the log.

        Without it a replay starts with pairs already confirmed from the
        original run and announces groups at moments nothing caused. Deriving
        ids does not save you: those events have genuinely different causes and
        so genuinely different ids, and the bus stores them.
        """
        for store in (self._people, self._pairs, self._groups):
            for key in [k for k in store if k[0] == tenant_id]:
                del store[key]

    # ── the loop ─────────────────────────────────────────────────────────────

    async def handle(self, event: EventLog) -> None:
        key = (event.tenant_id, event.session_id)
        payload = event.payload or {}

        if event.type == SESSION_ENDED:
            emitted = self._dissolve_all(event)
        elif event.type in (ZONE_ENTER, ZONE_EXIT):
            self._note_transition(key, event, payload)
            emitted = []
        else:
            emitted = self._on_detection(key, event, payload)

        if not emitted:
            return

        async with db.SessionLocal() as session:
            await db.scope_to_tenant(session, event.tenant_id)
            for out in emitted:
                await repository.append_event(session, out)
            await session.commit()

    # ── zone transitions, borrowed from the tracker ──────────────────────────

    def _note_transition(
        self, key: tuple[str, str], event: EventLog, payload: dict[str, Any]
    ) -> None:
        """Record when somebody entered or left a zone.

        Read off the tracker's events rather than recomputed — see the module
        docstring. Only the times are kept; which zone somebody is *in* is the
        tracker's business and this has no opinion about it.
        """
        anon_id = payload.get("anon_id") or payload.get("person_id")
        zone_id = payload.get("zone_id")
        if not anon_id or not zone_id:
            return

        person = self._people.setdefault(key, {}).setdefault(anon_id, Person())
        if event.type == ZONE_ENTER:
            person.entered_at[zone_id] = event.occurred_at
            person.zone = zone_id
        else:
            person.exited_at[zone_id] = event.occurred_at

    # ── detections, which is where the work happens ──────────────────────────

    def _on_detection(
        self, key: tuple[str, str], event: EventLog, payload: dict[str, Any]
    ) -> list[EventIn]:
        anon_id = person_key(payload)
        bbox = payload.get("bbox")
        frame_w = payload.get("frame_width")
        frame_h = payload.get("frame_height")
        if not anon_id or not bbox or not frame_w or not frame_h:
            # Malformed for this consumer's purposes. The tracker dead-letters
            # these and says why; a second copy of that complaint would put the
            # same event on `/ops` twice for one cause.
            return []

        settings = get_settings()
        now = event.occurred_at
        people = self._people.setdefault(key, {})

        cx, cy = centroid(bbox)
        nx, ny = normalize(cx, cy, frame_w, frame_h)

        person = people.setdefault(anon_id, Person())
        person.x, person.y, person.last_seen = nx, ny, now
        person.camera = payload.get("camera_id")
        self._prune(people, settings.tracker_max_tracked_people)

        pairs = self._pairs.setdefault(key, {})
        for other_id, other in people.items():
            if other_id == anon_id or other.last_seen is None:
                continue
            # Only compare against somebody seen recently. Two people are not
            # near each other because one of them stood there an hour ago.
            if (now - other.last_seen).total_seconds() > settings.group_break_seconds:
                continue
            # Never across cameras. Both positions below are normalized 0..1
            # within their own camera's frame, so two people in different frames
            # can measure a hand's width apart while standing in different
            # rooms — and `group_radius` would call them a pair. There is no
            # transform between the two frames (zones are normalized image
            # coordinates end to end; `homography` is refused for that reason,
            # roadmap.md), so this cannot be fixed by converting, only by
            # declining to compare.
            #
            # What that costs is real and worth stating: a couple who split up
            # across two camera views stop registering as a group until they are
            # both in one view again. The alternative is reporting strangers as
            # families, which is the failure this whole consumer exists to avoid.
            if other.camera != person.camera:
                continue
            self._observe(pairs, anon_id, other_id, person, other, now, settings)

        self._prune(pairs, settings.tracker_max_tracked_people * 4)
        return self._announce(key, event, now, settings)

    def _observe(
        self,
        pairs: dict[frozenset[str], Pair],
        a_id: str,
        b_id: str,
        a: Person,
        b: Person,
        now: dt.datetime,
        settings: Any,
    ) -> None:
        pair_key = frozenset((a_id, b_id))
        pair = pairs.setdefault(pair_key, Pair())
        pair.observed += 1

        distance = _hypot(a.x, a.y, b.x, b.y)
        if distance > settings.group_radius:
            return

        pair.together += 1
        pair.last_together = now
        if pair.first_together is None:
            pair.first_together = now

        # Travel accrues from the pair's midpoint, not from either person's, so
        # two people orbiting one spot do not accumulate distance between them.
        mid = ((a.x + b.x) / 2.0, (a.y + b.y) / 2.0)
        if pair.mid is not None:
            pair.travel += _hypot(mid[0], mid[1], pair.mid[0], pair.mid[1])
        pair.mid = mid

        if not pair.joint_transit and self._arrived_and_left_together(a, b, settings):
            pair.joint_transit = True

    @staticmethod
    def _arrived_and_left_together(a: Person, b: Person, settings: Any) -> bool:
        """Did these two enter and leave the same zone in step?

        Both halves are required, and that is the point. Arriving together is
        common — a door admits people in clumps. Arriving together *and* leaving
        together is what a queue does not do: its members join at different
        times and are served in order, so their entries and exits are spread
        even when they stand shoulder to shoulder throughout.
        """
        window = settings.group_joint_window_seconds
        for zone_id, a_in in a.entered_at.items():
            b_in = b.entered_at.get(zone_id)
            if b_in is None or abs((a_in - b_in).total_seconds()) > window:
                continue
            a_out, b_out = a.exited_at.get(zone_id), b.exited_at.get(zone_id)
            if a_out is None or b_out is None:
                continue
            if abs((a_out - b_out).total_seconds()) <= window:
                return True
        return False

    # ── from pairs to groups ─────────────────────────────────────────────────

    def _confirmed_pairs(
        self, pairs: dict[frozenset[str], Pair], now: dt.datetime, settings: Any
    ) -> list[frozenset[str]]:
        """The pairs that currently count. Either test, plus the two floors."""
        out: list[frozenset[str]] = []
        for pair_key, pair in pairs.items():
            if pair.together < settings.group_confirm_samples:
                continue
            if pair.cohesion < settings.group_min_cohesion:
                continue
            if pair.last_together is None:
                continue
            if (now - pair.last_together).total_seconds() > settings.group_break_seconds:
                continue  # drifted apart; not a pair any more
            if pair.travel >= settings.group_min_travel or pair.joint_transit:
                out.append(pair_key)
        return out

    @staticmethod
    def _components(edges: Iterable[frozenset[str]]) -> list[frozenset[str]]:
        """Connected components over confirmed pairs.

        Two people together, one of them with a third, is a group of three —
        which is how people actually walk, in a loose chain rather than a
        clique. Requiring every member to be paired with every other would split
        a family of four into fragments the moment one of them lagged.
        """
        parent: dict[str, str] = {}

        def find(x: str) -> str:
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for edge in edges:
            a, b = tuple(edge)
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        groups: dict[str, set[str]] = {}
        for member in list(parent):
            groups.setdefault(find(member), set()).add(member)
        return [frozenset(m) for m in groups.values() if len(m) >= 2]

    def _announce(
        self,
        key: tuple[str, str],
        event: EventLog,
        now: dt.datetime,
        settings: Any,
    ) -> list[EventIn]:
        """Emit for anything that changed since the last announcement."""
        pairs = self._pairs.get(key, {})
        confirmed = self._confirmed_pairs(pairs, now, settings)
        current = self._components(confirmed)
        known = self._groups.setdefault(key, {})

        emitted: list[EventIn] = []
        matched: set[str] = set()

        for members in current:
            existing = self._match(known, members)
            cohesion = self._cohesion(pairs, members)

            if existing is None:
                # A new group. Its id derives from the founding membership and
                # the moment it formed, and never changes afterwards — see
                # `_match` for why that matters more than it looks.
                first_seen = min(
                    (
                        pairs[p].first_together
                        for p in confirmed
                        if p <= members and pairs[p].first_together
                    ),
                    default=now,
                )
                group_id = str(
                    derive_event_id(
                        "group-id", *key, "|".join(sorted(members)), first_seen.isoformat()
                    )
                )
                known[group_id] = Group(group_id, members, first_seen, cohesion)
                matched.add(group_id)
                emitted.append(
                    self._event(event, known[group_id], members, cohesion, FORMED, now)
                )
                continue

            matched.add(existing.id)
            if existing.members != members:
                existing.members = members
                existing.cohesion = cohesion
                emitted.append(self._event(event, existing, members, cohesion, CHANGED, now))

        for group_id, group in list(known.items()):
            if group_id in matched:
                continue
            emitted.append(
                self._event(event, group, group.members, group.cohesion, DISSOLVED, now)
            )
            del known[group_id]

        return emitted

    @staticmethod
    def _match(known: dict[str, Group], members: frozenset[str]) -> Group | None:
        """Which already-announced group this membership is, if any.

        Matched by **overlap**, not equality, because the id must survive a
        change of membership. A group that gains a fourth person is the same
        group with one more person in it; minting a new id would give a family
        two histories and leave the first one looking as though it dissolved
        the moment somebody joined.

        The strongest overlap wins, so two groups merging attaches to the larger
        rather than to whichever happened to be first in the dict.
        """
        best: Group | None = None
        best_overlap = 0
        for group in known.values():
            overlap = len(group.members & members)
            if overlap > best_overlap:
                best, best_overlap = group, overlap
        return best if best_overlap >= 2 else None

    @staticmethod
    def _cohesion(pairs: dict[frozenset[str], Pair], members: frozenset[str]) -> float:
        """Mean cohesion over the member pairs that have actually been observed.

        Pairs within the group that were never seen together — the two ends of a
        chain — are left out rather than counted as zero. They are not evidence
        of a loose group, they are an absence of evidence about two people, and
        averaging a zero in would punish exactly the large groups that are
        hardest to detect.
        """
        seen = [pairs[p].cohesion for p in combinations_key(members) if p in pairs]
        return round(sum(seen) / len(seen), 3) if seen else 0.0

    def _event(
        self,
        cause: EventLog,
        group: Group,
        members: frozenset[str],
        cohesion: float,
        status: str,
        now: dt.datetime,
    ) -> EventIn:
        ordered = sorted(members)
        zone = self._zone_of(cause, ordered)
        return EventIn(
            event_id=derive_event_id(
                "group",
                cause.tenant_id,
                cause.session_id,
                group.id,
                status,
                "|".join(ordered),
                now.isoformat(),
            ),
            tenant_id=cause.tenant_id,
            session_id=cause.session_id,
            type=GROUP,
            payload={
                "group_id": group.id,
                "members": ordered,
                "size": len(ordered),
                "cohesion": cohesion,
                "zone_id": zone,
                "status": status,
            },
            occurred_at=now,
        )

    def _zone_of(self, cause: EventLog, members: list[str]) -> str | None:
        """Where the group is, if its members agree.

        None when they do not, rather than picking one. A group straddling a
        boundary is genuinely not in a zone, and naming one would put a group of
        four into a zone two of them are standing outside.
        """
        people = self._people.get((cause.tenant_id, cause.session_id), {})
        zones = {people[m].zone for m in members if m in people}
        return next(iter(zones)) if len(zones) == 1 else None

    # ── close-out and housekeeping ───────────────────────────────────────────

    def _dissolve_all(self, event: EventLog) -> list[EventIn]:
        """The operator closed the activation.

        Every open group is closed at the session's end rather than left
        announced-and-never-withdrawn — the same gap `session.ended` was added
        to fill for the visitors still in the room at close (Phase 2).
        """
        key = (event.tenant_id, event.session_id)
        known = self._groups.get(key, {})
        out = [
            self._event(
                event, g, g.members, g.cohesion, DISSOLVED, event.occurred_at
            )
            for g in known.values()
        ]
        known.clear()
        self._people.pop(key, None)
        self._pairs.pop(key, None)
        return out

    @staticmethod
    def _prune(store: dict, cap: int) -> None:
        """Insertion-ordered eviction, as `tracker._prune_where` does.

        The cap matters more here: pairs grow with the square of the number of
        live tracks, so an unbounded store on a busy floor is not a slow leak
        but a fast one.
        """
        while len(store) > cap:
            store.pop(next(iter(store)))


def combinations_key(members: frozenset[str]) -> list[frozenset[str]]:
    """Every unordered pair within a membership, as the keys `_pairs` uses."""
    return [frozenset(pair) for pair in combinations(sorted(members), 2)]
