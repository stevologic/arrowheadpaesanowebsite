"""FROZEN automerge-message rows and on-disk mutants.

Splits the step-4 refusal so unsigned-Review sign-off, signed-Review human
merge, and allowlist/extra-publish print different text. Callers are the
real ``python -P -m`` invocations from ci.yml, edition-qa.yml, and
narrative.yml.
"""
from __future__ import annotations

import ast
import base64
import hashlib
import io
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
from tools.chiefs_narrative.edition_overlay import overlay_edition_data


def _checkout_root() -> Path:
    here = Path(__file__).resolve().parents[2]
    if (here / ".github" / "workflows").is_dir():
        return here
    env = (os.environ.get("REVIEW_GATE_CHECKOUT") or "").strip()
    if env:
        return Path(env)
    return here


REPO = _checkout_root()
TEST_DIR = Path(__file__).resolve().parent
FIXTURE_DIR = TEST_DIR / "fixtures" / "pr363_preview"
MUTANT_DIR = TEST_DIR / "fixtures" / "review_gate_message_mutants"
CONTROL = (
    "tools.tests.test_review_gate_automerge_messages.ReviewGateAutomergeControl"
)
REPEAT = 6
FROZEN_IDS = (
    "R1",
    "R2",
    "R3'",
    "R4",
    "R5a",
    "R5b",
    "R6",
    "R7",
    "R8",
    "R9",
    "R10",
    "caller_ci",
    "caller_edition_qa",
    "caller_narrative",
    "M1",
    "M2",
    "M3",
    "MW",
    "MW-false",
    "MC",
    "M-R3'",
)
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
SIGNED_TEXT = "signed Review edition: human merge required"
ALLOWLIST_TEXT = "this PR is off the automerge allowlist"
COMBINED_TEXT = (
    "Review edition requires Karen's ed25519 sign-off "
    f"({review_gate.SIGNOFF_DIR}/<slug>.json + <slug>.sig), "
    "or this PR is off the automerge allowlist"
)
PAGES_TEXT = "skip: unsigned Review edition; not deploying to gh-pages"
CAP = review_gate.AUTOMERGE_REFUSAL_PATH_CAP
MUTANT_PATCHES = (
    ("M1", "M1.patch"),
    ("M2", "M2.patch"),
    ("M3", "M3.patch"),
    ("MW", "MW.patch"),
    ("MW-false", "MW-false.patch"),
    ("MC", "MC.patch"),
    ("M-R3'", "M-R3.patch"),
)


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


def _fixture_file(rel: str) -> Path:
    src = (FIXTURE_DIR / rel).resolve()
    src.relative_to(FIXTURE_DIR.resolve())
    live_data = (REPO / "data").resolve()
    try:
        src.relative_to(live_data)
    except ValueError:
        return src
    raise AssertionError(f"fixture path resolved under live data/: {src}")


def _copy_pr363_files(root: Path) -> None:
    for rel in PR363_PATHS:
        src = _fixture_file(rel)
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
    edition = json.loads((root / EDITION_363).read_text(encoding="utf-8"))
    mode = str((edition.get("phase") or {}).get("mode") or "")
    if mode != "preview":
        raise AssertionError(f"PR363 edition mode is {mode!r}, not preview")
    live = json.loads((root / LIVE_JSON).read_text(encoding="utf-8"))
    live_mode = str((live.get("phase") or {}).get("mode") or "")
    if live_mode != "preview":
        raise AssertionError(f"PR363 live mode is {live_mode!r}, not preview")


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


def _gate_cmd(args: list[str]) -> list[str]:
    return ["python", "-P", "-m", "tools.chiefs_narrative.review_gate", *args]


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
    if paths is not None:
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


def _record_table(rid: str, command: str, lines: list[str], exit_code: int = 0) -> None:
    RECEIPTS[rid] = {
        "id": rid,
        "command": command,
        "exit": exit_code,
        "last15": lines[-15:],
    }


def _install_develop_gate(dest: Path) -> Path:
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
    return dest


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
    test.assertEqual(_stderr_lines(proc), [SIGNOFF_TEXT])
    test.assertNotIn(SIGNED_TEXT, err)
    test.assertNotIn(ALLOWLIST_TEXT, err)
    test.assertNotIn(COMBINED_TEXT, err)
    test.assertNotIn("or this PR is off the automerge allowlist", err)


