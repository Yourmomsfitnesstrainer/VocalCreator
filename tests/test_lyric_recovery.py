import json
from copy import deepcopy

import pytest

from karaoke_generator.learning_v3 import build_learning_result_v3, piano_events_v3
from karaoke_generator.lyric_recovery import apply_lyric_evidence, prepare_lyric_evidence
from karaoke_generator.studio_models import NoteEvent
from karaoke_generator.syllables import canonical_words


def test_paragraph_onsets_stop_verse_tail_from_consuming_next_chorus_but_reject_isolated_asr_word():
    from karaoke_generator.lyric_recovery import _paragraph_anchors
    words = canonical_words("End verse\n\nI know\n\nWhen you", None, 30)
    for word, start, end in zip(words, [1, 2, 10, 10.2, 15, 20], [2, 3, 10.2, 11, 15.2, 20.2]):
        word.update(start=start, end=end, approximate=False, confidence=.9)
    assert _paragraph_anchors(words, (0, 30)) == [(0, 0), (2, 9.7)]


def note(identifier, start, end):
    return NoteEvent(identifier, start, end, 60, 0, .9, "fixture")


def candidates(words, times):
    return {"status": "available", "duration": 30, "cache_key": "fixture",
            "words": [{"id": word["id"], "text": word["text"], "start": start, "end": end,
                       "score": .5} for word, (start, end) in zip(words, times)]}


def test_asr_omitted_repeated_lines_are_all_recovered_by_occurrence_without_changing_lyrics():
    text = "Go! home.\nGo! home.\nGo! home."
    words = canonical_words(text, None, 30)
    # Independent fixture: three sung phrases with known, widely separated times.
    evidence = candidates(words, [(1, 2), (2, 3), (10, 11), (11, 12), (20, 21), (21, 22)])
    notes = [note("first", 1.1, 2.8), note("second", 10.1, 11.8), note("third", 20.1, 21.8)]
    for mode in ("light", "medium", "pro"):
        data = build_learning_result_v3(mode, notes, None, canonical_text=text, lyric_evidence=evidence,
                                        timeline={"duration": 30}, provenance={})
        assert data["canonical_text"] == text
        assert [w["text"] for w in data["words"]] == text.split()
        assert len({w["id"] for w in data["words"]}) == 6
        assert [(w["start"], w["end"]) for w in data["words"]] == [(1, 2), (2, 3), (10, 11), (11, 12), (20, 21), (21, 22)]
        assert [[label["text"] for label in n["labels"]] for n in data["notes"]] == [["Go!", "home."]]*3
        assert data["diagnostics"]["notes_without_text"] == 0
        assert [(n.start, n.end) for n in piano_events_v3(data)] == [(1.1, 2.8), (10.1, 11.8), (20.1, 21.8)]


def test_manual_timing_wrong_occurrence_and_invalid_evidence_are_never_overwritten():
    words = canonical_words("Go go", None, 30)
    words[0].update(start=3, end=4, timing={"source": "manual"})
    evidence = candidates(words, [(5, 6), (7, 8)])
    before = deepcopy(words)
    assert apply_lyric_evidence(words, evidence)[0] == before[0]
    assert words == before
    for change in ({"text": "other"}, {"id": "foreign-occurrence"}, {"start": float("nan")}, {"end": 31}):
        altered = deepcopy(evidence)
        altered["words"][1].update(change)
        assert apply_lyric_evidence(words, altered)[1] == before[1]


def test_note_edges_receive_text_and_distant_context_is_explicit_not_recognition():
    text = "Home"
    words = canonical_words(text, None, 30)
    evidence = candidates(words, [(2, 3)])
    notes = [note("edge", 1.8, 1.95), note("voiced", 2, 3.15), note("context", 10, 11)]
    data = build_learning_result_v3("pro", notes, None, canonical_text=text, lyric_evidence=evidence,
                                    timeline={"duration": 30}, provenance={})
    assert all(n["labels"] and n["labels"][0]["text"] == "Home" for n in data["notes"])
    assert data["notes"][0]["labels"][0]["kind"] == "boundary-extension"
    assert data["notes"][1]["labels"][-1]["end"] == 3.15
    assert data["notes"][2]["labels"][0]["status"] == "context"
    assert "не подтверждено" in data["notes"][2]["labels"][0]["message"]
    assert data["diagnostics"]["notes_with_lyric_context"] == 1


