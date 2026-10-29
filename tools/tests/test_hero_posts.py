"""Official X hero pins: allowlist, sidecar, generator, embed render."""
from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.chiefs_narrative import hero_posts

ROOT = Path(__file__).resolve().parents[2]
HUGO = shutil.which("hugo") or str(Path.home() / ".local" / "hugo" / "hugo")
WEEK8_SLUG = "2026-10-28-1750"
SIGNED_SLUG = "2026-10-27-1915"
CLEAN_ID = "2115544469839745052"
CLEAN_URL = f"https://x.com/Chiefs/status/{CLEAN_ID}"
DELETED_ID = "1999999999999999999"
DELETED_URL = f"https://x.com/Chiefs/status/{DELETED_ID}"


def _preview(opponent="Denver Broncos", *, week=8, at="@ Empower Field at Mile High",
             home_away="away", slug=WEEK8_SLUG):
    return {
        "slug": slug,
        "headline": "Denver is the assignment",
        "phase": {"mode": "preview", "type": "regular", "week": week, "label": "Week 8"},
        "nextGame": {"opponent": opponent, "label": "Week 8 · Sun, Nov 1", "at": at},
        "markets": {"game": {"opponent": opponent, "homeAway": home_away}},
    }


def _clean_pin(**overrides):
    row = {
        "id": CLEAN_ID,
        "handle": "Chiefs",
        "url": CLEAN_URL,
        "postedAt": "2026-10-28T20:41:21Z",
        "text": "Rashee Rice and Omarr Norman-Lott were limited in practice today.",
        "alt": "Official Kansas City Chiefs injury report from Denver week practice.",
        "sponsored": False,
    }
    row.update(overrides)
    return row


def _catalog_row(**overrides):
    row = {
        "week": 8,
        "seasonType": "reg",
        "opponentAbbr": "DEN",
        "homeAway": "away",
        "mode": "preview",
        **_clean_pin(),
    }
    row.update(overrides)
    return row


def _temp_sidecar(payload=None):
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    data = root / "data"
    data.mkdir()
    body = payload if payload is not None else {
        "schemaVersion": 1,
        "allowlist": list(hero_posts.ALLOWLIST_DISPLAY),
        "catalog": [],
        "pins": {},
    }
    (data / "hero_posts.json").write_text(json.dumps(body, indent=2), encoding="utf-8")
    return tmp, root


def _copy_signed_tree(root: Path):
    editions_name = "narrative" + "_editions"
    editions = root / "data" / editions_name
    editions.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(
        ROOT / "data" / editions_name / f"{SIGNED_SLUG}.json",
        editions / f"{SIGNED_SLUG}.json",
    )
    signoff = root / "signoff" / "review"
    signoff.mkdir(parents=True, exist_ok=True)
    for suffix in (".json", ".sig"):
        src = ROOT / "signoff" / "review" / f"{SIGNED_SLUG}{suffix}"
        if src.is_file():
            shutil.copyfile(src, signoff / src.name)
    pubkey = ROOT / "scripts" / "review_signoff_pubkey.pem"
    if pubkey.is_file():
        dest = root / "scripts"
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(pubkey, dest / pubkey.name)


