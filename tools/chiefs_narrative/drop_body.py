"""Render dropped-sentence notes for the daily narrative PR body."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tools.chiefs_narrative import config


def drop_body(repair: dict | None) -> str:
    """Markdown block listing salvage drops, or empty when there are none."""
    rows = [str(row) for row in ((repair or {}).get("droppedSentences") or []) if row]
    if not rows:
        return ""
    held = bool((repair or {}).get("holdAutomerge"))
    reason = (
        "Fact-check salvage removed these lines. Automerge is held."
        if held
        else "Fact-check salvage removed these lines."
    )
    lines = ["## Dropped sentences", "", reason, ""]
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
