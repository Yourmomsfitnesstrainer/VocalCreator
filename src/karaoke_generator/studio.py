from __future__ import annotations

import json
import resource
import shutil
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .alignment import MAPPING_ALGORITHM_VERSION, align_lyrics
from .audio import prepare_audio, probe_duration, timeline_report
from .lyrics import cleanup_report, parse_lyrics_file, text_sha256
from .melody import (
    NOTE_SEGMENTATION_VERSION,
    WORD_NOTE_MAPPING_VERSION,
    link_words_to_notes,
    segment_notes,
)
from .models import AlignmentResult
from .piano import PIANO_SYNTH_VERSION, render_piano
from .pitch import cached_pitch
from .separation import SeparationResult, create_separator
from .studio_models import MelodyResult, NoteEvent, WordNoteLink
from .timing_cache import cached_timing, file_sha256, fingerprint, package_versions, write_json


STUDIO_SCHEMA_VERSION = 1
StudioProgressCallback = Callable[[str, str], None]


def _prepare_syllable_score(output_dir: Path, job_id: str, manifest: dict) -> dict:
    # Called only by an explicit analysis; reading a saved job never runs models.
    from .syllable_score_storage import prepare_score
    return prepare_score(output_dir, job_id, manifest)


def run_studio(
    audio: Path,
    lyrics: Path,
    output_dir: Path,
    config: dict[str, Any],
    *,
    input_type: str = "mix",
    job_id: str | None = None,
    progress_callback: StudioProgressCallback | None = None,
) -> dict[str, Any]:
    if input_type not in {"mix", "vocal"}:
        raise ValueError("Studio input type must be 'mix' or 'vocal'")
    audio = audio.expanduser().resolve()
    lyrics = lyrics.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    if not audio.is_file():
        raise FileNotFoundError(f"Audio file not found: {audio}")
    if not lyrics.is_file():
        raise FileNotFoundError(f"Lyrics file not found: {lyrics}")
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir = output_dir / "work"
    cache_dir = work_dir / "studio-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "studio.json"
    previous = _load_previous(manifest_path)
    started = time.monotonic()
    manifest = _new_manifest(job_id or uuid.uuid4().hex, audio, lyrics, input_type)
    manifest["cache_keys"] = dict(previous.get("cache_keys") or {})
    if previous.get("id") == manifest["id"]:
        manifest["created_at"] = previous.get("created_at", manifest["created_at"])
    _persist_manifest(manifest_path, manifest)
    stage_started_at: dict[str, float] = {}

    def stage(name: str, label: str, status: str, **details: Any) -> None:
        if status == "running":
            stage_started_at[name] = time.monotonic()
        elif name in stage_started_at:
            details.setdefault(
                "processing_seconds", round(time.monotonic() - stage_started_at[name], 6)
            )
        manifest["current_stage"] = name
        manifest["stage_label"] = label
        manifest["updated_at"] = _utc_now()
        manifest["stages"].setdefault(name, {}).update(status=status, **details)
        _persist_manifest(manifest_path, manifest)
        if progress_callback:
            progress_callback(label, details.get("detail", ""))

    def fail(name: str, label: str, exc: Exception, *, fatal: bool) -> None:
        message = str(exc) or exc.__class__.__name__
        manifest["errors"].append({"stage": name, "message": message})
        stage(name, label, "failed", detail=message)
        manifest["status"] = "failed" if fatal else "partial"
        manifest["updated_at"] = _utc_now()
        manifest["elapsed_seconds"] = round(time.monotonic() - started, 6)
        _persist_manifest(manifest_path, manifest)

    manifest["status"] = "running"
    try:
        stage("preparation", "Подготовка аудио и текста", "running")
        document = parse_lyrics_file(lyrics)
        original_audio = output_dir / f"original{audio.suffix.lower() or '.audio'}"
        original_lyrics = output_dir / "lyrics.txt"
        _copy_if_different(audio, original_audio)
        _copy_if_different(lyrics, original_lyrics)
        processed_lyrics = output_dir / "processed_lyrics.txt"
        processed_lyrics.write_text(document.processed_text, encoding="utf-8")
        cleanup_path = output_dir / "lyrics_cleanup.json"
        write_json(cleanup_path, cleanup_report(document))
        source = work_dir / "source.wav"
        source_key = fingerprint(
            {"audio_sha256": file_sha256(original_audio), "preparation_algorithm": "2"}
        )
        if previous.get("cache_keys", {}).get("source") != source_key or not source.exists():
            prepare_audio(original_audio, source)
        duration = probe_duration(source)
        manifest["timeline"] = {
            "duration": duration,
            "timebase": "decoded-original-seconds",
            "source_offset_sec": 0.0,
        }
        manifest["input"].update(
            audio_sha256=file_sha256(original_audio),
            lyrics_sha256=file_sha256(original_lyrics),
        )
        manifest["cache_keys"]["source"] = source_key
        manifest["artifacts"].update(
            {
                "lyrics.txt": "lyrics.txt",
                "processed_lyrics.txt": "processed_lyrics.txt",
                "lyrics_cleanup.json": "lyrics_cleanup.json",
            }
        )
        stage("preparation", "Подготовка аудио и текста", "complete", duration=duration)
    except Exception as exc:
        fail("preparation", "Подготовка аудио и текста", exc, fatal=True)
        return manifest

    try:
        stage("separation", "Выделение вокала и минуса", "running")
        if input_type == "vocal":
            vocals = output_dir / "vocals.wav"
            shutil.copy2(source, vocals)
            separation = SeparationResult(
                vocals,
                None,
                "provided-vocal",
                {"backend": "provided-vocal", "audio_sha256": file_sha256(vocals)},
            )
            manifest["stages"]["instrumental"] = {
                "status": "unavailable",
                "detail": "Сопровождение не загружено для готового вокала",
            }
        else:
            separation_config = dict(config.get("separation") or {})
            separation_key = fingerprint(
                {
                    "source_sha256": file_sha256(source),
                    "config": separation_config,
                    "runtime": package_versions().get("packages", {}),
                }
            )
            cached_vocals = output_dir / "vocals.wav"
            cached_instrumental = output_dir / "instrumental.wav"
            if (
                previous.get("cache_keys", {}).get("separation") == separation_key
                and cached_vocals.exists()
                and cached_instrumental.exists()
            ):
                old = previous.get("separation", {})
                separation = SeparationResult(
                    cached_vocals,
                    cached_instrumental,
                    str(old.get("backend", "cached")),
                    dict(old.get("provenance") or {}),
                )
            else:
                separator = create_separator(separation_config)
                if not separator.available():
                    backend = separation_config.get("backend", "demucs")
                    raise RuntimeError(f"Separator '{backend}' is unavailable; install its optional dependency")
                separation = separator.separate(source, output_dir)
            manifest["cache_keys"]["separation"] = separation_key
        vocals = separation.vocals
        instrumental = separation.instrumental
        audio_timeline = timeline_report(original_audio, source, vocals, instrumental)
        manifest["separation"] = {
            "status": "provided_vocal" if input_type == "vocal" else "separated",
            "backend": separation.backend,
            "provenance": separation.provenance or {},
            "timeline": audio_timeline,
        }
        manifest["artifacts"]["vocals.wav"] = "vocals.wav"
        if instrumental is not None:
            manifest["artifacts"]["instrumental.wav"] = "instrumental.wav"
        stage("separation", "Выделение вокала и минуса", "complete")
    except Exception as exc:
        fail("separation", "Выделение вокала и минуса", exc, fatal=True)
        return manifest

    alignment: AlignmentResult | None = None
    try:
        stage("alignment", "Привязка точного текста", "running")
        alignment_config = dict(config.get("alignment") or {})
        timed_words, detected_language, timing_details = cached_timing(
            vocals,
            document,
            str(alignment_config.get("language", "auto")),
            duration,
            alignment_config,
            work_dir,
        )
        alignment_key = fingerprint(
            {
                "timing_key": timing_details["timing_key"],
                "mapping_algorithm": MAPPING_ALGORITHM_VERSION,
                "text_sha256": text_sha256(document),
                "min_similarity": alignment_config.get("min_similarity", 0.62),
            }
        )
        alignment_path = output_dir / "alignment.json"
        if alignment_path.exists() and manifest["cache_keys"].get("alignment") == alignment_key:
            alignment = AlignmentResult.from_dict(json.loads(alignment_path.read_text(encoding="utf-8")))
        else:
            alignment = align_lyrics(
                document,
                timed_words,
                duration,
                detected_language,
                str(alignment_config.get("backend", "whisperx")),
                float(alignment_config.get("min_similarity", 0.62)),
            )
        alignment.diagnostics.update(timing_details, audio_timeline=audio_timeline)
        write_json(alignment_path, alignment.to_dict())
        manifest["cache_keys"]["alignment"] = alignment_key
        manifest["alignment"] = {
            "status": "available",
            "backend": alignment.backend,
            "quality": alignment.to_dict()["quality"],
        }
        manifest["artifacts"]["alignment.json"] = "alignment.json"
        stage("alignment", "Привязка точного текста", "complete")
    except Exception as exc:
        manifest["alignment"] = {"status": "unavailable"}
        fail("alignment", "Привязка точного текста", exc, fatal=False)

    try:
        stage("pitch", "Анализ высоты голоса", "running")
        pitch_options = dict(config.get("pitch") or {})
        frames, pitch_diagnostics, pitch_key = cached_pitch(vocals, pitch_options, cache_dir)
        manifest["cache_keys"]["pitch"] = pitch_key
        stage(
            "pitch",
            "Анализ высоты голоса",
            "complete",
            backend=pitch_diagnostics.get("backend"),
            voiced_frames=pitch_diagnostics.get("voiced_frame_count", 0),
        )
    except Exception as exc:
        fail("pitch", "Анализ высоты голоса", exc, fatal=False)
        return _finish(manifest_path, manifest, started)

    try:
        stage("melody", "Сегментация нот и связь со словами", "running")
        note_options = dict(config.get("notes") or {})
        hop_seconds = float(pitch_diagnostics["hop_seconds"])
        notes_key = fingerprint(
            {
                "pitch_key": pitch_key,
                "algorithm": NOTE_SEGMENTATION_VERSION,
                "options": note_options,
            }
        )
        notes_path = cache_dir / f"notes-{notes_key}.json"
        if notes_path.exists():
            notes = [NoteEvent(**raw) for raw in json.loads(notes_path.read_text(encoding="utf-8"))["notes"]]
            notes_cache_hit = True
        else:
            notes = segment_notes(
                frames,
                duration=duration,
                source=str(pitch_diagnostics.get("backend", "unknown")),
                hop_seconds=hop_seconds,
                min_note_seconds=float(note_options.get("min_note_seconds", 0.08)),
                max_gap_seconds=float(note_options.get("max_gap_seconds", 0.055)),
            )
            write_json(notes_path, {"notes": [note.__dict__ for note in notes]})
            notes_cache_hit = False
        alignment_hash = file_sha256(output_dir / "alignment.json") if alignment is not None else "unavailable"
        mapping_key = fingerprint(
            {
                "notes_key": notes_key,
                "alignment_sha256": alignment_hash,
                "algorithm": WORD_NOTE_MAPPING_VERSION,
            }
        )
        mapping_path = cache_dir / f"mapping-{mapping_key}.json"
        if mapping_path.exists():
            links = [WordNoteLink(**raw) for raw in json.loads(mapping_path.read_text(encoding="utf-8"))["links"]]
            mapping_cache_hit = True
        else:
            links = link_words_to_notes(alignment, notes)
            write_json(mapping_path, {"links": [link.__dict__ for link in links]})
            mapping_cache_hit = False
        melody = MelodyResult(
            timeline=manifest["timeline"],
            pitch_frames=frames,
            notes=notes,
            word_note_links=links,
            diagnostics={
                "pitch": pitch_diagnostics,
                "notes": {
                    "algorithm_version": NOTE_SEGMENTATION_VERSION,
                    "count": len(notes),
                    "cache_hit": notes_cache_hit,
                    "octave_corrections": [],
                },
                "word_note_mapping": {
                    "algorithm_version": WORD_NOTE_MAPPING_VERSION,
                    "link_count": len(links),
                    "cache_hit": mapping_cache_hit,
                    "status": "available" if alignment is not None else "unavailable",
                },
            },
            provenance={
                "input_type": input_type,
                "pitch_source": "vocal-stem",
                "separation": manifest["separation"],
            },
        )
        melody_path = output_dir / "melody.json"
        write_json(melody_path, melody.to_dict())
        manifest["cache_keys"].update(notes=notes_key, mapping=mapping_key)
        manifest["melody"] = {
            "status": "available" if notes else "empty",
            "note_count": len(notes),
            "pitch_frame_count": len(frames),
            "word_note_link_count": len(links),
        }
        manifest["artifacts"]["melody.json"] = "melody.json"
        stage("melody", "Сегментация нот и связь со словами", "complete" if notes else "empty")
    except Exception as exc:
        fail("melody", "Сегментация нот и связь со словами", exc, fatal=False)
        return _finish(manifest_path, manifest, started)

    try:
        stage("piano", "Синтез партии пианино", "running")
        piano_options = dict(config.get("piano") or {})
        piano_key = fingerprint(
            {
                "notes_key": notes_key,
                "algorithm": PIANO_SYNTH_VERSION,
                "options": piano_options,
                "duration": duration,
            }
        )
        piano_path = output_dir / "piano.wav"
        if manifest["cache_keys"].get("piano") == piano_key and piano_path.exists():
            piano_diagnostics = {"cache_hit": True}
        else:
            piano_diagnostics = render_piano(
                notes, piano_path, duration=duration, options=piano_options
            )
            piano_diagnostics["cache_hit"] = False
        manifest["cache_keys"]["piano"] = piano_key
        manifest["piano"] = piano_diagnostics
        manifest["artifacts"]["piano.wav"] = "piano.wav"
        stage("piano", "Синтез партии пианино", "complete")
    except Exception as exc:
        fail("piano", "Синтез партии пианино", exc, fatal=False)

    try:
        stage("syllables", "Подготовка слоговой партии", "running")
        score = _prepare_syllable_score(output_dir, manifest["id"], manifest)
        if score["status"] not in {"ready", "partial"}:
            raise RuntimeError(f"Слоговая партия не подготовлена: {score.get('error') or score['status']}")
        manifest["syllables"] = {"status": score["status"],
                                  "base_analysis_key": score.get("base_analysis_key"),
                                  "revision": score.get("revision", 0)}
        stage("syllables", "Подготовка слоговой партии", "complete")
    except Exception as exc:
        fail("syllables", "Слоговая партия требует повторной подготовки", exc, fatal=False)

    return _finish(manifest_path, manifest, started)


