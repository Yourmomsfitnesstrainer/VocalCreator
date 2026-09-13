import json
import math
import wave
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from karaoke_generator.learning import build_learning_result, piano_events
from karaoke_generator.learning_storage import energy_attacks, prepare_learning
from karaoke_generator.melody import stable_note_events, link_words_to_notes
from karaoke_generator.models import AlignedLine, AlignedWord, AlignmentQuality, AlignmentResult
from karaoke_generator.piano import render_piano
from karaoke_generator.studio_models import MelodyResult, NoteEvent, PitchFrame

CONTRACTS = json.loads((Path(__file__).parent / "fixtures/learning/contracts.json").read_text())
CASES = CONTRACTS["calibration"] + CONTRACTS["holdout"]


def alignment(words, duration):
    return AlignmentResult("en", duration, "fixture", [AlignedLine("fixture", 0, duration, words)],
                           "manual", AlignmentQuality(len(words), len(words), len(words), 0, 1))


def word(start, end, text="word", source="manual"):
    return AlignedWord(text, text, start, end, 0.9, source != "interpolated", source, timing={"source": source})


def note(identifier, start, end, midi, confidence=0.9):
    return NoteEvent(identifier, start, end, midi, 0, confidence, "fixture", confidence < 0.45)


def frame(time, midi, score=0.9):
    return PitchFrame(time, None if midi is None else 440 * 2 ** ((midi - 69) / 12), midi,
                      midi is not None, score if midi is not None else 0, score)


def fixture(case):
    duration = case.get("duration") or case["events"][-1][1]
    frames = []
    for index in range(round(duration * 100)):
        time = index / 100
        pitch = None
        if case["id"] == "vibrato":
            pitch = case["center"] + case["amplitude"] * math.sin(time * 2 * math.pi * case["frequency"])
        elif case["id"] == "portamento":
            a, b = case["ramp"]
            progress = min(1, max(0, (time - a) / (b - a)))
            pitch = case["start_pitch"] + progress * (case["end_pitch"] - case["start_pitch"])
        else:
            pitch = next((p for start, end, p in case["events"] if start <= time < end), None)
        frames.append(frame(time, pitch))
    return frames, duration


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_authored_note_contracts(case):
    frames, duration = fixture(case)
    untouched = [asdict(item) for item in frames]
    notes, diagnostics = stable_note_events(frames, duration=duration, hop_seconds=0.01,
                                            attack_times=case.get("attack_times"))
    if "pro_count" in case:
        assert len(notes) == case["pro_count"]
    if case.get("events") and case["id"] != "approximate_alignment":
        assert [n.midi for n in notes] == [event[2] for event in case["events"]]
        assert [n.start for n in notes] == pytest.approx([event[0] for event in case["events"]], abs=0.011)
    if case["id"] == "portamento":
        assert [n.midi for n in notes] == [60, 67]
    assert [asdict(item) for item in frames] == untouched
    bounds = case.get("word", [0, duration])
    aligned = alignment([word(*bounds, source=case.get("timing_source", "manual"))], duration)
    for mode, limit in (("light", 1), ("medium", 2), ("pro", None)):
        result = build_learning_result(mode, notes, aligned, timeline={"duration": duration}, provenance={})
        if limit:
            assert len(result.notes) <= limit
            if case.get("learning_count") == 0:
                assert result.notes == []
                assert result.words[0]["status"] == "check_timing"
            else:
                assert result.notes
            assert all(n.midi in {source.midi for source in notes} for n in result.notes)
        else:
            assert len(result.notes) == len(notes)
        if "light_pitch" in case and mode == "light":
            assert result.notes[0].midi == case["light_pitch"]
        if "gap" in case:
            a, b = case["gap"]
            assert all(interval.end <= a or interval.start >= b for group in result.notes for interval in group.intervals)
        assert result.words[0]["start"] == bounds[0]
        assert result.words[0]["end"] == bounds[1]
    assert diagnostics["calibration"].endswith("pending")


def test_medium_uses_one_source_boundary_and_never_alternates_top_two_pitches():
    notes = [note(str(i), i / 4, (i + 1) / 4, pitch) for i, pitch in enumerate([60, 72, 60, 72])]
    result = build_learning_result("medium", notes, alignment([word(0, 1)], 1), timeline={"duration": 1}, provenance={})
    assert 1 <= len(result.notes) <= 2
    assert [i for group in result.notes for i in group.source_note_ids] == ["0", "1", "2", "3"]
    if len(result.notes) == 2:
        assert result.notes[0].end == result.notes[1].start
        assert result.notes[0].end in {0.25, 0.5, 0.75}


