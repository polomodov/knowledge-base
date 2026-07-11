# 0007. Выбрать пересобираемые эмбеддинги и экстрактивный GraphRAG / Adopt rebuildable embeddings and extractive GraphRAG

```json adr-meta
{
  "id": "0007",
  "titleRu": "Выбрать пересобираемые эмбеддинги и экстрактивный GraphRAG",
  "titleEn": "Adopt rebuildable embeddings and extractive GraphRAG",
  "status": "accepted",
  "date": "2026-07-11",
  "deciders": [
    "knowledge-base maintainer"
  ],
  "tags": [
    "embeddings",
    "graphrag",
    "retrieval",
    "graph",
    "derived-data"
  ],
  "supersedes": [],
  "supersededBy": []
}
```

## RU

### Контекст и проблема

ADR 0003 выбрал ArangoDB как единое ядро для полнотекстового, векторного и графового поиска, но не определил качество эмбеддингов, жизненный цикл производных индексов и способ построения GraphRAG. Начальный детерминированный hash-вектор позволял проверить plumbing без внешних зависимостей, но не давал содержательного семантического поиска. Граф провенанса не влиял на retrieval, similarity-рёбра и сообщества отсутствовали, а full-text view индексировал тело документа одновременно на уровне документа и чанка.

В ходе GraphRAG-эпика план GR-0 был принят в PR #22, а развилки GR-1…GR-6 и отложенный GR-3c реализованы последовательно в PR #23–#33. Этот ретроспективный ADR не являлся предварительным одобрением изменений и фиксирует уже принятое устройство: подключаемые эмбеддинги, явно пересобираемые производные слои, ограниченное использование графа в ранжировании и экстрактивный local/global GraphRAG без LLM в retrieval-ядре.

### Y-statement

В контексте локальной персональной базы знаний на ArangoDB, столкнувшись с несемантичными hash-векторами, устаревающими производными графами и необходимостью пересобираемого и проверяемого GraphRAG без передачи личных текстов внешнему сервису, мы решили выбрать подключаемые embedding providers и явно пересобираемый экстрактивный GraphRAG, чтобы получить содержательный семантический и графовый retrieval при сохранении offline/zero-dependency пути, принимая ручной жизненный цикл перестройки, тяжёлую опциональную ML-зависимость и менее связные ответы без генеративной модели.

### Драйверы решения

- Ingest и запрос должны использовать один configured provider/model/dimension, а оператор должен поддерживать однородность persisted embedding space полным rebuild.
- Дефолтный путь, тесты и CI должны оставаться детерминированными, локальными и без обязательных runtime-зависимостей.
- Реальный корпус должен поддерживать качественную локальную семантическую модель без отправки личных текстов во внешний API.
- Смена provider, модели или размерности не должна требовать повторного ingest исходных данных.
- Эмбеддинги, similarity-рёбра, сообщества и summaries являются производными данными и должны полностью пересобираться из нормализованных документов и чанков.
- Графовый сигнал должен улучшать precision/recall, но не иметь возможности неконтролируемо перевесить лексический или семантический hit.
- Local/global GraphRAG должен возвращать цитируемый контекст со сквозным provenance и явно сообщать о runtime-ошибках retrieval/graph слоя.
- Реализация должна сохранять единую retrieval-гранулярность: body-текст и семантические векторы индексируются по чанкам, а заголовок документа остаётся доступен полнотекстовому поиску.

### Рассмотренные варианты

- **Оставить только hash-эмбеддинги и обычный text/vector retrieval.** Самый простой и полностью воспроизводимый вариант проверяет инфраструктуру, но не даёт полезной семантики, similarity-графа и corpus-level GraphRAG.
- **Использовать внешний embedding API и LLM для summaries и ответов.** Даёт качественные векторы и более связный синтез, но создаёт риски приватности, сетевую и vendor-зависимость, переменную стоимость и худшую воспроизводимость.
- **Сделать локальный ML/graph stack обязательным.** `sentence-transformers` и готовые graph-библиотеки упрощают отдельные алгоритмы, но добавляют тяжёлые зависимости, включая ML runtime, ко всем установкам и CI.
- **Подключаемые providers и пересобираемый экстрактивный GraphRAG.** Hash остаётся лёгким dev/test default, локальная `sentence-transformers`-модель подключается лениво и вручную, а similarity-граф, Louvain-сообщества и экстрактивные summaries строятся контролируемым кодом проекта.

