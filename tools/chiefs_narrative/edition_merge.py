"""Refuse edition auto-merge and promote on hold or open review findings.

The daily workflow may merge a ``narrative/update-*`` Preview PR into
develop and promote develop to main only when this module returns
clean. A ``hold`` label, an unresolved review thread, or a
CHANGES_REQUESTED review is a hard refuse. Callers run this from the
trusted base checkout (``python -P -m``), never from PR-head tools.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys


HOLD_LABEL = "hold"
CHANGES_REQUESTED = "CHANGES_REQUESTED"


class MergeLookupError(RuntimeError):
    """GitHub API failed; refuse closed instead of merging."""


def _label_names(labels) -> list[str]:
    names: list[str] = []
    for item in labels or []:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict):
            names.append(str(item.get("name") or ""))
    return names


def labels_include_hold(labels) -> bool:
    """True when the PR carries the automerge ``hold`` label."""
    return any(name.strip().lower() == HOLD_LABEL for name in _label_names(labels))


def review_requests_changes(reviews, review_decision: str | None = None) -> bool:
    """True when GitHub or a submitted review asked for changes."""
    if str(review_decision or "").upper() == CHANGES_REQUESTED:
        return True
    for review in reviews or []:
        if not isinstance(review, dict):
            continue
        if str(review.get("state") or "").upper() == CHANGES_REQUESTED:
            return True
    return False


def open_review_threads(threads) -> bool:
    """True when any review thread is still unresolved."""
    for thread in threads or []:
        if not isinstance(thread, dict):
            continue
        if thread.get("isResolved") is False:
            return True
    return False


def refuse_reason(
    *,
    labels=None,
    reviews=None,
    threads=None,
    review_decision: str | None = None,
) -> str | None:
    """Human-readable refuse, or None when auto-merge/promote may proceed."""
    if labels_include_hold(labels):
        return "hold label"
    if review_requests_changes(reviews, review_decision):
        return "requested-changes review"
    if open_review_threads(threads):
        return "unresolved review thread"
    return None


def merge_refused(
    *,
    labels=None,
    reviews=None,
    threads=None,
    review_decision: str | None = None,
) -> bool:
    return refuse_reason(
        labels=labels,
        reviews=reviews,
        threads=threads,
        review_decision=review_decision,
    ) is not None


def _gh_json(args: list[str]) -> object:
    try:
        raw = subprocess.check_output(["gh", *args], text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise MergeLookupError(f"edition merge lookup failed: {exc}") from exc
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise MergeLookupError("edition merge lookup returned invalid JSON") from exc


def _repo_parts() -> tuple[str, str]:
    repo = os.environ.get("GITHUB_REPOSITORY") or ""
    if "/" not in repo:
        raise MergeLookupError("GITHUB_REPOSITORY is not set")
    owner, name = repo.split("/", 1)
    return owner, name


def fetch_pr(pr_ref: str) -> dict:
    payload = _gh_json(
        [
            "pr",
            "view",
            pr_ref,
            "--json",
            "number,url,labels,reviews,reviewDecision",
        ]
    )
    if not isinstance(payload, dict):
        raise MergeLookupError("gh pr view returned a non-object")
    return payload


def fetch_review_threads(number: int) -> list[dict]:
    owner, name = _repo_parts()
    query = (
        "query($owner:String!, $name:String!, $number:Int!) {"
        "  repository(owner:$owner, name:$name) {"
        "    pullRequest(number:$number) {"
        "      reviewThreads(first:100) { nodes { isResolved } }"
        "    }"
        "  }"
        "}"
    )
    payload = _gh_json(
        [
            "api",
            "graphql",
            "-f",
            f"query={query}",
            "-F",
            f"owner={owner}",
            "-F",
            f"name={name}",
            "-F",
            f"number={int(number)}",
        ]
    )
    if not isinstance(payload, dict):
        raise MergeLookupError("review thread lookup returned a non-object")
    nodes = (
        ((payload.get("data") or {}).get("repository") or {})
        .get("pullRequest")
        or {}
    ).get("reviewThreads") or {}
    rows = nodes.get("nodes") or []
    return [row for row in rows if isinstance(row, dict)]


def inspect_pr(pr_ref: str) -> dict:
    pr = fetch_pr(pr_ref)
    number = pr.get("number")
    threads = fetch_review_threads(int(number)) if number is not None else []
    reason = refuse_reason(
        labels=pr.get("labels"),
        reviews=pr.get("reviews"),
        threads=threads,
        review_decision=pr.get("reviewDecision"),
    )
    return {
        "url": pr.get("url") or pr_ref,
        "number": number,
        "reason": reason,
        "refused": reason is not None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Refuse edition auto-merge/promote on hold or open reviews"
    )
    parser.add_argument("--pr", required=True, help="PR URL or number")
    args = parser.parse_args(argv)
    try:
        result = inspect_pr(args.pr)
    except MergeLookupError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if result["refused"]:
        print(
            f"Refusing edition auto-merge and promote: {result['reason']}",
            file=sys.stderr,
        )
        return 1
    print("edition merge: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
