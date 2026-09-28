"""Copy allowlisted edition data from a PR head onto a main checkout.

Used by edition-qa.yml after tools/tests/fixtures are loaded from main.
Never copies or executes PR code. Any changed path outside the edition
allow-list fails the gate so mixed data+code PRs cannot automerge.
SVGs may change only under the current narrative.json slug, must be
xo-*.svg, and must use an XML element/attribute allow-list.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from xml.etree import ElementTree

ALLOWED_FILES = {
    "data/narrative.json",
    "data/narrative_archive.json",
    "data/narrative_repair.json",
    "data/wire.json",
    "data/schedule_2026.json",
}
ALLOWED_DIRS = ("data/narrative_editions/",)
NARRATIVE_SLUG_XO = re.compile(
    r"^public/images/narrative/(?P<slug>\d{4}-\d{2}-\d{2}-\d{4})/xo-[A-Za-z0-9_.-]+\.svg$"
)
FORBIDDEN_ELEMENTS = frozenset(
    {"script", "foreignobject", "iframe", "object", "embed"}
)
ANIMATE_ELEMENTS = frozenset(
    {"set", "animate", "animatetransform", "animatemotion"}
)
ALLOWED_ELEMENTS = frozenset(
    {
        "svg",
        "g",
        "defs",
        "marker",
        "path",
        "rect",
        "line",
        "text",
        "circle",
        "ellipse",
        "title",
        "desc",
    }
) | ANIMATE_ELEMENTS
ALLOWED_ATTRS = frozenset(
    {
        "xmlns",
        "viewbox",
        "width",
        "height",
        "x",
        "y",
        "x1",
        "y1",
        "x2",
        "y2",
        "cx",
        "cy",
        "r",
        "rx",
        "ry",
        "d",
        "fill",
        "stroke",
        "stroke-width",
        "stroke-dasharray",
        "stroke-linecap",
        "stroke-linejoin",
        "opacity",
        "font-family",
        "font-size",
        "font-weight",
        "text-anchor",
        "id",
        "marker-end",
        "markerwidth",
        "markerheight",
        "refx",
        "refy",
        "orient",
        "role",
        "aria-label",
        "class",
        "paint-order",
        "letter-spacing",
        "attributename",
        "to",
        "from",
        "dur",
        "repeatcount",
        "values",
    }
)
JS_HREF = re.compile(r"^\s*javascript:", re.IGNORECASE)


def _local_name(tag: str) -> str:
    if tag.startswith("{"):
        return tag.rsplit("}", 1)[-1]
    return tag.split(":")[-1]


def current_slug(src: Path, dst: Path | None = None) -> str:
    for root in (src, dst) if dst is not None else (src,):
        path = root / "data" / "narrative.json"
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        slug = str((payload or {}).get("slug") or "").strip()
        if slug:
            return slug
    return ""


def is_allowed_rel(rel: Path, slug: str = "") -> bool:
    posix = rel.as_posix()
    if posix in ALLOWED_FILES:
        return True
    if any(posix.startswith(prefix) for prefix in ALLOWED_DIRS):
        return True
    match = NARRATIVE_SLUG_XO.match(posix)
    if not match:
        return False
    return bool(slug) and match.group("slug") == slug


def svg_payload_rejected(content: str) -> bool:
    text = (content or "").strip()
    if not text:
        return True
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        return True
    for node in root.iter():
        name = _local_name(node.tag).lower()
        if name in FORBIDDEN_ELEMENTS:
            return True
        if name not in ALLOWED_ELEMENTS:
            return True
        for raw_key, value in node.attrib.items():
            attr = _local_name(raw_key).lower()
            if attr.startswith("on"):
                return True
            if attr in {"href", "xlink:href"} and JS_HREF.match(str(value or "")):
                return True
            if (
                name in ANIMATE_ELEMENTS
                and attr == "attributename"
                and str(value or "").lower().startswith("on")
            ):
                return True
            if attr not in ALLOWED_ATTRS and not attr.startswith("xmlns"):
                return True
    return False


def _file_map(root: Path, skip_parts: set[str]) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        if skip_parts.intersection(path.relative_to(root).parts):
            continue
        files[path.relative_to(root).as_posix()] = path
    return files


def changed_rels(src: Path, dst: Path) -> list[str]:
    src_map = _file_map(src, {".git"})
    if not (src / ".git").exists():
        return sorted(src_map)
    dst_map = _file_map(dst, {".git", ".pr-head"})
    changed = []
    for rel in set(src_map) | set(dst_map):
        left = src_map.get(rel)
        right = dst_map.get(rel)
        if left is None or right is None:
            changed.append(rel)
            continue
        if left.read_bytes() != right.read_bytes():
            changed.append(rel)
    return sorted(changed)


def overlay_edition_data(src: Path, dst: Path) -> int:
    slug = current_slug(src, dst)
    changed = changed_rels(src, dst)
    blocked = [rel for rel in changed if not is_allowed_rel(Path(rel), slug)]
    if blocked:
        raise ValueError(
            "PR changes paths outside the edition allow-list: " + ", ".join(blocked)
        )
    copied = 0
    for rel in changed:
        path = src / rel
        if not path.is_file() or path.is_symlink():
            continue
        if NARRATIVE_SLUG_XO.match(rel):
            text = path.read_text(encoding="utf-8", errors="replace")
            if svg_payload_rejected(text):
                raise ValueError(
                    f"refusing PR SVG with disallowed XML: {rel}"
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
