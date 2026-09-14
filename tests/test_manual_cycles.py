"""Pinned corpus/history and actual executable profile replay (synthetic fixtures)."""
import copy
import importlib.util
from pathlib import Path
import shutil

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from karaoke_generator import manual_auto, manual_cycles as lab, manual_cycles_web, manual_lyrics, studio_web

spec = importlib.util.spec_from_file_location("manual_lab_fixture", Path(__file__).parents[1] / "scripts/create_manual_lab_fixture.py")
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


@pytest.fixture
def corpus(tmp_path):
    data = fixtures.create_fixture(tmp_path / "fixture", run=False)
    return Path(data["lab_root"]), Path(data["jobs_root"]), data


def simulated_run(monkeypatch, corpus, *, wrong_source=False, changed_inputs=False):
    root, jobs, fixture = corpus
    called = []
    def run(profile_root, profile_id, result_dir):
        assert profile_root == root
        # Signature has no human project or reference; saved manual text is ignored.
        called.append((profile_id, result_dir.parent.name))
        source = manual_auto._source(result_dir)
        if wrong_source:
            source["lyrics_sha256"] = "changed"
        inputs = {"alignment.json": "same-analysis"}
        if changed_inputs and profile_id == fixture["profiles"]["candidate"]["en"]:
            inputs["alignment.json"] = "modified-between-sides"
        return {"run_id": f"run{len(called)}", "source": source, "inputs": inputs,
                "annotations": [], "occurrences": [], "roles": [], "automatic": True, "human_verified": False}
    monkeypatch.setattr(lab, "run_profile", run)
    return called


def test_requires_six_checked_tuning_and_two_distinct_control_recordings(corpus):
    root, _, _ = corpus
    saved = lab.state(root)
    assert lab.readiness(saved["corpus"]) == {"counts": {"en": {"tuning": 3, "control": 1},
        "ru": {"tuning": 3, "control": 1}}, "ready": True,
        "message": "Нужны 3 EN + 3 RU проверенных эталона и отдельные контрольные песни: 1 EN + 1 RU"}
    missing = [e for e in saved["corpus"] if e["job_id"] != "fixtureen2"]
    assert lab.readiness(missing)["ready"] is False
    entry = copy.deepcopy(saved["corpus"][0])
    entry["split"] = "control"
    with pytest.raises(ValueError, match="и настройкой, и контролем"):
        lab.register(root, entry, saved["revision"])


def test_registry_rejects_mismatched_language_source_and_stale_revision(corpus):
    root, _, _ = corpus
    state = lab.state(root)
    entry = copy.deepcopy(state["corpus"][0])
    entry["language"] = "ru"
    with pytest.raises(ValueError, match="Язык эталона"):
        lab.register(root, entry, state["revision"])
    entry = copy.deepcopy(state["corpus"][0])
    entry["reference"]["source"]["lyrics_sha256"] = "wrong-txt"
    with pytest.raises(ValueError, match="другой записи"):
        lab.register(root, entry, state["revision"])
    with pytest.raises(ValueError, match="другой вкладке"):
        lab.register(root, state["corpus"][0], state["revision"] - 1)


def test_cycle_pins_reference_versions_and_keeps_fixed_controls_for_next_cycle(corpus):
    root, _, fixture = corpus
    first = lab.get_cycle(root, fixture["cycle_id"])
    path = root / "cycles" / first["id"] / "cycle.json"
    original = path.read_bytes()
    state = lab.state(root)
    entry = copy.deepcopy(state["corpus"][0])
    entry["reference"]["version"] += 1
    entry["reference"]["label"] = "Synthetic reference revision 2"
    lab.register(root, entry, state["revision"])
    second = lab.create_cycle(root, baselines=fixture["profiles"]["baseline"], candidates=fixture["profiles"]["candidate"],
                              changes="Synthetic second cycle", expected_revision=lab.state(root)["revision"])
    assert path.read_bytes() == original
    assert first["corpus"][0]["reference"]["version"] == 1
    second_entry = next(e for e in second["corpus"] if e["job_id"] == entry["job_id"])
    assert second_entry["reference"]["version"] == 2
    assert {e["job_id"] for e in first["corpus"] if e["split"] == "control"} == {
        e["job_id"] for e in second["corpus"] if e["split"] == "control"}
    assert "не является независимой" in second["control_policy"]


def test_run_uses_only_automatic_control_outputs_and_cannot_overwrite_history(corpus, monkeypatch):
    root, jobs, fixture = corpus
    called = simulated_run(monkeypatch, corpus)
    # A distinctive manual correction must not appear in either automatic result.
    result = jobs / "fixtureen3" / "result"
    project = manual_lyrics.load_project(result, "fixtureen3")
    project["annotations"][0]["text"] = "HUMAN ANSWER MUST NOT LEAK"
    manual_lyrics.save_project(result, "fixtureen3", lab.read(result / "studio.json"), project)
    cycle = lab.run_cycle(root, fixture["cycle_id"], jobs)
    assert cycle["status"] == "ready" and len(called) == 4
    assert {job_id for _, job_id in called} == {"fixtureen3", "fixtureru3"}
    assert cycle["reviews"] == {} and cycle["decisions"] == {"en": None, "ru": None}
    target = root / "cycles" / cycle["id"] / "fixtureen3" / "baseline.json"
    saved = target.read_bytes()
    assert b"HUMAN ANSWER" not in saved
    assert cycle["results"]["fixtureen3"]["baseline"]["metrics"]["metrics"] is None
    with pytest.raises(ValueError, match="уже запускался"):
        lab.run_cycle(root, cycle["id"], jobs)
    assert target.read_bytes() == saved


