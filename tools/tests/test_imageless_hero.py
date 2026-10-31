"""Stephen's rule: no X photo means no synthesized hero on the live edition."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.chiefs_narrative import config, edition_overlay, hero_media, schema

ROOT = Path(__file__).resolve().parents[2]
EDITION_SLUG = "2026-10-31-1537"
FORBIDDEN = (
    "hero-scene",
    "hero-scenes",
    "nrt-event-illustration",
    "Not a photograph",
)


def _hugo_bin() -> str | None:
    found = shutil.which("hugo")
    if found:
        return found
    fallback = Path.home() / ".local" / "hugo" / "hugo"
    if fallback.is_file():
        return str(fallback)
    return None


class ImagelessLiveEdition(unittest.TestCase):
    @unittest.skipUnless(
        _hugo_bin() or os.environ.get("GITHUB_ACTIONS"),
        "Hugo unavailable",
    )
    def test_live_pages_render_without_synthesized_hero(self):
        """Real Hugo build: home, desk, and this edition have no drawn hero."""
        binary = _hugo_bin()
        self.assertTrue(
            binary,
            "hugo must be installed in CI gates; render test cannot skip",
        )
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "dist"
            result = subprocess.run(
                [binary, "--gc", "--minify", "--destination", str(dest)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            pages = {
                "/": dest / "index.html",
                "/narrative/": dest / "narrative" / "index.html",
                f"/narrative/{EDITION_SLUG}/": dest
                / "narrative"
                / EDITION_SLUG
                / "index.html",
            }
            for route, path in pages.items():
                with self.subTest(route=route):
                    self.assertTrue(path.is_file(), route)
                    html = path.read_text(encoding="utf-8")
                    for token in FORBIDDEN:
                        self.assertEqual(
                            html.count(token),
                            0,
                            f"{route} contains {token!r}",
                        )
        print("LIVE_EDITION_HUGO_RENDER_RAN", flush=True)

    def test_live_narrative_equals_current_edition_file(self):
        live = json.loads(config.NARRATIVE_JSON.read_text(encoding="utf-8"))
        slug = live["slug"]
        edition_path = config.EDITIONS_DIR / f"{slug}.json"
        self.assertTrue(edition_path.is_file(), edition_path)
        edition = json.loads(edition_path.read_text(encoding="utf-8"))
        self.assertEqual(live.get("heroMediaId"), edition.get("heroMediaId"))
        self.assertEqual(live.get("slug"), edition.get("slug"))
        self.assertEqual(live.get("generatedAt"), edition.get("generatedAt"))
        self.assertEqual(live.get("headline"), edition.get("headline"))
        self.assertEqual(live, edition)

    def test_generator_default_and_attach_fallback_write_none(self):
        self.assertEqual(hero_media.FALLBACK_ID, "none")
        normalized = schema.normalize(
            {"heroMediaId": "invented"}, phase={}, meta={}
        )
        self.assertEqual(normalized["heroMediaId"], "none")
        attached = {"slug": "edition", "heroMediaId": "forged"}
        with tempfile.TemporaryDirectory() as tmp:
            hero_media.attach(attached, root=tmp)
        self.assertEqual(attached["heroMediaId"], "none")
        media_src = (ROOT / "tools" / "chiefs_narrative" / "hero_media.py").read_text(
            encoding="utf-8"
        )
        schema_src = (ROOT / "tools" / "chiefs_narrative" / "schema.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('FALLBACK_ID = "none"', media_src)
        self.assertIn('"heroMediaId": "none"', schema_src)
        self.assertNotIn(
            'FALLBACK_ID = "article-illustration"', media_src
        )

    def test_bot_edition_paths_remain_overlay_allowlisted(self):
        live_rel = config.NARRATIVE_JSON.relative_to(config.REPO_ROOT).as_posix()
        edition_rel = (
            config.EDITIONS_DIR.relative_to(config.REPO_ROOT) / f"{EDITION_SLUG}.json"
        ).as_posix()
        self.assertIn(live_rel, edition_overlay.ALLOWED_FILES)
        self.assertTrue(edition_overlay.is_allowed_rel(Path(live_rel)))
        self.assertTrue(edition_overlay.is_allowed_rel(Path(edition_rel)))
        self.assertFalse(
            edition_overlay.is_allowed_rel(
                Path("layouts/partials/narrative-edition.html")
            )
        )
        self.assertFalse(
            edition_overlay.is_allowed_rel(
                Path("tools/chiefs_narrative/hero_media.py")
            )
        )
