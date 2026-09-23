"""Minimal OpenAI-compatible chat client.

Works unchanged against llama.cpp's ``llama-server`` (``/v1``), Ollama (``/v1``) and Hailo's
``hailo-ollama`` on the AI HAT+ 2, which all speak this dialect. Non-streaming: the robot
speaks whole sentences, and small models finish a two-sentence reply in a few seconds.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger(__name__)


@dataclass
class ChatResult:
    text: str
    latency_s: float
    model: str = ""
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class ChatClient:
    def __init__(
        self, endpoint: str, model: str = "default", api_key: str = "not-needed", timeout_s: float = 30.0
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout_s, connect=2.0), headers={"Authorization": f"Bearer {api_key}"}
        )
        self._json_mode_ok = True

    def healthy(self) -> bool:
        try:
            r = self._client.get(f"{self.endpoint}/models", timeout=2.0)
            return r.status_code < 500
        except httpx.HTTPError:
            return False

    def models(self) -> list[str]:
        try:
            r = self._client.get(f"{self.endpoint}/models", timeout=3.0)
            r.raise_for_status()
            data = r.json()
            return [str(m.get("id", "")) for m in data.get("data", [])]
        except (httpx.HTTPError, ValueError):
            return []

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 120,
        temperature: float = 0.7,
        json_mode: bool = True,
        extra: dict[str, Any] | None = None,
    ) -> ChatResult:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if json_mode and self._json_mode_ok:
            body["response_format"] = {"type": "json_object"}
        if extra:
            body.update(extra)
        t0 = time.monotonic()
        r = self._client.post(f"{self.endpoint}/chat/completions", json=body)
        if r.status_code == 400 and "response_format" in body:
            # servers without JSON mode: remember and retry without it
            log.info("server rejected response_format; disabling JSON mode")
            self._json_mode_ok = False
            body.pop("response_format")
            r = self._client.post(f"{self.endpoint}/chat/completions", json=body)
        r.raise_for_status()
        data = r.json()
        choice = data["choices"][0]
        content = choice.get("message", {}).get("content") or choice.get("text") or ""
        usage = data.get("usage") or {}
        return ChatResult(
            text=str(content),
            latency_s=time.monotonic() - t0,
            model=str(data.get("model", "")),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
        )

    def close(self) -> None:
        self._client.close()
