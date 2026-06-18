# TPA Engine Architecture

`tpa-engine` is intended to grow into a static-analysis engine, not just a small
graph exporter. The package is organized around ports, adapters, and analysis
frontends so new analyzers can be added without rewriting the CLI or sinks.

## Layers

```text
src/tpa_engine/
  model.py                 # core :Cg ontology and schema validation
  backends.py              # extraction backend port + registry
  sinks.py                 # graph sink port + registry
  fitness.py               # structural gate registry over emitted graphs
  ast_backend.py           # compatibility AST backend: small, stable graph
  scip_backend.py          # SCIP adapter: type-resolved Python facts
  frontends/
    python_static.py       # richer Python AST static-analysis frontend
    scala_static.py        # Scala/SBT source-structure frontend
```

## Design Rules

- `model.py` owns vocabulary. Backends emit `Graph`; sinks never infer schema.
- `backends.py` is the extraction port. A new analyzer is a registered backend,
  not a CLI branch.
- `sinks.py` is the persistence port. A new output target is a registered sink,
  not an `_emit()` rewrite.
- Compatibility backends stay stable. Larger analysis work goes into a new
  frontend/backend so old count-based tests remain meaningful.
- Static facts should be emitted as graph facts with evidence-friendly
  properties, not as ad hoc JSON side channels.

## Backend Families

`ast`
: Minimal stdlib fallback. It emits `Module`, `Class`, `Function` and
  `DEFINES`, `IMPORTS`, `CALLS`. This backend is intentionally conservative.

`scip`
: Type-resolved backend using `scip-python` output. This is the precision path
  for calls and symbol ownership.

`python-ast-static`
: The growth backend for a larger static analyzer. It still uses stdlib AST, but
  emits richer facts: `INHERITS`, `DECORATES`, `REFERENCES`, and `ASSIGNS` in
  addition to the core graph.

`scala-source-static`
: Zero-dependency Scala/SBT source scanner for package, import, type, method,
  call, and reference facts. This is useful for making Joern-scale Scala repos
  agent-queryable in `:Cg` even when Joern itself is not the storage/query
  surface.

`joern`
: Joern JSON CPG export importer. It consumes `--joern-export`, normalizes
  recognized CPG facts into `:Cg`, and can optionally copy the raw export with
  `--joern-raw-out` for debugging/provenance. The default graph that agents see
  remains `:Cg`; raw Joern storage is not the query contract.

## Joern Boundary

`tpa-engine` must not become a Joern wrapper. The engine's control plane is the
owned `:Cg` ontology; Joern is one precision backend beside AST, SCIP,
tree-sitter-style frontends, cpggen/atom, or later analyzers.

| option | decision |
|--------|----------|
| `tpa-engine = Joern wrapper` | Reject. It makes the engine depend on Joern installation, schema, DSL, and storage, weakening ownership of `:Cg`. |
| `tpa-engine + Joern backend` | Accept. Use Joern as a precise analyzer, then normalize results into `:Cg`. |
| `tpa-engine = KG control plane` | Preferred. Multiple analyzers feed one governed ontology; sinks export Neo4j, JSON, GraphML, and optional raw artifacts. |

The Joern adapter contract is:

1. consume Joern/CPG output (`--joern-export` for the MVP);
2. map Joern nodes/edges into `CgNode` and `CgEdge`;
3. preserve evidence/provenance in scalar `attrs` where useful;
4. validate with `model.validate(graph)`;
5. emit to normal sinks (`neo4j`, `json`, `graphml`);
6. optionally retain a `joern_raw` artifact for analysis debugging only
   (`--joern-raw-out`).

The adapter must not leak Joern labels, edge names, storage assumptions, or Scala
DSL expectations into the public `:Cg` contract. If Joern discovers facts that
`:Cg` cannot represent, extend `model.py` deliberately and test conformance,
rather than persisting an off-ontology side channel as the default graph.

## Agent-Native Graph

Joern graph storage is powerful for human security/static-analysis workflows,
but it is not the default agent interface. Agents should query the normalized KG
with stable Cypher over `:Cg` plus governance/domain nodes:

```cypher
(:Requirement)-[:BINDS_TO]->(:CgFunction)
(:Test)-[:COVERS]->(:Requirement)
(:Gate)-[:ASSERTS]->(:Cg)
(:OoptddVerdict)-[:OBSERVED]->(:CgFunction)
(:CgFunction)-[:CALLS]->(:CgFunction)
(:CgModule)-[:IMPORTS]->(:CgModule)
```

This keeps common agent questions direct:

- Which functions satisfy this requirement?
- Which tests are affected if this function changes?
- Which source anchor is tied to this failing verdict?
- Which edge is central to breaking this module cycle?
- Which code fragment has a broken Longinus binding?

The storage rule is therefore:

- default query/storage surface: `:Cg` in Neo4j or deterministic JSON/GraphML;
- optional forensic surface: raw Joern export;
- no downstream agent depends on Joern's runtime, DSL, or native schema.

## SOLID Boundary

- Single Responsibility: graph schema, extraction, output, and fitness gates are
  separate modules.
- Open/Closed: new backends, sinks, and gates register behind protocols.
- Liskov: every backend returns the same `Graph` model and must pass schema
  conformance.
- Interface Segregation: backends only build graphs; sinks only write graphs.
- Dependency Inversion: CLI depends on registries/protocols, not concrete
  extractor implementations.

## Growth Path

The static-analysis engine should grow by adding frontends and analysis passes:

1. language frontend extracts typed graph facts;
2. graph validation rejects off-ontology output;
3. analysis predicates/gates query the graph;
4. sinks persist the same graph to JSON, GraphML, Neo4j, or later stores.

The next high-value static facts are:

- control-flow and data-flow edges;
- callsite nodes with line/span evidence;
- class member and attribute resolution;
- import alias and re-export modeling;
- package/module ownership and external dependency nodes;
- incremental indexing keyed by file hashes.
