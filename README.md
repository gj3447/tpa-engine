# tpa-engine

Turn **any** repo into a `:Cg` Neo4j / GraphML knowledge graph that **you own** —
deterministic, no LLM, no vendor schema.

## What it is

`tpa-engine` is a small standalone tool that extracts a code graph
(Module / Class / Function nodes; `DEFINES` / `CALLS` / `IMPORTS` edges) from a
Python repo and writes it into **your own ontology** (`:Cg`, partitioned by a
`cg_corpus` key you choose) instead of whatever schema a vendored indexer would
impose. Backends share one schema:

- **`scip`** — runs [`scip-python`](https://github.com/sourcegraph/scip-python)
  under the hood and parses the resulting `index.scip`. Type-resolved, so it
  follows `Protocol`/dynamic dispatch and does **not** confuse identically-named
  methods. More precise; needs the `scip-python` node binary.
- **`ast`** — stdlib `ast` only. **Zero dependencies, no node binary.** A solid,
  honest fallback that drops (and counts) calls it cannot resolve rather than
  guessing — but it is less precise than `scip` (see *Honest limits*).
- **`python-ast-static`** — the larger static-analysis track. It keeps `ast`
  stable while emitting richer facts from stdlib AST: `INHERITS`, `DECORATES`,
  `REFERENCES`, and `ASSIGNS` in addition to the core graph.
- **`scala-source-static`** — zero-dependency Scala/SBT source scanning for
  package/import/type/method/call/reference facts. It is a pragmatic fallback
  for Joern-scale Scala repos when the agent-facing graph still needs to be
  owned `:Cg`.

Future precision analyzers should follow the same rule: they are **backends into
`:Cg`**, not replacements for `:Cg`. In particular, Joern belongs here as a
Joern backend/importer that normalizes CPG facts into the owned ontology. It
should not turn `tpa-engine` into a Joern wrapper or make agents depend on
Joern's native schema, storage, runtime, or Scala DSL. Raw Joern output can be
kept as an optional debug/provenance artifact, but the agent-facing graph remains
Neo4j/JSON/GraphML over `:Cg`.

### The thesis: deterministic extractor + LLM on top

The graph is produced by a **deterministic, auditable, re-runnable** extractor
(AST is exact; SCIP is compiler-grade; output is byte-identical across runs). You
then put TPA / agents / LLM reasoning **on top of a graph you own** — your labels,
your `cg_corpus` partition, your `(qualified_name, cg_corpus)` MERGE keys — rather
than asking an LLM to hallucinate structure or trusting a third-party schema you
cannot govern. Determinism is the contract: the LLM reasons, the extractor never
lies.

## Install

```bash
pip install -e .                 # ast backend works immediately, zero deps
pip install -e '.[all]'          # + neo4j (Neo4j sink) + protobuf (scip backend)
```

For the **scip backend** you also need the external node binary (local indexing
only, no network calls out):

```bash
npm install -g @sourcegraph/scip-python
```

## Usage

```bash
# ast backend, file output, no DB needed (safe anywhere)
tpa-engine index /path/to/repo --backend ast --corpus myrepo --out graphml

# richer stdlib static analysis graph
tpa-engine index /path/to/repo --backend python-ast-static --corpus myrepo-static --out json

# Scala/SBT source-structure graph
tpa-engine index /path/to/scala-repo --backend scala-source-static --corpus myscala --out json

# scip backend (runs scip-python, then parses) -> Neo4j
tpa-engine index /path/to/repo --backend scip --corpus myrepo-scip --out neo4j \
    --neo4j-uri bolt://localhost:7687 --neo4j-user neo4j --neo4j-password ***

# consumer workspace path: HTTP MCP server owns the Neo4j credential/driver hop
tpa-engine index /path/to/repo --backend scala-source-static --corpus myscala \
    --out mcp-neo4j --mcp-neo4j-url http://localhost:55013/mcp/

# reuse an existing index.scip instead of re-running scip-python
tpa-engine index /path/to/repo --backend scip --scip-index index.scip \
    --corpus myrepo-scip --out json --output myrepo
```

### `check` — structural fitness gate (CI)

Turn the graph into a pass/fail gate: `tpa-engine check` builds the graph (ast backend
by default — zero-dep, CI-safe) and **exits nonzero when the import-cycle count exceeds
`--max-cycles`** (default 0 = no cycles allowed). An import cycle = a strongly-connected
component (size > 1) of the module `IMPORTS` graph (`fitness.import_cycles` /
`cycle_count`, iterative Tarjan, deterministic). This is the closure: point the
engine-dissection tool back at your own engine and fail CI on a structural regression
(the role import-linter's independence/no-cycle contracts play).

```bash
# fail the build if the repo has ANY import cycle
tpa-engine check /path/to/repo --backend ast --src-subdir src --corpus myrepo --max-cycles 0

# allow a known-debt baseline of 1 cycle (ratchet); --show prints cycles even when OK
tpa-engine check /path/to/repo --backend ast --corpus myrepo --max-cycles 1 --show
```

**Data-driven gates (`--gate`).** Beyond cycles, any structural property is a gate
expressed as DATA — `predicate:op:threshold[:arg]` — so a new gate is a string, not a
code edit (the CodeQL "structural property = query over relations" / jQAssistant
"rules-as-data" idea). Shipped predicates: `import_cycles`, `fan_in` (max distinct
callers of any function), `god_object_loc` (max node LOC), `layering` (imports against a
declared module order). Repeatable; pass iff all pass; offenders are surfaced.

```bash
# fail if any function has > 10 distinct callers OR any unit exceeds 500 LOC
tpa-engine check /path/to/repo --backend ast --src-subdir src --corpus myrepo \
    --gate fan_in:>:10 --gate god_object_loc:>:500
# layering: nothing in an earlier layer may import a later one
tpa-engine check . --backend ast --src-subdir src --corpus myrepo \
    --gate layering:>:0:core,domain,adapters,ui
```

New predicates self-register via the `@predicate` seam in `fitness.py` (mirrors the
`@check` registry); the `check()` runner is never edited to add one.

**Brownfield ratchet (`--baseline`).** On a repo with pre-existing debt, accept the
current cycle *rows* as a checked-in `baseline.json` and fail only on NEW rows — finer
than the coarse `--max-cycles` count (a cycle SWAP that keeps the count unchanged still
fails). `--update-baseline` re-accepts the current set (write-only-if-changed).

```bash
tpa-engine check . --backend ast --src-subdir src --corpus myrepo \
    --baseline baseline.json --update-baseline   # accept current debt
tpa-engine check . --backend ast --src-subdir src --corpus myrepo \
    --baseline baseline.json                     # green unless a NEW cycle appears
```

Connection flags default to env vars: `TPA_ENGINE_NEO4J_URI`,
`TPA_ENGINE_NEO4J_USER`, `TPA_ENGINE_NEO4J_PASSWORD`.

MCP connection flags default to `TPA_ENGINE_MCP_NEO4J_URL`, falling back to the
consumer MCP URL. `--out mcp-neo4j` keeps the same `:Cg` ontology but uses
`write_neo4j_cypher` / `read_neo4j_cypher` over MCP instead of Bolt credentials.
By default it also adds `:ConsumerCodeSymbol(id)` compatibility labels so consumer's
existing indexes accelerate relationship endpoint matching.

Outputs: `--out graphml` / `--out json` write a file (no DB); `--out neo4j`
idempotently MERGEs into Neo4j over Bolt; `--out mcp-neo4j` MERGEs through an
MCP Neo4j server. Both DB sinks clear only the chosen `cg_corpus` first.

## The `:Cg` schema

**Nodes** (base label `:Cg` + one structural label):

| `type`     | Neo4j label  | meaning                                   |
|------------|--------------|-------------------------------------------|
| `Module`   | `:CgModule`  | a `.py` file / package (dotted path)      |
| `Class`    | `:CgClass`   | a class definition                        |
| `Function` | `:CgFunction`| function or method (`kind` distinguishes) |
| `Term`     | `:CgTerm`    | field / attribute (scip backend only)     |

Node props: `qualified_name`, `name`, `kind`, `module`, `file`, `lineno`, `loc`,
`cg_corpus`, `cg_node_type`.

**Edges** (each carries `cg_corpus`):

| `etype`   | direction                         | meaning                          |
|-----------|-----------------------------------|----------------------------------|
| `DEFINES` | Module/Class → Class/Function     | lexical containment              |
| `CALLS`   | Function → Function (`weight`)    | call-site count                  |
| `IMPORTS` | Module → Module (`weight`)        | module dependency                |
| `INHERITS` | Class → Class/base symbol        | class inheritance                |
| `DECORATES` | Class/Function → decorator      | decorator use                    |
| `REFERENCES` | Scope → symbol                 | symbol reference                 |
| `ASSIGNS` | Scope → Term                      | assignment-introduced term       |

**`cg_corpus` partitioning** — the MERGE key is the composite
`(qualified_name, cg_corpus)`. Pick a corpus per repo (or per backend variant of
the same repo); multiple corpora live in one database with no collision. Schema
is defined once in [`src/tpa_engine/model.py`](src/tpa_engine/model.py) and shared
by both backends and all sinks.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the static-analysis
engine architecture, Joern boundary, and extension rules.

### Longinus-join example query

Because you own the schema, you can join your `:Cg` graph straight against your
own KG governance layer (e.g. a Longinus `ReferenceSite` / `ConsumerSemanticContract`
anchored to a `qualified_name`):

```cypher
// Which contracted symbols actually have inbound CALLS edges
// (i.e. are reachable) in the extracted graph?
MATCH (rs:ReferenceSite)
MATCH (f:CgFunction {qualified_name: rs.lexical_symbol, cg_corpus: $corpus})
OPTIONAL MATCH (caller:Cg)-[c:CALLS {cg_corpus: $corpus}]->(f)
RETURN rs.contract_id, f.qualified_name,
       count(caller) AS inbound_calls,
       CASE WHEN count(caller) = 0 THEN 'ORPHAN_OR_DISPATCH' ELSE 'REACHED' END AS status
ORDER BY inbound_calls ASC;
```

## Honest limits

1. **Registry / string dispatch is invisible.** `get_backend("victorialogs")` →
   class is a dict lookup; neither backend emits a type edge, so registry-only-
   reached methods read as dead. Fundamental to static analysis, not a bug.
2. **`scip-python` emits no Import-role occurrences**, so `IMPORTS` is
   reconstructed as a *module-dependency* graph (any cross-module symbol use) —
   faithful to dependency intent, slightly broader than literal `import` lines.
3. **The `ast` backend is less precise than `scip`.** The motivating, *measured*
   example: on `ooptdd`, the ast backend attributes **42 callers to
   `ontology.Ontology.get`** — a bare-name collision where every `.get(` in the
   repo (`dict.get`, `environ.get`, …) is matched to the only `get` definition it
   knows. The scip backend's type resolution finds `Ontology.get` has just **2
   real callers**. (That single fake hotspot was ~29% of the ast backend's CALLS
   edges in the src-only head-to-head.) scip also recovers `Protocol`-dispatch
   edges — e.g. `gate.evaluate -> Backend.query` — that the ast backend cannot
   see at all. Use `--backend scip` when call-graph precision matters; `--backend
   ast` when you want zero setup and an honest-but-coarser graph.

## Layout

```
src/tpa_engine/
  model.py        # the :Cg ontology — SINGLE SOURCE of the schema
  scip_backend.py # scip-python index.scip -> Graph (type-precise)
  ast_backend.py  # stdlib ast -> Graph (zero-dep fallback)
  frontends/
    python_static.py # richer stdlib static-analysis frontend
    scala_static.py  # Scala/SBT source-structure frontend for Joern-scale repos
  neo4j_sink.py   # idempotent corpus-namespaced MERGE loader
  graphml_sink.py # GraphML + node-link JSON file output (no DB)
  fitness.py      # structural gates: import-cycle SCC (import_cycles / cycle_count)
  cli.py          # `tpa-engine index ...` / `tpa-engine check ...`
  scip_pb2.py     # generated SCIP protobuf bindings (scip.proto bundled)
tests/            # ast extraction + SCIP tokenizer + schema invariants + fitness
```
