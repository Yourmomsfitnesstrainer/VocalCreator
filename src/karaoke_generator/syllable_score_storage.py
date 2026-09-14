"""Immutable syllable candidates and optimistic manual revisions.

Only explicit preparation calls the existing analysis derivatives. Readers and
savers validate the saved source-note snapshot without invoking an analysis.
An index replacement is the sole publication point for every transaction.
"""
from __future__ import annotations

import copy
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import uuid

from .timing_cache import file_sha256, fingerprint

STORAGE_VERSION = "syllable-storage-1"
_LOCK = threading.RLock()
_ACTIVE: set[str] = set()


class ScoreError(ValueError):
    def __init__(self, message: str, *, status: int = 422, code: str = "invalid_score", **details):
        super().__init__(message)
        self.status = status
        self.details = {"message": message, "code": code, **details}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _root(result_dir: Path):
    return Path(result_dir) / "syllable-score"


def _read(path: Path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("expected object")
        return value
    except (OSError, ValueError) as exc:
        raise ScoreError("Сохранённый файл повреждён или недоступен; прежние данные сохранены",
                         status=409, code="unreadable", file=path.name) from exc


def _write(path: Path, payload: dict, *, exclusive=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".score-", suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            json.dump(payload, output, ensure_ascii=False, allow_nan=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        if exclusive:
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
        # Persist the directory entry as well as the file before publishing index.
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@contextmanager
def _locked(result_dir: Path):
    with _LOCK:
        root = _root(result_dir)
        root.mkdir(parents=True, exist_ok=True)
        with (root / ".lock").open("a") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _index(result_dir: Path, job_id: str):
    path = _root(result_dir) / "index.json"
    if not path.exists():
        return {"schema_version": 1, "job_id": job_id, "status": "not_prepared", "operation": None,
                "published": None, "draft": None, "autos": {}, "revisions": {}, "requests": {}}
    data = _read(path)
    if data.get("schema_version") != 1 or data.get("job_id") != job_id:
        raise ScoreError("Несовместимый индекс слоговой партии", status=409, code="incompatible")
    if not all(isinstance(data.get(field), dict) for field in ("autos", "revisions", "requests")):
        raise ScoreError("Неполный индекс слоговой партии", status=409, code="unreadable")
    return data


def _valid_key(value):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _checked_file(path: Path, expected: str):
    try:
        if not _valid_key(expected) or file_sha256(path) != expected:
            raise ValueError("payload SHA mismatch")
        return _read(path)
    except (OSError, ValueError) as exc:
        raise ScoreError("Контрольная сумма сохранённой партии не совпадает", status=409,
                         code="payload_corrupt", file=path.name) from exc


def _auto(result_dir: Path, index: dict, key: str):
    if not _valid_key(key) or key not in index["autos"]:
        raise ScoreError("Автоматическая версия не найдена", status=404, code="not_found")
    record = index["autos"][key]
    if not isinstance(record, dict) or not all(_valid_key(record.get(field)) for field in ("payload_sha256", "notes_payload_sha256")):
        raise ScoreError("Неполная запись автоматической версии", status=409, code="invalid_artifact")
    directory = _root(result_dir) / "auto" / key
    notes = _checked_file(directory / "source-notes.json", record["notes_payload_sha256"])
    data = _checked_file(directory / "syllable-score.json", record["payload_sha256"])
    from .syllable_score import validate_score
    try:
        validate_score(data, notes["notes"], expected_job_id=index["job_id"],
                       expected_source=notes["source"], expected_base_analysis_key=key)
        if fingerprint({"notes": notes["notes"]}) != data["source"]["notes_sha256"]:
            raise ValueError("source note identity mismatch")
    except (ValueError, KeyError, TypeError) as exc:
        raise ScoreError("Сохранённая база не прошла проверку схемы", status=409,
                         code="invalid_artifact") from exc
    return data, notes["notes"]


def read_artifact(result_dir: Path, job_id: str, key: str):
    return _auto(result_dir, _index(result_dir, job_id), key)[0]


def read_source_notes(result_dir: Path, job_id: str, key: str):
    document, notes = _auto(result_dir, _index(result_dir, job_id), key)
    piano = _source_piano(result_dir, document)
    return {"job_id": job_id, "base_analysis_key": key, "source": document["source"], "notes": notes,
            "full_melody_key": piano[0]["cache_key"] if piano else None,
            "full_melody_mode": piano[0]["mode"] if piano else None,
            "source_piano_sha256": piano[0]["piano_sha256"] if piano else None,
            "source_piano_url": f"/api/studio/jobs/{job_id}/syllables/artifacts/{key}/piano.wav" if piano else None}


def _source_piano(result_dir: Path, document: dict):
    """Resolve the saved score's piano, including a no-longer-current full base."""
    from .v3_storage import MODES, _read_cache
    key = document["provenance"].get("full_melody_key")
    if key is None:
        return None
    if not _valid_key(key):
        raise ScoreError("Неизвестна исходная партия пианино", status=409, code="invalid_source_piano")
    directory = Path(result_dir) / "learning-v3" / key
    mode = _read(directory / "learning.json").get("mode")
    full = _read_cache(directory, key, mode) if mode in MODES else None
    if (full is None or document["provenance"].get("full_melody_mode", mode) != mode
            or fingerprint({"notes": full.get("notes")}) != document["source"]["notes_sha256"]
            or document["provenance"].get("full_piano_sha256", full["piano_sha256"]) != full["piano_sha256"]):
        raise ScoreError("Сохранённое пианино или его нотная база повреждены; новая партия не подставлена",
                         status=409, code="invalid_source_piano")
    return full, directory / "piano.wav"


def source_piano_path(result_dir: Path, job_id: str, key: str):
    document, _ = _auto(result_dir, _index(result_dir, job_id), key)
    piano = _source_piano(result_dir, document)
    if piano is None:
        raise ScoreError("У этой партии нет сохранённого пианино", status=404, code="not_found")
    return piano[1]


def _revision(result_dir: Path, index: dict, revision: int):
    if type(revision) is not int or revision < 1 or str(revision) not in index["revisions"]:
        raise ScoreError("Ревизия не найдена", status=404, code="not_found")
    record = index["revisions"][str(revision)]
    if not isinstance(record, dict) or not _valid_key(record.get("payload_sha256")) or not _valid_key(record.get("base_analysis_key")):
        raise ScoreError("Неполная запись ручной ревизии", status=409, code="invalid_revision")
    data = _checked_file(_root(result_dir) / "revisions" / f"{revision}.json", record["payload_sha256"])
    base, notes = _auto(result_dir, index, record["base_analysis_key"])
    from .syllable_score import validate_score
    try:
        validate_score(data, notes, expected_job_id=index["job_id"], expected_source=base["source"],
                       expected_base_analysis_key=base["base_analysis_key"])
        if data.get("revision") != revision or data.get("canonical_text") != base["canonical_text"]:
            raise ValueError("revision or canonical text mismatch")
    except (ValueError, KeyError, TypeError) as exc:
        raise ScoreError("Ручная ревизия не прошла проверку схемы", status=409,
                         code="invalid_revision") from exc
    return data


def read_revision(result_dir: Path, job_id: str, revision: int):
    return _revision(result_dir, _index(result_dir, job_id), revision)


def _operation_alive(operation):
    if not operation:
        return False
    if operation.get("pid") == os.getpid():
        return operation.get("id") in _ACTIVE
    try:
        os.kill(operation["pid"], 0)
        return True
    except (OSError, KeyError, TypeError):
        return False


def _state(result_dir: Path, index: dict):
    prefix = f"/api/studio/jobs/{index['job_id']}/syllables"
    published = copy.deepcopy(index.get("published"))
    draft = copy.deepcopy(index.get("draft"))
    errors = []
    if published:
        try:
            _auto(result_dir, index, published["key"])
            published["url"] = f"{prefix}/artifacts/{published['key']}/syllable-score.json"
            published["source_notes_url"] = f"{prefix}/artifacts/{published['key']}/source-notes.json"
        except ScoreError as exc:
            errors.append(exc.details)
            published = None
    if draft:
        try:
            _revision(result_dir, index, draft["revision"])
            draft["url"] = f"{prefix}/revisions/{draft['revision']}"
            draft["source_notes_url"] = f"{prefix}/artifacts/{draft['base_analysis_key']}/source-notes.json"
        except ScoreError as exc:
            errors.append(exc.details)
            draft = None
    status = index.get("status", "not_prepared")
    if status in {"queued", "running"} and not _operation_alive(index.get("operation")):
        status = "interrupted"
    if errors:
        status = "partial" if published or draft else "failed"
    effective = draft or published
    return {"status": status, "job_id": index["job_id"],
            "operation_id": (index.get("operation") or {}).get("id"),
            "published": published, "draft": draft,
            "effective_url": effective.get("url") if effective else None,
            "effective_source_notes_url": effective.get("source_notes_url") if effective else None,
            "base_analysis_key": draft["base_analysis_key"] if draft else published["key"] if published else None,
            "revision": draft["revision"] if draft else 0,
            "candidate_available": bool(published and draft and published["key"] != draft["base_analysis_key"]),
            "error": errors[0] if errors else index.get("error")}


def read_state(result_dir: Path, job_id: str):
    """No writes, derivative preparation, model loading, or implicit migration."""
    return _state(result_dir, _index(result_dir, job_id))


def _algorithms():
    from . import syllable_score, syllables, learning_v3, lyric_recovery
    from .v3_storage import _algorithms as v3_algorithms
    return {"storage": STORAGE_VERSION, "score": syllable_score.SCORE_VERSION,
            "mapper": syllable_score.MAPPER_VERSION, "options": syllable_score.SCORE_OPTIONS,
            "implementations": {module.__name__: file_sha256(Path(module.__file__))
                                for module in (syllable_score, syllables, learning_v3, lyric_recovery)},
            "full_melody": v3_algorithms()}


def _input_identity(result_dir: Path):
    # Identity records exact saved inputs; no paths are accepted from a client.
    required = [result_dir / name for name in ("lyrics.txt", "melody.json", "vocals.wav")]
    originals = sorted(result_dir.glob("original.*"))
    if not originals:
        originals = sorted((result_dir.parent / "input").glob("audio.*"))
    if len(originals) != 1 or any(not p.is_file() for p in required):
        raise ScoreError("Нужны сохранённые исходники, вокал, текст и ноты", status=409, code="missing_sources")
    paths = [*required, *originals]
    if (result_dir / "alignment.json").is_file():
        paths.append(result_dir / "alignment.json")
    paths += sorted((result_dir / "work/timing-cache").glob("refined-*.json"))
    paths += sorted((result_dir / "work/timing-cache").glob("asr-*.json"))
    identities = {str(path.relative_to(result_dir.parent)): file_sha256(path) for path in paths}
    return {"files": identities, "audio_sha256": file_sha256(originals[0]), "algorithms": _algorithms()}


def _request(payload: dict, method: str):
    rid = payload.get("request_id")
    if not isinstance(rid, str) or not 1 <= len(rid) <= 160 or not re.fullmatch(r"[A-Za-z0-9_.:-]+", rid):
        raise ScoreError("Нужен request_id длиной 1–160 символов", code="invalid_request_id")
    try:
        digest = fingerprint({"method": method, "body": payload})
    except (ValueError, TypeError) as exc:
        raise ScoreError("Тело запроса содержит недопустимое значение") from exc
    return rid, digest


def _prior_request(index, rid, digest):
    prior = index["requests"].get(rid)
    if prior and prior["digest"] != digest:
        raise ScoreError("request_id уже использован с другим телом", status=409, code="request_conflict")
    return prior


def queue_prepare(result_dir: Path, job_id: str, manifest: dict, payload: dict):
    """Reserve an explicit operation; return (state, HTTP code, operation to run)."""
    if set(payload) - {"request_id", "retry"} or type(payload.get("retry", False)) is not bool:
        raise ScoreError("Ожидаются request_id и retry: true/false")
    rid, digest = _request(payload, "POST")
    with _locked(result_dir):
        index = _index(result_dir, job_id)
        prior = _prior_request(index, rid, digest)
        if prior:
            state = _state(result_dir, index)
            return state, 202 if state["status"] in {"queued", "running"} else 200, None
        if manifest.get("status") in {"queued", "running"}:
            raise ScoreError("Дождитесь завершения исходного анализа", status=409, code="analysis_running")
        state = _state(result_dir, index)
        if state["status"] in {"queued", "running"}:
            index["requests"][rid] = {"digest": digest, "operation_id": state["operation_id"]}
            _write(_root(result_dir) / "index.json", index)
            return state, 202, None
        if state["status"] in {"failed", "interrupted"} and not payload.get("retry", False):
            raise ScoreError("Подготовка прервана; повторите явно с retry=true", status=409,
                             code="retry_required", preparation_state=state)
        inputs = _input_identity(result_dir)
        if index.get("published") and state.get("published") and index["published"].get("input_key") == fingerprint(inputs):
            index["requests"][rid] = {"digest": digest, "result_key": index["published"]["key"]}
            index["status"], index["error"] = "ready", None
            _write(_root(result_dir) / "index.json", index)
            return _state(result_dir, index), 200, None
        operation = {"id": uuid.uuid4().hex, "pid": os.getpid(), "request_id": rid, "created_at": _now()}
        index.update(status="queued", operation=operation, error=None)
        index["requests"][rid] = {"digest": digest, "operation_id": operation["id"]}
        _write(_root(result_dir) / "index.json", index)
        _ACTIVE.add(operation["id"])
        return _state(result_dir, index), 202, operation["id"]


def _build(result_dir: Path, job_id: str, manifest: dict):
    from .models import AlignmentResult
    from .studio_models import NoteEvent
    from .syllables import canonical_words, load_character_evidence
    from .syllable_score import build_score, validate_score
    from .v3_storage import prepare_v3, ready_modes
    from .lyric_recovery import prepare_lyric_evidence
    available = set(manifest.get("artifacts") or {})
    available.update(name for name in ("lyrics.txt", "alignment.json", "vocals.wav", "melody.json")
                     if (result_dir / name).is_file())
    inputs = _input_identity(result_dir)
    compatible = ready_modes(result_dir, available)
    mode = next((name for name in ("pro", "light", "medium") if name in compatible), "pro")
    if mode in compatible:
        full = compatible[mode]
    else:
        full, _ = prepare_v3(result_dir, mode, available=available)
    notes = full["notes"]
    duration = full["timeline"]["duration"]
    text = (result_dir / "lyrics.txt").read_text(encoding="utf-8")
    alignment = AlignmentResult.from_dict(_read(result_dir / "alignment.json")) if (result_dir / "alignment.json").is_file() else None
    character = load_character_evidence(result_dir)
    words = canonical_words(text, alignment, duration)
    # Reuse the same acoustic spans; no note segmentation for labels.
    events = [NoteEvent(note["id"], note["start"], note["end"], note["midi"],
                        note.get("cents", 0), note.get("confidence", 0), note.get("source", "saved"))
              for note in notes]
    language = getattr(alignment, "language", None) or full.get("language", "unknown")
    lyric = prepare_lyric_evidence(result_dir / "vocals.wav", words, events, duration=duration,
                                   language=language, recognized_words=character.get("recognized_words", []),
                                   cache_root=result_dir / "lyric-recovery-v3")
    source = {"duration": duration, "language": language, "audio_sha256": inputs["audio_sha256"],
              "vocals_sha256": file_sha256(result_dir / "vocals.wav"),
              "lyrics_sha256": file_sha256(result_dir / "lyrics.txt"),
              "notes_sha256": fingerprint({"notes": notes})}
    evidence = {"character": fingerprint(character), "lyric": fingerprint(lyric),
                "full_melody_key": full["cache_key"]}
    key = fingerprint({"inputs": inputs, "source": source, "evidence": evidence})
    data = build_score(text, alignment, notes, job_id=job_id, source=source,
                       base_analysis_key=key, character_evidence=character, lyric_evidence=lyric)
    data.setdefault("provenance", {}).update({"inputs": inputs, "evidence": evidence,
        "character_evidence": character.get("diagnostics", {}),
        "lyric_evidence": {k: lyric.get(k) for k in ("cache_key", "payload_sha256", "status", "reason", "spec")},
        "source_notes": "full-melody", "full_melody_key": full["cache_key"],
        "full_melody_mode": mode,
        **({"full_piano_sha256": full["piano_sha256"]} if full.get("piano_sha256") else {}),
        "full_melody_cache_hit": mode in compatible})
    validate_score(data, notes, expected_job_id=job_id, expected_source=source, expected_base_analysis_key=key)
    if _input_identity(result_dir) != inputs:
        raise ScoreError("Исходники изменились во время подготовки; повторите анализ", status=409, code="source_changed")
    return data, notes, inputs


def run_prepare(result_dir: Path, job_id: str, manifest: dict, operation_id: str):
    try:
        with _locked(result_dir):
            index = _index(result_dir, job_id)
            if (index.get("operation") or {}).get("id") != operation_id:
                return
            index["status"] = "running"
            _write(_root(result_dir) / "index.json", index)
        data, notes, inputs = _build(result_dir, job_id, manifest)
        with _locked(result_dir):
            index = _index(result_dir, job_id)
            if (index.get("operation") or {}).get("id") != operation_id:
                return
            key = data["base_analysis_key"]
            directory = _root(result_dir) / "auto" / key
            if key in index["autos"]:
                _auto(result_dir, index, key)
                record = index["autos"][key]
            else:
                # Unpublished partial output never becomes an artifact. Preserve
                # it for diagnosis rather than overwriting its immutable files.
                if directory.exists():
                    directory.rename(directory.with_name(f".incomplete-{key}-{uuid.uuid4().hex}"))
                _write(directory / "source-notes.json", {"source": data["source"], "notes": notes}, exclusive=True)
                _write(directory / "syllable-score.json", data, exclusive=True)
                record = {"key": key, "payload_sha256": file_sha256(directory / "syllable-score.json"),
                          "notes_payload_sha256": file_sha256(directory / "source-notes.json"),
                          "input_key": fingerprint(inputs), "created_at": _now()}
                index["autos"][key] = record
                _auto(result_dir, index, key)  # SHA and schema before publication.
            index.update(status="ready", published=record, error=None)
            _write(_root(result_dir) / "index.json", index)
    except Exception as exc:
        try:
            with _locked(result_dir):
                index = _index(result_dir, job_id)
                if (index.get("operation") or {}).get("id") == operation_id:
                    details = exc.details if isinstance(exc, ScoreError) else {"code": "prepare_failed", "message": str(exc)}
                    index.update(status="failed", error=details)
                    _write(_root(result_dir) / "index.json", index)
        except (OSError, ScoreError):
            pass  # The previous index stays readable; dead operation reports interrupted.
    finally:
        _ACTIVE.discard(operation_id)
    return read_state(result_dir, job_id)


def prepare_score(result_dir: Path, job_id: str, manifest: dict):
    """Synchronous entry for an explicit complete analysis, also used by HTTP worker."""
    state, _, operation = queue_prepare(result_dir, job_id, manifest,
                                         {"request_id": uuid.uuid4().hex, "retry": True})
    return run_prepare(result_dir, job_id, manifest, operation) if operation else state


def save_revision(result_dir: Path, job_id: str, payload: dict):
    if set(payload) - {"request_id", "base_revision", "document"}:
        raise ScoreError("Неизвестные поля сохранения")
    rid, digest = _request(payload, "PUT")
    base_revision = payload.get("base_revision")
    if type(base_revision) is not int or base_revision < 0 or not isinstance(payload.get("document"), dict):
        raise ScoreError("Нужны base_revision и document")
    with _locked(result_dir):
        index = _index(result_dir, job_id)
        prior = _prior_request(index, rid, digest)
        if prior:
            revision = prior["revision"]
            return {**_state(result_dir, index), "revision": revision,
                    "document": _revision(result_dir, index, revision)}
        current_revision = (index.get("draft") or {}).get("revision", 0)
        if base_revision != current_revision:
            raise ScoreError("Эта партия уже сохранена в другой вкладке. Ваши правки остаются локально",
                             status=409, code="revision_conflict", current_revision=current_revision)
        data = copy.deepcopy(payload["document"])
        key = data.get("base_analysis_key")
        expected_key = (index.get("draft") or {}).get("base_analysis_key") or (index.get("published") or {}).get("key")
        if key != expected_key:
            raise ScoreError("Ручная партия относится к другой базе; автоматический перенос запрещён",
                             status=409, code="base_conflict", base_analysis_key=expected_key)
        base, notes = _auto(result_dir, index, key)
        if data.get("canonical_text") != base["canonical_text"] or data.get("source") != base["source"]:
            raise ScoreError("Исходный текст и источник базы нельзя заменять при сохранении",
                             status=409, code="source_mismatch")
        if data.get("revision") != base_revision:
            raise ScoreError("Ревизия документа не совпадает с base_revision", status=409,
                             code="revision_conflict", current_revision=current_revision)
        from .syllable_score import validate_score
        try:
            validate_score(data, notes, expected_job_id=job_id, expected_source=base["source"], expected_base_analysis_key=key)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise ScoreError(f"Проверьте слоги и временные области: {exc}") from exc
        # A failed earlier index write may leave an orphan; never overwrite it.
        revision = current_revision + 1
        while (_root(result_dir) / "revisions" / f"{revision}.json").exists():
            revision += 1
        data["revision"] = revision
        # Cache identities/algorithm versions belong to the immutable base,
        # never to a client-supplied replacement provenance object.
        data["provenance"] = {**copy.deepcopy(base["provenance"]),
                              "base_revision": base_revision, "saved_at": _now()}
        destination = _root(result_dir) / "revisions" / f"{revision}.json"
        _write(destination, data, exclusive=True)
        record = {"revision": revision, "base_analysis_key": key, "payload_sha256": file_sha256(destination)}
        index["revisions"][str(revision)] = record
        index["draft"] = record
        index["requests"][rid] = {"digest": digest, "revision": revision}
        _revision(result_dir, index, revision)
        _write(_root(result_dir) / "index.json", index)
        return {**_state(result_dir, index), "revision": revision, "document": data}


def read_legacy(result_dir: Path):
    path = Path(result_dir) / "manual-lyrics.json"
    if not path.is_file():
        raise ScoreError("Прежних ручных правок нет", status=404, code="not_found")
    return _read(path)
