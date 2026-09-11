---
title: "Точки входа"
status: accepted
tags:
  - "architecture"
  - "entry-points"
---


## Overview
Проект имеет два локальных входа: CLI и HTTP-интерфейс; каждый сохраняет режимы студии и Karaoke MP4.

## Content
### CLI
- `karaoke-gen` — console script из `@pyproject.toml`; диспетчер в `@src/karaoke_generator/cli.py`.

### HTTP
- `GET /` — вокальная студия с библиотекой, микшером и общей временной шкалой.
- `GET /karaoke` — прежняя форма Karaoke MP4.
- `POST /api/studio/jobs` — принимает audio + UTF-8 TXT, валидирует их и создаёт постоянное фоновое задание.
- `GET /api/studio/jobs` — перечисляет сохранённые результаты после перезапуска.
- `GET /api/studio/jobs/{job_id}` — возвращает стадии, частичное состояние и allowlisted ссылки.
- `GET /api/studio/jobs/{job_id}/artifacts/{filename}` — отдаёт разрешённый studio artifact.
- `POST /api/jobs` — прежняя форма загрузки и индикатор выполнения.
- `POST /api/jobs` — создаёт фоновую генерацию и возвращает URL статуса.
- `GET /api/jobs/{job_id}` — возвращает состояние, процент, стадию, ошибку или ссылки.
- `POST /generate` — синхронный fallback для клиента без JavaScript.
- `GET /jobs/{job_id}/{filename}` — отдаёт разрешённый итоговый артефакт.
- Реализация HTTP-поверхности находится в `@src/karaoke_generator/web.py`.

## Examples
Локальный Web-вход запускается командой `karaoke-gen web --port 8080`.