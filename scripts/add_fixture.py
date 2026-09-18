"""Register a recorded clip for the fixture STT: python scripts/add_fixture.py demo/audio/clip.ogg "transcript text" """
import hashlib, json, pathlib, sys
clip, transcript = pathlib.Path(sys.argv[1]), sys.argv[2]
f = pathlib.Path(__file__).resolve().parents[1] / "demo" / "fixture_transcripts.json"
d = json.loads(f.read_text(encoding="utf-8")); d[hashlib.sha256(clip.read_bytes()).hexdigest()] = transcript
f.write_text(json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8"); print("registered", clip.name)
