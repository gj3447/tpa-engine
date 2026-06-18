"""Stdlib-``ast`` code-graph backend — zero dependencies, no node binary.

Walks a package source tree and emits a :Cg ``Graph`` (see ``model.py``) with
Module/Class/Function nodes and DEFINES/CALLS/IMPORTS edges. Determinism: AST
is exact, all iteration is sorted, no wall-clock / randomness / network — the
same source yields a byte-identical graph.

Call resolution is best-effort *without type inference* (priority: imported
repo def -> module-local def -> unique global simple-name). Ambiguous bare
names are dropped and counted rather than guessed, which keeps the graph
honest but LESS precise than the scip backend — notably it CANNOT tell
``Protocol``-typed dispatch apart, and a bare ``.get(`` matched against a
single ``get`` definition is exactly the kind of false hotspot the scip
backend fixes (see README).

Refactored from ``tmp_tpa_engine_diy/extract_tpa_engine.py``: the networkx
``DiGraph`` is replaced by the shared ``model.Graph`` so the schema is owned in
one place, and module discovery is generalised to any package root.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

from .model import (
    NODE_CLASS,
    NODE_FUNCTION,
    NODE_MODULE,
    CgNode,
    Graph,
)


# --------------------------------------------------------------------------- #
# 1. Discover modules and map file -> dotted module qualified name
# --------------------------------------------------------------------------- #
def discover_modules(src_root: Path, package_anchor: Path) -> dict[Path, str]:
    """Map each ``*.py`` under ``src_root`` to its dotted module name.

    The dotted name is computed relative to ``package_anchor`` (the directory
    that contains the top-level package), so ``<anchor>/pkg/sub/mod.py`` ->
    ``pkg.sub.mod`` and a package ``__init__.py`` collapses to the package's
    dotted name.
    """
    modules: dict[Path, str] = {}
    for path in sorted(src_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        if not path.is_relative_to(package_anchor):
            # file lives outside the package root (e.g. repo-level scripts/
            # examples/ when anchoring at src/) — not part of the package graph
            continue
        rel = path.relative_to(package_anchor).with_suffix("")
        parts = list(rel.parts)
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        if not parts:
            continue
        modules[path] = ".".join(parts)
    return modules


def find_package_anchor(src_root: Path) -> Path:
    """Heuristically locate the directory containing the top-level package.

    A ``src/`` layout anchors at ``src/``; otherwise the parent of the first
    top-level package directory (one holding ``__init__.py``) under
    ``src_root``; falling back to ``src_root`` itself for flat script dirs.
    """
    src_root = src_root.resolve()
    if src_root.name == "src":
        return src_root
    src_child = src_root / "src"
    if src_child.is_dir():
        return src_child
    # parent of the shallowest package dir is the anchor
    for child in sorted(src_root.iterdir()):
        if child.is_dir() and (child / "__init__.py").exists():
            return src_root
    return src_root


# --------------------------------------------------------------------------- #
# 2. Per-module AST visitor: collect defs, imports, call sites
# --------------------------------------------------------------------------- #
class ModuleScanner(ast.NodeVisitor):
    """Single-pass collector over one module's AST."""

    def __init__(self, module_qn: str):
        self.module_qn = module_qn
        # defs: list of (qualified_name, kind, lineno, end_lineno, simple_name)
        self.defs: list[tuple[str, str, int, int, str]] = []
        # imports: imported_local_name -> target_dotted (module or symbol)
        self.imports: dict[str, str] = {}
        # call sites: list of (enclosing_def_qn, callee_spelling, receiver_hint)
        self.calls: list[tuple[str, str, str | None]] = []
        self._scope: list[str] = [module_qn]
        self._class_depth = 0

    # ---- imports -------------------------------------------------------- #
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            local = alias.asname or alias.name.split(".")[0]
            self.imports[local] = alias.name
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level:  # relative import resolved against this module's package
            base_parts = self.module_qn.split(".")
            base = ".".join(base_parts[: len(base_parts) - node.level])
            mod = f"{base}.{node.module}" if node.module else base
        else:
            mod = node.module or ""
        for alias in node.names:
            local = alias.asname or alias.name
            self.imports[local] = f"{mod}.{alias.name}" if mod else alias.name
        self.generic_visit(node)

    # ---- definitions ---------------------------------------------------- #
    def _enter_def(self, node, kind: str) -> None:
        qn = f"{self._scope[-1]}.{node.name}"
        end = getattr(node, "end_lineno", node.lineno) or node.lineno
        self.defs.append((qn, kind, node.lineno, end, node.name))
        self._scope.append(qn)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._enter_def(node, "class")
        self._class_depth += 1
        self.generic_visit(node)
        self._class_depth -= 1
        self._scope.pop()

    def _visit_func(self, node) -> None:
        kind = "method" if self._class_depth > 0 else "function"
        self._enter_def(node, kind)
        self.generic_visit(node)
        self._scope.pop()

    visit_FunctionDef = _visit_func
    visit_AsyncFunctionDef = _visit_func

    # ---- call sites ----------------------------------------------------- #
    def visit_Call(self, node: ast.Call) -> None:
        enclosing = self._scope[-1]
        func = node.func
        if isinstance(func, ast.Name):
            self.calls.append((enclosing, func.id, None))
        elif isinstance(func, ast.Attribute):
            recv = func.value
            hint = recv.id if isinstance(recv, ast.Name) else None
            self.calls.append((enclosing, func.attr, hint))
        # call on a subscript/call result -> unresolvable, skip
        self.generic_visit(node)


