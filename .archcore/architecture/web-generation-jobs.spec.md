---
title: "Контракт фоновой Web-генерации"
status: rejected
tags:
  - "jobs"
  - "progress"
  - "spec"
  - "web"
---

Исторический контракт. Karaoke MP4 удалён по BL-004 14 сентября 2026 года; требования ниже больше не действуют. Общие аудио и alignment сохранены в студии.

## Purpose & Scope
Спецификация задаёт контракт фоновой Web-генерации karaoke. Браузерный UI и локальные HTTP-клиенты зависят от состояний job, процентов, настроек alignment и ссылок.
За пределами: сохранение очереди между перезапусками, несколько процессов Uvicorn и удалённый хостинг.

## Surface
- HTTP-приложение, хранилище job и HTML: `@src/karaoke_generator/web.py`.
- Отчёт стадий pipeline: `@src/karaoke_generator/pipeline.py`.
- Проверки Web-контракта: `@tests/test_web.py`.
- Точки входа: `GET /`, `POST /api/jobs`, `GET /api/jobs/{job_id}`, `POST /generate`, `GET /jobs/{job_id}/{filename}`.
- Состояния job: `queued`, `running`, `complete`, `failed`.

## Normative Behavior
1. WHEN `POST /api/jobs` получает допустимые файлы и настройки, Web API MUST вернуть `202` с `job_id` и `status_url`.
2. WHEN Web API принимает job, Web API MUST запустить генерацию после формирования HTTP-ответа.
3. WHILE job выполняется, Web API MUST публиковать монотонный целый `progress` от 0 до 100.
4. WHEN job завершается, Web API MUST установить `complete`, `progress=100` и ссылки на MP4, ASS, alignment, processed lyrics и cleanup report.
5. WHEN браузер отправляет форму, Web UI MUST создать job без полной перезагрузки страницы и блокировать повторный запуск до результата.
6. WHEN job завершается, Web UI MUST показать ссылки на артефакты и встроенное видео.
7. Web UI MUST позволять выбрать `whisperx` или `faster-whisper`, модель `small`, `medium` или `large-v3`, состояние VAD и timing offset.
8. WHEN пользователь вводит `English` или `Russian`, Web API MUST преобразовать значение в `en` или `ru`.
9. WHILE индикатор видим, Web UI MUST обновлять `aria-valuenow` текущим процентом.
10. WHEN результат готов, Web UI MUST назвать долю текстовых совпадений «Сопоставление текста».
11. WHEN результат готов, Web UI MUST показать модели ASR/уточнения, применённый offset и числа источников тайминга.

## Constraints & Invariants
- `progress` MUST оставаться в диапазоне 0–100 и не уменьшаться.
- Timing offset MUST находиться в диапазоне −1000…+1000 мс; отрицательное значение означает раннюю подсветку.
- Default формы и HTTP-параметра равен 0 мс.
- Без эталонной разметки Web показывает отсутствие измеренной точности времени.
- Web API MUST защищать чтение и изменение `JOBS` одним lock.
- Web API MUST хранить job в памяти одного локального Uvicorn-процесса и сохранять файлы под системным временным каталогом.
- Endpoint артефактов MUST разрешать только `karaoke.mp4`, `karaoke.ass`, `alignment.json`, `processed_lyrics.txt` и `lyrics_cleanup.json`.
- Синхронный `POST /generate` MUST оставаться fallback для клиента без JavaScript.

## Failure Behavior
1. IF формат аудио, режим, язык, backend, model или offset недопустим, THEN Web API MUST вернуть `400` до постановки job в очередь.
2. IF `job_id` отсутствует или имеет неверный формат, THEN Web API MUST вернуть `404`.
3. IF pipeline завершается исключением, THEN Web API MUST установить `failed` и сохранить текст ошибки.
4. WHEN Web UI получает `failed`, Web UI MUST показать ошибку и снова включить кнопку.
5. IF запрошенный артефакт отсутствует, THEN Web API MUST вернуть `404`.
6. WHEN процесс перезапускается, Web API MAY потерять состояния job.

## Conformance
Проверка включает `@tests/test_web.py` и live HTTP-сценарий с наблюдаемыми границами прогресса.
