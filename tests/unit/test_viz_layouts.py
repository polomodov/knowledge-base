import math

import pytest

from knowledge_base.viz_layouts import UNCLUSTERED_ZONE, fruchterman_reingold, phyllotaxis_layout


def test_fruchterman_reingold_handles_empty_and_single_node_graphs() -> None:
    assert fruchterman_reingold([], []) == {}
    assert fruchterman_reingold(["only"], [], width=8.0, height=6.0) == {"only": (4.0, 3.0)}


def test_fruchterman_reingold_is_seeded_and_input_order_independent() -> None:
    nodes = ["delta", "alpha", "charlie", "bravo"]
    edges = [
        ("alpha", "bravo", 1.0),
        ("bravo", "charlie", 2.0),
        ("charlie", "delta", 1.5),
        ("delta", "alpha", 0.5),
    ]

    expected = fruchterman_reingold(nodes, edges, seed=17, iterations=20)
    reversed_input = fruchterman_reingold(
        reversed(nodes),
        [(right, left, weight) for left, right, weight in reversed(edges)],
        seed=17,
        iterations=20,
    )

    assert reversed_input == expected
    assert fruchterman_reingold(nodes, edges, seed=18, iterations=20) != expected


def test_fruchterman_reingold_folds_parallel_edges_and_uses_weights() -> None:
    nodes = ["a", "b", "c"]
    split_weight = fruchterman_reingold(
        nodes,
        [("a", "b", 2.0), ("b", "a", 3.0), ("a", "c", 1.0), ("b", "c", 1.0)],
        seed=9,
        iterations=40,
    )
    folded_weight = fruchterman_reingold(
        nodes,
        [("a", "b", 5.0), ("a", "c", 1.0), ("b", "c", 1.0)],
        seed=9,
        iterations=40,
    )

    assert split_weight == folded_weight
    assert (
        fruchterman_reingold(
            nodes,
            [("b", "c", 1.0), ("a", "c", 1.0), ("b", "a", 3.0), ("a", "b", 2.0)],
            seed=9,
            iterations=40,
        )
        == split_weight
    )
    assert _distance(folded_weight["a"], folded_weight["b"]) < _distance(folded_weight["a"], folded_weight["c"])


def test_fruchterman_reingold_rejects_invalid_edges_and_canvas() -> None:
    with pytest.raises(ValueError, match="unknown node"):
        fruchterman_reingold(["a", "b"], [("a", "missing", 1.0)])
    with pytest.raises(ValueError, match="positive finite"):
        fruchterman_reingold(["a", "b"], [("a", "b", 0.0)])
    with pytest.raises(ValueError, match="width"):
        fruchterman_reingold(["a", "b"], [("a", "b", 1.0)], width=0.0)


def test_phyllotaxis_is_order_independent_and_keeps_points_inside_disks() -> None:
    layout = phyllotaxis_layout(
        {"community-b": ["doc-3"], "community-a": ["doc-2", "doc-1"]},
        {"community-b": (0.8, 0.2), "community-a": (0.2, 0.3)},
        point_spacing=0.02,
        point_radius=0.005,
        padding=0.01,
        min_disk_radius=0.08,
    )
    reversed_layout = phyllotaxis_layout(
        {"community-a": ["doc-1", "doc-2"], "community-b": ["doc-3"]},
        {"community-a": (0.2, 0.3), "community-b": (0.8, 0.2)},
        point_spacing=0.02,
        point_radius=0.005,
        padding=0.01,
        min_disk_radius=0.08,
    )

    assert reversed_layout == layout
    assert layout.disks["community-a"].radius == 0.08
    assert layout.positions["doc-1"] != layout.positions["doc-2"]
    for document, community in (("doc-1", "community-a"), ("doc-2", "community-a"), ("doc-3", "community-b")):
        disk = layout.disks[community]
        assert _distance(layout.positions[document], disk.center) + 0.005 <= disk.radius


def test_phyllotaxis_gives_size_two_community_a_clickable_minimum_radius() -> None:
    layout = phyllotaxis_layout(
        {"tiny": ["first", "second"]},
        {"tiny": (2.0, 3.0)},
        min_disk_radius=0.25,
    )

    assert layout.disks["tiny"].radius == 0.25
    assert set(layout.positions) == {"first", "second"}


def test_phyllotaxis_places_isolated_documents_in_a_separate_unclustered_zone() -> None:
    layout = phyllotaxis_layout(
        {"community": ["member"]},
        {"community": (0.5, 0.5)},
        unclustered_documents=["isolated"],
        min_disk_radius=0.1,
        unclustered_gap=0.03,
    )

    community = layout.disks["community"]
    unclustered = layout.disks[UNCLUSTERED_ZONE]
    assert layout.unclustered_zone == UNCLUSTERED_ZONE
    assert unclustered.center[0] - unclustered.radius >= community.center[0] + community.radius + 0.03
    assert layout.positions["isolated"] == unclustered.center


def test_phyllotaxis_handles_no_documents_and_unclustered_only() -> None:
    assert phyllotaxis_layout({}, {}).positions == {}
    isolated = phyllotaxis_layout({}, {}, unclustered_documents=["lonely"])
    assert isolated.disks[UNCLUSTERED_ZONE].center == (0.5, 0.5)
    assert isolated.positions["lonely"] == (0.5, 0.5)


def test_phyllotaxis_rejects_ambiguous_membership() -> None:
    with pytest.raises(ValueError, match="more than one layout zone"):
        phyllotaxis_layout({"one": ["duplicate"], "two": ["duplicate"]}, {"one": (0.0, 0.0), "two": (1.0, 1.0)})
    with pytest.raises(ValueError, match="missing center"):
        phyllotaxis_layout({"one": ["document"]}, {})


def _distance(left: tuple[float, float], right: tuple[float, float]) -> float:
    return math.hypot(left[0] - right[0], left[1] - right[1])
