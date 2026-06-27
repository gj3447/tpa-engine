"""``tpa_engine`` CLI — turn any repo into a :Cg knowledge graph you own.

    tpa_engine index <repo_path> --backend {scip,ast} --corpus <name> \
        --out {neo4j,graphml,json} [connection/output flags]

Deterministic, no LLM, no network beyond scip-python's local indexing.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .backends import BACKENDS, BackendRequest
from .sinks import SINKS
from .model import Graph


def _build_graph(args) -> Graph:
    # Single registry dispatch — backend selection is a lookup, not a conditional.
    repo = Path(args.repo_path).resolve()
    if not repo.exists():
        sys.exit(f"error: repo path does not exist: {repo}")
    return BACKENDS[args.backend].build_graph(BackendRequest.from_args(args))


def _emit(graph: Graph, args) -> None:
    # Single sink dispatch — one Protocol method, not a per-sink free-function branch.
    counts = graph.counts()
    if args.out == "neo4j":
        uri = args.neo4j_uri or os.environ.get("TPA_ENGINE_NEO4J_URI", "bolt://localhost:7687")
        user = args.neo4j_user or os.environ.get("TPA_ENGINE_NEO4J_USER", "neo4j")
        pw = args.neo4j_password or os.environ.get("TPA_ENGINE_NEO4J_PASSWORD")
        if not pw:
            sys.exit("error: neo4j password required "
                     "(--neo4j-password or TPA_ENGINE_NEO4J_PASSWORD)")
        summary = SINKS["neo4j"].write(graph, uri=uri, user=user, password=pw,
                                       database=args.neo4j_database)
        print(f"[tpa-engine] loaded corpus '{graph.corpus}' into {uri}")
        print(f"  node labels: {summary['labels']}")
        print(f"  edge types : {summary['edges']}")
    elif args.out == "mcp-neo4j":
        from .mcp_neo4j_sink import DEFAULT_URL

        url = args.mcp_neo4j_url or os.environ.get("TPA_ENGINE_MCP_NEO4J_URL", DEFAULT_URL)
        summary = SINKS["mcp-neo4j"].write(
            graph,
            url=url,
            batch_size=args.mcp_batch_size,
            clear=not args.mcp_no_clear,
            bpc_compat=not args.mcp_no_bpc_compat,
        )
        print(f"[tpa-engine] loaded corpus '{graph.corpus}' through MCP {url}")
        print(f"  node labels: {summary['labels']}")
        print(f"  edge types : {summary['edges']}")
        print(f"  readback   : {summary['readback']}")
    else:
        stem = Path(args.output or f"{args.corpus}_tpa_engine")
        suffix = ".graphml" if args.out == "graphml" else ".json"
        p = SINKS[args.out].write(graph, path=stem.with_suffix(suffix))
        print(f"[tpa-engine] wrote {p}")

    print(f"[tpa-engine] nodes={counts['total_nodes']} {counts['nodes']}")
    print(f"[tpa-engine] edges={counts['total_edges']} {counts['edges']}")
    if graph.stats:
        print(f"[tpa-engine] backend stats: {graph.stats}")


def _check(args) -> int:
    """Structural fitness gate: fail (nonzero exit) on a structural regression.

    Default = import-cycle budget (``--max-cycles``). With one or more ``--gate``, runs a
    COMPOSITION of data-driven structural gates (``predicate:op:threshold[:arg]``) — a new
    gate is DATA, not a code edit. The CI closure: tpa_engine polices the code it studies.
    """
    graph = _build_graph(args)
    if getattr(args, "gates", None):
        from .fitness import check as run_gates
        from .fitness import parse_gate
        results = run_gates(graph, [parse_gate(s) for s in args.gates])
        failed = False
        for r in results:
            arg = f":{r.gate.arg}" if r.gate.arg else ""
            print(f"[tpa-engine] {'FAIL' if r.failed else 'OK'} "
                  f"{r.gate.predicate} {r.gate.op} {r.gate.threshold}{arg} (value={r.value})")
            if r.failed:
                failed = True
                for name, contrib in r.offenders[:10]:
                    print(f"    offender: {name} ({contrib})")
        if failed:
            print("[tpa-engine] FAIL: a structural gate was violated", file=sys.stderr)
            return 1
        print(f"[tpa-engine] OK: all {len(results)} gate(s) passed")
        return 0
    from .fitness import import_cycles
    cycles = import_cycles(graph)
    if getattr(args, "baseline", None):
        from pathlib import Path

        from .baseline import Baseline
        bpath = Path(args.baseline)
        if getattr(args, "update_baseline", False):
            wrote = Baseline.save_if_changed(bpath, args.corpus, cycles)
            print(f"[tpa-engine] baseline {'updated' if wrote else 'unchanged'}: "
                  f"{bpath} ({len(cycles)} cycle row(s))")
            return 0
        new, fixed = Baseline.load(bpath).diff(cycles)
        print(f"[tpa-engine] corpus '{args.corpus}': {len(cycles)} cycle(s); "
              f"baseline known {len(cycles) - len(new)}, new {len(new)}, fixed {len(fixed)}")
        if new:
            for c in new:
                print("  NEW CYCLE: " + " <-> ".join(c), file=sys.stderr)
            print(f"[tpa-engine] FAIL: {len(new)} new import cycle(s) absent from baseline",
                  file=sys.stderr)
            return 1
        print("[tpa-engine] OK: no new cycles vs baseline")
        return 0
    n = len(cycles)
    print(f"[tpa-engine] corpus '{args.corpus}': {n} import cycle(s); "
          f"budget --max-cycles={args.max_cycles}")
    if cycles and (n > args.max_cycles or args.show):
        for c in cycles:
            print("  CYCLE: " + " <-> ".join(c))
    if n > args.max_cycles:
        print(f"[tpa-engine] FAIL: {n} import cycle(s) exceeds budget "
              f"{args.max_cycles}", file=sys.stderr)
        return 1
    print("[tpa-engine] OK: within cycle budget")
    return 0


def _add_graph_args(sp: argparse.ArgumentParser) -> None:
    """Args shared by `index` and `check` — everything `_build_graph` consumes."""
    sp.add_argument("repo_path", help="path to the repo to index")
    sp.add_argument("--backend", choices=tuple(sorted(BACKENDS)), default="ast",
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
    sp.add_argument("--joern-export", default=None,
                    help="joern: read an existing Joern JSON CPG export "
                         "(omit to run joern-parse on the repo automatically)")
    sp.add_argument("--joern-raw-out", default=None,
                    help="joern: optional path to copy the raw export artifact")
    sp.add_argument("--joern-home", default=None,
                    help="joern: joern-cli dir (else $JOERN_HOME or joern-parse on PATH)")
    sp.add_argument("--joern-language", default=None,
                    help="joern: joern-parse --language frontend (javasrc, c, jssrc, "
                         "pythonsrc, ...); auto-detected if omitted")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tpa-engine",
        description="Turn any repo into a :Cg Neo4j/GraphML knowledge graph "
                    "you own (deterministic, no LLM).")
    sub = p.add_subparsers(dest="command", required=True)

    idx = sub.add_parser("index", help="index a repo into a :Cg graph")
    _add_graph_args(idx)
    idx.add_argument("--out", choices=tuple(sorted(SINKS)),
                     default="graphml", help="output sink (default: graphml)")
    idx.add_argument("--output", help="output file stem (graphml/json sinks)")
    # neo4j connection (env-defaulted)
    idx.add_argument("--neo4j-uri", default=None,
                     help="default env TPA_ENGINE_NEO4J_URI / bolt://localhost:7687")
    idx.add_argument("--neo4j-user", default=None,
                     help="default env TPA_ENGINE_NEO4J_USER / neo4j")
    idx.add_argument("--neo4j-password", default=None,
                     help="default env TPA_ENGINE_NEO4J_PASSWORD")
    idx.add_argument("--neo4j-database", default=None,
                     help="optional Neo4j database name")
    # MCP Neo4j connection (consumer default)
    idx.add_argument("--mcp-neo4j-url", default=None,
                     help="default env TPA_ENGINE_MCP_NEO4J_URL / consumer MCP URL")
    idx.add_argument("--mcp-batch-size", type=int, default=1000,
                     help="MCP Neo4j batch size (default: 1000)")
    idx.add_argument("--mcp-no-clear", action="store_true",
                     help="do not clear the existing :Cg corpus before MCP load")
    idx.add_argument("--mcp-no-bpc-compat", action="store_true",
                     help="do not add ConsumerCodeSymbol labels/ids for consumer index compatibility")

    chk = sub.add_parser("check",
                         help="fitness gate: nonzero exit on a structural regression "
                              "(import cycles)")
    _add_graph_args(chk)
    chk.add_argument("--max-cycles", type=int, default=0,
                     help="max allowed import cycles before failing (default 0)")
    chk.add_argument("--show", action="store_true",
                     help="print all cycles even when within budget")
    chk.add_argument("--gate", action="append", default=None, dest="gates",
                     help="data-driven structural gate 'predicate:op:threshold[:arg]', "
                          "repeatable (e.g. fan_in:>:3, god_object_loc:>:500, "
                          "layering:>:0:core,domain,ui). Supersedes --max-cycles. "
                          "Predicates: import_cycles, fan_in, god_object_loc, layering.")
    chk.add_argument("--baseline", default=None,
                     help="path to baseline.json: accept its cycle rows as known debt, "
                          "fail only on NEW cycles (per-row set-membership ratchet)")
    chk.add_argument("--update-baseline", action="store_true",
                     help="rewrite --baseline to the CURRENT cycle set "
                          "(write-only-if-changed) and exit 0")
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
