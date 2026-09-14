"""Align the complete supplied lyric, including words omitted by ASR.

This is a versioned derivative. Neither the lyric nor the original alignment is
rewritten. CTC scores are uncalibrated; recovered boundaries remain approximate.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path

from .timing_cache import file_sha256, fingerprint, package_versions, write_json

RECOVERY_VERSION = "complete-lyrics-ctc-2"
OPTIONS = {"chunk_seconds": 20, "context_seconds": 1, "vocal_margin_seconds": .35}


def _ctc_targets(words: list[dict], dictionary: dict) -> tuple[list[int], list, int]:
    """Exclude the model's blank symbol, even when its spelling is punctuation.

    The English torchaudio vocabulary spells CTC blank as '-'. A literal
    hyphen in the supplied lyric must not become an alignment target. Keep
    source spelling intact; only phonetic evidence excludes that symbol.
    """
    blank = dictionary.get("[pad]", dictionary.get("<pad>", 0))
    separator = dictionary.get("|")
    tokens, ranges = [], []
    for word in words:
        characters = [c for c in word["text"]
                      if c != "|" and c.lower() in dictionary and dictionary[c.lower()] != blank]
        if tokens and characters and separator is not None and separator != blank:
            tokens.append(separator)
        start = len(tokens)
        tokens.extend(dictionary[c.lower()] for c in characters)
        ranges.append((start, len(tokens), characters))
    return tokens, ranges, blank


def _paragraph_anchors(words: list[dict], bounds: tuple[float, float]) -> list[tuple[int, float]]:
    """Useful stanza onsets constrain recovery; compressed words never anchor it.

    A low-confidence or isolated onset (the old 'When' several seconds before
    'you') must not pull a neighbouring chorus out of its actual vocal region.
    """
    anchors = [(0, bounds[0])]
    for index, word in enumerate(words[1:], 1):
        if word["line_index"] <= words[index-1]["line_index"]+1:
            continue
        start, end = word["start"], word["end"]
        if (word["approximate"] or start is None or end is None
                or not .04 <= end-start <= 2.5 or (word.get("confidence") or 0) < .25):
            continue
        following = words[index+1] if index+1 < len(words) else None
        if following and not following["approximate"] and following["start"] is not None and following["start"]-end > 2.5:
            continue
        lower = max(bounds[0], start-.3)
        if anchors[-1][1] < lower < bounds[1]:
            anchors.append((index, lower))
    return anchors


def apply_lyric_evidence(words: list[dict], evidence: dict | None) -> list[dict]:
    """Match by occurrence ID AND spelling, never by a repeated word's spelling alone."""
    result = deepcopy(words)
    candidates = {word.get("id"): word for word in (evidence or {}).get("words", [])}
    duration = (evidence or {}).get("duration", 0)
    for word in result:
        candidate = candidates.get(word["id"])
        if not candidate or candidate.get("text") != word["text"]:
            continue
        if (word.get("timing") or {}).get("source") == "manual":
            continue
        start, end = candidate.get("start"), candidate.get("end")
        if not all(isinstance(t, (int, float)) and math.isfinite(t) for t in (start, end)):
            continue
        if not 0 <= start < end <= duration:
            continue
        # Keep a usable original local boundary when the independent complete
        # lyric agrees on its onset. This preserves stronger character evidence
        # and avoids stretching an already good word through a CTC blank tail.
        if (not word["approximate"] and word["status"] != "check_timing"
                and word["start"] is not None and abs(start-word["start"]) <= .18):
            continue
        word["original_timing"] = {k: deepcopy(word[k]) for k in ("start", "end", "timing", "status")}
        word.update(start=start, end=end, approximate=True, aligned=True,
                    status="approximate", alignment_source="lyrics-ctc",
                    message="Привязка полной лирики к вокалу; границы приблизительные",
                    timing={"source": "lyrics_ctc", "score": candidate.get("score"),
                            "score_policy": "uncalibrated", "evidence_key": evidence.get("cache_key")})
    return result


