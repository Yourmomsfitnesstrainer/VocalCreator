"""HTTP boundary for local manual annotation and human reference files."""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException

from . import manual_lyrics as storage

router = APIRouter(prefix="/api/studio/jobs/{job_id}/manual", tags=["manual-lyrics"])


def _context(job_id: str):
    from .studio_web import _job_dir, _read_manifest
    return _job_dir(job_id) / "result", _read_manifest(job_id)


def _call(function, *args):
    try:
        return function(*args)
    except storage.ManualError as exc:
        raise HTTPException(exc.status, exc.details) from exc
    except OSError as exc:
        raise HTTPException(507, {"code": "write_failed", "message": "Не удалось записать файл. Правки остаются в редакторе; повторите сохранение"}) from exc


@router.get("")
def get_manual(job_id: str):
    result_dir, manifest = _context(job_id)
    return _call(storage.read_project, result_dir, job_id, manifest)


@router.put("")
def put_manual(job_id: str, project: dict = Body(...)):
    result_dir, manifest = _context(job_id)
    return _call(storage.save_project, result_dir, job_id, manifest, project)


@router.get("/references")
def references(job_id: str):
    result_dir, _ = _context(job_id)
    return _call(storage.list_references, result_dir)


@router.post("/references", status_code=201)
def make_reference(job_id: str, options: dict = Body(...)):
    result_dir, manifest = _context(job_id)
    return _call(storage.create_reference, result_dir, job_id, manifest, options)


@router.get("/references/{reference_id}")
def get_reference(job_id: str, reference_id: str):
    result_dir, _ = _context(job_id)
    return _call(storage.read_reference, result_dir, reference_id)


@router.post("/import")
def restore_reference(job_id: str, payload: dict = Body(...)):
    result_dir, manifest = _context(job_id)
    return _call(storage.import_reference, result_dir, job_id, manifest, payload)
