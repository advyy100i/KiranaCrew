"""Build the STT chain from settings. Primary = STT_PROVIDER; the other real provider is the fallback."""
import pathlib

from app.config import Settings

from .base import ChainSTT
from .faster_whisper import FasterWhisperSTT
from .fixture import FixtureSTT
from .groq import GroqSTT

FIXTURE_PATH = pathlib.Path(__file__).resolve().parents[3] / "demo" / "fixture_transcripts.json"
_cache: dict[str, ChainSTT] = {}


def build_stt(s: Settings, fixture_map: dict | None = None) -> ChainSTT:
    key = f"{s.stt_provider}:{s.whisper_model}:{bool(s.groq_api_key)}"
    if fixture_map is None and key in _cache:
        return _cache[key]
    providers: list = []
    local = FasterWhisperSTT(s.whisper_model, s.whisper_device, s.whisper_compute_type, s.whisper_download_root or None)
    if s.stt_provider == "fixture":
        providers.append(FixtureSTT(fixture_map, path=FIXTURE_PATH))
    elif s.stt_provider == "groq":
        if s.groq_api_key:
            providers.append(GroqSTT(s.groq_api_key, s.groq_stt_model))
        providers.append(local)
    else:
        providers.append(local)
        if s.groq_api_key:
            providers.append(GroqSTT(s.groq_api_key, s.groq_stt_model))
    chain = ChainSTT(providers)
    if fixture_map is None:
        _cache[key] = chain
    return chain
