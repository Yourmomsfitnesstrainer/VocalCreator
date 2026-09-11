from __future__ import annotations

import importlib.util
import math
import subprocess
from importlib import metadata
from pathlib import Path
from typing import Any

from .audio import find_ffmpeg
from .melody import hz_to_midi
from .studio_models import PitchFrame
from .timing_cache import file_sha256, fingerprint, write_json


PITCH_ANALYSIS_VERSION = "1"


class PitchAnalyzer:
    name = "unknown"

    def analyze(self, audio: Path, options: dict[str, Any]) -> tuple[list[PitchFrame], dict[str, Any]]:
        raise NotImplementedError


class TorchCrepeAnalyzer(PitchAnalyzer):
    name = "torchcrepe"

    @staticmethod
    def available() -> bool:
        return importlib.util.find_spec("torchcrepe") is not None and importlib.util.find_spec("torch") is not None

    def analyze(self, audio: Path, options: dict[str, Any]) -> tuple[list[PitchFrame], dict[str, Any]]:
        if not self.available():
            raise RuntimeError("torchcrepe is not installed; install the 'studio' extra")
        import torch
        import torchcrepe

        requested_device = str(options.get("device", "auto"))
        if requested_device == "auto":
            if torch.backends.mps.is_available():
                device = "mps"
            elif torch.cuda.is_available():
                device = "cuda:0"
            else:
                device = "cpu"
        else:
            device = requested_device
        audio_tensor, sample_rate = torchcrepe.load.audio(str(audio))
        if audio_tensor.ndim == 1:
            audio_tensor = audio_tensor.unsqueeze(0)
        elif audio_tensor.shape[0] > 1:
            audio_tensor = audio_tensor.mean(dim=0, keepdim=True)
        hop_length = max(1, round(sample_rate * float(options.get("hop_seconds", 0.01))))
        fmin = float(options.get("fmin", 65.0))
        fmax = float(options.get("fmax", 1100.0))
        threshold = float(options.get("periodicity_threshold", 0.32))
        silence_db = float(options.get("silence_db", -55.0))
        pitch, periodicity = torchcrepe.predict(
            audio_tensor,
            sample_rate,
            hop_length,
            fmin,
            fmax,
            str(options.get("model", "full")),
            batch_size=int(options.get("batch_size", 1024)),
            device=device,
            return_periodicity=True,
        )
        periodicity = torchcrepe.filter.median(periodicity, 3)
        pitch = torchcrepe.filter.mean(pitch, 3)
        pitch_values = pitch.detach().cpu().numpy().reshape(-1)
        scores = periodicity.detach().cpu().numpy().reshape(-1)
        power = audio_tensor.unsqueeze(1).square()
        window_size = int(getattr(torchcrepe, "WINDOW_SIZE", 1024))
        rms = torch.nn.functional.avg_pool1d(
            torch.nn.functional.pad(power, (window_size // 2, window_size // 2)),
            kernel_size=window_size,
            stride=hop_length,
        ).sqrt().squeeze().detach().cpu().numpy().reshape(-1)
        frames = []
        for index, (raw_hz, raw_score) in enumerate(zip(pitch_values, scores)):
            hz = float(raw_hz)
            score = float(raw_score)
            frame_db = 20.0 * math.log10(max(float(rms[min(index, len(rms) - 1)]), 1e-8))
            voiced = (
                math.isfinite(hz)
                and fmin <= hz <= fmax
                and score >= threshold
                and frame_db >= silence_db
            )
            frames.append(
                PitchFrame(
                    time=round(index * hop_length / sample_rate, 6),
                    hz=round(hz, 6) if voiced else None,
                    midi=round(hz_to_midi(hz), 6) if voiced else None,
                    voiced=voiced,
                    periodicity=round(score, 6) if math.isfinite(score) else 0.0,
                    raw_score=round(score, 6) if math.isfinite(score) else 0.0,
                )
            )
        return frames, {
            "algorithm_version": PITCH_ANALYSIS_VERSION,
            "backend": self.name,
            "package_version": metadata.version("torchcrepe"),
            "device": device,
            "sample_rate": sample_rate,
            "hop_length": hop_length,
            "hop_seconds": hop_length / sample_rate,
            "fmin": fmin,
            "fmax": fmax,
            "periodicity_threshold": threshold,
            "silence_db": silence_db,
            "frame_count": len(frames),
            "voiced_frame_count": sum(frame.voiced for frame in frames),
        }


class AutocorrelationPitchAnalyzer(PitchAnalyzer):
    """Dependency-light baseline for tests and diagnostics, never a claimed quality winner."""

    name = "autocorrelation-baseline"

    def analyze(self, audio: Path, options: dict[str, Any]) -> tuple[list[PitchFrame], dict[str, Any]]:
        try:
            import numpy as np
        except ImportError as exc:
            raise RuntimeError("NumPy is required for the pitch baseline") from exc
        sample_rate = int(options.get("baseline_sample_rate", 16000))
        hop_seconds = float(options.get("hop_seconds", 0.01))
        hop_length = max(1, round(sample_rate * hop_seconds))
        frame_length = int(options.get("baseline_frame_length", 2048))
        fmin = float(options.get("fmin", 65.0))
        fmax = float(options.get("fmax", 1100.0))
        threshold = float(options.get("periodicity_threshold", 0.42))
        command = [
            find_ffmpeg(), "-v", "error", "-i", str(audio), "-vn", "-ac", "1", "-ar",
            str(sample_rate), "-f", "f32le", "-",
        ]
        decoded = subprocess.run(command, capture_output=True, check=True).stdout
        signal = np.frombuffer(decoded, dtype="<f4")
        window = np.hanning(frame_length).astype(np.float32)
        minimum_lag = max(1, int(sample_rate / fmax))
        maximum_lag = min(frame_length - 2, math.ceil(sample_rate / fmin))
        frames: list[PitchFrame] = []
        for start in range(0, max(0, len(signal) - frame_length + 1), hop_length):
            chunk = signal[start : start + frame_length].astype(np.float64)
            rms = float(np.sqrt(np.mean(chunk * chunk)))
            score = 0.0
            hz = None
            if rms >= 1e-4:
                chunk = (chunk - chunk.mean()) * window
                spectrum = np.fft.rfft(chunk, n=frame_length * 2)
                autocorrelation = np.fft.irfft(spectrum * np.conj(spectrum))[:frame_length]
                if autocorrelation[0] > 1e-12:
                    region = autocorrelation[minimum_lag : maximum_lag + 1]
                    lag = int(np.argmax(region)) + minimum_lag
                    score = float(autocorrelation[lag] / autocorrelation[0])
                    if minimum_lag < lag < maximum_lag:
                        left, center, right = autocorrelation[lag - 1 : lag + 2]
                        denominator = left - 2 * center + right
                        if abs(denominator) > 1e-12:
                            lag += float(0.5 * (left - right) / denominator)
                    candidate = sample_rate / lag
                    if score >= threshold and fmin <= candidate <= fmax:
                        hz = float(candidate)
            voiced = hz is not None
            frames.append(
                PitchFrame(
                    time=round(start / sample_rate, 6),
                    hz=round(hz, 6) if hz is not None else None,
                    midi=round(hz_to_midi(hz), 6) if hz is not None else None,
                    voiced=voiced,
                    periodicity=round(max(0.0, min(1.0, score)), 6),
                    raw_score=round(score, 6),
                )
            )
        return frames, {
            "algorithm_version": PITCH_ANALYSIS_VERSION,
            "backend": self.name,
            "status": "experimental-baseline",
            "device": "cpu",
            "sample_rate": sample_rate,
            "hop_length": hop_length,
            "hop_seconds": hop_length / sample_rate,
            "fmin": fmin,
            "fmax": fmax,
            "periodicity_threshold": threshold,
            "frame_count": len(frames),
            "voiced_frame_count": sum(frame.voiced for frame in frames),
        }


def create_pitch_analyzer(name: str) -> PitchAnalyzer:
    if name == "torchcrepe":
        return TorchCrepeAnalyzer()
    if name in {"autocorrelation", "autocorrelation-baseline"}:
        return AutocorrelationPitchAnalyzer()
    if name == "rmvpe":
        raise RuntimeError(
            "RMVPE has no stable packaged runner in this project yet; use torchcrepe or the explicit baseline"
        )
    raise ValueError(f"Unknown pitch backend: {name}")


def cached_pitch(
    audio: Path,
    options: dict[str, Any],
    cache_dir: Path,
) -> tuple[list[PitchFrame], dict[str, Any], str]:
    backend_name = str(options.get("backend", "torchcrepe"))
    spec = {
        "algorithm_version": PITCH_ANALYSIS_VERSION,
        "audio_sha256": file_sha256(audio),
        "backend": backend_name,
        "options": options,
    }
    key = fingerprint(spec)
    path = cache_dir / f"pitch-{key}.json"
    if path.exists():
        raw = __import__("json").loads(path.read_text(encoding="utf-8"))
        return [PitchFrame(**frame) for frame in raw["frames"]], {**raw["diagnostics"], "cache_hit": True}, key
    analyzer = create_pitch_analyzer(backend_name)
    frames, diagnostics = analyzer.analyze(audio, options)
    diagnostics = {**diagnostics, "cache_hit": False}
    write_json(path, {"spec": spec, "diagnostics": diagnostics, "frames": [frame.__dict__ for frame in frames]})
    return frames, diagnostics, key
