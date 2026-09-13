"""V3 contracts. These authored fixtures are not independent musical labels."""
import json
import hashlib
from pathlib import Path

import pytest

from karaoke_generator.learning_v3 import build_learning_result_v3, piano_events_v3
from karaoke_generator.models import AlignedLine, AlignedWord, AlignmentQuality, AlignmentResult
from karaoke_generator.studio_models import NoteEvent
from karaoke_generator.syllables import build_text_parts, canonical_words, load_character_evidence, proposed_spans


def alignment(entries, duration=5, language="en"):
    words = [AlignedWord(text, text.lower(), start, end, .9, source != "interpolated", source,
                         timing={"source": source}) for text, start, end, source in entries]
    return AlignmentResult(language, duration, "fixture", [AlignedLine(" ".join(w.text for w in words), 0, duration, words)],
                           "manual", AlignmentQuality(len(words), len(words), len(words), 0, 1))


def note(identifier, start, end, midi):
    return NoteEvent(identifier, start, end, midi, 0, .9, "authored-fixture")


def evidence(text="Mama!", intervals=None):
    intervals = intervals or [(0, .12), (.12, .7), (.7, .8), (.8, 1.2)]
    chars = [{"char": char, "start": start, "end": end, "score": .9}
             for char, (start, end) in zip([c for c in text if c.isalpha()], intervals)]
    return {"words": [{"text": text, "start": intervals[0][0], "end": intervals[-1][1],
                       "characters": chars, "evidence_sha256": "fixture"}]}


def result(mode="pro", text="Mama!", aligned=None, notes=None, chars=None):
    return build_learning_result_v3(mode, notes or [], aligned,
                                    canonical_text=text, timeline={"duration": 5}, provenance={},
                                    character_evidence=chars)


def test_canonical_text_repetitions_case_punctuation_and_missing_words_survive_every_mode():
    text = "\nOh!  oh,\n x? missing (I)\tI.\n"
    aligned = alignment([("Oh!", 0, .2, "manual"), ("oh,", .2, .4, "manual"),
                         ("x?", .4, .5, "manual"), ("(I)", 1, 1.2, "manual"), ("I.", 1.2, 1.4, "manual")])
    for mode in ("light", "medium", "pro"):
        data = result(mode, text, aligned)
        assert data["canonical_text"] == text
        assert [word["text"] for word in data["words"]] == text.split()
        assert len({word["id"] for word in data["words"]}) == 6
        assert all(text[word["char_start"]:word["char_end"]] == word["text"] for word in data["words"])
        missing = data["words"][3]
        assert missing["start"] is None and missing["end"] is None
        assert missing["note_ids"] == []
        assert missing["status"] == "unavailable"
        assert data["words"][4]["start"] == 1


@pytest.mark.parametrize("start,end", [(float("nan"), 1), (0, float("inf")), (2, 1), (-1, .1), (0, 8)])
def test_invalid_word_timing_is_null_and_cannot_create_labels(start, end):
    data = result(aligned=alignment([("Mama!", start, end, "manual")]), notes=[note("n", 0, 1, 60)], chars=evidence())
    assert data["words"][0]["start"] is None
    assert data["note_text_links"] == []
    assert data["notes"][0]["text_status"] == "unavailable"
    json.dumps(data, allow_nan=False)


def test_real_character_boundary_not_even_split_and_exact_source_ranges():
    data = result(aligned=alignment([("Mama!", 0, 1.2, "manual")]), chars=evidence())
    parts = data["text_parts"]
    assert [part["text"] for part in parts] == ["Ma", "ma!"]
    assert [(part["start"], part["end"]) for part in parts] == [(0, .7), (.7, 1.2)]
    assert parts[0]["end"] != 1.2 / 2
    assert "".join(part["text"] for part in parts) == "Mama!"
    assert all(part["status"] == "approximate" for part in parts)
    assert all(part["provenance"]["timing_method"] == "ctc-character-onset" for part in parts)


