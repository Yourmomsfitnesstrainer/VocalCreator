def test_asr_refinement_cache_invalidation(tmp_path, monkeypatch):
    from copy import deepcopy
    from karaoke_generator import timing_cache as cache
    from karaoke_generator.config import DEFAULT_CONFIG
    from karaoke_generator.lyrics import parse_lyrics_text
    from karaoke_generator.models import TimedWord
    audio = tmp_path/'audio.wav'
    audio.write_bytes(b'waveform-one')
    document = parse_lyrics_text('один два')
    options = deepcopy(DEFAULT_CONFIG['alignment'])
    calls = {'asr': 0, 'refine': 0}

    class Backend:
        def transcribe(self, *args):
            calls['asr'] += 1
            return [TimedWord('один', 1, 1.3, .99), TimedWord('два', 1.6, 1.9, .99)], 'ru'
        def refine(self, audio, words, *args):
            calls['refine'] += 1
            return words

    monkeypatch.setattr(cache, 'create_backend', lambda *a, **kw: Backend())
    monkeypatch.setattr(cache, 'model_identity', lambda name, **kw: {'name': name, 'revision': 'fixed'})
    def run():
        return cache.cached_timing(audio, document, 'ru', 3, options, tmp_path)[2]
    assert run()['asr_cache_hit'] is False
    assert run()['refinement_cache_hit'] is True
    assert calls == {'asr': 1, 'refine': 1}
    options['align_models']['ru'] = 'another-model'
    assert run()['asr_cache_hit'] is True
    assert calls == {'asr': 1, 'refine': 2}
    options['context_seconds'] = .5
    run()
    assert calls == {'asr': 1, 'refine': 3}
    monkeypatch.setattr(cache, 'REFINEMENT_ALGORITHM_VERSION', 'future')
    run()
    assert calls == {'asr': 1, 'refine': 4}
    options['vad_filter'] = False
    run()
    assert calls == {'asr': 2, 'refine': 5}
    audio.write_bytes(b'waveform-two')
    run()
    assert calls == {'asr': 3, 'refine': 6}