# --------------------------------------------------------------------------- #
# 3. Build the graph
# --------------------------------------------------------------------------- #
def build_graph(src_root: Path, corpus: str,
                package_anchor: Path | None = None) -> Graph:
    """Extract a :Cg ``Graph`` from a Python source tree using stdlib ast."""
    src_root = Path(src_root).resolve()
    anchor = (package_anchor or find_package_anchor(src_root)).resolve()
    # Walk from the anchor when it is deeper than src_root (e.g. repo-root
    # invocation resolves anchor=<repo>/src) so the graph is scoped to the
    # package and repo-level scripts/examples are not pulled in.
    walk_root = anchor if anchor.is_relative_to(src_root) and anchor != src_root else src_root
    modules = discover_modules(walk_root, anchor)

    g = Graph(corpus=corpus)
    scanners: dict[str, ModuleScanner] = {}
    simple_to_qns: dict[str, set[str]] = defaultdict(set)
    qn_kind: dict[str, str] = {}

    # -- pass 1: parse every module; add Module/Class/Function + DEFINES
    for path, module_qn in sorted(modules.items(), key=lambda kv: kv[1]):
        text = path.read_text(encoding="utf-8")
        nloc = text.count("\n") + 1
        tree = ast.parse(text, filename=str(path))
        scanner = ModuleScanner(module_qn)
        scanner.visit(tree)
        scanners[module_qn] = scanner

        g.add_node(CgNode(
            qualified_name=module_qn, name=module_qn.split(".")[-1],
            type=NODE_MODULE, kind="module", module=module_qn,
            file=str(path), lineno=1, loc=nloc,
        ))
        for qn, kind, lineno, end, simple in scanner.defs:
            ntype = NODE_CLASS if kind == "class" else NODE_FUNCTION
            g.add_node(CgNode(
                qualified_name=qn, name=simple, type=ntype, kind=kind,
                module=module_qn, file=str(path),
                lineno=lineno, loc=end - lineno + 1,
            ))
            simple_to_qns[simple].add(qn)
            qn_kind[qn] = kind
            parent = qn.rsplit(".", 1)[0]
            if parent in g.nodes:
                g.add_edge(parent, qn, "DEFINES")

    module_names = set(modules.values())

    # -- pass 2: IMPORTS edges (repo-local module targets only)
    for module_qn, scanner in scanners.items():
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
                g.add_edge(module_qn, tgt, "IMPORTS")

    # -- pass 3: CALLS edges (best-effort repo-local resolution)
    stats = {"calls_seen": 0, "calls_resolved": 0,
             "calls_ambiguous": 0, "calls_external": 0}
    for module_qn, scanner in scanners.items():
        import_resolved: dict[str, str] = {
            local: target for local, target in scanner.imports.items()
            if target in qn_kind
        }
        local_defs = {
            qn.split(".")[-1]: qn
            for (qn, *_rest) in scanner.defs
            if qn_kind.get(qn) and qn.count(".") == module_qn.count(".") + 1
        }
        for enclosing, callee, _hint in scanner.calls:
            stats["calls_seen"] += 1
            target_qn = None
            if callee in import_resolved:
                target_qn = import_resolved[callee]
            elif callee in local_defs:
                target_qn = local_defs[callee]
            else:
                cands = simple_to_qns.get(callee, set())
                if len(cands) == 1:
                    target_qn = next(iter(cands))
                elif len(cands) > 1:
                    stats["calls_ambiguous"] += 1
                    continue
            if target_qn and target_qn in g.nodes and enclosing in g.nodes:
                stats["calls_resolved"] += 1
                g.add_edge(enclosing, target_qn, "CALLS")
            else:
                stats["calls_external"] += 1

    g.stats = stats
    return g
