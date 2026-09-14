"""Explicit, local proposals for the editable lyric layer.

This module deliberately does not import the editor, the melody builder or ML
models. Lexical repetitions use the source-verified ASR cache; timbral roles are
computed from the actual vocal PCM. Both are proposals, never human validation.
Everything needed to replay the stage lives here (stdlib + numpy).
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from difflib import SequenceMatcher
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import tempfile
import threading
import unicodedata
import uuid
import wave

ALGORITHM_VERSION = "manual-auto-1"
DEFAULT_PARAMETERS = {
    "repeat_lexical_similarity": .82,
    "repeat_same_interval_seconds": .24,
    "repeat_min_seconds": .06,
    "role_max_clusters": 4,
    "role_min_group_size": 3,
    "role_cluster_margin": .12,
    "role_min_silhouette": .20,
    "role_window_seconds": 2.0,
    "role_hop_seconds": 1.0,
    "audio_min_rms": .00015,
    "timing_offset_seconds": 0.,
}
_COLORS = ["#c6f36b", "#b6a0f5", "#f4b66f", "#78cddd", "#ef96b4", "#c6c4bd"]
_LOCK = threading.Lock()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode()).hexdigest()


def _read(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Ожидался объект JSON: {path.name}")
    return data


def _write_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=".writing-", delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _key(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKC", text).casefold()
                   if unicodedata.category(c)[0] in {"L", "N"})


def _number(value) -> float | None:
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (ValueError, TypeError):
        return None


def _interval(item: dict, duration: float) -> tuple[float, float] | None:
    start, end = _number(item.get("start")), _number(item.get("end"))
    if start is None or end is None or not 0 <= start < end <= duration + .001:
        return None
    start, end = round(start, 3), min(round(end, 3), duration)
    return (start, end) if start < end else None


def normalize_parameters(profile: dict | None = None) -> dict:
    parameters = (profile or {}).get("parameters", profile or {})
    if not isinstance(parameters, dict):
        raise ValueError("Параметры профиля должны быть объектом")
    unknown = set(parameters) - set(DEFAULT_PARAMETERS)
    if unknown:
        raise ValueError("Неизвестные параметры анализа: " + ", ".join(sorted(unknown)))
    result = {**DEFAULT_PARAMETERS, **parameters}
    for name, value in result.items():
        if isinstance(value, bool) or _number(value) is None:
            raise ValueError(f"Некорректный параметр {name}")
        result[name] = float(value)
    for name in ["repeat_lexical_similarity", "role_cluster_margin", "role_min_silhouette"]:
        if not 0 <= result[name] <= 1:
            raise ValueError(f"Параметр {name} должен быть от 0 до 1")
    for name in ["role_max_clusters", "role_min_group_size"]:
        if result[name] != int(result[name]) or not 1 <= result[name] <= 12:
            raise ValueError(f"Некорректный параметр {name}")
        result[name] = int(result[name])
    for name in ["role_window_seconds", "role_hop_seconds"]:
        if not .2 <= result[name] <= 10:
            raise ValueError(f"Параметр {name} должен быть от 0.2 до 10 секунд")
    for name in ["repeat_min_seconds", "repeat_same_interval_seconds", "audio_min_rms"]:
        if not 0 <= result[name] <= 2:
            raise ValueError(f"Некорректный параметр {name}")
    if abs(result["timing_offset_seconds"]) > 5:
        raise ValueError("Сдвиг профиля ограничен пятью секундами")
    return result


def algorithm_identity(profile: dict | None = None) -> dict:
    import numpy as np
    return {"version": ALGORITHM_VERSION, "code_sha256": _hash_file(Path(__file__)),
            "parameters": normalize_parameters(profile), "numpy": np.__version__,
            "python": f"{sys.version_info.major}.{sys.version_info.minor}",
            "models": {"roles": "local-logmel-cepstra-kmeans-v1",
                       "repeats": "source-verified-existing-asr"}}


def _inputs(result_dir: Path) -> dict:
    paths = [result_dir / name for name in ("lyrics.txt", "alignment.json", "vocals.wav")]
    # The available upstream ASR set is an input. Newly added or modified cache
    # files invalidate this stage; rejected evidence is reported, never hidden.
    paths += sorted((result_dir / "work/timing-cache").glob("asr-*.json"))
    paths += sorted((result_dir / "work/timing-cache").glob("refined-*.json"))
    return {str(path.relative_to(result_dir)): _hash_file(path)
            for path in paths if path.is_file()}


def _source(result_dir: Path) -> dict:
    inputs = result_dir.parent / "input"
    audio = sorted(inputs.glob("audio.*")) or sorted(result_dir.glob("original.*"))
    lyric = inputs / "lyrics.txt"
    if not lyric.is_file():
        lyric = result_dir / "lyrics.txt"
    return {"audio_sha256": _hash_file(audio[0]) if audio else None,
            "lyrics_sha256": _hash_file(lyric),
            "vocals_sha256": _hash_file(result_dir / "vocals.wav")
            if (result_dir / "vocals.wav").is_file() else None}


def _canonical(text: str, alignment: dict, duration: float) -> list[dict]:
    words, previous_end, line, index = [], 0, 0, 0
    for match in re.finditer(r"\S+", text):
        newlines = text[previous_end:match.start()].count("\n")
        if newlines:
            line += newlines
            index = 0
        value = match.group()
        digest = hashlib.sha256(value.encode()).hexdigest()[:10]
        words.append({"id": f"v3-l{line:04d}-w{index:04d}-{digest}", "text": value,
                      "source_token_index": len(words), "line_index": line,
                      "char_start": match.start(), "char_end": match.end(),
                      "start": None, "end": None, "timing_source": "unplaced"})
        index += 1
        previous_end = match.end()
    aligned = [word for line in alignment.get("lines", []) for word in line.get("words", [])]
    matching = SequenceMatcher(a=[_key(w["text"]) for w in words],
                               b=[_key(w.get("text", "")) for w in aligned], autojunk=False)
    for tag, a0, a1, b0, b1 in matching.get_opcodes():
        # A same-size substitution may be ASR spelling; retain TXT and treat its
        # existing placement as approximate. No ASR spelling enters the layer.
        if tag != "equal" and not (tag == "replace" and a1 - a0 == b1 - b0):
            continue
        for entry, original in zip(words[a0:a1], aligned[b0:b1]):
            interval = _interval(original, duration)
            if not _key(entry["text"]) or interval is None:
                continue
            # Original manual JSON bounds can seed the editable project, but an
            # automatic run must not mistake a human edit for model output.
            timing = original.get("timing") or {}
            generated = timing.get("generated")
            if timing.get("source") == "manual" or (generated and
                    (original.get("start"), original.get("end")) !=
                    (generated.get("start"), generated.get("end"))):
                interval = _interval(generated or {}, duration)
                if interval is None:
                    continue
            entry.update(start=interval[0], end=interval[1], timing_source="saved-alignment")
    return words


def _prompt_text_hash(text: str) -> str:
    """Reproduce the existing lyric-prompt cleanup before checking ASR identity.

    This is intentionally versioned with this executable. Raw TXT bytes remain
    the project identity; prompt whitespace/section cleanup is not a new TXT.
    """
    lines, pending_break, skip_promo = [], False, False
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff").split("\n"):
        line = " ".join(raw.split())
        if not line:
            pending_break = bool(lines)
            continue
        if re.fullmatch(r"\[[^\[\]\n]{1,100}\]", line):
            skip_promo = False
            pending_break = bool(lines)
            continue
        if line.casefold() in {"you might also like", "you may also like", "вам также может понравиться"}:
            skip_promo = True
        if skip_promo or any(re.fullmatch(pattern, line, re.IGNORECASE) for pattern in
                             [r"\d*\s*embed", r"translations?(?:\s+\d+)?", r"contributors?(?:\s+\d+)?"]):
            continue
        if pending_break:
            lines.append("")
            pending_break = False
        lines.append(line)
    return hashlib.sha256(("\n".join(lines).strip() + "\n").encode()).hexdigest()


def _asr_evidence(result_dir: Path, inputs: dict, duration: float) -> tuple[list[dict], dict]:
    text_hash = inputs.get("lyrics.txt")
    prompt_hash = _prompt_text_hash((result_dir / "lyrics.txt").read_text(encoding="utf-8"))
    vocal_hash = inputs.get("vocals.wav")
    sources, rejected, candidates = [], [], []
    directory = result_dir / "work/timing-cache"
    for path in sorted(directory.glob("asr-*.json")):
        try:
            data = _read(path)
            spec = data.get("spec") or {}
            expected = path.stem.removeprefix("asr-")
            # Existing cache fingerprint uses the default JSON ASCII encoding.
            digest = hashlib.sha256(json.dumps(spec, sort_keys=True, allow_nan=False).encode()).hexdigest()
            if expected != digest:
                raise ValueError("Повреждён ключ ASR")
            if not vocal_hash or spec.get("audio_sha256") != vocal_hash:
                raise ValueError("ASR относится к другой вокальной дорожке")
            if spec.get("text_sha256") not in (None, text_hash, prompt_hash):
                raise ValueError("ASR использовал другой TXT")
            if spec.get("backend") == "uniform":
                raise ValueError("Равномерная расстановка не является свидетельством повтора")
            if abs(float(spec.get("duration", duration)) - duration) > .1:
                raise ValueError("ASR относится к другой длительности")
            raw_words = data.get("words", [])
            # Prefer a linked refined cache if it preserves the lexical stream.
            # Refinement cannot create lexical occurrences on its own.
            refinement = None
            for refined_path in sorted(directory.glob("refined-*.json")):
                refined = _read(refined_path)
                ref_spec = refined.get("spec") or {}
                ref_key = hashlib.sha256(json.dumps(ref_spec, sort_keys=True, allow_nan=False).encode()).hexdigest()
                if (ref_spec.get("asr_key") == expected and refined_path.stem == f"refined-{ref_key}"
                        and len(refined.get("words", [])) == len(raw_words)
                        and [_key(w.get("text", "")) for w in refined["words"]] ==
                        [_key(w.get("text", "")) for w in raw_words]):
                    refinement = refined_path.name
                    raw_words = [{**raw, **ref} if _interval(ref, duration) else raw
                                 for raw, ref in zip(raw_words, refined["words"])]
                    break
            source = {"file": str(path.relative_to(result_dir)), "sha256": inputs[str(path.relative_to(result_dir))],
                      "model": spec.get("model"), "refinement": refinement,
                      "audio_sha256": vocal_hash, "text_sha256": spec.get("text_sha256")}
            sources.append(source)
            for number, word in enumerate(raw_words):
                if isinstance(word, dict) and _interval(word, duration) and _key(word.get("text", "")):
                    candidates.append({**word, "evidence_source": path.name, "evidence_index": number})
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            rejected.append({"file": path.name, "reason": str(exc)})
    # Multiple valid cached ASR runs must not multiply the same acoustic word.
    unique = []
    for word in sorted(candidates, key=lambda w: (w["start"], w["end"], w["evidence_source"])):
        if any(_key(old["text"]) == _key(word["text"]) and
               abs(old["start"] - word["start"]) < .12 and abs(old["end"] - word["end"]) < .18
               for old in unique[-20:]):
            continue
        unique.append(word)
    return unique, {"status": "available" if sources else "unavailable", "sources": sources,
                    "rejected": rejected, "recognized_words": len(unique),
                    "inference_performed": False, "cache_provenance_verified": bool(sources)}


def _read_vocal(path: Path):
    import numpy as np
    with wave.open(str(path), "rb") as stream:
        channels, width, rate, frames = (stream.getnchannels(), stream.getsampwidth(),
                                         stream.getframerate(), stream.getnframes())
        raw = stream.readframes(frames)
    if width == 1:
        samples = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128) / 128
    elif width in (2, 4):
        samples = np.frombuffer(raw, dtype=f"<i{width}").astype(np.float32) / (2 ** (width * 8 - 1))
    elif width == 3:
        octets = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        packed = octets[:, 0] | (octets[:, 1] << 8) | (octets[:, 2] << 16)
        samples = ((packed ^ 0x800000) - 0x800000).astype(np.float32) / 8388608
    else:
        raise ValueError("Неподдерживаемая PCM-разрядность вокала")
    samples = samples.reshape(-1, channels).mean(axis=1)
    duration = frames / rate
    if rate != 16000:
        # Deterministic resampling for descriptors only; original audio untouched.
        # A small Hann low-pass avoids aliasing when reducing the sampling rate.
        if rate > 16000:
            taps = 63
            grid = np.arange(taps) - (taps - 1) / 2
            cutoff = 7200 / rate
            kernel = 2 * cutoff * np.sinc(2 * cutoff * grid) * np.hanning(taps)
            samples = np.convolve(samples, kernel / kernel.sum(), mode="same")
        samples = np.interp(np.arange(round(duration * 16000)) * rate / 16000,
                            np.arange(len(samples)), samples).astype(np.float32)
    return samples, 16000, {"source_sample_rate": rate, "source_channels": channels,
                           "source_frames": frames, "duration": duration,
                           "decoded_samples": len(samples)}


def _audio_features(samples, rate: int, parameters: dict):
    """Gain-normalized mel cepstra; no F0, note MIDI or identity/gender input."""
    import numpy as np
    size, hop, fft_size, bands = 400, 160, 512, 32
    if len(samples) < size:
        return None
    frames = np.lib.stride_tricks.sliding_window_view(samples, size)[::hop]
    energy = np.sqrt(np.mean(frames * frames, axis=1))
    frequencies = np.fft.rfftfreq(fft_size, 1 / rate)
    mel = lambda hz: 2595 * np.log10(1 + hz / 700)
    edges = 700 * (10 ** (np.linspace(mel(80), mel(7200), bands + 2) / 2595) - 1)
    filters = np.array([np.maximum(0, np.minimum((frequencies - edges[i]) / (edges[i + 1] - edges[i]),
                                               (edges[i + 2] - frequencies) / (edges[i + 2] - edges[i + 1])))
                        for i in range(bands)])
    power = np.abs(np.fft.rfft(frames * np.hanning(size), n=fft_size, axis=1)) ** 2
    logmel = np.log(np.maximum(power @ filters.T, 1e-12))
    logmel -= logmel.mean(axis=1, keepdims=True)
    # Ignore c0 (level) and keep spectral-envelope coefficients, including their
    # within-window spread. Vowels/production can still confuse this heuristic.
    dct = np.cos(np.pi / bands * (np.arange(bands)[:, None] + .5) * np.arange(1, 13))
    cepstra = logmel @ dct / math.sqrt(bands)
    times = (np.arange(len(frames)) * hop + size / 2) / rate
    gate = max(parameters["audio_min_rms"], float(np.percentile(energy, 80)) * .035)
    active = energy > gate
    windows, vectors = [], []
    width, step = parameters["role_window_seconds"], parameters["role_hop_seconds"]
    for start in np.arange(0, len(samples) / rate, step):
        mask = (times >= start) & (times < start + width) & active
        if mask.sum() < min(12, width * 100 * .4):
            continue
        selected = cepstra[mask]
        vectors.append(np.concatenate([np.median(selected, axis=0), np.std(selected, axis=0) * .35]))
        windows.append({"start": round(float(start), 3), "end": round(min(float(start + width), len(samples) / rate), 3),
                        "active_frames": int(mask.sum())})
    return {"vectors": np.asarray(vectors), "windows": windows, "frame_times": times,
            "energy": energy, "active": active, "gate": gate,
            "frame_count": len(frames), "active_frames": int(active.sum())}


def _cluster_roles(features: dict | None, annotations: list[dict], parameters: dict) -> tuple[list, dict]:
    import numpy as np
    limitation = ("Тембровые группы — приблизительные роли: тембр зависит также от фонем, эффектов и сведения. "
                  "Наложенные и похожие голоса могут остаться без роли; отдельные певцы по дорожкам не выделяются.")
    diagnostics = {"method": "gain-normalized-logmel-cepstra-kmeans", "uses_pitch": False,
                   "approximate": True, "score_policy": "uncalibrated", "message": limitation,
                   "audio_processed": features is not None, "clusters_tested": [], "windows": []}
    if features is None or len(features["windows"]) < parameters["role_min_group_size"] * 2:
        diagnostics.update(status="unassigned", reason="Недостаточно активного вокала для сравнения тембров")
        return [], diagnostics
    raw = features["vectors"]
    scale = np.maximum(np.std(raw, axis=0), .5)
    vectors = np.clip((raw - np.median(raw, axis=0)) / scale, -3, 3)
    # A window spanning a voice transition has high variance. Variance is an
    # uncertainty hint, not a separate singer; keep its clustering weight low.
    vectors[:, 12:] *= .15
    distances = np.sqrt(np.maximum(((vectors[:, None] - vectors[None, :]) ** 2).mean(axis=2), 0))
    count = len(vectors)
    best = None
    for k in range(2, min(parameters["role_max_clusters"], count // parameters["role_min_group_size"]) + 1):
        chosen = [int(np.argmin(np.linalg.norm(vectors - vectors.mean(axis=0), axis=1)))]
        while len(chosen) < k:
            # A single effect/transient must not monopolize one cluster center.
            # Seed from the distant population, below the extreme outlier tail.
            candidates = [i for i in np.argsort(distances[:, chosen].min(axis=1)) if i not in chosen]
            chosen.append(int(candidates[int((len(candidates) - 1) * .9)]))
        centers = vectors[chosen].copy()
        for _ in range(50):
            squared = ((vectors[:, None] - centers[None, :]) ** 2).mean(axis=2)
            labels = squared.argmin(axis=1)
            if any(np.sum(labels == j) == 0 for j in range(k)):
                break
            updated = np.array([vectors[labels == j].mean(axis=0) for j in range(k)])
            if np.allclose(updated, centers, atol=1e-6):
                break
            centers = updated
        sizes = [int(np.sum(labels == j)) for j in range(k)]
        if min(sizes) < parameters["role_min_group_size"]:
            diagnostics["clusters_tested"].append({"k": k, "sizes": sizes, "accepted": False, "reason": "small-group"})
            continue
        silhouettes = []
        for index, label in enumerate(labels):
            own = distances[index, labels == label].sum() / max(1, sizes[label] - 1)
            other = min(distances[index, labels == j].mean() for j in range(k) if j != label)
            silhouettes.append((other - own) / max(float(other), float(own), 1e-9))
        score = float(np.mean(silhouettes))
        quality = score - .025 * (k - 2)
        diagnostics["clusters_tested"].append({"k": k, "sizes": sizes, "silhouette": round(score, 4),
                                                "accepted": score >= parameters["role_min_silhouette"]})
        if score >= parameters["role_min_silhouette"] and (best is None or quality > best[0]):
            best = quality, labels.copy(), centers.copy(), score
    diagnostics.update(frame_count=features["frame_count"], active_frames=features["active_frames"],
                       feature_windows=count, feature_dimensions=vectors.shape[1],
                       audio_gate_rms=round(features["gate"], 7),
                       feature_sha256=hashlib.sha256(raw.astype("<f4").tobytes()).hexdigest())
    if best is None:
        diagnostics.update(status="unassigned", reason="Тембровые группы недостаточно различимы")
        return [], diagnostics
    _, labels, centers, score = best
    # Stable display numbering by first appearance; no gender/singer inference.
    order = sorted(range(len(centers)), key=lambda j: int(np.flatnonzero(labels == j)[0]))
    mapping = {label: f"auto-role-{index + 1}" for index, label in enumerate(order)}
    roles = [{"id": mapping[label], "name": f"Роль {i + 1}", "color": _COLORS[i % len(_COLORS)],
              "approximate": True, "provenance": {"kind": "automatic", "method": diagnostics["method"]}}
             for i, label in enumerate(order)]
    squared = ((vectors[:, None] - centers[None, :]) ** 2).mean(axis=2)
    ranked = np.sort(squared, axis=1)
    margins = (ranked[:, 1] - ranked[:, 0]) / np.maximum(ranked[:, 1], 1e-9)
    windows = [{**window, "role_id": mapping[int(label)] if margin >= parameters["role_cluster_margin"] else None,
                "margin": round(float(margin), 4)}
               for window, label, margin in zip(features["windows"], labels, margins)]
    for annotation in annotations:
        if annotation.get("start") is None:
            continue
        center = (annotation["start"] + annotation["end"]) / 2
        nearby = [(max(0, min(annotation["end"], window["end"]) - max(annotation["start"], window["start"])),
                   window) for window in windows if window["start"] <= center <= window["end"]]
        votes = Counter()
        for overlap, window in nearby:
            votes[window["role_id"]] += overlap
        ranked_votes = votes.most_common()
        if ranked_votes and ranked_votes[0][0] is not None:
            first = ranked_votes[0][1]
            second = ranked_votes[1][1] if len(ranked_votes) > 1 else 0
            if (first - second) / max(first, 1e-9) >= parameters["role_cluster_margin"]:
                annotation["role_id"] = ranked_votes[0][0]
        annotation["provenance"]["role"] = {"method": diagnostics["method"], "approximate": True,
                                             "status": "proposed" if annotation["role_id"] else "ambiguous"}
    diagnostics.update(status="proposed", proposed_roles=len(roles), silhouette=round(score, 4), windows=windows,
                       assigned_annotations=sum(a.get("role_id") is not None for a in annotations))
    return roles, diagnostics


def _has_audio(interval: tuple, features: dict | None) -> bool:
    if features is None:
        return False
    mask = (features["frame_times"] >= interval[0]) & (features["frame_times"] <= interval[1])
    return bool((features["active"] & mask).any())


def _annotation(word: dict, start, end, identifier: str, provenance: dict) -> dict:
    return {"annotation_id": f"auto-annotation-{identifier}", "occurrence_id": f"auto-occurrence-{identifier}",
            "unit": "word", "text": word["text"], "start": start, "end": end, "role_id": None,
            "source_word_id": word["id"], "source_part_id": None,
            "source_token_index": word["source_token_index"], "source_text": word["text"],
            "provenance": {"kind": "automatic", **provenance}, "approximate": True}


def build_proposals(text: str, alignment: dict, recognized: list[dict], *, duration: float,
                    features: dict | None, parameters: dict | None = None) -> dict:
    """Pure proposal assembly. ASR must already have passed source verification."""
    parameters = normalize_parameters(parameters)
    words = _canonical(text, alignment, duration)
    annotations = [_annotation(w, w["start"], w["end"], f"source-{i}",
                               {"timing": w["timing_source"], "repeat": False}) for i, w in enumerate(words)]
    keys = [_key(w["text"]) for w in words]
    asr_keys = [_key(w.get("text", "")) for w in recognized]
    repeats, recovered, skipped = 0, 0, Counter()
    matched_existing = set()
    for index, acoustic in enumerate(recognized):
        interval = _interval(acoustic, duration)
        if interval is None or interval[1] - interval[0] < parameters["repeat_min_seconds"]:
            skipped["invalid_interval"] += 1
            continue
        token = asr_keys[index]
        candidates = [i for i, key in enumerate(keys) if token and key == token]
        match_kind = "exact-txt"
        if not candidates and len(token) >= 4:
            scores = [(SequenceMatcher(None, token, key).ratio(), i) for i, key in enumerate(keys) if len(key) >= 4]
            if scores:
                best = max(score for score, _ in scores)
                candidates = [i for score, i in scores if score == best and score >= parameters["repeat_lexical_similarity"]]
                match_kind = "approximate-txt"
        if not candidates:
            skipped["outside_txt"] += 1
            continue
        # Neighboring lexical agreement locates a phrase/chorus in TXT, while
        # single substantial words may propose an echo without phrase context.
        def context_score(i):
            score = 0
            for delta in (-2, -1, 1, 2):
                if not (0 <= i + delta < len(keys) and 0 <= index + delta < len(asr_keys)):
                    continue
                neighbour = recognized[index + delta]
                gap = max(0., neighbour["start"] - acoustic["end"], acoustic["start"] - neighbour["end"])
                if gap > 1.25 * abs(delta):
                    continue
                wanted, heard = keys[i + delta], asr_keys[index + delta]
                if wanted and (wanted == heard or (min(len(wanted), len(heard)) >= 4
                        and SequenceMatcher(None, wanted, heard).ratio() >= parameters["repeat_lexical_similarity"])):
                    score += 1
            return score
        ranked = sorted(candidates, key=lambda i: (-context_score(i),
                        abs((words[i]["start"] if words[i]["start"] is not None else interval[0]) - interval[0]), i))
        selected = ranked[0]
        context = context_score(selected)
        if (len(token) <= 3 or match_kind == "approximate-txt") and context < 1:
            skipped["weak_repeat_context"] += 1
            continue
        existing = [a for a in annotations[:len(words)]
                    if _key(a["text"]) == keys[selected] and a["start"] is not None
                    and a["annotation_id"] not in matched_existing]
        matching_existing = [a for a in existing
                             if abs(a["start"] - interval[0]) <= parameters["repeat_same_interval_seconds"]
                             or (max(0., min(a["end"], interval[1]) - max(a["start"], interval[0])) /
                                 max(.001, min(a["end"] - a["start"], interval[1] - interval[0])) >= .5)]
        if matching_existing:
            closest = min(matching_existing, key=lambda a: abs(a["start"] - interval[0]) + abs(a["end"] - interval[1]))
            # One acoustic occurrence explains at most one base occurrence.
            # A second distinct ASR occurrence may overlap the first (back/echo).
            matched_existing.add(closest["annotation_id"])
            skipped["existing_occurrence"] += 1
            continue
        if not _has_audio(interval, features):
            skipped["no_active_audio"] += 1
            continue
        provenance = {"timing": "asr-audio-proposal", "lexical_match": match_kind,
                      "context_tokens": context, "evidence_source": acoustic.get("evidence_source"),
                      "evidence_index": acoustic.get("evidence_index"), "repeat": True}
        unplaced = next((i for i in ranked if annotations[i]["start"] is None), None)
        if unplaced is not None:
            annotations[unplaced].update(start=interval[0], end=interval[1])
            annotations[unplaced]["provenance"].update(provenance, repeat=False)
            matched_existing.add(annotations[unplaced]["annotation_id"])
            recovered += 1
        else:
            identifier = _fingerprint({"source": words[selected]["id"], "interval": interval,
                                       "evidence": acoustic.get("evidence_source"),
                                       "index": acoustic.get("evidence_index", index)})[:24]
            annotations.append(_annotation(words[selected], *interval, identifier, provenance))
            repeats += 1
    # Missing canonical words between real anchors receive an explicitly rough
    # placement over active audio. Nothing copies all TXT into every vocal span.
    for index, annotation in enumerate(annotations[:len(words)]):
        if annotation["start"] is not None or not keys[index]:
            continue
        left = next((j for j in range(index - 1, -1, -1) if annotations[j]["end"] is not None), None)
        right = next((j for j in range(index + 1, len(words)) if annotations[j]["start"] is not None), None)
        if left is None or right is None:
            continue
        start, end = annotations[left]["end"], annotations[right]["start"]
        remaining = right - index
        if .06 * remaining <= end - start <= 3 * remaining and _has_audio((start, end), features):
            annotation.update(start=round(start, 3), end=round(start + (end - start) / remaining, 3))
            annotation["provenance"]["timing"] = "approximate-between-audio-anchors"
            recovered += 1
    roles, role_diagnostics = _cluster_roles(features, annotations, parameters)
    offset = parameters["timing_offset_seconds"]
    if offset:
        for annotation in annotations:
            if annotation["start"] is not None:
                start, end = annotation["start"] + offset, annotation["end"] + offset
                if 0 <= start < end <= duration:
                    annotation.update(start=round(start, 3), end=min(round(end, 3), duration))
                    annotation["provenance"]["profile_timing_offset_seconds"] = offset
                else:
                    annotation["provenance"]["profile_offset_skipped"] = "recording-boundary"
    occurrences = [{"occurrence_id": a["occurrence_id"], "annotation_ids": [a["annotation_id"]],
                    "complete": True, "source_word_id": a["source_word_id"]} for a in annotations]
    return {"canonical_text": text, "annotations": annotations, "occurrences": occurrences, "roles": roles,
            "unplaced": [a["annotation_id"] for a in annotations if a["start"] is None],
            "source_words": words,
            "diagnostics": {"text": {"source_tokens": len(words), "additional_occurrences": repeats,
                                        "recovered_placements": recovered, "skipped": dict(skipped),
                                        "policy": "TXT spelling; approximate placements; no human verification"},
                            "roles": role_diagnostics}}


def ready_manual_proposal(result_dir: Path, *, profile: dict | None = None,
                          require_current_algorithm: bool = True) -> dict | None:
    """Read-only lookup. Never decodes audio, computes features or writes files."""
    result_dir = Path(result_dir)
    try:
        index = _read(result_dir / "manual-auto/index.json")
        run_id = index.get("run_id", "")
        if not re.fullmatch(r"[a-f0-9]{64}(?:-[a-f0-9]{12})?", run_id):
            return None
        data = _read(result_dir / "manual-auto/runs" / f"{run_id}.json")
        if (data.get("schema_version") != 1 or data.get("run_id") != run_id
                or data.get("inputs") != _inputs(result_dir)
                or data.get("source") != _source(result_dir)
                or _fingerprint(data) != index.get("result_sha256")):
            return None
        saved_code = str((data.get("algorithm") or {}).get("code_sha256", ""))
        if (not re.fullmatch(r"[a-f0-9]{64}", saved_code)
                or _hash_file(result_dir / "manual-auto/algorithms" / f"{saved_code}.py") != saved_code):
            return None
        if require_current_algorithm and data.get("algorithm") != algorithm_identity(profile):
            return None
        return data
    except (OSError, ValueError, TypeError, KeyError, AttributeError, ImportError):
        return None


def propose_manual_annotations(result_dir: Path, *, profile: dict | None = None, force: bool = False) -> dict:
    """Explicit computation; publish a separate immutable automatic run.

    A failure in role extraction returns diagnosable unassigned roles and leaves
    usable text placement. Missing TXT is a hard error. Manual project files are
    neither inputs nor outputs of this function.
    """
    result_dir = Path(result_dir)
    with _LOCK:
        algorithm = algorithm_identity(profile)
        if not force:
            cached = ready_manual_proposal(result_dir, profile=profile)
            if cached is not None:
                return cached
        inputs = _inputs(result_dir)
        source = _source(result_dir)
        text = (result_dir / "lyrics.txt").read_text(encoding="utf-8")
        if not text.strip():
            raise ValueError("Нет исходного TXT текущей песни")
        try:
            alignment = _read(result_dir / "alignment.json")
        except (OSError, ValueError):
            alignment = {}
        features, decode, audio_error = None, {}, None
        duration = _number(alignment.get("duration")) or 0.
        try:
            samples, rate, decode = _read_vocal(result_dir / "vocals.wav")
            duration = decode["duration"]
            features = _audio_features(samples, rate, algorithm["parameters"])
        except (OSError, ValueError, wave.Error, EOFError, ImportError) as exc:
            audio_error = str(exc)
        if duration <= 0:
            raise ValueError("Не удалось определить длительность исходной записи")
        recognized, asr_diagnostics = _asr_evidence(result_dir, inputs, duration)
        result = build_proposals(text, alignment, recognized, duration=duration,
                                 features=features, parameters=algorithm["parameters"])
        result["diagnostics"].update(asr=asr_diagnostics, audio={**decode, "error": audio_error,
                                                               "sha256": inputs.get("vocals.wav")})
        if audio_error:
            result["diagnostics"]["roles"].update(status="unavailable", reason=audio_error)
        if _inputs(result_dir) != inputs or _source(result_dir) != source:
            raise ValueError("Исходники изменились во время анализа; результат не опубликован")
        cache_key = _fingerprint({"inputs": inputs, "source": source, "algorithm": algorithm})
        root = result_dir / "manual-auto"
        run_id = cache_key
        if force or (root / "runs" / f"{run_id}.json").exists():
            run_id += "-" + uuid.uuid4().hex[:12]
        result.update(schema_version=1, run_id=run_id, cache_key=cache_key,
                      created_at=datetime.now(timezone.utc).isoformat(), source=source,
                      inputs=inputs, algorithm=algorithm, duration=duration,
                      language=alignment.get("language", "unknown"), automatic=True,
                      human_verified=False)
        # Save exact executable bytes alongside the result for profile replay.
        code_path = root / "algorithms" / f"{algorithm['code_sha256']}.py"
        code_path.parent.mkdir(parents=True, exist_ok=True)
        if not code_path.exists():
            with code_path.open("xb") as stream:
                stream.write(Path(__file__).read_bytes())
        elif _hash_file(code_path) != algorithm["code_sha256"]:
            raise ValueError("Повреждён сохранённый алгоритм; старый файл сохранён для диагностики")
        _write_atomic(root / "runs" / f"{run_id}.json", result)
        _write_atomic(root / "index.json", {"run_id": run_id, "inputs": inputs, "algorithm": algorithm,
                                             "result_sha256": _fingerprint(result)})
        return result
