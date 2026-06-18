"""Per-violation-row baseline ratchet (jQAssistant BaselineManager absorption).

Accept the CURRENT set of violation rows as a checked-in ``baseline.json`` artifact and
fail only on NEWLY-introduced rows (set membership) — so the structural gate is adoptable
on a brownfield repo with pre-existing debt WITHOUT going green-and-blind, and a cycle
SWAP that leaves the coarse count unchanged still fails (the row-set is strictly finer than
an integer count). A violation ROW = one import cycle, canonicalized as the sorted tuple of
module names (matching ``fitness.import_cycles``' determinism contract, so the file is
byte-stable and diff/merge-friendly).

Lifecycle mirrors ``BaselineManager``: ``load`` (start) → ``is_existing``/``diff`` (per-row)
→ ``save_if_changed`` (stop, write-only-on-change = no noise commits).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

_VERSION = 1


@dataclass(frozen=True)
class Baseline:
    """A set of known-debt violation rows (canonical sorted-tuple keys)."""

    rows: frozenset[tuple[str, ...]] = field(default_factory=frozenset)

    @staticmethod
    def _key(cycle: list[str]) -> tuple[str, ...]:
        return tuple(sorted(cycle))

    @staticmethod
    def load(path: Path) -> Baseline:
        """Read a ``baseline.json``. A missing file is an EMPTY baseline (not an error) —
        first-adoption ergonomics."""
        if not path.exists():
            return Baseline()
        data = json.loads(path.read_text(encoding="utf-8"))
        return Baseline(frozenset(Baseline._key(c) for c in data.get("cycles", [])))

    def is_existing(self, cycle: list[str]) -> bool:
        """Per-row ``isExisting``: is this cycle already known debt (order-insensitive)?"""
        return self._key(cycle) in self.rows

    def diff(self, current: list[list[str]]) -> tuple[list[list[str]], list[list[str]]]:
        """``(new_rows, fixed_rows)``: new = present now & absent from baseline (the FAIL
        set); fixed = in baseline & gone now (info only — the ratchet moves down)."""
        cur = {self._key(c) for c in current}
        new = sorted([list(k) for k in cur if k not in self.rows])
        fixed = sorted([list(k) for k in self.rows if k not in cur])
        return new, fixed

    @staticmethod
    def _canonical_bytes(corpus: str, cycles: list[list[str]]) -> bytes:
        rows = sorted([sorted(c) for c in cycles])
        blob = json.dumps({"version": _VERSION, "corpus": corpus, "cycles": rows},
                          sort_keys=True, indent=2) + "\n"
        return blob.encode("utf-8")

    @staticmethod
    def save_if_changed(path: Path, corpus: str, cycles: list[list[str]]) -> bool:
        """Write ``baseline.json`` ONLY if the canonical bytes differ from the current file
        (the no-noise-commit rule). Returns whether it actually wrote."""
        new_bytes = Baseline._canonical_bytes(corpus, cycles)
        if path.exists() and path.read_bytes() == new_bytes:
            return False
        path.write_bytes(new_bytes)
        return True
