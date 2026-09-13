<!-- archcore:start --> managed by `archcore init` — edit outside these markers
## Archcore — project context for this repo

This repo's architecture, decisions, rules, specs and patterns live in `.archcore/`,
reachable through the Archcore MCP tools. Consult them even on code you think you
know — a decision or rule may already constrain it.

- Touching this repo's real code or behavior → search first; read only what matches.
- A decision was made ("we'll use X", "from now on Y") → record it.
- A module / API / system has no doc — or a search comes back empty → capture it.
- Planning a feature or refactor → scope it against what's already decided.

A `.archcore/` may also mount read-only **global sources** — shared, org-wide
context not shown in the session-start list. `list_documents` / `search_documents`
surface them alongside local docs, tagged `source_kind: "global"`. When present,
treat them as defaults a local doc can override — never edit or relate to one.

The search is cheap — lean on it. Skip it only for turns this repo would have no
opinion on: syntax trivia, throwaway snippets, pure mechanics.
<!-- archcore:end -->

## Постоянный проверочный пример

После реализации ТЗ или исправления запускай локальный продукт на паре из `scripts/check_reference_song.py`, проверяй готовый результат в новой вкладке браузера, оставляй его открытым и сообщай пользователю ссылку с приглашением посмотреть. Пользователь 13 сентября 2026 года разрешил хранить предоставленные MP3/RTF/TXT Seekae в Git как пример: `references/seekae-test-and-recognise/`. Скрипт использует эти файлы из репозитория. Остальные пользовательские исходники и производные данные храни локально, вне Git, до отдельного поручения. Сохраняй исходные файлы и результаты анализа.

Бэклог проверочного корпуса: `docs/backlog.md`, BL-001 — три разных английских и три разных русских трека с точной лирикой. Seekae — EN-01; остаётся добавить два английских и три русских примера. Это задача на будущее, без автоматического подбора файлов или запуска моделей. При прямом поручении выключить локальный сервер оставляй его выключенным; упаковка примеров и документация допускают проверку `--check-files-only`.

## Принятая рабочая демка

13 сентября 2026 года пользователь закрепил принятый результат `bfd2665` как рабочую демку **0.1**. Актуальные ветки — `version-0.1` и `master`; прежние ветки `version-0.2` и `version-0.3` удалены по его поручению. Номера внутренних форматов `learning-v3`, `tempo-v3`, schema 3 и исторические названия ТЗ не являются версией приложения. До нового поручения сохраняй принятые поведение и интерфейс; незавершённые исследования не запускай автоматически.

## Целевая платформа

VocalCreator предназначен только для десктопа. Пользователь повторно подтвердил это 13 сентября 2026 года: мобильная версия не нужна вообще. Не трать время на мобильную разработку, адаптацию и мобильную приёмку; проверяй настольный браузер, включая низкие desktop-окна.
