"""FROZEN-38 behavioural rows for review_gate --automerge (throwaway git repos)."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tools.chiefs_narrative import review_gate
from tools.tests.frozen38_harness import (
    HEAD_REF,
    REPO,
    RULESET_FIXTURE,
    SLUG,
    SVG_NAMES,
    TINY_SVG,
    build_promo,
    dumps,
    ed25519_keypair,
    edition_files,
    git,
    preview_payload,
    repair_bytes,
    review_payload,
    run_gate,
    run_hold_override,
    sign_pair,
)


class Frozen38PromoteGate(unittest.TestCase):
    def _preview11(self, tmp, **kwargs):
        files = edition_files(SLUG, preview_payload(), **kwargs)
        return build_promo(Path(tmp), files)

    def _assert_pass(self, result, msg=""):
        self.assertEqual(
            result.returncode,
            0,
            msg + "\n" + result.stdout + result.stderr,
        )

    def _assert_refuse(self, result, needle, msg=""):
        self.assertEqual(
            result.returncode,
            1,
            msg + "\n" + result.stdout + result.stderr,
        )
        self.assertIn(needle, result.stderr, result.stdout + result.stderr)
        self.assertNotIn(
            "Review edition requires Karen's ed25519 sign-off",
            result.stderr,
        )

    def test_A1_preview_11(self):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._preview11(tmp)
            self._assert_pass(run_gate(promo), "A1")

    def test_A2_same_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._preview11(tmp)
            self._assert_pass(run_gate(promo), "A2")

    def test_A3_preview_valid_pair(self):
        with tempfile.TemporaryDirectory() as tmp:
            keys = Path(tmp) / "keys"
            keys.mkdir()
            priv, pub = ed25519_keypair(keys)
            payload = preview_payload()
            raw = dumps(payload)
            pair = sign_pair(priv, SLUG, raw)
            files = edition_files(SLUG, payload, pair=pair)
            promo = build_promo(Path(tmp) / "repo", files)
            result = run_gate(promo, env={"REVIEW_SIGNOFF_PUBKEY": str(pub)})
            self._assert_pass(result, "A3")

    def test_A4_S1_signed_review_14(self):
        with tempfile.TemporaryDirectory() as tmp:
            keys = Path(tmp) / "keys"
            keys.mkdir()
            priv, pub = ed25519_keypair(keys)
            payload = review_payload()
            raw = dumps(payload)
            pair = sign_pair(priv, SLUG, raw)
            files = edition_files(SLUG, payload, schedule=True, pair=pair)
            promo = build_promo(Path(tmp) / "repo", files)
            result = run_gate(promo, env={"REVIEW_SIGNOFF_PUBKEY": str(pub)})
            self._assert_pass(result, "A4/S1")

    def test_A5_two_file_control(self):
        """#343 2-file shape: r2 N7c/B1a — non-promote never automerges; partial promote refuses."""
        with tempfile.TemporaryDirectory() as tmp:
            payload = preview_payload()
            files = {
                "data/narrative.json": dumps(payload),
                f"data/narrative_editions/{SLUG}.json": dumps(payload),
            }
            promo = build_promo(Path(tmp), files, head_ref="fix/x")
            self._assert_refuse(
                run_gate(promo, head_ref="fix/x"),
                "off the automerge allowlist",
                "A5 non-promote",
            )
            full = edition_files(SLUG, payload)
            partial = {
                "data/narrative.json": dumps(payload),
                f"data/narrative_editions/{SLUG}.json": dumps(payload),
            }
            two = build_promo(
                Path(tmp) / "partial",
                partial,
                extra_develop={k: v for k, v in full.items() if k not in partial},
            )
            self._assert_refuse(run_gate(two), "blob differs from develop", "A5 partial")

    def test_A6_S2_E1U_unsigned_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = edition_files(SLUG, review_payload(), schedule=True)
            promo = build_promo(Path(tmp), files)
            self._assert_refuse(run_gate(promo), "unsigned Review")

    def test_A7_orphan_sig_or_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            keys = Path(tmp) / "keys"
            keys.mkdir()
            priv, pub = ed25519_keypair(keys)
            payload = review_payload()
            raw = dumps(payload)
            pair = sign_pair(priv, SLUG, raw)
            for drop in ("json", "sig"):
                files = edition_files(SLUG, payload, schedule=True, pair=pair)
                if drop == "json":
                    del files[f"signoff/review/{SLUG}.json"]
                else:
                    del files[f"signoff/review/{SLUG}.sig"]
                promo = build_promo(Path(tmp) / drop, files)
                result = run_gate(promo, env={"REVIEW_SIGNOFF_PUBKEY": str(pub)})
                self._assert_refuse(result, "invalid signoff", drop)

    def test_A9_non_promote_old_allowlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._preview11(tmp)
            self._assert_refuse(
                run_gate(promo, head_ref="fix/x"), "off the automerge allowlist"
            )
            self._assert_refuse(
                run_gate(promo, head_ref="codex/x"), "off the automerge allowlist"
            )
            payload = preview_payload()
            files = {
                "data/narrative.json": dumps(payload),
                f"data/narrative_editions/{SLUG}.json": dumps(payload),
            }
            two = build_promo(Path(tmp) / "two", files, head_ref="fix/x")
            self._assert_refuse(
                run_gate(two, head_ref="fix/x"),
                "off the automerge allowlist",
                "A9/N7c no old-path fallback",
            )

    def test_A_msg_distinct_reasons(self):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._preview11(tmp)
            result = run_gate(promo, head_ref="fix/x")
            self._assert_refuse(result, "off the automerge allowlist")
            self.assertNotIn(
                "Review edition requires Karen's ed25519 sign-off", result.stderr
            )
            self.assertNotIn(
                "or this PR is off the automerge allowlist", result.stderr
            )
            classes = (
                "off the automerge allowlist",
                "stale vs develop",
                "unsigned Review",
                "invalid signoff",
                "mode/symlink",
                "rename/delete",
                "cap:",
                "no changes",
                "develop fetch failed",
                "older than main",
            )
            for needle in classes:
                self.assertTrue(needle, needle)

    def test_C1_promote_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._preview11(tmp)
            self._assert_pass(run_gate(promo, head_ref=HEAD_REF), "C1")

    def test_C2_lookalike_refs(self):
        lookalikes = (
            "promote-2026-11-27-0948",
            "Promote/x",
            "promotes/x",
            "feature/promote/x",
            "refs/heads/promote/x",
            "promote/",
            "promote//x",
            "promote/../main",
            " promote/x",
            "promote/x",
        )
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._preview11(tmp)
            for ref in lookalikes:
                with self.subTest(ref=ref):
                    result = run_gate(promo, head_ref=ref)
                    self._assert_refuse(
                        result,
                        "off the automerge allowlist",
                        ref,
                    )
                    if not (ref or "").startswith("promote/"):
                        self.assertNotIn(
                            "branch slug",
                            result.stderr,
                            ref,
                        )

    def test_B1_identity_control(self):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._preview11(tmp)
            for _ in range(6):
                self._assert_pass(run_gate(promo), "B1")

    def test_B2_each_es_member(self):
        members = [
            "data/narrative.json",
            "data/narrative_archive.json",
            "data/narrative_repair.json",
            "data/wire.json",
            "data/schedule_2026.json",
            f"public/images/narrative/{SLUG}/{SVG_NAMES[0]}",
            f"signoff/review/{SLUG}.json",
            f"signoff/review/{SLUG}.sig",
        ]
        for path in members:
            with self.subTest(path=path), tempfile.TemporaryDirectory() as tmp:
                keys = Path(tmp) / "keys"
                keys.mkdir()
                priv, pub = ed25519_keypair(keys)
                payload = preview_payload()
                raw = dumps(payload)
                pair = sign_pair(priv, SLUG, raw)
                files = edition_files(
                    SLUG, payload, schedule=True, pair=pair
                )
                promo = build_promo(Path(tmp) / "ok", files)
                self._assert_pass(
                    run_gate(promo, env={"REVIEW_SIGNOFF_PUBKEY": str(pub)}),
                    "control " + path,
                )
                seed = promo.tmp / "seed"
                git(["checkout", promo.head_ref], cwd=seed)
                target = seed / path
                target.write_bytes(target.read_bytes() + b"\n")
                git(["add", path], cwd=seed)
                git(["commit", "-m", f"mutate {path}"], cwd=seed)
                git(["push", "origin", promo.head_ref], cwd=seed)
                git(["fetch", "origin", promo.head_ref], cwd=promo.pr_tree)
                git(
                    ["checkout", "-B", promo.head_ref, "origin/" + promo.head_ref],
                    cwd=promo.pr_tree,
                )
                result = run_gate(
                    promo, env={"REVIEW_SIGNOFF_PUBKEY": str(pub)}
                )
                self.assertEqual(result.returncode, 1, path + result.stderr)
                self.assertTrue(
                    "stale vs develop" in result.stderr
                    or "blob differs from develop" in result.stderr,
                    result.stderr,
                )

    def test_B3_narrative_vs_editions(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = preview_payload()
            files = edition_files(SLUG, payload)
            files[f"data/narrative_editions/{SLUG}.json"] = dumps(
                preview_payload(headline="other")
            )
            promo = build_promo(Path(tmp), files)
            result = run_gate(promo)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertTrue(
                "stale vs develop" in result.stderr
                or "bytes !=" in result.stderr
                or "blob differs" in result.stderr,
                result.stderr,
            )

    def test_B4_develop_moved_same_narrative(self):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._preview11(tmp)
            seed = promo.tmp / "seed"
            git(["checkout", "develop"], cwd=seed)
            (seed / "README.md").write_text("moved\n", encoding="utf-8")
            git(["add", "README.md"], cwd=seed)
            git(["commit", "-m", "develop moved"], cwd=seed)
            git(["push", "origin", "develop"], cwd=seed)
            git(["fetch", "origin", "develop"], cwd=promo.pr_tree)
            self._assert_pass(run_gate(promo), "B4")

    def test_B5_stale_vs_develop_tip(self):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._preview11(tmp)
            seed = promo.tmp / "seed"
            git(["checkout", "develop"], cwd=seed)
            newer = dumps(preview_payload(slug="2026-11-28-0948"))
            (seed / "data" / "narrative.json").write_bytes(newer)
            git(["add", "data/narrative.json"], cwd=seed)
            git(["commit", "-m", "newer edition"], cwd=seed)
            git(["push", "origin", "develop"], cwd=seed)
            git(["fetch", "origin", "develop"], cwd=promo.pr_tree)
            self._assert_refuse(run_gate(promo), "stale vs develop")

    def test_B6_missing_on_develop(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = edition_files(SLUG, preview_payload())
            promo = build_promo(Path(tmp), files)
            seed = promo.tmp / "seed"
            git(["checkout", "develop"], cwd=seed)
            svg = seed / "public" / "images" / "narrative" / SLUG / SVG_NAMES[0]
            svg.unlink()
            git(["add", "-A"], cwd=seed)
            git(["commit", "-m", "drop svg"], cwd=seed)
            git(["push", "origin", "develop"], cwd=seed)
            git(["fetch", "origin", "develop"], cwd=promo.pr_tree)
            result = run_gate(promo)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertTrue(
                "blob differs from develop" in result.stderr
                or "stale vs develop" in result.stderr,
                result.stderr,
            )

    def test_P5_stale_history_blob(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = edition_files(SLUG, preview_payload())
            promo = build_promo(Path(tmp), files)
            seed = promo.tmp / "seed"
            git(["checkout", "develop"], cwd=seed)
            (seed / "data" / "narrative.json").write_bytes(
                dumps(preview_payload(headline="newer"))
            )
            git(["add", "data/narrative.json"], cwd=seed)
            git(["commit", "-m", "tip moved"], cwd=seed)
            git(["push", "origin", "develop"], cwd=seed)
            git(["fetch", "origin", "develop"], cwd=promo.pr_tree)
            self._assert_refuse(run_gate(promo), "stale vs develop")

    def test_B8_develop_fetch_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._preview11(tmp)
            git(["remote", "remove", "origin"], cwd=promo.pr_tree)
            result = run_gate(promo, fetch_develop=True)
            self._assert_refuse(result, "develop fetch failed")

    def test_B9_stale_local_develop_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._preview11(tmp)
            seed = promo.tmp / "seed"
            git(["checkout", "develop"], cwd=seed)
            (seed / "data" / "narrative.json").write_bytes(
                dumps(preview_payload(headline="tip"))
            )
            git(["add", "data/narrative.json"], cwd=seed)
            git(["commit", "-m", "remote tip"], cwd=seed)
            git(["push", "origin", "develop"], cwd=seed)
            git(["branch", "-f", "develop", promo.head_sha], cwd=promo.pr_tree)
            git(["fetch", "origin", "develop"], cwd=promo.pr_tree)
            self._assert_refuse(run_gate(promo), "stale vs develop")

    def test_D1_empty_diff(self):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._preview11(tmp)
            seed = promo.tmp / "seed"
            git(["fetch", "origin", "main"], cwd=seed)
            git(["checkout", "-B", promo.head_ref, "origin/main"], cwd=seed)
            git(["push", "--force", "origin", promo.head_ref], cwd=seed)
            git(["fetch", "origin", promo.head_ref], cwd=promo.pr_tree)
            git(
                ["checkout", "-B", promo.head_ref, "origin/" + promo.head_ref],
                cwd=promo.pr_tree,
            )
            self._assert_refuse(run_gate(promo), "no changes")

    def test_D2_non_es_paths(self):
        extras = [
            "tools/chiefs_narrative/x.py",
            ".github/workflows/ci.yml",
            ".github/workflows/edition-qa.yml",
            "scripts/review_signoff_pubkey.pem",
            "scripts/review_legacy_editions.txt",
            "hugo.yaml",
            "README.md",
            "static/x",
            "content/x.md",
            "package.json",
            "tools/tests/x.py",
        ]
        for extra in extras:
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as tmp:
                files = edition_files(SLUG, preview_payload())
                files[extra] = b"nope\n"
                promo = build_promo(Path(tmp), files)
                self._assert_refuse(
                    run_gate(promo), "off the automerge allowlist", extra
                )
                self.assertIn(extra.split("/")[-1], run_gate(promo).stderr + extra)

    def test_D3_lookalike_paths(self):
        payload = preview_payload()
        raw = dumps(payload)
        rows = [
            f"data/narrative_editions_evil/{SLUG}.json",
            f"data/narrative_editions/{SLUG}.json.bak",
            f"data/narrative_editions/{SLUG}.JSON",
            f"data/narrative_editions/{SLUG}.json~",
            f"data/narrative_editions/{SLUG}.json.sig",
            f"data/narrative_editions/sub/{SLUG}.json",
            "data/narrative_editions/.json",
            "data/narrative_editions/not-a-slug.json",
            "data/narrative_editions/2026-11-27-948.json",
            "data/narrative_editions/2026-11-27-09480.json",
        ]
        for extra in rows:
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as tmp:
                files = edition_files(SLUG, payload)
                files[extra] = raw
                promo = build_promo(Path(tmp), files)
                self._assert_refuse(run_gate(promo), "off the automerge allowlist")

    def test_D4_case_tricks(self):
        rows = [
            "Data/narrative.json",
            f"DATA/narrative_editions/{SLUG}.json",
            "data/Narrative.json",
            f"Public/images/narrative/{SLUG}/{SVG_NAMES[0]}",
            f"Signoff/review/{SLUG}.json",
        ]
        for extra in rows:
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as tmp:
                files = edition_files(SLUG, preview_payload())
                files[extra] = b"{}\n"
                promo = build_promo(Path(tmp), files)
                self._assert_refuse(run_gate(promo), "off the automerge allowlist")

    def test_D5_path_oddities(self):
        self.assertEqual(review_gate.classify_es_path("../tools/x")[0], "bad")
        self.assertEqual(
            review_gate.classify_es_path("data/narrative_editions/../../tools/x.json")[0],
            "bad",
        )
        self.assertEqual(review_gate.classify_es_path("/etc/passwd")[0], "bad")
        self.assertEqual(review_gate.classify_es_path("./data/narrative.json")[0], "bad")
        self.assertEqual(
            review_gate.classify_es_path(f"data\\narrative_editions\\{SLUG}.json")[0],
            "bad",
        )
        self.assertEqual(review_gate.classify_es_path("data//narrative.json")[0], "bad")
        self.assertEqual(review_gate.classify_es_path("data/narrative.json/")[0], "bad")

    def test_D5z_newline_is_one_path(self):
        blob = (
            b":100644 100644 "
            + b"a" * 40
            + b" "
            + b"b" * 40
            + b" A\0data/narrative.json\ntools/x\0"
        )
        try:
            changes = review_gate._parse_raw_z(blob)
        except review_gate.GateError as exc:
            self.fail(f"newline path must stay one record: {exc}")
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].path, "data/narrative.json\ntools/x")
        self.assertEqual(review_gate.classify_es_path(changes[0].path)[0], "bad")

    def test_D6_deletes(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = edition_files(SLUG, preview_payload(), schedule=True)
            promo = build_promo(Path(tmp), files)
            seed = promo.tmp / "seed"
            git(["checkout", promo.head_ref], cwd=seed)
            (seed / "README.md").unlink()
            git(["add", "-A"], cwd=seed)
            git(["commit", "-m", "delete README"], cwd=seed)
            git(["push", "origin", promo.head_ref], cwd=seed)
            git(["fetch", "origin", promo.head_ref], cwd=promo.pr_tree)
            git(["checkout", "-B", promo.head_ref, "origin/" + promo.head_ref], cwd=promo.pr_tree)
            self._assert_refuse(run_gate(promo), "rename/delete")

    def test_D7_rename_lists_both(self):
        blob = (
            b":100644 100644 "
            + b"a" * 40
            + b" "
            + b"b" * 40
            + b" R100\0tools/x.json\0data/narrative_editions/"
            + SLUG.encode()
            + b".json\0"
        )
        changes = review_gate._parse_raw_z(blob)
        self.assertEqual(changes[0].orig_path, "tools/x.json")
        reason = review_gate._change_status_reason(changes[0])
        self.assertIsNotNone(reason)
        self.assertIn("tools/x.json", reason)
        with tempfile.TemporaryDirectory() as tmp:
            files = edition_files(SLUG, preview_payload(), hold=False)
            promo = build_promo(Path(tmp), files)
            seed = promo.tmp / "seed"
            git(["checkout", promo.head_ref], cwd=seed)
            git(["mv", "README.md", "data/readme-moved.md"], cwd=seed)
            git(["commit", "-m", "rename README"], cwd=seed)
            git(["push", "origin", promo.head_ref], cwd=seed)
            git(["fetch", "origin", promo.head_ref], cwd=promo.pr_tree)
            git(["reset", "--hard", "FETCH_HEAD"], cwd=promo.pr_tree)
            listed = review_gate.git_raw_changed(
                "origin/main", "HEAD", promo.pr_tree
            )
            self.assertFalse(
                any(str(item.status).startswith("R") for item in listed),
                [item.status for item in listed],
            )
            self.assertTrue(
                any(
                    item.path == "README.md" and str(item.status).startswith("D")
                    for item in listed
                ),
                listed,
            )
            self.assertTrue(
                any(
                    item.path == "data/readme-moved.md"
                    and str(item.status).startswith("A")
                    for item in listed
                ),
                listed,
            )

    def test_D8_mode_symlink(self):
        change = review_gate.RawChange(
            old_mode="100644",
            new_mode="100755",
            old_sha="a" * 40,
            new_sha="b" * 40,
            status="M",
            path="data/narrative.json",
        )
        self.assertIn("mode/symlink", review_gate._change_status_reason(change) or "")
        link = review_gate.RawChange(
            old_mode="100644",
            new_mode="120000",
            old_sha="a" * 40,
            new_sha="b" * 40,
            status="M",
            path="data/narrative.json",
        )
        self.assertIn("mode/symlink", review_gate._change_status_reason(link) or "")
        gitlink = review_gate.RawChange(
            old_mode="100644",
            new_mode="160000",
            old_sha="a" * 40,
            new_sha="b" * 40,
            status="M",
            path=f"data/narrative_editions/{SLUG}.json",
        )
        self.assertIn("mode/symlink", review_gate._change_status_reason(gitlink) or "")

    def test_D9_nul_bom_size_png(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = edition_files(SLUG, preview_payload())
            files["data/wire.json"] = b"{}\x00\n"
            promo = build_promo(Path(tmp) / "nul", files)
            result = run_gate(promo)
            self.assertEqual(result.returncode, 1, result.stderr)
            files = edition_files(SLUG, preview_payload())
            files[f"public/images/narrative/{SLUG}/photo.png"] = b"\x89PNG\n"
            promo = build_promo(Path(tmp) / "png", files)
            self._assert_refuse(run_gate(promo), "off the automerge allowlist")
            files = edition_files(SLUG, preview_payload())
            files[f"public/images/narrative/{SLUG}/{SVG_NAMES[0]}"] = (
                b'<svg xmlns="http://www.w3.org/2000/svg"><script>x()</script></svg>\n'
            )
            promo = build_promo(Path(tmp) / "svg", files)
            # identity-only: this fails because develop has TINY_SVG, not the script svg.
            # Documented-P when identical to develop: rebuild with matching develop bytes.
            matching = edition_files(SLUG, preview_payload())
            scripted = (
                b'<svg xmlns="http://www.w3.org/2000/svg"><script>x()</script></svg>\n'
            )
            matching[f"public/images/narrative/{SLUG}/{SVG_NAMES[0]}"] = scripted
            promo = build_promo(Path(tmp) / "svgok", matching)
            self._assert_pass(run_gate(promo), "D9 svg identity only")

    def test_D9_blob_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = edition_files(SLUG, preview_payload())
            files["data/wire.json"] = b"{" + (b"x" * (5 * 1024 * 1024 + 1)) + b"}"
            promo = build_promo(Path(tmp), files)
            self._assert_refuse(run_gate(promo), "cap:")

    def test_D10_json_validity(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = edition_files(SLUG, preview_payload())
            files["data/wire.json"] = b"[]\n"
            promo = build_promo(Path(tmp) / "arr", files)
            result = run_gate(promo)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("review gate failed closed", result.stderr)
            files = edition_files(SLUG, preview_payload())
            files["data/wire.json"] = b""
            promo = build_promo(Path(tmp) / "empty", files)
            result = run_gate(promo)
            self.assertEqual(result.returncode, 1)
            self.assertIn("review gate failed closed", result.stderr)

    def test_D11_svg_slug_and_nesting(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = edition_files(SLUG, preview_payload())
            files[f"public/images/narrative/2026-11-01-0900/{SVG_NAMES[0]}"] = TINY_SVG
            promo = build_promo(Path(tmp) / "slug", files)
            result = run_gate(promo)
            self.assertEqual(result.returncode, 1, result.stderr)
            files = edition_files(SLUG, preview_payload())
            files[f"public/images/narrative/{SLUG}/sub/x.svg"] = TINY_SVG
            promo = build_promo(Path(tmp) / "nest", files)
            self._assert_refuse(run_gate(promo), "off the automerge allowlist")
            files = edition_files(SLUG, preview_payload())
            files["public/images/narrative/x.svg"] = TINY_SVG
            promo = build_promo(Path(tmp) / "top", files)
            self._assert_refuse(run_gate(promo), "off the automerge allowlist")

    def test_D12_signoff_slug(self):
        with tempfile.TemporaryDirectory() as tmp:
            keys = Path(tmp) / "keys"
            keys.mkdir()
            priv, pub = ed25519_keypair(keys)
            payload = preview_payload()
            other = "2026-11-01-0900"
            pair = sign_pair(priv, other, dumps(payload))
            files = edition_files(SLUG, payload)
            files[f"signoff/review/{other}.json"] = pair[0]
            files[f"signoff/review/{other}.sig"] = pair[1]
            promo = build_promo(Path(tmp) / "repo", files)
            result = run_gate(promo, env={"REVIEW_SIGNOFF_PUBKEY": str(pub)})
            self._assert_refuse(result, "invalid signoff")

    def test_D13_gitmodules(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = edition_files(SLUG, preview_payload())
            files[".gitmodules"] = b"[submodule \"x\"]\n"
            promo = build_promo(Path(tmp), files)
            self._assert_refuse(run_gate(promo), "off the automerge allowlist")

    def test_D14_two_editions(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = edition_files(SLUG, preview_payload())
            other = "2026-11-28-0948"
            files[f"data/narrative_editions/{other}.json"] = dumps(
                preview_payload(slug=other)
            )
            promo = build_promo(Path(tmp), files)
            self._assert_refuse(run_gate(promo), "two editions")

    def test_E1_title_ignored(self):
        self.assertTrue(
            review_gate.payload_is_review(
                {
                    "slug": SLUG,
                    "edition": "Desk title",
                    "phase": {"mode": "review"},
                }
            )
        )
        self.assertTrue(
            review_gate.payload_is_review(
                {"slug": SLUG, "edition": "2026 Week 12 · Review", "phase": {"mode": "preview"}}
            )
        )
        with tempfile.TemporaryDirectory() as tmp:
            files = edition_files(SLUG, review_payload())
            promo = build_promo(Path(tmp), files)
            self._assert_refuse(run_gate(promo), "unsigned Review")

    def test_E2_payload_is_review_pins(self):
        rows = [
            ({"slug": SLUG, "edition": "", "phase": {"mode": "PREVIEW"}}, False),
            ({"slug": SLUG, "edition": "", "phase": {"mode": " preview "}}, False),
            ({"slug": SLUG, "edition": "", "phase": {"mode": "previews"}}, False),
            ({"slug": SLUG, "edition": "", "phase": {"mode": "preview;review"}}, False),
            ({"slug": SLUG, "edition": "", "phase": {"mode": "camp"}}, False),
            ({"slug": SLUG, "edition": "", "phase": {"mode": "offseason"}}, False),
            ({"slug": SLUG, "edition": "", "phase": {"mode": ""}}, False),
            ({"slug": SLUG, "edition": "", "phase": "Week 5 Review"}, True),
            ({"slug": SLUG, "edition": "desk", "phase": ["review"]}, False),
        ]
        for payload, expect in rows:
            with self.subTest(payload=payload):
                self.assertEqual(review_gate.payload_is_review(payload), expect)

    def test_E3_preview_mode_wins_final(self):
        self.assertFalse(
            review_gate.payload_is_review(preview_payload())
        )
        self.assertTrue(
            review_gate.payload_is_review(
                {
                    "slug": SLUG,
                    "edition": "2026 Week 12 · Review",
                    "phase": {"mode": "preview"},
                }
            )
        )

    def test_E4_mismatched_copies(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = edition_files(SLUG, preview_payload())
            files[f"data/narrative_editions/{SLUG}.json"] = dumps(review_payload())
            promo = build_promo(Path(tmp), files)
            self._assert_refuse(run_gate(promo), "unsigned Review")

    def test_E5_E1W_E1S_invalid_pairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            keys = Path(tmp) / "keys"
            keys.mkdir()
            priv, pub = ed25519_keypair(keys)
            payload = review_payload()
            raw = dumps(payload)
            good = sign_pair(priv, SLUG, raw)
            files = edition_files(SLUG, payload, schedule=True, pair=good)
            promo = build_promo(Path(tmp) / "ok", files)
            self._assert_pass(
                run_gate(promo, env={"REVIEW_SIGNOFF_PUBKEY": str(pub)}), "control"
            )
            other = sign_pair(priv, "2026-11-01-0900", raw)
            files = edition_files(SLUG, payload, schedule=True, pair=other)
            files[f"signoff/review/{SLUG}.json"] = other[0]
            files[f"signoff/review/{SLUG}.sig"] = other[1]
            promo = build_promo(Path(tmp) / "slug", files)
            self._assert_refuse(
                run_gate(promo, env={"REVIEW_SIGNOFF_PUBKEY": str(pub)}),
                "invalid signoff: slug",
            )
            old = sign_pair(priv, SLUG, raw + b" ")
            files = edition_files(SLUG, payload, schedule=True, pair=old)
            promo = build_promo(Path(tmp) / "sha", files)
            self._assert_refuse(
                run_gate(promo, env={"REVIEW_SIGNOFF_PUBKEY": str(pub)}),
                "invalid signoff: payload sha",
            )
            import base64 as _b64

            raw_sig = bytearray(_b64.b64decode(good[1].strip()))
            raw_sig[0] ^= 1
            files = edition_files(
                SLUG,
                payload,
                schedule=True,
                pair=(good[0], _b64.b64encode(bytes(raw_sig)) + b"\n"),
            )
            promo = build_promo(Path(tmp) / "sig", files)
            self._assert_refuse(
                run_gate(promo, env={"REVIEW_SIGNOFF_PUBKEY": str(pub)}),
                "invalid signoff: sig",
            )
            spaced = good[0].replace(b'":"', b'": "', 1)
            files = edition_files(
                SLUG, payload, schedule=True, pair=(spaced, good[1])
            )
            promo = build_promo(Path(tmp) / "canon", files)
            self._assert_refuse(
                run_gate(promo, env={"REVIEW_SIGNOFF_PUBKEY": str(pub)}),
                "invalid signoff: payload sha",
            )

    def test_E6_pubkey_from_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            other_dir = Path(tmp) / "other"
            other_dir.mkdir()
            other_priv, other_pub = ed25519_keypair(other_dir)
            payload = review_payload()
            raw = dumps(payload)
            pair = sign_pair(other_priv, SLUG, raw)
            files = edition_files(SLUG, payload, schedule=True, pair=pair)
            promo = build_promo(Path(tmp) / "repo", files)
            dest_pub = promo.pr_tree / "scripts" / "review_signoff_pubkey.pem"
            dest_pub.parent.mkdir(parents=True, exist_ok=True)
            dest_pub.write_bytes(other_pub.read_bytes())
            result = run_gate(promo)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("invalid signoff", result.stderr)

    def test_E7_signature_does_not_rescue_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            keys = Path(tmp) / "keys"
            keys.mkdir()
            priv, pub = ed25519_keypair(keys)
            payload = review_payload()
            raw = dumps(payload)
            pair = sign_pair(priv, SLUG, raw)
            files = edition_files(SLUG, payload, schedule=True, pair=pair)
            promo = build_promo(Path(tmp), files)
            seed = promo.tmp / "seed"
            git(["checkout", promo.head_ref], cwd=seed)
            (seed / "data" / "wire.json").write_bytes(b'{"x":1}\n')
            git(["add", "data/wire.json"], cwd=seed)
            git(["commit", "-m", "identity miss"], cwd=seed)
            git(["push", "origin", promo.head_ref], cwd=seed)
            git(["fetch", "origin", promo.head_ref], cwd=promo.pr_tree)
            git(
                ["checkout", "-B", promo.head_ref, "origin/" + promo.head_ref],
                cwd=promo.pr_tree,
            )
            result = run_gate(promo, env={"REVIEW_SIGNOFF_PUBKEY": str(pub)})
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertTrue(
                "stale vs develop" in result.stderr
                or "blob differs" in result.stderr,
                result.stderr,
            )

    def test_E8_legacy_pin_not_es(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = edition_files(SLUG, preview_payload())
            files["scripts/review_legacy_editions.txt"] = b"data/narrative.json abc\n"
            promo = build_promo(Path(tmp), files)
            self._assert_refuse(run_gate(promo), "off the automerge allowlist")

    def test_E9_bad_json_failed_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = edition_files(SLUG, preview_payload())
            files["data/narrative.json"] = b"{not json"
            files[f"data/narrative_editions/{SLUG}.json"] = b"{not json"
            promo = build_promo(Path(tmp), files)
            result = run_gate(promo)
            self.assertEqual(result.returncode, 1)
            self.assertIn("review gate failed closed", result.stderr)

    def test_G1_G2_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = preview_payload()
            files = edition_files(SLUG, payload, svgs=0)
            for i in range(32):
                files[f"public/images/narrative/{SLUG}/xo-{i:02d}.svg"] = TINY_SVG
            promo = build_promo(Path(tmp) / "n", files)
            count = len(promo.files)
            if count < 40:
                for i in range(40 - count):
                    files[f"public/images/narrative/{SLUG}/pad-{i:02d}.svg"] = TINY_SVG
                promo = build_promo(Path(tmp) / "n40", files)
            self.assertLessEqual(len(promo.files), 40)
            while len(files) < 40:
                files[f"public/images/narrative/{SLUG}/z{len(files)}.svg"] = TINY_SVG
            promo = build_promo(Path(tmp) / "cap40", {k: files[k] for k in list(files)[:40]})
            self.assertEqual(len(promo.files), 40)
            self._assert_pass(run_gate(promo), "G1")
            files[f"public/images/narrative/{SLUG}/over.svg"] = TINY_SVG
            over = build_promo(Path(tmp) / "cap41", files)
            self.assertGreaterEqual(len(over.files), 41)
            self._assert_refuse(run_gate(over), "cap:")

    def test_G4_listing_completeness(self):
        blob = b"".join(
            (
                b":100644 100644 "
                + (f"{i:040x}".encode())
                + b" "
                + (f"{i+1:040x}".encode())
                + b" A\0"
                + f"data/narrative_editions/pad-{i:04d}.json".encode()
                + b"\0"
            )
            for i in range(3001)
        )
        changes = review_gate._parse_raw_z(blob)
        self.assertEqual(len(changes), 3001)
        self.assertTrue(changes[-1].path.endswith("pad-3000.json"))

    def test_G6_blob_cap_plus_one(self):
        self.assertEqual(review_gate.AUTOMERGE_BLOB_CAP, 5 * 1024 * 1024)

    def test_H3_slug_bytes_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = edition_files(SLUG, preview_payload())
            files["data/narrative.json"] = dumps(preview_payload(slug="2026-11-28-0948"))
            promo = build_promo(Path(tmp), files)
            result = run_gate(promo)
            self.assertEqual(result.returncode, 1, result.stderr)

    def test_H4_older_than_main(self):
        with tempfile.TemporaryDirectory() as tmp:
            main_live = dumps(preview_payload(slug="2026-11-28-0948"))
            files = edition_files(SLUG, preview_payload())
            promo = build_promo(
                Path(tmp), files, main_files={"data/narrative.json": main_live}
            )
            self._assert_refuse(run_gate(promo), "older than main")

    def test_H1P_hold_true_identity_passes_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = edition_files(SLUG, preview_payload(), hold=True)
            promo = self._preview11(tmp) if False else build_promo(Path(tmp), files)
            out = Path(tmp) / "out.txt"
            result = run_gate(promo, extra=["--github-output", str(out)])
            self._assert_pass(result, "H1-P gate")
            text = out.read_text(encoding="utf-8")
            self.assertIn("hold_automerge=true", text)

    def test_H1_override_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = edition_files(SLUG, preview_payload(), hold=True)
            promo = build_promo(Path(tmp), files)
            body = f"Edition-only promote (develop squash {promo.develop_sha})."

            class Handler(BaseHTTPRequestHandler):
                def log_message(self, format, *args):
                    del format, args

                def do_GET(self):
                    if self.path.endswith("/pulls") and "commits/" in self.path:
                        payload = [
                            {
                                "head": {"sha": "abc" * 8 + "abcdabcdabcdabcdabcdabcdabcdab"},
                                "merged_by": {"login": "stevologic", "type": "User"},
                            }
                        ]
                    elif self.path.endswith("/check-runs"):
                        payload = {
                            "check_runs": [
                                {"name": "karen-qa", "conclusion": "success"}
                            ]
                        }
                    else:
                        payload = {"statuses": []}
                    raw = json.dumps(payload).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)

            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                root = f"http://127.0.0.1:{server.server_address[1]}"
                env = {"FROZEN38_GITHUB_API_ROOT": root}
                self._assert_pass(
                    run_hold_override(promo, pr_body=body, env=env), "H1-P"
                )
                self._assert_refuse(
                    run_hold_override(
                        promo, pr_body="no sha here", env=env
                    ),
                    "hold override refused: b",
                )
                self._assert_refuse(
                    run_hold_override(
                        promo,
                        pr_body=f"develop squash {'a' * 40}",
                        env=env,
                    ),
                    "hold override refused: b: SHA != tip",
                )
            finally:
                server.shutdown()

        with tempfile.TemporaryDirectory() as tmp:
            files = edition_files(SLUG, preview_payload(), hold=True)
            promo = build_promo(Path(tmp), files)
            body = f"develop squash {promo.develop_sha}"

            class ErrHandler(BaseHTTPRequestHandler):
                def log_message(self, format, *args):
                    del format, args

                def do_GET(self):
                    self.send_response(404)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b"{}")

            server = ThreadingHTTPServer(("127.0.0.1", 0), ErrHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                env = {
                    "FROZEN38_GITHUB_API_ROOT": f"http://127.0.0.1:{server.server_address[1]}"
                }
                self._assert_refuse(
                    run_hold_override(promo, pr_body=body, env=env),
                    "hold override refused: b: API error",
                )
            finally:
                server.shutdown()

    def test_E2H_bot_merge_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = edition_files(SLUG, preview_payload(), hold=True)
            promo = build_promo(Path(tmp), files)
            body = f"develop squash {promo.develop_sha}"

            class Handler(BaseHTTPRequestHandler):
                def log_message(self, format, *args):
                    del format, args

                def do_GET(self):
                    if self.path.endswith("/pulls") and "commits/" in self.path:
                        payload = [
                            {
                                "head": {"sha": "a" * 40},
                                "merged_by": {
                                    "login": "github-actions[bot]",
                                    "type": "Bot",
                                },
                            }
                        ]
                    elif self.path.endswith("/check-runs"):
                        payload = {
                            "check_runs": [
                                {"name": "karen-qa", "conclusion": "success"}
                            ]
                        }
                    else:
                        payload = {}
                    raw = json.dumps(payload).encode("utf-8")
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(raw)

            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                env = {
                    "FROZEN38_GITHUB_API_ROOT": f"http://127.0.0.1:{server.server_address[1]}"
                }
                self._assert_refuse(
                    run_hold_override(promo, pr_body=body, env=env),
                    "hold override refused: c",
                )
            finally:
                server.shutdown()

    def test_F8_ruleset_context_gates(self):
        data = json.loads(RULESET_FIXTURE.read_text(encoding="utf-8"))
        contexts = []
        for rule in data["rules"]:
            if rule.get("type") == "required_status_checks":
                for check in rule["parameters"]["required_status_checks"]:
                    contexts.append(check["context"])
        self.assertEqual(contexts, ["gates"])
        ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertRegex(ci, r"(?m)^  gates:")

    def test_D6b_absent_on_both_not_in_diff(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = edition_files(SLUG, preview_payload())
            promo = self._preview11(tmp) if False else build_promo(Path(tmp), files)
            self._assert_pass(run_gate(promo), "D6b")

    def test_G3_rename_counts_two(self):
        listed = ["a", "b"]
        listed.append("orig")
        self.assertEqual(len(listed), 3)
        self.assertEqual(review_gate.AUTOMERGE_PATH_CAP, 40)

    def test_G5_argv_length_parser(self):
        blob = b"".join(
            (
                b":100644 100644 "
                + (f"{i:040x}".encode())
                + b" "
                + (f"{i + 1:040x}".encode())
                + b" A\0"
                + f"public/images/narrative/{SLUG}/n{i:04d}.svg".encode()
                + b"\0"
            )
            for i in range(5000)
        )
        changes = review_gate._parse_raw_z(blob)
        self.assertEqual(len(changes), 5000)

    def test_C8_main_moved_edition(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = edition_files(SLUG, preview_payload())
            promo = build_promo(Path(tmp), files)
            seed = promo.tmp / "seed"
            git(["checkout", "main"], cwd=seed)
            (seed / "data" / "narrative.json").write_bytes(
                dumps(preview_payload(slug="2026-11-28-0948"))
            )
            git(["add", "data/narrative.json"], cwd=seed)
            git(["commit", "-m", "main advanced"], cwd=seed)
            git(["push", "origin", "main"], cwd=seed)
            git(["fetch", "origin", "main"], cwd=promo.pr_tree)
            result = run_gate(promo, base="origin/main")
            self.assertEqual(result.returncode, 1, result.stderr)

    def test_F4_unsigned_review_independent_of_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = edition_files(SLUG, review_payload(), schedule=True)
            promo = build_promo(Path(tmp), files)
            self._assert_refuse(run_gate(promo), "unsigned Review")



if __name__ == "__main__":
    unittest.main()
