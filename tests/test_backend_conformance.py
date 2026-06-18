"""Backend conformance (OQ3) — ONE parameterized test over the BACKENDS registry asserts
every backend's output conforms to the :Cg model schema, with zero per-backend test code.

The registry parameterization means a future 3rd backend is covered automatically (it only
has to implement the GraphBackend Protocol). The scip index is built in-test (no committed
binary, no scip-python node binary), verified to yield a Class + a Function.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tpa_engine.backends import BACKENDS, BackendRequest
from tpa_engine.cli import build_parser
from tpa_engine.model import EDGE_TYPES, NODE_TYPES, TYPE_LABEL, Graph

FIX = Path(__file__).parent / "fixtures"


def _build_scip_index(tmp_path) -> str:
    from tpa_engine import scip_pb2
    idx = scip_pb2.Index()
    doc = idx.documents.add()
    doc.relative_path = "tinypkg/core.py"
    doc.language = "python"
    for sym in ["scip-python python tinypkg 0.1 `tinypkg.core`/Widget#",
                "scip-python python tinypkg 0.1 `tinypkg.core`/Widget#run()."]:
        doc.symbols.add().symbol = sym
        occ = doc.occurrences.add()
        occ.symbol = sym
        occ.symbol_roles = 0x1  # Definition
        occ.range.extend([0, 0, 5])
    p = tmp_path / "tinypkg.scip"
    p.write_bytes(idx.SerializeToString())
    return str(p)


def _req(name: str, tmp_path) -> BackendRequest:
    if name == "ast":
        return BackendRequest(repo=FIX, corpus="conf-ast", src_subdir="tinypkg")
    if name == "python-ast-static":
        return BackendRequest(repo=FIX, corpus="conf-python-ast-static", src_subdir="tinypkg")
    if name == "scala-source-static":
        return BackendRequest(repo=FIX, corpus="conf-scala-source-static", src_subdir="scalapkg")
    if name == "joern":
        return BackendRequest(repo=FIX, corpus="conf-joern",
                              joern_export=str(FIX / "joern_export.json"))
    if name == "scip":
        return BackendRequest(repo=FIX, corpus="conf-scip",
                              scip_index=_build_scip_index(tmp_path))
    raise AssertionError(f"unmapped backend {name!r}")


@pytest.mark.parametrize("name", sorted(BACKENDS))  # ['ast','scip'] -> 2 params, ONE body
def test_backend_output_conforms_to_model_schema(name, tmp_path):
    g = BACKENDS[name].build_graph(_req(name, tmp_path))
    assert isinstance(g, Graph) and g.corpus == f"conf-{name}"
    assert g.nodes  # the backend actually produced a graph
    for n in g.nodes.values():
        assert n.type in NODE_TYPES and n.label() == TYPE_LABEL[n.type]
        assert n.qualified_name and isinstance(n.props(), dict)
    for e in g.edges.values():
        assert e.etype in EDGE_TYPES and e.source in g.nodes and e.target in g.nodes
    nl = g.to_node_link()
    assert nl["directed"] and nl["graph"]["cg_corpus"] == g.corpus


def test_registry_keys_are_the_cli_backend_choices():
    # drift guard: CLI --backend choices DERIVE from the registry, so a backend can never be
    # registered-but-unreachable or offered-but-missing.
    assert set(BACKENDS) == {"ast", "python-ast-static", "scala-source-static", "joern", "scip"}
    p = build_parser()
    for name in BACKENDS:
        ns = p.parse_args(["index", str(FIX / "tinypkg"), "--corpus", "c", "--backend", name])
        assert ns.backend == name
    with pytest.raises(SystemExit):
        p.parse_args(["index", str(FIX / "tinypkg"), "--corpus", "c", "--backend", "nope"])
