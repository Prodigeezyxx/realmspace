"""
The privacy mask on the edge, as executable claims.

`privacy.md` §"Sensitive zones" promises that pixels inside an operator's opt-out
polygon are "masked before any model runs". Until Phase 6 nothing did it — the
doc said so, the session wizard repeated it to the operator, and no code
anywhere touched a pixel.

The load-bearing tests are the three refusals. Everything else here could pass
while a booth ran unmasked because the wifi was down.

Imports without numpy or cv2, deliberately, for the reason `pytest.ini` records
about `bus_client.py`: this is the venv that always gets run, and a mask whose
arithmetic is only testable where OpenCV is installed is a mask whose arithmetic
goes untested.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from perception.mask import MaskFetcher, MaskUnavailable

CAM = "cam-1"
S = "s_test"
RIGHT_HALF = [[0.5, 0.0], [1.0, 0.0], [1.0, 1.0], [0.5, 1.0]]


class FakeBus:
    """A `BusClient` with the network replaced by a scripted list of answers."""

    base_url = "http://bus.invalid"

    def __init__(self, *answers: tuple[int, Any], enabled: bool = True) -> None:
        self.enabled = enabled
        self.answers = list(answers)
        self.paths: list[str] = []

    def get_json(self, path: str) -> tuple[int, Any]:
        self.paths.append(path)
        # The last answer repeats, so a test only scripts what it cares about.
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


def fetcher(bus: FakeBus, tmp_path, **kwargs) -> MaskFetcher:
    return MaskFetcher(
        bus,
        session_id=S,
        camera_id=CAM,
        cache_file=str(tmp_path / "mask.json"),
        **kwargs,
    )


def masked(polygon: list, revision: int = 1) -> tuple[int, dict]:
    return 200, {"cameraId": CAM, "polygon": polygon, "revision": revision}


# ── the three refusals ────────────────────────────────────────────────────────


def test_an_unreachable_backend_with_no_cache_refuses_to_start(tmp_path) -> None:
    """**The one that matters.**

    A booth that starts unmasked because the wifi was down has recorded somebody
    who was promised they would not be. There is nothing to review afterwards
    and nothing to retract, which is why this is the one failure in the system
    that stops the process instead of warning.
    """
    with pytest.raises(MaskUnavailable) as raised:
        fetcher(FakeBus((0, None)), tmp_path).start()

    assert "no cached mask" in str(raised.value)
    assert "privacy.md" in str(raised.value)


def test_an_undeclared_camera_refuses_to_start(tmp_path) -> None:
    """404 is a typo in `--camera-id`, not a booth with no sensitive surface.

    Collapsing the two would make a misspelling read as a deliberate decision
    not to mask — and it would read that way silently, on a camera that is
    pointing at something.
    """
    with pytest.raises(MaskUnavailable) as raised:
        fetcher(FakeBus((404, None)), tmp_path).start()

    assert "not declared" in str(raised.value)


def test_a_cache_for_a_different_camera_does_not_rescue_a_404(tmp_path) -> None:
    """A cached mask is for one camera. It cannot answer for another.

    Worth its own test because "fall back to the cache" is the obvious
    simplification, and it would apply the front camera's polygon to whatever
    the operator actually mistyped.
    """
    (tmp_path / "mask.json").write_text(
        json.dumps({"camera_id": "cam-other", "polygon": RIGHT_HALF, "revision": 3})
    )
    with pytest.raises(MaskUnavailable):
        fetcher(FakeBus((0, None)), tmp_path).start()


# ── the outage it does survive ────────────────────────────────────────────────


def test_a_cached_mask_carries_a_box_through_an_outage(tmp_path) -> None:
    """The deployment this exists for: a laptop on a venue LAN, wifi dropping.

    A box that boots during an outage has to mask, and the only mask it can have
    is the one it saw last.
    """
    online = fetcher(FakeBus(masked(RIGHT_HALF, revision=4)), tmp_path)
    online.start()

    offline = fetcher(FakeBus((0, None)), tmp_path)
    mask = offline.start()

    assert mask.polygon == RIGHT_HALF
    assert mask.revision == 4
    assert mask.source == "cache"


def test_a_half_written_cache_is_ignored_rather_than_crashing(tmp_path) -> None:
    """Power lost mid-write. The atomic rename is why this normally cannot
    happen; this is what the reader does if it does anyway."""
    (tmp_path / "mask.json").write_text('{"camera_id": "cam-1", "poly')

    with pytest.raises(MaskUnavailable):
        fetcher(FakeBus((0, None)), tmp_path).start()


def test_no_bus_means_no_mask_and_no_refusal(tmp_path) -> None:
    """Running without `--bus-url` is not a deployment that lost its config.

    There is no backend to have drawn a mask on, so nothing has been promised to
    anybody. Refusing here would break every stdout-only run of the stub.
    """
    mask = fetcher(FakeBus((0, None), enabled=False), tmp_path).start()
    assert mask.polygon is None
    assert mask.source == "disabled"


# ── a camera with no mask is a real answer ────────────────────────────────────


def test_a_declared_camera_with_no_mask_runs_unmasked(tmp_path) -> None:
    """`polygon: null` is a booth with no sensitive surface — a decision."""
    mask = fetcher(FakeBus(masked(None, revision=0)), tmp_path).start()
    assert mask.polygon is None
    assert mask.masks_anything is False


# ── picking up a redraw mid-session ───────────────────────────────────────────


def test_a_redraw_is_picked_up_on_the_next_poll(tmp_path) -> None:
    left = [[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0]]
    bus = FakeBus(masked(RIGHT_HALF, revision=1), masked(left, revision=2))
    f = fetcher(bus, tmp_path, poll_seconds=0.0)
    f.start()

    assert f.poll() is True
    assert f.mask.polygon == left
    assert f.mask.revision == 2


def test_an_unchanged_revision_is_not_a_change(tmp_path) -> None:
    """The revision is the whole point of polling: one integer, not a polygon
    comparison, and not a TTL that has to choose between refetching constantly
    and applying an out-of-date mask."""
    f = fetcher(FakeBus(masked(RIGHT_HALF, revision=1)), tmp_path, poll_seconds=0.0)
    f.start()
    assert f.poll() is False


def test_a_failed_poll_keeps_the_mask_it_has(tmp_path) -> None:
    """Unlike `start`, a mid-session failure is not fatal.

    Here we hold a mask the backend gave us and the worst case is being one
    revision behind for a few seconds. Refusing would drop the camera over a
    wifi blip and lose the activation.
    """
    bus = FakeBus(masked(RIGHT_HALF, revision=1), (0, None))
    f = fetcher(bus, tmp_path, poll_seconds=0.0)
    f.start()

    assert f.poll() is False
    assert f.mask.polygon == RIGHT_HALF


def test_polling_is_rate_limited(tmp_path) -> None:
    """A request per frame at 20fps would be 1,200 a minute per camera."""
    bus = FakeBus(masked(RIGHT_HALF, revision=1))
    f = fetcher(bus, tmp_path, poll_seconds=3600.0)
    f.start()

    assert f.poll() is False
    assert len(bus.paths) == 1  # start only


# ── the arithmetic ────────────────────────────────────────────────────────────


def test_normalized_coordinates_become_pixels_for_this_frame(tmp_path) -> None:
    """The inverse of `consumers/zones.py:normalize`.

    If the two disagree, a mask drawn over the right half of the preview blanks
    a different part of the frame — and the operator has no way to see that from
    the dashboard, because the dashboard shows them the polygon they drew.
    """
    f = fetcher(FakeBus(masked(RIGHT_HALF)), tmp_path)
    f.start()

    assert f.polygon_pixels(480, 640) == [[320, 0], [640, 0], [640, 480], [320, 480]]


def test_a_changed_frame_size_rebuilds_the_polygon(tmp_path) -> None:
    """A source switched mid-run — a clip after a webcam — would otherwise be
    masked with the previous resolution's pixels."""
    f = fetcher(FakeBus(masked(RIGHT_HALF)), tmp_path)
    f.start()

    assert f.polygon_pixels(480, 640)[0] == [320, 0]
    assert f.polygon_pixels(1080, 1920)[0] == [960, 0]