def prepare_lyric_evidence(audio: Path, words: list[dict], notes: list, *,
                           duration: float, language: str, cache_root: Path,
                           recognized_words: list[dict] | None = None) -> dict:
    if not words or not notes or all((w.get("timing") or {}).get("source") == "manual" for w in words):
        return {"status": "not-needed", "words": [], "duration": duration}
    if not audio.is_file() or language not in {"en", "ru"}:
        return {"status": "unavailable", "reason": "Нет вокала или модели языка", "words": [], "duration": duration}
    # Silent tails otherwise attract weak final tokens. This limits evidence to
    # the saved vocal activity, including consonant context, without moving notes.
    bounds = (max(0., min(n.start for n in notes)-1.),
              min(duration, max(n.end for n in notes)+OPTIONS["vocal_margin_seconds"]))
    versions = package_versions()["packages"]
    spec = {"algorithm": RECOVERY_VERSION, "implementation": file_sha256(Path(__file__)),
            "audio_sha256": file_sha256(audio), "words": [(w["id"], w["text"]) for w in words],
            "duration": duration, "bounds": bounds, "language": language, "options": OPTIONS,
            "paragraph_anchors": _paragraph_anchors(words, bounds),
            "recognized_words_sha256": fingerprint({"words": recognized_words or []}),
            "packages": {key: versions[key] for key in ("torch", "torchaudio", "whisperx", "faster-whisper", "ctranslate2")}}
    spec = json.loads(json.dumps(spec))
    key = fingerprint(spec)
    destination = cache_root / f"{key}.json"
    try:
        cached = json.loads(destination.read_text(encoding="utf-8"))
        # JSON roundtrip normalizes tuples in the specification.
        if cached.get("cache_key") == key and cached.get("spec") == json.loads(json.dumps(spec)):
            payload = {k: v for k, v in cached.items() if k != "payload_sha256"}
            if cached.get("payload_sha256") == fingerprint(payload):
                return cached
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    try:
        candidates, model = _align_complete_lyrics(audio, words, language, bounds)
        candidates, phrase_mapping = refine_recognized_phrases(words, candidates, recognized_words or [])
        model["recognized_phrase_mapping"] = phrase_mapping
    except (ImportError, OSError, RuntimeError, ValueError, KeyError) as exc:
        # Keep usable previous analysis; never cache a transient model failure.
        return {"status": "unavailable", "reason": str(exc), "words": [], "duration": duration}
    result = {"status": "available", "words": candidates, "model": model,
              "cache_key": key, "spec": spec, "duration": duration,
              "score_policy": "uncalibrated", "inference": "complete-canonical-lyrics"}
    result["payload_sha256"] = fingerprint(result)
    write_json(destination, result)
    return result


def refine_recognized_phrases(words: list[dict], candidates: list[dict], recognized: list[dict]):
    """Use complete ASR phrases near the new acoustic occurrence, not old IDs.

    Original global text matching could assign the ONLY recognized chorus to its
    last occurrence. CTC locates the omitted occurrences; intact three-plus-word
    phrases then restore the stronger saved local word boundaries. A phrase
    stitched across a long ASR omission cannot act as evidence.
    """
    from .lyrics import comparison_key
    by_id = {word["id"]: deepcopy(word) for word in candidates}
    lines = {}
    for word in words:
        lines.setdefault(word["line_index"], []).append(word)
    choices = []
    for line_id, line in lines.items():
        main, depth = [], 0
        for word in line:
            opening = word["text"].count("(")
            if not depth and not opening:
                main.append(word)
            depth = max(0, depth+opening-word["text"].count(")"))
        if len(main) < 3 or main[0]["id"] not in by_id:
            continue
        expected = by_id[main[0]["id"]]["start"]
        for start in range(len(recognized)-2):
            matched = []
            for word, observed in zip(main, recognized[start:]):
                if comparison_key(word["text"]) != comparison_key(str(observed.get("text", ""))):
                    break
                a, b = observed.get("start"), observed.get("end")
                if not all(isinstance(t, (int, float)) and math.isfinite(t) for t in (a, b)) or not 0 <= a < b or b-a > 2.5:
                    break
                if matched and not -.1 <= a-matched[-1][1]["end"] <= 2.5:
                    break
                matched.append((word, observed))
            if len(matched) >= 3 and abs(matched[0][1]["start"]-expected) <= 4:
                choices.append((abs(matched[0][1]["start"]-expected), -len(matched), line_id, start, matched))
    used_lines, used_source, mapped = set(), set(), 0
    for _, _, line_id, first, matched in sorted(choices, key=lambda item: item[:4]):
        source = set(range(first, first+len(matched)))
        if line_id in used_lines or source & used_source:
            continue
        for word, observed in matched:
            by_id[word["id"]] = {"id": word["id"], "text": word["text"], "start": observed["start"], "end": observed["end"],
                "score": observed.get("confidence"), "characters": observed.get("characters", []),
                "evidence_sha256": observed.get("evidence_sha256"), "model": "saved-asr-phrase+ctc",
                "recovery": "remapped-recognized-phrase"}
        mapped += len(matched)
        used_lines.add(line_id)
        used_source.update(source)
    return [by_id[word["id"]] for word in words if word["id"] in by_id], {"phrases": len(used_lines), "words": mapped}


