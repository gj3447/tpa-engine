"""Richer Python AST frontend for TPA's static-analysis engine track.

The legacy ``ast`` backend is intentionally small and compatibility-focused. This
frontend is the growth path for a larger static analyzer: it emits the same core
:Cg graph plus additional static facts (inheritance, decorators, references,
assignments) without changing the old backend's output contract.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

from tpa_engine.ast_backend import discover_modules, find_package_anchor
from tpa_engine.model import (
    EDGE_ASSIGNS,
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


def _dotted(expr: ast.AST) -> str | None:
    """Best-effort dotted spelling for a symbol expression."""
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        base = _dotted(expr.value)
        return f"{base}.{expr.attr}" if base else expr.attr
    if isinstance(expr, ast.Call):
        return _dotted(expr.func)
    if isinstance(expr, ast.Subscript):
        return _dotted(expr.value)
    return None


class StaticScanner(ast.NodeVisitor):
    """Collect definitions and source-level facts for one Python module."""

    def __init__(self, module_qn: str):
        self.module_qn = module_qn
        self.defs: list[tuple[str, str, int, int, str]] = []
        self.imports: dict[str, str] = {}
        self.calls: list[tuple[str, str]] = []
        self.inherits: list[tuple[str, str, int]] = []
        self.decorators: list[tuple[str, str, int]] = []
        self.assigns: list[tuple[str, str, int]] = []
        self.references: list[tuple[str, str, int]] = []
        self._scope: list[str] = [module_qn]
        self._class_depth = 0

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            local = alias.asname or alias.name.split(".")[0]
            self.imports[local] = alias.name
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level:
            base_parts = self.module_qn.split(".")
            base = ".".join(base_parts[: len(base_parts) - node.level])
            mod = f"{base}.{node.module}" if node.module else base
        else:
            mod = node.module or ""
        for alias in node.names:
            local = alias.asname or alias.name
            self.imports[local] = f"{mod}.{alias.name}" if mod else alias.name
        self.generic_visit(node)

    def _enter_def(self, node, kind: str) -> str:
        qn = f"{self._scope[-1]}.{node.name}"
        end = getattr(node, "end_lineno", node.lineno) or node.lineno
        self.defs.append((qn, kind, node.lineno, end, node.name))
        self._scope.append(qn)
        return qn

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qn = self._enter_def(node, "class")
        for base in node.bases:
            name = _dotted(base)
            if name:
                self.inherits.append((qn, name, getattr(base, "lineno", node.lineno)))
        for deco in node.decorator_list:
            name = _dotted(deco)
            if name:
                self.decorators.append((qn, name, getattr(deco, "lineno", node.lineno)))
        self._class_depth += 1
        self.generic_visit(node)
        self._class_depth -= 1
        self._scope.pop()

    def _visit_func(self, node) -> None:
        kind = "method" if self._class_depth > 0 else "function"
        qn = self._enter_def(node, kind)
        for deco in node.decorator_list:
            name = _dotted(deco)
            if name:
                self.decorators.append((qn, name, getattr(deco, "lineno", node.lineno)))
        self.generic_visit(node)
        self._scope.pop()

    visit_FunctionDef = _visit_func
    visit_AsyncFunctionDef = _visit_func

    def visit_Call(self, node: ast.Call) -> None:
        name = _dotted(node.func)
        if name:
            self.calls.append((self._scope[-1], name))
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._record_target(target, getattr(node, "lineno", 0))
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._record_target(node.target, getattr(node, "lineno", 0))
        self.generic_visit(node)

    def _record_target(self, target: ast.AST, lineno: int) -> None:
        if isinstance(target, ast.Name):
            self.assigns.append((self._scope[-1], target.id, lineno))
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                self._record_target(elt, lineno)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self.references.append((self._scope[-1], node.id, getattr(node, "lineno", 0)))
        self.generic_visit(node)


def _resolve_symbol(
    spelling: str,
    module_qn: str,
    scanner: StaticScanner,
    simple_to_qns: dict[str, set[str]],
    module_names: set[str],
    qn_kind: dict[str, str],
) -> str:
    """Resolve a source spelling to a repo symbol or a stable external stub key."""
    head = spelling.split(".", 1)[0]
    imported = scanner.imports.get(head)
    if imported:
        tail = spelling[len(head):]
        cand = imported + tail
        if cand in qn_kind or cand in module_names:
            return cand
        while cand:
            if cand in qn_kind or cand in module_names:
                return cand
            cand = cand.rsplit(".", 1)[0] if "." in cand else ""
        return f"external:{imported + tail}"

    local = f"{module_qn}.{spelling}"
    if local in qn_kind or local in module_names:
        return local

    cands = simple_to_qns.get(spelling.rsplit(".", 1)[-1], set())
    if len(cands) == 1:
        return next(iter(cands))
    return f"external:{spelling}"


def _ensure_symbol(g: Graph, qn: str, *, preferred_type: str = NODE_TERM) -> None:
    if qn in g.nodes:
        return
    if qn.startswith("external:"):
        name = qn.removeprefix("external:").rsplit(".", 1)[-1]
        g.add_node(CgNode(qn, name, preferred_type, "external", attrs={"external": True}))
        return
    g.ensure_stub(qn, type=preferred_type, kind=preferred_type.lower())


def build_graph(src_root: Path, corpus: str, package_anchor: Path | None = None) -> Graph:
    """Extract a richer static graph from Python source using only stdlib ``ast``."""
    src_root = Path(src_root).resolve()
    anchor = (package_anchor or find_package_anchor(src_root)).resolve()
    walk_root = anchor if anchor.is_relative_to(src_root) and anchor != src_root else src_root
    modules = discover_modules(walk_root, anchor)

    g = Graph(corpus=corpus)
    scanners: dict[str, StaticScanner] = {}
    simple_to_qns: dict[str, set[str]] = defaultdict(set)
    qn_kind: dict[str, str] = {}

    for path, module_qn in sorted(modules.items(), key=lambda kv: kv[1]):
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))
        scanner = StaticScanner(module_qn)
        scanner.visit(tree)
        scanners[module_qn] = scanner

        g.add_node(CgNode(
            module_qn, module_qn.split(".")[-1], NODE_MODULE, "module",
            module=module_qn, file=str(path), lineno=1, loc=text.count("\n") + 1,
        ))
        for qn, kind, lineno, end, simple in scanner.defs:
            ntype = NODE_CLASS if kind == "class" else NODE_FUNCTION
            g.add_node(CgNode(qn, simple, ntype, kind, module_qn, str(path), lineno,
                              end - lineno + 1))
            simple_to_qns[simple].add(qn)
            qn_kind[qn] = kind
            parent = qn.rsplit(".", 1)[0]
            if parent in g.nodes:
                g.add_edge(parent, qn, EDGE_DEFINES)

    module_names = set(modules.values())
    stats = {
        "calls_seen": 0,
        "calls_resolved": 0,
        "inheritance_edges": 0,
        "decorator_edges": 0,
        "assignment_edges": 0,
        "reference_edges": 0,
        "external_symbols": 0,
    }

    for module_qn, scanner in sorted(scanners.items()):
        local_targets: set[str] = set()
        for target in scanner.imports.values():
            cand = target
            while cand:
                if cand in module_names:
                    local_targets.add(cand)
                    break
                cand = cand.rsplit(".", 1)[0] if "." in cand else ""
        for tgt in sorted(local_targets):
            if tgt != module_qn:
                g.add_edge(module_qn, tgt, EDGE_IMPORTS)

        for src, spelling in scanner.calls:
            stats["calls_seen"] += 1
            tgt = _resolve_symbol(spelling, module_qn, scanner, simple_to_qns, module_names, qn_kind)
            preferred = NODE_FUNCTION if not tgt.startswith("external:") else NODE_TERM
            _ensure_symbol(g, tgt, preferred_type=preferred)
            if tgt.startswith("external:"):
                stats["external_symbols"] += 1
            else:
                stats["calls_resolved"] += 1
            if src in g.nodes:
                g.add_edge(src, tgt, EDGE_CALLS)

        for src, spelling, _lineno in scanner.inherits:
            tgt = _resolve_symbol(spelling, module_qn, scanner, simple_to_qns, module_names, qn_kind)
            _ensure_symbol(g, tgt, preferred_type=NODE_CLASS)
            g.add_edge(src, tgt, EDGE_INHERITS)
            stats["inheritance_edges"] += 1

        for src, spelling, _lineno in scanner.decorators:
            tgt = _resolve_symbol(spelling, module_qn, scanner, simple_to_qns, module_names, qn_kind)
            _ensure_symbol(g, tgt, preferred_type=NODE_FUNCTION)
            g.add_edge(src, tgt, EDGE_DECORATES)
            stats["decorator_edges"] += 1

        for src, name, lineno in scanner.assigns:
            term_qn = f"{src}.{name}"
            g.add_node(CgNode(term_qn, name, NODE_TERM, "local", module_qn, "", lineno))
            qn_kind[term_qn] = "term"
            simple_to_qns[name].add(term_qn)
            if src in g.nodes:
                g.add_edge(src, term_qn, EDGE_ASSIGNS)
                stats["assignment_edges"] += 1

        for src, spelling, _lineno in scanner.references:
            tgt = _resolve_symbol(spelling, module_qn, scanner, simple_to_qns, module_names, qn_kind)
            if tgt.startswith("external:") or tgt == src:
                continue
            _ensure_symbol(g, tgt, preferred_type=NODE_TERM)
            if src in g.nodes:
                g.add_edge(src, tgt, EDGE_REFERENCES)
                stats["reference_edges"] += 1

    g.stats = stats
    return g
