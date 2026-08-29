"""
A dropped frame is not the end of the footage.

The bug these are written against: perception opened the camera, the first
`cap.read()` returned nothing, and the run ended one second later with
`{"type": "session_end", "frames": 0}` and no other explanation. Found on
2026-08-28 while trying to measure something else, which is how it had survived
— it only shows up on a device that is a fraction slow to wake.

Driven by a fake capture rather than a webcam, so these run everywhere and fail
for one reason. `perception/capture.py` takes anything with a `.read()` for
exactly that, the same split `heading.py` and `bus_client.py` already use.
"""

from __future__ import annotations

from perception.capture import open_first_frame, read_frame


class FakeCapture:
    """A camera that fails a stated number of times, then works.

    `fails=None` never works at all — the genuinely dead camera, as opposed to
    the slow one, which is the distinction the module exists to make.
    """

    def __init__(self, fails: int | None = 0) -> None:
        self.fails = fails
        self.calls = 0

    def read(self):
        self.calls += 1
        if self.fails is None:
            return False, None
        if self.calls <= self.fails:
            return False, None
        return True, f"frame-{self.calls}"


class Clock:
    """Time that only moves when somebody sleeps, so a timeout is exact."""

    def __init__(self) -> None:
        self.t = 0.0

    def sleep(self, seconds: float) -> None:
        self.t += seconds

    def now(self) -> float:
        return self.t


class TestFirstFrame:
    def test_a_camera_that_wakes_late_is_waited_for(self):
        """The actual bug. One failed read used to end the whole run."""
        clock = Clock()
        cap = FakeCapture(fails=5)

        ok, frame, attempts = open_first_frame(cap, sleep=clock.sleep, now=clock.now)

        assert ok is True
        assert frame == "frame-6"
        assert attempts == 6

    def test_a_camera_that_never_delivers_gives_up(self):
        """Distinct from never opening, and it gets its own exit code.

        Giving up matters as much as waiting: a booth with a dead camera should
        find out at startup, not after an hour of recording nothing.
        """
        clock = Clock()
        cap = FakeCapture(fails=None)

        ok, frame, attempts = open_first_frame(
            cap, timeout=1.0, sleep=clock.sleep, now=clock.now
        )

        assert ok is False
        assert frame is None
        assert attempts > 1, "it should have retried rather than failing on the first read"

    def test_a_working_camera_is_not_slowed_down(self):
        """One read, no sleep. The common case pays nothing for the fix."""
        clock = Clock()
        cap = FakeCapture(fails=0)

        ok, _frame, attempts = open_first_frame(cap, sleep=clock.sleep, now=clock.now)

        assert (ok, attempts) == (True, 1)
        assert clock.t == 0.0


class TestReadFrame:
    def test_a_camera_stutter_is_tolerated_and_counted(self):
        clock = Clock()
        cap = FakeCapture(fails=3)

        ok, frame, dropped = read_frame(
            cap, live=True, sleep=clock.sleep, now=clock.now
        )

        assert ok is True
        assert frame == "frame-4"
        # Reported rather than swallowed: a run that limped should be
        # distinguishable from a clean one.
        assert dropped == 3

    def test_a_camera_that_goes_away_ends_the_run(self):
        clock = Clock()
        cap = FakeCapture(fails=None)

        ok, _frame, dropped = read_frame(
            cap, live=True, timeout=1.0, sleep=clock.sleep, now=clock.now
        )

        assert ok is False
        assert dropped > 1

    def test_the_end_of_a_file_ends_the_loop_immediately(self):
        """The half that must not change.

        `not ok` on a file is end-of-file. Retrying there would spin for the
        timeout at the end of every clip — and every replay of recorded footage
        ends this way, so it is the common path, not an edge case.
        """
        clock = Clock()
        cap = FakeCapture(fails=None)

        ok, _frame, dropped = read_frame(
            cap, live=False, sleep=clock.sleep, now=clock.now
        )

        assert ok is False
        assert dropped == 0
        assert cap.calls == 1, "a file source must not be retried"
        assert clock.t == 0.0

    def test_an_ordinary_frame_costs_one_read(self):
        clock = Clock()
        cap = FakeCapture(fails=0)

        ok, frame, dropped = read_frame(cap, live=True, sleep=clock.sleep, now=clock.now)

        assert (ok, frame, dropped) == (True, "frame-1", 0)
        assert clock.t == 0.0
