"""Deterministic ast-backend extraction on the tinypkg fixture.

The fixture is fixed source, so node/edge counts are exact constants. These
also lock the honest behaviours: external calls (range/print) are dropped, and
repo-local calls resolve to the right qualified names.
"""

from __future__ import annotations

from pathlib import Path

from codegraph import ast_backend
from codegraph.graphml_sink import write_graphml, write_json

FIXTURES = Path(__file__).parent / "fixtures"


def _build():
    return ast_backend.build_graph(FIXTURES, corpus="tinypkg-test")


def test_node_counts_exact():
    g = _build()
    c = g.counts()
    assert c["nodes"] == {"Module": 3, "Class": 1, "Function": 4}
    assert c["total_nodes"] == 8


def test_edge_counts_exact():
    g = _build()
    c = g.counts()
    assert c["edges"] == {"DEFINES": 5, "IMPORTS": 1, "CALLS": 3}
    assert c["total_edges"] == 9


def test_calls_resolved_to_qualified_names():
    g = _build()
    calls = {(e.source, e.target) for e in g.edge_list() if e.etype == "CALLS"}
    assert ("tinypkg.core.Engine.run", "tinypkg.util.helper") in calls
    assert ("tinypkg.core.Engine.run", "tinypkg.core.Engine.step") in calls
    assert ("tinypkg.core.top_level", "tinypkg.util.helper") in calls


def test_imports_edge_repo_local():
    g = _build()
    imports = {(e.source, e.target) for e in g.edge_list() if e.etype == "IMPORTS"}
    assert imports == {("tinypkg.core", "tinypkg.util")}


def test_external_calls_dropped_and_counted():
    g = _build()
    # 5 call sites seen, 2 external (range, print), 3 resolved, 0 ambiguous
    assert g.stats == {"calls_seen": 5, "calls_resolved": 3,
                       "calls_ambiguous": 0, "calls_external": 2}


def test_determinism_byte_identical(tmp_path):
    g1 = _build()
    g2 = _build()
    p1 = write_json(g1, tmp_path / "a.json")
    p2 = write_json(g2, tmp_path / "b.json")
    assert p1.read_bytes() == p2.read_bytes()


def test_graphml_written_with_sane_content(tmp_path):
    g = _build()
    p = write_graphml(g, tmp_path / "out.graphml")
    text = p.read_text()
    assert text.startswith("<?xml")
    assert text.count("<node ") == 8
    assert text.count("<edge ") == 9
    assert "cg_corpus" in text
