"""Fitness gate — import-cycle detection (SCC over the module IMPORTS subgraph)
+ the data-driven composable-predicate registry (a new gate is data, not a code edit)."""

from __future__ import annotations

import pytest

from tpa_engine.fitness import (
    PREDICATE_REGISTRY,
    check,
    cycle_count,
    import_cycles,
    parse_gate,
    structural_assertions,
)
from tpa_engine.model import EDGE_IMPORTS, NODE_FUNCTION, NODE_MODULE, CgNode, Graph


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


# ---- composable data-driven gates (OQ6 absorption) -------------------------- #

def _calls_graph(edges: list[tuple[str, str]]) -> Graph:
    g = Graph(corpus="t")
    seen: set[str] = set()
    for s, t in edges:
        for q in (s, t):
            if q not in seen:
                g.add_node(CgNode(q, q.split(".")[-1], type=NODE_FUNCTION,
                                  kind="function", module=q.rsplit(".", 1)[0]))
                seen.add(q)
    for s, t in edges:
        g.add_edge(s, t, "CALLS")
    return g


def test_predicate_registry_expresses_at_least_3_gates():
    # PRE-REGISTERED METRIC (OQ6): n_structural_fitness_gates 1 -> >=3
    assert {"import_cycles", "fan_in", "god_object_loc", "layering"} <= set(PREDICATE_REGISTRY)
    assert len(PREDICATE_REGISTRY) >= 3


def test_new_fan_in_gate_added_as_DATA_red_then_green():
    # 'hot' has 4 distinct callers -> fan_in concentration = 4. The gate is a STRING;
    # no edit to the check() runner was needed to add 'fan_in' as an expressible gate.
    g = _calls_graph([("m.a", "m.hot"), ("m.b", "m.hot"), ("m.c", "m.hot"),
                      ("m.d", "m.hot"), ("m.a", "m.cold")])
    red = check(g, [parse_gate("fan_in:>:3")])      # RED: 4 > 3 -> failed
    assert red[0].failed and red[0].value == 4
    assert ("m.hot", 4) in red[0].offenders
    green = check(g, [parse_gate("fan_in:>:4")])     # GREEN: 4 > 4 is False
    assert not green[0].failed


def test_god_object_loc_gate_as_data():
    g = Graph(corpus="t")
    g.add_node(CgNode("m.big", "big", type=NODE_FUNCTION, kind="function", loc=500))
    g.add_node(CgNode("m.small", "small", type=NODE_FUNCTION, kind="function", loc=10))
    assert check(g, [parse_gate("god_object_loc:>:400")])[0].failed       # 500 > 400
    assert not check(g, [parse_gate("god_object_loc:>:600")])[0].failed   # 500 > 600 False


def test_composition_passes_iff_all_pass():
    g = _calls_graph([("m.a", "m.hot"), ("m.b", "m.hot")])  # fan_in=2, no cycles
    results = check(g, [parse_gate("import_cycles:>:0"), parse_gate("fan_in:>:5")])
    assert not any(r.failed for r in results)  # both pass


def test_unregistered_predicate_raises_with_available_keys():
    with pytest.raises(KeyError):
        check(Graph(corpus="t"), [parse_gate("not_a_predicate:>:0")])


def test_parse_gate_roundtrip_with_arg():
    g = parse_gate("layering:>:0:core,domain,ui")
    assert (g.predicate, g.op, g.threshold, g.arg) == ("layering", ">", 0, "core,domain,ui")
