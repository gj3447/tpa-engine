"""Sink conformance (OQ9) — one parameterized test over the SINKS registry; a new sink is
data, not a cli._emit edit. Plus the read-back seam (SECONDARY metric 0 -> >=1)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from tpa_engine import neo4j_sink
from tpa_engine.cli import build_parser
from tpa_engine.model import Graph
from tpa_engine.sinks import SINKS, GraphSink, register


@dataclass(frozen=True)
class _FakeSink:
    name: str = "fake"

    def write(self, graph, **opts):
        return ("wrote", graph.corpus, opts)


def test_a_new_sink_registers_as_data_not_a_cli_edit():
    # the point: a 4th sink needs ZERO cli._emit edit — just register().
    try:
        register(_FakeSink())
        assert "fake" in SINKS and isinstance(SINKS["fake"], GraphSink)
        out = SINKS["fake"].write(Graph(corpus="c"), path="x")
        assert out[0] == "wrote" and out[1] == "c"
    finally:
        SINKS.pop("fake", None)


@pytest.mark.parametrize("name", sorted(SINKS))  # neo4j/graphml/json -> 3, ONE shared body
def test_every_sink_satisfies_the_protocol(name):
    sink = SINKS[name]
    assert isinstance(sink, GraphSink)
    assert sink.name == name and callable(sink.write)


def test_neo4j_sink_has_read_back_seam():
    # SECONDARY metric 0 -> >=1: a read-back method exists (the incremental prerequisite).
    assert hasattr(SINKS["neo4j"], "count") and callable(SINKS["neo4j"].count)
    assert callable(neo4j_sink.count)


def test_mcp_neo4j_sink_has_read_back_seam():
    assert hasattr(SINKS["mcp-neo4j"], "count") and callable(SINKS["mcp-neo4j"].count)


def test_out_choices_derive_from_registry():
    # drift guard: --out choices == registry keys (no offered-but-missing / registered-but-unreachable)
    p = build_parser()
    for name in SINKS:
        ns = p.parse_args(["index", ".", "--corpus", "c", "--out", name])
        assert ns.out == name
    with pytest.raises(SystemExit):
        p.parse_args(["index", ".", "--corpus", "c", "--out", "nope"])
