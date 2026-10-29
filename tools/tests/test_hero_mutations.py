"""Mutation tests for hero selector, pins, Hugo fixtures, and fallback caption."""
from __future__ import annotations

import importlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from tools.chiefs_narrative import hero_posts, hero_scenes
from tools.tests.test_hero_posts import (
    CLEAN_ID,
    CLEAN_URL,
    SIGNED_SLUG,
    WEEK8_SLUG,
    _catalog_row,
    _clean_pin,
    _copy_signed_tree,
    _preview,
    _temp_sidecar,
    _week8_fixture,
    render_hero_fixtures,
)
from tools.tests.test_hero_scenes import _edition

ROOT = Path(__file__).resolve().parents[2]
SCENES_SRC = ROOT / "tools" / "chiefs_narrative" / "hero_scenes.py"
POSTS_SRC = ROOT / "tools" / "chiefs_narrative" / "hero_posts.py"
OVERLAY_SRC = ROOT / "tools" / "chiefs_narrative" / "edition_overlay.py"
WORKFLOW_SRC = ROOT / ".github" / "workflows" / "narrative.yml"
SCENE_TMPL = ROOT / "layouts" / "partials" / "nrt-hero-scene.html"
POST_TMPL = ROOT / "layouts" / "partials" / "nrt-hero-post.html"
MEDIA_TMPL = ROOT / "layouts" / "partials" / "nrt-hero-media.html"
CSS_SRC = ROOT / "public" / "css" / "editorial-layout.css"
EVIDENCE = ROOT / "docs" / "hero-evidence" / "mutation-report.md"


def _mutate_file(path: Path, old: str, new: str) -> str:
    source = path.read_text(encoding="utf-8")
    if old not in source:
        raise AssertionError(f"needle missing in {path.name}")
    mutated = source.replace(old, new, 1)
    if mutated == source:
        raise AssertionError(f"mutation did not change {path.name}")
    return mutated


def _load_mutated_module(tmp: Path, name: str, filename: str, source: str):
    dest = tmp / name
    shutil.copytree(ROOT / "tools" / "chiefs_narrative", dest)
    (dest / filename).write_text(source, encoding="utf-8")
    sys.path.insert(0, str(tmp))
    try:
        return importlib.import_module(f"{name}.{filename[:-3]}")
    finally:
        sys.path.pop(0)


def _clear_module(name: str):
    for key in list(sys.modules):
        if key == name or key.startswith(name + "."):
            sys.modules.pop(key, None)


def _kill_week_opponent(module) -> str | None:
    schedule = module.load_schedule(ROOT)
    catalog = module.build_catalog(schedule)
    scene = module.resolve(
        _edition("preview", "Seattle Seahawks", week=8, home_away="away",
                 at="@ Lumen Field"),
        schedule=schedule, catalog=catalog,
    )
    if scene.get("id") == "preview-away-den":
        return None
    return f"survived: mismatch resolved to {scene.get('id')}"


def _kill_helmet(module) -> str | None:
    svg = module.render_svg(module.build_catalog(module.load_schedule(ROOT))["scenes"]["preview-away-den"])
    if "translate(-492" in svg or not module.helmets_in_viewbox(svg):
        return None
    return f"survived: helmet xs={module.helmet_xs(svg)}"


def _kill_fallback_caption(module) -> str | None:
    scene = module._fallback_scene("preview")
    if "arrowhead" in scene["caption"].lower() or "missouri" in scene["caption"].lower():
        return None
    return f"survived: caption={scene['caption']!r}"


def _kill_london(module) -> str | None:
    scene = module.resolve(
        _edition("preview", "Miami Dolphins", at="Wembley Stadium",
                 home_away="neutral"),
        schedule=module.load_schedule(ROOT), catalog=module.build_catalog(module.load_schedule(ROOT)),
    )
    if scene.get("id") in {"preview-away-mia", "preview-fallback"}:
        return None
    return f"survived: london/mia resolved to {scene.get('id')}"


def _kill_neutral_alt(module) -> str | None:
    scene = module.resolve(
        _edition("preview", "San Francisco 49ers", phase_type="postseason",
                 at="Levi's Stadium"),
        schedule=module.load_schedule(ROOT), catalog=module.build_catalog(module.load_schedule(ROOT)),
    )
    if "road venue" in scene.get("alt", "").lower():
        return None
    return f"survived: alt={scene.get('alt')!r}"


