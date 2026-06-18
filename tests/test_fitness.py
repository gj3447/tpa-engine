"""Fitness gate — import-cycle detection (SCC over the module IMPORTS subgraph)."""

from __future__ import annotations

from tpa_engine.fitness import cycle_count, import_cycles, structural_assertions
from tpa_engine.model import EDGE_IMPORTS, NODE_MODULE, CgNode, Graph


def _mod_graph(edges: list[tuple[str, str]], etype: str = EDGE_IMPORTS) -> Graph:
    g = Graph(corpus="t")
    names: set[str] = set()
    for s, t in edges:
        names.add(s)
        names.add(t)
    for m in sorted(names):
        g.add_node(CgNode(m, m.split(".")[-1], type=NODE_MODULE, kind="module"))
    for s, t in edges:
        g.add_edge(s, t, etype)
    return g


def test_dag_has_no_cycles():
    g = _mod_graph([("a.x", "a.y"), ("a.y", "a.z")])
    assert import_cycles(g) == []
    assert cycle_count(g) == 0


def test_two_module_cycle_detected():
    g = _mod_graph([("a.x", "a.y"), ("a.y", "a.x")])
    assert import_cycles(g) == [["a.x", "a.y"]]
    assert cycle_count(g) == 1


def test_three_module_cycle_detected():
    g = _mod_graph([("a", "b"), ("b", "c"), ("c", "a")])
    assert import_cycles(g) == [["a", "b", "c"]]


def test_self_import_is_not_a_cycle():
    g = _mod_graph([("a", "a")])
    assert cycle_count(g) == 0


def test_two_independent_cycles_are_sorted_and_deterministic():
    g = _mod_graph([("x", "y"), ("y", "x"), ("a", "b"), ("b", "a")])
    assert import_cycles(g) == [["a", "b"], ["x", "y"]]
    # byte-stable across repeated runs (determinism contract)
    assert import_cycles(g) == import_cycles(g)


def test_only_imports_edges_count_not_calls():
    # the SAME mutual edges as CALLS must NOT register as an import cycle
    g = _mod_graph([("a", "b"), ("b", "a")], etype="CALLS")
    assert cycle_count(g) == 0


def test_structural_assertions_is_documented_stub():
    # returns [] today (needs scip type edges) — a documented stub, not a silent gate
    g = _mod_graph([("a", "b")])
    assert structural_assertions(g) == []
