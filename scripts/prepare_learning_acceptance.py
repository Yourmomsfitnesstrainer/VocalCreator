"""Prepare eight private listening candidates and blank independent annotations.

This selects material, not reference notes. The user must annotate before
comparing candidates; no automatic result becomes a human reference.
"""
from __future__ import annotations

import argparse
import json
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def crop(source: Path, destination: Path, start: float, end: float):
    with wave.open(str(source), "rb") as reader:
        rate = reader.getframerate()
        reader.setpos(round(start * rate))
        data = reader.readframes(round((end - start) * rate))
        with wave.open(str(destination), "wb") as writer:
            writer.setparams(reader.getparams())
            writer.writeframes(data)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("result", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "output/v2-qa/listening")
    args = parser.parse_args()
    from karaoke_generator.config import load_config
    from karaoke_generator.learning_storage import prepare_learning
    from karaoke_generator.timing_cache import file_sha256
    args.output.mkdir(parents=True, exist_ok=True)
    melody = json.loads((args.result / "melody.json").read_text())
    notes, duration = melody["notes"], melody["timeline"]["duration"]
    if len(notes) < 8 or duration < 48:
        raise SystemExit("Choose a result with at least eight notes and 48 seconds of material")
    modes = {mode: prepare_learning(args.result, mode, piano_options=load_config().get("piano"))[1] / "piano.wav"
             for mode in ("light", "medium", "pro")}
    starts = []
    for fraction in (.03, .15, .27, .39, .51, .63, .75, .90):
        start = max(0, min(duration - 6, notes[round((len(notes) - 1) * fraction)]["start"] - 1))
        starts.append(round(start, 3))
    manifest = {"source_melody_sha256": file_sha256(args.result / "melody.json"),
                "source_vocals_sha256": file_sha256(args.result / "vocals.wav"),
                "selection": "Automatically selected candidates from the user's saved song, not human reference labels",
                "human_acoustic_acceptance": "pending", "fragments": []}
    rows = []
    for index, start in enumerate(starts):
        identifier = f"fragment-{index + 1:02d}"
        end = min(duration, start + 6)
        artifacts = {}
        for kind, path in {"vocal": args.result / "vocals.wav", **modes}.items():
            filename = f"{identifier}-{kind}.wav"
            crop(path, args.output / filename, start, end)
            artifacts[kind] = filename
        record = {"id": identifier, "source_start": start, "source_end": end,
                  "split": "calibration" if index < 4 else "holdout", "artifacts": artifacts,
                  "human_reference_notes": None, "human_word_timing": None,
                  "phenomena": [], "easier_to_follow": None, "melody_recognizable": None,
                  "false_jumps": None, "comment": ""}
        manifest["fragments"].append(record)
        audio = ''.join(f'<label>{label}<audio controls preload="none" src="{artifacts[kind]}"></audio></label>'
                        for kind, label in (("light", "Light"), ("medium", "Medium"), ("pro", "Pro")))
        rows.append(f'<section><h2>{identifier} · {start:.3f}–{end:.3f} с · {record["split"]}</h2>'
                    f'<label>Исходный вокал<audio controls preload="none" src="{artifacts["vocal"]}"></audio></label>'
                    f'<label>Независимые границы и высоты; паузы и атаки<textarea data-id="{identifier}" placeholder="Времена относительно начала этого фрагмента. Не копируйте автоматические ноты."></textarea></label>'
                    f'<details><summary>Сравнить после независимой разметки</summary>{audio}'
                    '<p>Легче ли следовать нотам? Узнаётся ли мелодия? Есть ли ложные скачки?</p></details></section>')
    (args.output / "annotations.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    html = '''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>VocalCreator — музыкальная приёмка v2</title><style>body{font:16px/1.5 system-ui;background:#0d1015;color:#f4f6fb;max-width:900px;margin:auto;padding:24px}section{border-top:1px solid #424958;padding:20px 0}h2{font-size:18px}label{display:block;margin:12px 0}audio,textarea{display:block;width:100%;margin-top:8px}textarea{min-height:90px;background:#191e28;color:#f4f6fb}summary,button{cursor:pointer;padding:12px}button{font:inherit}</style>
<h1>Музыкальная приёмка v2</h1><p>Это восемь кандидатов из вашей песни. Их музыкальный тип и правильные ноты ещё не подтверждены. Сначала разметьте исходный вокал, затем сравните режимы. Первые четыре фрагмента предназначены для калибровки, последние четыре — для отдельной проверки.</p>
<p>Для полного ТЗ проверьте покрытие: вибрато, портаменто, мелизм, повторная атака, короткое и длинное слово, внутренняя пауза, неточная привязка. Если явления нет в этих кандидатах, выберите другой участок; отсутствие нельзя засчитывать как проверку.</p>
''' + ''.join(rows) + '''<button id="save">Скачать введённую разметку</button><script>document.querySelector('#save').onclick=()=>{const data=[...document.querySelectorAll('textarea')].map(n=>({id:n.dataset.id,human_annotation:n.value}));const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));a.download='human-annotations-v2.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);};</script></html>'''
    (args.output / "index.html").write_text(html)
    print(json.dumps({"output": str(args.output), "fragments": len(starts), "source_starts": starts,
                      "human_labels": "not provided; left blank"}))


if __name__ == "__main__":
    main()
