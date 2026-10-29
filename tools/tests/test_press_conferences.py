"""Press-conference YouTube embeds: schema, oEmbed gate, template, collector."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from tools.chiefs_narrative import generate, press_conferences, prompts, schema

ROOT = Path(__file__).resolve().parents[2]
_CT = ZoneInfo("America/Chicago")

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

REID_URL = "https://www.youtube.com/watch?v=wV0rX_GUtTg"
POSTGAME_URL = "https://www.youtube.com/watch?v=HqrXUR4Gg4o"
REID_TITLE = "Andy Reid Speaks to the Media | OCTOBER 19, 2026"
POSTGAME_TITLE = (
    "Andy Reid, Patrick Mahomes, Ken Walker & Nick Bolton "
    "Postgame Press Conferences - NFL Week 6"
)


def _oembed(title, author="Kansas City Chiefs", handle="KansasCityChiefs"):
    return {
        "title": title,
        "author_name": author,
        "author_url": f"https://www.youtube.com/@{handle}",
        "type": "video",
        "html": "<iframe></iframe>",
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


class PressConferenceSchema(unittest.TestCase):
    def test_normalize_keeps_named_youtube_watch_url(self):
        narrative = _normalize(
            {
                "pressConferences": [
                    {
                        "url": REID_URL + "&t=12s",
                        "speaker": "Andy Reid",
                        "channel": "Kansas City Chiefs",
                        "title": REID_TITLE,
                        "date": "2026-10-19T05:14:26+00:00",
                    }
                ]
            }
        )
        row = narrative["pressConferences"][0]
        self.assertEqual(row["url"], REID_URL)
        self.assertEqual(row["videoId"], "wV0rX_GUtTg")
        self.assertEqual(row["speaker"], "Andy Reid")
        self.assertEqual(row["date"], "2026-10-19")

    def test_normalize_drops_speakerless_and_non_youtube(self):
        narrative = _normalize(
            {
                "press_conferences": [
                    {"url": REID_URL, "speaker": ""},
                    {"url": "https://vimeo.com/12345", "speaker": "Andy Reid"},
                    {"url": "https://example.com/watch?v=wV0rX_GUtTg", "speaker": "Andy Reid"},
                ]
            }
        )
        self.assertEqual(narrative["pressConferences"], [])

    def test_older_edition_defaults_to_empty_list(self):
        narrative = _normalize({"headline": "Tape"})
        self.assertEqual(narrative["pressConferences"], [])


class PressConferenceVerification(unittest.TestCase):
    def test_verify_keeps_allowlisted_oembed(self):
        payload = {
            "url": REID_URL,
            "speaker": "Andy Reid",
            "title": REID_TITLE,
            "date": "2026-10-19",
        }

        def fetch(url):
            self.assertEqual(url, REID_URL)
            return _oembed(REID_TITLE)

        verified = press_conferences.verify_embed(
            payload, oembed_fetch=fetch, phase=WEEK_PHASE
        )
        self.assertIsNotNone(verified)
        self.assertEqual(verified["speaker"], "Andy Reid")
        self.assertEqual(verified["channel"], "Kansas City Chiefs")

    def test_verify_rejects_dead_unknown_and_wrong_week(self):
        row = {
            "url": REID_URL,
            "speaker": "Andy Reid",
            "title": REID_TITLE,
            "date": "2026-10-19",
        }
        self.assertIsNone(
            press_conferences.verify_embed(row, oembed_fetch=lambda url: None)
        )
        self.assertIsNone(
            press_conferences.verify_embed(
                row,
                oembed_fetch=lambda url: _oembed(REID_TITLE, "Chiefs Fan Cuts", "chiefsfancuts"),
            )
        )
        old = dict(row)
        old["date"] = "2026-10-16"
        old["title"] = "Andy Reid Speaks to the Media | OCTOBER 16, 2026"
        self.assertIsNone(
            press_conferences.verify_embed(
                old,
                oembed_fetch=lambda url: _oembed(old["title"]),
                phase=WEEK_PHASE,
            )
        )

    def test_verify_renames_speaker_to_match_oembed_title(self):
        row = {
            "url": REID_URL,
            "speaker": "Steve Spagnuolo",
            "title": "Steve Spagnuolo Speaks to the Media",
            "date": "2026-10-19",
        }
        verified = press_conferences.verify_embed(
            row,
            oembed_fetch=lambda url: _oembed(REID_TITLE),
            phase=WEEK_PHASE,
        )
        self.assertIsNotNone(verified)
        self.assertEqual(verified["speaker"], "Andy Reid")
        self.assertEqual(verified["title"], REID_TITLE)

    def test_gate_fails_on_dead_or_unverified_published_embeds(self):
        narrative = {
            "pressConferences": [
                {
                    "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    "videoId": "dQw4w9WgXcQ",
                    "speaker": "Andy Reid",
                    "title": REID_TITLE,
                    "date": "2026-10-19",
                }
            ]
        }
        issues = press_conferences.check_embeds(
            narrative,
            oembed_fetch=lambda url: None,
            phase=WEEK_PHASE,
        )
        self.assertTrue(issues)
        self.assertTrue(all(item.startswith("press conference:") for item in issues))

    def test_gate_passes_verified_week_embeds(self):
        narrative = {
            "pressConferences": [
                {
                    "url": REID_URL,
                    "speaker": "Andy Reid",
                    "title": REID_TITLE,
                    "date": "2026-10-19",
                },
                {
                    "url": POSTGAME_URL,
                    "speaker": "Andy Reid, Patrick Mahomes, Ken Walker, and Nick Bolton",
                    "title": POSTGAME_TITLE,
                    "date": "2026-10-18",
                },
            ]
        }

        def fetch(url):
            if "wV0rX_GUtTg" in url:
                return _oembed(REID_TITLE)
            if "HqrXUR4Gg4o" in url:
                return _oembed(POSTGAME_TITLE)
            return None

        self.assertEqual(
            press_conferences.check_embeds(
                narrative, oembed_fetch=fetch, phase=WEEK_PHASE
            ),
            [],
        )

    def test_attach_skips_future_placeholder_and_keeps_verified(self):
        future_url = "https://www.youtube.com/watch?v=FzPyAEhwr3U"
        future_title = (
            "Andy Reid and select players Speak to Media | OCTOBER 24, 2026"
        )
        placeholder = {
            "type": "video",
            "html": '<iframe title="Andy Reid Speaks to Media"></iframe>',
        }
        signals = {
            "pressConferences": [
                {
                    "url": future_url,
                    "speaker": "Andy Reid",
                    "title": future_title,
                    "date": "2026-10-24",
                },
                {
                    "url": REID_URL,
                    "speaker": "Andy Reid",
                    "title": REID_TITLE,
                    "date": "2026-10-19",
                },
            ]
        }

        def fetch(url):
            if "FzPyAEhwr3U" in url:
                return placeholder
            if "wV0rX_GUtTg" in url:
                return _oembed(REID_TITLE)
            return None

        narrative = {"pressConferences": []}
        with patch.object(
            press_conferences,
            "_now",
            return_value=datetime(2026, 10, 20, 16, 14, tzinfo=timezone.utc),
        ):
            press_conferences.attach_verified(
                narrative, signals, WEEK_PHASE, oembed_fetch=fetch
            )
        self.assertEqual(len(narrative["pressConferences"]), 1)
        self.assertEqual(narrative["pressConferences"][0]["url"], REID_URL)

    def test_collect_skips_future_session_dates(self):
        xml = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:yt="http://www.youtube.com/xml/schemas/2015">
  <entry>
    <yt:videoId>wV0rX_GUtTg</yt:videoId>
    <title>Andy Reid Speaks to the Media | OCTOBER 19, 2026</title>
    <published>2026-10-19T12:00:00Z</published>
  </entry>
  <entry>
    <yt:videoId>FzPyAEhwr3U</yt:videoId>
    <title>Andy Reid and select players Speak to Media | OCTOBER 30, 2026</title>
    <published>2026-10-28T12:00:00Z</published>
  </entry>
</feed>
"""
        with patch.object(press_conferences, "_http_get", return_value=xml), patch.object(
            press_conferences,
            "_now",
            return_value=datetime(2026, 10, 28, 16, 14, tzinfo=timezone.utc),
        ):
            rows = press_conferences.collect_candidates()
        urls = [row["url"] for row in rows]
        self.assertIn(REID_URL, urls)
        self.assertFalse(any("FzPyAEhwr3U" in url for url in urls))

    def test_attach_overwrites_invented_clips_and_omits_unverified(self):
        narrative = _normalize(
            {
                "pressConferences": [
                    {
                        "url": "https://www.youtube.com/watch?v=AAAAAAAAAAA",
                        "speaker": "Andy Reid",
                    }
                ]
            }
        )
        signals = {
            "pressConferences": [
                {
                    "url": REID_URL,
                    "speaker": "Andy Reid",
                    "title": REID_TITLE,
                    "date": "2026-10-19",
                }
            ]
        }
        press_conferences.attach_verified(
            narrative,
            signals,
            WEEK_PHASE,
            oembed_fetch=lambda url: _oembed(REID_TITLE),
        )
        self.assertEqual(narrative["pressConferences"][0]["url"], REID_URL)

        dead = {"pressConferences": []}
        press_conferences.attach_verified(
            dead,
            signals,
            WEEK_PHASE,
            oembed_fetch=lambda url: None,
        )
        self.assertEqual(dead["pressConferences"], [])

    def test_author_name_missing_allowlists_via_url(self):
        """author_name missing on its own is still official if author_url is."""
        payload = {
            "title": REID_TITLE,
            "author_url": "https://www.youtube.com/@KansasCityChiefs",
            "type": "video",
            "html": "<iframe></iframe>",
        }
        narrative = {"pressConferences": []}
        press_conferences.attach_verified(
            narrative,
            {
                "pressConferences": [
                    {
                        "url": REID_URL,
                        "speaker": "Andy Reid",
                        "title": REID_TITLE,
                        "date": "2026-10-19",
                    }
                ]
            },
            WEEK_PHASE,
            oembed_fetch=lambda url: payload,
        )
        self.assertEqual(narrative["pressConferences"][0]["url"], REID_URL)

    def test_author_url_missing_allowlists_via_name(self):
        """author_url missing on its own is still official if author_name is."""
        payload = {
            "title": REID_TITLE,
            "author_name": "Kansas City Chiefs",
            "type": "video",
            "html": "<iframe></iframe>",
        }
        narrative = {"pressConferences": []}
        press_conferences.attach_verified(
            narrative,
            {
                "pressConferences": [
                    {
                        "url": REID_URL,
                        "speaker": "Andy Reid",
                        "title": REID_TITLE,
                        "date": "2026-10-19",
                    }
                ]
            },
            WEEK_PHASE,
            oembed_fetch=lambda url: payload,
        )
        self.assertEqual(narrative["pressConferences"][0]["url"], REID_URL)

    def test_today_presser_is_kept_when_future_uses_strict_greater_than(self):
        """`>=` instead of `>` would treat today's CT date as future and drop it."""
        today_title = "Andy Reid Speaks to the Media | OCTOBER 20, 2026"
        signals = {
            "pressConferences": [
                {
                    "url": REID_URL,
                    "speaker": "Andy Reid",
                    "title": today_title,
                    "date": "2026-10-20",
                }
            ]
        }
        narrative = {"pressConferences": []}
        with patch.object(
            press_conferences,
            "_now",
            return_value=datetime(2026, 10, 20, 16, 14, tzinfo=timezone.utc),
        ):
            press_conferences.attach_verified(
                narrative,
                signals,
                WEEK_PHASE,
                oembed_fetch=lambda url: _oembed(today_title),
            )
        self.assertEqual(narrative["pressConferences"][0]["url"], REID_URL)

        xml = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:yt="http://www.youtube.com/xml/schemas/2015">
  <entry>
    <yt:videoId>wV0rX_GUtTg</yt:videoId>
    <title>Andy Reid Speaks to the Media | OCTOBER 20, 2026</title>
    <published>2026-10-20T12:00:00Z</published>
  </entry>
