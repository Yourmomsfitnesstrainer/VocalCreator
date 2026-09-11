import math
import wave

import pytest

from karaoke_generator.melody import hz_to_midi, link_words_to_notes, segment_notes
from karaoke_generator.models import (
    AlignedLine,
    AlignedWord,
    AlignmentQuality,
    AlignmentResult,
)
from karaoke_generator.piano import render_piano
from karaoke_generator.pitch import AutocorrelationPitchAnalyzer, TorchCrepeAnalyzer
from karaoke_generator.studio_models import MelodyResult, NoteEvent, PitchFrame


def _frame(time: float, midi: float | None, score: float = 0.9) -> PitchFrame:
    hz = 440 * 2 ** ((midi - 69) / 12) if midi is not None else None
    return PitchFrame(time, hz, midi, midi is not None, score if midi is not None else 0, score)


def test_vibrato_stays_one_note_but_repeated_attack_is_separate() -> None:
    frames = [_frame(index * 0.01, 69 + math.sin(index / 3) * 0.28) for index in range(35)]
    frames += [_frame(0.35 + index * 0.01, None) for index in range(8)]
    frames += [_frame(0.43 + index * 0.01, 69 + math.sin(index / 3) * 0.2) for index in range(30)]

    notes = segment_notes(frames, duration=0.73, source="test", hop_seconds=0.01)

    assert [(note.midi, round(note.start, 2), round(note.end, 2)) for note in notes] == [
        (69, 0.0, 0.35),
        (69, 0.43, 0.73),
    ]


def test_one_word_links_to_every_overlapping_note() -> None:
    word = AlignedWord("sing", "sing", 0.1, 0.9, 0.99, True, "refined", timing={"source": "refined"})
    alignment = AlignmentResult(
        "en",
        1.0,
        "hash",
        [AlignedLine("sing", 0.1, 0.9, [word])],
        "whisperx",
        AlignmentQuality(1, 1, 1, 0, 1.0),
    )
    notes = [
        NoteEvent("n1", 0.0, 0.4, 60, 0, 0.9, "test"),
        NoteEvent("n2", 0.4, 0.95, 62, 0, 0.9, "test"),
    ]

    links = link_words_to_notes(alignment, notes)

    assert [link.note_id for link in links] == ["n1", "n2"]
    assert [(link.start, link.end, link.status) for link in links] == [
        (0.1, 0.4, "confirmed"),
        (0.4, 0.9, "confirmed"),
    ]


def test_melody_reader_rejects_out_of_timeline_note() -> None:
    payload = {
        "schema_version": 1,
        "timeline": {"duration": 1.0},
        "pitch_frames": [],
        "notes": [{"id": "n1", "start": 0.5, "end": 1.2, "midi": 60, "cents": 0, "confidence": 0.9, "source": "test", "uncertain": False}],
        "word_note_links": [],
        "diagnostics": {},
        "provenance": {},
    }
    with pytest.raises(ValueError, match="outside the audio timeline"):
        MelodyResult.from_dict(payload)


def test_additive_piano_has_requested_timeline(tmp_path) -> None:
    output = tmp_path / "piano.wav"
    render_piano([NoteEvent("n1", 0.1, 0.4, 69, 0, 0.9, "test")], output, duration=1.0)
    with wave.open(str(output), "rb") as audio:
        assert audio.getnchannels() == 2
        assert audio.getframerate() == 44100
        assert audio.getnframes() == 44100


def test_autocorrelation_baseline_tracks_calibration_tone(tmp_path) -> None:
    np = pytest.importorskip("numpy")
    path = tmp_path / "a3.wav"
    samples = np.sin(2 * np.pi * 220 * np.arange(16000) / 16000)
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        audio.writeframes((samples * 16000).astype("<i2").tobytes())

    frames, diagnostics = AutocorrelationPitchAnalyzer().analyze(
        path, {"hop_seconds": 0.01, "fmin": 65, "fmax": 1100, "periodicity_threshold": 0.4}
    )

    voiced = [frame for frame in frames if frame.voiced]
    assert diagnostics["status"] == "experimental-baseline"
    assert len(voiced) > 50
    assert abs(sum(frame.midi for frame in voiced) / len(voiced) - hz_to_midi(220)) < 0.08


def test_torchcrepe_stereo_calibration_tone_is_not_classified_as_silence(tmp_path) -> None:
    pytest.importorskip("torchcrepe")
    np = pytest.importorskip("numpy")
    path = tmp_path / "a4-stereo.wav"
    samples = np.sin(2 * np.pi * 440 * np.arange(16000) / 16000)
    stereo = np.column_stack([samples, samples])
    with wave.open(str(path), "wb") as audio:
        audio.setparams((2, 2, 16000, 0, "NONE", "not compressed"))
        audio.writeframes((stereo * 16000).astype("<i2").tobytes())

    frames, diagnostics = TorchCrepeAnalyzer().analyze(
        path,
        {
            "device": "cpu",
            "model": "tiny",
            "hop_seconds": 0.01,
            "fmin": 65,
            "fmax": 1100,
            "periodicity_threshold": 0.32,
            "silence_db": -55,
            "batch_size": 256,
        },
    )

    voiced = [frame for frame in frames if frame.voiced]
    assert diagnostics["voiced_frame_count"] > 80
    assert abs(sum(frame.midi for frame in voiced) / len(voiced) - 69) < 0.15
