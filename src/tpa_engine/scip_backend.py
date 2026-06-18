"""scip-python code-graph backend — type-precise via SCIP indexing.

Parses an ``index.scip`` produced by ``scip-python`` (an external node binary;
see README) into the user's own :Cg ``Graph`` (``model.py``). Unlike the ast
backend, scip resolves the *type* of a call receiver, so ``backend.ship(...)``
on a ``Protocol``-typed variable links to the canonical ``Backend.ship``
definition, and a bare ``.get(`` does not collapse onto an unrelated ``get``
definition.

Refactored from ``tmp_tpa_engine_scip/scip_to_graph.py``: the hardcoded
``package == "ooptdd"`` / ``src.`` stripping is generalised to *any* repo —
the set of "own" packages is auto-detected from the index (or passed in), and
the module-prefix to strip is derived rather than literal.

SCIP semantics used
-------------------
* ``Occurrence.symbol_roles`` bitset: Definition=0x1, ReadAccess=0x8.
* A Definition occurrence marks where a symbol lives; per document we sort
  function/method defs by start line and use the half-open interval
  ``[def_start, next_def_start)`` for caller containment (binary search).
* A non-definition reference to a callable symbol inside function F's body
  => F CALLS that callee.
* scip-python emits NO Import-role occurrences; IMPORTS is reconstructed as a
  module-dependency edge (module A references a symbol owned by module B =>
  A IMPORTS B).

SCIP symbol grammar (python)
----------------------------
``scip-python python <pkg> <ver> `<module.path>`/Desc#Desc.method().``
descriptor suffixes: ``/``=namespace(module) ``#``=type(class)
``().``=method ``.``=term(field/attr) — ``local …`` symbols are skipped.
"""

from __future__ import annotations

import re
from collections import Counter

ROLE_DEFINITION = 0x1
ROLE_READ = 0x8
PROJECT_SCHEME = "scip-python"

# A module path is wrapped in backticks:  `pkg.sub.mod`/
_MOD_RE = re.compile(r"^`([^`]+)`/")


def _import_scip_pb2():
    """Import the bundled scip protobuf module (lazy — protobuf optional)."""
    from . import scip_pb2  # noqa: PLC0415  (lazy: keeps ast backend dep-free)
    return scip_pb2


# --------------------------------------------------------------------------- #
# Symbol parsing
# --------------------------------------------------------------------------- #
def parse_symbol(sym: str, own_packages: set[str] | None,
                 strip_prefixes: tuple[str, ...]):
    """Parse a SCIP symbol into a structured node identity, or None.

    None = local / stdlib / external symbol we do not model as a repo node.
    ``own_packages`` (if given) restricts to those scip package names; None
    means "model every non-local symbol" (used when the package set was not
    pre-detected).
    """
    if sym.startswith("local "):
        return None
    parts = sym.split(" ", 4)  # [scheme, language, package, version, descriptors]
    if len(parts) < 5 or parts[0] != PROJECT_SCHEME:
        return None
    package, descriptors = parts[2], parts[4]
    if own_packages is not None and package not in own_packages:
        return None
    return decode_descriptors(descriptors, strip_prefixes)


def decode_descriptors(desc: str, strip_prefixes: tuple[str, ...] = ()):
    """Decode the descriptor chain into module/class/method identity."""
    m = _MOD_RE.match(desc)
    if not m:
        return None
    raw_module = m.group(1)
    rest = desc[m.end():]

    module = raw_module
    for pref in strip_prefixes:
        if module == pref:
            module = ""
            break
        if module.startswith(pref + "."):
            module = module[len(pref) + 1:]
            break

    if rest in ("__init__:", ""):
        return {"kind": "module", "module": module, "qualified_name": module,
                "name": module.rsplit(".", 1)[-1] if module else module}

    # tokenise descriptor chain; tokens end in '#' (type) '().' (method) '.' (term)
    tokens: list[tuple[str, str]] = []
    i, n = 0, len(rest)
    while i < n:
        j = i
        while j < n and rest[j] not in "#.(":
            j += 1
        name = rest[i:j]
        if rest[j:j + 3] == "().":
            tokens.append((name, "method"))
            i = j + 3
        elif j < n and rest[j] == "#":
            tokens.append((name, "type"))
            i = j + 1
        elif j < n and rest[j] == ".":
            tokens.append((name, "term"))
            i = j + 1
        else:
            break  # trailing params like ().(arg) — stop

    if not tokens:
        return {"kind": "module", "module": module, "qualified_name": module,
                "name": module.rsplit(".", 1)[-1] if module else module}

    qn = (module + "." if module else "") + ".".join(t[0] for t in tokens)
    last_name, last_suffix = tokens[-1]
    is_method = any(s == "type" for _, s in tokens[:-1])

    if last_suffix == "method":
        return {"kind": "function", "module": module, "qualified_name": qn,
                "name": last_name, "is_method": is_method}
    if last_suffix == "type":
        return {"kind": "class", "module": module, "qualified_name": qn,
                "name": last_name}
    # bare term: field/var/attr (scip-python emits free funcs with '().')
    return {"kind": "term", "module": module, "qualified_name": qn,
            "name": last_name, "is_method": is_method}


