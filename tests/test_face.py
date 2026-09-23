from __future__ import annotations

import random

import pygame
import pytest

from robot.core.config import FaceConfig
from robot.face import expressions
from robot.face.animator import FaceAnimator
from robot.face.procedural import (
    EYE_HEIGHT,
    EYE_WIDTH,
    FaceParams,
    LidParams,
    closed_eyes,
    eye_outline,
    lerp,
    lid_polygon,
)
from robot.face.renderer import FaceRenderer, PanelGeometry, QualityController, parse_color


def lit_pixels(surface: pygame.Surface, color=(0x3E, 0xE0, 0xE6)) -> int:
    arr = pygame.surfarray.pixels3d(surface)
    mask = (arr[..., 0] == color[0]) & (arr[..., 1] == color[1]) & (arr[..., 2] == color[2])
    return int(mask.sum())


def bbox_of_lit(surface: pygame.Surface, color=(0x3E, 0xE0, 0xE6)):
    import numpy as np

    arr = pygame.surfarray.pixels3d(surface)
    mask = (arr[..., 0] == color[0]) & (arr[..., 1] == color[1]) & (arr[..., 2] == color[2])
    xs, ys = np.nonzero(mask)
    return xs.min(), xs.max(), ys.min(), ys.max()


def test_vector_round_trip_all_presets():
    for name in expressions.names():
        f = expressions.get(name)
        assert FaceParams.from_vector(f.to_vector()) == f
        assert len(f.to_vector()) == 43


def test_aliases_resolve_and_unknown_raises():
    assert expressions.resolve("happy") == "happiness"
    assert expressions.resolve("SLEEPY") == "tiredness"
    with pytest.raises(KeyError):
        expressions.resolve("smug")


def test_lerp_endpoints():
    a, b = expressions.get("neutral"), expressions.get("surprise")
    assert lerp(a, b, 0.0) == a
    assert lerp(a, b, 1.0) == b
    mid = lerp(a, b, 0.5)
    assert mid.left.scale_x == pytest.approx((0.8 + 1.25) / 2)


def test_eye_outline_is_within_eye_box_and_mirrors():
    left = eye_outline(FaceParams().left, mirror=False)
    right = eye_outline(FaceParams().right, mirror=True)
    for x, y in left + right:
        assert -EYE_WIDTH / 2 - 1e-6 <= x <= EYE_WIDTH / 2 + 1e-6
        assert -EYE_HEIGHT / 2 - 1e-6 <= y <= EYE_HEIGHT / 2 + 1e-6
    # asymmetric corner radius shows up on opposite sides for the two eyes
    e = FaceParams().left
    e.upper_inner_radius_x = 1.0
    e.upper_inner_radius_y = 1.0
    l_pts = eye_outline(e, mirror=False)
    r_pts = eye_outline(e, mirror=True)
    assert {(round(x, 4), round(y, 4)) for x, y in l_pts} == {(round(-x, 4), round(y, 4)) for x, y in r_pts}


def test_lid_open_is_none_and_closed_covers_eye():
    assert lid_polygon(LidParams(), upper=True) is None
    poly = lid_polygon(LidParams(y=1.0), upper=True)
    assert poly is not None
    ys = [y for _, y in poly]
    assert min(ys) <= -EYE_HEIGHT / 2 and max(ys) >= EYE_HEIGHT / 2 - 1e-6


def test_lid_angle_sign_convention():
    """Anger uses a negative angle on the left eye: the inner (right) end must drop."""
    poly = lid_polygon(LidParams(y=0.3, angle=-30.0), upper=True)
    assert poly is not None
    lowest = max(poly, key=lambda p: p[1])  # the lid edge's lowest point
    assert lowest[0] > 0  # is on the inner (right) side for the left eye
    poly_r = lid_polygon(LidParams(y=0.3, angle=30.0), upper=True)
    assert poly_r is not None
    assert max(poly_r, key=lambda p: p[1])[0] < 0


@pytest.fixture(scope="module")
def surface_480():
    pygame.init()
    return pygame.Surface((480, 480))


def test_render_neutral_is_symmetric_and_lit(surface_480):
    r = FaceRenderer(FaceConfig(), PanelGeometry(480, 480))
    r.render(expressions.get("neutral"), surface_480)
    lit = lit_pixels(surface_480)
    assert lit > 480 * 480 * 0.05
    x0, x1, y0, y1 = bbox_of_lit(surface_480)
    assert abs((x0 + x1) / 2 - 239.5) < 2  # centred horizontally
    assert abs((y0 + y1) / 2 - 239.5) < 2


