"""Expression presets.

Ported from PyCozmo's ``expressions.py`` (MIT), itself based on Catherine Chambers'
"Expressive Eyes" work. Values are kept identical so anyone familiar with Cozmo's face
gets the same vocabulary. Aliases at the bottom are the names the orchestrator uses.
"""

from __future__ import annotations

from collections.abc import Callable

from robot.core.vocabulary import ALIASES, EXPRESSIONS, resolve_expression
from robot.face.procedural import EyeParams, FaceParams

Preset = Callable[[], FaceParams]


def _base() -> FaceParams:
    f = FaceParams()
    for e in f.eyes:
        e.scale_x = 0.8
        e.scale_y = 0.8
    return f


def neutral() -> FaceParams:
    return _base()


def anger() -> FaceParams:
    f = _base()
    f.left.upper_lid.y, f.left.upper_lid.angle = 0.6, -30.0
    f.right.upper_lid.y, f.right.upper_lid.angle = 0.6, 30.0
    return f


def sadness() -> FaceParams:
    f = _base()
    f.left.upper_lid.y, f.left.upper_lid.angle = 0.6, 20.0
    f.right.upper_lid.y, f.right.upper_lid.angle = 0.6, -20.0
    return f


def happiness() -> FaceParams:
    f = _base()
    for e in f.eyes:
        e.upper_outer_radius_x = 1.0
        e.upper_inner_radius_x = 1.0
        e.lower_lid.y = 0.4
        e.lower_lid.bend = 0.4
    return f


def surprise() -> FaceParams:
    f = _base()
    for e in f.eyes:
        e.scale_x = 1.25
        e.scale_y = 1.25
    return f


def disgust() -> FaceParams:
    f = _base()
    f.left.upper_lid.y, f.left.upper_lid.angle = 0.3, 10.0
    f.left.lower_lid.y = 0.3
    f.right.upper_lid.y, f.right.upper_lid.angle = 0.2, 20.0
    f.right.lower_lid.y, f.right.lower_lid.angle = 0.2, 10.0
    return f


def fear() -> FaceParams:
    f = _base()
    f.left.upper_lid.angle, f.left.upper_lid.bend = 30.0, 0.1
    f.left.lower_lid.y, f.left.lower_lid.angle = 0.4, 10.0
    f.right.upper_lid.angle, f.right.upper_lid.bend = -30.0, 0.1
    f.right.lower_lid.y, f.right.lower_lid.angle = 0.4, -10.0
    return f


def pleading() -> FaceParams:
    f = _base()
    f.left.upper_lid.angle, f.left.lower_lid.y = 30.0, 0.5
    f.right.upper_lid.angle, f.right.lower_lid.y = -30.0, 0.5
    return f


def vulnerability() -> FaceParams:
    f = _base()
    f.left.upper_lid.angle, f.left.upper_lid.y = 20.0, 0.3
    f.left.lower_lid.angle, f.left.lower_lid.y = 10.0, 0.5
    f.right.upper_lid.angle, f.right.upper_lid.y = -20.0, 0.3
    f.right.lower_lid.angle, f.right.lower_lid.y = -10.0, 0.5
    return f


def despair() -> FaceParams:
    f = _base()
    f.left.upper_lid.angle, f.left.upper_lid.y = 30.0, 0.6
    f.right.upper_lid.angle, f.right.upper_lid.y = -30.0, 0.6
    return f


def guilt() -> FaceParams:
    f = _base()
    f.left.upper_lid.angle, f.left.upper_lid.y, f.left.upper_lid.bend = 10.0, 0.6, 0.3
    f.right.upper_lid.angle, f.right.upper_lid.y, f.right.upper_lid.bend = -10.0, 0.6, 0.3
    return f


def disappointment() -> FaceParams:
    f = _base()
    f.left.upper_lid.angle, f.left.upper_lid.y, f.left.lower_lid.y = -10.0, 0.3, 0.4
    f.right.upper_lid.angle, f.right.upper_lid.y, f.right.lower_lid.y = 10.0, 0.3, 0.4
    return f


def embarrassment() -> FaceParams:
    f = _base()
    f.left.upper_lid.angle, f.left.upper_lid.y, f.left.upper_lid.bend = 10.0, 0.5, 0.1
    f.left.lower_lid.y = 0.1
    f.right.upper_lid.angle, f.right.upper_lid.y, f.right.upper_lid.bend = -10.0, 0.5, 0.1
    f.right.lower_lid.y = 0.1
    return f


def horror() -> FaceParams:
    f = _base()
    f.left.upper_lid.angle = 20.0
    f.right.upper_lid.angle = -20.0
    return f


def skepticism() -> FaceParams:
    f = _base()
    f.left.upper_lid.angle, f.left.upper_lid.y = -10.0, 0.4
    f.right.upper_lid.angle, f.right.upper_lid.y = 25.0, 0.15
    return f


