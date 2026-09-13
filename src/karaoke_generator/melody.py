from __future__ import annotations

import hashlib
import math
import statistics
from dataclasses import replace

from .models import AlignmentResult, AlignedWord
from .studio_models import MelodyResult, NoteEvent, PitchFrame, WordNoteLink


NOTE_SEGMENTATION_VERSION = "1"
WORD_NOTE_MAPPING_VERSION = "1"
STABLE_SEGMENTATION_VERSION = "2"

# Engineering defaults, versioned with derived results. Real-singing calibration
# remains separate from the synthetic conformance fixtures.
STABLE_OPTIONS = {
    "minimum_event_seconds": 0.06,
    "transition_semitones": 0.8,
    "plateau_range_semitones": 0.45,
    "confidence_floor": 0.45,
}


def stable_note_events(
    frames: list[PitchFrame],
    *,
    duration: float,
    hop_seconds: float,
    attack_times: list[float] | None = None,
    options: dict | None = None,
) -> tuple[list[NoteEvent], dict]:
    """Detect plateaus with hysteresis; retain the untouched contour separately.

    Explicit unvoiced frames and missing frames split sounding islands. Energy
    attacks may split a plateau even if its pitch has not changed. Smooth ramps
    without a supported plateau do not generate a chromatic staircase.
    """
    settings = {**STABLE_OPTIONS, **(options or {})}
    minimum = float(settings["minimum_event_seconds"])
    window = max(3, math.ceil(minimum / hop_seconds))
    islands: list[list[PitchFrame]] = []
    separated = True
    for frame in frames:
        if not frame.voiced or frame.midi is None:
            separated = True
            continue
        if separated or not islands or frame.time - islands[-1][-1].time > hop_seconds * 1.5:
            islands.append([])
        islands[-1].append(frame)
        separated = False
    notes: list[NoteEvent] = []
    boundaries: list[dict] = []
    rejected: list[dict] = []
    attacks = sorted(attack_times or [])
    for island in islands:
        values = [float(frame.midi) for frame in island]
        if len(values) < window:
            rejected.append({"start": island[0].time, "reason": "insufficient_plateau_support"})
            continue
        label = round(statistics.median(values[:window]))
        cuts = [(0, label)]
        index = window
        while index + window <= len(values):
            local = values[index:index + window]
            target = round(statistics.median(local))
            distant = abs(statistics.median(local) - label) >= settings["transition_semitones"]
            plateau = max(local) - min(local) <= settings["plateau_range_semitones"]
            if target != label and distant and plateau:
                # The transition begins at observed support, never at a word midpoint.
                boundary = index
                while boundary > cuts[-1][0] + window and abs(values[boundary - 1] - target) <= 0.4:
                    boundary -= 1
                if boundary - cuts[-1][0] >= window:
                    cuts.append((boundary, target))
                    boundaries.append({"time": island[boundary].time, "reason": "supported_pitch_transition"})
                    label = target
                    index += window
                    continue
            index += 1
        for cut_index, (start, pitch) in enumerate(cuts):
            stop = cuts[cut_index + 1][0] if cut_index + 1 < len(cuts) else len(island)
            attack_cuts = [start]
            for attack in attacks:
                if not island[start].time + minimum <= attack <= island[stop - 1].time + hop_seconds - minimum:
                    continue
                cut = min(range(start, stop), key=lambda i: abs(island[i].time - attack))
                if cut - attack_cuts[-1] >= window and stop - cut >= window:
                    attack_cuts.append(cut)
                    boundaries.append({"time": island[cut].time, "reason": "energy_reattack"})
            attack_cuts.append(stop)
            for begin, end in zip(attack_cuts, attack_cuts[1:]):
                run = island[begin:end]
                confidence = statistics.fmean(frame.periodicity for frame in run)
                center = statistics.median(values[begin:end])
                if not 0 <= pitch <= 127:
                    continue
                notes.append(NoteEvent(
                    id=f"p{len(notes):05d}", start=round(run[0].time, 6),
                    end=round(min(duration, run[-1].time + hop_seconds), 6),
                    midi=pitch, cents=round((center - pitch) * 100, 3),
                    confidence=round(confidence, 6), source="stable-pitch-v2",
                    uncertain=confidence < settings["confidence_floor"] or abs(center - pitch) > 0.4,
                ))
    return notes, {"algorithm_version": STABLE_SEGMENTATION_VERSION, "parameters": settings,
                   "boundaries": boundaries, "rejected": rejected,
                   "calibration": "synthetic-contracts; human acoustic acceptance pending"}


def hz_to_midi(hz: float) -> float:
    if not math.isfinite(hz) or hz <= 0:
        raise ValueError("Pitch frequency must be positive and finite")
    return 69.0 + 12.0 * math.log2(hz / 440.0)


def midi_to_name(midi: int) -> str:
    names = ("C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B")
    if not 0 <= midi <= 127:
        raise ValueError("MIDI note must be between 0 and 127")
    return f"{names[midi % 12]}{midi // 12 - 1}"


