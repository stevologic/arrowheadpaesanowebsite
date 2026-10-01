"""Fail CI when a GitHub Actions workflow is not valid YAML."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from tools.chiefs_narrative import config


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


def main(argv: list[str] | None = None) -> int:
    del argv
    try:
        loaded = load_workflows()
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    for path in loaded:
        print(f"ok {path.as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
