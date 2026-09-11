---
title: "Публичная поверхность"
status: accepted
tags:
  - "architecture"
  - "surface"
---


## Overview
Публичная поверхность состоит из пяти CLI-команд и двух совместимых локальных HTTP API.

## Content
### CLI
- `generate` — запускает прежний karaoke pipeline из аудио и точного TXT.
- `studio` — запускает audio-only анализ вокала, нот, слов, пианино и доступного минуса.
- `render` — повторно рендерит MP4 из отредактированного `alignment.json`.
- `doctor` — проверяет Python, архитектуру, FFmpeg/libass, faster-whisper, WhisperX и Demucs.
- `web` — запускает локальный FastAPI UI через Uvicorn.
- Короткая форма с `--audio` без подкоманды маршрутизируется в `generate`; определения находятся в `@src/karaoke_generator/cli.py`.

### HTTP
- `GET /` отдаёт студию; `GET /karaoke` сохраняет прежний Web UI.
- `POST /api/studio/jobs` создаёт постоянное задание студии.
- `GET /api/studio/jobs` и `GET /api/studio/jobs/{job_id}` возвращают библиотеку и состояние.
- `GET /api/studio/jobs/{job_id}/artifacts/{filename}` отдаёт только артефакты из allowlist манифеста.
- `POST /api/jobs` создаёт фоновую генерацию.
- `GET /api/jobs/{job_id}` публикует состояние и процент выполнения.
- `GET /jobs/{job_id}/{filename}` отдаёт MP4, ASS или alignment JSON.
- `POST /generate` сохраняет синхронный HTML fallback.
- Определения находятся в `@src/karaoke_generator/web.py`.

## Examples
CLI-клиент использует `karaoke-gen generate`; браузерный клиент начинает с `GET /`.