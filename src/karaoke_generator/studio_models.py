from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class PitchFrame:
    time: float
    hz: float | None
    midi: float | None
    voiced: bool
    periodicity: float
    raw_score: float


@dataclass(frozen=True)
class NoteEvent:
    id: str
    start: float
    end: float
    midi: int
    cents: float
    confidence: float
    source: str
    uncertain: bool = False


@dataclass(frozen=True)
class WordNoteLink:
    word_id: str
    note_id: str
    start: float
    end: float
    status: str


@dataclass
class MelodyResult:
    timeline: dict[str, Any]
    pitch_frames: list[PitchFrame]
    notes: list[NoteEvent]
    word_note_links: list[WordNoteLink]
    diagnostics: dict[str, Any]
    provenance: dict[str, Any]
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MelodyResult":
        if int(data.get("schema_version", 0)) != 1:
            raise ValueError("Unsupported melody JSON schema")
        timeline = dict(data.get("timeline") or {})
        duration = _finite_number(timeline.get("duration"), "timeline.duration")
        if duration <= 0:
            raise ValueError("timeline.duration must be positive")
        frames = [PitchFrame(**raw) for raw in data.get("pitch_frames", [])]
        notes = [NoteEvent(**raw) for raw in data.get("notes", [])]
        links = [WordNoteLink(**raw) for raw in data.get("word_note_links", [])]
        for frame in frames:
            _bounded_interval(frame.time, frame.time, duration, "pitch frame", allow_empty=True)
            if frame.hz is not None and (not math.isfinite(frame.hz) or frame.hz <= 0):
                raise ValueError("pitch frame hz must be positive or null")
            if frame.midi is not None and not math.isfinite(frame.midi):
                raise ValueError("pitch frame midi must be finite or null")
        note_ids = set()
        for note in notes:
            _bounded_interval(note.start, note.end, duration, f"note {note.id}")
            if note.id in note_ids:
                raise ValueError(f"duplicate note id: {note.id}")
            if not 0 <= note.midi <= 127:
                raise ValueError(f"note {note.id} midi is outside 0..127")
            note_ids.add(note.id)
        for link in links:
            _bounded_interval(link.start, link.end, duration, f"link {link.word_id}")
            if link.note_id not in note_ids:
                raise ValueError(f"link references unknown note: {link.note_id}")
            if link.status not in {"confirmed", "approximate"}:
                raise ValueError(f"invalid word-note link status: {link.status}")
        return cls(
            timeline=timeline,
            pitch_frames=frames,
            notes=notes,
            word_note_links=links,
            diagnostics=dict(data.get("diagnostics") or {}),
            provenance=dict(data.get("provenance") or {}),
        )


def _finite_number(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number


def _bounded_interval(
    start: Any,
    end: Any,
    duration: float,
    label: str,
    *,
    allow_empty: bool = False,
) -> None:
    start_number = _finite_number(start, f"{label}.start")
    end_number = _finite_number(end, f"{label}.end")
    valid_order = start_number <= end_number if allow_empty else start_number < end_number
    if not valid_order or start_number < 0 or end_number > duration + 1e-6:
        raise ValueError(f"{label} interval is outside the audio timeline")
