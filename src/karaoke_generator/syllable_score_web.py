"""HTTP API for explicit syllable preparation and immutable manual revisions."""
from fastapi import APIRouter, BackgroundTasks, Body, HTTPException, Response
from fastapi.responses import FileResponse

from . import syllable_score_storage as storage

router = APIRouter(prefix="/api/studio/jobs/{job_id}/syllables", tags=["syllables"])


def _context(job_id):
    from .studio_web import _job_dir, _read_manifest
    return _job_dir(job_id) / "result", _read_manifest(job_id)


def _call(function, *args):
    try:
        return function(*args)
    except storage.ScoreError as exc:
        raise HTTPException(exc.status, exc.details) from exc
    except OSError as exc:
        raise HTTPException(507, {"code": "write_failed", "message":
            "Не удалось сохранить файл. Правки остаются в редакторе; повторите сохранение"}) from exc


@router.get("")
def get_state(job_id: str):
    result, _ = _context(job_id)
    return _call(storage.read_state, result, job_id)


@router.post("")
def prepare(job_id: str, background_tasks: BackgroundTasks, response: Response, payload: dict = Body(...)):
    result, manifest = _context(job_id)
    state, status, operation = _call(storage.queue_prepare, result, job_id, manifest, payload)
    response.status_code = status
    if operation:
        background_tasks.add_task(storage.run_prepare, result, job_id, manifest, operation)
    return state


@router.put("")
def save(job_id: str, payload: dict = Body(...)):
    result, _ = _context(job_id)
    return _call(storage.save_revision, result, job_id, payload)


@router.get("/artifacts/{key}/syllable-score.json")
def artifact(job_id: str, key: str):
    result, _ = _context(job_id)
    return _call(storage.read_artifact, result, job_id, key)


@router.get("/revisions/{revision}")
def revision(job_id: str, revision: int):
    result, _ = _context(job_id)
    return _call(storage.read_revision, result, job_id, revision)


@router.get("/artifacts/{key}/source-notes.json")
def source_notes(job_id: str, key: str):
    result, _ = _context(job_id)
    return _call(storage.read_source_notes, result, job_id, key)


@router.get("/artifacts/{key}/piano.wav")
def source_piano(job_id: str, key: str):
    result, _ = _context(job_id)
    path = _call(storage.source_piano_path, result, job_id, key)
    return FileResponse(path, media_type="audio/wav", filename="piano.wav")


@router.get("/legacy")
def legacy(job_id: str):
    result, _ = _context(job_id)
    return _call(storage.read_legacy, result)
