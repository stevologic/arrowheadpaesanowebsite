"""Refuse unsigned Review editions on every path to main and gh-pages.

A Review-phase edition JSON (phase.mode=review or ``Week N · Week M Review``)
cannot automerge or deploy unless a human signed it off. Sign-off is either
the ``qa-pass`` label on the PR or ``data/review_signoff/<slug>.json``
containing PASS. Easy to disable later via ``REVIEW_SIGNOFF_REQUIRED``.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from tools.chiefs_narrative import config

REVIEW_SIGNOFF_REQUIRED = True
QA_PASS_LABEL = "qa-pass"
SIGNOFF_DIR = "data/review_signoff"
EDITION_NAMES = frozenset(
    {
        "data/narrative.json",
        "data/narrative_archive.json",
    }
)
_REVIEW_EDITION = re.compile(
    r"Week\s+\d+\s+·\s+Week\s+\d+\s+Review",
    re.IGNORECASE,
)


def edition_path(path: str | Path) -> bool:
    text = str(path).replace("\\", "/")
    if text in EDITION_NAMES:
        return True
    return text.startswith("data/narrative_editions/") and text.endswith(".json")


def load_payload(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def payload_is_review(payload: dict | None) -> bool:
    """True for phase.mode=review or a 'Week N · Week M Review' edition.

    Duplicated from facts.is_review_edition so automerge/pages can run
    this module without importing facts (and therefore requests).
    """
    if not payload:
        return False
    phase = payload.get("phase") if isinstance(payload.get("phase"), dict) else {}
    if str((phase or {}).get("mode") or "").strip().lower() == "review":
        return True
    edition = str(
        payload.get("edition") or (phase or {}).get("edition") or ""
    )
    return bool(_REVIEW_EDITION.search(edition))


def labels_include_qa_pass(labels) -> bool:
    for label in labels or []:
        if str(label).strip().lower() == QA_PASS_LABEL:
            return True
    return False


def signoff_path(slug: str, root: Path | None = None) -> Path:
    base = Path(root) if root is not None else config.REPO_ROOT
    return base / SIGNOFF_DIR / f"{slug}.json"


def committed_signoff(slug: str, root: Path | None = None) -> bool:
    if not slug:
        return False
    path = signoff_path(slug, root)
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return "pass" in text.lower()


def review_signed_off(
    payload: dict | None, *, labels=None, root: Path | None = None
) -> bool:
    if labels_include_qa_pass(labels):
        return True
    slug = str((payload or {}).get("slug") or "").strip()
    return committed_signoff(slug, root)


def should_block_review(
    payload: dict | None, *, labels=None, root: Path | None = None
) -> bool:
    if not REVIEW_SIGNOFF_REQUIRED:
        return False
    if not payload_is_review(payload):
        return False
    return not review_signed_off(payload, labels=labels, root=root)


def changed_review_payloads(
    paths, *, root: Path | None = None
) -> list[tuple[str, dict]]:
    base = Path(root) if root is not None else config.REPO_ROOT
    found: list[tuple[str, dict]] = []
    for raw in paths or []:
        rel = str(raw).replace("\\", "/")
        if not edition_path(rel):
            continue
        payload = load_payload(base / rel)
        if payload_is_review(payload):
            found.append((rel, payload or {}))
    return found


def automerge_blocked(
    paths, *, labels=None, root: Path | None = None
) -> bool:
    """True when a changed Review edition JSON has no human sign-off."""
    for _rel, payload in changed_review_payloads(paths, root=root):
        if should_block_review(payload, labels=labels, root=root):
            return True
    return False


def pages_blocked(
    paths=None, *, root: Path | None = None
) -> bool:
    """True when a changed or current Review edition is unsigned."""
    base = Path(root) if root is not None else config.REPO_ROOT
    if paths:
        return automerge_blocked(paths, root=base)
    current = load_payload(base / "data" / "narrative.json")
    return should_block_review(current, root=base)


def _git_changed(base: str, root: Path) -> list[str]:
    cmd = ["git", "diff", "--name-only", f"{base}...HEAD"]
    try:
        out = subprocess.check_output(
            cmd, cwd=root, text=True, stderr=subprocess.DEVNULL
        )
    except (OSError, subprocess.CalledProcessError):
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def _git_changed_from_parent(root: Path) -> list[str]:
    try:
        out = subprocess.check_output(
            ["git", "diff", "--name-only", "HEAD^", "HEAD"],
            cwd=root,
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def _split_labels(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.replace("\n", ",").split(",") if part.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Review edition publish gate")
    parser.add_argument("--automerge", action="store_true")
    parser.add_argument("--pages", action="store_true")
    parser.add_argument("--base", help="git ref to diff against for --automerge")
    parser.add_argument("--labels", default="", help="PR labels, comma or newline")
    parser.add_argument("--root", default="")
    parser.add_argument("paths", nargs="*", help="changed paths (tests / overrides)")
    args = parser.parse_args(argv)
    root = Path(args.root) if args.root else config.REPO_ROOT
    labels = _split_labels(args.labels)
    paths = list(args.paths)
    if args.automerge:
        if not paths:
            paths = _git_changed(args.base or "origin/main", root)
        if automerge_blocked(paths, labels=labels, root=root):
            print(
                "Review edition requires human sign-off "
                f"({QA_PASS_LABEL} label or {SIGNOFF_DIR}/<slug>.json)",
                file=sys.stderr,
            )
            return 1
        return 0
    if args.pages:
        if not paths:
            paths = _git_changed_from_parent(root)
        if pages_blocked(paths or None, root=root):
            print(
                "skip: unsigned Review edition; not deploying to gh-pages",
                file=sys.stderr,
            )
            return 2
        return 0
    parser.error("choose --automerge or --pages")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
