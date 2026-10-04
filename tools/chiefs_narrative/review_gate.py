"""Refuse unsigned Review editions on every path to main and gh-pages.

A Review-phase edition (phase.mode in review/recap/postgame, a Review
label with any separator, a completed last-game final, or a final
score only in body prose when the edition is not an explicit Preview)
cannot automerge or deploy unless Karen signed it with the committed
ed25519 public key. Sign-off is ``signoff/review/<slug>.json`` plus
``<slug>.sig`` — never under ``data/``, never a label, never a
substring. Easy to disable later via ``REVIEW_SIGNOFF_REQUIRED``.

Automerge must run this module from the *base* checkout, never the PR
head, so a PR cannot rewrite the gate and then pass it. Workflows do
that with ``cd "$GATE_BASE" && python -P -m … --root <PR-tree>``: ``-P``
keeps the PR working directory off ``sys.path``, and ``--root`` is only
a path argument.

Automerge is an allowlist, not a growing blocklist. Only generated
Preview data may automerge: ``data/narrative.json`` and Preview
``data/narrative_editions/*.json``. Everything else — tests,
requirements, scripts, ``public/``, layouts, workflows, the gate
itself, README — needs a human merge. A Review edition, even a
signed one, still needs a human merge.

After Hugo writes ``dist/``, ``--pages --dist`` fails closed unless
every file under the publish dir is a known asset or a page Hugo
generated from a Preview, signed Review, or legacy-pinned edition.
staticDir copies that are not assets fail closed. Dirs and mounts
come from the resolved ``hugo config``, never hardcoded paths.

Any non-``.json`` file under ``data/`` (``.yaml``, ``.yml``, ``.toml``,
``.JSON``, ``.sig``) and any Hugo config besides ``hugo.yaml`` fail
closed on both automerge and pages: Hugo would otherwise prefer them
over the scanned JSON.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from tools.chiefs_narrative import config

REVIEW_SIGNOFF_REQUIRED = True
SIGNOFF_DIR = "signoff/review"
PUBLIC_KEY_REL = "scripts/review_signoff_pubkey.pem"
LEGACY_MANIFEST_REL = "scripts/review_legacy_editions.txt"
LEGACY_CUTOFF = (2026, 10, 1)
_LEGACY_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
SIGNOFF_SLUG_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{4}$")
EDITION_NAMES = frozenset(
    {
        "data/narrative.json",
        "data/narrative_archive.json",
    }
)
_REVIEW_MODES = frozenset({"review", "recap", "postgame"})
_PREVIEW_MODES = frozenset({"preview", "camp", "offseason"})
# Label Review with ·, en-dash, em-dash, hyphen, &middot;, or no mark.
_REVIEW_EDITION = re.compile(
    r"Week\s+\d+\s*"
    r"(?:[·\u00b7\u2013\u2014\-]|&middot;|&#183;)?\s*"
    r"(?:Week\s+\d+\s+)?"
    r"Review",
    re.IGNORECASE,
)
_ALLOWED_HUGO_CONFIG = "hugo.yaml"
_BLOCKED_HUGO_CONFIGS = frozenset(
    {
        "hugo.toml",
        "hugo.json",
        "hugo.yml",
        "config.toml",
        "config.yaml",
        "config.yml",
        "config.json",
    }
)
# Automerge allowlist: generated Preview data only. Test files,
# dependency pins, scripts, and workflows are never listed.
AUTOMERGE_ALLOWED_PATHS = frozenset(
    {
        "data/narrative.json",
    }
)
AUTOMERGE_ALLOWED_PREFIXES = ()
_ASSET_SUFFIXES = frozenset(
    {
        ".svg",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".gif",
        ".ico",
        ".css",
        ".js",
        ".woff",
        ".woff2",
        ".map",
        ".txt",
    }
)
_ASSET_NAMES = frozenset(
    {".nojekyll", "CNAME", "robots.txt", "favicon.svg", "favicon.ico"}
)
_TEXT_SUFFIXES = frozenset({".html", ".htm", ".xml", ".xhtml"})
_DOC_SUFFIXES = frozenset({".md", ".markdown", ".rst"})
_SLUG_HREF = re.compile(r"/narrative/(\d{4}-\d{2}-\d{2}-\d{4})/")
_PROSE_KEYS = (
    "headline",
    "dek",
    "lede",
    "theEdge",
    "analysis",
    "body",
    "whatWorked",
    "whatDidnt",
    "lookAhead",
    "lastGameReview",
)
_BODY_SCORE = re.compile(r"\b(\d{1,2})\s*[–-]\s*(\d{1,2})\b")
_REVIEW_HTML = re.compile(
    r"PUBLICREVIEW|Week\s+\d+.{0,40}Review|"
    r"\b(?:final(?:\s+score)?)\b.{0,24}\d{1,2}\s*[–-]\s*\d{1,2}",
    re.IGNORECASE | re.DOTALL,
)
_HUGO_DIR_KEYS = ("staticdir", "contentdir", "datadir", "layoutdir")


class GateError(RuntimeError):
    """Fail closed: a read or git diff failed, so the gate must block."""


def _posix_rel(path: str | Path) -> str:
    text = str(path).replace("\\", "/")
    if text.startswith("./"):
        text = text[2:]
    return text


def edition_path(path: str | Path) -> bool:
    """True for known edition JSON, including a case-folded editions dir."""
    text = _posix_rel(path)
    lower = text.lower()
    if lower in {name.lower() for name in EDITION_NAMES}:
        return text.endswith(".json")
    parts = text.split("/")
    if (
        len(parts) >= 3
        and parts[0].lower() == "data"
        and parts[1].lower() == "narrative_editions"
    ):
        return text.endswith(".json")
    return False


def extra_publish_path(path: str | Path) -> bool:
    """True for a Hugo-readable path the JSON edition scan would miss."""
    text = _posix_rel(path)
    name = text.rsplit("/", 1)[-1]
    lower_name = name.lower()
    if text == _ALLOWED_HUGO_CONFIG:
        return False
    if lower_name in {item.lower() for item in _BLOCKED_HUGO_CONFIGS}:
        return True
    if text == name and lower_name.startswith("hugo.") and lower_name != "hugo.yaml":
        return True
    parts = text.split("/")
    if parts and parts[0].lower() == "config":
        return True
    if parts and parts[0].lower() == "data":
        return not text.endswith(".json")
    return False


def _hugo_bin() -> str | None:
    found = shutil.which("hugo")
    if found:
        return found
    fallback = Path.home() / ".local" / "hugo" / "hugo"
    if fallback.is_file():
        return str(fallback)
    return None


def load_hugo_config(root: Path) -> dict:
    """Resolved Hugo dirs and mounts. Fail closed if hugo or a key is missing."""
    binary = _hugo_bin()
    if not binary:
        raise GateError("hugo is required to resolve staticDir/contentDir/mounts")
    try:
        raw = subprocess.check_output(
            [binary, "config", "--format", "json"],
            cwd=root,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise GateError(f"hugo config failed: {exc}") from exc
    try:
        cfg = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GateError(f"hugo config is not JSON: {exc}") from exc
    if not isinstance(cfg, dict):
        raise GateError("hugo config is not an object")
    missing = [key for key in _HUGO_DIR_KEYS if key not in cfg]
    if missing:
        raise GateError(f"hugo config missing {', '.join(missing)}")
    mounts = (cfg.get("module") or {}).get("mounts")
    if not isinstance(mounts, list):
        raise GateError("hugo config missing module.mounts")
    return cfg


def _as_rel_dirs(value) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        text = value.replace("\\", "/").strip().strip("/")
        return [text] if text else []
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            out.extend(_as_rel_dirs(item))
        return out
    return []


def resolve_hugo_surface(root: Path) -> dict[str, list[str] | str]:
    """static/content/data/layout dirs plus publishDir from `hugo config`."""
    cfg = load_hugo_config(root)
    static_dirs = _as_rel_dirs(cfg.get("staticdir"))
    content_dirs = _as_rel_dirs(cfg.get("contentdir"))
    data_dirs = _as_rel_dirs(cfg.get("datadir"))
    layout_dirs = _as_rel_dirs(cfg.get("layoutdir"))
    publish = str(cfg.get("publishdir") or "dist").replace("\\", "/").strip("/")
    for mount in (cfg.get("module") or {}).get("mounts") or []:
        if not isinstance(mount, dict):
            continue
        source = str(mount.get("source") or "").replace("\\", "/").strip()
        target = str(mount.get("target") or "").replace("\\", "/").strip()
        if not source or source.startswith("/") or ".." in source.split("/"):
            continue
        top = target.split("/", 1)[0]
        if top == "static":
            static_dirs.append(source)
        elif top == "content":
            content_dirs.append(source)
        elif top == "data":
            data_dirs.append(source)
        elif top == "layouts":
            layout_dirs.append(source)

    def _unique(items: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in items:
            if item and item not in seen:
                seen.add(item)
                out.append(item)
        return out

    return {
        "static": _unique(static_dirs),
        "content": _unique(content_dirs),
        "data": _unique(data_dirs),
        "layouts": _unique(layout_dirs),
        "publish": publish or "dist",
    }


def extra_publish_surface(root: Path) -> bool:
    """True when the tree has a non-JSON data file or extra Hugo config."""
    base = Path(root)
    if not base.is_dir():
        return False
    for name in _BLOCKED_HUGO_CONFIGS:
        if (base / name).is_file():
            return True
    for child in base.iterdir() if base.is_dir() else []:
        if not child.is_file():
            continue
        lower = child.name.lower()
        if lower.startswith("hugo.") and lower != "hugo.yaml":
            return True
    config_dir = base / "config"
    if config_dir.is_dir():
        for path in config_dir.rglob("*"):
            if path.is_file():
                return True
    data = base / "data"
    if not data.is_dir():
        return False
    for path in data.rglob("*"):
        if path.is_file() and not path.name.endswith(".json"):
            return True
    return False


def public_key_path(root: Path | None = None) -> Path:
    """Public key bundled with this module (the checkout that is running).

    Automerge points PYTHONPATH at the base tree so a PR cannot swap the
    key. Tests may set REVIEW_SIGNOFF_PUBKEY to a throwaway public key.
    """
    del root
    override = (os.environ.get("REVIEW_SIGNOFF_PUBKEY") or "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / PUBLIC_KEY_REL


def load_payload(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise GateError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise GateError(f"cannot parse {path}: {exc}") from exc
    return data if isinstance(data, dict) else None


def _phase_mode_and_edition(payload: dict) -> tuple[str, str, dict | None]:
    """mode, edition label, phase.lastGame. phase may be a dict or a string."""
    raw = payload.get("phase")
    edition = str(payload.get("edition") or "")
    if isinstance(raw, dict):
        mode = str(raw.get("mode") or "").strip().lower()
        edition = edition or str(raw.get("edition") or "")
        last = raw.get("lastGame") if isinstance(raw.get("lastGame"), dict) else None
        return mode, edition, last
    if isinstance(raw, str):
        token = raw.strip()
        return token.lower(), edition or token, None
    return "", edition, None


def _completed_last_game(block: dict | None) -> bool:
    """True when lastGame carries a completed final score."""
    if not isinstance(block, dict):
        return False
    completed = block.get("completed")
    if completed not in (True, "true", "True", 1):
        status = str(block.get("status") or "").strip().lower()
        if status not in {"final", "post", "status_final"}:
            return False
    kc = block.get("kcScore")
    opp = block.get("oppScore")
    try:
        int(kc)
        int(opp)
    except (TypeError, ValueError):
        return False
    return True


def _walk_prose(value, parts: list[str]) -> None:
    if isinstance(value, str):
        text = value.strip()
        if text:
            parts.append(text)
        return
    if isinstance(value, list):
        for item in value:
            _walk_prose(item, parts)
        return
    if isinstance(value, dict):
        for item in value.values():
            _walk_prose(item, parts)


def _payload_prose(payload: dict) -> str:
    parts: list[str] = []
    for key in _PROSE_KEYS:
        _walk_prose(payload.get(key), parts)
    return "\n".join(parts)


def _looks_like_final_score(left: str, right: str) -> bool:
    try:
        first = int(left)
        second = int(right)
    except (TypeError, ValueError):
        return False
    if first > 70 or second > 70:
        return False
    if first == 0 and second == 0:
        return False
    if max(first, second) <= 4:
        return False
    return True


def _body_has_final_score(payload: dict) -> bool:
    """Cheap prose-score check when phase/lastGame/label are all non-Review."""
    blob = _payload_prose(payload)
    if not blob:
        return False
    for match in _BODY_SCORE.finditer(blob):
        if _looks_like_final_score(match.group(1), match.group(2)):
            return True
    return False


def payload_is_review(payload: dict | None) -> bool:
    """True for review/recap/postgame, a Review label, last-game, or body score.

    Uses phase.mode (dict or string) and lastGame scores — not a single
    middot label. An explicit Preview/camp/offseason mode is not a Review
    just because lastGameReview still holds last week's final. A missing
    phase + missing lastGame still counts when body prose has a final.
    """
    if not payload:
        return False
    mode, edition, phase_last = _phase_mode_and_edition(payload)
    if mode in _REVIEW_MODES:
        return True
    if _REVIEW_EDITION.search(edition):
        return True
    if mode in _PREVIEW_MODES:
        return False
    last = payload.get("lastGame")
    if not isinstance(last, dict):
        last = phase_last
    if _completed_last_game(last if isinstance(last, dict) else None):
        return True
    return _body_has_final_score(payload)


def signoff_canonical_bytes(slug: str, sha: str) -> bytes:
    return (
        '{"slug":"' + slug + '","verdict":"PASS","sha":"' + sha + '"}'
    ).encode("ascii")


def _safe_signoff_slug(slug: str) -> bool:
    if not slug or not SIGNOFF_SLUG_RE.fullmatch(slug):
        return False
    if "/" in slug or "\\" in slug or ".." in slug:
        return False
    return True


def _verify_ed25519(message: bytes, signature: bytes, pubkey_path: Path) -> bool:
    if not pubkey_path.is_file() or len(signature) != 64:
        return False
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            msg_f = tmp_path / "msg"
            sig_f = tmp_path / "sig"
            msg_f.write_bytes(message)
            sig_f.write_bytes(signature)
            subprocess.check_output(
                [
                    "openssl",
                    "pkeyutl",
                    "-verify",
                    "-inkey",
                    str(pubkey_path),
                    "-pubin",
                    "-rawin",
                    "-in",
                    str(msg_f),
                    "-sigfile",
                    str(sig_f),
                ],
                stderr=subprocess.STDOUT,
            )
    except (OSError, subprocess.CalledProcessError):
        return False
    return True


def verify_signoff(
    slug: str, edition_bytes: bytes, *, root: Path | None = None
) -> bool:
    """True only for Karen's compact JSON + matching ed25519 signature."""
    if not _safe_signoff_slug(slug):
        return False
    base = Path(root) if root is not None else config.REPO_ROOT
    json_path = base / SIGNOFF_DIR / f"{slug}.json"
    sig_path = base / SIGNOFF_DIR / f"{slug}.sig"
    if not json_path.is_file() or not sig_path.is_file():
        return False
    try:
        raw = json_path.read_bytes()
        sig_text = sig_path.read_text(encoding="ascii")
    except OSError:
        return False
    sha = hashlib.sha256(edition_bytes).hexdigest()
    expected = signoff_canonical_bytes(slug, sha)
    if raw != expected:
        return False
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return False
    if not isinstance(data, dict):
        return False
    if data.get("verdict") != "PASS":
        return False
    if data.get("slug") != slug:
        return False
    if data.get("sha") != sha:
        return False
    try:
        signature = base64.b64decode(sig_text.strip(), validate=True)
    except (ValueError, binascii.Error):
        return False
    return _verify_ed25519(
        message=raw, signature=signature, pubkey_path=public_key_path(base)
    )


