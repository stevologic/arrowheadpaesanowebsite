"""Locker-room X posts: schema, oEmbed gate, template, empty state."""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from tools.chiefs_narrative import generate, locker_room, prompts, schema

ROOT = Path(__file__).resolve().parents[2]

WEEK_PHASE = {
    "type": "regular",
    "label": "Week 7",
    "week": 7,
    "mode": "preview",
    "lastGame": {
        "date": "2026-10-18T20:25:00Z",
        "opponent": "Los Angeles Chargers",
        "completed": True,
    },
    "nextGame": {
        "date": "2026-10-25T00:20:00Z",
        "opponent": "Seattle Seahawks",
    },
}

WALKER_URL = "https://x.com/Kenneth_Walker9/status/2112203440080482333"
MAHOMES_URL = "https://x.com/PatrickMahomes/status/2111990066990260570"
FAN_URL = "https://x.com/TravisKelce/status/2112203440080482333"


def _oembed(author_url, html, name="Player"):
    return {
        "author_name": name,
        "author_url": author_url,
        "html": html,
        "type": "rich",
    }


def _normalize(raw):
    return schema.normalize(
        raw,
        phase=WEEK_PHASE,
        meta={
            "generatedAt": "2026-10-20T17:37:10+00:00",
            "generator": "test",
            "record": "5-0",
            "markets": {},
        },
    )


class LockerRoomSchema(unittest.TestCase):
    def test_normalize_keeps_named_status_url(self):
        narrative = _normalize(
            {
                "lockerRoom": {
                    "posts": [
                        {
                            "url": WALKER_URL + "?s=20",
                            "name": "Kenneth Walker III",
                            "team": "KC",
                            "date": "2026-10-19T15:25:17+00:00",
                            "highlight": True,
                        }
                    ]
                }
            }
        )
        row = narrative["lockerRoom"]["posts"][0]
        self.assertEqual(row["url"], WALKER_URL)
        self.assertEqual(row["handle"], "@Kenneth_Walker9")
        self.assertEqual(row["date"], "2026-10-19")
        self.assertTrue(row["highlight"])

    def test_normalize_drops_non_status_and_defaults_empty(self):
        narrative = _normalize(
            {
                "locker_room": [
                    {"url": "https://x.com/Kenneth_Walker9", "name": "Walker"},
                    {"url": "https://example.com/status/1", "name": "Walker"},
                ]
            }
        )
        self.assertEqual(narrative["lockerRoom"], {"posts": []})

    def test_older_edition_defaults_to_empty_posts(self):
        narrative = _normalize({"headline": "Tape"})
        self.assertEqual(narrative["lockerRoom"], {"posts": []})


