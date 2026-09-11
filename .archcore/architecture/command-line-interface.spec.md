---
title: "Контракт командной строки"
status: draft
tags:
  - "cli"
  - "spec"
---

## Purpose & Scope
Спецификация определяет пользовательский контракт `karaoke-gen`. Его потребляют локальные пользователи, shell-скрипты и Docker.
За пределами: алгоритмы генерации и HTML локального Web UI.

## Surface
- Console script: `@pyproject.toml`.
- Парсер, overrides и dispatch: `@src/karaoke_generator/cli.py`.
- Команды: `generate`, `studio`, `render`, `doctor`, `web`.

## Normative Behavior
1. WHEN argv содержит `--audio` без подкоманды, CLI MUST вставить `generate`.
2. WHEN вызывается `generate`, CLI MUST требовать пути audio, lyrics и output.
3. WHEN переданы overrides, CLI MUST применить их поверх YAML-конфигурации.
4. WHEN передан `--vad` или `--no-vad`, CLI MUST включить или отключить VAD.
5. WHEN generate или render получает `--timing-offset-ms`, CLI MUST применить override поверх YAML.
6. WHEN вызывается `render`, CLI MUST создать видео без запуска alignment backend.
7. WHEN вызывается `doctor`, CLI MUST вывести состояние FFmpeg/libass, faster-whisper, WhisperX, Demucs, torchcrepe и Mel-Band RoFormer.
8. WHEN вызывается `web`, CLI MUST запускать Uvicorn на выбранных host и port.
9. WHEN генерация завершена, CLI MUST вывести имена и пути созданных artifacts.
10. WHEN вызывается `studio`, CLI MUST завершить audio-only анализ без обязательного MP4 и сохранить `studio.json`.
11. WHEN `studio --input-type mix` не получает настоящий vocal stem, `karaoke-gen studio` MUST вернуть ненулевой код завершения и явное сообщение об ошибке выделения.
12. В этом случае `karaoke-gen studio` MUST NOT публиковать исходный mix как `vocals.wav`.
13. WHEN меняется `--separator-backend` без явного `--separator-model`, CLI MUST выбрать совместимую модель backend.
14. WHEN указан `--separator-device`, CLI MUST передать фактический Torch device опциональному separator.

## Constraints & Invariants
- The CLI MUST принимать audio mode только из `original` и `instrumental`.
- The CLI MUST принимать alignment backend только из `faster-whisper`, `whisperx` и `uniform`.
- Studio pitch backend MUST быть `torchcrepe` или диагностическим `autocorrelation`.
- Studio separator backend MUST быть `demucs` или `melband-roformer`.
- Timing offset MUST находиться в диапазоне −1000…+1000 мс.
- Default новых запусков равен 0 мс. Явные значения в существующем YAML сохраняются.
- `--config` перед подкомандой render не переключает CLI в generate.
- The resolution MUST иметь форму `<width>x<height>`.
- The default web binding MUST быть `127.0.0.1:8080`.

## Failure Behavior
1. IF resolution не разбирается, THEN CLI MUST выдать ошибку с сообщением о формате `1920x1080`.
2. IF timing offset выходит за диапазон, THEN CLI MUST выдать ошибку с понятным сообщением.
3. IF web extra отсутствует, THEN CLI MUST вывести команду установки `.[web]`.
4. IF обязательный аргумент отсутствует, THEN argparse MUST завершить команду с ненулевым кодом.

## Conformance
Проверка парсинга и overrides находится в `@tests/test_cli.py`.