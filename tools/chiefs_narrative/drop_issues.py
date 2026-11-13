"""File or update the tracking issue that lists dropped (and shipping-corrected) lines.

N9/A10/A12/R1: one issue per edition slug, label ``narrative-drops``. Reruns of
the same run/edition do not open a duplicate. A later regeneration of the
same slug updates the open issue. Dropped text is quoted so shell
metacharacters and @mentions cannot expand or ping.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from tools.chiefs_narrative import config, facts

ISSUE_LABEL = facts.DROPS_ISSUE_LABEL
TITLE_PREFIX = facts.DROPS_ISSUE_TITLE_PREFIX


def issue_title(slug: str) -> str:
    return f"{TITLE_PREFIX} {slug}".strip()


def safe_issue_line(text: str) -> str:
    """Quote a dropped/corrected line: no shell expansion, no @pings."""
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    raw = raw.replace("@", "@\u200b")
    raw = raw.replace("```", "'''")
    return f"```\n{raw}\n```"


def _run_id() -> str:
    return str(os.environ.get("GITHUB_RUN_ID") or "").strip()


def build_issue_body(
    *,
    slug: str,
    drops: list[str],
    corrections: list[str] | None = None,
    run_id: str = "",
    shortfall: int = 0,
    edition: str = "",
) -> str:
    lines = [
        f"Edition slug: `{slug}`",
        f"Edition: {edition or slug}",
        f"Run id: `{run_id or _run_id() or 'unknown'}`",
        "",
        "## Dropped sentences",
        "",
    ]
    if drops:
        for row in drops:
            lines.append(safe_issue_line(row))
            lines.append("")
    else:
        lines.append("(none)")
        lines.append("")
    if corrections:
        lines.extend(["## Corrected sentences", ""])
        for row in corrections:
            lines.append(safe_issue_line(row))
            lines.append("")
    if shortfall > 0:
        lines.extend(
            [
                "## Word-count shortfall",
                "",
                f"Remainder is {shortfall} words under PUBLISH_WORD_FLOOR "
                f"({facts.PUBLISH_WORD_FLOOR}).",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _gh(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["gh", *args],
        check=check,
        capture_output=True,
        text=True,
    )


def list_open_drop_issues() -> list[dict]:
    ran = _gh(
        [
            "issue",
            "list",
            "--label",
            ISSUE_LABEL,
            "--state",
            "open",
            "--json",
            "number,title,body",
            "--limit",
            "100",
        ],
        check=False,
    )
    if ran.returncode != 0:
        return []
    try:
        payload = json.loads(ran.stdout or "[]")
    except json.JSONDecodeError:
        return []
    return payload if isinstance(payload, list) else []


def find_open_issue(slug: str) -> dict | None:
    title = issue_title(slug)
    for row in list_open_drop_issues():
        if str(row.get("title") or "") == title:
            return row
    return None


def ensure_label() -> None:
    _gh(
        [
            "label",
            "create",
            ISSUE_LABEL,
            "--color",
            "FBCE4A",
            "--description",
            "Chiefs Narrative dropped lines",
            "--force",
        ],
        check=False,
    )


def _same_run_already_logged(body: str, run_id: str, slug: str) -> bool:
    if not run_id:
        return False
    return (
        f"Run id: `{run_id}`" in (body or "")
        and f"Edition slug: `{slug}`" in (body or "")
    )


def sync_drop_issue(
    *,
    slug: str,
    drops: list[str],
    corrections: list[str] | None = None,
    run_id: str = "",
    shortfall: int = 0,
    edition: str = "",
    hold: bool = False,
) -> str | None:
    """Create or update the tracking issue. None when nothing to log.

    Shipping editions (hold=False) log every dropped line. Held editions
    still log drops so the desk can see what salvage removed. N1: empty
    drops and no shipping corrections → no issue.
    """
    corrections = [row for row in (corrections or []) if row]
    drops = [row for row in (drops or []) if row]
    if not drops and (hold or not corrections):
        return None
    ensure_label()
    body = build_issue_body(
        slug=slug,
        drops=drops,
        corrections=corrections,
        run_id=run_id or _run_id(),
        shortfall=shortfall,
        edition=edition,
    )
    existing = find_open_issue(slug)
    if existing:
        number = str(existing.get("number") or "")
        prior = str(existing.get("body") or "")
        if _same_run_already_logged(prior, run_id or _run_id(), slug) and prior == body:
            return f"#{number}"
        body_file_edit = _write_temp_body(body)
        _gh(["issue", "edit", number, "--body-file", str(body_file_edit)])
        return f"#{number}"
    body_file = _write_temp_body(body)
    created = _gh(
        [
            "issue",
            "create",
            "--title",
            issue_title(slug),
            "--label",
            ISSUE_LABEL,
            "--body-file",
            str(body_file),
        ]
    )
    url = (created.stdout or "").strip()
    return url or issue_title(slug)


def _write_temp_body(body: str) -> Path:
    root = Path(os.environ.get("RUNNER_TEMP") or "/tmp")
    root.mkdir(parents=True, exist_ok=True)
    path = root / "narrative-drops-issue.md"
    path.write_text(body, encoding="utf-8")
    return path


def _resolve_data_file(explicit: Path | None, fallback: Path) -> Path:
    if explicit is not None:
        return explicit
    cwd_copy = Path.cwd() / "data" / fallback.name
    if cwd_copy.is_file():
        return cwd_copy
    hidden = Path.cwd() / fallback.name
    if fallback.name.startswith(".") and hidden.is_file():
        return hidden
    return fallback


def load_repair(path: Path | None = None) -> dict:
    target = _resolve_data_file(path, config.REPAIR_JSON)
    if not target.is_file():
        return {}
    try:
        loaded = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def load_narrative(path: Path | None = None) -> dict:
    target = _resolve_data_file(path, config.NARRATIVE_JSON)
    if not target.is_file():
        return {}
    try:
        loaded = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="File dropped-lines tracking issue.")
    parser.add_argument("--repair", default="", help="Path to narrative_repair.json")
    parser.add_argument("--narrative", default="", help="Path to narrative.json")
    args = parser.parse_args(argv)
    repair = load_repair(Path(args.repair) if args.repair else None)
    narrative = load_narrative(Path(args.narrative) if args.narrative else None)
    slug = str(narrative.get("slug") or repair.get("slug") or "").strip()
    if not slug:
        print("no edition slug; skipping drop issue")
        return 0
    drops = [str(row) for row in (repair.get("droppedSentences") or []) if row]
    corrections = [
        str(row) for row in (repair.get("correctedSentences") or []) if row
    ]
    hold = bool(repair.get("holdAutomerge"))
    shortfall = int(repair.get("wordShortfall") or 0)
    result = sync_drop_issue(
        slug=slug,
        drops=drops,
        corrections=corrections,
        run_id=_run_id(),
        shortfall=shortfall,
        edition=str(narrative.get("edition") or ""),
        hold=hold,
    )
    if result:
        print(f"drop issue: {result}")
    else:
        print("no dropped or shipping-corrected lines; skipping drop issue")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
