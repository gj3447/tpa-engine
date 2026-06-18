"""Sink registry — one ``GraphSink`` Protocol + a string-keyed dispatch registry.

Each output target (neo4j / graphml / json) is a registered sink dispatched by a lookup,
not a 3-way ``if args.out`` branch — so a new sink is a registration, not a CLI edit
(structural mirror of ``backends.py``). The sink bodies stay in ``neo4j_sink``/``graphml_sink``;
the adapters only provide the uniform Protocol seam plus a read-back on the neo4j sink (the
prerequisite for incremental load-then-verify round-trips). This is blarify's
``AbstractDbManager`` (save + query behind one Port) and jQAssistant's ``Store`` interface,
translated to Python via the same registry idiom the backends already use.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from .model import Graph


@runtime_checkable
class GraphSink(Protocol):
    """Write a :Cg ``Graph`` to an output target."""

    name: str

    def write(self, graph: Graph, **opts) -> object: ...


SINKS: dict[str, GraphSink] = {}


def register(sink: GraphSink) -> GraphSink:
    """Register a sink under its ``name`` (duplicate-key guard, mirrors backends.register)."""
    if sink.name in SINKS:
        raise ValueError(f"duplicate sink {sink.name!r}")
    SINKS[sink.name] = sink
    return sink


@dataclass(frozen=True)
class Neo4jSink:
    name: str = "neo4j"

    def write(self, graph: Graph, *, uri, user, password, database=None) -> dict:
        from . import neo4j_sink
        return neo4j_sink.load(graph, uri=uri, user=user, password=password,
                               database=database)

    def count(self, corpus: str, *, uri, user, password, database=None) -> int:
        """Read-back: number of :Cg nodes for ``corpus`` currently in the store."""
        from . import neo4j_sink
        return neo4j_sink.count(corpus, uri=uri, user=user, password=password,
                                database=database)


@dataclass(frozen=True)
class GraphmlSink:
    name: str = "graphml"

    def write(self, graph: Graph, *, path) -> Path:
        from . import graphml_sink
        return graphml_sink.write_graphml(graph, path)


@dataclass(frozen=True)
class JsonSink:
    name: str = "json"

    def write(self, graph: Graph, *, path) -> Path:
        from . import graphml_sink
        return graphml_sink.write_json(graph, path)


register(Neo4jSink())
register(GraphmlSink())
register(JsonSink())
