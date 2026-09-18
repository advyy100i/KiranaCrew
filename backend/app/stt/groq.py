"""Groq hosted Whisper (free tier). Accepts Telegram .ogg directly. verbose_json gives per-segment confidence."""
import time

import httpx

from .base import STTError, STTResult

URL = "https://api.groq.com/openai/v1/audio/transcriptions"
PROMPT = "Ramesh Kumar ne paanch kilo chawal udhaar pe liya. Sunita Devi ne do sau rupaye chukaye."


class GroqSTT:
    name = "groq"

    def __init__(self, api_key: str, model: str = "whisper-large-v3-turbo", timeout: float = 60.0):
        if not api_key:
            raise STTError("GROQ_API_KEY not set")
        self.api_key, self.model, self.timeout = api_key, model, timeout

    def transcribe(self, audio: bytes, filename: str, language: str = "hi") -> STTResult:
        t0 = time.perf_counter()
        for attempt in range(3):
            r = httpx.post(URL, headers={"Authorization": f"Bearer {self.api_key}"},
                           files={"file": (filename, audio)},
                           data={"model": self.model, "language": language, "response_format": "verbose_json",
                                 "prompt": PROMPT, "temperature": 0}, timeout=self.timeout)
            if r.status_code == 429 and attempt < 2:
                time.sleep(float(r.headers.get("retry-after", 2 * (attempt + 1))))
                continue
            if r.status_code >= 400:
                raise STTError(f"groq {r.status_code}: {r.text[:200]}")
            body = r.json()
            segs = body.get("segments") or []
            avg = sum(s.get("avg_logprob", 0) for s in segs) / len(segs) if segs else None
            nsp = max((s.get("no_speech_prob", 0) for s in segs), default=None)
            return STTResult(text=(body.get("text") or "").strip(), provider=self.name, model=self.model,
                             language=body.get("language"), avg_logprob=avg, no_speech_prob=nsp,
                             latency_ms=int((time.perf_counter() - t0) * 1000))
        raise STTError("groq: rate limited")
