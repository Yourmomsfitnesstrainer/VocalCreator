import pytest

np = pytest.importorskip('numpy')
from karaoke_generator.audio import _compare_timeline


def test_same_duration_does_not_hide_shift_or_local_drift():
    rng = np.random.default_rng(42)
    signal = rng.normal(0, .2, 40000)
    assert _compare_timeline(signal, signal)['status'] == 'verified'
    shifted = np.r_[np.zeros(400), signal[:-400]]
    report = _compare_timeline(signal, shifted)
    assert report['status'] == 'mismatch'
    assert all(x['lag_ms'] == 200 for x in report['observations'])
    shifted[:13000] = signal[:13000]
    assert _compare_timeline(signal, shifted)['status'] == 'mismatch'


def test_silence_does_not_claim_timeline_verified():
    assert _compare_timeline(np.zeros(10000), np.zeros(10000))['status'] == 'inconclusive'


def test_periodic_tone_does_not_create_a_false_timeline_mismatch():
    time = np.arange(6000) / 2000
    tone = np.sin(2 * np.pi * 440 * time)
    report = _compare_timeline(tone, tone)
    assert report['status'] == 'inconclusive'
    assert all(item['unambiguous'] is False for item in report['observations'])


def test_inconsistent_stem_sum_is_not_verified():
    signal = np.random.default_rng(45).normal(0, .2, 40000)
    wrong_sum = .7*signal + .3*np.r_[np.zeros(400), signal[:-400]]
    assert _compare_timeline(signal, wrong_sum)['status'] != 'verified'


def test_mp3_stream_origin_is_normalized_on_decoded_sample_axis(tmp_path):
    import subprocess
    import wave
    from karaoke_generator.audio import find_ffmpeg, prepare_audio, timeline_report, probe_timeline
    original = tmp_path/'original.wav'
    signal = np.random.default_rng(44).uniform(-.3, .3, 48000*3)
    with wave.open(str(original), 'wb') as wav:
        wav.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
        wav.writeframes((signal*32767).astype('<i2').tobytes())
    mp3, source = tmp_path/'original.mp3', tmp_path/'source.wav'
    subprocess.run([find_ffmpeg(), '-v', 'error', '-i', str(original), '-c:a', 'libmp3lame', str(mp3)], check=True)
    assert float(probe_timeline(mp3)['streams'][0]['start_time']) > 0
    prepare_audio(mp3, source)
    report = timeline_report(mp3, source, source, None)
    assert report['checks']['preparation']['status'] == 'verified'
    assert report['streams']['source']['streams'][0]['sample_rate'] == '44100'
    assert report['streams']['source']['wav_samples'] == 44100*3
