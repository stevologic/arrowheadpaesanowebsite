"""Queue backpressure for Chiefs Narrative publication PRs.

An open same-day ``narrative/update-*`` full-edition PR to develop/main
still blocks provider work so a pending draft is not reprinted.
Schedule-only (slate) PRs refresh ``data/schedule_2026.json`` only and do
not block a full-mode desk. Automation must not close, merge, or delete
those heads (``maintenance.json`` sets ``close/merge/delete: false`` and
protects ``narrative/update-*``).

A held draft older than 24 hours, or from a previous America/Chicago
calendar day, is treated as stale: the next scheduled desk may generate
again. The older PR stays open for human review.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

_CT = ZoneInfo("America/Chicago")
PUBLICATION_HEAD_PREFIX = "narrative/update-"
PUBLICATION_BASES = frozenset({"develop", "main"})
STALE_AFTER = timedelta(hours=24)
SLATE_TITLE_PREFIX = "Chiefs schedule"
FULL_DESK_MODE = "full"


class QueueLookupError(RuntimeError):
    """GitHub API failed; the workflow must fail closed."""


def _parse_iso(stamp: str | None) -> datetime | None:
    raw = (stamp or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _normalized_desk_mode(desk_mode: str | None) -> str:
    return (desk_mode or "").strip().lower()


def is_slate_only_publication_pr(pr: dict) -> bool:
    """True when the PR is a schedule-only slate refresh, not a full edition.

    Untitled or ambiguous publication PRs are not treated as slate-only so
    the queue stays fail-closed.
    """
    title = str((pr or {}).get("title") or "")
    if title.startswith(SLATE_TITLE_PREFIX):
        return True
    body = str((pr or {}).get("body") or "").lower()
    return "schedule-only refresh" in body or (
        "narrative text was not regenerated" in body
    )


def is_stale_publication_pr(pr: dict, *, now: datetime | None = None) -> bool:
    """True when a bot edition PR must not block the next scheduled run."""
    created = _parse_iso((pr or {}).get("created_at"))
    if created is None:
        return False
    now = now or datetime.now(timezone.utc)
    if now - created >= STALE_AFTER:
        return True
    return created.astimezone(_CT).date() < now.astimezone(_CT).date()


def is_blocking_publication_pr(
    pr: dict, *, now: datetime | None = None, desk_mode: str | None = None
) -> bool:
    """True when this open PR should skip generation."""
    head = str(((pr or {}).get("head") or {}).get("ref") or "")
    base = str(((pr or {}).get("base") or {}).get("ref") or "")
    if not head.startswith(PUBLICATION_HEAD_PREFIX):
        return False
    if base not in PUBLICATION_BASES:
        return False
    if is_stale_publication_pr(pr, now=now):
        return False
    if _normalized_desk_mode(desk_mode) == FULL_DESK_MODE and (
        is_slate_only_publication_pr(pr)
    ):
        return False
    return True


def pending_publication_numbers(
    prs, *, now: datetime | None = None, desk_mode: str | None = None
) -> list[int]:
    """Fresh bot edition PR numbers that block this desk, oldest first."""
    rows = []
    for pr in prs or []:
        if not isinstance(pr, dict):
            continue
        if not is_blocking_publication_pr(pr, now=now, desk_mode=desk_mode):
            continue
        number = pr.get("number")
        if number is None:
            continue
        rows.append(int(number))
    return rows


def _load_open_pulls() -> list[dict]:
    repo = os.environ.get("GITHUB_REPOSITORY") or ""
    if not repo:
        raise QueueLookupError("GITHUB_REPOSITORY is not set")
    try:
        raw = subprocess.check_output(
            [
                "gh",
                "api",
                "--paginate",
                f"repos/{repo}/pulls?state=open&per_page=100",
            ],
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise QueueLookupError(f"publication queue lookup failed: {exc}") from exc
    text = (raw or "").strip()
    if not text:
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise QueueLookupError("publication queue returned invalid JSON") from exc
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    raise QueueLookupError("publication queue returned a non-list payload")


def _write_output(pending: list[int]) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    value = "true" if pending else "false"
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"pending={value}\n")
    if pending:
        shown = " ".join(str(n) for n in pending)
        print(
            f"Publication PR(s) await review: {shown}. "
            "Skipping repeat generation."
        )
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as fh:
                fh.write(
                    f"Publication PR(s) {shown} await workflow "
                    "approval/review; no provider request was made.\n"
                )
    else:
        print("pending=false")


def main(argv=None) -> int:
    del argv
    try:
        pending = pending_publication_numbers(
            _load_open_pulls(), desk_mode=os.environ.get("DESK_MODE")
        )
    except QueueLookupError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    _write_output(pending)
    return 0


if __name__ == "__main__":
    sys.exit(main())
