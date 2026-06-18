"""Schema-conformance validator (OQ4) — the drift test. validate() mechanically catches an
off-ontology emission (a dangling edge) that construction-time guards do not surface."""
from __future__ import annotations

from tpa_engine.model import CgNode, Graph, validate


def test_clean_graph_conforms():
    g = Graph(corpus="c")
    g.add_node(CgNode("m", "m", type="Module", kind="module"))
    g.add_node(CgNode("m.f", "f", type="Function", kind="function"))
    g.add_edge("m", "m.f", "DEFINES")
    assert validate(g) == []


def test_dangling_edge_is_a_typed_failure():
    g = Graph(corpus="c")
    g.add_node(CgNode("a", "a", type="Function", kind="function"))
    g.add_edge("a", "GHOST", "CALLS")  # GHOST never added — silently accepted by add_edge
    vs = validate(g)
    assert any(v.kind == "dangling_edge" and "GHOST" in v.detail for v in vs)


def test_validator_is_deterministic():
    g = Graph(corpus="c")
    g.add_node(CgNode("a", "a", type="Function", kind="function"))
    g.add_edge("a", "X", "CALLS")
    g.add_edge("a", "Y", "CALLS")
    assert validate(g) == validate(g)
