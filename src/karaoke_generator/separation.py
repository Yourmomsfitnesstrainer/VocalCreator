from __future__ import annotations

import importlib.util
import hashlib
import shutil
import subprocess
import sys
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any

from .audio import ExternalCommandError


@dataclass(frozen=True)
class SeparationResult:
    vocals: Path
    instrumental: Path | None
    backend: str
    provenance: dict[str, Any] | None = None


class DemucsSeparator:
    def __init__(self, model: str = "htdemucs") -> None:
        self.model = model

    @staticmethod
    def available() -> bool:
        return importlib.util.find_spec("demucs") is not None

    def separate(self, source: Path, output_dir: Path) -> SeparationResult:
        if not self.available():
            raise RuntimeError("Demucs is not installed; install the 'separation' extra")
        work = output_dir / "demucs"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "demucs",
                "--two-stems=vocals",
                "-n",
                self.model,
                "-d",
                "cpu",
                "-o",
                str(work),
                str(source),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            detail = "\n".join(result.stderr.strip().splitlines()[-12:])
            raise ExternalCommandError(f"Vocal separation failed.\n{detail}")
        stem_dir = work / self.model / source.stem
        vocals_source = stem_dir / "vocals.wav"
        instrumental_source = stem_dir / "no_vocals.wav"
        if not vocals_source.exists() or not instrumental_source.exists():
            raise RuntimeError(f"Demucs completed but stems were not found in {stem_dir}")
        vocals = output_dir / "vocals.wav"
        instrumental = output_dir / "instrumental.wav"
        shutil.copy2(vocals_source, vocals)
        shutil.copy2(instrumental_source, instrumental)
        return SeparationResult(
            vocals,
            instrumental,
            f"demucs:{self.model}",
            {
                "backend": "demucs",
                "model": self.model,
                "device": "cpu",
                "package_version": _package_version("demucs"),
                "weights": _demucs_weight_hashes(self.model),
            },
        )


class MelBandRoformerSeparator:
    """Adapter for the separately installed, checksum-aware inference CLI."""

    def __init__(
        self,
        model: str = "melband-roformer-kim-vocals",
        *,
        device: str = "auto",
    ) -> None:
        self.model = model
        self.device = device

    @staticmethod
    def available() -> bool:
        return importlib.util.find_spec("mel_band_roformer") is not None

    def separate(self, source: Path, output_dir: Path) -> SeparationResult:
        if not self.available():
            raise RuntimeError(
                "melband-roformer-infer is not installed; install the 'studio-roformer' extra"
            )
        work = output_dir / "melband-roformer"
        input_dir = work / "input"
        stems_dir = work / "stems"
        input_dir.mkdir(parents=True, exist_ok=True)
        stems_dir.mkdir(parents=True, exist_ok=True)
        staged_source = input_dir / "source.wav"
        shutil.copy2(source, staged_source)
        resolved_device = _resolve_torch_device(self.device)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "mel_band_roformer.inference",
                "--input_folder",
                str(input_dir),
                "--store_dir",
                str(stems_dir),
                "--model",
                self.model,
                "--device",
                resolved_device,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            detail = "\n".join((result.stderr or result.stdout).strip().splitlines()[-12:])
            raise ExternalCommandError(f"Mel-Band RoFormer separation failed.\n{detail}")
        candidates = [path for path in stems_dir.rglob("*.wav") if path.is_file()]
        vocals_source = _find_named_stem(candidates, "vocal")
        instrumental_source = _find_named_stem(candidates, "instrument")
        if vocals_source is None or instrumental_source is None:
            names = ", ".join(path.name for path in candidates) or "none"
            raise RuntimeError(f"Mel-Band RoFormer completed without both stems; found: {names}")
        vocals = output_dir / "vocals.wav"
        instrumental = output_dir / "instrumental.wav"
        shutil.copy2(vocals_source, vocals)
        shutil.copy2(instrumental_source, instrumental)
        return SeparationResult(
            vocals,
            instrumental,
            f"melband-roformer:{self.model}",
            {
                "backend": "melband-roformer-infer",
                "model": self.model,
                "device": resolved_device,
                "package_version": _package_version("melband-roformer-infer"),
                "weights": _hash_candidate_model_files(),
            },
        )


def original_audio_fallback(source: Path, output_dir: Path) -> SeparationResult:
    vocals = output_dir / "vocals.wav"
    shutil.copy2(source, vocals)
    return SeparationResult(vocals, None, "original-audio", {"backend": "original-audio"})


def create_separator(config: dict[str, Any]):
    backend = str(config.get("backend", "demucs"))
    if backend == "demucs":
        return DemucsSeparator(str(config.get("model", "htdemucs")))
    if backend == "melband-roformer":
        return MelBandRoformerSeparator(
            str(config.get("model", "melband-roformer-kim-vocals")),
            device=str(config.get("device", "auto")),
        )
    raise ValueError(f"Unknown separation backend: {backend}")


def _package_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def _resolve_torch_device(preferred: str) -> str:
    if preferred != "auto":
        return preferred
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda:0"
    except ImportError:
        pass
    return "cpu"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _demucs_weight_hashes(model: str) -> dict[str, str] | None:
    try:
        from demucs.hf import DEFAULT_NAMESPACE, hf_repo_name
        from huggingface_hub import scan_cache_dir

        namespace = DEFAULT_NAMESPACE
        model_name = model.removeprefix("hf://")
        if "/" in model_name:
            namespace, model_name = model_name.split("/", 1)
        repo_id = f"{namespace}/{hf_repo_name(model_name)}"
        cache = scan_cache_dir()
        matches = {
            f"{repo.repo_id}@{revision.commit_hash}/{cached_file.file_name}": _sha256(
                Path(cached_file.blob_path)
            )
            for repo in cache.repos
            if repo.repo_id.lower() == repo_id.lower()
            for revision in repo.revisions
            for cached_file in revision.files
            if Path(cached_file.file_name).suffix == ".safetensors"
        }
        if matches:
            return dict(sorted(matches.items()))
    except (ImportError, OSError, ValueError):
        pass

    try:
        import torch

        root = Path(torch.hub.get_dir()) / "checkpoints"
        matches = sorted(path for path in root.glob("*.th") if path.is_file())
        return {path.name: _sha256(path) for path in matches} or None
    except (ImportError, OSError):
        return None


def _hash_candidate_model_files() -> dict[str, str] | None:
    roots = [Path.home() / ".cache" / "melband-roformer-infer", Path("models")]
    matches = {
        f"{root.name}/{path.relative_to(root)}": _sha256(path)
        for root in roots
        if root.exists()
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in {".ckpt", ".pt", ".pth"}
    }
    return dict(sorted(matches.items())) or None


def _find_named_stem(paths: list[Path], token: str) -> Path | None:
    token = token.lower()
    matches = [path for path in paths if token in path.stem.lower()]
    return min(matches, key=lambda path: len(path.name)) if matches else None
