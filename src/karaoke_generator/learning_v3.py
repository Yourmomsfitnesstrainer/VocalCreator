"""V3 derives labels independently of the shared sounding event sequence."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .models import AlignmentResult
from .studio_models import NoteEvent
from .syllables import build_text_parts, canonical_words
from .lyric_recovery import apply_lyric_evidence

LEARNING_V3_VERSION = "3.0.1"
MODES = ("light", "medium", "pro")


def build_learning_result_v3(mode: str, notes: list[NoteEvent], alignment: AlignmentResult | None,
                             *, timeline: dict[str, Any], provenance: dict[str, Any],
                             canonical_text: str | None = None,
                             character_evidence: dict | list | None = None,
                             lyric_evidence: dict | None = None,
                             language: str | None = None, options: dict | None = None) -> dict:
    """Preserve every stable event, real gap, canonical token, and source range.

    V3 deliberately starts all modes from the same confirmed events; differences
    require independent calibration rather than an artificial per-word quota.
    Text overlaps become labels, never additional piano attacks or held gaps.
    """
    if mode not in MODES:
        raise ValueError("Неизвестный режим мелодии")
    language = language or (alignment.language if alignment else "unknown")
    text_source = "canonical-text" if canonical_text is not None else "alignment-reconstructed"
    if canonical_text is None:
        canonical_text = "\n".join(line.text for line in alignment.lines) if alignment else ""
    words = canonical_words(canonical_text, alignment, float(timeline["duration"]))
    words = apply_lyric_evidence(words, lyric_evidence)
    if lyric_evidence and lyric_evidence.get("words"):
        saved = character_evidence.get("words", []) if isinstance(character_evidence, dict) else character_evidence or []
        character_evidence = {"words": [*saved, *lyric_evidence["words"]]}
    parts, diagnostics = build_text_parts(words, character_evidence, language, options=options)
    note_dicts = [{**asdict(note), "source": "stable-event-v3",
                   "intervals": [{"start": note.start, "end": note.end, "source_note_ids": [note.id]}],
                   "source_note_ids": [note.id], "labels": []}
                  for note in sorted(notes, key=lambda item: (item.start, item.end, item.id))]
    links, word_links, linked_words, used_parts = [], [], set(), set()
    word_by_id = {word["id"]: word for word in words}
    for note in note_dicts:
        for part in parts:
            if part["start"] is None or part["end"] is None:
                continue
            # Approximate text can be labelled as such; no notes are invented.
            start, end = max(note["start"], part["start"]), min(note["end"], part["end"])
            if end <= start:
                continue
            word = word_by_id[part["word_id"]]
            if word["status"] == "check_timing":
                continue  # invalid original intervals need acoustic repair, not deletion of the lyric
            approximate = part["status"] in {"fallback", "approximate"} or word["approximate"] or note["uncertain"]
            label = {"note_id": note["id"], "part_id": part["id"], "word_id": part["word_id"],
                     "text": part["text"], "start": start, "end": end,
                     "continuation": part["id"] in used_parts,
                     "status": "approximate" if approximate else "confirmed",
                     "kind": part["kind"], "message": part["message"]}
            links.append(label)
            note["labels"].append(label)
            used_parts.add(part["id"])
            linked_words.add(word["id"])
        # Word/CTC and F0 boundaries measure different things. A sung note must
        # keep its label at the visible part of the bar, including its tail.
        if note["labels"]:
            note["labels"].sort(key=lambda label: (label["start"], label["end"]))
            for label, boundary, value in ((note["labels"][0], "start", note["start"]),
                                            (note["labels"][-1], "end", note["end"])):
                if label[boundary] != value:
                    label["source_"+boundary] = label[boundary]
                    label["status"] = "approximate"
                    label["message"] = "Подпись продлена до границы вокальной ноты"
            note["labels"][0]["start"] = note["start"]
            note["labels"][-1]["end"] = note["end"]
        else:
            usable = [part for part in parts if part["start"] is not None and part["end"] is not None
                      and word_by_id[part["word_id"]]["status"] != "check_timing"]
            if usable:
                nearest = min(usable, key=lambda part: max(part["start"]-note["end"], note["start"]-part["end"], 0))
                gap = max(nearest["start"]-note["end"], note["start"]-nearest["end"], 0)
                # Distant text is context, not a claim to have recognized that
                # word. Prefer preceding context until the next lyric starts.
                if gap > .35:
                    preceding = [part for part in usable if part["end"] <= note["start"]]
                    nearest = max(preceding, key=lambda part: part["end"]) if preceding else nearest
                word = word_by_id[nearest["word_id"]]
                context = gap > .35
                label = {"note_id": note["id"], "part_id": nearest["id"], "word_id": word["id"],
                         "text": word["text"] if context else nearest["text"],
                         "start": note["start"], "end": note["end"],
                         "continuation": nearest["id"] in used_parts,
                         "status": "context" if context else "approximate",
                         "kind": "lyric-context" if context else "boundary-extension",
                         "message": "Контекст лирики; слово на этом участке не подтверждено" if context else
                                    "Приблизительная граница слова и вокальной ноты"}
                note["labels"].append(label)
                links.append(label)
                linked_words.add(word["id"])
                used_parts.add(nearest["id"])
        # Merge only label overlaps for compatibility links, not sounding events.
        for word_id in dict.fromkeys(label["word_id"] for label in note["labels"]):
            matching = [label for label in note["labels"] if label["word_id"] == word_id]
            word_links.append({"word_id": word_id, "note_id": note["id"],
                               "start": min(label["start"] for label in matching),
                               "end": max(label["end"] for label in matching),
                               "status": "context" if any(label["status"] == "context" for label in matching) else
                                         "approximate" if any(label["status"] == "approximate" for label in matching) else "confirmed"})
        if not note["labels"]:
            note["text_status"] = "unavailable"
            note["text_message"] = "Текст для ноты не определён"
        else:
            note["text_status"] = "available"
            note["text_message"] = ""
    for word in words:
        word["part_ids"] = [part["id"] for part in parts if part["word_id"] == word["id"]]
        word["note_ids"] = [link["note_id"] for link in word_links if link["word_id"] == word["id"]]
        if word["id"] not in linked_words and word["status"] not in {"unavailable", "check_timing"}:
            word.update(status="unavailable", message="Нота не определена")
    return {"schema_version": 3, "mode": mode, "timeline": dict(timeline),
            "canonical_text": canonical_text, "notes": note_dicts, "words": words,
            "text_parts": parts, "note_text_links": links, "word_note_links": word_links,
            "diagnostics": {"algorithm_version": LEARNING_V3_VERSION,
                            "count_unit": "acoustic-event", "note_count": len(note_dicts),
                            "words_without_notes": sum(not word["note_ids"] for word in words),
                            "notes_without_text": sum(not note["labels"] for note in note_dicts),
                            "notes_with_lyric_context": sum(any(label["status"] == "context" for label in note["labels"]) for note in note_dicts),
                            "lyric_recovery": {key: value for key, value in (lyric_evidence or {}).items()
                                               if key not in {"words", "spec", "payload_sha256"}},
                            "parts": diagnostics,
                            "mode_policy": "preserve-all-stable-events; simplification calibration pending",
                            "calibration": "synthetic contracts and local CTC evidence; independent musical acceptance pending"},
            "provenance": {**provenance, "canonical_text_source": text_source,
                           "simplification_version": LEARNING_V3_VERSION}}


def piano_events_v3(data: dict) -> list[NoteEvent]:
    """One attack per sounding event; labels cannot change the audio schedule."""
    return [NoteEvent(note["id"], interval["start"], interval["end"], note["midi"], note["cents"],
                      note["confidence"], note["source"], note["uncertain"])
            for note in data["notes"] for interval in note["intervals"]]