def _kill_mexico(module) -> str | None:
    edition = _edition("preview", "Los Angeles Chargers", at="Estadio Azteca, Mexico City")
    venue = module.venue_key(module._venue_blob(edition, edition["nextGame"]))
    ha = module._home_away(edition, edition["nextGame"], "preview", venue)
    if ha == "away" or (venue == "mexico-city" and ha != "neutral"):
        return None
    scene = module.resolve(edition, schedule=module.load_schedule(ROOT),
                           catalog=module.build_catalog(module.load_schedule(ROOT)))
    if "road venue" in scene.get("alt", "").lower():
        return None
    return f"survived: venue={venue} ha={ha} id={scene.get('id')}"


def _kill_levis(module) -> str | None:
    edition = _edition("preview", "San Francisco 49ers", phase_type="postseason",
                       at="Levi's Stadium")
    venue = module.venue_key(edition["nextGame"]["at"])
    ha = module._home_away(edition, edition["nextGame"], "preview", venue)
    if venue != "super-bowl" or ha != "neutral":
        return None
    return f"survived: venue={venue} ha={ha}"


def _kill_post_den(module) -> str | None:
    scene = module.resolve(
        _edition("preview", "Denver Broncos", phase_type="postseason"),
        schedule=module.load_schedule(ROOT), catalog=module.build_catalog(module.load_schedule(ROOT)),
    )
    if scene.get("id") == "preview-away-den":
        return None
    return f"survived: post DEN resolved to {scene.get('id')}"


def _kill_sponsored(module) -> str | None:
    row = _clean_pin(sponsored=True)
    if module.normalize_pin(row) is not None:
        return None
    return "survived: sponsored pin was accepted"


def _kill_betting(module) -> str | None:
    row = _clean_pin(text="DraftKings Over/Under 40.5")
    if module.normalize_pin(row) is not None:
        return None
    return "survived: betting copy was accepted"


def _kill_week_opponent_pin(module) -> str | None:
    tmp, root = _temp_sidecar({
        "schemaVersion": 1,
        "allowlist": list(module.ALLOWLIST_DISPLAY),
        "catalog": [_catalog_row()],
        "pins": {},
    })
    try:
        pin = module.select_catalog_pin(
            _preview(opponent="Seattle Seahawks", week=8, at="@ Lumen Field",
                     slug="2026-10-29-1400"),
            root=root,
        )
        if pin and pin.get("id") == CLEAN_ID:
            return None
        return f"survived: mismatch pin={pin}"
    finally:
        tmp.cleanup()


def _kill_signed_pin(module) -> str | None:
    tmp, root = _temp_sidecar()
    try:
        _copy_signed_tree(root)
        payload = {
            "schemaVersion": 1,
            "allowlist": list(module.ALLOWLIST_DISPLAY),
            "catalog": [],
            "pins": {
                SIGNED_SLUG: _clean_pin(handle="NFL", url=f"https://x.com/NFL/status/{CLEAN_ID}",
                                       text="Fabricated league pin", alt="Fabricated league pin"),
            },
        }
        (root / "data" / "hero_posts.json").write_text(json.dumps(payload), encoding="utf-8")
        issues = module.validate_sidecar(root)
        if not any(SIGNED_SLUG in item for item in issues):
            return None
        return f"survived: issues={issues}"
    finally:
        tmp.cleanup()


def _kill_whitelist(module) -> str | None:
    if WEEK8_SLUG not in getattr(module, "SIGNED_PIN_WHITELIST", ()):
        return None
    return f"survived: whitelist={getattr(module, 'SIGNED_PIN_WHITELIST', None)}"


def _kill_hugo_week_opponent(layouts: Path) -> str | None:
    temp, page, _ = render_hero_fixtures(
        [_week8_fixture(slug="week-mismatch", opponent="Seattle Seahawks",
                        at="@ Lumen Field")],
        layouts=layouts,
    )
    try:
        if "preview-away-den.svg" in page and "preview-away-sea.svg" not in page:
            return None
        return "survived: Hugo still required week/opponent agreement"
    finally:
        temp.cleanup()


