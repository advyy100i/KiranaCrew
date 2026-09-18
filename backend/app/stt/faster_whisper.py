"""Local CTranslate2 Whisper. The model is loaded once per process (first call), never per request."""
import io
import threading
import time

from .base import STTError, STTResult

PROMPT = "Ramesh Kumar ne paanch kilo chawal udhaar pe liya. Sunita Devi ne do sau rupaye chukaye."


class FasterWhisperSTT:
    name = "faster_whisper"

    def __init__(self, model: str = "large-v3-turbo", device: str = "cpu", compute_type: str = "int8",
                 download_root: str | None = None):
        self.model, self.device, self.compute_type, self.download_root = model, device, compute_type, download_root
        self._model = None
        self._lock = threading.Lock()

    def load(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    try:
                        from faster_whisper import WhisperModel
                    except ImportError as e:
                        raise STTError("faster-whisper not installed (pip install faster-whisper)") from e
                    self._model = WhisperModel(self.model, device=self.device, compute_type=self.compute_type,
                                               download_root=self.download_root)
        return self._model

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def transcribe(self, audio: bytes, filename: str, language: str = "hi") -> STTResult:
        t0 = time.perf_counter()
        model = self.load()
        try:
            segments, info = model.transcribe(io.BytesIO(audio), language=language, vad_filter=True, beam_size=5,
                                              initial_prompt=PROMPT, temperature=0.0)
            segs = list(segments)
        except Exception as e:                      # noqa: BLE001
            raise STTError(f"faster-whisper failed: {e}") from e
        text = " ".join(s.text.strip() for s in segs).strip()
        avg = sum(s.avg_logprob for s in segs) / len(segs) if segs else None
        nsp = max((s.no_speech_prob for s in segs), default=None)
        return STTResult(text=text, provider=self.name, model=self.model, language=info.language, avg_logprob=avg,
                         no_speech_prob=nsp, latency_ms=int((time.perf_counter() - t0) * 1000))
