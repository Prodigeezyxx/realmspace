"""
Zone geometry — a port of dashboard/src/skills/zone-detect.ts.

Deliberately a port, not a reimplementation. The browser already decides who is
inside a zone (`pointInPolygon`, lines 21–38 there) and the dashboard's live view
runs on that logic today. If the edge used a different rule, the same person
could be "in the Lounge" on screen and "in the Atrium" in the report, and nobody
would be able to say which was right.

Keep the two in sync. If one changes, change both.
"""

from __future__ import annotations

import math
from typing import Any, Sequence

Point = Sequence[float]
Polygon = Sequence[Point]


def point_in_polygon(x: float, y: float, polygon: Polygon) -> bool:
    """Ray-casting: count edge crossings to the left of the point.

    Odd number of crossings → inside. Line-for-line the same algorithm as
    zone-detect.ts:21–38, including its behaviour on the boundary, so the two
    never disagree about a person standing exactly on a zone edge.
    """
    inside = False
    n = len(polygon)
    if n < 3:  # not a polygon
        return False

    j = n - 1
    for i in range(n):
        xi, yi = polygon[i][0], polygon[i][1]
        xj, yj = polygon[j][0], polygon[j][1]
        if (yi > y) != (yj > y):
            if x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                inside = not inside
        j = i
    return inside


def normalize(cx: float, cy: float, frame_width: float, frame_height: float) -> tuple[float, float]:
    """Pixel coords → 0..1, matching zone-detect.ts:41.

    Zone polygons are stored normalized so a zone drawn once survives a change
    of camera resolution. Detections arrive in pixels, so every comparison has
    to go through here first.
    """
    if frame_width <= 0 or frame_height <= 0:
        raise ValueError(
            f"frame dimensions must be positive, got {frame_width}x{frame_height}"
        )
    return cx / frame_width, cy / frame_height


def centroid(bbox: Sequence[float]) -> tuple[float, float]:
    """Centre of an [x1, y1, x2, y2] box.

    Note the format: perception/realmspace.py emits xyxy (corner-to-corner),
    while the browser's tracker.ts uses [x, y, width, height]. Same picture,
    different convention — the bus carries xyxy, which is what YOLO returns
    natively, so nothing has to convert on the way in.
    """
    x1, y1, x2, y2 = bbox[0], bbox[1], bbox[2], bbox[3]
    return (x1 + x2) / 2.0, (y1 + y2) / 2.0


def dist_to_segment(
    px: float, py: float, x1: float, y1: float, x2: float, y2: float
) -> float:
    """Shortest distance from a point to a line *segment*, not to its infinite line.

    The clamp on `t` is what makes it a segment: without it, a point beyond the
    end of an edge measures to an imaginary continuation of that edge, and a
    person standing well past the corner of a zone would read as adjacent to it.
    """
    dx = x2 - x1
    dy = y2 - y1
    len_sq = dx * dx + dy * dy
    if len_sq == 0:  # degenerate edge — the two ends are the same point
        t = 0.0
    else:
        t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / len_sq))
    ex = x1 + t * dx - px
    ey = y1 + t * dy - py
    return math.hypot(ex, ey)


def dist_to_polygon(x: float, y: float, polygon: Polygon) -> float:
    """Distance from a normalized point to a polygon. Zero if inside it.

    Used for pass-by: somebody who came within a short distance of a zone and
    never went in. Inside counts as zero rather than as a negative depth,
    because "how far in were they" is not a question pass-by asks — they either
    entered, in which case they are not a pass-by at all, or they did not.

    Line-for-line the same as `distToPolygon` in the postgres-track's
    `spatial-deriver.ts`, for the reason this whole module exists: if the two
    disagree about who counted as passing by, the same visitor is a negative
    signal on one track and nothing at all on the other.
    """
    if point_in_polygon(x, y, polygon):
        return 0.0

    n = len(polygon)
    if n < 2:
        return math.inf

    smallest = math.inf
    j = n - 1
    for i in range(n):
        smallest = min(
            smallest,
            dist_to_segment(
                x, y, polygon[j][0], polygon[j][1], polygon[i][0], polygon[i][1]
            ),
        )
        j = i
    return smallest


