"""``codegraph`` CLI — turn any repo into a :Cg knowledge graph you own.

    codegraph index <repo_path> --backend {scip,ast} --corpus <name> \
        --out {neo4j,graphml,json} [connection/output flags]

Deterministic, no LLM, no network beyond scip-python's local indexing.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .model import Graph


def _build_graph(args) -> Graph:
    repo = Path(args.repo_path).resolve()
    if not repo.exists():
        sys.exit(f"error: repo path does not exist: {repo}")

    if args.backend == "ast":
        from . import ast_backend
        src_root = repo / args.src_subdir if args.src_subdir else repo
        return ast_backend.build_graph(src_root, corpus=args.corpus)

    # scip backend
    from . import scip_backend
    index_path = args.scip_index
    if index_path:
        index_path = str(Path(index_path).resolve())
    else:
        print(f"[codegraph] running scip-python on {repo} ...", file=sys.stderr)
        index_path = scip_backend.run_scip_python(
            str(repo), project_name=args.project_name)
    doc_filter = args.doc_filter or (f"{args.src_subdir}/" if args.src_subdir else None)
    own = set(args.package) if args.package else None
    return scip_backend.build_graph(
        index_path, corpus=args.corpus, own_packages=own, doc_filter=doc_filter)


def _emit(graph: Graph, args) -> None:
    counts = graph.counts()
    if args.out == "neo4j":
        from . import neo4j_sink
        uri = args.neo4j_uri or os.environ.get("CODEGRAPH_NEO4J_URI", "bolt://localhost:7687")
        user = args.neo4j_user or os.environ.get("CODEGRAPH_NEO4J_USER", "neo4j")
        pw = args.neo4j_password or os.environ.get("CODEGRAPH_NEO4J_PASSWORD")
        if not pw:
            sys.exit("error: neo4j password required "
                     "(--neo4j-password or CODEGRAPH_NEO4J_PASSWORD)")
        summary = neo4j_sink.load(graph, uri=uri, user=user, password=pw,
                                  database=args.neo4j_database)
        print(f"[codegraph] loaded corpus '{graph.corpus}' into {uri}")
        print(f"  node labels: {summary['labels']}")
        print(f"  edge types : {summary['edges']}")
    else:
        from . import graphml_sink
        stem = Path(args.output or f"{args.corpus}_codegraph")
        if args.out == "graphml":
            p = graphml_sink.write_graphml(graph, stem.with_suffix(".graphml"))
        else:  # json
            p = graphml_sink.write_json(graph, stem.with_suffix(".json"))
        print(f"[codegraph] wrote {p}")

    print(f"[codegraph] nodes={counts['total_nodes']} {counts['nodes']}")
    print(f"[codegraph] edges={counts['total_edges']} {counts['edges']}")
    if graph.stats:
        print(f"[codegraph] backend stats: {graph.stats}")


def _check(args) -> int:
    """Structural fitness gate: fail (nonzero exit) on too many import cycles.

    The CI closure — codegraph polices the codebase it was built to study. Exit 1
    when the import-cycle count exceeds ``--max-cycles`` (default 0 = no cycles allowed).
    """
    from .fitness import import_cycles
    graph = _build_graph(args)
    cycles = import_cycles(graph)
    n = len(cycles)
    print(f"[codegraph] corpus '{args.corpus}': {n} import cycle(s); "
          f"budget --max-cycles={args.max_cycles}")
    if cycles and (n > args.max_cycles or args.show):
        for c in cycles:
            print("  CYCLE: " + " <-> ".join(c))
    if n > args.max_cycles:
        print(f"[codegraph] FAIL: {n} import cycle(s) exceeds budget "
              f"{args.max_cycles}", file=sys.stderr)
        return 1
    print("[codegraph] OK: within cycle budget")
    return 0


def _add_graph_args(sp: argparse.ArgumentParser) -> None:
    """Args shared by `index` and `check` — everything `_build_graph` consumes."""
    sp.add_argument("repo_path", help="path to the repo to index")
    sp.add_argument("--backend", choices=("scip", "ast"), default="ast",
                    help="scip = type-precise (needs scip-python node binary); "
                         "ast = stdlib-only fallback (default)")
    sp.add_argument("--corpus", required=True,
                    help="cg_corpus partition name (the key you own)")
    sp.add_argument("--src-subdir", default=None,
                    help="source subdir under repo (e.g. 'src'); "
                         "ast auto-detects if omitted")
    sp.add_argument("--scip-index", default=None,
                    help="use an existing index.scip instead of running scip-python")
    sp.add_argument("--project-name", default=None,
                    help="scip-python --project-name")
    sp.add_argument("--package", action="append", default=None,
                    help="restrict scip to these package names (repeatable; "
                         "auto-detected if omitted)")
    sp.add_argument("--doc-filter", default=None,
                    help="scip: only documents under this path prefix")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="codegraph",
        description="Turn any repo into a :Cg Neo4j/GraphML knowledge graph "
                    "you own (deterministic, no LLM).")
    sub = p.add_subparsers(dest="command", required=True)

    idx = sub.add_parser("index", help="index a repo into a :Cg graph")
    _add_graph_args(idx)
    idx.add_argument("--out", choices=("neo4j", "graphml", "json"),
                     default="graphml", help="output sink (default: graphml)")
    idx.add_argument("--output", help="output file stem (graphml/json sinks)")
    # neo4j connection (env-defaulted)
    idx.add_argument("--neo4j-uri", default=None,
                     help="default env CODEGRAPH_NEO4J_URI / bolt://localhost:7687")
    idx.add_argument("--neo4j-user", default=None,
                     help="default env CODEGRAPH_NEO4J_USER / neo4j")
    idx.add_argument("--neo4j-password", default=None,
                     help="default env CODEGRAPH_NEO4J_PASSWORD")
    idx.add_argument("--neo4j-database", default=None,
                     help="optional Neo4j database name")

    chk = sub.add_parser("check",
                         help="fitness gate: nonzero exit on a structural regression "
                              "(import cycles)")
    _add_graph_args(chk)
    chk.add_argument("--max-cycles", type=int, default=0,
                     help="max allowed import cycles before failing (default 0)")
    chk.add_argument("--show", action="store_true",
                     help="print all cycles even when within budget")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "check":
        return _check(args)
    if args.command == "index":
        graph = _build_graph(args)
        _emit(graph, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
