"""Karen's hero mutants (REPORT-85d84ac9) plus the holding set."""
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
    SIGNED_SLUG,
    WEEK8_SLUG,
    _catalog_row,
    _clean_pin,
    _copy_signed_tree,
    _preview,
    _temp_sidecar,
    _week8_fixture,
    _write_unsigned_edition,
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


def _kill_o2_hugo(layouts: Path) -> str | None:
    fixture = {
        "slug": "at-only-away",
        "headline": "Denver is the assignment",
        "generatedAt": "2026-10-28T17:50:00Z",
        "phase": {"mode": "preview", "type": "regular", "label": "Preview"},
        "record": "5-1",
        "edition": "Preview",
        "nextGame": {"opponent": "Denver Broncos", "at": "@ the visiting stadium"},
        "injuries": [],
        "pressConferenceLinks": [],
        "pressConferences": [],
    }
    temp, page, _ = render_hero_fixtures([fixture], layouts=layouts)
    try:
        if "preview-away-den.svg" not in page:
            return None
        return "survived: Hugo still treated @ as away"
    finally:
        temp.cleanup()


def _kill_o4_markets(module) -> str | None:
    scene = module.resolve(
        _edition("preview", "Miami Dolphins", home_away="neutral"),
        schedule=module.load_schedule(ROOT),
        catalog=module.build_catalog(module.load_schedule(ROOT)),
    )
    if scene.get("id") != "preview-neutral-mia":
        return None
    return f"survived: markets.homeAway still resolved {scene.get('id')}"


def _kill_o7_at(module) -> str | None:
    scene = module.resolve(
        _edition("preview", "Denver Broncos", phase_type="postseason",
                 at="@ the visiting stadium"),
        schedule=module.load_schedule(ROOT),
        catalog=module.build_catalog(module.load_schedule(ROOT)),
    )
    if scene.get("id") != "preview-away-den":
        return None
    return f"survived: @ prefix still resolved {scene.get('id')}"


def _kill_o8_camp(module) -> str | None:
    scene = module.resolve(
        {"headline": "Camp", "phase": {"mode": "training-camp", "type": "training-camp"}},
        schedule=module.load_schedule(ROOT),
        catalog=module.build_catalog(module.load_schedule(ROOT)),
    )
    if scene.get("id") != "camp-fallback":
        return None
    return f"survived: training-camp still mapped to {scene.get('id')}"


def _kill_o8_hugo(layouts: Path) -> str | None:
    fixture = {
        "slug": "training-camp",
        "headline": "Camp is the work",
        "generatedAt": "2026-08-01T12:00:00Z",
        "phase": {"mode": "training-camp", "type": "training-camp", "label": "Training Camp"},
        "record": "0-0",
        "edition": "Training Camp",
        "injuries": [],
        "pressConferenceLinks": [],
        "pressConferences": [],
    }
    temp, page, _ = render_hero_fixtures([fixture], layouts=layouts)
    try:
        if "camp-fallback.svg" not in page:
            return None
        return "survived: Hugo still mapped training-camp to camp-fallback"
    finally:
        temp.cleanup()


def _kill_n6_id(module) -> str | None:
    if module.normalize_pin(_clean_pin(id="123456789", url="https://x.com/Chiefs/status/123456789")):
        return None
    return "survived: short id still rejected"


def _kill_n7_mismatch(module) -> str | None:
    row = _clean_pin(url=f"https://x.com/NFL/status/{CLEAN_ID}")
    if module.normalize_pin(row):
        return None
    return "survived: url/handle mismatch still rejected"


def _kill_n9_drift(module) -> str | None:
    tmp, root = _temp_sidecar({
        "schemaVersion": 1,
        "allowlist": list(module.ALLOWLIST_DISPLAY) + ["Arians"],
        "catalog": [],
        "pins": {},
    })
    try:
        issues = module.validate_sidecar(root)
        if not any("allowlist" in item for item in issues):
            return None
        return f"survived: drift issues={issues}"
    finally:
        tmp.cleanup()


