"""Official X hero pins: allowlist, sidecar, generator, embed render."""
from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.chiefs_narrative import generate, hero_posts

ROOT = Path(__file__).resolve().parents[2]
HUGO = shutil.which("hugo") or str(Path.home() / ".local" / "hugo" / "hugo")
WEEK8_SLUG = "2026-10-28-1750"
WEEK8_ID = "2115471247190438112"
WEEK8_URL = f"https://x.com/Chiefs/status/{WEEK8_ID}"


def _preview(opponent="Denver Broncos", *, week=8, at="@ Empower Field at Mile High",
             home_away="away", slug=WEEK8_SLUG):
    return {
        "slug": slug,
        "headline": "Denver is the assignment",
        "phase": {"mode": "preview", "type": "regular", "week": week, "label": "Week 8"},
        "nextGame": {"opponent": opponent, "label": "Week 8 · Sun, Nov 1", "at": at},
        "markets": {"game": {"opponent": opponent, "homeAway": home_away}},
    }


class HeroPostAllowlist(unittest.TestCase):
    def test_week8_pin_is_allowlisted_chiefs_snowflake(self):
        pin = hero_posts.pin_for_slug(WEEK8_SLUG)
        self.assertIsNotNone(pin)
        self.assertEqual(pin["id"], WEEK8_ID)
        self.assertEqual(pin["handle"], "Chiefs")
        self.assertEqual(pin["url"], WEEK8_URL)
        self.assertEqual(pin["text"], "Sights set on Denver.")
        self.assertIn("Denver", pin["alt"])

    def test_off_list_and_malformed_ids_are_rejected(self):
        good = {
            "id": WEEK8_ID,
            "handle": "Chiefs",
            "url": WEEK8_URL,
            "postedAt": "2026-10-28T15:50:23Z",
            "text": "Sights set on Denver.",
        }
        self.assertIsNotNone(hero_posts.normalize_pin(good))
        for handle in ("ArrowheadPride", "Polymarket", "DraftKings", "ChiefsFan",
                       "KCMemeTeam", "sponsoredchiefs"):
            row = dict(good)
            row["handle"] = handle
            row["url"] = f"https://x.com/{handle}/status/{WEEK8_ID}"
            self.assertIsNone(hero_posts.normalize_pin(row), handle)
        self.assertIsNone(hero_posts.normalize_pin({**good, "id": "abc"}))
        self.assertIsNone(hero_posts.normalize_pin({**good, "id": "12"}))
        self.assertIsNone(hero_posts.normalize_pin({**good, "url": "https://x.com/Chiefs/status/nope"}))
        self.assertIsNone(hero_posts.normalize_pin("https://x.com/Chiefs/status/" + WEEK8_ID))

    def test_validate_sidecar_is_offline(self):
        with patch("urllib.request.urlopen", side_effect=AssertionError("network")):
            with patch("requests.get", side_effect=AssertionError("network")):
                issues = hero_posts.validate_sidecar()
        self.assertEqual(issues, [])

    def test_gate_fails_off_list_handle_in_sidecar(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "data"
            data.mkdir()
            payload = json.loads((ROOT / "data" / "hero_posts.json").read_text(encoding="utf-8"))
            payload["pins"]["2026-10-01-0000"] = {
                "id": WEEK8_ID,
                "handle": "ArrowheadPride",
                "url": f"https://x.com/ArrowheadPride/status/{WEEK8_ID}",
                "text": "fan recap",
            }
            (data / "hero_posts.json").write_text(json.dumps(payload), encoding="utf-8")
            issues = hero_posts.validate_sidecar(root)
            self.assertTrue(any("2026-10-01-0000" in item for item in issues))

    def test_generator_pins_new_edition_in_sidecar_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "data"
            data.mkdir()
            shutil.copyfile(ROOT / "data" / "hero_posts.json", data / "hero_posts.json")
            edition = _preview(slug="2026-10-29-1200")
            before = json.dumps(edition)
            pin = hero_posts.attach_sidecar(edition, root=root)
            self.assertIsNotNone(pin)
            self.assertEqual(pin["id"], WEEK8_ID)
            self.assertEqual(json.dumps(edition), before)
            written = hero_posts.pin_for_slug("2026-10-29-1200", root=root)
            self.assertEqual(written["id"], WEEK8_ID)
            self.assertIsNone(hero_posts.pin_for_slug("2026-10-27-1915", root=root))

    def test_allowlist_lives_in_code(self):
        self.assertIn("chiefs", hero_posts.ALLOWLIST)
        self.assertIn("chiefspr", hero_posts.ALLOWLIST)
        self.assertIn("nfl", hero_posts.ALLOWLIST)
        self.assertIn("theathletic", hero_posts.ALLOWLIST)
        self.assertIn("espn", hero_posts.ALLOWLIST)
        src = (ROOT / "tools" / "chiefs_narrative" / "hero_posts.py").read_text(encoding="utf-8")
        self.assertIn("ALLOWLIST", src)
        self.assertNotIn("arrowheadpride", hero_posts.ALLOWLIST)
        self.assertNotIn("polymarket", hero_posts.ALLOWLIST)

    def test_csp_if_present_allows_exactly_platform_twitter(self):
        hits = []
        for path in ROOT.rglob("*"):
            if not path.is_file() or "node_modules" in path.parts or ".git" in path.parts:
                continue
            if path.suffix.lower() not in {".html", ".js", ".css", ".yml", ".yaml", ".json", ".toml", ".headers"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if re.search(r"content-security-policy", text, re.I):
                hits.append(path)
                self.assertIn("platform.twitter.com", text)
                self.assertNotIn("cdn.syndication.twimg.com", text)

    def test_no_network_imports_in_hero_posts(self):
        src = (ROOT / "tools" / "chiefs_narrative" / "hero_posts.py").read_text(encoding="utf-8")
        self.assertNotIn("requests", src)
        self.assertNotIn("urllib.request", src)
        self.assertNotIn("publish.twitter.com", src)
        self.assertNotIn("api.x.com", src)


@unittest.skipUnless(Path(HUGO).is_file(), "Hugo unavailable")
class HeroPostRendering(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        source = Path(cls.temp.name) / "source"
        source.mkdir()
        (source / "data").mkdir()
        shutil.copytree(ROOT / "layouts" / "partials", source / "layouts" / "partials")
        shutil.copyfile(ROOT / "data" / "hero_scenes.json", source / "data" / "hero_scenes.json")
        shutil.copyfile(ROOT / "data" / "schedule_2026.json", source / "data" / "schedule_2026.json")
        shutil.copyfile(ROOT / "data" / "hero_media.json", source / "data" / "hero_media.json")
        shutil.copyfile(ROOT / "data" / "hero_posts.json", source / "data" / "hero_posts.json")
        public_scenes = source / "public" / "images" / "hero-scenes"
        public_scenes.parent.mkdir(parents=True)
        shutil.copytree(ROOT / "public" / "images" / "hero-scenes", public_scenes)
        (source / "hugo.yaml").write_text(
            "baseURL: https://example.test/\nstaticDir:\n  - public\n", encoding="utf-8"
        )
        fixtures = [
            {
                "slug": WEEK8_SLUG,
                "headline": "Denver is the assignment after 213 yards",
                "generatedAt": "2026-10-28T17:50:00Z",
                "phase": {"mode": "preview", "type": "regular", "week": 8, "label": "Week 8"},
                "record": "5-1",
                "edition": "2026 Week 8 · Preview",
                "nextGame": {
                    "opponent": "Denver Broncos",
                    "label": "Week 8 · Sun, Nov 1 · 3:25 PM CT",
                    "at": "@ Empower Field at Mile High",
                },
                "markets": {"game": {"opponent": "Denver Broncos", "homeAway": "away"}},
                "lastGameReview": {"opponent": "Seattle Seahawks", "label": "Week 7"},
                "injuries": [],
                "pressConferenceLinks": [],
                "pressConferences": [],
            },
            {
                "slug": "2026-10-27-1915",
                "headline": "213 yards, a 50-yard answer",
                "generatedAt": "2026-10-27T19:15:00Z",
                "phase": {"mode": "review", "type": "regular", "week": 7, "label": "Week 7"},
                "record": "5-1",
                "edition": "2026 Week 7 · Review",
                "nextGame": {"opponent": "Denver Broncos"},
                "lastGameReview": {
                    "opponent": "Seattle Seahawks",
                    "label": "Week 7 · Sun, Oct 25 · 7:20 PM CT",
                    "lede": "At Lumen Field, Seattle controlled both lines.",
                },
                "injuries": [],
                "pressConferenceLinks": [],
                "pressConferences": [],
            },
        ]
        (source / "data" / "fixtures.json").write_text(json.dumps(fixtures), encoding="utf-8")
        (source / "layouts" / "index.html").write_text(
            '{{ range .Site.Data.fixtures }}{{ partial "narrative-edition.html" (dict "n" . "site" $.Site "isCurrent" true) }}{{ end }}',
            encoding="utf-8",
        )
        cls.dist = Path(cls.temp.name) / "site"
        result = subprocess.run(
            [HUGO, "--minify", "--destination", str(cls.dist)],
            cwd=source, capture_output=True, text=True,
        )
        if result.returncode:
            cls.temp.cleanup()
            raise AssertionError(result.stdout + result.stderr)
        cls.page = (cls.dist / "index.html").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def _heroes(self):
        return [html.unescape(h) for h in re.findall(
            r"<figure\b[^>]*class=['\"]?nrt-hero-media\b.*?</figure>", self.page, re.S
        )]

    def test_week8_renders_blockquote_credit_and_link(self):
        den, sea = self._heroes()
        self.assertRegex(den, r'data-hero-kind="?x-embed"?')
        self.assertRegex(den, r'class="?twitter-tweet"?')
        self.assertIn(WEEK8_URL, den)
        self.assertIn("@Chiefs", den)
        self.assertIn("Sights set on Denver.", den)
        self.assertIn("View post on X", den)
        self.assertIn("Source:", den)
        self.assertIn("nrt-hero-media__visual--embed", den)
        self.assertRegex(self.page, r'<script\s+async\s+src="?https://platform\.twitter\.com/widgets\.js"?')
        self.assertIn("async", self.page[self.page.find("platform.twitter.com/widgets.js") - 80:])
        self.assertNotIn("pbs.twimg.com", den)
        self.assertNotIn("video.twimg.com", den)
        self.assertNotIn("syndication.twitter.com", den)

    def test_unpinned_archive_uses_svg_and_does_not_load_widgets_twice(self):
        den, sea = self._heroes()
        self.assertRegex(sea, r'data-hero-kind="?article-illustration"?')
        self.assertIn("review-away-sea.svg", sea)
        self.assertNotRegex(sea, r'data-hero-kind="?x-embed"?')
        self.assertNotIn("Arrowhead", sea)
        scripts = re.findall(r"https://platform\.twitter\.com/widgets\.js", self.page)
        self.assertEqual(scripts, ["https://platform.twitter.com/widgets.js"])

    def test_hero_partial_only_mentions_platform_twitter(self):
        media = (ROOT / "layouts" / "partials" / "nrt-hero-media.html").read_text(encoding="utf-8")
        self.assertIn("https://platform.twitter.com/widgets.js", media)
        self.assertNotIn("cdn.jsdelivr", media)
        self.assertNotIn("googletagmanager", media)
        self.assertIn('partial "nrt-hero-post.html"', media)


class HeroPostGenerateHook(unittest.TestCase):
    def test_generate_writes_sidecar_after_edition_payload(self):
        src = (ROOT / "tools" / "chiefs_narrative" / "generate.py").read_text(encoding="utf-8")
        write_at = src.find("_write_edition_payload(narrative)")
        attach_at = src.find("hero_posts.attach_sidecar(narrative)")
        self.assertGreater(write_at, 0)
        self.assertGreater(attach_at, write_at)
        self.assertIn("issues.extend(hero_posts.validate_sidecar())", src)
