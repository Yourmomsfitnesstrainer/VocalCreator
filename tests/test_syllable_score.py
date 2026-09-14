"""Authored contract fixtures, not acoustic acceptance labels (S01–S24)."""
from copy import deepcopy
import json

import pytest

from karaoke_generator.models import AlignedLine, AlignedWord, AlignmentQuality, AlignmentResult
from karaoke_generator.syllable_score import build_score, normalize_score, validate_score
from karaoke_generator.syllables import proposed_spans


def aligned(words, duration=5, language="ru"):
    entries = [AlignedWord(text, text.lower(), start, end, .9, True, "refined", timing={"source": "refined"})
               for text, start, end in words]
    return AlignmentResult(language, duration, "fixture", [AlignedLine(" ".join(w.text for w in entries), 0, duration, entries)],
                           "fixture", AlignmentQuality(len(entries), len(entries), len(entries), 0, 1))


def note(identifier, start, end, midi=60, **extra):
    return {"id": identifier, "start": start, "end": end, "midi": midi, "cents": 0, "confidence": .9,
            "source": "authored", "uncertain": False, **extra}


def chars(text="молоко", starts=(0, .1, .45, .5, 1.15, 1.2), end=1.7, score=.9):
    letters = [c for c in text if c.isalpha()]
    boundaries = [*starts, end]
    return {"words": [{"text": text, "start": starts[0], "end": end, "evidence_sha256": "authored-ctc",
                       "characters": [{"char": c, "start": a, "end": b, "score": score}
                                      for c, a, b in zip(letters, boundaries, boundaries[1:])]}]}


def score(text="молоко", words=None, notes=None, evidence=None, language="ru"):
    words = words if words is not None else [(text, 0, 1.7)]
    return build_score(text, aligned(words, language=language), notes or [], job_id="job-fixture",
                       source={"duration": 5, "language": language, "notes_sha256": "immutable-fixture"},
                       base_analysis_key="base-fixture", character_evidence=evidence)


def test_s01_three_syllables_use_ctc_not_note_count_or_equal_division():
    notes = [note("n1", 0, .45), note("n2", .45, 1.15), note("n3", 1.15, 1.7)]
    result = score(notes=notes, evidence=chars())
    assert [u["text"] for u in result["units"]] == ["мо", "ло", "ко"]
    assert [(u["start"], u["end"]) for u in result["units"]] == [(0, .45), (.45, 1.15), (1.15, 1.7)]
    assert all(u["origin"] == "automatic" and u["segmentation_status"] == "approximate" for u in result["units"])
    assert result["provenance"]["diagnostics"]["word_fallback"] == 0
    assert [l["source_note_id"] for l in result["note_links"]] == ["n1", "n2", "n3"]
    assert "".join(u["text"] for u in result["units"]) == "молоко"


def test_s02_s04_melisma_pitch_changes_do_not_multiply_syllables_or_fill_pauses():
    notes = [note("n1", 0, .4, 60), note("n2", .5, 1, 67), note("n3", 1.2, 1.7, 61)]
    result = score("ма", notes=notes)
    assert len(result["units"]) == 1
    assert len({l["unit_id"] for l in result["note_links"]}) == 1
    assert [l["continuation"] for l in result["note_links"]] == [False, True, True]
    assert result["units"][0]["intervals"] == [
        {"start": 0, "end": .4, "support": "note_link"},
        {"start": .5, "end": 1, "support": "note_link"},
        {"start": 1.2, "end": 1.7, "support": "note_link"}]


def test_s03_multiple_syllables_share_unchanged_note_and_attack():
    notes = [note("same-note", 0, 1.7)]
    original = deepcopy(notes)
    result = score(notes=notes, evidence=chars())
    assert len(result["units"]) == 3
    assert {l["source_note_id"] for l in result["note_links"]} == {"same-note"}
    assert notes == original


def test_s05_weak_ctc_is_an_explicit_approximate_candidate():
    result = score(notes=[note("n", 0, 1.7)], evidence=chars(score=.12))
    assert [u["text"] for u in result["units"]] == ["мо", "ло", "ко"]
    assert all(u["timing_status"] == "approximate" and u["origin"] == "automatic" for u in result["units"])
    assert all("weak_ctc_boundary_proposal" in u["reason_codes"] for u in result["units"])


def test_s06_no_character_times_keeps_one_explicit_fallback():
    result = score(notes=[note("n", 0, 1.7)])
    assert len(result["units"]) == 1
    assert result["units"][0]["kind"] == "word_fallback"
    assert result["units"][0]["text"] == "молоко"
    assert result["provenance"]["diagnostics"]["fallback_fraction"] == 1
    assert result["unresolved"][0]["reason_code"] == "character_evidence_unavailable"


