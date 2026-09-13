from __future__ import annotations

import math
import wave
from pathlib import Path
from typing import Any

from .studio_models import NoteEvent


PIANO_SYNTH_VERSION = "2"


def render_piano(
    notes: list[NoteEvent],
    output: Path,
    *,
    duration: float,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Render an original additive piano-like timbre; no external sample asset is used."""
    try:
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("NumPy is required to render the piano track") from exc
    options = options or {}
    sample_rate = int(options.get("sample_rate", 44100))
    total_samples = max(1, math.ceil(duration * sample_rate))
    mix = np.zeros(total_samples, dtype=np.float32)
    harmonics = ((1.0, 1.0), (2.0, 0.46), (3.0, 0.25), (4.0, 0.12), (5.0, 0.055))
    release = float(options.get("release_seconds", 0.18))
    attack = float(options.get("attack_seconds", 0.008))
    gain = float(options.get("gain", 0.22))
    bounded = bool(options.get("bound_to_intervals", False))
    for note in notes:
        start = max(0, round(note.start * sample_rate))
        stop = min(total_samples, round((note.end + (0 if bounded else release)) * sample_rate))
        if stop <= start:
            continue
        seconds = np.arange(stop - start, dtype=np.float32) / sample_rate
        held = max(0.01, note.end - note.start)
        envelope = np.minimum(1.0, seconds / max(attack, 1e-4))
        envelope *= np.exp(-2.35 * seconds / max(held + release, 0.08))
        release_start = max(0.0, held)
        release_mask = seconds > release_start
        envelope[release_mask] *= np.maximum(
            0.0, 1.0 - (seconds[release_mask] - release_start) / max(release, 1e-4)
        )
        if bounded:
            # A release tail must not fill a word boundary or an internal pause.
            envelope *= np.minimum(1.0, np.maximum(0.0, held - seconds) / max(attack, 1e-4))
        frequency = 440.0 * (2.0 ** ((note.midi - 69) / 12.0))
        tone = np.zeros_like(seconds)
        for harmonic, amplitude in harmonics:
            tone += amplitude * np.sin(2 * np.pi * frequency * harmonic * seconds)
        mix[start:stop] += gain * envelope * tone
    peak = float(np.max(np.abs(mix))) if len(mix) else 0.0
    if peak > 0.96:
        mix *= 0.96 / peak
    pcm = (mix * 32767).astype("<i2")
    stereo = np.empty((len(pcm), 2), dtype="<i2")
    stereo[:, 0] = pcm
    stereo[:, 1] = pcm
    temp = output.with_suffix(".tmp.wav")
    output.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(temp), "wb") as handle:
        handle.setparams((2, 2, sample_rate, 0, "NONE", "not compressed"))
        handle.writeframes(stereo.tobytes())
    temp.replace(output)
    return {
        "synth": "vocalcreator-additive-piano",
        "algorithm_version": PIANO_SYNTH_VERSION,
        "license": "MIT (project source code; no external samples)",
        "sample_rate": sample_rate,
        "channels": 2,
        "note_count": len(notes),
        "duration": duration,
        "peak_before_normalization": peak,
        "bound_to_intervals": bounded,
    }