def _kill_n11_hugo(layouts: Path) -> str | None:
    temp, page, _ = render_hero_fixtures(
        [_week8_fixture(slug="short-id")],
        pins={"short-id": _clean_pin(id="123456789", url="https://x.com/Chiefs/status/123456789")},
        layouts=layouts,
    )
    try:
        if "twitter-tweet" in page:
            return None
        return "survived: Hugo still rejected the short id"
    finally:
        temp.cleanup()


def _kill_n12_widgets(layouts: Path) -> str | None:
    temp, page, _ = render_hero_fixtures([_week8_fixture()], pins={}, layouts=layouts)
    try:
        if "widgets.js" in page:
            return None
        return "survived: unpinned page still has no widgets.js"
    finally:
        temp.cleanup()


def _kill_n13_hugo(layouts: Path) -> str | None:
    temp, page, _ = render_hero_fixtures(
        [_week8_fixture(slug="url-mismatch")],
        pins={"url-mismatch": _clean_pin(url=f"https://x.com/NFL/status/{CLEAN_ID}")},
        layouts=layouts,
    )
    try:
        if "twitter-tweet" in page:
            return None
        return "survived: Hugo still rejected the mismatched url"
    finally:
        temp.cleanup()


def _kill_d4_caption(module) -> str | None:
    if module.normalize_pin(_clean_pin(text="clean copy", caption="DraftKings board")):
        return None
    return "survived: caption still scanned"


def _kill_d6_hugo(layouts: Path) -> str | None:
    temp, page, _ = render_hero_fixtures(
        [_week8_fixture(slug="betting-caption")],
        pins={"betting-caption": _clean_pin(text="clean copy", caption="DraftKings board")},
        layouts=layouts,
    )
    try:
        if "twitter-tweet" in page:
            return None
        return "survived: Hugo still rejected betting caption"
    finally:
        temp.cleanup()


def _kill_d7_hugo(layouts: Path) -> str | None:
    temp, page, _ = render_hero_fixtures(
        [_week8_fixture(slug="sponsored-pin")],
        pins={"sponsored-pin": _clean_pin(sponsored=True)},
        layouts=layouts,
    )
    try:
        if "twitter-tweet" in page:
            return None
        return "survived: Hugo still rejected the sponsored flag"
    finally:
        temp.cleanup()


def _kill_a2_mode(module) -> str | None:
    tmp, root = _temp_sidecar({
        "schemaVersion": 1,
        "allowlist": list(module.ALLOWLIST_DISPLAY),
        "catalog": [_catalog_row(mode="review")],
        "pins": {},
    })
    try:
        edition = _preview(slug="2026-10-29-1500")
        _write_unsigned_edition(root, edition)
        pin = module.select_catalog_pin(edition, root=root)
        if pin:
            return None
        return f"survived: review catalog row still ignored for preview ({pin})"
    finally:
        tmp.cleanup()


def _kill_a3_guard(module) -> str | None:
    tmp, root = _temp_sidecar({
        "schemaVersion": 1,
        "allowlist": list(module.ALLOWLIST_DISPLAY),
        "catalog": [_catalog_row(week=None)],
        "pins": {},
    })
    try:
        edition = _preview(slug="2026-10-29-1400")
        edition["phase"] = {"mode": "preview", "type": "regular", "label": "Preview"}
        _write_unsigned_edition(root, edition)
        pin = module.select_catalog_pin(edition, root=root)
        if pin:
            return None
        return "survived: missing week still refused a catalog pin"
    finally:
        tmp.cleanup()


def _kill_j1(text: str) -> str | None:
    inspect_gate = (
        "if (unavailable(fig))" in text
        and 'settle(fig, "fallback")' in text
    )
    regex = "return /this (?:post|tweet) is unavailable/i.test(text);" in text
    if not inspect_gate or not regex:
        return None
    return "survived: unavailable check still present"


def _kill_j2(text: str) -> str | None:
    if "8000" not in text or "fallback" not in text:
        return None
    return "survived: 8s timeout still present"


def _kill_j3(text: str) -> str | None:
    if "var MIN = 240" not in text or "embedHeight(fig) >= MIN" not in text:
        return None
    return "survived: 240px minimum still present"


