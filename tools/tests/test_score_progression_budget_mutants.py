"""On-disk mutants for score-progression budget-exhaustion guards.

Each patch is a single-guard failure. A behavioural killer in
ScoreProgressionBudgetExhaustion must fail after the patch is applied
to a copy of tools/, never to the live tree.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MUTANTS = Path(__file__).resolve().parent / "fixtures" / "score_progression_budget_mutants"
KILLERS = (
    (
        "M1-cap-off-by-one",
        "test_p3_ninth_surviving_claim_fully_present",
        "cap boundary off-by-one",
    ),
    (
        "M2-stall-not-fail-closed",
        "test_p8_stall_on_pass_one_fails_closed",
        "stall exit not fail-closed",
    ),
    (
        "M3-partial-cut-allowed",
        "test_p3_ninth_surviving_claim_fully_present",
        "partial-cut allowed",
    ),
    (
        "M4-snippet-clip-exhausted-field",
        "test_p11_snippet_drop_value_does_not_touch_exhausted_field",
        "snippet clipping on an exhausted field",
    ),
    (
        "M5-leftover-not-emitted",
        "test_p4_exactly_one_hard_budget_leftover",
        "the leftover not emitted",
    ),
    (
        "M6-holdAutomerge-not-set",
        "test_p5_hold_automerge_on_exhaustion",
        "holdAutomerge not set",
    ),
)


class ScoreProgressionBudgetMutants(unittest.TestCase):
    def test_each_on_disk_mutant_is_killed(self):
        for name, killer, _guard in KILLERS:
            with self.subTest(mutant=name, killer=killer):
                patch = MUTANTS / f"{name}.patch"
                self.assertTrue(patch.is_file(), patch)
                with tempfile.TemporaryDirectory() as tmp:
                    dest = Path(tmp)
                    shutil.copytree(
                        REPO / "tools",
                        dest / "tools",
                        ignore=shutil.ignore_patterns(
                            "__pycache__", "*.pyc", ".mypy_cache"
                        ),
                    )
                    applied = subprocess.run(
                        ["patch", "-p1", "--forward", "--batch", "-i", str(patch)],
                        cwd=dest,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(
                        applied.returncode,
                        0,
                        applied.stdout + applied.stderr,
                    )
                    killed = subprocess.run(
                        [
                            sys.executable,
                            "-m",
                            "unittest",
                            (
                                "tools.tests.test_archive_replay_qa."
                                f"ScoreProgressionBudgetExhaustion.{killer}"
                            ),
                            "-q",
                        ],
                        cwd=dest,
                        capture_output=True,
                        text=True,
                        env={**os.environ, "PYTHONPATH": str(dest)},
                    )
                    self.assertNotEqual(
                        killed.returncode,
                        0,
                        f"{name} survived {killer}\n{killed.stdout}\n{killed.stderr}",
                    )


if __name__ == "__main__":
    unittest.main()
