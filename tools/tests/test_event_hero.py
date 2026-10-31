"""Render fixed editions: no synthesized hero, no unlicensed photos."""
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HUGO = shutil.which("hugo") or str(Path.home() / ".local" / "hugo" / "hugo")


@unittest.skipUnless(Path(HUGO).is_file(), "Hugo unavailable")
class EventHeroRendering(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        source = Path(cls.temp.name) / "source"
        source.mkdir()
        (source / "data").mkdir()
        shutil.copytree(ROOT / "layouts" / "partials", source / "layouts" / "partials")
        (source / "hugo.yaml").write_text(
            "baseURL: https://example.test/\nstaticDir:\n  - public\n", encoding="utf-8"
        )
        (source / "layouts" / "index.html").write_text(
            '{{ range .Site.Data.fixtures }}{{ partial "narrative-edition.html" '
            '(dict "n" . "site" $.Site "isCurrent" true) }}{{ end }}',
            encoding="utf-8",
        )
        fixtures = []
        for mode, opponent in [
            ("preview", "Test Sharks"),
            ("review", "Test Ravens"),
            ("camp", "Test Bears"),
        ]:
            fixtures.append(
                {
                    "slug": "fixture-" + mode,
                    "headline": "Fixture " + mode,
                    "generatedAt": "2026-01-02T12:00:00Z",
                    "phase": {"mode": mode, "label": mode.title()},
                    "record": "0-0",
                    "edition": "Test edition",
                    "nextGame": {"opponent": opponent, "label": "Fixture next-game date"},
                    "lastGameReview": {
                        "opponent": opponent,
                        "label": "Fixture previous-game date",
                    },
                    "injuries": [
                        {
                            "player": "Fixture Player",
                            "image": "https://static.clubs.nfl.com/unlicensed.webp",
                            "imageSource": "https://example.test/profile",
                        }
                    ],
                    "pressConferenceLinks": [
                        {
                            "speaker": "Fixture Speaker",
                            "url": "https://example.test/video",
                            "title": "Fixture interview",
                            "dateLabel": "Fixture date",
                            "image": "https://static.clubs.nfl.com/unlicensed.webp",
                        }
                    ],
                    "pressConferences": [
                        {
                            "videoId": "xCnSucR_NCw",
                            "title": "Fixture verified player",
                            "speaker": "Fixture Speaker",
                            "dateLabel": "Fixture date",
                            "channel": "Fixture Channel",
                            "url": "https://www.youtube.com/watch?v=xCnSucR_NCw",
                        }
                    ],
                }
            )
        (source / "data" / "fixtures.json").write_text(json.dumps(fixtures), encoding="utf-8")
        (source / "data" / "hero_media.json").write_text(
            '{"fallback":{},"items":{}}', encoding="utf-8"
        )
        (source / "data" / "hero_posts.json").write_text(
            '{"schemaVersion":1,"allowlist":[],"pins":{},"catalog":[]}',
            encoding="utf-8",
        )
        (source / "data" / "people_media.json").write_text('{"people":{}}', encoding="utf-8")
        (source / "public" / "images").mkdir(parents=True)
        cls.dist = Path(cls.temp.name) / "site"
        result = subprocess.run(
            [HUGO, "--minify", "--destination", str(cls.dist)],
            cwd=source,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            cls.temp.cleanup()
            raise AssertionError(result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_each_edition_has_no_synthesized_hero(self):
        page = (self.dist / "index.html").read_text(encoding="utf-8")
        heroes = re.findall(
            r'<figure\b[^>]*class=["\']?nrt-hero-media\b.*?</figure>', page, re.S
        )
        self.assertEqual(heroes, [])
        self.assertNotIn("article-illustration", page)
        self.assertNotIn("nrt-event-illustration", page)
        self.assertNotIn("hero-scenes", page)
        self.assertNotIn("Event photograph not yet cleared.", page)
        for mode in ("preview", "review", "camp"):
            self.assertIn("Fixture " + mode, page)

    def test_raw_official_images_are_not_republished_without_ledger(self):
        page = (self.dist / "index.html").read_text(encoding="utf-8")
        self.assertNotRegex(page, r"<img[^>]+static\.clubs\.nfl\.com")
        self.assertNotIn("illustrative silhouette", page)
        self.assertNotIn("person-media--silhouette", page)
        self.assertIn("Player profile", page)
        self.assertIn("Watch at Chiefs.com", page)
        self.assertIn("youtube.com/watch?v=xCnSucR_NCw", page)
