import json
import wave
from copy import deepcopy

from karaoke_generator.config import DEFAULT_CONFIG
from karaoke_generator.models import TimedWord
from karaoke_generator.studio_models import PitchFrame


def _audio(path, duration=1.0):
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\0\0" * round(duration * 16000))


def _timing(*args, **kwargs):
    return [TimedWord("one", 0.1, 0.45, 0.99), TimedWord("two", 0.5, 0.9, 0.99)], "en", {
        "timing_key": "timing-key",
        "asr_cache_hit": False,
        "refinement_cache_hit": None,
        "asr": {"model": {"name": "test"}},
    }


def _pitch(*args, **kwargs):
    frames = [PitchFrame(index * 0.01, 440.0, 69.0, True, 0.9, 0.9) for index in range(100)]
    return frames, {"backend": "test-pitch", "hop_seconds": 0.01, "voiced_frame_count": 100}, "pitch-key"


def test_provided_vocal_builds_complete_audio_only_result(tmp_path, monkeypatch) -> None:
    from karaoke_generator import studio
    audio, lyrics, output = tmp_path / "voice.wav", tmp_path / "lyrics.txt", tmp_path / "result"
    _audio(audio)
    lyrics.write_text("one two")
    monkeypatch.setattr(studio, "cached_timing", _timing)
    monkeypatch.setattr(studio, "cached_pitch", _pitch)
    config = deepcopy(DEFAULT_CONFIG)
    config["alignment"]["backend"] = "uniform"

    manifest = studio.run_studio(audio, lyrics, output, config, input_type="vocal", job_id="job1")

    assert manifest["status"] == "complete"
    assert manifest["stages"]["pitch"]["processing_seconds"] >= 0
    assert manifest["runtime"]["peak_rss_bytes"] > 0
    assert manifest["separation"]["status"] == "provided_vocal"
    assert "instrumental.wav" not in manifest["artifacts"]
    assert {"vocals.wav", "piano.wav", "melody.json", "alignment.json", "studio.json"} <= set(manifest["artifacts"])
    melody = json.loads((output / "melody.json").read_text())
    assert len(melody["notes"]) == 1
    assert {link["note_id"] for link in melody["word_note_links"]} == {"n00000"}


def test_alignment_failure_keeps_vocal_melody_and_piano(tmp_path, monkeypatch) -> None:
    from karaoke_generator import studio
    audio, lyrics, output = tmp_path / "voice.wav", tmp_path / "lyrics.txt", tmp_path / "result"
    _audio(audio)
    lyrics.write_text("one two")

    def fail_alignment(*args, **kwargs):
        raise RuntimeError("ASR unavailable")

    monkeypatch.setattr(studio, "cached_timing", fail_alignment)
    monkeypatch.setattr(studio, "cached_pitch", _pitch)

    manifest = studio.run_studio(audio, lyrics, output, deepcopy(DEFAULT_CONFIG), input_type="vocal")

    assert manifest["status"] == "partial"
    assert manifest["alignment"]["status"] == "unavailable"
    assert "alignment.json" not in manifest["artifacts"]
    assert {"vocals.wav", "melody.json", "piano.wav"} <= set(manifest["artifacts"])
    assert json.loads((output / "melody.json").read_text())["word_note_links"] == []


def test_mix_never_publishes_original_as_vocal_when_separator_is_missing(tmp_path, monkeypatch) -> None:
    from karaoke_generator import studio
    audio, lyrics, output = tmp_path / "mix.wav", tmp_path / "lyrics.txt", tmp_path / "result"
    _audio(audio)
    lyrics.write_text("one")

    class MissingSeparator:
        @staticmethod
        def available():
            return False

    monkeypatch.setattr(studio, "create_separator", lambda config: MissingSeparator())

    manifest = studio.run_studio(audio, lyrics, output, deepcopy(DEFAULT_CONFIG), input_type="mix")

    assert manifest["status"] == "failed"
    assert manifest["stages"]["separation"]["status"] == "failed"
    assert "vocals.wav" not in manifest["artifacts"]
    assert not (output / "vocals.wav").exists()
    assert (output / "lyrics.txt").exists()
