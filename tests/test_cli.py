from karaoke_generator.cli import _apply_overrides, _parser
from karaoke_generator.config import load_config


def test_generate_arguments_are_parsed() -> None:
    args = _parser().parse_args(
        ["generate", "--audio", "song.wav", "--lyrics", "lyrics.txt", "--output", "result"]
    )
    assert args.command == "generate"
    assert args.audio.name == "song.wav"


def test_generate_timing_overrides_are_applied() -> None:
    args = _parser().parse_args(
        [
            "generate",
            "--audio",
            "song.wav",
            "--lyrics",
            "lyrics.txt",
            "--output",
            "result",
            "--no-vad",
            "--timing-offset-ms",
            "-325",
        ]
    )
    config = load_config()

    _apply_overrides(config, args)

    assert config["alignment"]["vad_filter"] is False
    assert config["karaoke"]["timing_offset_ms"] == -325


def test_render_offset_override_and_legacy_config(tmp_path):
    args = _parser().parse_args(['render', '--alignment', 'a.json', '--audio', 'a.wav',
                                '--output', 'a.mp4', '--timing-offset-ms', '125'])
    old_config = tmp_path / 'old.yaml'
    old_config.write_text('karaoke:\n  timing_offset_ms: -250\n')
    config = load_config(old_config)
    assert config['karaoke']['timing_offset_ms'] == -250
    _apply_overrides(config, args)
    assert config['karaoke']['timing_offset_ms'] == 125
    assert old_config.read_text().endswith('-250\n')
    assert load_config(tmp_path/'missing.yaml')['karaoke']['timing_offset_ms'] == 0


def test_main_render_with_explicit_config_and_offset(tmp_path, monkeypatch):
    from karaoke_generator import cli
    config = tmp_path/'custom.yaml'
    config.write_text('karaoke:\n  timing_offset_ms: -250\n')
    captured = {}
    def render(alignment, audio, output, settings, **kwargs):
        captured.update(settings)
        return {}
    monkeypatch.setattr(cli, 'rerender', render)
    cli.main(['--config', str(config), 'render', '--alignment', 'a.json', '--audio', 'a.mp3',
              '--output', 'a.mp4', '--timing-offset-ms', '75'])
    assert captured['karaoke']['timing_offset_ms'] == 75


def test_studio_backend_switches_to_compatible_default_model(monkeypatch):
    from karaoke_generator import cli

    captured = {}

    def run(*args, **kwargs):
        captured.update(args[3])
        return {"status": "complete", "artifacts": {}}

    monkeypatch.setattr(cli, "run_studio", run)
    cli.main(
        [
            "studio",
            "--audio",
            "song.wav",
            "--lyrics",
            "lyrics.txt",
            "--output",
            "result",
            "--separator-backend",
            "melband-roformer",
        ]
    )

    assert captured["separation"]["model"] == "melband-roformer-kim-vocals"
