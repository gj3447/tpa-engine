"""Deterministic Scala source frontend for large Scala repos such as Joern.

This is not a Scala compiler. It is a conservative source-structure extractor
that turns packages, source files, type declarations, methods, imports, and
simple call/reference spellings into the shared :Cg graph. The purpose is to
make Scala projects agent-queryable in the owned KG when Joern itself cannot
parse Scala source.
"""

from __future__ import annotations

import re
from pathlib import Path

from tpa_engine.model import (
    EDGE_CALLS,
    EDGE_DECORATES,
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
)

_PACKAGE_RE = re.compile(r"^\s*package\s+([A-Za-z_][A-Za-z0-9_.]*)")
_IMPORT_RE = re.compile(r"^\s*import\s+(.+)")
_TYPE_RE = re.compile(
    r"^\s*(?:private\s+|protected\s+|sealed\s+|abstract\s+|final\s+|case\s+)*"
    r"(class|object|trait|enum)\s+([A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\s*(?:extends|with)\s+([A-Za-z_][A-Za-z0-9_.]*))?"
)
_DEF_RE = re.compile(
    r"^\s*(?:private\s+|protected\s+|override\s+|final\s+|abstract\s+|implicit\s+)*"
    r"def\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:\[|\()?"
)
_ANNOTATION_RE = re.compile(r"^\s*@([A-Za-z_][A-Za-z0-9_.]*)")
_CALL_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_REF_RE = re.compile(r"\b([A-Z][A-Za-z0-9_]*(?:\.[A-Z][A-Za-z0-9_]*)*)\b")
_KEYWORDS = {
    "if", "for", "while", "match", "catch", "try", "new", "return", "throw",
    "Some", "None", "Left", "Right", "Option", "Seq", "List", "Set", "Map",
}


def _module_name(path: Path, root: Path, package_name: str) -> str:
    rel = path.relative_to(root).with_suffix("")
    suffix = ".".join(part for part in rel.parts if part not in {"src", "main", "test", "scala"})
    if package_name and suffix:
        tail = suffix.rsplit(".", 1)[-1]
        return f"{package_name}.{tail}"
    return package_name or suffix.replace("/", ".")


def _clean_import(raw: str) -> str:
    return raw.strip().rstrip(";").replace("{", "").replace("}", "").split(",")[0].strip()


def _ensure_external(g: Graph, qn: str, *, type: str = NODE_TERM, kind: str = "external") -> None:
    if qn in g.nodes:
        return
    name = qn.removeprefix("external:").rsplit(".", 1)[-1]
    g.add_node(CgNode(qn, name, type, kind, attrs={"external": True}))


