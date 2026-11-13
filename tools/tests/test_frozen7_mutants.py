"""FROZEN-7 on-disk mutants. Killed only by a behavioural AssertionError.

Each patch is a single-guard failure. A nonzero exit from an import error,
syntax error, or crash is not a kill. 6/6 against a 6x clean control.
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
MUTANTS = Path(__file__).resolve().parent / "mutants"
REPEATS = 6

# Frozen id -> unittest path that must FAIL with AssertionError.
KILLERS = (
    (
        "MG1-restore-drops-only-hold",
        "tools.tests.test_frozen7_drops_only.Frozen7Cases.test_n2_drops_only_ships_and_files_one_issue",
    ),
    (
        "MG2-K1-skip-review-requires-human",
        "tools.tests.test_frozen7_drops_only.Frozen7HoldCauses.test_k1_review_requires_human",
    ),
    (
        "MG2-K2-skip-hard-leftover",
        "tools.tests.test_frozen7_drops_only.Frozen7HoldCauses.test_k2a_hard_archive_replay_leftover",
    ),
    (
        "MG2-K4-skip-headline-lede-touch",
        "tools.tests.test_frozen7_drops_only.Frozen7HoldCauses.test_k4_body_correction_ships_headline_holds",
    ),
    (
        "MG2-K5-skip-missing-headline",
        "tools.tests.test_frozen7_drops_only.Frozen7HoldCauses.test_k5_missing_headline",
    ),
    (
        "MG2-K6-skip-empty-headline",
        "tools.tests.test_frozen7_drops_only.Frozen7HoldCauses.test_k6_empty_headline_after_restore",
    ),
    (
        "MG2-K7-skip-offseason-open-slate",
        "tools.tests.test_frozen7_drops_only.Frozen7HoldCauses.test_k7_offseason_while_slate_open",
    ),
    (
        "MG2-K8-skip-playoff-next-unknown",
        "tools.tests.test_frozen7_drops_only.Frozen7HoldCauses.test_k8_playoff_next_unknown",
    ),
    (
        "MG2-K9-skip-word-floor",
        "tools.tests.test_frozen7_drops_only.Frozen7HoldCauses.test_k9_word_floor",
    ),
    (
        "MG3-K10-delete-force-hold",
        "tools.tests.test_frozen7_drops_only.Frozen7HoldCauses.test_k10_check_edition_force_hold",
    ),
    (
        "MG4-j2-blockers-print-only",
        "tools.tests.test_frozen7_drops_only.Frozen7Cases.test_n5_dangling_reference_holds_via_j2",
    ),
    (
        "MG5-word-floor-le",
        "tools.tests.test_frozen7_drops_only.Frozen7Cases.test_n7_word_floor_boundaries",
    ),
    (
        "MG6-sentinel-string-match-swap",
        "tools.tests.test_frozen7_drops_only.Frozen7HoldCauses.test_k2a_hard_archive_replay_leftover",
    ),
    (
        "MG7-issue-dedupe-off",
        "tools.tests.test_frozen7_drops_only.Frozen7Cases.test_n9_issue_dedupe_update_and_safe_quoting",
    ),
    (
        "MG8-skip-remainder-gates-when-drops",
        "tools.tests.test_frozen7_drops_only.Frozen7Cases.test_n10_remainder_roster_error_holds",
    ),
    (
        "MG9-hold-repair-drops-off-by-one",
        "tools.tests.test_frozen7_drops_only.Frozen7Cases.test_n8_hold_repair_drops_8_and_9",
    ),
    (
        "MG9b-aggregate-drop-ceiling-removed",
        "tools.tests.test_frozen7_drops_only.Frozen7Cases.test_n8_aggregate_ceiling_split_across_stages",
    ),
    (
        "M4-5-drop-budget-merge-signal",
        "tools.tests.test_score_progression_budget_frozen.ScoreProgressionBudgetExhaustion.test_c1_build_salvage_repair_leftover_and_hold",
    ),
    (
        "MR1-headline-correction-not-held",
        "tools.tests.test_frozen7_drops_only.Frozen7Addenda.test_r2_headline_correction_holds",
    ),
    (
        "MR2-lede-correction-not-held",
        "tools.tests.test_frozen7_drops_only.Frozen7Addenda.test_r3_lede_correction_holds_next_sentence_does_not",
    ),
    (
        "MR3-lede-boundary-off-by-one",
        "tools.tests.test_frozen7_drops_only.Frozen7Addenda.test_r3_lede_correction_holds_next_sentence_does_not",
    ),
    (
        "MR2b-retry-snapshot-removed",
        "tools.tests.test_frozen7_drops_only.Frozen7Addenda.test_a10_retry_known_error_snapshot",
    ),
    (
        "MR3b-whole-field-substring-compare",
        "tools.tests.test_frozen7_drops_only.Frozen7Addenda.test_r3_lede_correction_holds_next_sentence_does_not",
    ),
    (
        "MR4-orphan-check-skipped",
        "tools.tests.test_frozen7_drops_only.Frozen7Addenda.test_r4_dangling_reference_on_correction_holds",
    ),
    (
        "MR5-correction-issue-not-filed",
        "tools.tests.test_frozen7_drops_only.Frozen7Addenda.test_r1_body_correction_ships_and_logs",
    ),
    (
        "MR6-lede-drop-not-held",
        "tools.tests.test_frozen7_drops_only.Frozen7Addenda.test_a10_one_lede_drop_holds_p2s1_ships",
    ),
    (
        "MR7-dropped-lines-not-logged",
        "tools.tests.test_frozen7_drops_only.Frozen7Cases.test_n2_drops_only_ships_and_files_one_issue",
    ),
    (
        "MW1-flex-applied-with-no-drops",
        "tools.tests.test_frozen7_drops_only.Frozen7Addenda.test_a12_flexed_floor_boundaries",
    ),
    (
        "MW2-flex-applied-despite-dangling-ref",
        "tools.tests.test_frozen7_drops_only.Frozen7Addenda.test_a12_flexed_floor_boundaries",
    ),
    (
        "MW3-off-by-one-3315",
        "tools.tests.test_frozen7_drops_only.Frozen7Addenda.test_a12_flexed_floor_boundaries",
    ),
    (
        "MW4-shortfall-not-logged",
        "tools.tests.test_frozen7_drops_only.Frozen7Addenda.test_a12_flexed_floor_boundaries",
    ),
)


def _is_behavioural_kill(proc: subprocess.CompletedProcess) -> bool:
    blob = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode == 0:
        return False
    if "FAIL:" not in blob and "AssertionError" not in blob:
        return False
    if "FAIL:" not in blob and "ERROR:" in blob:
        return False
    crash = (
        "SyntaxError" in blob
        or "ImportError" in blob
        or "ModuleNotFoundError" in blob
        or "IndentationError" in blob
    )
    if crash and "AssertionError" not in blob:
        return False
    return True


def _run_unittest(target: str, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "unittest", target, "-q"],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
    )


def _copy_tree(dest: Path) -> None:
    shutil.copytree(
        REPO / "tools",
        dest / "tools",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".mypy_cache"),
    )
    shutil.copytree(
        REPO / ".github",
        dest / ".github",
        ignore=shutil.ignore_patterns("__pycache__"),
    )


class Frozen7Mutants(unittest.TestCase):
    def test_clean_control_six_times(self):
        env = {**os.environ, "PYTHONPATH": str(REPO)}
        unique = list(dict.fromkeys(killer for _name, killer in KILLERS))
        for round_id in range(REPEATS):
            with self.subTest(clean=round_id):
                ran = subprocess.run(
                    [sys.executable, "-m", "unittest", *unique, "-q"],
                    cwd=REPO,
                    capture_output=True,
                    text=True,
                    env=env,
                )
                self.assertEqual(
                    ran.returncode,
                    0,
                    f"clean control {round_id} failed\n{ran.stdout}\n{ran.stderr}",
                )

    def test_each_on_disk_mutant_is_killed_six_times(self):
        self.assertTrue(MUTANTS.is_dir(), MUTANTS)
        for name, killer in KILLERS:
            with self.subTest(mutant=name, killer=killer):
                patch = MUTANTS / f"{name}.patch"
                self.assertTrue(patch.is_file(), patch)
                kills = 0
                last = ""
                for round_id in range(REPEATS):
                    with tempfile.TemporaryDirectory() as tmp:
                        dest = Path(tmp)
                        _copy_tree(dest)
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
                        env = {**os.environ, "PYTHONPATH": str(dest)}
                        killed = _run_unittest(killer, dest, env)
                        if not _is_behavioural_kill(killed):
                            last = (
                                f"{name} round {round_id} not a behavioural kill "
                                f"rc={killed.returncode}\n{killed.stdout}\n{killed.stderr}"
                            )
                            break
                        kills += 1
                self.assertEqual(kills, REPEATS, last)


if __name__ == "__main__":
    unittest.main()