def test_s06_unknown_pronunciation_not_invented_from_notes():
    result = score("rhythm", notes=[note("a", 0, .4), note("b", .4, 1)], language="unknown")
    assert [u["kind"] for u in result["units"]] == ["word_fallback"]
    assert "pronunciation_unknown" in result["units"][0]["reason_codes"]


def test_s07_distant_notes_get_no_contextual_or_nearest_label():
    result = score("ма", words=[("ма", 0, .5)], notes=[note("distant", 3, 4)])
    assert result["note_links"] == []
    assert result["units"][0]["start"] is None
    assert result["unresolved"][-1]["reason_code"] == "timing_unavailable"


def test_s12_deleting_first_last_links_rebuilds_activity_and_keeps_unpitched_support():
    notes = [note("a", 0, .4), note("b", .5, 1), note("c", 1.2, 1.7)]
    original = score("ма", notes=notes)
    edited = deepcopy(original)
    edited["units"][0]["origin"] = "manual"
    edited["units"][0]["intervals"].append({"start": 2, "end": 2.2, "support": "unpitched"})
    edited["note_links"] = edited["note_links"][1:-1]
    normalized = normalize_score(edited)
    validate_score(normalized, notes)
    assert normalized["units"][0]["start"] == .5
    assert normalized["units"][0]["end"] == 2.2
    assert normalized["note_links"][0]["continuation"] is False
    assert normalized["units"][0]["intervals"] == [
        {"start": .5, "end": 1, "support": "note_link"}, {"start": 2, "end": 2.2, "support": "unpitched"}]
    assert original["units"][0]["start"] == 0
    normalized["note_links"] = []
    unlinked = normalize_score(normalized)
    validate_score(unlinked, notes)
    assert unlinked["units"][0]["intervals"] == [{"start": 2, "end": 2.2, "support": "unpitched"}]
    assert any(u["reason_code"] == "no_note_link" for u in unlinked["unresolved"])
    unlinked["units"][0]["intervals"] = []
    unplaced = normalize_score(unlinked)
    validate_score(unplaced, notes)
    assert unplaced["units"][0]["start"] is None
    assert normalize_score(unplaced) == unplaced
    assert normalize_score(original) == original  # undo restores all original IDs/timing


def test_s11_overlap_between_different_units_is_rejected_even_after_normalization():
    notes = [note("n", 0, 1.7)]
    edited = score(notes=notes, evidence=chars())
    edited["note_links"][0]["end"] = .6
    with pytest.raises(ValueError, match="overlapping active"):
        validate_score(normalize_score(edited), notes)


def test_s11_link_without_note_support_and_stale_continuation_are_rejected():
    notes = [note("n", 0, 1.7)]
    edited = score("ма", notes=notes)
    edited["note_links"][0]["continuation"] = True
    with pytest.raises(ValueError, match="continuation"):
        validate_score(edited, notes)
    edited = score("ма", notes=notes)
    edited["note_links"] = []
    with pytest.raises(ValueError, match="surviving links"):
        validate_score(edited, notes)


def test_s17_without_f0_or_alignment_preserves_unresolved_source():
    result = build_score("молоко", None, [], job_id="j", source={"duration": 5, "language": "ru"}, base_analysis_key="b")
    assert result["canonical_text"] == "молоко"
    assert result["note_links"] == []
    assert all(u["start"] is None and u["end"] is None and not u["intervals"] for u in result["units"])
    assert any(i["unit_id"] == result["units"][0]["unit_id"] for i in result["unresolved"])
    unit = result["units"][0]
    unit.update(origin="manual", timing_status="approximate", intervals=[{"start": 1, "end": 1.2, "support": "unpitched"}])
    result = normalize_score(result)
    validate_score(result, [])
    assert result["units"][0]["start"] == 1


def test_s13_unicode_ranges_and_manual_override_are_explicit():
    text = "🎵 ма, ма!"
    result = score(text, words=[("ма,", 0, .7), ("ма!", 1, 1.7)])
    assert len(result["occurrences"]) == 3
    assert result["occurrences"][1]["source_ranges"] == [{"start": 2, "end": 5}]
    assert len({o["occurrence_id"] for o in result["occurrences"]}) == 3
    edited = deepcopy(result)
    unit = edited["units"][1]
    unit["text"] = "мя"
    with pytest.raises(ValueError, match="manual_override"):
        validate_score(edited, [])
    unit.update(origin="manual", manual_override=True, source_text="ма,")
    validate_score(edited, [])
    assert result["canonical_text"] == text


