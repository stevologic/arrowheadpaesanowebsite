"""Fail CI when a GitHub Actions workflow is not valid YAML.

Also refuse a write-scoped job (any permission: write, not only
contents: write) that runs unittest, pip, npm, make, repo scripts, or
repo Python beyond an allowlisted module loaded from a trusted tree
(``cd "$GATE_BASE"`` / ``PYTHONPATH="$GATE_BASE"``, or a main/develop
checkout). Mentioning GATE_BASE is not enough: ``python -c`` / heredoc
imports are keyed off the working directory actually in use.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

from tools.chiefs_narrative import config

_ALLOWED_WRITE_MODULES = frozenset(
    {
        "tools.chiefs_narrative.review_gate",
        "tools.chiefs_narrative.edition_merge",
    }
)
# python, python3, python3.11, python -P -B, and $PY / $PYTHON.
# (?<![A-Za-z0-9_]) so $PY matches; \b does not, because $ is not a word char.
_PY = (
    r"(?:(?<![A-Za-z0-9_])(?:python(?:3(?:\.\d+)?)?|"
    r"\$\{?(?:PY|PYTHON|PYTHON3)[A-Za-z0-9_]*\}?))"
)
_PY_FLAGS = rf"{_PY}(?:\s+-\S+)*"
_FORBIDDEN_WRITE = (
    re.compile(r"\bunittest\s+discover\b"),
    re.compile(rf"{_PY_FLAGS}\s+-m\s+unittest\b"),
    re.compile(r"\bpip(?:3(?:\.\d+)?)?\s+install\s+-r\b"),
    re.compile(rf"{_PY_FLAGS}\s+-m\s+tools\.chiefs_narrative\.generate\b"),
    re.compile(rf"{_PY_FLAGS}\s+-m\s+tools\.tests\b"),
    re.compile(rf"{_PY_FLAGS}\s+-m\s+pytest\b|\bpytest\b"),
    re.compile(
        r"\bpip(?:3(?:\.\d+)?)?(?:\s+-\S+)*\s+install\s+"
        r"(?:-\S+\s+)*-(?:e|-editable)\b"
    ),
    re.compile(
        r"\bpip(?:3(?:\.\d+)?)?(?:\s+-\S+)*\s+install\s+"
        r"(?:-\S+\s+)*\.(?:\s|$)"
    ),
    re.compile(r"(?:^|[;\n|&])\s*make\b"),
    re.compile(r"(?:^|[;\n|&])\s*npm\b"),
    re.compile(
        r"(?:^|[;\n|&])\s*(?:source|\.)\s+[\"']?(?:\./)?(?:tools/|[\w./-]+\.(?:sh|bash|env))"
    ),
    re.compile(
        r"\b(?:sh|bash|dash|zsh)\s+[\"']?(?:\./|\$\{?GITHUB_WORKSPACE\}?/)?"
        r"(?:tools/[\w./-]+|[\w./-]+\.sh|script)\b"
    ),
)
_REPO_PY_MODULE = re.compile(rf"{_PY_FLAGS}\s+-m\s+([A-Za-z0-9_.]+)")
_PY_SCRIPT = re.compile(
    rf"{_PY_FLAGS}\s+[\"']?((?:\.?\.?/)?[\w./-]+\.py)\b"
)
_PY_C = re.compile(rf"{_PY_FLAGS}\s+-c\b")
_PY_HEREDOC = re.compile(rf"{_PY}(?:\s+-\S+)*\s+-?\s*<<")
_RUNPY = re.compile(r"\brunpy\b")
_IMPORT_DUNDER = re.compile(r"__import__\s*\(")
_TOOLS_IMPORT = re.compile(
    r"from tools(?:\.chiefs_narrative)? import|import tools\."
)
_WORKSPACE_PYTHONPATH = re.compile(
    r"(?:export\s+)?PYTHONPATH\s*=\s*[\"']?\$\{?GITHUB_WORKSPACE\}?"
)
_GATE_PYTHONPATH = re.compile(
    r"(?:export\s+)?PYTHONPATH\s*=\s*[\"']?\$\{?GATE_BASE\}?"
)
_CD = re.compile(r"(?:^|[;\n|&])\s*cd\s+([^\n|&;]+)")
_TRUSTED_REFS = frozenset(
    {"main", "develop", "refs/heads/main", "refs/heads/develop"}
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


def _permissions_write(perms) -> bool:
    if perms == "write-all":
        return True
    if isinstance(perms, dict):
        return any(value == "write" for value in perms.values())
    return False


def _job_is_write_scoped(workflow: dict, job: dict) -> bool:
    if "permissions" in job:
        return _permissions_write(job.get("permissions"))
    return _permissions_write(workflow.get("permissions"))


def _workflow_on(workflow: dict):
    return workflow.get("on", workflow.get(True))


def _has_pr_trigger(on) -> bool:
    if isinstance(on, dict):
        return "pull_request" in on or "pull_request_target" in on
    if isinstance(on, list):
        return any(item in {"pull_request", "pull_request_target"} for item in on)
    return on in {"pull_request", "pull_request_target"}


def _push_branches(on) -> set[str]:
    if not isinstance(on, dict):
        return set()
    push = on.get("push")
    if push is None:
        return set()
    if push is True:
        return set()
    if isinstance(push, dict):
        raw = push.get("branches") or []
        return {str(item) for item in raw}
    return set()


def _checkout_refs(job: dict) -> list[str]:
    refs: list[str] = []
    for step in job.get("steps") or []:
        if not isinstance(step, dict):
            continue
        if "actions/checkout" not in str(step.get("uses") or ""):
            continue
        refs.append(str((step.get("with") or {}).get("ref") or "").strip())
    return refs


def _job_checkout_trusted(workflow: dict, job: dict) -> bool:
    """True when GITHUB_WORKSPACE is main/develop, not a PR head."""
    refs = _checkout_refs(job)
    if any("pull_request.head" in item for item in refs):
        return False
    if any(item in _TRUSTED_REFS for item in refs):
        return True
    on = _workflow_on(workflow)
    if _has_pr_trigger(on):
        return False
    branches = _push_branches(on)
    if branches and branches <= {"main", "develop"} and all(not item for item in refs):
        return True
    return False


def _without_comments(script: str) -> str:
    """Drop unquoted # comments so docs like ``python -m would`` do not match."""
    cleaned: list[str] = []
    for line in script.splitlines():
        kept: list[str] = []
        in_single = in_double = False
        for char in line:
            if char == "'" and not in_double:
                in_single = not in_single
            elif char == '"' and not in_single:
                in_double = not in_double
            elif char == "#" and not in_single and not in_double:
                break
            kept.append(char)
        cleaned.append("".join(kept))
    return "\n".join(cleaned)


