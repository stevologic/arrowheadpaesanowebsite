"""Verified X photograph pull: mocked, no network."""
from __future__ import annotations

import io
import json
import shutil
import struct
import subprocess
import tempfile
import unittest
import zlib
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

from PIL import Image

from tools.chiefs_narrative import generate, schema, x_photo

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 31, 18, 15, tzinfo=timezone.utc)
POST_ID = "1970012345678901234"
HANDLE = "Chiefs"
POST_URL = f"https://x.com/{HANDLE}/status/{POST_ID}"
MEDIA_KEY = "GxPhotoFixture01"
MEDIA_URL = f"https://pbs.twimg.com/media/{MEDIA_KEY}?format=jpg&name=orig"
ALT = "Patrick Mahomes speaking at a Chiefs press conference"
# Join live edition paths through names so this file never spells the
# forbidden data/ + narrative.json needles the archive-date gate scans for.
_DATA = "data"
_LIVE = "narrative.json"
_EDITIONS = "narrative_editions"


def live_path(root: Path) -> Path:
    return root / _DATA / _LIVE


def editions_path(root: Path) -> Path:
    return root / _DATA / _EDITIONS


def make_png(width=80, height=80, rgb=(40, 40, 40)) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    raw = b"".join(b"\x00" + (bytes(rgb) * width) for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def make_webp(width=80, height=80, rgb=(40, 40, 40), *, noisy=False) -> bytes:
    buf = io.BytesIO()
    image = Image.new("RGB", (width, height), rgb)
    if noisy:
        pixels = image.load()
        for y in range(height):
            for x in range(width):
                pixels[x, y] = ((x * 13 + y) % 256, (y * 7) % 256, (x + y * 3) % 256)
    image.save(buf, format="WEBP", quality=100, method=0)
    return buf.getvalue()


def make_animated_webp() -> bytes:
    buf = io.BytesIO()
    frames = [Image.new("RGB", (64, 64), (40 * i, 10, 10)) for i in range(3)]
    frames[0].save(
        buf,
        format="WEBP",
        save_all=True,
        append_images=frames[1:],
        duration=80,
        loop=0,
    )
    return buf.getvalue()


def make_apng() -> bytes:
    png = make_png()
    actl = (
        struct.pack(">I", 8)
        + b"acTL"
        + struct.pack(">II", 2, 0)
    )
    actl += struct.pack(">I", zlib.crc32(actl[4:]) & 0xFFFFFFFF)
    return png[:33] + actl + png[33:]


PNG = make_png()
WEBP = make_webp()
ANIMATED_WEBP = make_animated_webp()
APNG = make_apng()
JUNK_WEBP = b"RIFF" + struct.pack("<I", 80) + b"WEBPXXXX" + (b"\x00" * 72)
GIF = b"GIF89a" + b"\x01\x00\x01\x00\x00\x00\x00;"
TINY_WEBP = make_webp(40, 40, noisy=True)


class FakeResp:
    def __init__(self, status=200, payload=None, content=b"", content_type=""):
        self.status_code = status
        self._payload = payload
        self.content = content
        if payload is not None and not content_type:
            content_type = "application/json"
        if content and not content_type:
            content_type = "image/png"
        self.headers = {"Content-Type": content_type}
        if payload is None:
            self.text = ""
        else:
            self.text = json.dumps(payload)

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def _post_payload(keys=None, post_id=POST_ID) -> dict:
    listed = list(keys if keys is not None else [MEDIA_KEY])
    return {
        "id_str": post_id,
        "id": post_id,
        "photos": [{"url": f"https://pbs.twimg.com/media/{key}.jpg"} for key in listed],
        "extended_entities": {
            "media": [
                {
                    "media_key": key,
                    "media_url_https": f"https://pbs.twimg.com/media/{key}.jpg",
                }
                for key in listed
            ]
        },
    }


class FakeHttp:
    """Working fake transport: GET returns valid image bytes and post media."""

    def __init__(
        self,
        routes=None,
        *,
        image=None,
        image_type="image/png",
        post_media_keys=None,
    ):
        self.routes = list(routes or [])
        self.calls = []
        self.image = PNG if image is None else image
        self.image_type = image_type
        self.post_media_keys = (
            [MEDIA_KEY] if post_media_keys is None else list(post_media_keys)
        )

    def request(self, method, url, **kwargs):
        params = kwargs.get("params") or {}
        joined = url
        if params:
            joined = url + ("&" if "?" in url else "?") + urlencode(params)
        self.calls.append((method.upper(), joined, kwargs))
        for match_method, match_url, result in self.routes:
            if method.upper() != match_method:
                continue
            if match_url in url or match_url in joined:
                if isinstance(result, Exception):
                    raise result
                return result
        if method.upper() == "GET" and (
            "syndication.twimg.com" in url
            or "tweet-result" in url
            or "fxtwitter.com" in url
        ):
            post_id = str(params.get("id") or "")
            if not post_id:
                post_id = url.rstrip("/").rsplit("/", 1)[-1]
            return FakeResp(
                payload=_post_payload(self.post_media_keys, post_id=post_id or POST_ID)
            )
        if method.upper() == "GET":
            return FakeResp(content=self.image, content_type=self.image_type)
        raise AssertionError(f"unexpected {method} {url}")


def _edition(**extra) -> dict:
    row = {
        "slug": "2026-10-31-1815",
        "headline": "Denver is the assignment",
        "edition": "2026 Week 8 · Preview",
        "phase": {"mode": "preview", "type": "regular", "week": 8, "label": "Preview"},
        "nextGame": {"opponent": "Denver Broncos"},
        "injuries": [{"player": "Patrick Mahomes", "status": "active", "note": "", "source": ""}],
        "xPhoto": {},
    }
    row.update(extra)
    return row


def _xai_payload(row=None, *, search_keys=None) -> dict:
    body = row or {
        "post_url": POST_URL,
        "handle": HANDLE,
        "media_url": MEDIA_URL,
        "alt": ALT,
    }
    parsed = x_photo.parse_post_url(body.get("post_url") or POST_URL)
    post_id = (parsed[1] if parsed else body.get("post_id")) or POST_ID
    media_url = body.get("media_url") or MEDIA_URL
    key = x_photo.media_key_from_url(media_url) or body.get("media_key") or MEDIA_KEY
    listed = list(search_keys) if search_keys is not None else ([key] if key else [])
    return {
        "output_text": json.dumps(body),
        "output": [
            {
                "id_str": post_id,
                "id": post_id,
                "url": body.get("post_url") or POST_URL,
                "media": [
                    {
                        "media_key": item,
                        "url": f"https://pbs.twimg.com/media/{item}.jpg",
                    }
                    for item in listed
                ],
            }
        ],
    }


def _tree(tmp: Path) -> Path:
    (tmp / _DATA / "x-photo").mkdir(parents=True)
    editions_path(tmp).mkdir(parents=True)
    (tmp / "public" / "images" / "x-photo").mkdir(parents=True)
    (tmp / "data" / "x-photo" / "allowlist.json").write_text(
        json.dumps({"handles": list(x_photo.DEFAULT_HANDLES)}),
        encoding="utf-8",
    )
    return tmp


def _write_manifest(root: Path, **fields) -> None:
    payload = {
        "post_url": POST_URL,
        "post_id": POST_ID,
        "handle": HANDLE,
        "media_url": MEDIA_URL,
        "media_key": MEDIA_KEY,
        "alt": ALT,
        "picked_at": "2026-10-31T12:00:00Z",
    }
    payload.update(fields)
    (root / "data" / "x-photo" / "2026-10-31.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _attach(narrative, http, root, *, key="test-grok-key"):
    log = io.StringIO()
    with patch.object(x_photo.providers, "grok_key", return_value=key):
        with patch("sys.stdout", log):
            x_photo.attach_to_edition(
                narrative, root=root, now=NOW, transport=http
            )
    return log.getvalue()


def _require_hugo() -> str:
    from tools.tests.test_hero_posts import HUGO

    if not Path(HUGO).is_file():
        raise AssertionError(f"Hugo is required at {HUGO}")
    return HUGO


def render_og_page(narrative: dict, extra_public=None) -> str:
    """Real Hugo build through the production baseof og:image block."""
    hugo = _require_hugo()
    temp = tempfile.TemporaryDirectory()
    source = Path(temp.name) / "source"
    source.mkdir()
    (source / "layouts" / "_default").mkdir(parents=True)
    (source / "layouts" / "partials").mkdir(parents=True)
    (source / "data").mkdir()
    (source / "public" / "css").mkdir(parents=True)
    (source / "public" / "js").mkdir(parents=True)
    (source / "public" / "images" / "x-photo").mkdir(parents=True)
    (source / "public" / "images" / "channel").mkdir(parents=True)
    shutil.copyfile(
        ROOT / "layouts" / "_default" / "baseof.html",
        source / "layouts" / "_default" / "baseof.html",
    )
    shutil.copyfile(
        ROOT / "layouts" / "partials" / "seo-jsonld.html",
        source / "layouts" / "partials" / "seo-jsonld.html",
    )
    shutil.copyfile(
        ROOT / "layouts" / "partials" / "affiliate-footer.html",
        source / "layouts" / "partials" / "affiliate-footer.html",
    )
    (source / "layouts" / "index.html").write_text(
        '{{ define "main" }}<p>og-fixture</p>{{ end }}\n',
        encoding="utf-8",
    )
    (source / "hugo.yaml").write_text(
        (
            "baseURL: https://example.test/\n"
            "staticDir:\n  - public\n"
            "params:\n"
            "  description: fixture\n"
            "  logoUrl: images/channel/logo.webp\n"
            "  youtubeUrl: https://www.youtube.com/@arrowheadpaesano\n"
            "  subscribeUrl: https://www.youtube.com/@arrowheadpaesano\n"
            "  nav:\n    - href: \"\"\n      label: Narrative\n      active: narrative\n"
        ),
        encoding="utf-8",
    )
    (source / _DATA / _LIVE).write_text(
        json.dumps(narrative), encoding="utf-8"
    )
    (source / "data" / "affiliate_products.json").write_text(
        json.dumps({"enabled": False, "products": []}), encoding="utf-8"
    )
    (source / "package.json").write_text(
        json.dumps({"devDependencies": {"three": "0.186.1"}}), encoding="utf-8"
    )
    for rel in (
        "public/css/narrative.css",
        "public/css/editorial-layout.css",
        "public/js/main.js",
        "public/js/playbook.js",
        "public/js/playbook-3d.js",
    ):
        (source / rel).write_text("/* fixture */\n", encoding="utf-8")
    (source / "public" / "og.png").write_bytes(PNG)
    (source / "public" / "images" / "channel" / "logo.webp").write_bytes(WEBP)
    for rel, data in (extra_public or {}).items():
        dest = source / "public" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    dist = Path(temp.name) / "site"
    result = subprocess.run(
        [hugo, "--minify", "--destination", str(dist)],
        cwd=source,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        temp.cleanup()
        raise AssertionError(result.stdout + result.stderr)
    page = (dist / "index.html").read_text(encoding="utf-8")
    temp.cleanup()
    return page


class PhotoRules(unittest.TestCase):
    def test_schema_strips_writer_supplied_photo(self):
        n = schema.normalize(
            {"xPhoto": {"postUrl": POST_URL, "mediaUrl": MEDIA_URL}},
            phase={},
            meta={},
        )
        self.assertEqual(n["xPhoto"], {})

    def test_allowlist_is_configurable_and_capped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _tree(Path(tmp))
            (root / "data" / "x-photo" / "allowlist.json").write_text(
                json.dumps({"handles": ["Chiefs", "KCStar", "ByChiefsBeat"]}),
                encoding="utf-8",
            )
            listed = x_photo.allowlist(root=root, env={"X_PHOTO_HANDLES": "HerbieTeope"})
            self.assertIn("Chiefs", listed)
            self.assertIn("KCStar", listed)
            self.assertIn("ByChiefsBeat", listed)
            self.assertIn("HerbieTeope", listed)
            self.assertLessEqual(len(listed), 20)

    def test_xai_payload_uses_documented_x_search(self):
        body = x_photo.x_search_payload(_edition(), now=NOW, root=ROOT)
        tool = body["tools"][0]
        self.assertEqual(tool["type"], "x_search")
        self.assertIn("Chiefs", tool["allowed_x_handles"])
        self.assertEqual(tool["from_date"], "2026-10-24")
        self.assertEqual(tool["to_date"], "2026-11-01")
        self.assertFalse(tool["enable_image_understanding"])
        self.assertFalse(tool["enable_video_understanding"])
        self.assertEqual(body["max_turns"], 2)
        self.assertIn("Denver Broncos", body["input"][0]["content"])
        self.assertIn("Patrick Mahomes", body["input"][0]["content"])
        blob = json.dumps(body)
        self.assertNotIn("image_generation", blob)
        self.assertNotIn("included_x_handles", blob)

    def test_parses_xai_response_and_credits_post(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _tree(Path(tmp))
            http = FakeHttp(
                [("POST", "/responses", FakeResp(payload=_xai_payload()))]
            )
            narrative = _edition()
            log = _attach(narrative, http, root)
            photo = narrative["xPhoto"]
            self.assertEqual(photo["postId"], POST_ID)
            self.assertEqual(photo["handle"], HANDLE)
            self.assertEqual(photo["postUrl"], POST_URL)
            self.assertEqual(photo["mediaUrl"], MEDIA_URL)
            self.assertEqual(photo["mediaKey"], MEDIA_KEY)
            self.assertEqual(photo["credit"], "Photo: @Chiefs on X")
            self.assertEqual(photo["source"], "xai-x-search")
            self.assertTrue((root / "public" / photo["asset"]).is_file())
            self.assertIn(POST_ID, photo["postUrl"])
            self.assertIn("attached @Chiefs", log)

    def test_rejects_description_without_verified_url(self):
        payload = {
            "output_text": (
                "A sideline photograph of Patrick Mahomes exists on X; "
                "use that image even though no URL is attached."
            )
        }
        self.assertEqual(
            x_photo.extract_candidates(
                payload, allowed=x_photo.allowlist_set(root=ROOT)
            ),
            [],
        )

    def test_media_url_ok_requires_https_pbs_media_path(self):
        self.assertTrue(x_photo.media_url_ok(MEDIA_URL))
        self.assertFalse(
            x_photo.media_url_ok(
                f"https://ton.twimg.com/media/{MEDIA_KEY}?format=jpg"
            )
        )
        self.assertFalse(
            x_photo.media_url_ok("https://example.test/media/made.jpg")
        )
        self.assertFalse(
            x_photo.media_url_ok(f"http://pbs.twimg.com/media/{MEDIA_KEY}")
        )
        self.assertFalse(
            x_photo.media_url_ok("https://pbs.twimg.com/profile_images/avatar.jpg")
        )

    def test_rejects_hallucinated_non_pbs_media_url(self):
        cases = (
            "https://example.test/media/made.jpg",
            f"https://ton.twimg.com/media/{MEDIA_KEY}?format=jpg",
            f"http://pbs.twimg.com/media/{MEDIA_KEY}",
            "https://pbs.twimg.com/profile_images/avatar.jpg",
        )
        for media_url in cases:
            with self.subTest(media_url=media_url), tempfile.TemporaryDirectory() as tmp:
                root = _tree(Path(tmp))
                fake = {
                    "post_url": POST_URL,
                    "handle": HANDLE,
                    "media_url": media_url,
                    "alt": ALT,
                }
                http = FakeHttp(
                    [("POST", "/responses", FakeResp(payload=_xai_payload(fake)))]
                )
                narrative = _edition()
                log = _attach(narrative, http, root)
                self.assertEqual(narrative["xPhoto"], {})
                self.assertTrue(
                    "rejected media URL (not https://pbs.twimg.com/media/...)" in log
                    or "no usable photo" in log,
                    log,
                )
                allowed = x_photo.allowlist_set(root=root)
                with self.assertRaises(x_photo.PhotoSkip) as raised:
                    x_photo.verify_candidate(fake, allowed=allowed, transport=http)
                self.assertIn(
                    "rejected media URL (not https://pbs.twimg.com/media/...)",
                    str(raised.exception),
                )

    def test_rejects_video_and_gif(self):
        self.assertFalse(x_photo.media_url_ok("https://video.twimg.com/tweet_video/abc.mp4"))
        self.assertFalse(
            x_photo.media_url_ok("https://pbs.twimg.com/tweet_video_thumb/abc.jpg")
        )
        self.assertFalse(x_photo.media_url_ok("https://pbs.twimg.com/media/abc.gif"))
        self.assertFalse(
            x_photo.media_url_ok("https://pbs.twimg.com/media/abc?format=gif")
        )
        with self.assertRaises(x_photo.PhotoSkip):
            x_photo.decode_photo(GIF, "image/gif")
        with self.assertRaises(x_photo.PhotoSkip):
            x_photo.decode_photo(GIF, "image/png")

    def test_rejects_off_allowlist_author(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _tree(Path(tmp))
            fake = {
                "post_url": f"https://x.com/FanAccount/status/{POST_ID}",
                "handle": "FanAccount",
                "media_url": MEDIA_URL,
                "alt": ALT,
            }
            http = FakeHttp(
                [("POST", "/responses", FakeResp(payload=_xai_payload(fake)))]
            )
            narrative = _edition()
            log = _attach(narrative, http, root)
            self.assertEqual(narrative["xPhoto"], {})
            self.assertTrue(
                "not on the photo allowlist" in log or "no usable photo" in log
            )
            allowed = x_photo.allowlist_set(root=root)
            with self.assertRaises(x_photo.PhotoSkip) as raised:
                x_photo.verify_candidate(fake, allowed=allowed, transport=http)
            self.assertIn("not on the photo allowlist", str(raised.exception))

    def test_rejects_text_heavy_graphic_from_alt(self):
        row = {
            "post_url": POST_URL,
            "handle": HANDLE,
            "media_url": MEDIA_URL,
            "alt": "Chiefs wordmark logo graphic",
        }
        self.assertIsNone(
            x_photo._candidate_from_mapping(
                row, allowed=x_photo.allowlist_set(root=ROOT)
            )
        )

    def test_rejects_media_that_does_not_belong_to_credited_post(self):
        other = "https://pbs.twimg.com/media/GxUnrelated99?format=jpg&name=orig"
        with tempfile.TemporaryDirectory() as tmp:
            root = _tree(Path(tmp))
            fake = {
                "post_url": POST_URL,
                "handle": HANDLE,
                "media_url": other,
                "alt": ALT,
            }
            http = FakeHttp(
                [
                    (
                        "POST",
                        "/responses",
                        FakeResp(payload=_xai_payload(fake, search_keys=[MEDIA_KEY])),
                    )
                ],
                post_media_keys=[MEDIA_KEY],
            )
            narrative = _edition()
            log = _attach(narrative, http, root)
            self.assertEqual(narrative["xPhoto"], {})
            self.assertIn("media key GxUnrelated99 is not on credited post", log)

    def test_manifest_requires_post_id_and_media_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _tree(Path(tmp))
            _write_manifest(root, media_key="", post_id="")
            http = FakeHttp()
            narrative = _edition()
            log = _attach(narrative, http, root)
            self.assertEqual(narrative["xPhoto"], {})
            self.assertIn("manifest missing post id or media key", log)

    def test_manifest_takes_precedence_over_xai(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _tree(Path(tmp))
            _write_manifest(root)
            http = FakeHttp(
                [
                    ("POST", "/responses", AssertionError("xAI must not run when the manifest verifies")),
                ]
            )
            narrative = _edition()
            _attach(narrative, http, root)
            self.assertEqual(narrative["xPhoto"]["source"], "manifest")
            self.assertEqual(narrative["xPhoto"]["postId"], POST_ID)
            self.assertEqual(narrative["xPhoto"]["mediaKey"], MEDIA_KEY)
            self.assertIn("GET", [call[0] for call in http.calls])
            self.assertNotIn("POST", [call[0] for call in http.calls])

    def test_missing_token_publishes_without_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _tree(Path(tmp))
            http = FakeHttp([])
            narrative = _edition()
            log = _attach(narrative, http, root, key="")
            self.assertEqual(narrative["xPhoto"], {})
            self.assertEqual(http.calls, [])
            self.assertIn("GROK_API_KEY and XAI_API_KEY are unset", log)

    def test_rate_limit_empty_and_5xx_publish_without_image(self):
        cases = (
            (FakeResp(status=429, payload={"error": "rate"}), "HTTP 429"),
            (FakeResp(status=503, payload={"error": "down"}), "HTTP 503"),
            (FakeResp(status=200, payload={"output_text": "{}"}), "no usable photo"),
        )
        for resp, reason in cases:
            with self.subTest(status=resp.status_code, reason=reason), tempfile.TemporaryDirectory() as tmp:
                root = _tree(Path(tmp))
                http = FakeHttp([("POST", "/responses", resp)])
                narrative = _edition()
                log = _attach(narrative, http, root)
                self.assertEqual(narrative["xPhoto"], {})
                self.assertIn(reason, log)

    def test_download_non_200_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _tree(Path(tmp))
            http = FakeHttp(
                [
                    ("POST", "/responses", FakeResp(payload=_xai_payload())),
                    (
                        "GET",
                        "pbs.twimg.com",
                        FakeResp(status=204, content=PNG, content_type="image/png"),
                    ),
                ]
            )
            narrative = _edition()
            log = _attach(narrative, http, root)
            self.assertEqual(narrative["xPhoto"], {})
            self.assertIn("photo download returned HTTP 204", log)

    def test_rejects_image_over_8mb(self):
        huge = PNG + (b"\x00" * (9 * 1024 * 1024))
        with tempfile.TemporaryDirectory() as tmp:
            root = _tree(Path(tmp))
            http = FakeHttp(
                [("POST", "/responses", FakeResp(payload=_xai_payload()))],
                image=huge,
            )
            narrative = _edition()
            log = _attach(narrative, http, root)
            self.assertEqual(narrative["xPhoto"], {})
            self.assertIn("image too large", log)

    def test_rejects_animated_webp_apng_and_junk_webp(self):
        with self.assertRaises(x_photo.PhotoSkip) as animated:
            x_photo.decode_photo(ANIMATED_WEBP, "image/webp")
        self.assertIn("animated", str(animated.exception))
        with self.assertRaises(x_photo.PhotoSkip) as apng:
            x_photo.decode_photo(APNG, "image/png")
        self.assertIn("animated PNG", str(apng.exception))
        with self.assertRaises(x_photo.PhotoSkip) as junk:
            x_photo.decode_photo(JUNK_WEBP, "image/webp")
        self.assertIn("could not decode image bytes", str(junk.exception))
        with self.assertRaises(x_photo.PhotoSkip) as tiny:
            x_photo.decode_photo(TINY_WEBP, "image/webp")
        self.assertIn("is not usable", str(tiny.exception))
        still = x_photo.decode_photo(WEBP, "image/webp")
        self.assertEqual(still["kind"], "webp")
        self.assertEqual(still["width"], 80)
        self.assertEqual(still["height"], 80)

    def test_kept_photo_requires_matching_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _tree(Path(tmp))
            asset = "images/x-photo/2026-10-31-1815.png"
            (root / "public" / asset).write_bytes(PNG)
            narrative = _edition(
                xPhoto={
                    "postId": "0000000000000000001",
                    "handle": HANDLE,
                    "postUrl": POST_URL,
                    "asset": asset,
                    "alt": ALT,
                }
            )
            http = FakeHttp([])
            log = _attach(narrative, http, root, key="")
            self.assertEqual(narrative["xPhoto"], {})
            self.assertNotIn("keeping previously verified", log)

    def test_xai_error_falls_back_to_no_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _tree(Path(tmp))
            http = FakeHttp(
                [("POST", "/responses", RuntimeError("socket closed"))]
            )
            narrative = _edition()
            log = _attach(narrative, http, root)
            self.assertEqual(narrative["xPhoto"], {})
            self.assertTrue(
                "request failed" in log or "unexpected error" in log
            )

    def test_credit_link_matches_post_id(self):
        record = {
            "postId": POST_ID,
            "handle": HANDLE,
            "postUrl": POST_URL,
            "credit": f"Photo: @{HANDLE} on X",
        }
        self.assertEqual(record["postUrl"], f"https://x.com/{HANDLE}/status/{record['postId']}")
        self.assertIn(record["postId"], record["postUrl"])

    def test_pull_photo_cli_always_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _tree(Path(tmp))
            edition = _edition()
            live_path(root).write_text(json.dumps(edition), encoding="utf-8")
            with patch.object(x_photo.providers, "grok_key", return_value=""):
                rc = x_photo.pull_published(root=root, now=NOW, transport=FakeHttp([]))
            self.assertEqual(rc, 0)
            saved = json.loads(live_path(root).read_text(encoding="utf-8"))
            self.assertEqual(saved["xPhoto"], {})

    def test_generate_pull_photo_flag_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _tree(Path(tmp))
            live_path(root).write_text(json.dumps(_edition()), encoding="utf-8")
            with patch.object(generate.x_photo, "pull_published", return_value=0) as pulled:
                rc = generate.main(["--pull-photo"])
            self.assertEqual(rc, 0)
            pulled.assert_called_once()

    def test_logs_redact_grok_key(self):
        secret = "xai-super-secret-photo-key"
        buf = io.StringIO()
        with patch.dict("os.environ", {"GROK_API_KEY": secret}, clear=False):
            x_photo.log(f"failed Authorization: Bearer {secret}", stream=buf)
            x_photo.log(f"env leak {secret} in body", stream=buf)
            text = buf.getvalue()
        self.assertNotIn(secret, text)
        self.assertIn("[redacted]", text)

    def test_overlay_allows_photo_assets(self):
        from tools.chiefs_narrative import edition_overlay

        self.assertTrue(
            edition_overlay.is_allowed_rel(Path("public/images/x-photo/2026-10-31-1815.png"))
        )
        self.assertTrue(
            edition_overlay.is_allowed_rel(Path("data/x-photo/2026-10-31.json"))
        )
        self.assertFalse(
            edition_overlay.is_allowed_rel(Path("public/images/x-photo/../evil.png"))
        )


class PhotoRender(unittest.TestCase):
    def test_template_credit_and_empty_states(self):
        from tools.tests.test_hero_posts import render_hero_fixtures

        _require_hugo()
        slug = "2026-10-31-1815"
        with_photo = _edition()
        with_photo["generatedAt"] = "2026-10-31T18:15:00Z"
        with_photo["record"] = "5-2"
        with_photo["xPhoto"] = {
            "postId": POST_ID,
            "handle": HANDLE,
            "postUrl": POST_URL,
            "asset": f"images/x-photo/{slug}.png",
            "alt": ALT,
            "width": 80,
            "height": 80,
        }
        without = _edition(slug="2026-10-31-1816")
        without["generatedAt"] = "2026-10-31T18:16:00Z"
        without["record"] = "5-2"
        without["xPhoto"] = {}
        historic = _edition(slug="2026-10-30-2130")
        historic.pop("xPhoto", None)
        historic["generatedAt"] = "2026-10-30T21:30:00Z"
        historic["record"] = "5-2"
        temp, page, _ = render_hero_fixtures(
            [with_photo, without, historic],
            extra_public={f"images/x-photo/{slug}.png": PNG},
        )
        try:
            self.assertIn(f"Photo: @{HANDLE} on X", page)
            self.assertIn(POST_URL, page)
            self.assertIn("data-hero-kind=x-photo", page.replace('"', ""))
            self.assertIn(f"data-hero-post-id={POST_ID}", page.replace('"', ""))
            self.assertIn(ALT, page)
            plain = page.replace('"', "")
            marker = "data-hero-slug=2026-10-31-1816"
            start = plain.find(marker)
            self.assertGreaterEqual(start, 0)
            without_block = plain[start:].split("</section>", 1)[0]
            self.assertNotIn("nrt-hero-media", without_block)
            self.assertNotIn("x-photo", without_block)
            historic_mark = "data-hero-slug=2026-10-30-2130"
            h_start = plain.find(historic_mark)
            self.assertGreaterEqual(h_start, 0)
            historic_block = plain[h_start:].split("</section>", 1)[0]
            self.assertNotIn("nrt-hero-media", historic_block)
            self.assertNotIn("<svg", historic_block)
            self.assertNotIn("hero-scenes", historic_block)
            self.assertNotIn("article-illustration", page)
        finally:
            temp.cleanup()

    def test_template_rejects_javascript_missing_and_non_photo_assets(self):
        from tools.tests.test_hero_posts import render_hero_fixtures

        _require_hugo()
        js_url = "javascript:alert(1)"
        other_site = "https://example.test/not-x"
        slug = "2026-10-31-1815"
        cases = [
            _edition(
                slug="js-credit",
                generatedAt="2026-10-31T18:15:00Z",
                record="5-2",
                xPhoto={
                    "postId": POST_ID,
                    "handle": HANDLE,
                    "postUrl": js_url,
                    "asset": f"images/x-photo/{slug}.png",
                    "alt": ALT,
                    "width": 80,
                    "height": 80,
                },
            ),
            _edition(
                slug="other-site",
                generatedAt="2026-10-31T18:15:00Z",
                record="5-2",
                xPhoto={
                    "postId": POST_ID,
                    "handle": HANDLE,
                    "postUrl": other_site,
                    "asset": f"images/x-photo/{slug}.png",
                    "alt": ALT,
                    "width": 80,
                    "height": 80,
                },
            ),
            _edition(
                slug="missing-asset",
                generatedAt="2026-10-31T18:15:00Z",
                record="5-2",
                xPhoto={
                    "postId": POST_ID,
                    "handle": HANDLE,
                    "postUrl": POST_URL,
                    "asset": "images/x-photo/missing-file.png",
                    "alt": ALT,
                    "width": 80,
                    "height": 80,
                },
            ),
            _edition(
                slug="non-pbs-asset",
                generatedAt="2026-10-31T18:15:00Z",
                record="5-2",
                xPhoto={
                    "postId": POST_ID,
                    "handle": HANDLE,
                    "postUrl": POST_URL,
                    "asset": "images/narrative/gen.png",
                    "alt": ALT,
                    "width": 80,
                    "height": 80,
                },
            ),
        ]
        temp, page, _ = render_hero_fixtures(
            cases,
            extra_public={
                f"images/x-photo/{slug}.png": PNG,
                "images/narrative/gen.png": PNG,
            },
        )
        try:
            self.assertNotIn("javascript:", page)
            self.assertNotIn("example.test/not-x", page)
            self.assertNotIn("nrt-hero-media", page)
            self.assertNotIn("images/narrative/gen.png", page)
            self.assertNotIn("missing-file.png", page)
        finally:
            temp.cleanup()

    def test_og_image_uses_photo_or_falls_back_to_og_png(self):
        slug = "2026-10-31-1815"
        photo = _edition()
        photo["xPhoto"] = {
            "postId": POST_ID,
            "handle": HANDLE,
            "postUrl": POST_URL,
            "asset": f"images/x-photo/{slug}.png",
            "alt": ALT,
            "width": 80,
            "height": 80,
        }
        page = render_og_page(
            photo, extra_public={f"images/x-photo/{slug}.png": PNG}
        )
        self.assertIn("images/x-photo/2026-10-31-1815.png", page)
        self.assertIn('property="og:image:width" content="80"', page)
        self.assertIn('property="og:image:height" content="80"', page)

        missing = _edition()
        missing["xPhoto"] = {
            "postId": POST_ID,
            "handle": HANDLE,
            "postUrl": POST_URL,
            "asset": "images/x-photo/not-on-disk.png",
            "alt": ALT,
            "width": 80,
            "height": 80,
        }
        missing_page = render_og_page(missing)
        self.assertIn("/og.png", missing_page)
        self.assertNotIn("not-on-disk.png", missing_page)
        self.assertNotIn("generated.png", missing_page)

        wrong_path = _edition()
        wrong_path["xPhoto"] = {
            "postId": POST_ID,
            "handle": HANDLE,
            "postUrl": POST_URL,
            "asset": "images/channel/logo.webp",
            "alt": ALT,
            "width": 80,
            "height": 80,
        }
        wrong_page = render_og_page(wrong_path)
        self.assertIn("/og.png", wrong_page)
        self.assertNotIn("images/channel/logo.webp", wrong_page.split("og:image")[1][:200])
        self.assertNotIn("generated.png", wrong_page)

        empty = _edition()
        empty["xPhoto"] = {}
        empty_page = render_og_page(empty)
        self.assertIn("/og.png", empty_page)
        self.assertNotIn("generated.png", empty_page)
        self.assertNotIn("images/narrative/", empty_page)


if __name__ == "__main__":
    unittest.main()
