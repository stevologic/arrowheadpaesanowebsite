"""Topic-matched hero illustrations: schedule coverage, preview vs review, assets."""
from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.chiefs_narrative import hero_scenes

ROOT = Path(__file__).resolve().parents[2]
HUGO = shutil.which("hugo") or str(Path.home() / ".local" / "hugo" / "hugo")
GENERIC_MARKERS = (
    "KANSAS CITY CHIEFS",
    "TEMPORARY ARTICLE ILLUSTRATION",
    "Temporary article illustration",
    'fill="#991c35"',
    'fill="#102e24"',
    "MATCHUP PREVIEW",
    "GAME REVIEW",
)


def _edition(mode, opponent, *, week=None, phase_type="regular", home_away="", at="",
             headline="Fixture headline", label="Fixture date", lede=""):
    phase = {"mode": mode, "type": phase_type, "label": mode.title()}
    if week is not None:
        phase["week"] = week
    row = {"headline": headline, "phase": phase, "slug": f"fix-{mode}"}
    if mode == "preview":
        row["nextGame"] = {"opponent": opponent, "label": label, "at": at}
        if home_away:
            row["markets"] = {"game": {"opponent": opponent, "homeAway": home_away}}
    elif mode == "review":
        row["lastGameReview"] = {"opponent": opponent, "label": label, "lede": lede, "at": at}
    return row


class HeroSceneCatalog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schedule = hero_scenes.load_schedule(ROOT)
        cls.catalog = hero_scenes.build_catalog(cls.schedule)

    def test_every_schedule_opponent_maps_preview_and_review(self):
        missing = []
        for game in self.schedule:
            for mode in ("preview", "review"):
                edition = _edition(
                    mode, game["opponent"], week=game["week"],
                    phase_type="preseason" if game["seasonType"] == "pre" else "regular",
                    home_away=game["homeAway"], at=game["venue"],
                )
                scene = hero_scenes.resolve(edition, schedule=self.schedule, catalog=self.catalog)
                expected = hero_scenes.scene_id(mode, game["homeAway"], game["opponentAbbr"])
                if scene.get("id") != expected or not scene.get("mapped", True):
                    missing.append((game["id"], mode, scene.get("id"), expected))
                self.assertEqual(scene["opponentAbbr"], game["opponentAbbr"])
                self.assertEqual(scene["homeAway"], game["homeAway"])
                self.assertIn(game["venue"].split()[0].lower(), scene["alt"].lower() + scene["venue"].lower() + scene["venueKey"])
        self.assertEqual(missing, [])

    def test_preview_and_review_pick_distinct_matching_scenes(self):
        den_preview = hero_scenes.resolve(
            _edition("preview", "Denver Broncos", week=8, home_away="away",
                     at="@ Empower Field at Mile High",
                     headline="Denver is the assignment after 213 yards"),
            schedule=self.schedule, catalog=self.catalog,
        )
        sea_review = hero_scenes.resolve(
            _edition("review", "Seattle Seahawks", week=7,
                     label="Week 7 · Sun, Oct 25 · 7:20 PM CT",
                     lede="At Lumen Field, Seattle controlled both lines.",
                     headline="213 yards, a 50-yard answer"),
            schedule=self.schedule, catalog=self.catalog,
        )
        self.assertEqual(den_preview["id"], "preview-away-den")
        self.assertEqual(sea_review["id"], "review-away-sea")
        self.assertNotEqual(den_preview["id"], sea_review["id"])
        self.assertIn("mountain", den_preview["alt"].lower())
        self.assertIn("rain", sea_review["alt"].lower())
        self.assertIn("night", sea_review["alt"].lower())
        den_home = hero_scenes.resolve(
            _edition("review", "Denver Broncos", week=1, home_away="home", at="Arrowhead Stadium"),
            schedule=self.schedule, catalog=self.catalog,
        )
        self.assertEqual(den_home["id"], "review-home-den")
        self.assertNotEqual(den_home["id"], den_preview["id"])

    def test_unknown_opponent_uses_phase_fallback_not_a_mapped_card(self):
        scene = hero_scenes.resolve(
            _edition("preview", "Test Sharks"),
            schedule=self.schedule, catalog=self.catalog,
        )
        self.assertEqual(scene["id"], "preview-fallback")
        self.assertFalse(scene["mapped"])

    def test_committed_assets_match_generator_and_have_no_wordmarks(self):
        generated = hero_scenes.build_catalog(self.schedule)
        committed = json.loads((ROOT / "data" / "hero_scenes.json").read_text(encoding="utf-8"))
        self.assertEqual(committed, hero_scenes.catalog_for_hugo(generated))
        scene_dir = ROOT / "public" / "images" / "hero-scenes"
        for sid, scene in generated["scenes"].items():
            path = scene_dir / f"{sid}.svg"
            self.assertTrue(path.is_file(), sid)
            svg = path.read_text(encoding="utf-8")
            self.assertEqual(svg, hero_scenes.render_svg(scene), sid)
            hits = hero_scenes.asset_text_violations(svg)
            self.assertEqual(hits, [], f"{sid}: {hits}")
            for marker in GENERIC_MARKERS:
                self.assertNotIn(marker, svg)
            self.assertNotIn("<text", svg)

    def test_home_arrowhead_and_phase_fallbacks_exist(self):
        homes = [m for m in self.catalog["matchups"] if m["homeAway"] == "home"]
        self.assertGreaterEqual(len(homes), 8)
        for row in homes:
            self.assertEqual(row["venueKey"], "arrowhead")
            self.assertIn(row["previewScene"], self.catalog["scenes"])
        for mode in hero_scenes.PHASE_MODES:
            self.assertIn(hero_scenes.FALLBACK_IDS[mode], self.catalog["scenes"])