def _assert_signed_only(test: unittest.TestCase, proc: subprocess.CompletedProcess[str]) -> None:
    err = proc.stderr or ""
    test.assertEqual(proc.returncode, 1)
    test.assertEqual(_stderr_lines(proc), [SIGNED_TEXT])
    test.assertNotIn(SIGNOFF_TEXT, err)
    test.assertNotIn(ALLOWLIST_TEXT, err)
    test.assertNotIn(COMBINED_TEXT, err)


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
    test.assertNotIn(SIGNED_TEXT, err)
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
    test.assertNotIn(SIGNED_TEXT, err)
    test.assertNotIn(COMBINED_TEXT, err)
    signoff_at = next(i for i, line in enumerate(lines) if line == SIGNOFF_TEXT)
    allow_at = next(i for i, line in enumerate(lines) if line == ALLOWLIST_TEXT)
    test.assertLess(signoff_at, allow_at)
    for rel in offending:
        test.assertIn(json.dumps(rel, ensure_ascii=True), lines)


def _expected_refusal_lines(paths, *, root: Path) -> list[str]:
    unsigned, signed = review_gate._automerge_review_flags(paths, root=root)
    lines: list[str] = []
    if unsigned:
        lines.append(SIGNOFF_TEXT)
    if signed:
        lines.append(SIGNED_TEXT)
    if review_gate._automerge_allowlist_or_extra(paths, root=root):
        lines.append(ALLOWLIST_TEXT)
        lines.extend(review_gate._automerge_path_refusal_lines(paths, root=root))
    return lines


def _assert_r8_row(
    test: unittest.TestCase,
    proc: subprocess.CompletedProcess[str],
    paths: list[str],
    root: Path,
) -> None:
    blocked = review_gate.automerge_blocked(paths, root=root)
    test.assertEqual(proc.returncode != 0, blocked)
    expected = _expected_refusal_lines(paths, root=root)
    lines = _stderr_lines(proc)
    if proc.returncode == 0:
        test.assertEqual(proc.returncode, 0)
        test.assertEqual(lines, [])
        test.assertEqual(expected, [])
        return
    test.assertEqual(proc.returncode, 1)
    test.assertTrue(lines, "exit 1 must print at least one refusal line")
    test.assertEqual(lines, expected)
    review_reason = False
    for raw in paths or []:
        if review_gate.automerge_review_edition_path(raw):
            payload = review_gate.load_payload(root / review_gate._posix_rel(raw))
            if review_gate.payload_is_review(payload):
                review_reason = True
    allow_reason = review_gate._automerge_allowlist_or_extra(paths, root=root)
    for line in lines:
        if line in {SIGNOFF_TEXT, SIGNED_TEXT}:
            test.assertTrue(review_reason, line)
        elif line == ALLOWLIST_TEXT:
            test.assertTrue(allow_reason, line)
        elif line.startswith("(+") and line.endswith("more)"):
            test.assertTrue(allow_reason, line)
        else:
            test.assertTrue(allow_reason, line)
            quoted = json.loads(line)
            test.assertTrue(
                (not review_gate.automerge_allowlisted(quoted))
                or review_gate.extra_publish_path(quoted),
                quoted,
            )


def _write_invalid_sig(root: Path, rel: str, body: str) -> None:
    signoff = root / review_gate.SIGNOFF_DIR
    signoff.mkdir(parents=True, exist_ok=True)
    slug = Path(rel).stem
    (signoff / f"{slug}.json").write_bytes(
        review_gate.signoff_canonical_bytes(
            slug, hashlib.sha256(body.encode("utf-8")).hexdigest()
        )
    )
    (signoff / f"{slug}.sig").write_text("not-a-signature\n", encoding="ascii")


