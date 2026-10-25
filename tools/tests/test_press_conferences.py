"""Press-conference YouTube embeds: schema, oEmbed gate, template, collector."""
from __future__ import annotations

import unittest
from datetime import datetime
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

    def test_attach_overwrites_invented_clips_and_fails_closed(self):
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

        with self.assertRaises(press_conferences.UnverifiedPressConferenceError):
            press_conferences.attach_verified(
                {"pressConferences": []},
                signals,
                WEEK_PHASE,
                oembed_fetch=lambda url: None,
            )

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
