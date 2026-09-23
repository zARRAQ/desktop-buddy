"""Rule-based fallback brain. Runs when no language model is reachable, and in the simulator."""

from __future__ import annotations

import datetime as dt
import random
import re

from robot.brain.prompt import Reply

JOKES = [
    "Why did the robot go on holiday? It needed to recharge.",
    "I would tell you a joke about my battery, but it has no charge.",
    "What do you call a robot who takes the long way round? R2 detour.",
]

NAME_PATTERNS = [
    re.compile(r"\bmy name is ([A-Z][a-z]+)", re.IGNORECASE),
    re.compile(r"\bi am ([A-Z][a-z]+)\b", re.IGNORECASE),
    re.compile(r"\bi'm ([A-Z][a-z]+)\b", re.IGNORECASE),
    re.compile(r"\bcall me ([A-Z][a-z]+)", re.IGNORECASE),
]
STOPWORDS = {
    "fine",
    "good",
    "here",
    "back",
    "sorry",
    "hungry",
    "tired",
    "busy",
    "not",
    "just",
    "so",
    "very",
    "really",
    "ok",
    "okay",
}


def extract_name(text: str) -> str | None:
    for pat in NAME_PATTERNS:
        m = pat.search(text)
        if m:
            name = m.group(1).strip().capitalize()
            if name.lower() not in STOPWORDS and len(name) > 1:
                return name
    return None


class ScriptedBrain:
    name = "scripted"

    def __init__(self, robot_name: str = "Buddy", *, rng: random.Random | None = None) -> None:
        self.robot_name = robot_name
        self.rng = rng or random.Random()

    def reply(self, text: str, person: str | None, facts: list[str]) -> Reply:
        t = text.strip().lower()
        if not t:
            return Reply("I didn't catch that.", "confusion")
        remember_match = re.search(r"\b(?:remember|note) that (.+)", t)
        if remember_match:
            fact = remember_match.group(1).strip().rstrip(".")
            return Reply("Got it, I'll remember that.", "happiness", "nod", [fact])
        name = extract_name(text)
        if name and person is None:
            return Reply(f"Nice to meet you, {name}!", "excitement", "nod", [])
        if re.search(r"\b(hello|hi|hey|good (morning|afternoon|evening))\b", t):
            who = f", {person}" if person else ""
            return Reply(f"Hello{who}! Good to see you.", "happiness", "nod")
        if "your name" in t or "who are you" in t:
            return Reply(f"I'm {self.robot_name}, your desk robot.", "happiness")
        if "who am i" in t or "my name" in t:
            if person:
                return Reply(f"You're {person}, of course.", "happiness", "nod")
            return Reply("I don't know your name yet. What is it?", "confusion")
        if "what do you know about me" in t or "what do you remember" in t:
            if facts:
                return Reply("I remember: " + "; ".join(facts[:3]) + ".", "thinking")
            return Reply("Not much yet. Tell me something to remember.", "confusion")
        if "time" in t and ("what" in t or "tell" in t):
            now = dt.datetime.now()
            return Reply(f"It's {now.strftime('%H:%M')}.", "neutral")
        if "date" in t or "day is it" in t:
            return Reply(f"It's {dt.datetime.now().strftime('%A, %d %B')}.", "neutral")
        if "joke" in t:
            return Reply(self.rng.choice(JOKES), "excitement")
        if "how are you" in t:
            return Reply("Running smoothly, thanks. How are you?", "happiness")
        if re.search(r"\b(thanks|thank you)\b", t):
            return Reply("Any time.", "happiness", "nod")
        if re.search(r"\b(bye|goodbye|see you)\b", t):
            return Reply("Bye! I'll be here.", "sadness", "nod")
        if "sleep" in t or "good night" in t:
            return Reply("Going to rest. Wake me when you need me.", "tiredness")
        if "?" in text:
            return Reply("I'm not sure, but I like the question.", "thinking")
        return Reply("I see. Tell me more.", "listening")
