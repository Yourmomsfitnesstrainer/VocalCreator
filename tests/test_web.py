from pathlib import Path
import io
import wave

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from karaoke_generator import studio_web, web
from karaoke_generator.timing_cache import write_json


def _fake_generate(
    audio: Path,
    lyrics: Path,
    output_dir: Path,
    config: dict,
    *,
    background: str | None = None,
    progress_callback=None,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    for progress, label in ((0, "Preparing audio"), (40, "Separating vocals"), (80, "Rendering MP4"), (100, "Rendering MP4")):
        if progress_callback:
            progress_callback(progress, label)
    artifacts = {}
    for filename in (
        "karaoke.mp4",
        "karaoke.ass",
        "alignment.json",
        "processed_lyrics.txt",
        "lyrics_cleanup.json",
    ):
        path = output_dir / filename
        path.write_bytes(b"demo")
        artifacts[filename] = path
    return artifacts


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
    assert 'href="/karaoke"' in response.text


def test_legacy_karaoke_ui_remains_available() -> None:
    response = TestClient(web.app).get("/karaoke")
    assert response.status_code == 200
    assert 'id="progress-panel"' in response.text
    assert 'name="backend"' in response.text


def test_language_name_and_whitespace_are_normalized() -> None:
    assert web._normalize_language(" English ") == "en"
    assert web._normalize_language(" RU ") == "ru"


def test_invalid_language_is_rejected_before_generation() -> None:
    with pytest.raises(HTTPException) as error:
        web._normalize_language("x")
    assert error.value.status_code == 400


def test_invalid_timing_offset_is_rejected() -> None:
    with pytest.raises(HTTPException) as error:
        web._normalize_generation_options("whisperx", "small", -1001)
    assert error.value.status_code == 400


def test_background_job_reaches_100_and_exposes_artifacts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(web, "JOBS_ROOT", tmp_path)
    monkeypatch.setattr(web, "generate", _fake_generate)
    web.JOBS.clear()
    client = TestClient(web.app)

    response = client.post(
        "/api/jobs",
        files={"audio": ("song.mp3", b"audio", "audio/mpeg"), "lyrics": ("lyrics.txt", b"hello", "text/plain")},
        data={"language": "en", "audio_mode": "original"},
    )
    assert response.status_code == 202
    status = client.get(response.json()["status_url"]).json()
    assert status["status"] == "complete"
    assert status["progress"] == 100
    assert status["stage"] == "Karaoke ready"
    assert client.get(status["artifacts"]["karaoke.mp4"]).status_code == 200


def test_background_job_reports_failure(tmp_path: Path, monkeypatch) -> None:
    def fail(*args, progress_callback=None, **kwargs):
        if progress_callback:
            progress_callback(20, "Separating vocals")
        raise RuntimeError("alignment exploded")

    monkeypatch.setattr(web, "JOBS_ROOT", tmp_path)
    monkeypatch.setattr(web, "generate", fail)
    web.JOBS.clear()
    client = TestClient(web.app)
    response = client.post(
        "/api/jobs",
        files={"audio": ("song.wav", b"audio", "audio/wav"), "lyrics": ("lyrics.txt", b"hello", "text/plain")},
    )
    status = client.get(response.json()["status_url"]).json()
    assert status["status"] == "failed"
    assert status["progress"] == 20
    assert status["error"] == "alignment exploded"


def test_background_job_reports_configuration_failure(tmp_path: Path, monkeypatch) -> None:
    def fail_to_load_config() -> dict:
        raise RuntimeError("config unavailable")

    monkeypatch.setattr(web, "load_config", fail_to_load_config)
    web.JOBS.clear()
    web.JOBS["job"] = {
        "id": "job",
        "status": "queued",
        "progress": 0,
        "stage": "Queued",
        "detail": "Waiting for a worker",
        "artifacts": {},
    }

    web._run_job("job", tmp_path / "song.wav", tmp_path / "lyrics.txt", "en", "original")

    status = web._job_snapshot("job")
    assert status["status"] == "failed"
    assert status["error"] == "config unavailable"


def test_web_job_applies_alignment_controls(tmp_path: Path, monkeypatch) -> None:
    captured: dict = {}

    def capture_generate(audio, lyrics, output_dir, config, **kwargs):
        captured["config"] = config
        return _fake_generate(audio, lyrics, output_dir, config, **kwargs)

    monkeypatch.setattr(web, "JOBS_ROOT", tmp_path)
    monkeypatch.setattr(web, "generate", capture_generate)
    web.JOBS.clear()
    response = TestClient(web.app).post(
        "/api/jobs",
        files={
            "audio": ("song.mp3", b"audio", "audio/mpeg"),
            "lyrics": ("lyrics.txt", "Привет".encode(), "text/plain"),
        },
        data={
            "language": "ru",
            "audio_mode": "original",
            "backend": "whisperx",
            "model": "medium",
            "vad_filter": "false",
            "timing_offset_ms": "-325",
        },
    )

    assert response.status_code == 202
    assert captured["config"]["alignment"]["backend"] == "whisperx"
    assert captured["config"]["alignment"]["model"] == "medium"
    assert captured["config"]["alignment"]["vad_filter"] is False
    assert captured["config"]["karaoke"]["timing_offset_ms"] == -325


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