def zone_for_point(nx: float, ny: float, zones: Sequence[dict[str, Any]]) -> str | None:
    """Which zone contains this normalized point, or None.

    First match wins, as in zone-detect.ts:66–72 which `break`s on the first
    containing zone. Overlapping zones are therefore resolved by order, not by
    area — worth knowing if an operator draws a zone inside another one.
    """
    for zone in zones:
        polygon = zone.get("polygon") or []
        if point_in_polygon(nx, ny, polygon):
            return zone["id"]
    return None


def ray_segment_distance(
    ox: float,
    oy: float,
    dx: float,
    dy: float,
    ax: float,
    ay: float,
    bx: float,
    by: float,
) -> float:
    """How far along a ray it crosses a segment, or `inf` if it never does.

    Standard 2D ray/segment intersection: solve `origin + t·direction = a + u·(b − a)`
    for `t ≥ 0` and `0 ≤ u ≤ 1`. Returns `t`, which is a distance because the
    direction is a unit vector.

    Parallel lines (a zero cross product) return `inf` rather than raising. A ray
    running exactly along a zone edge is a degenerate case an operator can
    genuinely produce by drawing an axis-aligned zone, and "never hits it" is the
    honest answer — a ray in the plane of an edge has not crossed into anything.
    """
    ex, ey = bx - ax, by - ay
    denom = dx * ey - dy * ex
    if denom == 0:
        return math.inf

    ox_a, oy_a = ax - ox, ay - oy
    t = (ox_a * ey - oy_a * ex) / denom
    u = (ox_a * dy - oy_a * dx) / denom
    if t < 0 or not (0.0 <= u <= 1.0):
        return math.inf
    return t


def first_zone_along_ray(
    nx: float,
    ny: float,
    heading: float,
    zones: Sequence[dict[str, Any]],
    exclude: str | None = None,
) -> tuple[str, float] | None:
    """The first zone a ray from (nx, ny) crosses, and how far away it is.

    **Backend only, and deliberately not ported.** The rest of this module is a
    line-for-line port of `dashboard/src/skills/zone-detect.ts` because the
    browser and the edge both decide who is inside a zone, and a disagreement
    there would put the same visitor in two rooms. Nothing in the browser casts
    a ray: gaze is derived once, on the edge, from keypoints that never leave
    the machine (`privacy.md`). If a browser reader is ever added, port this
    then — and keep the two in sync from that day.

    `exclude` is the zone the person is standing in. Looking at the floor you
    are on is not attention, and a ray originating inside a polygon always
    crosses that polygon on its way out, so without this every gaze would land
    on the zone the visitor already occupies.

    Nearest hit wins. Two zones along the same line of sight is a real booth
    layout — a product wall behind a plinth — and the near one is what a person
    is looking at. Overlapping zones are resolved by distance here rather than
    by draw order, which is what `zone_for_point` does; the two questions are
    different enough that matching its behaviour would be the wrong kind of
    consistency.
    """
    dx, dy = math.cos(heading), math.sin(heading)

    best_id: str | None = None
    best_t = math.inf

    for zone in zones:
        if exclude is not None and zone["id"] == exclude:
            continue
        polygon = zone.get("polygon") or []
        n = len(polygon)
        if n < 3:
            continue

        j = n - 1
        for i in range(n):
            t = ray_segment_distance(
                nx,
                ny,
                dx,
                dy,
                polygon[j][0],
                polygon[j][1],
                polygon[i][0],
                polygon[i][1],
            )
            if t < best_t:
                best_t = t
                best_id = zone["id"]
            j = i

    if best_id is None:
        return None
    return best_id, best_t
