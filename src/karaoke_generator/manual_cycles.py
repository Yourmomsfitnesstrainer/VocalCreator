"""Local, immutable analysis profiles and repeatable human-reviewed comparison cycles."""
from __future__ import annotations

import copy
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .manual_evaluation import EVALUATOR_VERSION, MATCH_WINDOW, evaluate_manual

_LOCK = threading.RLock()
LANGUAGES = ('en', 'ru')


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.'+path.name+'.'+uuid.uuid4().hex)
    try:
        with temporary.open('x', encoding='utf-8') as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def locked(root):
    import fcntl
    root.mkdir(parents=True, exist_ok=True)
    with _LOCK, (root/'.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _id(identifier):
    if not isinstance(identifier, str) or not identifier.isalnum():
        raise ValueError('Некорректный ID')
    return identifier


def environment():
    versions = {'python': platform.python_version()}
    for name in ('numpy', 'scipy', 'librosa', 'soundfile', 'PyYAML'):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def create_profile(root, *, language, name, parameters=None, previous_profile=None):
    from .manual_auto import normalize_parameters
    normalized_parameters = normalize_parameters({'parameters': parameters or {}})
    if language not in LANGUAGES or not str(name).strip():
        raise ValueError('Укажите язык EN/RU и название профиля')
    if previous_profile:
        previous = get_profile(root, previous_profile)
        if previous['language'] != language:
            raise ValueError('Предыдущий профиль должен иметь тот же язык')
    identifier = uuid.uuid4().hex
    directory = root/'profiles'/identifier
    source = directory/'source'/'karaoke_generator'
    source.mkdir(parents=True, exist_ok=False)
    files = {}
    for path in sorted(Path(__file__).parent.glob('*.py')):
        data = path.read_bytes()
        (source/path.name).write_bytes(data)
        files[path.name] = digest(data)
    profile = {'id': identifier, 'language': language, 'name': str(name).strip(), 'created_at': now(),
               'scope': 'manual-automatic-proposals; upstream acoustic artifacts frozen per run',
               'previous_profile': previous_profile, 'parameters': normalized_parameters,
               'source_files': files, 'source_sha256': digest(json.dumps(files, sort_keys=True).encode()),
               'environment': environment(), 'models': 'Recorded in each immutable upstream analysis input',
               'entrypoint': 'karaoke_generator.manual_auto.propose_manual_annotations'}
    write(directory/'profile.json', profile)
    return profile


def get_profile(root, identifier):
    return read(root/'profiles'/_id(identifier)/'profile.json')


def run_profile(root, identifier, result_dir):
    """Dispatch the preserved code, never relabel execution of the current module."""
    profile = get_profile(root, identifier)
    if profile['environment'] != environment():
        raise ValueError('Окружение профиля изменилось; восстановите записанные версии зависимостей')
    source = root/'profiles'/identifier/'source'
    for filename, expected in profile['source_files'].items():
        if digest((source/'karaoke_generator'/filename).read_bytes()) != expected:
            raise ValueError('Сохранённый исполняемый код профиля повреждён')
    script = ('import sys,json; sys.path.insert(0,sys.argv[1]); '
              'from pathlib import Path; '
              'from karaoke_generator.manual_auto import propose_manual_annotations; '
              'p=json.loads(sys.argv[3]); '
              'r=propose_manual_annotations(Path(sys.argv[2]),profile=p,force=True); '
              'print("MANUAL_RESULT="+json.dumps(r,ensure_ascii=False))')
    completed = subprocess.run([sys.executable, '-c', script, str(source.resolve()), str(Path(result_dir).resolve()),
                                json.dumps(profile)], cwd=source, capture_output=True, text=True, timeout=1200)
    lines = [s for s in completed.stdout.splitlines() if s.startswith('MANUAL_RESULT=')]
    if completed.returncode or not lines:
        raise RuntimeError('Расчёт профиля завершился ошибкой: '+completed.stderr[-3000:])
    data = json.loads(lines[-1].split('=', 1)[1])
    data['profile_id'] = identifier
    data['profile_source_sha256'] = profile['source_sha256']
    return data


def state(root):
    path = root/'state.json'
    return read(path) if path.exists() else {'revision': 0, 'corpus': [], 'active': {'en': None, 'ru': None},
                                             'activation_history': [], 'control_history': []}


def register(root, entry, expected_revision):
    if entry.get('language') not in LANGUAGES or entry.get('split') not in ('tuning', 'control'):
        raise ValueError('Укажите EN/RU и назначение: настройка или контроль')
    if entry['split'] == 'tuning' and not entry.get('reference'):
        raise ValueError('Для настройки нужен сохранённый проверенный эталон')
    if entry.get('reference') and not entry['reference'].get('project', {}).get('reviews'):
        raise ValueError('Эталон не содержит проверенных интервалов')
    if entry.get('reference'):
        reference = entry['reference']
        if reference.get('language') != entry['language']:
            raise ValueError('Язык эталона не совпадает с языком записи в корпусе')
        if any(reference.get('source', {}).get(key) != entry['source'].get(key)
               for key in ('audio_sha256', 'lyrics_sha256')):
            raise ValueError('Эталон относится к другой записи или версии TXT')
    with locked(root):
        data = state(root)
        if expected_revision != data['revision']:
            raise ValueError('Корпус изменился в другой вкладке. Обновите список')
        audio = entry['source']['audio_sha256']
        conflict = [e for e in data['corpus'] if e['source']['audio_sha256'] == audio and e['split'] != entry['split']]
        if conflict:
            raise ValueError('Одна запись не может быть и настройкой, и контролем')
        current = [e for e in data['corpus'] if not (e['source']['audio_sha256'] == audio and e['split'] == entry['split'])]
        if entry['split'] == 'control':
            # The user can replace the selected control, but every prior cycle pins its own inputs.
            current = [e for e in current if not (e['split'] == 'control' and e['language'] == entry['language'])]
            data['control_history'].append(copy.deepcopy(entry))
        current.append(copy.deepcopy(entry))
        data.update(corpus=current, revision=data['revision']+1)
        write(root/'state.json', data)
        return data


def readiness(corpus):
    counts = {lang: {'tuning': len({e['source']['audio_sha256'] for e in corpus if e['language'] == lang and e['split'] == 'tuning' and e.get('reference')}),
                     'control': sum(e['language'] == lang and e['split'] == 'control' for e in corpus)} for lang in LANGUAGES}
    return {'counts': counts, 'ready': all(c['tuning'] >= 3 and c['control'] == 1 for c in counts.values()),
            'message': 'Нужны 3 EN + 3 RU проверенных эталона и отдельные контрольные песни: 1 EN + 1 RU'}


def create_cycle(root, *, baselines, candidates, changes, expected_revision):
    with locked(root):
        saved = state(root)
        if saved['revision'] != expected_revision:
            raise ValueError('Состав корпуса изменился; обновите список перед запуском')
        ready = readiness(saved['corpus'])
        if not ready['ready']:
            raise ValueError(ready['message'])
        profiles = {}
        for lang in LANGUAGES:
            for side, ids in (('baseline', baselines), ('candidate', candidates)):
                profile = get_profile(root, ids.get(lang, ''))
                if profile['language'] != lang:
                    raise ValueError('Язык профиля не совпадает с языком сравнения')
                profiles[f'{lang}:{side}'] = profile
        if not str(changes).strip():
            raise ValueError('Опишите изменения кандидата')
        identifier = uuid.uuid4().hex
        cycle = {'id': identifier, 'created_at': now(), 'status': 'queued', 'changes': changes,
                 'corpus': copy.deepcopy(saved['corpus']), 'profiles': profiles,
                 'baseline': dict(baselines), 'candidate': dict(candidates), 'results': {},
                 'reviews': {}, 'decisions': {'en': None, 'ru': None}, 'events': [],
                 'evaluation_policy': {'version': EVALUATOR_VERSION, 'match_window': MATCH_WINDOW},
                 'control_policy': 'Постоянная знакомая контрольная пара; повторная проверка не является независимой оценкой новых песен'}
        write(root/'cycles'/identifier/'cycle.json', cycle)
        return cycle


def get_cycle(root, identifier):
    return read(root/'cycles'/_id(identifier)/'cycle.json')


def _event(root, identifier, status, **data):
    with locked(root):
        cycle = get_cycle(root, identifier)
        cycle.update(status=status, updated_at=now(), **data)
        cycle['events'].append({'at': now(), 'status': status})
        write(root/'cycles'/identifier/'cycle.json', cycle)
        return cycle


def run_cycle(root, identifier, jobs_root):
    with locked(root):
        cycle = get_cycle(root, identifier)
        if cycle['status'] != 'queued':
            raise ValueError('Этот цикл уже запускался; создайте новый, чтобы сохранить историю')
        cycle.update(status='running', updated_at=now())
        cycle['events'].append({'at': now(), 'status': 'running'})
        write(root/'cycles'/identifier/'cycle.json', cycle)
    results = {}
    try:
        for entry in cycle['corpus']:
            if entry['split'] != 'control':
                continue
            job_id, lang = entry['job_id'], entry['language']
            result_dir = jobs_root/_id(job_id)/'result'
            from .manual_lyrics import load_project
            actual = load_project(result_dir, job_id)
            for key in ('audio_sha256', 'lyrics_sha256'):
                if actual['source'].get(key) != entry['source'].get(key):
                    raise ValueError('Контрольные исходники изменились; прежний цикл сохранён')
            results[job_id] = {'language': lang, 'title': entry['title'], 'source': entry['source']}
            baseline_inputs = None
            for side in ('baseline', 'candidate'):
                data = run_profile(root, cycle[side][lang], result_dir)
                # Human corrections and review answers are never input to the recognizer.
                if any(data.get('source', {}).get(key) != entry['source'].get(key)
                       for key in ('audio_sha256', 'lyrics_sha256')):
                    raise ValueError('Исходники автоматического результата отличаются от зафиксированных в цикле')
                if side == 'baseline':
                    baseline_inputs = data.get('inputs')
                elif data.get('inputs') != baseline_inputs:
                    raise ValueError('Акустические входы изменились между версиями до и после')
                data['source'] = entry['source']
                target = root/'cycles'/identifier/job_id/(side+'.json')
                write(target, data)
                result = {'run_id': data.get('run_id'), 'profile_id': cycle[side][lang],
                          'sha256': digest(target.read_bytes()), 'file': f'{job_id}/{side}.json',
                          'metrics': evaluate_manual(entry.get('reference'), data)}
                results[job_id][side] = result
                _event(root, identifier, 'running', results=results)
        return _event(root, identifier, 'ready', results=results)
    except Exception as exc:
        return _event(root, identifier, 'error', error=str(exc), results=results)


def review_cycle(root, identifier, job_id, verdict, note):
    if verdict not in ('better', 'same', 'worse'):
        raise ValueError('Выберите: лучше, без улучшения или хуже')
    with locked(root):
        cycle = get_cycle(root, identifier)
        if cycle['status'] not in ('ready', 'user_reviewed', 'profile_accepted') or job_id not in cycle['results']:
            raise ValueError('Дождитесь готового сравнения этой песни')
        review = {'verdict': verdict, 'note': str(note), 'at': now()}
        cycle['reviews'][job_id] = review
        cycle['events'].append({'at': now(), 'status': 'user_reviewed', 'job_id': job_id, **review})
        cycle['status'] = 'user_reviewed'
        write(root/'cycles'/identifier/'cycle.json', cycle)
        return cycle


def activate(root, identifier, language):
    if language not in LANGUAGES:
        raise ValueError('Неизвестный язык')
    with locked(root):
        cycle, saved = get_cycle(root, identifier), state(root)
        songs = [k for k, v in cycle['results'].items() if v['language'] == language]
        if not songs or not all(k in cycle['reviews'] and 'candidate' in cycle['results'][k] for k in songs):
            raise ValueError('Сначала оцените готовое сравнение для этого языка')
        profile_id = cycle['candidate'][language]
        if saved['active'][language] == profile_id:
            return saved
        entry = {'at': now(), 'language': language, 'previous': saved['active'][language] or cycle['baseline'][language],
                 'profile_id': profile_id, 'cycle_id': identifier, 'action': 'accept'}
        saved['active'][language] = profile_id
        saved['activation_history'].append(entry)
        saved['revision'] += 1
        cycle['decisions'][language] = entry
        cycle['events'].append(dict(entry, status='profile_accepted'))
        cycle['status'] = 'profile_accepted'
        write(root/'state.json', saved)
        write(root/'cycles'/identifier/'cycle.json', cycle)
        return saved


def rollback(root, language):
    if language not in LANGUAGES:
        raise ValueError('Неизвестный язык')
    with locked(root):
        saved = state(root)
        history = [e for e in saved['activation_history'] if e['language'] == language]
        if not history:
            raise ValueError('Предыдущего профиля пока нет')
        current, previous = saved['active'][language], history[-1]['previous']
        saved['active'][language] = previous
        saved['activation_history'].append({'at': now(), 'language': language, 'previous': current,
                                            'profile_id': previous, 'action': 'rollback'})
        saved['revision'] += 1
        write(root/'state.json', saved)
        return saved


def catalogue(root):
    saved = state(root)
    profiles = [read(p) for p in sorted((root/'profiles').glob('*/profile.json'))]
    cycles = [read(p) for p in sorted((root/'cycles').glob('*/cycle.json'))]
    return {**saved, 'readiness': readiness(saved['corpus']), 'profiles': profiles,
            'cycles': sorted(cycles, key=lambda c: c['created_at'], reverse=True)}