def test_parts_melisma_and_pause_preserved_without_any_per_word_limit():
    notes = [note(str(i), start, end, midi) for i, (start, end, midi) in enumerate(
        [(0, .2, 60), (.2, .4, 62), (.5, .7, 64), (.7, .9, 65), (1, 1.2, 67)])]
    for mode in ("light", "medium", "pro"):
        data = result(mode, aligned=alignment([("Mama!", 0, 1.2, "manual")]), notes=notes, chars=evidence())
        assert [event["midi"] for event in data["notes"]] == [60, 62, 64, 65, 67]
        assert len(piano_events_v3(data)) == 5
        assert [(event.start, event.end) for event in piano_events_v3(data)] == [(n.start, n.end) for n in notes]
        labels = data["note_text_links"]
        assert [label["text"] for label in labels] == ["Ma", "Ma", "Ma", "ma!", "ma!"]
        assert [label["continuation"] for label in labels] == [False, True, True, False, True]
        assert labels[0]["part_id"] == labels[2]["part_id"]
        assert all(event.end <= .4 or event.start >= .5 for event in piano_events_v3(data))


def test_multiple_words_on_one_note_never_create_piano_reattacks():
    data = result(text="I see you", aligned=alignment([("I", 0, .2, "manual"), ("see", .2, .6, "manual"),
                                                      ("you", .6, 1, "manual")]), notes=[note("n", 0, 1, 60)])
    assert [label["text"] for label in data["notes"][0]["labels"]] == ["I", "see", "you"]
    assert len(data["words"]) == 3
    assert len(piano_events_v3(data)) == 1


@pytest.mark.parametrize("change,reason", [
    ("absent", "character_evidence_unavailable"), ("wrong_text", "character_evidence_unavailable"),
    ("low_score", "weak_character_evidence"), ("low_vowel", "weak_vowel_evidence"),
    ("overlap", "invalid_character_timing"), ("nan", "invalid_character_timing"),
    ("missing_char", "character_text_mismatch"), ("old_timing", "character_evidence_unavailable"),
])
def test_uncertain_evidence_keeps_full_word_and_specific_fallback_reason(change, reason):
    chars = evidence()
    if change == "absent": chars = None
    elif change == "wrong_text": chars["words"][0]["text"] = "Papa!"
    elif change == "old_timing": chars["words"][0]["start"] = .2
    elif change == "low_score":
        for char in chars["words"][0]["characters"]: char["score"] = .1
    elif change == "low_vowel": chars["words"][0]["characters"][1]["score"] = .01
    elif change == "overlap": chars["words"][0]["characters"][2]["start"] = .3
    elif change == "nan": chars["words"][0]["characters"][2]["start"] = float("nan")
    elif change == "missing_char": chars["words"][0]["characters"].pop()
    data = result(aligned=alignment([("Mama!", 0, 1.2, "manual")]), notes=[note("n", 0, 1.2, 60)], chars=chars)
    assert len(data["text_parts"]) == 1
    assert data["text_parts"][0]["text"] == "Mama!"
    assert data["text_parts"][0]["reason"] == reason
    assert data["diagnostics"]["parts"]["words_with_parts"] == 0
    assert data["diagnostics"]["parts"]["whole_word_fallback"] == 1


def test_approximate_word_timing_cannot_be_promoted_by_character_cache():
    data = result(aligned=alignment([("Mama!", 0, 1.2, "interpolated")]), chars=evidence())
    assert data["text_parts"][0]["kind"] == "whole-word-fallback"
    assert data["text_parts"][0]["reason"] == "word_timing_approximate"


def test_anomalous_approximate_interval_keeps_text_but_cannot_cover_instrumental_notes():
    data = result(aligned=alignment([("Mama!", 0, 5, "interpolated")]), notes=[note("n", 3, 4, 60)])
    assert data["words"][0]["start"] == 0 and data["words"][0]["end"] == 5
    assert data["words"][0]["status"] == "check_timing"
    assert data["words"][0]["text"] == "Mama!"
    assert data["note_text_links"] == []
    assert len(data["notes"]) == 1


