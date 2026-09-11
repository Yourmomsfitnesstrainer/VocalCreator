from __future__ import annotations

import subprocess
import wave

from karaoke_generator.separation import MelBandRoformerSeparator


def _wav(path) -> None:
    with wave.open(str(path), "wb") as audio:
        audio.setparams((2, 2, 44100, 0, "NONE", "not compressed"))
        audio.writeframes(b"\0\0\0\0" * 441)


def test_melband_adapter_uses_supported_cli_and_records_resolved_device(
    tmp_path, monkeypatch
) -> None:
    source = tmp_path / "source.wav"
    output = tmp_path / "output"
    output.mkdir()
    _wav(source)
    called: list[str] = []

    monkeypatch.setattr(MelBandRoformerSeparator, "available", lambda _: True)
    monkeypatch.setattr("karaoke_generator.separation._resolve_torch_device", lambda _: "mps")
    monkeypatch.setattr("karaoke_generator.separation._package_version", lambda _: "0.1.5")
    monkeypatch.setattr("karaoke_generator.separation._hash_candidate_model_files", lambda: {"m.ckpt": "sha"})

    def run(command, **kwargs):
        called.extend(command)
        stems = output / "melband-roformer" / "stems"
        stems.mkdir(parents=True, exist_ok=True)
        _wav(stems / "source_vocals.wav")
        _wav(stems / "source_instrumental.wav")
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr("karaoke_generator.separation.subprocess.run", run)

    result = MelBandRoformerSeparator().separate(source, output)

    assert "--backend" not in called
    assert called[called.index("--device") + 1] == "mps"
    assert result.provenance == {
        "backend": "melband-roformer-infer",
        "model": "melband-roformer-kim-vocals",
        "device": "mps",
        "package_version": "0.1.5",
        "weights": {"m.ckpt": "sha"},
    }
    assert result.vocals.exists()
    assert result.instrumental is not None and result.instrumental.exists()