Для community detection также проверялись label propagation и connected components. На плотном similarity-графе реального корпуса label propagation схлопывал почти весь корпус в одно мегасообщество, а пороговые connected components либо сохраняли гигантскую компоненту, либо теряли слишком много документов. Детерминированная Louvain-оптимизация модулярности дала полезное тематическое разбиение того же связного графа.

### Итоговое решение

Выбран вариант: подключаемые providers и пересобираемый экстрактивный GraphRAG.

- `EmbeddingProvider` задаёт `model`, `dimension` и `embed(text)`. `hash` (`hash-v1`, по умолчанию) остаётся детерминированным offline dev/test provider. `local` лениво импортирует установленный пользователем `sentence-transformers`; тяжёлая библиотека намеренно не входит в обязательный lock/runtime. Local provider проверяет native dimension модели против settings, но model revision/weights fingerprint не фиксируются: одинаковая строка model считается тем же space, а полностью offline запуск требует заранее cached/pinned artifact.
- Ingest чанков и embedding-backed retrieval создают provider из одного конфига, а каждый чанк хранит `embedding_model`. Semantic full-scan фильтрует model и vector length. Обычный schema bootstrap при уже существующем vector index принимает conflict/409 без проверки его параметров; только `kb index rebuild --target embeddings` гарантированно удаляет и пересоздаёт index под configured dimension. Поэтому смена provider/model/dimension требует этого явного target, а settings сами по себе не доказывают однородность persisted space.
- Семантические векторы и similarity-связи строятся на уровне чанков. ArangoSearch индексирует body-текст только через `chunks.text`, чтобы не удваивать BM25-статистику; `documents.title` остаётся отдельным полнотекстовым полем.
- Смена provider, модели или размерности выполняется без re-ingest командой `kb index rebuild --target embeddings`: vector index пересоздаётся под новую размерность, чанки переэмбеддятся, а устаревшие `item_related_to_item` удаляются.
- Производный слой перестраивается явной последовательностью `embeddings → related → communities`. После полного однородного re-embedding `related` выбирает для каждого чанка top-K соседей из других документов с тем же `embedding_model` и cosine не ниже порога. Whole-corpus path использует approximate ANN; edge keys, порядок записи и повторная замена идемпотентны, но candidate set при ANN boundary/ties не гарантирован bit-for-bit. Проверка сравнивает model string, но не vector length/revision; mixed или частично перестроенный corpus является неподдержанным промежуточным состоянием и может породить некорректные связи.
- `communities` сводит chunk-level связи к document graph через `SUM` весов всех chunk-pair edges, полностью заменяет прежнее разбиение/summaries и запускает детерминированный pure-Python Louvain для фиксированного входного графа. `top_topics` считает mention edges от document и chunks без distinct-document dedup. Поэтому длинные/многочанковые документы и повторные mentions имеют больший вес как в topology, так и в label/summary сообщества; это действующая семантика GR-4, а не нейтральная агрегация по документам.
- `embeddings`, `item_related_to_item`, `communities`, `document_in_community` и community summaries считаются rebuildable derived outputs, а не источником истины. Дорогие mutating targets намеренно не входят в обычный `--target all`; оператор запускает их явно после изменения upstream-конфига или корпуса.
- Semantic retrieval применяет настраиваемый relevance gate `min_similarity`. Hybrid сначала сливает BM25 и cosine, затем добавляет ограниченный graph boost за общие сущности и similarity-связи. Graph-only expansion заполняет только свободные после гейта слоты, уважает source scope, добавляется после прямых hits и ограничивает score так, чтобы не перевесить их.
- Community summary строится экстрактивно из размера сообщества и topics с наибольшим числом mention edges. `local` собирает вокруг retrieval-сидов сущности, similarity-соседей и сообщества. `global` рассматривает только сообщества bounded hybrid candidate pool (limit сейчас не меньше 50, фактических hits может быть меньше), суммирует scores попавших кандидатов без нормализации по размеру и возвращает summary с документами-цитатами; это retrieval-conditioned обзор, а не полный проход по всем community summaries. LLM не участвует ни в построении summaries, ни в retrieval-ранжировании, ни в формировании ответа.

### Последствия

