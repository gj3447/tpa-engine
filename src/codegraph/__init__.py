"""codegraph — deterministic repo -> :Cg knowledge-graph extractor.

Two backends (``scip`` type-precise, ``ast`` stdlib-only), two file sinks
(``graphml``, ``json``) and a Neo4j sink, all sharing ONE schema defined in
``model.py``. No LLM, no network beyond scip-python's local indexing.

The thesis: a *deterministic* extractor produces a graph you own (your :Cg
ontology, your cg_corpus partition, your MERGE keys) — then put the LLM/agent
reasoning ON TOP of that owned graph instead of trusting a vendor schema.
"""

from .model import (
    EDGE_TYPES,
    NODE_TYPES,
    TYPE_LABEL,
    CgEdge,
    CgNode,
    Graph,
)

__version__ = "0.1.0"

__all__ = [
    "Graph", "CgNode", "CgEdge",
    "NODE_TYPES", "EDGE_TYPES", "TYPE_LABEL",
    "__version__",
]