def build_graph(src_root: Path, corpus: str) -> Graph:
    """Extract a :Cg graph from Scala/SBT/Java source files using regex scanning."""
    src_root = Path(src_root).resolve()
    files = [
        p for p in sorted(src_root.rglob("*"))
        if p.is_file() and ".git" not in p.parts and p.suffix in {".scala", ".sbt", ".java"}
    ]
    g = Graph(corpus=corpus)
    simple_defs: dict[str, set[str]] = {}
    pending_calls: list[tuple[str, str]] = []
    pending_refs: list[tuple[str, str]] = []
    stats = {
        "files": len(files),
        "type_declarations": 0,
        "defs": 0,
        "imports": 0,
        "calls_seen": 0,
        "calls_resolved": 0,
        "references": 0,
        "external_symbols": 0,
    }

    for path in files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        lines = text.splitlines()
        package_name = "(default)"
        for line in lines:
            m = _PACKAGE_RE.match(line)
            if m:
                package_name = m.group(1)
                break
        module_qn = _module_name(path, src_root, package_name)
        g.add_node(CgNode(
            module_qn, module_qn.rsplit(".", 1)[-1], NODE_MODULE, "module",
            module=package_name, file=str(path), lineno=1, loc=len(lines),
        ))

        scope = module_qn
        type_indent: int | None = None
        active_func: str | None = None
        last_annotations: list[str] = []
        for lineno, line in enumerate(lines, 1):
            stripped = line.strip()
            indent = len(line) - len(line.lstrip())
            if stripped and type_indent is not None and indent <= type_indent:
                scope = module_qn
                type_indent = None
                active_func = None

            ann = _ANNOTATION_RE.match(line)
            if ann:
                last_annotations.append(ann.group(1))

            imp = _IMPORT_RE.match(line)
            if imp:
                target = _clean_import(imp.group(1))
                if target:
                    tgt_qn = f"external:{target}"
                    _ensure_external(g, tgt_qn, type=NODE_MODULE)
                    g.add_edge(module_qn, tgt_qn, EDGE_IMPORTS)
                    stats["imports"] += 1

            typ = _TYPE_RE.match(line)
            if typ:
                kind, name, base = typ.groups()
                qn = f"{module_qn}.{name}"
                g.add_node(CgNode(
                    qn, name, NODE_CLASS, kind, package_name, str(path), lineno, 1,
                ))
                g.add_edge(module_qn, qn, EDGE_DEFINES)
                simple_defs.setdefault(name, set()).add(qn)
                stats["type_declarations"] += 1
                for deco in last_annotations:
                    tgt = f"external:{deco}"
                    _ensure_external(g, tgt, type=NODE_FUNCTION)
                    g.add_edge(qn, tgt, EDGE_DECORATES)
                last_annotations.clear()
                if base:
                    tgt = f"external:{base}"
                    _ensure_external(g, tgt, type=NODE_CLASS)
                    g.add_edge(qn, tgt, EDGE_INHERITS)
                scope = qn
                type_indent = indent
                active_func = None
                continue

            fun = _DEF_RE.match(line)
            if fun:
                name = fun.group(1)
                qn = f"{scope}.{name}"
                g.add_node(CgNode(
                    qn, name, NODE_FUNCTION, "method" if scope != module_qn else "function",
                    package_name, str(path), lineno, 1,
                ))
                g.add_edge(scope, qn, EDGE_DEFINES)
                simple_defs.setdefault(name, set()).add(qn)
                stats["defs"] += 1
                for deco in last_annotations:
                    tgt = f"external:{deco}"
                    _ensure_external(g, tgt, type=NODE_FUNCTION)
                    g.add_edge(qn, tgt, EDGE_DECORATES)
                last_annotations.clear()
                active_func = qn
                current_func = None  # skip the signature line; parse the following body lines
            else:
                current_func = active_func or (scope if scope != module_qn else None)

            if current_func:
                for call in _CALL_RE.findall(line):
                    if call in _KEYWORDS or call == "def":
                        continue
                    pending_calls.append((current_func, call))
                    stats["calls_seen"] += 1
                for ref in _REF_RE.findall(line):
                    if ref not in _KEYWORDS:
                        pending_refs.append((current_func, ref))

    for source, spelling in pending_calls:
        cands = simple_defs.get(spelling, set())
        if len(cands) == 1:
            target = next(iter(cands))
            g.add_edge(source, target, EDGE_CALLS)
            stats["calls_resolved"] += 1
        else:
            target = f"external:{spelling}"
            _ensure_external(g, target, type=NODE_FUNCTION)
            g.add_edge(source, target, EDGE_CALLS)
            stats["external_symbols"] += 1

    for source, spelling in pending_refs:
        cands = simple_defs.get(spelling.rsplit(".", 1)[-1], set())
        if len(cands) == 1:
            target = next(iter(cands))
        else:
            target = f"external:{spelling}"
            _ensure_external(g, target, type=NODE_TERM)
            stats["external_symbols"] += 1
        if source != target:
            g.add_edge(source, target, EDGE_REFERENCES)
            stats["references"] += 1

    g.stats = stats
    return g
