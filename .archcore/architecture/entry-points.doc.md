---
title: "Точки входа"
status: accepted
tags:
  - "architecture"
  - "entry-points"
---


## Overview

Демка 0.1 имеет CLI и локальный HTTP-вход. Основная страница — десктопная студия; прежний Karaoke MP4 сохраняет собственный маршрут.

## Content

| Вход | Файл | Назначение |
|---|---|---|
| `karaoke-gen` | @pyproject.toml, @src/karaoke_generator/cli.py | Команды `generate/studio/render/doctor/web` |
| `GET /` | @src/karaoke_generator/web.py | @src/karaoke_generator/static/studio.html, studio.css, studio.js |
| `GET /karaoke` | @src/karaoke_generator/web.py | Прежняя форма Karaoke MP4 |
| `/api/studio` | @src/karaoke_generator/studio_web.py | Библиотека, анализ, исходные и производные артефакты |
| `/api/jobs`, `/generate` | @src/karaoke_generator/web.py | Фоновые задания и синхронный fallback Karaoke |
| Браузерные проверки | @scripts/check_studio_browser.py, @tests/browser_checks.js | Изолированная страница `/__checks` и локальный отчёт |
| Постоянная песня | @scripts/check_reference_song.py | MP3/RTF, все 305 слов, три режима и темп через работающий API |

`/__checks` существует только в проверочном runner, обычный `karaoke-gen web` его не добавляет. Источник версии OpenAPI — `__version__` в @src/karaoke_generator/__init__.py; версия пакета задаётся @pyproject.toml.

Запуск `python -m karaoke_generator` проходит через @src/karaoke_generator/__main__.py к тому же CLI-диспетчеру.

## Examples

Обычный запуск: `karaoke-gen web --host 127.0.0.1 --port 8080`. Сохранённый результат открывается через `/?job={id}&t=104.5&zoom=8&height=3`; параметры задают задание, секунды оригинала и масштабы.
