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


# -- overlays: mouth while speaking, bars while listening, dots while thinking -------------


def _lit_below(surface: pygame.Surface, y_from: int) -> int:
    """Non-background pixels in the strip below ``y_from`` (the overlay area)."""
    arr = pygame.surfarray.pixels3d(surface)
    return int((arr[:, y_from:, :].sum(axis=2) > 0).sum())


def _settled(anim: FaceAnimator, steps: int = 30, dt: float = 0.02):
    for _ in range(steps):
        face = anim.update(dt)
    return face


def test_overlay_modes_fade_in_and_out_and_lift_eyes():
    cfg = FaceConfig(idle_drift=False, saccade_interval_s=(100, 100), blink_interval_s=(100, 100))
    anim = FaceAnimator(cfg, rng=random.Random(0))
    assert not anim.overlay().visible
    rest = _settled(anim)
    anim.set_mode("speaking")
    anim.update(0.02)
    ov = anim.overlay()
    assert 0.0 < ov.blend < 1.0  # fading in
    lifted = _settled(anim)
    assert anim.overlay().blend == pytest.approx(1.0)
    assert lifted.center_y < rest.center_y  # eyes move up to make room
    anim.set_mode("none")
    back = _settled(anim)
    assert not anim.overlay().visible
    assert back.center_y == pytest.approx(rest.center_y, abs=1e-6)
    with pytest.raises(KeyError):
        anim.set_mode("singing")


def test_mouth_only_while_speaking(surface_480):
    cfg = FaceConfig(idle_drift=False, saccade_interval_s=(100, 100), blink_interval_s=(100, 100))
    anim = FaceAnimator(cfg, rng=random.Random(0))
    r = FaceRenderer(cfg, PanelGeometry(480, 480))
    # y of the overlay strip on a 480 panel: centre 240 + 27 * (480/128*1.15) ~ 356; check below 330
    strip = 330
    _settled(anim)
    r.render(anim.update(0.02), surface_480, overlay=anim.overlay())
    assert _lit_below(surface_480, strip) == 0

    anim.set_mode("speaking")
    _settled(anim)
    opens = []
    for _ in range(40):
        face = anim.update(0.02)
        ov = anim.overlay()
        opens.append(ov.mouth_open)
        r.render(face, surface_480, overlay=ov)
        assert _lit_below(surface_480, strip) > 0
    assert max(opens) - min(opens) > 0.3  # it moves
    # a real TTS amplitude overrides the synthetic envelope
    anim.speech_level = 1.0
    anim.update(0.02)
    assert anim.overlay().mouth_open == 1.0
    anim.set_mode("none")
    _settled(anim)
    r.render(anim.update(0.02), surface_480, overlay=anim.overlay())
    assert _lit_below(surface_480, strip) == 0


def test_listening_and_thinking_indicators_render_and_differ(surface_480):
    cfg = FaceConfig(idle_drift=False, saccade_interval_s=(100, 100), blink_interval_s=(100, 100))
    anim = FaceAnimator(cfg, rng=random.Random(0))
    r = FaceRenderer(cfg, PanelGeometry(480, 480))
    strip = 330
    anim.set_mode("listening")
    face = _settled(anim)
    ov = anim.overlay()
    assert len(ov.levels) == 5
    r.render(face, surface_480, overlay=ov)
    bars = pygame.surfarray.array3d(surface_480)[:, strip:, :].copy()
    assert bars.sum() > 0

    anim.set_mode("thinking")
    face = _settled(anim)
    ov = anim.overlay()
    r.render(face, surface_480, overlay=ov)
    dots = pygame.surfarray.array3d(surface_480)[:, strip:, :].copy()
    assert dots.sum() > 0
    assert (bars != dots).any()
    # the dots cycle: brightness pattern changes over time
    anim.update(0.4)
    r.render(anim.update(0.02), surface_480, overlay=anim.overlay())
    later = pygame.surfarray.array3d(surface_480)[:, strip:, :].copy()
    assert (later != dots).any()

    # config switches
    quiet = FaceRenderer(FaceConfig(indicators=False, mouth=False), PanelGeometry(480, 480))
    quiet.render(face, surface_480, overlay=anim.overlay())
    assert _lit_below(surface_480, strip) == 0
    anim.set_mode("speaking")
    quiet.render(_settled(anim), surface_480, overlay=anim.overlay())
    assert _lit_below(surface_480, strip) == 0


def test_overlays_on_mono_and_round_panels():
    from robot.face.animator import MODES

    pygame.init()
    for panel in (PanelGeometry(128, 64, color="mono1"), PanelGeometry(240, 240, shape="round")):
        surf = pygame.Surface((panel.width, panel.height))
        r = FaceRenderer(FaceConfig(), panel)
        anim = FaceAnimator(FaceConfig(), rng=random.Random(0))
        for mode in MODES:
            anim.set_mode(mode)
            r.render(_settled(anim), surf, overlay=anim.overlay())
            if panel.is_mono:
                arr = pygame.surfarray.pixels3d(surf)
                assert set(map(int, arr.reshape(-1))) <= {0, 255}


def test_face_service_maps_bus_topics_to_modes(config, hub):
    from robot.core.messages import FaceMode, OrchestratorState, VoiceSpeaking
    from robot.face.service import FaceService
    from robot.hal.display.null import NullDisplay

    svc = FaceService(config, hub.client("face"), display=NullDisplay(128, 128))
    svc.bus.subscribe(*svc.subscriptions)
    svc.setup()
    probe = hub.client("probe")

    def pump() -> None:
        while (env := svc.bus.recv(0)) is not None:
            svc.on_message(env)

    probe.publish_payload(OrchestratorState(state="listening"))
    pump()
    assert svc.animator.mode == "listening"
    assert svc.animator.expression == "listening"
    probe.publish_payload(OrchestratorState(state="speaking"))
    probe.publish_payload(VoiceSpeaking(state="end"))
    pump()
    assert svc.animator.mode == "none"
    probe.publish_payload(VoiceSpeaking(state="start"))
    pump()
    assert svc.animator.mode == "speaking"
    probe.publish_payload(OrchestratorState(state="attending"))
    pump()
    assert svc.animator.mode == "none"
    probe.publish_payload(FaceMode(mode="thinking"))
    pump()
    assert svc.animator.mode == "thinking"
    svc.tick(0.02)
    svc.teardown()