def render_hero_fixtures(fixtures, *, pins=None, layouts=None, css_text=None):
    """Render narrative hero partials through a temporary Hugo site."""
    temp = tempfile.TemporaryDirectory()
    source = Path(temp.name) / "source"
    source.mkdir()
    (source / "data").mkdir()
    layout_src = layouts or (ROOT / "layouts" / "partials")
    shutil.copytree(layout_src, source / "layouts" / "partials")
    shutil.copyfile(ROOT / "data" / "hero_scenes.json", source / "data" / "hero_scenes.json")
    shutil.copyfile(ROOT / "data" / "schedule_2026.json", source / "data" / "schedule_2026.json")
    shutil.copyfile(ROOT / "data" / "hero_media.json", source / "data" / "hero_media.json")
    sidecar = {
        "schemaVersion": 1,
        "allowlist": list(hero_posts.ALLOWLIST_DISPLAY),
        "catalog": [],
        "pins": pins or {},
    }
    (source / "data" / "hero_posts.json").write_text(json.dumps(sidecar), encoding="utf-8")
    public_scenes = source / "public" / "images" / "hero-scenes"
    public_scenes.parent.mkdir(parents=True)
    shutil.copytree(ROOT / "public" / "images" / "hero-scenes", public_scenes)
    css_dir = source / "public" / "css"
    css_dir.mkdir(parents=True, exist_ok=True)
    css = css_text if css_text is not None else (
        ROOT / "public" / "css" / "editorial-layout.css"
    ).read_text(encoding="utf-8")
    (css_dir / "editorial-layout.css").write_text(css, encoding="utf-8")
    (source / "hugo.yaml").write_text(
        "baseURL: https://example.test/\nstaticDir:\n  - public\n", encoding="utf-8"
    )
    (source / "data" / "fixtures.json").write_text(json.dumps(fixtures), encoding="utf-8")
    (source / "layouts" / "index.html").write_text(
        '{{ range .Site.Data.fixtures }}<section data-hero-slug="{{ .slug }}">'
        '{{ partial "nrt-hero-media.html" (dict "n" . "site" $.Site) }}'
        "</section>{{ end }}\n"
        '<link rel="stylesheet" href="{{ "css/editorial-layout.css" | relURL }}">\n',
        encoding="utf-8",
    )
    dist = Path(temp.name) / "site"
    result = subprocess.run(
        [HUGO, "--minify", "--destination", str(dist)],
        cwd=source, capture_output=True, text=True,
    )
    if result.returncode:
        temp.cleanup()
        raise AssertionError(result.stdout + result.stderr)
    page = (dist / "index.html").read_text(encoding="utf-8")
    css_out = (dist / "css" / "editorial-layout.css").read_text(encoding="utf-8")
    return temp, page, css_out


def _week8_fixture(slug=WEEK8_SLUG, opponent="Denver Broncos", week=8, at="@ Empower Field at Mile High"):
    return {
        "slug": slug,
        "headline": "Denver is the assignment after 213 yards",
        "generatedAt": "2026-10-28T17:50:00Z",
        "phase": {"mode": "preview", "type": "regular", "week": week, "label": f"Week {week}"},
        "record": "5-1",
        "edition": f"2026 Week {week} · Preview",
        "nextGame": {
            "opponent": opponent,
            "label": f"Week {week} · Sun, Nov 1 · 3:25 PM CT",
            "at": at,
        },
        "markets": {"game": {"opponent": opponent, "homeAway": "away"}},
        "lastGameReview": {"opponent": "Seattle Seahawks", "label": "Week 7"},
        "injuries": [],
        "pressConferenceLinks": [],
        "pressConferences": [],
    }