def _new_manifest(job_id: str, audio: Path, lyrics: Path, input_type: str) -> dict[str, Any]:
    now = _utc_now()
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
            "audio_name": audio.name,
            "lyrics_name": lyrics.name,
        },
        "timeline": {},
        "stages": {},
        "cache_keys": {},
        "artifacts": {},
        "learning": {"schema_version": 1, "preparation": "on-demand", "modes": ["light", "medium", "pro"]},
        "errors": [],
        "runtime": package_versions(),
    }


def _finish(path: Path, manifest: dict[str, Any], started: float) -> dict[str, Any]:
    if manifest.get("status") != "failed":
        incomplete = bool(manifest.get("errors")) or manifest.get("melody", {}).get("status") != "available"
        manifest["status"] = "partial" if incomplete else "complete"
    manifest["current_stage"] = "finished"
    manifest["stage_label"] = "Результат готов" if manifest["status"] == "complete" else "Доступен частичный результат"
    manifest["updated_at"] = _utc_now()
    manifest["elapsed_seconds"] = round(time.monotonic() - started, 6)
    _persist_manifest(path, manifest)
    return manifest


def _persist_manifest(path: Path, manifest: dict[str, Any]) -> None:
    manifest["artifacts"]["studio.json"] = "studio.json"
    manifest.setdefault("runtime", {})["peak_rss_bytes"] = _peak_rss_bytes()
    write_json(path, manifest)


def _load_previous(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _copy_if_different(source: Path, destination: Path) -> None:
    if source == destination:
        return
    if destination.exists() and file_sha256(source) == file_sha256(destination):
        return
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    shutil.copy2(source, temporary)
    temporary.replace(destination)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _peak_rss_bytes() -> int:
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return value if sys.platform == "darwin" else value * 1024
