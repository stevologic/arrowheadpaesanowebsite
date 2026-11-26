"""Throwaway git origins and CLI runners for FROZEN-38 promote automerge."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import yaml

from tools.chiefs_narrative import review_gate

REPO = Path(__file__).resolve().parents[2]
SLUG = "2026-11-27-0948"
HEAD_REF = f"promote/{SLUG}"
SVG_NAMES = (
    "xo-cover_two.svg",
    "xo-inside_zone.svg",
    "xo-mesh.svg",
    "xo-play_action_boot.svg",
    "xo-spacing_screen.svg",
    "xo-zone_blitz.svg",
)
TINY_SVG = b'<svg xmlns="http://www.w3.org/2000/svg"/>\n'
RULESET_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "frozen38_ruleset.json"


def git(args: list[str], cwd: Path, **kwargs) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.setdefault("GIT_AUTHOR_NAME", "Stephen M Abbott")
    env.setdefault("GIT_AUTHOR_EMAIL", "test@example.com")
    env.setdefault("GIT_COMMITTER_NAME", "Stephen M Abbott")
    env.setdefault("GIT_COMMITTER_EMAIL", "test@example.com")
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    kwargs.setdefault("check", True)
    kwargs.setdefault("env", env)
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        **kwargs,
    )


def _git_ident(cwd: Path) -> None:
    git(["config", "user.name", "Stephen M Abbott"], cwd)
    git(["config", "user.email", "test@example.com"], cwd)
    git(["config", "commit.gpgsign", "false"], cwd)


def preview_payload(slug: str = SLUG, **extra) -> dict:
    payload = {
        "slug": slug,
        "edition": "2026 Week 12 · Preview",
        "phase": {"mode": "preview", "week": 12, "label": "Week 12"},
        "headline": "Preview headline",
        "lastGame": {"completed": True, "kcScore": 34, "oppScore": 7},
    }
    payload.update(extra)
    return payload


def review_payload(slug: str = SLUG, **extra) -> dict:
    payload = {
        "slug": slug,
        "edition": "2026 Week 12 · Review",
        "phase": {"mode": "review", "week": 12, "label": "Week 12"},
        "headline": "Review headline",
        "lastGame": {"completed": True, "kcScore": 34, "oppScore": 7},
    }
    payload.update(extra)
    return payload


def dumps(payload: dict) -> bytes:
    return (json.dumps(payload, indent=2) + "\n").encode("utf-8")


def repair_bytes(*, hold: bool = True) -> bytes:
    return dumps(
        {
            "droppedSentences": [],
            "correctedSentences": [],
            "leftoverIssues": [],
            "holdOnlySentinels": [],
            "headlineOrLedeTouched": False,
            "wordShortfall": 0,
            "holdAutomerge": hold,
        }
    )


def sign_pair(priv: Path, slug: str, edition_bytes: bytes) -> tuple[bytes, bytes]:
    sha = hashlib.sha256(edition_bytes).hexdigest()
    message = review_gate.signoff_canonical_bytes(slug, sha)
    sigbin = Path(tempfile.mkdtemp()) / "sigbin"
    msg = Path(tempfile.mkdtemp()) / "msg"
    msg.write_bytes(message)
    subprocess.check_call(
        [
            "openssl",
            "pkeyutl",
            "-sign",
            "-inkey",
            str(priv),
            "-rawin",
            "-in",
            str(msg),
            "-out",
            str(sigbin),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    import base64

    sig = base64.b64encode(sigbin.read_bytes()) + b"\n"
    return message, sig


def ed25519_keypair(tmp: Path) -> tuple[Path, Path]:
    priv = tmp / "priv.pem"
    pub = tmp / "pub.pem"
    subprocess.check_call(
        ["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(priv)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.check_call(
        ["openssl", "pkey", "-in", str(priv), "-pubout", "-out", str(pub)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return priv, pub


def edition_files(
    slug: str,
    payload: dict,
    *,
    schedule: bool = False,
    svgs: int = 6,
    pair: tuple[bytes, bytes] | None = None,
    hold: bool = True,
) -> dict[str, bytes]:
    raw = dumps(payload)
    files = {
        "data/narrative.json": raw,
        "data/narrative_archive.json": b"[]\n",
        "data/narrative_repair.json": repair_bytes(hold=hold),
        "data/wire.json": b"{}\n",
        f"data/narrative_editions/{slug}.json": raw,
    }
    if schedule:
        files["data/schedule_2026.json"] = b"[]\n"
    for name in SVG_NAMES[:svgs]:
        files[f"public/images/narrative/{slug}/{name}"] = TINY_SVG
    if pair is not None:
        files[f"signoff/review/{slug}.json"] = pair[0]
        files[f"signoff/review/{slug}.sig"] = pair[1]
    return files


def _copy_gate_tree(dest: Path) -> None:
    pkg = dest / "tools" / "chiefs_narrative"
    pkg.mkdir(parents=True, exist_ok=True)
    src = REPO / "tools" / "chiefs_narrative"
    for name in ("__init__.py", "config.py", "review_gate.py"):
        shutil.copy2(src / name, pkg / name)
    scripts = dest / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPO / "scripts" / "review_signoff_pubkey.pem", scripts / "review_signoff_pubkey.pem")
    (dest / "README.md").write_text("seed\n", encoding="utf-8")
    (dest / "data").mkdir(exist_ok=True)
    (dest / "data" / "narrative.json").write_bytes(
        dumps(preview_payload("2026-11-01-0900"))
    )


@dataclass
class PromoRepo:
    tmp: Path
    origin: Path
    main_tree: Path
    pr_tree: Path
    develop_sha: str
    head_sha: str
    head_ref: str
    slug: str
    files: dict[str, bytes] = field(default_factory=dict)


def build_promo(
    tmp: Path,
    files: dict[str, bytes],
    *,
    slug: str = SLUG,
    head_ref: str | None = None,
    main_files: dict[str, bytes] | None = None,
    extra_develop: dict[str, bytes] | None = None,
) -> PromoRepo:
    """Bare origin with main+develop; one promote commit on main's tip."""
    head_ref = head_ref or f"promote/{slug}"
    tmp.mkdir(parents=True, exist_ok=True)
    origin = tmp / "origin.git"
    git(["init", "--bare", str(origin)], cwd=tmp)
    seed = tmp / "seed"
    seed.mkdir()
    git(["init"], cwd=seed)
    _git_ident(seed)
    git(["remote", "add", "origin", str(origin)], cwd=seed)
    _copy_gate_tree(seed)
    if main_files:
        for rel, body in main_files.items():
            path = seed / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
    git(["add", "-A"], cwd=seed)
    git(["commit", "-m", "main seed"], cwd=seed)
    git(["branch", "-M", "main"], cwd=seed)
    git(["push", "-u", "origin", "main"], cwd=seed)

    git(["checkout", "-B", "develop"], cwd=seed)
    for rel, body in files.items():
        path = seed / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    if extra_develop:
        for rel, body in extra_develop.items():
            path = seed / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
    git(["add", "-A"], cwd=seed)
    git(["commit", "-m", "develop edition"], cwd=seed)
    git(["push", "-u", "origin", "develop"], cwd=seed)
    develop_sha = git(["rev-parse", "HEAD"], cwd=seed).stdout.strip()

    git(["fetch", "origin", "main"], cwd=seed)
    git(["checkout", "-B", head_ref, "origin/main"], cwd=seed)
    for rel, body in files.items():
        path = seed / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    git(["add", "-A"], cwd=seed)
    git(["commit", "-m", f"Promote {slug}"], cwd=seed)
    git(["push", "-u", "origin", head_ref], cwd=seed)
    head_sha = git(["rev-parse", "HEAD"], cwd=seed).stdout.strip()

    main_tree = tmp / "main-tree"
    pr_tree = tmp / "pr-tree"
    git(["clone", "--branch", "main", str(origin), str(main_tree)], cwd=tmp)
    git(["clone", "--branch", head_ref, str(origin), str(pr_tree)], cwd=tmp)
    _git_ident(main_tree)
    _git_ident(pr_tree)
    git(["fetch", "origin", "develop"], cwd=pr_tree)
    git(["fetch", "origin", "main"], cwd=pr_tree)
    git(["fetch", "origin", "develop"], cwd=main_tree)
    return PromoRepo(
        tmp=tmp,
        origin=origin,
        main_tree=main_tree,
        pr_tree=pr_tree,
        develop_sha=develop_sha,
        head_sha=head_sha,
        head_ref=head_ref,
        slug=slug,
        files=files,
    )