def _job_scripts(job: dict) -> list[str]:
    scripts: list[str] = []
    for step in job.get("steps") or []:
        if not isinstance(step, dict):
            continue
        run = step.get("run")
        if isinstance(run, str):
            scripts.append(_without_comments(run))
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


def _last_cd(script: str) -> str:
    targets = [match.group(1).strip().strip("\"'") for match in _CD.finditer(script)]
    return targets[-1] if targets else ""


def _tree_trusted(script: str, job_trusted: bool) -> bool:
    """Trusted only when cwd/PYTHONPATH is GATE_BASE or the checkout is."""
    cd = _last_cd(script)
    if "GITHUB_WORKSPACE" in cd:
        return False
    if _WORKSPACE_PYTHONPATH.search(script):
        return False
    if "GATE_BASE" in cd:
        return True
    if _GATE_PYTHONPATH.search(script) and "GITHUB_WORKSPACE" not in cd:
        return True
    return job_trusted


def _imports_repo_code(script: str) -> bool:
    return bool(
        _TOOLS_IMPORT.search(script)
        or _IMPORT_DUNDER.search(script)
        or _RUNPY.search(script)
    )


def _uses_inline_python(script: str) -> bool:
    return bool(_PY_C.search(script) or _PY_HEREDOC.search(script))


def audit_write_jobs(root: Path | None = None) -> list[str]:
    """Return human-readable failures for write jobs that execute repo code."""
    errors: list[str] = []
    for path, doc in load_workflows(root).items():
        if not isinstance(doc, dict):
            continue
        jobs = doc.get("jobs") if isinstance(doc.get("jobs"), dict) else {}
        for job_name, job in jobs.items():
            if not isinstance(job, dict) or not _job_is_write_scoped(doc, job):
                continue
            prefix = f"{path.name}:{job_name}"
            for checkout in _checkout_persist_false(job):
                errors.append(
                    f"{prefix}: write-token checkout "
                    f"{checkout!r} missing persist-credentials: false"
                )
            job_trusted = _job_checkout_trusted(doc, job)
            for script in _job_scripts(job):
                trusted = _tree_trusted(script, job_trusted)
                for rx in _FORBIDDEN_WRITE:
                    if rx.search(script):
                        errors.append(
                            f"{prefix}: write-token job runs {rx.pattern}"
                        )
                if _WORKSPACE_PYTHONPATH.search(script):
                    errors.append(
                        f"{prefix}: write-token job sets PYTHONPATH to "
                        "GITHUB_WORKSPACE (PR head)"
                    )
                if "GITHUB_WORKSPACE" in _last_cd(script):
                    errors.append(
                        f"{prefix}: write-token job cds to GITHUB_WORKSPACE "
                        "before running"
                    )
                if _uses_inline_python(script) and _imports_repo_code(script):
                    if not trusted:
                        errors.append(
                            f"{prefix}: write-token job runs python -c/heredoc "
                            "repo import without a trusted working dir"
                        )
                for match in _REPO_PY_MODULE.finditer(script):
                    module = match.group(1)
                    token = match.group(0)
                    if module in {"unittest", "pytest"}:
                        continue
                    if module == "runpy":
                        errors.append(
                            f"{prefix}: write-token job runs {token}"
                        )
                        continue
                    if module in _ALLOWED_WRITE_MODULES and trusted:
                        continue
                    errors.append(f"{prefix}: write-token job runs {token}")
                for match in _PY_SCRIPT.finditer(script):
                    errors.append(
                        f"{prefix}: write-token job runs repo script "
                        f"{match.group(1)}"
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
