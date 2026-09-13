"""Local, content-addressed pitch-preserving playback audio; no ML stages.

All channels of all tracks pass through one offline Rubber Band R3 stretcher.
The shared stretch profile and sample origin survive splitting back into tracks.
Source WAVs are opened read-only. Publication exposes only a complete directory.
"""
from __future__ import annotations

from contextlib import ExitStack
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import wave

TEMPO_RATES = (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0)
TEMPO_VERSION = "rubberband-r3-offline-joint-v3-1"
TEMPO_OPTIONS = {"engine": "R3", "processing": "offline", "pitch_scale": 1.0,
                 "channels": "together", "sample_format": "PCM_16",
                 "origin_seconds": 0, "duration_rounding": "nearest-sample"}
_TRACK_NAMES = frozenset(("vocals", "instrumental", "piano"))
_PREPARE_LOCK = threading.Lock()
_BLOCK_FRAMES = 65536


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _fingerprint(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _wave_info(path: Path) -> dict:
    try:
        with wave.open(str(path), "rb") as audio:
            if audio.getcomptype() != "NONE" or audio.getsampwidth() != 2:
                raise ValueError("Изменение скорости требует сохранённую дорожку WAV PCM16.")
            return {"channels": audio.getnchannels(), "sample_rate": audio.getframerate(),
                    "frames": audio.getnframes()}
    except (OSError, EOFError, wave.Error) as exc:
        raise ValueError(f"Не удалось прочитать аудиодорожку {path.name}: {exc}") from exc


def _rubberband() -> tuple[str, str]:
    candidates = (os.environ.get("KARAOKE_RUBBERBAND"), shutil.which("rubberband"),
                  "/opt/homebrew/bin/rubberband", "/usr/local/bin/rubberband")
    for candidate in dict.fromkeys(path for path in candidates if path):
        if not Path(candidate).is_file():
            continue
        try:
            result = subprocess.run([candidate, "--version"], capture_output=True, text=True,
                                    timeout=10, check=True)
            version = (result.stdout or result.stderr).strip()
            major = int(version.split(".")[0])
        except (OSError, subprocess.SubprocessError, ValueError):
            continue
        if major >= 3:
            return candidate, version
    raise ValueError("Для сохранения высоты при изменении скорости нужен локальный Rubber Band 3+ "
                     "(macOS: brew install rubberband). Обычное воспроизведение остаётся доступно.")


def _join_tracks(paths: dict[str, Path], info: dict[str, dict], destination: Path) -> None:
    import numpy as np

    first = next(iter(info.values()))
    with ExitStack() as stack:
        readers = [stack.enter_context(wave.open(str(path), "rb")) for path in paths.values()]
        writer = stack.enter_context(wave.open(str(destination), "wb"))
        writer.setparams((sum(item["channels"] for item in info.values()), 2,
                          first["sample_rate"], 0, "NONE", "not compressed"))
        for offset in range(0, first["frames"], _BLOCK_FRAMES):
            count = min(_BLOCK_FRAMES, first["frames"] - offset)
            chunks = []
            for reader, item in zip(readers, info.values()):
                samples = np.frombuffer(reader.readframes(count), dtype="<i2")
                if samples.size != count * item["channels"]:
                    raise ValueError("Аудиодорожка обрезана или изменилась во время подготовки скорости.")
                chunks.append(samples.reshape(count, item["channels"]))
            writer.writeframesraw(np.concatenate(chunks, axis=1).tobytes())


def _split_tracks(source: Path, destination: Path, info: dict[str, dict], frames: int) -> None:
    import numpy as np

    first = next(iter(info.values()))
    channels = sum(item["channels"] for item in info.values())
    output = _wave_info(source)
    # Offline processing compensates its start delay and produces a precise
    # duration. Only sub-sample rounding is corrected, never an unexplained tail.
    if (output["channels"] != channels or output["sample_rate"] != first["sample_rate"]
            or abs(output["frames"] - frames) > 2):
        raise ValueError("Движок изменения скорости нарушил общую длительность или формат дорожек.")
    with ExitStack() as stack:
        reader = stack.enter_context(wave.open(str(source), "rb"))
        writers = []
        for name, item in info.items():
            writer = stack.enter_context(wave.open(str(destination / f"{name}.wav"), "wb"))
            writer.setparams((item["channels"], 2, item["sample_rate"], 0, "NONE", "not compressed"))
            writers.append(writer)
        for offset in range(0, frames, _BLOCK_FRAMES):
            count = min(_BLOCK_FRAMES, frames - offset)
            raw = np.frombuffer(reader.readframes(count), dtype="<i2").reshape(-1, channels)
            if len(raw) < count:
                raw = np.pad(raw, ((0, count - len(raw)), (0, 0)))
            column = 0
            for writer, item in zip(writers, info.values()):
                writer.writeframesraw(raw[:, column:column + item["channels"]].tobytes())
                column += item["channels"]


def _read_cache(destination: Path, key: str, info: dict[str, dict], frames: int) -> dict | None:
    if not destination.exists():
        return None
    try:
        data = json.loads((destination / "tempo.json").read_text(encoding="utf-8"))
        identity_keys = ("algorithm", "runtime", "parameters", "rate", "source_duration",
                         "source_sha256", "source_format")
        sample_rate = next(iter(info.values()))["sample_rate"]
        if (data["cache_key"] != key or _fingerprint({name: data[name] for name in identity_keys}) != key
                or data["tracks"] != {name: f"{name}.wav" for name in info}
                or data["schema_version"] != 3 or data["frames"] != frames
                or data["sample_rate"] != sample_rate or data["duration"] != frames / sample_rate
                or data["origin_seconds"] != 0
                or data["channels"] != {name: item["channels"] for name, item in info.items()}):
            raise ValueError("Неверный манифест")
        for name, item in info.items():
            path = destination / f"{name}.wav"
            if _wave_info(path) != {**item, "frames": frames} or _sha256(path) != data["output_sha256"][name]:
                raise ValueError("Повреждена дорожка")
        return data
    except (OSError, KeyError, ValueError, TypeError) as exc:
        raise ValueError("Сохранённый вариант скорости повреждён. Исходные дорожки остаются доступны.") from exc


def prepare_tempo(track_paths: dict[str, Path], destination_root: Path, rate: float,
                  source_duration: float) -> tuple[dict, Path]:
    """Return ``(metadata, directory)`` containing synchronized playback WAVs.

    ``metadata['tracks']`` maps available track names to local filenames. All
    outputs have ``frames`` samples at ``sample_rate`` and origin zero. Transport
    positions stay in original seconds: prepared audio offset = source / rate.
    ``destination_root/tempo-v3/<cache_key>`` is separate from v1/v2 artifacts.
    This synchronous function belongs in a worker thread, never the HTTP loop.
    """
    if isinstance(rate, bool) or not isinstance(rate, (float, int)) or rate not in TEMPO_RATES:
        raise ValueError("Неизвестная скорость. Доступны 0,25×, 0,5×, 0,75×, 1×, 1,25×, 1,5×, 1,75×, 2×.")
    if (isinstance(source_duration, bool) or not isinstance(source_duration, (float, int))
            or not math.isfinite(source_duration) or source_duration <= 0):
        raise ValueError("Неизвестна длительность исходной записи.")
    if not track_paths or not set(track_paths).issubset(_TRACK_NAMES):
        raise ValueError("Для изменения скорости нужны доступные вокал, минус или пианино.")
    paths = {name: Path(track_paths[name]).resolve() for name in sorted(track_paths)}
    with _PREPARE_LOCK:
        info = {name: _wave_info(path) for name, path in paths.items()}
        first = next(iter(info.values()))
        if any(item["channels"] not in (1, 2) or item["frames"] != first["frames"]
               or item["sample_rate"] != first["sample_rate"] for item in info.values()):
            raise ValueError("Дорожки должны иметь одинаковую частоту и длительность, по одному или два канала.")
        sample_rate = first["sample_rate"]
        if not 8000 <= sample_rate <= 192000 or abs(first["frames"] - source_duration * sample_rate) > 1:
            raise ValueError("Длительность дорожек не совпадает с исходной временной шкалой.")
        frames = round(first["frames"] / rate)
        binary, runtime = _rubberband() if rate != 1 else (None, "identity-pcm16")
        hashes = {name: _sha256(path) for name, path in paths.items()}
        identity = {"algorithm": TEMPO_VERSION, "runtime": runtime, "parameters": TEMPO_OPTIONS,
                    "rate": float(rate), "source_duration": float(source_duration),
                    "source_sha256": hashes, "source_format": info}
        key = _fingerprint(identity)
        cache_root = Path(destination_root).resolve() / "tempo-v3"
        destination = cache_root / key
        cached = _read_cache(destination, key, info, frames)
        if cached is not None:
            return cached, destination
        cache_root.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=".prepare-", dir=cache_root))
        try:
            if rate == 1:
                for name, path in paths.items():
                    shutil.copyfile(path, temporary / f"{name}.wav")
            else:
                joint, stretched = temporary / "joint.wav", temporary / "stretched.wav"
                _join_tracks(paths, info, joint)
                try:
                    result = subprocess.run([binary, "--fine", "--centre-focus", "--quiet", "--tempo",
                                             str(rate), str(joint), str(stretched)],
                                            capture_output=True, text=True, timeout=1800, check=False)
                except (OSError, subprocess.TimeoutExpired) as exc:
                    raise ValueError("Не удалось подготовить выбранную скорость локальным движком Rubber Band.") from exc
                if result.returncode:
                    detail = " ".join(result.stderr.strip().splitlines()[-3:])[:500]
                    raise ValueError(f"Не удалось подготовить выбранную скорость: {detail}")
                _split_tracks(stretched, temporary, info, frames)
                joint.unlink()
                stretched.unlink()
            if any(_sha256(path) != hashes[name] for name, path in paths.items()):
                raise ValueError("Исходные дорожки изменились во время подготовки скорости. Повторите выбор.")
            data = {**identity, "schema_version": 3, "cache_key": key, "origin_seconds": 0,
                    "sample_rate": sample_rate, "frames": frames, "duration": frames / sample_rate,
                    "tracks": {name: f"{name}.wav" for name in paths},
                    "channels": {name: item["channels"] for name, item in info.items()},
                    "output_sha256": {name: _sha256(temporary / f"{name}.wav") for name in paths}}
            (temporary / "tempo.json").write_text(json.dumps(data, ensure_ascii=False, indent=2,
                                                            allow_nan=False) + "\n", encoding="utf-8")
            try:
                temporary.rename(destination)
            except OSError:
                # A second process may have atomically published the same key.
                cached = _read_cache(destination, key, info, frames)
                if cached is None:
                    raise
                return cached, destination
            return data, destination
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
