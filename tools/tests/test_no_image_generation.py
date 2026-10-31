"""Fail if image generation or synthesized hero art re-enters the pipeline."""
from __future__ import annotations

import unittest
from pathlib import Path

from tools.tests.test_hero_posts import HUGO, render_hero_fixtures
from tools.tests.test_x_photo import _edition

ROOT = Path(__file__).resolve().parents[2]
SCAN_ROOTS = (
    ROOT / "tools" / "chiefs_narrative",
    ROOT / ".github" / "workflows",
    ROOT / "layouts",
    ROOT / "scripts",
)
SKIP_NAMES = {"test_no_image_generation.py", "test_x_photo_mutations.py"}
FORBIDDEN = (
    "images/generations",
    "grok-imagine",
    "image_generation",
    "api.x.ai/v1/images",
    "openai.com/v1/images",
    "/2/tweets/search",
    "nrt-event-illustration",
    "nrt-hero-scene",
    "images/hero-scenes",
    "article-illustration",
    "person-media--silhouette",
    "not a photograph",
)
REMOVED_PATHS = (
    ROOT / "tools" / "chiefs_narrative" / "hero_scenes.py",
    ROOT / "layouts" / "partials" / "nrt-hero-scene.html",
    ROOT / "layouts" / "partials" / "nrt-event-illustration.html",
    ROOT / "data" / "hero_scenes.json",
    ROOT / "public" / "images" / "hero-scenes",
    ROOT / "docs" / "hero-evidence",
)


class NoImageGenerationGuard(unittest.TestCase):
    def test_pipeline_has_no_image_generation_or_x_bearer(self):
        hits: list[str] = []
        for root in SCAN_ROOTS:
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if not path.is_file() or path.name in SKIP_NAMES:
                    continue
                if path.suffix.lower() not in {
                    ".py",
                    ".yml",
                    ".yaml",
                    ".html",
                    ".js",
                    ".mjs",
                    ".sh",
                    ".md",
                    ".json",
                }:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
                for needle in FORBIDDEN:
                    if needle in text:
                        rel = path.relative_to(ROOT).as_posix()
                        hits.append(f"{rel}: {needle}")
        self.assertEqual(hits, [], "image generation or synthesis leaked back:\n" + "\n".join(hits))

    def test_x_photo_uses_documented_xai_search_not_generation(self):
        src = (ROOT / "tools" / "chiefs_narrative" / "x_photo.py").read_text(encoding="utf-8")
        self.assertIn('type": "x_search"', src)
        self.assertIn("allowed_x_handles", src)
        self.assertIn("/responses", src)
        self.assertNotIn("included_x_handles", src)
        self.assertNotIn("search_parameters", src)
        self.assertNotIn("images/generations", src)
        self.assertNotIn('os.environ.get("X_BEARER_TOKEN")', src)
        workflow = (ROOT / ".github" / "workflows" / "narrative.yml").read_text(encoding="utf-8")
        self.assertIn("--pull-photo", workflow)
        self.assertIn("secrets.GROK_API_KEY", workflow)
        self.assertNotIn("X_BEARER_TOKEN", workflow)
        self.assertNotIn("secrets.X_BEARER_TOKEN", workflow)

    def test_synthesized_hero_paths_are_gone(self):
        for path in REMOVED_PATHS:
            self.assertFalse(path.exists(), path)
        layouts = (ROOT / "layouts").rglob("*.html")
        for path in layouts:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("nrt-hero-scene.html", text, path.name)
            self.assertNotIn("nrt-event-illustration.html", text, path.name)
            self.assertNotIn("article-illustration", text, path.name)
            self.assertNotIn("images/hero-scenes", text, path.name)
            self.assertNotIn("person-media--silhouette", text, path.name)
            self.assertNotIn("not a photograph", text.lower(), path.name)

    def test_x_photo_public_dir_has_only_real_photographs(self):
        folder = ROOT / "public" / "images" / "x-photo"
        if not folder.exists():
            return
        allowed = {".webp", ".png", ".jpg", ".jpeg"}
        extras = [
            path.name
            for path in folder.iterdir()
            if path.is_file() and path.suffix.lower() not in allowed
        ]
        self.assertEqual(extras, [], extras)

    def test_no_photo_editions_render_without_synthesized_art(self):
        if not Path(HUGO).is_file():
            raise AssertionError(f"Hugo is required at {HUGO}")
        current = _edition(slug="2026-10-31-1816")
        current["generatedAt"] = "2026-10-31T18:16:00Z"
        current["record"] = "5-2"
        current["xPhoto"] = {}
        historic = _edition(slug="2026-10-30-2130")
        historic.pop("xPhoto", None)
        historic["generatedAt"] = "2026-10-30T21:30:00Z"
        historic["record"] = "5-2"
        historic["heroMediaId"] = "article-illustration"
        temp, page, css = render_hero_fixtures([current, historic], pins={})
        try:
            self.assertNotIn("<svg", page)
            self.assertNotIn("hero-scenes", page)
            self.assertNotIn("nrt-event-illustration", page)
            self.assertNotIn("article-illustration", page)
            self.assertNotIn("nrt-hero-media", page)
            self.assertNotIn("nrt-event-illustration", css)
            self.assertNotIn("article-illustration", css)
            self.assertNotIn("widgets.js", page)
        finally:
            temp.cleanup()
