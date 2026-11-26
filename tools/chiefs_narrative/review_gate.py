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
a git directory whose objects are read with ``git cat-file`` /
``git diff``. Head content is never imported, installed, or executed.

Automerge is an allowlist, not a growing blocklist. Every ref under
``promote/`` takes the edition/develop path (hold, repair, archive and
wire are read from the develop-tip git objects). A non-``promote/``
ref never automerges. A promote whose diff touches ``.github/`` or
``tools/`` is always a human merge (authoritative in edition-qa.yml;
ci.yml YAML is from head — residual #426).

A ``promote/<slug>`` PR (slug ``YYYY-MM-DD-HHMM``, optional suffix such
as ``-lede``) may automerge when the slug path set plus blobs exactly
equal the freshly fetched ``origin/develop`` tip, modes are ``100644``,
and a Review carries a valid ed25519 pair for this slug and payload
(pubkey from this BASE tree). Unsigned Reviews still refuse.
``holdAutomerge`` is read from develop-tip ``narrative_repair.json``.
When it is true the hold-override step (always run) binds human-merge
and ``karen-qa`` to the PR whose merge *introduced* the edition blobs.

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
import urllib.error
import urllib.request
from dataclasses import dataclass
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
PROMOTE_HEAD_RE = re.compile(
    r"^promote/(\d{4}-\d{2}-\d{2}-\d{4})(?:-[A-Za-z0-9._-]+)?$"
)
PROMOTE_PREFIX = "promote/"
AUTOMERGE_PATH_CAP = 40
AUTOMERGE_BLOB_CAP = 5 * 1024 * 1024
DEVELOP_REF_DEFAULT = "origin/develop"
ES_FIXED_PATHS = frozenset(
    {
        "data/narrative.json",
        "data/narrative_archive.json",
        "data/narrative_repair.json",
        "data/wire.json",
        "data/schedule_2026.json",
    }
)
_SHA40 = re.compile(r"\b([0-9a-f]{40})\b")
COMPARE_FILE_CAP = 300
COMMIT_PAGE_CAP = 250
_KAREN_QA_CONTEXT = "karen-qa"
_DEVELOP_SQUASH = re.compile(
    r"develop squash ([0-9a-f]{7,40})\b", re.IGNORECASE
)
_FROM_DEVELOP = re.compile(
    r"from develop ([0-9a-f]{7,40})\b", re.IGNORECASE
)
_KAREN_QA_NAMES = frozenset({"karen-qa", "Karen QA", "karen_qa"})
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
    """True when a non-promote PR is off the Preview allowlist or is a Review.

    ``promote/<slug>`` automerge is decided by ``automerge_check`` (ES set,
    develop identity, signed Review). This helper keeps the original
    Preview-only rule for every other caller and for existing tests.
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


@dataclass(frozen=True)
class RawChange:
    old_mode: str
    new_mode: str
    old_sha: str
    new_sha: str
    status: str
    path: str
    orig_path: str | None = None


def is_promote_head_ref(head_ref: str | None) -> bool:
    """True for any one-segment ref under ``promote/`` (including ``-lede``)."""
    text = head_ref or ""
    if not text.startswith(PROMOTE_PREFIX):
        return False
    rest = text[len(PROMOTE_PREFIX) :]
    if not rest or "/" in rest or rest.startswith(".") or ".." in rest.split("-"):
        return False
    if "\n" in rest or "\0" in rest or " " in rest:
        return False
    return True


def promote_slug_from_ref(head_ref: str) -> str | None:
    match = PROMOTE_HEAD_RE.fullmatch(head_ref or "")
    return match.group(1) if match else None


def _git_output(
    args: list[str],
    *,
    cwd: Path,
    text: bool = False,
) -> str | bytes:
    try:
        return subprocess.check_output(
            ["git", *args],
            cwd=cwd,
            stderr=subprocess.STDOUT,
            text=text,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "output", "") or exc
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", "replace")
        raise GateError(f"git {' '.join(args)} failed: {detail}") from exc


def fetch_origin_develop(root: Path, develop_ref: str = DEVELOP_REF_DEFAULT) -> str:
    """Fetch ``origin/develop`` into the remote-tracking ref only. Never a local branch."""
    try:
        subprocess.check_output(
            [
                "git",
                "fetch",
                "--no-tags",
                "origin",
                "+refs/heads/develop:refs/remotes/origin/develop",
            ],
            cwd=root,
            stderr=subprocess.STDOUT,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "output", b"") or b""
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", "replace")
        raise GateError(f"develop fetch failed: {detail or exc}") from exc
    return _resolve_develop_sha(root, develop_ref)


def _resolve_develop_sha(root: Path, develop_ref: str) -> str:
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "--verify", f"{develop_ref}^{{commit}}"],
            cwd=root,
            text=True,
            stderr=subprocess.STDOUT,
        ).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise GateError("develop fetch failed: ref missing") from exc
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise GateError("develop fetch failed: ref missing")
    return sha


def _parse_raw_z(blob: bytes) -> list[RawChange]:
    """Parse ``git diff -z --raw --no-renames`` NUL records as whole paths."""
    if not blob:
        return []
    parts = blob.split(b"\0")
    changes: list[RawChange] = []
    i = 0
    while i < len(parts):
        meta = parts[i]
        if meta == b"":
            i += 1
            continue
        if not meta.startswith(b":"):
            raise GateError(f"git raw diff: unexpected record {meta!r}")
        text = meta.decode("utf-8", "surrogateescape")
        bits = text[1:].split()
        if len(bits) != 5:
            raise GateError(f"git raw diff: bad meta {text!r}")
        old_mode, new_mode, old_sha, new_sha, status = bits
        i += 1
        if i >= len(parts):
            raise GateError("git raw diff: truncated path")
        path = parts[i].decode("utf-8", "surrogateescape")
        i += 1
        orig: str | None = None
        if status[:1] in {"R", "C"}:
            orig = path
            if i >= len(parts):
                raise GateError("git raw diff: truncated rename path")
            path = parts[i].decode("utf-8", "surrogateescape")
            i += 1
        changes.append(
            RawChange(
                old_mode=old_mode,
                new_mode=new_mode,
                old_sha=old_sha,
                new_sha=new_sha,
                status=status,
                path=path,
                orig_path=orig,
            )
        )
    return changes


def git_raw_changed(
    base: str, head: str, root: Path, *, detect_renames: bool = False
) -> list[RawChange]:
    """List the net diff with rename expansion and embedded-NUL paths intact."""
    rename_flag = "-M" if detect_renames else "--no-renames"
    try:
        out = subprocess.check_output(
            [
                "git",
                "-c",
                "core.abbrev=no",
                "diff",
                "--full-index",
                "-z",
                "--raw",
                rename_flag,
                "--no-ext-diff",
                base,
                head,
            ],
            cwd=root,
            stderr=subprocess.STDOUT,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "output", b"") or b""
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", "replace")
        raise GateError(f"git diff failed ({base} {head}): {detail or exc}") from exc
    return _parse_raw_z(out)


def _git_changed(base: str, root: Path) -> list[str]:
    """Name-only view of ``git_raw_changed`` (legacy callers / argv-less CLI)."""
    head = "HEAD"
    return [item.path for item in git_raw_changed(base, head, root)]


def _path_is_exact(path: str) -> bool:
    """True when the git path is already a case-sensitive relative POSIX name."""
    if not path:
        return False
    if "\0" in path or "\n" in path or "\r" in path:
        return False
    if "\\" in path or path.startswith("/") or path.startswith("./"):
        return False
    if path.endswith("/") or "//" in path:
        return False
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return False
    return True


def classify_es_path(path: str) -> tuple[str, str | None]:
    """Return ``(kind, slug)`` for an ES member, or ``('bad', None)``.

    Exact case-sensitive match. No ``./``, ``\\``, ``//``, nested editions,
    or non-slug names. SVG is one level under ``public/images/narrative/<slug>/``.
    """
    if not _path_is_exact(path):
        return "bad", None
    if path in ES_FIXED_PATHS:
        return "fixed", None
    if path.startswith("data/narrative_editions/") and path.count("/") == 2:
        name = path.rsplit("/", 1)[-1]
        if name.endswith(".json"):
            slug = name[: -len(".json")]
            if SIGNOFF_SLUG_RE.fullmatch(slug):
                return "editions", slug
    if path.startswith("public/images/narrative/") and path.count("/") == 4:
        parts = path.split("/")
        slug = parts[3]
        name = parts[4]
        if SIGNOFF_SLUG_RE.fullmatch(slug) and name.endswith(".svg") and ".." not in name:
            return "svg", slug
    if path.startswith("signoff/review/") and path.count("/") == 2:
        name = path.rsplit("/", 1)[-1]
        if name.endswith(".json"):
            slug = name[: -len(".json")]
            if SIGNOFF_SLUG_RE.fullmatch(slug):
                return "signoff_json", slug
        if name.endswith(".sig"):
            slug = name[: -len(".sig")]
            if SIGNOFF_SLUG_RE.fullmatch(slug):
                return "signoff_sig", slug
    return "bad", None


def _change_status_reason(change: RawChange) -> str | None:
    if change.orig_path:
        return f"rename/delete: {change.orig_path} -> {change.path}"
    letter = change.status[:1]
    if letter in {"D", "R", "C", "T", "U", "X", "B"}:
        return f"rename/delete: {change.path}"
    if letter not in {"A", "M"}:
        return f"rename/delete: {change.path}"
    if change.new_mode == "120000" or change.old_mode == "120000":
        return f"mode/symlink: {change.path}"
    if change.new_mode == "160000" or change.old_mode == "160000":
        return f"mode/symlink: {change.path}"
    if change.new_mode != "100644":
        return f"mode/symlink: {change.path}"
    if letter == "M" and change.old_mode != "100644":
        return f"mode/symlink: {change.path}"
    if letter == "A" and change.old_mode not in {"000000", "100644"}:
        return f"mode/symlink: {change.path}"
    return None


def _ls_tree(root: Path, ref: str, path: str) -> tuple[str, str, str] | None:
    try:
        out = subprocess.check_output(
            ["git", "ls-tree", "-z", ref, "--", path],
            cwd=root,
            stderr=subprocess.STDOUT,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise GateError(f"git ls-tree failed ({ref} {path}): {exc}") from exc
    if not out or out == b"\0":
        return None
    rec = out.split(b"\0", 1)[0]
    if not rec:
        return None
    try:
        meta, name = rec.split(b"\t", 1)
    except ValueError as exc:
        raise GateError(f"git ls-tree: bad record for {path}") from exc
    del name
    bits = meta.decode("ascii", "replace").split()
    if len(bits) != 3:
        raise GateError(f"git ls-tree: bad meta for {path}")
    return bits[0], bits[1], bits[2]


def _iter_ls_tree(root: Path, ref: str, prefix: str = "") -> list[tuple[str, str, str]]:
    """Return ``(path, mode, blob_sha)`` rows for a tree (git objects only)."""
    args = ["ls-tree", "-r", "-z", "--full-tree", ref]
    if prefix:
        args.extend(["--", prefix])
    try:
        out = subprocess.check_output(
            ["git", *args],
            cwd=root,
            stderr=subprocess.STDOUT,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise GateError(f"git ls-tree failed ({ref} {prefix}): {exc}") from exc
    rows: list[tuple[str, str, str]] = []
    if not out:
        return rows
    for rec in out.split(b"\0"):
        if not rec:
            continue
        try:
            meta, name = rec.split(b"\t", 1)
        except ValueError as exc:
            raise GateError(f"git ls-tree: bad record under {prefix}") from exc
        bits = meta.decode("ascii", "replace").split()
        if len(bits) != 3:
            continue
        mode, kind, sha = bits
        if kind != "blob":
            continue
        path = name.decode("utf-8", "surrogateescape")
        rows.append((path, mode, sha))
    return rows


def slug_tree_files(root: Path, ref: str, slug: str) -> dict[str, tuple[str, str]]:
    """Edition path set at ``ref``: core JSON, SVGs, optional schedule/signoff."""
    out: dict[str, tuple[str, str]] = {}
    core = [
        "data/narrative.json",
        "data/narrative_archive.json",
        "data/narrative_repair.json",
        "data/wire.json",
        f"data/narrative_editions/{slug}.json",
        "data/schedule_2026.json",
        f"{SIGNOFF_DIR}/{slug}.json",
        f"{SIGNOFF_DIR}/{slug}.sig",
    ]
    for path in core:
        ent = _ls_tree(root, ref, path)
        if ent is not None:
            out[path] = (ent[0], ent[2])
    prefix = f"public/images/narrative/{slug}/"
    for path, mode, sha in _iter_ls_tree(root, ref, prefix):
        kind, path_slug = classify_es_path(path)
        if kind == "svg" and path_slug == slug:
            out[path] = (mode, sha)
    return out


def edition_introducing_sha(root: Path, develop_sha: str, slug: str) -> str:
    """Latest first-parent commit on develop that introduced the edition blob."""
    path = f"data/narrative_editions/{slug}.json"
    if _ls_tree(root, develop_sha, path) is None:
        path = "data/narrative.json"
    try:
        out = subprocess.check_output(
            [
                "git",
                "log",
                "-1",
                "--first-parent",
                "--format=%H",
                develop_sha,
                "--",
                path,
            ],
            cwd=root,
            text=True,
            stderr=subprocess.STDOUT,
        ).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise GateError(f"introducing commit lookup failed: {exc}") from exc
    if not re.fullmatch(r"[0-9a-f]{40}", out or ""):
        raise GateError("no introducing commit")
    return out


def _slug_blobs_changed(
    root: Path, old_sha: str, new_sha: str, slug: str
) -> bool:
    old = slug_tree_files(root, old_sha, slug)
    new = slug_tree_files(root, new_sha, slug)
    return old != new


def _cat_blob(root: Path, sha: str) -> bytes:
    try:
        return subprocess.check_output(
            ["git", "cat-file", "blob", sha],
            cwd=root,
            stderr=subprocess.STDOUT,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise GateError(f"git cat-file failed ({sha}): {exc}") from exc


def _strict_json_load(raw: bytes, path: str):
    """Parse JSON. BOM, NUL, invalid UTF-8, duplicates fail closed."""
    if b"\x00" in raw:
        raise GateError(f"NUL bytes in {path}")
    if raw.startswith(b"\xef\xbb\xbf"):
        raise GateError(f"UTF-8 BOM in {path}")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GateError(f"invalid UTF-8 in {path}: {exc}") from exc
    if not text.strip():
        raise GateError(f"empty JSON in {path}")

    def _hook(pairs: list[tuple[str, object]]) -> dict:
        out: dict[str, object] = {}
        for key, value in pairs:
            if key in out:
                raise GateError(f"duplicate key {key!r} in {path}")
            out[key] = value
        return out

    try:
        return json.loads(text, object_pairs_hook=_hook)
    except json.JSONDecodeError as exc:
        raise GateError(f"cannot parse {path}: {exc}") from exc


def strict_json_object(raw: bytes, path: str) -> dict:
    """Parse an ES JSON object. Archive/schedule lists use ``_strict_json_load``."""
    data = _strict_json_load(raw, path)
    if not isinstance(data, dict):
        raise GateError(f"{path} is not a JSON object")
    return data


def _write_github_output(path: str, values: dict[str, str]) -> None:
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def github_api_get(
    url_path: str, token: str | None = None, *, want_headers: bool = False
):
    """GET a GitHub API path. Tests may set FROZEN38_GITHUB_API_ROOT.

    Never raises into a traceback for 404/5xx/non-JSON/timeout: those return
    a status plus a dict the caller refuses by name (F1e).
    """
    root = (os.environ.get("FROZEN38_GITHUB_API_ROOT") or "https://api.github.com").rstrip(
        "/"
    )
    if url_path.startswith("http://") or url_path.startswith("https://"):
        url = url_path
    else:
        url = root + (url_path if url_path.startswith("/") else "/" + url_path)
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "arrowheadpaesano-review-gate",
    }
    auth = token or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    if auth:
        headers["Authorization"] = f"Bearer {auth}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    header_map: dict[str, str] = {}
    try:
        with urllib.request.urlopen(request, timeout=20) as resp:
            body = resp.read()
            status = getattr(resp, "status", 200)
            header_map = {str(k): str(v) for k, v in resp.headers.items()}
    except urllib.error.HTTPError as exc:
        raw = exc.read() if exc.fp else b""
        header_map = {str(k): str(v) for k, v in (exc.headers or {}).items()}
        try:
            parsed = json.loads(raw.decode("utf-8")) if raw else {}
        except json.JSONDecodeError:
            parsed = {"_non_json": True, "message": raw.decode("utf-8", "replace")}
        if want_headers:
            return int(exc.code), parsed, header_map
        return int(exc.code), parsed
    except (urllib.error.URLError, TimeoutError, OSError):
        parsed = {"_timeout": True}
        if want_headers:
            return 599, parsed, header_map
        return 599, parsed
    try:
        parsed = json.loads(body.decode("utf-8")) if body else {}
    except json.JSONDecodeError:
        parsed = {"_non_json": True}
    if want_headers:
        return int(status), parsed, header_map
    return int(status), parsed


def _link_next(headers: dict[str, str]) -> str | None:
    blob = headers.get("Link") or headers.get("link") or ""
    for part in blob.split(","):
        if 'rel="next"' in part or "rel='next'" in part:
            match = re.search(r"<([^>]+)>", part)
            if match:
                return match.group(1)
    return None


def github_api_get_paginated(
    url_path: str,
    *,
    page_cap: int,
    token: str | None = None,
) -> tuple[int, list, str | None]:
    """Follow Link rel=next. Fail closed when a page hits ``page_cap`` with no next."""
    collected: list = []
    url: str | None = url_path
    while url:
        code, payload, headers = github_api_get(url, token=token, want_headers=True)
        if code != 200:
            return code, collected, f"HTTP {code}"
        if isinstance(payload, dict) and (payload.get("_non_json") or payload.get("_timeout")):
            why = "timeout" if payload.get("_timeout") else "non-JSON"
            return code, collected, why
        if not isinstance(payload, list):
            return code, collected, "not a list"
        collected.extend(payload)
        if len(payload) > page_cap:
            return code, collected, "truncated"
        nxt = _link_next(headers)
        if len(payload) == page_cap and not nxt:
            return code, collected, "truncated"
        url = nxt
    return 200, collected, None


def _expand_commit_sha(root: Path | None, token: str) -> str | None:
    if re.fullmatch(r"[0-9a-f]{40}", token):
        return token
    if root is None or not re.fullmatch(r"[0-9a-f]{7,40}", token):
        return None
    try:
        full = subprocess.check_output(
            ["git", "rev-parse", "--verify", f"{token}^{{commit}}"],
            cwd=root,
            text=True,
            stderr=subprocess.STDOUT,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return full if re.fullmatch(r"[0-9a-f]{40}", full) else None


def extract_develop_sha_from_body(body: str, root: Path | None = None) -> str | None:
    """Parse #418 ``develop squash <short>`` / ``from develop <short>`` first."""
    if not body:
        return None
    token = None
    match = _DEVELOP_SQUASH.search(body)
    if match:
        token = match.group(1)
    if token is None:
        match = _FROM_DEVELOP.search(body)
        if match:
            token = match.group(1)
    if token is None:
        found = _SHA40.findall(body)
        if len(found) == 1:
            token = found[0]
    if not token:
        return None
    expanded = _expand_commit_sha(root, token)
    return expanded or (token if re.fullmatch(r"[0-9a-f]{40}", token) else None)


def _is_bot_merger(merged_by: object) -> bool:
    """F1b: type Bot and/or login ending [bot]. No extra identity list."""
    if not isinstance(merged_by, dict):
        return False
    login = str(merged_by.get("login") or "")
    mtype = str(merged_by.get("type") or "")
    return mtype == "Bot" or login.endswith("[bot]")


def _payload_error(payload: object) -> str | None:
    if isinstance(payload, dict) and payload.get("_timeout"):
        return "timeout"
    if isinstance(payload, dict) and payload.get("_non_json"):
        return "non-JSON"
    return None


def _merged_develop_candidates(pulls: object) -> tuple[list[dict], str | None]:
    """N-F4: ignore open and closed-unmerged and non-develop bases.

    An open PR never converts a bot-merged develop PR into a pass (N4a).
    Two merged develop PRs claiming the sha refuse (N4c).
    """
    if not isinstance(pulls, list):
        return [], "zero or multiple PRs (0)"
    merged: list[dict] = []
    for item in pulls:
        if not isinstance(item, dict):
            continue
        if not item.get("merged_at"):
            continue
        base = item.get("base") if isinstance(item.get("base"), dict) else {}
        if str(base.get("ref") or "") != "develop":
            continue
        merged.append(item)
    if len(merged) > 1:
        return merged, f"zero or multiple PRs ({len(merged)})"
    return merged, None


def _pr_number(pr: dict) -> int | None:
    try:
        number = int(pr.get("number"))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def fetch_pull(repository: str, number: int) -> tuple[dict | None, str | None]:
    """GET /pulls/{n}. Merger identity lives only here (F1)."""
    code, payload = github_api_get(f"/repos/{repository}/pulls/{number}")
    err = _payload_error(payload)
    if err:
        return None, f"pulls {err}"
    if code == 404:
        return None, "pulls HTTP 404"
    if code >= 500:
        return None, f"pulls HTTP {code}"
    if code != 200:
        return None, f"pulls HTTP {code}"
    if not isinstance(payload, dict):
        return None, "pulls non-JSON"
    return payload, None


def merger_identity(pr: dict) -> tuple[bool, str]:
    """Pass only type User whose login does not end in [bot] (F1a/b/c/d)."""
    if "merged_by" not in pr:
        return False, "merged_by missing"
    merged_by = pr.get("merged_by")
    if merged_by is None:
        return False, "merged_by null"
    if not isinstance(merged_by, dict):
        return False, "merged_by missing"
    login = str(merged_by.get("login") or "")
    mtype = str(merged_by.get("type") or "")
    if mtype != "User" or not login or login.endswith("[bot]") or mtype == "Bot":
        return False, "bot merge"
    return True, "ok"


def _karen_qa_success(head_sha: str, repository: str) -> tuple[bool, str]:
    """N-F3: latest commit *status* with context exactly ``karen-qa``."""
    code, payload = github_api_get(
        f"/repos/{repository}/commits/{head_sha}/statuses?per_page=100"
    )
    err = _payload_error(payload)
    if err:
        return False, f"karen-qa {err}"
    if code != 200:
        return False, f"karen-qa HTTP {code}"
    rows: list[dict]
    if isinstance(payload, list):
        rows = [row for row in payload if isinstance(row, dict)]
    elif isinstance(payload, dict):
        rows = [row for row in (payload.get("statuses") or []) if isinstance(row, dict)]
    else:
        return False, "karen-qa absent"
    rows = sorted(
        rows,
        key=lambda row: str(row.get("updated_at") or row.get("created_at") or ""),
        reverse=True,
    )
    exact = [row for row in rows if row.get("context") == _KAREN_QA_CONTEXT]
    if not exact:
        for row in rows:
            context = str(row.get("context") or "")
            if context != _KAREN_QA_CONTEXT and "karen" in context.lower():
                return False, f"karen-qa lookalike {context}"
        return False, "karen-qa absent"
    state = str(exact[0].get("state") or "").lower()
    if state == "success":
        return True, "ok"
    return False, f"karen-qa {state or 'absent'}"


def pr_contains_commit(repository: str, number: int, sha: str, merge_commit_sha: str) -> tuple[bool, str | None]:
    """F2m/F2n: sha equals merge_commit_sha or is in paginated /pulls/{n}/commits."""
    if merge_commit_sha and sha == merge_commit_sha:
        return True, None
    code, commits, why = github_api_get_paginated(
        f"/repos/{repository}/pulls/{number}/commits?per_page={COMMIT_PAGE_CAP}",
        page_cap=COMMIT_PAGE_CAP,
    )
    if why == "truncated":
        return False, "commits truncated"
    if code != 200:
        return False, why or f"HTTP {code}"
    for row in commits:
        if isinstance(row, dict) and str(row.get("sha") or "") == sha:
            return True, None
    return False, None


def resolve_contributor_pr(sha: str, repository: str) -> tuple[dict | None, str]:
    """Map a commit to exactly one human-merged develop PR (F1 + N-F4 + F2m/n)."""
    code, pulls = github_api_get(f"/repos/{repository}/commits/{sha}/pulls")
    err = _payload_error(pulls)
    if err:
        return None, f"unresolvable: {err}"
    if code == 404:
        return None, "no introducing PR"
    if code != 200:
        return None, f"unresolvable: HTTP {code}"
    candidates, multi = _merged_develop_candidates(pulls)
    if multi and candidates:
        return None, "multiple PRs"
    if not candidates:
        return None, "no introducing PR"
    summary = candidates[0]
    number = _pr_number(summary)
    if number is None:
        return None, "no introducing PR"
    merge_sha = str(summary.get("merge_commit_sha") or "")
    contained, why = pr_contains_commit(repository, number, sha, merge_sha)
    if why:
        return None, why
    if not contained:
        return None, "unresolvable"
    full, fetch_why = fetch_pull(repository, number)
    if fetch_why:
        return None, fetch_why
    if full is None:
        return None, "unresolvable"
    return full, "ok"


def _commits_touching(root: Path, start: str, end: str, path: str) -> list[str]:
    blob = _git_output(
        ["log", "--format=%H", f"{start}..{end}", "--", path],
        cwd=root,
        text=True,
    )
    return [line.strip() for line in str(blob).splitlines() if line.strip()]


def _compare_truncated(repository: str, base: str, head: str) -> str | None:
    """F2j: GitHub compare 300-file page. Fail closed when truncated or at cap."""
    code, payload = github_api_get(
        f"/repos/{repository}/compare/{base}...{head}?per_page={COMPARE_FILE_CAP}"
    )
    if code != 200:
        return None
    if not isinstance(payload, dict) or payload.get("_non_json") or payload.get("_timeout"):
        return None
    files = payload.get("files") or []
    truncated = bool(payload.get("truncated"))
    if truncated or (isinstance(files, list) and len(files) >= COMPARE_FILE_CAP):
        return "compare truncated"
    return None


def provenance_diff_paths(changes: list[RawChange]) -> list[tuple[str, str]]:
    """F2a: adds, modifies, deletes, and both sides of a rename."""
    paths: list[tuple[str, str]] = []
    for change in changes:
        paths.append((change.path, change.status))
        if change.orig_path:
            paths.append((change.orig_path, change.status))
    return paths


def path_provenance_check(
    *,
    root: Path,
    base: str,
    head: str,
    develop_sha: str,
    repository: str,
) -> str | None:
    """F2: every promote-diff path's develop contributors since base are human+karen-qa."""
    if not repository or not base:
        return "provenance: missing base"
    cap = _compare_truncated(repository, base, head)
    if cap:
        return f"provenance: {cap}"
    try:
        changes = git_raw_changed(base, head, root)
    except GateError as exc:
        return f"provenance: {exc}"
    paths = provenance_diff_paths(changes)
    for path, status in paths:
        del status
        try:
            shas = _commits_touching(root, base, develop_sha, path)
        except GateError as exc:
            return f"provenance: {exc}"
        if not shas:
            return f"provenance: unresolvable {path}"
        for sha in shas:
            pr, why = resolve_contributor_pr(sha, repository)
            if pr is None:
                return f"provenance: {why}"
            ok, ident = merger_identity(pr)
            if not ok:
                return f"provenance: {ident}"
            head_info = pr.get("head") if isinstance(pr.get("head"), dict) else {}
            pr_head = str(head_info.get("sha") or "").strip()
            if not pr_head:
                return "provenance: missing PR head SHA"
            karen_ok, karen_why = _karen_qa_success(pr_head, repository)
            if not karen_ok:
                return f"provenance: {karen_why}"
    return None


def _verify_signoff_bytes(
    slug: str,
    edition_bytes: bytes,
    json_bytes: bytes | None,
    sig_text: str | None,
    pubkey: Path,
) -> str | None:
    """Return a refuse reason, or None when the pair is valid."""
    if json_bytes is None and sig_text is None:
        return "unsigned Review"
    if json_bytes is None:
        return "invalid signoff: missing json"
    if sig_text is None or sig_text.strip() == "":
        return "invalid signoff: missing sig"
    sha = hashlib.sha256(edition_bytes).hexdigest()
    expected = signoff_canonical_bytes(slug, sha)
    try:
        data = json.loads(json_bytes)
    except json.JSONDecodeError:
        return "invalid signoff: payload sha"
    if not isinstance(data, dict):
        return "invalid signoff: payload sha"
    pair_slug = str(data.get("slug") or "")
    if pair_slug != slug:
        return "invalid signoff: slug"
    if data.get("sha") != sha:
        return "invalid signoff: payload sha"
    if json_bytes != expected:
        return "invalid signoff: payload sha"
    if data.get("verdict") != "PASS":
        return "invalid signoff: payload sha"
    try:
        signature = base64.b64decode(sig_text.strip(), validate=True)
    except (ValueError, binascii.Error):
        return "invalid signoff: sig"
    if len(signature) != 64:
        return "invalid signoff: sig"
    if not _verify_ed25519(message=json_bytes, signature=signature, pubkey_path=pubkey):
        return "invalid signoff: sig"
    return None


def _blob_at(root: Path, ref: str, path: str) -> bytes | None:
    entry = _ls_tree(root, ref, path)
    if entry is None:
        return None
    return _cat_blob(root, entry[2])


def _slug_from_edition_bytes(raw: bytes, path: str) -> str:
    payload = strict_json_object(raw, path)
    return str(payload.get("slug") or "").strip()


def hold_override_check(
    *,
    root: Path,
    develop_ref: str,
    develop_sha: str,
    head: str,
    pr_body: str,
    repository: str,
    token: str | None = None,
    edition_slug: str | None = None,
    base: str = "",
) -> str | None:
    """E2 (b)/(c). Hold/repair/archive/wire come from develop-tip objects.

    Provenance is the PR that *introduced* the edition blobs, not develop tip.
    Merger identity is GET /pulls/{n}.merged_by only (F1). F2 then checks every
    promote-diff path's develop contributors since base.
    """
    del token
    del develop_ref
    repair_bytes = _blob_at(root, develop_sha, "data/narrative_repair.json")
    hold = False
    if repair_bytes is not None:
        try:
            repair = strict_json_object(repair_bytes, "data/narrative_repair.json")
        except GateError as exc:
            return f"hold override refused: a: {exc}"
        hold = bool(repair.get("holdAutomerge"))
    if hold:
        named = extract_develop_sha_from_body(pr_body, root=root)
        if not named:
            return "hold override refused: b: missing body SHA"
        if named != develop_sha:
            return "hold override refused: b: SHA != tip"
        if not repository:
            return "hold override refused: b: missing repository"
        slug = edition_slug
        if not slug:
            live = _blob_at(root, develop_sha, "data/narrative.json")
            if live is not None:
                try:
                    slug = _slug_from_edition_bytes(live, "data/narrative.json")
                except GateError:
                    slug = ""
        try:
            intro = edition_introducing_sha(root, develop_sha, slug or "")
        except GateError as exc:
            return f"hold override refused: b: {exc}"
        code, pulls = github_api_get(f"/repos/{repository}/commits/{intro}/pulls")
        err = _payload_error(pulls)
        if err:
            return f"hold override refused: b: API error: {err}"
        if code != 200:
            return f"hold override refused: b: API error: HTTP {code}"
        if not isinstance(pulls, list):
            return "hold override refused: b: zero or multiple PRs (0)"
        if len(pulls) == 0:
            return "hold override refused: b: no introducing PR"
        candidates, multi = _merged_develop_candidates(pulls)
        if multi:
            return f"hold override refused: b: {multi}"
        if not candidates:
            return "hold override refused: b: no introducing PR"
        if len(candidates) != 1:
            return f"hold override refused: b: zero or multiple PRs ({len(candidates)})"
        summary = candidates[0]
        if not isinstance(summary, dict):
            return "hold override refused: b: zero or multiple PRs"
        number = _pr_number(summary)
        if number is None:
            return "hold override refused: b: no introducing PR"
        pr, fetch_why = fetch_pull(repository, number)
        if fetch_why:
            return f"hold override refused: b: {fetch_why}"
        if pr is None:
            return "hold override refused: b: no introducing PR"
        head_info = pr.get("head") if isinstance(pr.get("head"), dict) else {}
        pr_head = str(head_info.get("sha") or "").strip()
        if not pr_head:
            return "hold override refused: b: missing PR head SHA"
        ok, why = _karen_qa_success(pr_head, repository)
        if not ok:
            return f"hold override refused: b: {why}"
        ident_ok, ident_why = merger_identity(pr)
        if not ident_ok:
            return f"hold override refused: c: {ident_why}"
    if repository and base:
        prov = path_provenance_check(
            root=root,
            base=base,
            head=head,
            develop_sha=develop_sha,
            repository=repository,
        )
        if prov:
            return prov
    del head
    return None


def _payload_from_head_blob(root: Path, head: str, rel: str) -> dict | None:
    raw = _blob_at(root, head, rel)
    if raw is None:
        return None
    data = _strict_json_load(raw, rel)
    return data if isinstance(data, dict) else None


def _legacy_paths_reason(paths: list[str], root: Path, head: str) -> str | None:
    """Non-promote refs never automerge. Reasons come from HEAD git objects."""
    if not paths:
        return "no changes"
    if len(paths) > AUTOMERGE_PATH_CAP:
        return f"cap: {len(paths)} paths exceeds {AUTOMERGE_PATH_CAP}"
    unsigned = None
    allow = None
    for raw in paths:
        rel = str(raw)
        if rel.startswith(".github/") or rel.startswith("tools/"):
            allow = allow or f"off the automerge allowlist: {rel}"
        if extra_publish_path(rel):
            allow = allow or f"off the automerge allowlist: {rel}"
        if rel == "data/narrative.json" or _editions_json_path(_posix_rel(rel)):
            try:
                payload = _payload_from_head_blob(root, head, rel)
            except GateError:
                payload = None
            if payload_is_review(payload):
                unsigned = unsigned or f"unsigned Review: {rel}"
        if not automerge_allowlisted(rel):
            allow = allow or f"off the automerge allowlist: {rel}"
    if unsigned:
        return unsigned
    if allow:
        return allow
    return "off the automerge allowlist"


def automerge_check(
    *,
    root: Path,
    base: str,
    head: str,
    head_ref: str,
    develop_ref: str,
    fetch_develop: bool,
    paths_override: list[str] | None = None,
    pinned_develop_sha: str = "",
) -> tuple[str | None, dict[str, str]]:
    """Return ``(refuse_reason, github_output)``. Reason None means pass."""
    meta: dict[str, str] = {
        "head_sha": "",
        "develop_sha": "",
        "hold_automerge": "false",
        "verdict": "pass",
    }
    try:
        head_sha = subprocess.check_output(
            ["git", "rev-parse", "--verify", f"{head}^{{commit}}"],
            cwd=root,
            text=True,
            stderr=subprocess.STDOUT,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        head_sha = head
    meta["head_sha"] = head_sha

    if paths_override is not None:
        reason = _legacy_paths_reason(paths_override, root, head)
        if reason:
            meta["verdict"] = "refuse"
        return reason, meta

    promote = is_promote_head_ref(head_ref)
    try:
        if fetch_develop:
            develop_sha = fetch_origin_develop(root, develop_ref)
        else:
            develop_sha = _resolve_develop_sha(root, develop_ref)
    except GateError as exc:
        msg = str(exc)
        if "develop fetch failed" not in msg:
            msg = f"develop fetch failed: {exc}"
        meta["verdict"] = "refuse"
        return msg, meta
    meta["develop_sha"] = develop_sha

    changes = git_raw_changed(base, head, root)
    if not changes:
        meta["verdict"] = "refuse"
        return "no changes", meta

    listed: list[str] = []
    for change in changes:
        listed.append(change.path)
        if change.orig_path:
            listed.append(change.orig_path)
    if len(listed) > AUTOMERGE_PATH_CAP:
        meta["verdict"] = "refuse"
        return f"cap: {len(listed)} paths exceeds {AUTOMERGE_PATH_CAP}", meta

    if not promote:
        names = []
        for change in changes:
            names.append(change.path)
            if change.orig_path:
                names.append(change.orig_path)
        reason = _legacy_paths_reason(names, root, head)
        meta["verdict"] = "refuse"
        return reason or "off the automerge allowlist", meta

    branch_slug = promote_slug_from_ref(head_ref)
    if not branch_slug:
        meta["verdict"] = "refuse"
        return "off the automerge allowlist: branch slug", meta

    slugs: set[str] = set()
    edition_json_paths: list[str] = []
    signoff_json: dict[str, RawChange] = {}
    signoff_sig: dict[str, RawChange] = {}
    for change in changes:
        status_reason = _change_status_reason(change)
        if status_reason:
            meta["verdict"] = "refuse"
            return status_reason, meta
        for candidate in (change.path, change.orig_path):
            if not candidate:
                continue
            if candidate.startswith(".github/") or candidate.startswith("tools/"):
                meta["verdict"] = "refuse"
                return f"off the automerge allowlist: {candidate}", meta
            if candidate in {".gitattributes", ".gitmodules"} or candidate.endswith(
                "/.gitattributes"
            ) or candidate.endswith("/.gitmodules"):
                meta["verdict"] = "refuse"
                return f"off the automerge allowlist: {candidate}", meta
        kind, slug = classify_es_path(change.path)
        if kind == "bad":
            meta["verdict"] = "refuse"
            return f"off the automerge allowlist: {change.path}", meta
        if slug and kind in {"editions", "svg"}:
            slugs.add(slug)
        if kind in {"editions", "fixed"} and change.path.endswith(".json"):
            if kind == "editions" or change.path == "data/narrative.json":
                edition_json_paths.append(change.path)
        if kind == "signoff_json" and slug:
            signoff_json[slug] = change
        if kind == "signoff_sig" and slug:
            signoff_sig[slug] = change
        if len(change.new_sha.strip("0")) == 0:
            meta["verdict"] = "refuse"
            return f"rename/delete: {change.path}", meta
        blob = _cat_blob(root, change.new_sha)
        if len(blob) > AUTOMERGE_BLOB_CAP:
            meta["verdict"] = "refuse"
            return (
                f"cap: blob {change.path} is {len(blob)} bytes "
                f"(limit {AUTOMERGE_BLOB_CAP})"
            ), meta
        if change.path.endswith(".json"):
            if change.path in {"data/narrative_archive.json", "data/schedule_2026.json"}:
                data = _strict_json_load(blob, change.path)
                if not isinstance(data, (dict, list)):
                    raise GateError(f"{change.path} is not JSON")
            else:
                strict_json_object(blob, change.path)
        dev = _ls_tree(root, develop_sha, change.path)
        if dev is None:
            meta["verdict"] = "refuse"
            return f"blob differs from develop {develop_sha}: {change.path}", meta
        dev_mode, _dev_type, dev_sha = dev
        if dev_sha != change.new_sha:
            meta["verdict"] = "refuse"
            return f"stale vs develop {develop_sha}: {change.path}", meta
        if dev_mode != change.new_mode or dev_mode != "100644":
            meta["verdict"] = "refuse"
            return f"mode/symlink: {change.path}", meta

    if len(slugs) > 1:
        meta["verdict"] = "refuse"
        return f"off the automerge allowlist: two editions {sorted(slugs)}", meta
    edition_slug = branch_slug
    live_bytes = _blob_at(root, head, "data/narrative.json")
    if live_bytes is None:
        meta["verdict"] = "refuse"
        return "off the automerge allowlist: data/narrative.json", meta
    live_slug = _slug_from_edition_bytes(live_bytes, "data/narrative.json")
    if live_slug and live_slug != edition_slug:
        meta["verdict"] = "refuse"
        return (
            f"off the automerge allowlist: narrative.json slug {live_slug} "
            f"!= branch slug {edition_slug}"
        ), meta
    if live_slug and SIGNOFF_SLUG_RE.fullmatch(live_slug):
        slugs.add(live_slug)
    develop_files = slug_tree_files(root, develop_sha, edition_slug)
    head_files = slug_tree_files(root, head, edition_slug)
    missing = sorted(set(develop_files) - set(head_files))
    extra = sorted(set(head_files) - set(develop_files))
    if missing:
        meta["verdict"] = "refuse"
        return f"blob differs from develop {develop_sha}: {missing[0]}", meta
    if extra:
        meta["verdict"] = "refuse"
        return f"blob differs from develop {develop_sha}: {extra[0]}", meta
    for path, (dev_mode, dev_blob) in sorted(develop_files.items()):
        head_mode, head_blob = head_files[path]
        if head_blob != dev_blob:
            meta["verdict"] = "refuse"
            return f"stale vs develop {develop_sha}: {path}", meta
        if dev_mode != "100644" or head_mode != "100644":
            meta["verdict"] = "refuse"
            return f"mode/symlink: {path}", meta
    if pinned_develop_sha and pinned_develop_sha != develop_sha:
        if _slug_blobs_changed(root, pinned_develop_sha, develop_sha, edition_slug):
            meta["verdict"] = "refuse"
            return f"stale vs develop {develop_sha}", meta
    editions_path = f"data/narrative_editions/{edition_slug}.json"
    editions_bytes = _blob_at(root, head, editions_path)
    pubkey = public_key_path()
    review_paths: list[str] = []
    for rel in ("data/narrative.json", editions_path):
        raw = live_bytes if rel == "data/narrative.json" else editions_bytes
        if raw is None:
            continue
        payload = strict_json_object(raw, rel)
        try:
            is_review = payload_is_review(payload)
        except GateError:
            raise
        if is_review:
            review_paths.append(rel)

    pair_json = _blob_at(root, head, f"{SIGNOFF_DIR}/{edition_slug}.json")
    pair_sig_raw = _blob_at(root, head, f"{SIGNOFF_DIR}/{edition_slug}.sig")
    pair_sig = (
        pair_sig_raw.decode("ascii")
        if pair_sig_raw is not None
        else None
    )
    pair_in_diff = edition_slug in signoff_json or edition_slug in signoff_sig
    other_signoff = set(signoff_json) | set(signoff_sig)
    other_signoff.discard(edition_slug)
    if other_signoff:
        meta["verdict"] = "refuse"
        return f"invalid signoff: slug {sorted(other_signoff)[0]}", meta

    edition_for_sig = editions_bytes if editions_bytes is not None else live_bytes
    if review_paths:
        why = _verify_signoff_bytes(
            edition_slug, edition_for_sig, pair_json, pair_sig, pubkey
        )
        if why == "unsigned Review":
            meta["verdict"] = "refuse"
            return f"unsigned Review: {review_paths[0]}", meta
        if why:
            meta["verdict"] = "refuse"
            return f"{why}: {review_paths[0]}", meta

    if editions_bytes is not None and editions_bytes != live_bytes:
        meta["verdict"] = "refuse"
        return f"off the automerge allowlist: {editions_path} bytes != data/narrative.json", meta
    if editions_bytes is not None:
        copy_slug = _slug_from_edition_bytes(editions_bytes, editions_path)
        if copy_slug != edition_slug:
            meta["verdict"] = "refuse"
            return (
                f"off the automerge allowlist: {editions_path} slug {copy_slug} "
                f"!= {edition_slug}"
            ), meta

    main_live = _blob_at(root, base, "data/narrative.json")
    if main_live is not None:
        try:
            main_slug = _slug_from_edition_bytes(main_live, "data/narrative.json")
        except GateError:
            main_slug = ""
        if main_slug and edition_slug < main_slug:
            meta["verdict"] = "refuse"
            return f"older than main: {edition_slug} < {main_slug}", meta

    repair_bytes = _blob_at(root, develop_sha, "data/narrative_repair.json")
    if repair_bytes is not None:
        repair = strict_json_object(repair_bytes, "data/narrative_repair.json")
        if repair.get("holdAutomerge"):
            meta["hold_automerge"] = "true"

    if not review_paths and (pair_in_diff or pair_json is not None or pair_sig is not None):
        why = _verify_signoff_bytes(
            edition_slug, edition_for_sig, pair_json, pair_sig, pubkey
        )
        if why:
            meta["verdict"] = "refuse"
            labeled = why if why != "unsigned Review" else "invalid signoff: missing pair"
            return f"{labeled}: signoff/review/{edition_slug}", meta

    return None, meta


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
    parser.add_argument("--head", default="HEAD", help="PR head SHA or ref (git objects)")
    parser.add_argument(
        "--head-ref",
        default="",
        help="PR branch name; must arrive via env, never ${{ github.head_ref }} in run:",
    )
    parser.add_argument(
        "--develop-ref",
        default=DEVELOP_REF_DEFAULT,
        help="remote-tracking develop tip for identity (never a local develop branch)",
    )
    parser.add_argument(
        "--fetch-develop",
        action="store_true",
        help="git fetch origin develop into refs/remotes/origin/develop before identity",
    )
    parser.add_argument(
        "--github-output",
        default="",
        help="append develop_sha/head_sha/hold_automerge/verdict (GITHUB_OUTPUT)",
    )
    parser.add_argument(
        "--hold-override",
        action="store_true",
        help="E2 (a)(b)(c) check; no head checkout; uses develop blobs + GitHub API",
    )
    parser.add_argument(
        "--pinned-develop-sha",
        default="",
        help="gate-time develop SHA; re-verify refuses if tip moved and slug blobs changed",
    )
    parser.add_argument("--pr-body", default="")
    parser.add_argument("--pr-number", default="")
    parser.add_argument("--repository", default="")
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
        if args.hold_override:
            develop_ref = args.develop_ref or DEVELOP_REF_DEFAULT
            if args.fetch_develop:
                develop_sha = fetch_origin_develop(root, develop_ref)
            else:
                develop_sha = _resolve_develop_sha(root, develop_ref)
            body = args.pr_body
            if not body and args.pr_number and args.repository:
                code, pr = github_api_get(
                    f"/repos/{args.repository}/pulls/{args.pr_number}"
                )
                if code != 200 or not isinstance(pr, dict):
                    print(
                        f"hold override refused: b: API error: HTTP {code}",
                        file=sys.stderr,
                    )
                    return 1
                body = str(pr.get("body") or "")
            reason = hold_override_check(
                root=root,
                develop_ref=develop_ref,
                develop_sha=develop_sha,
                head=args.head or "HEAD",
                pr_body=body,
                repository=args.repository
                or os.environ.get("GITHUB_REPOSITORY")
                or "",
                edition_slug=promote_slug_from_ref(args.head_ref or ""),
                base=args.base or "",
            )
            if reason:
                print(reason, file=sys.stderr)
                return 1
            return 0
        if args.automerge:
            reason, meta = automerge_check(
                root=root,
                base=args.base or "origin/main",
                head=args.head or "HEAD",
                head_ref=args.head_ref or "",
                develop_ref=args.develop_ref or DEVELOP_REF_DEFAULT,
                fetch_develop=bool(args.fetch_develop),
                paths_override=paths or None,
                pinned_develop_sha=args.pinned_develop_sha or "",
            )
            _write_github_output(args.github_output, meta)
            if reason:
                print(reason, file=sys.stderr)
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
