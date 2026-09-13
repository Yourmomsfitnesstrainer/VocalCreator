"""Lazy v0.1 migration: only derived files are written, without ML inference."""
from __future__ import annotations

import json
import math
import shutil
import statistics
import tempfile
import threading
import wave
from dataclasses import asdict
from pathlib import Path

from .learning import LEARNING_OPTIONS, LEARNING_VERSION, MODES, build_learning_result, piano_events
from .melody import STABLE_OPTIONS, STABLE_SEGMENTATION_VERSION, stable_note_events
from .models import AlignmentResult
from .piano import PIANO_SYNTH_VERSION, render_piano
from .studio_models import MelodyResult, NoteEvent
from .timing_cache import file_sha256, fingerprint, write_json

# Locks bound both disk publication and concurrent CPU/memory use. Hashing may
# happen concurrently; files become visible only after all outputs exist.
_PREPARE_LOCK = threading.Lock()
ATTACK_VERSION = "rms-dip-1"
ATTACK_OPTIONS = {"window_seconds": 0.005, "context_seconds": 0.04,
                  "rise_ratio": 2.8, "fall_ratio": 2.0, "minimum_spacing_seconds": 0.06}


def energy_attacks(path: Path | None) -> tuple[list[float], dict]:
    """Conservative same-pitch reattacks from the saved PCM stem, not new F0."""
    if path is None or not path.is_file():
        return [], {"status": "unavailable", "reason": "No saved vocal audio; reattacks use saved events/voicing only"}
    import numpy as np
    try:
        with wave.open(str(path), "rb") as audio:
            if audio.getsampwidth() != 2 or audio.getcomptype() != "NONE":
                return [], {"status": "unavailable", "reason": "Energy attacks require PCM16 WAV"}
            rate, channels = audio.getframerate(), audio.getnchannels()
            block = max(1, round(rate * ATTACK_OPTIONS["window_seconds"]))
            rms = []
            while raw := audio.readframes(block):
                samples = np.frombuffer(raw, dtype="<i2").astype(np.float32).reshape(-1, channels)
                # Energy over channels avoids phase cancellation of a stereo stem.
                rms.append(float(np.sqrt(np.mean(samples * samples))) / 32768)
    except (OSError, wave.Error, ValueError) as exc:
        return [], {"status": "unavailable", "reason": str(exc)}
    attacks = []
    hop = block / rate
    context = max(2, round(ATTACK_OPTIONS["context_seconds"] / hop))
    for index in range(context, len(rms) - context):
        floor = max(rms[index], 1e-5)
        before, after = max(rms[index-context:index]), max(rms[index+1:index+context+1])
        if before < 0.001 or after < 0.001 or before / floor < ATTACK_OPTIONS["fall_ratio"] or after / floor < ATTACK_OPTIONS["rise_ratio"]:
            continue
        if rms[index] > min(rms[index-1:index+2]):
            continue
        crossing = next((i for i in range(index + 1, index + context + 1) if rms[i] >= after * 0.5), index + 1)
        time = crossing * hop
        if not attacks or time - attacks[-1] >= ATTACK_OPTIONS["minimum_spacing_seconds"]:
            attacks.append(round(time, 6))
    return attacks, {"status": "available", "algorithm_version": ATTACK_VERSION,
                     "parameters": ATTACK_OPTIONS, "count": len(attacks)}


