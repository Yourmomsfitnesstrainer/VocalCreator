"""Pure syllable proposals and schema-1 invariants over immutable acoustic notes.

No inference, files, note synthesis or editor state lives here. Orthographic
candidates always remain proposals. CTC onsets may locate a candidate; pitch
changes and the number of notes never determine how many syllables exist.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import asdict, is_dataclass
import math
from typing import Any

from .lyrics import comparison_key
from .lyric_recovery import apply_lyric_evidence
from .models import AlignmentResult
from .syllables import PARTS_OPTIONS, build_text_parts, canonical_words, proposed_spans

SCORE_VERSION = "syllable-score-1"
MAPPER_VERSION = "ctc-overlap-2"
SCORE_OPTIONS = {"minimum_link_seconds": .005, "minimum_candidate_seconds": .02,
                 "weak_ctc_minimum_score": .02, "maximum_candidate_word_seconds": 6.0,
                 "same_occurrence_endpoint_tolerance_seconds": .35, "maximum_unvoiced_boundary_gap_seconds": .12}
_STATUSES = {"suggested", "approximate", "unavailable"}
_ORIGINS = {"automatic", "manual", "legacy"}
_EPS = 1e-7
_DYNAMIC_UNRESOLVED = {"no_note_link", "timing_unavailable"}


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _note_dicts(notes: list) -> list[dict]:
    return [asdict(note) if is_dataclass(note) else deepcopy(note) for note in notes]


def _acoustic_intervals(note: dict) -> list[dict]:
    return note.get("intervals", [note])


def _merged(intervals: list[dict], support: str) -> list[dict]:
    result = []
    for interval in sorted(intervals, key=lambda item: (item["start"], item["end"])):
        start, end = interval["start"], interval["end"]
        if result and start <= result[-1]["end"] + _EPS:
            result[-1]["end"] = max(result[-1]["end"], end)
        else:
            result.append({"start": start, "end": end, "support": support})
    return result


def normalize_score(document: dict) -> dict:
    """Rebuild dependent timing/continuations without mutating input or notes.

    Explicit unpitched support survives link removal. Stale generated intervals
    never survive their last supporting link. Structural and overlap checks are
    the validator's responsibility, so normalization cannot silently fix them.
    """
    result = deepcopy(document)
    links = result.get("note_links", [])
    by_unit: dict[str, list] = {}
    for link in links:
        by_unit.setdefault(link.get("unit_id"), []).append(link)
    unresolved = [item for item in result.get("unresolved", [])
                  if item.get("reason_code", item.get("code")) not in _DYNAMIC_UNRESOLVED]
    for unit in result.get("units", []):
        own = sorted(by_unit.get(unit["unit_id"], []),
                     key=lambda item: (item["start"], item["end"], item["link_id"]))
        for index, link in enumerate(own):
            link["continuation"] = index != 0
        intervals = _merged(own, "note_link")
        intervals += _merged([item for item in unit.get("intervals", [])
                              if item.get("support") == "unpitched"], "unpitched")
        intervals.sort(key=lambda item: (item["start"], item["end"], item["support"]))
        unit["intervals"] = intervals
        unit["start"] = min((item["start"] for item in intervals), default=None)
        unit["end"] = max((item["end"] for item in intervals), default=None)
        if not own:
            code = "no_note_link" if intervals else "timing_unavailable"
            unresolved.append({"unit_id": unit["unit_id"], "occurrence_id": unit["occurrence_id"],
                               "reason_code": code, "text": unit["text"], "candidates": []})
        if not intervals:
            unit["timing_status"] = "unavailable"
    result["note_links"] = sorted(links, key=lambda item: (item["start"], item["end"], item["link_id"]))
    result["unresolved"] = unresolved
    return result


def _ranges(value: Any, text: str, label: str) -> str:
    if not isinstance(value, list):
        raise ValueError(f"{label}: source_ranges must be an array")
    previous = 0
    pieces = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError(f"{label}: invalid source range")
        start, end = item.get("start"), item.get("end")
        if (type(start) is not int or type(end) is not int or
                not 0 <= start < end <= len(text) or start < previous):
            raise ValueError(f"{label}: invalid Unicode source range")
        previous = end
        pieces.append(text[start:end])
    return "".join(pieces)


def _bounds(item: dict, duration: float, label: str) -> tuple[float, float]:
    a, b = _number(item.get("start")), _number(item.get("end"))
    if a is None or b is None or not 0 <= a < b <= duration + _EPS:
        raise ValueError(f"{label}: invalid time interval")
    return a, b


def _contained(a: float, b: float, intervals: list[dict]) -> bool:
    return any(i["start"] <= a + _EPS and b <= i["end"] + _EPS for i in intervals)


def _validate_score(document: dict, notes: list, *, expected_job_id: str | None = None,
                    expected_source: dict | None = None,
                    expected_base_analysis_key: str | None = None) -> None:
    """Reject malformed or inconsistent proposals/revisions with ValueError.

    Call on the submitted payload before saving: normalization is an editor
    operation, not permission to silently repair corrupt or overlapping input.
    """
    if not isinstance(document, dict) or type(document.get("schema_version")) is not int or document["schema_version"] != 1:
        raise ValueError("Unsupported syllable-score schema")
    for field in ("job_id", "base_analysis_key"):
        if not isinstance(document.get(field), str) or not document[field]:
            raise ValueError(f"Missing {field}")
    if expected_job_id is not None and document["job_id"] != expected_job_id:
        raise ValueError("Syllable score belongs to another job")
    if expected_base_analysis_key is not None and document["base_analysis_key"] != expected_base_analysis_key:
        raise ValueError("Syllable score belongs to another analysis")
    if type(document.get("revision")) is not int or document["revision"] < 0:
        raise ValueError("Invalid revision")
    source = document.get("source")
    if not isinstance(source, dict):
        raise ValueError("Missing source identity")
    duration = _number(source.get("duration"))
    if duration is None or duration <= 0 or not isinstance(source.get("language"), str):
        raise ValueError("Invalid source duration/language")
    if expected_source is not None and source != expected_source:
        raise ValueError("Syllable score source identity changed")
    if not isinstance(document.get("canonical_text"), str) or not isinstance(document.get("provenance"), dict):
        raise ValueError("Invalid canonical text/provenance")
    text = document["canonical_text"]
    for field in ("units", "occurrences", "note_links", "unresolved"):
        if not isinstance(document.get(field), list) or any(not isinstance(i, dict) for i in document[field]):
            raise ValueError(f"Invalid {field}")
    ids: set[str] = set()
    def register(value: Any) -> None:
        if not isinstance(value, str) or not value or value in ids:
            raise ValueError("Duplicate or invalid entity ID")
        ids.add(value)
    note_by_id = {}
    for note in _note_dicts(notes):
        if not isinstance(note.get("id"), str) or note["id"] in note_by_id:
            raise ValueError("Invalid acoustic source note identity")
        _bounds(note, duration, "source note")
        previous_end = -1.
        for interval in _acoustic_intervals(note):
            a, b = _bounds(interval, duration, "source note interval")
            if a < previous_end - _EPS or not note["start"] - _EPS <= a < b <= note["end"] + _EPS:
                raise ValueError("Invalid source acoustic intervals")
            previous_end = b
        note_by_id[note["id"]] = note
    occurrences = {}
    for occurrence in document["occurrences"]:
        identifier = occurrence.get("occurrence_id")
        register(identifier)
        occurrences[identifier] = occurrence
        original = _ranges(occurrence.get("source_ranges"), text, "occurrence")
        if occurrence.get("source_text") != original:
            raise ValueError("Occurrence source text differs from canonical text")
        if (not isinstance(occurrence.get("source_word_id"), str) or not occurrence["source_word_id"]):
            if occurrence.get("origin") != "manual" or occurrence.get("source_word_id") is not None:
                raise ValueError("Invalid source_word_id")
        if not isinstance(occurrence.get("unit_ids"), list):
            raise ValueError("Invalid occurrence unit IDs")
    units, active = {}, []
    for unit in document["units"]:
        identifier = unit.get("unit_id")
        register(identifier)
        units[identifier] = unit
        if unit.get("occurrence_id") not in occurrences:
            raise ValueError("Unknown unit occurrence")
        if unit.get("kind") not in {"syllable", "word_fallback"} or not isinstance(unit.get("text"), str) or not unit["text"].strip():
            raise ValueError("Invalid unit kind/text")
        if type(unit.get("order")) is not int or unit["order"] < 0:
            raise ValueError("Invalid unit order")
        if unit.get("origin") not in _ORIGINS or any(unit.get(k) not in _STATUSES for k in ("segmentation_status", "timing_status")):
            raise ValueError("Invalid unit origin/status")
        if not isinstance(unit.get("reason_codes"), list) or any(not isinstance(s, str) for s in unit["reason_codes"]):
            raise ValueError("Invalid unit reason codes")
        original = _ranges(unit.get("source_ranges"), text, "unit")
        occurrence = occurrences[unit["occurrence_id"]]
        if any(not _contained(r["start"], r["end"], occurrence["source_ranges"]) for r in unit["source_ranges"]):
            raise ValueError("Unit source range leaves its occurrence")
        overridden = unit.get("manual_override", False)
        if type(overridden) is not bool:
            raise ValueError("Invalid manual_override")
        if original != unit["text"] or not unit["source_ranges"]:
            if unit["origin"] != "manual" or not overridden or unit.get("source_text") != original:
                raise ValueError("Manual text requires manual_override and original source_text")
        elif overridden and (unit["origin"] != "manual" or unit.get("source_text") != original):
            raise ValueError("Invalid manual source provenance")
        intervals = unit.get("intervals")
        if not isinstance(intervals, list) or any(not isinstance(i, dict) for i in intervals):
            raise ValueError("Invalid active intervals")
        previous_end = -1.
        for interval in intervals:
            a, b = _bounds(interval, duration, "unit interval")
            if a < previous_end - _EPS or interval.get("support") not in {"note_link", "unpitched"}:
                raise ValueError("Unordered or overlapping unit intervals")
            if interval["support"] == "unpitched" and unit["origin"] != "manual":
                raise ValueError("Automatic unit cannot invent unpitched timing")
            previous_end = b
            active.append((a, b, identifier))
        if intervals:
            _bounds(unit, duration, "unit")
            if unit.get("start") != intervals[0]["start"] or unit.get("end") != intervals[-1]["end"]:
                raise ValueError("Unit bounds do not match active intervals")
            if unit["timing_status"] == "unavailable":
                raise ValueError("Placed unit cannot have unavailable timing")
        elif unit.get("start") is not None or unit.get("end") is not None or unit["timing_status"] != "unavailable":
            raise ValueError("Unplaced unit must have null bounds and unavailable timing")
    previous = None
    for a, b, identifier in sorted(active):
        if previous is not None and a < previous[1] - _EPS and identifier != previous[2]:
            raise ValueError("Leading-voice units have overlapping active intervals")
        if previous is None or b > previous[1]:
            previous = (a, b, identifier)
    for occurrence in occurrences.values():
        listed = occurrence["unit_ids"]
        expected = sorted((u for u in units.values() if u["occurrence_id"] == occurrence["occurrence_id"]),
                          key=lambda unit: unit["order"])
        if listed != [u["unit_id"] for u in expected] or len({u["order"] for u in expected}) != len(expected):
            raise ValueError("Occurrence unit order/references are inconsistent")
        # Text order follows first active onsets. A manual reassignment may
        # insert a different unit inside a gap of a melisma while its original
        # unit retains the regions before and after. Active overlap remains
        # forbidden globally; the bounding envelope is not an active interval.
        previous_start = -1.
        for unit in expected:
            if unit["start"] is not None:
                if unit["start"] < previous_start - _EPS:
                    raise ValueError("Units run backwards inside an occurrence")
                previous_start = unit["start"]
        if expected and all(u["origin"] == "automatic" for u in expected):
            ranges = [r for u in expected for r in u["source_ranges"]]
            if _ranges(ranges, text, "automatic occurrence") != occurrence["source_text"]:
                raise ValueError("Automatic syllables do not reconstruct their source occurrence")
            if any(u["kind"] == "word_fallback" for u in expected) and len(expected) != 1:
                raise ValueError("A fallback is one whole word")
    by_unit = {}
    for link in document["note_links"]:
        register(link.get("link_id"))
        unit, note = units.get(link.get("unit_id")), note_by_id.get(link.get("source_note_id"))
        if unit is None or note is None:
            raise ValueError("Link refers to unknown unit or acoustic note")
        a, b = _bounds(link, duration, "note link")
        if not _contained(a, b, _acoustic_intervals(note)):
            raise ValueError("Link leaves its acoustic note")
        if not _contained(a, b, [i for i in unit["intervals"] if i["support"] == "note_link"]):
            raise ValueError("Link leaves its supported unit interval")
        if link.get("origin") not in _ORIGINS or type(link.get("continuation")) is not bool:
            raise ValueError("Invalid link origin/continuation")
        by_unit.setdefault(unit["unit_id"], []).append(link)
    for unit in units.values():
        own = sorted(by_unit.get(unit["unit_id"], []), key=lambda i: (i["start"], i["end"], i["link_id"]))
        if any(link["continuation"] != (index != 0) for index, link in enumerate(own)):
            raise ValueError("Invalid melisma continuation order")
        expected = _merged(own, "note_link")
        actual = [i for i in unit["intervals"] if i["support"] == "note_link"]
        if expected != actual:
            raise ValueError("Active note intervals differ from surviving links")
    for item in document["unresolved"]:
        for name, collection in (("unit_id", units), ("occurrence_id", occurrences)):
            if name in item and item[name] not in collection:
                raise ValueError("Unresolved item refers to unknown entity")
        if "link_id" in item and item["link_id"] not in {l["link_id"] for l in document["note_links"]}:
            raise ValueError("Unresolved item refers to unknown link")
        if not isinstance(item.get("reason_code", item.get("code")), str):
            raise ValueError("Unresolved item lacks reason")
    for unit in units.values():
        if not by_unit.get(unit["unit_id"]) and not any(i.get("unit_id") == unit["unit_id"] for i in document["unresolved"]):
            raise ValueError("Unlinked text must remain in unresolved items")


def validate_score(document: dict, notes: list, *, expected_job_id: str | None = None,
                   expected_source: dict | None = None,
                   expected_base_analysis_key: str | None = None) -> None:
    """Validate an untrusted JSON payload; malformed shapes fail with ValueError."""
    try:
        _validate_score(document, notes, expected_job_id=expected_job_id,
                        expected_source=expected_source,
                        expected_base_analysis_key=expected_base_analysis_key)
    except (TypeError, KeyError, IndexError, AttributeError, OverflowError) as exc:
        raise ValueError("Malformed syllable-score payload") from exc


def _supports_boundary_gap(start: float, end: float, notes: list[dict], maximum_gap: float) -> bool:
    """A sparse CTC blank can be a held vowel only with local acoustic support."""
    cursor = start
    for interval in sorted((i for note in notes for i in _acoustic_intervals(note)), key=lambda i: (i["start"], i["end"])):
        if interval["end"] <= cursor or interval["start"] >= end:
            continue
        if interval["start"] - cursor > maximum_gap + _EPS:
            return False
        cursor = max(cursor, interval["end"])
        if cursor >= end:
            return True
    return end - cursor <= maximum_gap + _EPS


def _approximate_parts(word: dict, candidates: list[dict], language: str, options: dict,
                       notes: list[dict], word_window: tuple[float, float]) -> list[dict] | None:
    """Use weak but locally monotone CTC onsets, never equal note/letter division."""
    spans, nuclei = proposed_spans(word["text"], language)
    if (not nuclei or word["start"] is None or word["end"] is None or word.get("status") == "check_timing"
            or word["end"] - word["start"] > options["maximum_candidate_word_seconds"]):
        return None
    if len(spans) == 1:
        return [{"text": word["text"], "char_start": word["char_start"], "char_end": word["char_end"],
                 "start": word["start"], "end": word["end"], "reason": "approximate_word_timing",
                 "provenance": {"timing_method": "saved-word-boundaries", "linguistic_method": "orthographic-proposal"}}]
    choices = []
    for candidate in candidates:
        if (comparison_key(str(candidate.get("text", ""))) != comparison_key(word["text"])
                or candidate.get("id") is not None and candidate["id"] != word["id"]):
            continue
        a, b = _number(candidate.get("start")), _number(candidate.get("end"))
        if a is None or b is None or not 0 <= a < b or b-a > options["maximum_candidate_word_seconds"]:
            continue
        endpoint_error = max(abs(a-word["start"]), abs(b-word["end"]))
        same_occurrence = candidate.get("id") == word["id"]
        if same_occurrence:
            # Complete-lyric CTC identifies this occurrence explicitly. Strong
            # ASR word edges may refer to a spelling substitution; retain the
            # local CTC interval as an approximate candidate, within neighbours.
            if (endpoint_error > options["same_occurrence_endpoint_tolerance_seconds"]
                    or a < word_window[0] - .001 or b > word_window[1] + .001):
                continue
            candidate_start, candidate_end = a, b
        else:
            if endpoint_error > .08:
                continue
            candidate_start, candidate_end = word["start"], word["end"]
        offsets = [i for i, char in enumerate(word["text"]) if comparison_key(char)]
        chars = [c for c in candidate.get("characters", []) if comparison_key(str(c.get("char", "")))]
        if len(chars) != len(offsets) or any(comparison_key(word["text"][i]) != comparison_key(str(c.get("char", ""))) for i, c in zip(offsets, chars)):
            continue
        mapped = dict(zip(offsets, chars))
        previous_end, valid = candidate_start - .001, True
        for char in chars:
            start, end = _number(char.get("start")), _number(char.get("end"))
            if start is None or end is None or not candidate_start-.001 <= start < end <= candidate_end+.001 or start < previous_end-.001:
                valid = False
                break
            previous_end = end
        if not valid:
            continue
        scores = [_number(c.get("score")) for c in chars]
        scores = [s for s in scores if s is not None and 0 <= s <= 1]
        if not scores or sum(scores)/len(scores) < options["weak_ctc_minimum_score"]:
            continue
        boundaries = [candidate_start]
        boundary_gaps = []
        for offset, _ in spans[1:]:
            right = next((i for i in offsets if i >= offset), None)
            left = next((i for i in reversed(offsets) if i < offset), None)
            if right is None or left is None:
                valid = False
                break
            gap_start, gap_end = mapped[left]["end"], mapped[right]["start"]
            if gap_end - gap_start > PARTS_OPTIONS["maximum_boundary_gap_seconds"]:
                supported = _supports_boundary_gap(gap_start, gap_end, notes, options["maximum_unvoiced_boundary_gap_seconds"])
                # An exact occurrence's observed character onset remains a
                # useful approximate boundary even where F0 is absent. Sparse
                # CTC emission is not itself a silence detector. The mapper
                # clips each resulting unit to real notes; no gap is filled.
                if not supported and not same_occurrence:
                    valid = False
                    break
                boundary_gaps.append({"start": gap_start, "end": gap_end, "acoustic_note_support": supported})
            boundaries.append(float(mapped[right]["start"]))
        boundaries.append(candidate_end)
        if not valid or any(b-a < options["minimum_candidate_seconds"] for a, b in zip(boundaries, boundaries[1:])):
            continue
        choices.append((endpoint_error, candidate, spans, boundaries, same_occurrence, boundary_gaps))
    if not choices:
        return None
    _, candidate, spans, boundaries, same_occurrence, boundary_gaps = min(choices, key=lambda item: item[0])
    return [{"text": word["text"][a:b], "char_start": word["char_start"]+a, "char_end": word["char_start"]+b,
             "start": start, "end": end, "reason": "local_occurrence_ctc_proposal" if same_occurrence else "weak_ctc_boundary_proposal",
             "reason_codes": ["sparse_ctc_emission_without_pitch_support"] if any(not gap["acoustic_note_support"] for gap in boundary_gaps) else [],
             "provenance": {"timing_method": "ctc-character-onset", "linguistic_method": "orthographic-proposal",
                            "evidence_sha256": candidate.get("evidence_sha256"), "model": candidate.get("model"),
                            "evidence_occurrence_id": candidate.get("id"),
                            "saved_word_interval": {"start": word["start"], "end": word["end"]},
                            "ctc_boundary_gaps": boundary_gaps}}
            for (a, b), start, end in zip(spans, boundaries, boundaries[1:])]


def build_score(canonical_text: str, alignment: AlignmentResult | dict | None, notes: list, *,
                job_id: str, source: dict, base_analysis_key: str,
                character_evidence: dict | list | None = None, lyric_evidence: dict | None = None,
                options: dict | None = None) -> dict:
    """Construct a deterministic automatic candidate; inputs stay untouched."""
    settings = {**SCORE_OPTIONS, **(options or {})}
    if set(settings) != set(SCORE_OPTIONS) or any(_number(v) is None or v < 0 for v in settings.values()):
        raise ValueError("Invalid syllable score options")
    if isinstance(alignment, dict):
        alignment = AlignmentResult.from_dict(alignment)
    duration = float(source["duration"])
    language = source.get("language") or (alignment.language if alignment else "unknown")
    words = apply_lyric_evidence(canonical_words(canonical_text, alignment, duration), lyric_evidence)
    candidates = character_evidence.get("words", []) if isinstance(character_evidence, dict) else character_evidence or []
    candidates = [*candidates, *(lyric_evidence or {}).get("words", [])]
    # Saved ASR characters use local text/endpoints; complete-lyric CTC also
    # carries an exact occurrence ID. Never let one repeat consume another's
    # evidence just because their spelling and erroneous times happen to agree.
    part_by_word = {}
    _, part_diagnostics = build_text_parts([], [], language)
    part_counts = Counter()
    for word in words:
        eligible = [candidate for candidate in candidates
                    if candidate.get("id") is None or candidate["id"] == word["id"]]
        word_parts, diagnostics = build_text_parts([word], eligible, language)
        part_by_word[word["id"]] = word_parts
        for field in ("total_words", "words_with_parts", "single_part_words", "whole_word_fallback"):
            part_diagnostics[field] += diagnostics[field]
        part_counts.update(diagnostics["fallback_reasons"])
    part_diagnostics["whole_word_fallback_fraction"] = part_diagnostics["whole_word_fallback"] / len(words) if words else 0
    part_diagnostics["fallback_reasons"] = dict(part_counts)
    note_dicts = sorted(_note_dicts(notes), key=lambda n: (n["start"], n["end"], n["id"]))
    document = {"schema_version": 1, "job_id": job_id, "source": deepcopy(source),
                "base_analysis_key": base_analysis_key, "revision": 0, "canonical_text": canonical_text,
                "occurrences": [], "units": [], "note_links": [], "unresolved": [],
                "provenance": {"builder_version": SCORE_VERSION, "mapper_version": MAPPER_VERSION,
                               "parameters": settings, "parts_diagnostics": part_diagnostics,
                               "character_evidence": deepcopy(character_evidence.get("diagnostics", {})) if isinstance(character_evidence, dict) else {},
                               "lyric_evidence_key": (lyric_evidence or {}).get("cache_key"),
                               "linguistic_accuracy": "orthographic proposals; human acceptance pending",
                               "inference_performed": False}}
    occupied = []
    for word_index, word in enumerate(words):
        occurrence_id = "occ-" + word["id"]
        occurrence = {"occurrence_id": occurrence_id, "source_word_id": word["id"], "source_text": word["text"],
                      "source_ranges": [{"start": word["char_start"], "end": word["char_end"]}], "unit_ids": []}
        document["occurrences"].append(occurrence)
        selected = part_by_word[word["id"]]
        if selected[0]["kind"] == "whole-word-fallback":
            previous = next((w for w in reversed(words[:word_index]) if w["end"] is not None), None)
            following = next((w for w in words[word_index+1:] if w["start"] is not None), None)
            window = (previous["end"] if previous else 0., following["start"] if following else duration)
            approximate = _approximate_parts(word, candidates, language, settings, note_dicts, window)
            if approximate:
                selected = approximate
        for order, part in enumerate(selected):
            unit_id = f"unit-{word['id']}-{order:03d}"
            occurrence["unit_ids"].append(unit_id)
            fallback = part.get("kind") == "whole-word-fallback"
            reason = part.get("reason")
            unit = {"unit_id": unit_id, "occurrence_id": occurrence_id,
                    "kind": "word_fallback" if fallback else "syllable", "text": part["text"], "order": order,
                    "source_ranges": [{"start": part["char_start"], "end": part["char_end"]}],
                    "source_text": part["text"], "manual_override": False,
                    "start": None, "end": None, "intervals": [], "origin": "automatic",
                    "segmentation_status": "unavailable" if fallback else "approximate" if len(selected) > 1 else "suggested",
                    "timing_status": "approximate" if fallback or reason or word["approximate"] or len(selected) > 1 else "suggested",
                    "reason_codes": ([reason] if reason else (["orthographic_syllable_proposal"] if len(selected) > 1 else [])) + part.get("reason_codes", []),
                    "provenance": {**deepcopy(part.get("provenance", {})),
                                   "timing_candidate": {"start": part["start"], "end": part["end"]}}}
            document["units"].append(unit)
            if fallback:
                document["unresolved"].append({"unit_id": unit_id, "occurrence_id": occurrence_id,
                                               "reason_code": reason or "segmentation_unavailable", "text": unit["text"], "candidates": []})
            if part["start"] is None or part["end"] is None or word.get("status") == "check_timing":
                continue
            proposals = []
            for note in note_dicts:
                for interval in _acoustic_intervals(note):
                    a, b = max(part["start"], interval["start"]), min(part["end"], interval["end"])
                    if b-a >= settings["minimum_link_seconds"]:
                        proposals.append({"link_id": f"link-{unit_id}-{len(proposals):04d}", "unit_id": unit_id,
                                          "source_note_id": note["id"], "start": a, "end": b,
                                          "continuation": bool(proposals), "origin": "automatic"})
            # Competing leading-voice candidates never enter effective links.
            conflicting = [p for p in proposals if any(p["start"] < b-_EPS and p["end"] > a+_EPS for a, b in occupied)]
            if conflicting:
                document["unresolved"].append({"unit_id": unit_id, "occurrence_id": occurrence_id,
                                               "reason_code": "overlapping_lead_candidates", "text": unit["text"],
                                               "candidates": deepcopy(proposals)})
                unit["reason_codes"].append("overlapping_lead_candidates")
                continue
            document["note_links"].extend(proposals)
            occupied.extend((p["start"], p["end"]) for p in proposals)
    document = normalize_score(document)
    counts = Counter(unit["kind"] for unit in document["units"])
    document["provenance"]["diagnostics"] = {
        "language": language, "occurrences": len(words), "syllables": counts["syllable"],
        "word_fallback": counts["word_fallback"], "note_links": len(document["note_links"]),
        "unplaced_units": sum(not u["intervals"] for u in document["units"]),
        "fallback_fraction": counts["word_fallback"] / len(words) if words else 0,
        "fallback_reasons": dict(Counter(r for u in document["units"] if u["kind"] == "word_fallback" for r in u["reason_codes"])),
        "human_accepted": False}
    validate_score(document, note_dicts, expected_job_id=job_id, expected_source=source,
                   expected_base_analysis_key=base_analysis_key)
    return document