class ReviewGateAutomergeControl(unittest.TestCase):
    """Behavioural message tests the on-disk mutants must die against."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._key_tmp = tempfile.TemporaryDirectory()
        cls.priv, cls.pub = _ed25519_keypair(Path(cls._key_tmp.name))
        cls.pub_env = {"REVIEW_SIGNOFF_PUBKEY": str(cls.pub)}
        cls._old_pub = os.environ.get("REVIEW_SIGNOFF_PUBKEY")
        os.environ["REVIEW_SIGNOFF_PUBKEY"] = str(cls.pub)

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._old_pub is None:
            os.environ.pop("REVIEW_SIGNOFF_PUBKEY", None)
        else:
            os.environ["REVIEW_SIGNOFF_PUBKEY"] = cls._old_pub
        cls._key_tmp.cleanup()

    def test_r1_unsigned_review_signoff_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_files(root, {REVIEW_REL: _json_file(_review_payload())})
            proc = _run_automerge(root, [REVIEW_REL])
            _record("R1", proc, _gate_cmd(["--automerge", "--root", str(root), "--", REVIEW_REL]))
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
            _record(
                "R2",
                proc,
                _gate_cmd(["--automerge", "--root", str(root), "--", *PR363_PATHS]),
            )
            _assert_allowlist_paths(self, proc, PR363_OFFENDING)
            for allowed in (LIVE_JSON, EDITION_363):
                self.assertNotIn(json.dumps(allowed, ensure_ascii=True), _stderr_lines(proc))
            self.assertEqual(
                review_gate.automerge_blocked(list(PR363_PATHS), root=root),
                _legacy_automerge_blocked(list(PR363_PATHS), root=root),
            )

    def test_r3p_signed_allowlisted_review_human_merge_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            body = _json_file(_review_payload())
            _write_files(root, {REVIEW_REL: body})
            _sign_review_edition(
                self.priv, SLUG_REVIEW, body.encode("utf-8"), root / review_gate.SIGNOFF_DIR
            )
            proc = _run_automerge(root, [REVIEW_REL], env=self.pub_env)
            _record(
                "R3'",
                proc,
                _gate_cmd(["--automerge", "--root", str(root), "--", REVIEW_REL]),
            )
            _assert_signed_only(self, proc)
            self.assertTrue(review_gate.automerge_blocked([REVIEW_REL], root=root))
            self.assertEqual(
                review_gate.automerge_blocked([REVIEW_REL], root=root),
                _legacy_automerge_blocked([REVIEW_REL], root=root),
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
            _record("R4", proc, _gate_cmd(["--automerge", "--root", str(root), "--", *paths]))
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
            _write_invalid_sig(root, REVIEW_REL, body)
            proc = _run_automerge(root, [REVIEW_REL], env=self.pub_env)
            _record("R5a", proc, _gate_cmd(["--automerge", "--root", str(root), "--", REVIEW_REL]))
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
            _record("R5b", proc, _gate_cmd(["--automerge", "--root", str(root), "--", REVIEW_REL]))
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
            _record("R6", proc, _gate_cmd(["--automerge", "--root", str(root), "--", *evil]))
            err = proc.stderr or ""
            self.assertEqual(proc.returncode, 1)
            self.assertIn(ALLOWLIST_TEXT, err)
            self.assertNotIn(SIGNOFF_TEXT, err)
            self.assertNotIn(SIGNED_TEXT, err)
            for rel in evil:
                self.assertIn(json.dumps(rel, ensure_ascii=True), _stderr_lines(proc))
            self.assertNotRegex(err, r"(?m)^::")
            self.assertNotIn("\n::error::", err)
            self.assertNotIn("\x1b", err)
            self.assertNotIn("\033", err)
            for line in _stderr_lines(proc):
                if line in {ALLOWLIST_TEXT, SIGNOFF_TEXT, SIGNED_TEXT} or line.startswith("(+"):
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

    def test_r9_archive_only_same_decision_and_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_files(root, {ARCHIVE_JSON: _json_file(_review_payload())})
            paths = [ARCHIVE_JSON]
            proc = _run_automerge(root, paths)
            _record("R9", proc, _gate_cmd(["--automerge", "--root", str(root), "--", *paths]))
            _assert_allowlist_paths(self, proc, paths)
            self.assertFalse(review_gate.automerge_review_edition_path(ARCHIVE_JSON))
            self.assertTrue(review_gate.edition_path(ARCHIVE_JSON))
            self.assertEqual(
                review_gate.automerge_blocked(paths, root=root),
                _legacy_automerge_blocked(paths, root=root),
            )
            self.assertEqual(
                review_gate.automerge_refusal_lines(paths, root=root),
                [ALLOWLIST_TEXT, json.dumps(ARCHIVE_JSON, ensure_ascii=True)],
            )


class ReviewGateAutomergeRows(unittest.TestCase):
    """R7–R10, real callers, --pages no-regression vs develop."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._key_tmp = tempfile.TemporaryDirectory()
        cls.priv, cls.pub = _ed25519_keypair(Path(cls._key_tmp.name))
        cls.pub_env = {"REVIEW_SIGNOFF_PUBKEY": str(cls.pub)}
        cls._old_pub = os.environ.get("REVIEW_SIGNOFF_PUBKEY")
        os.environ["REVIEW_SIGNOFF_PUBKEY"] = str(cls.pub)

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._old_pub is None:
            os.environ.pop("REVIEW_SIGNOFF_PUBKEY", None)
        else:
            os.environ["REVIEW_SIGNOFF_PUBKEY"] = cls._old_pub
        cls._key_tmp.cleanup()

    def test_r7_exit_codes_match_develop_and_wrapper(self) -> None:
        rows: list[tuple[str, int, int, bool, bool]] = []
        with tempfile.TemporaryDirectory() as tmp:
            dest = _install_develop_gate(Path(tmp) / "develop")
            cases_dir = Path(tmp) / "cases"
            cases: list[tuple[str, Path, list[str], dict[str, str] | None]] = []

            r1 = cases_dir / "R1"
            _write_files(r1, {REVIEW_REL: _json_file(_review_payload())})
            cases.append(("R1", r1, [REVIEW_REL], None))

            r2 = cases_dir / "R2"
            _copy_pr363_files(r2)
            cases.append(("R2", r2, list(PR363_PATHS), None))

            r3 = cases_dir / "R3p"
            body = _json_file(_review_payload())
            _write_files(r3, {REVIEW_REL: body})
            _sign_review_edition(
                self.priv, SLUG_REVIEW, body.encode("utf-8"), r3 / review_gate.SIGNOFF_DIR
            )
            cases.append(("R3'", r3, [REVIEW_REL], self.pub_env))

            r4 = cases_dir / "R4"
            _write_files(
                r4,
                {REVIEW_REL: _json_file(_review_payload()), "README.md": "x\n"},
            )
            cases.append(("R4", r4, [REVIEW_REL, "README.md"], None))

            r5a = cases_dir / "R5a"
            body = _json_file(_review_payload())
            _write_files(r5a, {REVIEW_REL: body})
            _write_invalid_sig(r5a, REVIEW_REL, body)
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
            _record_table(
                "R7",
                "python -P -m tools.chiefs_narrative.review_gate --automerge --root <tree> -- <paths>  # base=origin/develop vs head",
                table_lines,
            )
            self.assertEqual([row[1] for row in rows], [row[2] for row in rows])
            self.assertEqual([row[3] for row in rows], [row[4] for row in rows])

    def test_r8_invariant_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = _install_develop_gate(Path(tmp) / "develop")
            cases_dir = Path(tmp) / "cases"
            rows: list[
                tuple[str, list[str] | None, Path, dict[str, str] | None, str | None]
            ] = []

            unsigned = cases_dir / "unsigned"
            _write_files(unsigned, {REVIEW_REL: _json_file(_review_payload())})
            rows.append(("unsigned Review", [REVIEW_REL], unsigned, None, None))

            signed = cases_dir / "signed"
            body = _json_file(_review_payload())
            _write_files(signed, {REVIEW_REL: body})
            _sign_review_edition(
                self.priv, SLUG_REVIEW, body.encode("utf-8"), signed / review_gate.SIGNOFF_DIR
            )
            rows.append(("signed Review", [REVIEW_REL], signed, self.pub_env, None))

            invalid = cases_dir / "invalid"
            body = _json_file(_review_payload())
            _write_files(invalid, {REVIEW_REL: body})
            _write_invalid_sig(invalid, REVIEW_REL, body)
            rows.append(("present but invalid sig", [REVIEW_REL], invalid, self.pub_env, None))

            off = cases_dir / "off"
            _write_files(off, {"README.md": "x\n"})
            rows.append(("off-allowlist only", ["README.md"], off, None, None))

            off_unsigned = cases_dir / "off_unsigned"
            _write_files(
                off_unsigned,
                {REVIEW_REL: _json_file(_review_payload()), "README.md": "x\n"},
            )
            rows.append(
                (
                    "off-allowlist plus unsigned Review",
                    [REVIEW_REL, "README.md"],
                    off_unsigned,
                    None,
                    None,
                )
            )

            off_signed = cases_dir / "off_signed"
            body = _json_file(_review_payload())
            _write_files(off_signed, {REVIEW_REL: body, "README.md": "x\n"})
            _sign_review_edition(
                self.priv,
                SLUG_REVIEW,
                body.encode("utf-8"),
                off_signed / review_gate.SIGNOFF_DIR,
            )
            rows.append(
                (
                    "off-allowlist plus signed Review",
                    [REVIEW_REL, "README.md"],
                    off_signed,
                    self.pub_env,
                    None,
                )
            )

            off_invalid = cases_dir / "off_invalid"
            body = _json_file(_review_payload())
            _write_files(off_invalid, {REVIEW_REL: body, "README.md": "x\n"})
            _write_invalid_sig(off_invalid, REVIEW_REL, body)
            rows.append(
                (
                    "off-allowlist plus invalid sig",
                    [REVIEW_REL, "README.md"],
                    off_invalid,
                    self.pub_env,
                    None,
                )
            )

            archive = cases_dir / "archive"
            _write_files(archive, {ARCHIVE_JSON: _json_file(_review_payload())})
            rows.append(("archive only", [ARCHIVE_JSON], archive, None, None))

            archive_plus = cases_dir / "archive_plus"
            _write_files(
                archive_plus,
                {
                    ARCHIVE_JSON: _json_file(_review_payload()),
                    EDITION_363: _fixture_file(EDITION_363).read_text(encoding="utf-8"),
                },
            )
            rows.append(
                (
                    "archive plus allowlisted edition",
                    [ARCHIVE_JSON, EDITION_363],
                    archive_plus,
                    None,
                    None,
                )
            )

            preview = cases_dir / "preview"
            _write_files(
                preview,
                {LIVE_JSON: _fixture_file(LIVE_JSON).read_text(encoding="utf-8")},
            )
            rows.append(("Preview allowlisted", [LIVE_JSON], preview, None, None))

            empty = cases_dir / "empty"
            empty.mkdir(parents=True)
            _init_git(empty)
            _write_files(
                empty,
                {LIVE_JSON: _fixture_file(LIVE_JSON).read_text(encoding="utf-8")},
            )
            empty_base = _git_commit(empty, "base")
            subprocess.check_call(
                ["git", "commit", "--allow-empty", "-m", "unchanged"],
                cwd=empty,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            rows.append(("empty diff", None, empty, None, empty_base))

            table = ["case | base_exit | head_exit | refusal"]
            last_proc = None
            for name, paths, root, env, git_base in rows:
                head = _run_automerge(root, paths, base=git_base, env=env)
                base = _run_automerge(
                    root, paths, gate_base=dest, base=git_base, env=env
                )
                self.assertEqual(head.returncode, base.returncode, name)
                decided = [] if paths is None else paths
                _assert_r8_row(self, head, decided, root)
                last_proc = head
                refusal = " | ".join(_stderr_lines(head)) or "(empty)"
                table.append(f"{name} | {base.returncode} | {head.returncode} | {refusal}")
            _record(
                "R8",
                last_proc or subprocess.CompletedProcess([], 0, "", "\n".join(table)),
                _gate_cmd(["--automerge", "--root", "<tree>", "--", "<paths>"]),
            )
            RECEIPTS["R8"]["last15"] = table[-15:]
            RECEIPTS["R8"]["exit"] = 0
            RECEIPTS["R8"]["command"] = (
                "python -P -m tools.chiefs_narrative.review_gate --automerge --root <tree> -- <paths>  # R8 table vs origin/develop"
            )

    def test_r10_frozen_fixtures_only(self) -> None:
        src_path = Path(__file__).resolve()
        source = src_path.read_text(encoding="utf-8")
        live_data = (REPO / "data").resolve()
        fixture_data = (FIXTURE_DIR / "data").resolve()
        self.assertTrue(FIXTURE_DIR.is_dir(), FIXTURE_DIR)
        self.assertTrue(fixture_data.is_dir(), fixture_data)
        self.assertNotEqual(live_data, fixture_data)
        for rel in PR363_PATHS:
            path = _fixture_file(rel)
            self.assertTrue(path.is_file(), path)
            path.relative_to(FIXTURE_DIR.resolve())
        live = json.loads(_fixture_file(LIVE_JSON).read_text(encoding="utf-8"))
        edition = json.loads(_fixture_file(EDITION_363).read_text(encoding="utf-8"))
        self.assertEqual((live.get("phase") or {}).get("mode"), "preview")
        self.assertEqual((edition.get("phase") or {}).get("mode"), "preview")
        self.assertNotRegex(source, r"REPO\s*/\s*(LIVE_JSON|ARCHIVE_JSON|EDITION_363|REPAIR_JSON|rel)\b")
        self.assertNotRegex(source, r'REPO\s*/\s*["\']data/')
        self.assertNotRegex(source, r"parents\[2\]\s*/\s*['\"]data['\"]")
        tree = ast.parse(source)
        offenders: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value.replace("\\", "/")
                if text.startswith("data/") or text == "data":
                    continue
                resolved = None
                try:
                    cand = Path(text)
                    if cand.is_absolute():
                        resolved = cand.resolve()
                except OSError:
                    resolved = None
                if resolved is None:
                    continue
                try:
                    resolved.relative_to(live_data)
                except ValueError:
                    continue
                try:
                    resolved.relative_to(FIXTURE_DIR.resolve())
                except ValueError:
                    offenders.append(text)
        self.assertEqual(offenders, [], f"live data/ paths in tests: {offenders}")
        self.assertIn("pr363_preview", source)
        _record_table(
            "R10",
            "python -m unittest tools.tests.test_review_gate_automerge_messages.ReviewGateAutomergeRows.test_r10_frozen_fixtures_only",
            [
                "fixture_dir=" + str(FIXTURE_DIR),
                "live_mode=" + str((live.get("phase") or {}).get("mode")),
                "edition_mode=" + str((edition.get("phase") or {}).get("mode")),
                "offenders=" + str(offenders),
            ],
        )

    def test_caller_ci_yml_automerge_base_root(self) -> None:
        script = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn(
            "python -P -m tools.chiefs_narrative.review_gate --automerge",
            script,
        )
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            _init_git(tree)
            _write_files(tree, {"README.md": "base\n", "tools/keep.txt": "ci caller\n"})
            base = _git_commit(tree, "base")
            _copy_pr363_files(tree)
            _write_files(tree, {"README.md": "head also changes README\n"})
            _git_commit(tree, "head")
            proc = _run_automerge(tree, base=base)
            _record(
                "caller_ci",
                proc,
                _gate_cmd(["--automerge", "--base", base, "--root", str(tree)]),
            )
            _assert_allowlist_paths(self, proc, (*PR363_OFFENDING, "README.md"))

    def test_caller_edition_qa_yml_overlay_then_automerge(self) -> None:
        script = (REPO / ".github" / "workflows" / "edition-qa.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("edition_overlay", script)
        self.assertIn(
            "python -P -m tools.chiefs_narrative.review_gate --automerge",
            script,
        )
        self.assertIn("public/images/" + _LIVE + "/**", script)
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            pr_head = Path(tmp) / "pr-head"
            overlay_dst = Path(tmp) / "overlay-dst"
            repo.mkdir()
            _init_git(repo)
            _write_files(
                repo,
                {LIVE_JSON: _fixture_file(LIVE_JSON).read_text(encoding="utf-8")},
            )
            base = _git_commit(repo, "base")
            _copy_pr363_files(repo)
            head = _git_commit(repo, "head")

            def _ignore_git(_dir: str, names: list[str]) -> list[str]:
                return [name for name in names if name == ".git"]

            shutil.copytree(repo, pr_head, ignore=_ignore_git)
            subprocess.check_call(
                ["git", "worktree", "add", "--detach", str(overlay_dst), base],
                cwd=repo,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            copied = overlay_edition_data(
                pr_head,
                overlay_dst,
                base_sha=base,
                head_sha=head,
                repo=repo,
            )
            self.assertGreater(copied, 0)
            for rel in PR363_PATHS:
                self.assertTrue((overlay_dst / rel).is_file(), rel)
            proc = _run_automerge(repo, base=base)
            _record(
                "caller_edition_qa",
                proc,
                _gate_cmd(["--automerge", "--base", base, "--root", str(repo)]),
            )
            _assert_allowlist_paths(self, proc, PR363_OFFENDING)
            self.assertNotIn(
                json.dumps("README.md", ensure_ascii=True), _stderr_lines(proc)
            )

    def test_caller_narrative_yml_pages_byte_identical_to_develop(self) -> None:
        script = (REPO / ".github" / "workflows" / "narrative.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "python -P -m tools.chiefs_narrative.review_gate --pages",
            script,
        )
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "tree"
            dest = _install_develop_gate(Path(tmp) / "develop")
            _write_files(
                tree,
                {
                    LIVE_JSON: _json_file(_review_payload()),
                    REVIEW_REL: _json_file(_review_payload()),
                },
            )
            args = ["--pages", "--root", str(tree)]
            head = _run_gate(_loaded_gate_base(), args)
            base = _run_gate(dest, args)
            self.assertEqual(head.returncode, base.returncode)
            self.assertEqual(head.stdout, base.stdout)
            self.assertEqual(head.stderr, base.stderr)
            _record("caller_narrative", head, _gate_cmd(args))
            self.assertEqual(head.returncode, 2)
            self.assertIn(PAGES_TEXT, head.stderr or "")
            self.assertNotIn(COMBINED_TEXT, head.stderr or "")
            self.assertNotIn(ALLOWLIST_TEXT, head.stderr or "")
            self.assertNotIn(SIGNED_TEXT, head.stderr or "")

    def test_pages_mode_is_not_the_automerge_merged_message(self) -> None:
        src = (REPO / "tools" / "chiefs_narrative" / "review_gate.py").read_text(
            encoding="utf-8"
        )
        pages = src.split("if args.pages:", 1)[1].split("except GateError", 1)[0]
        self.assertIn(PAGES_TEXT, pages)
        self.assertNotIn("automerge allowlist", pages)
        self.assertNotIn(COMBINED_TEXT, pages)
        self.assertNotIn(SIGNED_TEXT, pages)


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
            "REVIEW_GATE_CHECKOUT": str(REPO),
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
        for name, filename in MUTANT_PATCHES:
            patch = MUTANT_DIR / filename
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
                        "REVIEW_GATE_CHECKOUT": str(REPO),
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
                "command": f"patch -p1 -i {filename} && python -m unittest {CONTROL} -q  # {REPEAT}x",
                "exit": last.returncode if last else -1,
                "last15": blob.splitlines()[-15:],
            }


class ReviewGateAutomergeZPreflight(unittest.TestCase):
    """Pass standalone, alone, and in reverse order."""

    def test_z_preflight_frozen_ids(self) -> None:
        missing = [rid for rid in FROZEN_IDS if rid not in RECEIPTS]
        if missing:
            if os.environ.get("_REVIEW_GATE_PREFLIGHT_NESTED") == "1":
                self.fail(f"missing frozen receipts after nested run: {missing}")
            env = os.environ.copy()
            env["_REVIEW_GATE_PREFLIGHT_NESTED"] = "1"
            loader = unittest.TestLoader()
            suite = unittest.TestSuite()
            module = sys.modules[__name__]
            for cls in (
                ReviewGateAutomergeControl,
                ReviewGateAutomergeRows,
                ReviewGateAutomergeMutants,
            ):
                suite.addTests(loader.loadTestsFromTestCase(cls))
            buf = io.StringIO()
            result = unittest.TextTestRunner(stream=buf, verbosity=0).run(suite)
            self.assertTrue(
                result.wasSuccessful(),
                buf.getvalue() or f"nested {cls.__name__ if False else 'suite'} failed",
            )
            del env, module
        missing = [rid for rid in FROZEN_IDS if rid not in RECEIPTS]
        self.assertEqual(missing, [], f"missing frozen receipts: {missing}")
        listed = set(FROZEN_IDS)
        extra = sorted(
            rid
            for rid in RECEIPTS
            if rid not in listed and rid != "control"
        )
        self.assertEqual(extra, [], f"undeclared receipts: {extra}")
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
