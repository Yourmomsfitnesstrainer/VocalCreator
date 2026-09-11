from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


class ExternalCommandError(RuntimeError):
    pass


def _candidate_ffmpeg_paths() -> list[str]:
    candidates = [
        os.environ.get("KARAOKE_FFMPEG", ""),
        "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",
        "/usr/local/opt/ffmpeg-full/bin/ffmpeg",
        shutil.which("ffmpeg") or "",
    ]
    return list(dict.fromkeys(candidate for candidate in candidates if candidate))


def ffmpeg_has_ass(path: str) -> bool:
    result = subprocess.run(
        [path, "-hide_banner", "-filters"], capture_output=True, text=True, check=False
    )
    filters = result.stdout + result.stderr
    return any(line.split()[1:2] == ["ass"] for line in filters.splitlines() if line.strip())


def find_ffmpeg(require_ass: bool = False) -> str:
    for candidate in _candidate_ffmpeg_paths():
        if not Path(candidate).exists():
            continue
        if not require_ass or ffmpeg_has_ass(candidate):
            return candidate
    if require_ass:
        raise RuntimeError(
            "FFmpeg with the libass 'ass' filter was not found. On macOS run "
            "`brew install ffmpeg-full`, or set KARAOKE_FFMPEG to a compatible binary."
        )
    raise RuntimeError("FFmpeg was not found. Install it or set KARAOKE_FFMPEG.")


def find_ffprobe(ffmpeg_path: str | None = None) -> str:
    if ffmpeg_path:
        sibling = str(Path(ffmpeg_path).with_name("ffprobe"))
        if Path(sibling).exists():
            return sibling
    path = shutil.which("ffprobe")
    if path:
        return path
    raise RuntimeError("ffprobe was not found")


def run_command(command: list[str], *, cwd: Path | None = None) -> str:
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode:
        detail = "\n".join((result.stderr or result.stdout).strip().splitlines()[-12:])
        raise ExternalCommandError(f"Command failed ({result.returncode}): {' '.join(command[:3])}\n{detail}")

    return result.stdout + result.stderr


def probe_duration(path: Path, ffmpeg_path: str | None = None) -> float:
    ffprobe = find_ffprobe(ffmpeg_path)
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise ExternalCommandError(f"Could not read audio duration: {result.stderr.strip()}")
    return float(result.stdout.strip())


def prepare_audio(input_path: Path, output_path: Path) -> None:
    ffmpeg = find_ffmpeg()
    temp_path = output_path.with_suffix(".tmp.wav")
    command = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
        "-vn",
        "-af",
        "asetpts=N/SR/TB",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "44100",
        "-ac",
        "2",
        str(temp_path),
    ]
    run_command(command)
    temp_path.replace(output_path)



def probe_timeline(path: Path) -> dict:
    """Container timestamps and actual WAV sample counts are different observations."""
    import json
    import wave
    result = subprocess.run([find_ffprobe(find_ffmpeg()), '-v', 'error', '-show_streams',
                             '-show_format', '-of', 'json', str(path)],
                            capture_output=True, text=True, check=True)
    raw = json.loads(result.stdout)
    streams = [{key: stream.get(key) for key in ('codec_type', 'codec_name', 'start_time',
               'duration', 'sample_rate', 'time_base', 'channels', 'nb_frames')}
               for stream in raw['streams'] if stream.get('codec_type') in ('audio', 'video')]
    samples = None
    if path.suffix.lower() == '.wav':
        try:
            with wave.open(str(path), 'rb') as wav:
                samples = wav.getnframes()
        except (wave.Error, EOFError):
            pass
    return {'streams': streams, 'wav_samples': samples,
            'format_start': raw.get('format', {}).get('start_time'),
            'format_duration': raw.get('format', {}).get('duration')}


def _decode_for_timeline(path: Path):
    import numpy as np
    result = subprocess.run([find_ffmpeg(), '-v', 'error', '-i', str(path), '-vn',
                             '-ac', '1', '-ar', '2000', '-f', 'f32le', '-'],
                            capture_output=True, check=True)
    return np.frombuffer(result.stdout, '<f4')


