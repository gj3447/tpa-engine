"""Joern JSON CPG export -> owned :Cg graph importer.

This module deliberately treats Joern as an input adapter, not as the storage or
query contract. It accepts a small, stable JSON shape:

    {"nodes": [{"id": 1, "label": "METHOD", ...}], "edges": [...]}

Node fields may use either Joern-style names (``fullName``, ``lineNumber``) or
snake_case aliases. Edges may use ``label``, ``type``, or ``etype``. The importer
normalizes recognized facts into ``model.Graph`` and drops raw schema details
into scalar ``attr_joern_*`` properties for provenance.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .model import (
    EDGE_CALLS,
    EDGE_DEFINES,
    EDGE_IMPORTS,
    EDGE_INHERITS,
    EDGE_REFERENCES,
    NODE_CLASS,
    NODE_FUNCTION,
    NODE_MODULE,
    NODE_TERM,
    CgNode,
    Graph,
    validate,
)

_MODULE_LABELS = {"FILE", "NAMESPACE", "NAMESPACE_BLOCK", "PACKAGE", "MODULE"}
_CLASS_LABELS = {"TYPE_DECL", "TYPE", "CLASS", "TRAIT", "OBJECT", "ENUM"}
_FUNCTION_LABELS = {"METHOD", "FUNCTION", "LAMBDA"}
_TERM_LABELS = {"MEMBER", "LOCAL", "IDENTIFIER", "PARAMETER"}

_EDGE_MAP = {
    "CALL": EDGE_CALLS,
    "CALLS": EDGE_CALLS,
    "CONTAINS": EDGE_DEFINES,
    "DEFINES": EDGE_DEFINES,
    "AST": EDGE_DEFINES,
    "IMPORT": EDGE_IMPORTS,
    "IMPORTS": EDGE_IMPORTS,
    "INCLUDE": EDGE_IMPORTS,
    "INHERITS": EDGE_INHERITS,
    "EXTENDS": EDGE_INHERITS,
    "BINDS": EDGE_INHERITS,
    "REF": EDGE_REFERENCES,
    "REFERENCES": EDGE_REFERENCES,
    "REACHING_DEF": EDGE_REFERENCES,
}


def build_graph(export_path: Path, *, corpus: str, raw_out: Path | None = None) -> Graph:
    """Read a Joern JSON export and normalize recognized facts into ``Graph``."""

    export_path = Path(export_path).resolve()
    data = json.loads(export_path.read_text(encoding="utf-8"))
    if raw_out:
        raw_out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(export_path, raw_out)

    raw_nodes = _raw_nodes(data)
    raw_edges = _raw_edges(data)
    g = Graph(corpus=corpus)
    id_to_qn: dict[str, str] = {}
    stats = {
        "raw_nodes": len(raw_nodes),
        "raw_edges": len(raw_edges),
        "nodes_imported": 0,
        "edges_imported": 0,
        "raw_artifact": str(raw_out) if raw_out else "",
    }

    for raw in raw_nodes:
        node = _node(raw)
        if not node:
            continue
        g.add_node(node)
        raw_id = _value(raw, "id", "_id", "key")
        if raw_id is not None:
            id_to_qn[str(raw_id)] = node.qualified_name
        stats["nodes_imported"] += 1

    call_node_sources = _call_node_sources(raw_nodes, raw_edges)
    for raw in raw_edges:
        raw_etype = str(_value(raw, "label", "type", "etype", "name") or "").upper()
        etype = _EDGE_MAP.get(raw_etype)
        if not etype:
            continue
        source = _edge_endpoint(raw, "source", "src", "outV", "from", "tail")
        target = _edge_endpoint(raw, "target", "dst", "inV", "to", "head")
        target_qn = id_to_qn.get(str(target))

        if etype == EDGE_CALLS:
            owner_id = call_node_sources.get(str(source))
            source_qn = id_to_qn.get(owner_id) if owner_id else id_to_qn.get(str(source))
        else:
            source_qn = id_to_qn.get(str(source))
        if source_qn not in g.nodes or target_qn not in g.nodes:
            continue
        if source_qn == target_qn and etype == EDGE_DEFINES:
            continue
        g.add_edge(source_qn, target_qn, etype, weight=_int(_value(raw, "weight"), 1))
        stats["edges_imported"] += 1

    violations = validate(g)
    if violations:
        refs = ", ".join(v.ref for v in violations[:5])
        raise ValueError(f"joern import produced invalid :Cg graph: {refs}")
    g.stats = stats
    return g


def _raw_nodes(data: Any) -> list[dict]:
    if isinstance(data, dict):
        nodes = data.get("nodes") or data.get("vertices") or []
    elif isinstance(data, list):
        nodes = [x for x in data if isinstance(x, dict) and "source" not in x and "target" not in x]
    else:
        nodes = []
    return [n for n in nodes if isinstance(n, dict)]


def _raw_edges(data: Any) -> list[dict]:
    if isinstance(data, dict):
        edges = data.get("edges") or data.get("links") or []
    elif isinstance(data, list):
        edges = [x for x in data if isinstance(x, dict) and ("source" in x or "target" in x)]
    else:
        edges = []
    return [e for e in edges if isinstance(e, dict)]


def _node(raw: dict) -> CgNode | None:
    label = _label(raw)
    node_type, kind = _kind(label)
    if not node_type:
        return None
    qn = _qualified_name(raw, label)
    if not qn:
        return None
    return CgNode(
        qualified_name=qn,
        name=str(_value(raw, "name", "NAME") or qn.rsplit(".", 1)[-1]),
        type=node_type,
        kind=kind,
        module=str(_value(raw, "module", "namespace", "packageName", "filename") or ""),
        file=str(_value(raw, "filename", "file", "FILENAME") or ""),
        lineno=_int(_value(raw, "lineNumber", "line_number", "lineno", "LINE_NUMBER"), 0),
        loc=_int(_value(raw, "loc", "LINE_COUNT"), 0),
        attrs=_attrs(raw, label),
    )


def _kind(label: str) -> tuple[str | None, str]:
    if label in _MODULE_LABELS:
        return NODE_MODULE, "module"
    if label in _CLASS_LABELS:
        return NODE_CLASS, label.lower()
    if label in _FUNCTION_LABELS:
        return NODE_FUNCTION, "method" if label == "METHOD" else "function"
    if label in _TERM_LABELS:
        return NODE_TERM, label.lower()
    return None, ""


def _qualified_name(raw: dict, label: str) -> str:
    if label in _MODULE_LABELS:
        filename = _value(raw, "filename", "file", "name")
        qn = _value(raw, "fullName", "full_name", "qualified_name", "name")
        return _clean_qn(qn or filename)
    return _clean_qn(_value(raw, "fullName", "full_name", "qualified_name", "name"))


def _call_node_sources(raw_nodes: list[dict], raw_edges: list[dict]) -> dict[str, str]:
    """Map Joern CALL node id -> enclosing mapped node id via AST/CONTAINS edges."""

    call_ids = {
        str(_value(n, "id", "_id", "key")) for n in raw_nodes
        if _label(n) == "CALL" and _value(n, "id", "_id", "key") is not None
    }
    out: dict[str, str] = {}
    for edge in raw_edges:
        etype = str(_value(edge, "label", "type", "etype", "name") or "").upper()
        if etype not in {"AST", "CONTAINS", "DEFINES"}:
            continue
        source = _edge_endpoint(edge, "source", "src", "outV", "from", "tail")
        target = _edge_endpoint(edge, "target", "dst", "inV", "to", "head")
        if str(target) in call_ids and source is not None:
            out[str(target)] = str(source)
    return out


def _attrs(raw: dict, label: str) -> dict:
    out = {"joern_label": label}
    for key in ("id", "_id", "signature", "code", "parserTypeName", "order"):
        value = _value(raw, key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            out[f"joern_{key.lstrip('_')}"] = value
    return out


def _label(raw: dict) -> str:
    labels = _value(raw, "labels", "label", "type", "_label")
    if isinstance(labels, list) and labels:
        return str(labels[-1]).upper()
    return str(labels or "").upper()


def _value(raw: dict, *keys: str) -> Any:
    for key in keys:
        if key in raw:
            return raw[key]
        upper = key.upper()
        if upper in raw:
            return raw[upper]
    return None


def _edge_endpoint(raw: dict, *keys: str) -> Any:
    value = _value(raw, *keys)
    if isinstance(value, dict):
        return _value(value, "id", "_id", "key")
    return value


def _clean_qn(value: Any) -> str:
    text = str(value or "").strip()
    return text.replace("/", ".").replace("\\", ".").removesuffix(".scala").removesuffix(".java")


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
