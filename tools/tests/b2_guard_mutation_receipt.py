#!/usr/bin/env python3
"""Drop-one mutants of B2 --check-edition guards vs isolating tests.

Not named test_*.py so unittest discover does not run mutations.
Control and mutant each run 6x.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable
N = 6

MEMBERS: list[dict] = [
    {
        "id": "m_after",
        "file": "tools/chiefs_narrative/facts.py",
        "old": (
            '_WIN_AFTER_SCORE_REACHED_ENDASH = re.compile(\n'
            '    r"(?<!non-)(?<!un-)\\bafter\\s+the\\s+score\\s+reached\\s+"\n'
            '    r"(\\d{1,2})\\s*–\\s*(\\d{1,2})\\b",\n'
            "    re.I,\n"
            ")"
        ),
        "new": (
            '_WIN_AFTER_SCORE_REACHED_ENDASH = re.compile(\n'
            '    r"(?!x)x",\n'
            "    re.I,\n"
            ")"
        ),
        "test": (
            "tools.tests.test_window_scoring_counts."
            "WindowScoringCounts.test_win_after_score_reached_endash"
        ),
        "fixture": (
            "Kansas City scored five touchdowns after the score reached 14–12."
        ),
    },
    {
        "id": "m_total",
        "file": "tools/chiefs_narrative/facts.py",
        "old": (
            "    return int(row.get(\"rush\") or 0) + int(row.get(\"rec\") or 0)"
        ),
        "new": "    return int(row.get(\"rush\") or 0)",
        "test": (
            "tools.tests.test_b2_check_edition."
            "PlayerTdTotals.test_m_total_flags_one_vs_espn_three"
        ),
        "fixture": "Walker scored one touchdown all day, the 2-yard run at Q3 0:11.",
    },
    {
        "id": "n1_like_with_like",
        "file": "tools/chiefs_narrative/facts.py",
        "old": (
            "        else:\n"
            "            row = totals.get(player) or {}\n"
            "            official = _player_td_sum(row) if row else None\n"
            '            label = "total TDs"'
        ),
        "new": (
            "        else:\n"
            "            official = rush_only.get(player)\n"
            '            label = "total TDs"'
        ),
        "test": (
            "tools.tests.test_b2_check_edition."
            "SourceTdScope.test_rushing_leader_vs_all_purpose_is_not_a_conflict"
        ),
        "fixture": "Chiefs.com three-touchdown vs ESPN rushing-leader 1 TD",
    },
    {
        "id": "m_fox_tv",
        "file": "tools/chiefs_narrative/facts.py",
        "old": (
            "    if not left or not right:\n"
            "        return True\n"
            "    if left == right:\n"
            "        return True\n"
            "    left_c = re.sub(r\"\\s+\", \"\", left)\n"
            "    right_c = re.sub(r\"\\s+\", \"\", right)\n"
            "    return left_c in right_c or right_c in left_c\n"
        ),
        "new": (
            "    if not left or not right:\n"
            "        return True\n"
            "    if left == right:\n"
            "        return True\n"
            "    left_c = re.sub(r\"\\s+\", \"\", left)\n"
            "    right_c = re.sub(r\"\\s+\", \"\", right)\n"
            "    return True\n"
        ),
        "test": (
            "tools.tests.test_b2_check_edition."
            "NextKickoffTv.test_m_fox_flags_tv"
        ),
        "fixture": "nextGame.tv FOX vs ESPN CBS",
        "unique": "def _tv_matches",
    },
    {
        "id": "m_fox_kickoff",
        "file": "tools/chiefs_narrative/facts.py",
        "old": (
            "    if left is None or right is None:\n"
            "        return True\n"
            "    return left == right\n"
        ),
        "new": (
            "    if left is None or right is None:\n"
            "        return True\n"
            "    return True\n"
        ),
        "test": (
            "tools.tests.test_b2_check_edition."
            "NextKickoffTv.test_m_fox_flags_kickoff"
        ),
        "fixture": "nextGame 3:25 PM CT vs ESPN 12:00 PM CT",
    },
    {
        "id": "red_zone_tds",
        "file": "tools/chiefs_narrative/facts.py",
        "old": (
            "def _red_zone_tds(pair: tuple[int, int]) -> int:\n"
            '    """TD (made) side of ESPN redZoneEff. Mutant: return trips instead."""\n'
            "    return int(pair[0])\n"
        ),
        "new": (
            "def _red_zone_tds(pair: tuple[int, int]) -> int:\n"
            '    """TD (made) side of ESPN redZoneEff. Mutant: return trips instead."""\n'
            "    return 5\n"
        ),
        "test": (
            "tools.tests.test_b2_check_edition."
            "RedZoneThirdDown.test_five_red_zone_tds_flags"
        ),
        "fixture": "red-zone finish that produced five touchdowns",
    },
    {
        "id": "third_down",
        "file": "tools/chiefs_narrative/facts.py",
        "old": (
            "            if claimed == official:\n"
            "                continue\n"
            "            if (\n"
            '                team == "KC"\n'
        ),
        "new": (
            "            if True:\n"
            "                continue\n"
            "            if (\n"
            '                team == "KC"\n'
        ),
        "test": (
            "tools.tests.test_b2_check_edition."
            "RedZoneThirdDown.test_third_down_seven_of_eleven_flags"
        ),
        "fixture": "Kansas City converted 7 of 11 third downs.",
    },
    {
        "id": "hold_automerge",
        "file": "tools/chiefs_narrative/edition_merge.py",
        "old": "    if repair_holds_automerge(root):\n",
        "new": "    if False and repair_holds_automerge(root):\n",
        "test": (
            "tools.tests.test_b2_check_edition."
            "HoldAutomergePaths.test_inspect_pr_refuses_hold_automerge"
        ),
        "fixture": "holdAutomerge true refuses edition_merge --pr",
    },
    {
        "id": "n3_changed_archive",
        "file": "tools/chiefs_narrative/generate.py",
        "old": (
            "def _fact_check_changed_editions() -> list[str]:\n"
            '    """Fact-check changed archive edition JSON files. Mutant: return []."""\n'
        ),
        "new": (
            "def _fact_check_changed_editions() -> list[str]:\n"
            '    """Fact-check changed archive edition JSON files. Mutant: return []."""\n'
            "    return []\n"
        ),
        "test": (
            "tools.tests.test_b2_check_edition."
            "N3ChangedEditions.test_n3_archived_false_count_fails_check_edition"
        ),
        "fixture": "five touchdowns only in a changed archived edition file",
    },
    {
        "id": "n3_live_equals_latest",
        "file": "tools/chiefs_narrative/generate.py",
        "old": (
            "def _live_matches_latest_edition() -> list[str]:\n"
            '    """Assert live narrative.json equals the latest edition file. Mutant: skip."""\n'
        ),
        "new": (
            "def _live_matches_latest_edition() -> list[str]:\n"
            '    """Assert live narrative.json equals the latest edition file. Mutant: skip."""\n'
            "    return []\n"
        ),
        "test": (
            "tools.tests.test_b2_check_edition."
            "N3ChangedEditions.test_n3_live_differs_from_latest_fails"
        ),
        "fixture": "live narrative.json differs from the latest edition file",
    },
]


def _run(selector: str) -> tuple[int, str]:
    proc = subprocess.run(
        [PYTHON, "-m", "unittest", selector, "-v"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _repeat(selector: str, n: int) -> tuple[int, int, str]:
    ok = fail = 0
    detail = ""
    for _ in range(n):
        rc, out = _run(selector)
        if rc == 0:
            ok += 1
        else:
            fail += 1
            detail = out[-1200:]
    return ok, fail, detail


def _apply(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise SystemExit(f"need exactly one match in {path}: {old[:80]!r} count={text.count(old)}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def main() -> int:
    rows = []
    originals: dict[Path, str] = {}
    try:
        for member in MEMBERS:
            path = ROOT / member["file"]
            originals.setdefault(path, path.read_text(encoding="utf-8"))
            selector = member["test"]
            ctrl_ok, ctrl_fail, ctrl_detail = _repeat(selector, N)
            if ctrl_fail:
                print("CONTROL FAIL", member["id"], ctrl_detail[-500:])
                return 1
            _apply(path, member["old"], member["new"])
            try:
                mut_ok, mut_fail, detail = _repeat(selector, N)
            finally:
                path.write_text(originals[path], encoding="utf-8")
            vacuous = mut_fail == 0
            rows.append(
                {
                    "id": member["id"],
                    "fixture": member["fixture"],
                    "mutant": f"drop {member['id']}",
                    "test": member["test"].rsplit(".", 1)[-1],
                    "control": f"{ctrl_ok}/{N} pass",
                    "kills": f"{mut_fail}/{N}",
                    "actual": "FAIL" if mut_fail else "PASS",
                    "expected": "FAIL",
                    "vacuous": vacuous,
                    "detail": detail[-400:] if mut_fail else "control-like pass",
                }
            )
            flag = "VACUOUS" if vacuous else "KILLED"
            print(f"{flag:8} {member['id']:20} {mut_fail}/{N}")
    finally:
        for path, text in originals.items():
            path.write_text(text, encoding="utf-8")

    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/b2-guard-receipt")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "class_coverage.json").write_text(
        json.dumps(rows, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# B2 class-coverage receipt",
        "",
        "| item | isolating fixture | mutant | kill n/n | actual vs expected | vacuous? |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['id']} | {row['fixture']} | {row['mutant']} | "
            f"{row['kills']} | {row['actual']} vs {row['expected']} | "
            f"{'yes' if row['vacuous'] else 'no'} |"
        )
    (out_dir / "receipt.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(out_dir / "receipt.md")
    vacuous = [row["id"] for row in rows if row["vacuous"]]
    if vacuous:
        print("VACUOUS MEMBERS", vacuous)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
