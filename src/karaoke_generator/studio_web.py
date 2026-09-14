from __future__ import annotations

import json
import os
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .audio import probe_duration
from .config import load_config
from .learning import MODES
from .learning_storage import prepare_learning
from .studio import STUDIO_SCHEMA_VERSION, run_studio
from .timing_cache import write_json
from .v3_storage import prepare_v3, read_text, ready_modes, valid_key


router = APIRouter(prefix="/api/studio", tags=["studio"])
STUDIO_ARTIFACTS = {
    "vocals.wav",
    "instrumental.wav",
    "piano.wav",
    "melody.json",
    "alignment.json",
    "studio.json",
    "lyrics.txt",
    "processed_lyrics.txt",
    "lyrics_cleanup.json",
}


def _default_root() -> Path:
    configured = os.environ.get("VOCAL_CREATOR_DATA_DIR")
    base = Path(configured).expanduser() if configured else Path.home() / "Library" / "Application Support" / "VocalCreator"
    return base / "jobs"


STUDIO_ROOT = _default_root()
STUDIO_ROOT.mkdir(parents=True, exist_ok=True)
STUDIO_JOBS: dict[str, dict[str, Any]] = {}
STUDIO_LOCK = threading.Lock()


def _job_dir(job_id: str) -> Path:
    if not job_id.isalnum():
        raise HTTPException(404)
    return STUDIO_ROOT / job_id


def _manifest_path(job_id: str) -> Path:
    return _job_dir(job_id) / "result" / "studio.json"


def _read_manifest(job_id: str) -> dict[str, Any]:
    with STUDIO_LOCK:
        active = STUDIO_JOBS.get(job_id)
        if active is not None:
            return dict(active)
    path = _manifest_path(job_id)
    if not path.is_file():
        raise HTTPException(404)
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise HTTPException(500, "Saved studio manifest is unreadable") from exc
    if int(manifest.get("schema_version", 0)) != STUDIO_SCHEMA_VERSION:
        raise HTTPException(409, "Saved studio format is not supported by this version")
    return manifest


def _public_manifest(job_id: str, manifest: dict[str, Any]) -> dict[str, Any]:
    public = {key: value for key, value in manifest.items() if key not in {"cache_keys"}}
    available = manifest.get("artifacts") or {}
    public["artifacts"] = {
        name: f"/api/studio/jobs/{job_id}/artifacts/{name}"
        for name in available
        if name in STUDIO_ARTIFACTS
    }
    public["status_url"] = f"/api/studio/jobs/{job_id}"
    cached = ready_modes(_job_dir(job_id) / "result", set(available))
    public["v3_modes"] = {
        mode: f"/api/studio/jobs/{job_id}/v3/{mode}/{data['cache_key']}/learning.json"
        for mode, data in cached.items()
    }
    return public


def _store_active(job_id: str, manifest: dict[str, Any]) -> None:
    with STUDIO_LOCK:
        STUDIO_JOBS[job_id] = dict(manifest)


def _queued_manifest(job_id: str, audio: UploadFile, lyrics: UploadFile, input_type: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": STUDIO_SCHEMA_VERSION,
        "id": job_id,
        "status": "queued",
        "current_stage": "queued",
        "stage_label": "Ожидание запуска",
        "created_at": now,
        "updated_at": now,
        "input": {
            "type": input_type,
            "audio_name": audio.filename or "audio",
            "lyrics_name": lyrics.filename or "lyrics.txt",
        },
        "timeline": {},
        "stages": {},
        "artifacts": {},
        "errors": [],
    }


