"""FROZEN-38 workflow rows: real run: blocks, if: evaluator, C4/C1b/A-two."""
from __future__ import annotations

import fnmatch
import json
import os
import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

from tools.tests.frozen38_harness import (
    HEAD_REF,
    REPO,
    SLUG,
    TINY_SVG,
    build_promo,
    edition_files,
    edition_qa_globs,
    evaluate_if,
    git,
    github_ns,
    load_workflow,
    preview_payload,
    run_gate,
    step_env,
    step_run,
    write_gh_stub,
)


def _automerge_if(name: str) -> str:
    return str(load_workflow(name)["jobs"]["automerge"]["if"])


class Frozen38Workflows(unittest.TestCase):
    def test_A8_develop_head_excluded(self):
        for name in ("ci.yml", "edition-qa.yml"):
            expr = _automerge_if(name)
            gh = github_ns(head_ref="develop")
            labels = []
            self.assertFalse(
                evaluate_if(expr, gh, labels=labels),
                name,
            )
        narrative = (REPO / ".github" / "workflows" / "narrative.yml").read_bytes()
        self.assertNotIn(b"review_gate --automerge", narrative)

    def test_A10_hand_merge_not_blocked(self):
        for name in ("ci.yml", "edition-qa.yml"):
            text = (REPO / ".github" / "workflows" / name).read_text(encoding="utf-8")
            self.assertNotIn("disable-auto", text)
            self.assertIn("--match-head-commit", text)

    def test_A_two_and_C1b_Y_no_auto(self):
        for name in ("ci.yml", "edition-qa.yml"):
            with self.subTest(name=name):
                text = (REPO / ".github" / "workflows" / name).read_text(encoding="utf-8")
                self.assertNotRegex(text, r"gh pr merge[\s\S]{0,200}--auto")
                wf = load_workflow(name)
                gate = step_run(wf, "automerge", "Refuse unsigned Review editions")
                merge = step_run(wf, "automerge", "Squash-merge the pull request")
                self.assertIn("set -euo pipefail", gate)
                self.assertIn("set -euo pipefail", merge)
                self.assertNotIn("|| true", gate)
                self.assertNotIn("|| true", merge)
                self.assertNotIn(" --auto", merge)
                self.assertNotIn("--auto ", merge)
                self.assertIn("--match-head-commit", merge)
                self.assertIn('"$HEAD_SHA"', merge)
                self.assertIn('--head-ref "$HEAD_REF"', gate)
                self.assertNotIn("${{ github.head_ref }}", gate)
                self.assertIn(
                    'python -P -m tools.chiefs_narrative.review_gate --automerge',
                    gate,
                )
                self.assertIn('--root "${GITHUB_WORKSPACE}"', gate)
                self.assertIn("cd \"$GATE_BASE\"", gate)

    def test_C3_fork_if_false(self):
        for name in ("ci.yml", "edition-qa.yml"):
            expr = _automerge_if(name)
            gh = github_ns(head_repo="evil/fork")
            self.assertFalse(evaluate_if(expr, gh, labels=[]), name)
        qa = load_workflow("edition-qa.yml")
        checkout = qa["jobs"]["automerge"]["steps"][0]
        self.assertIn("base.sha", str(checkout.get("with", {}).get("ref")))
        fetch = step_run(qa, "automerge", "Fetch PR head as git objects")
        self.assertIn("git fetch", fetch)
        self.assertNotIn("pip install", fetch)

    def test_C4_head_ref_env_only(self):
        sentinel = Path("/tmp/pwn-frozen38-c4-shell")
        if sentinel.exists():
            sentinel.unlink()
        for name in ("ci.yml", "edition-qa.yml"):
            wf = load_workflow(name)
            job = wf["jobs"]["automerge"]
            for step in job["steps"]:
                run = str(step.get("run") or "")
                self.assertNotIn("${{ github.head_ref }}", run, name)
                self.assertNotIn("${{github.head_ref}}", run, name)
            gate = step_run(wf, "automerge", "Refuse unsigned Review editions")
            script = gate
            evil = 'promote/x"; touch /tmp/pwn-frozen38-c4-shell; echo "'
            if "${{ github.head_ref }}" in script:
                script = script.replace("${{ github.head_ref }}", evil)
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                python = tmp_path / "python"
                git_stub = tmp_path / "git"
                python.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
                git_stub.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
                python.chmod(0o755)
                git_stub.chmod(0o755)
                env = {
                    **os.environ,
                    "PATH": str(tmp_path) + os.pathsep + os.environ.get("PATH", ""),
                    "HEAD_REF": evil,
                    "HEAD_SHA": "abc",
                    "BASE_REF": "main",
                    "GITHUB_WORKSPACE": str(tmp_path),
                    "GITHUB_OUTPUT": str(tmp_path / "out"),
                    "RUNNER_TEMP": str(tmp_path),
                }
                subprocess.run(
                    ["bash", "-lc", script],
                    cwd=tmp_path,
                    capture_output=True,
                    text=True,
                    env=env,
                )
        self.assertFalse(sentinel.exists(), "head_ref injected into the shell")

    def test_C4_S_no_secrets_on_head_touching_steps(self):
        secret_names = ("Refuse unsigned Review editions", "Re-verify against develop", "Fetch PR head as git objects")
        for name in ("ci.yml", "edition-qa.yml"):
            wf = load_workflow(name)
            for step in wf["jobs"]["automerge"]["steps"]:
                step_name = step.get("name")
                env = step.get("env") or {}
                blob = json.dumps(env)
                if step_name in secret_names:
                    self.assertNotIn("secrets.", blob, f"{name} {step_name}")
                    self.assertNotIn("GH_TOKEN", blob, f"{name} {step_name}")
                    self.assertNotIn("GITHUB_TOKEN", blob.replace("GITHUB_REPOSITORY", ""), f"{name} {step_name}")

    def test_C4_R_head_gate_module_never_runs(self):
        qa = load_workflow("edition-qa.yml")
        checkout = None
        for step in qa["jobs"]["automerge"]["steps"]:
            if "actions/checkout" in str(step.get("uses") or ""):
                checkout = step
                break
        self.assertIsNotNone(checkout)
        ref = str((checkout.get("with") or {}).get("ref"))
        self.assertIn("base.sha", ref)
        self.assertNotIn("head.sha", ref)
        with tempfile.TemporaryDirectory() as tmp:
            files = edition_files(SLUG, preview_payload())
            files["tools/chiefs_narrative/review_gate.py"] = (
                b"import pathlib\npathlib.Path('/tmp/pwn-frozen38-c4').write_text('pwned')\nraise SystemExit(0)\n"
            )
            promo = build_promo(Path(tmp), files)
            sentinel = Path("/tmp/pwn-frozen38-c4")
            if sentinel.exists():
                sentinel.unlink()
            result = run_gate(promo)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("off the automerge allowlist", result.stderr)
            self.assertFalse(sentinel.exists(), "head review_gate.py executed")

    def test_C5_actor_table(self):
        for name in ("ci.yml", "edition-qa.yml"):
            expr = _automerge_if(name)
            self.assertTrue(
                evaluate_if(expr, github_ns(), labels=[]), name
            )
            self.assertFalse(
                evaluate_if(expr, github_ns(actor="github-actions[bot]"), labels=[]),
                name,
            )
            self.assertFalse(
                evaluate_if(expr, github_ns(author="github-actions[bot]"), labels=[]),
                name,
            )
            self.assertFalse(
                evaluate_if(expr, github_ns(actor="dependabot[bot]"), labels=[]),
                name,
            )
        expr = _automerge_if("edition-qa.yml")
        self.assertFalse(
            evaluate_if(expr, github_ns(association="NONE"), labels=[]),
        )

    def test_C6_draft_hold_narrative_develop(self):
        rows = [
            {"draft": True},
            {"labels": ["hold"]},
            {"head_ref": "narrative/update-x"},
            {"head_ref": "develop"},
        ]
        for name in ("ci.yml", "edition-qa.yml"):
            expr = _automerge_if(name)
            for row in rows:
                gh = github_ns(**row)
                labels = row.get("labels") or []
                self.assertFalse(
                    evaluate_if(expr, gh, labels=labels),
                    f"{name} {row}",
                )

    def test_C7_base_not_main(self):
        for name in ("ci.yml", "edition-qa.yml"):
            expr = _automerge_if(name)
            self.assertFalse(
                evaluate_if(expr, github_ns(base_ref="develop"), labels=[]),
                name,
            )

    def test_F1_needs_gates(self):
        for name in ("ci.yml", "edition-qa.yml"):
            expr = _automerge_if(name)
            self.assertNotIn("always()", expr)
            self.assertNotIn("!cancelled()", expr)
            gh = github_ns()
            self.assertTrue(evaluate_if(expr, gh, needs={"gates": "success"}, labels=[]))
            for state in ("failure", "cancelled", "skipped"):
                self.assertFalse(
                    evaluate_if(expr, gh, needs={"gates": state}, labels=[]),
                    f"{name} {state}",
                )

    def test_F2_gates_has_no_if(self):
        ci = load_workflow("ci.yml")
        self.assertNotIn("if", ci["jobs"]["gates"])
        text = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertNotIn("if: !startsWith(github.head_ref,'promote/')", text)

    def test_F7_no_swallowed_rc(self):
        for name in ("ci.yml", "edition-qa.yml"):
            wf = load_workflow(name)
            gate = step_run(wf, "automerge", "Refuse unsigned Review editions")
            reverify = step_run(wf, "automerge", "Re-verify against develop")
            labels = step_run(wf, "automerge", "Re-read labels and draft")
            for script in (gate, reverify, labels):
                self.assertIn("set -euo pipefail", script)
                self.assertNotIn("|| true", script)
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                python = tmp_path / "python"
                python.write_text("#!/bin/bash\nexit 1\n", encoding="utf-8")
                python.chmod(0o755)
                env = {
                    **os.environ,
                    "PATH": str(tmp_path) + os.pathsep + os.environ.get("PATH", ""),
                    "GITHUB_WORKSPACE": str(tmp_path),
                    "GITHUB_OUTPUT": str(tmp_path / "out"),
                    "HEAD_REF": HEAD_REF,
                    "HEAD_SHA": "abc",
                    "BASE_REF": "main",
                    "GATE_BASE": str(tmp_path),
                    "PYTHONPATH": str(tmp_path),
                }
                line = (
                    'PYTHONPATH="$GATE_BASE" python -P -m tools.chiefs_narrative.review_gate --automerge '
                    '--root "${GITHUB_WORKSPACE}"'
                )
                if "|| true" in gate:
                    line += " || true"
                result = subprocess.run(
                    ["bash", "-lc", "set -euo pipefail\n" + line],
                    cwd=tmp_path,
                    capture_output=True,
                    text=True,
                    env=env,
                )
                self.assertNotEqual(result.returncode, 0, name)

    def test_H2_L_D_F_label_reread(self):
        for name in ("ci.yml", "edition-qa.yml"):
            with self.subTest(name=name):
                wf = load_workflow(name)
                script = step_run(wf, "automerge", "Re-read labels and draft")
                merge = step_run(wf, "automerge", "Squash-merge the pull request")
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = Path(tmp)
                    log = tmp_path / "gh.log"
                    stub = tmp_path / "gh"
                    write_gh_stub(
                        stub,
                        log,
                        pr_json={"draft": False, "labels": [{"name": "hold"}]},
                    )
                    env = {
                        **os.environ,
                        "PATH": str(tmp_path) + os.pathsep + os.environ.get("PATH", ""),
                        "GH_TOKEN": "x",
                        "PR_NUMBER": "1",
                        "GITHUB_REPOSITORY": "stevologic/arrowheadpaesanowebsite",
                        "PR_URL": "https://example.test/1",
                        "HEAD_SHA": "abc",
                    }
                    first = subprocess.run(
                        ["bash", "-lc", script],
                        cwd=tmp_path,
                        capture_output=True,
                        text=True,
                        env=env,
                    )
                    self.assertNotEqual(first.returncode, 0, first.stderr)
                    self.assertFalse((log.with_suffix(".log.merge")).exists())
                    write_gh_stub(
                        stub,
                        log,
                        pr_json={"draft": True, "labels": []},
                    )
                    second = subprocess.run(
                        ["bash", "-lc", script],
                        cwd=tmp_path,
                        capture_output=True,
                        text=True,
                        env=env,
                    )
                    self.assertNotEqual(second.returncode, 0)
                    write_gh_stub(stub, log, fail_api=True)
                    third = subprocess.run(
                        ["bash", "-lc", script],
                        cwd=tmp_path,
                        capture_output=True,
                        text=True,
                        env=env,
                    )
                    self.assertNotEqual(third.returncode, 0)
                    write_gh_stub(
                        stub, log, pr_json={"draft": False, "labels": []}
                    )
                    ok = subprocess.run(
                        ["bash", "-lc", script + "\n" + merge],
                        cwd=tmp_path,
                        capture_output=True,
                        text=True,
                        env=env,
                    )
                    self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
                    merges = (tmp_path / "gh.log.merge").read_text(encoding="utf-8")
                    self.assertEqual(merges.strip().count("merge"), 1)
                    self.assertIn("--match-head-commit", (tmp_path / "gh.log").read_text())

    def test_C1b_B_hold_between_enable_and_merge(self):
        wf = load_workflow("ci.yml")
        script = step_run(wf, "automerge", "Re-read labels and draft")
        merge = step_run(wf, "automerge", "Squash-merge the pull request")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log = tmp_path / "gh.log"
            stub = tmp_path / "gh"
            write_gh_stub(
                stub, log, pr_json={"draft": False, "labels": [{"name": "hold"}]}
            )
            env = {
                **os.environ,
                "PATH": str(tmp_path) + os.pathsep + os.environ.get("PATH", ""),
                "GH_TOKEN": "x",
                "PR_NUMBER": "1",
                "GITHUB_REPOSITORY": "stevologic/arrowheadpaesanowebsite",
                "PR_URL": "https://example.test/1",
                "HEAD_SHA": "deadbeef",
            }
            result = subprocess.run(
                ["bash", "-lc", script + "\n" + merge],
                cwd=tmp_path,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((tmp_path / "gh.log.merge").exists())

    def test_A_two_one_pages_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log = tmp_path / "gh.log"
            stub = tmp_path / "gh"
            state = tmp_path / "merged_once"

            script = f"""#!/bin/bash
set -euo pipefail
echo "$0 $*" >> "{log}"
if [[ "$*" == *"pr merge"* ]]; then
  if [ -f "{state}" ]; then
    echo "already merged" >&2
    exit 1
  fi
  echo merged > "{state}"
  echo merge >> "{log}.merge"
  exit 0
fi
if [[ "$*" == *"workflow run"* ]]; then
  echo pages >> "{log}.pages"
  exit 0
fi
if [[ "$*" == *"api"* ]]; then
  printf '%s\\n' '{{"draft": false, "labels": []}}'
  exit 0
fi
exit 0
"""
            stub.write_text(script, encoding="utf-8")
            stub.chmod(0o755)
            env = {
                **os.environ,
                "PATH": str(tmp_path) + os.pathsep + os.environ.get("PATH", ""),
                "GH_TOKEN": "x",
                "PR_NUMBER": "1",
                "GITHUB_REPOSITORY": "stevologic/arrowheadpaesanowebsite",
                "PR_URL": "https://example.test/1",
                "HEAD_SHA": "abc",
            }
            pages_calls = 0
            for name in ("ci.yml", "edition-qa.yml"):
                wf = load_workflow(name)
                labels = step_run(wf, "automerge", "Re-read labels and draft")
                merge = step_run(wf, "automerge", "Squash-merge the pull request")
                pages = step_run(wf, "automerge", "Trigger Pages deploy")
                result = subprocess.run(
                    ["bash", "-lc", "\n".join((labels, merge, pages))],
                    cwd=tmp_path,
                    capture_output=True,
                    text=True,
                    env=env,
                )
                if result.returncode == 0:
                    pages_calls += 1
            self.assertEqual(pages_calls, 1)
            self.assertEqual(
                (tmp_path / "gh.log.pages").read_text(encoding="utf-8").strip().count("pages"),
                1,
            )

    def test_H5_bot_opener_excluded(self):
        for name in ("ci.yml", "edition-qa.yml"):
            expr = _automerge_if(name)
            self.assertFalse(
                evaluate_if(
                    expr,
                    github_ns(actor="github-actions[bot]", author="github-actions[bot]"),
                    labels=[],
                ),
                name,
            )

    def test_E_parity_globs(self):
        globs = edition_qa_globs()
        expected = [
            "data/narrative.json",
            "data/narrative_archive.json",
            "data/narrative_repair.json",
            "data/narrative_editions/**",
            "data/wire.json",
            "data/schedule_2026.json",
            "public/images/narrative/**",
            "signoff/review/**",
        ]
        self.assertEqual(globs, expected)
        es_ok = [
            "data/narrative.json",
            "data/narrative_archive.json",
            "data/narrative_repair.json",
            "data/wire.json",
            "data/schedule_2026.json",
            f"data/narrative_editions/{SLUG}.json",
            f"public/images/narrative/{SLUG}/xo-mesh.svg",
            f"signoff/review/{SLUG}.json",
            f"signoff/review/{SLUG}.sig",
        ]
        es_bad = [
            "tools/chiefs_narrative/review_gate.py",
            "README.md",
            "static/x",
            f"data/narrative_editions_evil/{SLUG}.json",
        ]
        for path in es_ok:
            self.assertTrue(
                any(fnmatch.fnmatch(path, glob) for glob in globs), path
            )
        for path in es_bad:
            self.assertFalse(
                any(fnmatch.fnmatch(path, glob) for glob in globs), path
            )

    def test_F6_cancelled_gate_no_merge(self):
        wf = load_workflow("ci.yml")
        self.assertEqual(wf["concurrency"]["cancel-in-progress"], "true")
        merge = step_run(wf, "automerge", "Squash-merge the pull request")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log = tmp_path / "gh.log"
            stub = tmp_path / "gh"
            write_gh_stub(stub, log)
            env = {
                **os.environ,
                "PATH": str(tmp_path) + os.pathsep + os.environ.get("PATH", ""),
                "GH_TOKEN": "x",
                "PR_URL": "https://example.test/1",
                "HEAD_SHA": "abc",
            }
            gate = "set -euo pipefail\nexit 1\n"
            result = subprocess.run(
                ["bash", "-lc", gate + merge],
                cwd=tmp_path,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((tmp_path / "gh.log.merge").exists())

    def test_B7_reverify_refuses_merge(self):
        wf = load_workflow("ci.yml")
        reverify = step_run(wf, "automerge", "Re-verify against develop")
        merge = step_run(wf, "automerge", "Squash-merge the pull request")
        self.assertIn("review_gate --automerge", reverify)
        self.assertIn("--fetch-develop", reverify)
        with tempfile.TemporaryDirectory() as tmp:
            files = edition_files(SLUG, preview_payload())
            promo = build_promo(Path(tmp), files)
            first = run_gate(promo)
            self.assertEqual(first.returncode, 0, first.stderr)
            seed = promo.tmp / "seed"
            git(["checkout", "develop"], cwd=seed)
            (seed / "data" / "narrative.json").write_bytes(
                json_bytes_newer()
            )
            git(["add", "data/narrative.json"], cwd=seed)
            git(["commit", "-m", "D2"], cwd=seed)
            git(["push", "origin", "develop"], cwd=seed)
            git(["fetch", "origin", "develop"], cwd=promo.pr_tree)
            second = run_gate(promo)
            self.assertEqual(second.returncode, 1, second.stderr)
            self.assertIn("stale vs develop", second.stderr)

    def test_B10_match_head_commit_is_event_sha(self):
        for name in ("ci.yml", "edition-qa.yml"):
            merge = step_run(load_workflow(name), "automerge", "Squash-merge the pull request")
            self.assertIn("--match-head-commit \"$HEAD_SHA\"", merge)
            env = step_env(load_workflow(name), "automerge", "Squash-merge the pull request")
            self.assertIn("HEAD_SHA", env)
            self.assertIn("github.event.pull_request.head.sha", str(env["HEAD_SHA"]))

    def test_H2_residual_documented_in_source(self):
        ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("Re-read labels and draft", ci)


def json_bytes_newer() -> bytes:
    from tools.tests.frozen38_harness import dumps, preview_payload

    return dumps(preview_payload(headline="newer tip"))


if __name__ == "__main__":
    unittest.main()
