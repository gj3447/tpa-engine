"""Per-row baseline ratchet (OQ2) — brownfield adoptability without green-and-blind.

A violation ROW = one import cycle. The baseline accepts current debt (exit 0) and fails
only on NEW rows (exit 1) — strictly finer than the coarse ``--max-cycles`` integer count.
"""
from __future__ import annotations

from pathlib import Path

from tpa_engine.baseline import Baseline
from tpa_engine.cli import main

TWO = [["legacy.a", "legacy.b"], ["legacy.c", "legacy.d"]]


def _probe(root: Path, cycles: list[list[str]]) -> Path:
    """Write a tiny src tree realizing each 2-module import cycle; return the repo root."""
    src = root / "src" / "legacy"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("")
    for c in cycles:
        m1, m2 = c[0].split(".")[-1], c[1].split(".")[-1]
        (src / f"{m1}.py").write_text(f"from legacy.{m2} import x\n\n\ndef x():\n    return 1\n")
        (src / f"{m2}.py").write_text(f"from legacy.{m1} import x as y\n\n\ndef x():\n    return y\n")
    return root


def test_row_membership_unit():
    bl = Baseline.load(Path("/nonexistent.json"))          # missing -> empty, not error
    assert bl.diff(TWO) == (sorted(sorted(c) for c in TWO), [])   # all NEW vs empty
    bl2 = Baseline(rows=frozenset({("legacy.a", "legacy.b"), ("legacy.c", "legacy.d")}))
    assert bl2.is_existing(["legacy.b", "legacy.a"])       # order-insensitive row key
    assert bl2.diff(TWO) == ([], [])                       # no NEW rows -> green


def test_save_if_changed_is_idempotent(tmp_path):
    p = tmp_path / "baseline.json"
    assert Baseline.save_if_changed(p, "c", TWO) is True   # first write
    assert Baseline.save_if_changed(p, "c", TWO) is False  # unchanged -> no rewrite (no noise)
    assert Baseline.load(p).diff(TWO) == ([], [])


def test_e2e_ratchet(tmp_path):
    repo = _probe(tmp_path / "r0", TWO)
    bjson = tmp_path / "baseline.json"
    base = ["check", str(repo), "--src-subdir", "src", "--corpus", "p"]
    # (1) RED today: no baseline, 2 existing cycles, --max-cycles 0 -> exit 1
    assert main([*base, "--max-cycles", "0"]) == 1
    # accept current debt
    assert main([*base, "--baseline", str(bjson), "--update-baseline"]) == 0
    # (2) GREEN: baseline absorbs the 2 existing cycles -> exit 0 (false-RED absorbed)
    assert main([*base, "--baseline", str(bjson)]) == 0
    # (3) RED: a 3rd NEW cycle e<->f vs the same baseline -> exit 1
    repo3 = _probe(tmp_path / "r3", [*TWO, ["legacy.e", "legacy.f"]])
    assert main(["check", str(repo3), "--src-subdir", "src", "--corpus", "p",
                 "--baseline", str(bjson)]) == 1


def test_swap_keeps_count_but_fails_baseline_novel(tmp_path):
    # NOVEL: SWAP c<->d for e<->f (total count stays 2) -> baseline exit 1, while the coarse
    # --max-cycles 2 is row-BLIND and exits 0 -> proves the row-set is strictly finer.
    bjson = tmp_path / "baseline.json"
    Baseline.save_if_changed(bjson, "p", TWO)  # baseline = {a-b, c-d}
    repo = _probe(tmp_path / "r4", [["legacy.a", "legacy.b"], ["legacy.e", "legacy.f"]])
    args = ["check", str(repo), "--src-subdir", "src", "--corpus", "p"]
    assert main([*args, "--baseline", str(bjson)]) == 1   # e-f is NEW -> fail
    assert main([*args, "--max-cycles", "2"]) == 0        # coarse count is blind
