"""Fail CI when a GitHub Actions workflow is not valid YAML.

Also refuse a write-token job that runs unittest, ``pip install -r``, or
repo Python beyond the allowlisted Review gate (main-branch
``python -P -m tools.chiefs_narrative.review_gate``).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

from tools.chiefs_narrative import config

_FORBIDDEN_WRITE = (
    re.compile(r"\bunittest\s+discover\b"),
    re.compile(r"\bpython(?:3)?\s+-m\s+unittest\b"),
    re.compile(r"\bpip(?:3)?\s+install\s+-r\b"),
    re.compile(r"\bpython(?:3)?\s+-m\s+tools\.chiefs_narrative\.generate\b"),
    re.compile(r"\bpython(?:3)?\s+-m\s+tools\.tests\b"),
)
_ALLOWED_WRITE_PY = re.compile(
    r"python(?:3)?\s+-P\s+-m\s+tools\.chiefs_narrative\.review_gate"
    r"|python(?:3)?\s+-m\s+tools\.chiefs_narrative\.review_gate"
    r"|python(?:3)?\s+-c\s+\"import json;"
)
_TOOLS_IMPORT = re.compile(
    r"from tools(?:\.chiefs_narrative)? import|import tools\."
)


def workflow_paths(root: Path | None = None) -> list[Path]:
    base = (root or config.REPO_ROOT) / ".github" / "workflows"
    return sorted(p for p in base.iterdir() if p.suffix in {".yml", ".yaml"})


def load_workflows(root: Path | None = None) -> dict[Path, object]:
    loaded: dict[Path, object] = {}
    errors: list[str] = []
    for path in workflow_paths(root):
        try:
            loaded[path] = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            errors.append(f"{path}: {exc}")
    if errors:
        raise ValueError("workflow YAML failed to parse:\n" + "\n".join(errors))
    return loaded


def _job_contents_write(workflow: dict, job: dict) -> bool:
    job_perms = job.get("permissions")
    if job_perms == "write-all":
        return True
    if isinstance(job_perms, dict):
        return job_perms.get("contents") == "write"
    wf = workflow.get("permissions")
    if wf == "write-all":
        return True
    if isinstance(wf, dict):
        return wf.get("contents") == "write"
    return False


def _job_scripts(job: dict) -> list[str]:
    scripts: list[str] = []
    for step in job.get("steps") or []:
        if not isinstance(step, dict):
            continue
        run = step.get("run")
        if isinstance(run, str):
            scripts.append(run)
    return scripts


def _checkout_persist_false(job: dict) -> list[str]:
    missing: list[str] = []
    for step in job.get("steps") or []:
        if not isinstance(step, dict):
            continue
        uses = str(step.get("uses") or "")
        if "actions/checkout" not in uses:
            continue
        persist = (step.get("with") or {}).get("persist-credentials")
        if persist is not False:
            missing.append(str(step.get("name") or uses))
    return missing


def audit_write_jobs(root: Path | None = None) -> list[str]:
    """Return human-readable failures for write jobs that execute repo code."""
    errors: list[str] = []
    for path, doc in load_workflows(root).items():
        if not isinstance(doc, dict):
            continue
        jobs = doc.get("jobs") if isinstance(doc.get("jobs"), dict) else {}
        for job_name, job in jobs.items():
            if not isinstance(job, dict) or not _job_contents_write(doc, job):
                continue
            for checkout in _checkout_persist_false(job):
                errors.append(
                    f"{path.name}:{job_name}: write-token checkout "
                    f"{checkout!r} missing persist-credentials: false"
                )
            blob = "\n".join(_job_scripts(job))
            for rx in _FORBIDDEN_WRITE:
                if rx.search(blob):
                    errors.append(
                        f"{path.name}:{job_name}: write-token job runs {rx.pattern}"
                    )
            if _TOOLS_IMPORT.search(blob) and "GATE_BASE" not in blob:
                errors.append(
                    f"{path.name}:{job_name}: write-token job imports repo tools "
                    "without GATE_BASE"
                )
            if "python -m tools." in blob or "python3 -m tools." in blob:
                if not _ALLOWED_WRITE_PY.search(blob):
                    errors.append(
                        f"{path.name}:{job_name}: write-token job runs repo "
                        "python beyond review_gate"
                    )
                leftover = blob
                for match in re.finditer(
                    r"python(?:3)?\s+-m\s+tools\.[A-Za-z0-9_.]+", leftover
                ):
                    token = match.group(0)
                    if "review_gate" not in token:
                        errors.append(
                            f"{path.name}:{job_name}: write-token job runs {token}"
                        )
    return errors


def main(argv: list[str] | None = None) -> int:
    del argv
    try:
        loaded = load_workflows()
        write_errors = audit_write_jobs()
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    if write_errors:
        print("write-token job audit failed:", file=sys.stderr)
        print("\n".join(write_errors), file=sys.stderr)
        return 1
    for path in loaded:
        print(f"ok {path.as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
