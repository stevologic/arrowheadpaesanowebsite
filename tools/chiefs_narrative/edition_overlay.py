"""Copy allowlisted edition data from a PR head onto a main checkout.

Used by edition-qa.yml after tools/tests/fixtures are loaded from main.
Never copies or executes PR code. Changed paths come from
``git diff --name-status -z base...head`` (three-dot) so a stale branch
cannot overlay leftover files and so ``__pycache__`` written by
``python3 -m`` cannot fail a data-only PR.

Any path outside the edition allow-list fails the gate so mixed
data+code PRs cannot automerge. SVGs may change only under the current
narrative.json slug, only as ``xo-<concept>.svg`` for a card in that
edition, and must use an XML element/attribute allow-list.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
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
    r"^public/images/narrative/(?P<slug>\d{4}-\d{2}-\d{2}-\d{4})/"
    r"xo-(?P<concept>[A-Za-z0-9_]+)\.svg$"
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
XML_DECL = re.compile(r"^<\?xml(?:\s|\?)", re.IGNORECASE)
DOCTYPE = re.compile(r"<!DOCTYPE\b", re.IGNORECASE)
MAX_SVG_BYTES = 256 * 1024
PROTECTED_DELETES = frozenset({"data/wire.json"})


def _local_name(tag: str) -> str:
    if tag.startswith("{"):
        return tag.rsplit("}", 1)[-1]
    return tag.split(":")[-1]


def _load_narrative(src: Path, dst: Path | None = None) -> dict:
    for root in (src, dst) if dst is not None else (src,):
        path = root / "data" / "narrative.json"
        if not path.is_file() or path.is_symlink():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload:
            return payload
    return {}


def current_slug(src: Path, dst: Path | None = None) -> str:
    return str(_load_narrative(src, dst).get("slug") or "").strip()


def narrative_concepts(src: Path, dst: Path | None = None) -> set[str]:
    payload = _load_narrative(src, dst)
    concepts = set()
    for card in payload.get("xsandos") or []:
        if not isinstance(card, dict):
            continue
        concept = str(card.get("concept") or "").strip()
        if concept:
            concepts.add(concept)
    return concepts


def is_allowed_rel(
    rel: Path, slug: str = "", concepts: set[str] | None = None
) -> bool:
    posix = rel.as_posix()
    if posix in ALLOWED_FILES:
        return True
    if any(posix.startswith(prefix) for prefix in ALLOWED_DIRS):
        return Path(posix).suffix == ".json"
    match = NARRATIVE_SLUG_XO.match(posix)
    if not match:
        return False
    if not slug or match.group("slug") != slug:
        return False
    if concepts is not None and match.group("concept") not in concepts:
        return False
    return True


def _svg_prolog_rejected(text: str) -> bool:
    if DOCTYPE.search(text or ""):
        return True
    for match in re.finditer(r"<\?", text or ""):
        prefix = text[: match.start()]
        if prefix.strip():
            return True
        if not XML_DECL.match(text[match.start() :]):
            return True
    return False


def svg_payload_rejected(content: str, raw: bytes | None = None) -> bool:
    data = raw if raw is not None else (content or "").encode("utf-8")
    if len(data) > MAX_SVG_BYTES:
        return True
    text = content if content is not None else data.decode("utf-8", errors="replace")
    text = (text or "").strip()
    if not text:
        return True
    if _svg_prolog_rejected(text):
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


def parse_name_status(raw: bytes) -> list[tuple[str, str, str]]:
    """Parse ``git diff --name-status -z`` output into (status, path, dest)."""
    parts = [p.decode("utf-8", errors="replace") for p in raw.split(b"\0") if p]
    entries: list[tuple[str, str, str]] = []
    index = 0
    while index < len(parts):
        status = parts[index]
        index += 1
        if not status:
            continue
        code = status[0]
        if code in {"R", "C"}:
            if index + 1 >= len(parts):
                raise ValueError("truncated git name-status rename/copy")
            src_path = parts[index]
            dst_path = parts[index + 1]
            index += 2
            entries.append((status, src_path, dst_path))
            continue
        if index >= len(parts):
            raise ValueError("truncated git name-status entry")
        entries.append((status, parts[index], ""))
        index += 1
    return entries


def git_name_status(repo: Path, base_sha: str, head_sha: str) -> list[tuple[str, str, str]]:
    if not base_sha or not head_sha:
        raise ValueError("git name-status needs base and head SHAs")
    proc = subprocess.run(
        ["git", "diff", "--name-status", "-z", f"{base_sha}...{head_sha}"],
        cwd=repo,
        check=False,
        capture_output=True,
    )
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
        raise ValueError(f"git diff {base_sha}...{head_sha} failed: {err}")
    return parse_name_status(proc.stdout)


def _src_additions(src: Path) -> list[tuple[str, str, str]]:
    """Test helper: treat every regular file under src as an add."""
    entries = []
    for path in src.rglob("*"):
        rel = path.relative_to(src).as_posix()
        if ".git" in path.relative_to(src).parts:
            continue
        if path.is_symlink() or path.is_file():
            entries.append(("A", rel, ""))
    return sorted(entries, key=lambda item: item[1])


def _is_current_slug_svg(rel: str, slug: str) -> bool:
    match = NARRATIVE_SLUG_XO.match(rel)
    return bool(slug and match and match.group("slug") == slug)


def _require_card_svgs(dst: Path, slug: str, concepts: set[str]) -> None:
    if not slug or not concepts:
        return
    missing = []
    for concept in sorted(concepts):
        rel = f"public/images/narrative/{slug}/xo-{concept}.svg"
        path = dst / rel
        if path.is_symlink() or not path.is_file():
            missing.append(rel)
    if missing:
        raise ValueError("missing XO SVG after overlay: " + ", ".join(missing))


def overlay_edition_data(
    src: Path,
    dst: Path,
    *,
    base_sha: str | None = None,
    head_sha: str | None = None,
    repo: Path | None = None,
) -> int:
    slug = current_slug(src, dst)
    concepts = narrative_concepts(src, dst)
    if base_sha or head_sha:
        entries = git_name_status(repo or dst, base_sha or "", head_sha or "")
    else:
        entries = _src_additions(src)
    blocked: list[str] = []
    for status, path, dest in entries:
        code = status[:1]
        if code in {"R", "C"}:
            raise ValueError(
                "PR renames are not allowed: " + " -> ".join(p for p in (path, dest) if p)
            )
        if code == "T":
            raise ValueError(f"PR type changes are not allowed: {path}")
        if code not in {"A", "M", "D"}:
            raise ValueError(f"PR change {status} is not allowed: {path}")
        for rel in (path, dest) if dest else (path,):
            src_path = src / rel
            dst_path = dst / rel
            if src_path.is_symlink() or dst_path.is_symlink():
                raise ValueError(f"refusing symlink: {rel}")
            if not is_allowed_rel(Path(rel), slug, concepts):
                blocked.append(rel)
        if code == "D" and (
            path in PROTECTED_DELETES or _is_current_slug_svg(path, slug)
        ):
            raise ValueError(f"PR deletions are not allowed: {path}")
    if blocked:
        raise ValueError(
            "PR changes paths outside the edition allow-list: " + ", ".join(blocked)
        )
    copied = 0
    for status, path, _dest in entries:
        if status[:1] not in {"A", "M"}:
            continue
        src_path = src / path
        if src_path.is_symlink():
            raise ValueError(f"refusing symlink: {path}")
        if not src_path.is_file():
            raise ValueError(f"PR path missing from head: {path}")
        if NARRATIVE_SLUG_XO.match(path):
            raw = src_path.read_bytes()
            text = raw.decode("utf-8", errors="replace")
            if svg_payload_rejected(text, raw):
                raise ValueError(f"refusing PR SVG with disallowed XML: {path}")
        target = dst / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src_path, target)
        copied += 1
    _require_card_svgs(dst, slug, concepts)
    return copied


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Overlay PR edition data onto main.")
    parser.add_argument("src", type=Path, help="PR head checkout (e.g. .pr-head)")
    parser.add_argument("dst", type=Path, help="main checkout (workspace root)")
    parser.add_argument(
        "--base",
        required=True,
        help="PR base SHA for git diff base...head",
    )
    parser.add_argument(
        "--head",
        required=True,
        help="PR head SHA for git diff base...head",
    )
    args = parser.parse_args(argv)
    src = args.src.resolve()
    dst = args.dst.resolve()
    if not src.is_dir():
        print(f"ERROR: missing PR head at {src}", file=sys.stderr)
        return 1
    try:
        copied = overlay_edition_data(
            src, dst, base_sha=args.base, head_sha=args.head, repo=dst
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    shutil.rmtree(src)
    print(f"overlaid {copied} edition files from the PR head")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
