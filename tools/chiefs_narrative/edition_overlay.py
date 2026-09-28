"""Copy allowlisted edition data from a PR head onto a main checkout.

Used by edition-qa.yml after tools/tests/fixtures are loaded from main.
Never copies or executes PR tools. SVGs under public/images/narrative/
must be <slug>/xo-*.svg and must not carry script or on*= handlers.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

ALLOWED_FILES = {
    "data/narrative.json",
    "data/narrative_archive.json",
    "data/narrative_repair.json",
    "data/wire.json",
    "data/schedule_2026.json",
}
ALLOWED_DIRS = ("data/narrative_editions/",)
NARRATIVE_SLUG_XO = re.compile(
    r"^public/images/narrative/\d{4}-\d{2}-\d{2}-\d{4}/xo-[A-Za-z0-9_.-]+\.svg$"
)
UNSAFE_SVG = re.compile(r"<\s*script\b|on[A-Za-z]+\s*=", re.IGNORECASE)


def is_allowed_rel(rel: Path) -> bool:
    posix = rel.as_posix()
    if posix in ALLOWED_FILES:
        return True
    if any(posix.startswith(prefix) for prefix in ALLOWED_DIRS):
        return True
    return bool(NARRATIVE_SLUG_XO.match(posix))


def svg_payload_rejected(content: str) -> bool:
    return UNSAFE_SVG.search(content) is not None


def overlay_edition_data(src: Path, dst: Path) -> int:
    copied = 0
    for path in src.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        rel = path.relative_to(src)
        if not is_allowed_rel(rel):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if NARRATIVE_SLUG_XO.match(rel.as_posix()) and svg_payload_rejected(text):
            raise ValueError(
                f"refusing PR SVG with script/on* handler: {rel.as_posix()}"
            )
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        copied += 1
    return copied


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Overlay PR edition data onto main.")
    parser.add_argument("src", type=Path, help="PR head checkout (e.g. .pr-head)")
    parser.add_argument("dst", type=Path, help="main checkout (workspace root)")
    args = parser.parse_args(argv)
    src = args.src.resolve()
    dst = args.dst.resolve()
    if not src.is_dir():
        print(f"ERROR: missing PR head at {src}", file=sys.stderr)
        return 1
    try:
        copied = overlay_edition_data(src, dst)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    shutil.rmtree(src)
    print(f"overlaid {copied} edition files from the PR head")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
