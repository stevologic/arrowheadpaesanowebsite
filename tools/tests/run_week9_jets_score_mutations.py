#!/usr/bin/env python3
"""Replay the Week 9 Jets score mutants and write the on-disk table.

Invokes the behavioural unittest that records each mutant, the test that
killed it, and actual vs expected. Exit 0 only when every mutant is killed.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "tools" / "tests" / "fixtures" / "week9_jets_score_mutations.md"
TEST = (
    "tools.tests.test_narrative.Oct14EditionGates."
    "test_jets_week9_score_mutants_are_killed"
)


def main() -> int:
    result = subprocess.run(
        [sys.executable, "-m", "unittest", TEST, "-v"],
        cwd=ROOT,
    )
    if result.returncode != 0:
        return result.returncode
    if not REPORT.is_file():
        print(f"missing mutation report: {REPORT}", file=sys.stderr)
        return 1
    print(REPORT.read_text(encoding="utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
