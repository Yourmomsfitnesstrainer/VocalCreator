#!/usr/bin/env python3
"""Run real ASR/refiners and metamorphic checks without acoustic ground truth."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, replace
import json
from pathlib import Path
import time

from karaoke_generator.alignment import align_lyrics, WhisperXBackend, ASR_ALGORITHM_VERSION, REFINEMENT_ALGORITHM_VERSION, MAPPING_ALGORITHM_VERSION
from karaoke_generator.audio import prepare_audio, probe_duration, find_ffmpeg, run_command
from karaoke_generator.config import load_config
from karaoke_generator.lyrics import parse_lyrics_file, parse_lyrics_text
from karaoke_generator.timing_cache import cached_timing, fingerprint, file_sha256, model_identity, package_versions, write_json, _word_payload, _read_word


def compare_transformed(reference, candidate, offsets):
    ref = [w for line in reference.lines for w in line.words]
    actual = [w for line in candidate.lines for w in line.words]
    if len(actual) != len(ref)*len(offsets):
        raise ValueError('Transformed output dropped words')
    rows = []
    for copy_index, offset in enumerate(offsets):
        for index, original in enumerate(ref):
            word = actual[copy_index*len(ref)+index]
            if word.text != original.text:
                raise ValueError('Transformed output changed display text')
            rows.append({'copy': copy_index, 'index': index, 'text': word.text,
                         'delta_start_ms': round((word.start-original.start-offset)*1000, 3),
                         'delta_end_ms': round((word.end-original.end-offset)*1000, 3),
                         'source_before': original.timing['source'], 'source_after': word.timing['source']})
    values = sorted(max(abs(r['delta_start_ms']),abs(r['delta_end_ms'])) for r in rows)
    import math
    return {'words': len(rows), 'max_boundary_change_ms': max(values, default=0),
            'p90_boundary_change_ms': values[max(0, math.ceil(.9*len(values))-1)] if values else None,
            'over_40ms': sum(value > 40 for value in values), 'rows': rows,
            'meaning': 'Stability under a known transformation, not acoustic error'}


def validate(result, expected):
    words = [w for line in result.lines for w in line.words]
    if [w.text for w in words] != [w.display for w in expected.words]:
        raise ValueError('Alignment changed display text')
    if not all(0 <= w.start < w.end <= result.duration for w in words):
        raise ValueError('Alignment has invalid word intervals')
    if not all(left.end <= right.start+.001 for left,right in zip(words,words[1:])):
        raise ValueError('Alignment has overlapping word intervals')
    reasons = Counter(i.get('reason', 'asr') for w in words for i in w.timing['inputs'])
    return {'words': len(words), 'text_matching': asdict(result.quality),
            'sources': dict(Counter(w.timing['source'] for w in words)),
            'corrections': sum(bool(w.timing['corrections']) for w in words), 'refinement_reasons': dict(reasons)}


def run(audio, lyrics, output, language, asr_model, models, contexts):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    document = parse_lyrics_file(lyrics)
    source = output/'analysis.wav'
    if not source.exists() or not (output/'input-sha256.txt').exists() or (output/'input-sha256.txt').read_text() != file_sha256(audio):
        prepare_audio(audio,source)
        (output/'input-sha256.txt').write_text(file_sha256(audio))
    duration = probe_duration(source)
    options = load_config()['alignment']
    options.update(backend='faster-whisper', model=asr_model, language=language, device='cpu')
    print(f'ASR {asr_model}: {duration:.2f}s, {len(document.words)} display words',flush=True)
    base, detected, details = cached_timing(source, document, language, duration, options, output/'work')
    raw_alignment = align_lyrics(document,base,duration,detected,'faster-whisper')
    write_json(output/'asr.alignment.json',raw_alignment.to_dict())
    report = {'versions': package_versions(),
              'algorithms': {'asr': ASR_ALGORITHM_VERSION, 'refinement': REFINEMENT_ALGORITHM_VERSION,
                             'mapping': MAPPING_ALGORITHM_VERSION},
              'asr': details, 'inputs': {'audio_sha256': file_sha256(audio),
              'analysis_sha256':file_sha256(source), 'lyrics_sha256':file_sha256(lyrics)},
              'asr_checks':validate(raw_alignment,document), 'variants':{},
              'ground_truth':False, 'human_review':'pending'}
    write_json(output/'report.json',report)
    transforms = []
    shifted=output/'leading-silence.wav'
    run_command([find_ffmpeg(),'-v','error','-y','-i',str(source),'-af','adelay=750:all=1',str(shifted)])
    transforms.append(('leading-silence',shifted,[replace(w,start=w.start+.75,end=w.end+.75) for w in base],document,[.75]))
    quieter=output/'quieter.wav'
    run_command([find_ffmpeg(),'-v','error','-y','-i',str(source),'-af','volume=0.5',str(quieter)])
    transforms.append(('quieter',quieter,base,document,[0]))
    repeat=output/'repeated.wav'
    run_command([find_ffmpeg(),'-v','error','-y','-i',str(source),'-i',str(source),'-filter_complex',
                 '[0:a]apad=pad_dur=1[first];[first][1:a]concat=n=2:v=0:a=1[out]','-map','[out]',str(repeat)])
    last_segment=max((w.segment_id or 0) for w in base)+1
    repeated_words=base+[replace(w,start=w.start+duration+1,end=w.end+duration+1,
                                segment_id=(w.segment_id or 0)+last_segment) for w in base]
    transforms.append(('repeated',repeat,repeated_words,
                       parse_lyrics_text(document.processed_text+'\n\n'+document.processed_text),[0,duration+1]))
    for model in models:
        for context in contexts:
            label=f'{model}:context={context}'
            print(f'Refiner {label}',flush=True)
            backend=WhisperXBackend(device='cpu',align_models={detected:model},context_seconds=context)
            def refine_cached(path, words, doc):
                spec={'audio':file_sha256(path),'asr':fingerprint({'words':[_word_payload(w) for w in words]}),
                      'model':model_identity(model),'context':context,'algorithm':REFINEMENT_ALGORITHM_VERSION,
                      'versions':package_versions()}
                target=output/'work'/f'refiner-{fingerprint(spec)}.json'
                start=time.monotonic()
                if target.exists():
                    refined=[_read_word(w) for w in json.loads(target.read_text())['words']]
                else:
                    refined=backend.refine(path,words,detected,probe_duration(path))
                    spec['model']=model_identity(model)
                    target=output/'work'/f'refiner-{fingerprint(spec)}.json'
                    write_json(target,{'spec':spec,'words':[_word_payload(w) for w in refined]})
                aligned=align_lyrics(doc,refined,probe_duration(path),detected,'whisperx')
                return aligned,round(time.monotonic()-start,3)
            aligned,seconds=refine_cached(source,base,document)
            artifact=output/f'variant-{len(report["variants"])+1}'
            artifact.mkdir(exist_ok=True)
            write_json(artifact/'alignment.json',aligned.to_dict())
            variant={'model':model_identity(model),'context_seconds':context,'seconds':seconds,
                     'checks':validate(aligned,document),'transforms':{},'artifact':str(artifact)}
            report['variants'][label]=variant
            print(variant['checks'],flush=True)
            write_json(output/'report.json',report)
            for name,path,words,doc,offsets in transforms:
                print(f'  Transform {name}',flush=True)
                changed,elapsed=refine_cached(path,words,doc)
                checks = validate(changed,doc)
                write_json(artifact/f'{name}.alignment.json',changed.to_dict())
                comparison=compare_transformed(aligned,changed,offsets)
                variant['transforms'][name]={**comparison,'seconds':elapsed,'checks':checks}
                print({k:v for k,v in comparison.items() if k!='rows'},flush=True)
                write_json(output/'report.json',report)
    write_json(output/'report.json',report)
    print(f'Report: {output / "report.json"}',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audio',type=Path,required=True)
    parser.add_argument('--lyrics',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--language',default='ru')
    parser.add_argument('--asr-model',default='medium')
    parser.add_argument('--models',nargs='+',default=['bond005/wav2vec2-base-ru'])
    parser.add_argument('--contexts',nargs='+',type=float,default=[0,.3,.6])
    args=parser.parse_args()
    run(args.audio,args.lyrics,args.output,args.language,args.asr_model,args.models,args.contexts)
