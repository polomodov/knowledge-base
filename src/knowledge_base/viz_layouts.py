from __future__ import annotations

import math
import random
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

type Point = tuple[float, float]
type WeightedEdge = tuple[str, str, float]

UNCLUSTERED_ZONE = "unclustered"

_EPSILON = 1e-12
_GOLDEN_ANGLE = math.pi * (3.0 - math.sqrt(5.0))


@dataclass(frozen=True, slots=True)
class Disk:
    """A circular map zone that contains document points."""

    center: Point
    radius: float


@dataclass(frozen=True, slots=True)
class DocumentLayout:
    """Phyllotaxis positions and the disks that contain them."""

    positions: dict[str, Point]
    disks: dict[str, Disk]
    unclustered_zone: str | None


def fruchterman_reingold(
    nodes: Iterable[str],
    edges: Iterable[WeightedEdge],
    *,
    seed: int = 0,
    width: float = 1.0,
    height: float = 1.0,
    iterations: int = 50,
) -> dict[str, Point]:
    """Lay out a weighted undirected graph deterministically.

    Node ids, edge direction, and input insertion order do not affect the result. Parallel edges
    are folded by summing their weights. Coordinates are constrained to ``[0, width]`` and
    ``[0, height]`` so a renderer can use them directly in a view box.
    """

    if not math.isfinite(width) or width <= 0:
        raise ValueError("width must be a positive finite number")
    if not math.isfinite(height) or height <= 0:
        raise ValueError("height must be a positive finite number")
    if iterations < 0:
        raise ValueError("iterations must be non-negative")

    ordered_nodes = sorted(set(nodes))
    if not ordered_nodes:
        return {}
    if len(ordered_nodes) == 1:
        return {ordered_nodes[0]: (width / 2.0, height / 2.0)}

    ordered_edges = _canonical_edges(ordered_nodes, edges)
    generator = random.Random(seed)
    positions = {node: (generator.random() * width, generator.random() * height) for node in ordered_nodes}
    node_count = len(ordered_nodes)
    ideal_distance = math.sqrt(width * height / node_count)
    initial_temperature = min(width, height) / 10.0

    for iteration in range(iterations):
        displacement = {node: [0.0, 0.0] for node in ordered_nodes}

        # Repulsion is evaluated once per unordered pair; the opposite force is applied to the
        # other endpoint. The deterministic fallback direction handles a coincident pair without
        # using a process-randomized hash.
        for left_index, left in enumerate(ordered_nodes):
            for right_index in range(left_index + 1, node_count):
                right = ordered_nodes[right_index]
                dx = positions[left][0] - positions[right][0]
                dy = positions[left][1] - positions[right][1]
                distance = math.hypot(dx, dy)
                if distance < _EPSILON:
                    angle = _GOLDEN_ANGLE * (left_index * node_count + right_index + 1)
                    dx, dy, distance = math.cos(angle) * _EPSILON, math.sin(angle) * _EPSILON, _EPSILON
                force = ideal_distance * ideal_distance / distance
                force_x, force_y = dx / distance * force, dy / distance * force
                displacement[left][0] += force_x
                displacement[left][1] += force_y
                displacement[right][0] -= force_x
                displacement[right][1] -= force_y

        # A larger edge weight produces a proportionally stronger attraction. The canonical fold
        # makes parallel/reversed edges equivalent and removes their input-order dependence.
        for left, right, weight in ordered_edges:
            dx = positions[left][0] - positions[right][0]
            dy = positions[left][1] - positions[right][1]
            distance = max(math.hypot(dx, dy), _EPSILON)
            force = distance * distance / ideal_distance * weight
            force_x, force_y = dx / distance * force, dy / distance * force
            displacement[left][0] -= force_x
            displacement[left][1] -= force_y
            displacement[right][0] += force_x
            displacement[right][1] += force_y

        temperature = initial_temperature * (1.0 - iteration / max(iterations, 1))
        for node in ordered_nodes:
            dx, dy = displacement[node]
            distance = math.hypot(dx, dy)
            if distance > _EPSILON:
                step = min(distance, temperature)
                x = positions[node][0] + dx / distance * step
                y = positions[node][1] + dy / distance * step
                positions[node] = (min(width, max(0.0, x)), min(height, max(0.0, y)))

    return positions


