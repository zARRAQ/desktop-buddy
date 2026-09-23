"""System prompt construction and robust parsing of the model's structured reply."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from robot.core.vocabulary import resolve_expression

EXPRESSION_CHOICES = (
    "neutral, happiness, sadness, anger, surprise, fear, confusion, excitement, amazement, "
    "tiredness, boredom, skepticism, embarrassment, pleading, thinking"
)
GESTURES = ("nod", "shake", "none")

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


@dataclass
class Reply:
    say: str
    expression: str = "neutral"
    gesture: str | None = None
    remember: list[str] = field(default_factory=list)


def system_prompt(persona: str, person: str | None, facts: list[str]) -> str:
    who = (
        f"You are talking with {person}."
        if person
        else "You are talking with someone whose face you do not recognise yet."
    )
    facts_text = "\n".join(f"- {f}" for f in facts) if facts else "- nothing yet"
    return (
        f"{persona}\n\n{who}\n"
        f"Things you remember about them:\n{facts_text}\n\n"
        "Reply with ONE JSON object and nothing else, shaped exactly like this:\n"
        '{"say": "<what you say aloud, at most two short sentences>", '
        f'"expression": "<one of: {EXPRESSION_CHOICES}>", '
        '"gesture": "<nod | shake | none>", '
        '"remember": ["<a new fact about this person worth keeping, if any>"]}\n'
        "Keep it spoken-language, no markdown, no emoji."
    )


def parse_reply(text: str) -> Reply:
    """Accept JSON, fenced JSON, JSON with prose around it, or plain text."""
    cleaned = _THINK_RE.sub("", text).strip()
    candidate = cleaned
    m = _FENCE_RE.search(cleaned)
    if m:
        candidate = m.group(1).strip()
    obj = _try_json(candidate)
    if obj is None:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end > start:
            obj = _try_json(cleaned[start : end + 1])
    if not isinstance(obj, dict):
        return Reply(say=_plain(cleaned))
    say = obj.get("say") or obj.get("text") or obj.get("response") or ""
    if not isinstance(say, str) or not say.strip():
        say = _plain(cleaned) if "{" not in cleaned else "Hmm."
    expression = str(obj.get("expression") or "neutral")
    try:
        expression = resolve_expression(expression)
    except KeyError:
        expression = "neutral"
    gesture_raw = obj.get("gesture")
    gesture = str(gesture_raw).lower() if gesture_raw else None
    if gesture not in ("nod", "shake"):
        gesture = None
    remember_raw = obj.get("remember") or []
    if isinstance(remember_raw, str):
        remember_raw = [remember_raw]
    remember = [str(r).strip() for r in remember_raw if isinstance(r, str | int | float) and str(r).strip()]
    return Reply(say=say.strip(), expression=expression, gesture=gesture, remember=remember[:3])


def _try_json(text: str) -> object | None:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def _plain(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip().strip("`")
    if len(text) > 300:
        text = text[:297].rsplit(" ", 1)[0] + "..."
    return text or "Hmm."
