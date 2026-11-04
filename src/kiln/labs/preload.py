"""The K3 preload graph: hundreds of MiB of Python objects built to look like framework data.

WHY THIS MODULE EXISTS
    Instagram's master process loaded Django (lots of dicts, lists, strings and model
    instances) and then forked workers that were supposed to share it. To reproduce the
    "copy-on-read" problem we need a similar object graph of a known size, built the same
    way every time (a fixed random seed). It is built in the master (fork), the forkserver
    (forkserver) or each worker (spawn: the control group, where nothing is shared).

HOW IT FITS
    master/arbiter.py or launch/forkserver_main.py ──build_graph(labs.preload_mb)──▶ GRAPH
    os.fork() ──▶ every worker sees the same GRAPH (shared pages, until written)
    labs.touch_preloaded task ──touch(GRAPH, fraction)──▶ refcount writes ──▶ page copies

READ FIRST
    docs/concepts/copy-on-write.md, docs/concepts/refcounting.md,
    docs/concepts/immortal-objects.md, docs/python/tracemalloc-basics.md

MILESTONE
    Introduced in K3.
"""

import math
import random
import sys
import time
from dataclasses import dataclass, field
from typing import Literal

MIB = 1024 * 1024
# 50 short words for the "immortal-heavy" composition: string literals in source code are
# immortal (PEP 683), so records built from these words share them instead of copying.
_WORDS = [
    "alpha",
    "beta",
    "gamma",
    "delta",
    "epsilon",
    "zeta",
    "eta",
    "theta",
    "iota",
    "kappa",
    "lambda",
    "mu",
    "nu",
    "xi",
    "omicron",
    "pi",
    "rho",
    "sigma",
    "tau",
    "upsilon",
    "phi",
    "chi",
    "psi",
    "omega",
    "red",
    "green",
    "blue",
    "cyan",
    "magenta",
    "yellow",
    "black",
    "white",
    "grey",
    "north",
    "south",
    "east",
    "west",
    "up",
    "down",
    "left",
    "right",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
]
SAMPLE_RECORDS = 5000  # records traced with tracemalloc to measure bytes per record

Composition = Literal["mortal", "immortal-heavy"]


class Owner:
    """A plain class with a __dict__, mimicking a framework "model instance" (a Django user).

    Why:
        Real framework data isn't only dicts and lists: it's objects whose attributes live in
        a per-instance dict. One Owner is shared by 10 records, like a foreign key.
    """

    def __init__(self, uid: int, name: str, perms: list[str]) -> None:
        """Store the attributes.

        Args:
            uid: the owner's id.
            name: the owner's name.
            perms: the owner's permission names.
        """
        self.uid = uid
        self.name = name
        self.perms = perms


@dataclass(slots=True)
class PreloadGraph:
    """The built graph and what it is made of.

    Why:
        Keeping the composition and the measured size with the data lets every lab record
        say exactly what was shared.
    """

    records: list[dict[str, object]] = field(default_factory=list)
    owners: list[Owner] = field(default_factory=list)
    composition: str = "mortal"
    target_mb: int = 0
    traced_bytes: int = 0  # bytes per record (traced on a sample) × number of records
    build_s: float = 0.0


# LAB: the graph every worker reads. Module-level on purpose (code-style §6 exception): it must
# exist *before* fork so that the forked workers share its pages.
GRAPH: PreloadGraph | None = None


def _random_text(rng: random.Random, length: int = 16) -> str:
    """A fresh string of random letters.

    Why:
        "".join() builds a new str object at runtime, which is neither interned nor immortal,
        exactly like strings parsed from a database or a config file.

    Args:
        rng: the seeded random generator.
        length: how many characters.

    Returns:
        The string.
    """
    return "".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(length))


def _mortal_record(rng: random.Random, index: int, owner: Owner) -> dict[str, object]:
    """One "mortal" record: every leaf value is an ordinary, refcounted object.

    Why:
        It mimics framework data (ids, names, tags, scores), whose reads write refcounts.

    Args:
        rng: the seeded random generator.
        index: the record number.
        owner: the shared Owner.

    Returns:
        The record.
    """
    return {
        # WHY: ints ≥ 2**40 are never in the small-int cache, so each is a separate object.
        "id": 2**40 + index,
        "name": _random_text(rng),
        "tags": [_random_text(rng, 8) for _ in range(4)],
        "scores": [rng.random() for _ in range(8)],
        "meta": {"rank": 2**40 + rng.randrange(10**6), "kind": _random_text(rng, 6), "n": index},
        "owner": owner,
    }


def _immortal_record(rng: random.Random, owner: Owner) -> dict[str, object]:
    """One "immortal-heavy" record: the same shape, but the leaves are immortal objects.

    Why:
        Small ints, None/True/False and string literals are immortal (PEP 683, gate G3.6):
        reading them never writes their refcount. The dicts and lists holding them are still
        ordinary objects, so this separates "leaf writes" from "container writes" (lab (b)).

    Args:
        rng: the seeded random generator.
        owner: the shared Owner.

    Returns:
        The record.
    """
    return {
        "id": rng.randrange(257),
        "name": rng.choice(_WORDS),
        "tags": [rng.choice(_WORDS) for _ in range(4)],
        "scores": [rng.randrange(257) for _ in range(8)],
        "meta": {"rank": rng.choice((None, True, False)), "kind": rng.choice(_WORDS), "n": 7},
        "owner": owner,
    }


