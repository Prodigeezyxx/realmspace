"""
The two pieces of arithmetic gaze rests on, tested without a database.

`heading_from_keypoints` lives in `perception/heading.py`, which imports no cv2
and is split out of `realmspace.py` for that reason — the same reasoning
`pytest.ini` records for `bus_client.py`: the backend venv is the one that
always gets run, so the arithmetic worth testing lives where it can be.
"""

from __future__ import annotations

import math

import pytest

from app.consumers.zones import first_zone_along_ray, ray_segment_distance
from perception.heading import heading_from_keypoints

NEAR = {"id": "near", "polygon": [[0.5, 0.4], [0.6, 0.4], [0.6, 0.6], [0.5, 0.6]]}
FAR = {"id": "far", "polygon": [[0.8, 0.4], [0.9, 0.4], [0.9, 0.6], [0.8, 0.6]]}
AROUND = {"id": "around", "polygon": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]}

RIGHT, LEFT, UP = 0.0, math.pi, -math.pi / 2


class TestRayGeometry:
    def test_the_nearest_polygon_along_the_ray_wins(self):
        # Two stands on the same line of sight is an ordinary booth layout, and
        # the near one is what somebody is looking at.
        assert first_zone_along_ray(0.1, 0.5, RIGHT, [FAR, NEAR]) == ("near", pytest.approx(0.4))
        # Order in the list must not decide it — `zone_for_point` resolves
        # overlaps by draw order and this deliberately does not.
        assert first_zone_along_ray(0.1, 0.5, RIGHT, [NEAR, FAR])[0] == "near"

    def test_a_ray_pointing_away_hits_nothing(self):
        assert first_zone_along_ray(0.1, 0.5, LEFT, [NEAR, FAR]) is None

    def test_a_ray_that_misses_hits_nothing(self):
        # Level with the top of both polygons rather than through them.
        assert first_zone_along_ray(0.1, 0.05, RIGHT, [NEAR, FAR]) is None

    def test_the_zone_you_stand_in_can_be_excluded(self):
        # A ray from inside a polygon always crosses it on the way out, so
        # without this every look lands on the floor the visitor is already on.
        assert first_zone_along_ray(0.5, 0.5, RIGHT, [AROUND])[0] == "around"
        assert first_zone_along_ray(0.5, 0.5, RIGHT, [AROUND], exclude="around") is None

    def test_a_degenerate_polygon_is_skipped_not_raised(self):
        # An operator can save a zone with two points; it is not a shape and
        # this is not the place to complain about it.
        line = {"id": "line", "polygon": [[0.5, 0.4], [0.6, 0.4]]}
        assert first_zone_along_ray(0.1, 0.5, RIGHT, [line]) is None

    def test_a_ray_parallel_to_an_edge_never_crosses_it(self):
        # Axis-aligned zones make this reachable by drawing, not by contrivance.
        assert ray_segment_distance(0.0, 0.4, 1.0, 0.0, 0.5, 0.4, 0.6, 0.4) == math.inf

    def test_a_segment_behind_the_origin_is_not_a_hit(self):
        assert ray_segment_distance(0.9, 0.5, 1.0, 0.0, 0.5, 0.4, 0.5, 0.6) == math.inf


def keypoints(spec: dict[int, tuple[float, float, float]]):
    """17 COCO slots, with only the named ones visible."""
    points = [[0.0, 0.0] for _ in range(17)]
    confidences = [0.0] * 17
    for index, (x, y, conf) in spec.items():
        points[index] = [x, y]
        confidences[index] = conf
    return points, confidences


#: Anatomical left shoulder on the image's right — somebody facing the camera.
FACING = {5: (60.0, 60.0, 0.9), 6: (40.0, 60.0, 0.9)}
#: The mirror image: facing away.
AWAY = {5: (40.0, 60.0, 0.9), 6: (60.0, 60.0, 0.9)}


class TestHeading:
    def test_a_face_on_visitor_faces_the_camera(self):
        # y grows downward and nearer things sit lower in frame, so facing the
        # camera is +y.
        heading, confidence = heading_from_keypoints(*keypoints({**FACING, 0: (50.0, 40.0, 0.9)}))
        assert math.degrees(heading) == pytest.approx(90.0)
        assert confidence == pytest.approx(0.81)

    def test_a_visitor_seen_from_behind_faces_away(self):
        heading, confidence = heading_from_keypoints(*keypoints({**AWAY, 3: (55.0, 40.0, 0.8)}))
        assert math.degrees(heading) == pytest.approx(-90.0)
        # Ears are a weaker corroboration than a face, and the number says so.
        assert confidence < 0.81

    def test_the_shoulder_line_carries_left_and_right(self):
        # Rotate the shoulders and the heading rotates with them — this is the
        # half of the answer the head keypoints do not supply.
        turned = {5: (60.0, 50.0, 0.9), 6: (50.0, 70.0, 0.9), 0: (60.0, 40.0, 0.9)}
        heading, _ = heading_from_keypoints(*keypoints(turned))
        assert 0.0 < math.degrees(heading) < 90.0

    def test_it_declines_when_the_two_readings_disagree(self):
        # A visible face says front; shoulders in the back-on order say the
        # opposite. The model has almost certainly mislabelled which shoulder is
        # which — its weakest case is a back view — and guessing would put the
        # visitor looking at the opposite wall.
        assert heading_from_keypoints(*keypoints({**AWAY, 0: (50.0, 40.0, 0.9)})) is None

    def test_it_declines_without_both_shoulders(self):
        assert heading_from_keypoints(*keypoints({5: (60.0, 60.0, 0.9), 0: (50.0, 40.0, 0.9)})) is None

    def test_it_declines_with_no_head_keypoints(self):
        # The axis is known and the direction along it is not.
        assert heading_from_keypoints(*keypoints(FACING)) is None

    def test_it_declines_when_the_shoulders_coincide(self):
        # Edge-on to the camera: the normal is meaningless, not imprecise.
        edge_on = {5: (50.0, 60.0, 0.9), 6: (50.0, 60.0, 0.9), 0: (50.0, 40.0, 0.9)}
        assert heading_from_keypoints(*keypoints(edge_on)) is None

    def test_it_declines_on_a_low_confidence_shoulder(self):
        faint = {**FACING, 5: (60.0, 60.0, 0.2), 0: (50.0, 40.0, 0.9)}
        assert heading_from_keypoints(*keypoints(faint)) is None
