---
title: "BL-011 — подтверждение вхождений и итоговая расшифровка"
status: rejected
tags:
  - "alignment"
  - "quality"
  - "vocal-studio"
---

> Отменено пользователем 14 сентября 2026 года. Актуальное направление: @docs/manual-lyrics-editor-v1.md и @.archcore/vocal-studio/manual-lyrics-editor.prd.md (проект). Приоритет TXT, приблизительные времена, дополнительные повторы, ручные роли и сохранение исходных нот заменяют прежний строгий допуск. Ниже сохранён исторический текст; его требования не возобновлять.

---
title: "BL-011 — подтверждение вхождений и итоговая расшифровка"
status: draft
tags:
  - "alignment"
  - "quality"
  - "vocal-studio"
---

## Purpose & Scope
Проект контракта BL-011: допуск конкретных вхождений лирики и публикация подтверждённого результата. Потребители — интерфейс исполнения, TXT/JSON-экспорт и проекция BL-010. Обоснование и калибровка: @docs/confirmed-lyrics-v1.md, разделы 3–5, 8.
Изменение не применяется к принятой демке до отдельного поручения реализации. Исходные форматы сохраняются; слово «подтверждено» означает прохождение версии алгоритма, а не отсутствие модельных ошибок.
## Surface
Существующие точки: @src/karaoke_generator/alignment.py, @src/karaoke_generator/timing_cache.py, @src/karaoke_generator/syllables.py, @src/karaoke_generator/studio.py, @src/karaoke_generator/studio_web.py.
Предлагаемые новые модули: `lyric_confirmation.py` — чистое решение по свидетельствам; `confirmation_storage.py` — подготовка и публикация. Их ещё нет; размещение — `src/karaoke_generator/`.
| Артефакт/поле | Контракт schema 1 |
|---|---|
| `confirmation.json` | `schema_version=1`, `policy_version`, `profile_id`, `job_id`, `generation_id`, `input_fingerprint`, `source`, `occurrences[]`, `observations[]`, `diagnostics`, `provenance` |
| `source` | SHA-256 оригинального аудио, вокала, текста; длительность; язык; ссылка на акустические события |
| `occurrences[]` | Все канонические `word_id`, `line_index`, `word_index`, `char_start/end`, `source_text`; `decision`, `reason_codes[]`, `evidence_ids[]` |
| `decision` | `confirmed`, `unconfirmed`, `rejected`; неоднозначность и отсутствие данных описываются причинами |
| `observations[]` | Уникальный `evidence_id`, источник/канал, model/revision, ASR text, prompt policy, время, score components, ASR span IDs, `observation_group_id`, фактические acoustic features |
| `occurrences[].timing` | `start/end` в исходных секундах либо оба null; `quality=acoustic|approximate|unavailable`; источники границ |
| `occurrences[].support_intervals` | `support_id`, `word_id`, `start/end`, `kind=asr_word|ctc_word_support`, `evidence_ids[]`; неперекрывающиеся интервалы без недоказанных пауз |
| `performance.json` | `schema_version=1`, `job_id`, `generation_id`, `input_fingerprint`, `source`, `confirmation_key`, `words[]`, `support_intervals[]`, `text_parts[]`, `display_notes[]`, `word_note_links[]`, `part_note_links[]`, `diagnostics` |
| `performance.words[]` | Только confirmed: `word_id`, исходный текст/диапазон/строка, пригодные `start/end`, `timing_quality`, `support_ids[]`, ссылки на свидетельства |
| `transcript.txt` | UTF-8/LF; confirmed-слова по порядку источника, пунктуация слова сохранена, пустые строки без слов опущены |
`performance.support_intervals[]` содержит используемые интервалы confirmation с теми же support ID; `confirmation_key` равен cache_key публикуемого комплекта. job/generation/input fingerprint совпадают во всех JSON.
Причины: `no_independent_asr`, `text_mismatch`, `ambiguous_occurrence`, `asr_span_reused`, `acoustic_gate_failed`, `invalid_timing`, `compound_boundary_missing`, `unsupported_token`, `provenance_mismatch`, `profile_unavailable`, `model_unavailable`.
Производные комплекты: `confirmed-lyrics-v1/<cache_key>/`; рядом атомарный `index.json`. Исходные `alignment.json`, `melody.json`, `learning-v3` не меняют схему.
### Предлагаемый HTTP-контракт
Все маршруты ниже добавляются под `/api/studio/jobs/{job_id}`; существующий `GET /text` остаётся исходной лирикой.
| Запрос | Ответ |
|---|---|
| `GET /confirmation` | 200: `job_id`, `status`, `operation`, `published`, `reason`; отсутствующие объекты null |
| `POST /confirmation`, тело `{retry: false}` | 200 с готовым совместимым комплектом; 202 с operation_id при постановке/существующей операции |
| `POST /confirmation`, тело `{retry: true}` | Повтор failed/interrupted/partial с новым generation ID; при running возвращается та же операция |
| `GET /confirmation/{key}/{filename}` | 200 только для завершённого разрешённого артефакта; 404 неизвестный key/filename |
`operation={operation_id,generation_id,status}` описывает текущую/последнюю попытку; `published={generation_id,cache_key,input_fingerprint,status,counts,artifacts}` — последнюю целую совместимую публикацию. Ошибка новой попытки не стирает published. Верхний status отражает operation, иначе published, иначе not_prepared. counts содержит canonical/confirmed/unconfirmed/rejected; artifacts — разрешённые локальные URL.
Состояния: `not_prepared→queued→running→ready|partial|failed|interrupted`. `ready` означает все стадии закончены, включая корректно пустой итог; это не успешная музыкальная приёмка. `partial` публикует только целостный проверенный поднабор с диагностикой неполных стадий.
POST: 404 неизвестное задание; 409 нет обязательных исходников/задание ещё анализируется; 422 некорректное тело; 503 отсутствует пригодный профиль/модель до запуска. Ошибка после 202 отражается в состоянии операции. GET никогда не готовит данные.
## Normative Behavior
1. WHEN анализ начат явно, распознаватель MUST получить независимую гипотезу без текста лирики в prompt или hotwords.
2. WHEN распознаватель создаёт свидетельство, storage MUST записать фактические настройки и происхождение аудио.
3. WHEN проверяется старый ASR, validator MUST проверить отсутствие текстовых подсказок.
4. WHEN нормализатор сравнивает слова, нормализатор MUST сохранить исходный display-текст.
5. WHEN matcher выбирает слово, matcher MUST потребовать разрешённое полное нормализованное совпадение.
6. WHEN matcher обрабатывает повторы, matcher MUST сохранить монотонность конкретных word ID.
7. IF несколько вхождений неразличимы, THEN matcher MUST оставить кандидат неподтверждённым.
8. WHEN matcher использовал ASR-span, matcher MUST запретить его повторное использование для другого исполнения.
9. WHEN составное совпадение содержит несколько lyric-слов, validator MUST проверить отдельные акустические границы каждого слова.
10. WHEN CTC уточняет кандидат, validator MUST сохранить независимое подтверждение слова отдельно от качества времени.
11. IF есть только полный CTC или интерполяция, THEN validator MUST оставить слово неподтверждённым.
12. WHEN validator допускает слово, validator MUST проверить все пять условий раздела 5.4 пояснительной записки.
13. WHEN времена пригодны, publisher MUST включить только confirmed-вхождения в performance.words.
14. WHEN exporter создаёт TXT, exporter MUST использовать тот же набор word ID, что performance.words.
15. WHEN сохраняется решение, storage MUST сохранить причины и ссылки на использованные свидетельства.
16. WHEN пользователь открывает результат, API MUST только прочитать состояние проверки.
17. WHEN повторяется одинаковая подготовка, storage MUST переиспользовать текущую операцию или готовый совместимый комплект.
18. WHEN меняется текст, storage MUST пересчитать сопоставление без автоматического повторения независимого ASR.
19. WHEN меняется вокал, storage MUST инвалидировать зависимые свидетельства.
20. WHEN publisher завершает комплект, publisher MUST проверить SHA и ссылочную целостность перед переключением индекса.
21. WHEN пользователь изменил только времена, validator MUST сохранить правку без автоматического подтверждения слова.
22. WHEN профиль отсутствует, publisher MUST запретить публикацию экспериментальных решений как готового подтверждённого результата.
23. WHEN окна или проходы перекрываются, matcher MUST объединить наблюдения одного исполнения до сопоставления лирики.
24. WHEN observation_group использована, matcher MUST запретить её использование для другого повтора.
25. WHEN projector запрашивает поддержку распева, validator MUST проверить путь ctc_word_support из раздела 6 пояснительной записки.
## Constraints & Invariants
- Validator MUST использовать независимые признаки наличия слова, конкретного вхождения и качества границ. `aligned`, `confidence`, `≈` по отдельности недостаточны.
- Matcher MUST NOT допускать перевод, транслитерацию, fuzzy, стемминг и совпадение подстроки как полное совпадение.
- Нормализация MUST следовать версии из раздела 5.1; `е/ё` сохраняется как причина эквивалентности.
- Составной matcher MUST ограничиваться 1:1/1:2/2:1/2:2 внутри блока. Слияние разных лексических слов по одному равенству букв запрещено.
- Matcher MUST допускать изменение разбиения только по явному апострофу/дефису либо документированному правилу токенизации профиля.
- Validator MUST требовать `0≤start<end≤duration`, конечные числа и пригодный профиль времени; null-времена исключают слово из исполнения.
- Validator MUST NOT считать отсутствие F0 самостоятельным доказательством отсутствия слова.
- Источником display MUST оставаться диапазон канонического текста. Неподтверждённые промежутки не восстанавливаются в экспорте.
- Storage MUST сохранять каждое исходное вхождение и старые артефакты; решение привязано к паре text SHA + word ID.
- Независимый ASR-кеш MUST зависеть от аудио/model/options; кеш решений дополнительно зависит от текста, профиля, нормализации, matcher, evidence generation и нот.
- Validator MUST проверять уникальность support/evidence ID и разрешение всех ссылок внутри одной generation.
- Generation ID MUST оставаться метаданными публикации; семантический cache_key не зависит от случайного operation ID.
- Acoustic features MUST иметь определения, единицы и фактические значения; отсутствие обязательного признака запрещает соответствующий маршрут допуска.
- Profile MUST содержать численные параметры, версии и отчёт калибровки; score модели не объявляется вероятностью точности.
- Storage MUST хранить payload SHA вне проверяемого payload; index содержит хеши всех публикуемых файлов.
- API MUST ограничить filename списком `confirmation.json|performance.json|transcript.txt`; произвольные пути не допускаются.
- Неполный результат MUST иметь отдельный статус; transient-ошибка не становится бесконечно переиспользуемым ready-кешем.
- Общий текст и акустические файлы MUST оставаться локальными; внешняя отправка данных не входит в этот контракт.
## Failure Behavior
1. IF свидетельств недостаточно, THEN validator MUST вернуть unconfirmed с причиной.
2. IF происхождение противоречиво, THEN validator MUST отвергнуть свидетельство.
3. IF допущенных слов нет, THEN API MUST вернуть пустой итог с фактической диагностикой.
4. IF артефакт повреждён, THEN storage MUST сохранить его для диагностики вне текущего индекса.
5. IF подготовка прервана, THEN storage MUST сохранить последнюю целую генерацию и статус interrupted.
6. IF модель отказала после запуска, THEN API MUST отразить отказ стадии без выдуманного fallback.
7. IF профиль не соответствует модели, THEN validator MUST запретить подтверждение этим профилем.
8. IF старая генерация относится к другим входам, THEN API MUST исключить её из текущих artifacts.
## Conformance
Матрица A01–A05/A07–A09/A14–A17/A20–A23 и независимая акустическая приёмка — @docs/confirmed-lyrics-v1.md. Точки проверок: @tests/test_alignment.py, @tests/test_timing_cache.py, @tests/test_lyric_recovery.py, @tests/test_studio.py и новые `tests/test_lyric_confirmation.py`, `tests/test_confirmation_storage.py`.
Модельные результаты и цели не проверены в рамках подготовки этого ТЗ. Успешный HTTP 202 означает только постановку операции.
