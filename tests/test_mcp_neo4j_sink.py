from __future__ import annotations

import json

from tpa_engine import mcp_neo4j_sink
from tpa_engine.model import CgNode, Graph, NODE_FUNCTION, NODE_MODULE


class _Response:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        raw = "event: message\n" + "data: " + json.dumps(self.payload) + "\n"
        return raw.encode()


def test_mcp_neo4j_load_uses_write_tool_and_reads_back(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout):
        body = json.loads(req.data.decode())
        calls.append(body)
        tool = body["params"]["name"]
        if tool == "read_neo4j_cypher":
            result = {"content": [{"type": "text", "text": '[{"nodes": 2, "edges": 1}]'}]}
        else:
            result = {"content": [{"type": "text", "text": '{"_contains_updates": true}'}]}
        return _Response({"jsonrpc": "2.0", "id": body["id"], "result": result})

    monkeypatch.setattr(mcp_neo4j_sink.urllib.request, "urlopen", fake_urlopen)

    g = Graph(corpus="c")
    g.add_node(CgNode("m", "m", NODE_MODULE, "module"))
    g.add_node(CgNode("m.f", "f", NODE_FUNCTION, "function"))
    g.add_edge("m", "m.f", "DEFINES")

    summary = mcp_neo4j_sink.load(g, url="http://mcp/", batch_size=1)

    assert summary["readback"] == {"nodes": 2, "edges": 1}
    assert calls[0]["params"]["name"] == "write_neo4j_cypher"
    assert calls[-1]["params"]["name"] == "read_neo4j_cypher"
    write_queries = [c["params"]["arguments"]["query"] for c in calls[:-1]]
    assert all(q.startswith("MERGE") for q in write_queries)
    assert any(":ConsumerCodeSymbol:CgModule" in q for q in write_queries)


def test_mcp_neo4j_count_preserves_nodes_when_edges_absent(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout):
        body = json.loads(req.data.decode())
        seen["query"] = body["params"]["arguments"]["query"]
        result = {"content": [{"type": "text", "text": '[{"nodes": 2, "edges": 0}]'}]}
        return _Response({"jsonrpc": "2.0", "id": body["id"], "result": result})

    monkeypatch.setattr(mcp_neo4j_sink.urllib.request, "urlopen", fake_urlopen)

    assert mcp_neo4j_sink.count("c", url="http://mcp/") == {"nodes": 2, "edges": 0}
    assert "OPTIONAL MATCH" in seen["query"]


def test_mcp_client_accepts_plain_json_response(monkeypatch):
    class JsonResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"content": [{"type": "text", "text": '[{"nodes": 1, "edges": 0}]'}]},
                }
            ).encode()

    monkeypatch.setattr(
        mcp_neo4j_sink.urllib.request, "urlopen", lambda req, timeout: JsonResponse()
    )

    assert mcp_neo4j_sink.count("c", url="http://mcp/") == {"nodes": 1, "edges": 0}