def _align_complete_lyrics(audio: Path, words: list[dict], language: str,
                           bounds: tuple[float, float]) -> tuple[list[dict], dict]:
    import numpy as np
    import torch
    import torchaudio
    import whisperx
    from .alignment import _prepare_alignment_window

    model, metadata = whisperx.load_align_model(language_code=language, device="cpu")
    dictionary = metadata["dictionary"]
    from whisperx.alignment import DEFAULT_ALIGN_MODELS_HF, DEFAULT_ALIGN_MODELS_TORCH
    name = DEFAULT_ALIGN_MODELS_TORCH.get(language) or DEFAULT_ALIGN_MODELS_HF.get(language)
    extractor = None
    if metadata["type"] == "huggingface":
        from transformers import AutoFeatureExtractor
        extractor = AutoFeatureExtractor.from_pretrained(name)
    waveform = whisperx.load_audio(str(audio))
    sample_rate = 16000
    length = len(waveform)/sample_rate
    chunks, times = [], []
    previous_threads = torch.get_num_threads()
    try:
        torch.set_num_threads(min(4, previous_threads))
        for start in range(int(bounds[0]*sample_rate), min(len(waveform), math.ceil(bounds[1]*sample_rate)),
                           OPTIONS["chunk_seconds"]*sample_rate):
            lower = max(0., start/sample_rate-OPTIONS["context_seconds"])
            upper = min(length, start/sample_rate+OPTIONS["chunk_seconds"]+OPTIONS["context_seconds"])
            prepared = _prepare_alignment_window(waveform, lower, upper, extractor)
            segment = torch.from_numpy(prepared[int(lower*sample_rate):int(upper*sample_rate)]).unsqueeze(0)
            with torch.inference_mode():
                output = model(segment)
                logits = output[0] if metadata["type"] == "torchaudio" else output.logits
                emission = torch.log_softmax(logits, dim=-1)[0].cpu()
            stamps = lower+(np.arange(len(emission))+.5)*(upper-lower)/len(emission)
            keep = (stamps >= start/sample_rate) & (stamps < min(bounds[1], start/sample_rate+OPTIONS["chunk_seconds"]))
            chunks.append(emission[keep])
            times.extend(stamps[keep])
    finally:
        torch.set_num_threads(previous_threads)
    emission, times = torch.cat(chunks), np.asarray(times)
    tokens, ranges, blank = _ctc_targets(words, dictionary)
    if not tokens:
        return [], {"name": name, "device": "cpu"}
    step = float(np.median(np.diff(times)))
    anchors = _paragraph_anchors(words, bounds)+[(len(words), bounds[1])]
    token_times = []
    for (first, lower), (last, upper) in zip(anchors, anchors[1:]):
        # Each separator belongs to the preceding section; it does not consume
        # an occurrence from the following stanza.
        token_start = ranges[first][0]
        token_end = ranges[last][0] if last < len(words) else len(tokens)
        selected = (times >= lower) & (times < upper)
        section_times = times[selected]
        paths, scores = torchaudio.functional.forced_align(
            emission[selected].unsqueeze(0), torch.tensor([tokens[token_start:token_end]], dtype=torch.int32), blank=blank)
        spans = torchaudio.functional.merge_tokens(paths[0], scores[0].exp(), blank=blank)
        if len(spans) != token_end-token_start:
            raise RuntimeError("CTC не сохранил полную последовательность лирики")
        token_times.extend((round(max(lower, float(section_times[span.start])-step/2), 4),
                            round(min(upper, float(section_times[span.end-1])+step/2), 4),
                            float(span.score)) for span in spans)
    result = []
    for word, (start, end, characters) in zip(words, ranges):
        if start == end:
            continue
        chars = [{"char": char, "start": span[0], "end": span[1], "score": span[2]}
                 for char, span in zip(characters, token_times[start:end])]
        result.append({"id": word["id"], "text": word["text"], "start": chars[0]["start"],
                       "end": chars[-1]["end"], "score": sum(c["score"] for c in chars)/len(chars),
                       "characters": chars, "model": name})
    return result, {"name": name, "device": "cpu"}
