"""File sinks for a :Cg ``Graph`` — GraphML and node-link JSON, no DB needed.

GraphML is hand-emitted (no networkx dependency) so the ast backend stays
zero-dep. Output is deterministic: nodes/edges are written in the sorted order
``model.Graph`` guarantees, attribute keys are fixed, and there is no
wall-clock or randomness — re-running on the same graph is byte-identical.
"""

from __future__ import annotations

import json
from pathlib import Path
from xml.sax.saxutils import escape

from .model import Graph

# (attr key, python type, graphml type) for node and edge attributes
_NODE_ATTRS = [
    ("name", str, "string"), ("type", str, "string"), ("kind", str, "string"),
    ("module", str, "string"), ("file", str, "string"),
    ("qualified_name", str, "string"), ("lineno", int, "int"), ("loc", int, "int"),
    ("cg_corpus", str, "string"),
]
_EDGE_ATTRS = [
    ("etype", str, "string"), ("weight", int, "int"), ("cg_corpus", str, "string"),
]


def write_graphml(graph: Graph, path: str | Path) -> Path:
    """Write the graph as GraphML to ``path`` (deterministic)."""
    path = Path(path)
    lines: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">',
    ]
    # attribute key declarations
    for i, (name, _pt, gt) in enumerate(_NODE_ATTRS):
        lines.append(f'  <key id="n{i}" for="node" attr.name="{name}" attr.type="{gt}"/>')
    for i, (name, _pt, gt) in enumerate(_EDGE_ATTRS):
        lines.append(f'  <key id="e{i}" for="edge" attr.name="{name}" attr.type="{gt}"/>')
    lines.append(f'  <graph edgedefault="directed" id="{escape(graph.corpus)}">')

    for node in graph.node_list():
        props = node.props()
        props["cg_corpus"] = graph.corpus
        lines.append(f'    <node id="{escape(node.qualified_name)}">')
        for i, (name, _pt, _gt) in enumerate(_NODE_ATTRS):
            val = props.get(name, "")
            lines.append(f'      <data key="n{i}">{escape(str(val))}</data>')
        lines.append("    </node>")

    for j, edge in enumerate(graph.edge_list()):
        vals = {"etype": edge.etype, "weight": edge.weight, "cg_corpus": graph.corpus}
        lines.append(
            f'    <edge id="e{j}" source="{escape(edge.source)}" '
            f'target="{escape(edge.target)}">'
        )
        for i, (name, _pt, _gt) in enumerate(_EDGE_ATTRS):
            lines.append(f'      <data key="e{i}">{escape(str(vals[name]))}</data>')
        lines.append("    </edge>")

    lines.append("  </graph>")
    lines.append("</graphml>")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_json(graph: Graph, path: str | Path) -> Path:
    """Write the graph as deterministic node-link JSON to ``path``."""
    path = Path(path)
    path.write_text(
        json.dumps(graph.to_node_link(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return path
