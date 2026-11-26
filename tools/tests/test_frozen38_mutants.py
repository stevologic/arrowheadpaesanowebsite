"""Unittest wrapper for FROZEN-38 mutants. Kill = AssertionError only."""
from __future__ import annotations

import unittest

from tools.tests.frozen38_mutants import MUTANTS, apply_mutant, run_one


class Frozen38Mutants(unittest.TestCase):
    def test_mutant_specs_apply(self):
        from pathlib import Path

        from tools.tests.frozen38_mutants import mutant_old_present

        root = Path(__file__).resolve().parents[2]
        missing = []
        for mid, _killer, rel, old, _new in MUTANTS:
            for hunk in mutant_old_present(root, rel, old):
                missing.append(f"{mid} {rel}: {hunk!r}")
        self.assertEqual(missing, [])

    def test_six_x_clean_control(self):
        from tools.tests.frozen38_mutants import run_clean_control

        rows = run_clean_control(6)
        self.assertEqual(len(rows), 6)
        for row in rows:
            self.assertEqual(row["returncode"], 0, row.get("stderr"))

    def test_each_mutant_is_killed_behaviourally(self):
        from tools.tests.frozen38_mutants import run_all

        rows = run_all(workers=4)
        failures = [row for row in rows if not row.get("ok")]
        detail = "\n".join(
            f"{row['id']} {row.get('killer')} rc={row.get('returncode')} "
            f"assertion={row.get('assertion')} {row.get('why') or ''} "
            f"{(row.get('stderr') or '')[-200:]}"
            for row in failures
        )
        self.assertEqual(failures, [], detail)


if __name__ == "__main__":
    unittest.main()
