"""The :Cg code-graph ontology — the SINGLE SOURCE of the schema.

Every backend (scip, ast) emits into the dataclasses defined here, and every
sink (neo4j, graphml, json) consumes them. Backends never decide labels,
edge types, MERGE keys, or corpus namespacing — that knowledge lives here and
only here, so the graph is the *user's own* ontology, not a vendor schema.

Schema
------
Node types (``CgNode.type``):
    Module    — a .py file / package (qualified_name = dotted module path)
    Class     — a class definition
    Function  — a function or method (``kind`` ∈ {"function", "method"})
    Term      — a field / attribute / module-level variable (scip backend only)

Edge types (``CgEdge.etype``):
    DEFINES   — parent (module|class) -> child (class|function|method)
    CALLS     — function -> function, weight = number of call sites
    IMPORTS   — module -> module dependency, weight = reference count

Neo4j labels: every node carries the base label ``:Cg`` plus one structural
label from ``TYPE_LABEL``. The MERGE key is the composite
``(qualified_name, cg_corpus)`` so multiple corpora (e.g. one per repo, or a
scip vs ast variant of the same repo) coexist in one database without
collision.

``cg_corpus`` is the partition key the user owns — pick any string per repo.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ----- node / edge type vocabulary (the closed ontology) ------------------- #
NODE_MODULE = "Module"
NODE_CLASS = "Class"
NODE_FUNCTION = "Function"
NODE_TERM = "Term"
NODE_TYPES = (NODE_MODULE, NODE_CLASS, NODE_FUNCTION, NODE_TERM)

EDGE_DEFINES = "DEFINES"
EDGE_CALLS = "CALLS"
EDGE_IMPORTS = "IMPORTS"
EDGE_TYPES = (EDGE_DEFINES, EDGE_CALLS, EDGE_IMPORTS)

# structural Neo4j label per node type (in addition to the base :Cg label)
TYPE_LABEL = {
    NODE_MODULE: "CgModule",
    NODE_CLASS: "CgClass",
    NODE_FUNCTION: "CgFunction",
    NODE_TERM: "CgTerm",
}


@dataclass
class CgNode:
    """One vertex in the :Cg graph. ``qualified_name`` is the identity key."""

    qualified_name: str
    name: str
    type: str  # one of NODE_TYPES
    kind: str  # "module" | "class" | "function" | "method" | "term"
    module: str = ""
    file: str = ""
    lineno: int = 0
    loc: int = 0

    def __post_init__(self) -> None:
        if self.type not in NODE_TYPES:
            raise ValueError(f"unknown node type {self.type!r}")

    def label(self) -> str:
        """Structural Neo4j label (e.g. 'CgFunction')."""
        return TYPE_LABEL[self.type]

    def props(self) -> dict:
        """Flat scalar property map (graphml/Neo4j safe — no nested values)."""
        return {
            "qualified_name": self.qualified_name,
            "name": self.name,
            "type": self.type,
            "kind": self.kind,
            "module": self.module,
            "file": self.file,
            "lineno": self.lineno,
            "loc": self.loc,
        }


@dataclass
class CgEdge:
    """One directed edge. (source, target, etype) is the identity."""

    source: str  # qualified_name of source node
    target: str  # qualified_name of target node
    etype: str  # one of EDGE_TYPES
    weight: int = 1

    def __post_init__(self) -> None:
        if self.etype not in EDGE_TYPES:
            raise ValueError(f"unknown edge type {self.etype!r}")


@dataclass
class Graph:
    """A whole extracted code graph, owned by one ``cg_corpus`` partition.

    Backends build one of these; sinks serialise it. ``add_*`` are idempotent
    on identity and aggregate CALLS/IMPORTS weights, so a backend can stream
    occurrences without pre-deduplicating.
    """

    corpus: str
    nodes: dict[str, CgNode] = field(default_factory=dict)
    # edge identity -> CgEdge ; identity = (source, target, etype)
    edges: dict[tuple[str, str, str], CgEdge] = field(default_factory=dict)
    # backend-supplied diagnostics (call resolution stats etc.) — not persisted
    stats: dict = field(default_factory=dict)

    # -- mutation ---------------------------------------------------------- #
    def add_node(self, node: CgNode) -> CgNode:
        """Insert a node; first writer wins on identity (qualified_name)."""
        return self.nodes.setdefault(node.qualified_name, node)

    def ensure_stub(self, qualified_name: str, type: str = NODE_FUNCTION,
                    kind: str = "function") -> CgNode:
        """Ensure an edge endpoint exists, materialising a stub if unseen.

        Used when a call/define target was resolved by identity but its own
        definition occurrence lives in a document we did not (or could not)
        fully parse.
        """
        if qualified_name in self.nodes:
            return self.nodes[qualified_name]
        name = qualified_name.rsplit(".", 1)[-1]
        module = qualified_name.rsplit(".", 1)[0] if "." in qualified_name else qualified_name
        return self.add_node(CgNode(
            qualified_name=qualified_name, name=name, type=type, kind=kind,
            module=module, file="",
        ))

    def add_edge(self, source: str, target: str, etype: str, weight: int = 1) -> None:
        """Add or aggregate an edge. CALLS/IMPORTS weights accumulate."""
        key = (source, target, etype)
        existing = self.edges.get(key)
        if existing is None:
            self.edges[key] = CgEdge(source, target, etype, weight)
        else:
            existing.weight += weight

    # -- queries ----------------------------------------------------------- #
    def node_list(self) -> list[CgNode]:
        """Nodes in deterministic (qualified_name) order."""
        return [self.nodes[k] for k in sorted(self.nodes)]

    def edge_list(self) -> list[CgEdge]:
        """Edges in deterministic (etype, source, target) order."""
        return [self.edges[k] for k in sorted(self.edges, key=lambda e: (e[2], e[0], e[1]))]

    def counts(self) -> dict:
        """{'nodes': {type: n}, 'edges': {etype: n}} summary."""
        nt: dict[str, int] = {}
        for n in self.nodes.values():
            nt[n.type] = nt.get(n.type, 0) + 1
        et: dict[str, int] = {}
        for e in self.edges.values():
            et[e.etype] = et.get(e.etype, 0) + 1
        return {"nodes": nt, "edges": et,
                "total_nodes": len(self.nodes), "total_edges": len(self.edges)}

    def to_node_link(self) -> dict:
        """networkx-compatible node-link dict (deterministic ordering)."""
        return {
            "directed": True,
            "multigraph": False,
            "graph": {"cg_corpus": self.corpus},
            "nodes": [{**n.props(), "id": n.qualified_name} for n in self.node_list()],
            "links": [
                {"source": e.source, "target": e.target,
                 "etype": e.etype, "weight": e.weight}
                for e in self.edge_list()
            ],
        }