def _edition_file_for(payload: dict | None, root: Path) -> Path | None:
    slug = str((payload or {}).get("slug") or "").strip()
    live = root / "data" / "narrative.json"
    if live.is_file():
        loaded = load_payload(live)
        if loaded and str(loaded.get("slug") or "").strip() == slug:
            return live
    if _safe_signoff_slug(slug):
        edition = root / "data" / "narrative_editions" / f"{slug}.json"
        if edition.is_file():
            return edition
    if live.is_file():
        return live
    return None


def _legacy_date_ok(slug: str) -> bool:
    """Refuse any pin dated 2026-10-01 or later, even if listed."""
    if not _safe_signoff_slug(slug):
        return False
    try:
        when = (int(slug[0:4]), int(slug[5:7]), int(slug[8:10]))
    except ValueError:
        return False
    return when < LEGACY_CUTOFF


def _legacy_slug_from_path(rel: str) -> str:
    name = Path(_posix_rel(rel)).stem
    return name


def _legacy_sha_matches(actual: str, pinned: str) -> bool:
    """Full 64-char lowercase equality. Prefix compares must not pass."""
    if not _LEGACY_SHA_RE.fullmatch(actual):
        return False
    if not _LEGACY_SHA_RE.fullmatch(pinned):
        return False
    return actual == pinned


