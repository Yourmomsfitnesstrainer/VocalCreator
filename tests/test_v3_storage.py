import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from karaoke_generator.models import AlignedLine, AlignedWord, AlignmentQuality, AlignmentResult
from karaoke_generator.studio_models import MelodyResult, NoteEvent
from karaoke_generator.v3_storage import prepare_v3, ready_modes


def saved(root: Path):
    root.mkdir(parents=True)
    words = [AlignedWord("ma", "ma", 0, .4, .9, True, "manual", timing={"source": "manual"}),
             AlignedWord("ma", "ma", .4, 1, .9, True, "manual", timing={"source": "manual"})]
    alignment = AlignmentResult("en", 1, "fixture", [AlignedLine("ma ma", 0, 1, words)],
                                "manual", AlignmentQuality(2, 2, 2, 0, 1))
    melody = MelodyResult({"duration": 1}, [], [NoteEvent("same", 0, 1, 69, 0, .9, "fixture")], [], {}, {})
    (root / "melody.json").write_text(json.dumps(melody.to_dict()))
    (root / "alignment.json").write_text(json.dumps(alignment.to_dict()))
    (root / "lyrics.txt").write_text("ma ma\nlost!")
    available = {"melody.json", "alignment.json", "lyrics.txt"}
    (root / "studio.json").write_text(json.dumps({"schema_version": 1, "id": root.parent.name,
        "status": "complete", "timeline": {"duration": 1}, "artifacts": dict.fromkeys(available, "saved")}))
    return available


def test_complete_text_no_extra_attacks_immutable_inputs_and_cache(tmp_path, monkeypatch):
    import karaoke_generator.v3_storage as storage
    available = saved(tmp_path / "result")
    root = tmp_path / "result"
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    for mode in ("light", "medium", "pro"):
        data, directory = prepare_v3(root, mode, available=available)
        assert directory.parent.name == "learning-v3"
        assert [word["text"] for word in data["words"]] == ["ma", "ma", "lost!"]
        assert len(data["notes"]) == data["piano"]["note_count"] == 1
        assert data["words"][-1]["note_ids"] == []
        assert data["canonical_text"] == "ma ma\nlost!"
    assert set(ready_modes(root, available)) == {"light", "medium", "pro"}
    monkeypatch.setattr(storage, "render_piano", lambda *a, **kw: pytest.fail("render on cache hit"))
    cached, _ = prepare_v3(root, "pro", available=available)
    assert cached == data
    assert {name: (root / name).read_bytes() for name in before} == before
    assert not (root / "learning").exists()


def test_text_and_acoustic_evidence_invalidate_all_ready_modes(tmp_path):
    root = tmp_path / "result"; available = saved(root)
    initial, _ = prepare_v3(root, "light", available=available)
    (root / "lyrics.txt").write_text("ma ma\nlost! another")
    assert ready_modes(root, available) == {}
    changed, _ = prepare_v3(root, "light", available=available)
    assert changed["cache_key"] != initial["cache_key"]
    assert changed["words"][-1]["text"] == "another"
    evidence = root / "work/timing-cache/refined-fixture.json"; evidence.parent.mkdir(parents=True)
    evidence.write_text(json.dumps({"words": []}))
    assert ready_modes(root, available) == {}
    changed_again, _ = prepare_v3(root, "light", available=available)
    assert changed_again["cache_key"] != changed["cache_key"]


def test_failed_preparation_keeps_prior_mode_and_cleans_temporary(tmp_path, monkeypatch):
    import karaoke_generator.v3_storage as storage
    root = tmp_path / "result"; available = saved(root)
    first, directory = prepare_v3(root, "light", available=available)
    before = (directory / "piano.wav").read_bytes()
    def fail(*a, **kw):
        raise RuntimeError("injected failure")
    monkeypatch.setattr(storage, "render_piano", fail)
    with pytest.raises(RuntimeError, match="injected failure"):
        prepare_v3(root, "medium", available=available)
    assert set(ready_modes(root, available)) == {"light"}
    assert (directory / "piano.wav").read_bytes() == before
    assert not list(directory.parent.glob(".prepare-*"))


