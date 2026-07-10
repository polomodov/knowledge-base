GRAPH_NAME = "knowledge_graph"
TEXT_VIEW_NAME = "kb_text_view"
VECTOR_DIMENSION = 8

# GR-3: cross-document similarity edges (item_related_to_item). Each chunk links to at most
# RELATED_TOP_K most-similar chunks from other documents whose cosine is at least RELATED_MIN_SCORE.
RELATED_TOP_K = 5
RELATED_MIN_SCORE = 0.5

DOCUMENT_COLLECTIONS = [
    "sources",
    "raw_snapshots",
    "documents",
    "chunks",
    "topics",
    "authors",
    "works",
    "import_runs",
    "index_runs",
]

EDGE_COLLECTIONS = [
    "document_from_source",
    "chunk_of_document",
    "document_mentions_topic",
    "document_mentions_author",
    "document_references_work",
    "chunk_derived_from_raw",
    "item_related_to_item",
]