def test_recovery_cache_is_shared_preserves_sources_and_rejects_tampering(tmp_path, monkeypatch):
    import karaoke_generator.lyric_recovery as recovery
    audio = tmp_path / "vocals.wav"
    audio.write_bytes(b"local-vocal-input")
    words = canonical_words("Go go", None, 30)
    calls = []

    def infer(audio, full_lyric, language, bounds):
        calls.append([w["id"] for w in full_lyric])
        return candidates(full_lyric, [(1, 2), (10, 11)])["words"], {"name": "fixture"}

    monkeypatch.setattr(recovery, "_align_complete_lyrics", infer)
    kwargs = dict(duration=30, language="en", cache_root=tmp_path / "cache")
    first = prepare_lyric_evidence(audio, words, [note("a", 1, 11)], **kwargs)
    again = prepare_lyric_evidence(audio, words, [note("a", 1, 11)], **kwargs)
    assert first == again and calls == [[w["id"] for w in words]]
    assert audio.read_bytes() == b"local-vocal-input"
    path = tmp_path / "cache" / f"{first['cache_key']}.json"
    corrupt = json.loads(path.read_text())
    corrupt["words"][1]["start"] = 999
    path.write_text(json.dumps(corrupt))
    repaired = prepare_lyric_evidence(audio, words, [note("a", 1, 11)], **kwargs)
    assert len(calls) == 2 and repaired["words"][1]["start"] == 10
    audio.write_bytes(b"different-vocal-input")
    changed = prepare_lyric_evidence(audio, words, [note("a", 1, 11)], **kwargs)
    assert changed["cache_key"] != first["cache_key"] and len(calls) == 3


def test_missing_model_is_reported_and_is_retried_instead_of_caching_failure(tmp_path, monkeypatch):
    import karaoke_generator.lyric_recovery as recovery
    audio = tmp_path / "vocals.wav"
    audio.write_bytes(b"local-input")
    words = canonical_words("Go", None, 30)
    calls = []

    def fail(*args):
        calls.append(1)
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(recovery, "_align_complete_lyrics", fail)
    for _ in range(2):
        evidence = prepare_lyric_evidence(audio, words, [note("a", 1, 2)], duration=30, language="en", cache_root=tmp_path / "cache")
        assert evidence["status"] == "unavailable" and evidence["reason"] == "model unavailable"
    assert len(calls) == 2 and not (tmp_path / "cache").exists()


def test_changed_stanza_anchor_invalidates_recovery_even_with_identical_audio_and_text(tmp_path, monkeypatch):
    import karaoke_generator.lyric_recovery as recovery
    audio = tmp_path / "vocals.wav"
    audio.write_bytes(b"same-audio")
    words = canonical_words("Go\n\nGo", None, 30)
    monkeypatch.setattr(recovery, "_align_complete_lyrics", lambda *args: ([], {"name": "fixture"}))
    kwargs = dict(duration=30, language="en", cache_root=tmp_path / "cache")
    first = prepare_lyric_evidence(audio, words, [note("a", 1, 22)], **kwargs)
    words[1].update(start=20, end=21, approximate=False, confidence=.9)
    changed = prepare_lyric_evidence(audio, words, [note("a", 1, 22)], **kwargs)
    assert first["cache_key"] != changed["cache_key"]


def test_recognized_chorus_maps_to_nearest_occurrence_instead_of_last_repeat():
    from karaoke_generator.lyric_recovery import refine_recognized_phrases
    words = canonical_words("I know what to do (Do)\nI know what to do (Do)", None, 30)
    predicted = candidates(words, [(3+i*.3, 3.2+i*.3) for i in range(6)]+[(20+i*.3, 20.2+i*.3) for i in range(6)])["words"]
    heard = [{"text": text, "start": 1+i*.4, "end": 1.3+i*.4, "confidence": .9} for i, text in enumerate("I know what to do".split())]
    recovered, diagnostics = refine_recognized_phrases(words, predicted, heard)
    assert [w["start"] for w in recovered[:5]] == [1, 1.4, 1.8, 2.2, 2.6]
    assert recovered[6:] == predicted[6:]
    assert recovered[5] == predicted[5]  # echo is not another copy of ASR 'do'
    assert diagnostics == {"phrases": 1, "words": 5}


def test_asr_words_stitched_across_missing_audio_cannot_move_a_good_repeat():
    from karaoke_generator.lyric_recovery import refine_recognized_phrases
    words = canonical_words("I know what", None, 30)
    predicted = candidates(words, [(5, 5.3), (5.4, 5.7), (5.8, 6.1)])["words"]
    heard = [{"text": word, "start": start, "end": start+.3} for word, start in zip(["I", "know", "what"], [4, 12, 20])]
    recovered, diagnostics = refine_recognized_phrases(words, predicted, heard)
    assert recovered == predicted and diagnostics["words"] == 0
