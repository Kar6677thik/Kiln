"""Tests for the K3 preload graph (K3 spec §5.3): deterministic, the right size, touch counts."""

import tracemalloc

import pytest

from kiln.labs import preload

pytestmark = pytest.mark.unit


def test_same_seed_builds_the_same_graph() -> None:
    """Same size + seed → identical records (the lab's runs must be comparable)."""
    first = preload.build_graph(2, seed=3)
    second = preload.build_graph(2, seed=3)
    assert len(first.records) == len(second.records)
    for a, b in zip(first.records[:50], second.records[:50], strict=True):
        # WHY: Owner objects compare by identity, so compare everything else, then their fields.
        assert {k: v for k, v in a.items() if k != "owner"} == {
            k: v for k, v in b.items() if k != "owner"
        }
        assert vars(a["owner"]) == vars(b["owner"])


def test_different_seeds_differ() -> None:
    """A different seed gives different data."""
    assert preload.build_graph(1, seed=1).records[0] != preload.build_graph(1, seed=2).records[0]


@pytest.mark.parametrize("composition", ["mortal", "immortal-heavy"])
def test_size_is_within_ten_percent_of_the_target(composition: preload.Composition) -> None:
    """A fully traced build of the graph is within ±10% of the requested MiB.

    build_graph traces only a sample and extrapolates (ADR-036); here we trace everything to
    check the extrapolation against the real total.
    """
    tracemalloc.start()
    try:
        before = tracemalloc.get_traced_memory()[0]
        graph = preload.build_graph(20, composition=composition)
        real = tracemalloc.get_traced_memory()[0] - before
    finally:
        tracemalloc.stop()
    assert 0.9 * 20 * preload.MIB <= real <= 1.1 * 20 * preload.MIB
    assert 0.9 * real <= graph.traced_bytes <= 1.1 * real


def test_touch_visits_the_requested_fraction() -> None:
    """touch(f) visits ceil(f × records) records: 0, a quarter, all."""
    graph = preload.build_graph(1)
    total = len(graph.records)
    assert preload.touch(graph, 0.0) == 0
    assert preload.touch(graph, 0.25) == -(-total // 4)  # PY: -(-a // b) is ceil(a / b)
    assert preload.touch(graph, 1.0) == total


def test_owners_are_shared_between_ten_records() -> None:
    """Records 0–9 point at the same Owner object, like a foreign key."""
    graph = preload.build_graph(1)
    assert graph.records[0]["owner"] is graph.records[9]["owner"]
    assert graph.records[0]["owner"] is not graph.records[10]["owner"]


def test_describe_counts_objects_per_type() -> None:
    """describe() reports records, owners and per-type object counts."""
    info = preload.describe(preload.build_graph(1))
    counts = info["objects_by_type"]
    assert isinstance(counts, dict)
    assert counts["dict"] >= 2 * int(str(info["records"]))  # each record + its meta dict
