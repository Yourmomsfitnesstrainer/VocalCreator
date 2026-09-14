---
title: "Контракт командной строки"
status: draft
tags:
  - "cli"
  - "spec"
---

## Purpose & Scope

Пользовательский контракт karaoke-gen для локального анализа и HTTP-сервера. Создание Karaoke MP4 удалено по BL-004.

## Surface

@pyproject.toml: console script. @src/karaoke_generator/cli.py: parser, overrides и dispatch.
Команды: studio, doctor, web. Python module использует тот же диспетчер.

## Normative Behavior

1. При вызове studio CLI MUST требовать audio, lyrics и output.
2. При overrides CLI MUST применить язык, alignment backend и модель поверх YAML.
3. При studio CLI MUST сохранить аудиорезультаты и studio.json.
4. При неудачном выделении голоса CLI MUST завершиться ошибкой, сохранив диагностику.
5. При ошибке separator CLI MUST NOT выдавать исходный mix за vocals.wav.
6. При смене separator backend без модели CLI MUST выбрать совместимую модель.
7. При separator-device CLI MUST передать выбранный Torch device.
8. При doctor CLI MUST вывести состояние FFmpeg и опциональных ML-зависимостей.
9. При web CLI MUST запустить Uvicorn на выбранных host и port.
10. При generate или render CLI MUST вернуть ошибку неизвестной команды.
11. При отсутствии команды CLI MUST показать справку без генерации.

## Constraints & Invariants

Alignment backend: faster-whisper, whisperx, uniform. Pitch: torchcrepe или диагностический autocorrelation. Separator: demucs или melband-roformer. Input type: mix или vocal.
Default web binding: 127.0.0.1:8080. Libass, размеры видео, видеофон, ASS и timing-offset видео больше не являются CLI-параметрами.
Имя karaoke-gen сохранено для совместимости запуска.

## Failure Behavior

1. При неизвестной команде или отсутствии обязательного аргумента argparse MUST вернуть ненулевой код.
2. При отсутствии web extra CLI MUST вывести команду установки.
3. При статусе failed у анализа CLI MUST завершиться ненулевым кодом.

## Conformance

@tests/test_cli.py проверяет выбор backend и отсутствие удалённых команд. Python-регрессии студии проверяют сохранение аудиорезультатов.
