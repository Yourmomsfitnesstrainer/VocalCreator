"""Preserve the two user-provided acceptance pairs outside the repository."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

REFERENCE_ROOT = Path.home() / "Library/Application Support/VocalCreator/references/syllable-score-v1"
SONGS = {
    "sputnik": {
        "title": "Sputnik — You Shaped Hole in My Heart", "language": "en",
        "audio": "sputnik you shaped hole in my heart.mp3",
        "lyrics": "sputnik you shaped hole in my heart.rtf",
        "audio_sha256": "a5c7e93eededb3d26b302a98bb2e53b3e4ace25afcbd935e38f5fd4d30031706",
        "lyrics_sha256": "5a377dbe38bfa12063696a2e266e8b9bd819bced13ae52872910c3faeb5d2f99",
    },
    "mujuice": {
        "title": "Mujuice — Журавли (Misha Mishenko Remix)", "language": "ru",
        "audio": "Mujuice_-_ZHuravli_Misha_Mishenko_Remix_(Bib.fm).mp3",
        "lyrics": "Журавли (Misha Mishenko Remix) Mujuice, Misha Mishenko.rtf",
        "audio_sha256": "d39e07c01aa0666a326cd074f5184b47a4557d38a6c83db17cb7f76e9706499f",
        "lyrics_sha256": "a8ea8cbbcb559731dfc771568528115f2f3191e0389731b51520caaee4dc9930",
    },
}


def prepare(root: Path = REFERENCE_ROOT) -> None:
    for name, song in SONGS.items():
        destination = root / name
        destination.mkdir(parents=True, exist_ok=True)
        for field, filename in (("audio", "audio.mp3"), ("lyrics", "lyrics-source.rtf")):
            source = Path.home() / "Downloads" / song[field]
            content = source.read_bytes()
            if hashlib.sha256(content).hexdigest() != song[field + "_sha256"]:
                raise ValueError(f"Source SHA mismatch: {source}")
            target = destination / filename
            if target.exists():
                if target.read_bytes() != content:
                    raise ValueError(f"Preserved copy differs; refusing to replace: {target}")
            else:
                shutil.copy2(source, target)
        text = subprocess.check_output(["textutil", "-convert", "txt", "-encoding", "UTF-8", "-stdout", str(destination / "lyrics-source.rtf")])
        text.decode("utf-8")
        target = destination / "lyrics.txt"
        if target.exists() and target.read_bytes() != text:
            raise ValueError(f"Extracted text differs; refusing to replace: {target}")
        if not target.exists():
            target.write_bytes(text)
        files = {}
        for filename in ("audio.mp3", "lyrics-source.rtf", "lyrics.txt"):
            content = (destination / filename).read_bytes()
            files[filename] = {"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
        manifest = {"title": song["title"], "language": song["language"], "canonical_word_count": len(text.decode().split()),
                    "files": files, "source": {"audio": song["audio"], "lyrics": song["lyrics"]},
                    "conversion": "macOS textutil -convert txt -encoding UTF-8 -stdout; original RTF unchanged",
                    "publication": "local-only; no permission to put media or lyrics in Git"}
        target = destination / "manifest.json"
        if target.exists() and json.loads(target.read_text()) != manifest:
            raise ValueError(f"Manifest differs; refusing to replace: {target}")
        if not target.exists():
            target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{name}: preserved and verified {manifest['canonical_word_count']} words at {destination}")


if __name__ == "__main__":
    prepare()
