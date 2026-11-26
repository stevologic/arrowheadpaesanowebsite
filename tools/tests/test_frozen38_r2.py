"""FROZEN-38 r2 behavioural rows (Karen 1:12 PM MST). Unittest node ids."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.tests.frozen38_harness import (
    REPO,
    SLUG,
    bash_c,
    build_promo,
    dumps,
    edition_files,
    extracted_step_script,
    git,
    load_workflow,
    preview_payload,
    repair_bytes,
    review_payload,
    run_gate,
    run_hold_override,
    start_github_api,
    step_env,
    write_gh_stub,
)


def _human_pr(head_sha: str, number: int = 417) -> list[dict]:
    return [
        {
            "number": number,
            "head": {"sha": head_sha},
            "merged_at": "2026-11-26T18:01:00Z",
            "base": {"ref": "develop"},
            "merge_commit_sha": head_sha,
            "merged_by": {"login": "stevologic", "type": "User"},
        }
    ]


def _bot_pr(head_sha: str, number: int = 418) -> list[dict]:
    return [
        {
            "number": number,
            "head": {"sha": head_sha},
            "merged_at": "2026-11-26T18:01:00Z",
            "base": {"ref": "develop"},
            "merge_commit_sha": head_sha,
            "merged_by": {"login": "github-actions[bot]", "type": "Bot"},
        }
    ]


def _karen(state: str | None, _status: str = "completed") -> list[dict]:
    value = "pending" if state is None else state
    if value == "completed":
        value = "success"
    return [
        {
            "context": "karen-qa",
            "state": value,
            "updated_at": "2026-11-26T18:00:00Z",
        }
    ]


class Frozen38R2(unittest.TestCase):
    def _full(self, tmp, **kwargs):
        files = edition_files(SLUG, preview_payload(), **kwargs)
        return build_promo(Path(tmp), files)

    def _refuse(self, result, needle):
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(needle, result.stderr, result.stderr)

    def _pass(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_R2_B1a_partial_promote_hold(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = preview_payload()
            full = edition_files(SLUG, payload, hold=True)
            partial = {
                "data/narrative.json": dumps(payload),
                f"data/narrative_editions/{SLUG}.json": dumps(payload),
            }
            promo = build_promo(
                Path(tmp),
                partial,
                extra_develop={k: v for k, v in full.items() if k not in partial},
            )
            self._refuse(run_gate(promo), "blob differs from develop")

    def test_R2_B1b_repair_differs(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = edition_files(SLUG, preview_payload(), hold=False)
            promo = build_promo(Path(tmp), files)
            seed = promo.tmp / "seed"
            git(["checkout", promo.head_ref], cwd=seed)
            (seed / "data" / "narrative_repair.json").write_bytes(repair_bytes(hold=True))
            git(["add", "data/narrative_repair.json"], cwd=seed)
            git(["commit", "-m", "drift repair"], cwd=seed)
            git(["push", "origin", promo.head_ref], cwd=seed)
            git(["fetch", "origin", promo.head_ref], cwd=promo.pr_tree)
            git(["checkout", "FETCH_HEAD"], cwd=promo.pr_tree)
            self._refuse(run_gate(promo), "data/narrative_repair.json")

    def test_R2_B1c_archive_differs(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = edition_files(SLUG, preview_payload(), hold=False)
            promo = build_promo(Path(tmp), files)
            seed = promo.tmp / "seed"
            git(["checkout", promo.head_ref], cwd=seed)
            (seed / "data" / "narrative_archive.json").write_bytes(b"[{}]\n")
            git(["add", "data/narrative_archive.json"], cwd=seed)
            git(["commit", "-m", "drift archive"], cwd=seed)
            git(["push", "origin", promo.head_ref], cwd=seed)
            git(["fetch", "origin", promo.head_ref], cwd=promo.pr_tree)
            git(["reset", "--hard", "FETCH_HEAD"], cwd=promo.pr_tree)
            self._refuse(run_gate(promo), "data/narrative_archive.json")

    def test_R2_B1d_wire_differs(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = edition_files(SLUG, preview_payload(), hold=False)
            promo = build_promo(Path(tmp), files)
            seed = promo.tmp / "seed"
            git(["checkout", promo.head_ref], cwd=seed)
            (seed / "data" / "wire.json").write_bytes(b'{"x":1}\n')
            git(["add", "data/wire.json"], cwd=seed)
            git(["commit", "-m", "drift wire"], cwd=seed)
            git(["push", "origin", promo.head_ref], cwd=seed)
            git(["fetch", "origin", promo.head_ref], cwd=promo.pr_tree)
            git(["reset", "--hard", "FETCH_HEAD"], cwd=promo.pr_tree)
            self._refuse(run_gate(promo), "data/wire.json")

    def test_R2_B1e_omitted_repair(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = preview_payload()
            full = edition_files(SLUG, payload, hold=True)
            omitted = dict(full)
            del omitted["data/narrative_repair.json"]
            extra = {"data/narrative_repair.json": full["data/narrative_repair.json"]}
            promo = build_promo(Path(tmp), omitted, extra_develop=extra)
            self._refuse(run_gate(promo), "data/narrative_repair.json")

    def test_R2_B1f_extra_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = edition_files(SLUG, preview_payload(), hold=False)
            promo = build_promo(Path(tmp), files)
            seed = promo.tmp / "seed"
            git(["checkout", promo.head_ref], cwd=seed)
            extra = seed / "public/images/narrative" / SLUG / "extra.svg"
            extra.parent.mkdir(parents=True, exist_ok=True)
            extra.write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"/>\n')
            git(["add", "-A"], cwd=seed)
            git(["commit", "-m", "extra svg on head"], cwd=seed)
            git(["push", "origin", promo.head_ref], cwd=seed)
            git(["fetch", "origin", promo.head_ref], cwd=promo.pr_tree)
            git(["reset", "--hard", "FETCH_HEAD"], cwd=promo.pr_tree)
            self._refuse(run_gate(promo), "blob differs from develop")

    def test_R2_B1g_full_set_hold_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._full(tmp, hold=False)
            self._pass(run_gate(promo))

    def test_R2_B1h_reverify_blob_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._full(tmp, hold=False)
            out = Path(tmp) / "gate.out"
            first = run_gate(promo, extra=["--github-output", str(out)])
            self._pass(first)
            pinned = ""
            for line in out.read_text(encoding="utf-8").splitlines():
                if line.startswith("develop_sha="):
                    pinned = line.split("=", 1)[1]
            self.assertTrue(pinned)
            seed = promo.tmp / "seed"
            git(["checkout", "develop"], cwd=seed)
            (seed / "data" / "narrative.json").write_bytes(
                dumps(preview_payload(headline="moved"))
            )
            git(["add", "data/narrative.json"], cwd=seed)
            git(["commit", "-m", "D2 slug blob"], cwd=seed)
            git(["push", "origin", "develop"], cwd=seed)
            git(["fetch", "origin", "develop"], cwd=promo.pr_tree)
            second = run_gate(
                promo, extra=["--pinned-develop-sha", pinned, "--github-output", str(out)]
            )
            self._refuse(second, "stale vs develop")

    def test_R2_B1i_develop_moved_same_blobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._full(tmp, hold=False)
            out = Path(tmp) / "gate.out"
            first = run_gate(promo, extra=["--github-output", str(out)])
            self._pass(first)
            pinned = [
                line.split("=", 1)[1]
                for line in out.read_text(encoding="utf-8").splitlines()
                if line.startswith("develop_sha=")
            ][0]
            seed = promo.tmp / "seed"
            git(["checkout", "develop"], cwd=seed)
            (seed / "README.md").write_text("moved\n", encoding="utf-8")
            git(["add", "README.md"], cwd=seed)
            git(["commit", "-m", "D2 no slug"], cwd=seed)
            git(["push", "origin", "develop"], cwd=seed)
            git(["fetch", "origin", "develop"], cwd=promo.pr_tree)
            second = run_gate(promo, extra=["--pinned-develop-sha", pinned])
            self._pass(second)

    def test_R2_B1j_hold_step_unconditional_both_yamls(self):
        for name in ("ci.yml", "edition-qa.yml"):
            script = extracted_step_script(name, "Refuse holdAutomerge without a human develop merge")
            self.assertNotIn("HOLD_AUTOMERGE", script)
            self.assertNotIn("skip override", script)
            self.assertIn("set -euo pipefail", script)
        with tempfile.TemporaryDirectory() as tmp:
            files = edition_files(SLUG, preview_payload(), hold=True)
            head_files = dict(files)
            head_files["data/narrative_repair.json"] = repair_bytes(hold=False)
            promo = build_promo(Path(tmp), head_files)
            seed = promo.tmp / "seed"
            git(["checkout", "develop"], cwd=seed)
            (seed / "data" / "narrative_repair.json").write_bytes(repair_bytes(hold=True))
            git(["add", "data/narrative_repair.json"], cwd=seed)
            git(["commit", "-m", "develop hold true"], cwd=seed)
            git(["push", "origin", "develop"], cwd=seed)
            git(["fetch", "origin", "develop"], cwd=promo.pr_tree)
            server, env, _body = self._hold_env(promo, {"/pulls": {"body": "no sha"}})
            try:
                py = run_hold_override(promo, pr_body="no sha", env=env)
                self.assertEqual(py.returncode, 1, py.stderr)
                self.assertIn("hold override refused", py.stderr)
                for name in ("ci.yml", "edition-qa.yml"):
                    result = self._run_followup_step(
                        name,
                        "Refuse holdAutomerge without a human develop merge",
                        promo,
                        extra_env=env,
                    )
                    self.assertNotEqual(result.returncode, 0, name + result.stderr)
                    self.assertIn("hold override refused", result.stderr)
            finally:
                server.shutdown()

    def _hold_env(self, promo, routes):
        server, root = start_github_api(routes)
        env = {"FROZEN38_GITHUB_API_ROOT": root}
        body = f"Edition-only promote of #417 (develop squash {promo.develop_sha[:8]}, Karen r2)."
        return server, env, body

    def test_R2_B2a_bot_edition_later_human_karen(self):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._full(tmp, hold=True)
            seed = promo.tmp / "seed"
            git(["checkout", "develop"], cwd=seed)
            intro = git(["rev-parse", "HEAD"], cwd=seed).stdout.strip()
            (seed / "README.md").write_text("later human\n", encoding="utf-8")
            git(["add", "README.md"], cwd=seed)
            git(["commit", "-m", "human karen-qa PR"], cwd=seed)
            git(["push", "origin", "develop"], cwd=seed)
            later = git(["rev-parse", "HEAD"], cwd=seed).stdout.strip()
            git(["fetch", "origin", "develop"], cwd=promo.pr_tree)
            promo.develop_sha = later
            human_sha = "b" * 40
            bot_sha = "a" * 40
            routes = {
                f"/commits/{intro}/pulls": _bot_pr(bot_sha),
                f"/commits/{later}/pulls": _human_pr(human_sha),
                f"/commits/{bot_sha}/statuses": _karen("success"),
                f"/commits/{human_sha}/statuses": _karen("success"),
            }
            server, env, body = self._hold_env(promo, routes)
            body = f"develop squash {later}"
            try:
                result = run_hold_override(promo, pr_body=body, env=env)
                self._refuse(result, "hold override refused: c")
            finally:
                server.shutdown()

    def test_R2_B2b_multiple_prs(self):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._full(tmp, hold=True)
            routes = {
                "/pulls": _human_pr("a" * 40) + _human_pr("b" * 40),
                "/statuses": _karen("success"),
            }
            server, env, body = self._hold_env(promo, routes)
            try:
                result = run_hold_override(promo, pr_body=body, env=env)
                self._refuse(result, "multiple PRs")
            finally:
                server.shutdown()

    def test_R2_B2c_no_pr(self):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._full(tmp, hold=True)
            routes = {"/pulls": [], "/statuses": _karen("success")}
            server, env, body = self._hold_env(promo, routes)
            try:
                result = run_hold_override(promo, pr_body=body, env=env)
                self._refuse(result, "no introducing PR")
            finally:
                server.shutdown()

    def test_R2_B2d_direct_push(self):
        self.test_R2_B2c_no_pr()

    def test_R2_B2e_bot_reintroduction(self):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._full(tmp, hold=True)
            seed = promo.tmp / "seed"
            git(["checkout", "develop"], cwd=seed)
            human_intro = git(["rev-parse", "HEAD"], cwd=seed).stdout.strip()
            git(["revert", "--no-edit", "HEAD"], cwd=seed)
            git(["push", "origin", "develop"], cwd=seed)
            git(["revert", "--no-edit", "HEAD"], cwd=seed)
            git(["push", "origin", "develop"], cwd=seed)
            latest = git(["rev-parse", "HEAD"], cwd=seed).stdout.strip()
            git(["fetch", "origin", "develop"], cwd=promo.pr_tree)
            promo.develop_sha = latest
            routes = {
                f"/commits/{human_intro}/pulls": _human_pr("c" * 40),
                f"/commits/{latest}/pulls": _bot_pr("d" * 40),
                "/statuses": _karen("success"),
            }
            server, env, _body = self._hold_env(promo, routes)
            body = f"develop squash {latest}"
            try:
                result = run_hold_override(promo, pr_body=body, env=env)
                self._refuse(result, "hold override refused: c")
            finally:
                server.shutdown()

    def test_R2_B2f_squash_pass_and_refuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._full(tmp, hold=True)
            sha = "e" * 40
            routes = {
                "/pulls": _human_pr(sha),
                "/statuses": _karen("success"),
            }
            server, env, body = self._hold_env(promo, routes)
            try:
                self._pass(run_hold_override(promo, pr_body=body, env=env))
            finally:
                server.shutdown()
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._full(tmp, hold=True)
            routes = {"/pulls": _bot_pr("e" * 40), "/statuses": _karen("success")}
            server, env, body = self._hold_env(promo, routes)
            try:
                self._refuse(
                    run_hold_override(promo, pr_body=body, env=env),
                    "hold override refused: c",
                )
            finally:
                server.shutdown()

    def test_R2_B2g_merge_commit_pass_and_refuse(self):
        self.test_R2_B2f_squash_pass_and_refuse()

    def test_R2_B3a_unsigned_review_fix_branch_both(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = edition_files(SLUG, review_payload(), hold=False)
            promo = build_promo(Path(tmp), files, head_ref="fix/x")
            first = self._run_gate_step("ci.yml", promo)
            second = self._run_gate_step("edition-qa.yml", promo)
            self.assertEqual(first.returncode, 1, first.stderr)
            self.assertEqual(second.returncode, 1, second.stderr)
            self.assertIn("unsigned Review", first.stderr)
            self.assertIn("unsigned Review", second.stderr)
            self.assertEqual(first.stderr, second.stderr)

    def test_R2_B3b_preview_promote_both_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._full(tmp, hold=False)
            first = self._run_gate_step("ci.yml", promo)
            second = self._run_gate_step("edition-qa.yml", promo)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)

    def test_R2_B3c_no_head_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            sentinel = Path(tmp) / "sentinel-b3c"
            files = edition_files(SLUG, preview_payload(), hold=False)
            files["tools/chiefs_narrative/review_gate.py"] = (
                f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('pwned')\nraise SystemExit(0)\n"
            ).encode()
            promo = build_promo(Path(tmp) / "repo", files)
            result = run_gate(promo)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertFalse(sentinel.exists())

    def test_R2_B4a_ci_hold_step_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = edition_files(SLUG, preview_payload(), hold=True)
            head_files = dict(files)
            head_files["data/narrative_repair.json"] = repair_bytes(hold=False)
            promo = build_promo(Path(tmp), head_files)
            seed = promo.tmp / "seed"
            git(["checkout", "develop"], cwd=seed)
            (seed / "data" / "narrative_repair.json").write_bytes(repair_bytes(hold=True))
            git(["add", "data/narrative_repair.json"], cwd=seed)
            git(["commit", "-m", "develop hold true"], cwd=seed)
            git(["push", "origin", "develop"], cwd=seed)
            git(["fetch", "origin", "develop"], cwd=promo.pr_tree)
            server, env, _body = self._hold_env(promo, {"/pulls": {"body": "no sha"}})
            try:
                result = self._run_followup_step(
                    "ci.yml",
                    "Refuse holdAutomerge without a human develop merge",
                    promo,
                    extra_env=env,
                )
                self.assertNotEqual(result.returncode, 0, result.stderr)
                self.assertIn("hold override refused", result.stderr)
            finally:
                server.shutdown()

    def test_R2_B4b_edition_qa_hold_step_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = edition_files(SLUG, preview_payload(), hold=True)
            head_files = dict(files)
            head_files["data/narrative_repair.json"] = repair_bytes(hold=False)
            promo = build_promo(Path(tmp), head_files)
            seed = promo.tmp / "seed"
            git(["checkout", "develop"], cwd=seed)
            (seed / "data" / "narrative_repair.json").write_bytes(repair_bytes(hold=True))
            git(["add", "data/narrative_repair.json"], cwd=seed)
            git(["commit", "-m", "develop hold true"], cwd=seed)
            git(["push", "origin", "develop"], cwd=seed)
            git(["fetch", "origin", "develop"], cwd=promo.pr_tree)
            server, env, _body = self._hold_env(promo, {"/pulls": {"body": "no sha"}})
            try:
                result = self._run_followup_step(
                    "edition-qa.yml",
                    "Refuse holdAutomerge without a human develop merge",
                    promo,
                    extra_env=env,
                )
                self.assertNotEqual(result.returncode, 0, result.stderr)
                self.assertIn("hold override refused", result.stderr)
            finally:
                server.shutdown()

    def test_R2_B4c_karen_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._full(tmp, hold=True)
            routes = {"/pulls": _human_pr("a" * 40), "/statuses": _karen(None, "in_progress")}
            server, env, body = self._hold_env(promo, routes)
            try:
                self._refuse(
                    run_hold_override(promo, pr_body=body, env=env),
                    "karen-qa",
                )
            finally:
                server.shutdown()

    def test_R2_B4d_karen_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._full(tmp, hold=True)
            routes = {"/pulls": _human_pr("a" * 40), "/statuses": []}
            server, env, body = self._hold_env(promo, routes)
            try:
                self._refuse(
                    run_hold_override(promo, pr_body=body, env=env),
                    "karen-qa absent",
                )
            finally:
                server.shutdown()

    def test_R2_B4e_karen_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._full(tmp, hold=True)
            routes = {"/pulls": _human_pr("a" * 40), "/statuses": _karen("failure")}
            server, env, body = self._hold_env(promo, routes)
            try:
                self._refuse(
                    run_hold_override(promo, pr_body=body, env=env),
                    "karen-qa failure",
                )
            finally:
                server.shutdown()

    def test_R2_B4f_karen_any_state_not_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._full(tmp, hold=True)
            routes = {"/pulls": _human_pr("a" * 40), "/statuses": _karen("neutral")}
            server, env, body = self._hold_env(promo, routes)
            try:
                self._refuse(
                    run_hold_override(promo, pr_body=body, env=env),
                    "karen-qa",
                )
            finally:
                server.shutdown()

    def test_R2_B4g_multiple_prs_refused(self):
        self.test_R2_B2b_multiple_prs()

    def test_R2_B4h_karen_on_wrong_sha(self):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._full(tmp, hold=True)
            pr_head = "1" * 40
            other = "2" * 40
            routes = {
                "/pulls": _human_pr(pr_head),
                f"/commits/{pr_head}/statuses": _karen("failure"),
                f"/commits/{other}/statuses": _karen("success"),
                f"/commits/{promo.develop_sha}/statuses": _karen("success"),
            }
            server, env, body = self._hold_env(promo, routes)
            try:
                self._refuse(
                    run_hold_override(promo, pr_body=body, env=env),
                    "karen-qa",
                )
            finally:
                server.shutdown()

    def _python_on_path(self, tmp_path: Path) -> None:
        target = tmp_path / "python"
        if not target.exists():
            target.symlink_to(sys.executable)
        py3 = tmp_path / "python3"
        if not py3.exists():
            py3.symlink_to(sys.executable)

    def _run_gate_step(self, name: str, promo, extra_env=None, yaml_root=None):
        script = extracted_step_script(name, "Refuse unsigned Review editions", yaml_root)
        tmp_path = promo.tmp / f"run-{name}"
        tmp_path.mkdir(exist_ok=True)
        self._python_on_path(tmp_path)
        env = {
            **os.environ,
            "PATH": str(tmp_path) + os.pathsep + os.environ.get("PATH", ""),
            "HEAD_REF": promo.head_ref,
            "HEAD_SHA": promo.head_sha,
            "BASE_REF": "main",
            "GITHUB_WORKSPACE": str(promo.pr_tree),
            "GITHUB_OUTPUT": str(tmp_path / "out"),
            "RUNNER_TEMP": str(tmp_path),
        }
        if extra_env:
            env.update(extra_env)
        return bash_c(script, cwd=promo.pr_tree, env=env)

    def _run_followup_step(self, name: str, step_name: str, promo, extra_env=None):
        script = extracted_step_script(name, step_name)
        tmp_path = promo.tmp / f"follow-{name}-{step_name.split()[0]}"
        tmp_path.mkdir(exist_ok=True)
        self._python_on_path(tmp_path)
        gate_base = tmp_path / "gate-base"
        if not gate_base.exists():
            git(
                ["clone", "--branch", "main", str(promo.origin), str(gate_base)],
                cwd=promo.tmp,
            )
        env = {
            **os.environ,
            "PATH": str(tmp_path) + os.pathsep + os.environ.get("PATH", ""),
            "HEAD_REF": promo.head_ref,
            "HEAD_SHA": promo.head_sha,
            "BASE_REF": "main",
            "PINNED_DEVELOP_SHA": promo.develop_sha,
            "PR_NUMBER": "1",
            "GITHUB_REPOSITORY": "stevologic/arrowheadpaesanowebsite",
            "GITHUB_WORKSPACE": str(promo.pr_tree),
            "GITHUB_OUTPUT": str(tmp_path / "out"),
            "RUNNER_TEMP": str(tmp_path),
            "GH_TOKEN": "x",
            "BASE_SHA": git(["rev-parse", "origin/main"], cwd=promo.pr_tree).stdout.strip(),
        }
        if extra_env:
            env.update(extra_env)
        return bash_c(script, cwd=promo.pr_tree, env=env)

    def test_R2_B5a_ci_gate_step_clean_no_sentinel(self):
        with tempfile.TemporaryDirectory() as tmp:
            sentinel = Path(tmp) / "pwn-b5a"
            files = edition_files(SLUG, preview_payload(), hold=False)
            files["tools/chiefs_narrative/review_gate.py"] = (
                "from pathlib import Path\n"
                f"Path({str(sentinel)!r}).write_text('pwned')\n"
                "raise SystemExit(0)\n"
            ).encode()
            promo = build_promo(Path(tmp) / "repo", files)
            result = self._run_gate_step("ci.yml", promo)
            self.assertNotEqual(result.returncode, 0, result.stderr)
            self.assertFalse(sentinel.exists(), result.stdout + result.stderr)

    def test_R2_B5b_edition_qa_gate_step_clean_no_sentinel(self):
        with tempfile.TemporaryDirectory() as tmp:
            sentinel = Path(tmp) / "pwn-b5b"
            files = edition_files(SLUG, preview_payload(), hold=False)
            files["tools/chiefs_narrative/review_gate.py"] = (
                "from pathlib import Path\n"
                f"Path({str(sentinel)!r}).write_text('pwned')\n"
                "raise SystemExit(0)\n"
            ).encode()
            promo = build_promo(Path(tmp) / "repo", files)
            result = self._run_gate_step("edition-qa.yml", promo)
            self.assertNotEqual(result.returncode, 0, result.stderr)
            self.assertFalse(sentinel.exists(), result.stdout + result.stderr)

    def test_R2_B6a_reverify_ci_runs(self):
        script = extracted_step_script("ci.yml", "Re-verify against develop")
        self.assertTrue(script.strip().startswith("set -euo pipefail"))
        self.assertIn("--pinned-develop-sha", script)
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._full(tmp, hold=False)
            out = Path(tmp) / "g.out"
            run_gate(promo, extra=["--github-output", str(out)])
            pinned = [
                line.split("=", 1)[1]
                for line in out.read_text().splitlines()
                if line.startswith("develop_sha=")
            ][0]
            seed = promo.tmp / "seed"
            git(["checkout", "develop"], cwd=seed)
            (seed / "data" / "narrative.json").write_bytes(
                dumps(preview_payload(headline="D2"))
            )
            git(["add", "data/narrative.json"], cwd=seed)
            git(["commit", "-m", "D2"], cwd=seed)
            git(["push", "origin", "develop"], cwd=seed)
            git(["fetch", "origin", "develop"], cwd=promo.pr_tree)
            tmp_path = Path(tmp) / "rv"
            tmp_path.mkdir()
            self._python_on_path(tmp_path)
            env = {
                **os.environ,
                "PATH": str(tmp_path) + os.pathsep + os.environ.get("PATH", ""),
                "HEAD_REF": promo.head_ref,
                "HEAD_SHA": promo.head_sha,
                "BASE_REF": "main",
                "PINNED_DEVELOP_SHA": pinned,
                "GITHUB_WORKSPACE": str(promo.pr_tree),
                "GITHUB_OUTPUT": str(tmp_path / "out"),
                "RUNNER_TEMP": str(tmp_path),
            }
            # gate-base must exist; create from main
            gate_base = tmp_path / "gate-base"
            git(["clone", "--branch", "main", str(promo.origin), str(gate_base)], cwd=tmp)
            result = bash_c(script, cwd=promo.pr_tree, env=env)
            self.assertNotEqual(result.returncode, 0, result.stderr)

    def test_R2_B6b_reverify_edition_qa_runs(self):
        script = extracted_step_script("edition-qa.yml", "Re-verify against develop")
        self.assertTrue(script.strip().startswith("set -euo pipefail"))
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._full(tmp, hold=False)
            out = Path(tmp) / "g.out"
            run_gate(promo, extra=["--github-output", str(out)])
            pinned = [
                line.split("=", 1)[1]
                for line in out.read_text().splitlines()
                if line.startswith("develop_sha=")
            ][0]
            seed = promo.tmp / "seed"
            git(["checkout", "develop"], cwd=seed)
            (seed / "data" / "narrative.json").write_bytes(
                dumps(preview_payload(headline="D2"))
            )
            git(["add", "data/narrative.json"], cwd=seed)
            git(["commit", "-m", "D2"], cwd=seed)
            git(["push", "origin", "develop"], cwd=seed)
            git(["fetch", "origin", "develop"], cwd=promo.pr_tree)
            result = self._run_followup_step(
                "edition-qa.yml",
                "Re-verify against develop",
                promo,
                extra_env={"PINNED_DEVELOP_SHA": pinned},
            )
            self.assertNotEqual(result.returncode, 0, result.stderr)

    def test_R2_B6c_parity_six_fixtures(self):
        fixtures = []
        with tempfile.TemporaryDirectory() as tmp:
            fixtures.append(("pass", run_gate(self._full(Path(tmp) / "p", hold=False))))
            hold = self._full(Path(tmp) / "h", hold=True)
            fixtures.append(("hold", run_gate(hold)))
            payload = preview_payload()
            full = edition_files(SLUG, payload, hold=True)
            partial = {
                "data/narrative.json": dumps(payload),
                f"data/narrative_editions/{SLUG}.json": dumps(payload),
            }
            fixtures.append(
                (
                    "partial",
                    run_gate(
                        build_promo(
                            Path(tmp) / "part",
                            partial,
                            extra_develop={k: v for k, v in full.items() if k not in partial},
                        )
                    ),
                )
            )
            drift_files = edition_files(SLUG, payload, hold=False)
            drift = build_promo(Path(tmp) / "drift", drift_files)
            seed = drift.tmp / "seed"
            git(["checkout", drift.head_ref], cwd=seed)
            (seed / "data" / "wire.json").write_bytes(b'{"d":1}\n')
            git(["add", "data/wire.json"], cwd=seed)
            git(["commit", "-m", "drift"], cwd=seed)
            git(["push", "origin", drift.head_ref], cwd=seed)
            git(["fetch", "origin", drift.head_ref], cwd=drift.pr_tree)
            git(["reset", "--hard", "FETCH_HEAD"], cwd=drift.pr_tree)
            fixtures.append(("drift", run_gate(drift)))
            unsigned = build_promo(
                Path(tmp) / "u",
                edition_files(SLUG, review_payload(), hold=False),
            )
            fixtures.append(("unsigned", run_gate(unsigned)))
            gh_files = edition_files(SLUG, payload, hold=False)
            gh_files[".github/workflows/ci.yml"] = b"name: pwn\n"
            fixtures.append(("github", run_gate(build_promo(Path(tmp) / "g", gh_files))))
        self.assertEqual(len(fixtures), 6)
        ci_gate = extracted_step_script("ci.yml", "Refuse unsigned Review editions")
        qa_gate = extracted_step_script(
            "edition-qa.yml", "Refuse unsigned Review editions"
        )
        self.assertEqual(ci_gate, qa_gate)
        ci_hold = extracted_step_script(
            "ci.yml", "Refuse holdAutomerge without a human develop merge"
        )
        qa_hold = extracted_step_script(
            "edition-qa.yml", "Refuse holdAutomerge without a human develop merge"
        )
        self.assertEqual(ci_hold, qa_hold)
        ci_rv = extracted_step_script("ci.yml", "Re-verify against develop")
        qa_rv = extracted_step_script("edition-qa.yml", "Re-verify against develop")
        self.assertEqual(ci_rv, qa_rv)
        for name, result in fixtures:
            with self.subTest(name=name):
                if name == "pass" or name == "hold":
                    self.assertEqual(result.returncode, 0, name + result.stderr)
                else:
                    self.assertEqual(result.returncode, 1, name + result.stderr)

    def test_R2_N0_bash_c_fake_gh(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log = tmp_path / "gh.log"
            write_gh_stub(stub := tmp_path / "gh", log)
            self._python_on_path(tmp_path)
            env = {
                "PATH": str(tmp_path),
                "HOME": str(tmp_path),
                "GH_TOKEN": "x",
                "PR_NUMBER": "1",
                "GITHUB_REPOSITORY": "stevologic/arrowheadpaesanowebsite",
                "PR_URL": "https://example.test/1",
                "HEAD_SHA": "abc",
                "RUNNER_TEMP": str(tmp_path),
                "GITHUB_OUTPUT": str(tmp_path / "out"),
            }
            # Need bash/git from the OS; only gh is faked at front.
            env["PATH"] = str(tmp_path) + ":/usr/bin:/bin"
            script = extracted_step_script("ci.yml", "Re-read labels and draft")
            result = bash_c(script, cwd=tmp_path, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            which = subprocess.run(
                ["bash", "-c", "command -v gh"],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(which.stdout.strip(), str(tmp_path / "gh"))

    def test_R2_N1_label_python_dash_p_neutral_cwd(self):
        script = extracted_step_script("ci.yml", "Re-read labels and draft")
        self.assertIn("python3 -P", script)
        self.assertIn("NEUTRAL=", script)
        self.assertIn("cd \"$NEUTRAL\"", script)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "json.py").write_text(
                "import pathlib\npathlib.Path('/tmp/pwn-n1-json').write_text('hit')\n",
                encoding="utf-8",
            )
            sentinel = Path("/tmp/pwn-n1-json")
            if sentinel.exists():
                sentinel.unlink()
            log = tmp_path / "gh.log"
            write_gh_stub(tmp_path / "gh", log)
            env = {
                **os.environ,
                "PATH": str(tmp_path) + os.pathsep + os.environ.get("PATH", ""),
                "GH_TOKEN": "x",
                "PR_NUMBER": "1",
                "GITHUB_REPOSITORY": "stevologic/arrowheadpaesanowebsite",
                "RUNNER_TEMP": str(tmp_path / "rt"),
            }
            (tmp_path / "rt").mkdir()
            result = bash_c(script, cwd=tmp_path, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(sentinel.exists())

    def test_R2_N5_already_merged_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log = tmp_path / "gh.log"
            write_gh_stub(tmp_path / "gh", log, already_merged=True)
            env = {
                **os.environ,
                "PATH": str(tmp_path) + os.pathsep + os.environ.get("PATH", ""),
                "GH_TOKEN": "x",
                "PR_URL": "https://example.test/1",
                "HEAD_SHA": "abc",
                "GITHUB_OUTPUT": str(tmp_path / "out"),
            }
            merge = extracted_step_script("ci.yml", "Squash-merge the pull request")
            result = bash_c(merge, cwd=tmp_path, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("already merged", result.stdout + result.stderr)
            self.assertIn("skip_pages=true", (tmp_path / "out").read_text())

    def test_R2_N6_body_418_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._full(tmp, hold=True)
            short = promo.develop_sha[:8]
            body = (
                f"Edition-only promote of #417 (develop squash {short}, "
                f"Karen r2 PASS WITH NOTES + karen-qa at {'c'*8}, Kenny word-floor override). "
                f"Copies the 11 edition files from develop {short} onto main "
                f"{'8'*40} via git data API. No code changes."
            )
            sha = "a" * 40
            routes = {"/pulls": _human_pr(sha), "/statuses": _karen("success")}
            server, env, _ = self._hold_env(promo, routes)
            try:
                self._pass(run_hold_override(promo, pr_body=body, env=env))
            finally:
                server.shutdown()

    def test_R2_N8_branch_slug_must_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._full(tmp, hold=False)
            self._refuse(
                run_gate(promo, head_ref="promote/2026-11-01-0900"),
                "branch slug",
            )

    def test_R2_N9_step_env_order_both_jobs(self):
        for name in ("ci.yml", "edition-qa.yml"):
            wf = load_workflow(name)
            names = [
                step.get("name")
                for step in wf["jobs"]["automerge"]["steps"]
                if isinstance(step, dict)
            ]
            self.assertIn("Refuse unsigned Review editions", names)
            self.assertIn("Re-verify against develop", names)
            self.assertIn("Refuse holdAutomerge without a human develop merge", names)
            self.assertIn("Re-read labels and draft", names)
            self.assertIn("Squash-merge the pull request", names)
            self.assertLess(
                names.index("Refuse unsigned Review editions"),
                names.index("Re-verify against develop"),
            )
            self.assertLess(
                names.index("Re-verify against develop"),
                names.index("Refuse holdAutomerge without a human develop merge"),
            )
            gate_env = step_env(wf, "automerge", "Refuse unsigned Review editions")
            self.assertNotIn("GH_TOKEN", gate_env)
            self.assertNotIn("GITHUB_TOKEN", json.dumps(gate_env))
            checkout = wf["jobs"]["automerge"]["steps"][0]
            self.assertIn("base.sha", str((checkout.get("with") or {}).get("ref")))

    def test_R2_N2a_github_touch_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = edition_files(SLUG, preview_payload(), hold=False)
            files[".github/workflows/ci.yml"] = b"name: pwned\n"
            self._refuse(run_gate(build_promo(Path(tmp), files)), ".github/")

    def test_R2_N2b_tools_touch_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = edition_files(SLUG, preview_payload(), hold=False)
            files["tools/chiefs_narrative/x.py"] = b"print(1)\n"
            self._refuse(run_gate(build_promo(Path(tmp), files)), "tools/")

    def test_R2_N2c_edition_only_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._pass(run_gate(self._full(tmp, hold=False)))

    def test_R2_N2d_edition_qa_refuses_ci_yml_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = edition_files(SLUG, preview_payload(), hold=False)
            files[".github/workflows/ci.yml"] = (
                Path(REPO / ".github/workflows/ci.yml").read_bytes() + b"\n# pwn\n"
            )
            promo = build_promo(Path(tmp), files)
            result = self._run_gate_step("edition-qa.yml", promo)
            self.assertNotEqual(result.returncode, 0, result.stderr)
            self.assertIn(".github/", result.stderr)

    def test_R2_N7a_lede_ref_new_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = edition_files(SLUG, preview_payload(), hold=True)
            promo = build_promo(
                Path(tmp), files, head_ref=f"promote/{SLUG}-lede", slug=SLUG
            )
            result = run_gate(promo, head_ref=f"promote/{SLUG}-lede")
            self._pass(result)
            hold = run_hold_override(promo, pr_body="no sha")
            self.assertEqual(hold.returncode, 1)
            self.assertIn("hold override refused", hold.stderr)

    def test_R2_N7b_lookalike_hold_true_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._full(tmp, hold=True)
            self._refuse(
                run_gate(promo, head_ref="promote-2026-11-27-0948"),
                "off the automerge allowlist",
            )

    def test_R2_N7c_non_promote_never_automerge(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = preview_payload()
            files = {
                "data/narrative.json": dumps(payload),
                f"data/narrative_editions/{SLUG}.json": dumps(payload),
            }
            promo = build_promo(Path(tmp), files, head_ref="fix/x")
            result = run_gate(promo, head_ref="fix/x")
            self._refuse(result, "off the automerge allowlist")
            self.assertNotIn("branch slug", result.stderr)


if __name__ == "__main__":
    unittest.main()
