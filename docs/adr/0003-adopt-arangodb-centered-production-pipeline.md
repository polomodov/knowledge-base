# 0003. Выбрать ArangoDB-centered production pipeline / Adopt an ArangoDB-centered production pipeline

```json adr-meta
{
  "id": "0003",
  "titleRu": "Выбрать ArangoDB-centered production pipeline",
  "titleEn": "Adopt an ArangoDB-centered production pipeline",
  "status": "accepted",
  "date": "2026-06-23",
  "deciders": ["knowledge-base maintainer"],
  "tags": ["arangodb", "storage", "search", "graph", "rag"],
  "supersedes": [],
  "supersededBy": []
}
```

## RU

### Контекст и проблема

`knowledge-base` должен стать production-grade пайплайном для персональных знаний: ingest, нормализация, provenance, полнотекстовый поиск, embeddings, графовые связи, GraphRAG и writing/research workflows. Первоначальная идея polyglot stack с отдельными Postgres, OpenSearch, Qdrant и graph DB хорошо проверяет специализированные движки, но добавляет много движущихся частей до появления первых реальных источников данных.

Проекту нужен достаточно мощный, но управляемый v1 stack: он должен позволить проверить документы, граф, search и vector retrieval без преждевременной операционной сложности.

### Y-statement

В контексте персональной knowledge database с production-like требованиями к поиску, графу и retrieval, столкнувшись с риском чрезмерно сложного polyglot stack, мы решили выбрать ArangoDB как primary multi-model ядро, чтобы сократить количество сервисов и интеграционного кода, принимая зависимость от одного движка и его возможностей ArangoSearch/vector indexes.

### Драйверы решения

- Нужны document model, graph traversal, full-text search и vector search в одном воспроизводимом локальном stack.
- Нужно сохранить production-grade дисциплину: healthchecks, schema/index bootstrap, idempotent reindex, observability-ready jobs.
- Raw, processed, generated и specs должны оставаться раздельными.
- Все retrieval-результаты должны возвращать provenance и source references.
- Stack должен позволять будущий split-out в Qdrant/OpenSearch/отдельную graph DB, если ArangoDB станет bottleneck.

### Рассмотренные варианты

- **ArangoDB-centered.** Один multi-model движок для documents, graph, ArangoSearch/BM25 и vector indexes; меньше сервисов, проще локальный запуск и меньше failure modes.
- **Polyglot specialized services.** Postgres + OpenSearch + Qdrant + graph DB дают лучшие специализированные границы, но резко увеличивают операционную сложность и интеграционный код.
- **Postgres-centered.** Postgres + full-text + pgvector проще, но графовый слой и GraphRAG-связи будут менее выразительными.
- **Embedded local stack.** DuckDB/LanceDB/Kuzu удобны для экспериментов, но слабее как production-like runtime.

### Итоговое решение

Выбран вариант: ArangoDB-centered architecture для первого production knowledge pipeline.

Runtime stack v1:

- ArangoDB: canonical documents, graph edges, ArangoSearch full-text, vector indexes.
- MinIO: optional для крупных raw/binary snapshots, если ArangoDB metadata + local files недостаточно.
- Dagster: optional but recommended для orchestration, asset lineage, retries и observability.
- Python package/CLI: ingest, normalize, index, query, export.

OpenSearch, Qdrant, Neo4j, TypeDB и отдельный Postgres не входят в v1 plan. Интерфейсы storage/search/vector/graph должны остаться достаточно явными, чтобы позже вынести отдельный движок без переписывания source adapters и пользовательских workflow.

### Последствия

- Хорошо: меньше сервисов и интеграционного кода при сохранении document, graph, search и vector сценариев.
- Хорошо: проще локальный production-like запуск и быстрее первый end-to-end fixture.
- Плохо: ArangoDB становится критическим ядром; отказ или ограничения движка затрагивают сразу storage, graph и retrieval.
- Плохо: если vector search или full-text ranking окажутся недостаточными, потребуется отдельный ADR для split-out в Qdrant/OpenSearch.
- Нейтрально: MinIO и Dagster остаются optional в v1, но design должен не блокировать их подключение.
- Нейтрально: real personal data остается вне git; в репозитории живут только specs, схемы, safe fixtures и docs.

### План пересмотра

Пересмотреть решение после первого end-to-end fixture и первых реальных imports, либо раньше, если ArangoDB vector search, ArangoSearch ranking, graph traversal performance или operational constraints станут явным bottleneck.

