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

## Текущие проверочные примеры

По прямому поручению 14 сентября 2026 года после реализации слоговой партии проверяй ОБЕ новые пары: Sputnik — You Shaped Hole in My Heart (en) и Mujuice — Журавли (Misha Mishenko Remix) (ru). Запускай `scripts/prepare_syllable_references.py`, затем `scripts/check_reference_song.py`. Открой каждый результат в отдельной новой вкладке desktop-браузера, оставь обе вкладки и сервер доступными пользователю и сообщи обе ссылки.

Seekae и СБПЧ — Abracadabra больше не запускай через анализ: пользователь заменил эти сложные песни. Старые исходники, результаты и историю сохраняй. Прежнее разрешение на хранение старых examples в Git не распространяется на новые песни. Новые MP3/RTF, UTF-8 TXT, локальные манифесты и результаты хранятся вне Git в `~/Library/Application Support/VocalCreator/`. Оригинальные RTF не переписываются. Изменённая стадия слогов выполняется заново; повторно используются только проверенно неизменённые стадии с происхождением кешей.

Поручение разрешает полный T1–T7 из `docs/syllable-score-v1.md`; старое ограничение «только ТЗ» снято. Бэклог расширения корпуса остаётся будущей работой без автоматического подбора песен. При прямом поручении выключить сервер оставляй его выключенным; для упаковки допускается `--check-files-only`.

## Принятая рабочая демка

13 сентября 2026 года пользователь закрепил принятый результат `bfd2665` как рабочую демку **0.1**. Актуальные ветки — `version-0.1` и `master`; прежние ветки `version-0.2` и `version-0.3` удалены по его поручению. Номера внутренних форматов `learning-v3`, `tempo-v3`, schema 3 и исторические названия ТЗ не являются версией приложения. До нового поручения сохраняй принятые поведение и интерфейс; незавершённые исследования не запускай автоматически.

## Целевая платформа

VocalCreator предназначен только для десктопа. Пользователь повторно подтвердил это 13 сентября 2026 года: мобильная версия не нужна вообще. Не трать время на мобильную разработку, адаптацию и мобильную приёмку; проверяй настольный браузер, включая низкие desktop-окна.
