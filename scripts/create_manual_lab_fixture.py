"""Create an isolated, unmistakably synthetic comparison lab for desktop QA.

Never points at the normal VocalCreator data directory. No real human reference,
acceptance decision or claimed model improvement is produced by this script.
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import struct
import wave
from pathlib import Path

from karaoke_generator import manual_cycles as lab
from karaoke_generator import manual_lyrics as storage

LABEL = "ТЕСТОВЫЙ ПРИМЕР — не человеческий эталон"


def make_song(jobs_root: Path, job_id: str, language: str, ordinal: int) -> tuple[Path, dict]:
    result = jobs_root / job_id / "result"
    inputs = result.parent / "input"
    inputs.mkdir(parents=True, exist_ok=False)
    result.mkdir()
    rate, duration, frequency = 16000, 6, 150 + ordinal * 31
    audio = inputs / "audio.wav"
    with wave.open(str(audio), "wb") as stream:
        stream.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        stream.writeframes(b"".join(struct.pack("<h", round(6500 * math.sin(2 * math.pi * frequency * i / rate)))
                                    for i in range(rate * duration)))
    text = "Go home" if language == "en" else "Иду домой"
    (inputs / "lyrics.txt").write_text(text, encoding="utf-8")
    shutil.copy2(inputs / "lyrics.txt", result / "lyrics.txt")
    shutil.copy2(audio, result / "original.wav")
    for name in ("vocals.wav", "instrumental.wav", "piano.wav"):
        shutil.copy2(audio, result / name)
    words = [{"text": word, "normalized": word.lower(), "start": 1.0 + i * 2,
              "end": 2.0 + i * 2, "aligned": True, "confidence": None,
              "alignment_source": "synthetic-qa", "timing": {"source": "synthetic-qa"}}
             for i, word in enumerate(text.split())]
    alignment = {"schema_version": 3, "language": language, "duration": duration,
                 "backend": "synthetic-qa", "lines": [{"text": text, "start": 1, "end": 4, "words": words}]}
    lab.write(result / "alignment.json", alignment)
    melody = {"schema_version": 1, "timeline": {"duration": duration}, "pitch_frames": [],
              "notes": [{"id": f"n{i}", "start": w["start"], "end": w["end"], "midi": 60 + i,
                         "cents": 0, "confidence": 0, "source": "synthetic-qa", "uncertain": True}
                        for i, w in enumerate(words)], "word_note_links": [],
              "diagnostics": {"synthetic_fixture": True}, "provenance": {"label": LABEL}}
    lab.write(result / "melody.json", melody)
    manifest = {"schema_version": 1, "id": job_id, "status": "complete", "created_at": lab.now(),
                "updated_at": lab.now(), "input": {"audio_name": f"{LABEL} · {language.upper()} {ordinal}",
                                                  "lyrics_name": "lyrics.txt", "type": "vocal"},
                "timeline": {"duration": duration}, "artifacts": {name: name for name in
                    ("studio.json", "lyrics.txt", "alignment.json", "melody.json", "vocals.wav", "instrumental.wav", "piano.wav")},
                "stages": {}, "errors": [], "synthetic_fixture": True}
    lab.write(result / "studio.json", manifest)
    return result, manifest


def create_fixture(destination: Path, *, run: bool = True) -> dict:
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    jobs_root, lab_root = destination / "jobs", destination / "manual-lab"
    profiles = {"baseline": {}, "candidate": {}}
    for language in ("en", "ru"):
        for side, offset in (("baseline", 0), ("candidate", .15)):
            profile = lab.create_profile(lab_root, language=language, name=f"{LABEL} · {side} {language}",
                                         parameters={"timing_offset_seconds": offset})
            profiles[side][language] = profile["id"]
    for ordinal, (language, index) in enumerate((lang, n) for lang in ("en", "ru") for n in range(4)):
        job_id = f"fixture{language}{index}"
        result, manifest = make_song(jobs_root, job_id, language, ordinal)
        project = storage.read_project(result, job_id, manifest)
        project["synthetic_fixture"] = True
        project["reviews"] = [{"start": 0, "end": project["duration"], "all_roles": True}]
        project = storage.save_project(result, job_id, manifest, project)
        reference = storage.create_reference(result, job_id, manifest,
                                             {"revision": project["revision"], "language": language,
                                              "label": LABEL, "difficult_cases": [LABEL]}) if index < 3 else None
        entry = {"job_id": job_id, "title": manifest["input"]["audio_name"], "language": language,
                 "split": "tuning" if index < 3 else "control", "source": project["source"],
                 "reference": reference, "reference_id": reference["reference_id"] if reference else None,
                 "difficult_cases": [LABEL], "synthetic_fixture": True}
        lab.register(lab_root, entry, lab.state(lab_root)["revision"])
    cycle = lab.create_cycle(lab_root, baselines=profiles["baseline"], candidates=profiles["candidate"],
                             changes=f"{LABEL}. Искусственный сдвиг +0.15 с для проверки сравнения; это не улучшение",
                             expected_revision=lab.state(lab_root)["revision"])
    if run:
        cycle = lab.run_cycle(lab_root, cycle["id"], jobs_root)
        if cycle["status"] != "ready":
            raise RuntimeError(cycle.get("error", "Synthetic cycle failed"))
    result = {"data_dir": str(destination), "jobs_root": str(jobs_root), "lab_root": str(lab_root),
              "profiles": profiles, "cycle_id": cycle["id"], "status": cycle["status"], "label": LABEL}
    lab.write(destination / "fixture.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("output/manual-lab-qa/data"))
    parser.add_argument("--no-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(create_fixture(args.output, run=not args.no_run), ensure_ascii=False, indent=2))
