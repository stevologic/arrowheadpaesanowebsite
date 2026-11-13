#!/usr/bin/env python3
"""#354 r2 mutant harness. Outside unittest files. Behavioural kills only."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MUTANTS = Path(__file__).resolve().parent
RECEIPTS = MUTANTS / "receipts"
REPEATS = 6
PYTHON = sys.executable
A14 = "tools.tests.test_frozen7_a14_lede.Frozen7A14Lede"

KILLERS = (
    ("MS-A10w", f"{A14}.test_b2_build"),
    ("MS-A10w-s1", f"{A14}.test_b2_build"),
    ("MS-A10w-s2", f"{A14}.test_b2_build_stage2"),
    ("MS-A10w-s3", f"{A14}.test_b2_build_stage3"),
    ("MS-A10k", f"{A14}.test_b2_known_errors"),
    ("resolver-reverted-lastGameReview-lede", f"{A14}.test_a14_1_dek_drop_holds"),
    ("dek-ignored", f"{A14}.test_a14_1_dek_drop_holds"),
    ("union-dropped", f"{A14}.test_a14_3_lastgamereview_lede_drop_holds"),
)

EQUIVALENT = ()

_CRASH_IN_MESSAGE = (
    "Traceback",
    "SyntaxError",
    "NameError",
    "ImportError",
    "ModuleNotFoundError",
    "IndentationError",
)


def _unittest_counts(blob: str) -> tuple[int, int]:
    match = re.search(r"FAILED \(([^)]*)\)", blob)
    if not match:
        if re.search(r"\nOK\b", blob) or blob.strip().endswith("OK"):
            return 0, 0
        return 0, 0
    inner = match.group(1)
    failures = 0
    errors = 0
    found = re.search(r"failures=(\d+)", inner)
    if found:
        failures = int(found.group(1))
    found = re.search(r"errors=(\d+)", inner)
    if found:
        errors = int(found.group(1))
    return failures, errors


def _assertion_messages(blob: str) -> list[str]:
    return re.findall(
        r"AssertionError:(.*?)(?=\n(?:FAIL:|ERROR:|Ran |\Z))", blob, re.S
    )


def _fail_lines(blob: str) -> list[str]:
    return [line.strip() for line in blob.splitlines() if line.startswith("FAIL:")]


def _is_behavioural_kill(proc: subprocess.CompletedProcess) -> tuple[bool, str]:
    blob = (proc.stdout or "") + (proc.stderr or "")
    failures, errors = _unittest_counts(blob)
    if proc.returncode == 0 or failures < 1 or errors != 0:
        return False, f"rc={proc.returncode} failures={failures} errors={errors}\n{blob}"
    for message in _assertion_messages(blob):
        if any(token in message for token in _CRASH_IN_MESSAGE):
            return False, f"subprocess traceback in assertion\n{blob}"
    fails = _fail_lines(blob)
    asserts = [item.strip().splitlines()[0] for item in _assertion_messages(blob)]
    receipt = "; ".join(fails + asserts) or "FAIL"
    return True, receipt


def _run_unittest(target: str, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PYTHON, "-m", "unittest", target, "-q"],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
    )


def _copy_tree(dest: Path) -> None:
    shutil.copytree(
        REPO / "tools",
        dest / "tools",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".mypy_cache", "_regen.py"),
    )
    shutil.copytree(
        REPO / "layouts",
        dest / "layouts",
        ignore=shutil.ignore_patterns("__pycache__"),
    )


def main() -> int:
    if RECEIPTS.exists():
        shutil.rmtree(RECEIPTS)
    RECEIPTS.mkdir(parents=True, exist_ok=True)
    summary: list[dict] = []
    failed = 0
    grouped: dict[str, dict] = {}
    for name, killer in KILLERS:
        patch = MUTANTS / f"{name}.patch"
        if not patch.is_file():
            print(f"MISSING {patch}", file=sys.stderr)
            failed += 1
            continue
        kills = 0
        controls = 0
        last = ""
        last_receipt = ""
        for round_id in range(REPEATS):
            with tempfile.TemporaryDirectory() as tmp:
                dest = Path(tmp)
                _copy_tree(dest)
                env = {**os.environ, "PYTHONPATH": str(dest)}
                clean = _run_unittest(killer, dest, env)
                if clean.returncode == 0:
                    controls += 1
                else:
                    last = (
                        f"{name} clean control {round_id} failed "
                        f"in copied tree\n{clean.stdout}\n{clean.stderr}"
                    )
                    break
                applied = subprocess.run(
                    ["patch", "-p1", "--forward", "--batch", "-i", str(patch)],
                    cwd=dest,
                    capture_output=True,
                    text=True,
                )
                if applied.returncode != 0:
                    last = applied.stdout + applied.stderr
                    break
                killed = _run_unittest(killer, dest, env)
                ok, detail = _is_behavioural_kill(killed)
                if not ok:
                    last = (
                        f"{name} round {round_id} not a behavioural kill "
                        f"{detail}"
                    )
                    break
                last_receipt = detail
                kills += 1
        row = {
            "mutant": name,
            "killer": killer,
            "kills": kills,
            "control_passes": controls,
            "repeats": REPEATS,
            "receipt": last_receipt,
            "error": last,
        }
        summary.append(row)
        acc = grouped.setdefault(
            name,
            {"kills": 0, "control_passes": 0, "rounds": 0, "killers": []},
        )
        acc["kills"] += kills
        acc["control_passes"] += controls
        acc["rounds"] += REPEATS
        acc["killers"].append(killer)
        receipt_path = RECEIPTS / f"{name}.txt"
        blob = (
            f"mutant: {name}\n"
            f"killer: {killer}\n"
            f"kills: {kills}/{REPEATS}\n"
            f"control_passes: {controls}/{REPEATS}\n"
            f"receipt: {last_receipt}\n"
        )
        if last:
            blob += f"error:\n{last}\n"
        existing = receipt_path.read_text(encoding="utf-8") if receipt_path.is_file() else ""
        receipt_path.write_text(existing + blob + "\n", encoding="utf-8")
        status = "KILLED" if kills == REPEATS and controls == REPEATS else "SURVIVED"
        if status != "KILLED":
            failed += 1
        print(f"{status} {name} {killer} {kills}/{REPEATS} control {controls}/{REPEATS}")
        if last and status != "KILLED":
            print(last[:2000])
    for name, killer in EQUIVALENT:
        patch = MUTANTS / f"{name}.patch"
        if not patch.is_file():
            print(f"MISSING {patch}", file=sys.stderr)
            failed += 1
            continue
        survives = 0
        controls = 0
        last = ""
        for round_id in range(REPEATS):
            with tempfile.TemporaryDirectory() as tmp:
                dest = Path(tmp)
                _copy_tree(dest)
                env = {**os.environ, "PYTHONPATH": str(dest)}
                clean = _run_unittest(killer, dest, env)
                if clean.returncode == 0:
                    controls += 1
                else:
                    last = (
                        f"{name} clean control {round_id} failed "
                        f"in copied tree\n{clean.stdout}\n{clean.stderr}"
                    )
                    break
                applied = subprocess.run(
                    ["patch", "-p1", "--forward", "--batch", "-i", str(patch)],
                    cwd=dest,
                    capture_output=True,
                    text=True,
                )
                if applied.returncode != 0:
                    last = applied.stdout + applied.stderr
                    break
                mutated = _run_unittest(killer, dest, env)
                if mutated.returncode != 0:
                    last = (
                        f"{name} round {round_id} not equivalent "
                        f"(mutant must still PASS)\n{mutated.stdout}\n{mutated.stderr}"
                    )
                    break
                survives += 1
        row = {
            "mutant": name,
            "killer": killer,
            "equivalent": True,
            "survives": survives,
            "control_passes": controls,
            "repeats": REPEATS,
            "error": last,
        }
        summary.append(row)
        acc = grouped.setdefault(
            name,
            {
                "kills": 0,
                "survives": 0,
                "control_passes": 0,
                "rounds": 0,
                "killers": [],
                "equivalent": True,
            },
        )
        acc["survives"] += survives
        acc["control_passes"] += controls
        acc["rounds"] += REPEATS
        acc["killers"].append(killer)
        receipt_path = RECEIPTS / f"{name}.txt"
        blob = (
            f"mutant: {name}\n"
            f"killer: {killer}\n"
            f"equivalent: yes\n"
            f"survives: {survives}/{REPEATS}\n"
            f"control_passes: {controls}/{REPEATS}\n"
        )
        if last:
            blob += f"error:\n{last}\n"
        existing = receipt_path.read_text(encoding="utf-8") if receipt_path.is_file() else ""
        receipt_path.write_text(existing + blob + "\n", encoding="utf-8")
        status = (
            "EQUIVALENT"
            if survives == REPEATS and controls == REPEATS
            else "NOT-EQUIVALENT"
        )
        if status != "EQUIVALENT":
            failed += 1
        print(
            f"{status} {name} {killer} survives {survives}/{REPEATS} "
            f"control {controls}/{REPEATS}"
        )
        if last and status != "EQUIVALENT":
            print(last[:2000])
    (RECEIPTS / "summary.json").write_text(
        json.dumps({"rows": summary, "grouped": grouped}, indent=2) + "\n",
        encoding="utf-8",
    )
    table_lines = [
        "mutant\tkills\tcontrol\treceipt",
    ]
    for name, acc in grouped.items():
        if acc.get("equivalent"):
            table_lines.append(
                f"{name}\tEQUIV {acc.get('survives', 0)}/{acc['rounds']}\t"
                f"{acc['control_passes']}/{acc['rounds']}\t"
                f"tests/mutants_354_r2/receipts/{name}.txt"
            )
        else:
            table_lines.append(
                f"{name}\t{acc['kills']}/{acc['rounds']}\t"
                f"{acc['control_passes']}/{acc['rounds']}\t"
                f"tests/mutants_354_r2/receipts/{name}.txt"
            )
    (RECEIPTS / "table.txt").write_text("\n".join(table_lines) + "\n", encoding="utf-8")
    print("\n".join(table_lines))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
