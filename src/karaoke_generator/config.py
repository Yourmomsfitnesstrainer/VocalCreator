from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONFIG: dict[str, Any] = {
    "alignment": {
        "backend": "whisperx",
        "language": "auto",
        "model": "small",
        "device": "auto",
        "compute_type": "int8",
        "min_similarity": 0.62,
        "use_lyrics_prompt": True,
        "vad_filter": True,
        "vad_threshold": 0.30,
        "vad_min_silence_duration_ms": 1000,
        "vad_speech_pad_ms": 600,
        "context_seconds": 0.3,
        "max_window_seconds": 20.0,
        "score_thresholds": {},
        "align_models": {"ru": "bond005/wav2vec2-base-ru"},
    },
    "separation": {
        "enabled": "auto",
        "backend": "demucs",
        "model": "htdemucs",
        "device": "auto",
    },
    "pitch": {
        "backend": "torchcrepe",
        "device": "auto",
        "model": "full",
        "hop_seconds": 0.01,
        "fmin": 65.0,
        "fmax": 1100.0,
        "periodicity_threshold": 0.32,
        "silence_db": -55.0,
        "batch_size": 1024,
    },
    "notes": {"min_note_seconds": 0.08, "max_gap_seconds": 0.055},
    "piano": {
        "sample_rate": 44100,
        "attack_seconds": 0.008,
        "release_seconds": 0.18,
        "gain": 0.22,
    },
    "video": {
        "width": 1920,
        "height": 1080,
        "fps": 30,
        "background": "procedural",
        "background_color": "#080B1A",
    },
    "karaoke": {
        "font": "Arial",
        "font_size": 72,
        "max_lines": 2,
        "max_chars_per_line": 42,
        "active_color": "#FFD43B",
        "inactive_color": "#F2F3F5",
        "preview_color": "#A7ABB7",
        "timing_offset_ms": 0,
    },
    "output": {"audio_mode": "instrumental", "video_bitrate": "8M", "audio_bitrate": "256k"},
}


def _merge(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def load_config(path: Path | None = None) -> dict[str, Any]:
    config = deepcopy(DEFAULT_CONFIG)
    candidate = path or (Path.cwd() / "config.yaml")
    if candidate.exists():
        loaded = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"Config root must be a mapping: {candidate}")
        _merge(config, loaded)
    return config