def _compare_timeline(reference, candidate) -> dict:
    import numpy as np
    rate, radius = 2000, 1000
    length_error = (len(candidate)-len(reference))/rate
    observations = []
    # Check distinct regions; a duration match alone cannot detect a shift.
    for block in np.array_split(np.arange(len(reference)), 3):
        if len(block) < 600:
            continue
        size = min(4000, len(block))
        choices = np.linspace(int(block[0]), int(block[-1])-size+1, 7).astype(int)
        at = max(choices, key=lambda i: float(np.dot(reference[i:i+size], reference[i:i+size])))
        template = reference[at:at+size].astype(np.float64)
        template -= template.mean()
        energy = float(np.dot(template, template))
        if energy < 1e-8:
            continue
        lo, hi = max(0, at-radius), min(len(candidate), at+size+radius)
        search = candidate[lo:hi].astype(np.float64)
        if len(search) < size:
            continue
        dots = np.correlate(search, template, 'valid')
        sums = np.r_[0., np.cumsum(search)]
        squares = np.r_[0., np.cumsum(search*search)]
        variance = np.maximum(0., squares[size:]-squares[:-size]-(sums[size:]-sums[:-size])**2/size)
        correlations = dots / np.sqrt(np.maximum(energy*variance, 1e-30))
        peak = int(np.argmax(correlations))
        # A periodic calibration tone can have several equally plausible lags.
        # Such a peak cannot prove a shift and must stay inconclusive.
        alternatives = correlations.copy()
        alternatives[max(0, peak-8):min(len(alternatives), peak+9)] = -1
        second = float(np.max(alternatives)) if len(alternatives) > 17 else -1.
        margin = float(correlations[peak]) - second
        observations.append({'at_seconds': at/rate, 'lag_ms': (lo+peak-at)*1000/rate,
                             'correlation': round(float(correlations[peak]), 6),
                             'peak_margin': round(margin, 6),
                             'unambiguous': margin >= .02})
    confident = [item for item in observations if item['correlation'] >= .7 and item['unambiguous']]
    status = ('mismatch' if abs(length_error) > .04 or any(abs(item['lag_ms']) > 10 for item in confident)
              else 'verified' if len(confident) >= 2 and all(x['correlation'] >= .98 for x in observations) else 'inconclusive')
    return {'status': status, 'length_delta_seconds': length_error, 'observations': observations,
            'decoded_sample_rate': rate, 'reference_samples': len(reference), 'candidate_samples': len(candidate),
            'method': 'decoded_pcm_2000hz_normalized_correlation_three_regions'}


def timeline_report(original: Path, source: Path, vocals: Path, instrumental: Path | None,
                    *, video_path: Path | None = None) -> dict:
    from .timing_cache import file_sha256
    paths = {'original': original, 'source': source, 'vocals': vocals}
    if instrumental:
        paths['instrumental'] = instrumental
    if video_path:
        paths['mp4'] = video_path
    report = {'streams': {key: probe_timeline(path) for key, path in paths.items()}, 'checks': {}}
    try:
        source_pcm = _decode_for_timeline(source)
        report['checks']['preparation'] = _compare_timeline(_decode_for_timeline(original), source_pcm)
        if instrumental:
            import numpy as np
            vocal_pcm, music_pcm = _decode_for_timeline(vocals), _decode_for_timeline(instrumental)
            if len(vocal_pcm) != len(music_pcm):
                report['checks']['stem_reconstruction'] = {'status': 'mismatch', 'reason': 'stem_lengths_differ'}
            else:
                report['checks']['stem_reconstruction'] = _compare_timeline(source_pcm, vocal_pcm+music_pcm)
        else:
            report['checks']['vocals'] = ({'status': 'verified', 'method': 'identical_file_sha256'}
                if file_sha256(source) == file_sha256(vocals) else _compare_timeline(source_pcm, _decode_for_timeline(vocals)))
        if video_path:
            report['checks']['mp4_audio'] = _compare_timeline(source_pcm, _decode_for_timeline(video_path))
    except ImportError:
        report['checks']['pcm'] = {'status': 'inconclusive', 'reason': 'numpy_unavailable'}
    failures = [key for key, value in report['checks'].items() if value['status'] == 'mismatch']
    if failures:
        raise RuntimeError(f"Audio timeline mismatch: {', '.join(failures)}; {report['checks']}")
    return report
