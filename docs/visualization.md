# Визуализация и экспорт графа

V4 реализует два воспроизводимых read-only представления поверх нормализованных данных и derived-индексов ArangoDB:

- `kb export graph` — переносимый node-link JSON или GraphML для Gephi, yEd и других графовых инструментов;
- `kb viz build` — один самодостаточный HTML-файл с картой сообществ/топиков, таймлайном и ego-графом документов.

Артефакты являются generated outputs, не источником истины. Они не содержат body/chunk text, но раскрывают заголовки, URL, даты, темы, сообщества и топологию личного корпуса. Храните их в gitignored `data/generated/` и не публикуйте без проверки.

## Быстрый старт

Перед сборкой обновите производные слои в правильном порядке:

```bash
uv run kb index rebuild --target related
uv run kb index rebuild --target communities
```

Собрать офлайн-интерфейс:

```bash
uv run kb viz build
# data/generated/viz/knowledge-base.html

uv run kb viz build \
  --output data/generated/viz/custom.html \
  --timeline-top-topics 15
```

Экспортировать полный doc-level граф:

```bash
uv run kb export graph \
  --format json \
  --output data/generated/graph/knowledge-base.json

uv run kb export graph \
  --format graphml \
  --output data/generated/graph/knowledge-base.graphml

uv run kb export graph \
  --format json \
  --ego DOCUMENT_KEY \
  --output data/generated/graph/ego.json
```

Обе команды по умолчанию включают только `status == "published"`. `--include-drafts` явно добавляет `draft`, но не fixture/служебные статусы. Сообщество со скрытым member не экспортирует stored summary/top topics: его видимые документы попадают в `unclustered`, чтобы draft-only metadata не протекли через derived rollup. `--topic-min-documents` у graph export задаёт минимальное число distinct-документов для topic co-occurrence (по умолчанию 2).

## Контракт graph export

Node-link JSON имеет стабильную верхнеуровневую форму:

```text
schema_version, directed=false, multigraph=false, meta, nodes[], links[]
```

Типы узлов:

- `document`: `id`, `key`, `title`/`label`, безопасный `url`, `source_key`, `community`, `published_at`, `topics`, а для `--ego` — `is_ego_center`;
- `topic`: `id`, `key`, `label`.

Типы рёбер:

- `document_similarity`: полный distinct doc-pair fold, `weight = MAX(chunk-pair weight)`, сериализованный до трёх знаков, и display-only `chunk_pairs`;
- `document_topic`: каноническое distinct-document membership без двойного счёта document+chunk mentions;
- `topic_cooccurrence`: пара топиков и `document_count` distinct-документов.

GraphML содержит те же узлы/рёбра и typed `key` declarations (`string`, `int`, `double`, `boolean`). Порядок узлов и рёбер детерминирован; XML сериализуется стандартным `xml.etree.ElementTree` и проверяется обратным parse.

`--ego` валидирует центральный документ, выбирает его top-10 соседей по неокруглённому MAX-weight и экспортирует индуцированный подграф с общими топиками. Центр записывается как graph-level `ego_document_key` и `is_ego_center=true`. Неизвестный, скрытый или draft-документ без `--include-drafts` завершает команду ошибкой; изолированный документ даёт валидный одноузловой ego export без ложной рекомендации перестройки.

## Контракт offline HTML

`kb viz build` вычисляет агрегации и layout в Python, затем атомарно подставляет компактный JSON в пакетный `viz_template.html`. Payload содержит:

- `meta`: время/БД, embedding model, последние успешные runs `embeddings`/`related`/`communities`, счётчики, thresholds и consistency warnings;
- `sources`, `communities`, `topics`, `documents` с заранее вычисленными координатами;
- `topic_edges`, `community_topic_edges`, компактные `similarity_edges = [sourceIndex, targetIndex, weight, chunkPairs]` и `ego_neighbors` с каноническим top-10, ранжированным в Python по неокруглённому весу;
- `timeline`: непрерывные месяцы, sparse series по источникам и top-топикам, `docs_without_dates`.

В HTML работают без сети и сервера:

1. карта сообществ/топиков с SVG-рёбрами и Canvas-точками документов, threshold, pan/zoom и hit-testing;
2. таймлайн со stacked source bars и переключаемыми topic lines;
3. ego-граф выбранного документа: top-10 первого кольца, второе кольцо и общий cap 60 узлов.

Шаблон использует CSP, классический inline ES2020 без CDN/npm/fetch/localStorage, динамический текст только через `textContent`/`createTextNode`, а ссылки — только `http`/`https`. JSON экранирует closing-script и HTML-comment последовательности. Статический гейт:

```bash
node scripts/check-viz-template.mjs
```

Ручная проверка `file://` описана в [viz-smoke-checklist.md](viz-smoke-checklist.md).

## Деградации и свежесть

- Небутстрапнутая БД не изменяется автоматически: команда завершается с подсказкой `kb platform bootstrap`.
- Пустая БД создаёт валидный no-data HTML.
- Два и более выбранных документа без similarity-рёбер дают `related_index_empty` и безопасную полную последовательность перестройки; одноузловой corpus/ego считается валидным.
- `communities` старше последнего успешного `related` дают `communities_older_than_related`.
- Документы без community попадают в явную зону `unclustered` и считаются в `isolated_documents`.
- HTML больше 5 000 000 bytes всё ещё записывается атомарно, но получает `artifact_size_exceeds_budget`, status `degraded` и ненулевой CLI exit code.

Warnings не доказывают полную свежесть относительно последнего ingest: текущая модель не хранит corpus generation. Это принятое ограничение [ADR 0008](adr/0008-adopt-offline-visualization-and-graph-export.md).

## Контрольный замер корпуса

Замер на рабочем корпусе 11 июля 2026 года:

| Метрика | Факт |
|---|---:|
| documents / chunks | 2 972 / 24 877 |
| полный doc-level similarity fold | 80 114 рёбер |
| HTML top-10 union | 22 248 рёбер |
| topics / communities / isolated | 422 / 11 / 2 |
| topic co-occurrence ≥2 | 1 878 пар |
| offline HTML | 2 551 328 bytes |
| node-link JSON | 19 024 276 bytes |
| GraphML | 28 687 774 bytes |
| полный `kb viz build` | ≈5,6 s |

GraphML и JSON совпали по числу узлов/рёбер (3 394 / 94 357) и прошли parse/round-trip. Размеры зависят от текущего корпуса и включённых drafts.