def prepare_learning(result_dir: Path, mode: str, *, piano_options: dict | None = None,
                     learning_options: dict | None = None, alignment_available: bool = True,
                     vocals_available: bool = True) -> tuple[dict, Path]:
    if mode not in MODES:
        raise ValueError("Неизвестный режим мелодии")
    melody_path, alignment_path, vocals_path = (result_dir / name for name in ("melody.json", "alignment.json", "vocals.wav"))
    if not melody_path.is_file():
        raise ValueError("Исходные данные нот отсутствуют. Готовые аудиодорожки остаются доступны.")
    # The same lock protects source snapshots and every cache publication.
    with _PREPARE_LOCK:
        melody_raw = json.loads(melody_path.read_text(encoding="utf-8"))
        melody = MelodyResult.from_dict(melody_raw)
        alignment_raw = None
        alignment = None
        alignment_error = None
        if alignment_available and alignment_path.is_file():
            try:
                alignment_raw = json.loads(alignment_path.read_text(encoding="utf-8"))
                alignment = AlignmentResult.from_dict(alignment_raw)
                if any(not math.isfinite(t) or t < 0 or t > melody.timeline["duration"] + 1e-6
                       for line in alignment.lines for word in line.words for t in (word.start, word.end)):
                    raise ValueError("Граница слова выходит за временную шкалу")
            except (ValueError, KeyError, TypeError) as exc:
                alignment = None
                alignment_error = f"Временная разметка текста повреждена: {exc}"
        if mode != "pro" and alignment_error:
            raise ValueError(alignment_error)
        source_hash = fingerprint(melody_raw)
        word_hash = fingerprint(alignment_raw) if alignment is not None else "unavailable"
        vocal_hash = file_sha256(vocals_path) if vocals_available and vocals_path.is_file() else "unavailable"
        stable_key = fingerprint({"source": source_hash, "vocal_audio": vocal_hash,
                                  "segmentation": STABLE_SEGMENTATION_VERSION, "parameters": STABLE_OPTIONS,
                                  "attacks": ATTACK_VERSION, "attack_parameters": ATTACK_OPTIONS})
        settings = {**LEARNING_OPTIONS, **(learning_options or {})}
        synth = {"sample_rate": 44100, "attack_seconds": 0.008, "gain": 0.22,
                 **(piano_options or {}), "bound_to_intervals": True}
        key = fingerprint({"source": source_hash, "words": word_hash,
                           "alignment_error": alignment_error,
                           "word_schema": alignment.schema_version if alignment else None,
                           "stable": stable_key, "learning": LEARNING_VERSION, "mode": mode,
                           "parameters": settings, "synth": PIANO_SYNTH_VERSION, "synth_parameters": synth})
        cache_root = result_dir / "learning"
        destination = cache_root / key
        if all((destination / name).is_file() for name in ("learning.json", "piano.wav")):
            data = json.loads((destination / "learning.json").read_text(encoding="utf-8"))
            return data, destination
        stable_path = cache_root / f"events-{stable_key}.json"
        if stable_path.is_file():
            stable = json.loads(stable_path.read_text(encoding="utf-8"))
            notes = [NoteEvent(**raw) for raw in stable["notes"]]
        else:
            if melody.pitch_frames:
                times = [frame.time for frame in melody.pitch_frames]
                differences = [b-a for a, b in zip(times, times[1:]) if b > a]
                hop = statistics.median(differences) if differences else 0.01
                attacks, attack_diagnostics = energy_attacks(vocals_path if vocals_available else None)
                notes, diagnostics = stable_note_events(melody.pitch_frames, duration=melody.timeline["duration"],
                                                        hop_seconds=hop, attack_times=attacks)
                diagnostics["attacks"] = attack_diagnostics
            else:
                notes = melody.notes
                diagnostics = {"status": "saved-events", "reason": "Нет pitch-кадров: сохранённые события без повторной сегментации",
                               "attacks": {"status": "unavailable"}}
            stable = {"notes": [asdict(note) for note in notes], "diagnostics": diagnostics,
                      "source_event_links": {note.id: [raw.id for raw in melody.notes
                                                       if min(note.end, raw.end) > max(note.start, raw.start)] for note in notes}}
            write_json(stable_path, stable)
        provenance = {"source_melody_sha256": source_hash, "canonical_alignment_sha256": word_hash,
                      "vocal_audio_sha256": vocal_hash, "source_artifact": "melody.json",
                      "canonical_alignment_schema": alignment.schema_version if alignment else None,
                      "stable_cache_key": stable_key, "stable_events": stable,
                      "simplification_version": LEARNING_VERSION, "segmentation_version": STABLE_SEGMENTATION_VERSION,
                      "piano_version": PIANO_SYNTH_VERSION, "piano_parameters": synth,
                      "alignment_error": alignment_error}
        result = build_learning_result(mode, notes, alignment, timeline=melody.timeline,
                                       provenance=provenance, options=settings)
        data = result.to_dict()
        data["cache_key"] = key
        cache_root.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=".prepare-", dir=cache_root))
        try:
            data["piano"] = render_piano(piano_events(result), temporary / "piano.wav",
                                         duration=melody.timeline["duration"], options=synth)
            write_json(temporary / "learning.json", data)
            temporary.rename(destination)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
        return data, destination