def test_a_camera_with_no_mask_has_no_pixels_to_fill(tmp_path) -> None:
    f = fetcher(FakeBus(masked(None, revision=0)), tmp_path)
    f.start()
    assert f.polygon_pixels(480, 640) is None


def test_apply_fills_the_polygon_and_returns_the_same_frame(tmp_path) -> None:
    """In place, and the caller keeps no copy.

    Masking onto a copy would leave the unmasked frame alive for the rest of the
    iteration, and `--save` and the preview window would each have to remember
    which one to write.
    """

    class FakeFrame:
        shape = (480, 640, 3)

    class FakeCv2:
        def __init__(self) -> None:
            self.calls: list[tuple] = []

        def fillPoly(self, frame, polygons, colour) -> None:  # noqa: N802
            self.calls.append((frame, [p.tolist() for p in polygons], colour))

    f = fetcher(FakeBus(masked(RIGHT_HALF)), tmp_path)
    f.start()
    frame, cv2 = FakeFrame(), FakeCv2()

    pytest.importorskip("numpy", reason="apply is the one method that needs it")
    assert f.apply(frame, cv2) is frame
    assert cv2.calls[0][1] == [[[320, 0], [640, 0], [640, 480], [320, 480]]]
    assert cv2.calls[0][2] == (0, 0, 0)


def test_apply_on_an_unmasked_camera_touches_nothing(tmp_path) -> None:
    class FakeFrame:
        shape = (480, 640, 3)

    class ExplodingCv2:
        def fillPoly(self, *_args) -> None:  # noqa: N802
            raise AssertionError("nothing to fill")

    f = fetcher(FakeBus(masked(None, revision=0)), tmp_path)
    f.start()
    frame = FakeFrame()
    assert f.apply(frame, ExplodingCv2()) is frame


# ── the request it makes ──────────────────────────────────────────────────────


def test_it_asks_the_endpoint_the_router_serves(tmp_path) -> None:
    """Pinned because the two halves are in different languages and different
    directories, and a path typo would only surface on a real booth."""
    bus = FakeBus(masked(RIGHT_HALF))
    fetcher(bus, tmp_path).start()
    assert bus.paths == [f"/v1/sessions/{S}/cameras/{CAM}/mask"]