def test_one_bad_word_does_not_discard_other_words_parts():
    data = result(text="Mama! Mama!", aligned=alignment([("Mama!", 0, 1.2, "manual"), ("Mama!", 2, 3, "manual")]), chars=evidence())
    assert [part["text"] for part in data["text_parts"]] == ["Ma", "ma!", "Mama!"]
    assert data["diagnostics"]["parts"]["words_with_parts"] == 1


def test_absent_alignment_or_empty_text_never_hides_original_text_or_notes():
    for mode in ("light", "medium", "pro"):
        data = result(mode, "No timing.\nNo timing.", notes=[note("n", 0, 1, 60)])
        assert len(data["words"]) == 4 and len(data["notes"]) == 1
        assert data["note_text_links"] == []
        assert all(part["reason"] == "word_timing_unavailable" for part in data["text_parts"])
    assert canonical_words("", alignment([("old", 0, 1, "manual")]), 5) == []


def test_loader_only_reads_saved_refined_characters_and_rejects_foreign_text(tmp_path):
    cache = tmp_path / "work" / "timing-cache"
    cache.mkdir(parents=True)
    key = "a" * 64
    word = evidence()["words"][0]
    word["timing"] = {"source": "refined"}
    payload = {"spec": {"asr_key": key}, "words": [word]}
    (tmp_path / "vocals.wav").write_bytes(b"fixture-original-vocal")
    audio_hash = hashlib.sha256(b"fixture-original-vocal").hexdigest()
    (tmp_path / "alignment.json").write_text(json.dumps({"source_text_sha256": "matching"}))
    (cache / f"asr-{key}.json").write_text(json.dumps({"spec": {"text_sha256": "different", "audio_sha256": audio_hash}}))
    path = cache / "refined-fixture.json"
    path.write_text(json.dumps(payload))
    original = path.read_bytes()
    assert load_character_evidence(tmp_path)["words"] == []
    (cache / f"asr-{key}.json").write_text(json.dumps({"spec": {"text_sha256": "matching", "audio_sha256": audio_hash}}))
    loaded = load_character_evidence(tmp_path)
    assert len(loaded["words"]) == 1
    assert loaded["diagnostics"]["inference_performed"] is False
    assert path.read_bytes() == original


def test_foreign_audio_with_identical_word_text_and_endpoints_is_not_used(tmp_path):
    cache = tmp_path / "work" / "timing-cache"
    cache.mkdir(parents=True)
    key = "b" * 64
    word = evidence()["words"][0]
    word["timing"] = {"source": "refined"}
    (tmp_path / "alignment.json").write_text(json.dumps({"source_text_sha256": "text"}))
    (tmp_path / "vocals.wav").write_bytes(b"current vocal")
    (cache / f"asr-{key}.json").write_text(json.dumps({"spec": {"text_sha256": "text", "audio_sha256": hashlib.sha256(b"different vocal").hexdigest()}}))
    (cache / "refined-stale.json").write_text(json.dumps({"spec": {"asr_key": key}, "words": [word]}))
    loaded = load_character_evidence(tmp_path)
    assert loaded["words"] == []
    assert loaded["diagnostics"]["errors"][0]["reason"] == "character cache belongs to different vocal audio"
    data = result(aligned=alignment([("Mama!", 0, 1.2, "manual")]), chars=loaded)
    assert data["text_parts"][0]["reason"] == "character_evidence_unavailable"


def test_pronunciation_heuristic_preserves_characters_and_does_not_claim_timestamps():
    for text, language in [("Recognise!", "en"), ("мама", "ru"), ("неизвестность", "ru"),
                           ("overdrive", "en"), ("table", "en"), ("unknown", "xx")]:
        spans, nuclei = proposed_spans(text, language)
        assert "".join(text[a:b] for a, b in spans) == text
        assert all(isinstance(a, int) and isinstance(b, int) for a, b in spans)