def load_legacy_pins(root: Path | None = None) -> dict[str, str]:
    """repo-relative edition path → pinned sha256.

    Missing or empty file: no grandfathering. Never writes this file.
    New entries are a human-merge change. Malformed lines fail closed.
    """
    base = Path(root) if root is not None else config.REPO_ROOT
    path = base / LEGACY_MANIFEST_REL
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="ascii")
    except OSError as exc:
        raise GateError(f"cannot read {path}: {exc}") from exc
    pins: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 2:
            raise GateError(f"malformed legacy pin: {line!r}")
        rel, sha = parts
        rel = _posix_rel(rel)
        if not rel.startswith("data/") or not rel.endswith(".json"):
            raise GateError(f"legacy pin path not allowed: {rel!r}")
        slug = _legacy_slug_from_path(rel)
        if not _legacy_date_ok(slug):
            raise GateError(f"legacy pin slug not allowed: {slug!r}")
        if not _LEGACY_SHA_RE.fullmatch(sha):
            raise GateError(f"legacy pin sha is not 64 lowercase hex: {rel}")
        pins[rel] = sha
    return pins


def review_legacy_pinned(
    payload: dict | None,
    *,
    root: Path | None = None,
    edition_file: Path | None = None,
) -> bool:
    """True only for the pinned path whose bytes still match the full sha."""
    base = Path(root) if root is not None else config.REPO_ROOT
    path = (
        Path(edition_file)
        if edition_file is not None
        else _edition_file_for(payload, base)
    )
    if path is None or not path.is_file():
        return False
    try:
        rel = path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        rel = _posix_rel(path)
    slug = _legacy_slug_from_path(rel)
    if not _legacy_date_ok(slug):
        return False
    pins = load_legacy_pins(base)
    pinned = pins.get(rel)
    if not pinned:
        return False
    try:
        edition_bytes = path.read_bytes()
    except OSError as exc:
        raise GateError(f"cannot read edition {path}: {exc}") from exc
    return _legacy_sha_matches(
        hashlib.sha256(edition_bytes).hexdigest(), pinned
    )


