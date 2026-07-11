from knowledge_base.indexing import _communities_from_partition, _louvain


def _sizes(partition: dict[str, list[str]]) -> list[int]:
    return sorted((len(members) for members in partition.values()), reverse=True)


def test_louvain_splits_two_dense_clusters() -> None:
    # Two dense triangles {a,b,c} and {x,y,z} joined only by a weak bridge (c-x, weight 0.1). Louvain
    # maximizes modularity, so it must recover the two clusters rather than merge them (GR-4).
    adjacency = {
        "a": {"b": 1.0, "c": 1.0},
        "b": {"a": 1.0, "c": 1.0},
        "c": {"a": 1.0, "b": 1.0, "x": 0.1},
        "x": {"y": 1.0, "z": 1.0, "c": 0.1},
        "y": {"x": 1.0, "z": 1.0},
        "z": {"x": 1.0, "y": 1.0},
    }
    communities = _communities_from_partition(_louvain(adjacency), min_size=2)
    assert communities == [["a", "b", "c"], ["x", "y", "z"]]


def test_louvain_splits_a_single_connected_component() -> None:
    # The key property label propagation lacked: two cliques linked by a *single* edge form one
    # connected component, but Louvain still splits them (LP would flood one label over both).
    adjacency = {
        "a": {"b": 5.0, "c": 5.0}, "b": {"a": 5.0, "c": 5.0}, "c": {"a": 5.0, "b": 5.0, "d": 0.2},
        "d": {"e": 5.0, "f": 5.0, "c": 0.2}, "e": {"d": 5.0, "f": 5.0}, "f": {"d": 5.0, "e": 5.0},
    }
    assert _sizes(_louvain(adjacency)) == [3, 3]


def test_louvain_is_deterministic() -> None:
    adjacency = {
        "a": {"b": 1.0, "c": 1.0}, "b": {"a": 1.0, "c": 1.0}, "c": {"a": 1.0, "b": 1.0},
        "x": {"y": 1.0}, "y": {"x": 1.0},
    }
    assert _louvain(adjacency) == _louvain(adjacency)


def test_louvain_handles_empty_and_edgeless_graphs() -> None:
    assert _louvain({}) == {}
    # Isolated nodes (no edges) stay singletons and are dropped by the min_size filter.
    assert _communities_from_partition(_louvain({"a": {}, "b": {}}), min_size=2) == []


def test_communities_from_partition_drops_small_and_orders() -> None:
    partition = {"c1": ["b", "a"], "c2": ["c"], "c3": ["e", "d"]}
    # c2 is a singleton -> dropped; members and communities are sorted for determinism.
    assert _communities_from_partition(partition, min_size=2) == [["a", "b"], ["d", "e"]]
    # A higher floor drops everything here.
    assert _communities_from_partition(partition, min_size=3) == []
