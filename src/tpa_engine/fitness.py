"""Fitness functions over a :Cg graph — structural assertions for CI gates.

This is the closure: the engine-dissection tool turned back on its own author. Run
``tpa_engine check`` in CI and fail the build on a structural regression (a reintroduced
import cycle). The ast backend is deterministic and zero-dependency, so an "import
cycle count" is a *fact about the source*, not an LLM opinion — a trustworthy gate,
the same role import-linter's independence/layers contracts play.

The metric: strongly-connected components (size > 1) of the module IMPORTS subgraph.
An SCC with more than one module is a genuine cyclic dependency; a self-import is
ignored. Both the cycle list and each cycle are sorted, so output is fully
deterministic (byte-stable across runs, matching the tool's determinism contract).
"""
from __future__ import annotations

import operator
from collections.abc import Callable
from dataclasses import dataclass

from .model import EDGE_CALLS, EDGE_IMPORTS, NODE_MODULE, Graph


def _module_adjacency(graph: Graph) -> dict[str, list[str]]:
    """Module qualified_name -> sorted module targets, over IMPORTS edges only.

    Restricted to module->module edges (drops function/class nodes and self-loops),
    which is the dependency graph whose cycles we police.
    """
    modules = {n.qualified_name for n in graph.nodes.values() if n.type == NODE_MODULE}
    adj: dict[str, set[str]] = {m: set() for m in modules}
    for e in graph.edges.values():
        if (e.etype == EDGE_IMPORTS and e.source in modules
                and e.target in modules and e.source != e.target):
            adj[e.source].add(e.target)
    return {m: sorted(targets) for m, targets in adj.items()}


def import_cycles(graph: Graph) -> list[list[str]]:
    """All import cycles = strongly-connected components (size > 1) of the module
    IMPORTS subgraph, each a sorted list of qualified_names, the whole list sorted.

    Iterative Tarjan (no recursion-limit risk on large repos).
    """
    adj = _module_adjacency(graph)
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    counter = 0
    sccs: list[list[str]] = []

    for root in sorted(adj):
        if root in index:
            continue
        # explicit work stack of (node, next-neighbour-position) frames
        work: list[list] = [[root, 0]]
        while work:
            frame = work[-1]
            v, i = frame[0], frame[1]
            if i == 0:
                index[v] = low[v] = counter
                counter += 1
                stack.append(v)
                on_stack.add(v)
            neighbours = adj[v]
            if i < len(neighbours):
                frame[1] = i + 1
                w = neighbours[i]
                if w not in index:
                    work.append([w, 0])
                elif w in on_stack:
                    low[v] = min(low[v], index[w])
            else:
                if low[v] == index[v]:  # v is an SCC root — pop the component
                    comp: list[str] = []
                    while True:
                        w = stack.pop()
                        on_stack.discard(w)
                        comp.append(w)
                        if w == v:
                            break
                    sccs.append(sorted(comp))
                work.pop()
                if work:  # propagate low-link up to the parent frame
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[v])
    return sorted(c for c in sccs if len(c) > 1)


def cycle_count(graph: Graph) -> int:
    """Number of import cycles (SCCs with > 1 module)."""
    return len(import_cycles(graph))


def structural_assertions(graph: Graph) -> list[str]:
    """Future home for richer structural gates — e.g. "every gate check-key dispatched
    in ``evaluate()`` resolves to a registered handler" (the registry-bypass assertion).

    That assertion needs type-precise edges from the **scip** backend; the ast backend
    cannot see string/registry dispatch (README "Honest limits"). So this returns ``[]``
    today — a *documented* stub, NOT a silent no-op gate. Wire it when the scip backend
    feeds CI.
    """
    return []


# ---- composable structural fitness gates (data-driven) ---------------------- #
# The single hardcoded cycle gate generalizes into a REGISTRY of structural predicates
# over the :Cg graph — CodeQL's "structural property = query over relations" /
# jQAssistant's "rules-as-data", realized via the SAME string-keyed single-dispatch
# registry ooptdd already evolved for its trace-check predicates (gate.CHECK_REGISTRY).
# A gate is DATA — ``(predicate, op, threshold)`` — so a NEW gate is added by registering
# a predicate, never by editing the check() runner below.

# A predicate maps a graph (+ optional arg) -> (metric_value, offenders), where offenders
# is a sorted list of (qualified_name, contribution). Deterministic by construction.
PREDICATE_REGISTRY: dict[str, Callable] = {}


