"""
Which way a person is facing, from a pose skeleton.

## Its own module, with no cv2

`realmspace.py` imports cv2 at the top, so nothing in it can be imported by the
backend venv — and this is the one piece of pure arithmetic in perception, the
piece most worth testing. `bus_client.py` is split out for exactly this reason
and `pytest.ini` records it: the backend venv is the one that always gets run.
`tests/test_gaze_geometry.py` imports this directly.

## What this is, and what it is not

A monocular camera gives a *facing direction*, not a gaze vector. There is no
depth here and no eye tracking; somebody looking up and somebody looking
straight ahead are the same measurement.

`privacy.md` sanctions exactly this much — pose keypoints kept "briefly" to
compute a gaze vector, face mesh "discarded after gaze vector is computed", face
embeddings "never computed" — and this function is the whole of what the
skeleton is used for. **The keypoints never leave the frame loop.** Two scalars
do: a heading and a confidence. The bus is append-only and exported, so anything
posted to it is permanent, and a skeleton is a great deal more identifying than
a box.
"""

from __future__ import annotations

import math

# COCO keypoint indices, in the order every ultralytics pose model returns them.
NOSE, L_EYE, R_EYE, L_EAR, R_EAR, L_SHOULDER, R_SHOULDER = 0, 1, 2, 3, 4, 5, 6

#: Below this a keypoint is a guess about a limb the model could not see.
KEYPOINT_MIN_CONF = 0.5


def heading_from_keypoints(keypoints, confidences) -> tuple[float, float] | None:
    """Which way a person faces, in the image plane, or None to say nothing.

    ## What this is, and what it is not

    A monocular camera gives a *facing direction*, not a gaze vector. There is
    no depth here and no eye tracking; somebody looking up and somebody looking
    straight ahead are the same measurement. `privacy.md` sanctions exactly this
    much — pose keypoints kept "briefly" to compute a gaze vector, with face
    mesh "discarded" and embeddings never computed — and this function is the
    whole of what the skeleton is used for. **The keypoints do not leave the
    frame loop.** Two scalars do.

    ## How

    The shoulder line gives the body's axis. A person faces along one of its two
    normals, and which one is genuinely ambiguous from the shoulders alone — the
    same skeleton fits somebody walking towards the camera and away from it. The
    head keypoints break the tie: a nose or an eye between the shoulders means
    the front is towards the camera, ears alone mean it is away.

    ## Why it refuses so often

    `detection_rate` drift was left unemitted for being confounded with the room
    emptying, and grouping refuses to call proximity company. Gaze has the same
    trap: a detector that answers on every frame reports every head turn as
    interest, which looks like coverage and is worse than nothing. So this
    returns None whenever the evidence does not settle it, and the caller emits
    nothing at all rather than a low-confidence guess.

    Returns `(heading_radians, confidence)` or None.
    """
    def visible(i: int) -> bool:
        return i < len(confidences) and float(confidences[i]) >= KEYPOINT_MIN_CONF

    # Both shoulders or nothing: one shoulder gives no axis to take a normal of.
    if not (visible(L_SHOULDER) and visible(R_SHOULDER)):
        return None

    lx, ly = float(keypoints[L_SHOULDER][0]), float(keypoints[L_SHOULDER][1])
    rx, ry = float(keypoints[R_SHOULDER][0]), float(keypoints[R_SHOULDER][1])

    sx, sy = lx - rx, ly - ry
    span = math.hypot(sx, sy)
    # Shoulders on top of each other: the person is edge-on to the camera and
    # the normal is numerically meaningless, not merely imprecise.
    if span < 1e-6:
        return None

    # Rotating the shoulder axis by 90° gives the facing direction. Which of
    # the two normals it is comes out of the shoulder *order*: ultralytics
    # labels shoulders anatomically, so a person facing the camera has their
    # left shoulder on the image's right, and one facing away has it on the
    # left. The ordering therefore already carries front-versus-back — an
    # extra flip on top of it would cancel the information out.
    nx, ny = -sy / span, sx / span

    # In image coordinates y grows downward, and things nearer the camera sit
    # lower in the frame, so a normal with ny > 0 means facing the camera.
    faces_camera_by_shoulders = ny > 0

    front = visible(NOSE) or visible(L_EYE) or visible(R_EYE)
    ears_only = (visible(L_EAR) or visible(R_EAR)) and not front
    if not front and not ears_only:
        # A torso with no head keypoints at all. The axis is known and the
        # direction along it is not, which is exactly the case to decline.
        return None

    # Two independent readings of the same fact: a visible face means the front
    # is towards the camera, ears alone mean it is away, and the shoulder order
    # says the same thing from different evidence. When they disagree the model
    # has almost certainly mislabelled which shoulder is which — its weakest
    # case is a back view — and the honest output is nothing. Guessing here is
    # how a visitor gets recorded looking at the opposite wall.
    if front != faces_camera_by_shoulders:
        return None

    heading = math.atan2(ny, nx)

    # Confidence is the evidence, not a constant. The shoulders establish the
    # axis and the head keypoints corroborate the direction, so both halves are
    # in the number — and a face-on read is worth more than an ears-only one,
    # because a face is a stronger corroboration than the back of a head.
    shoulder_conf = min(float(confidences[L_SHOULDER]), float(confidences[R_SHOULDER]))
    head_idx = [i for i in (NOSE, L_EYE, R_EYE, L_EAR, R_EAR) if visible(i)]
    head_conf = max(float(confidences[i]) for i in head_idx)
    confidence = shoulder_conf * head_conf * (1.0 if front else 0.7)

    return heading, round(confidence, 3)