def _kill_j4(text: str) -> str | None:
    if "var poll = setInterval" not in text:
        return None
    poll = text.split("var poll = setInterval", 1)[1][:240]
    if "inspect(fig)" not in poll:
        return None
    return "survived: poll still calls inspect"


def _kill_f3(module) -> str | None:
    tmp, root = _temp_sidecar()
    try:
        path = root / "data" / ("narrative" + "_editions") / "2099-01-01-0000.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[1]", encoding="utf-8")
        if module.edition_pin_allowed("2099-01-01-0000", root=root):
            return None
        return "survived: non-object edition still failed closed"
    finally:
        tmp.cleanup()


def _kill_f4(module) -> str | None:
    tmp, root = _temp_sidecar()
    try:
        edition = _preview(slug="2026-10-29-1600")
        _write_unsigned_edition(root, edition)
        def boom(*_args, **_kwargs):
            raise module.review_gate.GateError("sig check failed")
        original = module.review_gate.review_signed_off
        module.review_gate.review_signed_off = boom
        try:
            if module.edition_pin_allowed("2026-10-29-1600", root=root):
                return None
            return "survived: GateError still failed closed"
        finally:
            module.review_gate.review_signed_off = original
    finally:
        tmp.cleanup()


def _kill_sponsored(module) -> str | None:
    if module.normalize_pin(_clean_pin(sponsored=True)) is not None:
        return None
    return "survived: sponsored pin was accepted"


def _kill_betting(module) -> str | None:
    if module.normalize_pin(_clean_pin(text="DraftKings Over/Under 40.5")) is not None:
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


def _kill_fail_closed(module) -> str | None:
    tmp, root = _temp_sidecar()
    try:
        if module.edition_pin_allowed("1999-01-01-0000", root=root):
            return None
        return "survived: missing edition still failed closed"
    finally:
        tmp.cleanup()


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
            "mediaReviewed": True,
        }},
        layouts=layouts,
    )
    try:
        if 'data-hero-kind="x-embed"' in page or "twitter-tweet" in page:
            return None
        return "survived: Hugo still rejected the off-list pin"
    finally:
        temp.cleanup()


def _kill_hugo_signed(layouts: Path) -> str | None:
    temp, page, _ = render_hero_fixtures(
        [{
            "slug": SIGNED_SLUG,
            "headline": "213 yards",
            "generatedAt": "2026-10-27T19:15:00Z",
            "phase": {"mode": "review", "type": "regular", "week": 7, "label": "Week 7"},
            "record": "5-1",
            "edition": "2026 Week 7 · Review",
            "lastGameReview": {"opponent": "Seattle Seahawks", "label": "Week 7"},
            "injuries": [],
            "pressConferenceLinks": [],
            "pressConferences": [],
        }],
        pins={SIGNED_SLUG: _clean_pin(handle="NFL", url=f"https://x.com/NFL/status/{CLEAN_ID}")},
        layouts=layouts,
        signoff_slugs=(SIGNED_SLUG,),
    )
    try:
        if "twitter-tweet" in page:
            return None
        return "survived: Hugo still locked the signed edition"
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


def _kill_css(css_text: str) -> str | None:
    if "16 / 10" in css_text or "16/10" in css_text:
        return None
    if "640px" not in css_text or "--nrt-embed-reserve" not in css_text:
        return None
    if "html.nrt-hero-js" not in css_text:
        return None
    fallback_zero = False
    for part in css_text.split('[data-hero-state="fallback"]')[1:]:
        if "min-height: 0" in part[:280]:
            fallback_zero = True
            break
    if not fallback_zero:
        return None
    idx = 0
    while True:
        pos = css_text.find("min-height: var(--nrt-embed-reserve)", idx)
        if pos < 0:
            break
        window = css_text[max(0, pos - 400):pos]
        if "html.nrt-hero-js" not in window:
            return None
        idx = pos + 1
    if "clip: rect(0 0 0 0)" in css_text and "ready" in css_text:
        ready = css_text.split('[data-hero-state="ready"]')[-1][:400]
        if "1px" in ready and "fallback" in css_text.split('[data-hero-state="ready"]')[0][-80:]:
            return None
    return "survived: reserved height and SVG underlay still present"


