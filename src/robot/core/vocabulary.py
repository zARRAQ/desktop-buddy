"""Shared vocabulary: expression names the face can render and the aliases other services use.

Lives in core because the brain, the orchestrator and the face all speak it, and services
must not import each other.
"""

from __future__ import annotations

EXPRESSIONS: tuple[str, ...] = (
    "neutral",
    "anger",
    "sadness",
    "happiness",
    "surprise",
    "disgust",
    "fear",
    "pleading",
    "vulnerability",
    "despair",
    "guilt",
    "disappointment",
    "embarrassment",
    "horror",
    "skepticism",
    "annoyance",
    "fury",
    "suspicion",
    "rejection",
    "boredom",
    "tiredness",
    "asleep",
    "confusion",
    "amazement",
    "excitement",
    "listening",
    "thinking",
)

ALIASES: dict[str, str] = {
    "happy": "happiness",
    "sad": "sadness",
    "angry": "anger",
    "surprised": "surprise",
    "scared": "fear",
    "afraid": "fear",
    "sleepy": "tiredness",
    "tired": "tiredness",
    "sleep": "asleep",
    "bored": "boredom",
    "confused": "confusion",
    "curious": "confusion",
    "excited": "excitement",
    "amazed": "amazement",
    "annoyed": "annoyance",
    "suspicious": "suspicion",
    "skeptical": "skepticism",
    "guilty": "guilt",
    "embarrassed": "embarrassment",
    "disappointed": "disappointment",
    "calm": "neutral",
    "idle": "neutral",
    "attentive": "listening",
    "speaking": "happiness",
}


def resolve_expression(name: str) -> str:
    """Canonical expression name, or KeyError."""
    key = name.strip().lower()
    key = ALIASES.get(key, key)
    if key not in EXPRESSIONS:
        raise KeyError(f"unknown expression {name!r}")
    return key
