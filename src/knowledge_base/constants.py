GRAPH_NAME = "knowledge_graph"
TEXT_VIEW_NAME = "kb_text_view"
VECTOR_DIMENSION = 8

# GR-3: cross-document similarity edges (item_related_to_item). Each chunk links to at most
# RELATED_TOP_K most-similar chunks from other documents whose cosine is at least RELATED_MIN_SCORE.
# RELATED_EDGE_METHOD tags these derived edges so writers and readers never confuse them with
# non-derived item_related_to_item edges of another kind.
RELATED_TOP_K = 5
RELATED_MIN_SCORE = 0.5
RELATED_EDGE_METHOD = "embedding-similarity"

# GR-4: community detection over the item_related_to_item similarity graph (label propagation).
# Only communities of at least COMMUNITY_MIN_SIZE documents are stored; summaries list the top
# shared topics. COMMUNITY_METHOD tags the derived community nodes/edges.
COMMUNITY_MIN_SIZE = 2
COMMUNITY_TOP_TOPICS = 5
COMMUNITY_METHOD = "label-propagation"

DOCUMENT_COLLECTIONS = [
    "sources",
    "raw_snapshots",
    "documents",
    "chunks",
    "topics",
    "authors",
    "works",
    "communities",
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
    "document_in_community",
]
