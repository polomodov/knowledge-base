# v4 Визуализация: план реализации

**Проект:** `knowledge-base` — персональная база знаний на ArangoDB
**Дата:** 11 июля 2026
**Контекст:** мультиагентное проектирование v4-среза (3 параллельных дизайнера: graph export, HTML-архитектура, агрегирующие AQL) с состязательной верификацией против инвариантов проекта и повторным замером всех чисел на реальном корпусе. Все «measured»-значения в этом плане перепроверены независимым верификатором read-only запросами к живой БД.

## Статус: V4-эпик завершён ✅

Python/CLI/HTML-реализация V4-1…V4-6 смерджена в `main` через [PR #42](https://github.com/polomodov/knowledge-base/pull/42) (`990a3cb`); unit, seeded integration, real-corpus build, JSON/GraphML parse round-trip, wheel resource check, ruff, mypy, ADR и Node template gates зелёные. 12 июля 2026 года владелец подтвердил итоговый ручной acceptance артефактов. [viz-smoke-checklist.md](viz-smoke-checklist.md) сохраняется как воспроизводимая процедура регрессионной проверки browser/tool matrix.

## Выбранная форма (решение пользователя)

1. **`kb export graph`** — экспорт графа знаний в стандартные форматы (node-link JSON + GraphML) для внешних инструментов (Gephi/Obsidian/yEd).
2. **`kb viz build`** — самодостаточный статический HTML в `data/generated/` (inline vanilla JS + встроенный пре-агрегированный JSON, работает офлайн из `file://`, без CDN, без сервера, без npm-сборки).
3. **Три вида** (все выбраны): карта сообществ и топиков, таймлайн публикаций, выборочный граф документов (ego-подграф).

Формат трекера — как в [graphrag-plan.md](graphrag-plan.md): шаги `V4-N` сохраняют отдельные зависимости и acceptance gates. Исходно планировался отдельный PR на шаг; после прямого запроса реализовать весь v4 выполнение консолидировано в `codex/v4-visualizations` с логическими волнами по подсистемам и единым полным гейтом (ruff + format + mypy + pytest incl. integration + Node/template + состязательное ревью) перед публикацией. Spec Kit-спека не заводится: v4 — ограниченный сквозной architecture-эпик с явно зафиксированным scope, поэтому применяется docs plan tracker по [ADR 0009](adr/0009-scope-spec-kit-and-plan-tracker-workflows.md).

## Реальный корпус (замерено, июль 2026)

| Метрика | Значение |
|---|---|
| documents / chunks | 2 972 / 24 877 (mpnet-768d) |
| chunk-level similarity рёбра | 102 556 |
| **doc-level fold (дедуп пар)** | **80 114 пар**; топ-10 на документ (union) = **22 248 рёбер** |
| степень документа (doc-level) | p50=40, p90=108, max=423 |
| topics | 422 (7 кириллических label) |
| topic co-occurrence (distinct-doc, канонический дедуп) | ≥2: **1 878** пар · ≥3: 1 301 · ≥5: **853** · ≥10: 498 |
| communities | 11, размеры **[1019, 634, 566, 454, 118, 94, 30, 24, 16, 13, 2]** |
| изолированные документы (без рёбер и сообщества) | 2 |
| authors / works | 2 / **0** → вид «книги/авторы» вакуумен, де-скоупим |
| published_at | 100 % покрытие, 2019-03…2026-06 (88 месяцев, 127 непустых ячеек месяц×источник) |
| заголовки | **2 566/2 972 кириллица**, **2 449/2 972 с emoji (non-BMP)**, 537 с `&`/`"`, 0 с `<` (пока), все URL https |
| language / status | все `unknown` / все `published` (drafts возможны через `--include-drafts`) |
| offline HTML | **2 551 328 bytes** на полном корпусе (потолок 5 MB) |
| graph export | node-link JSON **19 024 276 bytes** · GraphML **28 687 774 bytes** |
| layout 433 узлов (FR, чистый Python) | ≈1.06 s — приемлемо для CLI-команды |

## Канонические контракты (зафиксировано по итогам верификации)

Эти решения сняли противоречия между дизайнерами — при реализации не пересматривать без замеров:

1. **Один канонический fold-хелпер** `document_mentions_topic` → distinct-документы (ловушка GR-5: рёбра пишутся на уровне и документа, и чанка). Семантика: дедуп по doc key; ребро отбрасывается при null-эндпоинте или если документ не входит в запрошенный published/draft scope; confidence/method filters не применяются. Один хелпер переиспользуется для размеров топиков, co-occurrence и timeline series; unit fixture содержит doc-level И chunk-level ребро одной пары (док, топик) и проверяет отсутствие двойного счёта.
2. **Doc-level similarity fold: `MAX(weight)` + `chunk_pairs`** (число чанк-пар — display-only). Прецедент — `_related_documents` (retrieval.py), **не** `_document_similarity_adjacency` (та суммирует — на SUM построены Louvain-сообщества; расхождение намеренное, зафиксировать в graphrag-plan.md одной строкой).
3. **Топ-K=10 рёбер на документ** по чистому MAX-weight с детерминированным тай-брейком (`weight DESC, doc ASC` — прецедент `_related_documents`); никаких изобретённых бонусов за multiplicity в ранжировании.
4. **Сериализация:** `json.dumps(..., ensure_ascii=False, separators=(",", ":"))`, int-индексы документов, веса до 3 знаков. CLI отдаёт размер в байтах; фикстурный тест-потолок на размер.
5. **XSS/embedding-контракт:** `<script type="application/json" id="kb-data">`, экранирование `</` → `<\/` и `<!--`; чтение через `JSON.parse(textContent)`. Весь динамический текст в DOM — только `textContent`/`createTextNode`, **никогда** `innerHTML` (грep-тест шаблона). Санитизация href: allowlist http/https на этапе сборки (юнит-тест с `javascript:`-URL). Обязательный round-trip тест: заголовок с кириллицей + emoji + `"` + `&` + `</script>` проходит через собранный HTML байт-в-байт.
6. **Unicode:** `<meta charset="utf-8">` в первых 1024 байтах (файл открывается из `file://` без HTTP-заголовков); в JS обрезка строк только code-point-safe (`Array.from`), не `substring` по суррогатным парам.
7. **Свежесть/провенанс артефакта:** в payload — `meta` (built_at, БД, счётчики, embedding_model, последние index_runs по target) с видимым футером; предупреждения при сборке: communities старше related-сборки, related пуст при наличии эмбеддингов (состояние после `--target embeddings` до `--target related`); атомарная запись (temp + `os.replace`); в тестах детерминизма `meta` исключается (или clock инъектируется).
8. **Деградации:** небутстрапнутая БД → чистая ненулевая ошибка с подсказкой `kb platform bootstrap` (visualization-команды не мутируют БД); 0 документов → валидный «no data» HTML; 0 similarity-рёбер при двух и более документах в выбранном published/draft scope → предупреждение с командой починки (одноузловой corpus/ego валиден); commIdx=-1 (изолированные документы) → явная «unclustered»-зона на карте + `isolated_documents` в emit_json; статусный exit-code по конвенции PR #19.
9. **Drafts:** по умолчанию в артефакты попадают только `status == "published"`; флаг `--include-drafts`; счётчики по статусам в отчёте сборки.
10. **JS не тестируется pytest'ом** (zero-dependency инвариант): агрегации, inclusion thresholds, base map layouts и canonical top-K считаются в тестируемом Python. JS остаётся тонким интерактивным слоем: применяет выбранный display threshold и вычисляет bounded radial coordinates текущего ego-графа под viewport. Интеграционный тест собирает HTML из изолированной тест-БД и проверяет: нужные element ids, парсящийся `kb-data`, счётчики против фикстуры, потолок байтов. CI-only `node --check` извлечённых `<script>` (Node 24 фиксируется через setup-node) + ручной smoke-чеклист (Chrome/Firefox/Safari из `file://`) как acceptance-шаг PR.
11. **Fixture-корпус не может проверить 2 из 3 видов** (1 документ → 0 рёбер, 0 сообществ, 1 ячейка таймлайна): интеграционные тесты используют **seeded-корпус** по прецеденту `test_build_communities_clusters_similarity_graph` (свои доки/чанки/рёбра с фиксированными весами в отдельной БД); fixture-тест остаётся как явный тест пустого/вырожденного состояния. Веса hash-эмбеддингов семантически произвольны — рёбра сидятся напрямую, не через ANN.
12. **Шаблон** `viz_template.html` — пакетный файл через `importlib.resources` (юнит-тест грузит именно через `importlib.resources.files`, не через `__file__`; комментарий в pyproject.toml, что wheel обязан нести шаблон). JS: ES2020, классические inline-скрипты (без модулей/`fetch`/top-level await/localStorage — ограничения `file://`).
13. **`_export_zone_warning`** параметризуется описанием содержимого (`content="document titles and URLs"` и т.п.) и применяется в `export_jsonl`, `kb export graph`, `kb viz build`; warn-not-block; юнит-тест на срабатывание для каждой команды.
14. **Coverage-гейт:** `--cov-fail-under=70` — новый модуль `visualizing.py` едет в одном PR со своими тестами.

## Приоритетный план

| Шаг | Ветка / PR | Что | Зависит от | Статус |
|----:|------------|-----|-----------|--------|
| V4-0 | [`docs/visualization-plan` / PR #37](https://github.com/polomodov/knowledge-base/pull/37) | этот план | — | ✅ merged в `main` (`d2f9f43`) |
| V4-1 | `codex/v4-visualizations` | агрегационное ядро + канонический дедуп-хелпер | — | ✅ реализован и протестирован |
| V4-2 | `codex/v4-visualizations` | `kb export graph` (node-link JSON + GraphML) | V4-1 | ✅ реализован; round-trip и ручной acceptance подтверждены |
| V4-3 | `codex/v4-visualizations` | детерминированные лейауты (FR + phyllotaxis) | V4-1 | ✅ реализован и протестирован |
| V4-4 | `codex/v4-visualizations` | `kb viz build`: шаблон + сборка HTML + деградации | V4-1, V4-3 | ✅ реализован и протестирован |
| V4-5 | `codex/v4-visualizations` | JS трёх видов + smoke-чеклист + `node --check` CI | V4-4 | ✅ реализован; автогейты и ручной acceptance подтверждены |
| V4-6 | `codex/v4-visualizations` | roadmap/README/диаграммы, де-скоуп authors/works | V4-1…V4-5 | ✅ документация синхронизирована |

### V4-1 — Агрегационное ядро (`src/knowledge_base/visualizing.py`)

**Задача:** пять валидированных агрегаций: (1) doc-level similarity fold (контракт №2/№3); (2) topic co-occurrence на distinct-документах (контракт №1) с порогом; (3) community rollups — summary/top_topics читаются из stored-узлов `communities` (single source, без пересчёта), размеры/членство — запросом; **display-label сообщества у stored-узлов отсутствует** (`build_communities` пишет только `size`/`method`/`top_topics`/`summary`/`created_at`, ревью PR #37) — label **деривируется** на этапе агрегации детерминированным правилом: топ-2 из stored `top_topics` через « · » (напр. «Architecture · SystemDesign»), фолбэк на `_key` при пустых `top_topics`; read-only, без изменения схемы/миграций; правило пиновано юнит-тестом (включая пустой `top_topics`); (4) таймлайн-корзины месяц×источник и месяц×топ-топики (distinct-doc, защитный FILTER null published_at + счётчик `docs_without_dates`); (5) ego-подграф одного документа (соседи + общие сущности). Фактический warm-run: memberships ≈0.88 s, полный 80k similarity fold ≈2.4 s, top-10 union ≈2.7 s; исходная цель `<2 s` для materialized similarity не достигнута, но полный `kb viz build` укладывается примерно в 5.6 s.
**Критерии приёмки:** канонический дедуп-хелпер с пиновой фикстурой (док+чанк ребро одной пары — один счёт); замеренные пороги совпадают с таблицей корпуса; юнит-тесты на маленьких синтетических графах; seeded-интеграционные тесты (контракт №11); никаких записей в БД.

### V4-2 — `kb export graph`

**Задача:** экспорт **полного** doc-level фолда (80 114 пар — не top-K аппроксимация HTML) + topic co-occurrence + community-атрибуты узлов. Форматы: node-link JSON и GraphML (stdlib `xml.etree`, typed `attr.type`, экранирование кириллицы/emoji/кавычек — round-trip тест через re-parse). Узлы: key, title, source_key, community, published_at, top-topics; **без текста документов**. CLI: `--format json|graphml`, `--output`, `--ego <doc_key>` (валидированный ego-запрос V4-1); `_export_zone_warning` (контракт №13); emit_json с числом узлов/рёбер/байтов; детерминированный порядок. Фактический GraphML полного корпуса — 28 687 774 bytes: выше исходной оценки 15–25 MB из-за добавленных canonical `document_topic` links и graph-level metadata, но без body/chunk text.
**Критерии приёмки:** GraphML открывается в Gephi/yEd (ручная проверка — acceptance в PR); JSON и GraphML согласованы по содержимому; интеграционный тест на seeded-корпусе.

### V4-3 — Детерминированные лейауты

**Задача:** seeded Fruchterman–Reingold для 433 узлов карты (422 топика + 11 сообществ, ≈6.2 s на build — приемлемо); phyllotaxis-размещение 2 972 док-точек внутри дисков сообществ (O(n); полный FR на 2 972 узлах в чистом Python — минуты, отвергнут); минимальный радиус диска/пузыря (сообщество размера 2 должно быть видимым и кликабельным); явная «unclustered»-зона для commIdx=-1; guards n=0/n=1 (деление на ноль на пустой БД).
**Критерии приёмки:** юнит-тесты детерминизма (одинаковый граф → одинаковые координаты, независимо от порядка вставки — прецедент теста Louvain); тесты на маленьких графах (CI не гоняет 6-секундный layout); размещение сообщества из 2 документов и изолированного документа покрыто тестами.

### V4-4 — `kb viz build` + шаблон

**Задача:** сборка payload (контракты №4–№9) + инъекция в `viz_template.html` (контракты №5, №6, №12) → `data/generated/viz/knowledge-base.html`. Meta/футер свежести, предупреждения о рассинхроне derived-слоя, атомарная запись, статусный exit-code, `--timeline-top-topics` (default 10), `--include-drafts`.
**Критерии приёмки:** round-trip тесты unicode/XSS (контракт №5); тест зоны экспорта; тест пустой БД («no data» HTML) и БД без related-рёбер (warning); интеграционный тест на seeded-корпусе: element ids, `JSON.parse(kb-data)`, счётчики, потолок байтов; `isolated_documents` и байты в emit_json.

### V4-5 — Три вида (JS)

**Задача:** (1) карта сообществ/топиков — SVG-топик-граф с FR-координатами + canvas-подложка док-точек (rAF-батчи по 500, grid hit-testing), слайдер порога co-occurrence (embed ≥2 = 1 878 рёбер, default показа ≥5 = 853); (2) таймлайн — SVG stacked-бары месяц×источник + режим линий топ-топиков; (3) ego-вид — детерминированный радиальный layout в JS при выборе (центр → кольцо top-K соседей → кольцо 2, cap ≈60 узлов), честная подпись «показаны top-10 соседей; полный граф — `kb export graph`». Общее: табы, поиск по заголовкам/топикам (lazy-индекс, code-point-safe), pan/zoom, тултипы, клик по точке → ego, provenance-ссылки.
**Критерии приёмки:** ручной smoke-чеклист (три браузера, `file://`, все табы, поиск, ego-клик) в описании PR; `node --check` шаг в CI; вся логика порогов/данных остаётся в Python-тестах.

### V4-6 — Синхронизация документации

**Задача:** roadmap v4 → факт (де-скоуп «книги/авторы» — 0 works/2 authors, источник как цвет/фасет; убрать невыполнимое обещание); README «Что уже есть» + быстрый старт (`kb export graph`, `kb viz build`); architecture.md — подсистема Visualization реализована; диаграмма конвейера сборки viz; AGENTS.md-правило синхронизации соблюсти в том же PR.

## Метод

Выполнение прошло в единой feature-ветке с логическими коммитами V4-1…V4-6 и завершилось merge [PR #42](https://github.com/polomodov/knowledge-base/pull/42) в `main` после полного гейта и состязательного мультиагентного ревью. Все «measured»-числа получены из реальных read-only замеров; расхождения перепроверялись независимым замером. Итоговый ручной acceptance подтверждён владельцем 12 июля 2026 года; точечный browser/tool-чеклист остаётся процедурой для последующих регрессионных прогонов.