def review_signed_off(
    payload: dict | None,
    *,
    root: Path | None = None,
    edition_file: Path | None = None,
) -> bool:
    slug = str((payload or {}).get("slug") or "").strip()
    if not _safe_signoff_slug(slug):
        return False
    base = Path(root) if root is not None else config.REPO_ROOT
    path = Path(edition_file) if edition_file is not None else _edition_file_for(payload, base)
    if path is None or not path.is_file():
        return False
    try:
        edition_bytes = path.read_bytes()
    except OSError as exc:
        raise GateError(f"cannot read edition {path}: {exc}") from exc
    return verify_signoff(slug, edition_bytes, root=base)


def should_block_review(
    payload: dict | None,
    *,
    root: Path | None = None,
    edition_file: Path | None = None,
) -> bool:
    if not REVIEW_SIGNOFF_REQUIRED:
        return False
    if not payload_is_review(payload):
        return False
    if review_signed_off(payload, root=root, edition_file=edition_file):
        return False
    return not review_legacy_pinned(
        payload, root=root, edition_file=edition_file
    )


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


def _editions_json_path(rel: str) -> bool:
    text = _posix_rel(rel)
    parts = text.split("/")
    return (
        len(parts) >= 3
        and parts[0].lower() == "data"
        and parts[1].lower() == "narrative_editions"
        and text.endswith(".json")
    )