def test_repeated_words_unknown_notes_and_cross_word_source_are_preserved():
    notes = [note("shared", 0, 1, 60), note("quiet", 1, 2, 72, 0.1)]
    aligned = alignment([word(0, 0.5, "repeat"), word(0.5, 1, "repeat"), word(1, 2, "quiet"), word(2, 3, "silent")], 3)
    original_links = link_words_to_notes(aligned, notes)
    for mode in ("light", "medium", "pro"):
        result = build_learning_result(mode, notes, aligned, timeline={"duration": 3}, provenance={})
        assert len(result.words) == 4
        assert len({w["id"] for w in result.words}) == 4
        if mode == "pro":
            assert len(result.notes) == 2
            assert [(link.start, link.end) for link in result.word_note_links if link.note_id == "shared"] == [(0, 0.5), (0.5, 1)]
        else:
            assert len(result.notes) == 2
            assert all(n.source_note_ids == ["shared"] for n in result.notes)
            assert result.words[2]["status"] == "unavailable"
    assert link_words_to_notes(aligned, notes) == original_links


def test_short_sole_overlap_is_retained_but_neighbour_tail_is_not_a_step():
    notes = [note("tail", 0, 0.21, 60), note("body", 0.21, 1, 72)]
    result = build_learning_result("light", notes, alignment([word(0.2, 1)], 1), timeline={"duration": 1}, provenance={})
    assert result.notes[0].source_note_ids == ["body"]
    result = build_learning_result("light", notes, alignment([word(0.2, 0.21)], 1), timeline={"duration": 1}, provenance={})
    assert result.notes[0].source_note_ids == ["tail"]


def test_no_alignment_disables_only_simplified_modes():
    notes = [note("n", 0, 1, 69)]
    for mode in ("light", "medium"):
        with pytest.raises(ValueError, match="Нет временной разметки"):
            build_learning_result(mode, notes, None, timeline={"duration": 1}, provenance={})
    assert len(build_learning_result("pro", notes, None, timeline={"duration": 1}, provenance={}).notes) == 1


def test_piano_is_silent_in_internal_gap_and_outside_word(tmp_path):
    notes = [note("a", 0, 0.3, 60), note("b", 0.6, 1, 64)]
    result = build_learning_result("light", notes, alignment([word(0.1, 0.9)], 1), timeline={"duration": 1}, provenance={})
    assert len(result.notes) == 1
    assert len(piano_events(result)) == 2
    path = tmp_path / "piano.wav"
    render_piano(piano_events(result), path, duration=1, options={"bound_to_intervals": True})
    with wave.open(str(path), "rb") as audio:
        rate = audio.getframerate()
        samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").reshape(-1, 2)
    for a, b in ((0, 0.1), (0.3, 0.6), (0.9, 1)):
        assert np.max(np.abs(samples[round(a * rate):round(b * rate)])) == 0
    assert np.max(np.abs(samples[round(0.15 * rate):round(0.25 * rate)])) > 100


def write_saved_result(path):
    path.mkdir(parents=True, exist_ok=True)
    aligned = alignment([word(0, 1)], 1)
    notes = [note("raw", 0, 1, 69)]
    melody = MelodyResult({"duration": 1}, [frame(i / 100, 69) for i in range(100)], notes,
                          link_words_to_notes(aligned, notes), {}, {})
    (path / "melody.json").write_text(json.dumps(melody.to_dict()))
    (path / "alignment.json").write_text(json.dumps(aligned.to_dict()))
    (path / "studio.json").write_text(json.dumps({"schema_version": 1, "status": "complete", "id": path.parent.name,
                                                "artifacts": {"melody.json": "melody.json", "alignment.json": "alignment.json"}}))
    return {p.name: p.read_bytes() for p in path.iterdir() if p.is_file()}