@unittest.skipUnless(Path(HUGO).is_file(), "Hugo unavailable")
class HeroSceneRendering(unittest.TestCase):
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
        public_scenes = source / "public" / "images" / "hero-scenes"
        public_scenes.parent.mkdir(parents=True)
        shutil.copytree(ROOT / "public" / "images" / "hero-scenes", public_scenes)
        (source / "hugo.yaml").write_text(
            "baseURL: https://example.test/\nstaticDir:\n  - public\n", encoding="utf-8"
        )
        fixtures = [
            {
                "slug": "denver-preview",
                "headline": "Denver is the assignment after 213 yards and a 3-20 night at Lumen",
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
                "slug": "seattle-review",
                "headline": "213 yards, a 50-yard answer, and a short week to Denver",
                "generatedAt": "2026-10-27T19:15:00Z",
                "phase": {"mode": "review", "type": "regular", "week": 7, "label": "Week 7"},
                "record": "5-1",
                "edition": "2026 Week 7 · Review",
                "nextGame": {"opponent": "Denver Broncos", "at": "@ Empower Field at Mile High"},
                "lastGameReview": {
                    "opponent": "Seattle Seahawks",
                    "label": "Week 7 · Sun, Oct 25 · 7:20 PM CT",
                    "lede": "At Lumen Field, Seattle controlled both lines.",
                },
                "injuries": [],
                "pressConferenceLinks": [],
                "pressConferences": [],
            },
            {
                "slug": "unknown-preview",
                "headline": "Fixture preview",
                "generatedAt": "2026-01-02T12:00:00Z",
                "phase": {"mode": "preview", "label": "Preview"},
                "record": "0-0",
                "edition": "Test edition",
                "nextGame": {"opponent": "Test Sharks", "label": "Fixture next-game date"},
                "lastGameReview": {"opponent": "Test Ravens", "label": "Fixture previous-game date"},
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
        return re.findall(r"<figure\b[^>]*class=['\"]?nrt-hero-media\b.*?</figure>", self.page, re.S)

    def test_mapped_editions_do_not_use_the_generic_card(self):
        heroes = self._heroes()
        self.assertEqual(len(heroes), 3)
        den, sea, unknown = (html.unescape(h) for h in heroes)
        self.assertRegex(den, r'data-hero-scene="?preview-away-den"?')
        self.assertRegex(den, r'data-hero-mapped="?true"?')
        self.assertIn("preview-away-den.svg", den)
        self.assertIn("mountain", den.lower())
        self.assertRegex(sea, r'data-hero-scene="?review-away-sea"?')
        self.assertIn("review-away-sea.svg", sea)
        self.assertRegex(sea, r'data-hero-mapped="?true"?')
        self.assertIn("Article illustration", den)
        self.assertNotIn("Temporary article illustration", den)
        for hero in (den, sea):
            for marker in GENERIC_MARKERS:
                self.assertNotIn(marker, hero)
        self.assertRegex(unknown, r'data-hero-scene="?preview-fallback"?')
        self.assertRegex(unknown, r'data-hero-mapped="?false"?')
        self.assertNotIn("KANSAS CITY CHIEFS", unknown)
        self.assertNotIn('fill="#991c35"', unknown)

    def test_template_never_emits_retired_split_panel(self):
        illustration = (ROOT / "layouts" / "partials" / "nrt-event-illustration.html").read_text(encoding="utf-8")
        media = (ROOT / "layouts" / "partials" / "nrt-hero-media.html").read_text(encoding="utf-8")
        self.assertNotIn("KANSAS CITY CHIEFS", illustration)
        self.assertNotIn("#991c35", illustration)
        self.assertNotIn("TEMPORARY", media)
        self.assertNotIn("Temporary article illustration", media)
        self.assertIn("nrt-hero-scene.html", media)


class HeroScenePublishedShape(unittest.TestCase):
    def test_week8_denver_preview_shape_selects_mile_high_scene(self):
        edition = _edition(
            "preview", "Denver Broncos", week=8, phase_type="regular",
            home_away="away", at="@ Empower Field at Mile High",
            headline="Denver is the assignment after 213 yards and a 3-20 night at Lumen",
            label="Week 8 · Sun, Nov 1 · 3:25 PM CT",
        )
        scene = hero_scenes.resolve(edition)
        self.assertEqual(scene["id"], "preview-away-den")
        self.assertEqual(scene["venueKey"], "empower-field")
        self.assertTrue(scene["mapped"])
        self.assertIn("mountain", scene["alt"].lower())
