"""Local, conservative text-part timing; canonical text is never rewritten.

Orthographic vowel nuclei propose *pronounced parts*, not verified phonemes.
Only saved/local CTC character evidence may supply interior acoustic boundaries.
Scores are model scores and are deliberately not called calibrated probabilities.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from difflib import SequenceMatcher
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any

from .lyrics import comparison_key, normalize_for_alignment
from .models import AlignmentResult

PARTS_VERSION = "grapheme-ctc-1"
PARTS_OPTIONS = {"minimum_mean_ctc_score": 0.35, "minimum_nucleus_ctc_score": 0.20,
                 "maximum_boundary_gap_seconds": 0.12, "minimum_part_seconds": 0.02,
                 "maximum_word_seconds": 6.0, "evidence_endpoint_tolerance_seconds": 0.04}
_APPROXIMATE = {"interpolated", "approximate_split", "unknown"}
_EN_ONSETS = {"bl", "br", "ch", "cl", "cr", "dr", "fl", "fr", "gl", "gr", "pl", "pr",
              "sc", "sh", "sk", "sl", "sm", "sn", "sp", "st", "sw", "th", "tr", "tw", "wh",
              "scr", "shr", "spl", "spr", "squ", "str", "thr"}


def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def canonical_words(canonical_text: str | None, alignment: AlignmentResult | None,
                    duration: float) -> list[dict]:
    """Keep every whitespace-delimited occurrence, including punctuation/repeats.

    Offsets index the exact supplied Unicode string. Exact monotonic sequence
    matching attaches existing times; insertions cannot consume another repeat.
    Missing/invalid intervals remain null instead of invented evenly split times.
    """
    if canonical_text is None:
        canonical_text = "\n".join(line.text for line in alignment.lines) if alignment else ""
    words, line_index, word_index, previous_end = [], 0, 0, 0
    for match in re.finditer(r"\S+", canonical_text):
        newlines = canonical_text[previous_end:match.start()].count("\n")
        if newlines:
            line_index += newlines
            word_index = 0
        value = match.group()
        digest = hashlib.sha256(value.encode()).hexdigest()[:10]
        words.append({"id": f"v3-l{line_index:04d}-w{word_index:04d}-{digest}",
                      "text": value, "normalized": normalize_for_alignment(value),
                      "line_index": line_index, "word_index": word_index,
                      "char_start": match.start(), "char_end": match.end(),
                      "start": None, "end": None, "confidence": None, "aligned": False,
                      "alignment_source": "unavailable", "timing": None, "approximate": True,
                      "status": "unavailable", "message": "Время слова не определено"})
        word_index += 1
        previous_end = match.end()
    aligned = [word for line in alignment.lines for word in line.words] if alignment else []
    matching = SequenceMatcher(a=[comparison_key(word["text"]) for word in words],
                               b=[comparison_key(word.text) for word in aligned], autojunk=False)
    for block in matching.get_matching_blocks():
        for offset in range(block.size):
            entry, original = words[block.a + offset], aligned[block.b + offset]
            # Punctuation-only tokens have no acoustic pronunciation to match.
            if not comparison_key(entry["text"]):
                continue
            start, end = _number(original.start), _number(original.end)
            source = (original.timing or {}).get("source", original.alignment_source)
            approximate = source != "manual" and (not original.aligned or source in _APPROXIMATE)
            entry.update(confidence=original.confidence, aligned=original.aligned,
                         alignment_source=original.alignment_source, timing=original.timing,
                         approximate=approximate)
            if start is None or end is None or not 0 <= start < end <= duration + 1e-6:
                entry.update(approximate=True, status="check_timing", message="Проверьте привязку слова")
                continue
            entry.update(start=start, end=end, status="approximate" if approximate else "available",
                         message="Приблизительная привязка" if approximate else "")
            if approximate and not 0.04 <= end-start <= 2.5:
                entry.update(status="check_timing", message="Проверьте привязку слова")
    return words


def load_character_evidence(result_dir: Path) -> dict:
    """Read saved character alignment, without inference or cache mutation.

    A linked ASR cache must match this result's text and actual saved vocal hash.
    Individual candidate text and endpoints are checked again when assigning a
    word, so obsolete evidence cannot silently override edited canonical times.
    """
    directory = Path(result_dir) / "work" / "timing-cache"
    words, recognized, sources, errors = [], [], [], []
    try:
        text_hash = json.loads((Path(result_dir) / "alignment.json").read_text(encoding="utf-8")).get("source_text_sha256")
    except (OSError, ValueError, TypeError, AttributeError):
        text_hash = None
    vocal_hash = None
    try:
        digest = hashlib.sha256()
        with (Path(result_dir) / "vocals.wav").open("rb") as audio:
            while chunk := audio.read(1024 * 1024):
                digest.update(chunk)
        vocal_hash = digest.hexdigest()
    except OSError:
        pass
    for path in sorted(directory.glob("refined-*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or not isinstance(raw.get("words"), list):
                raise ValueError("invalid character cache")
            asr_key = (raw.get("spec") or {}).get("asr_key")
            if not text_hash or not vocal_hash:
                raise ValueError("canonical text or saved vocal unavailable for provenance verification")
            # Hashes are cache keys, never paths supplied by an input file.
            if not re.fullmatch(r"[a-f0-9]{64}", str(asr_key)):
                raise ValueError("invalid or missing ASR cache key")
            asr = json.loads((directory / f"asr-{asr_key}.json").read_text(encoding="utf-8"))
            if (asr.get("spec") or {}).get("text_sha256") != text_hash:
                raise ValueError("character cache belongs to different canonical text")
            if (asr.get("spec") or {}).get("audio_sha256") != vocal_hash:
                raise ValueError("character cache belongs to different vocal audio")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            for word in raw["words"]:
                if isinstance(word, dict):
                    recognized.append({**word, "evidence_source": path.name, "evidence_sha256": digest})
                if isinstance(word, dict) and word.get("characters") and (word.get("timing") or {}).get("source") == "refined":
                    words.append({**word, "evidence_source": path.name,
                                  "evidence_sha256": digest,
                                  "model": (raw.get("spec") or {}).get("model")})
            sources.append({"name": path.name, "sha256": digest})
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            errors.append({"name": path.name, "reason": str(exc)})
    return {"words": words, "recognized_words": recognized, "diagnostics": {"status": "available" if words else "unavailable",
            "source": "saved-ctc-characters", "sources": sources, "errors": errors,
            "word_candidates": len(words), "inference_performed": False,
            "verified_vocal_sha256": vocal_hash, "canonical_text_sha256": text_hash}}


def _nuclei(text: str, language: str) -> list[tuple[int, int]]:
    """Spelling-based pronunciation candidates; no dictionary accuracy claim."""
    if language.startswith("ru"):
        return [(m.start(), m.end()) for m in re.finditer("[аеёиоуыэюя]", text)]
    if not language.startswith("en") or re.search(r"[^a-z]", text):
        return []
    nuclei = [(m.start(), m.end()) for m in re.finditer(r"[aeiou]+|(?<=[^aeiou])y", text)]
    if len(nuclei) > 1 and text.endswith("e") and nuclei[-1] == (len(text)-1, len(text)):
        # consonant + le retains its syllabic nucleus (table, little).
        if not (len(text) > 2 and text[-2] == "l" and text[-3] not in "aeiou"):
            nuclei.pop()
    if len(nuclei) > 1 and text.endswith("ed") and nuclei[-1][0] == len(text)-2 and text[-3:-2] not in {"t", "d"}:
        nuclei.pop()
    if len(nuclei) > 1 and text.endswith("es") and nuclei[-1][0] == len(text)-2:
        if not text[:-2].endswith(("s", "x", "z", "ch", "sh")):
            nuclei.pop()
    return nuclei


def proposed_spans(text: str, language: str) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """Return exact display ranges and vowel nuclei, never acoustic timestamps."""
    letter_offsets = [index for index, char in enumerate(text) if char.isalpha()]
    clean = "".join(text[index].lower() for index in letter_offsets)
    nuclei = _nuclei(clean, language)
    if len(nuclei) < 2:
        return [(0, len(text))], [(letter_offsets[a], letter_offsets[b-1]+1) for a, b in nuclei]
    starts = [0]
    for previous, current in zip(nuclei, nuclei[1:]):
        consonants = clean[previous[1]:current[0]]
        onset = 0 if not consonants else 1
        if language.startswith("en"):
            for length in (2, 3):
                if len(consonants) >= length and consonants[-length:] in _EN_ONSETS:
                    onset = length
        # coda/onset preference proposes a spelling part; CTC determines time.
        boundary = current[0] - onset
        starts.append(letter_offsets[boundary])
    return list(zip(starts, starts[1:] + [len(text)])), [
        (letter_offsets[a], letter_offsets[b-1]+1) for a, b in nuclei]


def _candidate(word: dict, evidence: list[dict], settings: dict) -> dict | None:
    if word["start"] is None or word["end"] is None:
        return None
    tolerance = settings["evidence_endpoint_tolerance_seconds"]
    matches = []
    for candidate in evidence:
        start, end = _number(candidate.get("start")), _number(candidate.get("end"))
        if comparison_key(str(candidate.get("text", ""))) != comparison_key(word["text"]):
            continue
        if start is None or end is None:
            continue
        error = abs(start-word["start"]) + abs(end-word["end"])
        if max(abs(start-word["start"]), abs(end-word["end"])) <= tolerance:
            matches.append((error, candidate))
    return min(matches, key=lambda match: match[0])[1] if matches else None


def _mapped_characters(text: str, candidate: dict) -> dict[int, dict] | None:
    offsets = [index for index, char in enumerate(text) if comparison_key(char)]
    chars = [char for char in candidate.get("characters", []) if comparison_key(str(char.get("char", "")))]
    if "".join(comparison_key(text[index]) for index in offsets) != "".join(comparison_key(str(char.get("char", ""))) for char in chars):
        return None
    if len(offsets) != len(chars):
        return None
    return dict(zip(offsets, chars))


def build_text_parts(words: list[dict], character_evidence: dict | list | None,
                     language: str, *, options: dict | None = None) -> tuple[list[dict], dict]:
    """Attach actual CTC boundaries to proposed parts or explain word fallback."""
    settings = {**PARTS_OPTIONS, **(options or {})}
    candidates = character_evidence.get("words", []) if isinstance(character_evidence, dict) else character_evidence or []
    parts, reasons, split_words, monosyllables = [], Counter(), 0, 0
    for word in words:
        spans, nuclei = proposed_spans(word["text"], language)
        reason, candidate, mapped, intervals, scores = None, None, None, [], []
        if word["start"] is None or word["end"] is None:
            reason = "word_timing_unavailable"
        elif word["approximate"] and (word.get("timing") or {}).get("source") != "lyrics_ctc":
            reason = "word_timing_approximate"
        elif word["end"] - word["start"] > settings["maximum_word_seconds"]:
            reason = "word_interval_too_long"
        elif not nuclei:
            reason = "pronunciation_unknown"
        elif len(spans) == 1:
            monosyllables += 1
        else:
            candidate = _candidate(word, candidates, settings)
            if candidate is None:
                reason = "character_evidence_unavailable"
            else:
                mapped = _mapped_characters(word["text"], candidate)
                if not mapped:
                    reason = "character_text_mismatch"
            if mapped:
                previous_end = -math.inf
                for offset, char in mapped.items():
                    start, end, score = _number(char.get("start")), _number(char.get("end")), _number(char.get("score"))
                    if start is None or end is None or not word["start"]-0.001 <= start < end <= word["end"]+0.001 or start < previous_end-0.001:
                        reason = "invalid_character_timing"
                        break
                    previous_end = end
                    if score is not None and 0 <= score <= 1:
                        scores.append(score)
                if reason is None and (not scores or sum(scores)/len(scores) < settings["minimum_mean_ctc_score"]):
                    reason = "weak_character_evidence"
                for a, b in nuclei:
                    nucleus_scores = [_number(mapped[i].get("score")) for i in range(a, b) if i in mapped]
                    if reason is None and (not nucleus_scores or max(s or 0 for s in nucleus_scores) < settings["minimum_nucleus_ctc_score"]):
                        reason = "weak_vowel_evidence"
                boundaries = [word["start"]]
                for start, _ in spans[1:]:
                    left = max((i for i in mapped if i < start), default=None)
                    right = min((i for i in mapped if i >= start), default=None)
                    if reason is not None:
                        break
                    if left is None or right is None:
                        reason = "boundary_evidence_missing"
                        break
                    gap = mapped[right]["start"] - mapped[left]["end"]
                    if gap > settings["maximum_boundary_gap_seconds"]:
                        reason = "ambiguous_boundary_gap"
                        break
                    # The next character's onset is observed CTC evidence; no
                    # equal division by letters, notes, or predicted syllables.
                    boundaries.append(float(mapped[right]["start"]))
                boundaries.append(word["end"])
                intervals = list(zip(boundaries, boundaries[1:]))
                if reason is None and any(end-start < settings["minimum_part_seconds"] for start, end in intervals):
                    reason = "part_interval_too_short"
        is_split = reason is None and len(spans) > 1
        if is_split:
            split_words += 1
        else:
            spans = [(0, len(word["text"]))]
            intervals = [(word["start"], word["end"])]
            if reason:
                reasons[reason] += 1
        for index, ((char_start, char_end), (start, end)) in enumerate(zip(spans, intervals)):
            is_whole = len(spans) == 1
            parts.append({"id": f"{word['id']}-p{index:03d}", "word_id": word["id"],
                          "index": index, "text": word["text"][char_start:char_end],
                          "char_start": word["char_start"]+char_start, "char_end": word["char_start"]+char_end,
                          "word_char_start": char_start, "word_char_end": char_end,
                          "start": start, "end": end,
                          "kind": "whole-word-fallback" if reason else "word" if is_whole else "pronounced-part",
                          "source": "canonical-word" if is_whole else "grapheme-nuclei+ctc",
                          "confidence": None if is_whole else round(sum(scores)/len(scores), 4),
                          "status": "fallback" if reason else "approximate" if is_split else "available",
                          "reason": reason,
                          "message": "Часть слова не определена" if reason else "Граница части приблизительная" if is_split else "",
                          "provenance": {"algorithm_version": PARTS_VERSION,
                                         "linguistic_method": "spelling-vowel-nuclei; not phoneme verification",
                                         "timing_method": "word-boundaries" if is_whole else "ctc-character-onset",
                                         "score_policy": "uncalibrated-model-score",
                                         "evidence_sha256": candidate.get("evidence_sha256") if candidate else None,
                                         "model": candidate.get("model") if candidate else None}})
    total = len(words)
    return parts, {"algorithm_version": PARTS_VERSION, "parameters": settings,
                   "total_words": total, "words_with_parts": split_words,
                   "single_part_words": monosyllables,
                   "whole_word_fallback": sum(reasons.values()),
                   "whole_word_fallback_fraction": sum(reasons.values()) / total if total else 0,
                   "fallback_reasons": dict(sorted(reasons.items())),
                   "linguistic_accuracy": "heuristic pronunciation parts; independent human acceptance pending",
                   "acoustic_accuracy": "CTC evidence; independent boundary acceptance pending"}


def align_word_characters(vocals_path: Path, words: list[dict], *, language: str,
                          duration: float) -> dict:
    """Optional explicit local refinement; no recognition/separation/F0 or download.

    The normal path reuses saved evidence. This helper is reserved for a one-time
    preparation when a cached English alignment model is already on this Mac.
    """
    checkpoint = Path.home() / ".cache/torch/hub/checkpoints/wav2vec2_fairseq_base_ls960_asr_ls960.pth"
    if language != "en" or not checkpoint.is_file():
        return {"words": [], "diagnostics": {"status": "unavailable", "reason": "supported_local_model_unavailable",
                "inference_performed": False}}
    from .alignment import WhisperXBackend
    from .models import TimedWord
    base = [TimedWord(word["text"], word["start"], word["end"], word.get("confidence"), segment_id=word["line_index"])
            for word in words if word["start"] is not None and word["end"] is not None and not word["approximate"]]
    backend = WhisperXBackend(device="cpu")
    refined = backend.refine(vocals_path, base, language, duration)
    return {"words": [asdict(word) for word in refined], "diagnostics": {"status": "available",
            "source": "local-ctc-refinement", "model": "WAV2VEC2_ASR_BASE_960H",
            "inference_performed": True, "word_candidates": len(refined)}}
