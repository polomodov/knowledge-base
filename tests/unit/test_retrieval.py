from knowledge_base.retrieval import _dedup_best_by_document, _merge_hybrid


def _text(document_key: str, bm25: float, *, chunk_key: str | None = None, snippet: str = "snippet") -> dict:
    return {
        "id": f"documents/{document_key}",
        "document_key": document_key,
        "chunk_key": chunk_key,
        "title": document_key,
        "snippet": snippet,
        "score": bm25,
        "score_components": {"bm25": bm25, "vector": None, "graph_boost": None},
        "provenance": {"source_key": "s", "raw_snapshot_key": "r", "import_run_key": "i"},
    }


def _semantic(document_key: str, cosine: float, *, chunk_key: str = "c0") -> dict:
    return {
        "id": f"chunks/{chunk_key}",
        "document_key": document_key,
        "chunk_key": chunk_key,
        "title": document_key,
        "snippet": "snippet",
        "score": cosine,
        "score_components": {"bm25": None, "vector": cosine, "graph_boost": None},
        "provenance": {"source_key": "s", "raw_snapshot_key": "r", "import_run_key": "i"},
    }


def test_merge_hybrid_negative_cosine_does_not_subtract() -> None:
    # A semantic-only hit with a negative cosine must not score below zero (finding #16).
    results = _merge_hybrid([], [_semantic("d1", -0.4)], limit=10)
    assert len(results) == 1
    assert results[0]["score"] == 0.0
    assert results[0]["score_components"]["vector"] == -0.4  # raw value preserved for transparency


def test_merge_hybrid_collapses_document_and_its_chunks() -> None:
    # One document surfacing as a document-row and a chunk-row must not take two slots (finding #14).
    text = [_text("d1", 5.0, chunk_key=None), _text("d1", 3.0, chunk_key="d1-c0")]
    results = _merge_hybrid(text, [], limit=10)
    assert len(results) == 1
    assert results[0]["document_key"] == "d1"
    assert results[0]["score_components"]["bm25"] == 5.0  # keeps the higher-BM25 representative


def test_merge_hybrid_ranks_text_and_combines_vector() -> None:
    text = [_text("d1", 10.0), _text("d2", 2.0)]
    semantic = [_semantic("d2", 0.9)]
    results = _merge_hybrid(text, semantic, limit=10)
    scores = {result["document_key"]: result["score"] for result in results}
    assert scores["d1"] == 1.0  # top BM25 -> 1.0, no vector
    assert round(scores["d2"], 6) == 0.9  # bottom BM25 -> 0.0, plus clamped cosine 0.9
    assert results[0]["document_key"] == "d1"


def test_merge_hybrid_graph_boost_is_null_not_lexical() -> None:
    # Hybrid must not fabricate a graph_boost from snippet substring matching (finding #15).
    results = _merge_hybrid([_text("d1", 5.0, snippet="machine learning")], [], limit=10)
    assert results[0]["score_components"]["graph_boost"] is None


def test_merge_hybrid_respects_limit() -> None:
    text = [_text(f"d{i}", float(i)) for i in range(5)]
    results = _merge_hybrid(text, [], limit=2)
    assert len(results) == 2
    assert [result["document_key"] for result in results] == ["d4", "d3"]


def test_dedup_best_by_document_keeps_first_per_document() -> None:
    # Input is sorted by score descending; the first (best) chunk per document wins,
    # and order is preserved (finding #14).
    scored = [
        {"id": "c1", "document_key": "d1", "score": 0.9},
        {"id": "c2", "document_key": "d1", "score": 0.7},
        {"id": "c3", "document_key": "d2", "score": 0.6},
    ]
    deduped = _dedup_best_by_document(scored)
    assert [item["document_key"] for item in deduped] == ["d1", "d2"]
    assert deduped[0]["id"] == "c1"  # best chunk of d1, not the 0.7 one
