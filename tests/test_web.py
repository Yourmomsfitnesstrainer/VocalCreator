from pathlib import Path
import io
import wave

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from karaoke_generator import studio_web, web
from karaoke_generator.timing_cache import write_json


def test_index_contains_accessible_studio_and_shared_transport() -> None:
    response = TestClient(web.app).get("/")
    assert response.status_code == 200
    assert 'id="studio-form"' in response.text
    assert 'id="timeline-canvas"' in response.text
    assert 'id="track-controls"' in response.text
    assert 'id="note-inspector"' in response.text
    assert 'id="previous-note"' in response.text
    assert 'id="result-recovery"' in response.text
    assert 'id="transport-status"' in response.text
    assert 'aria-describedby="timeline-semantic-summary note-inspector"' in response.text
    assert 'name="input_type"' in response.text
    assert 'href="/karaoke"' not in response.text
    assert 'learning-mode' not in response.text


def _wav_bytes(duration: float = 0.1, sample_rate: int = 16000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setparams((1, 2, sample_rate, 0, "NONE", "not compressed"))
        audio.writeframes(b"\0\0" * round(duration * sample_rate))
    return buffer.getvalue()


def test_studio_job_persists_and_only_exposes_allowlisted_artifacts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(studio_web, "STUDIO_ROOT", tmp_path)
    studio_web.STUDIO_JOBS.clear()
    captured: dict = {}

    def fake_run(audio, lyrics, output_dir, config, *, input_type, job_id, progress_callback):
        captured["config"] = config
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "vocals.wav").write_bytes(_wav_bytes())
        (output_dir / "secret.bin").write_bytes(b"private")
        manifest = {
            "schema_version": 1,
            "id": job_id,
            "status": "complete",
            "current_stage": "finished",
            "stage_label": "Результат готов",
            "created_at": "2026-09-11T10:00:00+00:00",
            "updated_at": "2026-09-11T10:01:00+00:00",
            "input": {"type": input_type, "audio_name": audio.name, "lyrics_name": lyrics.name},
            "timeline": {"duration": 0.1},
            "stages": {"preparation": {"status": "complete"}},
            "artifacts": {"vocals.wav": "vocals.wav", "studio.json": "studio.json", "secret.bin": "secret.bin"},
            "errors": [],
        }
        write_json(output_dir / "studio.json", manifest)
        progress_callback("Результат готов", "")
        return manifest

    monkeypatch.setattr(studio_web, "run_studio", fake_run)
    client = TestClient(web.app)
    response = client.post(
        "/api/studio/jobs",
        files={
            "audio": ("voice.wav", _wav_bytes(), "audio/wav"),
            "lyrics": ("lyrics.txt", "one word".encode(), "text/plain"),
        },
        data={
            "input_type": "vocal",
            "language": "en",
            "pitch_backend": "torchcrepe",
            "separator_backend": "melband-roformer",
        },
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    status = client.get(response.json()["status_url"]).json()
    assert status["status"] == "complete"
    assert status["input"]["audio_name"] == "voice.wav"
    assert captured["config"]["separation"]["model"] == "melband-roformer-kim-vocals"
    assert set(status["artifacts"]) == {"vocals.wav", "studio.json"}
    assert client.get(status["artifacts"]["vocals.wav"]).status_code == 200
    assert client.get(f"/api/studio/jobs/{job_id}/artifacts/secret.bin").status_code == 404


def test_studio_recovery_marks_running_manifest_interrupted(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(studio_web, "STUDIO_ROOT", tmp_path)
    studio_web.STUDIO_JOBS.clear()
    manifest_path = tmp_path / "saved" / "result" / "studio.json"
    manifest = {
        "schema_version": 1,
        "id": "saved",
        "status": "running",
        "current_stage": "pitch",
        "stage_label": "Анализ высоты голоса",
        "artifacts": {},
        "errors": [],
    }
    write_json(manifest_path, manifest)

    studio_web.recover_interrupted_jobs()

    recovered = __import__("json").loads(manifest_path.read_text())
    assert recovered["status"] == "interrupted"
    assert recovered["errors"] == [{"stage": "pitch", "message": "Local service restarted"}]


@pytest.mark.parametrize("method,path", [("get", "/karaoke"), ("post", "/generate"), ("post", "/api/jobs"), ("get", "/api/jobs/old"), ("get", "/jobs/old/karaoke.mp4")])
def test_removed_video_routes_are_unavailable(method, path):
    assert getattr(TestClient(web.app), method)(path).status_code == 404
