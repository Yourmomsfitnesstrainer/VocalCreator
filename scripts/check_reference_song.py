"""Exercise Sputnik and Mujuice through the local studio and syllable-score stage.

Media and reports remain outside Git. Browser playback and human listening are
separate acceptance steps; API success does not assert them. Legacy songs are preserved.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
from prepare_syllable_references import REFERENCE_ROOT, SONGS

REFERENCES = {name: REFERENCE_ROOT / name for name in SONGS}


def verify_reference(reference: Path) -> dict:
    manifest = json.loads((reference / "manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["files"].items():
        data = (reference / name).read_bytes()
        if len(data) != expected["bytes"] or hashlib.sha256(data).hexdigest() != expected["sha256"]:
            raise ValueError(f"Reference file changed: {reference / name}")
    lyrics = (reference / "lyrics.txt").read_bytes().decode("utf-8")
    if len(lyrics.split()) != manifest["canonical_word_count"]:
        raise ValueError(f"Canonical word count changed: {reference}")
    return manifest


def check_song(args, reference: Path, manifest: dict, report: dict) -> dict:
    import httpx

    audio_path = reference / "audio.mp3"
    lyrics = (reference / "lyrics.txt").read_bytes()
    audio_hash = manifest["files"]["audio.mp3"]["sha256"]
    lyrics_hash = manifest["files"]["lyrics.txt"]["sha256"]
    expected_words = manifest["canonical_word_count"]
    report.update(audio_sha256=audio_hash, lyrics_sha256=lyrics_hash, modes={}, rates={},
                  browser_verified=False, human_acceptance="pending")
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
            with audio_path.open("rb") as audio:
                accepted = post("/api/studio/jobs", files={"audio": (audio_path.name, audio, "audio/mpeg"),
                                  "lyrics": ("reference-lyrics.txt", lyrics, "text/plain")},
                                data={"input_type": "mix", "language": manifest["language"]})
            while True:
                job = get(f"/api/studio/jobs/{accepted['job_id']}")
                if job["status"] not in {"queued", "running"}:
                    break
                print(f"{manifest['title']}: {job.get('stage_label', job['status'])}", flush=True)
                time.sleep(5)
        if job["status"] != "complete":
            raise RuntimeError(f"Reference analysis ended with {job['status']}: {job.get('errors')}")
        prefix = f"/api/studio/jobs/{job['id']}"
        canonical = get(f"{prefix}/text")
        if canonical["canonical_text"].encode() != lyrics or len(canonical["words"]) != expected_words:
            raise AssertionError(f"Canonical lyrics or one of the {expected_words} occurrences was lost")
        for mode in ("full",):
            start = time.monotonic()
            data = post(f"{prefix}/melody")
            assert data["canonical_text"].encode() == lyrics
            assert [w["text"] for w in data["words"]] == [w["text"] for w in canonical["words"]]
            assert len({w["id"] for w in data["words"]}) == expected_words
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
            prepared = post(f"{prefix}/tempo/full/{rate}")
            assert abs(prepared["duration"] * rate - job["timeline"]["duration"]) < .001
            assert set(prepared["artifacts"]) == {"vocals.wav", "instrumental.wav", "piano.wav"}
            report["rates"][str(rate)] = {"seconds": round(time.monotonic() - start, 3), "cache_key": prepared["cache_key"],
                                          "duration": prepared["duration"]}
            print(f"{rate}x ready", flush=True)
        # Explicitly execute the new text-only stage; never call legacy role grouping.
        import uuid
        print(f"{manifest['title']}: preparing syllable score", flush=True)
        started = time.monotonic()
        score_state = post(f"{prefix}/syllables", json={"request_id": str(uuid.uuid4()), "retry": False})
        while score_state["status"] in {"queued", "running"}:
            time.sleep(1)
            score_state = get(f"{prefix}/syllables")
        if score_state["status"] not in {"ready", "partial"}:
            raise AssertionError(f"Syllable stage failed: {score_state}")
        published = score_state["published"]
        score = get(published["url"])
        from karaoke_generator.syllable_score import validate_score
        validate_score(score, data["notes"], expected_job_id=job["id"])
        assert score["source"]["audio_sha256"] == audio_hash
        assert score["source"]["lyrics_sha256"] == lyrics_hash
        assert score["job_id"] == job["id"] and score["schema_version"] == 1
        assert score["canonical_text"].encode() == lyrics
        assert len(score["occurrences"]) >= expected_words, "Canonical occurrences lost"
        assert len({u["unit_id"] for u in score["units"]}) == len(score["units"])
        assert all(u["origin"] == "automatic" for u in score["units"]), "Auto must not claim manual verification"
        linked = {link["unit_id"] for link in score["note_links"]}
        fallback = [u for u in score["units"] if u["kind"] == "word_fallback"]
        syllables = [u for u in score["units"] if u["kind"] == "syllable"]
        score_report = {"base_analysis_key": score["base_analysis_key"], "canonical_words": expected_words,
                        "occurrences": len(score["occurrences"]), "units": len(score["units"]),
                        "syllables": len(syllables), "word_fallback": len(fallback),
                        "note_links": len(score["note_links"]), "linked_units": len(linked),
                        "approximate_units": sum(u["timing_status"] == "approximate" or u["segmentation_status"] == "approximate" for u in score["units"]),
                        "unplaced_units": sum(not u["intervals"] for u in score["units"]),
                        "unresolved": len(score["unresolved"]), "provenance": score["provenance"],
                        "unlabelled_notes": len({n["id"] for n in data["notes"]} - {l["source_note_id"] for l in score["note_links"]}),
                        "fallback_words": [{"text": u["text"], "reasons": u["reason_codes"]} for u in fallback],
                        "seconds": round(time.monotonic() - started, 3)}
        report["syllable_score"] = score_report
        if len(fallback) > expected_words / 2:
            raise AssertionError(f"Mass fallback: {len(fallback)}/{expected_words}; automatic syllable acceptance incomplete")
        report.update(source_job_id=job["id"], job_id=job["id"],
                      url=f"{args.url.rstrip('/')}/?job={job['id']}",
                      status="api-passed", duration=job["timeline"]["duration"])
    print(f"Open in browser and verify playback: {report['url']}")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--output", type=Path, default=Path.home() / "Library/Application Support/VocalCreator/syllable-score-20260914/verification/reference-check")
    parser.add_argument("--song", choices=("all", *REFERENCES), default="all",
                        help="Both songs by default; a single song is only a targeted check")
    parser.add_argument("--reanalyze", action="store_true", help="Run a new full analysis when an upstream analysis stage changed")
    parser.add_argument("--rates", default="0.25,0.5,1,2", help="Comma-separated playback rates to prepare")
    parser.add_argument("--check-files-only", action="store_true", help="Verify registered inputs without a server or analysis")
    args = parser.parse_args()
    if urlparse(args.url).hostname not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("Reference audio and lyrics may only be sent to loopback")
    selected = REFERENCES if args.song == "all" else {args.song: REFERENCES[args.song]}
    # Verify every selected fixture before any API call, including the original lyrics.
    verified = {name: verify_reference(path) for name, path in selected.items()}
    if args.check_files_only:
        for name, manifest in verified.items():
            print(f"{name}: SHA-256, sizes and UTF-8 verified; {manifest['canonical_word_count']} words")
        print(f"{len(verified)} reference song(s) verified; no server or models used")
        return
    # Keep earlier reports and results when another acceptance run is made.
    from datetime import datetime, timezone

    run_output = args.output / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run_output.mkdir(parents=True, exist_ok=False)
    reports = []
    for name, reference in selected.items():
        manifest = verified[name]
        report = {"song": name, "title": manifest["title"], "language": manifest["language"]}
        song_output = run_output / name
        song_output.mkdir()
        try:
            check_song(args, reference, manifest, report)
        except Exception as exc:
            report.update(status="failed", error=str(exc))
            print(f"{name}: FAILED: {exc}", flush=True)
        (song_output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        reports.append(report)
    summary = {"status": "api-passed" if all(r["status"] == "api-passed" for r in reports) else "failed",
               "songs": reports, "browser_verified": False, "human_acceptance": "pending"}
    (run_output / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Corpus report: {run_output / 'report.json'}")
    if summary["status"] != "api-passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