</feed>
"""
        with patch.object(press_conferences, "_http_get", return_value=xml), patch.object(
            press_conferences,
            "_now",
            return_value=datetime(2026, 10, 20, 16, 14, tzinfo=timezone.utc),
        ):
            rows = press_conferences.collect_candidates()
        self.assertIn(REID_URL, [row["url"] for row in rows])

    def test_future_single_identity_field_is_omitted_not_published(self):
        """Name-only or url-only is identity; a future date is omitted, not shipped."""
        future = {
            "url": "https://www.youtube.com/watch?v=FzPyAEhwr3U",
            "speaker": "Andy Reid",
            "title": "Andy Reid Speaks to the Media | OCTOBER 24, 2026",
            "date": "2026-10-24",
        }
        name_only = {
            "title": future["title"],
            "author_name": "Kansas City Chiefs",
            "type": "video",
            "html": "<iframe></iframe>",
        }
        url_only = {
            "title": future["title"],
            "author_url": "https://www.youtube.com/@KansasCityChiefs",
            "type": "video",
            "html": "<iframe></iframe>",
        }
        now = datetime(2026, 10, 20, 16, 14, tzinfo=timezone.utc)
        for payload in (name_only, url_only):
            narrative = {"pressConferences": []}
            with patch.object(press_conferences, "_now", return_value=now):
                press_conferences.attach_verified(
                    narrative,
                    {"pressConferences": [future]},
                    WEEK_PHASE,
                    oembed_fetch=lambda url, _p=payload: _p,
                )
                issues = press_conferences.check_embeds(
                    {"pressConferences": [future]},
                    oembed_fetch=lambda url, _p=payload: _p,
                    phase=WEEK_PHASE,
                )
            self.assertEqual(narrative["pressConferences"], [])
            self.assertTrue(any("unverified embed" in item for item in issues))

    def test_missing_date_identityless_is_omitted_not_skipped_as_future(self):
        """A missing date is not future; an identity-less 200 is omitted, not shipped."""
        placeholder = {
            "type": "video",
            "html": '<iframe title="Andy Reid Speaks to Media"></iframe>',
        }
        signals = {
            "pressConferences": [
                {
                    "url": REID_URL,
                    "speaker": "Andy Reid",
                    "title": "Andy Reid Speaks to the Media",
                }
            ]
        }
        narrative = {"pressConferences": []}
        press_conferences.attach_verified(
            narrative,
            signals,
            WEEK_PHASE,
            oembed_fetch=lambda url: placeholder,
        )
        self.assertEqual(narrative["pressConferences"], [])
        session = press_conferences._session_from_item(signals["pressConferences"][0])
        self.assertIsNone(session)
        self.assertFalse(press_conferences.session_is_future(session))
        self.assertFalse(
            press_conferences._candidate_not_ready(
                signals["pressConferences"][0], placeholder
            )
        )

    def test_past_dated_identityless_oembed_is_omitted(self):
        placeholder = {
            "type": "video",
            "html": '<iframe title="Andy Reid Speaks to Media"></iframe>',
        }
        signals = {
            "pressConferences": [
                {
                    "url": "https://www.youtube.com/watch?v=1I3b1uRI24o",
                    "speaker": "Andy Reid",
                    "title": (
                        "Andy Reid, Patrick Mahomes, and Select Players "
                        "Speak to Media | OCTOBER 20, 2026"
                    ),
                    "date": "2026-10-20",
                }
            ]
        }
        narrative = {"pressConferences": []}
        with patch.object(
            press_conferences,
            "_now",
            return_value=datetime(2026, 10, 20, 16, 14, tzinfo=timezone.utc),
        ):
            press_conferences.attach_verified(
                narrative,
                signals,
                WEEK_PHASE,
                oembed_fetch=lambda url: placeholder,
            )
        self.assertEqual(narrative["pressConferences"], [])

    def test_published_future_allowlisted_embed_fails_closed(self):
        """Removing the published-embed future gate would let this pass."""
        future_title = "Andy Reid Speaks to the Media | OCTOBER 21, 2026"
        published = {
            "pressConferences": [
                {
                    "url": REID_URL,
                    "speaker": "Andy Reid",
                    "title": future_title,
                    "date": "2026-10-21",
                }
            ]
        }
        with patch.object(
            press_conferences,
            "_now",
            return_value=datetime(2026, 10, 20, 16, 14, tzinfo=timezone.utc),
        ):
            issues = press_conferences.check_embeds(
                published,
                oembed_fetch=lambda url: _oembed(future_title),
                phase=WEEK_PHASE,
            )
        self.assertTrue(issues)
        self.assertTrue(any("unverified embed" in item for item in issues))

    def test_fetch_exception_is_unverified_not_empty_placeholder(self):
        """Turning a fetch exception into {} would skip a future identity-less."""
        future_url = "https://www.youtube.com/watch?v=FzPyAEhwr3U"
        signals = {
            "pressConferences": [
                {
                    "url": future_url,
                    "speaker": "Andy Reid",
                    "title": (
                        "Andy Reid and select players Speak to Media | "
                        "OCTOBER 24, 2026"
                    ),
                    "date": "2026-10-24",
                }
            ]
        }

        def boom(url):
            raise RuntimeError("oembed down")

        narrative = {"pressConferences": []}
        with patch.object(
            press_conferences,
            "_now",
            return_value=datetime(2026, 10, 20, 16, 14, tzinfo=timezone.utc),
        ):
            press_conferences.attach_verified(
                narrative,
                signals,
                WEEK_PHASE,
                oembed_fetch=boom,
            )
        self.assertEqual(narrative["pressConferences"], [])
        self.assertFalse(
            press_conferences.upcoming_placeholder(None)
        )

    def test_today_identityless_collect_candidate_does_not_abort_edition(self):
        """Run 40911045488: JcLyxvT2pkU passed collect then failed verify.

        Collect skips only future session dates. A today-dated Chiefs
        livestream still in the identity-less oEmbed-200 placeholder
        state must be omitted after write, not raise and abort the
        edition. A verified sibling stays. Publishing it still fails.
        """
        today_url = "https://www.youtube.com/watch?v=JcLyxvT2pkU"
        today_title = "Coordinators and Chris Jones Speak to Media | OCTOBER 29, 2026"
        week8 = {
            "type": "regular",
            "label": "Week 8",
            "week": 8,
            "mode": "preview",
            "lastGame": {
                "date": "2026-10-25T00:20:00Z",
                "opponent": "Seattle Seahawks",
                "completed": True,
            },
            "nextGame": {
                "date": "2026-11-01T20:25:00Z",
                "opponent": "Denver Broncos",
            },
        }
        placeholder = {
            "type": "video",
            "html": (
                '<iframe title="Coordinators and Chris Jones Speak to Media '
                '| OCTOBER 29, 2026"></iframe>'
            ),
        }
        run_at = datetime(2026, 10, 29, 16, 23, tzinfo=timezone.utc)
        today_item = {
            "url": today_url,
            "speaker": "Chris Jones",
            "title": today_title,
            "date": "2026-10-29",
        }
        session = press_conferences._session_from_item(today_item)
        self.assertFalse(press_conferences.session_is_future(session, now=run_at))
        self.assertTrue(press_conferences.in_game_week(session, week8))
        self.assertFalse(
            press_conferences._candidate_not_ready(today_item, placeholder)
        )

        xml = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:yt="http://www.youtube.com/xml/schemas/2015">
  <entry>
    <yt:videoId>wV0rX_GUtTg</yt:videoId>
    <title>Andy Reid Speaks to the Media | OCTOBER 28, 2026</title>
    <published>2026-10-28T17:50:00Z</published>
  </entry>
  <entry>
    <yt:videoId>JcLyxvT2pkU</yt:videoId>
    <title>Coordinators and Chris Jones Speak to Media | OCTOBER 29, 2026</title>
    <published>2026-10-29T15:00:00Z</published>
  </entry>
  <entry>
    <yt:videoId>FzPyAEhwr3U</yt:videoId>
    <title>Andy Reid and select players Speak to Media | OCTOBER 30, 2026</title>
    <published>2026-10-29T15:00:00Z</published>
  </entry>
</feed>
"""
        with patch.object(press_conferences, "_http_get", return_value=xml), patch.object(
            press_conferences,
            "_now",
            return_value=run_at,
        ):
            rows = press_conferences.collect_candidates()
        urls = [row["url"] for row in rows]
        self.assertIn(today_url, urls)
        self.assertIn(REID_URL, urls)
        self.assertFalse(any("FzPyAEhwr3U" in url for url in urls))

        def fetch(url):
            if "JcLyxvT2pkU" in url:
                return placeholder
            if "wV0rX_GUtTg" in url:
                return _oembed("Andy Reid Speaks to the Media | OCTOBER 28, 2026")
            return None

        narrative = {"pressConferences": []}
        with patch.object(press_conferences, "_now", return_value=run_at):
            press_conferences.attach_verified(
                narrative,
                {
                    "pressConferences": [
                        today_item,
                        {
                            "url": REID_URL,
                            "speaker": "Andy Reid",
                            "title": "Andy Reid Speaks to the Media | OCTOBER 28, 2026",
                            "date": "2026-10-28",
                        },
                    ]
                },
                week8,
                oembed_fetch=fetch,
            )
        self.assertEqual(len(narrative["pressConferences"]), 1)
        self.assertEqual(narrative["pressConferences"][0]["url"], REID_URL)

        published = {
            "pressConferences": [
                {
                    "url": today_url,
                    "speaker": "Chris Jones",
                    "title": today_title,
                    "date": "2026-10-29",
                }
            ]
        }
        with patch.object(press_conferences, "_now", return_value=run_at):
            issues = press_conferences.check_embeds(
                published,
                oembed_fetch=lambda url: placeholder,
                phase=week8,
            )
        self.assertTrue(any("unverified embed" in item for item in issues))

    def test_today_is_america_chicago_not_utc(self):
        """04:00 UTC Oct 29 is still Oct 28 in Chicago; Oct 29 is tomorrow."""
        xml = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:yt="http://www.youtube.com/xml/schemas/2015">
  <entry>
    <yt:videoId>wV0rX_GUtTg</yt:videoId>
    <title>Andy Reid Speaks to the Media | OCTOBER 28, 2026</title>
    <published>2026-10-28T12:00:00Z</published>
  </entry>
  <entry>
    <yt:videoId>FzPyAEhwr3U</yt:videoId>
    <title>Andy Reid Speaks to the Media | OCTOBER 29, 2026</title>
    <published>2026-10-29T03:00:00Z</published>
  </entry>
