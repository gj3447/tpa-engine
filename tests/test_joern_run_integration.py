"""Opt-in integration test: actually run Joern on a fixture, then import the export.

Exercises ``joern_backend.run_joern`` (joern-parse + the bundled CPGQL dump) end-to-end
through ``build_graph``. Skipped unless a Joern install and a JVM are reachable — set
``JOERN_HOME`` (the joern-cli dir) and ``JAVA_HOME`` (or have ``joern-parse``/``java`` on
PATH) to opt in. The pure-importer path is covered tool-free by ``test_joern_backend.py``;
this proves the runner emits a JSON that round-trips into a conformant :Cg graph.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from tpa_engine import joern_backend
from tpa_engine.model import (
    EDGE_CALLS,
    EDGE_DEFINES,
    EDGE_IMPORTS,
    EDGE_INHERITS,
    NODE_CLASS,
    validate,
)

FIX = Path(__file__).parent / "fixtures" / "javapkg"


def _joern_home() -> str | None:
    env = os.environ.get("JOERN_HOME")
    if env and (Path(env) / "joern-parse").exists():
        return env
    on_path = shutil.which("joern-parse")
    return str(Path(on_path).resolve().parent) if on_path else None


_HAVE_JOERN = _joern_home() is not None and bool(
    shutil.which("java") or os.environ.get("JAVA_HOME"))


@pytest.mark.skipif(
    not _HAVE_JOERN,
    reason="joern + JVM not available (opt-in: set JOERN_HOME + JAVA_HOME)")
def test_run_joern_javapkg_round_trips_to_cg(tmp_path):
    export = joern_backend.run_joern(
        str(FIX), joern_home=_joern_home(), language="javasrc", workdir=str(tmp_path))
    g = joern_backend.build_graph(Path(export), corpus="javapkg-int")

    assert validate(g) == []
    assert g.nodes["com.acme.App"].type == NODE_CLASS
    greet = "com.acme.Greeter.greet:java.lang.String(java.lang.String)"
    run = "com.acme.App.run:void(java.lang.String)"
    edges = {(e.source, e.etype, e.target) for e in g.edge_list()}
    assert ("com.acme", EDGE_DEFINES, "com.acme.App") in edges
    assert ("com.acme.App", EDGE_DEFINES, run) in edges
    assert ("com.acme.App", EDGE_INHERITS, "com.acme.Base") in edges
    assert (run, EDGE_CALLS, greet) in edges
    assert ("com.acme", EDGE_IMPORTS, "com.acme.Greeter") in edges
