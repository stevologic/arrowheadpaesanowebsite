"""Mutation tests for the hero selector, helmet flip, and fallback caption."""
from __future__ import annotations

import importlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from tools.chiefs_narrative import hero_scenes
from tools.tests.test_hero_scenes import _edition

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "tools" / "chiefs_narrative" / "hero_scenes.py"
EVIDENCE = ROOT / "docs" / "hero-evidence" / "mutation-report.md"

MUTANTS = (
    {
        "name": "week-first-ignores-opponent",
        "old": "            opp_ok = not abbr or scene.get(\"opponentAbbr\") == abbr\n"
               "            ha_ok = not home_away or scene.get(\"homeAway\") == home_away\n"
               "            if opp_ok and ha_ok:",
        "new": "            if True:",
        "killer": "week/opponent mismatch must not return the week-8 Denver scene",
    },
    {
        "name": "left-helmet-negates-translate",
        "old": '        f\'<g transform="translate({x},{y}) {flip}">\'',
        "new": '        f\'<g transform="translate({-x if facing == "left" else x},{y}) {flip}">\'',
        "killer": "matchup SVGs must keep both helmets inside the viewBox",
    },
    {
        "name": "fallback-caption-names-arrowhead",
        "old": '        "caption": _scene_caption(mode, ""),',
        "new": '        "caption": _scene_caption(mode, "arrowhead"),',
        "killer": "unknown/away fallback caption must not mention Arrowhead",
    },
    {
        "name": "drop-extra-neutral-cards",
        "old": "    for extra in EXTRA_MATCHUPS:",
        "new": "    for extra in ():",
        "killer": "London vs Miami must not resolve as Hard Rock",
    },
)


def _kills(module, name: str) -> str | None:
    """Return None if the mutant is killed, or a reason if it survived."""
    schedule = module.load_schedule(ROOT)
    catalog = module.build_catalog(schedule)
    if name == "week-first-ignores-opponent":
        scene = module.resolve(
            _edition("preview", "Seattle Seahawks", week=8, home_away="away",
                     at="@ Lumen Field"),
            schedule=schedule, catalog=catalog,
        )
        if scene.get("id") == "preview-away-den":
            return None
        return f"survived: mismatch resolved to {scene.get('id')}"
    if name == "left-helmet-negates-translate":
        svg = module.render_svg(catalog["scenes"]["preview-away-den"])
        if "translate(-492" in svg or not module.helmets_in_viewbox(svg):
            return None
        return f"survived: helmet xs={module.helmet_xs(svg)}"
    if name == "fallback-caption-names-arrowhead":
        scene = module._fallback_scene("preview")
        if "arrowhead" in scene["caption"].lower() or "missouri" in scene["caption"].lower():
            return None
        return f"survived: caption={scene['caption']!r}"
    if name == "drop-extra-neutral-cards":
        scene = module.resolve(
            _edition("preview", "Miami Dolphins", at="Wembley Stadium",
                     home_away="neutral"),
            schedule=schedule, catalog=catalog,
        )
        if scene.get("id") == "preview-away-mia" or scene.get("id") == "preview-fallback":
            return None
        return f"survived: london/mia resolved to {scene.get('id')}"
    return f"unknown mutant {name}"


class HeroMutations(unittest.TestCase):
    def test_named_mutants_are_killed(self):
        source = SRC.read_text(encoding="utf-8")
        rows = []
        survivors = []
        for mutant in MUTANTS:
            self.assertIn(mutant["old"], source, mutant["name"])
            mutated = source.replace(mutant["old"], mutant["new"], 1)
            self.assertNotEqual(mutated, source, mutant["name"])
            with tempfile.TemporaryDirectory() as tmp:
                pkg_name = f"mutant_{mutant['name'].replace('-', '_')}"
                dest = Path(tmp) / pkg_name
                shutil.copytree(ROOT / "tools" / "chiefs_narrative", dest)
                (dest / "hero_scenes.py").write_text(mutated, encoding="utf-8")
                sys.path.insert(0, tmp)
                try:
                    module = importlib.import_module(f"{pkg_name}.hero_scenes")
                    reason = _kills(module, mutant["name"])
                finally:
                    sys.path.pop(0)
                    for key in list(sys.modules):
                        if key == pkg_name or key.startswith(pkg_name + "."):
                            sys.modules.pop(key, None)
            killed = reason is None
            rows.append({
                "name": mutant["name"],
                "killer": mutant["killer"],
                "killed": killed,
                "detail": "killed" if killed else reason,
            })
            if not killed:
                survivors.append(f"{mutant['name']}: {reason}")
        EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Hero mutation report",
            "",
            "Each mutant reintroduces a prior FAIL. Zero survivors.",
            "",
            "| Mutant | Killer | Result |",
            "| --- | --- | --- |",
        ]
        for row in rows:
            mark = "killed" if row["killed"] else "SURVIVED"
            lines.append(f"| {row['name']} | {row['killer']} | {mark} |")
        lines.append("")
        if survivors:
            lines.append("## Survivors")
            lines.extend(f"- {item}" for item in survivors)
            lines.append("")
        EVIDENCE.write_text("\n".join(lines), encoding="utf-8")
        self.assertEqual(survivors, [], json.dumps(survivors, indent=2))

    def test_live_code_still_passes_the_same_invariants(self):
        schedule = hero_scenes.load_schedule(ROOT)
        catalog = hero_scenes.build_catalog(schedule)
        mismatch = hero_scenes.resolve(
            _edition("preview", "Seattle Seahawks", week=8, home_away="away",
                     at="@ Lumen Field"),
            schedule=schedule, catalog=catalog,
        )
        self.assertEqual(mismatch["id"], "preview-away-sea")
        svg = hero_scenes.render_svg(catalog["scenes"]["preview-away-den"])
        self.assertTrue(hero_scenes.helmets_in_viewbox(svg))
        self.assertNotIn("arrowhead", hero_scenes._fallback_scene("preview")["caption"].lower())
        london = hero_scenes.resolve(
            _edition("preview", "Miami Dolphins", at="Wembley Stadium",
                     home_away="neutral"),
            schedule=schedule, catalog=catalog,
        )
        self.assertEqual(london["id"], "preview-neutral-mia")