@pytest.mark.parametrize("word,count", [("shaped", 1), ("heart", 1), ("hole", 1), ("you", 1),
                                        ("my", 1), ("table", 2), ("little", 2), ("needed", 2),
                                        ("молоко", 3), ("журавли", 3), ("моё", 2)])
def test_s22_silent_english_letters_and_russian_vowel_combinations(word, count):
    language = "ru" if any("а" <= c <= "я" or c == "ё" for c in word) else "en"
    spans, _ = proposed_spans(word, language)
    assert len(spans) == count
    assert "".join(word[a:b] for a, b in spans) == word


def test_s23_competing_lead_voice_times_remain_candidates():
    notes = [note("n", 0, 2)]
    result = score("ма ма", words=[("ма", 0, 1), ("ма", .5, 1.7)], notes=notes)
    assert len(result["note_links"]) == 1
    assert any(i["reason_code"] == "overlapping_lead_candidates" and i["candidates"] for i in result["unresolved"])
    assert result["units"][1]["start"] is None


def test_s24_inputs_and_piano_regions_are_unchanged_during_build_edit_validate():
    notes = [note("n", 0, 1.7, intervals=[{"start": 0, "end": .5}, {"start": .7, "end": 1.7}])]
    alignment = aligned([("ма", 0, 1.7)])
    source = {"duration": 5, "language": "ru"}
    original = json.dumps([notes, alignment.to_dict(), source], sort_keys=True)
    result = build_score("ма", alignment, notes, job_id="j", source=source, base_analysis_key="b")
    assert len(result["note_links"]) == 2
    assert [i["end"] for i in result["units"][0]["intervals"]] == [.5, 1.7]
    changed = normalize_score(result)
    validate_score(changed, notes)
    assert json.dumps([notes, alignment.to_dict(), source], sort_keys=True) == original
    invalid = deepcopy(result)
    invalid["note_links"][0]["end"] = .6
    with pytest.raises(ValueError, match="acoustic note"):
        validate_score(normalize_score(invalid), notes)


@pytest.mark.parametrize("mutation", [
    lambda d: d["units"].append(deepcopy(d["units"][0])),
    lambda d: d["units"][0].update(source_ranges=[{"start": 0, "end": 100}]),
    lambda d: d["units"][0].update(start=float("nan")),
    lambda d: d["note_links"][0].update(source_note_id="unknown"),
    lambda d: d["units"][0].update(occurrence_id="unknown"),
    lambda d: d["occurrences"][0].update(unit_ids=[]),
    lambda d: d["units"][0].update(order=True),
    lambda d: d.update(schema_version=9),
])
def test_invalid_ids_ranges_times_links_and_order_fail_closed(mutation):
    notes = [note("n", 0, 1.7)]
    result = score("ма", notes=notes)
    mutation(result)
    with pytest.raises(ValueError):
        validate_score(result, notes)


def test_identity_mismatch_is_rejected():
    notes = [note("n", 0, 1.7)]
    result = score("ма", notes=notes)
    for expected in ({"expected_job_id": "other"}, {"expected_source": {}}, {"expected_base_analysis_key": "other"}):
        with pytest.raises(ValueError):
            validate_score(result, notes, **expected)


def test_s11_manual_overlap_across_separate_occurrences_is_rejected():
    notes = [note("n", 0, 2)]
    edited = score("ма ма", words=[("ма", 0, .8), ("ма", 1, 1.7)], notes=notes)
    edited["note_links"][1]["start"] = .7
    with pytest.raises(ValueError):
        validate_score(normalize_score(edited), notes)


def test_s09_middle_reassignment_keeps_other_melisma_links_and_onset_order():
    notes = [note("a", 1, 2), note("b", 2, 3), note("c", 3, 4)]
    original = score("ма", words=[("ма", 1, 4)], notes=notes)
    edited = deepcopy(original)
    old = edited["units"][0]
    new = deepcopy(old)
    new.update(unit_id="manual-middle", text="ми", order=1, source_ranges=[], source_text="",
               manual_override=True, origin="manual", intervals=[])
    old["origin"] = "manual"
    edited["units"].append(new)
    edited["occurrences"][0]["unit_ids"].append(new["unit_id"])
    edited["note_links"][1].update(unit_id=new["unit_id"], origin="manual")
    edited = normalize_score(edited)
    validate_score(edited, notes)
    assert edited["units"][0]["intervals"] == [
        {"start": 1, "end": 2, "support": "note_link"},
        {"start": 3, "end": 4, "support": "note_link"}]
    assert edited["units"][1]["intervals"] == [{"start": 2, "end": 3, "support": "note_link"}]
    assert [l["link_id"] for l in edited["note_links"]] == [l["link_id"] for l in original["note_links"]]
    assert [l["continuation"] for l in edited["note_links"]] == [False, False, True]
    assert len(edited["occurrences"]) == 1
    reversed_order = deepcopy(edited)
    reversed_order["units"][0]["order"], reversed_order["units"][1]["order"] = 1, 0
    reversed_order["occurrences"][0]["unit_ids"].reverse()
    with pytest.raises(ValueError, match="backwards"):
        validate_score(reversed_order, notes)