def automerge_allowlisted(path: str | Path) -> bool:
    """True only for generated Preview edition JSON. Never for executable paths."""
    rel = _posix_rel(path)
    if rel in AUTOMERGE_ALLOWED_PATHS:
        return True
    if AUTOMERGE_ALLOWED_PREFIXES and any(
        rel.startswith(prefix) for prefix in AUTOMERGE_ALLOWED_PREFIXES
    ):
        return True
    return _editions_json_path(rel)


def requires_human_merge(paths, *, root: Path | None = None) -> bool:
    """True unless every path is on the automerge allowlist.

    ``root`` is accepted so callers can pass the PR tree; Preview-vs-
    Review is decided in ``automerge_blocked``, not here.
    """
    del root
    for raw in paths or []:
        if not automerge_allowlisted(raw):
            return True
    return False


def automerge_blocked(paths, *, root: Path | None = None) -> bool:
    """True when the PR is off the allowlist or a Review edition changed.

    Signed Reviews still need a human merge. Only Preview edition JSON
    may automerge.
    """
    if requires_human_merge(paths, root=root):
        return True
    base = Path(root) if root is not None else config.REPO_ROOT
    if extra_publish_surface(base):
        return True
    for raw in paths or []:
        if extra_publish_path(raw):
            return True
        rel = _posix_rel(raw)
        if rel == "data/narrative.json" or _editions_json_path(rel):
            payload = load_payload(base / rel)
            if payload_is_review(payload):
                return True
    return False


