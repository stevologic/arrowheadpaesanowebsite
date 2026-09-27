"""CI gates for narrative comments.

Offline only — no network, no paid APIs, no Turnstile siteverify. Run with:
  python -m unittest discover -s tools/tests -v
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.comments.service import (
    EMPTY_STATE,
    HONEYPOT_FIELD,
    NOT_CONNECTED_COPY,
    TURNSTILE_FAIL_TOKEN,
    TURNSTILE_PASS_TOKEN,
    CommentError,
    CommentStore,
)
from tools.comments.server import handle

ROOT = Path(__file__).resolve().parents[2]


def _store() -> CommentStore:
    return CommentStore(admin_token="secret-admin")


class CommentStoreTests(unittest.TestCase):
    def test_empty_state_copy_and_list(self):
        store = _store()
        self.assertEqual(store.empty_state(), "No comments yet. Be the first.")
        self.assertEqual(store.empty_state(), EMPTY_STATE)
        self.assertEqual(store.list_public("2026-09-26-1355")["comments"], [])

    def test_post_then_list(self):
        store = _store()
        row = store.post(
            {
                "slug": "2026-09-26-1355",
                "name": "Travis",
                "body": "Great read. The pass rush is the whole story.",
                HONEYPOT_FIELD: "",
            },
            ip="203.0.113.10",
            turnstile_token=TURNSTILE_PASS_TOKEN,
        )
        self.assertIsNotNone(row)
        self.assertEqual(row["name"], "Travis")
        listed = store.list_public("2026-09-26-1355")["comments"]
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["body"], "Great read. The pass rush is the whole story.")
        self.assertFalse(listed[0]["hidden"])

    def test_honeypot_is_ignored_not_stored(self):
        store = _store()
        row = store.post(
            {
                "slug": "2026-09-26-1355",
                "name": "Bot",
                "body": "Buy cheap jerseys http://spam.example",
                HONEYPOT_FIELD: "https://spam.example",
            },
            ip="203.0.113.99",
            turnstile_token=TURNSTILE_PASS_TOKEN,
        )
        self.assertIsNone(row)
        self.assertEqual(store.list_public("2026-09-26-1355")["comments"], [])

    def test_turnstile_failure_rejects(self):
        store = _store()
        with self.assertRaises(CommentError) as ctx:
            store.post(
                {
                    "slug": "2026-09-26-1355",
                    "name": "Travis",
                    "body": "Should not land.",
                },
                ip="203.0.113.10",
                turnstile_token=TURNSTILE_FAIL_TOKEN,
            )
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(store.list_public("2026-09-26-1355")["comments"], [])

    def test_missing_turnstile_token_rejects(self):
        store = _store()
        with self.assertRaises(CommentError) as ctx:
            store.post(
                {"slug": "2026-09-26-1355", "name": "Travis", "body": "Nope."},
                ip="203.0.113.10",
                turnstile_token="",
            )
        self.assertEqual(ctx.exception.status, 400)

    def test_rate_limit_rejects_burst(self):
        store = CommentStore(admin_token="secret-admin", rate_min_interval_sec=60, rate_max=2)
        payload = {
            "slug": "2026-09-26-1355",
            "name": "Fan",
            "body": "First take.",
        }
        store.post(payload, ip="198.51.100.8", turnstile_token=TURNSTILE_PASS_TOKEN)
        with self.assertRaises(CommentError) as ctx:
            store.post(
                {**payload, "body": "Second take right away."},
                ip="198.51.100.8",
                turnstile_token=TURNSTILE_PASS_TOKEN,
            )
        self.assertEqual(ctx.exception.status, 429)
        self.assertEqual(len(store.list_public("2026-09-26-1355")["comments"]), 1)

    def test_hide_then_public_empty_admin_still_sees(self):
        store = _store()
        row = store.post(
            {"slug": "2026-09-26-1355", "name": "Travis", "body": "Hide me please."},
            ip="203.0.113.10",
            turnstile_token=TURNSTILE_PASS_TOKEN,
        )
        hidden = store.hide(row["id"], True)
        self.assertTrue(hidden["hidden"])
        self.assertEqual(store.list_public("2026-09-26-1355")["comments"], [])
        admin = store.list_admin("2026-09-26-1355")
        self.assertEqual(len(admin), 1)
        self.assertTrue(admin[0]["hidden"])

    def test_delete_removes_comment(self):
        store = _store()
        row = store.post(
            {"slug": "2026-09-26-1355", "name": "Travis", "body": "Delete me."},
            ip="203.0.113.10",
            turnstile_token=TURNSTILE_PASS_TOKEN,
        )
        store.delete(row["id"])
        self.assertEqual(store.list_public("2026-09-26-1355")["comments"], [])
        self.assertEqual(store.list_admin("2026-09-26-1355"), [])
        self.assertNotIn(row["id"], store._comments)
        with self.assertRaises(CommentError) as ctx:
            store.delete(row["id"])
        self.assertEqual(ctx.exception.status, 404)

    def test_request_id_is_idempotent(self):
        store = _store()
        first = store.post(
            {
                "slug": "2026-09-26-1355",
                "name": "Travis",
                "body": "Once is enough.",
                "requestId": "11111111-1111-4111-8111-111111111111",
            },
            ip="203.0.113.10",
            turnstile_token=TURNSTILE_PASS_TOKEN,
        )
        second = store.post(
            {
                "slug": "2026-09-26-1355",
                "name": "Travis",
                "body": "Once is enough.",
                "requestId": "11111111-1111-4111-8111-111111111111",
            },
            ip="203.0.113.11",
            turnstile_token=TURNSTILE_PASS_TOKEN,
        )
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(store.list_public("2026-09-26-1355")["comments"]), 1)
        reused = store.post(
            {
                "slug": "2026-09-26-1355",
                "name": "Travis",
                "body": "Once is enough.",
                "requestId": "11111111-1111-4111-8111-111111111111",
            },
            ip="203.0.113.12",
            turnstile_token=TURNSTILE_FAIL_TOKEN,
        )
        self.assertEqual(first["id"], reused["id"])
        self.assertEqual(len(store.list_public("2026-09-26-1355")["comments"]), 1)
        store.hide(first["id"], True)
        hidden_replay = store.post(
            {
                "slug": "2026-09-26-1355",
                "name": "Travis",
                "body": "Once is enough.",
                "requestId": "11111111-1111-4111-8111-111111111111",
            },
            ip="203.0.113.13",
            turnstile_token=TURNSTILE_FAIL_TOKEN,
        )
        self.assertEqual(hidden_replay, {"id": first["id"], "status": "hidden"})

    def test_unknown_slug_is_404_without_storing(self):
        store = _store()
        with self.assertRaises(CommentError) as ctx:
            store.post(
                {"slug": "not-a-real-edition", "name": "Travis", "body": "Nope."},
                ip="203.0.113.10",
                turnstile_token=TURNSTILE_PASS_TOKEN,
            )
        self.assertEqual(ctx.exception.status, 404)
        self.assertEqual(store._comments, {})

    def test_admin_token_required(self):
        store = _store()
        with self.assertRaises(CommentError) as ctx:
            store.require_admin("wrong")
        self.assertEqual(ctx.exception.status, 401)
        store.require_admin("Bearer secret-admin")

    def test_strips_markup(self):
        store = _store()
        row = store.post(
            {
                "slug": "2026-09-26-1355",
                "name": "Pat<script>",
                "body": "Hello <b>Kingdom</b>",
            },
            ip="203.0.113.10",
            turnstile_token=TURNSTILE_PASS_TOKEN,
        )
        self.assertEqual(row["name"], "Patscript")
        self.assertEqual(row["body"], "Hello bKingdom/b")


class CommentHttpTests(unittest.TestCase):
    def setUp(self):
        from tools.comments import server as comments_server

        comments_server.STORE = CommentStore(admin_token="secret-admin")
        self.store = comments_server.STORE

    def test_http_empty_then_post_then_hide(self):
        status, raw = handle("GET", "/comments?slug=2026-09-26-1355", {}, {})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["comments"], [])

        status, raw = handle(
            "POST",
            "/comments",
            {
                "slug": "2026-09-26-1355",
                "name": "Steve",
                "body": "Tape does not lie.",
                "turnstileToken": TURNSTILE_PASS_TOKEN,
            },
            {},
        )
        self.assertEqual(status, 201)
        comment_id = json.loads(raw)["comment"]["id"]

        status, raw = handle("GET", "/comments?slug=2026-09-26-1355", {}, {})
        self.assertEqual(len(json.loads(raw)["comments"]), 1)

        status, raw = handle(
            "POST",
            f"/comments/{comment_id}/hide",
            {},
            {"authorization": "Bearer secret-admin"},
        )
        self.assertEqual(status, 200)
        status, raw = handle("GET", "/comments?slug=2026-09-26-1355", {}, {})
        self.assertEqual(json.loads(raw)["comments"], [])

        status, raw = handle(
            "DELETE",
            f"/comments/{comment_id}",
            {},
            {"authorization": "Bearer secret-admin"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(self.store.list_admin(), [])

    def test_http_spam_and_unauthorized_moderation(self):
        status, raw = handle(
            "POST",
            "/comments",
            {
                "slug": "2026-09-26-1355",
                "name": "Bot",
                "body": "spam",
                HONEYPOT_FIELD: "filled",
                "turnstileToken": TURNSTILE_PASS_TOKEN,
            },
            {},
        )
        self.assertEqual(status, 201)
        self.assertTrue(json.loads(raw).get("ignored"))
        status, raw = handle("GET", "/comments?slug=2026-09-26-1355", {}, {})
        self.assertEqual(json.loads(raw)["comments"], [])

        status, raw = handle("POST", "/comments/nope/hide", {}, {})
        self.assertEqual(status, 401)
        status, raw = handle("GET", "/comments?all=1", {}, {})
        self.assertEqual(status, 401)


def _hugo_bin() -> str:
    for candidate in (
        os.environ.get("HUGO_BIN"),
        shutil.which("hugo"),
        str(ROOT / "node_modules" / ".bin" / "hugo"),
        str(Path.home() / ".local" / "hugo" / "hugo"),
    ):
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise FileNotFoundError(
        "hugo is required for comment render tests; install it on PATH or via hugo-bin"
    )


class CommentWiringTests(unittest.TestCase):
    def _build_narrative_html(self, api: str, turnstile: str) -> str:
        hugo = _hugo_bin()
        dest = Path(tempfile.mkdtemp(prefix="nrt-comments-"))
        self._last_dest = dest
        cfg = Path(tempfile.mkdtemp(prefix="nrt-cfg-")) / "hugo.yaml"
        text = (ROOT / "hugo.yaml").read_text(encoding="utf-8")
        if re.search(r"^  commentsApiUrl:", text, re.M):
            text = re.sub(r"^  commentsApiUrl:.*$", f"  commentsApiUrl: {json.dumps(api)}", text, count=1, flags=re.M)
            text = re.sub(
                r"^  commentsTurnstileSiteKey:.*$",
                f"  commentsTurnstileSiteKey: {json.dumps(turnstile)}",
                text,
                count=1,
                flags=re.M,
            )
        else:
            text += (
                f"\n  commentsApiUrl: {json.dumps(api)}\n"
                f"  commentsTurnstileSiteKey: {json.dumps(turnstile)}\n"
            )
        cfg.write_text(text, encoding="utf-8")
        env = os.environ.copy()
        env.pop("HUGO_COMMENTS_API_URL", None)
        env.pop("HUGO_TURNSTILE_SITE_KEY", None)
        proc = subprocess.run(
            [hugo, "--gc", "-d", str(dest), "--config", str(cfg)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            self.fail((proc.stderr or "") + "\n" + (proc.stdout or "") or "hugo failed")
        return (dest / "narrative" / "index.html").read_text(encoding="utf-8")

    def test_unset_config_renders_nothing(self):
        """Runs Hugo with unset, whitespace, and set comment params; asserts built HTML."""
        self.assertIn("strings.TrimSpace", (ROOT / "layouts/partials/narrative-comments.html").read_text(encoding="utf-8"))

        unset = self._build_narrative_html("", "")
        self.assertNotIn("nrt-comments", unset)
        self.assertNotIn("challenges.cloudflare.com/turnstile/v0/api.js", unset)

        whitespace = self._build_narrative_html("   ", "   ")
        self.assertNotIn("nrt-comments", whitespace)
        self.assertNotIn("challenges.cloudflare.com/turnstile/v0/api.js", whitespace)

        configured = self._build_narrative_html(
            "https://comments.example.workers.dev",
            "1x00000000000000000000AA",
        )
        self.assertIn("nrt-comments", configured)
        self.assertIn("challenges.cloudflare.com/turnstile/v0/api.js", configured)
        self.assertIn("nrt_hp_x7", configured)
        slugs_path = self._last_dest / "comments-slugs.json"
        self.assertTrue(slugs_path.is_file(), "Hugo must emit comments-slugs.json")
        slugs = json.loads(slugs_path.read_text(encoding="utf-8"))
        self.assertIn("2026-09-26-1355", slugs)

        js = (ROOT / "public/js/narrative-comments.js").read_text(encoding="utf-8")
        self.assertNotIn(NOT_CONNECTED_COPY, js)
        self.assertNotIn("sessionStorage", js)
        self.assertIn("Load older comments", configured)
        self.assertIn("Load older comments", js)
        self.assertIn("Retry", js)
        self.assertIn('mode === "retry"', js)
        self.assertIn("Couldn't load older comments.", configured)
        self.assertIn("Couldn't load older comments.", js)
        self.assertIn("data-older-error", configured)
        self.assertIn("requestId", js)
        self.assertIn("waitForFreshTurnstile", js)
        self.assertIn("turnstile.reset", js)
        self.assertIn("postWithReplay", js)
        self.assertIn("__nrtPostWithReplay", js)
        self.assertIn("Your comment was received and is pending/removed.", js)
        self.assertIn("Reached the page cap", js)
        css = (ROOT / "public/css/comments.css").read_text(encoding="utf-8")
        self.assertIn("fieldset:disabled", css)
        pkg = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(int(str(pkg["engines"]["node"]).lstrip(">=").split(".")[0]), 22)

    def test_partial_empty_state_and_page_includes(self):
        partial = (ROOT / "layouts/partials/narrative-comments.html").read_text(encoding="utf-8")
        self.assertIn("No comments yet. Be the first.", partial)
        self.assertIn("nrt-comments", partial)
        self.assertIn("nrt_hp_x7", partial)
        self.assertIn(HONEYPOT_FIELD, partial)
        single = (ROOT / "layouts/narrative/single.html").read_text(encoding="utf-8")
        edition = (ROOT / "layouts/narrative/edition.html").read_text(encoding="utf-8")
        self.assertIn("narrative-comments.html", single)
        self.assertIn("narrative-comments.html", edition)
        edition_template = (ROOT / "layouts/partials/narrative-edition.html").read_text(encoding="utf-8")
        self.assertNotIn("narrative-comments.html", edition_template)

    def test_worker_has_spam_and_moderation_gates(self):
        worker = (ROOT / "tools/comments/worker.js").read_text(encoding="utf-8")
        self.assertIn("TURNSTILE_SECRET", worker)
        self.assertIn("nrt_hp_x7", worker)
        self.assertIn("RATE_MAX", worker)
        self.assertIn("COMMENTS_ADMIN_TOKEN", worker)
        self.assertIn('parts[2] === "hide"', worker)
        self.assertIn("DELETE FROM comments", worker)
        self.assertIn("blockConcurrencyWhile", worker)
        self.assertIn("schema_version", worker)
        self.assertIn("ALTER TABLE comments ADD COLUMN requestId", worker)
        self.assertIn("comments_created_id", worker)
        self.assertIn("comments_hidden_created", worker)
        self.assertIn("DROP INDEX IF EXISTS comments_slug_created", worker)
        self.assertIn("PUBLIC_LIST_SQL", worker)
        self.assertIn("no-store", worker)
        self.assertIn("by-request-id", worker)


if __name__ == "__main__":
    unittest.main()
