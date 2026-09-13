"""Derived, word-scoped exercise notes. Never rewrite canonical analysis files."""
from __future__ import annotations

from dataclasses import asdict
import math
from typing import Any

from .melody import link_words_to_notes, word_id
from .models import AlignmentResult
from .studio_models import LearningInterval, LearningNote, LearningResult, NoteEvent, WordNoteLink

LEARNING_VERSION = "1"
MODES = ("light", "medium", "pro")
LEARNING_OPTIONS = {
    "confidence_floor": 0.45,
    "minimum_overlap_seconds": 0.03,
    "minimum_word_fraction": 0.25,
    "minimum_note_fraction": 0.15,
    "approximate_minimum_seconds": 0.04,
    "approximate_maximum_seconds": 2.5,
    "medium_minimum_group_seconds": 0.08,
    "medium_relative_improvement": 0.25,
    "medium_boundary_penalty": 0.15,
}


def _representative(events: list[tuple[NoteEvent, float, float]]) -> tuple[int, float]:
    weights = [(note.midi, (end - start) * max(0.0, min(1.0, note.confidence)))
               for note, start, end in events]
    pitches = sorted({pitch for pitch, _ in weights})
    # A duration/confidence-weighted medoid must be one of the observed pitches.
    pitch = min(pitches, key=lambda p: (sum(abs(p - q) * w for q, w in weights),
                                       -sum(w for q, w in weights if p == q), p))
    return pitch, sum(abs(pitch - q) * w for q, w in weights)


def _sequential_groups(events: list, mode: str, settings: dict) -> list[list]:
    if mode == "light" or len(events) < 2:
        return [events]
    _, single_loss = _representative(events)
    weight = sum((end - start) * note.confidence for note, start, end in events)
    best: tuple[float, int] | None = None
    for index in range(1, len(events)):
        left, right = events[:index], events[index:]
        if left[-1][0].midi == right[0][0].midi:
            continue
        if min(sum(end - start for _, start, end in side) for side in (left, right)) < settings["medium_minimum_group_seconds"]:
            continue
        lp, ll = _representative(left)
        rp, rl = _representative(right)
        if lp == rp:
            continue
        loss = ll + rl + weight * settings["medium_boundary_penalty"]
        if best is None or loss < best[0]:
            best = loss, index
    if best is not None and single_loss > 0 and best[0] < single_loss * (1 - settings["medium_relative_improvement"]):
        return [events[:best[1]], events[best[1]:]]
    return [events]


def _group(identifier: str, events: list, *, approximate: bool, pro: bool = False) -> LearningNote:
    pitch, _ = _representative(events)
    intervals: list[LearningInterval] = []
    for note, start, end in events:
        # Adjacent source events may share an exercise height; actual gaps remain.
        if intervals and abs(intervals[-1].end - start) < 1e-6:
            previous = intervals.pop()
            intervals.append(LearningInterval(previous.start, end, previous.source_note_ids + [note.id]))
        else:
            intervals.append(LearningInterval(start, end, [note.id]))
    total = sum(end - start for _, start, end in events)
    confidence = sum(note.confidence * (end - start) for note, start, end in events) / total
    return LearningNote(identifier, intervals[0].start, intervals[-1].end, pitch,
                        events[0][0].cents if pro else 0, confidence,
                        "stable-event" if pro else "word-exercise",
                        approximate or any(note.uncertain for note, _, _ in events),
                        intervals, list(dict.fromkeys(note.id for note, _, _ in events)))


def build_learning_result(
    mode: str,
    notes: list[NoteEvent],
    alignment: AlignmentResult | None,
    *,
    timeline: dict[str, Any],
    provenance: dict[str, Any],
    options: dict | None = None,
) -> LearningResult:
    if mode not in MODES:
        raise ValueError("Unknown learning mode")
    settings = {**LEARNING_OPTIONS, **(options or {})}
    if mode != "pro" and (alignment is None or not any(line.words for line in alignment.lines)):
        raise ValueError("Нет временной разметки текста. Доступен Pro; Light и Medium требуют привязки слов.")
    groups: list[LearningNote] = []
    links: list[WordNoteLink] = []
    words: list[dict] = []
    if mode == "pro":
        groups = [_group(note.id, [(note, note.start, note.end)], approximate=note.uncertain, pro=True) for note in notes]
        links = link_words_to_notes(alignment, notes)
    for line_index, line in enumerate(alignment.lines if alignment else []):
        for word_index, word in enumerate(line.words):
            identifier = word_id(line_index, word_index, word)
            timing_source = (word.timing or {}).get("source", word.alignment_source)
            approximate = timing_source != "manual" and (not word.aligned or timing_source in {"interpolated", "approximate_split", "unknown"})
            length = word.end - word.start
            valid = all(math.isfinite(t) for t in (word.start, word.end)) and 0 <= word.start < word.end <= timeline["duration"] + 1e-6
            anomalous = not valid or (approximate and not settings["approximate_minimum_seconds"] <= length <= settings["approximate_maximum_seconds"])
            entry = {**asdict(word), "id": identifier, "line_index": line_index,
                     "word_index": word_index, "approximate": approximate,
                     "status": "unavailable", "message": "Нота не определена"}
            if anomalous:
                entry.update(status="check_timing", message="Проверьте привязку слова")
            if mode == "pro":
                if not anomalous and any(link.word_id == identifier for link in links):
                    entry.update(status="approximate" if approximate else "available", message="Приблизительная привязка" if approximate else "")
                words.append(entry)
                continue
            events = []
            if not anomalous:
                for note in notes:
                    start, end = max(word.start, note.start), min(word.end, note.end)
                    overlap = end - start
                    if overlap <= 0 or note.confidence < settings["confidence_floor"]:
                        continue
                    if overlap + 1e-9 < min(settings["minimum_overlap_seconds"], length * settings["minimum_word_fraction"]):
                        continue
                    if overlap / length < settings["minimum_word_fraction"] and overlap / (note.end - note.start) < settings["minimum_note_fraction"]:
                        continue
                    events.append((note, start, end))
            if events:
                for group_index, events_group in enumerate(_sequential_groups(events, mode, settings)):
                    group = _group(f"{mode}-{identifier}-{group_index}", events_group, approximate=approximate)
                    groups.append(group)
                    for interval in group.intervals:
                        links.append(WordNoteLink(identifier, group.id, interval.start, interval.end,
                                                  "approximate" if group.uncertain else "confirmed"))
                entry.update(status="approximate" if approximate else "available",
                             message="Приблизительная привязка" if approximate else "")
            words.append(entry)
    groups.sort(key=lambda group: (group.start, group.end, group.id))
    return LearningResult(mode, timeline, groups, words, links,
        {"algorithm_version": LEARNING_VERSION, "parameters": settings,
         "count_unit": "acoustic-event" if mode == "pro" else "logical-exercise-group",
         "note_count": len(groups), "words_without_notes": sum(word["status"] in {"unavailable", "check_timing"} for word in words),
         "calibration": "synthetic-contracts; human acoustic acceptance pending"}, provenance)


def piano_events(result: LearningResult) -> list[NoteEvent]:
    """One piano attack per sounding interval; group count stays unchanged."""
    return [NoteEvent(f"{note.id}-{index}", interval.start, interval.end, note.midi,
                      note.cents, note.confidence, note.source, note.uncertain)
            for note in result.notes for index, interval in enumerate(note.intervals)]
