---
title: "Локальный запуск проекта"
status: accepted
tags:
  - "onboarding"
---



Читатель и исполнитель — владелец локальной демки 0.1; задача — запустить студию и открыть результаты обеих постоянных песен.

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

Сначала выполните `PYTHONPATH=src .venv/bin/python scripts/prepare_syllable_references.py`. Сценарий проверяет MP3/RTF пользователя и сохраняет копии и TXT вне Git.

`PYTHONPATH=src .venv/bin/python scripts/check_reference_song.py --check-files-only` проверяет обе новые пары: Sputnik (198 слов, en) и Mujuice — Журавли (Misha Mishenko Remix) (118 слов, ru). Каталог источников: ~/Library/Application Support/VocalCreator/references/syllable-score-v1/.

`PYTHONPATH=src .venv/bin/python scripts/check_reference_song.py --url http://127.0.0.1:8080` проверяет обе песни через реальный локальный продукт, полную мелодию, скорости и слоговую стадию. Отдельные отчёты сохраняются вне Git; готовые результаты открываются в двух новых вкладках desktop-браузера. --song sputnik/mujuice служит отдельной диагностике, --reanalyze создаёт свежий анализ при изменении верхних стадий.

Поручение пользователя от 14 сентября 2026 года отменяет запуск Seekae/Abracadabra через анализ. Старые исходники, разрешённые в Git, и результаты сохраняются как история. Новые MP3/RTF/TXT и результаты в Git не добавляются.

Для отдельной браузерной проверки запустите `PYTHONPATH=src .venv/bin/python scripts/check_studio_browser.py --port 8082 --output output/browser-qa --data-dir output/browser-qa/data`. Откройте `http://127.0.0.1:8082/__checks`; кнопка запуска сохраняет измерения в browser-report.json. Затем откройте песню в новой вкладке и проверьте воспроизведение.

Проверка BL-004–BL-009 использовала `output/karaoke-reading-qa/data` на порту 8086; исходная библиотека остаётся отдельной. Исторические исходники Seekae и Abracadabra сохранены в references; текущие пары Sputnik/Mujuice и все производные файлы остаются локальными. Расширение набора до трёх английских и трёх русских песен записано в @docs/backlog.md.

## Common Issues

- Пустая библиотека: проверьте `VOCAL_CREATOR_DATA_DIR` и соответствующий jobs/result/studio.json.
- Порт занят: выберите другой порт; два процесса не запускаются на одном адресе.
- Темп недоступен: проверьте `rubberband --version`; `KARAOKE_RUBBERBAND` задаёт путь к бинарнику.
- Прерывание анализа: после перезапуска незавершённый manifest получает interrupted; сохранённые дорожки остаются.
- Долгая первая обработка: отсутствуют локальные веса или кеш. Установка зависимостей не подтверждает готовность моделей.
- MPS для Mel-Band RoFormer: на проверенной связке complex scatter не поддерживается, применяется CPU.
