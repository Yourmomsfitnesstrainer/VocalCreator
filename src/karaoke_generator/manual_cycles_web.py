"""HTTP boundary for the local evaluation lab and explicit automatic re-analysis."""
from __future__ import annotations

import copy
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Body, HTTPException

from . import manual_cycles as lab
from .manual_lyrics import ManualError

router = APIRouter(prefix='/api/studio', tags=['manual-comparisons'])


def _root():
    from .studio_web import STUDIO_ROOT
    return STUDIO_ROOT.parent/'manual-lab'


def _result(job_id):
    from .studio_web import _job_dir, _read_manifest
    manifest = _read_manifest(job_id)
    if manifest.get('status') in ('running', 'queued'):
        raise HTTPException(409, 'Дождитесь исходного анализа')
    return _job_dir(job_id)/'result', manifest


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except ManualError as exc:
        raise HTTPException(exc.status, exc.details) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, 'Сохранённая версия или исходный файл не найдены') from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(409, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(500, f'Не удалось сохранить локальные данные: {exc}') from exc


@router.get('/manual-lab')
def catalogue():
    from .studio_web import STUDIO_ROOT, _read_manifest
    from .manual_lyrics import list_references
    data = lab.catalogue(_root())
    available = []
    for path in STUDIO_ROOT.glob('*/result/studio.json'):
        try:
            job = _read_manifest(path.parent.parent.name)
            refs = list_references(path.parent)['references']
            if job.get('status') in ('complete', 'partial'):
                available.append({'job_id': path.parent.parent.name, 'title': job.get('input', {}).get('audio_name', path.parent.parent.name),
                                  'references': refs})
        except (OSError, ValueError, HTTPException):
            continue
    data['available_jobs'] = available
    return data


@router.post('/manual-lab/corpus')
def corpus_entry(payload: dict = Body(...)):
    from .manual_lyrics import read_project, read_reference
    result, manifest = _result(payload.get('job_id', ''))
    project = _call(read_project, result, payload['job_id'], manifest)
    reference = _call(read_reference, result, payload['reference_id']) if payload.get('reference_id') else None
    entry = {'job_id': payload['job_id'], 'title': manifest.get('input', {}).get('audio_name', payload['job_id']),
             'language': payload.get('language'), 'split': payload.get('split'),
             'source': project['source'], 'reference': reference,
             'reference_id': payload.get('reference_id'), 'difficult_cases': (reference or {}).get('difficult_cases', [])}
    return _call(lab.register, _root(), entry, payload.get('revision'))


@router.post('/manual-lab/profiles')
def profile_create(payload: dict = Body(...)):
    return _call(lab.create_profile, _root(), language=payload.get('language'), name=payload.get('name', ''),
                 parameters=payload.get('parameters', {}), previous_profile=payload.get('previous_profile'))


@router.post('/manual-lab/cycles', status_code=202)
def cycle_create(background_tasks: BackgroundTasks, payload: dict = Body(...)):
    from .studio_web import STUDIO_ROOT
    cycle = _call(lab.create_cycle, _root(), baselines=payload.get('baseline', {}), candidates=payload.get('candidate', {}),
                  changes=payload.get('changes', ''), expected_revision=payload.get('revision'))
    background_tasks.add_task(lab.run_cycle, _root(), cycle['id'], STUDIO_ROOT)
    return cycle


@router.get('/manual-lab/cycles/{cycle_id}')
def cycle_get(cycle_id: str):
    return _call(lab.get_cycle, _root(), cycle_id)


@router.get('/manual-lab/cycles/{cycle_id}/results/{job_id}/{side}')
def cycle_result(cycle_id: str, job_id: str, side: str):
    cycle = _call(lab.get_cycle, _root(), cycle_id)
    if job_id not in cycle['results'] or side not in ('baseline', 'candidate') or side not in cycle['results'][job_id]:
        raise HTTPException(404)
    target = _root()/'cycles'/cycle_id/job_id/(side+'.json')
    if lab.digest(target.read_bytes()) != cycle['results'][job_id][side]['sha256']:
        raise HTTPException(409, 'Сохранённый автоматический результат повреждён')
    return _call(lab.read, target)


@router.post('/manual-lab/cycles/{cycle_id}/review')
def cycle_review(cycle_id: str, payload: dict = Body(...)):
    return _call(lab.review_cycle, _root(), cycle_id, payload.get('job_id'), payload.get('verdict'), payload.get('note', ''))


@router.post('/manual-lab/cycles/{cycle_id}/accept/{language}')
def cycle_accept(cycle_id: str, language: str):
    return _call(lab.activate, _root(), cycle_id, language)


@router.post('/manual-lab/profiles/rollback/{language}')
def profile_rollback(language: str):
    return _call(lab.rollback, _root(), language)


@router.post('/jobs/{job_id}/manual/auto')
def automatic_result(job_id: str, payload: dict = Body(default={})):
    """Create a separate derived job; source analysis and old drafts remain intact."""
    from .studio_web import _job_dir, _store_active
    from .manual_auto import propose_manual_annotations
    from .manual_lyrics import read_project
    source, old_manifest = _result(job_id)
    source_project = _call(read_project, source, job_id, old_manifest)
    identifier = uuid.uuid4().hex
    destination = _job_dir(identifier)
    destination.mkdir(parents=True, exist_ok=False)
    manifest = copy.deepcopy(old_manifest)
    manifest.update(id=identifier, created_at=lab.now(), updated_at=lab.now(), derived_from=job_id)
    manifest['input']['audio_name'] = old_manifest.get('input', {}).get('audio_name', 'Песня').split(' · предложения')[0]+' · предложения'
    try:
        if (source.parent/'input').exists():
            shutil.copytree(source.parent/'input', destination/'input')
        shutil.copytree(source, destination/'result', ignore=shutil.ignore_patterns('manual*', 'references', '*.lock', '*.tmp'))
        lab.write(destination/'result'/'studio.json', manifest)
        language = source_project.get('language')
        profile_id = payload.get('profile_id') or lab.state(_root())['active'].get(language)
        if profile_id:
            profile = _call(lab.get_profile, _root(), profile_id)
            if language in lab.LANGUAGES and profile['language'] != language:
                raise ValueError('Язык выбранного профиля не совпадает с песней')
            automatic = lab.run_profile(_root(), profile_id, destination/'result')
        else:
            automatic = propose_manual_annotations(destination/'result', force=True)
        manifest['manual_automatic'] = {'run_id': automatic['run_id'], 'derived_from': job_id,
                                        'profile_id': profile_id, 'status': 'complete', 'created_at': lab.now()}
        lab.write(destination/'result'/'studio.json', manifest)
        _store_active(identifier, manifest)
        return {'job_id': identifier, 'url': f'/?job={identifier}', 'automatic': automatic}
    except Exception as exc:
        manifest.update(status='partial', updated_at=lab.now())
        manifest.setdefault('errors', []).append({'stage': 'manual-automatic', 'message': str(exc)})
        lab.write(destination/'result'/'studio.json', manifest)
        _store_active(identifier, manifest)
        raise HTTPException(500, f'Предложения не готовы; исходный проект сохранён: {exc}') from exc
