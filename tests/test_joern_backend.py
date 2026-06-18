from __future__ import annotations

from pathlib import Path

from tpa_engine import joern_backend
from tpa_engine.model import (
    EDGE_CALLS,
    EDGE_DEFINES,
    EDGE_IMPORTS,
    EDGE_INHERITS,
    NODE_CLASS,
    NODE_FUNCTION,
    NODE_MODULE,
    validate,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_joern_export_normalizes_to_cg_graph():
    g = joern_backend.build_graph(FIXTURES / "joern_export.json", corpus="joern-test")
    edges = {(e.source, e.etype, e.target) for e in g.edge_list()}

    assert g.nodes["src.main.scala.com.acme.App"].type == NODE_MODULE
    assert g.nodes["com.acme.App"].type == NODE_CLASS
    assert g.nodes["com.acme.App.run"].type == NODE_FUNCTION
    assert ("src.main.scala.com.acme.App", EDGE_DEFINES, "com.acme.App") in edges
    assert ("com.acme.App", EDGE_DEFINES, "com.acme.App.run") in edges
    assert ("com.acme.App", EDGE_INHERITS, "com.acme.Base") in edges
    assert ("com.acme.App.run", EDGE_CALLS, "com.acme.Greeter.greet") in edges
    assert ("src.main.scala.com.acme.App", EDGE_IMPORTS, "com.acme.Greeter") in edges
    assert validate(g) == []
    assert g.stats["nodes_imported"] == 6
    assert g.stats["edges_imported"] == 7


def test_joern_raw_artifact_is_optional_copy(tmp_path):
    raw_out = tmp_path / "joern_raw.json"
    g = joern_backend.build_graph(
        FIXTURES / "joern_export.json", corpus="joern-test", raw_out=raw_out)

    assert raw_out.read_text(encoding="utf-8").startswith("{")
    assert g.stats["raw_artifact"] == str(raw_out)