def test_old_job_read_does_not_prepare_and_api_artifacts_are_scoped(tmp_path, monkeypatch):
    from karaoke_generator import studio_web, web
    monkeypatch.setattr(studio_web, "STUDIO_ROOT", tmp_path)
    monkeypatch.setattr(studio_web, "STUDIO_JOBS", {})
    available = saved(tmp_path / "old/result")
    client = TestClient(web.app)
    prefix = "/api/studio/jobs/old"
    assert client.get(prefix).json()["v3_modes"] == {}
    assert not (tmp_path / "old/result/learning-v3").exists()
    text = client.get(prefix + "/text").json()
    assert text["canonical_text"] == "ma ma\nlost!"
    assert [text["canonical_text"][w["char_start"]:w["char_end"]] for w in text["words"]] == ["ma", "ma", "lost!"]
    prepared = client.post(prefix + "/v3/light"); assert prepared.status_code == 200, prepared.text
    data = prepared.json()
    modes = client.get(prefix).json()["v3_modes"]
    assert modes == {"light": data["artifacts"]["learning.json"]}
    assert client.get(modes["light"]).json()["artifacts"] == data["artifacts"]
    assert client.get(data["artifacts"]["piano.wav"]).status_code == 200
    assert client.get(data["artifacts"]["piano.wav"].replace("/light/", "/pro/")).status_code == 404
    assert client.get(data["artifacts"]["piano.wav"].replace("piano.wav", "lyrics.txt")).status_code == 404
    assert client.post(prefix + "/v3/invalid").status_code == 404
    assert client.post(prefix + "/tempo/light/0.3").status_code == 409
    tempo = client.post(prefix + "/tempo/light/1"); assert tempo.status_code == 200, tempo.text
    assert set(tempo.json()["artifacts"]) == {"piano.wav"}
    assert client.get(tempo.json()["artifacts"]["piano.wav"]).status_code == 200


def test_missing_alignment_retains_exact_text_and_unknown_timings(tmp_path):
    root = tmp_path / "result"; available = saved(root)
    available.remove("alignment.json")
    data, _ = prepare_v3(root, "light", available=available)
    assert len(data["words"]) == 3
    assert all(word["start"] is None and word["note_ids"] == [] for word in data["words"])
    assert data["notes"][0]["text_status"] == "unavailable"


@pytest.mark.parametrize("damage", ["truncate", "missing"])
def test_corrupt_piano_is_not_ready_and_retry_rebuilds_derived_only(tmp_path, damage):
    root = tmp_path / "result"; available = saved(root)
    originals = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
    data, directory = prepare_v3(root, "light", available=available)
    good = (directory / "piano.wav").read_bytes()
    if damage == "missing":
        (directory / "piano.wav").unlink()
    else:
        (directory / "piano.wav").write_bytes(b"bad wav")
    assert ready_modes(root, available) == {}
    repaired, repaired_dir = prepare_v3(root, "light", available=available)
    assert repaired_dir == directory and repaired["cache_key"] == data["cache_key"]
    assert (repaired_dir / "piano.wav").read_bytes() == good
    assert set(ready_modes(root, available)) == {"light"}
    assert {name: (root / name).read_bytes() for name in originals} == originals


@pytest.mark.parametrize("bad", [[], None, {"lines": [None]}])
def test_malformed_alignment_shape_keeps_canonical_text(tmp_path, bad):
    root = tmp_path / "result"; available = saved(root)
    (root / "alignment.json").write_text(json.dumps(bad))
    data, _ = prepare_v3(root, "light", available=available)
    assert data["canonical_text"] == "ma ma\nlost!"
    assert len(data["words"]) == 3 and all(word["start"] is None for word in data["words"])


@pytest.mark.parametrize("bad", [[], None, {"modes": []}])
def test_bad_cache_index_does_not_break_library(tmp_path, bad):
    root = tmp_path / "result"; available = saved(root)
    path = root / "learning-v3/index.json"; path.parent.mkdir()
    path.write_text(json.dumps(bad))
    assert ready_modes(root, available) == {}


def test_unregistered_vocal_does_not_supply_character_evidence(tmp_path, monkeypatch):
    import karaoke_generator.syllables as syllables
    root = tmp_path / "result"; available = saved(root)
    (root / "vocals.wav").write_bytes(b"stale unregistered audio")
    monkeypatch.setattr(syllables, "load_character_evidence", lambda *a: pytest.fail("Read unavailable vocal evidence"))
    data, _ = prepare_v3(root, "light", available=available)
    assert "vocals.wav" not in data["provenance"]["inputs"]
