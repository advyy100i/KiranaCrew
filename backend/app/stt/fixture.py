"""Deterministic STT for tests and the "STT is down" demo: sha256(audio) -> transcript.
The map lives in a JSON file ({"<sha256>": "<transcript>"}) or is passed in directly."""
import hashlib
import json
import pathlib

from .base import STTError, STTResult


class FixtureSTT:
    name = "fixture"
    model = "fixture"

    def __init__(self, mapping: dict[str, str] | None = None, path: str | pathlib.Path | None = None):
        self.mapping = dict(mapping or {})
        if path and pathlib.Path(path).exists():
            self.mapping.update(json.loads(pathlib.Path(path).read_text(encoding="utf-8")))

    @staticmethod
    def key(audio: bytes) -> str:
        return hashlib.sha256(audio).hexdigest()

    def transcribe(self, audio: bytes, filename: str, language: str = "hi") -> STTResult:
        k = self.key(audio)
        if k not in self.mapping:
            raise STTError(f"fixture has no transcript for {k[:12]}")
        return STTResult(text=self.mapping[k], provider=self.name, model=self.model, language=language,
                         avg_logprob=-0.2, no_speech_prob=0.0, latency_ms=1)
