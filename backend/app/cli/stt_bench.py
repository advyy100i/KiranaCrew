"""STT benchmark: CER / WER and latency per provider over eval/audio/*.ogg with eval/audio/transcripts.jsonl
({"file": "clip01.ogg", "text": "human transcript"} per line).
   python -m app.cli.stt_bench --providers faster_whisper,groq [--model small]
Also writes the per-clip normalized-text parse result so STT errors that still parse correctly are visible."""
import argparse
import json
import pathlib
import time

from app.config import settings
from app.domain.normalize import normalize
from app.stt.faster_whisper import FasterWhisperSTT
from app.stt.groq import GroqSTT

AUDIO = pathlib.Path(__file__).resolve().parents[3] / "eval" / "audio"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--providers", default="faster_whisper")
    ap.add_argument("--model", default=None, help="faster-whisper model override (large-v3-turbo|medium|small)")
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    try:
        import jiwer
    except ImportError:
        raise SystemExit("pip install jiwer")
    refs = [json.loads(l) for l in (AUDIO / "transcripts.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()] if (AUDIO / "transcripts.jsonl").exists() else []
    if not refs:
        raise SystemExit(f"no clips: add .ogg files and transcripts.jsonl under {AUDIO}")
    results = {}
    for name in a.providers.split(","):
        p = (FasterWhisperSTT(a.model or settings.whisper_model, settings.whisper_device, settings.whisper_compute_type,
                              settings.whisper_download_root) if name == "faster_whisper"
             else GroqSTT(settings.groq_api_key, settings.groq_stt_model))
        if hasattr(p, "load"):
            t0 = time.perf_counter(); p.load(); print(f"{name}: model loaded in {time.perf_counter() - t0:.1f}s")
        hyps, lat, rows = [], [], []
        for r in refs:
            audio = (AUDIO / r["file"]).read_bytes()
            out = p.transcribe(audio, r["file"], "hi")
            hyps.append(out.text); lat.append(out.latency_ms)
            rows.append({"file": r["file"], "ref": r["text"], "hyp": out.text, "latency_ms": out.latency_ms,
                         "norm_ref": normalize(r["text"]), "norm_hyp": normalize(out.text)})
            print(f"  {r['file']:<20} {out.latency_ms:>6} ms  {out.text}")
        refs_t = [r["text"].lower() for r in refs]; hyps_t = [h.lower() for h in hyps]
        lat_s = sorted(lat)
        results[name] = {"model": getattr(p, "model", ""), "n": len(refs), "cer": jiwer.cer(refs_t, hyps_t),
                         "wer": jiwer.wer(refs_t, hyps_t),
                         "norm_cer": jiwer.cer([r["norm_ref"] for r in rows], [r["norm_hyp"] for r in rows]),
                         "p50_ms": lat_s[len(lat_s) // 2], "p95_ms": lat_s[int(len(lat_s) * 0.95)], "clips": rows}
        print(f"{name} ({results[name]['model']}): CER {results[name]['cer']:.3f}  WER {results[name]['wer']:.3f}  "
              f"CER-after-normalize {results[name]['norm_cer']:.3f}  p50 {results[name]['p50_ms']} ms  p95 {results[name]['p95_ms']} ms")
    if a.json:
        json.dump(results, open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
