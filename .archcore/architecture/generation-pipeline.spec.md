---
title: "Контракт генерационного pipeline"
status: rejected
tags:
  - "pipeline"
  - "spec"
---

Исторический контракт. Karaoke MP4 удалён по BL-004 14 сентября 2026 года; требования ниже больше не действуют. Общие аудио и alignment сохранены в студии.

## Purpose & Scope
Спецификация определяет оркестрацию полного локального пути `original MP3 + lyrics.txt → artifacts`. Её потребляют CLI и локальный Web UI.
За пределами: внутренние алгоритмы alignment, ASS и FFmpeg-фильтры. Кеш стадий реализует @src/karaoke_generator/timing_cache.py.

## Surface
- Полная генерация: `generate` в `@src/karaoke_generator/pipeline.py`.
- Повторный рендер: `rerender` в `@src/karaoke_generator/pipeline.py`.
- Конфигурационные секции: `alignment`, `separation`, `video`, `karaoke`, `output` в `@config.yaml`.

## Normative Behavior
1. WHEN вызывается `generate`, pipeline MUST выполнить подготовку, separation, alignment, ASS и MP4 в пяти наблюдаемых стадиях.
2. WHEN пользовательский TXT прочитан, pipeline MUST записать очищенный `processed_lyrics.txt` и отчёт `lyrics_cleanup.json`.
3. WHEN cleanup исключает строки, pipeline MUST записать их число в лог.
4. WHEN ключ входа совпадает, pipeline MAY использовать сохранённые audio, separation и alignment artifacts.
5. IF Demucs недоступен в режиме `auto`, THEN pipeline MUST продолжить с исходным аудио.
6. WHEN запрошен instrumental без готового stem, pipeline MUST рендерить исходное аудио и записать фактический режим.
7. WHEN `rerender` вызывается, pipeline MUST пропустить ML и создать ASS вместе с MP4.
8. WHEN alignment завершён, pipeline MUST записать в лог и metadata числа распознанных, напрямую сопоставленных и интерполированных слов.
9. WHEN создаётся ASS, pipeline MUST передать `timing_offset_ms` из karaoke-конфигурации.
10. WHEN меняется модель уточнения, pipeline MUST повторно использовать совпадающий ASR-кеш.
11. WHEN совпадает ключ сопоставления, pipeline MUST сохранить канонические ручные правки alignment.json.
12. WHEN генерация завершается, pipeline MUST записать фактические настройки, хеши входов, версии и длительности стадий.
13. WHEN вызывается rerender, pipeline MUST записать применённый offset в лог и отдельный `.render.json`.

## Constraints & Invariants
- Ключ ASR включает SHA-256 фактического WAV, текст prompt, язык, ASR/VAD-настройки, версии алгоритма и зависимостей, доступную ревизию модели.
- Ключ уточнения включает ключ ASR, модель, контекст, порог score, версии уточнителя и зависимостей.
- Ключ сопоставления включает ключ времён, версию mapping, SHA-256 TXT и порог сходства.
- Оформление и offset исключены из ML-ключей.
- Каждая стадия кеша публикуется атомарно до запуска последующей стадии.
- The pipeline MUST публиковать JSON через временный файл и атомарную замену.
- The result MUST включать video, subtitles, alignment, processed lyrics, cleanup report и vocals.
- The metadata MUST различать requested и actual audio mode.
- The metadata MUST содержать `alignment_quality` из опубликованного `alignment.json`.
- The alignment source MUST быть вокальным stem после успешного separation.

## Failure Behavior
1. IF входной audio отсутствует, THEN pipeline MUST выбросить `FileNotFoundError`.
2. IF входной lyrics отсутствует, THEN pipeline MUST выбросить `FileNotFoundError`.
3. IF separation включён строго и завершается ошибкой, THEN pipeline MUST передать ошибку вызывающему коду.
4. IF любая стадия завершается ошибкой, THEN stage logger MUST записать номер, название и длительность.

## Conformance
Реализация соответствует контракту, когда сохраняет порядок стадий, артефакты, ключи кеша и правила деградации. Проверка выполняется unit-тестами и smoke-сценариями из `@README.md`.
