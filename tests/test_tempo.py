import json
import math
import wave

import pytest

np = pytest.importorskip("numpy")

from karaoke_generator import tempo


def write_audio(path, signal, sample_rate=44100):
    signal = np.asarray(signal)
    if signal.ndim == 1:
        signal = signal[:, None]
    with wave.open(str(path), "wb") as writer:
        writer.setparams((signal.shape[1], 2, sample_rate, 0, "NONE", "not compressed"))
        writer.writeframes(np.round(signal * 32767).astype("<i2").tobytes())
    return path


def read_audio(path):
    with wave.open(str(path), "rb") as reader:
        signal = np.frombuffer(reader.readframes(reader.getnframes()), dtype="<i2").reshape(-1, reader.getnchannels())
        return signal / 32768, reader.getframerate()


@pytest.fixture
def tracks(tmp_path):
    sample_rate, duration = 44100, 4
    time = np.arange(sample_rate * duration) / sample_rate
    result = {}
    for name, frequency in (("vocals", 440), ("instrumental", 660), ("piano", 880)):
        signal = np.zeros_like(time)
        for start, end in ((.4, 1.2), (2.2, 3.4)):
            active = (time >= start) & (time < end)
            phase = time[active] - start
            envelope = np.minimum(phase / .008, 1) * np.minimum((end - time[active]) / .008, 1)
            signal[active] = .2 * np.sin(2 * np.pi * frequency * phase) * envelope
        result[name] = write_audio(tmp_path / f"{name}.wav", np.c_[signal, .8 * signal])
    return result


@pytest.fixture
def engine():
    try:
        return tempo._rubberband()
    except ValueError:
        pytest.skip("Local Rubber Band 3+ is required for the acoustic integration probe")


def frequency_of(signal, sample_rate):
    spectrum = np.abs(np.fft.rfft(signal * np.hanning(len(signal))))
    index = int(np.argmax(spectrum))
    values = np.log(np.maximum(spectrum[index - 1:index + 2], 1e-15))
    correction = .5 * (values[0] - values[2]) / (values[0] - 2 * values[1] + values[2])
    return (index + correction) * sample_rate / len(signal)


