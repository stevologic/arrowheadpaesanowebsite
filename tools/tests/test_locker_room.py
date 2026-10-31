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
WALKER_ID = "2112203440080482333"
MAHOMES_URL = "https://x.com/PatrickMahomes/status/2111990066990260570"
MAHOMES_RT_URL = "https://x.com/PatrickMahomes/status/2112231759832355290"
MAHOMES_RT_ID = "2112231759832355290"
FAN_URL = "https://x.com/TravisKelce/status/2112203440080482333"


def _author(handle):
    """Injected syndication lookup that returns a fixed screen_name."""
    return lambda status_id: handle.lstrip("@")


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
            payload,
            oembed_fetch=fetch,
            fallback_fetch=lambda url: None,
            syndication_fetch=_author("Kenneth_Walker9"),
            phase=WEEK_PHASE,
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
                syndication_fetch=_author("Kenneth_Walker9"),
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
                syndication_fetch=_author("TravisKelce"),
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
                syndication_fetch=_author("Kenneth_Walker9"),
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
            syndication_fetch=_author("PatrickMahomes"),
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
            syndication_fetch=_author("PatrickMahomes"),
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
            syndication_fetch=_author("Kenneth_Walker9"),
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
                syndication_fetch=_author("Kenneth_Walker9"),
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
            syndication_fetch=_author("Kenneth_Walker9"),
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
            syndication_fetch=_author("Kenneth_Walker9"),
        )
        self.assertEqual(narrative["lockerRoom"]["posts"][0]["url"], WALKER_URL)

    def test_verify_rejects_syndication_author_mismatch(self):
        """Claimed @PatrickMahomes must fail when tweet-result says @Chiefs."""
        row = {
            "url": MAHOMES_URL,
            "handle": "@PatrickMahomes",
            "date": "2026-10-18",
            "quote": "It's a denim day for QB1.",
        }
        self.assertIsNone(
            locker_room.verify_post(
                row,
                oembed_fetch=lambda url: _oembed(
                    "https://x.com/PatrickMahomes",
                    "<p>It&#39;s a denim day for QB1.</p> October 18, 2026",
                ),
                fallback_fetch=lambda url: None,
                syndication_fetch=_author("Chiefs"),
                phase=WEEK_PHASE,
            )
        )

    def test_verify_rejects_failed_syndication_lookup(self):
        """A failed tweet-result lookup is a hard fail, not a claimed-handle pass."""
        row = {
            "url": WALKER_URL,
            "handle": "@Kenneth_Walker9",
            "date": "2026-10-19",
        }
        self.assertIsNone(
            locker_room.verify_post(
                row,
                oembed_fetch=lambda url: _oembed(
                    "https://x.com/Kenneth_Walker9",
                    "<p>pic</p> October 19, 2026",
                ),
                fallback_fetch=lambda url: None,
                syndication_fetch=lambda status_id: None,
                phase=WEEK_PHASE,
            )
        )

    def test_retweet_keeps_syndication_unroll_by_claimed_handle(self):
        row = {
            "url": WALKER_URL,
            "handle": "@Kenneth_Walker9",
            "date": "2026-10-19",
            "retweetedBy": {
                "handle": "@PatrickMahomes",
                "statusId": MAHOMES_RT_ID,
                "url": MAHOMES_RT_URL,
            },
        }

        def payload(status_id):
            self.assertEqual(status_id, MAHOMES_RT_ID)
            return {"id_str": WALKER_ID}

        verified = locker_room.verify_post(
            row,
            oembed_fetch=lambda url: _oembed(
                "https://x.com/PatrickMahomes"
                if "2112231759832355290" in url
                else "https://x.com/Kenneth_Walker9",
                "<p>pic</p> October 19, 2026",
            ),
            fallback_fetch=lambda url: None,
            syndication_fetch=_author("Kenneth_Walker9"),
            syndication_payload_fetch=payload,
            phase=WEEK_PHASE,
        )
        self.assertIsNotNone(verified)
        self.assertEqual(verified["retweetedBy"]["statusId"], MAHOMES_RT_ID)

    def test_retweet_rejects_mismatch_and_failed_lookup(self):
        row = {
            "url": WALKER_URL,
            "handle": "@Kenneth_Walker9",
            "date": "2026-10-19",
            "retweetedBy": {
                "handle": "@PatrickMahomes",
                "statusId": MAHOMES_RT_ID,
                "url": MAHOMES_RT_URL,
            },
        }

        def oembed(url):
            author = (
                "https://x.com/PatrickMahomes"
                if MAHOMES_RT_ID in url
                else "https://x.com/Kenneth_Walker9"
            )
            return _oembed(author, "<p>pic</p> October 19, 2026")

        common = dict(
            oembed_fetch=oembed,
            fallback_fetch=lambda url: None,
            syndication_fetch=_author("Kenneth_Walker9"),
            phase=WEEK_PHASE,
        )
        self.assertIsNone(
            locker_room.verify_post(
                row, syndication_payload_fetch=lambda sid: None, **common
            )
        )
        self.assertIsNone(
            locker_room.verify_post(
                row,
                syndication_payload_fetch=lambda sid: {"id_str": MAHOMES_RT_ID},
                **common,
            )
        )
        self.assertIsNone(
            locker_room.verify_post(
                row,
                syndication_payload_fetch=lambda sid: {"id_str": "1"},
                **common,
            )
        )
        wrong_handle = dict(row)
        wrong_handle["retweetedBy"] = dict(row["retweetedBy"])
        self.assertIsNone(
            locker_room.verify_post(
                wrong_handle,
                oembed_fetch=lambda url: _oembed(
                    "https://x.com/Chiefs", "<p>pic</p> October 19, 2026"
                ),
                fallback_fetch=lambda url: None,
                syndication_fetch=_author("Kenneth_Walker9"),
                syndication_payload_fetch=lambda sid: {"id_str": WALKER_ID},
                phase=WEEK_PHASE,
            )
        )

    def test_locker_room_has_no_fixtweet_and_syndication_is_text_only(self):
        src = (ROOT / "tools" / "chiefs_narrative" / "locker_room.py").read_text(
            encoding="utf-8"
        )
        self.assertFalse(hasattr(locker_room, "fetch_fixtweet"))
        self.assertNotIn("FIXTWEET_ENDPOINT", src)
        self.assertNotIn("api.fxtwitter.com", src)
        self.assertTrue(hasattr(locker_room, "fetch_syndication"))
        self.assertTrue(hasattr(locker_room, "fetch_syndication_author"))
        self.assertIn("SYNDICATION_ENDPOINT", src)
        self.assertIn("_text_only_syndication", src)

    def test_production_callers_resolve_author_and_verify_retweet(self):
        """generate.py / collect.py do not inject hooks; defaults must hit the network."""
        gen = (ROOT / "tools" / "chiefs_narrative" / "generate.py").read_text(
            encoding="utf-8"
        )
        col = (ROOT / "tools" / "chiefs_narrative" / "collect.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("locker_room.check_posts(narrative, schedule=schedule)", gen)
        self.assertIn("locker_room.attach_verified(narrative, signals, ph)", gen)
        self.assertIn("locker_room.collect_candidates()", col)
        self.assertNotIn("syndication_fetch=", gen)
        self.assertNotIn("syndication_payload_fetch=", gen)
        self.assertNotIn("syndication_fetch=", col)
        self.assertNotIn("syndication_payload_fetch=", col)

        fat = {
            "id_str": WALKER_ID,
            "user": {"screen_name": "Kenneth_Walker9"},
            "text": "got the call",
            "media": [{"url": "https://pbs.twimg.com/media/x.jpg"}],
            "photos": [{"url": "https://pbs.twimg.com/media/x.jpg"}],
            "image": "https://pbs.twimg.com/media/x.jpg",
        }
        rt_fat = {
            "id_str": WALKER_ID,
            "user": {"screen_name": "Kenneth_Walker9"},
            "text": "unroll",
            "photos": [{"url": "https://pbs.twimg.com/media/rt.jpg"}],
        }

        def fake_get(url, params=None, **_kwargs):
            target = f"{url}?{params}" if params else str(url)
            class _Resp:
                status_code = 200

                def __init__(self, body):
                    self._body = body

                def json(self):
                    return self._body

            if "cdn.syndication.twimg.com" in target:
                sid = str((params or {}).get("id") or "")
                if sid == MAHOMES_RT_ID:
                    return _Resp(rt_fat)
                return _Resp(fat)
            if "publish.x.com" in target or "publish.twitter.com" in target:
                asked = str((params or {}).get("url") or "")
                handle = (
                    "PatrickMahomes" if MAHOMES_RT_ID in asked else "Kenneth_Walker9"
                )
                return _Resp(
                    _oembed(
                        f"https://x.com/{handle}",
                        "<p>got the call</p> October 19, 2026",
                        handle,
                    )
                )
            raise AssertionError(f"unexpected request {target}")

        row = {
            "url": WALKER_URL,
            "handle": "@Kenneth_Walker9",
            "date": "2026-10-19",
            "quote": "",
            "retweetedBy": {
                "handle": "@PatrickMahomes",
                "statusId": MAHOMES_RT_ID,
                "url": MAHOMES_RT_URL,
            },
        }
        with patch.object(locker_room.requests, "get", side_effect=fake_get):
            self.assertEqual(locker_room.collect_candidates(), [])
            author = locker_room.resolve_author(WALKER_URL)
            self.assertEqual(author, "Kenneth_Walker9")
            self.assertTrue(
                locker_room.verify_retweet(row),
                "default verify_retweet must use syndication, not return False",
            )
            issues = locker_room.check_posts(
                {"lockerRoom": {"posts": [row]}},
                phase=WEEK_PHASE,
            )
            self.assertEqual(issues, [])
            attached = locker_room.attach_verified(
                {"lockerRoom": {"posts": [dict(row)]}},
                {"lockerRoom": [dict(row)]},
                WEEK_PHASE,
            )
            self.assertEqual(len(attached["lockerRoom"]["posts"]), 1)

    def test_resolve_author_and_verify_retweet_never_return_or_attach_media(self):
        fat = {
            "id_str": WALKER_ID,
            "user": {
                "screen_name": "Kenneth_Walker9",
                "profile_image_url": "https://pbs.twimg.com/profile.jpg",
            },
            "text": "got the call",
            "full_text": "got the call",
            "media": [{"url": "https://pbs.twimg.com/media/x.jpg"}],
            "photos": [{"url": "https://pbs.twimg.com/media/x.jpg"}],
            "image": "https://pbs.twimg.com/media/x.jpg",
            "video": {"url": "https://video.twimg.com/x.mp4"},
        }

        def fake_get(url, params=None, **_kwargs):
            class _Resp:
                status_code = 200

                def __init__(self, body):
                    self._body = body

                def json(self):
                    return self._body

            if "cdn.syndication.twimg.com" in str(url):
                return _Resp(fat)
            if "publish.x.com" in str(url) or "publish.twitter.com" in str(url):
                asked = str((params or {}).get("url") or "")
                handle = (
                    "PatrickMahomes" if MAHOMES_RT_ID in asked else "Kenneth_Walker9"
                )
                return _Resp(
                    _oembed(
                        f"https://x.com/{handle}",
                        "<p>got the call</p> October 19, 2026",
                        handle,
                    )
                )
            raise AssertionError(url)

        row = {
            "url": WALKER_URL,
            "handle": "@Kenneth_Walker9",
            "retweetedBy": {
                "handle": "@PatrickMahomes",
                "statusId": MAHOMES_RT_ID,
                "url": MAHOMES_RT_URL,
            },
        }
        media_keys = (
            "media",
            "photos",
            "photo",
            "images",
            "image",
            "video",
            "profile_image_url",
        )
        with patch.object(locker_room.requests, "get", side_effect=fake_get):
            cleaned = locker_room.fetch_syndication(WALKER_ID)
            self.assertIsInstance(cleaned, dict)
            for key in media_keys:
                self.assertNotIn(key, cleaned)
                self.assertNotIn(key, cleaned.get("user") or {})
            author = locker_room.resolve_author(WALKER_URL)
            self.assertEqual(author, "Kenneth_Walker9")
            self.assertNotIn("pbs.twimg.com", author)
            self.assertIsInstance(author, str)
            ok = locker_room.verify_retweet(row)
            self.assertIs(ok, True)
            for key in media_keys:
                self.assertNotIn(key, row)
            self.assertNotIn("media", row.get("retweetedBy") or {})

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
        self.assertIn("View retweet", partial)
        self.assertIn(".retweetedBy", partial)
        self.assertNotIn("<iframe", partial)
        self.assertNotIn("twitter-tweet", partial)
        css = (ROOT / "public" / "css" / "narrative.css").read_text(encoding="utf-8")
        self.assertIn(".nrt-locker__list", css)
        self.assertIn("max-width: 74ch", css[css.find(".nrt-locker__list"):])
        self.assertIn("overflow-wrap: anywhere", css[css.find(".nrt-locker-post__note"):])

    def test_prompt_does_not_ask_the_model_for_status_ids(self):
        text = prompts.build_user_prompt({"news": [], "schedule": []}, WEEK_PHASE, [])
        hint = text[text.find("Return JSON with EXACTLY these keys") :]
        self.assertNotIn('"lockerRoom"', hint)
        self.assertIn("Do not invent or include X/Twitter status URLs", text)


if __name__ == "__main__":
    unittest.main()
