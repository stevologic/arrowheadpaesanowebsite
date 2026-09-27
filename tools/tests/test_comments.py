"""CI gates for narrative comments.

Offline only — no network, no paid APIs, no Turnstile siteverify. Run with:
  python -m unittest discover -s tools/tests -v
"""
from __future__ import annotations

import json
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
    is_comments_ui_enabled,
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
        self.assertEqual(store.list_public("2026-09-26-1355"), [])

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
        listed = store.list_public("2026-09-26-1355")
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
        self.assertEqual(store.list_public("2026-09-26-1355"), [])

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
        self.assertEqual(store.list_public("2026-09-26-1355"), [])

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
        self.assertEqual(len(store.list_public("2026-09-26-1355")), 1)

    def test_hide_then_public_empty_admin_still_sees(self):
        store = _store()
        row = store.post(
            {"slug": "2026-09-26-1355", "name": "Travis", "body": "Hide me please."},
            ip="203.0.113.10",
            turnstile_token=TURNSTILE_PASS_TOKEN,
        )
        hidden = store.hide(row["id"], True)
        self.assertTrue(hidden["hidden"])
        self.assertEqual(store.list_public("2026-09-26-1355"), [])
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
        self.assertEqual(store.list_public("2026-09-26-1355"), [])
        self.assertEqual(store.list_admin("2026-09-26-1355"), [])
        with self.assertRaises(CommentError) as ctx:
            store.delete(row["id"])
        self.assertEqual(ctx.exception.status, 404)

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


class CommentWiringTests(unittest.TestCase):
    def test_unset_config_renders_nothing(self):
        self.assertFalse(is_comments_ui_enabled("", ""))
        self.assertFalse(is_comments_ui_enabled(None, None))
        self.assertFalse(is_comments_ui_enabled("https://comments.example.workers.dev", ""))
        self.assertFalse(is_comments_ui_enabled("", "1x00000000000000000000AA"))
        self.assertFalse(is_comments_ui_enabled("  ", "  "))
        self.assertTrue(
            is_comments_ui_enabled(
                "https://comments.example.workers.dev",
                "1x00000000000000000000AA",
            )
        )

        yaml = (ROOT / "hugo.yaml").read_text(encoding="utf-8")
        self.assertRegex(yaml, r'commentsApiUrl:\s*""')
        self.assertRegex(yaml, r'commentsTurnstileSiteKey:\s*""')

        partial = (ROOT / "layouts/partials/narrative-comments.html").read_text(encoding="utf-8")
        guard = partial.index("if and $api $turnstile")
        for needle in ("nrt-comments", "No comments yet. Be the first.", "data-comments-form", "Comments"):
            self.assertGreater(partial.index(needle), guard, needle)
        self.assertGreater(partial.rfind("end"), partial.index("nrt-comments"))

        js = (ROOT / "public/js/narrative-comments.js").read_text(encoding="utf-8")
        self.assertNotIn(NOT_CONNECTED_COPY, js)
        self.assertNotIn("127.0.0.1:8787", js)

    def test_partial_empty_state_and_page_includes(self):
        partial = (ROOT / "layouts/partials/narrative-comments.html").read_text(encoding="utf-8")
        self.assertIn("No comments yet. Be the first.", partial)
        self.assertIn("nrt-comments", partial)
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
        self.assertIn("company", worker)
        self.assertIn("RATE_MAX", worker)
        self.assertIn("COMMENTS_ADMIN_TOKEN", worker)
        self.assertIn('parts[2] === "hide"', worker)


if __name__ == "__main__":
    unittest.main()
