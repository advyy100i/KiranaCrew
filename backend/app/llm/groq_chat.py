"""Groq chat completions (OpenAI-compatible), JSON mode."""
import time

import httpx

from .base import LLMError, LLMResponse

URL = "https://api.groq.com/openai/v1/chat/completions"


class GroqLLM:
    name = "groq"

    def __init__(self, api_key: str, model: str):
        if not api_key:
            raise LLMError("GROQ_API_KEY not set")
        self.api_key, self.model = api_key, model

    def complete_json(self, system: str, user: str, timeout: float = 30.0) -> LLMResponse:
        t0 = time.perf_counter()
        for attempt in range(4):
            try:
                r = httpx.post(URL, headers={"Authorization": f"Bearer {self.api_key}"}, json={
                    "model": self.model, "temperature": 0, "max_tokens": 300,
                    "response_format": {"type": "json_object"},
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                }, timeout=timeout)
            except httpx.HTTPError as e:
                raise LLMError(f"groq unreachable: {e}") from e
            if r.status_code in (429, 503) and attempt < 3:              # free tier: back off, never spin
                time.sleep(min(float(r.headers.get("retry-after", 2 ** attempt)), 20))
                continue
            break
        if r.status_code >= 400:
            raise LLMError(f"groq {r.status_code}: {r.text[:200]}")
        body = r.json()
        usage = body.get("usage", {})
        return LLMResponse(text=body["choices"][0]["message"]["content"], provider=self.name, model=self.model,
                           latency_ms=int((time.perf_counter() - t0) * 1000),
                           input_tokens=usage.get("prompt_tokens"), output_tokens=usage.get("completion_tokens"), raw=body)
