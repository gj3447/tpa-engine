"""Neo4j MCP sink for style-host HTTP MCP Cypher servers.

The native ``neo4j_sink`` talks Bolt with ``neo4j.GraphDatabase``. In the consumer
workspace the reachable contract is instead an MCP server exposing
``write_neo4j_cypher`` / ``read_neo4j_cypher`` over HTTP. This sink keeps the
same owned ``:Cg`` ontology and changes only the transport.
"""

from __future__ import annotations

import json
import urllib.request
from itertools import count as _ids

from .model import EDGE_TYPES, TYPE_LABEL, Graph

DEFAULT_URL = "http://localhost:55013/mcp/"


def load(
    graph: Graph,
    *,
    url: str = DEFAULT_URL,
    batch_size: int = 1000,
    clear: bool = True,
    bpc_compat: bool = True,
) -> dict:
    """Load ``graph`` through MCP Cypher tools.

    ``bpc_compat`` adds ``:ConsumerCodeSymbol(id)`` and a corpus-prefixed ``id`` so
    consumer's existing index can accelerate relationship endpoint matching. Nodes
    still carry ``:Cg`` and the structural ``:Cg*`` labels.
    """

    client = _McpClient(url)
    corpus = graph.corpus
    nodes = graph.node_list()
    edges = graph.edge_list()

    if clear:
        client.write(
            "MERGE (j:CgLoadJob {cg_corpus:$c}) "
            "SET j.status=$status, j.started_at=datetime() "
            "WITH j MATCH (n:Cg {cg_corpus:$c}) DETACH DELETE n "
            "RETURN count(n) AS deleted",
            {"c": corpus, "status": "clearing"},
        )
    client.write(
        "MERGE (j:CgLoadJob {cg_corpus:$c}) "
        "SET j.status=$status, j.expected_nodes=$nodes, "
        "    j.expected_edges=$edges, j.updated_at=datetime()",
        {"c": corpus, "status": "loading", "nodes": len(nodes), "edges": len(edges)},
    )
    if bpc_compat:
        client.write(
            "MERGE (repo:ConsumerCodeRepo {name:$c}) SET repo.kind=$kind, repo.updated_at=datetime()",
            {"c": corpus, "kind": "tpa-engine-cg"},
        )

    labels = {}
    for type_name, lbl in TYPE_LABEL.items():
        rows = [
            {
                **n.props(),
                "id": _node_id(corpus, n.qualified_name) if bpc_compat else n.qualified_name,
                "cg_corpus": corpus,
                "repo": corpus,
            }
            for n in nodes
            if n.type == type_name
        ]
        for chunk in _chunks(rows, batch_size):
            extra_label = ":ConsumerCodeSymbol" if bpc_compat else ""
            id_pattern = (
                "{id:r.id}"
                if bpc_compat
                else ("{qualified_name:r.qualified_name, cg_corpus:r.cg_corpus}")
            )
            client.write(
                f"MERGE (j:CgLoadJob {{cg_corpus:$c}}) WITH j UNWIND $rows AS r "
                f"MERGE (x:Cg{extra_label}:{lbl} {id_pattern}) "
                "SET x.qualified_name=r.qualified_name, x.cg_corpus=r.cg_corpus, "
                "    x.repo=r.repo, x.name=r.name, x.module=r.module, x.file=r.file, "
                "    x.kind=r.kind, x.cg_node_type=r.type, x.lineno=r.lineno, "
                "    x.loc=r.loc, j.last_node_type=$type_name, j.updated_at=datetime()",
                {"c": corpus, "rows": chunk, "type_name": type_name},
            )
        labels[lbl] = len(rows)

    edge_counts = {}
    for etype in EDGE_TYPES:
        if bpc_compat:
            rows = [
                {
                    "source_id": _node_id(corpus, e.source),
                    "target_id": _node_id(corpus, e.target),
                    "weight": e.weight,
                }
                for e in edges
                if e.etype == etype
            ]
            match = (
                "MATCH (a:ConsumerCodeSymbol {id:r.source_id}) MATCH (b:ConsumerCodeSymbol {id:r.target_id}) "
            )
        else:
            rows = [
                {"source": e.source, "target": e.target, "weight": e.weight}
                for e in edges
                if e.etype == etype
            ]
            match = (
                "MATCH (a:Cg {qualified_name:r.source, cg_corpus:$c}) "
                "MATCH (b:Cg {qualified_name:r.target, cg_corpus:$c}) "
            )
        for chunk in _chunks(rows, batch_size):
            client.write(
                f"MERGE (j:CgLoadJob {{cg_corpus:$c}}) WITH j UNWIND $rows AS r "
                f"{match}MERGE (a)-[e:{etype} {{cg_corpus:$c}}]->(b) "
                "SET e.weight=r.weight, j.last_edge_type=$etype, j.updated_at=datetime()",
                {"c": corpus, "rows": chunk, "etype": etype},
            )
        edge_counts[etype] = len(rows)

    client.write(
        "MERGE (j:CgLoadJob {cg_corpus:$c}) SET j.status=$status, j.finished_at=datetime()",
        {"c": corpus, "status": "loaded"},
    )
    readback = count(corpus, url=url)
    return {"corpus": corpus, "labels": labels, "edges": edge_counts, "readback": readback}


def count(corpus: str, *, url: str = DEFAULT_URL) -> dict:
    """Read back node/edge counts for ``corpus`` through MCP."""

    client = _McpClient(url)
    data = client.read(
        "MATCH (n:Cg {cg_corpus:$c}) WITH count(n) AS nodes "
        "OPTIONAL MATCH (:Cg {cg_corpus:$c})-[e {cg_corpus:$c}]->(:Cg) "
        "RETURN nodes, count(e) AS edges",
        {"c": corpus},
    )
    if not data:
        return {"nodes": 0, "edges": 0}
    return {"nodes": int(data[0]["nodes"]), "edges": int(data[0]["edges"])}


class _McpClient:
    def __init__(self, url: str) -> None:
        self.url = url
        self._ids = _ids(1)

    def write(self, query: str, params: dict) -> object:
        return self._tool("write_neo4j_cypher", {"query": query, "params": params})

    def read(self, query: str, params: dict) -> list[dict]:
        content = self._tool("read_neo4j_cypher", {"query": query, "params": params})
        text = content["content"][0]["text"]
        return json.loads(text)

    def _tool(self, name: str, arguments: dict) -> dict:
        payload = {
            "jsonrpc": "2.0",
            "id": next(self._ids),
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        req = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=300) as response:
            raw = response.read().decode()
        obj = _sse_json(raw)
        if "error" in obj:
            raise RuntimeError(obj["error"])
        result = obj["result"]
        if result.get("isError"):
            raise RuntimeError(result)
        return result


def _sse_json(raw: str) -> dict:
    stripped = raw.strip()
    if stripped.startswith("{"):
        return json.loads(stripped)
    for line in raw.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])
    raise RuntimeError(f"no MCP data event in response: {raw[:200]}")


def _node_id(corpus: str, qualified_name: str) -> str:
    return f"{corpus}::{qualified_name}"


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]
