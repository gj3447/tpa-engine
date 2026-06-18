"""Backend registry — one ``GraphBackend`` Protocol + a string-keyed dispatch registry.

The two extraction backends (``ast``, ``scip``) sit behind a single Protocol, so backend
selection in caller code is a registry lookup, not a hand-written conditional, and a new
backend is a registration — never a CLI edit. This is the blarify ``LanguageDefinitions`` /
Joern ``X2CpgFrontend`` / CodeQL signature-module idiom, translated to Python via the same
registry seam ooptdd evolved (``@check``/``CHECK_REGISTRY``). Extract-not-rewrite: the
adapters call the unchanged ``ast_backend``/``scip_backend`` build functions with the same
arguments the CLI passed before, so extraction logic is byte-identical.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from .model import Graph


@dataclass(frozen=True)
class BackendRequest:
    """Normalized input — every field the divergent backend signatures need, built once
    from CLI args so each backend's ``build_graph`` is a uniform call."""

    repo: Path
    corpus: str
    src_subdir: str | None = None
    scip_index: str | None = None
    project_name: str | None = None
    packages: tuple[str, ...] | None = None
    doc_filter: str | None = None
    joern_export: str | None = None
    joern_raw_out: str | None = None
    joern_home: str | None = None
    joern_language: str | None = None

    @classmethod
    def from_args(cls, args) -> BackendRequest:
        pkgs = getattr(args, "package", None)
        return cls(
            repo=Path(args.repo_path).resolve(),
            corpus=args.corpus,
            src_subdir=getattr(args, "src_subdir", None),
            scip_index=getattr(args, "scip_index", None),
            project_name=getattr(args, "project_name", None),
            packages=tuple(pkgs) if pkgs else None,
            doc_filter=getattr(args, "doc_filter", None),
            joern_export=getattr(args, "joern_export", None),
            joern_raw_out=getattr(args, "joern_raw_out", None),
            joern_home=getattr(args, "joern_home", None),
            joern_language=getattr(args, "joern_language", None),
        )


@runtime_checkable
class GraphBackend(Protocol):
    """A backend turns a repo (or a prebuilt index) into a :Cg ``Graph``."""

    name: str

    def build_graph(self, req: BackendRequest) -> Graph: ...


BACKENDS: dict[str, GraphBackend] = {}


def register(backend: GraphBackend) -> GraphBackend:
    """Register a backend under its ``name`` (duplicate-key guard, mirrors gate.check)."""
    if backend.name in BACKENDS:
        raise ValueError(f"duplicate backend {backend.name!r}")
    BACKENDS[backend.name] = backend
    return backend


@dataclass(frozen=True)
class AstBackend:
    name: str = "ast"

    def build_graph(self, req: BackendRequest) -> Graph:
        from . import ast_backend
        src_root = req.repo / req.src_subdir if req.src_subdir else req.repo
        return ast_backend.build_graph(src_root, corpus=req.corpus)


@dataclass(frozen=True)
class PythonAstStaticBackend:
    """Growth backend for the larger static analyzer track.

    It keeps the legacy ``ast`` backend stable while emitting richer facts
    (inheritance/decorators/references/assignments) from the same stdlib AST.
    """

    name: str = "python-ast-static"

    def build_graph(self, req: BackendRequest) -> Graph:
        from .frontends import python_static
        src_root = req.repo / req.src_subdir if req.src_subdir else req.repo
        return python_static.build_graph(src_root, corpus=req.corpus)


@dataclass(frozen=True)
class ScalaSourceStaticBackend:
    """Zero-dependency Scala/SBT source-structure backend.

    This is the fallback for Scala repos that Joern cannot parse as input source
    itself. It emits deterministic package/file/type/def/import/call facts into
    the owned :Cg ontology.
    """

    name: str = "scala-source-static"

    def build_graph(self, req: BackendRequest) -> Graph:
        from .frontends import scala_static
        src_root = req.repo / req.src_subdir if req.src_subdir else req.repo
        return scala_static.build_graph(src_root, corpus=req.corpus)


@dataclass(frozen=True)
class ScipBackend:
    name: str = "scip"

    def build_graph(self, req: BackendRequest) -> Graph:
        from . import scip_backend
        index_path = req.scip_index
        if index_path:
            index_path = str(Path(index_path).resolve())
        else:
            print(f"[tpa-engine] running scip-python on {req.repo} ...", file=sys.stderr)
            index_path = scip_backend.run_scip_python(
                str(req.repo), project_name=req.project_name)
        doc_filter = req.doc_filter or (f"{req.src_subdir}/" if req.src_subdir else None)
        own = set(req.packages) if req.packages else None
        return scip_backend.build_graph(
            index_path, corpus=req.corpus, own_packages=own, doc_filter=doc_filter)


@dataclass(frozen=True)
class JoernBackend:
    """Joern CPG export importer.

    Joern remains a precision backend feeding the owned :Cg ontology. Either reuse a
    prebuilt JSON export (``--joern-export``; tests/CI need no Joern install) or let the
    backend run Joern itself (``joern-parse`` + a bundled CPGQL dump) on ``req.repo`` —
    mirroring scip's "reuse an index or run the indexer" split.
    """

    name: str = "joern"

    def build_graph(self, req: BackendRequest) -> Graph:
        from . import joern_backend
        export = req.joern_export
        if export:
            export = str(Path(export).resolve())
        else:
            print(f"[tpa-engine] running joern on {req.repo} ...", file=sys.stderr)
            export = joern_backend.run_joern(
                str(req.repo), joern_home=req.joern_home, language=req.joern_language)
        return joern_backend.build_graph(
            Path(export),
            corpus=req.corpus,
            raw_out=Path(req.joern_raw_out).resolve() if req.joern_raw_out else None,
        )


register(AstBackend())
register(PythonAstStaticBackend())
register(ScalaSourceStaticBackend())
register(JoernBackend())
register(ScipBackend())