def test_lazy_migration_reuses_sources_and_invalidates_word_and_synth_cache(tmp_path, monkeypatch):
    import karaoke_generator.learning_storage as storage
    original = write_saved_result(tmp_path)
    first, first_dir = prepare_learning(tmp_path, "light")
    saved_render = storage.render_piano
    monkeypatch.setattr(storage, "render_piano", lambda *a, **kw: pytest.fail("Unexpected piano render on cache hit"))
    cached, cached_dir = prepare_learning(tmp_path, "light")
    assert cached == first and cached_dir == first_dir
    monkeypatch.setattr(storage, "render_piano", saved_render)
    assert {name: (tmp_path / name).read_bytes() for name in original} == original
    synth, _ = prepare_learning(tmp_path, "light", piano_options={"gain": 0.1})
    assert synth["cache_key"] != first["cache_key"]
    raw = json.loads((tmp_path / "alignment.json").read_text())
    raw["lines"][0]["words"][0]["start"] = 0.2
    (tmp_path / "alignment.json").write_text(json.dumps(raw))
    changed, _ = prepare_learning(tmp_path, "light")
    assert changed["cache_key"] != first["cache_key"]
    assert changed["notes"][0]["start"] == 0.2
    assert changed["provenance"]["stable_cache_key"] == first["provenance"]["stable_cache_key"]


def test_failure_does_not_publish_partial_mode_or_damage_previous_cache(tmp_path, monkeypatch):
    import karaoke_generator.learning_storage as storage
    write_saved_result(tmp_path)
    data, directory = prepare_learning(tmp_path, "pro")
    before = (directory / "piano.wav").read_bytes()
    def fail(*args, **kwargs):
        raise RuntimeError("injected synth failure")
    monkeypatch.setattr(storage, "render_piano", fail)
    with pytest.raises(RuntimeError, match="injected synth"):
        prepare_learning(tmp_path, "light")
    assert (directory / "piano.wav").read_bytes() == before
    assert not list((tmp_path / "learning").glob(".prepare-*"))
    assert len(list((tmp_path / "learning").glob("*/learning.json"))) == 1


def test_mode_api_exports_are_allowlisted_and_explicitly_named(tmp_path, monkeypatch):
    from karaoke_generator import studio_web, web
    monkeypatch.setattr(studio_web, "STUDIO_ROOT", tmp_path)
    studio_web.STUDIO_JOBS.clear()
    original = write_saved_result(tmp_path / "legacy" / "result")
    client = TestClient(web.app)
    response = client.post("/api/studio/jobs/legacy/learning/light")
    assert response.status_code == 200, response.text
    data = response.json()
    piano = client.get(data["artifacts"]["piano.wav"])
    assert piano.status_code == 200 and "piano-light.wav" in piano.headers["content-disposition"]
    assert client.get(data["artifacts"]["learning.json"]).json()["mode"] == "light"
    assert client.get(data["artifacts"]["piano.wav"].replace("/light/", "/pro/")).status_code == 404
    assert client.get(data["artifacts"]["piano.wav"].replace("piano.wav", "studio.json")).status_code == 404
    assert client.post("/api/studio/jobs/legacy/learning/invalid").status_code == 404
    assert {name: (tmp_path / "legacy/result" / name).read_bytes() for name in original} == original


def test_energy_dip_supports_reattack_with_continuous_pitch(tmp_path):
    rate = 16000
    time = np.arange(rate) / rate
    envelope = np.ones(rate)
    envelope[round(0.45*rate):round(0.48*rate)] = np.linspace(1, 0.02, round(0.03*rate))
    envelope[round(0.48*rate):round(0.5*rate)] = 0.02
    envelope[round(0.5*rate):round(0.51*rate)] = np.linspace(0.02, 1, round(0.01*rate))
    path = tmp_path / "vocal.wav"
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        audio.writeframes((np.sin(2*np.pi*440*time)*envelope*16000).astype("<i2").tobytes())
    attacks, _ = energy_attacks(path)
    assert len(attacks) == 1
    assert abs(attacks[0] - 0.5) < 0.02
    notes, _ = stable_note_events([frame(i/100, 69) for i in range(100)], duration=1,
                                  hop_seconds=0.01, attack_times=attacks)
    assert len(notes) == 2 and notes[0].midi == notes[1].midi == 69


def test_unregistered_stale_alignment_is_not_used_and_pro_survives_bad_alignment(tmp_path):
    write_saved_result(tmp_path)
    with pytest.raises(ValueError, match="Нет временной разметки"):
        prepare_learning(tmp_path, "light", alignment_available=False)
    pro, _ = prepare_learning(tmp_path, "pro", alignment_available=False)
    assert pro["words"] == [] and pro["notes"]
    raw = json.loads((tmp_path / "alignment.json").read_text())
    raw["lines"][0]["words"][0]["end"] = 100
    (tmp_path / "alignment.json").write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="Временная разметка текста повреждена"):
        prepare_learning(tmp_path, "light")
    pro, _ = prepare_learning(tmp_path, "pro")
    assert pro["words"] == [] and pro["notes"]
