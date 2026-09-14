"""Contract checks for actual audio proposals, not human quality acceptance."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import wave

import numpy as np
import pytest

from karaoke_generator import manual_auto as auto


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _voice_audio(path, duration=12, *, two_timbres=True, silent=False):
    """Two harmonic envelopes at the SAME 180 Hz fundamental, no pitch oracle."""
    rate = 16000
    t = np.arange(round(rate * duration)) / rate
    low = sum(np.sin(2 * np.pi * 180 * h * t) / h for h in range(1, 10))
    high = sum(np.sin(2 * np.pi * 180 * h * t) * (1 if 9 <= h <= 18 else .05)
               for h in range(1, 22)) / 5
    samples = np.where(t < duration / 2, low, high) if two_timbres else low
    samples = samples / max(1, abs(samples).max()) * .5
    if silent:
        samples[:] = 0
    with wave.open(str(path), "wb") as out:
        out.setparams((1, 2, rate, len(samples), "NONE", "not compressed"))
        out.writeframes((samples * 32767).astype("<i2").tobytes())
    return samples.astype(np.float32), rate


def _alignment(words, duration=12):
    return {"language": "en", "duration": duration, "lines": [{"words": [
        {"text": text, "start": start, "end": end,
         "timing": {"source": "interpolated", "generated": {"start": start, "end": end}}}
        for text, start, end in words]}]}


def _result(tmp_path, text="Go home!", aligned=None, asr=None, **audio_options):
    result = tmp_path / "job" / "result"
    result.mkdir(parents=True)
    (result / "lyrics.txt").write_text(text, encoding="utf-8")
    (result / "original.wav").write_bytes(b"original audio remains untouched")
    samples, rate = _voice_audio(result / "vocals.wav", **audio_options)
    aligned = aligned or [("Go", 1., 1.5), ("home!", 1.5, 2.)]
    _write_json(result / "alignment.json", _alignment(aligned, len(samples) / rate))
    if asr is not None:
        _asr(result, asr)
    return result


def _asr(result, words, *, vocal_hash=None, text_hash=None, backend="faster-whisper"):
    spec = {"audio_sha256": vocal_hash or auto._hash_file(result / "vocals.wav"),
            "text_sha256": text_hash or auto._hash_file(result / "lyrics.txt"),
            "duration": 12, "backend": backend, "model": {"name": "fixture-asr"}}
    key = hashlib.sha256(json.dumps(spec, sort_keys=True, allow_nan=False).encode()).hexdigest()
    path = result / "work/timing-cache" / f"asr-{key}.json"
    _write_json(path, {"spec": spec, "words": [
        {"text": word, "start": start, "end": end} for word, start, end in words]})
    return path


def test_txt_spelling_and_approximate_times_survive_weak_asr(tmp_path):
    result = _result(tmp_path, text="Recognise me!", aligned=[("Recognize", 1., 1.7), ("me", 1.7, 2.)])
    data = auto.propose_manual_annotations(result)
    assert [a["text"] for a in data["annotations"]] == ["Recognise", "me!"]
    assert [(a["start"], a["end"]) for a in data["annotations"]] == [(1., 1.7), (1.7, 2.)]
    assert all(a["approximate"] for a in data["annotations"])
    assert data["human_verified"] is False


def test_repeated_phrase_and_single_echo_have_independent_occurrences(tmp_path):
    result = _result(tmp_path, asr=[("Go", 1., 1.5), ("home", 1.5, 2.),
                                   ("Go", 5., 5.5), ("home", 5.5, 6.), ("home", 8., 8.5),
                                   ("alien", 9., 9.4)])
    data = auto.propose_manual_annotations(result)
    assert [a["text"] for a in data["annotations"]] == ["Go", "home!", "Go", "home!", "home!"]
    assert data["diagnostics"]["text"]["additional_occurrences"] == 3
    assert len({a["occurrence_id"] for a in data["annotations"]}) == 5
    assert len({a["annotation_id"] for a in data["annotations"]}) == 5
    assert all(a["annotation_id"] != a["occurrence_id"] for a in data["annotations"])
    homes = [a for a in data["annotations"] if a["text"] == "home!"]
    assert len({a["source_word_id"] for a in homes}) == 1
    assert data["diagnostics"]["text"]["skipped"]["outside_txt"] == 1
    assert all(o["complete"] and len(o["annotation_ids"]) == 1 for o in data["occurrences"])


def test_same_word_on_multiple_notes_cannot_create_occurrences(tmp_path):
    result = _result(tmp_path)
    _write_json(result / "melody.json", {"notes": [{"text": "home!", "start": i} for i in range(100)]})
    _write_json(result / "learning-v3/index.json", {"context": ["home!"] * 200})
    data = auto.propose_manual_annotations(result)
    assert len(data["annotations"]) == 2
    assert data["diagnostics"]["text"]["additional_occurrences"] == 0


def test_distinct_asr_echo_can_overlap_an_already_matched_source_word(tmp_path):
    result = _result(tmp_path, text="домой", aligned=[("домой", 1., 2.)],
                     asr=[("домой", 1., 2.), ("домой", 1.3, 2.4)])
    data = auto.propose_manual_annotations(result)
    assert [(a["start"], a["end"]) for a in data["annotations"]] == [(1., 2.), (1.3, 2.4)]
    assert len({a["occurrence_id"] for a in data["annotations"]}) == 2


@pytest.mark.parametrize("bad_evidence", ["wrong-audio", "wrong-txt", "uniform", "silent"])
def test_invalid_evidence_and_silence_never_propose_repeats(tmp_path, bad_evidence):
    result = _result(tmp_path, silent=bad_evidence == "silent")
    kwargs = {"vocal_hash": "a" * 64} if bad_evidence == "wrong-audio" else {}
    if bad_evidence == "wrong-txt":
        kwargs["text_hash"] = "b" * 64
    if bad_evidence == "uniform":
        kwargs["backend"] = "uniform"
    _asr(result, [("home", 7., 8.)], **kwargs)
    data = auto.propose_manual_annotations(result)
    assert len(data["annotations"]) == 2
    if bad_evidence != "silent":
        assert data["diagnostics"]["asr"]["rejected"]


def test_role_proposal_processes_pcm_and_distinguishes_timbres_at_same_pitch(tmp_path):
    result = _result(tmp_path, text="early late", aligned=[("early", 1., 2.), ("late", 9., 10.)])
    data = auto.propose_manual_annotations(result)
    diagnostic = data["diagnostics"]["roles"]
    assert diagnostic["audio_processed"] is True
    assert diagnostic["frame_count"] > 1000
    assert diagnostic["feature_windows"] >= 10
    assert diagnostic["feature_sha256"]
    assert diagnostic["uses_pitch"] is False
    assert len(data["roles"]) >= 2
    roles = [a["role_id"] for a in data["annotations"]]
    assert roles[0] and roles[1] and roles[0] != roles[1]
    assert all(r["approximate"] for r in data["roles"])
    assert not any("gender" in r for r in data["roles"])


def test_silent_or_missing_audio_is_diagnosable_and_editor_text_remains(tmp_path):
    result = _result(tmp_path, silent=True)
    data = auto.propose_manual_annotations(result)
    assert data["roles"] == []
    assert all(a["role_id"] is None for a in data["annotations"])
    (result / "vocals.wav").unlink()
    missing = auto.propose_manual_annotations(result)
    assert missing["diagnostics"]["roles"]["status"] == "unavailable"
    assert len(missing["annotations"]) == 2


def test_run_is_immutable_and_cache_hits_do_not_decode_audio(tmp_path, monkeypatch):
    result = _result(tmp_path)
    data = auto.propose_manual_annotations(result)
    path = result / "manual-auto/runs" / f"{data['run_id']}.json"
    before = path.read_bytes()
    monkeypatch.setattr(auto, "_read_vocal", lambda _: pytest.fail("Cache hit decoded audio"))
    assert auto.propose_manual_annotations(result) == data
    assert auto.ready_manual_proposal(result) == data
    assert path.read_bytes() == before
    (result / "lyrics.txt").write_text("new lyrics", encoding="utf-8")
    assert auto.ready_manual_proposal(result) is None
    assert path.read_bytes() == before


def test_force_and_parameter_change_recompute_and_preserve_previous_runs(tmp_path, monkeypatch):
    result = _result(tmp_path)
    first = auto.propose_manual_annotations(result)
    old = result / "manual-auto/runs" / f"{first['run_id']}.json"
    before = old.read_bytes()
    real = auto._read_vocal
    calls = []

    def record(path):
        calls.append(path)
        return real(path)

    monkeypatch.setattr(auto, "_read_vocal", record)
    forced = auto.propose_manual_annotations(result, force=True)
    candidate = auto.propose_manual_annotations(result, profile={"parameters": {"timing_offset_seconds": .5}})
    assert len(calls) == 2
    assert len({first["run_id"], forced["run_id"], candidate["run_id"]}) == 3
    assert candidate["annotations"][0]["start"] == first["annotations"][0]["start"] + .5
    assert old.read_bytes() == before
    assert auto.ready_manual_proposal(result) is None
    assert auto.ready_manual_proposal(result, require_current_algorithm=False) == candidate


def test_automatic_runs_ignore_manual_layer_and_human_bounds(tmp_path):
    result = _result(tmp_path)
    original = {p.name: auto._hash_file(p) for p in result.iterdir() if p.is_file()}
    _write_json(result / "manual-lyrics.json", {"annotations": [{"text": "foreign", "start": 5}]})
    alignment = json.loads((result / "alignment.json").read_text())
    alignment["lines"][0]["words"][0]["start"] = 7
    alignment["lines"][0]["words"][0]["end"] = 8
    _write_json(result / "alignment.json", alignment)
    data = auto.propose_manual_annotations(result)
    assert data["annotations"][0]["start"] == 1
    assert [a["text"] for a in data["annotations"]] == ["Go", "home!"]
    for name in ("vocals.wav", "original.wav", "lyrics.txt"):
        assert auto._hash_file(result / name) == original[name]
    assert json.loads((result / "manual-lyrics.json").read_text())["annotations"][0]["text"] == "foreign"


def test_unplaceable_source_is_retained_without_audio_free_invention(tmp_path):
    result = _result(tmp_path, text="first middle last", aligned=[("middle", 4., 5.)])
    data = auto.propose_manual_annotations(result)
    assert [a["text"] for a in data["annotations"]] == ["first", "middle", "last"]
    assert len(data["unplaced"]) == 2
    assert data["canonical_text"] == "first middle last"


def test_approximate_fuzzy_repeat_uses_txt_and_requires_phrase_context(tmp_path):
    result = _result(tmp_path, text="Recognise me", aligned=[("Recognise", 1., 1.5), ("me", 1.5, 2.)],
                     asr=[("Recognize", 5., 5.5), ("me", 5.5, 6.), ("Recognize", 9., 9.5)])
    data = auto.propose_manual_annotations(result)
    assert [a["text"] for a in data["annotations"]] == ["Recognise", "me", "Recognise", "me"]
    assert data["diagnostics"]["text"]["skipped"]["weak_repeat_context"] == 1


def test_duplicate_asr_cache_does_not_multiply_words(tmp_path):
    result = _result(tmp_path, asr=[("home", 5., 6.)])
    path = next((result / "work/timing-cache").glob("asr-*.json"))
    data = json.loads(path.read_text())
    data["spec"]["model"]["name"] = "second-fixture"
    key = hashlib.sha256(json.dumps(data["spec"], sort_keys=True, allow_nan=False).encode()).hexdigest()
    _write_json(path.with_name(f"asr-{key}.json"), data)
    proposed = auto.propose_manual_annotations(result)
    assert len(proposed["annotations"]) == 3


def test_verified_asr_accepts_existing_normalized_prompt_hash(tmp_path):
    result = _result(tmp_path, text="  Go   home!\r\n\r\n")
    prompt_hash = hashlib.sha256(b"Go home!\n").hexdigest()
    _asr(result, [("home", 5., 6.)], text_hash=prompt_hash)
    data = auto.propose_manual_annotations(result)
    assert len(data["annotations"]) == 3
    assert data["diagnostics"]["asr"]["cache_provenance_verified"] is True


def test_corrupt_result_is_not_silently_reused_or_overwritten(tmp_path):
    result = _result(tmp_path)
    first = auto.propose_manual_annotations(result)
    path = result / "manual-auto/runs" / f"{first['run_id']}.json"
    tampered = deepcopy(first)
    tampered["annotations"][0]["text"] = "tampered"
    _write_json(path, tampered)
    assert auto.ready_manual_proposal(result) is None
    new = auto.propose_manual_annotations(result)
    assert new["run_id"] != first["run_id"]
    assert json.loads(path.read_text())["annotations"][0]["text"] == "tampered"


def test_profile_rejects_unsupported_or_nonfinite_parameter():
    with pytest.raises(ValueError):
        auto.normalize_parameters({"magic_new_model": 1})
    with pytest.raises(ValueError):
        auto.normalize_parameters({"timing_offset_seconds": float("nan")})


def test_millisecond_rounding_never_crosses_fractional_recording_end():
    duration = 12.3456
    data = auto.build_proposals("home", _alignment([("home", 12, duration)], duration), [],
                                duration=duration, features=None)
    assert data["annotations"][0]["end"] <= duration
    assert data["annotations"][0]["start"] < data["annotations"][0]["end"]