def phyllotaxis_layout(
    community_documents: Mapping[str, Iterable[str]],
    community_centers: Mapping[str, Point],
    *,
    unclustered_documents: Iterable[str] = (),
    unclustered_center: Point | None = None,
    point_spacing: float = 0.012,
    point_radius: float = 0.004,
    padding: float = 0.008,
    min_disk_radius: float = 0.04,
    unclustered_gap: float = 0.04,
) -> DocumentLayout:
    """Place documents in stable phyllotaxis spirals inside community disks.

    Documents with no community are placed in a dedicated ``unclustered`` disk. When its center is
    not supplied, that disk is positioned to the right of every community disk with a visible gap,
    making it an explicit separate zone rather than an implicit community.
    """

    _validate_phyllotaxis_parameters(
        point_spacing=point_spacing,
        point_radius=point_radius,
        padding=padding,
        min_disk_radius=min_disk_radius,
        unclustered_gap=unclustered_gap,
    )
    if UNCLUSTERED_ZONE in community_documents:
        raise ValueError(f"{UNCLUSTERED_ZONE!r} is reserved for documents without a community")

    groups = {community: sorted(documents) for community, documents in community_documents.items()}
    isolated = sorted(unclustered_documents)
    _validate_unique_documents(groups, isolated)

    disks: dict[str, Disk] = {}
    positions: dict[str, Point] = {}
    for community in sorted(groups):
        if community not in community_centers:
            raise ValueError(f"missing center for community {community!r}")
        center = _validated_point(community_centers[community], name=f"center for community {community!r}")
        radius = _disk_radius(
            len(groups[community]),
            point_spacing=point_spacing,
            point_radius=point_radius,
            padding=padding,
            min_disk_radius=min_disk_radius,
        )
        disks[community] = Disk(center=center, radius=radius)
        positions.update(_spiral_positions(groups[community], center=center, spacing=point_spacing))

    unclustered_zone: str | None = None
    if isolated:
        isolated_radius = _disk_radius(
            len(isolated),
            point_spacing=point_spacing,
            point_radius=point_radius,
            padding=padding,
            min_disk_radius=min_disk_radius,
        )
        if unclustered_center is None:
            if disks:
                right_edge = max(disk.center[0] + disk.radius for disk in disks.values())
                isolated_center = (right_edge + unclustered_gap + isolated_radius, _mean_y(disks.values()))
            else:
                isolated_center = (0.5, 0.5)
        else:
            isolated_center = _validated_point(unclustered_center, name="unclustered_center")
        disks[UNCLUSTERED_ZONE] = Disk(center=isolated_center, radius=isolated_radius)
        positions.update(_spiral_positions(isolated, center=isolated_center, spacing=point_spacing))
        unclustered_zone = UNCLUSTERED_ZONE

    return DocumentLayout(positions=positions, disks=disks, unclustered_zone=unclustered_zone)


def _canonical_edges(nodes: list[str], edges: Iterable[WeightedEdge]) -> list[WeightedEdge]:
    known = set(nodes)
    folded: dict[tuple[str, str], list[float]] = {}
    for left, right, raw_weight in edges:
        if left not in known or right not in known:
            missing = left if left not in known else right
            raise ValueError(f"edge references unknown node {missing!r}")
        weight = float(raw_weight)
        if not math.isfinite(weight) or weight <= 0:
            raise ValueError("edge weights must be positive finite numbers")
        if left == right:
            continue
        pair = (left, right) if left < right else (right, left)
        folded.setdefault(pair, []).append(weight)

    canonical: list[WeightedEdge] = []
    for left, right in sorted(folded):
        # fsum makes a parallel-edge fold insensitive to the order in which differently-sized
        # floating-point weights arrived.
        weight = math.fsum(folded[(left, right)])
        if not math.isfinite(weight):
            raise ValueError("combined edge weights must be finite")
        canonical.append((left, right, weight))
    return canonical


def _validate_phyllotaxis_parameters(
    *, point_spacing: float, point_radius: float, padding: float, min_disk_radius: float, unclustered_gap: float
) -> None:
    values = {
        "point_spacing": point_spacing,
        "point_radius": point_radius,
        "padding": padding,
        "min_disk_radius": min_disk_radius,
        "unclustered_gap": unclustered_gap,
    }
    for name, value in values.items():
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{name} must be a non-negative finite number")
    if point_spacing == 0:
        raise ValueError("point_spacing must be greater than zero")
    if point_radius == 0:
        raise ValueError("point_radius must be greater than zero")


def _validated_point(point: Point, *, name: str) -> Point:
    if len(point) != 2:
        raise ValueError(f"{name} must contain exactly two coordinates")
    x, y = float(point[0]), float(point[1])
    if not math.isfinite(x) or not math.isfinite(y):
        raise ValueError(f"{name} coordinates must be finite")
    return x, y


def _validate_unique_documents(groups: Mapping[str, list[str]], isolated: list[str]) -> None:
    seen: set[str] = set()
    for community in sorted(groups):
        for document in groups[community]:
            if document in seen:
                raise ValueError(f"document {document!r} occurs in more than one layout zone")
            seen.add(document)
    for document in isolated:
        if document in seen:
            raise ValueError(f"document {document!r} occurs in more than one layout zone")
        seen.add(document)


def _disk_radius(count: int, *, point_spacing: float, point_radius: float, padding: float, min_disk_radius: float) -> float:
    outermost_point = point_spacing * math.sqrt(max(count - 1, 0))
    return max(min_disk_radius, outermost_point + point_radius + padding)


def _spiral_positions(documents: list[str], *, center: Point, spacing: float) -> dict[str, Point]:
    positions: dict[str, Point] = {}
    for index, document in enumerate(documents):
        radius = spacing * math.sqrt(index)
        angle = index * _GOLDEN_ANGLE
        positions[document] = (center[0] + radius * math.cos(angle), center[1] + radius * math.sin(angle))
    return positions


def _mean_y(disks: Iterable[Disk]) -> float:
    values = [disk.center[1] for disk in disks]
    return sum(values) / len(values)