def test_same_occurrence_ctc_can_refine_word_edges_without_crossing_neighbours():
    from karaoke_generator.syllables import canonical_words
    text = "молоко да"
    alignment = aligned([("молоко", .05, 1.8), ("да", 1.85, 2.3)])
    canonical = canonical_words(text, alignment, 5)
    evidence = chars()
    evidence["words"][0]["id"] = canonical[0]["id"]
    evidence["duration"] = 5
    notes = [note("n", 0, 1.7), note("next", 2, 2.3)]
    result = build_score(text, alignment, notes, job_id="j", source={"duration": 5, "language": "ru"},
                         base_analysis_key="b", lyric_evidence=evidence)
    assert [u["text"] for u in result["units"]] == ["мо", "ло", "ко", "да"]
    assert result["units"][0]["start"] == 0
    assert result["units"][2]["end"] == 1.7
    assert result["units"][0]["reason_codes"] == ["local_occurrence_ctc_proposal"]
    # Same spelling with another occurrence identity cannot supply those edges.
    evidence["words"][0]["id"] = "other-occurrence"
    unrelated = build_score(text, alignment, notes, job_id="j", source={"duration": 5, "language": "ru"},
                            base_analysis_key="b", lyric_evidence=evidence)
    assert unrelated["units"][0]["kind"] == "word_fallback"
    # A same-ID candidate extending through the next occurrence is rejected.
    evidence["words"][0]["id"] = canonical[0]["id"]
    evidence["words"][0]["end"] = 1.9
    evidence["words"][0]["characters"][-1]["end"] = 1.9
    crossing = build_score(text, alignment, notes, job_id="j", source={"duration": 5, "language": "ru"},
                           base_analysis_key="b", lyric_evidence=evidence)
    assert crossing["units"][0]["kind"] == "word_fallback"


def test_sparse_same_occurrence_ctc_keeps_syllables_without_inventing_notes_or_gap_activity():
    from karaoke_generator.syllables import canonical_words
    alignment = aligned([("мама", 0, 1.7)])
    canonical = canonical_words("мама", alignment, 5)
    evidence = {"duration": 5, "words": [{"id": canonical[0]["id"], "text": "мама", "start": 0, "end": 1.7,
        "characters": [{"char": c, "start": a, "end": b, "score": .8} for c, a, b in
                       [("м", 0, .04), ("а", .1, .14), ("м", 1.5, 1.54), ("а", 1.66, 1.7)]]}]}
    # Only the last vowel has a stable note. The first proposed syllable remains
    # inspectable in unresolved text with its observed timing candidate.
    notes = [note("one-real-note", 1.6, 1.7)]
    result = build_score("мама", alignment, notes, job_id="j", source={"duration": 5, "language": "ru"},
                         base_analysis_key="b", lyric_evidence=evidence)
    assert [u["text"] for u in result["units"]] == ["ма", "ма"]
    assert result["units"][0]["intervals"] == [] and result["units"][0]["start"] is None
    assert result["units"][0]["provenance"]["timing_candidate"] == {"start": 0, "end": 1.5}
    assert "sparse_ctc_emission_without_pitch_support" in result["units"][0]["reason_codes"]
    assert [(l["start"], l["end"]) for l in result["note_links"]] == [(1.6, 1.7)]
    assert any(i["unit_id"] == result["units"][0]["unit_id"] for i in result["unresolved"])
    # A lexical-only candidate has no reliable occurrence anchor for the sparse
    # gap; preserve the previous conservative fallback policy for this case.
    del evidence["words"][0]["id"]
    lexical_only = build_score("мама", alignment, notes, job_id="j", source={"duration": 5, "language": "ru"},
                               base_analysis_key="b", character_evidence=evidence)
    assert lexical_only["units"][0]["kind"] == "word_fallback"


def test_ctc_with_wrong_occurrence_id_cannot_supply_exact_matching_word_times():
    evidence = chars()
    evidence["words"][0]["id"] = "another-identical-occurrence"
    result = score(notes=[note("n", 0, 1.7)], evidence=evidence)
    assert result["units"][0]["kind"] == "word_fallback"
    assert result["units"][0]["text"] == "молоко"
