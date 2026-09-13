"""Versioned local exercise artifacts; original analysis and v2 stay immutable."""
from __future__ import annotations

import json
import shutil
import statistics
import tempfile
import threading
import uuid
import wave
from pathlib import Path

from .learning_storage import ATTACK_OPTIONS, ATTACK_VERSION, energy_attacks
from .melody import STABLE_OPTIONS, STABLE_SEGMENTATION_VERSION, stable_note_events
from .models import AlignmentResult
from .piano import PIANO_SYNTH_VERSION, render_piano
from .studio_models import MelodyResult, NoteEvent
from .timing_cache import file_sha256, fingerprint, write_json

V3_STORAGE_VERSION = "parts-artifacts-4"
_LOCK = threading.Lock()
MODES = ("light", "medium", "pro")


def _algorithms() -> dict:
    from . import learning_v3, syllables, lyric_recovery
    return {"storage": V3_STORAGE_VERSION, "segmentation": STABLE_SEGMENTATION_VERSION,
            "segmentation_parameters": STABLE_OPTIONS, "attacks": ATTACK_VERSION,
            "attack_parameters": ATTACK_OPTIONS, "piano": PIANO_SYNTH_VERSION,
            "learning": file_sha256(Path(learning_v3.__file__)),
            "parts": file_sha256(Path(syllables.__file__)),
            "lyric_recovery": file_sha256(Path(lyric_recovery.__file__))}


def _inputs(result_dir: Path, available: set[str]) -> dict:
    names = {"melody.json", "alignment.json", "lyrics.txt", "vocals.wav"} & available
    paths = [result_dir / name for name in sorted(names)]
    # Character timing is part of the acoustic input, not incidental metadata.
    if "alignment.json" in available:
        directory = result_dir / "work/timing-cache"
        paths += sorted(directory.glob("refined-*.json")) + sorted(directory.glob("asr-*.json"))
    return {str(path.relative_to(result_dir)): file_sha256(path) for path in paths if path.is_file()}