def onset_near(signal, expected, sample_rate):
    offset = max(0, round((expected - .2) * sample_rate))
    chunk = signal[offset:round((expected + .2) * sample_rate)]
    block = round(.001 * sample_rate)
    rms = np.sqrt(np.mean(chunk[:len(chunk) // block * block].reshape(-1, block) ** 2, axis=1))
    return (offset + int(np.flatnonzero(rms > .035)[0]) * block) / sample_rate


@pytest.mark.parametrize("rate", tempo.TEMPO_RATES)
def test_all_rates_keep_pitch_pauses_onsets_and_three_track_duration(tracks, tmp_path, engine, rate):
    original_hashes = {name: tempo._sha256(path) for name, path in tracks.items()}
    data, directory = tempo.prepare_tempo(tracks, tmp_path / "derived", rate, 4.0)
    onsets = []
    for name, frequency in (("vocals", 440), ("instrumental", 660), ("piano", 880)):
        signal, sample_rate = read_audio(directory / data["tracks"][name])
        assert len(signal) == round(sample_rate * 4 / rate) == data["frames"]
        assert signal.shape[1] == 2
        pitch = frequency_of(signal[round(.6 / rate * sample_rate):round(1.0 / rate * sample_rate), 0], sample_rate)
        assert abs(1200 * math.log2(pitch / frequency)) < 5
        pause = signal[round(1.4 / rate * sample_rate):round(2 / rate * sample_rate)]
        assert np.max(np.abs(pause)) < .001
        attacks = [onset_near(signal[:, 0], start / rate, sample_rate) for start in (.4, 2.2)]
        assert max(abs(observed - expected / rate) for observed, expected in zip(attacks, (.4, 2.2))) <= .05
        onsets.append(attacks)
    assert np.max(np.ptp(onsets, axis=0)) <= .02
    assert data["origin_seconds"] == 0
    assert data["parameters"]["pitch_scale"] == 1
    assert original_hashes == {name: tempo._sha256(path) for name, path in tracks.items()}


def test_cache_uses_source_hash_rate_and_parameters_without_rendering_again(tracks, tmp_path, engine, monkeypatch):
    first, directory = tempo.prepare_tempo(tracks, tmp_path / "derived", .5, 4)
    with monkeypatch.context() as patch:
        patch.setattr(tempo, "_join_tracks", lambda *args: pytest.fail("Cache hit rendered again"))
        again, same = tempo.prepare_tempo(dict(reversed(list(tracks.items()))), tmp_path / "derived", .5, 4.0)
    assert same == directory and again == first
    changed_rate, _ = tempo.prepare_tempo(tracks, tmp_path / "derived", .75, 4)
    assert changed_rate["cache_key"] != first["cache_key"]
    samples, sample_rate = read_audio(tracks["piano"])
    write_audio(tracks["piano"], samples * .5, sample_rate)
    changed_audio, _ = tempo.prepare_tempo(tracks, tmp_path / "derived", .5, 4)
    assert changed_audio["cache_key"] != first["cache_key"]
    monkeypatch.setattr(tempo, "TEMPO_OPTIONS", {**tempo.TEMPO_OPTIONS, "revision": "test"})
    changed_parameters, _ = tempo.prepare_tempo(tracks, tmp_path / "derived", .5, 4)
    assert changed_parameters["cache_key"] != changed_audio["cache_key"]


def test_identity_requires_no_tempo_engine_and_preserves_stereo_bytes(tracks, tmp_path, monkeypatch):
    signal, sr = read_audio(tracks["vocals"])
    write_audio(tracks["vocals"], np.c_[signal[:, 0], -signal[:, 0]], sr)
    monkeypatch.setattr(tempo, "_rubberband", lambda: pytest.fail("1x requested a tempo engine"))
    data, directory = tempo.prepare_tempo({"vocals": tracks["vocals"]}, tmp_path / "derived", 1, 4)
    assert (directory / data["tracks"]["vocals"]).read_bytes() == tracks["vocals"].read_bytes()


def test_joint_processing_preserves_stereo_and_does_not_mix_track_content(tracks, tmp_path, engine):
    signal, sr = read_audio(tracks["vocals"])
    write_audio(tracks["vocals"], np.c_[signal[:, 0], signal[:, 0]], sr)
    write_audio(tracks["instrumental"], np.zeros_like(signal), sr)
    data, directory = tempo.prepare_tempo(tracks, tmp_path / "derived", .25, 4)
    vocal, _ = read_audio(directory / data["tracks"]["vocals"])
    silent, _ = read_audio(directory / data["tracks"]["instrumental"])
    assert vocal.shape == (4 * sr * 4, 2)
    np.testing.assert_array_equal(vocal[:, 0], vocal[:, 1])
    assert np.count_nonzero(silent) == 0


def test_processing_failure_does_not_publish_partial_cache_or_change_sources(tracks, tmp_path, engine, monkeypatch):
    before = {name: path.read_bytes() for name, path in tracks.items()}
    monkeypatch.setattr(tempo, "_split_tracks", lambda *args: (_ for _ in ()).throw(ValueError("probe failure")))
    with pytest.raises(ValueError, match="probe failure"):
        tempo.prepare_tempo(tracks, tmp_path / "derived", .5, 4)
    assert list((tmp_path / "derived" / "tempo-v3").iterdir()) == []
    assert before == {name: path.read_bytes() for name, path in tracks.items()}


def test_corrupted_cached_audio_is_not_served(tracks, tmp_path):
    data, directory = tempo.prepare_tempo(tracks, tmp_path / "derived", 1, 4)
    audio = directory / data["tracks"]["piano"]
    raw = bytearray(audio.read_bytes())
    raw[-10] ^= 1
    audio.write_bytes(raw)
    with pytest.raises(ValueError, match="повреждён"):
        tempo.prepare_tempo(tracks, tmp_path / "derived", 1, 4)


@pytest.mark.parametrize("field,value", [("rate", .5), ("duration", 8), ("origin_seconds", .05)])
def test_corrupted_cached_transport_metadata_is_not_served(tracks, tmp_path, field, value):
    data, directory = tempo.prepare_tempo(tracks, tmp_path / "derived", 1, 4)
    data[field] = value
    (directory / "tempo.json").write_text(json.dumps(data))
    with pytest.raises(ValueError, match="повреждён"):
        tempo.prepare_tempo(tracks, tmp_path / "derived", 1, 4)


@pytest.mark.parametrize("rate", [0, -1, float("nan"), float("inf"), .8, True, "0.5"])
def test_invalid_speed_is_rejected_before_any_publication(tracks, tmp_path, rate):
    with pytest.raises(ValueError, match="скорость"):
        tempo.prepare_tempo(tracks, tmp_path / "derived", rate, 4)
    assert not (tmp_path / "derived").exists()


def test_mismatched_track_timeline_and_unsafe_track_names_are_rejected(tracks, tmp_path):
    with pytest.raises(ValueError, match="временной шкалой"):
        tempo.prepare_tempo(tracks, tmp_path / "derived", 1, 3)
    write_audio(tracks["piano"], np.zeros(100))
    with pytest.raises(ValueError, match="одинаковую"):
        tempo.prepare_tempo(tracks, tmp_path / "derived", 1, 4)
    with pytest.raises(ValueError, match="доступные"):
        tempo.prepare_tempo({"../piano": tracks["piano"]}, tmp_path / "derived", 1, 4)


def change_tail_frames(path, delta):
    """Preserve every existing PCM sample except an explicitly removed tail."""
    with wave.open(str(path), "rb") as reader:
        params = reader.getparams()
        pcm = reader.readframes(reader.getnframes())
    sample_bytes = 2 * params.nchannels
    adjusted = pcm + bytes(delta * sample_bytes) if delta > 0 else pcm[:delta * sample_bytes]
    with wave.open(str(path), "wb") as writer:
        writer.setparams(params)
        writer.writeframes(adjusted)


@pytest.mark.parametrize("delta", [-1, 1])
@pytest.mark.parametrize("rate", [1, .5])
def test_one_tail_sample_is_normalized_only_in_derived_tracks(tracks, tmp_path, engine, monkeypatch, delta, rate):
    change_tail_frames(tracks["piano"], delta)
    before = {name: path.read_bytes() for name, path in tracks.items()}
    if rate == 1:
        monkeypatch.setattr(tempo, "_rubberband", lambda: pytest.fail("1x requested Rubber Band"))
    data, directory = tempo.prepare_tempo(tracks, tmp_path / "derived", rate, 4)
    assert data["source_tail_adjustment"] == {
        "policy": "final-sample-only-v1", "target_frames": 176400,
        "track_frame_deltas": {"piano": -delta}}
    assert data["source_format"]["piano"]["frames"] == 176400 + delta
    assert before == {name: path.read_bytes() for name, path in tracks.items()}
    attacks = []
    for name, frequency in (("vocals", 440), ("instrumental", 660), ("piano", 880)):
        signal, sr = read_audio(directory / f"{name}.wav")
        assert len(signal) == data["frames"] == round(176400 / rate)
        if rate == 1:
            original, _ = read_audio(tracks[name])
            np.testing.assert_array_equal(signal[:min(len(original), len(signal))], original[:len(signal)])
            if len(original) < len(signal):
                np.testing.assert_array_equal(signal[-1], [0, 0])
        else:
            observed = frequency_of(signal[round(.6 / rate * sr):round(1 / rate * sr), 0], sr)
            assert abs(1200 * math.log2(observed / frequency)) < 5
            attacks.append(onset_near(signal[:, 0], .4 / rate, sr))
    if rate != 1:
        assert max(abs(attack - .4 / rate) for attack in attacks) <= .05
        assert np.ptp(attacks) <= .02
    with monkeypatch.context() as patch:
        patch.setattr(tempo, "_join_tracks", lambda *a, **k: pytest.fail("normalized cache rebuilt"))
        again, same = tempo.prepare_tempo(tracks, tmp_path / "derived", rate, 4)
    assert same == directory and again == data


def test_equal_length_inputs_keep_legacy_key_and_output_bytes(tracks, tmp_path):
    data, directory = tempo.prepare_tempo(tracks, tmp_path / "derived", 1, 4)
    legacy_identity = {"algorithm": "rubberband-r3-offline-joint-v3-1", "runtime": "identity-pcm16",
        "parameters": {"engine": "R3", "processing": "offline", "pitch_scale": 1.0,
                       "channels": "together", "sample_format": "PCM_16", "origin_seconds": 0,
                       "duration_rounding": "nearest-sample"},
        "rate": 1.0, "source_duration": 4.0,
        "source_sha256": {name: tempo._sha256(path) for name, path in sorted(tracks.items())},
        "source_format": {name: tempo._wave_info(path) for name, path in sorted(tracks.items())}}
    assert "source_tail_adjustment" not in data
    assert data["cache_key"] == tempo._fingerprint(legacy_identity)
    for name, path in tracks.items():
        assert (directory / f"{name}.wav").read_bytes() == path.read_bytes()


@pytest.mark.parametrize("delta", [-2, 2])
def test_two_sample_track_mismatch_still_fails_before_rendering(tracks, tmp_path, monkeypatch, delta):
    change_tail_frames(tracks["piano"], delta)
    monkeypatch.setattr(tempo, "_rubberband", lambda: pytest.fail("rejected mismatch requested engine"))
    with pytest.raises(ValueError, match="одинаковую"):
        tempo.prepare_tempo(tracks, tmp_path / "derived", .5, 4)
    assert not (tmp_path / "derived").exists()


@pytest.mark.parametrize("delta", [-1, 1])
def test_tail_adjustment_does_not_hide_truncated_pcm_data(tracks, tmp_path, delta):
    change_tail_frames(tracks["piano"], delta)
    # A header allowing a one-sample adjustment must never excuse a missing
    # actual sample. This covers both the short pad and ignored long tail paths.
    path = tracks["piano"]
    path.write_bytes(path.read_bytes()[:-4])
    before = {name: path.read_bytes() for name, path in tracks.items()}
    with pytest.raises(ValueError, match="обрезана"):
        tempo.prepare_tempo(tracks, tmp_path / "derived", 1, 4)
    assert list((tmp_path / "derived/tempo-v3").iterdir()) == []
    assert before == {name: path.read_bytes() for name, path in tracks.items()}


def test_last_sample_padding_works_across_read_block_boundary(tmp_path):
    count = tempo._BLOCK_FRAMES + 1
    paths = {"vocals": write_audio(tmp_path / "vocals.wav", np.ones(count) * .2),
             "piano": write_audio(tmp_path / "piano.wav", np.ones(count - 1) * .4)}
    info = {name: tempo._wave_info(path) for name, path in paths.items()}
    joined = tmp_path / "joint.wav"
    tempo._join_tracks(paths, info, joined, frames=count)
    samples, _ = read_audio(joined)
    assert len(samples) == count and samples[-1, 0] > .19 and samples[-1, 1] == 0
    assert np.all(samples[:-1, 1] > .39)


def test_corrupt_tail_policy_is_rejected_from_cache(tracks, tmp_path):
    change_tail_frames(tracks["piano"], 1)
    data, directory = tempo.prepare_tempo(tracks, tmp_path / "derived", 1, 4)
    data["source_tail_adjustment"]["target_frames"] -= 1
    (directory / "tempo.json").write_text(json.dumps(data))
    with pytest.raises(ValueError, match="повреждён"):
        tempo.prepare_tempo(tracks, tmp_path / "derived", 1, 4)
