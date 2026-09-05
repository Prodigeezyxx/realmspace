"""
The privacy mask, applied before any model runs.

`privacy.md` §"Sensitive zones": *"the operator can draw an 'opt-out' zone on the
calibration step. Pixels within that polygon are masked before any model runs."*
That sentence has been in the doc since Phase 0 and the session wizard repeats it
to the operator at setup. Nothing did it. This module and
`backend/app/routers/calibration.py` are it.

## Why it fails closed

An edge box with no mask and no way to fetch one **refuses to start**. That is
the same rule Phase 4 applied to `credential_encryption_key` (refuse to store
rather than write plaintext) and Phase 6 applied to the Firebase verifier (503
rather than trust an email), and it is here for a sharper reason than either:
every other failure in this system produces a number nobody can trust, while
this one produces a recording of a person who was promised they would not be
recorded. There is nothing to review afterwards and nothing to retract.

So the ordering is: a mask fetched from the backend, else the mask cached from
the last successful fetch, else — if the camera is declared to have one, or if
we cannot tell — stop.

## Why the cache is on disk

The deployment this exists for is a laptop on a venue LAN with the wifi dropping
(`event-bus-spec.md` §5, the same premise as the event buffer). A box that boots
during an outage has to mask, and the only mask it can have is the one it saw
last. The cache sits beside the event buffer for the same reason that file does.

Staleness is bounded by the revision, not by a TTL: the backend increments
`mask_revision` on every calibration, so a poll compares one integer and knows
whether what it holds is current. A TTL would have to choose between refetching
constantly and applying an out-of-date mask, and there is no safe answer to that
when the polygon is the difference between masking a payment terminal and not.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any

#: Beside the event buffer, for the same reason it is there.
DEFAULT_CACHE = ".mask-cache.json"

#: How often to ask whether the mask changed. An operator who redraws mid-session
#: expects it to take effect in seconds, not at the next restart; a camera loop
#: cannot afford a request per frame. Ten seconds is the same order as the
#: tracker's zone cache TTL, and for the same reason.
DEFAULT_POLL_SECONDS = 10.0


class MaskUnavailable(RuntimeError):
    """Raised at startup when we cannot establish whether to mask, or how.

    Deliberately fatal rather than a warning. A warning on stderr during a
    venue setup is a line nobody reads, and the consequence of missing it is
    unmasked pixels reaching a model.
    """


@dataclass
class Mask:
    """One camera's opt-out polygon, normalized 0..1, plus its revision.

    `polygon is None` is a camera the operator declared and drew no mask on — a
    booth with no sensitive surface. It is a real answer, not a missing one, and
    it is why `apply` is a no-op rather than an error in that case.
    """

    polygon: list[list[float]] | None
    revision: int
    #: Where this came from, for the line printed at startup. An operator
    #: watching a setup should be able to see "cache" and go and fix the network
    #: before doors open, rather than discovering it in the log afterwards.
    source: str = "backend"

    @property
    def masks_anything(self) -> bool:
        return bool(self.polygon)


def _read_cache(path: str) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None


def _write_cache(path: str, camera_id: str, mask: Mask) -> None:
    payload = {
        "camera_id": camera_id,
        "polygon": mask.polygon,
        "revision": mask.revision,
        "cached_at": time.time(),
    }
    try:
        # Write-then-rename, so a box that loses power mid-write boots from the
        # old mask rather than from half a JSON document it cannot parse — which
        # `_read_cache` would report as no cache at all, and startup would then
        # refuse on a booth that had a perfectly good mask a second earlier.
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        os.replace(tmp, path)
    except OSError:
        pass


class MaskFetcher:
    """Fetches, caches and applies one camera's privacy mask.

    Constructed disabled when there is no bus to ask. That is not a hole: with
    no `--bus-url` there is no backend to have drawn a mask on, so the operator
    has not been offered the feature and nothing has been promised. The refusal
    applies to a configured deployment that cannot reach its own configuration.
    """

    def __init__(
        self,
        bus: Any,
        session_id: str,
        camera_id: str,
        cache_file: str = DEFAULT_CACHE,
        poll_seconds: float = DEFAULT_POLL_SECONDS,
    ) -> None:
        self.bus = bus
        self.session_id = session_id
        self.camera_id = camera_id
        self.cache_file = cache_file
        self.poll_seconds = poll_seconds
        self.enabled = bool(getattr(bus, "enabled", False))
        self.mask: Mask | None = None
        self._last_poll = 0.0
        self._pixel_polygon: list[list[int]] | None = None
        self._pixel_shape: tuple[int, int] | None = None

    @property
    def _path(self) -> str:
        return f"/v1/sessions/{self.session_id}/cameras/{self.camera_id}/mask"

    # ── fetching ─────────────────────────────────────────────────────────────

    def _fetch(self) -> tuple[int, Mask | None]:
        status, body = self.bus.get_json(self._path)
        if status == 200 and isinstance(body, dict):
            polygon = body.get("polygon")
            return status, Mask(
                polygon=[list(point) for point in polygon] if polygon else None,
                revision=int(body.get("revision") or 0),
                source="backend",
            )
        return status, None

    def start(self) -> Mask:
        """Establish the mask before the first frame. Raises `MaskUnavailable`.

        The three outcomes, in the order they are tried:

        1. **The backend answers.** Use it, cache it, done — including when the
           answer is "no mask", which is a decision the operator made.
        2. **The backend says 404.** The camera is not declared on this session.
           Refuse: this is almost always a typo in `--camera-id`, and the
           failure it would otherwise cause is a booth running unmasked because
           it asked about a camera that does not exist. A cached mask does not
           rescue this — the cache would be for a different camera.
        3. **The backend is unreachable.** Fall back to the cache if it is for
           this camera; refuse if it is not. An outage must not silently
           downgrade a masked booth to an unmasked one.
        """
        if not self.enabled:
            self.mask = Mask(polygon=None, revision=0, source="disabled")
            return self.mask

        status, mask = self._fetch()
        if mask is not None:
            self.mask = mask
            _write_cache(self.cache_file, self.camera_id, mask)
            self._last_poll = time.monotonic()
            return mask

        if status == 404:
            raise MaskUnavailable(
                f"camera {self.camera_id!r} is not declared on session "
                f"{self.session_id!r}. Add it to the session's cameras (or fix "
                "--camera-id) before running: an undeclared camera cannot be "
                "masked, and running anyway would be an unmasked booth."
            )

        cached = _read_cache(self.cache_file)
        if cached and cached.get("camera_id") == self.camera_id:
            polygon = cached.get("polygon")
            self.mask = Mask(
                polygon=[list(point) for point in polygon] if polygon else None,
                revision=int(cached.get("revision") or 0),
                source="cache",
            )
            self._last_poll = time.monotonic()
            return self.mask

        raise MaskUnavailable(
            f"cannot reach {self.bus.base_url} to fetch the privacy mask for "
            f"camera {self.camera_id!r}, and no cached mask for that camera "
            "exists (HTTP status "
            f"{status or 'no response'}). Refusing to start: privacy.md promises "
            "masked pixels, and starting unmasked would break that silently. "
            "Bring the backend up once, or run without --bus-url."
        )

    def poll(self) -> bool:
        """Refetch if it is time to. True when the mask changed.

        A failure here is *not* fatal, unlike `start`. The distinction matters:
        at startup we do not know what to mask, while here we hold a mask the
        backend gave us and the worst case is that it is one revision behind for
        a few seconds. Refusing mid-session would drop the camera over a wifi
        blip and lose the activation.
        """
        if not self.enabled or self.mask is None:
            return False
        now = time.monotonic()
        if now - self._last_poll < self.poll_seconds:
            return False
        self._last_poll = now

        _, fetched = self._fetch()
        if fetched is None or fetched.revision == self.mask.revision:
            return False

        self.mask = fetched
        _write_cache(self.cache_file, self.camera_id, fetched)
        self._pixel_polygon = None  # geometry changed; rebuild on next frame
        return True

    # ── applying ─────────────────────────────────────────────────────────────

    def polygon_pixels(self, height: int, width: int) -> list[list[int]] | None:
        """The polygon in pixels for this frame size, built once and reused.

        The inverse of `consumers/zones.py:normalize`, which divides a detection
        by the frame size. The two have to agree about what a normalized
        coordinate means, or a mask drawn over the left half of the preview
        would blank a different half of the frame.

        Rebuilt when the frame size changes as well as when the polygon does —
        a source switched mid-run (a clip after a webcam) would otherwise be
        masked with the previous resolution's pixels.

        Plain lists, not a numpy array. This module deliberately imports neither
        numpy nor cv2 at module scope, for the reason `pytest.ini` records about
        `bus_client.py`: the backend venv is the one that always gets run, and a
        mask that can only be tested where OpenCV is installed is a mask whose
        arithmetic goes untested.
        """
        if self.mask is None or not self.mask.polygon:
            return None
        if self._pixel_polygon is not None and self._pixel_shape == (height, width):
            return self._pixel_polygon
        self._pixel_polygon = [
            [int(round(x * width)), int(round(y * height))] for x, y in self.mask.polygon
        ]
        self._pixel_shape = (height, width)
        return self._pixel_polygon

    def apply(self, frame: Any, cv2: Any) -> Any:
        """Fill the masked region with black, in place. Returns the frame.

        In place, and the caller keeps no copy of the original. Masking onto a
        copy would leave the unmasked frame in memory for the rest of the
        iteration, and the two things that write frames out — `--save` and the
        preview window — would each have to remember which copy to use. One
        frame object, already masked, is the version that cannot be got wrong.

        `cv2` is passed in rather than imported, and numpy is imported here
        rather than at module scope, so everything above this line runs in a
        venv that has neither.
        """
        polygon = self.polygon_pixels(frame.shape[0], frame.shape[1])
        if polygon is None:
            return frame
        import numpy as np

        cv2.fillPoly(frame, [np.array(polygon, dtype=np.int32)], (0, 0, 0))
        return frame
