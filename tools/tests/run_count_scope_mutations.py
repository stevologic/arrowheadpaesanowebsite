#!/usr/bin/env python3
"""On-disk mutants for the Q4 count-scope fix (run 42388486140)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable
FACTS = ROOT / "tools" / "chiefs_narrative" / "facts.py"
TESTS = [
    (
        "tools.tests.test_narrative.Oct14EditionGates."
        "test_q4_denver_td_count_ignores_prior_sentence_clock"
    ),
    (
        "tools.tests.test_narrative.Oct14EditionGates."
        "test_count_scope_play_clock_nearest_weak_and_sentence"
    ),
]


def _run(tests: list[str]) -> tuple[int, str]:
    result = subprocess.run(
        [PYTHON, "-m", "unittest", *tests],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    out = (result.stdout or "") + (result.stderr or "")
    return result.returncode, out


def _mutate(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise AssertionError(f"anchor missing in {path}: {old!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _restore(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def main() -> int:
    rows = []
    mutants = [
        {
            "id": "char-window-search",
            "desc": "revert count-scope to ±72-char leftmost search",
            "path": FACTS,
            "old": (
                "    sent_start, sent_end = _sentence_span(text, start)\n"
                "    window = text[sent_start:sent_end]\n"
            ),
            "new": (
                "    window = text[max(0, start - 72) : min(len(text), end + 72)]\n"
                "    match = _COUNT_SCOPE.search(window)\n"
                "    if not match:\n"
                "        return None\n"
                "    token = _normalize_scope_token(match.group(1))\n"
                "    if token in _WHOLE_GAME_SCOPES:\n"
                "        return None\n"
                "    if token in _SCOPE_QUARTERS:\n"
                "        return _SCOPE_QUARTERS[token]\n"
                "    return \"skip\"\n"
                "    sent_start, sent_end = _sentence_span(text, start)\n"
                "    window = text[sent_start:sent_end]\n"
            ),
            "tests": TESTS,
        },
        {
            "id": "weak-adjective-outranks-quarter",
            "desc": "let nearer early/late beat an explicit fourth quarter",
            "path": FACTS,
            "old": (
                "        weak = 1 if token in _WEAK_COUNT_SCOPES else 0\n"
                "        ranked.append((weak, dist, side, match.start(), token))\n"
            ),
            "new": (
                "        weak = 0\n"
                "        ranked.append((weak, dist, side, match.start(), token))\n"
            ),
            "tests": TESTS,
        },
        {
            "id": "M3-drop-play-clock-skip",
            "desc": "treat Qn MM:SS play clocks as count windows",
            "path": FACTS,
            "old": (
                "        if re.match(r\"q[1-4]$\", token) and "
                "_PLAY_CLOCK_AFTER.match(after):\n"
                "            continue\n"
            ),
            "new": (
                "        if re.match(r\"q[1-4]$\", token) and "
                "_PLAY_CLOCK_AFTER.match(after):\n"
                "            pass\n"
            ),
            "tests": TESTS,
        },
        {
            "id": "M5-weak-set-missing-late",
            "desc": "drop late from the weak adjective set",
            "path": FACTS,
            "old": (
                '_WEAK_COUNT_SCOPES = frozenset({"early", "opening", "late"})\n'
            ),
            "new": (
                '_WEAK_COUNT_SCOPES = frozenset({"early", "opening"})\n'
            ),
            "tests": TESTS,
        },
        {
            "id": "M6-leftmost-not-nearest",
            "desc": "leftmost qualifier instead of nearest (distance zeroed)",
            "path": FACTS,
            "old": (
                "        ranked.append((weak, dist, side, match.start(), token))\n"
            ),
            "new": (
                "        ranked.append((weak, 0, 0, match.start(), token))\n"
            ),
            "tests": TESTS,
        },
        {
            "id": "M8-whole-text-window",
            "desc": "search the whole text instead of the claim sentence",
            "path": FACTS,
            "old": (
                "    sent_start, sent_end = _sentence_span(text, start)\n"
                "    window = text[sent_start:sent_end]\n"
            ),
            "new": (
                "    sent_start, sent_end = 0, len(text)\n"
                "    window = text[sent_start:sent_end]\n"
            ),
            "tests": TESTS,
        },
    ]

    killed = 0
    for mutant in mutants:
        path: Path = mutant["path"]
        original = path.read_text(encoding="utf-8")
        try:
            _mutate(path, mutant["old"], mutant["new"])
            code, out = _run(mutant["tests"])
            status = "KILLED" if code != 0 else "SURVIVED"
            if code != 0:
                killed += 1
            last = ""
            for line in out.splitlines()[::-1]:
                if line.strip():
                    last = line.strip()
                    break
            rows.append((mutant["id"], mutant["desc"], status, last[:160]))
            print(f"{mutant['id']}: {status}", flush=True)
            if last:
                print(f"  {last}", flush=True)
        finally:
            _restore(path, original)

    print()
    print("| id | description | result | evidence |")
    print("| --- | --- | --- | --- |")
    for mid, desc, status, evidence in rows:
        safe = evidence.replace("|", "\\|")
        print(f"| `{mid}` | {desc} | **{status}** | {safe} |")
    print()
    print(f"killed {killed} / {len(mutants)}")
    return 0 if killed == len(mutants) else 1


if __name__ == "__main__":
    raise SystemExit(main())