def run_gate(
    promo: PromoRepo,
    *,
    head_ref: str | None = None,
    fetch_develop: bool = True,
    extra: list[str] | None = None,
    env: dict[str, str] | None = None,
    head: str = "HEAD",
    develop_ref: str | None = "origin/develop",
    base: str = "origin/main",
) -> subprocess.CompletedProcess:
    cmd = [
        sys.executable,
        "-P",
        "-m",
        "tools.chiefs_narrative.review_gate",
        "--automerge",
        "--base",
        base,
        "--head",
        head,
        "--head-ref",
        head_ref if head_ref is not None else promo.head_ref,
        "--root",
        str(promo.pr_tree),
    ]
    if develop_ref:
        cmd.extend(["--develop-ref", develop_ref])
    if fetch_develop:
        cmd.append("--fetch-develop")
    if extra:
        cmd.extend(extra)
    merged = {
        **os.environ,
        "PYTHONPATH": str(promo.main_tree),
        "GITHUB_WORKSPACE": str(promo.pr_tree),
    }
    if env:
        merged.update(env)
    return subprocess.run(
        cmd,
        cwd=promo.main_tree,
        capture_output=True,
        text=True,
        env=merged,
    )


def run_hold_override(
    promo: PromoRepo,
    *,
    pr_body: str,
    repository: str = "stevologic/arrowheadpaesanowebsite",
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    cmd = [
        sys.executable,
        "-P",
        "-m",
        "tools.chiefs_narrative.review_gate",
        "--hold-override",
        "--head",
        "HEAD",
        "--develop-ref",
        "origin/develop",
        "--pr-body",
        pr_body,
        "--repository",
        repository,
        "--root",
        str(promo.pr_tree),
    ]
    merged = {**os.environ, "PYTHONPATH": str(promo.main_tree)}
    if env:
        merged.update(env)
    return subprocess.run(
        cmd,
        cwd=promo.main_tree,
        capture_output=True,
        text=True,
        env=merged,
    )


def load_workflow(name: str, root: Path | None = None) -> dict:
    base = (root or REPO) / ".github" / "workflows" / name
    return yaml.load(base.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def step_run(workflow: dict, job: str, name: str) -> str:
    for step in workflow["jobs"][job]["steps"]:
        if isinstance(step, dict) and step.get("name") == name:
            return str(step.get("run") or "")
    raise AssertionError(f"{job} step {name!r} missing")


def step_env(workflow: dict, job: str, name: str) -> dict:
    for step in workflow["jobs"][job]["steps"]:
        if isinstance(step, dict) and step.get("name") == name:
            return dict(step.get("env") or {})
    return {}


def evaluate_if(expression: str, github, *, needs=None, labels=None) -> bool:
    """Table-driven GitHub `if:` evaluator (FROZEN-34 B1 precedent)."""
    expr = expression.strip()
    if expr.startswith("${{") and expr.endswith("}}"):
        expr = expr[3:-2].strip()
    if needs:
        gates = str(needs.get("gates") or "")
        if gates and gates != "success":
            if "always()" not in expr and "!cancelled()" not in expr:
                return False
    expr = expr.replace("github.event.pull_request.labels.*.name", "labels")
    expr = expr.replace("&&", " and ").replace("||", " or ")
    expr = re.sub(r"!(?!=)", "not ", expr)
    expr = re.sub(r"\bfalse\b", "False", expr)
    expr = re.sub(r"\btrue\b", "True", expr)
    functions = {
        "__builtins__": {},
        "fromJSON": json.loads,
        "contains": lambda items, value: value in items,
        "startsWith": lambda value, prefix: str(value).startswith(prefix),
        "always": lambda: True,
    }
    return bool(
        eval(
            " ".join(expr.split()),
            functions,
            {"github": github, "labels": labels or [], "needs": needs or {}},
        )
    )


def github_ns(
    *,
    event_name="pull_request",
    base_ref="main",
    head_ref=HEAD_REF,
    actor="stevologic",
    author="stevologic",
    draft=False,
    labels=None,
    association="OWNER",
    head_repo="stevologic/arrowheadpaesanowebsite",
    repository="stevologic/arrowheadpaesanowebsite",
):
    pr = SimpleNamespace(
        user=SimpleNamespace(login=author),
        draft=draft,
        author_association=association,
        head=SimpleNamespace(repo=SimpleNamespace(full_name=head_repo)),
        labels=[{"name": item} for item in (labels or [])],
    )
    return SimpleNamespace(
        base_ref=base_ref,
        actor=actor,
        event_name=event_name,
        head_ref=head_ref,
        repository=repository,
        event=SimpleNamespace(pull_request=pr),
    )


def write_gh_stub(path: Path, log: Path, *, merge_ok=True, pr_json=None, fail_api=False) -> None:
    pr_json = pr_json or {"draft": False, "labels": []}
    payload = json.dumps(pr_json)
    script = f"""#!/bin/bash
set -euo pipefail
echo "$0 $*" >> "{log}"
args="$*"
if [[ "$args" == *"--auto"* ]]; then
  echo "unexpected --auto" >&2
  exit 20
fi
if [[ "$args" == *"workflow run"* ]]; then
  echo "dispatched" >> "{log}.pages"
  exit 0
fi
if [[ "$args" == *"pr merge"* ]]; then
  if [ "{1 if merge_ok else 0}" = "1" ]; then
    echo "merged"
    echo "merge" >> "{log}.merge"
    exit 0
  fi
  echo "not merged" >&2
  exit 1
fi
if [[ "$args" == *"api"* ]]; then
  if [ "{1 if fail_api else 0}" = "1" ]; then
    echo "api fail" >&2
    exit 1
  fi
  printf '%s\\n' '{payload}'
  exit 0
fi
exit 0
"""
    path.write_text(script, encoding="utf-8")
    path.chmod(0o755)


def edition_qa_globs(root: Path | None = None) -> list[str]:
    doc = load_workflow("edition-qa.yml", root)
    on = doc.get("on") or doc.get(True) or {}
    prt = on.get("pull_request_target") or {}
    return list(prt.get("paths") or [])
