"""FROZEN rows R1–R7 and on-disk mutants M1/M2/M3/MW for automerge messages.

Splits the step-4 refusal so unsigned-Review sign-off and allowlist/extra-
publish print different text. Callers are the real ``python -P -m``
invocations from ci.yml, edition-qa.yml, and narrative.yml.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.chiefs_narrative import review_gate


def _fixture_repo() -> Path:
    """Real checkout that holds PR #363 blobs. Mutant copies of tools/ are not this."""
    override = (os.environ.get("REVIEW_GATE_FIXTURE_ROOT") or "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2]


def _ed25519_keypair(tmp: Path) -> tuple[Path, Path]:
    """Throwaway test keypair. Never committed."""
    priv = Path(tmp) / "test_ed25519_priv.pem"
    pub = Path(tmp) / "test_ed25519_pub.pem"
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


def _sign_review_edition(priv: Path, slug: str, edition_bytes: bytes, signoff_dir: Path) -> None:
    sha = hashlib.sha256(edition_bytes).hexdigest()
    payload = f'{{"slug":"{slug}","verdict":"PASS","sha":"{sha}"}}'
    signoff_dir.mkdir(parents=True, exist_ok=True)
    msg = signoff_dir / f"{slug}.msg"
    sigbin = signoff_dir / f"{slug}.sigbin"
    msg.write_bytes(payload.encode("ascii"))
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
    (signoff_dir / f"{slug}.json").write_bytes(payload.encode("ascii"))
    (signoff_dir / f"{slug}.sig").write_text(
        base64.b64encode(sigbin.read_bytes()).decode("ascii"),
        encoding="ascii",
    )
    msg.unlink()
    sigbin.unlink()


REPO = _fixture_repo()
MUTANT_DIR = Path(__file__).resolve().parent / "fixtures" / "review_gate_message_mutants"
CONTROL = (
    "tools.tests.test_review_gate_automerge_messages.ReviewGateAutomergeControl"
)
REPEAT = 6
FROZEN_IDS = ("R1", "R2", "R3", "R4", "R5a", "R5b", "R6", "R7", "M1", "M2", "M3", "MW")
RECEIPTS: dict[str, dict] = {}

SLUG_REVIEW = "2026-10-05-0937"
SLUG_OTHER = "2026-10-03-1538"
SLUG_363 = "2026-11-13-1727"
_LIVE = "narrative"
LIVE_JSON = "data/" + _LIVE + ".json"
ARCHIVE_JSON = "data/" + _LIVE + "_archive.json"
EDITIONS_DIR = "data/" + _LIVE + "_editions"
REPAIR_JSON = "data/" + _LIVE + "_repair.json"
REVIEW_REL = f"{EDITIONS_DIR}/{SLUG_REVIEW}.json"
PREVIEW_REL = f"{EDITIONS_DIR}/{SLUG_OTHER}.json"
EDITION_363 = f"{EDITIONS_DIR}/{SLUG_363}.json"
XO_363 = (
    "xo-cover_two.svg",
    "xo-four_verts.svg",
    "xo-inside_zone.svg",
    "xo-mesh.svg",
    "xo-spacing_screen.svg",
    "xo-zone_blitz.svg",
)
PR363_PATHS = (
    LIVE_JSON,
    ARCHIVE_JSON,
    EDITION_363,
    REPAIR_JSON,
    "data/wire.json",
    *[f"public/images/{_LIVE}/{SLUG_363}/{name}" for name in XO_363],
)
PR363_OFFENDING = (
    ARCHIVE_JSON,
    REPAIR_JSON,
    "data/wire.json",
    *[f"public/images/{_LIVE}/{SLUG_363}/{name}" for name in XO_363],
)
SIGNOFF_TEXT = (
    "Review edition requires Karen's ed25519 sign-off "
    f"({review_gate.SIGNOFF_DIR}/<slug>.json + <slug>.sig)"
)
ALLOWLIST_TEXT = "this PR is off the automerge allowlist"
COMBINED_TEXT = (
    "Review edition requires Karen's ed25519 sign-off "
    f"({review_gate.SIGNOFF_DIR}/<slug>.json + <slug>.sig), "
    "or this PR is off the automerge allowlist"
)
PAGES_TEXT = "skip: unsigned Review edition; not deploying to gh-pages"
CAP = review_gate.AUTOMERGE_REFUSAL_PATH_CAP


def _review_payload() -> dict:
    return {
        "slug": SLUG_REVIEW,
        "edition": "2026 Week 6 · Week 4 Review",
        "phase": {"mode": "review"},
    }


def _preview_payload() -> dict:
    return {
        "slug": SLUG_OTHER,
        "edition": "2026 Week 4 · Preview",
        "phase": {"mode": "preview"},
    }


def _preview_363() -> dict:
    return {
        "slug": SLUG_363,
        "edition": "2026 Week 10 · Preview",
        "phase": {"mode": "preview"},
    }


def _write_files(root: Path, files: dict[str, str | bytes]) -> None:
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(body, bytes):
            path.write_bytes(body)
        else:
            path.write_text(body, encoding="utf-8")


def _json_file(payload: dict) -> str:
    return json.dumps(payload) + "\n"


def _copy_pr363_files(root: Path) -> None:
    for rel in PR363_PATHS:
        src = REPO / rel
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
    edition = json.loads((root / EDITION_363).read_text(encoding="utf-8"))
    mode = str((edition.get("phase") or {}).get("mode") or "")
    if mode != "preview":
        raise AssertionError(f"PR363 edition mode is {mode!r}, not preview")


def _init_git(tree: Path) -> None:
    subprocess.check_call(
        ["git", "init"],
        cwd=tree,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.check_call(["git", "config", "user.email", "gate@test"], cwd=tree)
    subprocess.check_call(["git", "config", "user.name", "Gate Test"], cwd=tree)
    subprocess.check_call(["git", "config", "commit.gpgsign", "false"], cwd=tree)


def _git_commit(tree: Path, message: str) -> str:
    subprocess.check_call(
        ["git", "add", "-A"],
        cwd=tree,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.check_call(
        ["git", "commit", "-m", message],
        cwd=tree,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=tree, text=True
    ).strip()


def _run_gate(
    gate_base: Path,
    args: list[str],
    *,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    cmd = [
        sys.executable,
        "-P",
        "-m",
        "tools.chiefs_narrative.review_gate",
        *args,
    ]
    merged = {
        **os.environ,
        "PYTHONPATH": str(gate_base),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if env:
        merged.update(env)
    return subprocess.run(
        cmd,
        cwd=str(gate_base),
        env=merged,
        capture_output=True,
        text=True,
    )


def _loaded_gate_base() -> Path:
    """Checkout that provided the loaded review_gate (live tree or mutant copy)."""
    return Path(review_gate.__file__).resolve().parents[2]


def _run_automerge(
    root: Path,
    paths: list[str] | None = None,
    *,
    gate_base: Path | None = None,
    base: str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    args = ["--automerge", "--root", str(root)]
    if base:
        args.extend(["--base", base])
    if paths:
        args.append("--")
        args.extend(paths)
    return _run_gate(gate_base or _loaded_gate_base(), args, env=env)


def _record(rid: str, proc: subprocess.CompletedProcess[str], cmd: list[str]) -> None:
    blob = (proc.stderr or "") + (proc.stdout or "")
    RECEIPTS[rid] = {
        "id": rid,
        "command": subprocess.list2cmdline(cmd),
        "exit": proc.returncode,
        "last15": blob.splitlines()[-15:],
    }


def _legacy_automerge_blocked(paths, *, root: Path | None = None) -> bool:
    """Exact pre-split automerge_blocked body (bool only)."""
    if review_gate.requires_human_merge(paths, root=root):
        return True
    base = Path(root) if root is not None else review_gate.config.REPO_ROOT
    if review_gate.extra_publish_surface(base):
        return True
    for raw in paths or []:
        if review_gate.extra_publish_path(raw):
            return True
        rel = review_gate._posix_rel(raw)
        if rel == LIVE_JSON or review_gate._editions_json_path(rel):
            payload = review_gate.load_payload(base / rel)
            if review_gate.payload_is_review(payload):
                return True
    return False


def _stderr_lines(proc: subprocess.CompletedProcess[str]) -> list[str]:
    return (proc.stderr or "").splitlines()


def _assert_signoff_only(test: unittest.TestCase, proc: subprocess.CompletedProcess[str]) -> None:
    err = proc.stderr or ""
    test.assertEqual(proc.returncode, 1)
    test.assertIn(SIGNOFF_TEXT, err)
    test.assertNotIn(ALLOWLIST_TEXT, err)
    test.assertNotIn(COMBINED_TEXT, err)
    test.assertNotIn("or this PR is off the automerge allowlist", err)


def _assert_allowlist_paths(
    test: unittest.TestCase,
    proc: subprocess.CompletedProcess[str],
    offending: list[str] | tuple[str, ...],
) -> None:
    err = proc.stderr or ""
    lines = _stderr_lines(proc)
    test.assertEqual(proc.returncode, 1)
    test.assertIn(ALLOWLIST_TEXT, err)
    test.assertNotIn(SIGNOFF_TEXT, err)
    test.assertNotIn(COMBINED_TEXT, err)
    test.assertNotIn("or this PR is off the automerge allowlist", err)
    for rel in offending:
        test.assertIn(json.dumps(rel, ensure_ascii=True), lines)


def _assert_both(
    test: unittest.TestCase,
    proc: subprocess.CompletedProcess[str],
    offending: list[str] | tuple[str, ...],
) -> None:
    err = proc.stderr or ""
    lines = _stderr_lines(proc)
    test.assertEqual(proc.returncode, 1)
    test.assertIn(SIGNOFF_TEXT, err)
    test.assertIn(ALLOWLIST_TEXT, err)
    test.assertNotIn(COMBINED_TEXT, err)
    signoff_at = next(i for i, line in enumerate(lines) if line == SIGNOFF_TEXT)
    allow_at = next(i for i, line in enumerate(lines) if line == ALLOWLIST_TEXT)
    test.assertLess(signoff_at, allow_at)
    for rel in offending:
        test.assertIn(json.dumps(rel, ensure_ascii=True), lines)


class ReviewGateAutomergeControl(unittest.TestCase):
    """R1, R2, R4, R5a, R5b, R6 — the six behavioural message tests."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._key_tmp = tempfile.TemporaryDirectory()
        cls.priv, cls.pub = _ed25519_keypair(Path(cls._key_tmp.name))
        cls.pub_env = {"REVIEW_SIGNOFF_PUBKEY": str(cls.pub)}

    @classmethod
    def tearDownClass(cls) -> None:
        cls._key_tmp.cleanup()

    def test_r1_unsigned_review_signoff_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_files(root, {REVIEW_REL: _json_file(_review_payload())})
            proc = _run_automerge(root, [REVIEW_REL])
            _record("R1", proc, ["python", "-P", "-m", "tools.chiefs_narrative.review_gate", "--automerge", "--root", str(root), "--", REVIEW_REL])
            _assert_signoff_only(self, proc)
            self.assertEqual(
                review_gate.automerge_blocked([REVIEW_REL], root=root),
                _legacy_automerge_blocked([REVIEW_REL], root=root),
            )
            self.assertTrue(review_gate.automerge_blocked([REVIEW_REL], root=root))

    def test_r2_pr363_preview_allowlist_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_pr363_files(root)
            live = json.loads((root / LIVE_JSON).read_text(encoding="utf-8"))
            self.assertEqual((live.get("phase") or {}).get("mode"), "preview")
            proc = _run_automerge(root, list(PR363_PATHS))
            _record("R2", proc, ["python", "-P", "-m", "tools.chiefs_narrative.review_gate", "--automerge", "--root", str(root), "--", *PR363_PATHS])
            _assert_allowlist_paths(self, proc, PR363_OFFENDING)
            for allowed in (LIVE_JSON, EDITION_363):
                self.assertNotIn(json.dumps(allowed, ensure_ascii=True), _stderr_lines(proc))
            self.assertEqual(
                review_gate.automerge_blocked(list(PR363_PATHS), root=root),
                _legacy_automerge_blocked(list(PR363_PATHS), root=root),
            )

    def test_r4_both_causes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_files(
                root,
                {
                    REVIEW_REL: _json_file(_review_payload()),
                    "README.md": "off allowlist\n",
                },
            )
            paths = [REVIEW_REL, "README.md"]
            proc = _run_automerge(root, paths)
            _record("R4", proc, ["python", "-P", "-m", "tools.chiefs_narrative.review_gate", "--automerge", "--root", str(root), "--", *paths])
            _assert_both(self, proc, ["README.md"])
            self.assertEqual(
                review_gate.automerge_blocked(paths, root=root),
                _legacy_automerge_blocked(paths, root=root),
            )

    def test_r5a_bad_signature_signoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            body = _json_file(_review_payload())
            _write_files(root, {REVIEW_REL: body})
            signoff = root / review_gate.SIGNOFF_DIR
            signoff.mkdir(parents=True)
            (signoff / f"{SLUG_REVIEW}.json").write_bytes(
                review_gate.signoff_canonical_bytes(
                    SLUG_REVIEW, hashlib.sha256(body.encode("utf-8")).hexdigest()
                )
            )
            (signoff / f"{SLUG_REVIEW}.sig").write_text(
                "not-a-signature\n", encoding="ascii"
            )
            proc = _run_automerge(root, [REVIEW_REL], env=self.pub_env)
            _record("R5a", proc, ["python", "-P", "-m", "tools.chiefs_narrative.review_gate", "--automerge", "--root", str(root), "--", REVIEW_REL])
            _assert_signoff_only(self, proc)

    def test_r5b_wrong_slug_signoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            body = _json_file(_review_payload())
            _write_files(root, {REVIEW_REL: body})
            other = review_gate.signoff_canonical_bytes(
                SLUG_OTHER, hashlib.sha256(body.encode("utf-8")).hexdigest()
            )
            signoff = root / review_gate.SIGNOFF_DIR
            signoff.mkdir(parents=True)
            (signoff / f"{SLUG_REVIEW}.json").write_bytes(other)
            _sign_review_edition(self.priv, SLUG_OTHER, body.encode("utf-8"), signoff)
            (signoff / f"{SLUG_REVIEW}.sig").write_bytes(
                (signoff / f"{SLUG_OTHER}.sig").read_bytes()
            )
            proc = _run_automerge(root, [REVIEW_REL], env=self.pub_env)
            _record("R5b", proc, ["python", "-P", "-m", "tools.chiefs_narrative.review_gate", "--automerge", "--root", str(root), "--", REVIEW_REL])
            _assert_signoff_only(self, proc)

    def test_r6_untrusted_paths_escaped_and_capped(self) -> None:
        newline_path = "evil\n::error::x"
        leading = "::error::workflow"
        ansi = "public/\x1b[31mred.svg"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_files(
                root,
                {
                    PREVIEW_REL: _json_file(_preview_payload()),
                    "README.md": "x\n",
                },
            )
            evil = [newline_path, leading, ansi]
            proc = _run_automerge(root, evil)
            _record("R6", proc, ["python", "-P", "-m", "tools.chiefs_narrative.review_gate", "--automerge", "--root", str(root), "--", *evil])
            err = proc.stderr or ""
            self.assertEqual(proc.returncode, 1)
            self.assertIn(ALLOWLIST_TEXT, err)
            self.assertNotIn(SIGNOFF_TEXT, err)
            for rel in evil:
                self.assertIn(json.dumps(rel, ensure_ascii=True), _stderr_lines(proc))
            self.assertNotRegex(err, r"(?m)^::")
            self.assertNotIn("\n::error::", err)
            self.assertNotIn("\x1b", err)
            self.assertNotIn("\033", err)
            for line in _stderr_lines(proc):
                if line in {ALLOWLIST_TEXT, SIGNOFF_TEXT} or line.startswith("(+"):
                    continue
                self.assertTrue(line.startswith('"'), line)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            n_paths = [f"off/{i:02d}.txt" for i in range(CAP)]
            proc_n = _run_automerge(root, n_paths)
            lines_n = _stderr_lines(proc_n)
            self.assertEqual(proc_n.returncode, 1)
            for rel in n_paths:
                self.assertIn(json.dumps(rel, ensure_ascii=True), lines_n)
            self.assertNotIn("(+1 more)", "\n".join(lines_n))
            n1_paths = n_paths + [f"off/{CAP:02d}.txt"]
            proc_n1 = _run_automerge(root, n1_paths)
            lines_n1 = _stderr_lines(proc_n1)
            self.assertEqual(proc_n1.returncode, 1)
            for rel in n_paths:
                self.assertIn(json.dumps(rel, ensure_ascii=True), lines_n1)
            self.assertNotIn(json.dumps(n1_paths[-1], ensure_ascii=True), lines_n1)
            self.assertIn("(+1 more)", lines_n1)
            self.assertEqual(sum(1 for line in lines_n1 if line.startswith('"')), CAP)


class ReviewGateAutomergeRows(unittest.TestCase):
    """R3, R7, real callers, wrapper, --pages same-defect check."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._key_tmp = tempfile.TemporaryDirectory()
        cls.priv, cls.pub = _ed25519_keypair(Path(cls._key_tmp.name))
        cls.pub_env = {"REVIEW_SIGNOFF_PUBKEY": str(cls.pub)}

    @classmethod
    def tearDownClass(cls) -> None:
        cls._key_tmp.cleanup()

    def test_r3_allowlisted_signed_review_neither_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            body = _json_file(_review_payload())
            _write_files(root, {REVIEW_REL: body})
            _sign_review_edition(
                self.priv, SLUG_REVIEW, body.encode("utf-8"), root / review_gate.SIGNOFF_DIR
            )
            proc = _run_automerge(root, [REVIEW_REL], env=self.pub_env)
            _record("R3", proc, ["python", "-P", "-m", "tools.chiefs_narrative.review_gate", "--automerge", "--root", str(root), "--", REVIEW_REL])
            err = proc.stderr or ""
            self.assertNotIn(SIGNOFF_TEXT, err)
            self.assertNotIn(ALLOWLIST_TEXT, err)
            self.assertNotIn(COMBINED_TEXT, err)
            self.assertEqual(proc.returncode, 1)
            self.assertTrue(review_gate.automerge_blocked([REVIEW_REL], root=root))
            self.assertEqual(
                review_gate.automerge_blocked([REVIEW_REL], root=root),
                _legacy_automerge_blocked([REVIEW_REL], root=root),
            )

    def test_r7_exit_codes_match_develop_and_wrapper(self) -> None:
        rows: list[tuple[str, int, int, bool, bool]] = []
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            shutil.copytree(
                REPO / "tools",
                dest / "tools",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".mypy_cache"),
            )
            base_src = subprocess.check_output(
                ["git", "show", "origin/develop:tools/chiefs_narrative/review_gate.py"],
                cwd=REPO,
            )
            (dest / "tools" / "chiefs_narrative" / "review_gate.py").write_bytes(base_src)

            cases_dir = dest / "cases"
            cases: list[tuple[str, Path, list[str], dict[str, str] | None]] = []

            r1 = cases_dir / "R1"
            _write_files(r1, {REVIEW_REL: _json_file(_review_payload())})
            cases.append(("R1", r1, [REVIEW_REL], None))

            r2 = cases_dir / "R2"
            _copy_pr363_files(r2)
            cases.append(("R2", r2, list(PR363_PATHS), None))

            r3 = cases_dir / "R3"
            body = _json_file(_review_payload())
            _write_files(r3, {REVIEW_REL: body})
            _sign_review_edition(
                self.priv, SLUG_REVIEW, body.encode("utf-8"), r3 / review_gate.SIGNOFF_DIR
            )
            cases.append(("R3", r3, [REVIEW_REL], self.pub_env))

            r4 = cases_dir / "R4"
            _write_files(
                r4,
                {REVIEW_REL: _json_file(_review_payload()), "README.md": "x\n"},
            )
            cases.append(("R4", r4, [REVIEW_REL, "README.md"], None))

            r5a = cases_dir / "R5a"
            body = _json_file(_review_payload())
            _write_files(r5a, {REVIEW_REL: body})
            signoff = r5a / review_gate.SIGNOFF_DIR
            signoff.mkdir(parents=True)
            (signoff / f"{SLUG_REVIEW}.json").write_bytes(
                review_gate.signoff_canonical_bytes(
                    SLUG_REVIEW, hashlib.sha256(body.encode("utf-8")).hexdigest()
                )
            )
            (signoff / f"{SLUG_REVIEW}.sig").write_text("not-a-signature\n", encoding="ascii")
            cases.append(("R5a", r5a, [REVIEW_REL], self.pub_env))

            r5b = cases_dir / "R5b"
            body = _json_file(_review_payload())
            _write_files(r5b, {REVIEW_REL: body})
            other = review_gate.signoff_canonical_bytes(
                SLUG_OTHER, hashlib.sha256(body.encode("utf-8")).hexdigest()
            )
            signoff = r5b / review_gate.SIGNOFF_DIR
            signoff.mkdir(parents=True)
            (signoff / f"{SLUG_REVIEW}.json").write_bytes(other)
            _sign_review_edition(self.priv, SLUG_OTHER, body.encode("utf-8"), signoff)
            (signoff / f"{SLUG_REVIEW}.sig").write_bytes(
                (signoff / f"{SLUG_OTHER}.sig").read_bytes()
            )
            cases.append(("R5b", r5b, [REVIEW_REL], self.pub_env))

            r6 = cases_dir / "R6"
            evil = ["evil\n::error::x", "::error::workflow", "public/\x1b[31mred.svg"]
            _write_files(r6, {PREVIEW_REL: _json_file(_preview_payload())})
            cases.append(("R6", r6, evil, None))
            table_lines = ["row | base_exit | head_exit | base_blocked | head_blocked"]
            for rid, root, paths, env in cases:
                head = _run_automerge(root, paths, env=env)
                base = _run_automerge(root, paths, gate_base=dest, env=env)
                head_blocked = review_gate.automerge_blocked(paths, root=root)
                legacy_blocked = _legacy_automerge_blocked(paths, root=root)
                self.assertEqual(head.returncode, base.returncode, rid)
                self.assertEqual(head_blocked, legacy_blocked, rid)
                rows.append(
                    (rid, base.returncode, head.returncode, legacy_blocked, head_blocked)
                )
                table_lines.append(
                    f"{rid} | {base.returncode} | {head.returncode} | "
                    f"{legacy_blocked} | {head_blocked}"
                )
            blob = "\n".join(table_lines)
            RECEIPTS["R7"] = {
                "id": "R7",
                "command": "python -P -m tools.chiefs_narrative.review_gate --automerge --root <tree> -- <paths>  # base=origin/develop vs head",
                "exit": 0,
                "last15": blob.splitlines()[-15:],
            }
            self.assertEqual([row[1] for row in rows], [row[2] for row in rows])
            self.assertEqual([row[3] for row in rows], [row[4] for row in rows])

    def test_caller_ci_yml_automerge_base_root(self) -> None:
        script = (
            REPO / ".github" / "workflows" / "ci.yml"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "python -P -m tools.chiefs_narrative.review_gate --automerge",
            script,
        )
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            _init_git(tree)
            _write_files(tree, {"README.md": "base\n"})
            base = _git_commit(tree, "base")
            _copy_pr363_files(tree)
            _git_commit(tree, "head")
            proc = _run_automerge(tree, base=base)
            _record(
                "caller_ci",
                proc,
                [
                    "python",
                    "-P",
                    "-m",
                    "tools.chiefs_narrative.review_gate",
                    "--automerge",
                    "--base",
                    base,
                    "--root",
                    str(tree),
                ],
            )
            _assert_allowlist_paths(self, proc, PR363_OFFENDING)

    def test_caller_edition_qa_yml_automerge_base_root(self) -> None:
        script = (
            REPO / ".github" / "workflows" / "edition-qa.yml"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "python -P -m tools.chiefs_narrative.review_gate --automerge",
            script,
        )
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            _init_git(tree)
            _write_files(tree, {"README.md": "base\n"})
            base = _git_commit(tree, "base")
            _copy_pr363_files(tree)
            _git_commit(tree, "head")
            proc = _run_automerge(tree, base=base)
            _record(
                "caller_edition_qa",
                proc,
                [
                    "python",
                    "-P",
                    "-m",
                    "tools.chiefs_narrative.review_gate",
                    "--automerge",
                    "--base",
                    base,
                    "--root",
                    str(tree),
                ],
            )
            _assert_allowlist_paths(self, proc, PR363_OFFENDING)

    def test_caller_narrative_yml_pages_root(self) -> None:
        script = (
            REPO / ".github" / "workflows" / "narrative.yml"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "python -P -m tools.chiefs_narrative.review_gate --pages",
            script,
        )
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            _write_files(
                tree,
                {
                    LIVE_JSON: _json_file(_review_payload()),
                    REVIEW_REL: _json_file(_review_payload()),
                },
            )
            proc = _run_gate(
                REPO,
                ["--pages", "--root", str(tree)],
            )
            _record(
                "caller_narrative",
                proc,
                [
                    "python",
                    "-P",
                    "-m",
                    "tools.chiefs_narrative.review_gate",
                    "--pages",
                    "--root",
                    str(tree),
                ],
            )
            err = proc.stderr or ""
            self.assertEqual(proc.returncode, 2)
            self.assertIn(PAGES_TEXT, err)
            self.assertNotIn(COMBINED_TEXT, err)
            self.assertNotIn(ALLOWLIST_TEXT, err)

    def test_pages_mode_is_not_the_automerge_merged_message(self) -> None:
        src = (REPO / "tools" / "chiefs_narrative" / "review_gate.py").read_text(
            encoding="utf-8"
        )
        pages = src.split("if args.pages:", 1)[1].split("except GateError", 1)[0]
        self.assertIn(PAGES_TEXT, pages)
        self.assertNotIn("automerge allowlist", pages)
        self.assertNotIn(COMBINED_TEXT, pages)

def _killed_by_assertion(proc: subprocess.CompletedProcess[str]) -> bool:
    out = (proc.stderr or "") + (proc.stdout or "")
    if "AssertionError" not in out:
        return False
    if re.search(r"^ERROR: ", out, re.M):
        return False
    return proc.returncode != 0 and bool(re.search(r"^FAIL: ", out, re.M))


def _run_control(cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "unittest", CONTROL, "-q"],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
    )


class ReviewGateAutomergeMutants(unittest.TestCase):
    def test_mutants_killed_6x_vs_clean_control(self) -> None:
        live_root = Path(__file__).resolve().parents[2]
        env_live = {
            **os.environ,
            "PYTHONPATH": str(live_root),
            "PYTHONDONTWRITEBYTECODE": "1",
            "REVIEW_GATE_FIXTURE_ROOT": str(live_root),
        }
        control_pass = 0
        control_last = None
        for _ in range(REPEAT):
            control_last = _run_control(live_root, env_live)
            if control_last.returncode == 0:
                control_pass += 1
        self.assertEqual(control_pass, REPEAT, control_last.stderr if control_last else "")
        RECEIPTS["control"] = {
            "id": "control",
            "command": f"python -m unittest {CONTROL} -q  # {REPEAT}x live",
            "exit": 0,
            "last15": ((control_last.stderr or "") + (control_last.stdout or "")).splitlines()[-15:]
            if control_last
            else [],
        }
        for name in ("M1", "M2", "M3", "MW"):
            patch = MUTANT_DIR / f"{name}.patch"
            self.assertTrue(patch.is_file(), patch)
            kills = 0
            last = None
            details: list[str] = []
            for _ in range(REPEAT):
                with tempfile.TemporaryDirectory() as tmp:
                    dest = Path(tmp)
                    shutil.copytree(
                        REPO / "tools",
                        dest / "tools",
                        ignore=shutil.ignore_patterns(
                            "__pycache__", "*.pyc", ".mypy_cache"
                        ),
                    )
                    applied = subprocess.run(
                        ["patch", "-p1", "--forward", "--batch", "-i", str(patch)],
                        cwd=dest,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(
                        applied.returncode, 0, applied.stdout + applied.stderr
                    )
                    env = {
                        **os.environ,
                        "PYTHONPATH": str(dest),
                        "PYTHONDONTWRITEBYTECODE": "1",
                        "REVIEW_GATE_FIXTURE_ROOT": str(live_root),
                    }
                    last = _run_control(dest, env)
                    if _killed_by_assertion(last):
                        kills += 1
                    else:
                        details.append(
                            f"rc={last.returncode}\n{last.stdout}\n{last.stderr}"
                        )
            self.assertEqual(
                kills,
                REPEAT,
                f"{name} killed {kills}/{REPEAT}; not AssertionError FAIL:\n"
                + "\n".join(details),
            )
            blob = ((last.stderr or "") + (last.stdout or "")) if last else ""
            RECEIPTS[name] = {
                "id": name,
                "command": f"patch -p1 -i {patch.name} && python -m unittest {CONTROL} -q  # {REPEAT}x",
                "exit": last.returncode if last else -1,
                "last15": blob.splitlines()[-15:],
            }


class ReviewGateAutomergeZPreflight(unittest.TestCase):
    """Must run last so R1–R7 and M1/M2/M3/MW receipts are populated."""

    def test_z_preflight_frozen_ids(self) -> None:
        missing = [rid for rid in FROZEN_IDS if rid not in RECEIPTS]
        self.assertEqual(missing, [], f"missing frozen receipts: {missing}")
        for rid in FROZEN_IDS:
            row = RECEIPTS[rid]
            self.assertIn("command", row, rid)
            self.assertIn("exit", row, rid)
            self.assertIn("last15", row, rid)
            self.assertTrue(row["command"], rid)
            print(f"RECEIPT {rid} exit={row['exit']} cmd={row['command']}")
            for line in row["last15"]:
                print(f"  {line}")


if __name__ == "__main__":
    unittest.main()