### Ссылки

- [ArangoDB documentation](https://docs.arango.ai/)
- [ArangoSearch](https://docs.arango.ai/arangodb/stable/indexes-and-search/arangosearch/)
- [Vector indexes](https://docs.arango.ai/arangodb/stable/indexes-and-search/indexing/working-with-indexes/vector-indexes/)
- [Graph traversals](https://docs.arango.ai/arangodb/stable/aql/graphs/traversals/)
- [Docker installation](https://docs.arango.ai/arangodb/stable/operations/installation/docker/)

## EN

### Context and Problem Statement

`knowledge-base` should become a production-grade pipeline for personal knowledge: ingest, normalization, provenance, full-text search, embeddings, graph relations, GraphRAG, and writing/research workflows. The initial idea of a polyglot stack with separate Postgres, OpenSearch, Qdrant, and graph DB services is useful for testing specialized engines, but it adds many moving parts before the first real data sources exist.

The project needs a powerful but manageable v1 stack: it should let us test documents, graph, search, and vector retrieval without premature operational complexity.

### Y-statement

In the context of a personal knowledge database with production-like search, graph, and retrieval requirements, facing the risk of an overly complex polyglot stack, we decided to choose ArangoDB as the primary multi-model core to reduce services and integration code, accepting the dependency on one engine and its ArangoSearch/vector index capabilities.

### Decision Drivers

- The project needs document model, graph traversal, full-text search, and vector search in one reproducible local stack.
- The design must preserve production-grade discipline: healthchecks, schema/index bootstrap, idempotent reindex, and observability-ready jobs.
- Raw, processed, generated, and specs must stay separate.
- Every retrieval result must return provenance and source references.
- The stack should allow future split-out into Qdrant/OpenSearch/a dedicated graph DB if ArangoDB becomes a bottleneck.

### Considered Options

- **ArangoDB-centered.** One multi-model engine for documents, graph, ArangoSearch/BM25, and vector indexes; fewer services, easier local runtime, and fewer failure modes.
- **Polyglot specialized services.** Postgres + OpenSearch + Qdrant + graph DB provide strong specialized boundaries, but greatly increase operational complexity and integration code.
- **Postgres-centered.** Postgres + full-text + pgvector is simpler, but graph modeling and GraphRAG relations are less expressive.
- **Embedded local stack.** DuckDB/LanceDB/Kuzu are convenient for experiments, but weaker as a production-like runtime.

### Decision Outcome

Chosen option: ArangoDB-centered architecture for the first production knowledge pipeline.

Runtime stack v1:

- ArangoDB: canonical documents, graph edges, ArangoSearch full-text, vector indexes.
- MinIO: optional for large raw/binary snapshots if ArangoDB metadata + local files are insufficient.
- Dagster: optional but recommended for orchestration, asset lineage, retries, and observability.
- Python package/CLI: ingest, normalize, index, query, export.

OpenSearch, Qdrant, Neo4j, TypeDB, and a separate Postgres service are not part of the v1 plan. Storage/search/vector/graph interfaces should remain explicit enough to split out a dedicated engine later without rewriting source adapters and user workflows.

### Consequences

- Good: fewer services and less integration code while preserving document, graph, search, and vector scenarios.
- Good: simpler local production-like runtime and faster first end-to-end fixture.
- Bad: ArangoDB becomes the critical core; an engine outage or limitation affects storage, graph, and retrieval together.
- Bad: if vector search or full-text ranking proves insufficient, a separate ADR will be needed to split out Qdrant/OpenSearch.
- Neutral: MinIO and Dagster remain optional in v1, but the design must not block adding them.
- Neutral: real personal data remains outside git; the repository contains only specs, schemas, safe fixtures, and docs.

### Review Plan

Revisit this decision after the first end-to-end fixture and first real imports, or sooner if ArangoDB vector search, ArangoSearch ranking, graph traversal performance, or operational constraints become a clear bottleneck.

### Links

- [ArangoDB documentation](https://docs.arango.ai/)
- [ArangoSearch](https://docs.arango.ai/arangodb/stable/indexes-and-search/arangosearch/)
- [Vector indexes](https://docs.arango.ai/arangodb/stable/indexes-and-search/indexing/working-with-indexes/vector-indexes/)
- [Graph traversals](https://docs.arango.ai/arangodb/stable/aql/graphs/traversals/)
- [Docker installation](https://docs.arango.ai/arangodb/stable/operations/installation/docker/)
