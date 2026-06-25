# Research: Production Knowledge Pipeline

## Decision: ArangoDB-centered stack

**Decision**: Use ArangoDB as the primary multi-model runtime for v1.

**Rationale**: ArangoDB combines document collections, edge collections, graph traversal, ArangoSearch and vector indexes in one operational core. This keeps the first production-like pipeline meaningful while avoiding a premature Postgres + OpenSearch + Qdrant + graph DB service set.

**Alternatives considered**:

- **Polyglot specialized stack**: Strong specialized engines, but too many moving parts for the first source-independent slice.
- **Postgres-centered**: Simpler operationally, but graph traversal and GraphRAG modeling are less natural.
- **Embedded local stack**: Fast to start, but less representative of a production-like service runtime.

## Decision: Full-text search through ArangoSearch

**Decision**: Use ArangoSearch View(s) over `documents` and `chunks`, with BM25 scoring for lexical retrieval.

**Rationale**: ArangoSearch is part of ArangoDB and supports ranking functions such as BM25. Keeping full-text search in ArangoDB avoids an OpenSearch dependency in v1.

**Alternatives considered**:

- **OpenSearch**: Better standalone search platform, but adds service operations and synchronization.
- **Client-side text search**: Too weak for production-grade retrieval and ranking.

## Decision: Vector search through ArangoDB vector indexes

**Decision**: Store chunk embeddings in ArangoDB and create vector index(es) for semantic retrieval.

**Rationale**: ArangoDB vector indexes let v1 test semantic search without Qdrant. This is enough for a first GraphRAG pipeline and keeps integration simpler.

**Alternatives considered**:

- **Qdrant**: Stronger dedicated vector DB, but requires cross-service synchronization.
- **No vector search in v1**: Simpler, but misses the core retrieval experiment.

## Decision: Graph traversal through ArangoDB edge collections

**Decision**: Model knowledge graph relations with edge collections and query them through AQL traversals.

**Rationale**: ArangoDB graph traversal keeps graph exploration next to document/chunk data. Edge collections can represent source, chunk, topic, author, work and provenance relationships.

**Alternatives considered**:

- **Neo4j/TypeDB/NebulaGraph**: Strong graph-specialized options, but add separate operational and data synchronization concerns.
- **Pure document references**: Simpler, but weaker for neighborhood traversal and GraphRAG boosting.

## Decision: Optional MinIO and Dagster

**Decision**: Keep MinIO and Dagster out of the required v1 runtime while designing clean extension points.

**Rationale**: The first fixture should run with ArangoDB only. MinIO becomes useful when raw snapshots are large/binary-heavy. Dagster becomes useful when CLI tasks evolve into production assets with retries, schedules and observability.

## Open Questions

- Which local embedding model will be the default for real personal data?
- Should ArangoDB store raw payloads inline for small text snapshots, or only metadata plus local/object pointers?
- Which analyzer configuration is best for Russian and English text in ArangoSearch?
- When should vector search split out to Qdrant: corpus size, latency, recall quality or operational constraints?

## References

- [ArangoDB documentation](https://docs.arango.ai/)
- [ArangoSearch](https://docs.arango.ai/arangodb/stable/indexes-and-search/arangosearch/)
- [Vector indexes](https://docs.arango.ai/arangodb/stable/indexes-and-search/indexing/working-with-indexes/vector-indexes/)
- [Graph traversals](https://docs.arango.ai/arangodb/stable/aql/graphs/traversals/)
- [Docker installation](https://docs.arango.ai/arangodb/stable/operations/installation/docker/)