def _kill_hugo_allowlist(layouts: Path) -> str | None:
    temp, page, _ = render_hero_fixtures(
        [_week8_fixture(slug="offlist-pin")],
        pins={"offlist-pin": {
            "id": CLEAN_ID,
            "handle": "ArrowheadPride",
            "url": f"https://x.com/ArrowheadPride/status/{CLEAN_ID}",
            "text": "fan recap",
            "alt": "fan recap",
        }},
        layouts=layouts,
    )
    try:
        if 'data-hero-kind="x-embed"' in page or "twitter-tweet" in page:
            return None
        return "survived: Hugo still rejected the off-list pin"
    finally:
        temp.cleanup()


def _kill_hugo_neutral(layouts: Path) -> str | None:
    fixture = {
        "slug": "mexico-city",
        "headline": "Mexico City assignment",
        "generatedAt": "2026-11-15T12:00:00Z",
        "phase": {"mode": "preview", "type": "regular", "label": "Preview"},
        "record": "0-0",
        "edition": "Mexico City",
        "nextGame": {"opponent": "Los Angeles Chargers", "at": "Estadio Azteca, Mexico City",
                     "label": "International"},
        "injuries": [],
        "pressConferenceLinks": [],
        "pressConferences": [],
    }
    temp, page, _ = render_hero_fixtures([fixture], layouts=layouts)
    try:
        if "preview-home-lac.svg" in page or "preview-away-lac.svg" in page:
            return None
        return "survived: Hugo still treated Mexico City as unmapped/neutral"
    finally:
        temp.cleanup()


def _kill_hugo_svg(layouts: Path) -> str | None:
    temp, page, _ = render_hero_fixtures(
        [_week8_fixture(slug="clean-pin")],
        pins={"clean-pin": _clean_pin()},
        layouts=layouts,
    )
    try:
        if "nrt-hero-media__fallback" not in page or "preview-away-den.svg" not in page:
            return None
        return "survived: Hugo still kept the SVG fallback in the embed path"
    finally:
        temp.cleanup()


def _kill_hugo_sponsored(layouts: Path) -> str | None:
    temp, page, _ = render_hero_fixtures(
        [_week8_fixture(slug="sponsored-pin")],
        pins={"sponsored-pin": _clean_pin(sponsored=True)},
        layouts=layouts,
    )
    try:
        if "twitter-tweet" in page:
            return None
        return "survived: Hugo still rejected the sponsored pin"
    finally:
        temp.cleanup()


def _kill_css(css_text: str) -> str | None:
    if "16 / 10" in css_text or "16/10" in css_text:
        return None
    if "640px" not in css_text or "--nrt-embed-reserve" not in css_text:
        return None
    return "survived: reserved height and no 16:10 crop still present"


def _kill_workflow(text: str) -> str | None:
    if text.count("data/hero_posts.json") < 2:
        return None
    return "survived: daily workflow still stages the sidecar"


def _kill_overlay(text: str) -> str | None:
    if "data/hero_posts.json" not in text:
        return None
    return "survived: overlay allow-list still includes the sidecar"