def _published_edition_files(root: Path) -> list[Path]:
    """Every JSON Hugo can render as ``/narrative/`` or ``/narrative/<slug>/``.

    The editions directory name is matched case-insensitively so a
    ``Narrative_Editions`` drop still goes through the Review gate.
    Only exact ``.json`` suffixes are editions; ``.JSON`` is extra surface.
    """
    files: list[Path] = []
    data = root / "data"
    if data.is_dir():
        for child in sorted(data.iterdir()):
            if child.is_file() and child.name.lower() == "narrative.json":
                if child.name.endswith(".json"):
                    files.append(child)
            if child.is_dir() and child.name.lower() == "narrative_editions":
                files.extend(
                    sorted(
                        path
                        for path in child.iterdir()
                        if path.is_file() and path.name.endswith(".json")
                    )
                )
    current = root / "data" / "narrative.json"
    if current not in files:
        files.insert(0, current)
    return files


def _edition_file_for_slug(root: Path, slug: str, data_dirs: list[str]) -> Path | None:
    """Edition JSON that publishes ``/`` (empty slug) or ``/narrative/<slug>/``."""
    dirs = data_dirs or ["data"]
    if not slug:
        for rel in dirs:
            live = root / rel / "narrative.json"
            if live.is_file() and live.name.endswith(".json"):
                return live
        return None
    if not _safe_signoff_slug(slug):
        return None
    for rel in dirs:
        edition = root / rel / "narrative_editions" / f"{slug}.json"
        if edition.is_file() and edition.name.endswith(".json"):
            return edition
        live = root / rel / "narrative.json"
        if live.is_file() and live.name.endswith(".json"):
            loaded = load_payload(live)
            if loaded and str(loaded.get("slug") or "").strip() == slug:
                return live
    return None


def _static_page_sources(root: Path, static_dirs: list[str], rel_url: str) -> list[Path]:
    found: list[Path] = []
    for rel in static_dirs:
        candidate = root / rel / rel_url
        if candidate.is_file():
            found.append(candidate)
    return found


def _unexpected_content_pages(root: Path, content_dirs: list[str]) -> list[Path]:
    """content/narrative/<slug>/ pages that are not the section stub."""
    found: list[Path] = []
    for rel in content_dirs:
        base = root / rel / "narrative"
        if not base.is_dir():
            continue
        for child in base.iterdir():
            name = child.name
            if name.startswith("_"):
                continue
            if child.is_file() and name in {"_index.md", "_index.html"}:
                continue
            if child.is_dir() and _safe_signoff_slug(name):
                for extra in child.rglob("*"):
                    if extra.is_file():
                        found.append(extra)
            elif child.is_file() and _safe_signoff_slug(child.stem):
                found.append(child)
    return found


