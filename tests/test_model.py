"""Schema invariants for model.py (the single source of the :Cg ontology)."""

from __future__ import annotations

import pytest

from tpa_engine.model import TYPE_LABEL, CgEdge, CgNode, Graph


def test_node_type_validated():
    with pytest.raises(ValueError):
        CgNode("q", "n", type="Bogus", kind="x")


def test_edge_type_validated():
    with pytest.raises(ValueError):
        CgEdge("a", "b", etype="POINTS_AT")


def test_label_mapping_covers_all_node_types():
    for t in TYPE_LABEL:
        n = CgNode(f"q.{t}", t, type=t, kind="x")
        assert n.label() == TYPE_LABEL[t]


def test_calls_weight_aggregates():
    g = Graph(corpus="c")
    g.add_edge("a", "b", "CALLS", 1)
    g.add_edge("a", "b", "CALLS", 1)
    g.add_edge("a", "b", "CALLS", 1)
    edges = [e for e in g.edge_list() if e.etype == "CALLS"]
    assert len(edges) == 1
    assert edges[0].weight == 3


def test_add_node_first_writer_wins():
    g = Graph(corpus="c")
    a = g.add_node(CgNode("q", "first", type="Function", kind="function"))
    b = g.add_node(CgNode("q", "second", type="Function", kind="function"))
    assert a is b
    assert g.nodes["q"].name == "first"


def test_ensure_stub_materialises_endpoint():
    g = Graph(corpus="c")
    g.ensure_stub("pkg.mod.fn")
    assert "pkg.mod.fn" in g.nodes
    assert g.nodes["pkg.mod.fn"].module == "pkg.mod"


def test_node_link_deterministic_order():
    g = Graph(corpus="c")
    g.add_node(CgNode("z", "z", type="Function", kind="function"))
    g.add_node(CgNode("a", "a", type="Function", kind="function"))
    ids = [n["id"] for n in g.to_node_link()["nodes"]]
    assert ids == ["a", "z"]
