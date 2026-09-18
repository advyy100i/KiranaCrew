"""Gemini generateContent with JSON response mime type."""
import time

import httpx

from .base import LLMError, LLMResponse


class GeminiLLM:
    name = "gemini"

    def __init__(self, api_key: str, model: str):
        if not api_key:
            raise LLMError("GEMINI_API_KEY not set")
        self.api_key, self.model = api_key, model

    def complete_json(self, system: str, user: str, timeout: float = 30.0) -> LLMResponse:
        t0 = time.perf_counter()
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        try:
            r = httpx.post(url, params={"key": self.api_key}, json={
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"temperature": 0, "maxOutputTokens": 300, "responseMimeType": "application/json"},
            }, timeout=timeout)
        except httpx.HTTPError as e:
            raise LLMError(f"gemini unreachable: {e}") from e
        if r.status_code >= 400:
            raise LLMError(f"gemini {r.status_code}: {r.text[:200]}")
        body = r.json()
        try:
            text = body["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as e:
            raise LLMError(f"gemini: no candidates ({body.get('promptFeedback')})") from e
        usage = body.get("usageMetadata", {})
        return LLMResponse(text=text, provider=self.name, model=self.model,
                           latency_ms=int((time.perf_counter() - t0) * 1000),
                           input_tokens=usage.get("promptTokenCount"), output_tokens=usage.get("candidatesTokenCount"),
                           raw=body)
