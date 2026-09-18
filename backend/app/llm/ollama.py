"""Ollama on the host machine (/api/chat, format=json, temperature 0)."""
import time

import httpx

from .base import LLMError, LLMResponse


class OllamaLLM:
    name = "ollama"

    def __init__(self, base_url: str, model: str, num_gpu: int | None = None):
        self.base_url, self.model, self.num_gpu = base_url.rstrip("/"), model, num_gpu

    def reachable(self, timeout: float = 2.0) -> bool:
        try:
            r = httpx.get(f"{self.base_url}/api/tags", timeout=timeout)
            return r.status_code == 200 and any(m.get("name", "").startswith(self.model.split(":")[0])
                                                for m in r.json().get("models", []))
        except Exception:                                   # noqa: BLE001
            return False

    def complete_json(self, system: str, user: str, timeout: float = 60.0) -> LLMResponse:
        t0 = time.perf_counter()
        try:
            r = httpx.post(f"{self.base_url}/api/chat", json={
                "model": self.model, "stream": False, "format": "json",
                "options": {"temperature": 0, "num_predict": 300, **({"num_gpu": self.num_gpu} if self.num_gpu is not None and self.num_gpu >= 0 else {})},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            }, timeout=timeout)
        except httpx.HTTPError as e:
            raise LLMError(f"ollama unreachable: {e}") from e
        if r.status_code >= 400:
            raise LLMError(f"ollama {r.status_code}: {r.text[:200]}")
        body = r.json()
        return LLMResponse(text=body.get("message", {}).get("content", ""), provider=self.name, model=self.model,
                           latency_ms=int((time.perf_counter() - t0) * 1000),
                           input_tokens=body.get("prompt_eval_count"), output_tokens=body.get("eval_count"), raw=body)
