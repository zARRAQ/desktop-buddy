"""Procedural eye geometry.

The parameter model is a port of the procedural face from PyCozmo
(https://github.com/zayfod/pycozmo, MIT, which in turn follows Anki Cozmo's eye
parameterisation): each eye has a centre, scale, angle, four elliptical corner radii and
two lids, each lid having a closure fraction, an angle and a bend. Everything is a float, so
any two faces can be interpolated, which is what makes transitions and blinks cheap.

Unlike PyCozmo, which rasterises 1-bit images with Pillow, this module produces
polygons in a resolution-independent design space (the classic 128 x 64 Cozmo face), and
:mod:`robot.face.renderer` maps them onto whatever panel is attached.

Coordinates: x to the right, y down, origin at the face centre. Angles are degrees,
positive is counter-clockwise on screen.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

# Design space (PyCozmo / Cozmo units)
DESIGN_WIDTH = 128.0
DESIGN_HEIGHT = 64.0
EYE_WIDTH = 28.0
EYE_HEIGHT = 40.0
X_FACTOR = 0.55  # centre offsets are scaled by these, as in Cozmo
Y_FACTOR = 0.25
EYE_OFFSET_X = DESIGN_WIDTH / 5.0  # each eye sits this far from the face centre
CORNER_RADIUS = DESIGN_WIDTH / 20.0 + DESIGN_HEIGHT / 10.0  # 12.8
LID_HALF_WIDTH = 1.2 * EYE_WIDTH
LID_BEND_HALF_WIDTH = 1.2 * (EYE_WIDTH / 2.0)
ARC_STEPS = 8

Point = tuple[float, float]
Polygon = list[Point]


@dataclass
class LidParams:
    y: float = 0.0  # 0 open .. 1 fully closed
    angle: float = 0.0  # degrees, counter-clockwise on screen
    bend: float = 0.0  # 0 straight edge .. 1 full half-ellipse bulge


@dataclass
class EyeParams:
    center_x: float = 0.0
    center_y: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0
    angle: float = 0.0
    lower_inner_radius_x: float = 0.5
    lower_inner_radius_y: float = 0.5
    lower_outer_radius_x: float = 0.5
    lower_outer_radius_y: float = 0.5
    upper_inner_radius_x: float = 0.5
    upper_inner_radius_y: float = 0.5
    upper_outer_radius_x: float = 0.5
    upper_outer_radius_y: float = 0.5
    upper_lid: LidParams = field(default_factory=LidParams)
    lower_lid: LidParams = field(default_factory=LidParams)

    def to_vector(self) -> list[float]:
        return [
            self.center_x,
            self.center_y,
            self.scale_x,
            self.scale_y,
            self.angle,
            self.lower_inner_radius_x,
            self.lower_inner_radius_y,
            self.lower_outer_radius_x,
            self.lower_outer_radius_y,
            self.upper_inner_radius_x,
            self.upper_inner_radius_y,
            self.upper_outer_radius_x,
            self.upper_outer_radius_y,
            self.upper_lid.y,
            self.upper_lid.angle,
            self.upper_lid.bend,
            self.lower_lid.y,
            self.lower_lid.angle,
            self.lower_lid.bend,
        ]

    @classmethod
    def from_vector(cls, v: list[float]) -> EyeParams:
        if len(v) != 19:
            raise ValueError("eye vector must have 19 values")
        return cls(
            center_x=v[0],
            center_y=v[1],
            scale_x=v[2],
            scale_y=v[3],
            angle=v[4],
            lower_inner_radius_x=v[5],
            lower_inner_radius_y=v[6],
            lower_outer_radius_x=v[7],
            lower_outer_radius_y=v[8],
            upper_inner_radius_x=v[9],
            upper_inner_radius_y=v[10],
            upper_outer_radius_x=v[11],
            upper_outer_radius_y=v[12],
            upper_lid=LidParams(v[13], v[14], v[15]),
            lower_lid=LidParams(v[16], v[17], v[18]),
        )


@dataclass
class FaceParams:
    center_x: float = 0.0
    center_y: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0
    angle: float = 0.0
    left: EyeParams = field(default_factory=EyeParams)
    right: EyeParams = field(default_factory=EyeParams)

    def to_vector(self) -> list[float]:
        return [
            self.center_x,
            self.center_y,
            self.scale_x,
            self.scale_y,
            self.angle,
            *self.left.to_vector(),
            *self.right.to_vector(),
        ]

    @classmethod
    def from_vector(cls, v: list[float]) -> FaceParams:
        if len(v) != 43:
            raise ValueError("face vector must have 43 values")
        return cls(
            center_x=v[0],
            center_y=v[1],
            scale_x=v[2],
            scale_y=v[3],
            angle=v[4],
            left=EyeParams.from_vector(v[5:24]),
            right=EyeParams.from_vector(v[24:43]),
        )

    def copy(self) -> FaceParams:
        return FaceParams.from_vector(self.to_vector())

    @property
    def eyes(self) -> tuple[EyeParams, EyeParams]:
        return (self.left, self.right)


def lerp(a: FaceParams, b: FaceParams, t: float) -> FaceParams:
    """Linear interpolation, t in [0, 1]."""
    t = min(1.0, max(0.0, t))
    va, vb = a.to_vector(), b.to_vector()
    return FaceParams.from_vector([x + (y - x) * t for x, y in zip(va, vb, strict=True)])


def ease_in_out(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return t * t * (3.0 - 2.0 * t)


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


def _rotate(points: Polygon, angle_deg: float, cx: float, cy: float) -> Polygon:
    """Rotate counter-clockwise on screen (y down) about (cx, cy)."""
    if angle_deg == 0.0:
        return points
    a = math.radians(angle_deg)
    c, s = math.cos(a), math.sin(a)
    out: Polygon = []
    for x, y in points:
        dx, dy = x - cx, y - cy
        out.append((cx + dx * c + dy * s, cy - dx * s + dy * c))
    return out


def _affine(points: Polygon, sx: float, sy: float, tx: float, ty: float, cx: float = 0.0, cy: float = 0.0) -> Polygon:
    return [(cx + (x - cx) * sx + tx, cy + (y - cy) * sy + ty) for x, y in points]


def _arc(cx: float, cy: float, rx: float, ry: float, a0: float, a1: float) -> Polygon:
    """Elliptical arc from angle a0 to a1 (degrees, standard maths orientation, y down)."""
    if rx <= 0.0 or ry <= 0.0:
        # sharp corner: the arc collapses onto its centre
        return [(cx, cy)]
    pts: Polygon = []
    for i in range(ARC_STEPS + 1):
        a = math.radians(a0 + (a1 - a0) * i / ARC_STEPS)
        pts.append((cx + rx * math.cos(a), cy + ry * math.sin(a)))
    return pts


def eye_outline(eye: EyeParams, *, mirror: bool) -> Polygon:
    """Rounded rectangle with independent elliptical corners, in eye-local units.

    ``mirror`` is True for the right eye so that "inner" corners face the nose on both
    sides. The rectangle is EYE_WIDTH x EYE_HEIGHT centred on the origin.
    """
    hw, hh = EYE_WIDTH / 2.0, EYE_HEIGHT / 2.0
    c = CORNER_RADIUS

    def r(v: float, limit: float) -> float:
        return max(0.0, min(c * v, limit))

    # Corner radii (rx, ry), clamped so opposite corners never overlap.
    ui = (r(eye.upper_inner_radius_x, hw), r(eye.upper_inner_radius_y, hh))
    uo = (r(eye.upper_outer_radius_x, hw), r(eye.upper_outer_radius_y, hh))
    li = (r(eye.lower_inner_radius_x, hw), r(eye.lower_inner_radius_y, hh))
    lo = (r(eye.lower_outer_radius_x, hw), r(eye.lower_outer_radius_y, hh))

    # Assign corners to screen positions. Left eye: inner is on the right (+x).
    if mirror:
        top_left, top_right, bottom_right, bottom_left = ui, uo, lo, li
    else:
        top_left, top_right, bottom_right, bottom_left = uo, ui, li, lo

    pts: Polygon = []
    # Screen coordinates: y down. Angles in _arc are maths angles on a y-down plane, so
    # 180..270 sweeps the top-left corner from left-middle to top-middle.
    pts += _arc(-hw + top_left[0], -hh + top_left[1], top_left[0], top_left[1], 180, 270)
    pts += _arc(hw - top_right[0], -hh + top_right[1], top_right[0], top_right[1], 270, 360)
    pts += _arc(hw - bottom_right[0], hh - bottom_right[1], bottom_right[0], bottom_right[1], 0, 90)
    pts += _arc(-hw + bottom_left[0], hh - bottom_left[1], bottom_left[0], bottom_left[1], 90, 180)
    return pts


def lid_polygon(lid: LidParams, *, upper: bool) -> Polygon | None:
    """Occluder for one lid in eye-local units, or None when the lid is fully open.

    Mirrors PyCozmo: a band that descends ``y`` of the eye height from the eye's top edge,
    with a half-ellipse bulge of height ``(1 - y) * bend`` below its edge, rotated by
    ``angle`` about the top-centre of the eye. The lower lid is the same shape rotated a
    further 180 degrees about the bottom-centre.
    """
    if lid.y <= 0.0 and lid.bend <= 0.0:
        return None
    y = min(1.0, max(0.0, lid.y))
    hh = EYE_HEIGHT / 2.0
    pivot_y = -hh if upper else hh
    lid_height = EYE_HEIGHT * y
    band_top = -EYE_HEIGHT  # relative to pivot
    band_bottom = lid_height
    bend_h = EYE_HEIGHT * (1.0 - y) * max(0.0, min(1.0, lid.bend))

    poly: Polygon = [
        (-LID_HALF_WIDTH, band_top),
        (LID_HALF_WIDTH, band_top),
        (LID_HALF_WIDTH, band_bottom),
    ]
    if bend_h > 0.0:
        poly += _arc(0.0, band_bottom, LID_BEND_HALF_WIDTH, bend_h, 0, 180)[::-1]
    poly.append((-LID_HALF_WIDTH, band_bottom))

    # place relative to pivot, then rotate
    poly = [(x, y_ + pivot_y) for x, y_ in poly]
    angle = lid.angle + (0.0 if upper else 180.0)
    return _rotate(poly, angle, 0.0, pivot_y)


@dataclass
class EyeShapes:
    fill: Polygon
    occluders: list[Polygon]


def eye_shapes(eye: EyeParams, *, mirror: bool, eye_offset_x: float) -> EyeShapes:
    """Polygons for one eye in face-local design units, after eye rotation/scale/offset."""
    fill = eye_outline(eye, mirror=mirror)
    occluders = [
        p
        for p in (
            lid_polygon(eye.upper_lid, upper=True),
            lid_polygon(eye.lower_lid, upper=False),
        )
        if p is not None
    ]
    # PyCozmo rotates the eye image, then scales it in screen axes, then translates.
    tx = eye.center_x * X_FACTOR + eye_offset_x
    ty = eye.center_y * Y_FACTOR
    fill = _affine(_rotate(fill, eye.angle, 0.0, 0.0), eye.scale_x, eye.scale_y, tx, ty)
    occluders = [_affine(_rotate(p, eye.angle, 0.0, 0.0), eye.scale_x, eye.scale_y, tx, ty) for p in occluders]
    return EyeShapes(fill=fill, occluders=occluders)


@dataclass
class FaceShapes:
    """All polygons in design units centred on (0, 0)."""

    left: EyeShapes
    right: EyeShapes


def face_shapes(face: FaceParams, *, eye_separation: float = 1.0) -> FaceShapes:
    offset = EYE_OFFSET_X * eye_separation
    left = eye_shapes(face.left, mirror=False, eye_offset_x=-offset)
    right = eye_shapes(face.right, mirror=True, eye_offset_x=offset)

    def whole(points: Polygon) -> Polygon:
        pts = _rotate(points, face.angle, 0.0, 0.0)
        return _affine(pts, face.scale_x, face.scale_y, face.center_x * X_FACTOR, face.center_y * Y_FACTOR)

    return FaceShapes(
        left=EyeShapes(whole(left.fill), [whole(p) for p in left.occluders]),
        right=EyeShapes(whole(right.fill), [whole(p) for p in right.occluders]),
    )


def closed_eyes(face: FaceParams, amount: float) -> FaceParams:
    """Blink helper: squash both eyes vertically by ``amount`` in [0, 1]."""
    amount = min(1.0, max(0.0, amount))
    sy = 1.0 - 0.95 * amount
    sx = 1.0 + 0.12 * amount
    return replace(
        face,
        left=replace(face.left, scale_y=face.left.scale_y * sy, scale_x=face.left.scale_x * sx),
        right=replace(face.right, scale_y=face.right.scale_y * sy, scale_x=face.right.scale_x * sx),
    )
