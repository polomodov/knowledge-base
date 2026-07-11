from knowledge_base.indexing import _communities_from_labels, _label_propagation


def test_label_propagation_finds_two_clusters() -> None:
    # Two dense triangles {a,b,c} and {x,y,z} joined only by a weak bridge (c-x, weight 0.1) must
    # resolve to two separate labels (GR-4).
    adjacency = {
        "a": {"b": 1.0, "c": 1.0},
        "b": {"a": 1.0, "c": 1.0},
        "c": {"a": 1.0, "b": 1.0, "x": 0.1},
        "x": {"y": 1.0, "z": 1.0, "c": 0.1},
        "y": {"x": 1.0, "z": 1.0},
        "z": {"x": 1.0, "y": 1.0},
    }
    labels = _label_propagation(adjacency)
    assert labels["a"] == labels["b"] == labels["c"]
    assert labels["x"] == labels["y"] == labels["z"]
    assert labels["a"] != labels["x"]


def test_label_propagation_isolated_nodes_stay_singletons() -> None:
    labels = _label_propagation({"a": {}, "b": {}})
    assert labels == {"a": "a", "b": "b"}


def test_communities_from_labels_drops_small_and_orders() -> None:
    labels = {"b": "L1", "a": "L1", "c": "L2", "e": "L3", "d": "L3"}
    # L2 is a singleton -> dropped; members and communities are sorted for determinism.
    assert _communities_from_labels(labels, min_size=2) == [["a", "b"], ["d", "e"]]
    # A higher floor drops everything here.
    assert _communities_from_labels(labels, min_size=3) == []
