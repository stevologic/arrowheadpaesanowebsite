"""Fail if image generation or paid X API auth re-enters the edition pipeline."""
from __future__ import annotations

import unittest
from pathlib import Path

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
        self.assertEqual(hits, [], "image generation or X bearer leaked back:\n" + "\n".join(hits))

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
