"""Exercise the user's fixed local song through a running VocalCreator API.

The report and converted lyrics stay in ignored output/. Browser playback and
listening are separate required steps; a successful API run does not assert them.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

ROOT = Path(__file__).resolve().parents[1]
AUDIO = Path.home() / "Downloads/SEEKAE_-_TEST_AND_RECOGNISE_X_FLUME_RE_-_WORK_(mp3.pm).mp3"
LYRICS_RTF = Path.home() / "Downloads/ Seekae - Test & Recognise (Flume Re-Work)Lyric.rtf"
EXPECTED_AUDIO = "01525055fe716f694b96f2f3997edecfd1c6f84331c282f07898195cbda5f93c"
EXPECTED_LYRICS = "28424da287ccd160aa0cfce4ff804407b438128c57e6890ddfe30214c144b749"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8082")
    parser.add_argument("--output", type=Path, default=ROOT / "output/reference-check")
    parser.add_argument("--reanalyze", action="store_true", help="Run a new full analysis when an upstream analysis stage changed")
    parser.add_argument("--rates", default="0.5,1", help="Comma-separated playback rates to prepare")
    args = parser.parse_args()
    if urlparse(args.url).hostname not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("Reference audio and lyrics may only be sent to loopback")
    audio_hash = hashlib.sha256(AUDIO.read_bytes()).hexdigest()
    lyrics = subprocess.check_output(["textutil", "-convert", "txt", "-stdout", str(LYRICS_RTF)])
    lyrics_hash = hashlib.sha256(lyrics).hexdigest()
    if (audio_hash, lyrics_hash) != (EXPECTED_AUDIO, EXPECTED_LYRICS):
        raise SystemExit("Reference files changed: inspect them before replacing the fixed fixture")
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "reference-lyrics.txt").write_bytes(lyrics)
    report = {"audio_sha256": audio_hash, "lyrics_sha256": lyrics_hash, "modes": {}, "rates": {},
              "browser_verified": False, "human_acceptance": "pending"}
    with httpx.Client(base_url=args.url, timeout=1200) as client:
        def get(url):
            response = client.get(url); response.raise_for_status(); return response.json()
        def post(url, **kwargs):
            response = client.post(url, **kwargs); response.raise_for_status(); return response.json()
        library = get("/api/studio/jobs")["jobs"]
        job = next((item for item in library if item.get("input", {}).get("audio_sha256") == audio_hash
                    and item.get("input", {}).get("lyrics_sha256") == lyrics_hash
                    and item.get("status") in {"complete", "partial"}), None)
        if args.reanalyze or job is None:
            with AUDIO.open("rb") as audio:
                accepted = post("/api/studio/jobs", files={"audio": (AUDIO.name, audio, "audio/mpeg"),
                                  "lyrics": ("reference-lyrics.txt", lyrics, "text/plain")},
                                data={"input_type": "mix", "language": "en"})
            while True:
                job = get(f"/api/studio/jobs/{accepted['job_id']}")
                if job["status"] not in {"queued", "running"}:
                    break
                print(job.get("stage_label", job["status"]), flush=True)
                time.sleep(5)
        if job["status"] != "complete":
            raise RuntimeError(f"Reference analysis ended with {job['status']}: {job.get('errors')}")
        prefix = f"/api/studio/jobs/{job['id']}"
        canonical = get(f"{prefix}/text")
        if canonical["canonical_text"].encode() != lyrics or len(canonical["words"]) != 305:
            raise AssertionError("Canonical lyrics or one of the 305 occurrences was lost")
        for mode in ("light", "medium", "pro"):
            start = time.monotonic()
            data = post(f"{prefix}/v3/{mode}")
            assert data["canonical_text"].encode() == lyrics
            assert [w["text"] for w in data["words"]] == [w["text"] for w in canonical["words"]]
            assert len({w["id"] for w in data["words"]}) == 305
            assert all(note.get("labels") for note in data["notes"]), "A vocal note lost its lyric label"
            word_by_id = {word["id"]: word for word in data["words"]}
            for note in data["notes"]:
                for label in note["labels"]:
                    assert label["text"] and label["text"] in word_by_id[label["word_id"]]["text"]
                    if label["kind"] == "lyric-context":
                        assert label["status"] == "context", "Unverified context presented as recognition"
            report["modes"][mode] = {"notes": len(data["notes"]), "words": len(data["words"]),
                "diagnostics": data["diagnostics"], "seconds": round(time.monotonic() - start, 3), "cache_key": data["cache_key"]}
            print(f"{mode}: {len(data['notes'])} notes, {len(data['words'])} words", flush=True)
        for rate in map(float, args.rates.split(",")):
            start = time.monotonic()
            prepared = post(f"{prefix}/tempo/light/{rate}")
            assert abs(prepared["duration"] * rate - job["timeline"]["duration"]) < .001
            assert set(prepared["artifacts"]) == {"vocals.wav", "instrumental.wav", "piano.wav"}
            report["rates"][str(rate)] = {"seconds": round(time.monotonic() - start, 3), "cache_key": prepared["cache_key"],
                                          "duration": prepared["duration"]}
            print(f"{rate}x ready", flush=True)
        report.update(job_id=job["id"], url=f"{args.url}/?job={job['id']}", status="api-passed", duration=job["timeline"]["duration"])
    (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Open in browser and verify playback: {report['url']}")


if __name__ == "__main__":
    main()
