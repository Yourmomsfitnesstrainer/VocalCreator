---
title: "Стек проекта"
status: accepted
tags:
  - "conventions"
  - "stack"
---


## Rule

1. При изменении Python-кода разработчик MUST сохранять совместимость с Python 3.10–3.13.
2. При обработке аудио и Karaoke-рендере pipeline MUST использовать FFmpeg с libass.
3. При изменении зависимостей разработчик MUST сохранять ML и Web-пакеты в опциональных группах @pyproject.toml.
4. При разработке интерфейса разработчик MUST проверять настольный браузер.

## Rationale

Демка 0.1 работает локально. Стек анализа: faster-whisper, WhisperX, Demucs, NumPy и torchcrepe; Mel-Band RoFormer — отдельная группа studio-roformer. Фактический backend/device записывается в результат.

Клиент использует HTML/CSS/JavaScript без отдельной frontend-сборки, Canvas и Web Audio. Темп готовится локальным Rubber Band 3+ в offline R3; `KARAOKE_RUBBERBAND` задаёт путь к бинарнику.

## Enforcement

@pyproject.toml определяет диапазоны и extras. Python проверяется pytest; транспорт — `node --test tests/studio_player.test.cjs`; @tests/browser_checks.js проверяет настоящий браузер. @scripts/check_reference_song.py запускает пользовательскую пару через локальный API.

## Examples

Дополнительная установка для студии: `pip install -e '.[ml,web,separation,studio]'`. Модели, кеши и пользовательские данные находятся вне Git; предоставленные файлы @references/seekae-test-and-recognise/ включены по прямому поручению пользователя как постоянный пример.