def _save_upload(upload: UploadFile, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as handle:
        shutil.copyfileobj(upload.file, handle)
    if destination.stat().st_size == 0:
        raise HTTPException(400, f"Uploaded file is empty: {upload.filename or destination.name}")


def _run_studio_job(
    job_id: str,
    audio: Path,
    lyrics: Path,
    input_type: str,
    language: str,
    pitch_backend: str,
    separator_backend: str,
) -> None:
    config = load_config()
    config["alignment"]["language"] = language
    config["pitch"]["backend"] = pitch_backend
    config["separation"]["backend"] = separator_backend
    config["separation"]["model"] = (
        "htdemucs" if separator_backend == "demucs" else "melband-roformer-kim-vocals"
    )

    def report(label: str, detail: str) -> None:
        try:
            current = json.loads(_manifest_path(job_id).read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            with STUDIO_LOCK:
                current = dict(STUDIO_JOBS.get(job_id) or {})
            current.update(status="running", stage_label=label, detail=detail)
        _store_active(job_id, current)

    try:
        manifest = run_studio(
            audio,
            lyrics,
            _job_dir(job_id) / "result",
            config,
            input_type=input_type,
            job_id=job_id,
            progress_callback=report,
        )
    except Exception as exc:
        try:
            manifest = json.loads(_manifest_path(job_id).read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            manifest = dict(STUDIO_JOBS[job_id])
        manifest.update(status="failed", stage_label="Анализ остановлен")
        manifest.setdefault("errors", []).append({"stage": "internal", "message": str(exc)})
    _store_active(job_id, manifest)


@router.post("/jobs", status_code=202)
def start_studio_job(
    background_tasks: BackgroundTasks,
    audio: UploadFile = File(...),
    lyrics: UploadFile = File(...),
    input_type: str = Form("mix"),
    language: str = Form("auto"),
    pitch_backend: str = Form("torchcrepe"),
    separator_backend: str = Form("demucs"),
) -> dict[str, str]:
    if input_type not in {"mix", "vocal"}:
        raise HTTPException(400, "Invalid studio input type")
    if language != "auto" and not (language.isalpha() and 2 <= len(language) <= 3):
        raise HTTPException(400, "Language must be auto or a 2–3 letter ISO code")
    if pitch_backend not in {"torchcrepe", "autocorrelation"}:
        raise HTTPException(400, "Invalid pitch backend")
    if separator_backend not in {"demucs", "melband-roformer"}:
        raise HTTPException(400, "Invalid separator backend")
    audio_suffix = Path(audio.filename or "audio.wav").suffix.lower() or ".wav"
    if audio_suffix not in {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg"}:
        raise HTTPException(400, "Unsupported audio format")
    if Path(lyrics.filename or "lyrics.txt").suffix.lower() != ".txt":
        raise HTTPException(400, "Lyrics must be a UTF-8 TXT file")
    job_id = uuid.uuid4().hex
    input_dir = _job_dir(job_id) / "input"
    audio_path = input_dir / f"audio{audio_suffix}"
    lyrics_path = input_dir / "lyrics.txt"
    _save_upload(audio, audio_path)
    _save_upload(lyrics, lyrics_path)
    try:
        lyrics_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(400, "Lyrics must be valid UTF-8") from exc
    try:
        probe_duration(audio_path)
    except Exception as exc:
        raise HTTPException(400, f"Audio could not be decoded: {exc}") from exc
    manifest = _queued_manifest(job_id, audio, lyrics, input_type)
    _store_active(job_id, manifest)
    write_json(_manifest_path(job_id), manifest)
    background_tasks.add_task(
        _run_studio_job,
        job_id,
        audio_path,
        lyrics_path,
        input_type,
        language,
        pitch_backend,
        separator_backend,
    )
    return {"job_id": job_id, "status_url": f"/api/studio/jobs/{job_id}"}


@router.get("/jobs")
def list_studio_jobs() -> dict[str, list[dict[str, Any]]]:
    job_ids = {
        path.parent.parent.name
        for path in STUDIO_ROOT.glob("*/result/studio.json")
        if path.is_file()
    }
    with STUDIO_LOCK:
        job_ids.update(STUDIO_JOBS)
    jobs = []
    for job_id in job_ids:
        try:
            jobs.append(_public_manifest(job_id, _read_manifest(job_id)))
        except HTTPException:
            continue
    jobs.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    return {"jobs": jobs}


@router.get("/jobs/{job_id}")
def studio_job_status(job_id: str) -> dict[str, Any]:
    return _public_manifest(job_id, _read_manifest(job_id))


@router.get("/jobs/{job_id}/artifacts/{filename}")
def studio_artifact(job_id: str, filename: str) -> FileResponse:
    if filename not in STUDIO_ARTIFACTS:
        raise HTTPException(404)
    manifest = _read_manifest(job_id)
    if filename not in (manifest.get("artifacts") or {}):
        raise HTTPException(404)
    path = _job_dir(job_id) / "result" / filename
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, filename=filename)


@router.post("/jobs/{job_id}/learning/{mode}")
def studio_learning(job_id: str, mode: str) -> dict[str, Any]:
    manifest = _read_manifest(job_id)
    if mode not in MODES:
        raise HTTPException(404)
    if manifest.get("status") in {"queued", "running"}:
        raise HTTPException(409, "Дождитесь завершения исходного анализа")
    if "melody.json" not in (manifest.get("artifacts") or {}):
        raise HTTPException(409, "Нет совместимых данных нот. Готовое аудио можно прослушать отдельно.")
    try:
        data, _ = prepare_learning(_job_dir(job_id) / "result", mode,
                                   piano_options=load_config().get("piano"),
                                   alignment_available="alignment.json" in manifest.get("artifacts", {}),
                                   vocals_available="vocals.wav" in manifest.get("artifacts", {}))
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"Не удалось подготовить {mode.title()}: {exc}") from exc
    prefix = f"/api/studio/jobs/{job_id}/learning/{mode}/{data['cache_key']}"
    return {**data, "artifacts": {"piano.wav": f"{prefix}/piano.wav", "learning.json": f"{prefix}/learning.json"}}


@router.get("/jobs/{job_id}/learning/{mode}/{cache_key}/{filename}")
def studio_learning_artifact(job_id: str, mode: str, cache_key: str, filename: str) -> FileResponse:
    _read_manifest(job_id)
    if mode not in MODES or filename not in {"piano.wav", "learning.json"}:
        raise HTTPException(404)
    if len(cache_key) != 64 or any(char not in "0123456789abcdef" for char in cache_key):
        raise HTTPException(404)
    directory = _job_dir(job_id) / "result" / "learning" / cache_key
    try:
        data = json.loads((directory / "learning.json").read_text(encoding="utf-8"))
        if data.get("mode") != mode or data.get("cache_key") != cache_key:
            raise HTTPException(404)
    except (OSError, ValueError):
        raise HTTPException(404)
    path = directory / filename
    if not path.is_file():
        raise HTTPException(404)
    name = f"piano-{mode}.wav" if filename == "piano.wav" else f"melody-{mode}.json"
    return FileResponse(path, filename=name)


@router.get("/jobs/{job_id}/text")
def studio_text(job_id: str) -> dict[str, Any]:
    manifest = _read_manifest(job_id)
    return read_text(_job_dir(job_id) / "result", set(manifest.get("artifacts") or {}),
                     float(manifest.get("timeline", {}).get("duration", 0)))


def _v3_response(job_id: str, data: dict) -> dict:
    prefix = f"/api/studio/jobs/{job_id}/v3/{data['mode']}/{data['cache_key']}"
    return {**data, "artifacts": {name: f"{prefix}/{name}" for name in ("piano.wav", "learning.json")}}


def _full_mode(job_id: str, manifest: dict) -> str:
    # All v3 variants contain the same full events. Reuse old files verbatim.
    cached = ready_modes(_job_dir(job_id) / "result", set(manifest.get("artifacts") or {}))
    return next((mode for mode in ("pro", "light", "medium") if mode in cached), "pro")


@router.post("/jobs/{job_id}/melody")
def studio_full_melody(job_id: str) -> dict[str, Any]:
    manifest = _read_manifest(job_id)
    return studio_v3(job_id, _full_mode(job_id, manifest))


@router.post("/jobs/{job_id}/v3/{mode}")
def studio_v3(job_id: str, mode: str) -> dict[str, Any]:
    manifest = _read_manifest(job_id)
    if mode not in MODES:
        raise HTTPException(404)
    if manifest.get("status") in {"queued", "running"}:
        raise HTTPException(409, "Дождитесь завершения исходного анализа")
    try:
        data, _ = prepare_v3(_job_dir(job_id) / "result", mode,
                             available=set(manifest.get("artifacts") or {}), piano_options=load_config().get("piano"))
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"Не удалось подготовить части слов: {exc}") from exc
    return _v3_response(job_id, data)


@router.get("/jobs/{job_id}/v3/{mode}/{cache_key}/{filename}")
def studio_v3_artifact(job_id: str, mode: str, cache_key: str, filename: str):
    _read_manifest(job_id)
    if mode not in MODES or filename not in {"piano.wav", "learning.json"} or not valid_key(cache_key):
        raise HTTPException(404)
    directory = _job_dir(job_id) / "result/learning-v3" / cache_key
    try:
        data = json.loads((directory / "learning.json").read_text(encoding="utf-8"))
        if data.get("cache_key") != cache_key or data.get("mode") != mode:
            raise HTTPException(404)
    except (OSError, ValueError):
        raise HTTPException(404)
    if filename == "learning.json":
        return _v3_response(job_id, data)
    if not (directory / filename).is_file():
        raise HTTPException(404)
    return FileResponse(directory / filename, filename="piano.wav")


@router.post("/jobs/{job_id}/tempo/{mode}/{rate}")
def studio_tempo(job_id: str, mode: str, rate: float) -> dict[str, Any]:
    from .tempo import prepare_tempo

    manifest = _read_manifest(job_id)
    if mode not in (*MODES, "original", "full"):
        raise HTTPException(404)
    if manifest.get("status") in {"queued", "running"}:
        raise HTTPException(409, "Дождитесь завершения исходного анализа")
    available = set(manifest.get("artifacts") or {})
    root = _job_dir(job_id) / "result"
    tracks = {name: root / f"{name}.wav" for name in ("vocals", "instrumental", "piano") if f"{name}.wav" in available}
    try:
        if mode != "original":
            source_mode = _full_mode(job_id, manifest) if mode == "full" else mode
            _, directory = prepare_v3(root, source_mode, available=available, piano_options=load_config().get("piano"))
            tracks["piano"] = directory / "piano.wav"
        data, _ = prepare_tempo(tracks, root, rate, float(manifest.get("timeline", {}).get("duration", 0)))
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"Не удалось подготовить скорость: {exc}") from exc
    prefix = f"/api/studio/jobs/{job_id}/tempo-files/{data['cache_key']}"
    return {**data, "mode": mode, "artifacts": {filename: f"{prefix}/{filename}" for filename in data["tracks"].values()}}


@router.get("/jobs/{job_id}/tempo-files/{cache_key}/{filename}")
def studio_tempo_artifact(job_id: str, cache_key: str, filename: str) -> FileResponse:
    _read_manifest(job_id)
    if not valid_key(cache_key) or filename not in {"vocals.wav", "instrumental.wav", "piano.wav"}:
        raise HTTPException(404)
    path = _job_dir(job_id) / "result/tempo-v3" / cache_key / filename
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="audio/wav")


def recover_interrupted_jobs() -> None:
    for path in STUDIO_ROOT.glob("*/result/studio.json"):
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if manifest.get("status") not in {"queued", "running"}:
            continue
        manifest["status"] = "interrupted"
        manifest["stage_label"] = "Анализ был прерван перезапуском"
        manifest.setdefault("errors", []).append(
            {"stage": manifest.get("current_stage", "unknown"), "message": "Local service restarted"}
        )
        manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
        write_json(path, manifest)


recover_interrupted_jobs()