def _html_is_review_shaped(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return bool(_REVIEW_HTML.search(text))


def _is_allowed_asset(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    if name in _ASSET_NAMES:
        return True
    suffix = Path(name).suffix.lower()
    return suffix in _ASSET_SUFFIXES


def _known_generated_page(rel: str, index: dict[str, tuple[Path, dict]]) -> bool:
    """True for Hugo surfaces that map to the home, section, feeds, or a slug."""
    if rel in {"index.html", "index.xml", "sitemap.xml"}:
        return True
    if rel in {"narrative/index.html", "narrative/index.xml"}:
        return True
    parts = rel.split("/")
    if (
        len(parts) == 3
        and parts[0] == "narrative"
        and parts[2] in {"index.html", "index.xml"}
        and (parts[1] in index or _safe_signoff_slug(parts[1]))
    ):
        return True
    return False


def _allowed_edition_index(
    root: Path, data_dirs: list[str]
) -> dict[str, tuple[Path, dict]]:
    """slug (and '') -> (file, payload) for Preview / signed / legacy editions."""
    index: dict[str, tuple[Path, dict]] = {}
    files: list[Path] = []
    for rel in data_dirs:
        data = root / rel
        live = data / "narrative.json"
        if live.is_file() and live.name.endswith(".json"):
            files.append(live)
        editions = data / "narrative_editions"
        if editions.is_dir():
            files.extend(
                sorted(
                    path
                    for path in editions.iterdir()
                    if path.is_file() and path.name.endswith(".json")
                )
            )
    for path in files:
        payload = load_payload(path)
        if payload is None:
            raise GateError(f"cannot load edition {path}")
        if should_block_review(payload, root=root, edition_file=path):
            continue
        slug = str(payload.get("slug") or "").strip()
        if slug:
            index[slug] = (path, payload)
        if path.name == "narrative.json":
            index[""] = (path, payload)
    return index


def _text_matches_allowed(text: str, index: dict[str, tuple[Path, dict]]) -> bool:
    """Rendered HTML/XML must cite only allowed editions and their copy."""
    if "PUBLICREVIEW" in text:
        return False
    for slug in _SLUG_HREF.findall(text):
        if slug not in index:
            return False
    if not _REVIEW_HTML.search(text):
        return True
    for slug, (_path, payload) in index.items():
        headline = str(payload.get("headline") or "").strip()
        if headline and headline in text:
            return True
        if slug and slug in text:
            return True
        prose = _payload_prose(payload)
        if prose and prose[:80] and prose[:80] in text:
            return True
    return False


def _walk_dist_files(dist: Path) -> list[Path]:
    return sorted(path for path in dist.rglob("*") if path.is_file())


def output_blocked(dist: Path, *, root: Path | None = None) -> bool:
    """True when any built file is unsigned, unmapped, or from staticDir.

    Censuses **every** file under the publish dir — section pages, RSS,
    sitemap, 404, siblings, and arbitrary HTML/XML — not only
    ``dist/index.html`` and ``dist/narrative/<slug>/index.html``.
    Dirs come from ``hugo config``. Hugo missing fails closed.
    """
    base = Path(root) if root is not None else config.REPO_ROOT
    publish = Path(dist)
    if not publish.is_dir():
        raise GateError(f"build output missing: {publish}")
    surface = resolve_hugo_surface(base)
    if not surface["layouts"]:
        raise GateError("hugo config resolved no layoutDir")
    if not surface["static"] or not surface["content"] or not surface["data"]:
        raise GateError("hugo config resolved an empty publish surface")
    if _unexpected_content_pages(base, list(surface["content"])):
        return True
    index = _allowed_edition_index(base, list(surface["data"]))
    if "" not in index:
        return True
    home_html = publish / "index.html"
    if not home_html.is_file():
        raise GateError(f"no home page under {publish}")
    for path in _walk_dist_files(publish):
        try:
            rel = path.resolve().relative_to(publish.resolve()).as_posix()
        except ValueError:
            return True
        static_hits = _static_page_sources(base, list(surface["static"]), rel)
        suffix = Path(rel).suffix.lower()
        if static_hits:
            if _is_allowed_asset(rel):
                continue
            if suffix in _TEXT_SUFFIXES:
                return True
            if suffix not in _DOC_SUFFIXES:
                return True
            try:
                static_text = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                raise GateError(f"cannot read built file {path}: {exc}") from exc
            if (
                "PUBLICREVIEW" in static_text
                or _REVIEW_HTML.search(static_text)
                or _SLUG_HREF.search(static_text)
            ):
                return True
            continue
        if _is_allowed_asset(rel):
            continue
        if suffix not in _TEXT_SUFFIXES:
            return True
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise GateError(f"cannot read built file {path}: {exc}") from exc
        if static_hits:
            return True
        if not _text_matches_allowed(text, index):
            return True
        if not _known_generated_page(rel, index) and (
            _REVIEW_HTML.search(text) or "PUBLICREVIEW" in text
        ):
            return True
        name = Path(rel).name
        parent = Path(rel).parent.as_posix()
        if parent == "narrative" and name != "index.html" and name != "index.xml":
            stem = Path(name).stem
            if _safe_signoff_slug(stem) or _REVIEW_HTML.search(text):
                return True
        if parent.startswith("narrative/") and name not in {
            "index.html",
            "index.xml",
        }:
            if _REVIEW_HTML.search(text) or "PUBLICREVIEW" in text:
                return True
        parts = rel.split("/")
        if (
            len(parts) >= 2
            and parts[0] == "narrative"
            and _safe_signoff_slug(parts[1])
            and name == "index.html"
        ):
            edition = _edition_file_for_slug(base, parts[1], list(surface["data"]))
            if edition is None:
                return True
            payload = load_payload(edition)
            if payload is None or should_block_review(
                payload, root=base, edition_file=edition
            ):
                return True
        if rel == "index.html":
            live = index.get("")
            if live is None:
                return True
            if should_block_review(live[1], root=base, edition_file=live[0]):
                return True
    return False


def pages_blocked(paths=None, *, root: Path | None = None, dist: Path | None = None) -> bool:
    """True when any published Review edition is unsigned or unreadable.

    Hugo builds ``/narrative/`` from ``data/narrative.json`` and every
    ``/narrative/<slug>/`` from ``data/narrative_editions/<slug>.json``.
    A hand-merged held Review, an editions-only Review, and an edited
    archive copy of a signed Review must all block the next deploy —
    even when this push only touched README or the schedule.

    Extra Hugo data/config (yaml/toml/.JSON, hugo.toml, config/) also
    blocks: those files render without going through the JSON scan.

    When ``dist`` is set, also census the built output. Every home and
    ``/narrative/<slug>/`` page must map to Preview / signed Review /
    legacy-pinned edition JSON. staticDir copies fail closed.
    """
    del paths
    base = Path(root) if root is not None else config.REPO_ROOT
    if extra_publish_surface(base):
        return True
    current_path = base / "data" / "narrative.json"
    if not current_path.is_file():
        return True
    for path in _published_edition_files(base):
        if not path.is_file():
            if path == current_path:
                return True
            continue
        payload = load_payload(path)
        if payload is None:
            raise GateError(f"cannot load edition {path}")
        if should_block_review(payload, root=base, edition_file=path):
            return True
    if dist is not None:
        return output_blocked(Path(dist), root=base)
    return False


def _git_changed(base: str, root: Path) -> list[str]:
    cmd = ["git", "diff", "--name-only", f"{base}...HEAD"]
    try:
        out = subprocess.check_output(
            cmd, cwd=root, text=True, stderr=subprocess.STDOUT
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise GateError(f"git diff failed ({base}...HEAD): {exc}") from exc
    return [line.strip() for line in out.splitlines() if line.strip()]


def _git_changed_from_parent(root: Path) -> list[str]:
    try:
        out = subprocess.check_output(
            ["git", "diff", "--name-only", "HEAD^", "HEAD"],
            cwd=root,
            text=True,
            stderr=subprocess.STDOUT,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise GateError(f"git diff failed (HEAD^ HEAD): {exc}") from exc
    return [line.strip() for line in out.splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Review edition publish gate")
    parser.add_argument("--automerge", action="store_true")
    parser.add_argument("--pages", action="store_true")
    parser.add_argument("--base", help="git ref to diff against for --automerge")
    parser.add_argument("--root", default="")
    parser.add_argument(
        "--dist",
        default="",
        help="post-build publishDir to census (required on deploy)",
    )
    parser.add_argument("paths", nargs="*", help="changed paths (tests / overrides)")
    args = parser.parse_args(argv)
    root = Path(args.root) if args.root else config.REPO_ROOT
    paths = list(args.paths)
    try:
        if args.automerge:
            if not paths:
                paths = _git_changed(args.base or "origin/main", root)
            if automerge_blocked(paths, root=root):
                print(
                    "Review edition requires Karen's ed25519 sign-off "
                    f"({SIGNOFF_DIR}/<slug>.json + <slug>.sig), "
                    "or this PR is off the automerge allowlist",
                    file=sys.stderr,
                )
                return 1
            return 0
        if args.pages:
            # pages_blocked scans every published edition; the push diff
            # is not consulted, so a shallow checkout must not fail closed.
            dist = Path(args.dist) if args.dist else None
            if dist is not None and not dist.is_absolute():
                dist = root / dist
            if pages_blocked(paths, root=root, dist=dist):
                print(
                    "skip: unsigned Review edition; not deploying to gh-pages",
                    file=sys.stderr,
                )
                return 2
            return 0
    except GateError as exc:
        print(f"review gate failed closed: {exc}", file=sys.stderr)
        return 1
    parser.error("choose --automerge or --pages")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