def test_blink_reduces_lit_pixels(surface_480):
    r = FaceRenderer(FaceConfig(), PanelGeometry(480, 480))
    r.render(expressions.get("neutral"), surface_480)
    open_lit = lit_pixels(surface_480)
    r.render(closed_eyes(expressions.get("neutral"), 1.0), surface_480)
    closed_lit = lit_pixels(surface_480)
    assert closed_lit < open_lit * 0.25


def test_lids_occlude(surface_480):
    r = FaceRenderer(FaceConfig(), PanelGeometry(480, 480))
    r.render(expressions.get("neutral"), surface_480)
    base = lit_pixels(surface_480)
    r.render(expressions.get("tiredness"), surface_480)
    assert lit_pixels(surface_480) < base * 0.7
    r.render(expressions.get("happiness"), surface_480)
    assert lit_pixels(surface_480) < base


def test_every_preset_renders_without_error(surface_480):
    r = FaceRenderer(FaceConfig(), PanelGeometry(480, 480))
    for name in expressions.names():
        r.render(expressions.get(name), surface_480)
        assert lit_pixels(surface_480) > 0, name


def test_round_panel_masks_corners_and_pulls_eyes_in():
    pygame.init()
    s = pygame.Surface((240, 240))
    rect = FaceRenderer(FaceConfig(), PanelGeometry(240, 240, shape="rect"))
    rnd = FaceRenderer(FaceConfig(), PanelGeometry(240, 240, shape="round"))
    rect.render(expressions.get("neutral"), s)
    x0r, x1r, _, _ = bbox_of_lit(s)
    rnd.render(expressions.get("neutral"), s)
    x0, x1, _, _ = bbox_of_lit(s)
    assert (x1 - x0) < (x1r - x0r)
    assert s.get_at((0, 0))[:3] == (0, 0, 0)
    assert rnd.eye_separation == pytest.approx(0.88)


def test_mono_and_tiny_panels():
    pygame.init()
    s = pygame.Surface((128, 64))
    r = FaceRenderer(FaceConfig(), PanelGeometry(128, 64, color="mono1"))
    r.render(expressions.get("neutral"), s)
    arr = pygame.surfarray.pixels3d(s)
    # strictly two colours: no antialiasing on a 1-bit panel
    unique = {tuple(int(c) for c in px) for row in arr for px in row}
    assert unique <= {(0, 0, 0), (255, 255, 255)}
    assert (255, 255, 255) in unique


def test_stretch_and_fill_modes_change_transform():
    from robot.face.renderer import compute_transform

    fit = compute_transform(PanelGeometry(320, 240), 1.0)
    fill = compute_transform(PanelGeometry(320, 240, scale_mode="fill"), 1.0)
    stretch = compute_transform(PanelGeometry(320, 240, scale_mode="stretch"), 1.0)
    assert fit.scale_x == fit.scale_y == 240 / 128
    assert fill.scale_x == 320 / 128
    assert stretch.scale_x != stretch.scale_y


def test_parse_color():
    assert parse_color("#3EE0E6") == (0x3E, 0xE0, 0xE6)
    assert parse_color("fff") == (255, 255, 255)
    with pytest.raises(ValueError):
        parse_color("#12")


def test_animator_transitions_blinks_and_holds():
    cfg = FaceConfig(blink_interval_s=(0.5, 0.5), transition_ms=100, blink_duration_ms=100)
    anim = FaceAnimator(cfg, rng=random.Random(1))
    anim.set_expression("surprise")
    first = anim.update(0.01)
    assert first.left.scale_x < 1.25  # mid transition
    for _ in range(20):
        last = anim.update(0.01)
    assert last.left.scale_x == pytest.approx(1.25, abs=0.15)
    # hold then return
    anim.set_expression("anger", hold_ms=200)
    for _ in range(10):
        anim.update(0.01)
    assert anim.expression == "anger"
    for _ in range(40):
        anim.update(0.01)
    assert anim.expression == "surprise"
    # blinks happen on schedule
    for _ in range(300):
        anim.update(0.01)
    assert anim.blinks >= 3


def test_animator_look_moves_eyes():
    anim = FaceAnimator(FaceConfig(idle_drift=False, saccade_interval_s=(100, 100)), rng=random.Random(0))
    anim.look(1.0, 0.0)
    for _ in range(50):
        f = anim.update(0.02)
    assert f.center_x > 20
    anim.look(-1.0, 0.0)
    for _ in range(50):
        f = anim.update(0.02)
    assert f.center_x < -20


def test_quality_controller_degrades_and_recovers():
    q = QualityController(30, 15)
    t = 0.0
    # 4 seconds of slow frames -> at least one degradation
    for _ in range(120):
        t += 1 / 30
        q.record(0.08, now=t)
    assert q.level >= 1
    level = q.level
    for _ in range(30 * 25):
        t += 1 / 30
        q.record(0.005, now=t)
    assert q.level < level
