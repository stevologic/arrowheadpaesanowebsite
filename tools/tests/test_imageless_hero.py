"""Stephen's rule: built HTML never ships synthesized person or hero art."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.chiefs_narrative import config, edition_overlay, hero_media, schema

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_CLASS_OR_ELEMENT = (
    "person-media--silhouette",
    "nrt-event-illustration",
    "hero-scene",
)
NOT_A_PHOTOGRAPH = re.compile(r"not a photograph", re.I)


def _hugo_bin() -> str:
    found = shutil.which("hugo")
    if found:
        return found
    fallback = Path.home() / ".local" / "hugo" / "hugo"
    if fallback.is_file():
        return str(fallback)
    raise AssertionError("hugo must be installed; render test cannot skip")


def _class_or_element_hits(html: str, token: str) -> list[str]:
    """Match token as an HTML class or element, not a data-hero-* attribute."""
    class_re = re.compile(
        rf"""class\s*=\s*(['"])([^'"]*\b{re.escape(token)}\b[^'"]*)\1""",
        re.I,
    )
    el_re = re.compile(rf"<{re.escape(token)}\b", re.I)
    return [m.group(0) for m in class_re.finditer(html)] + [
        m.group(0) for m in el_re.finditer(html)
    ]


class ImagelessLiveEdition(unittest.TestCase):
    def test_every_built_page_omits_synthesized_media(self):
        """Real Hugo build: every HTML page fails closed on drawn stand-ins."""
        binary = _hugo_bin()
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
            pages = sorted(path for path in dest.rglob("*.html") if path.is_file())
            self.assertTrue(pages, "Hugo produced no HTML pages")
            for path in pages:
                rel = path.relative_to(dest).as_posix()
                html = path.read_text(encoding="utf-8")
                with self.subTest(page=rel):
                    for token in FORBIDDEN_CLASS_OR_ELEMENT:
                        hits = _class_or_element_hits(html, token)
                        self.assertEqual(
                            hits,
                            [],
                            f"{rel} contains {token!r} as a class or element: {hits}",
                        )
                    photo_hits = NOT_A_PHOTOGRAPH.findall(html)
                    self.assertEqual(
                        photo_hits,
                        [],
                        f"{rel} contains 'not a photograph': {photo_hits}",
                    )
                    # #299 data-hero-* attributes are legitimate and must not
                    # be treated as a hero-scene token.
                    if "data-hero-" in html:
                        self.assertEqual(_class_or_element_hits(html, "hero-scene"), [])
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
        live = json.loads(config.NARRATIVE_JSON.read_text(encoding="utf-8"))
        slug = live["slug"]
        live_rel = config.NARRATIVE_JSON.relative_to(config.REPO_ROOT).as_posix()
        edition_rel = (
            config.EDITIONS_DIR.relative_to(config.REPO_ROOT) / f"{slug}.json"
        ).as_posix()
        self.assertIn(live_rel, edition_overlay.ALLOWED_FILES)
        self.assertTrue(edition_overlay.is_allowed_rel(Path(live_rel)))
        self.assertTrue(edition_overlay.is_allowed_rel(Path(edition_rel)))
        self.assertTrue(
            edition_overlay.is_allowed_rel(Path("data/x-photo/people.json"))
        )
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
