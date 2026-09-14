"""Local editable lyrics and immutable human references, independent of acoustics.

Reading, saving and importing never invoke an analysis or audio synthesis stage.
Times are seconds of the original recording; overlaps are deliberately preserved.
"""
from __future__ import annotations

import copy
import fcntl
import json
import math
import os
import re
import tempfile
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .timing_cache import file_sha256

SCHEMA_VERSION = 1
FEATURES = ("presence", "text", "start", "end", "role")
_LOCK = threading.RLock()


class ManualError(ValueError):
    def __init__(self, message: str, *, status: int = 422, **details):
        super().__init__(message)
        self.status = status
        self.details = {"message": message, **details}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("expected object")
        return value
    except (OSError, ValueError) as exc:
        raise ManualError("Сохранённый файл не читается; прежние данные сохранены", status=409,
                          code="unreadable", path=str(path)) from exc


def _atomic_write(path: Path, value: dict, *, exclusive: bool = False) -> None:
    """Publish one complete JSON, after flushing; failed writes keep the old file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".manual-", suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            json.dump(value, output, ensure_ascii=False, indent=2, allow_nan=False)
            output.flush()
            os.fsync(output.fileno())
        if exclusive:
            os.link(temporary, path)  # An existing immutable version is never overwritten.
        else:
            os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@contextmanager
def _locked(result_dir: Path):
    # flock also guards multiple local server workers, not only browser requests.
    with _LOCK:
        result_dir.mkdir(parents=True, exist_ok=True)
        with (result_dir / ".manual-lyrics.lock").open("a") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def source_identity(result_dir: Path, previous: dict | None = None) -> dict:
    """Use byte identity of the uploaded audio/TXT, never a guessed replacement."""
    if previous:
        paths = {kind: Path(previous[f"{kind}_path"]) for kind in ("audio", "lyrics")}
    else:
        input_dir = result_dir.parent / "input"
        candidates = sorted(input_dir.glob("audio.*")) or sorted(result_dir.glob("original.*"))
        paths = {"audio": candidates[0] if len(candidates) == 1 else result_dir / "original.missing",
                 "lyrics": input_dir / "lyrics.txt" if (input_dir / "lyrics.txt").is_file()
                 else result_dir / "lyrics.txt"}
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise ManualError("Исходные файлы недоступны. Восстановите именно эти файлы", status=409,
                          code="missing_sources", missing=missing)
    result = {f"{kind}_sha256": file_sha256(path) for kind, path in paths.items()}
    result.update({f"{kind}_path": str(path.resolve()) for kind, path in paths.items()})
    if previous and any(result[key] != previous.get(key) for key in ("audio_sha256", "lyrics_sha256")):
        raise ManualError("Исходники изменились; разметка принадлежит прежней записи или TXT", status=409,
                          code="source_mismatch", expected=previous, actual=result)
    return result


def _identity_equal(left: dict, right: dict) -> bool:
    return all(left.get(key) and left.get(key) == right.get(key)
               for key in ("audio_sha256", "lyrics_sha256"))


def _interval(start, end, duration: float, *, nullable: bool = False) -> tuple:
    if nullable and start is None and end is None:
        return None, None
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (start, end)):
        raise ManualError("Начало и конец должны быть числами, либо оба не заданы")
    if not 0 <= start < end <= duration:
        raise ManualError("Нужны границы 0 ≤ начало < конец ≤ длительность записи")
    return round(start, 6), round(end, 6)


def _identifier(value, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 200:
        raise ManualError(f"Некорректный ID: {label}")
    return value


def import_analysis(data: dict, *, result_dir: Path, job_id: str, source: dict, duration: float,
                    source_analysis: dict | None = None) -> dict:
    """Import words/parts once by their own IDs/times, ignoring every note label."""
    annotations, occurrences, known = [], [], set()
    parts_by_word: dict[str, list] = {}
    for part in data.get("text_parts", []):
        if part.get("id") not in known:
            parts_by_word.setdefault(part.get("word_id"), []).append(part)
            known.add(part.get("id"))
    seen_words = set()
    for index, word in enumerate(data.get("words", [])):
        word_id = str(word.get("id", f"word-{index}"))
        if word_id in seen_words:
            continue
        seen_words.add(word_id)
        occurrence_id = "occ-" + word_id
        source_parts = parts_by_word.get(word_id, [])
        items = source_parts or [word]
        ids = []
        for part in items:
            part_id = str(part.get("id", word_id))
            annotation_id = "ann-" + part_id
            try:
                start, end = _interval(part.get("start"), part.get("end"), duration, nullable=True)
            except ManualError:
                start, end = None, None
            ids.append(annotation_id)
            annotations.append({"annotation_id": annotation_id, "occurrence_id": occurrence_id,
                                "unit": "part" if len(source_parts) > 1 else "word",
                                "text": part.get("text", word.get("text", "")), "start": start, "end": end,
                                "role_id": None, "source_word_id": word_id,
                                "source_part_id": part_id if source_parts else None,
                                "source_token_index": index, "source_text": word.get("text", ""),
                                "approximate": bool(word.get("approximate") or part.get("status") != "manual"),
                                "provenance": {"kind": "analysis-import", "timing_source": part.get("timing_source", word.get("timing_source")),
                                               "source_status": part.get("status"), "original_timing": part.get("original_timing")}})
        occurrences.append({"occurrence_id": occurrence_id, "annotation_ids": ids, "complete": True,
                            "source_word_id": word_id})
    return {"schema_version": SCHEMA_VERSION, "project_id": job_id, "job_id": job_id,
            "revision": 0, "saved_at": None, "duration": duration, "source": source,
            "source_analysis": source_analysis or {}, "canonical_text": data.get("canonical_text", ""),
            "language": data.get("language", "unknown"), "roles": [], "annotations": annotations,
            "occurrences": occurrences, "reviews": []}


def _initial_project(result_dir: Path, job_id: str, manifest: dict) -> dict:
    from .v3_storage import read_text, ready_modes

    source = source_identity(result_dir)
    duration = float(manifest.get("timeline", {}).get("duration", 0))
    if not math.isfinite(duration) or duration <= 0:
        raise ManualError("Длительность анализа недоступна", status=409)
    available = set(manifest.get("artifacts", {}))
    modes = ready_modes(result_dir, available)
    data = modes.get("medium") or modes.get("light") or modes.get("pro")
    if data:
        analysis = {"kind": "learning-v3", "cache_key": data["cache_key"], "mode": data["mode"]}
    else:
        data = read_text(result_dir, available, duration)
        analysis = {"kind": "source-words", "alignment_sha256": file_sha256(result_dir / "alignment.json")
                    if (result_dir / "alignment.json").is_file() else None}
    project = import_analysis(data, result_dir=result_dir, job_id=job_id, source=source,
                              duration=duration, source_analysis=analysis)
    try:
        from .manual_auto import ready_manual_proposal
    except ImportError:
        ready_manual_proposal = None
    proposal = ready_manual_proposal(result_dir, require_current_algorithm=False) if ready_manual_proposal else None
    if proposal:
        project.update(annotations=copy.deepcopy(proposal["annotations"]), roles=copy.deepcopy(proposal["roles"]),
                       occurrences=copy.deepcopy(proposal["occurrences"]),
                       source_analysis={"kind": "manual-auto", "run_id": proposal["run_id"],
                                        "algorithm": proposal.get("algorithm"), "diagnostics": proposal.get("diagnostics")})
    try:
        project["language"] = _read(result_dir / "alignment.json").get("language", "unknown")
    except ManualError:
        pass
    return project


def read_project(result_dir: Path, job_id: str, manifest: dict) -> dict:
    path = result_dir / "manual-lyrics.json"
    if not path.exists():
        return _initial_project(result_dir, job_id, manifest)
    project = _read(path)
    try:
        if project.get("schema_version") != SCHEMA_VERSION or project.get("job_id") != job_id:
            raise ManualError("Несовместимая версия проекта", status=409, code="incompatible_project")
        source_identity(result_dir, project["source"])
    except ManualError as exc:
        exc.details["saved_project"] = project
        raise
    return project


def load_project(result_dir: Path, job_id: str) -> dict:
    return read_project(result_dir, job_id, _read(result_dir / "studio.json"))


def _semantic(annotation: dict | None):
    if annotation is None:
        return None
    return tuple(annotation.get(key) for key in ("occurrence_id", "unit", "text", "start", "end", "role_id"))


def _overlap(item: dict, start: float, end: float) -> bool:
    return item.get("start") is not None and item.get("end") is not None and item["start"] < end and item["end"] > start


def _reviews(reviews: list, annotations: list, previous: dict, revision: int, duration: float) -> list:
    old_annotations = {a["annotation_id"]: a for a in previous.get("annotations", [])}
    new_annotations = {a["annotation_id"]: a for a in annotations}
    changed = []
    for key in old_annotations.keys() | new_annotations.keys():
        before, after = old_annotations.get(key), new_annotations.get(key)
        if _semantic(before) != _semantic(after):
            changed.extend(a for a in (before, after) if a is not None)
    previous_review_ids = {r["review_id"] for r in previous.get("reviews", [])}
    output, ids = [], set()
    for original in reviews:
        if not isinstance(original, dict):
            raise ManualError("Некорректная проверка интервала")
        review = copy.deepcopy(original)
        review_id = _identifier(review.get("review_id") or uuid.uuid4().hex, "проверка")
        if review_id in ids:
            raise ManualError("Повтор ID проверки")
        ids.add(review_id)
        start, end = _interval(review.get("start"), review.get("end"), duration)
        if review.get("all_roles") is not True:
            raise ManualError("Проверка интервала требует прослушать и проверить все роли")
        if review_id in previous_review_ids and any(_overlap(a, start, end) for a in changed):
            continue
        uncertainty = review.get("uncertainties", [])
        if not isinstance(uncertainty, list):
            raise ManualError("Неопределённости должны быть списком")
        for item in uncertainty:
            if not isinstance(item, dict) or not isinstance(item.get("features", []), list) or any(f not in FEATURES for f in item.get("features", [])):
                raise ManualError("Некорректные признаки неопределённости")
            if item.get("annotation_id") is not None and item["annotation_id"] not in new_annotations:
                raise ManualError("Неопределённость ссылается на отсутствующую плашку")
            if item.get("start") is not None or item.get("end") is not None:
                _interval(item.get("start"), item.get("end"), duration)
        feature_map = {}
        requested = review.get("features")
        for annotation in annotations:
            if not _overlap(annotation, start, end):
                continue
            aid = annotation["annotation_id"]
            onset = start <= annotation["start"] < end
            flags = {"presence": onset, "text": onset, "start": onset,
                     "end": start < annotation["end"] <= end, "role": onset}
            if requested is not None:
                flags = {key: flag and requested.get(aid, {}).get(key, False) is True for key, flag in flags.items()}
            for item in uncertainty:
                if item.get("annotation_id") not in (None, aid):
                    continue
                if item.get("start") is not None and not _overlap(annotation, item["start"], item["end"]):
                    continue
                for feature in item.get("features") or FEATURES:
                    flags[feature] = False
            feature_map[aid] = flags
        review.update(review_id=review_id, start=start, end=end, all_roles=True, revision=revision,
                      features=feature_map, uncertainties=uncertainty)
        output.append(review)
    # A later explicit doubt must not be cancelled by an older overlapping
    # review's true bit when evaluators combine coverage from several intervals.
    for owner in output:
        for uncertain in owner["uncertainties"]:
            start, end = uncertain.get("start", owner["start"]), uncertain.get("end", owner["end"])
            for annotation in annotations:
                aid = annotation["annotation_id"]
                if uncertain.get("annotation_id") not in (None, aid) or not _overlap(annotation, start, end):
                    continue
                for review in output:
                    for feature in uncertain.get("features") or FEATURES:
                        if aid in review["features"]:
                            review["features"][aid][feature] = False
    return output


def validate_project(value: dict, previous: dict, *, revision: int | None = None) -> dict:
    if not isinstance(value, dict):
        raise ManualError("Ожидается проект разметки")
    project = copy.deepcopy(value)
    if project.get("schema_version") != SCHEMA_VERSION:
        raise ManualError("Неизвестная схема проекта")
    for key in ("project_id", "job_id", "duration", "canonical_text", "source_analysis"):
        if project.get(key) != previous.get(key):
            raise ManualError(f"Нельзя заменить исходное поле проекта: {key}", status=409)
    if not _identity_equal(project.get("source", {}), previous["source"]):
        raise ManualError("Этот слой принадлежит другой записи или TXT", status=409, code="source_mismatch")
    project["source"] = copy.deepcopy(previous["source"])
    for key in ("roles", "annotations", "occurrences", "reviews"):
        if not isinstance(project.get(key), list):
            raise ManualError(f"{key}: ожидается список")
    duration = previous["duration"]
    role_ids, annotation_ids, occurrence_ids = set(), set(), set()
    for role in project["roles"]:
        if not isinstance(role, dict):
            raise ManualError("Некорректная роль")
        rid = _identifier(role.get("id"), "роль")
        if rid in role_ids or not isinstance(role.get("name"), str) or not role["name"].strip():
            raise ManualError("Роли должны иметь уникальные ID и непустые имена")
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", str(role.get("color", ""))):
            raise ManualError("Цвет роли должен быть в формате #RRGGBB")
        role_ids.add(rid)
    grouped = {}
    for annotation in project["annotations"]:
        if not isinstance(annotation, dict) or not isinstance(annotation.get("provenance", {}), dict):
            raise ManualError("Некорректная плашка или происхождение")
        aid = _identifier(annotation.get("annotation_id"), "плашка")
        oid = _identifier(annotation.get("occurrence_id"), "исполнение")
        if aid in annotation_ids or aid == oid:
            raise ManualError("ID плашек уникальны и отличаются от ID исполнений")
        annotation_ids.add(aid)
        if annotation.get("unit") not in {"word", "part"}:
            raise ManualError("Единица плашки — слово или часть")
        if not isinstance(annotation.get("text"), str) or not annotation["text"].strip():
            raise ManualError("Текст плашки не должен быть пустым")
        if annotation.get("role_id") is not None and annotation["role_id"] not in role_ids:
            raise ManualError("Плашка ссылается на отсутствующую роль")
        annotation["start"], annotation["end"] = _interval(annotation.get("start"), annotation.get("end"), duration, nullable=True)
        annotation.pop("duration", None)
        grouped.setdefault(oid, []).append(annotation)
    for occurrence in project["occurrences"]:
        if not isinstance(occurrence, dict):
            raise ManualError("Некорректное исполнение")
        oid = _identifier(occurrence.get("occurrence_id"), "исполнение")
        if oid in occurrence_ids or oid not in grouped or not isinstance(occurrence.get("complete"), bool):
            raise ManualError("Некорректный состав исполнений")
        occurrence_ids.add(oid)
        members = grouped[oid]
        ids = occurrence.get("annotation_ids", [])
        if not isinstance(ids, list) or len(ids) != len(set(ids)) or set(ids) != {a["annotation_id"] for a in members}:
            raise ManualError("Состав исполнения должен точно совпадать с его плашками")
        if any(a["unit"] == "word" for a in members) and len(members) != 1:
            raise ManualError("Целое слово — одна плашка; несколько плашек должны быть частями")
        # A copy cannot promote a selected subset of a word to a complete word.
        copied = [a.get("provenance", {}).get("copied_from_annotation_id") for a in members]
        if any(copied):
            old = {a["annotation_id"]: a for a in previous.get("annotations", [])}
            known = [old[cid] for cid in copied if cid in old]
            if known:
                original_ids = {a["annotation_id"] for a in old.values()
                                if a["occurrence_id"] in {a["occurrence_id"] for a in known}}
                original_groups = [o for o in previous["occurrences"] if o["occurrence_id"] in {a["occurrence_id"] for a in known}]
                if set(copied) != original_ids or not all(o["complete"] for o in original_groups):
                    occurrence["complete"] = False
        previous_group = next((o for o in previous["occurrences"] if o["occurrence_id"] == oid), None)
        if previous_group and (not previous_group["complete"] or set(ids) < set(previous_group["annotation_ids"])):
            occurrence["complete"] = False
    if occurrence_ids != set(grouped):
        raise ManualError("Каждая плашка должна принадлежать зарегистрированному исполнению")
    project["revision"] = revision if revision is not None else previous["revision"] + 1
    project["reviews"] = _reviews(project["reviews"], project["annotations"], previous, project["revision"], duration)
    project["saved_at"] = _now()
    return project


def save_project(result_dir: Path, job_id: str, manifest: dict, value: dict) -> dict:
    with _locked(result_dir):
        previous = read_project(result_dir, job_id, manifest)
        if type(value.get("revision")) is not int or value["revision"] != previous["revision"]:
            raise ManualError("Проект уже сохранён в другой вкладке. Ваши правки оставлены в этой вкладке", status=409,
                              code="revision_conflict", current_revision=previous["revision"])
        project = validate_project(value, previous)
        _atomic_write(result_dir / "manual-lyrics.json", project)
        return project


def review_coverage(project: dict) -> dict:
    intervals = sorted((r["start"], r["end"]) for r in project.get("reviews", []) if r.get("all_roles"))
    merged = []
    for start, end in intervals:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    masks = {}
    for review in project.get("reviews", []):
        for aid, features in review.get("features", {}).items():
            flags = masks.setdefault(aid, {f: False for f in FEATURES})
            for key in FEATURES:
                flags[key] |= features.get(key) is True
    seconds = sum(end - start for start, end in merged)
    return {"intervals": merged, "reviewed_seconds": seconds, "total_seconds": project["duration"],
            "unreviewed_seconds": max(0, project["duration"] - seconds),
            "uncertainty_count": sum(len(r.get("uncertainties", [])) for r in project.get("reviews", [])),
            "features": {f: sum(v[f] for v in masks.values()) for f in FEATURES},
            "all_roles": True, "human_reviewed": bool(intervals)}


def create_reference(result_dir: Path, job_id: str, manifest: dict, options: dict) -> dict:
    with _locked(result_dir):
        project = read_project(result_dir, job_id, manifest)
        if project["revision"] == 0 or options.get("revision") != project["revision"]:
            raise ManualError("Сначала сохраните актуальную ревизию проекта", status=409, code="revision_conflict",
                              current_revision=project["revision"])
        if not project.get("reviews"):
            raise ManualError("Для эталона явно проверьте хотя бы один интервал, включая все роли")
        language = options.get("language", project.get("language"))
        if language not in {"en", "ru"}:
            raise ManualError("Укажите язык эталона: en или ru")
        cases = options.get("difficult_cases", [])
        if not isinstance(cases, list) or any(not isinstance(c, str) for c in cases):
            raise ManualError("Сложные случаи должны быть списком описаний")
        refs = list_references(result_dir)["references"]
        reference = {"schema_version": SCHEMA_VERSION, "reference_id": uuid.uuid4().hex,
                     "version": max((r["version"] for r in refs), default=0) + 1,
                     "label": str(options.get("label", "Эталон")), "language": language,
                     "created_at": _now(), "project_revision": project["revision"],
                     "source": copy.deepcopy(project["source"]), "project": copy.deepcopy(project),
                     "coverage": review_coverage(project), "difficult_cases": cases}
        _atomic_write(result_dir / "manual-references" / (reference["reference_id"] + ".json"), reference, exclusive=True)
        return reference


def list_references(result_dir: Path) -> dict:
    references = []
    for path in sorted((result_dir / "manual-references").glob("*.json")):
        reference = _read(path)
        references.append({key: reference[key] for key in ("reference_id", "version", "label", "language", "created_at", "project_revision", "coverage")})
    return {"references": sorted(references, key=lambda r: r["version"], reverse=True)}


def read_reference(result_dir: Path, reference_id: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{32}", reference_id):
        raise ManualError("Эталон не найден", status=404)
    path = result_dir / "manual-references" / (reference_id + ".json")
    if not path.is_file():
        raise ManualError("Эталон не найден", status=404)
    return _read(path)


get_reference = read_reference


def import_reference(result_dir: Path, job_id: str, manifest: dict, payload: dict) -> dict:
    with _locked(result_dir):
        previous = read_project(result_dir, job_id, manifest)
        if payload.get("revision") != previous["revision"]:
            raise ManualError("Проект изменён в другой вкладке", status=409, code="revision_conflict",
                              current_revision=previous["revision"])
        reference = payload.get("reference")
        if not isinstance(reference, dict) or reference.get("schema_version") != SCHEMA_VERSION:
            raise ManualError("Неподдерживаемый файл эталона")
        if not _identity_equal(reference.get("source", {}), previous["source"]):
            raise ManualError("Эталон относится к другим исходным аудио или TXT", status=409, code="source_mismatch")
        project = copy.deepcopy(reference.get("project"))
        if not isinstance(project, dict) or not _identity_equal(project.get("source", {}), reference["source"]):
            raise ManualError("Источники снимка и эталона не совпадают")
        # Import to another local job of the exact same recording is safe, but
        # source paths and current analysis identity remain local to this job.
        for key in ("project_id", "job_id", "source", "source_analysis", "canonical_text"):
            project[key] = copy.deepcopy(previous[key])
        project["revision"] = previous["revision"]
        for review in project.get("reviews", []):
            review["review_id"] = uuid.uuid4().hex
        project = validate_project(project, previous)
        project["imported_reference"] = {"reference_id": reference.get("reference_id"), "version": reference.get("version")}
        # Preserve the supplied immutable reference separately even when its
        # source job no longer exists. Never overwrite an existing local ID.
        reference_id = reference.get("reference_id")
        if isinstance(reference_id, str) and re.fullmatch(r"[0-9a-f]{32}", reference_id):
            reference_path = result_dir / "manual-references" / (reference_id + ".json")
            if not reference_path.exists():
                _atomic_write(reference_path, reference, exclusive=True)
        _atomic_write(result_dir / "manual-lyrics.json", project)
        return project