# --------------------------------------------------------------------------- #
# Package / prefix auto-detection
# --------------------------------------------------------------------------- #
def detect_own_packages(idx) -> set[str]:
    """Find scip package names defined *within* this index (i.e. the repo's
    own code), by counting which packages have Definition occurrences."""
    defined: Counter[str] = Counter()
    for d in idx.documents:
        for oc in d.occurrences:
            if not (oc.symbol_roles & ROLE_DEFINITION):
                continue
            sym = oc.symbol
            if sym.startswith("local "):
                continue
            parts = sym.split(" ", 4)
            if len(parts) >= 3 and parts[0] == PROJECT_SCHEME:
                defined[parts[2]] += 1
    return set(defined)


def detect_strip_prefixes(idx) -> tuple[str, ...]:
    """Derive module-path prefixes to strip so the dotted module name matches
    the import-time name (e.g. a ``src/`` layout indexes ``src.pkg.mod`` but
    code imports ``pkg.mod``). Detected from document relative paths.
    """
    prefixes: set[str] = set()
    for d in idx.documents:
        rel = d.relative_path
        if rel.startswith("src/"):
            prefixes.add("src")
    return tuple(sorted(prefixes))


# --------------------------------------------------------------------------- #
# Build the graph
# --------------------------------------------------------------------------- #
def _module_of_doc(rel_path: str, strip_prefixes: tuple[str, ...]) -> str:
    p = rel_path
    for pref in strip_prefixes:
        if p.startswith(pref + "/"):
            p = p[len(pref) + 1:]
            break
    p = p[:-3] if p.endswith(".py") else p
    p = p.replace("/", ".")
    if p.endswith(".__init__"):
        p = p[: -len(".__init__")]
    return p