</feed>
"""
        evening_utc = datetime(2026, 10, 29, 4, 0, tzinfo=timezone.utc)
        self.assertEqual(evening_utc.astimezone(_CT).date().isoformat(), "2026-10-28")
        with patch.object(press_conferences, "_http_get", return_value=xml), patch.object(
            press_conferences,
            "_now",
            return_value=evening_utc,
        ):
            rows = press_conferences.collect_candidates()
        urls = [row["url"] for row in rows]
        self.assertIn(REID_URL, urls)
        self.assertFalse(any("FzPyAEhwr3U" in url for url in urls))
        oct29 = datetime(2026, 10, 29, tzinfo=_CT)
        self.assertTrue(press_conferences.session_is_future(oct29, now=evening_utc))
        oct28 = datetime(2026, 10, 28, tzinfo=_CT)
        self.assertFalse(press_conferences.session_is_future(oct28, now=evening_utc))

    def test_check_edition_fails_press_issues_even_when_held(self):
        payload = {
            "headline": "Tape",
            "pressConferences": [
                {
                    "url": REID_URL,
                    "speaker": "Andy Reid",
                    "title": REID_TITLE,
                    "date": "2026-10-19",
                }
            ],
            "xsandos": [],
        }
        with patch.object(
            generate,
            "check_published_edition",
            return_value=[f"press conference: unverified embed {REID_URL}"],
        ):
            rc = generate.main(["--check-edition"])
        self.assertEqual(rc, 1)


class PressConferenceTemplate(unittest.TestCase):
    def test_template_section_is_gated_and_names_the_speaker(self):
        edition = (
            ROOT / "layouts" / "partials" / "narrative-edition.html"
        ).read_text(encoding="utf-8")
        self.assertIn('{{ if or .pressConferences .pressConferenceLinks }}', edition)
        self.assertIn('{{ range .pressConferences }}', edition)
        self.assertIn('id="press-conferences"', edition)
        self.assertIn('index $sectionTitles "pressConferences"', edition)
        self.assertIn('partial "nrt-yt-embed.html"', edition)
        self.assertLess(
            edition.find('class="section nrt-story"'),
            edition.find('id="press-conferences"'),
        )
        self.assertLess(
            edition.find('id="press-conferences"'),
            edition.find('id="xo"'),
        )
        self.assertNotIn("nrt-hero", edition[edition.find("PRESS CONFERENCES"):])
        partial = (ROOT / "layouts" / "partials" / "nrt-yt-embed.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("nrt-yt-embed__thumb", partial)
        self.assertIn("nrt-yt-embed__speaker", partial)
        self.assertIn("{{ $speaker }}", partial)
        self.assertIn("if and $url $id $speaker", partial)
        self.assertNotIn("youtube-nocookie.com/embed/", partial)
        self.assertNotIn("<iframe", partial)
        css = (ROOT / "public" / "css" / "narrative.css").read_text(encoding="utf-8")
        self.assertIn(".nrt-press__list", css)
        self.assertIn("max-width: 74ch", css[css.find(".nrt-press__list"):])
        self.assertIn("160px", css[css.find(".nrt-yt-embed__thumb"):])
        js = (ROOT / "public" / "js" / "main.js").read_text(encoding="utf-8")
        self.assertIn("function initNarrativeYtEmbeds", js)
        self.assertIn("initNarrativeYtEmbeds(root)", js)
        self.assertIn("nrt-yt-embed__thumb", js)

    def test_prompt_does_not_ask_the_model_for_youtube_ids(self):
        text = prompts.build_user_prompt({"news": [], "schedule": []}, WEEK_PHASE, [])
        hint = __import__('json').loads(prompts._schema_hint(WEEK_PHASE, ''))
        self.assertNotIn('pressConferences', hint)
        self.assertNotIn('pressConferenceLinks', hint)
        self.assertNotIn('videoId', hint)
        self.assertIn("Do not invent or include YouTube video IDs", text)

    def test_speakers_from_title(self):
        self.assertEqual(
            press_conferences.speakers_from_title(REID_TITLE), "Andy Reid"
        )
        self.assertEqual(
            press_conferences.speakers_from_title(POSTGAME_TITLE),
            "Andy Reid, Patrick Mahomes, Ken Walker, and Nick Bolton",
        )

    def test_game_week_includes_postgame_calendar_day(self):
        postgame = datetime(2026, 10, 18, tzinfo=_CT)
        friday = datetime(2026, 10, 16, tzinfo=_CT)
        monday = datetime(2026, 10, 19, tzinfo=_CT)
        self.assertTrue(press_conferences.in_game_week(postgame, WEEK_PHASE))
        self.assertTrue(press_conferences.in_game_week(monday, WEEK_PHASE))
        self.assertFalse(press_conferences.in_game_week(friday, WEEK_PHASE))


if __name__ == "__main__":
    unittest.main()
