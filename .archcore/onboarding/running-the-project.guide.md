---
title: "Локальный запуск проекта"
status: accepted
tags:
  - "onboarding"
---

Читатель и исполнитель — владелец локальной демки 0.1; задача — запустить студию и повторно открыть проверенную песню.

## Prerequisites

Python 3.10–3.13, FFmpeg, локальное место для весов. На проверенном Mac используется Python 3.12. Для изменения темпа требуется Rubber Band 3+; без него доступно обычное воспроизведение.

## Steps

1. Откройте терминал в корне репозитория.
2. Установите системные зависимости на macOS:

```sh
brew install ffmpeg rubberband
```

3. Создайте окружение:

```sh
python3 -m venv .venv
```

4. Установите зависимости:

```sh
.venv/bin/python -m pip install -e '.[ml,web,dev,separation,studio]'
```

5. Запустите локальную студию:

```sh
PYTHONPATH=src .venv/bin/python -m karaoke_generator.cli web --host 127.0.0.1 --port 8080
```

6. Откройте `http://127.0.0.1:8080/` в настольном браузере.
7. Выберите результат библиотеки либо загрузите аудио и точный UTF-8 TXT.

Задания сохраняются в `~/Library/Application Support/VocalCreator/jobs`. `VOCAL_CREATOR_DATA_DIR` меняет базовый каталог, приложение добавляет `jobs`. Другой каталог означает другую библиотеку. Исходный RTF преобразуется в TXT через `textutil -convert txt`.

## Verification

`PYTHONPATH=src .venv/bin/python scripts/check_reference_song.py --check-files-only` проверяет целостность включённых в Git файлов без сервера и моделей. Downloads и textutil для этого прогона не нужны; UTF-8 TXT уже сохранён рядом с исходным RTF.

`PYTHONPATH=src .venv/bin/python scripts/check_reference_song.py --url http://127.0.0.1:8080` сверяет MP3, оригинальный RTF и готовый TXT из @references/seekae-test-and-recognise/, ждёт завершения анализа при отсутствии готового задания, проверяет 305 слов и проверяет полную мелодию и выбранные скорости. При изменении исходного анализа применяется `--reanalyze`; смена номера версии его не требует.

Для отдельной браузерной проверки запустите `PYTHONPATH=src .venv/bin/python scripts/check_studio_browser.py --port 8082 --output output/browser-qa --data-dir output/browser-qa/data`. Откройте `http://127.0.0.1:8082/__checks`; кнопка запуска сохраняет измерения в browser-report.json. Затем откройте песню в новой вкладке и проверьте воспроизведение.

Проверка BL-004–BL-009 использовала `output/karaoke-reading-qa/data` на порту 8086; исходная библиотека остаётся отдельной. Исходные файлы Seekae разрешены пользователем к хранению в Git; остальные аудио, текст, кеши и отчёты output остаются локальными. Расширение набора до трёх английских и трёх русских песен записано в @docs/backlog.md.

## Common Issues

- Пустая библиотека: проверьте `VOCAL_CREATOR_DATA_DIR` и соответствующий jobs/result/studio.json.
- Порт занят: выберите другой порт; два процесса не запускаются на одном адресе.
- Темп недоступен: проверьте `rubberband --version`; `KARAOKE_RUBBERBAND` задаёт путь к бинарнику.
- Прерывание анализа: после перезапуска незавершённый manifest получает interrupted; сохранённые дорожки остаются.
- Долгая первая обработка: отсутствуют локальные веса или кеш. Установка зависимостей не подтверждает готовность моделей.
- MPS для Mel-Band RoFormer: на проверенной связке complex scatter не поддерживается, применяется CPU.