def annoyance() -> FaceParams:
    f = _base()
    f.left.upper_lid.angle = -30.0
    f.left.lower_lid.angle, f.left.lower_lid.y = -10.0, 0.3
    f.right.upper_lid.angle, f.right.upper_lid.y = 30.0, 0.2
    f.right.lower_lid.angle, f.right.lower_lid.y = 5.0, 0.4
    f.right.upper_inner_radius_x = 1.0
    f.right.upper_outer_radius_x = 1.0
    return f


def fury() -> FaceParams:
    f = _base()
    f.left.upper_lid.angle, f.left.upper_lid.y, f.left.lower_lid.y = -30.0, 0.3, 0.4
    f.right.upper_lid.angle, f.right.upper_lid.y, f.right.lower_lid.y = 30.0, 0.3, 0.4
    return f


def suspicion() -> FaceParams:
    f = _base()
    f.left.upper_lid.angle, f.left.upper_lid.y, f.left.lower_lid.y = -10.0, 0.4, 0.5
    f.right.upper_lid.angle, f.right.upper_lid.y, f.right.lower_lid.y = 10.0, 0.4, 0.5
    return f


def rejection() -> FaceParams:
    f = _base()
    for e in f.eyes:
        e.upper_lid.angle, e.upper_lid.y = 25.0, 0.8
    return f


def boredom() -> FaceParams:
    f = _base()
    for e in f.eyes:
        e.upper_lid.y = 0.4
    return f


def tiredness() -> FaceParams:
    f = _base()
    f.left.upper_lid.angle, f.left.upper_lid.y, f.left.lower_lid.y = 5.0, 0.4, 0.5
    f.right.upper_lid.angle, f.right.upper_lid.y, f.right.lower_lid.y = -5.0, 0.4, 0.5
    return f


def asleep() -> FaceParams:
    f = _base()
    for e in f.eyes:
        e.center_y = 50.0
        e.upper_lid.y = 0.45
        e.lower_lid.y = 0.5
    return f


def confusion() -> FaceParams:
    f = _base()
    f.left.lower_lid.y, f.left.lower_lid.bend = 0.2, 0.2
    f.right.upper_lid.angle, f.right.upper_lid.y = -10.0, 0.3
    f.right.lower_lid.angle, f.right.lower_lid.y, f.right.lower_lid.bend = 5.0, 0.2, 0.2
    return f


def amazement() -> FaceParams:
    f = _base()
    for e in f.eyes:
        e.lower_lid.y = 0.2
    return f


def excitement() -> FaceParams:
    f = _base()
    for e in f.eyes:
        e.lower_lid.y, e.lower_lid.bend = 0.3, 0.2
    return f


def listening() -> FaceParams:
    """Attentive: slightly wider eyes, a hint of the lower lid. Not from PyCozmo."""
    f = _base()
    for e in f.eyes:
        e.scale_x = 0.9
        e.scale_y = 0.95
        e.lower_lid.y = 0.1
    return f


def thinking() -> FaceParams:
    """Eyes up and to one side. Not from PyCozmo."""
    f = _base()
    for e in f.eyes:
        e.center_x = 18.0
        e.center_y = -35.0
        e.upper_lid.y = 0.2
    return f


PRESETS: dict[str, Preset] = {
    "neutral": neutral,
    "anger": anger,
    "sadness": sadness,
    "happiness": happiness,
    "surprise": surprise,
    "disgust": disgust,
    "fear": fear,
    "pleading": pleading,
    "vulnerability": vulnerability,
    "despair": despair,
    "guilt": guilt,
    "disappointment": disappointment,
    "embarrassment": embarrassment,
    "horror": horror,
    "skepticism": skepticism,
    "annoyance": annoyance,
    "fury": fury,
    "suspicion": suspicion,
    "rejection": rejection,
    "boredom": boredom,
    "tiredness": tiredness,
    "asleep": asleep,
    "confusion": confusion,
    "amazement": amazement,
    "excitement": excitement,
    "listening": listening,
    "thinking": thinking,
}

assert set(PRESETS) == set(EXPRESSIONS), "face presets and core vocabulary disagree"

# Number keys in the simulator
NUMBER_KEYS: dict[int, str] = {
    1: "neutral",
    2: "happiness",
    3: "sadness",
    4: "anger",
    5: "surprise",
    6: "fear",
    7: "confusion",
    8: "tiredness",
    9: "excitement",
}


def resolve(name: str) -> str:
    return resolve_expression(name)


def aliases() -> dict[str, str]:
    return dict(ALIASES)


def get(name: str) -> FaceParams:
    return PRESETS[resolve(name)]()


def names() -> list[str]:
    return sorted(PRESETS)


def _all_eyes(f: FaceParams) -> tuple[EyeParams, EyeParams]:  # pragma: no cover - re-export
    return f.eyes