def _kill_workflow(text: str) -> str | None:
    if text.count("data/hero_posts.json") < 2:
        return None
    return "survived: daily workflow still stages the sidecar"


def _kill_overlay(text: str) -> str | None:
    if "data/hero_posts.json" not in text:
        return None
    return "survived: overlay allow-list still includes the sidecar"


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
    "o2": _kill_o2_hugo,
    "o4": _kill_o4_markets,
    "o7": _kill_o7_at,
    "o8": _kill_o8_camp,
    "o8_hugo": _kill_o8_hugo,
    "n6": _kill_n6_id,
    "n7": _kill_n7_mismatch,
    "n9": _kill_n9_drift,
    "n11": _kill_n11_hugo,
    "n12": _kill_n12_widgets,
    "n13": _kill_n13_hugo,
    "d4": _kill_d4_caption,
    "d6": _kill_d6_hugo,
    "d7": _kill_d7_hugo,
    "a2": _kill_a2_mode,
    "a3": _kill_a3_guard,
    "j1": _kill_j1,
    "j2": _kill_j2,
    "j3": _kill_j3,
    "j4": _kill_j4,
    "f3": _kill_f3,
    "f4": _kill_f4,
    "sponsored": _kill_sponsored,
    "betting": _kill_betting,
    "week_opponent_pin": _kill_week_opponent_pin,
    "signed_pin": _kill_signed_pin,
    "fail_closed": _kill_fail_closed,
    "hugo_week": _kill_hugo_week_opponent,
    "hugo_allowlist": _kill_hugo_allowlist,
    "hugo_signed": _kill_hugo_signed,
    "hugo_svg": _kill_hugo_svg,
    "css": _kill_css,
    "workflow": _kill_workflow,
    "overlay": _kill_overlay,
    "signed_attach": _kill_signed_attach,
}

