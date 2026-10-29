"""Render dropped-sentence notes for the daily narrative PR body."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tools.chiefs_narrative import config


def _leak_rows(repair: dict | None) -> list[str]:
    rows: list[str] = []
    for item in ((repair or {}).get("leftoverIssues") or []):
        text = str(item or "").strip()
        if "fact-checker note leaked" not in text:
            continue
        if ": " in text:
            text = text.split(": ", 1)[1].strip() or text
        rows.append(text)
    return rows


def drop_body(repair: dict | None) -> str:
    """Markdown block listing salvage corrections, drops, and held leaks."""
    rows = [str(row) for row in ((repair or {}).get("droppedSentences") or []) if row]
    fixes = [
        str(row) for row in ((repair or {}).get("correctedSentences") or []) if row
    ]
    leaks = _leak_rows(repair)
    if not rows and not fixes and not leaks:
        return ""
    held = bool((repair or {}).get("holdAutomerge"))
    hold_note = " Automerge is held." if held else ""
    lines: list[str] = []
    if leaks:
        lines.extend(
            [
                "## Leaked fact-checker notes",
                "",
                "These published sentences are process asides." + hold_note,
                "",
            ]
        )
        lines.extend(f"- {row}" for row in leaks)
        lines.append("")
    if fixes:
        lines.extend(
            [
                "## Corrected sentences",
                "",
                "Fact-check salvage rewrote these lines." + hold_note,
                "",
            ]
        )
        lines.extend(f"- {row}" for row in fixes)
        lines.append("")
    if rows:
        lines.extend(
            [
                "## Dropped sentences",
                "",
                "Fact-check salvage removed these lines." + hold_note,
                "",
            ]
        )
        lines.extend(f"- {row}" for row in rows)
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render narrative repair notes.")
    parser.add_argument(
        "repair",
        nargs="?",
        default=str(config.REPAIR_JSON),
        help="Path to narrative_repair.json",
    )
    args = parser.parse_args(argv)
    path = Path(args.repair)
    if not path.is_file():
        return 0
    payload = json.loads(path.read_text(encoding="utf-8"))
    body = drop_body(payload if isinstance(payload, dict) else {})
    if body:
        sys.stdout.write(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
