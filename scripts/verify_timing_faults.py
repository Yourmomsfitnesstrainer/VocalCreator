#!/usr/bin/env python3
"""Targeted fault probes in a disposable source copy; never mutate the checkout."""
import argparse
import difflib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
output = root/'output/timing-verification/probes'
output.mkdir(parents=True, exist_ok=True)
probes = [
    ('low-score-accepted', 'alignment.py', 'or score < min_score:', 'or score < 0:',
     'tests/test_alignment.py::test_rejected_refinement_preserves_asr_and_reason'),
    ('input-normalization-skipped', 'alignment.py', 'if feature_extractor is None:', 'if True:',
     'tests/test_alignment.py::test_model_input_normalization_preserves_audio_and_absolute_window'),
    ('rounding-clamp-skipped', 'alignment.py', 'if lower - .000500001 <= start < lower:', 'if False:',
     'tests/test_alignment.py::test_refinement_accepts_quantized_window_edges_without_asr_jump[0]'),
    ('stanza-boundary-ignored', 'alignment.py',
     'lyric_block_ids=[line.block_index for line in document.lines for _ in line.words],',
     'lyric_block_ids=None,',
     'tests/test_alignment.py::test_compound_matching_cannot_pull_stanza_ending_into_next_repeat'),
]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--probe', choices=[p[0] for p in probes])
args = parser.parse_args()
if args.probe:
    probes = [p for p in probes if p[0] == args.probe]
reports = []
with tempfile.TemporaryDirectory(prefix='karaoke-timing-probes-') as folder:
    isolated = Path(folder)
    shutil.copytree(root/'src', isolated/'src')
    shutil.copytree(root/'tests', isolated/'tests')
    env = {**os.environ, 'PYTHONPATH': str(isolated/'src')}
    imported = subprocess.check_output([sys.executable, '-c', 'import karaoke_generator; print(karaoke_generator.__file__)'],
                                      cwd=isolated, env=env, text=True)
    assert str(isolated) in imported
    for name, filename, before, after, test in probes:
        path = isolated/'src/karaoke_generator'/filename
        original = path.read_text()
        assert original.count(before) == 1, name
        def run():
            # Disable stale timestamp-based bytecode while swapping same-size probes.
            shutil.rmtree(path.parent/'__pycache__', ignore_errors=True)
            return subprocess.run([sys.executable, '-m', 'pytest', '-q', test], cwd=isolated,
                                  env=env, capture_output=True, text=True)
        baseline = run()
        assert baseline.returncode == 0, baseline.stdout+baseline.stderr
        changed = original.replace(before, after)
        (output/f'{name}.diff').write_text(''.join(difflib.unified_diff(original.splitlines(True), changed.splitlines(True),
                                                                    fromfile=filename, tofile=filename)))
        try:
            path.write_text(changed)
            result = run()
        finally:
            path.write_text(original)
        restored = run()
        (output/f'{name}.log').write_text(result.stdout+result.stderr)
        detected = result.returncode == 1 and 'failed' in result.stdout and 'ERROR collecting' not in result.stdout
        reports.append({'probe': name, 'test': test, 'baseline_passed': baseline.returncode == 0,
                        'detected': detected, 'restored_passed': restored.returncode == 0})
        assert detected and restored.returncode == 0, reports[-1]
report_path = output/(f'{args.probe}.report.json' if args.probe else 'report.json')
report_path.write_text(json.dumps(reports, indent=2)+'\n')
print(json.dumps(reports, indent=2))
