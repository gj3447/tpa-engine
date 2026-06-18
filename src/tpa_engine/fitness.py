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

from .model import EDGE_IMPORTS, NODE_MODULE, Graph


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
