from __future__ import annotations

from pathlib import Path

from tpa_engine.frontends import python_static
from tpa_engine.model import (
    EDGE_ASSIGNS,
    EDGE_CALLS,
    EDGE_DECORATES,
    EDGE_INHERITS,
    EDGE_REFERENCES,
    validate,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _build():
    return python_static.build_graph(FIXTURES, corpus="static-test")


def test_richer_static_edges_are_extracted():
    g = _build()
    edges = {(e.source, e.etype, e.target) for e in g.edge_list()}

    assert ("staticpkg.impl.Worker", EDGE_INHERITS, "staticpkg.base.Base") in edges
    assert ("staticpkg.impl.Worker.run", EDGE_DECORATES, "staticpkg.base.traced") in edges
    assert ("staticpkg.impl.Worker.run", EDGE_CALLS, "staticpkg.impl.helper") in edges
    assert ("staticpkg.impl", EDGE_ASSIGNS, "staticpkg.impl.MODULE_CONST") in edges
    assert ("staticpkg.impl.Worker.run", EDGE_ASSIGNS,
            "staticpkg.impl.Worker.run.local_value") in edges
    assert ("staticpkg.impl.Worker.run", EDGE_REFERENCES,
            "staticpkg.impl.MODULE_CONST") in edges


def test_richer_static_graph_conforms_to_schema():
    g = _build()
    assert validate(g) == []
    counts = g.counts()
    assert counts["edges"][EDGE_INHERITS] == 1
    assert counts["edges"][EDGE_DECORATES] == 1
    assert counts["edges"][EDGE_ASSIGNS] >= 2
    assert counts["edges"][EDGE_REFERENCES] >= 1
    assert g.stats["inheritance_edges"] == 1
    assert g.stats["decorator_edges"] == 1