- Хорошо: один конфигурационный provider обслуживает ingest и запрос, а semantic retrieval фильтрует кандидатов по model и ожидаемой длине там, где используется full-scan path.
- Хорошо: реальную локальную модель можно включить для существующего корпуса без повторного импорта и без отправки текста внешнему провайдеру.
- Хорошо: derived-слой явно пересобираем и проверяем; hash embedding, pure-Python Louvain для фиксированного графа и extractive formatting остаются детерминированными и пригодными для тестов.
- Хорошо: граф реально участвует в ranking и recall, но его вклад ограничен; local/global результаты сохраняют цитаты и provenance.
- Плохо: `sentence-transformers` и её ML runtime устанавливаются и обновляются отдельно от lock-файла проекта; воспроизводимость конкретной локальной модели требует операционной дисциплины.
- Плохо: полная перестройка эмбеддингов, ANN similarity-рёбер и сообществ на большом корпусе дорога и выполняется несколькими командами.
- Плохо: ANN candidate selection не обязана быть bit-for-bit стабильной, а `related` не проверяет vector length или model revision; частичный/mixed rebuild может молча построить неправильный similarity graph.
- Плохо: `SUM` chunk-pair weights, не дедуплицированные topic mentions и ненормализованные global scores могут смещать communities, labels и global ranking в пользу длинных документов или крупных групп.
- Плохо: model revision/fingerprint не хранится, а обычный bootstrap не сверяет параметры существующего vector index; воспроизводимость local model и согласованность index зависят от cached/pinned weights и дисциплины явного rebuild.
- Плохо: экстрактивные summaries и GraphRAG-контекст менее связны и выразительны, чем LLM-синтез, и не являются готовым прозаическим ответом.
- Нейтрально: жизненный цикл не транзакционный и не имеет автоматической freshness/invalidation проверки. Между `embeddings`, `related` и `communities` downstream-слой может быть пустым или устаревшим; `degraded` выставляется на runtime/AQL error, но не на логически отсутствующий или stale graph, поэтому оператор должен завершить и проверить всю последовательность перед оценкой local/global поиска.
- Нейтрально: hash provider проверяет контракты, а не semantic quality; результаты на нём нельзя использовать как оценку качества реального GraphRAG.

### План пересмотра

Пересмотреть решение, если retrieval-evaluation показывает устойчиво низкие precision/recall или вред от graph boost/expansion; повторные builds дают неприемлемо нестабильные related edges; community/global evaluation выявляет bias от длины документа, числа mentions или размера сообщества; размер корпуса делает ANN-построение, re-embedding или pure-Python Louvain неприемлемым bottleneck; потребуется embedding-space fingerprint/revision, строгая проверка vector-index params, несколько spaces, безопасное переключение без stale window или автоматическое invalidation; локальная зависимость станет надёжно фиксируемой как optional extra; либо writing/research workflow потребует генеративного synthesis. Подключение внешнего embedding/LLM provider, отдельного vector/graph engine или LLM-ответов поверх цитируемого retrieval потребует отдельного решения с privacy, cost, evaluation и provenance-контрактом.

### Ссылки

