"""Neo4j sink — load a :Cg ``Graph`` into Neo4j under the user's own ontology.

Idempotent: clears the prior run for this ``cg_corpus`` only, ensures the
composite ``(qualified_name, cg_corpus)`` uniqueness constraint per structural
label, then MERGEs nodes and edges in batches. Multiple corpora coexist with
no collision because every key and relationship carries ``cg_corpus``.

Refactored from ``tmp_tpa_engine_scip/scip_load_neo4j.py``: the label map and
schema now come from ``model.py`` (single source); URI/auth are parameters
(env-defaulted by the CLI) rather than hardcoded.
"""

from __future__ import annotations

from .model import EDGE_TYPES, TYPE_LABEL, Graph


def load(graph: Graph, uri: str, user: str, password: str,
         database: str | None = None, batch_size: int = 1000) -> dict:
    """Load ``graph`` into Neo4j; returns a {labels, edges} count summary.

    ``neo4j`` (the python driver) is imported lazily so the ast/graphml path
    has no hard dependency on it.
    """
    from neo4j import GraphDatabase  # noqa: PLC0415

    corpus = graph.corpus
    nodes = graph.node_list()
    edges = graph.edge_list()
    drv = GraphDatabase.driver(uri, auth=(user, password))
    try:
        with drv.session(database=database) if database else drv.session() as s:
            # clear prior run for THIS corpus only
            s.run("MATCH (n:Cg {cg_corpus:$c}) DETACH DELETE n", c=corpus)
            for lbl in TYPE_LABEL.values():
                s.run(
                    f"CREATE CONSTRAINT cg_{lbl} IF NOT EXISTS "
                    f"FOR (x:{lbl}) REQUIRE (x.qualified_name, x.cg_corpus) IS UNIQUE"
                )
            # nodes, batched per structural label
            for type_name, lbl in TYPE_LABEL.items():
                rows = [
                    {**n.props(), "cg_corpus": corpus}
                    for n in nodes if n.type == type_name
                ]
                for chunk in _chunks(rows, batch_size):
                    s.run(
                        f"UNWIND $rows AS r "
                        f"MERGE (x:Cg:{lbl} {{qualified_name:r.qualified_name, "
                        f"                    cg_corpus:r.cg_corpus}}) "
                        f"SET x.name=r.name, x.module=r.module, x.file=r.file, "
                        f"    x.kind=r.kind, x.cg_node_type=r.type, "
                        f"    x.lineno=r.lineno, x.loc=r.loc",
                        rows=chunk,
                    )
            # edges, batched per edge type (MERGE keeps it idempotent)
            for etype in EDGE_TYPES:
                rows = [
                    {"source": e.source, "target": e.target, "weight": e.weight}
                    for e in edges if e.etype == etype
                ]
                for chunk in _chunks(rows, batch_size):
                    s.run(
                        f"UNWIND $rows AS r "
                        f"MATCH (a:Cg {{qualified_name:r.source, cg_corpus:$c}}) "
                        f"MATCH (b:Cg {{qualified_name:r.target, cg_corpus:$c}}) "
                        f"MERGE (a)-[e:{etype} {{cg_corpus:$c}}]->(b) "
                        f"SET e.weight=r.weight",
                        rows=chunk, c=corpus,
                    )
            label_counts = s.run(
                "MATCH (n:Cg {cg_corpus:$c}) WITH labels(n) AS L "
                "RETURN [x IN L WHERE x<>'Cg'][0] AS lbl, count(*) AS c "
                "ORDER BY c DESC", c=corpus,
            ).data()
            edge_counts = s.run(
                "MATCH (:Cg {cg_corpus:$c})-[e {cg_corpus:$c}]->(:Cg) "
                "RETURN type(e) AS t, count(e) AS c ORDER BY c DESC", c=corpus,
            ).data()
    finally:
        drv.close()
    return {"corpus": corpus, "labels": label_counts, "edges": edge_counts}


def count(corpus: str, *, uri: str, user: str, password: str,
          database: str | None = None) -> int:
    """Read-back: number of :Cg nodes for ``corpus`` in the store.

    The prerequisite seam for incremental load-then-verify round-trips (idempotency
    assertions, diff-then-update) — it never touches the write path."""
    from neo4j import GraphDatabase  # noqa: PLC0415

    drv = GraphDatabase.driver(uri, auth=(user, password))
    try:
        with drv.session(database=database) if database else drv.session() as s:
            rec = s.run("MATCH (n:Cg {cg_corpus:$c}) RETURN count(n) AS c", c=corpus).single()
            return int(rec["c"]) if rec else 0
    finally:
        drv.close()


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]