@pytest.mark.parametrize("wrong_source,changed_inputs", [(True, False), (False, True)])
def test_midcycle_source_or_upstream_changes_cannot_be_relabelled_as_comparable(corpus, monkeypatch, wrong_source, changed_inputs):
    root, jobs, fixture = corpus
    simulated_run(monkeypatch, corpus, wrong_source=wrong_source, changed_inputs=changed_inputs)
    cycle = lab.run_cycle(root, fixture["cycle_id"], jobs)
    assert cycle["status"] == "error"
    assert "candidate" not in cycle["results"]["fixtureen3"]
    assert cycle["decisions"] == {"en": None, "ru": None}


def test_explicit_review_accept_and_rollback_are_independent_for_en_ru(corpus, monkeypatch):
    root, jobs, fixture = corpus
    simulated_run(monkeypatch, corpus)
    cycle = lab.run_cycle(root, fixture["cycle_id"], jobs)
    with pytest.raises(ValueError, match="Сначала оцените"):
        lab.activate(root, cycle["id"], "en")
    lab.review_cycle(root, cycle["id"], "fixtureen3", "same", "SYNTHETIC TEST DECISION, not owner acceptance")
    accepted = lab.activate(root, cycle["id"], "en")
    assert accepted["active"] == {"en": fixture["profiles"]["candidate"]["en"], "ru": None}
    assert lab.activate(root, cycle["id"], "en") == accepted  # repeated click is idempotent
    assert lab.get_cycle(root, cycle["id"])["decisions"]["ru"] is None
    reverted = lab.rollback(root, "en")
    assert reverted["active"] == {"en": fixture["profiles"]["baseline"]["en"], "ru": None}
    assert lab.get_cycle(root, cycle["id"])["results"] == cycle["results"]


def test_profile_rejects_unknown_parameters_before_creating_snapshot(tmp_path):
    with pytest.raises(ValueError, match="Неизвестные параметры"):
        lab.create_profile(tmp_path, language="en", name="invalid", parameters={"invented_setting": 1})
    assert not (tmp_path / "profiles").exists()


def test_actual_archived_executable_replay_applies_parameters_without_current_code(corpus, tmp_path, monkeypatch):
    root, jobs, _ = corpus
    # Snapshot a separate copy, then break its live source. Shared repo is untouched.
    live = tmp_path / "live-source"
    shutil.copytree(Path(lab.__file__).parent, live)
    monkeypatch.setattr(lab, "__file__", str(live / "manual_cycles.py"))
    baseline = lab.create_profile(root, language="en", name="Synthetic baseline", parameters={"timing_offset_seconds": 0})
    candidate = lab.create_profile(root, language="en", name="Synthetic candidate", parameters={"timing_offset_seconds": .25}, previous_profile=baseline["id"])
    (live / "manual_auto.py").write_text('raise RuntimeError("LIVE CODE MUST NOT EXECUTE")\n')
    result = jobs / "fixtureen3" / "result"
    first = lab.run_profile(root, baseline["id"], result)
    second = lab.run_profile(root, candidate["id"], result)
    assert first["annotations"][0]["start"] == 1.0
    assert second["annotations"][0]["start"] == 1.25
    assert second["annotations"][0]["end"] == first["annotations"][0]["end"] + .25
    assert first["algorithm"]["code_sha256"] == baseline["source_files"]["manual_auto.py"]
    assert second["inputs"] == first["inputs"] and second["source"] == first["source"]
    assert second["diagnostics"]["roles"]["audio_processed"] is True
    assert first["human_verified"] is False and second["human_verified"] is False
    displayed = manual_lyrics.load_project(result, "fixtureen3")
    assert displayed["annotations"][0]["start"] == 1.0  # saved manual layer remains untouched
    (root / "profiles" / baseline["id"] / "source/karaoke_generator/manual_auto.py").write_text("# damaged")
    with pytest.raises(ValueError, match="повреждён"):
        lab.run_profile(root, baseline["id"], result)


def test_http_corpus_requires_compatible_reference_and_keeps_error_details(corpus, monkeypatch):
    root, jobs, _ = corpus
    monkeypatch.setattr(studio_web, "STUDIO_ROOT", jobs)
    monkeypatch.setattr(studio_web, "STUDIO_JOBS", {})
    app = FastAPI()
    app.include_router(manual_cycles_web.router)
    client = TestClient(app)
    entry = lab.state(root)["corpus"][0]
    response = client.post("/api/studio/manual-lab/corpus", json={"revision": lab.state(root)["revision"],
        "job_id": entry["job_id"], "reference_id": entry["reference_id"], "language": "ru", "split": "tuning"})
    assert response.status_code == 409 and "Язык эталона" in response.json()["detail"]
    source = Path(entry["source"]["audio_path"])
    source.unlink()
    response = client.post("/api/studio/manual-lab/corpus", json={"revision": lab.state(root)["revision"],
        "job_id": entry["job_id"], "reference_id": entry["reference_id"], "language": "en", "split": "tuning"})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "missing_sources"
    assert response.json()["detail"]["saved_project"]["job_id"] == entry["job_id"]
