"""Refuse unsigned Review editions on every path to main and gh-pages.

A Review-phase edition (phase.mode=review or ``Week N · Week M Review``)
cannot automerge or deploy unless Karen signed it with the committed
ed25519 public key. Sign-off is ``data/review_signoff/<slug>.json`` plus
``<slug>.sig`` — never a label, never a substring. Easy to disable later
via ``REVIEW_SIGNOFF_REQUIRED``.

Automerge must run this module from the *base* checkout, never the PR
head, so a PR cannot rewrite the gate and then pass it.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from tools.chiefs_narrative import config

REVIEW_SIGNOFF_REQUIRED = True
SIGNOFF_DIR = "data/review_signoff"
PUBLIC_KEY_REL = "scripts/review_signoff_pubkey.pem"
SIGNOFF_SLUG_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{4}$")
EDITION_NAMES = frozenset(
    {
        "data/narrative.json",
        "data/narrative_archive.json",
    }
)
_REVIEW_EDITION = re.compile(
    r"Week\s+\d+\s+·\s+Week\s+\d+\s+Review",
    re.IGNORECASE,
)
HUMAN_MERGE_PATHS = frozenset(
    {
        "tools/chiefs_narrative/review_gate.py",
        "scripts/review_signoff_pubkey.pem",
        "CODEOWNERS",
        ".github/CODEOWNERS",
    }
)
HUMAN_MERGE_PREFIXES = (
    "data/review_signoff/",
    ".github/workflows/",
)


class GateError(RuntimeError):
    """Fail closed: a read or git diff failed, so the gate must block."""


def edition_path(path: str | Path) -> bool:
    text = str(path).replace("\\", "/")
    if text in EDITION_NAMES:
        return True
    return text.startswith("data/narrative_editions/") and text.endswith(".json")


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


def payload_is_review(payload: dict | None) -> bool:
    """True for phase.mode=review or a 'Week N · Week M Review' edition.

    Duplicated from facts.is_review_edition so automerge/pages can run
    this module without importing facts (and therefore requests).
    """
    if not payload:
        return False
    phase = payload.get("phase") if isinstance(payload.get("phase"), dict) else {}
    if str((phase or {}).get("mode") or "").strip().lower() == "review":
        return True
    edition = str(
        payload.get("edition") or (phase or {}).get("edition") or ""
    )
    return bool(_REVIEW_EDITION.search(edition))


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
    return not review_signed_off(payload, root=root, edition_file=edition_file)


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


def requires_human_merge(paths) -> bool:
    """PRs that touch the gate itself must not automerge."""
    for raw in paths or []:
        rel = str(raw).replace("\\", "/")
        if rel.startswith("./"):
            rel = rel[2:]
        if rel in HUMAN_MERGE_PATHS:
            return True
        if any(rel.startswith(prefix) for prefix in HUMAN_MERGE_PREFIXES):
            return True
    return False


def automerge_blocked(paths, *, root: Path | None = None) -> bool:
    """True when the PR needs a human, or a changed Review is unsigned."""
    if requires_human_merge(paths):
        return True
    base = Path(root) if root is not None else config.REPO_ROOT
    for rel, payload in changed_review_payloads(paths, root=base):
        if should_block_review(payload, root=base, edition_file=base / rel):
            return True
    return False


def pages_blocked(paths=None, *, root: Path | None = None) -> bool:
    """True when the edition currently published on main is an unsigned Review.

    Always inspects ``data/narrative.json`` — the file the site builds
    from — even when the push only touched README or the schedule. A
    two-commit push, a later slate refresh, and Trigger Pages deploy
    must all keep refusing until Karen signs.
    """
    base = Path(root) if root is not None else config.REPO_ROOT
    current_path = base / "data" / "narrative.json"
    if not current_path.is_file():
        return True
    current = load_payload(current_path)
    if should_block_review(current, root=base, edition_file=current_path):
        return True
    for rel, payload in changed_review_payloads(paths, root=base):
        if should_block_review(payload, root=base, edition_file=base / rel):
            return True
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
                    "or this PR touches the gate and needs a human merge",
                    file=sys.stderr,
                )
                return 1
            return 0
        if args.pages:
            if not paths:
                paths = _git_changed_from_parent(root)
            if pages_blocked(paths, root=root):
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
