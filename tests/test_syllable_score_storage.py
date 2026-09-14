"""S14/S15/S16: whole publications, conflict safety, retained old bases, no GET work."""
import copy
import json
import wave
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from karaoke_generator import syllable_score_storage as storage, syllable_score_web, studio_web
from karaoke_generator.syllable_score import build_score, normalize_score
from karaoke_generator.timing_cache import file_sha256, fingerprint

REAL_BUILD = storage._build


@pytest.fixture
def song(tmp_path, monkeypatch):
    result = tmp_path / "job" / "result"
    result.mkdir(parents=True)
    for name, value in {"original.mp3": b"original audio", "lyrics.txt": "ма да".encode(),
                        "vocals.wav": b"saved vocal", "melody.json": b"{}",
                        "piano.wav": b"original piano", "manual-lyrics.json": b'{"old":"manual"}'}.items():
        (result / name).write_bytes(value)
    manifest = {"schema_version": 1, "id": "job", "status": "ready", "timeline": {"duration": 5},
                "artifacts": {name: name for name in ("lyrics.txt", "vocals.wav", "melody.json")}}
    (result / "studio.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(storage, "_algorithms", lambda: {"test": "pure-builder-1"})
    calls = []

    def build(result_dir, job_id, _):
        calls.append(job_id)
        inputs = storage._input_identity(result_dir)
        suffix = file_sha256(result_dir / "melody.json")[:5]
        notes = [{"id": f"note-{i}-{suffix}", "start": a, "end": b, "midi": 60}
                 for i, (a, b) in enumerate(((.5, 1), (1, 1.5), (2, 3)))]
        source = {"audio_sha256": inputs["audio_sha256"], "vocals_sha256": file_sha256(result_dir / "vocals.wav"),
                  "lyrics_sha256": file_sha256(result_dir / "lyrics.txt"), "notes_sha256": fingerprint({"notes": notes}),
                  "duration": 5, "language": "ru"}
        alignment = {"language": "ru", "duration": 5, "source_text_sha256": "text", "backend": "fixture",
                     "lines": [{"text": "ма да", "start": .5, "end": 3,
                        "words": [{"text": t, "normalized": t, "start": a, "end": b,
                                   "aligned": True, "alignment_source": "direct"}
                                  for t, a, b in (("ма", .5, 1.5), ("да", 2, 3))]}]}
        key = fingerprint({"inputs": inputs, "source": source})
        doc = build_score("ма да", alignment, notes, job_id=job_id, source=source, base_analysis_key=key)
        return doc, notes, inputs

    monkeypatch.setattr(storage, "_build", build)
    return result, manifest, calls


def prepared(song):
    result, manifest, _ = song
    state = storage.prepare_score(result, "job", manifest)
    assert state["status"] == "ready", state
    return storage.read_artifact(result, "job", state["published"]["key"])


def changed(document, text="ручная правка"):
    result = copy.deepcopy(document)
    result["units"][0].update(text=text, origin="manual", manual_override=True)
    return result


def save(song, document, request_id="save-1"):
    return storage.save_revision(song[0], "job", {"request_id": request_id,
        "base_revision": document["revision"], "document": document})


def files(root):
    return {str(path.relative_to(root)): file_sha256(path) for path in root.rglob("*") if path.is_file()}


def test_get_without_preparation_neither_writes_nor_builds(song):
    before = files(song[0])
    state = storage.read_state(song[0], "job")
    assert state["status"] == "not_prepared" and state["effective_url"] is None
    assert files(song[0]) == before and song[2] == []


def test_complete_auto_immutable_with_payload_hash_and_note_snapshot(song):
    before = files(song[0])
    doc = prepared(song)
    state = storage.read_state(song[0], "job")
    record = state["published"]
    path = song[0] / "syllable-score" / "auto" / record["key"] / "syllable-score.json"
    assert record["payload_sha256"] == file_sha256(path)
    assert doc["revision"] == 0 and len(doc["note_links"]) == 3
    assert files(song[0]).items() >= before.items()
    snapshot = files(song[0])
    assert storage.read_artifact(song[0], "job", record["key"]) == doc
    assert storage.read_state(song[0], "job") == state
    assert files(song[0]) == snapshot  # reading all ready endpoints never writes
    assert prepared(song) == doc and song[2] == ["job"]


def test_put_retries_are_idempotent_and_different_body_conflicts(song):
    original = prepared(song)
    submitted = changed(original)
    first = save(song, submitted)
    assert first["revision"] == 1 and submitted["revision"] == 0
    assert save(song, submitted)["document"] == first["document"]
    assert storage.read_revision(song[0], "job", 1) == first["document"]
    with pytest.raises(storage.ScoreError) as error:
        save(song, changed(original, "другой запрос"))
    assert error.value.status == 409 and error.value.details["code"] == "request_conflict"
    assert storage.read_state(song[0], "job")["revision"] == 1


def test_client_cannot_replace_immutable_base_provenance(song):
    original = prepared(song)
    submitted = changed(original)
    submitted["provenance"] = {"builder_version": "invented", "inputs": {"fake": True}}
    saved = save(song, submitted)["document"]
    assert saved["provenance"]["builder_version"] == original["provenance"]["builder_version"]
    assert "inputs" not in saved["provenance"]  # pure fixture has no input map
    assert saved["provenance"]["base_revision"] == 0


def test_concurrent_tabs_only_one_save_succeeds(song):
    original = prepared(song)
    def attempt(i):
        try:
            return save(song, changed(original, f"вкладка {i}"), f"save-{i}")
        except storage.ScoreError as error:
            return error
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, (1, 2)))
    assert sum(isinstance(result, dict) for result in results) == 1
    error = next(result for result in results if isinstance(result, storage.ScoreError))
    assert error.status == 409 and error.details["current_revision"] == 1