def read_text(result_dir: Path, available: set[str], duration: float) -> dict:
    from .syllables import canonical_words

    alignment = None
    if "alignment.json" in available:
        try:
            alignment = AlignmentResult.from_dict(json.loads((result_dir / "alignment.json").read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass
    text = None
    if "lyrics.txt" in available and (result_dir / "lyrics.txt").is_file():
        text = (result_dir / "lyrics.txt").read_text(encoding="utf-8")
    source = "lyrics.txt" if text is not None else "alignment-reconstructed"
    if text is None:
        text = "\n".join(line.text for line in alignment.lines) if alignment else ""
    return {"canonical_text": text, "words": canonical_words(text, alignment, duration), "source": source}


def ready_modes(result_dir: Path, available: set[str]) -> dict[str, dict]:
    """Return only complete, still-current published cache entries. Never prepares."""
    index = result_dir / "learning-v3/index.json"
    try:
        published = json.loads(index.read_text(encoding="utf-8"))
        if not isinstance(published, dict) or not isinstance(published.get("modes"), dict):
            return {}
        if published.get("inputs") != _inputs(result_dir, available) or published.get("algorithms") != _algorithms():
            return {}
        result = {}
        for mode, key in published.get("modes", {}).items():
            if mode not in MODES or not valid_key(key):
                continue
            directory = index.parent / key
            data = _read_cache(directory, key, mode)
            if data:
                result[mode] = data
        return result
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def valid_key(key: str) -> bool:
    return isinstance(key, str) and len(key) == 64 and all(c in "0123456789abcdef" for c in key)


def _read_cache(directory: Path, key: str, mode: str) -> dict | None:
    try:
        data = json.loads((directory / "learning.json").read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("cache_key") != key or data.get("mode") != mode or data.get("schema_version") != 3:
            return None
        piano = directory / "piano.wav"
        if file_sha256(piano) != data.get("piano_sha256"):
            return None
        with wave.open(str(piano), "rb") as audio:
            if abs(audio.getnframes() / audio.getframerate() - data["timeline"]["duration"]) > .001:
                return None
        return data
    except (OSError, ValueError, KeyError, TypeError, AttributeError, wave.Error, EOFError):
        return None


def prepare_v3(result_dir: Path, mode: str, *, available: set[str], piano_options: dict | None = None) -> tuple[dict, Path]:
    from .learning_v3 import build_learning_result_v3
    from .syllables import load_character_evidence
    from .lyric_recovery import prepare_lyric_evidence

    if mode not in MODES:
        raise ValueError("Неизвестный режим мелодии")
    if "melody.json" not in available:
        raise ValueError("Нет исходных данных нот; сохранённые аудио и текст остаются доступны")
    with _LOCK:
        inputs = _inputs(result_dir, available)
        melody = MelodyResult.from_dict(json.loads((result_dir / "melody.json").read_text(encoding="utf-8")))
        text = read_text(result_dir, available, melody.timeline["duration"])
        alignment = None
        alignment_error = None
        if "alignment.json" in available:
            try:
                alignment = AlignmentResult.from_dict(json.loads((result_dir / "alignment.json").read_text(encoding="utf-8")))
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
                alignment_error = str(exc)
        evidence = load_character_evidence(result_dir) if alignment is not None and "vocals.wav" in available else {"words": [], "diagnostics": {"status": "unavailable"}}
        synth = {"sample_rate": 44100, "attack_seconds": .008, "gain": .22,
                 **(piano_options or {}), "bound_to_intervals": True}
        # Module hashes include algorithm constants and mapping rules in the key.
        algorithms = _algorithms()
        key = fingerprint({"inputs": inputs, "mode": mode, "algorithms": algorithms, "synth": synth,
                           "evidence": evidence})
        cache_root = result_dir / "learning-v3"
        destination = cache_root / key
        data = _read_cache(destination, key, mode)
        if data is None:
            if melody.pitch_frames:
                times = [frame.time for frame in melody.pitch_frames]
                steps = [b - a for a, b in zip(times, times[1:]) if b > a]
                attacks, attack_diagnostics = energy_attacks(result_dir / "vocals.wav" if "vocals.wav" in available else None)
                notes, segmentation = stable_note_events(melody.pitch_frames, duration=melody.timeline["duration"],
                                                         hop_seconds=statistics.median(steps) if steps else .01,
                                                         attack_times=attacks)
                segmentation["attacks"] = attack_diagnostics
            else:
                notes = melody.notes
                segmentation = {"status": "saved-events", "reason": "Нет pitch-кадров; использованы исходные события"}
            provenance = {"inputs": inputs, "algorithms": algorithms, "segmentation": segmentation,
                          "canonical_text_source": text["source"], "alignment_error": alignment_error,
                          "piano_parameters": synth}
            recovered = prepare_lyric_evidence(
                result_dir / "vocals.wav", text["words"], notes,
                duration=melody.timeline["duration"], language=getattr(alignment, "language", "unknown"),
                recognized_words=evidence.get("recognized_words", []),
                cache_root=result_dir / "lyric-recovery-v3") if "vocals.wav" in available else None
            data = build_learning_result_v3(mode, notes, alignment, timeline=melody.timeline, provenance=provenance,
                                            canonical_text=text["canonical_text"], character_evidence=evidence,
                                            lyric_evidence=recovered,
                                            language=getattr(alignment, "language", None))
            data["cache_key"] = key
            cache_root.mkdir(parents=True, exist_ok=True)
            temporary = Path(tempfile.mkdtemp(prefix=".prepare-", dir=cache_root))
            try:
                events = [NoteEvent(f"{note['id']}-{i}", interval["start"], interval["end"], note["midi"],
                                    note.get("cents", 0), note.get("confidence", 0), "v3-stable-event", note.get("uncertain", False))
                          for note in data["notes"] for i, interval in enumerate(note.get("intervals", [note]))]
                data["piano"] = render_piano(events, temporary / "piano.wav", duration=melody.timeline["duration"], options=synth)
                data["piano_sha256"] = file_sha256(temporary / "piano.wav")
                write_json(temporary / "learning.json", data)
                # Preserve a damaged derivative for diagnosis while rebuilding;
                # original/v2 artifacts are outside this cache root entirely.
                quarantine = None
                if destination.exists():
                    quarantine = cache_root / f".invalid-{key}-{uuid.uuid4().hex}"
                    destination.rename(quarantine)
                try:
                    temporary.rename(destination)
                except OSError:
                    if quarantine is not None:
                        quarantine.rename(destination)
                    raise
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
        existing = ready_modes(result_dir, available)
        modes = {name: item["cache_key"] for name, item in existing.items()}
        modes[mode] = key
        write_json(cache_root / "index.json", {"inputs": inputs, "modes": modes, "algorithms": algorithms})
        return data, destination
