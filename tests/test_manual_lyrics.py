"""Behavior contracts from SAVE/REF: no analysis, no lost edits, honest masks."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from karaoke_generator import manual_lyrics as storage
from karaoke_generator import manual_web, studio_web, v3_storage
from karaoke_generator.timing_cache import file_sha256


@pytest.fixture
def song(tmp_path, monkeypatch):
    result = tmp_path / "job" / "result"
    inputs = result.parent / "input"
    inputs.mkdir(parents=True)
    result.mkdir()
    (inputs / "audio.wav").write_bytes(b"original recording bytes")
    (inputs / "lyrics.txt").write_text("Hello again", encoding="utf-8")
    (result / "lyrics.txt").write_text("Hello again", encoding="utf-8")
    for name in ("vocals.wav", "instrumental.wav", "piano.wav", "melody.json", "alignment.json"):
        (result / name).write_bytes(b"{}" if name.endswith(".json") else b"unchanged sound")
    manifest = {"schema_version": 1, "id": "job", "timeline": {"duration": 20},
                "artifacts": {name: name for name in ("lyrics.txt", "alignment.json", "vocals.wav")}}
    (result / "studio.json").write_text(json.dumps(manifest))
    data = {"canonical_text": "Hello again", "language": "en", "cache_key": "a" * 64, "mode": "medium",
            "words": [{"id": "w1", "text": "Hello", "start": 1.1, "end": 2.5, "status": "manual"},
                      {"id": "w2", "text": "again", "start": 11.8, "end": 12.4}],
            "text_parts": [{"id": "p1", "word_id": "w1", "text": "Hel", "start": 1.1, "end": 1.8, "status": "manual"},
                           {"id": "p2", "word_id": "w1", "text": "lo", "start": 1.8, "end": 2.5, "status": "manual"}],
            # Rendering context and continued note labels must NEVER be imported.
            "notes": [{"labels": [{"word_id": "w1", "part_id": "p1", "start": 0, "end": 5,
                                    "text": "Hel", "continuation": True, "status": "context"}]}] * 7}
    monkeypatch.setattr(v3_storage, "ready_modes", lambda *_: {"medium": data})
    # A finished automatic proposal is optional; no test may invoke its analysis.
    try:
        from karaoke_generator import manual_auto
        monkeypatch.setattr(manual_auto, "ready_manual_proposal", lambda *_, **__: None)
    except ImportError:
        pass
    return result, manifest


def load(song):
    return storage.read_project(song[0], "job", song[1])


def save(song, project):
    return storage.save_project(song[0], "job", song[1], project)


def reviewed(song):
    project = load(song)
    project["reviews"] = [{"start": 0, "end": 5, "all_roles": True},
                          {"start": 10, "end": 12, "all_roles": True},
                          {"start": 15, "end": 16, "all_roles": True}]
    return save(song, project)


def test_import_uses_own_parts_once_and_read_does_not_write(song):
    before = {str(p): file_sha256(p) for p in song[0].rglob("*") if p.is_file()}
    project = load(song)
    assert [(a["text"], a["start"], a["end"]) for a in project["annotations"]] == [
        ("Hel", 1.1, 1.8), ("lo", 1.8, 2.5), ("again", 11.8, 12.4)]
    assert len(project["occurrences"]) == 2
    assert project["occurrences"][0]["annotation_ids"] == ["ann-p1", "ann-p2"]
    assert project["revision"] == 0 and project["reviews"] == []
    assert load(song)["annotations"] == project["annotations"]
    assert {str(p): file_sha256(p) for p in song[0].rglob("*") if p.is_file()} == before


def test_save_roundtrip_preserves_arbitrary_text_overlaps_roles_and_acoustics(song):
    before = {p.name: file_sha256(p) for p in song[0].iterdir() if p.is_file()}
    project = load(song)
    project["roles"] = [{"id": "lead", "name": "Тихий фон", "color": "#123ABC"}]
    project["annotations"][0].update(text="Свободный ручной текст", role_id="lead", start=1.5, end=3.5)
    saved = save(song, project)
    assert saved["revision"] == 1
    assert saved == load(song)
    assert saved["annotations"][1]["start"] == 1.8  # overlap was not normalized away
    assert saved["reviews"] == []
    assert {name: file_sha256(song[0] / name) for name in before} == before


def test_concurrent_tabs_cannot_lose_an_edit(song):
    first, second = load(song), load(song)
    first["annotations"][0]["text"] = "first"
    second["annotations"][0]["text"] = "second"
    def attempt(value):
        try:
            return save(song, value)
        except storage.ManualError as exc:
            return exc
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(attempt, (first, second)))
    assert sum(isinstance(r, dict) for r in results) == 1
    error = next(r for r in results if isinstance(r, storage.ManualError))
    assert error.status == 409 and error.details["code"] == "revision_conflict"
    assert error.details["current_revision"] == 1
    assert load(song)["annotations"][0]["text"] in {"first", "second"}
    assert second["annotations"][0]["text"] == "second" and second["revision"] == 0


def test_failed_atomic_write_retains_previous_bytes_and_retryable_draft(song, monkeypatch):
    previous = save(song, load(song))
    path = song[0] / "manual-lyrics.json"
    before = path.read_bytes()
    draft = copy.deepcopy(previous)
    draft["annotations"][0]["text"] = "must survive"
    def full_disk(*_):
        raise OSError("disk full")
    monkeypatch.setattr(storage.os, "replace", full_disk)
    with pytest.raises(OSError, match="disk full"):
        save(song, draft)
    assert path.read_bytes() == before
    assert draft["annotations"][0]["text"] == "must survive"
    assert not list(song[0].glob(".manual-*.tmp"))


@pytest.mark.parametrize("start,end", [(-1, 3), (2, 2), (5, 4), (19, 21), (float("nan"), 2), (None, 2), (True, 2)])
def test_invalid_group_cannot_partially_save(song, start, end):
    project = save(song, load(song))
    before = (song[0] / "manual-lyrics.json").read_bytes()
    project["annotations"][0]["text"] = "valid earlier edit"
    project["annotations"][1].update(start=start, end=end)
    with pytest.raises(storage.ManualError):
        save(song, project)
    assert (song[0] / "manual-lyrics.json").read_bytes() == before


def test_partial_copy_is_independent_incomplete_and_destinations_remain(song):
    project = load(song)
    initial = copy.deepcopy(project["annotations"])
    for ordinal in range(2):
        duplicate = copy.deepcopy(initial[0])
        aid, oid = f"copy-{ordinal}", f"copy-occ-{ordinal}"
        duplicate.update(annotation_id=aid, occurrence_id=oid, start=11.8, end=12.5,
                         provenance={"kind": "copy", "copied_from_annotation_id": initial[0]["annotation_id"]})
        project["annotations"].append(duplicate)
        project["occurrences"].append({"occurrence_id": oid, "annotation_ids": [aid], "complete": True})
    saved = save(song, project)
    assert saved["annotations"][:3] == initial
    assert len(saved["annotations"]) == 5 and len(saved["occurrences"]) == 4
    assert [o["complete"] for o in saved["occurrences"]] == [True, True, False, False]
    saved["annotations"][0]["text"] = "original edited"
    reopened = save(song, saved)
    assert reopened["annotations"][3]["text"] == "Hel"


def test_deleting_part_marks_existing_occurrence_incomplete(song):
    project = load(song)
    project["annotations"].pop(1)
    project["occurrences"][0]["annotation_ids"].pop(1)
    saved = save(song, project)
    assert saved["occurrences"][0]["complete"] is False
    saved["occurrences"][0]["complete"] = True
    assert save(song, saved)["occurrences"][0]["complete"] is False


def test_occurrence_membership_and_distinct_ids_are_validated(song):
    project = load(song)
    project["occurrences"][0]["annotation_ids"].append("missing")
    with pytest.raises(storage.ManualError, match="Состав"):
        save(song, project)
    project = load(song)
    project["annotations"][0]["annotation_id"] = project["annotations"][0]["occurrence_id"]
    with pytest.raises(storage.ManualError, match="отличаются"):
        save(song, project)


def test_review_boundary_empty_interval_and_uncertain_features(song):
    project = load(song)
    project["reviews"] = [{"start": 10, "end": 12, "all_roles": True,
                           "uncertainties": [{"annotation_id": "ann-w2", "features": ["role"], "note": "Тихий фон"}]},
                          {"start": 15, "end": 16, "all_roles": True}]
    saved = save(song, project)
    assert saved["reviews"][0]["features"]["ann-w2"] == {
        "presence": True, "text": True, "start": True, "end": False, "role": False}
    assert saved["reviews"][1]["features"] == {}
    coverage = storage.review_coverage(saved)
    assert coverage["reviewed_seconds"] == 3 and coverage["unreviewed_seconds"] == 17
    assert coverage["uncertainty_count"] == 1 and coverage["features"]["end"] == 0


def test_review_requires_all_roles_and_cannot_promote_outside_boundary(song):
    project = load(song)
    project["reviews"] = [{"start": 10, "end": 12, "all_roles": False}]
    with pytest.raises(storage.ManualError, match="все роли"):
        save(song, project)
    project["reviews"][0].update(all_roles=True, features={"ann-w2": {f: True for f in storage.FEATURES}})
    assert save(song, project)["reviews"][0]["features"]["ann-w2"]["end"] is False


def test_new_uncertainty_is_not_cancelled_by_older_overlapping_review(song):
    project = load(song)
    project["reviews"] = [{"start": 0, "end": 20, "all_roles": True}]
    project = save(song, project)
    project["reviews"].append({"start": 10, "end": 13, "all_roles": True,
                               "uncertainties": [{"annotation_id": "ann-w2", "features": ["role"], "note": "Сомнение"}]})
    project = save(song, project)
    assert all(r["features"]["ann-w2"]["role"] is False for r in project["reviews"])


def test_semantic_edit_invalidates_review_but_immutable_reference_stays(song):
    project = reviewed(song)
    reference = storage.create_reference(song[0], "job", song[1], {"revision": project["revision"], "language": "en"})
    path = song[0] / "manual-references" / (reference["reference_id"] + ".json")
    before = path.read_bytes()
    project["annotations"][0]["text"] = "changed"
    changed = save(song, project)
    assert [(r["start"], r["end"]) for r in changed["reviews"]] == [(10, 12), (15, 16)]
    assert path.read_bytes() == before
    assert storage.read_reference(song[0], reference["reference_id"])["project"]["annotations"][0]["text"] == "Hel"


def test_role_name_color_preserve_review_but_assignment_invalidates(song):
    project = load(song)
    project["roles"] = [{"id": "r1", "name": "Lead", "color": "#123456"}]
    project["annotations"][0]["role_id"] = "r1"
    project["reviews"] = [{"start": 0, "end": 5, "all_roles": True}]
    project = save(song, project)
    project["roles"][0].update(name="Main", color="#ABCDEF")
    project = save(song, project)
    assert len(project["reviews"]) == 1
    project["annotations"][0]["role_id"] = None
    assert save(song, project)["reviews"] == []


def test_source_changed_or_missing_preserves_saved_project_for_export(song):
    project = save(song, load(song))
    source = Path(project["source"]["audio_path"])
    source.write_bytes(b"another song")
    with pytest.raises(storage.ManualError) as failure:
        load(song)
    assert failure.value.details["code"] == "source_mismatch"
    assert failure.value.details["saved_project"] == project
    source.unlink()
    with pytest.raises(storage.ManualError) as failure:
        load(song)
    assert failure.value.details["code"] == "missing_sources"
    assert failure.value.details["saved_project"] == project


def test_reference_roundtrip_retains_masks_and_rejects_foreign_sources(song):
    project = reviewed(song)
    reference = storage.create_reference(song[0], "job", song[1], {"revision": project["revision"], "language": "en", "label": "v1"})
    project["annotations"][0]["text"] = "edit after reference"
    project = save(song, project)
    restored = storage.import_reference(song[0], "job", song[1], {"revision": project["revision"], "reference": reference})
    assert restored["annotations"] == reference["project"]["annotations"]
    assert [r["features"] for r in restored["reviews"]] == [r["features"] for r in reference["project"]["reviews"]]
    assert restored["revision"] == project["revision"] + 1
    assert storage.list_references(song[0])["references"][0]["label"] == "v1"
    reference["source"]["audio_sha256"] = "f" * 64
    with pytest.raises(storage.ManualError) as failure:
        storage.import_reference(song[0], "job", song[1], {"revision": restored["revision"], "reference": reference})
    assert failure.value.details["code"] == "source_mismatch" and load(song) == restored


def test_unreviewed_draft_is_not_reference(song):
    project = save(song, load(song))
    with pytest.raises(storage.ManualError, match="явно проверьте"):
        storage.create_reference(song[0], "job", song[1], {"revision": project["revision"], "language": "en"})


def test_web_conflict_and_write_error_leave_client_payload_available(song, monkeypatch):
    monkeypatch.setattr(studio_web, "STUDIO_ROOT", song[0].parent.parent)
    monkeypatch.setattr(studio_web, "STUDIO_JOBS", {})
    app = FastAPI()
    app.include_router(manual_web.router)
    client = TestClient(app)
    url = "/api/studio/jobs/job/manual"
    project = client.get(url).json()
    response = client.put(url, json=project)
    assert response.status_code == 200 and response.json()["revision"] == 1
    conflict = client.put(url, json=project)
    assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "revision_conflict"
    def broken(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(storage, "_atomic_write", broken)
    project = response.json()
    project["annotations"][0]["text"] = "unsaved remains"
    failure = client.put(url, json=project)
    assert failure.status_code == 507 and failure.json()["detail"]["code"] == "write_failed"
    assert project["annotations"][0]["text"] == "unsaved remains"
    assert client.get(url).json()["annotations"][0]["text"] == "Hel"