def segment_notes(
    frames: list[PitchFrame],
    *,
    duration: float,
    source: str,
    hop_seconds: float,
    min_note_seconds: float = 0.08,
    max_gap_seconds: float = 0.055,
) -> list[NoteEvent]:
    """Turn F0 frames into stable monophonic note events without flattening raw pitch."""
    voiced = [frame for frame in frames if frame.voiced and frame.midi is not None]
    if not voiced:
        return []
    islands: list[list[PitchFrame]] = []
    for frame in voiced:
        if not islands or frame.time - islands[-1][-1].time > max_gap_seconds:
            islands.append([frame])
        else:
            islands[-1].append(frame)

    notes: list[NoteEvent] = []
    for island in islands:
        midi_values = [float(frame.midi) for frame in island if frame.midi is not None]
        smoothed = [
            statistics.median(midi_values[max(0, index - 2) : index + 3])
            for index in range(len(midi_values))
        ]
        labels = [round(value) for value in smoothed]
        labels = _merge_short_runs(labels, island, min_note_seconds, hop_seconds)
        start_index = 0
        while start_index < len(island):
            label = labels[start_index]
            end_index = start_index + 1
            while end_index < len(island) and labels[end_index] == label:
                end_index += 1
            run = island[start_index:end_index]
            start = max(0.0, run[0].time)
            end = min(duration, run[-1].time + hop_seconds)
            if end - start >= min_note_seconds and 0 <= label <= 127:
                pitches = [float(frame.midi) for frame in run if frame.midi is not None]
                confidence = statistics.fmean(frame.periodicity for frame in run)
                notes.append(
                    NoteEvent(
                        id=f"n{len(notes):05d}",
                        start=round(start, 6),
                        end=round(end, 6),
                        midi=int(label),
                        cents=round(statistics.median(pitches) * 100 - label * 100, 3),
                        confidence=round(confidence, 6),
                        source=source,
                        uncertain=confidence < 0.45,
                    )
                )
            start_index = end_index
    return notes


def _merge_short_runs(
    labels: list[int],
    frames: list[PitchFrame],
    min_note_seconds: float,
    hop_seconds: float,
) -> list[int]:
    labels = list(labels)
    minimum_frames = max(2, math.ceil(min_note_seconds / hop_seconds))
    for _ in range(3):
        runs: list[tuple[int, int, int]] = []
        start = 0
        for index in range(1, len(labels) + 1):
            if index == len(labels) or labels[index] != labels[start]:
                runs.append((start, index, labels[start]))
                start = index
        changed = False
        for run_index, (start, end, label) in enumerate(runs):
            if end - start >= minimum_frames:
                continue
            candidates = []
            if run_index:
                candidates.append(runs[run_index - 1][2])
            if run_index + 1 < len(runs):
                candidates.append(runs[run_index + 1][2])
            if not candidates:
                continue
            pitch = statistics.median(float(frame.midi) for frame in frames[start:end] if frame.midi is not None)
            replacement = min(candidates, key=lambda candidate: abs(candidate - pitch))
            labels[start:end] = [replacement] * (end - start)
            changed = changed or replacement != label
        if not changed:
            break
    return labels


def word_id(line_index: int, word_index: int, word: AlignedWord) -> str:
    canonical = f"{word.normalized}\0{word.text}".encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()[:10]
    return f"l{line_index:04d}-w{word_index:04d}-{digest}"


def link_words_to_notes(
    alignment: AlignmentResult | None,
    notes: list[NoteEvent],
) -> list[WordNoteLink]:
    if alignment is None:
        return []
    links: list[WordNoteLink] = []
    approximate_sources = {"interpolated", "approximate_split", "unknown"}
    for line_index, line in enumerate(alignment.lines):
        for word_index, word in enumerate(line.words):
            for note in notes:
                start = max(word.start, note.start)
                end = min(word.end, note.end)
                if end <= start:
                    continue
                timing_source = (word.timing or {}).get("source", word.alignment_source)
                status = (
                    "approximate"
                    if note.uncertain or not word.aligned or timing_source in approximate_sources
                    else "confirmed"
                )
                links.append(
                    WordNoteLink(
                        word_id=word_id(line_index, word_index, word),
                        note_id=note.id,
                        start=round(start, 6),
                        end=round(end, 6),
                        status=status,
                    )
                )
    return links


def build_melody_result(
    frames: list[PitchFrame],
    *,
    duration: float,
    source: str,
    hop_seconds: float,
    alignment: AlignmentResult | None,
    pitch_diagnostics: dict,
    note_options: dict,
    provenance: dict,
) -> MelodyResult:
    notes = segment_notes(
        frames,
        duration=duration,
        source=source,
        hop_seconds=hop_seconds,
        min_note_seconds=float(note_options.get("min_note_seconds", 0.08)),
        max_gap_seconds=float(note_options.get("max_gap_seconds", 0.055)),
    )
    links = link_words_to_notes(alignment, notes)
    diagnostics = {
        "pitch": pitch_diagnostics,
        "notes": {
            "algorithm_version": NOTE_SEGMENTATION_VERSION,
            "count": len(notes),
            "minimum_duration_seconds": float(note_options.get("min_note_seconds", 0.08)),
            "octave_corrections": [],
        },
        "word_note_mapping": {
            "algorithm_version": WORD_NOTE_MAPPING_VERSION,
            "link_count": len(links),
            "status": "available" if alignment is not None else "unavailable",
        },
    }
    return MelodyResult(
        timeline={"duration": duration, "timebase": "decoded-original-seconds", "source_offset_sec": 0.0},
        pitch_frames=frames,
        notes=notes,
        word_note_links=links,
        diagnostics=diagnostics,
        provenance=provenance,
    )
