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
import os
import shutil
import subprocess
import tempfile
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


# --------------------------------------------------------------------------- #
# Joern runner — produce the JSON export this module consumes (needs a JVM)
# --------------------------------------------------------------------------- #
# A CPGQL script that dumps exactly the curated ``{nodes, edges}`` shape
# ``build_graph`` reads: NAMESPACE_BLOCK (one per package) / TYPE_DECL / METHOD
# nodes, and CONTAINS (DEFINES) / CALLS / INHERITS / IMPORTS edges. Synthetic
# ``<operator>.*`` calls are dropped; external callees/types are not emitted as
# own-graph nodes, so the importer's identity matching keeps the graph internal.
# ``importCpg`` populates the implicit ``cpg``; ``ujson`` ships on Joern's classpath.
_DUMP_SCRIPT = r"""
@main def main(cpgFile: String, outFile: String): Unit = {
  importCpg(cpgFile)
  def pkg(fn: String): String = if (fn.contains(".")) fn.substring(0, fn.lastIndexOf('.')) else ""
  val tds = cpg.typeDecl.isExternal(false).filterNot(_.filename == "<unknown>").l
  val methods = cpg.method.isExternal(false).filterNot(_.filename == "<empty>").l
  val tdByName = tds.map(t => t.fullName -> t.id).toMap
  val internalMethodIds = methods.map(_.id).toSet
  val filePkg = cpg.namespaceBlock.l.flatMap { n =>
    val fn = n.fullName
    if (fn.contains(":")) Some(n.filename -> fn.substring(fn.lastIndexOf(':') + 1)) else None
  }.toMap

  val packages = tds.map(t => pkg(t.fullName)).filter(_.nonEmpty).distinct
  val nodes = scala.collection.mutable.ArrayBuffer[ujson.Obj]()
  packages.foreach(p => nodes += ujson.Obj(
    "id" -> s"ns:$p", "label" -> "NAMESPACE_BLOCK", "fullName" -> p, "name" -> p.split('.').last))
  tds.foreach(t => nodes += ujson.Obj(
    "id" -> t.id.toString, "label" -> "TYPE_DECL", "fullName" -> t.fullName, "name" -> t.name,
    "filename" -> t.filename, "lineNumber" -> t.lineNumber.map(_.toString).getOrElse("")))
  methods.foreach(m => nodes += ujson.Obj(
    "id" -> m.id.toString, "label" -> "METHOD", "fullName" -> m.fullName, "name" -> m.name,
    "filename" -> m.filename, "lineNumber" -> m.lineNumber.map(_.toString).getOrElse("")))

  val edges = scala.collection.mutable.ArrayBuffer[ujson.Obj]()
  tds.foreach { t => val p = pkg(t.fullName)
    if (p.nonEmpty) edges += ujson.Obj("source" -> s"ns:$p", "target" -> t.id.toString, "label" -> "CONTAINS") }
  methods.foreach { m => m.typeDecl.id.headOption.foreach(tid =>
    edges += ujson.Obj("source" -> tid.toString, "target" -> m.id.toString, "label" -> "CONTAINS")) }
  cpg.call.filterNot(_.methodFullName.startsWith("<operator>")).foreach { c =>
    val callerId = c.method.id
    c.callee.isExternal(false).id.find(internalMethodIds.contains).foreach { cid =>
      if (callerId != cid) edges += ujson.Obj("source" -> callerId.toString, "target" -> cid.toString, "label" -> "CALLS") } }
  tds.foreach { t => t.inheritsFromTypeFullName.foreach { base =>
    tdByName.get(base).foreach(bid =>
      edges += ujson.Obj("source" -> t.id.toString, "target" -> bid.toString, "label" -> "INHERITS")) } }
  cpg.imports.foreach { i =>
    val ent = i.importedEntity.getOrElse("")
    val f = i.file.name.headOption.getOrElse("")
    val p = filePkg.getOrElse(f, "")
    if (p.nonEmpty && tdByName.contains(ent))
      edges += ujson.Obj("source" -> s"ns:$p", "target" -> tdByName(ent).toString, "label" -> "IMPORTS") }

  os.write.over(os.Path(outFile), ujson.write(ujson.Obj("nodes" -> nodes.toList, "edges" -> edges.toList), indent = 2))
  println(s"WROTE_OK ${outFile} nodes=${nodes.size} edges=${edges.size}")
}
"""


def _resolve_joern_home(joern_home: str | None) -> Path:
    """Locate a Joern install dir (the one holding ``joern`` + ``joern-parse``)."""
    if joern_home:
        home = Path(joern_home).resolve()
        if not (home / "joern-parse").exists():
            raise FileNotFoundError(f"--joern-home {home} has no joern-parse")
        return home
    env = os.environ.get("JOERN_HOME")
    if env and (Path(env) / "joern-parse").exists():
        return Path(env).resolve()
    on_path = shutil.which("joern-parse")
    if on_path:
        return Path(on_path).resolve().parent
    raise FileNotFoundError(
        "joern not found. Pass --joern-home <joern-cli dir>, set JOERN_HOME, or put "
        "joern-parse on PATH. Install: https://docs.joern.io/installation (needs a JVM; "
        "set JAVA_HOME if joern reports 'No java installations detected')."
    )


def run_joern(repo_path: str, *, joern_home: str | None = None,
              language: str | None = None, workdir: str | None = None) -> str:
    """Run ``joern-parse`` + the bundled CPGQL dump; return the export JSON path.

    The export is exactly the ``{nodes, edges}`` shape :func:`build_graph` reads, so the
    auto-run path and the ``--joern-export`` path share one importer. ``language`` is a
    ``joern-parse --language`` frontend (e.g. ``javasrc``, ``c``, ``jssrc``, ``pythonsrc``);
    ``None`` lets joern-parse auto-detect. Needs a JVM — a missing ``JAVA_HOME`` surfaces as
    joern's own "No java installations detected" error.
    """
    home = _resolve_joern_home(joern_home)
    repo = Path(repo_path).resolve()
    work = Path(workdir).resolve() if workdir else Path(tempfile.mkdtemp(prefix="tpa_joern_"))
    work.mkdir(parents=True, exist_ok=True)
    cpg = work / "cpg.bin"
    export = work / "joern_export.json"
    script = work / "joern_dump.sc"
    script.write_text(_DUMP_SCRIPT, encoding="utf-8")

    parse_cmd = [str(home / "joern-parse"), str(repo), "--output", str(cpg)]
    if language:
        parse_cmd += ["--language", language]
    # cwd=work: ``joern --script`` (importCpg) drops a ``workspace/`` dir in CWD — keep it
    # in the throwaway workdir instead of polluting the caller's repo.
    subprocess.run(parse_cmd, check=True, cwd=str(work))
    subprocess.run(
        [str(home / "joern"), "--script", str(script),
         "--param", f"cpgFile={cpg}", "--param", f"outFile={export}"],
        check=True, cwd=str(work),
    )
    if not export.exists():
        raise RuntimeError(f"joern dump produced no export at {export}")
    return str(export)