def _add_record(graph: PreloadGraph, rng: random.Random, index: int) -> None:
    """Append record number `index` (and a new Owner every 10 records).

    Why:
        The sampled and the untraced part of the build must create records identically.

    Args:
        graph: the graph being built.
        rng: the seeded random generator.
        index: the record number.
    """
    if index % 10 == 0:
        graph.owners.append(
            Owner(index, _random_text(rng), [_random_text(rng, 6) for _ in range(3)])
        )
    owner = graph.owners[-1]
    if graph.composition == "mortal":
        graph.records.append(_mortal_record(rng, index, owner))
    else:
        graph.records.append(_immortal_record(rng, owner))


def build_graph(mb: int, *, seed: int = 0, composition: Composition = "mortal") -> PreloadGraph:
    """Build about `mb` MiB of records, sized with tracemalloc on a sample.

    Why:
        tracemalloc counts exactly the bytes Python allocates, so the size is measured, not
        guessed. But it also keeps its own bookkeeping entry for *every* live allocation:
        tracing the whole 300 MiB build made the process grow by about 817 MiB, and that
        memory stayed in the process after tracemalloc.stop() (ADR-036). So we trace only the
        first SAMPLE_RECORDS records, compute bytes per record, and build the rest untraced.
        The records are generated the same way throughout, so the average holds;
        tests/unit/test_preload.py checks the total against a fully traced build.

    Args:
        mb: the target size in MiB.
        seed: the random seed; the same mb + seed gives the same graph.
        composition: "mortal" (framework-like) or "immortal-heavy".

    Returns:
        The graph (traced_bytes = bytes per sampled record × number of records).
    """
    # WHY: imported here, not at the top: the _tracemalloc C module refuses to load in
    # subinterpreters (K4), and the lab tasks importing this module run in them.
    import tracemalloc

    # WHY (noqa S311): reproducible test data, not cryptography.
    rng = random.Random(seed)  # noqa: S311
    graph = PreloadGraph(composition=composition, target_mb=mb)
    started = time.monotonic()
    was_tracing = tracemalloc.is_tracing()
    if not was_tracing:
        tracemalloc.start()
    baseline = tracemalloc.get_traced_memory()[0]
    for index in range(SAMPLE_RECORDS):
        _add_record(graph, rng, index)
    per_record = (tracemalloc.get_traced_memory()[0] - baseline) / SAMPLE_RECORDS
    if not was_tracing:
        tracemalloc.stop()
    total = max(SAMPLE_RECORDS, round(mb * MIB / per_record))
    for index in range(SAMPLE_RECORDS, total):
        _add_record(graph, rng, index)
    graph.traced_bytes = round(per_record * total)
    graph.build_s = round(time.monotonic() - started, 3)
    return graph


def ensure_graph(mb: int, composition: str = "mortal") -> PreloadGraph | None:
    """Build the global GRAPH if `mb` > 0 and it doesn't exist yet.

    Why:
        Under fork the master builds it and workers inherit it; under spawn each worker must
        build its own. One function used everywhere makes "inherited or not" automatic.

    Args:
        mb: labs.preload_mb (0 = no graph).
        composition: the composition.

    Returns:
        The graph, or None.
    """
    global GRAPH  # WHY: the graph must be a module global to be shared by fork
    if mb > 0 and GRAPH is None:
        GRAPH = build_graph(
            mb, composition="immortal-heavy" if composition == "immortal-heavy" else "mortal"
        )
    return GRAPH


def touch(graph: PreloadGraph, fraction: float) -> int:
    """Read every value of the first `fraction` of the records.

    Why:
        This is a *read-only* workload in Python terms, yet on the GIL build each read
        increments and decrements the object's reference count, a write to the page holding
        it. That is how reading shared data copies pages (docs/concepts/refcounting.md).

    Args:
        graph: the graph.
        fraction: 0.0–1.0.

    Returns:
        How many records were visited.
    """
    count = math.ceil(max(0.0, min(1.0, fraction)) * len(graph.records))
    total = 0
    for i in range(count):
        record = graph.records[i]
        for value in record.values():
            if isinstance(value, list):
                for item in value:
                    total += item is not None
            elif isinstance(value, dict):
                for item in value.values():
                    total += item is not None
            elif isinstance(value, Owner):
                total += len(value.name) + len(value.perms)
    return count if total >= 0 else 0


def describe(graph: PreloadGraph) -> dict[str, object]:
    """Count the graph's objects per type (for labs/k3/preload_graph.py and the records).

    Why:
        "300 MiB of objects" means little; "1.2 M dicts, 4.8 M str" explains which objects
        the refcount writes land on.

    Args:
        graph: the graph.

    Returns:
        Counts and sizes.
    """
    counts: dict[str, int] = {}
    seen: set[int] = set()
    stack: list[object] = [graph.records]
    while stack:
        obj = stack.pop()
        if id(obj) in seen:
            continue
        seen.add(id(obj))
        name = type(obj).__name__
        counts[name] = counts.get(name, 0) + 1
        if isinstance(obj, list):
            stack.extend(obj)
        elif isinstance(obj, dict):
            stack.extend(obj.values())
        elif isinstance(obj, Owner):
            stack.extend((obj.uid, obj.name, obj.perms))
    immortal = getattr(sys, "_is_immortal", None)
    return {
        "records": len(graph.records),
        "owners": len(graph.owners),
        "composition": graph.composition,
        "target_mb": graph.target_mb,
        "traced_mib": round(graph.traced_bytes / MIB, 1),
        "build_s": graph.build_s,
        "objects_by_type": dict(sorted(counts.items())),
        "immortal_check_available": immortal is not None,
    }
