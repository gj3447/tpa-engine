from __future__ import annotations

from pathlib import Path

from tpa_engine.frontends import scala_static
from tpa_engine.model import (
    EDGE_CALLS,
    EDGE_DECORATES,
    EDGE_DEFINES,
    EDGE_IMPORTS,
    EDGE_INHERITS,
    EDGE_REFERENCES,
    validate,
)

FIXTURES = Path(__file__).parent / "fixtures" / "scalapkg"


def test_scala_source_static_edges_are_extracted():
    g = scala_static.build_graph(FIXTURES, corpus="scala-test")
    edges = {(e.source, e.etype, e.target) for e in g.edge_list()}

    worker = "com.acme.impl.Impl.Worker"
    run = "com.acme.impl.Impl.Worker.run"
    helper = "com.acme.impl.Impl.helper"

    assert ("com.acme.impl.Impl", EDGE_DEFINES, worker) in edges
    assert (worker, EDGE_DEFINES, run) in edges
    assert (worker, EDGE_INHERITS, "external:Base") in edges
    assert (worker, EDGE_DECORATES, "external:service") in edges
    assert (run, EDGE_DECORATES, "external:traced") in edges
    assert (run, EDGE_CALLS, helper) in edges
    assert (run, EDGE_REFERENCES, "com.acme.Base.Base") in edges
    assert any(e[1] == EDGE_IMPORTS and e[0] == "com.acme.impl.Impl" for e in edges)


def test_scala_source_static_graph_conforms_to_schema():
    g = scala_static.build_graph(FIXTURES, corpus="scala-test")
    assert validate(g) == []
    assert g.stats["type_declarations"] >= 2
    assert g.stats["defs"] >= 3