- [ADR 0003: Выбрать ArangoDB-centered production pipeline](0003-adopt-arangodb-centered-production-pipeline.md)
- [GraphRAG: план реализации](../graphrag-plan.md)
- [Архитектура knowledge-base](../architecture.md)
- [Embedding providers](../../src/knowledge_base/embeddings.py)
- [Перестройка derived-индексов](../../src/knowledge_base/indexing.py)
- [Hybrid и local/global retrieval](../../src/knowledge_base/retrieval.py)
- [Модель данных production pipeline](../../specs/001-production-knowledge-pipeline/data-model.md)
- [PR #22: исходный GraphRAG plan](https://github.com/polomodov/knowledge-base/pull/22)
- [PR #33: завершение реализации graph candidate expansion](https://github.com/polomodov/knowledge-base/pull/33)

## EN

### Context and Problem Statement

ADR 0003 selected ArangoDB as the shared core for full-text, vector, and graph search, but it did not define embedding quality, the lifecycle of derived indexes, or how GraphRAG should be built. The initial deterministic hash vector made it possible to exercise the plumbing without external dependencies, but it did not provide meaningful semantic search. The provenance graph did not affect retrieval, similarity edges and communities did not exist, and the full-text view indexed document body text at both document and chunk granularity.

During the GraphRAG epic, PR #22 accepted the GR-0 plan, while PRs #23–#33 incrementally implemented GR-1…GR-6 and the deferred GR-3c. This retrospective ADR was not prior approval of those changes and records the resulting accepted architecture: pluggable embeddings, explicitly rebuildable derived layers, bounded use of graph signals in ranking, and extractive local/global GraphRAG without an LLM in the retrieval core.

### Y-statement

In the context of a local personal knowledge base on ArangoDB, facing non-semantic hash vectors, derived graphs that become stale, and the need for rebuildable and auditable GraphRAG without sending personal text to an external service, we decided to adopt pluggable embedding providers and explicitly rebuildable extractive GraphRAG to achieve meaningful semantic and graph retrieval while preserving an offline/zero-dependency path, accepting a manual rebuild lifecycle, a heavy optional ML dependency, and less fluent results without a generative model.

### Decision Drivers

- Ingest and queries must use one configured provider/model/dimension, while the operator maintains a uniform persisted embedding space through a complete rebuild.
- The default path, tests, and CI must remain deterministic, local, and free of mandatory runtime dependencies.
- The real corpus must support a capable local semantic model without sending personal text to an external API.
- Changing provider, model, or dimension must not require re-ingesting source data.
- Embeddings, similarity edges, communities, and summaries are derived data and must be fully rebuildable from normalized documents and chunks.
- Graph signals should improve precision/recall but must not be able to overwhelm a lexical or semantic hit without bounds.
- Local/global GraphRAG must return citable context with end-to-end provenance and report runtime retrieval/graph failures explicitly.
- The implementation must retain one retrieval granularity: body text and semantic vectors are indexed by chunk, while document titles remain available to full-text search.

### Considered Options

- **Keep hash embeddings and ordinary text/vector retrieval only.** The simplest, fully reproducible option exercises the infrastructure, but does not provide useful semantics, a similarity graph, or corpus-level GraphRAG.
- **Use an external embedding API and an LLM for summaries and answers.** This provides strong vectors and more fluent synthesis, but introduces privacy risk, network and vendor dependencies, variable cost, and weaker reproducibility.
- **Make a local ML/graph stack mandatory.** `sentence-transformers` and established graph libraries simplify individual algorithms, but add heavy dependencies, including an ML runtime, to every installation and CI job.
- **Use pluggable providers and rebuildable extractive GraphRAG.** Hash remains a lightweight dev/test default, a local `sentence-transformers` model is loaded lazily and installed manually, and the project builds the similarity graph, Louvain communities, and extractive summaries with controlled project-owned code.

Label propagation and connected components were also evaluated for community detection. On the dense similarity graph of the real corpus, label propagation collapsed almost the entire corpus into one mega-community, while thresholded connected components either retained a giant component or discarded too many documents. Deterministic Louvain modularity optimization produced useful thematic partitions of the same connected graph.

### Decision Outcome

Chosen option: pluggable providers and rebuildable extractive GraphRAG.

- `EmbeddingProvider` defines `model`, `dimension`, and `embed(text)` properties. `hash` (`hash-v1`, the default) remains the deterministic offline dev/test provider. `local` lazily imports a user-installed `sentence-transformers`; the heavy library deliberately remains outside the mandatory lock/runtime. The local provider checks the model's native dimension against settings, but no model revision/weights fingerprint is pinned or stored: the same model string is treated as the same space, and fully offline operation requires a pre-cached/pinned artifact.
- Chunk ingest and embedding-backed retrieval construct the provider from the same configuration, and every chunk stores `embedding_model`. Semantic full-scan filters model and vector length. Ordinary schema bootstrap accepts conflict/409 for an existing vector index without inspecting its parameters; only `kb index rebuild --target embeddings` reliably drops and recreates the index for the configured dimension. Changing provider/model/dimension therefore requires that explicit target; settings alone do not prove a uniform persisted space.
- Semantic vectors and similarity links are built at chunk granularity. ArangoSearch indexes body text only through `chunks.text` to avoid duplicated BM25 statistics; `documents.title` remains a separate full-text field.
- Changing provider, model, or dimension without re-ingest uses `kb index rebuild --target embeddings`: the vector index is recreated at the new dimension, chunks are re-embedded, and stale `item_related_to_item` edges are removed.
- The derived layer is rebuilt through the explicit `embeddings → related → communities` sequence. After a complete uniform re-embedding, `related` selects each chunk's top-K neighbours from other documents with the same `embedding_model` and at or above the cosine threshold. The whole-corpus path uses approximate ANN; edge keys, write order, and replacement are idempotent, but the candidate set at ANN boundaries/ties is not guaranteed bit-for-bit. The check compares the model string but not vector length/revision; a mixed or partially rebuilt corpus is an unsupported intermediate state and may produce invalid links.
- `communities` folds chunk-level links into a document graph using the `SUM` of all chunk-pair edge weights, fully replaces the prior partition/summaries, and runs deterministic pure-Python Louvain for a fixed input graph. `top_topics` counts mention edges from documents and chunks without distinct-document deduplication. Long/multi-chunk documents and repeated mentions therefore carry more weight in both community topology and labels/summaries; this is current GR-4 semantics, not neutral per-document aggregation.
- `embeddings`, `item_related_to_item`, `communities`, `document_in_community`, and community summaries are rebuildable derived outputs, not sources of truth. Expensive mutating targets deliberately remain outside ordinary `--target all`; the operator runs them explicitly after upstream configuration or corpus changes.
- Semantic retrieval applies a configurable `min_similarity` relevance gate. Hybrid first fuses BM25 and cosine, then adds a bounded graph boost for shared entities and similarity links. Graph-only expansion fills only slots left open by the gate, respects source scope, is appended after direct hits, and caps its score so it cannot outrank them.
- A community summary is extracted from community size and topics with the highest mention-edge counts. `local` assembles entities, similarity neighbours, and communities around retrieval seeds. `global` considers only communities reached by a bounded hybrid candidate pool (the limit is currently at least 50, while actual hits may be fewer), sums candidate scores without community-size normalization, and returns summaries with citing documents; it is a retrieval-conditioned overview, not a full pass over every community summary. No LLM participates in summary construction, retrieval ranking, or answer formation.

### Consequences

- Good: one configured provider serves ingest and queries, and semantic retrieval filters candidates by model and expected length on the full-scan path.
- Good: a real local model can be enabled for an existing corpus without re-import and without sending text to an external provider.
- Good: the derived layer is explicitly rebuildable and auditable; hash embeddings, pure-Python Louvain for a fixed graph, and extractive formatting remain deterministic and testable.
- Good: the graph materially affects ranking and recall, but its contribution is bounded; local/global results retain citations and provenance.
- Bad: `sentence-transformers` and its ML runtime are installed and updated outside the project's lock file; reproducing a specific local model requires operational discipline.
- Bad: fully rebuilding embeddings, ANN similarity edges, and communities for a large corpus is expensive and requires several commands.
- Bad: ANN candidate selection need not be bit-for-bit stable, and `related` does not check vector length or model revision; a partial/mixed rebuild can silently produce an invalid similarity graph.
- Bad: summed chunk-pair weights, non-deduplicated topic mentions, and unnormalized global scores can bias communities, labels, and global ranking toward long documents or large groups.
- Bad: model revision/fingerprint is not stored, and ordinary bootstrap does not verify existing vector-index parameters; local-model reproducibility and index consistency depend on cached/pinned weights and explicit rebuild discipline.
- Bad: extractive summaries and GraphRAG context are less fluent and expressive than LLM synthesis and are not a finished prose answer.
- Neutral: the lifecycle is neither transactional nor automatically freshness/invalidation checked. Between `embeddings`, `related`, and `communities`, the downstream layer may be empty or stale; `degraded` is set for runtime/AQL errors, not for a logically missing or stale graph, so the operator must complete and verify the sequence before evaluating local/global search.
- Neutral: the hash provider validates contracts, not semantic quality; its results must not be used to assess real GraphRAG quality.

### Review Plan

Revisit this decision if retrieval evaluation shows persistently poor precision/recall or harm from graph boost/expansion; repeat builds produce unacceptable related-edge instability; community/global evaluation reveals bias from document length, mention count, or community size; corpus size makes ANN construction, re-embedding, or pure-Python Louvain an unacceptable bottleneck; the system needs an embedding-space fingerprint/revision, strict vector-index parameter checks, several spaces, safe switching without a stale window, or automatic invalidation; the local dependency can be reliably pinned as an optional extra; or writing/research workflows require generative synthesis. Adding an external embedding/LLM provider, a dedicated vector/graph engine, or LLM answers on top of citable retrieval will require a separate decision covering privacy, cost, evaluation, and provenance contracts.

### Links

- [ADR 0003: Adopt an ArangoDB-centered production pipeline](0003-adopt-arangodb-centered-production-pipeline.md)
- [GraphRAG implementation plan](../graphrag-plan.md)
- [knowledge-base architecture](../architecture.md)
- [Embedding providers](../../src/knowledge_base/embeddings.py)
- [Derived-index rebuilds](../../src/knowledge_base/indexing.py)
- [Hybrid and local/global retrieval](../../src/knowledge_base/retrieval.py)
- [Production pipeline data model](../../specs/001-production-knowledge-pipeline/data-model.md)
- [PR #22: original GraphRAG plan](https://github.com/polomodov/knowledge-base/pull/22)
- [PR #33: graph-candidate-expansion implementation completion](https://github.com/polomodov/knowledge-base/pull/33)