MUTANTS = (
    {
        "name": "week-first-ignores-opponent",
        "path": SCENES_SRC,
        "old": "            opp_ok = not abbr or scene.get(\"opponentAbbr\") == abbr\n"
               "            ha_ok = not home_away or scene.get(\"homeAway\") == home_away\n"
               "            if opp_ok and ha_ok:",
        "new": "            if True:",
        "killer": "week/opponent mismatch must not return the week-8 Denver scene",
        "kind": "python-scenes",
        "check": "week_opponent",
    },
    {
        "name": "left-helmet-negates-translate",
        "path": SCENES_SRC,
        "old": '        f\'<g transform="translate({x},{y}) {flip}">\'',
        "new": '        f\'<g transform="translate({-x if facing == "left" else x},{y}) {flip}">\'',
        "killer": "matchup SVGs must keep both helmets inside the viewBox",
        "kind": "python-scenes",
        "check": "helmet",
    },
    {
        "name": "fallback-caption-names-arrowhead",
        "path": SCENES_SRC,
        "old": '        "caption": _scene_caption(mode, ""),',
        "new": '        "caption": _scene_caption(mode, "arrowhead"),',
        "killer": "unknown/away fallback caption must not mention Arrowhead",
        "kind": "python-scenes",
        "check": "fallback_caption",
    },
    {
        "name": "drop-extra-neutral-cards",
        "path": SCENES_SRC,
        "old": "    for extra in EXTRA_MATCHUPS:",
        "new": "    for extra in ():",
        "killer": "London vs Miami must not resolve as Hard Rock",
        "kind": "python-scenes",
        "check": "london",
    },
    {
        "name": "neutral-alt-says-road",
        "path": SCENES_SRC,
        "old": '    elif home_away == "neutral":\n        side = "a neutral site"',
        "new": '    elif home_away == "neutral":\n        side = "a road venue"',
        "killer": "neutral-site alt text must not say road venue",
        "kind": "python-scenes",
        "check": "neutral_alt",
    },
    {
        "name": "mexico-city-not-neutral",
        "path": SCENES_SRC,
        "old": 'NEUTRAL_VENUES = frozenset({"wembley", "super-bowl", "mexico-city"})',
        "new": 'NEUTRAL_VENUES = frozenset({"wembley", "super-bowl"})',
        "killer": "Mexico City must resolve as neutral, not a road venue",
        "kind": "python-scenes",
        "check": "mexico",
    },
    {
        "name": "levis-stadium-not-mapped",
        "path": SCENES_SRC,
        "old": '    "levi\'s stadium": "super-bowl",\n'
               '    "levis stadium": "super-bowl",\n'
               '    "levi stadium": "super-bowl",\n',
        "new": "",
        "killer": "Levi's Stadium Super Bowl must be a neutral venue",
        "kind": "python-scenes",
        "check": "levis",
    },
    {
        "name": "match-schedule-ignores-season",
        "path": SCENES_SRC,
        "old": "        if phase_season and game.get(\"seasonType\") != phase_season:\n"
               "            continue\n",
        "new": "",
        "killer": "postseason DEN with no venue must not steal week-8 Denver",
        "kind": "python-scenes",
        "check": "post_den",
    },
    {
        "name": "ignore-sponsored-flag",
        "path": POSTS_SRC,
        "old": "    if _truthy(value.get(\"sponsored\")):\n        return True\n",
        "new": "    if False:\n        return True\n",
        "killer": "sponsored=true in pin metadata must be rejected",
        "kind": "python-posts",
        "check": "sponsored",
    },
    {
        "name": "ignore-betting-keywords",
        "path": POSTS_SRC,
        "old": 'BETTING_DENY_RE = re.compile(\n    r"draftkings|fanduel|\\bodds\\b|o/u|over\\s*/\\s*under|sponsored|presented by|\\bbet\\b",\n    re.IGNORECASE,\n)',
        "new": "BETTING_DENY_RE = re.compile(r\"^$\")",
        "killer": "DraftKings / betting copy must be rejected",
        "kind": "python-posts",
        "check": "betting",
    },
    {
        "name": "select-catalog-season-only",
        "path": POSTS_SRC,
        "old": "        if row_abbr != abbr or row.get(\"week\") != week:\n            continue\n",
        "new": "        if False:\n            continue\n",
        "killer": "auto-pin must match week and opponent, not season alone",
        "kind": "python-posts",
        "check": "week_opponent_pin",
    },
    {
        "name": "allow-signed-edition-pins",
        "path": POSTS_SRC,
        "old": "        if not edition_pin_allowed(str(slug), root=root):\n"
               "            issues.append(f\"hero pin {slug} is attached to a signed edition\")\n",
        "new": "        if False:\n            issues.append(f\"hero pin {slug} is attached to a signed edition\")\n",
        "killer": "a fabricated @NFL pin on a signed edition must fail the gate",
        "kind": "python-posts",
        "check": "signed_pin",
    },
    {
        "name": "empty-signed-whitelist",
        "path": POSTS_SRC,
        "old": 'SIGNED_PIN_WHITELIST = frozenset({"2026-10-28-1750"})',
        "new": "SIGNED_PIN_WHITELIST = frozenset()",
        "killer": "the signed-pin whitelist must contain only 2026-10-28-1750",
        "kind": "python-posts",
        "check": "whitelist",
    },
    {
        "name": "hugo-week-opponent-check",
        "path": SCENE_TMPL,
        "old": "    {{- if and $oppOK $haOK -}}\n      {{- $id = . -}}\n      {{- $mapped = true -}}\n    {{- end -}}\n",
        "new": "    {{- $id = . -}}\n    {{- $mapped = true -}}\n",
        "killer": "Hugo week lookup must still require opponent agreement",
        "kind": "hugo",
        "check": "hugo_week",
    },
    {
        "name": "hugo-allowlist-check",
        "path": POST_TMPL,
        "old": "  {{- $listed := false -}}\n  {{- range $allow -}}\n    {{- if eq (lower .) (lower $handle) -}}{{- $listed = true -}}{{- end -}}\n  {{- end -}}\n",
        "new": "  {{- $listed := true -}}\n",
        "killer": "Hugo must reject an off-list handle even if the id is well-formed",
        "kind": "hugo",
        "check": "hugo_allowlist",
    },
    {
        "name": "hugo-mexico-not-neutral",
        "path": SCENE_TMPL,
        "old": '{{- $neutral := $catalog.neutralVenues | default (slice "wembley" "super-bowl" "mexico-city") -}}\n{{- if in $neutral $venue -}}{{- $ha = "neutral" -}}{{- end -}}\n',
        "new": '{{- $neutral := slice "wembley" "super-bowl" -}}\n{{- if in $neutral $venue -}}{{- $ha = "neutral" -}}{{- end -}}\n',
        "killer": "Hugo Mexico City must not resolve as a road/home Chargers scene",
        "kind": "hugo",
        "check": "hugo_neutral",
    },
    {
        "name": "hugo-pin-omits-svg",
        "path": MEDIA_TMPL,
        "old": '    <div class="nrt-hero-media__fallback" data-hero-fallback>{{ $illustration }}</div>\n',
        "new": "    <div class=\"nrt-hero-media__fallback\" data-hero-fallback></div>\n",
        "killer": "a pinned embed must still keep the SVG in the document",
        "kind": "hugo",
        "check": "hugo_svg",
    },
    {
        "name": "hugo-sponsored-ignored",
        "path": POST_TMPL,
        "old": "  {{- $sponsored := false -}}\n  {{- with .sponsored -}}\n    {{- if or (eq . true) (eq (lower (printf \"%v\" .)) \"true\") -}}{{- $sponsored = true -}}{{- end -}}\n  {{- end -}}\n",
        "new": "  {{- $sponsored := false -}}\n",
        "killer": "Hugo must reject a sponsored flag in pin metadata",
        "kind": "hugo",
        "check": "hugo_sponsored",
    },
    {
        "name": "embed-aspect-16-10-crop",
        "path": CSS_SRC,
        "old": "  min-height: var(--nrt-embed-reserve);\n  overflow: visible;\n",
        "new": "  aspect-ratio: 16 / 10;\n  min-height: 0;\n  overflow: hidden;\n",
        "killer": "the embed box must reserve ~640px and not crop at 16:10",
        "kind": "css",
        "check": "css",
    },
    {
        "name": "workflow-omits-sidecar",
        "path": WORKFLOW_SRC,
        "old": "            data/hero_posts.json \\\n",
        "new": "",
        "killer": "the daily workflow must git-add data/hero_posts.json",
        "kind": "text",
        "check": "workflow",
    },
    {
        "name": "overlay-omits-sidecar",
        "path": OVERLAY_SRC,
        "old": '    "data/hero_posts.json",\n',
        "new": "",
        "killer": "edition_overlay must allow data/hero_posts.json",
        "kind": "text",
        "check": "overlay",
    },
    {
        "name": "attach-skips-signed-check",
        "path": POSTS_SRC,
        "old": "    if not edition_pin_allowed(slug, root=root):\n        return None\n    pin = select_catalog_pin(narrative, root=root)\n",
        "new": "    pin = select_catalog_pin(narrative, root=root)\n",
        "killer": "attach_sidecar must refuse already-signed editions",
        "kind": "python-posts",
        "check": "signed_attach",
    },
)