class LockerRoomVerification(unittest.TestCase):
    def test_verify_keeps_allowlisted_oembed_in_week(self):
        payload = {
            "url": WALKER_URL,
            "handle": "@Kenneth_Walker9",
            "date": "2026-10-19",
        }

        def fetch(url):
            self.assertEqual(url, WALKER_URL)
            return _oembed(
                "https://x.com/Kenneth_Walker9",
                '<p lang="zxx"><a href="https://t.co/x">pic.twitter.com/x</a></p>'
                "&mdash; Kenneth (@Kenneth_Walker9) "
                '<a href="#">October 19, 2026</a>',
            )

        verified = locker_room.verify_post(
            payload, oembed_fetch=fetch, fallback_fetch=lambda url: None, phase=WEEK_PHASE
        )
        self.assertIsNotNone(verified)
        self.assertEqual(verified["name"], "Kenneth Walker III")
        self.assertEqual(verified["team"], "KC")

    def test_verify_rejects_dead_fan_and_wrong_week(self):
        row = {
            "url": WALKER_URL,
            "handle": "@Kenneth_Walker9",
            "date": "2026-10-19",
        }
        self.assertIsNone(
            locker_room.verify_post(
                row,
                oembed_fetch=lambda url: None,
                fallback_fetch=lambda url: None,
                phase=WEEK_PHASE,
            )
        )
        self.assertIsNone(
            locker_room.verify_post(
                {"url": FAN_URL, "handle": "@TravisKelce", "date": "2026-10-19"},
                oembed_fetch=lambda url: _oembed(
                    "https://x.com/TravisKelce",
                    "<p>fan clip</p> October 19, 2026",
                ),
                fallback_fetch=lambda url: None,
                phase=WEEK_PHASE,
            )
        )
        old = dict(row)
        old["date"] = "2026-10-16"
        self.assertIsNone(
            locker_room.verify_post(
                old,
                oembed_fetch=lambda url: _oembed(
                    "https://x.com/Kenneth_Walker9",
                    "<p>pic</p> October 16, 2026",
                ),
                fallback_fetch=lambda url: None,
                phase=WEEK_PHASE,
            )
        )

    def test_verify_rejects_invented_quote(self):
        row = {
            "url": MAHOMES_URL,
            "handle": "@PatrickMahomes",
            "date": "2026-10-19",
            "quote": "We are going to smash Seattle.",
        }
        verified = locker_room.verify_post(
            row,
            oembed_fetch=lambda url: _oembed(
                "https://x.com/PatrickMahomes",
                "<p>It&#39;s a denim day for QB1.</p> October 19, 2026",
            ),
            fallback_fetch=lambda url: None,
            phase=WEEK_PHASE,
        )
        self.assertIsNone(verified)

    def test_verify_keeps_verbatim_quote(self):
        row = {
            "url": MAHOMES_URL,
            "handle": "@PatrickMahomes",
            "date": "2026-10-19",
            "quote": "It's a denim day for QB1.",
        }
        verified = locker_room.verify_post(
            row,
            oembed_fetch=lambda url: _oembed(
                "https://x.com/PatrickMahomes",
                "<p>It&#39;s a denim day for QB1.</p> October 19, 2026",
            ),
            fallback_fetch=lambda url: None,
            phase=WEEK_PHASE,
        )
        self.assertIsNotNone(verified)
        self.assertEqual(verified["quote"], "It's a denim day for QB1.")

    def test_gate_fails_on_unverified_published_posts(self):
        narrative = {
            "lockerRoom": {
                "posts": [
                    {
                        "url": WALKER_URL,
                        "handle": "@Kenneth_Walker9",
                        "date": "2026-10-19",
                    }
                ]
            }
        }
        issues = locker_room.check_posts(
            narrative,
            oembed_fetch=lambda url: None,
            fallback_fetch=lambda url: None,
            phase=WEEK_PHASE,
        )
        self.assertTrue(issues)
        self.assertTrue(all(item.startswith("locker room:") for item in issues))

    def test_gate_passes_verified_week_posts(self):
        narrative = {
            "lockerRoom": {
                "posts": [
                    {
                        "url": WALKER_URL,
                        "handle": "@Kenneth_Walker9",
                        "date": "2026-10-19",
                    }
                ]
            }
        }
        self.assertEqual(
            locker_room.check_posts(
                narrative,
                oembed_fetch=lambda url: _oembed(
                    "https://x.com/Kenneth_Walker9",
                    "<p>pic</p> October 19, 2026",
                ),
                fallback_fetch=lambda url: None,
                phase=WEEK_PHASE,
            ),
            [],
        )

    def test_attach_overwrites_invented_posts_and_allows_empty(self):
        narrative = _normalize(
            {
                "lockerRoom": {
                    "posts": [{"url": "https://x.com/PatrickMahomes/status/1"}]
                }
            }
        )
        locker_room.attach_verified(
            narrative,
            {"lockerRoom": []},
            WEEK_PHASE,
            oembed_fetch=lambda url: None,
            fallback_fetch=lambda url: None,
        )
        self.assertEqual(narrative["lockerRoom"], {"posts": []})

        locker_room.attach_verified(
            narrative,
            {
                "lockerRoom": [
                    {
                        "url": WALKER_URL,
                        "handle": "@Kenneth_Walker9",
                        "date": "2026-10-19",
                    }
                ]
            },
            WEEK_PHASE,
            oembed_fetch=lambda url: _oembed(
                "https://x.com/Kenneth_Walker9",
                "<p>pic</p> October 19, 2026",
            ),
            fallback_fetch=lambda url: None,
        )
        self.assertEqual(narrative["lockerRoom"]["posts"][0]["url"], WALKER_URL)

    def test_check_edition_fails_locker_issues_even_when_held(self):
        with patch.object(
            generate,
            "check_published_edition",
            return_value=[f"locker room: unverified post {WALKER_URL}"],
        ):
            rc = generate.main(["--check-edition"])
        self.assertEqual(rc, 1)

    def test_collect_without_token_is_empty(self):
        with patch.dict("os.environ", {"X_BEARER_TOKEN": ""}, clear=False):
            self.assertEqual(locker_room.collect_candidates(), [])


class LockerRoomTemplate(unittest.TestCase):
    def test_template_section_is_after_story_and_compact(self):
        edition = (
            ROOT / "layouts" / "partials" / "narrative-edition.html"
        ).read_text(encoding="utf-8")
        self.assertIn('{{ with .lockerRoom }}', edition)
        self.assertIn('id="locker-room"', edition)
        self.assertIn("From the Locker Room", edition)
        self.assertIn("None verified this week.", edition)
        self.assertIn('partial "nrt-locker-post.html"', edition)
        self.assertLess(
            edition.find('class="section nrt-story"'),
            edition.find('id="locker-room"'),
        )
        self.assertLess(
            edition.find('id="locker-room"'),
            edition.find('id="press-conferences"'),
        )
        self.assertLess(
            edition.find('id="locker-room"'),
            edition.find('id="xo"'),
        )
        self.assertNotIn("nrt-hero", edition[edition.find("LOCKER ROOM"):])
        partial = (ROOT / "layouts" / "partials" / "nrt-locker-post.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("nrt-locker-post__quote", partial)
        self.assertIn("nrt-locker-post__link", partial)
        self.assertNotIn("<iframe", partial)
        self.assertNotIn("twitter-tweet", partial)
        css = (ROOT / "public" / "css" / "narrative.css").read_text(encoding="utf-8")
        self.assertIn(".nrt-locker__list", css)
        self.assertIn("max-width: 74ch", css[css.find(".nrt-locker__list"):])

    def test_prompt_does_not_ask_the_model_for_status_ids(self):
        text = prompts.build_user_prompt({"news": [], "schedule": []}, WEEK_PHASE, [])
        hint = text[text.find("Return JSON with EXACTLY these keys") :]
        self.assertNotIn('"lockerRoom"', hint)
        self.assertIn("Do not invent or include X/Twitter status URLs", text)


if __name__ == "__main__":
    unittest.main()
