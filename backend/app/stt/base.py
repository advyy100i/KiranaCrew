"""STT provider interface. Every provider is swappable by env var; tests use the fixture provider."""
from typing import Protocol

from pydantic import BaseModel


class STTResult(BaseModel):
    text: str
    provider: str
    model: str = ""
    language: str | None = None
    avg_logprob: float | None = None
    no_speech_prob: float | None = None
    latency_ms: int = 0


class STTError(Exception):
    pass


class STTProvider(Protocol):
    name: str

    def transcribe(self, audio: bytes, filename: str, language: str = "hi") -> STTResult: ...


class ChainSTT:
    """Try providers in order; the first success wins. Records every attempt for llm_calls."""
    name = "chain"

    def __init__(self, providers: list):
        self.providers = providers
        self.attempts: list[dict] = []

    def transcribe(self, audio: bytes, filename: str, language: str = "hi") -> STTResult:
        self.attempts = []
        last: Exception | None = None
        for p in self.providers:
            try:
                r = p.transcribe(audio, filename, language)
                self.attempts.append({"provider": p.name, "model": r.model, "ok": True, "latency_ms": r.latency_ms})
                return r
            except Exception as e:                        # noqa: BLE001 - any provider failure falls through
                self.attempts.append({"provider": p.name, "model": getattr(p, "model", ""), "ok": False,
                                      "error": str(e)[:500]})
                last = e
        raise STTError(f"all STT providers failed: {last}")