def build_graph(index_path: str, corpus: str, *,
                own_packages: set[str] | None = None,
                doc_filter: str | None = None):
    """Build a :Cg ``Graph`` from a SCIP index.

    ``own_packages``: restrict to these scip package names (auto-detected if
    None). ``doc_filter``: only documents whose ``relative_path`` starts with
    this prefix (e.g. ``"src/"``) — None = all documents.
    """
    from .model import (  # local import keeps module import cheap
        NODE_CLASS,
        NODE_FUNCTION,
        NODE_MODULE,
        NODE_TERM,
        CgNode,
        Graph,
    )

    scip_pb2 = _import_scip_pb2()
    idx = scip_pb2.Index()
    with open(index_path, "rb") as f:
        idx.ParseFromString(f.read())

    if own_packages is None:
        own_packages = detect_own_packages(idx) or None
    strip_prefixes = detect_strip_prefixes(idx)

    def keep_doc(rel: str) -> bool:
        return True if doc_filter is None else rel.startswith(doc_filter)

    docs = [d for d in idx.documents if keep_doc(d.relative_path)]
    g = Graph(corpus=corpus)
    stats = {"documents": len(docs), "occurrences": 0, "definitions": 0,
             "external_symbols": len(idx.external_symbols)}

    callable_syms: set[str] = set()
    doc_func_defs: dict[str, list[tuple[int, str]]] = {}

    def psym(sym):
        return parse_symbol(sym, own_packages, strip_prefixes)

    _KIND_TYPE = {"module": (NODE_MODULE, "module"),
                  "class": (NODE_CLASS, "class"),
                  "term": (NODE_TERM, "term")}

    def add_node(info, rel_path):
        qn = info["qualified_name"]
        if qn in g.nodes:
            return qn
        if info["kind"] == "function":
            ntype, kind = NODE_FUNCTION, ("method" if info.get("is_method") else "function")
        else:
            ntype, kind = _KIND_TYPE[info["kind"]]
        g.add_node(CgNode(
            qualified_name=qn, name=info["name"], type=ntype, kind=kind,
            module=info["module"], file=rel_path,
        ))
        return qn

    # Pass 1: definitions (nodes + DEFINES) + remember callable symbols/intervals
    for d in docs:
        rel = d.relative_path
        func_defs: list[tuple[int, str]] = []
        for si in d.symbols:
            info = psym(si.symbol)
            if info and info["kind"] == "function":
                callable_syms.add(si.symbol)
        for oc in d.occurrences:
            stats["occurrences"] += 1
            if not (oc.symbol_roles & ROLE_DEFINITION):
                continue
            stats["definitions"] += 1
            info = psym(oc.symbol)
            if info is None:
                continue
            qn = add_node(info, rel)
            _add_defines(info, g)
            if info["kind"] == "function":
                callable_syms.add(oc.symbol)
                func_defs.append((oc.range[0], qn))
        func_defs.sort()
        doc_func_defs[rel] = func_defs

    # Pass 2: CALLS + IMPORTS via reference occurrences
    for d in docs:
        rel = d.relative_path
        func_defs = doc_func_defs.get(rel, [])
        starts = [fd[0] for fd in func_defs]

        def enclosing_func(line, _starts=starts, _defs=func_defs):
            lo, hi, best = 0, len(_starts) - 1, None
            while lo <= hi:
                mid = (lo + hi) // 2
                if _starts[mid] <= line:
                    best = mid
                    lo = mid + 1
                else:
                    hi = mid - 1
            return None if best is None else _defs[best][1]

        src_mod = _module_of_doc(rel, strip_prefixes)
        for oc in d.occurrences:
            if oc.symbol_roles & ROLE_DEFINITION:
                continue
            info = psym(oc.symbol)
            if info is None:
                continue
            line = oc.range[0]
            tgt_mod = info["module"]
            if src_mod and tgt_mod and src_mod != tgt_mod:
                g.add_edge(src_mod, tgt_mod, "IMPORTS")
            if info["kind"] == "function" or (
                    info["kind"] == "term" and oc.symbol in callable_syms):
                callee_qn = info["qualified_name"]
                caller_qn = enclosing_func(line)
                if caller_qn and callee_qn and caller_qn != callee_qn:
                    g.add_edge(caller_qn, callee_qn, "CALLS")

    # Materialise any call endpoint whose own definition was outside the docs.
    for (a, b, etype) in list(g.edges):
        if etype == "CALLS":
            g.ensure_stub(a)
            g.ensure_stub(b)

    # Drop edges whose endpoints never became real nodes (defensive).
    for key in list(g.edges):
        a, b, _et = key
        if a not in g.nodes or b not in g.nodes:
            del g.edges[key]

    g.stats = stats
    return g


def _add_defines(info, g) -> None:
    qn = info["qualified_name"]
    if info["kind"] == "module" or "." not in qn:
        return
    parent = qn.rsplit(".", 1)[0]
    g.add_edge(parent, qn, "DEFINES")


# --------------------------------------------------------------------------- #
# scip-python runner
# --------------------------------------------------------------------------- #
def run_scip_python(repo_path: str, output: str = "index.scip",
                    project_name: str | None = None) -> str:
    """Run ``scip-python index`` on ``repo_path``, returning the index path.

    scip-python is an external node binary (``npm install -g
    @sourcegraph/scip-python``). Raises FileNotFoundError with install help if
    it is not on PATH. Local indexing only — no network.
    """
    import shutil
    import subprocess
    from pathlib import Path

    exe = shutil.which("scip-python")
    if exe is None:
        raise FileNotFoundError(
            "scip-python not found on PATH. Install it with:\n"
            "    npm install -g @sourcegraph/scip-python\n"
            "or use --backend ast (zero-dependency fallback)."
        )
    repo = Path(repo_path).resolve()
    out = (repo / output) if not Path(output).is_absolute() else Path(output)
    cmd = [exe, "index", "--output", str(out)]
    if project_name:
        cmd += ["--project-name", project_name]
    subprocess.run(cmd, cwd=str(repo), check=True)
    return str(out)
