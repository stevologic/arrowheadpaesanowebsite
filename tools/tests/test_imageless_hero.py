"""Stephen's rule: built HTML never ships synthesized person or hero art."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urljoin

from tools.chiefs_narrative import config, edition_overlay, generate, hero_media, schema

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_CLASS_OR_ELEMENT = (
    "person-media--silhouette",
    "nrt-event-illustration",
    "hero-scene",
)
NOT_A_PHOTOGRAPH = re.compile(r"not a photograph", re.I)
SLOT_CLASSES = frozenset({"person-media", "nrt-hero-media", "nrt-hero"})
MEDIA_SLOT_CLASSES = frozenset({"person-media", "nrt-hero-media"})
# Replaced/embedded hosts cannot be an empty media slot even with no children.
EMBEDDED_SLOT_TAGS = frozenset(
    {"svg", "img", "picture", "object", "embed", "canvas", "video", "iframe", "source"}
)
MEDIA_ATTRS = ("src", "data", "srcset", "poster")
NETWORK_MEDIA_MARKERS = (
    "x_search",
    "x_photo",
    "fxtwitter",
    "api.fxtwitter.com",
    "pull-photo",
    "pull_photo",
)
SHARE_IMAGE_PARTIAL = "og.png"
_HUGO_COMMENT = re.compile(r"\{\{-?\s*/\*.*?\*/\s*-?\}\}", re.S)


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


def _fail_network(*_args, **_kwargs):
    raise AssertionError("network call during media attach")


def _walk_html(dest: Path) -> list[Path]:
    found: list[Path] = []
    for dirpath, _dirnames, filenames in os.walk(dest):
        for name in filenames:
            if name.endswith(".html"):
                found.append(Path(dirpath) / name)
    return sorted(found)


def _site_base_url() -> str:
    match = re.search(r'^baseURL:\s*"([^"]+)"', (ROOT / "hugo.yaml").read_text(), re.M)
    if not match:
        raise AssertionError("hugo.yaml is missing baseURL")
    return match.group(1)


def expected_share_image_url() -> str:
    """Hugo absURL of og.png — the only allowed og:image / twitter:image."""
    return urljoin(_site_base_url(), SHARE_IMAGE_PARTIAL)


def _is_redirect_page(html: str) -> bool:
    return bool(re.search(r'http-equiv\s*=\s*["\']?refresh\b', html, re.I))


def _partial_emitted_markup(src: str) -> str:
    """What a comment-only Hugo partial emits: source minus comments, stripped."""
    return _HUGO_COMMENT.sub("", src).strip()


def _render_media_partials(binary: str) -> dict[str, str]:
    """Hugo-render person-media.html and nrt-hero-media.html in isolation."""
    with tempfile.TemporaryDirectory() as tmp:
        site = Path(tmp) / "site"
        dest = Path(tmp) / "out"
        partials = site / "layouts" / "partials"
        partials.mkdir(parents=True)
        for name in ("person-media.html", "nrt-hero-media.html"):
            shutil.copy(ROOT / "layouts" / "partials" / name, partials / name)
        (site / "layouts").mkdir(parents=True, exist_ok=True)
        (site / "layouts" / "index.html").write_text(
            "PERSON_MEDIA_BEGIN"
            '{{ partial "person-media.html" (dict "name" "Patrick Mahomes" "site" .Site) }}'
            "PERSON_MEDIA_END"
            "HERO_MEDIA_BEGIN"
            '{{ partial "nrt-hero-media.html" (dict "name" "Patrick Mahomes" "site" .Site) }}'
            "HERO_MEDIA_END"
            "SHARE_BEGIN{{ \"og.png\" | absURL }}SHARE_END\n",
            encoding="utf-8",
        )
        (site / "content").mkdir()
        (site / "content" / "_index.md").write_text(
            "---\ntitle: slot-probe\n---\n", encoding="utf-8"
        )
        (site / "hugo.yaml").write_text(
            f'baseURL: "{_site_base_url()}"\n', encoding="utf-8"
        )
        result = subprocess.run(
            [binary, "--gc", "--minify", "--destination", str(dest)],
            cwd=site,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise AssertionError(
                "hugo partial probe failed:\n" + result.stdout + result.stderr
            )
        rendered = (dest / "index.html").read_text(encoding="utf-8")
        person = rendered.split("PERSON_MEDIA_BEGIN", 1)[1].split(
            "PERSON_MEDIA_END", 1
        )[0]
        hero = rendered.split("HERO_MEDIA_BEGIN", 1)[1].split("HERO_MEDIA_END", 1)[0]
        share = rendered.split("SHARE_BEGIN", 1)[1].split("SHARE_END", 1)[0]
        return {
            "person-media.html": person,
            "nrt-hero-media.html": hero,
            "og.png": share,
        }


class _Slot:
    __slots__ = (
        "tag",
        "classes",
        "attrs",
        "text_parts",
        "child_tags",
        "comments",
        "descendant_classes",
    )

    def __init__(self, tag: str, classes: set[str], attrs: dict[str, str]) -> None:
        self.tag = tag
        self.classes = classes
        self.attrs = attrs
        self.text_parts: list[str] = []
        self.child_tags: list[str] = []
        self.comments: list[str] = []
        self.descendant_classes: set[str] = set()

    def inner_stripped(self) -> str:
        return "".join(self.text_parts).strip()

    def is_editorial_hero(self) -> bool:
        """The edition hero section, not a media slot: has nrt-hero__ children."""
        return "nrt-hero" in self.classes and any(
            name.startswith("nrt-hero__") for name in self.descendant_classes
        )

    def is_media_slot(self) -> bool:
        if self.classes & MEDIA_SLOT_CLASSES:
            return True
        if "nrt-hero" in self.classes and not self.is_editorial_hero():
            return True
        return False

    def emptiness_problems(self) -> list[str]:
        """Allowlist of nothing: no children, no text, no painted media."""
        problems: list[str] = []
        label = "." + ".".join(sorted(self.classes & SLOT_CLASSES)) or self.tag
        if self.tag in EMBEDDED_SLOT_TAGS:
            problems.append(f"{label} is <{self.tag}>")
        if self.child_tags:
            problems.append(f"{label} children={self.child_tags!r}")
        if self.inner_stripped():
            problems.append(f"{label} text={self.inner_stripped()!r}")
        if any(part.strip() for part in self.comments):
            problems.append(f"{label} comments={self.comments!r}")
        style = self.attrs.get("style", "")
        if "url(" in style.lower():
            problems.append(f"{label} style paints url(): {style!r}")
        for key in MEDIA_ATTRS:
            if self.attrs.get(key):
                problems.append(f"{label} {key}={self.attrs[key]!r}")
        return problems


class _AllowlistSlotParser(HTMLParser):
    """Collect media slots and share-image metas. Slots must emit nothing."""

    _VOID = frozenset(
        {
            "img",
            "meta",
            "link",
            "br",
            "hr",
            "input",
            "source",
            "embed",
            "area",
            "col",
            "wbr",
            "track",
        }
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, _Slot | None]] = []
        self.slots: list[_Slot] = []
        self.og_images: list[str] = []
        self.twitter_images: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        ad = {str(k).lower(): str(v or "") for k, v in attrs}
        classes = set(ad.get("class", "").split())
        if tag == "meta":
            prop = ad.get("property", "").lower()
            name = ad.get("name", "").lower()
            if prop == "og:image":
                self.og_images.append(ad.get("content", ""))
            if name == "twitter:image":
                self.twitter_images.append(ad.get("content", ""))
        slot = None
        if classes & SLOT_CLASSES:
            slot = _Slot(tag, classes, ad)
            self.slots.append(slot)
        for _tag, ancestor in self.stack:
            if ancestor is None:
                continue
            ancestor.child_tags.append(tag)
            ancestor.descendant_classes.update(classes)
        if tag not in self._VOID:
            self.stack.append((tag, slot))
        elif slot is not None:
            # Void media host (<embed class="person-media">) is a finished slot.
            pass

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        for _tag, slot in self.stack:
            if slot is not None:
                slot.text_parts.append(data)

    def handle_comment(self, data: str) -> None:
        for _tag, slot in self.stack:
            if slot is not None:
                slot.comments.append(data)


def _parse_page(html: str) -> _AllowlistSlotParser:
    parser = _AllowlistSlotParser()
    parser.feed(html)
    parser.close()
    return parser


class ImagelessLiveEdition(unittest.TestCase):
    def test_every_built_page_omits_synthesized_media(self):
        """Real Hugo build: media slots emit nothing; share images are og.png."""
        binary = _hugo_bin()
        expected_share = expected_share_image_url()
        rendered = _render_media_partials(binary)
        self.assertEqual(
            rendered["og.png"],
            expected_share,
            "probe absURL og.png must match hugo.yaml baseURL",
        )
        for label in ("person-media.html", "nrt-hero-media.html"):
            self.assertEqual(
                rendered[label].strip(),
                "",
                f"rendered {label} is not exactly empty: {rendered[label]!r}",
            )
            src = (ROOT / "layouts" / "partials" / label).read_text(encoding="utf-8")
            self.assertEqual(
                _partial_emitted_markup(src),
                "",
                f"{label} source emits markup after comments: "
                f"{_partial_emitted_markup(src)!r}",
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
            pages = sorted(path for path in dest.rglob("*.html") if path.is_file())
            walked = _walk_html(dest)
            self.assertTrue(walked, "Hugo produced no HTML pages")
            self.assertEqual(pages, walked)
            self.assertGreater(len(pages), 0)
            scanned: list[Path] = []
            for path in pages:
                rel = path.relative_to(dest).as_posix()
                html = path.read_text(encoding="utf-8")
                scanned.append(path)
                with self.subTest(page=rel):
                    for token in FORBIDDEN_CLASS_OR_ELEMENT:
                        hits = _class_or_element_hits(html, token)
                        self.assertEqual(
                            hits,
                            [],
                            f"{rel} contains {token!r} as a class or element: {hits}",
                        )
                    self.assertIn(
                        "nrt-event-illustration",
                        FORBIDDEN_CLASS_OR_ELEMENT,
                    )
                    photo_hits = NOT_A_PHOTOGRAPH.findall(html)
                    self.assertEqual(
                        photo_hits,
                        [],
                        f"{rel} contains 'not a photograph': {photo_hits}",
                    )
                    parsed = _parse_page(html)
                    slot_hits: list[str] = []
                    for slot in parsed.slots:
                        if not slot.is_media_slot():
                            continue
                        slot_hits.extend(slot.emptiness_problems())
                    self.assertEqual(
                        slot_hits,
                        [],
                        f"{rel} person-media/nrt-hero-media/nrt-hero slot "
                        f"is not exactly empty: {slot_hits}",
                    )
                    self.assertTrue(parsed.og_images, f"{rel} is missing og:image")
                    if not _is_redirect_page(html):
                        self.assertTrue(
                            parsed.twitter_images,
                            f"{rel} is missing twitter:image",
                        )
                    for content in parsed.og_images:
                        self.assertEqual(
                            content,
                            expected_share,
                            f"{rel} og:image is {content!r}, want {expected_share}",
                        )
                    for content in parsed.twitter_images:
                        self.assertEqual(
                            content,
                            expected_share,
                            f"{rel} twitter:image is {content!r}, "
                            f"want {expected_share}",
                        )
            self.assertEqual(sorted(scanned), walked)
            self.assertEqual(len(scanned), len(walked))
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

    def test_generator_and_edition_produce_no_media_without_network(self):
        """Attach writes no media; any network call fails closed."""
        live = json.loads(config.NARRATIVE_JSON.read_text(encoding="utf-8"))
        attached = {"slug": live.get("slug") or "edition", "heroMediaId": "forged"}
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in {"GROK_API_KEY", "XAI_API_KEY", "X_BEARER_TOKEN"}
        }
        with patch.dict(os.environ, env, clear=True):
            with patch("requests.get", side_effect=_fail_network), patch(
                "requests.post", side_effect=_fail_network
            ), patch("urllib.request.urlopen", side_effect=_fail_network):
                hero_media.attach(attached)
                copy = dict(live)
                hero_media.attach(copy)
        self.assertEqual(attached["heroMediaId"], "none")
        self.assertEqual(copy["heroMediaId"], "none")
        self.assertEqual(copy.get("heroMediaVerifiedAt") or "", "")

        media_src = (ROOT / "tools" / "chiefs_narrative" / "hero_media.py").read_text(
            encoding="utf-8"
        )
        generate_src = (ROOT / "tools" / "chiefs_narrative" / "generate.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("import requests", media_src)
        self.assertNotIn("requests.get", media_src)
        self.assertNotIn("requests.post", media_src)
        self.assertNotIn("urlopen", media_src)
        for src in (media_src, generate_src):
            for token in NETWORK_MEDIA_MARKERS:
                self.assertNotIn(token, src, token)
        self.assertNotIn("x_photo", generate_src)
        self.assertNotIn("data/x-photo", generate_src)
        self.assertFalse(hasattr(generate, "pull_photo"))
        self.assertFalse((ROOT / "tools" / "chiefs_narrative" / "x_photo.py").exists())
        self.assertFalse((ROOT / "data" / "x-photo").exists())

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