def _kill_signed_attach(module) -> str | None:
    tmp, root = _temp_sidecar({
        "schemaVersion": 1,
        "allowlist": list(module.ALLOWLIST_DISPLAY),
        "catalog": [_catalog_row()],
        "pins": {},
    })
    try:
        _copy_signed_tree(root)
        edition = _preview(slug=SIGNED_SLUG)
        pin = module.attach_sidecar(edition, root=root)
        if pin is not None:
            return None
        return "survived: attach_sidecar still refused the signed edition"
    finally:
        tmp.cleanup()


CHECKS = {
    "week_opponent": _kill_week_opponent,
    "helmet": _kill_helmet,
    "fallback_caption": _kill_fallback_caption,
    "london": _kill_london,
    "neutral_alt": _kill_neutral_alt,
    "mexico": _kill_mexico,
    "levis": _kill_levis,
    "post_den": _kill_post_den,
    "sponsored": _kill_sponsored,
    "betting": _kill_betting,
    "week_opponent_pin": _kill_week_opponent_pin,
    "signed_pin": _kill_signed_pin,
    "whitelist": _kill_whitelist,
    "hugo_week": _kill_hugo_week_opponent,
    "hugo_allowlist": _kill_hugo_allowlist,
    "hugo_neutral": _kill_hugo_neutral,
    "hugo_svg": _kill_hugo_svg,
    "hugo_sponsored": _kill_hugo_sponsored,
    "css": _kill_css,
    "workflow": _kill_workflow,
    "overlay": _kill_overlay,
    "signed_attach": _kill_signed_attach,
}


