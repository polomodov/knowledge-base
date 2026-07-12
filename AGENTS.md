# AGENTS.md

Инструкции для Codex и других агентов, работающих с `knowledge-base`.

## Контекст проекта

Это персональная база знаний из собственных источников: канала "Книжный куб", Medium-блога и других будущих архивов. Проект предназначен для поиска, визуализации, исследования и помощи при написании постов, статей и книг.

Сейчас репозиторий находится на ранней стадии. Не считайте, что ingest, storage, search, RAG или frontend уже реализованы, пока соответствующий код явно не появился в репозитории.

## Главные инварианты

- **Provenance обязателен.** Каждый импортированный элемент должен сохранять источник, дату получения или публикации, исходную ссылку или канал, а также контекст импорта.
- **Raw-данные не смешиваются с обработанными.** Сырой экспорт, нормализованные данные и generated outputs должны жить в разных зонах проекта.
- **Generated outputs не являются источником истины.** Черновики, summaries, embeddings-derived notes и LLM-выводы должны ссылаться на исходные материалы.
- **Персональные данные требуют осторожности.** Не добавляйте приватные экспорты, токены, cookies, ключи API и чувствительные архивы в репозиторий без явного решения владельца проекта.
- **Пайплайны должны быть воспроизводимыми.** Любой импорт или преобразование должны быть описаны явной командой, конфигом или документированным workflow.

## Как работать с изменениями

- Перед изменениями осмотрите текущую структуру репозитория и существующую документацию.
- Если добавляете новый источник, явно опишите его входные данные, выходные данные, ограничения и стратегию provenance.
- Если меняете структуру данных или папок, обновите `README.md` и [docs/architecture.md](docs/architecture.md).
- Если меняете этапы реализации или приоритеты, обновите [docs/roadmap.md](docs/roadmap.md).
- Для будущего Python-кода предпочитайте небольшие, тестируемые модули: source adapters, normalization, storage, search, visualization.
- Не добавляйте тяжелые зависимости для простых задач без причины, зафиксированной в документации или ADR.

## Architecture Decision Records (ADR)

ADR - docs-only записи архитектурных решений и компромиссов. Используйте ADR, когда изменение фиксирует значимый выбор: границы источников данных, provenance-контракт, storage-формат, стратегию поиска/RAG, приватность, визуализацию, автоматизацию или другой выбор, который будущему участнику нужно понять по причинам, а не только по итоговому diff.

ADR contract:

- ADR-файлы живут в `docs/adr/` и используют формат имени `NNNN-english-kebab-title.md`.
- Каждый ADR содержит fenced `adr-meta` JSON-блок с `id`, `titleRu`, `titleEn`, `status`, `date`, `deciders`, `tags`, `supersedes` и `supersededBy`.
- Каждый ADR сохраняет RU/EN-паритет обязательных секций из `docs/adr/template.md`.
- Разрешенные статусы: `proposed`, `accepted`, `rejected`, `deprecated`, `superseded`.
- Новый ADR создавайте командой `npm run adr:new -- --title-ru "..." --title-en "..."`.
- После добавления или изменения ADR запускайте `npm run generate:adr-index` и коммитьте обновленный `docs/adr/README.md`.
- Перед завершением ADR-изменений запускайте `npm run check:adr`.
- Перед завершением архитектурной фичи проверьте diff на ADR-значимые решения. Ретроспективный `accepted` ADR допустим для закрытия пробела, но должен назвать исходный план, PR, коммит или набор изменений и явно сказать, что не являлся предварительным одобрением.
- Accepted ADR не переписывайте для изменения истории. Если решение изменилось, создайте новый ADR и свяжите записи через `supersedes` / `supersededBy`.

## Ожидаемые зоны данных

Придерживайтесь такого разделения on-disk артефактов:

```text
data/raw/         # исходные экспорты и снимки источников (gitignored)
data/processed/   # зарезервировано; в v1 не материализуется
data/generated/   # exports, viz, research dossiers/handoffs/outputs (gitignored)
```

**Processed SSOT — ArangoDB**, а не `data/processed/`: нормализованные documents/chunks и derived indexes живут в базе. Каталог `data/processed/` зарезервирован на будущее и не является вторым источником истины. Research/writing artifacts пишет file CLI в `data/generated/research/`; MCP остаётся read-only (search/document/graph/health) и не публикует dossier packages — см. [ADR 0011](docs/adr/0011-clarify-mcp-vs-research-cli-boundary-and-processed-ssot-in-arangodb.md).

Если данные нельзя безопасно хранить в git, добавьте только структуру, примеры или инструкции, а сами данные держите вне репозитория.

## Качество документации

- Пишите документацию на русском, если пользователь не попросил иначе.
- Не обещайте реализованных возможностей, которых еще нет в коде.
- Явно различайте "сейчас есть" и "планируется".
- Сохраняйте ссылки между markdown-файлами рабочими.

## Spec-Driven Development

Проект использует scoped hybrid workflow из [ADR 0009](docs/adr/0009-scope-spec-kit-and-plan-tracker-workflows.md). GitHub Spec Kit остается default для новых пользовательских фич, feature/API/CLI-контрактов, source adapters и import workflows; он установлен через `specify` CLI с Codex integration, skills живут в `.agents/skills/`, а общие шаблоны и scripts - в `.specify/`.

- Для Spec Kit feature workflow используйте `$speckit-constitution`, `$speckit-specify`, `$speckit-plan`, `$speckit-tasks`, `$speckit-implement` и `$speckit-converge`.
- Для неоднозначных фич используйте `$speckit-clarify` перед планированием.
- Для проверки согласованности specs/plan/tasks используйте `$speckit-analyze` перед реализацией.
- Для ограниченного сквозного remediation-, audit-, research-, architecture- или infrastructure-эпика допустим docs plan tracker, если цель и границы уже явно зафиксированы. Причину выбора tracker вместо Spec Kit фиксируйте в самом плане или связанном ADR. Tracker должен содержать scope/out-of-scope, решения и открытые вопросы, зависимости, критерии приемки, статус шагов, валидацию и ссылки на ADR/PR/коммиты; один tracker является каноническим источником статуса эпика.
- Plan tracker не заменяет ADR. Простые исправления и локальные рефакторинги без изменения внешнего или архитектурного контракта не требуют полного Spec Kit или отдельного tracker.
- Feature specs по умолчанию пишите на русском с кратким English summary.
- Spec Kit specs, plans и tasks - project artifacts, но не `data/raw`, не `data/processed` и не `data/generated`.
- Не помещайте приватные raw-экспорты или чувствительные source data в spec-документы; вместо этого описывайте форму данных, ограничения и provenance.
- Git extension Spec Kit пока не включен. Не полагайтесь на автоматическое ветвление Spec Kit до отдельного решения.

<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan
at specs/007-writer-research-workflow/plan.md
<!-- SPECKIT END -->
