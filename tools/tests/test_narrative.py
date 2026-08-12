"""CI gates for the Chiefs Narrative engine.

Everything here runs offline — no network, no API keys — so it is a stable
merge gate. Run with:  python -m unittest discover -s tools/tests -v
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.chiefs_narrative import diagrams, generate, offline, prompts, providers, schema

# Canned inputs: the writers only use .get() lookups, so minimal dicts work.
CAMP_PHASE = {"type": "training-camp", "label": "Training Camp",
              "mode": "camp", "edition": "Test Camp Edition"}
WEEK_PHASE = {"type": "regular", "label": "Week 5", "mode": "preview",
              "edition": "Test Week Edition"}
SIGNALS = {"news": [], "markets": {}, "schedule": []}
NEXT = [{"opponent": "Denver Broncos", "homeAway": "home", "week": 1}]


class SixCardGuarantee(unittest.TestCase):
    """The GitHub Action refresh must always yield exactly six X&O cards."""

    def assert_six(self, cards):
        self.assertEqual(len(cards), 6)
        concepts = [c["concept"] for c in cards]
        self.assertEqual(len(set(concepts)), 6, f"repeated concepts: {concepts}")
        sides = [diagrams.CONCEPTS[c]["side"] for c in concepts]
        self.assertEqual(sides.count("offense"), 4)
        self.assertEqual(sides.count("defense"), 2)
        for c in cards:
            for field in ("title", "situation", "why", "coaching"):
                self.assertTrue(c.get(field), f"card missing {field}: {c}")

    def test_offline_camp_writer_emits_six(self):
        self.assert_six(offline.write(SIGNALS, CAMP_PHASE, NEXT)["xsandos"])

    def test_offline_week_writer_emits_six(self):
        self.assert_six(offline.write(SIGNALS, WEEK_PHASE, NEXT)["xsandos"])

    def test_schema_caps_at_six_and_clamps_concepts(self):
        raw = [{"title": f"t{i}", "situation": "s", "concept": "not-a-concept",
                "why": "w", "coaching": "c", "labels": {}} for i in range(9)]
        out = schema._norm_xsandos(raw)
        self.assertEqual(len(out), 6)
        for card in out:
            self.assertIn(card["concept"], diagrams.CONCEPTS)

    def test_top_up_restores_six_when_writer_under_delivers(self):
        narrative = schema.normalize(
            offline.write(SIGNALS, CAMP_PHASE, NEXT), phase=CAMP_PHASE,
            meta={"generatedAt": "2026-01-01T00:00:00+00:00",
                  "generator": "test", "record": "0-0", "markets": {}},
        )
        narrative["xsandos"] = narrative["xsandos"][:2]  # simulate a short LLM reply
        generate._ensure_six_xsandos(narrative, SIGNALS, CAMP_PHASE, NEXT)
        self.assert_six(narrative["xsandos"])

    def test_prompt_demands_exactly_six_grounded_cards(self):
        text = prompts.build_user_prompt(SIGNALS, CAMP_PHASE, NEXT)
        self.assertIn("EXACTLY 6 xsandos", text)
        for hint in ("injuries", "personnel", "coaching", "matchup"):
            self.assertIn(hint, text)


class Diagrams(unittest.TestCase):
    def test_every_concept_renders_valid_svg(self):
        with tempfile.TemporaryDirectory() as tmp:
            for key in diagrams.concept_keys():
                info = diagrams.write_diagram(tmp, f"xo-{key}", key)
                svg = (Path(tmp) / f"xo-{key}.svg").read_text(encoding="utf-8")
                self.assertTrue(svg.startswith("<svg"))
                self.assertTrue(svg.rstrip().endswith("</svg>"))
                self.assertEqual(info["side"], diagrams.CONCEPTS[key]["side"])


class EditionSlugs(unittest.TestCase):
    def test_slug_is_derived_from_timestamp(self):
        slug = generate._edition_slug({"generatedAt": "2026-07-25T19:30:00+00:00"})
        self.assertEqual(slug, "2026-07-25-1930")


class GrokModelSelection(unittest.TestCase):
    """GROK_MODEL (or XAI_MODEL) must resolve to grok-4.6 unless overridden."""

    def test_default_is_grok_4_6(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(providers.GROK_DEFAULT_MODEL, "grok-4.6")
            self.assertEqual(providers.grok_model(), "grok-4.6")

    def test_grok_model_env_wins(self):
        with patch.dict("os.environ", {"GROK_MODEL": "grok-4.6", "XAI_MODEL": "ignored"}, clear=True):
            self.assertEqual(providers.grok_model(), "grok-4.6")

    def test_xai_model_used_when_grok_model_unset(self):
        with patch.dict("os.environ", {"XAI_MODEL": "grok-4.6"}, clear=True):
            self.assertEqual(providers.grok_model(), "grok-4.6")

    def test_blank_grok_model_falls_back_to_default(self):
        with patch.dict("os.environ", {"GROK_MODEL": "  ", "XAI_MODEL": ""}, clear=True):
            self.assertEqual(providers.grok_model(), "grok-4.6")

    def test_workflow_passes_repository_variable(self):
        yaml = (Path(__file__).resolve().parents[2] / ".github" / "workflows" / "narrative.yml").read_text(encoding="utf-8")
        self.assertIn("GROK_MODEL: ${{ vars.GROK_MODEL }}", yaml)


if __name__ == "__main__":
    unittest.main()