MUTANTS = (
    {
        "name": "O2",
        "path": SCENE_TMPL,
        "old": '{{- if and (not $ha) (hasPrefix (strings.TrimSpace $at) "@") -}}{{- $ha = "away" -}}{{- end -}}\n',
        "new": "",
        "killer": "Hugo @ prefix must still mark the edition away",
        "kind": "hugo",
        "check": "o2",
    },
    {
        "name": "O4",
        "path": SCENES_SRC,
        "old": "    if mode == \"preview\":\n"
               "        market = ((edition.get(\"markets\") or {}).get(\"game\") or {})\n"
               "        ha = _norm(market.get(\"homeAway\"))\n"
               "        if ha in {\"home\", \"away\", \"neutral\"}:\n"
               "            market_abbr = opponent_abbr(market.get(\"opponent\"))\n"
               "            event_abbr = opponent_abbr(event.get(\"opponent\"))\n"
               "            if not market_abbr or not event_abbr or market_abbr == event_abbr:\n"
               "                return ha\n",
        "new": "",
        "killer": "markets.homeAway must still resolve an away Denver preview",
        "kind": "python-scenes",
        "check": "o4",
    },
    {
        "name": "O5",
        "path": SCENES_SRC,
        "old": "            if opp_ok and ha_ok:\n                return dict(scene)\n",
        "new": "            return dict(scene)\n",
        "killer": "week/opponent mismatch must not return the week-8 Denver scene",
        "kind": "python-scenes",
        "check": "week_opponent",
    },
    {
        "name": "O7",
        "path": SCENES_SRC,
        "old": "    if at.lstrip().startswith(\"{}\"):\n        return \"away\"\n".format("@"),
        "new": "",
        "killer": "Python @ prefix must still mark the edition away",
        "kind": "python-scenes",
        "check": "o7",
    },
    {
        "name": "O8",
        "path": SCENES_SRC,
        "old": "    if raw in {\"training-camp\", \"camp\"}:\n        return \"camp\"\n",
        "new": "    if raw in {\"camp\"}:\n        return \"camp\"\n",
        "killer": "training-camp must still resolve to camp-fallback",
        "kind": "python-scenes",
        "check": "o8",
    },
    {
        "name": "O8-hugo",
        "path": SCENE_TMPL,
        "old": '{{- if or (eq $mode "training-camp") (eq $mode "camp") -}}{{- $mode = "camp" -}}{{- end -}}\n',
        "new": '{{- if eq $mode "camp" -}}{{- $mode = "camp" -}}{{- end -}}\n',
        "killer": "Hugo training-camp must still use camp-fallback",
        "kind": "hugo",
        "check": "o8_hugo",
    },
    {
        "name": "N6",
        "path": POSTS_SRC,
        "old": 'POST_ID_RE = re.compile(r"^[0-9]{10,20}$")',
        "new": 'POST_ID_RE = re.compile(r"^[0-9]{1,20}$")',
        "killer": "a 9-digit id must still be rejected",
        "kind": "python-posts",
        "check": "n6",
    },
    {
        "name": "N7",
        "path": POSTS_SRC,
        "old": "        if handle.lower() != url_handle.lower() or post_id != url_id:\n            return None\n",
        "new": "        if False:\n            return None\n",
        "killer": "url/handle mismatch must still be rejected",
        "kind": "python-posts",
        "check": "n7",
    },
    {
        "name": "N9",
        "path": POSTS_SRC,
        "old": "    if [str(item) for item in listed] != list(ALLOWLIST_DISPLAY):\n"
               "        issues.append(\"hero pin allowlist in data/hero_posts.json does not match code\")\n",
        "new": "    if False:\n"
               "        issues.append(\"hero pin allowlist in data/hero_posts.json does not match code\")\n",
        "killer": "sidecar allowlist drift must still fail the gate",
        "kind": "python-posts",
        "check": "n9",
    },
    {
        "name": "N11",
        "path": POST_TMPL,
        "old": "  {{- $idOK := findRE `^[0-9]{10,20}$` $id -}}\n",
        "new": "  {{- $idOK := true -}}\n",
        "killer": "Hugo must reject a short id",
        "kind": "hugo",
        "check": "n11",
    },
    {
        "name": "N12",
        "path": MEDIA_TMPL,
        "old": "{{- else -}}\n<figure class=\"nrt-hero-media\"",
        "new": "{{- else -}}\n<script async src=\"https://platform.twitter.com/widgets.js\" charset=\"utf-8\"></script>\n<figure class=\"nrt-hero-media\"",
        "killer": "unpinned pages must not load widgets.js",
        "kind": "hugo",
        "check": "n12",
    },
    {
        "name": "N13",
        "path": POST_TMPL,
        "old": "  {{- $urlOK := true -}}\n"
               "  {{- if $url -}}\n"
               "    {{- $urlOK = findRE (printf `^https://(?:www\\.)?(?:x|twitter)\\.com/%s/status/%s/?$` $handle $id) $url -}}\n"
               "  {{- end -}}\n",
        "new": "  {{- $urlOK := true -}}\n",
        "killer": "Hugo must reject a handle/url mismatch",
        "kind": "hugo",
        "check": "n13",
    },
    {
        "name": "D4",
        "path": POSTS_SRC,
        "old": '            "text", "alt", "caption", "note",\n',
        "new": '            "text", "alt", "note",\n',
        "killer": "Python denylist must scan caption",
        "kind": "python-posts",
        "check": "d4",
    },
    {
        "name": "D6",
        "path": POST_TMPL,
        "old": "  {{- $denied := findRE `(?i)draftkings|fanduel|\\bodds\\b|o/u|over\\s*/\\s*under|sponsored|presented by|\\bbet\\b` $blob -}}\n",
        "new": "  {{- $denied := false -}}\n",
        "killer": "Hugo denylist must reject DraftKings in caption",
        "kind": "hugo",
        "check": "d6",
    },
    {
        "name": "D7",
        "path": POST_TMPL,
        "old": "  {{- if and $idOK $listed $urlOK $handle $reviewed (not $sponsored) (not $denied) (not $locked) -}}\n",
        "new": "  {{- if and $idOK $listed $urlOK $handle $reviewed (not $denied) (not $locked) -}}\n",
        "killer": "Hugo must reject a sponsored flag",
        "kind": "hugo",
        "check": "d7",
    },
    {
        "name": "A2",
        "path": POSTS_SRC,
        "old": "        if _s(row.get(\"mode\") or \"preview\") != mode:\n            continue\n",
        "new": "        if False:\n            continue\n",
        "killer": "auto-pin must require mode agreement",
        "kind": "python-posts",
        "check": "a2",
    },
    {
        "name": "A3",
        "path": POSTS_SRC,
        "old": "    if not week or not abbr:\n        return None\n",
        "new": "    if False:\n        return None\n",
        "killer": "auto-pin must refuse a missing week or opponent",
        "kind": "python-posts",
        "check": "a3",
    },
    {
        "name": "J1",
        "path": MEDIA_TMPL,
        "old": "    if (unavailable(fig)) {\n"
               "      settle(fig, \"fallback\");\n"
               "      return;\n"
               "    }\n",
        "new": "",
        "killer": "client must treat an unavailable post as fallback",
        "kind": "text",
        "check": "j1",
    },
    {
        "name": "J1-regex",
        "path": MEDIA_TMPL,
        "old": "    return /this (?:post|tweet) is unavailable/i.test(text);",
        "new": "    return false;",
        "killer": "unavailable() must still test the tombstone copy",
        "kind": "text",
        "check": "j1",
    },
    {
        "name": "J3",
        "path": MEDIA_TMPL,
        "old": "  var MIN = 240;",
        "new": "  var MIN = 0;",
        "killer": "ready must still require a 240px embed",
        "kind": "text",
        "check": "j3",
    },
    {
        "name": "J4",
        "path": MEDIA_TMPL,
        "old": "        n += 1;\n        inspect(fig);\n        if (bind() || n > 40) clearInterval(poll);\n",
        "new": "        n += 1;\n        if (bind() || n > 40) clearInterval(poll);\n",
        "killer": "the widgets.js poll must still call inspect()",
        "kind": "text",
        "check": "j4",
    },
    {
        "name": "J2",
        "path": MEDIA_TMPL,
        "old": "    var timer = setTimeout(function () { settle(fig, \"fallback\"); }, 8000);",
        "new": "    var timer = 0;",
        "killer": "client must fall back after 8s without a real embed",
        "kind": "text",
        "check": "j2",
    },
    {
        "name": "O1",
        "path": SCENES_SRC,
        "old": "            opp_ok = not abbr or scene.get(\"opponentAbbr\") == abbr\n"
               "            ha_ok = not home_away or scene.get(\"homeAway\") == home_away\n"
               "            if opp_ok and ha_ok:",
        "new": "            if True:",
        "killer": "week-first lookup must still require opponent agreement",
        "kind": "python-scenes",
        "check": "week_opponent",
    },
    {
        "name": "O3",
        "path": SCENES_SRC,
        "old": '        "caption": _scene_caption(mode, ""),',
        "new": '        "caption": _scene_caption(mode, "arrowhead"),',
        "killer": "unknown/away fallback caption must not mention Arrowhead",
        "kind": "python-scenes",
        "check": "fallback_caption",
    },
    {
        "name": "O6",
        "path": SCENES_SRC,
        "old": "    for extra in EXTRA_MATCHUPS:",
        "new": "    for extra in ():",
        "killer": "London vs Miami must not resolve as Hard Rock",
        "kind": "python-scenes",
        "check": "london",
    },
    {
        "name": "N16",
        "path": SCENES_SRC,
        "old": '        f\'<g transform="translate({x},{y}) {flip}">\'',
        "new": '        f\'<g transform="translate({-x if facing == "left" else x},{y}) {flip}">\'',
        "killer": "matchup SVGs must keep both helmets inside the viewBox",
        "kind": "python-scenes",
        "check": "helmet",
    },
    {
        "name": "D1",
        "path": POSTS_SRC,
        "old": "    if _truthy(value.get(\"sponsored\")):\n        return True\n",
        "new": "    if False:\n        return True\n",
        "killer": "sponsored=true in pin metadata must be rejected",
        "kind": "python-posts",
        "check": "sponsored",
    },
    {
        "name": "D2",
        "path": POSTS_SRC,
        "old": 'BETTING_DENY_RE = re.compile(\n    r"draftkings|fanduel|\\bodds\\b|o/u|over\\s*/\\s*under|sponsored|presented by|\\bbet\\b",\n    re.IGNORECASE,\n)',
        "new": "BETTING_DENY_RE = re.compile(r\"^$\")",
        "killer": "DraftKings / betting copy must be rejected",
        "kind": "python-posts",
        "check": "betting",
    },
    {
        "name": "N14",
        "path": POSTS_SRC,
        "old": "        if row_abbr != abbr or row.get(\"week\") != week:\n            continue\n",
        "new": "        if False:\n            continue\n",
        "killer": "auto-pin must match week and opponent, not season alone",
        "kind": "python-posts",
        "check": "week_opponent_pin",
    },
    {
        "name": "S1",
        "path": POSTS_SRC,
        "old": "        reason = edition_pin_block_reason(str(slug), root=root)\n"
               "        if reason:\n"
               "            issues.append(f\"hero pin {slug} {reason}\")\n",
        "new": "        reason = edition_pin_block_reason(str(slug), root=root)\n"
               "        if False:\n"
               "            issues.append(f\"hero pin {slug} {reason}\")\n",
        "killer": "a fabricated @NFL pin on a signed edition must fail the gate",
        "kind": "python-posts",
        "check": "signed_pin",
    },
    {
        "name": "S6",
        "path": POSTS_SRC,
        "old": "    if not path.is_file():\n        return \"has no readable edition\"\n",
        "new": "    if not path.is_file():\n        return None\n",
        "killer": "a missing edition file must fail closed",
        "kind": "python-posts",
        "check": "fail_closed",
    },
    {
        "name": "F3",
        "path": POSTS_SRC,
        "old": "    if not isinstance(payload, dict):\n        return \"edition is unreadable\"\n",
        "new": "    if not isinstance(payload, dict):\n        return None\n",
        "killer": "a non-object edition file must fail closed",
        "kind": "python-posts",
        "check": "f3",
    },
    {
        "name": "F4",
        "path": POSTS_SRC,
        "old": "    except review_gate.GateError:\n        return \"edition signature check failed\"\n",
        "new": "    except review_gate.GateError:\n        return None\n",
        "killer": "a signature-check GateError must fail closed",
        "kind": "python-posts",
        "check": "f4",
    },
    {
        "name": "N8",
        "path": SCENE_TMPL,
        "old": "    {{- if and $oppOK $haOK -}}\n      {{- $id = . -}}\n      {{- $mapped = true -}}\n    {{- end -}}\n",
        "new": "    {{- $id = . -}}\n    {{- $mapped = true -}}\n",
        "killer": "Hugo week lookup must still require opponent agreement",
        "kind": "hugo",
        "check": "hugo_week",
    },
    {
        "name": "N10",
        "path": POST_TMPL,
        "old": "  {{- $listed := false -}}\n  {{- range $allow -}}\n    {{- if eq (lower .) (lower $handle) -}}{{- $listed = true -}}{{- end -}}\n  {{- end -}}\n",
        "new": "  {{- $listed := true -}}\n",
        "killer": "Hugo must reject an off-list handle even if the id is well-formed",
        "kind": "hugo",
        "check": "hugo_allowlist",
    },
    {
        "name": "S7",
        "path": POST_TMPL,
        "old": "  {{- $locked := false -}}\n"
               "  {{- with $n.slug -}}\n"
               "    {{- if fileExists (printf \"signoff/review/%s.sig\" .) -}}{{- $locked = true -}}{{- end -}}\n"
               "  {{- end -}}\n",
        "new": "  {{- $locked := false -}}\n",
        "killer": "Hugo must refuse a pin on a signed Review",
        "kind": "hugo",
        "check": "hugo_signed",
    },
    {
        "name": "N4",
        "path": MEDIA_TMPL,
        "old": '    <div class="nrt-hero-media__fallback" data-hero-fallback>{{ $illustration }}</div>\n',
        "new": "    <div class=\"nrt-hero-media__fallback\" data-hero-fallback></div>\n",
        "killer": "a pinned embed must still keep the SVG in the document",
        "kind": "hugo",
        "check": "hugo_svg",
    },
    {
        "name": "N5",
        "path": CSS_SRC,
        "old": "  min-height: var(--nrt-embed-reserve);\n  overflow: visible;\n",
        "new": "  aspect-ratio: 16 / 10;\n  min-height: 0;\n  overflow: hidden;\n",
        "killer": "the embed box must reserve ~640px and not crop at 16:10",
        "kind": "css",
        "check": "css",
    },
    {
        "name": "N17",
        "path": CSS_SRC,
        "old": "html.nrt-hero-js .nrt-hero-media[data-hero-kind=\"x-embed\"][data-hero-state=\"fallback\"] .nrt-hero-media__visual--embed {\n  min-height: 0;\n}\n",
        "new": "",
        "killer": "fallback must drop the reserved embed height",
        "kind": "css",
        "check": "css",
    },
    {
        "name": "N18",
        "path": CSS_SRC,
        "old": "  height: auto;\n  min-height: 0;\n  overflow: visible;\n  background: #171416;\n",
        "new": "  height: auto;\n  min-height: var(--nrt-embed-reserve);\n  overflow: visible;\n  background: #171416;\n",
        "killer": "JS-off and fallback must not keep a reserved embed box",
        "kind": "css",
        "check": "css",
    },
    {
        "name": "N15",
        "path": OVERLAY_SRC,
        "old": '    "data/hero_posts.json",\n',
        "new": "",
        "killer": "edition_overlay must allow data/hero_posts.json",
        "kind": "text",
        "check": "overlay",
    },
    {
        "name": "N1",
        "path": WORKFLOW_SRC,
        "old": "            data/hero_posts.json \\\n",
        "new": "",
        "killer": "the daily workflow must git-add data/hero_posts.json",
        "kind": "text",
        "check": "workflow",
    },
    {
        "name": "S2",
        "path": POSTS_SRC,
        "old": "    if not edition_pin_allowed(slug, root=root):\n        return None\n    pin = select_catalog_pin(narrative, root=root)\n",
        "new": "    pin = select_catalog_pin(narrative, root=root)\n",
        "killer": "attach_sidecar must refuse already-signed editions",
        "kind": "python-posts",
        "check": "signed_attach",
    },
)


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
            "# Hero mutation report (Karen IDs)",
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
        self.assertIsNone(hero_posts.normalize_pin(_clean_pin(sponsored=True)))
        self.assertIsNone(hero_posts.normalize_pin(_clean_pin(text="FanDuel odds")))
        self.assertIsNone(hero_posts.normalize_pin(_clean_pin(text="clean", caption="DraftKings")))
        self.assertFalse(hasattr(hero_posts, "SIGNED_PIN_WHITELIST") and hero_posts.SIGNED_PIN_WHITELIST)
        workflow = WORKFLOW_SRC.read_text(encoding="utf-8")
        self.assertGreaterEqual(workflow.count("data/hero_posts.json"), 2)
        self.assertIn("data/hero_posts.json", OVERLAY_SRC.read_text(encoding="utf-8"))
        media = MEDIA_TMPL.read_text(encoding="utf-8")
        self.assertIn("embedHeight", media)
        self.assertIn("8000", media)
        self.assertIn("unavailable", media)
        self.assertIn("var MIN = 240", media)
        self.assertIn("if (unavailable(fig))", media)
        self.assertIn("inspect(fig);", media)
        self.assertIn('document.documentElement.classList.add("nrt-hero-js")', media)
        css = CSS_SRC.read_text(encoding="utf-8")
        self.assertIn("html.nrt-hero-js", css)
        self.assertIn('[data-hero-state="fallback"]', css)
        self.assertNotIn("SIGNED_PIN_WHITELIST", POSTS_SRC.read_text(encoding="utf-8"))
        self.assertIn("has no readable edition", POSTS_SRC.read_text(encoding="utf-8"))
        self.assertIn("edition signature check failed", POSTS_SRC.read_text(encoding="utf-8"))