def test_invalid_overlapping_links_return_422_without_publishing_anything(song):
    original = prepared(song)
    before = files(song[0])
    invalid = copy.deepcopy(original)
    invalid["note_links"][-1].update(source_note_id=invalid["note_links"][0]["source_note_id"], start=.5, end=1)
    invalid = normalize_score(invalid)
    with pytest.raises(storage.ScoreError) as error:
        save(song, invalid)
    assert error.value.status == 422
    assert files(song[0]) == before


def test_bad_derived_intervals_are_rejected_not_silently_normalized(song):
    invalid = prepared(song)
    invalid["units"][0]["end"] = 4
    with pytest.raises(storage.ScoreError) as error:
        save(song, invalid)
    assert error.value.status == 422


def test_failed_index_write_preserves_last_revision_and_orphan_is_not_readable(song, monkeypatch):
    doc = save(song, changed(prepared(song)))["document"]
    index_path = song[0] / "syllable-score/index.json"
    before = index_path.read_bytes()
    original_write = storage._write
    def fail_index(path, *args, **kwargs):
        if path.name == "index.json":
            raise OSError("disk full")
        return original_write(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(storage, "_write", fail_index)
        with pytest.raises(OSError):
            save(song, changed(doc, "остаться локально"), "save-2")
    assert index_path.read_bytes() == before
    assert storage.read_revision(song[0], "job", 1) == doc
    with pytest.raises(storage.ScoreError) as error:
        storage.read_revision(song[0], "job", 2)
    assert error.value.status == 404
    repaired = save(song, changed(doc, "остаться локально"), "save-2")
    assert repaired["revision"] == 3  # immutable orphan 2 remains preserved
    assert not list(song[0].rglob(".score-*.tmp"))


def test_new_analysis_retains_manual_base_and_can_still_save_it(song):
    old = save(song, changed(prepared(song)))["document"]
    old_key = old["base_analysis_key"]
    (song[0] / "melody.json").write_text('{"changed":true}')
    candidate = prepared(song)
    state = storage.read_state(song[0], "job")
    assert candidate["base_analysis_key"] != old_key
    assert state["candidate_available"] is True
    assert state["base_analysis_key"] == old_key and state["effective_url"].endswith("/revisions/1")
    latest = save(song, changed(old, "старая партия после анализа"), "save-old-base")["document"]
    assert latest["base_analysis_key"] == old_key
    assert storage.read_artifact(song[0], "job", old_key)["revision"] == 0
    candidate["revision"] = latest["revision"]
    with pytest.raises(storage.ScoreError) as error:
        save(song, candidate, "no-silent-rebase")
    assert error.value.status == 409 and error.value.details["code"] == "base_conflict"


def test_changed_original_text_or_identity_cannot_be_written_into_saved_base(song):
    original = prepared(song)
    invalid = copy.deepcopy(original)
    invalid["canonical_text"] = "подмена"
    with pytest.raises(storage.ScoreError) as error:
        save(song, invalid)
    assert error.value.status == 409
    invalid = copy.deepcopy(original)
    invalid["source"]["audio_sha256"] = "e" * 64
    with pytest.raises(storage.ScoreError) as error:
        save(song, invalid)
    assert error.value.details["code"] == "source_mismatch"


@pytest.mark.parametrize("corruption", ["truncated", "whitespace"])
def test_corrupt_or_unindexed_auto_is_never_served_as_ready(song, corruption):
    doc = prepared(song)
    key = doc["base_analysis_key"]
    file = song[0] / "syllable-score/auto" / key / "syllable-score.json"
    file.write_text('{"partial":' if corruption == "truncated" else file.read_text() + "\n")
    state = storage.read_state(song[0], "job")
    assert state["status"] == "failed" and state["published"] is None
    with pytest.raises(storage.ScoreError) as error:
        storage.read_artifact(song[0], "job", key)
    assert error.value.status == 409
    for key in ("../manual-lyrics", "f" * 64, "BAD", ".."):
        with pytest.raises(storage.ScoreError) as error:
            storage.read_artifact(song[0], "job", key)
        assert error.value.status == 404


def test_failed_new_candidate_preserves_previous_manual_and_published(song, monkeypatch):
    saved = save(song, changed(prepared(song)))["document"]
    previous = storage.read_state(song[0], "job")
    (song[0] / "vocals.wav").write_bytes(b"changed analysis")
    def fail(*_):
        raise ValueError("unavailable evidence")
    monkeypatch.setattr(storage, "_build", fail)
    state = storage.prepare_score(song[0], "job", song[1])
    assert state["status"] == "failed" and state["published"] == previous["published"]
    assert storage.read_revision(song[0], "job", 1) == saved
    assert state["draft"] == previous["draft"]


def test_queue_request_identity_and_interrupted_state_without_read_writes(song):
    state, status, operation = storage.queue_prepare(song[0], "job", song[1], {"request_id": "prepare-1", "retry": False})
    assert status == 202 and state["status"] == "queued" and operation
    assert storage.queue_prepare(song[0], "job", song[1], {"request_id": "prepare-1", "retry": False})[2] is None
    with pytest.raises(storage.ScoreError) as error:
        storage.queue_prepare(song[0], "job", song[1], {"request_id": "prepare-1", "retry": True})
    assert error.value.status == 409
    storage._ACTIVE.discard(operation)
    before = files(song[0])
    assert storage.read_state(song[0], "job")["status"] == "interrupted"
    assert files(song[0]) == before and song[2] == []
    with pytest.raises(storage.ScoreError) as error:
        storage.queue_prepare(song[0], "job", song[1], {"request_id": "prepare-2"})
    assert error.value.details["code"] == "retry_required"


def test_legacy_endpoint_reads_only_existing_raw_file(song):
    before = files(song[0])
    assert storage.read_legacy(song[0]) == {"old": "manual"}
    assert files(song[0]) == before
    (song[0] / "manual-lyrics.json").unlink()
    with pytest.raises(storage.ScoreError) as error:
        storage.read_legacy(song[0])
    assert error.value.status == 404 and not (song[0] / "manual-lyrics.json").exists()


def test_http_contract_unknown_job_prepare_get_put_conflict_and_507(song, monkeypatch):
    monkeypatch.setattr(studio_web, "STUDIO_ROOT", song[0].parent.parent)
    monkeypatch.setattr(studio_web, "STUDIO_JOBS", {})
    app = FastAPI()
    app.include_router(syllable_score_web.router)
    client = TestClient(app)
    url = "/api/studio/jobs/job/syllables"
    assert client.get("/api/studio/jobs/missing/syllables").status_code == 404
    assert client.get(url).json()["status"] == "not_prepared"
    response = client.post(url, json={"request_id": "http-prepare", "retry": False})
    assert response.status_code == 202 and response.json()["operation_id"]
    state = client.get(url).json()
    assert state["status"] == "ready"
    doc = client.get(state["effective_url"]).json()
    assert doc["schema_version"] == 1
    body = {"request_id": "http-save", "base_revision": 0, "document": changed(doc)}
    saved = client.put(url, json=body)
    assert saved.status_code == 200 and saved.json()["revision"] == 1
    assert client.put(url, json=body).json()["revision"] == 1
    body["request_id"] = "outdated-tab"
    assert client.put(url, json=body).status_code == 409
    bad = saved.json()["document"]
    bad["units"][0]["end"] = 5
    assert client.put(url, json={"request_id": "invalid", "base_revision": 1, "document": bad}).status_code == 422
    def full_disk(*_):
        raise OSError("disk full")
    monkeypatch.setattr(storage, "save_revision", full_disk)
    assert client.put(url, json=body).status_code == 507
    assert client.get(url + "/legacy").json() == {"old": "manual"}


def test_explicit_build_uses_displayed_full_notes_and_records_evidence_provenance(song, monkeypatch):
    from karaoke_generator import v3_storage, syllables, lyric_recovery
    source_notes = [{"id": "displayed-note", "start": .5, "end": 3, "midi": 61,
                     "intervals": [{"start": .5, "end": 1}, {"start": 2, "end": 3}],
                     "source_note_ids": ["original-event-1", "original-event-2"]}]
    full = {"cache_key": "b" * 64, "timeline": {"duration": 5}, "notes": source_notes, "language": "ru"}
    calls = []
    def prepare_full(*args, **kwargs):
        calls.append("full")
        return full, song[0]
    def prepare_lyrics(audio, words, notes, **kwargs):
        calls.append("lyric")
        assert notes[0].id == "displayed-note"
        assert kwargs["cache_root"] == song[0] / "lyric-recovery-v3"
        return {"status": "unavailable", "reason": "fixture without inference", "words": [], "duration": 5}
    monkeypatch.setattr(v3_storage, "ready_modes", lambda *_: {})
    monkeypatch.setattr(v3_storage, "prepare_v3", prepare_full)
    monkeypatch.setattr(syllables, "load_character_evidence", lambda *_: {"words": [], "diagnostics": {"inference_performed": False}})
    monkeypatch.setattr(lyric_recovery, "prepare_lyric_evidence", prepare_lyrics)
    before = files(song[0])
    document, notes, inputs = REAL_BUILD(song[0], "job", song[1])
    assert notes == source_notes and calls == ["full", "lyric"]
    assert document["source"]["notes_sha256"] == fingerprint({"notes": source_notes})
    assert document["provenance"]["full_melody_key"] == full["cache_key"]
    assert document["provenance"]["lyric_evidence"]["reason"] == "fixture without inference"
    assert document["provenance"]["inputs"] == inputs
    assert files(song[0]) == before
    # A changed saved CTC artifact is part of the input identity even when a
    # proposed timing later proves unusable. It cannot reuse the old score key.
    ctc = song[0] / "work/timing-cache/refined-new.json"
    ctc.parent.mkdir(parents=True)
    ctc.write_text('{"words":[]}')
    changed_document, _, _ = REAL_BUILD(song[0], "job", song[1])
    assert changed_document["base_analysis_key"] != document["base_analysis_key"]


def test_old_manual_notes_and_piano_stay_bound_after_reanalysis_without_prepare(song, monkeypatch):
    pure_build = storage._build
    def build_with_full(result, job_id, manifest):
        doc, notes, inputs = pure_build(result, job_id, manifest)
        full_key = fingerprint({"notes": notes})
        directory = result / "learning-v3" / full_key
        directory.mkdir(parents=True, exist_ok=True)
        piano = directory / "piano.wav"
        with wave.open(str(piano), "wb") as writer:
            writer.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            writer.writeframes(bytes(5 * 8000 * 2))
        full = {"schema_version": 3, "mode": "pro", "cache_key": full_key,
                "timeline": {"duration": 5}, "notes": notes, "piano_sha256": file_sha256(piano)}
        (directory / "learning.json").write_text(json.dumps(full))
        doc["provenance"].update(full_melody_key=full_key, full_melody_mode="pro", full_piano_sha256=full["piano_sha256"])
        return doc, notes, inputs
    monkeypatch.setattr(storage, "_build", build_with_full)
    old = save(song, changed(prepared(song)))["document"]
    old_key = old["base_analysis_key"]
    old_snapshot = storage.read_source_notes(song[0], "job", old_key)
    old_path = storage.source_piano_path(song[0], "job", old_key)
    old_hash = file_sha256(old_path)
    (song[0] / "melody.json").write_text('{"new notes":true}')
    fresh = prepared(song)
    assert fresh["provenance"]["full_melody_key"] != old["provenance"]["full_melody_key"]
    before = files(song[0])
    monkeypatch.setattr(storage, "_build", lambda *_: pytest.fail("snapshot GET prepared audio"))
    assert storage.read_source_notes(song[0], "job", old_key) == old_snapshot
    assert storage.source_piano_path(song[0], "job", old_key) == old_path
    assert old_snapshot["source_piano_sha256"] == old_hash
    assert old_snapshot["full_melody_mode"] == "pro"
    assert files(song[0]) == before
    monkeypatch.setattr(studio_web, "STUDIO_ROOT", song[0].parent.parent)
    monkeypatch.setattr(studio_web, "STUDIO_JOBS", {})
    app = FastAPI()
    app.include_router(syllable_score_web.router)
    client = TestClient(app)
    response = client.get(old_snapshot["source_piano_url"])
    assert response.status_code == 200 and response.content == old_path.read_bytes()
    # A changed old piano must fail closed, even while a fresh full base exists.
    raw = old_path.read_bytes()
    old_path.write_bytes(raw[:-2] + b"\1\0")
    assert client.get(old_snapshot["source_piano_url"]).status_code == 409
    with pytest.raises(storage.ScoreError) as error:
        storage.read_source_notes(song[0], "job", old_key)
    assert error.value.details["code"] == "invalid_source_piano"
