GRAPH_NAME = "knowledge_graph"
TEXT_VIEW_NAME = "kb_text_view"
VECTOR_DIMENSION = 8

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
