---
title: "Локальный запуск проекта"
status: accepted
tags:
  - "onboarding"
---

## Prerequisites

- macOS или Linux с Python 3.10+.
- FFmpeg с поддержкой libass.
- Для тяжёлых ML-режимов нужны локальное место для весов и достаточно памяти.

## Steps

1. Установить FFmpeg и создать окружение:

```sh
brew install ffmpeg-full
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[ml,web,dev,separation,studio]'
```

2. Запустить локальный сервис:

```sh
karaoke-gen web --port 8080
```

3. Открыть `http://127.0.0.1:8080/` для вокальной студии. Прежний интерфейс Karaoke MP4 доступен по `/karaoke`.

Постоянные задания хранятся в `~/Library/Application Support/VocalCreator/jobs`; базовый каталог можно изменить через `VOCAL_CREATOR_DATA_DIR`.

## Verification

1. Проверить окружение:

```sh
karaoke-gen doctor
```

2. Запустить короткий smoke test:

```sh
./scripts/make_demo.sh
```

3. Для студии загрузить короткий аудиофайл и убедиться, что библиотека показывает сохранённый результат после перезагрузки страницы.