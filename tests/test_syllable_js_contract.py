"""The shipped JS editor output must satisfy the real Python save validator."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from karaoke_generator.syllable_score import validate_score


def test_editor_transactions_roundtrip_server_validator():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for the cross-language editor contract')
    path = Path(__file__).parent / 'syllable_fixture.cjs'
    result = subprocess.run([node, str(path)], check=True, capture_output=True, text=True)
    payload = json.loads(result.stdout)
    original = payload['snapshots']['original']
    for label, score in payload['snapshots'].items():
        try:
            validate_score(score, payload['notes'], expected_job_id=original['job_id'],
                           expected_source=original['source'], expected_base_analysis_key=original['base_analysis_key'])
        except ValueError as error:
            pytest.fail(f'{label}: {error}')
