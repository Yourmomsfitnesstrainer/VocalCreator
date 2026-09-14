from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .audio import find_ffmpeg
from .config import load_config
from .pitch import TorchCrepeAnalyzer
from .separation import DemucsSeparator, MelBandRoformerSeparator
from .studio import run_studio


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="karaoke-gen", description="Local vocal melody studio")
    parser.add_argument("--config", type=Path, help="YAML config path")
    subparsers = parser.add_subparsers(dest="command")

    studio_parser = subparsers.add_parser("studio", help="Analyze the full vocal melody")
    studio_parser.add_argument("--audio", type=Path, required=True)
    studio_parser.add_argument("--lyrics", type=Path, required=True)
    studio_parser.add_argument("--output", type=Path, required=True)
    studio_parser.add_argument("--input-type", choices=["mix", "vocal"], default="mix")
    studio_parser.add_argument("--language")
    studio_parser.add_argument("--backend", choices=["faster-whisper", "whisperx", "uniform"])
    studio_parser.add_argument("--model")
    studio_parser.add_argument("--pitch-backend", choices=["torchcrepe", "autocorrelation"], default=None)
    studio_parser.add_argument("--pitch-device")
    studio_parser.add_argument("--separator-backend", choices=["demucs", "melband-roformer"])
    studio_parser.add_argument("--separator-model")
    studio_parser.add_argument("--separator-device", help="Torch device for optional separators")

    subparsers.add_parser("doctor", help="Check local runtime dependencies")
    web_parser = subparsers.add_parser("web", help="Start the local web UI")
    web_parser.add_argument("--host", default="127.0.0.1")
    web_parser.add_argument("--port", type=int, default=8080)
    return parser


def _apply_overrides(config: dict, args: argparse.Namespace) -> None:
    for option in ("language", "backend", "model"):
        value = getattr(args, option, None)
        if value:
            config["alignment"][option] = value


def _doctor() -> int:
    print(f"Python: {sys.version.split()[0]}")
    print(f"Architecture: {__import__('platform').machine()}")
    try:
        ffmpeg = find_ffmpeg()
        print(f"FFmpeg: OK ({ffmpeg})")
    except RuntimeError as exc:
        print(f"FFmpeg: MISSING ({exc})")
    import importlib.util

    print(
        "faster-whisper: OK"
        if importlib.util.find_spec("faster_whisper")
        else "faster-whisper: optional dependency missing"
    )
    print(
        "WhisperX: OK"
        if importlib.util.find_spec("whisperx")
        else "WhisperX: optional dependency missing"
    )
    print(f"Demucs: {'OK' if DemucsSeparator.available() else 'optional dependency missing'}")
    print(
        "torchcrepe: OK"
        if TorchCrepeAnalyzer.available()
        else "torchcrepe: optional studio dependency missing"
    )
    print(
        "Mel-Band RoFormer: OK"
        if MelBandRoformerSeparator.available()
        else "Mel-Band RoFormer: optional comparison dependency missing"
    )
    return 0


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = _parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.command == "doctor":
        raise SystemExit(_doctor())
    config = load_config(args.config)
    if args.command == "studio":
        _apply_overrides(config, args)
        if args.pitch_backend:
            config["pitch"]["backend"] = args.pitch_backend
        if args.pitch_device:
            config["pitch"]["device"] = args.pitch_device
        if args.separator_backend:
            config["separation"]["backend"] = args.separator_backend
            if not args.separator_model:
                config["separation"]["model"] = (
                    "htdemucs"
                    if args.separator_backend == "demucs"
                    else "melband-roformer-kim-vocals"
                )
        if args.separator_model:
            config["separation"]["model"] = args.separator_model
        if args.separator_device:
            config["separation"]["device"] = args.separator_device
        manifest = run_studio(
            args.audio,
            args.lyrics,
            args.output,
            config,
            input_type=args.input_type,
        )
        print(f"Studio status: {manifest['status']}")
        for name, relative in manifest["artifacts"].items():
            print(f"  {name}: {args.output / relative}")
        if manifest["status"] == "failed":
            raise SystemExit(1)
        return
    if args.command == "web":
        try:
            import uvicorn
        except ImportError as exc:
            raise SystemExit("Install the web extra: pip install -e '.[web]'") from exc
        uvicorn.run("karaoke_generator.web:app", host=args.host, port=args.port)
        return
    parser.print_help()


if __name__ == "__main__":
    main()