class HeroMutations(unittest.TestCase):
    def test_named_mutants_are_killed(self):
        rows = []
        survivors = []
        for mutant in MUTANTS:
            source = mutant["path"].read_text(encoding="utf-8")
            self.assertIn(mutant["old"], source, mutant["name"])
            mutated = source.replace(mutant["old"], mutant["new"], 1)
            self.assertNotEqual(mutated, source, mutant["name"])
            reason = None
            if mutant["kind"] == "python-scenes":
                with tempfile.TemporaryDirectory() as tmp:
                    name = f"mutant_{mutant['name'].replace('-', '_')}"
                    try:
                        module = _load_mutated_module(Path(tmp), name, "hero_scenes.py", mutated)
                        reason = CHECKS[mutant["check"]](module)
                    finally:
                        _clear_module(name)
            elif mutant["kind"] == "python-posts":
                with tempfile.TemporaryDirectory() as tmp:
                    name = f"mutant_{mutant['name'].replace('-', '_')}"
                    try:
                        module = _load_mutated_module(Path(tmp), name, "hero_posts.py", mutated)
                        reason = CHECKS[mutant["check"]](module)
                    finally:
                        _clear_module(name)
            elif mutant["kind"] == "hugo":
                with tempfile.TemporaryDirectory() as tmp:
                    layouts = Path(tmp) / "partials"
                    shutil.copytree(ROOT / "layouts" / "partials", layouts)
                    (layouts / mutant["path"].name).write_text(mutated, encoding="utf-8")
                    reason = CHECKS[mutant["check"]](layouts)
            else:
                reason = CHECKS[mutant["check"]](mutated)
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
            f"Evaluated {len(rows)} named mutants. Survivors are listed below.",
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
        else:
            lines.append("No survivors.")
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
        levis = hero_scenes.resolve(
            _edition("preview", "San Francisco 49ers", phase_type="postseason",
                     at="Levi's Stadium"),
            schedule=schedule, catalog=catalog,
        )
        self.assertEqual(levis["id"], "preview-neutral-sf")
        self.assertNotIn("road venue", levis["alt"].lower())
        post_den = hero_scenes.resolve(
            _edition("preview", "Denver Broncos", phase_type="postseason"),
            schedule=schedule, catalog=catalog,
        )
        self.assertEqual(post_den["id"], "preview-fallback")
        self.assertIsNone(hero_posts.normalize_pin(_clean_pin(sponsored=True)))
        self.assertIsNone(hero_posts.normalize_pin(_clean_pin(text="FanDuel odds")))
        self.assertEqual(hero_posts.SIGNED_PIN_WHITELIST, frozenset({WEEK8_SLUG}))
        workflow = WORKFLOW_SRC.read_text(encoding="utf-8")
        self.assertGreaterEqual(workflow.count("data/hero_posts.json"), 2)
        self.assertIn("data/hero_posts.json", OVERLAY_SRC.read_text(encoding="utf-8"))