class HeroPostAllowlist(unittest.TestCase):
    def test_week8_is_unpinned_and_pollution_removed(self):
        self.assertIsNone(hero_posts.pin_for_slug(WEEK8_SLUG))
        payload = json.loads((ROOT / "data" / "hero_posts.json").read_text(encoding="utf-8"))
        self.assertNotIn("2026-10-29-0230", payload.get("pins") or {})
        self.assertEqual(payload.get("pins") or {}, {})
        self.assertEqual(payload.get("catalog") or [], [])

    def test_off_list_malformed_sponsored_and_betting_are_rejected(self):
        good = _clean_pin()
        self.assertIsNotNone(hero_posts.normalize_pin(good))
        for handle in ("ArrowheadPride", "Polymarket", "DraftKings", "ChiefsFan",
                       "KCMemeTeam", "sponsoredchiefs"):
            row = dict(good)
            row["handle"] = handle
            row["url"] = f"https://x.com/{handle}/status/{CLEAN_ID}"
            self.assertIsNone(hero_posts.normalize_pin(row), handle)
        self.assertIsNone(hero_posts.normalize_pin({**good, "id": "abc"}))
        self.assertIsNone(hero_posts.normalize_pin({**good, "id": "12"}))
        self.assertIsNone(hero_posts.normalize_pin({**good, "url": "https://x.com/Chiefs/status/nope"}))
        self.assertIsNone(hero_posts.normalize_pin("https://x.com/Chiefs/status/" + CLEAN_ID))
        self.assertIsNone(hero_posts.normalize_pin({**good, "sponsored": True}))
        for text in ("DraftKings Over/Under", "FanDuel odds", "O/U 40.5",
                     "presented by a partner", "sponsored graphic", "bet the total"):
            self.assertIsNone(hero_posts.normalize_pin({**good, "text": text}), text)

    def test_validate_sidecar_is_offline(self):
        with patch("urllib.request.urlopen", side_effect=AssertionError("network")):
            with patch("requests.get", side_effect=AssertionError("network")):
                issues = hero_posts.validate_sidecar()
        self.assertEqual(issues, [])

    def test_gate_fails_off_list_handle_in_sidecar(self):
        tmp, root = _temp_sidecar({
            "schemaVersion": 1,
            "allowlist": list(hero_posts.ALLOWLIST_DISPLAY),
            "catalog": [],
            "pins": {
                "2026-10-01-0000": {
                    "id": CLEAN_ID,
                    "handle": "ArrowheadPride",
                    "url": f"https://x.com/ArrowheadPride/status/{CLEAN_ID}",
                    "text": "fan recap",
                }
            },
        })
        try:
            issues = hero_posts.validate_sidecar(root)
            self.assertTrue(any("2026-10-01-0000" in item for item in issues))
        finally:
            tmp.cleanup()

    def test_signed_edition_pin_fails_unless_whitelisted(self):
        tmp, root = _temp_sidecar()
        try:
            _copy_signed_tree(root)
            payload = json.loads((root / "data" / "hero_posts.json").read_text(encoding="utf-8"))
            payload["pins"][SIGNED_SLUG] = _clean_pin(
                handle="NFL",
                url=f"https://x.com/NFL/status/{CLEAN_ID}",
                text="Fabricated league pin",
                alt="Fabricated league pin",
            )
            payload["pins"][WEEK8_SLUG] = _clean_pin()
            (root / "data" / "hero_posts.json").write_text(json.dumps(payload), encoding="utf-8")
            issues = hero_posts.validate_sidecar(root)
            self.assertTrue(any(SIGNED_SLUG in item and "signed" in item for item in issues), issues)
            self.assertFalse(any(WEEK8_SLUG in item and "signed" in item for item in issues), issues)
            self.assertIsNone(hero_posts.pin_for_slug(SIGNED_SLUG, root=root))
        finally:
            tmp.cleanup()

    def test_auto_pin_requires_week_and_opponent_and_uses_temp_sidecar(self):
        tmp, root = _temp_sidecar({
            "schemaVersion": 1,
            "allowlist": list(hero_posts.ALLOWLIST_DISPLAY),
            "catalog": [_catalog_row()],
            "pins": {},
        })
        try:
            den = _preview(slug="2026-10-29-1200")
            before = json.dumps(den)
            pin = hero_posts.attach_sidecar(den, root=root)
            self.assertIsNotNone(pin)
            self.assertEqual(pin["id"], CLEAN_ID)
            self.assertEqual(json.dumps(den), before)
            written = hero_posts.pin_for_slug("2026-10-29-1200", root=root)
            self.assertEqual(written["id"], CLEAN_ID)
            sea = _preview(opponent="Seattle Seahawks", week=8, at="@ Lumen Field",
                           slug="2026-10-29-1300")
            self.assertIsNone(hero_posts.attach_sidecar(sea, root=root))
            self.assertIsNone(hero_posts.pin_for_slug("2026-10-29-1300", root=root))
            self.assertIsNone(hero_posts.pin_for_slug(SIGNED_SLUG, root=root))
            committed = json.loads((ROOT / "data" / "hero_posts.json").read_text(encoding="utf-8"))
            self.assertNotIn("2026-10-29-1200", committed.get("pins") or {})
        finally:
            tmp.cleanup()

    def test_allowlist_lives_in_code(self):
        self.assertIn("chiefs", hero_posts.ALLOWLIST)
        self.assertIn("chiefspr", hero_posts.ALLOWLIST)
        self.assertIn("nfl", hero_posts.ALLOWLIST)
        self.assertIn("theathletic", hero_posts.ALLOWLIST)
        self.assertIn("espn", hero_posts.ALLOWLIST)
        self.assertEqual(hero_posts.SIGNED_PIN_WHITELIST, frozenset({WEEK8_SLUG}))
        src = (ROOT / "tools" / "chiefs_narrative" / "hero_posts.py").read_text(encoding="utf-8")
        self.assertIn("ALLOWLIST", src)
        self.assertIn("SIGNED_PIN_WHITELIST", src)
        self.assertIn("BETTING_DENY_RE", src)
        self.assertNotIn("arrowheadpride", hero_posts.ALLOWLIST)
        self.assertNotIn("polymarket", hero_posts.ALLOWLIST)

    def test_csp_if_present_allows_exactly_platform_twitter(self):
        hits = []
        for path in ROOT.rglob("*"):
            if not path.is_file() or "node_modules" in path.parts or ".git" in path.parts:
                continue
            if path.suffix.lower() not in {".html", ".js", ".css", ".yml", ".yaml", ".json", ".toml", ".headers"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if re.search(r"content-security-policy", text, re.I):
                hits.append(path)
                self.assertIn("platform.twitter.com", text)
                self.assertNotIn("cdn.syndication.twimg.com", text)

    def test_no_network_imports_in_hero_posts(self):
        src = (ROOT / "tools" / "chiefs_narrative" / "hero_posts.py").read_text(encoding="utf-8")
        self.assertNotIn("requests", src)
        self.assertNotIn("urllib.request", src)
        self.assertNotIn("publish.twitter.com", src)
        self.assertNotIn("api.x.com", src)


@unittest.skipUnless(Path(HUGO).is_file(), "Hugo unavailable")
class HeroPostRendering(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures = [
            _week8_fixture(),
            {
                "slug": SIGNED_SLUG,
                "headline": "213 yards, a 50-yard answer",
                "generatedAt": "2026-10-27T19:15:00Z",
                "phase": {"mode": "review", "type": "regular", "week": 7, "label": "Week 7"},
                "record": "5-1",
                "edition": "2026 Week 7 · Review",
                "nextGame": {"opponent": "Denver Broncos"},
                "lastGameReview": {
                    "opponent": "Seattle Seahawks",
                    "label": "Week 7 · Sun, Oct 25 · 7:20 PM CT",
                    "lede": "At Lumen Field, Seattle controlled both lines.",
                },
                "injuries": [],
                "pressConferenceLinks": [],
                "pressConferences": [],
            },
            _week8_fixture(slug="clean-pin"),
            _week8_fixture(slug="deleted-pin"),
            _week8_fixture(slug="offlist-pin"),
            _week8_fixture(slug="week-mismatch", opponent="Seattle Seahawks",
                           at="@ Lumen Field"),
        ]
        pins = {
            "clean-pin": _clean_pin(),
            "deleted-pin": _clean_pin(id=DELETED_ID, url=DELETED_URL,
                                      text="This post will 404.",
                                      alt="Deleted-post fixture."),
            "offlist-pin": {
                "id": CLEAN_ID,
                "handle": "ArrowheadPride",
                "url": f"https://x.com/ArrowheadPride/status/{CLEAN_ID}",
                "text": "fan recap",
                "alt": "fan recap",
            },
        }
        cls.temp, cls.page, cls.css = render_hero_fixtures(fixtures, pins=pins)
        cls.blocks = {
            slug: html.unescape(match.group(0))
            for slug, match in (
                (item, re.search(
                    rf'<section data-hero-slug="?{re.escape(item)}"?.*?</section>',
                    cls.page, re.S,
                ))
                for item in (
                    WEEK8_SLUG, SIGNED_SLUG, "clean-pin", "deleted-pin",
                    "offlist-pin", "week-mismatch",
                )
            )
            if match
        }

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_unpinned_week8_and_archive_use_svg(self):
        week8 = self.blocks[WEEK8_SLUG]
        sea = self.blocks[SIGNED_SLUG]
        self.assertRegex(week8, r'data-hero-kind="?article-illustration"?')
        self.assertIn("preview-away-den.svg", week8)
        self.assertNotRegex(week8, r'data-hero-kind="?x-embed"?')
        self.assertRegex(sea, r'data-hero-kind="?article-illustration"?')
        self.assertIn("review-away-sea.svg", sea)
        self.assertNotIn("Arrowhead", sea)

    def test_clean_pin_keeps_svg_credit_link_and_reserved_height(self):
        den = self.blocks["clean-pin"]
        self.assertRegex(den, r'data-hero-kind="?x-embed"?')
        self.assertRegex(den, r'data-hero-state="?pending"?')
        self.assertRegex(den, r'class="?twitter-tweet"?')
        self.assertIn(CLEAN_URL, den)
        self.assertIn("@Chiefs", den)
        self.assertIn("View post on X", den)
        self.assertIn("Source:", den)
        self.assertIn("nrt-hero-media__fallback", den)
        self.assertIn("preview-away-den.svg", den)
        self.assertIn("nrt-hero-media__visual--embed", den)
        self.assertRegex(self.page, r'<script\s+async\s+src="?https://platform\.twitter\.com/widgets\.js"?')
        self.assertIn("unavailable", self.page)
        self.assertIn("twitter-tweet", self.page)
        self.assertIn("640px", self.css)
        self.assertIn("560px", self.css)
        self.assertNotIn("aspect-ratio: 16 / 10", self.css)
        self.assertNotIn("pbs.twimg.com", den)
        self.assertNotIn("video.twimg.com", den)
        self.assertNotIn("syndication.twitter.com", den)

    def test_deleted_and_offlist_keep_svg_visible(self):
        deleted = self.blocks["deleted-pin"]
        offlist = self.blocks["offlist-pin"]
        self.assertIn("preview-away-den.svg", deleted)
        self.assertIn("View post on X", deleted)
        self.assertIn(DELETED_URL, deleted)
        self.assertIn("nrt-hero-media__fallback", deleted)
        self.assertRegex(offlist, r'data-hero-kind="?article-illustration"?')
        self.assertIn("preview-away-den.svg", offlist)
        self.assertNotIn("twitter-tweet", offlist)

    def test_hugo_week_opponent_check_does_not_take_denver_for_seattle(self):
        mismatch = self.blocks["week-mismatch"]
        self.assertIn("preview-away-sea.svg", mismatch)
        self.assertNotIn("preview-away-den.svg", mismatch)

    def test_hero_partial_only_mentions_platform_twitter(self):
        media = (ROOT / "layouts" / "partials" / "nrt-hero-media.html").read_text(encoding="utf-8")
        self.assertIn("https://platform.twitter.com/widgets.js", media)
        self.assertIn("nrt-event-illustration.html", media)
        self.assertNotIn("cdn.jsdelivr", media)
        self.assertNotIn("googletagmanager", media)
        self.assertIn('partial "nrt-hero-post.html"', media)


class HeroPostGenerateHook(unittest.TestCase):
    def test_generate_writes_sidecar_after_edition_payload(self):
        src = (ROOT / "tools" / "chiefs_narrative" / "generate.py").read_text(encoding="utf-8")
        write_at = src.find("_write_edition_payload(narrative)")
        attach_at = src.find("hero_posts.attach_sidecar(narrative)")
        self.assertGreater(write_at, 0)
        self.assertGreater(attach_at, write_at)
        self.assertIn("issues.extend(hero_posts.validate_sidecar())", src)
        workflow = (ROOT / ".github" / "workflows" / "narrative.yml").read_text(encoding="utf-8")
        self.assertGreaterEqual(workflow.count("data/hero_posts.json"), 2)
