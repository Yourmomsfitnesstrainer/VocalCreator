import pytest
from karaoke_generator.cli import _parser


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


@pytest.mark.parametrize("command", ["generate", "render"])
def test_video_commands_are_removed(command):
    with pytest.raises(SystemExit) as error:
        _parser().parse_args([command])
    assert error.value.code == 2