def predicate(name: str) -> Callable:
    """Register a structural predicate under ``name``. Decoration-time only (a dict
    insert). A duplicate name raises — guarding silent overwrite (mirrors gate.check)."""
    def deco(fn: Callable) -> Callable:
        if name in PREDICATE_REGISTRY:
            raise ValueError(f"duplicate predicate {name!r}")
        PREDICATE_REGISTRY[name] = fn
        return fn
    return deco


@predicate("import_cycles")
def _p_import_cycles(graph: Graph, arg: str | None = None) -> tuple[int, list]:
    cycles = import_cycles(graph)
    return len(cycles), [(" <-> ".join(c), len(c)) for c in cycles]


@predicate("fan_in")
def _p_fan_in(graph: Graph, arg: str | None = None) -> tuple[int, list]:
    """Fan-in concentration: max number of DISTINCT callers of any one function."""
    inbound: dict[str, set[str]] = {}
    for e in graph.edges.values():
        if e.etype == EDGE_CALLS and e.source != e.target:
            inbound.setdefault(e.target, set()).add(e.source)
    counts = sorted(((t, len(srcs)) for t, srcs in inbound.items()),
                    key=lambda x: (-x[1], x[0]))
    return (counts[0][1] if counts else 0), counts


@predicate("god_object_loc")
def _p_god_object_loc(graph: Graph, arg: str | None = None) -> tuple[int, list]:
    """God-object LOC: max ``loc`` over all nodes (the biggest single unit)."""
    locs = sorted(((n.qualified_name, n.loc) for n in graph.nodes.values() if n.loc),
                  key=lambda x: (-x[1], x[0]))
    return (locs[0][1] if locs else 0), locs


@predicate("layering")
def _p_layering(graph: Graph, arg: str | None = None) -> tuple[int, list]:
    """Layering violations: count IMPORTS edges that go AGAINST a declared module order.

    ``arg`` = comma-separated layer keys, lowest first (e.g. 'core,domain,adapters,ui').
    A module is ranked by the first key it contains. An import from a LOWER layer to a
    HIGHER one is a violation (the back-edge)."""
    order = [k for k in (arg.split(",") if arg else []) if k]

    def rank(module: str) -> int:
        for i, key in enumerate(order):
            if key in module:
                return i
        return len(order)

    offenders = []
    for e in graph.edges.values():
        if e.etype == EDGE_IMPORTS and e.source != e.target:
            if rank(e.source) < rank(e.target):
                offenders.append((f"{e.source} -> {e.target}", 1))
    offenders.sort()
    return len(offenders), offenders


_GATE_OPS = {">": operator.gt, ">=": operator.ge, "<": operator.lt,
             "<=": operator.le, "==": operator.eq, "!=": operator.ne}


@dataclass(frozen=True)
class Gate:
    """A structural fitness gate as DATA. The gate FAILS when ``predicate(graph) op
    threshold`` is true — e.g. ``import_cycles:>:0`` fails when cycles > 0,
    ``fan_in:>:10`` fails when the worst fan-in exceeds 10."""

    predicate: str
    op: str
    threshold: int
    arg: str | None = None


@dataclass(frozen=True)
class GateResult:
    gate: Gate
    value: int
    offenders: list
    failed: bool


def parse_gate(spec: str) -> Gate:
    """Parse the data wire format ``predicate:op:threshold[:arg]`` —
    e.g. ``fan_in:>:3`` or ``layering:>:0:core,domain,ui``."""
    parts = spec.split(":", 3)
    if len(parts) < 3:
        raise ValueError(f"gate must be predicate:op:threshold[:arg], got {spec!r}")
    arg = parts[3] if len(parts) == 4 else None
    return Gate(parts[0], parts[1], int(parts[2]), arg)


def check(graph: Graph, gates: list[Gate]) -> list[GateResult]:
    """Run a composition of data-driven structural gates; pass iff EVERY gate passes.

    Single dispatch through ``PREDICATE_REGISTRY`` — adding a new structural gate is a
    new ``@predicate`` registration, never an edit to this runner. An unregistered
    predicate raises ``KeyError`` with the available keys."""
    results: list[GateResult] = []
    for g in gates:
        try:
            fn = PREDICATE_REGISTRY[g.predicate]
        except KeyError:
            have = ", ".join(sorted(PREDICATE_REGISTRY)) or "none"
            raise KeyError(f"unknown predicate {g.predicate!r} (have: {have})") from None
        value, offenders = fn(graph, g.arg)
        results.append(GateResult(g, value, offenders, bool(_GATE_OPS[g.op](value, g.threshold))))
    return results
