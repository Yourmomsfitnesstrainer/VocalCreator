---
title: "Публичная поверхность"
status: accepted
tags:
  - "architecture"
  - "surface"
---

## Overview

Публичная поверхность демки 0.1: три CLI-команды и локальный HTTP API студии. Маркировка приложения 0.1/0.1.0 не меняет существующие пути `/v3` и форматы файлов.

## Content

### CLI

| Команда | Назначение |
|---|---|
| `studio` | Анализ вокала, слов, высоты, нот и пианино |
| `doctor` | Диагностика локальных зависимостей |
| `web` | FastAPI/Uvicorn, loopback по умолчанию |

Команды создания видео и неявная команда для `--audio` удалены. Аргументы: @src/karaoke_generator/cli.py.

### HTTP студии

Префикс таблицы: `/api/studio/jobs`. Реализация: @src/karaoke_generator/studio_web.py.

| Метод и путь | Результат |
|---|---|
| `POST /` | Multipart audio + обязательный UTF-8 TXT; HTTP 202 и status_url |
| `GET /` | Библиотека по сохранённым манифестам |
| `GET /{id}` | Состояние, стадии, allowlisted artifacts и готовые v3_modes |
| `GET /{id}/artifacts/{filename}` | Исходный артефакт, зарегистрированный в манифесте |
| `GET /{id}/text` | Полная каноническая лирика и вхождения слов, включая слова без времени |
| `POST /{id}/melody` | Единая полная мелодия с повторным использованием готового v3-кеша |
| `POST /{id}/v3/{mode}` | Готовый JSON частей/связей и ссылки на производное пианино |
| `GET /{id}/v3/{mode}/{cache_key}/{filename}` | learning.json или piano.wav текущего формата |
| `POST /{id}/tempo/{mode}/{rate}` | Совместно подготовленные дорожки выбранной скорости |
| `GET /{id}/tempo-files/{cache_key}/{filename}` | WAV из опубликованного комплекта темпа |
| `POST /{id}/learning/{mode}` | Историческое учебное представление для совместимости |
| `GET /{id}/learning/{mode}/{cache_key}/{filename}` | JSON/WAV исторического формата |

В первых двух строках `/` означает сам префикс без завершающего слеша. Новый клиент получает `/melody` и использует `mode=full` для tempo. Имена `light|medium|pro` сохраняются для совместимости старых файлов/API; tempo также принимает `original`. Скорости: 0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2. Ключ производных — 64 шестнадцатеричных символа.

Загрузка принимает MP3/WAV/FLAC/M4A/AAC/OGG, `input_type=mix|vocal`, `language=auto` либо буквенный код длиной 2–3, `pitch_backend=torchcrepe|autocorrelation`, `separator_backend=demucs|melband-roformer`. RTF преобразуется в TXT вне HTTP API.

HTTP 202 означает принятие задания; завершение определяется через status_url. Подготовка производных синхронно возвращает готовый комплект. Неверные параметры загрузки дают 400, отсутствующие обязательные поля — 422, неизвестный артефакт/режим — 404, неподходящее состояние — 409, ошибка подготовки — 500. Неудачная подготовка сохраняет прежний опубликованный комплект.

Исходный allowlist: vocals.wav, instrumental.wav, piano.wav, melody.json, alignment.json, studio.json, lyrics.txt, processed_lyrics.txt, lyrics_cleanup.json. Наличие файла на диске без регистрации в манифесте не открывает исходный download endpoint.

### Удалённая поверхность

Karaoke MP4, `/karaoke`, `/api/jobs`, `/generate`, `/jobs/{id}/{filename}` удалены. Старые пользовательские файлы на диске сохраняются.

## Examples

Браузер открывает `GET /`, затем читает библиотеку и `GET /api/studio/jobs/{id}/text`. При наличии исходного анализа `POST /api/studio/jobs/{id}/melody` подготавливает части без полного повторного анализа песни.
