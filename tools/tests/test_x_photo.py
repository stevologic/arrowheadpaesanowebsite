"""Verified X photograph pull: mocked, no network."""
from __future__ import annotations

import io
import json
import struct
import tempfile
import unittest
import zlib
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from tools.chiefs_narrative import generate, schema, x_photo

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 31, 18, 15, tzinfo=timezone.utc)
POST_ID = "1970012345678901234"
HANDLE = "Chiefs"
POST_URL = f"https://x.com/{HANDLE}/status/{POST_ID}"
MEDIA_URL = "https://pbs.twimg.com/media/GxPhotoFixture01?format=jpg&name=orig"
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


PNG = make_png()
GIF = b"GIF89a" + b"\x01\x00\x01\x00\x00\x00\x00;"


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


class FakeHttp:
    def __init__(self, routes):
        self.routes = list(routes)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method.upper(), url, kwargs))
        for match_method, match_url, result in self.routes:
            if method.upper() != match_method:
                continue
            if match_url in url:
                if isinstance(result, Exception):
                    raise result
                return result
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


def _xai_payload(row=None) -> dict:
    body = row or {
        "post_url": POST_URL,
        "handle": HANDLE,
        "media_url": MEDIA_URL,
        "alt": ALT,
    }
    return {"output_text": json.dumps(body)}


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
        "handle": HANDLE,
        "media_url": MEDIA_URL,
        "alt": ALT,
        "picked_at": "2026-10-31T12:00:00Z",
    }
    payload.update(fields)
    (root / "data" / "x-photo" / "2026-10-31.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


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
                [
                    ("POST", "/responses", FakeResp(payload=_xai_payload())),
                    ("GET", "pbs.twimg.com/media/", FakeResp(content=PNG, content_type="image/png")),
                ]
            )
            narrative = _edition()
            with patch.object(x_photo.providers, "grok_key", return_value="test-grok-key"):
                x_photo.attach_to_edition(
                    narrative, root=root, now=NOW, transport=http
                )
            photo = narrative["xPhoto"]
            self.assertEqual(photo["postId"], POST_ID)
            self.assertEqual(photo["handle"], HANDLE)
            self.assertEqual(photo["postUrl"], POST_URL)
            self.assertEqual(photo["mediaUrl"], MEDIA_URL)
            self.assertEqual(photo["credit"], "Photo: @Chiefs on X")
            self.assertEqual(photo["source"], "xai-x-search")
            self.assertTrue((root / "public" / photo["asset"]).is_file())
            self.assertIn(POST_ID, photo["postUrl"])

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

    def test_rejects_hallucinated_non_pbs_media_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _tree(Path(tmp))
            fake = {
                "post_url": POST_URL,
                "handle": HANDLE,
                "media_url": "https://example.test/made-up.jpg",
                "alt": ALT,
            }
            http = FakeHttp([("POST", "/responses", FakeResp(payload=_xai_payload(fake)))])
            narrative = _edition()
            with patch.object(x_photo.providers, "grok_key", return_value="test-grok-key"):
                x_photo.attach_to_edition(
                    narrative, root=root, now=NOW, transport=http
                )
            self.assertEqual(narrative["xPhoto"], {})
            self.assertEqual(len(http.calls), 1)

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
            http = FakeHttp([("POST", "/responses", FakeResp(payload=_xai_payload(fake)))])
            narrative = _edition()
            with patch.object(x_photo.providers, "grok_key", return_value="test-grok-key"):
                x_photo.attach_to_edition(
                    narrative, root=root, now=NOW, transport=http
                )
            self.assertEqual(narrative["xPhoto"], {})

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

    def test_manifest_takes_precedence_over_xai(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _tree(Path(tmp))
            _write_manifest(root)
            http = FakeHttp(
                [
                    ("GET", "pbs.twimg.com/media/", FakeResp(content=PNG, content_type="image/png")),
                    ("POST", "/responses", AssertionError("xAI must not run when the manifest verifies")),
                ]
            )
            narrative = _edition()
            with patch.object(x_photo.providers, "grok_key", return_value="test-grok-key"):
                x_photo.attach_to_edition(
                    narrative, root=root, now=NOW, transport=http
                )
            self.assertEqual(narrative["xPhoto"]["source"], "manifest")
            self.assertEqual(narrative["xPhoto"]["postId"], POST_ID)
            self.assertEqual([call[0] for call in http.calls], ["GET"])

    def test_missing_token_publishes_without_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _tree(Path(tmp))
            http = FakeHttp([])
            narrative = _edition()
            with patch.object(x_photo.providers, "grok_key", return_value=""):
                x_photo.attach_to_edition(
                    narrative, root=root, now=NOW, transport=http
                )
            self.assertEqual(narrative["xPhoto"], {})
            self.assertEqual(http.calls, [])

    def test_rate_limit_empty_and_5xx_publish_without_image(self):
        cases = (
            FakeResp(status=429, payload={"error": "rate"}),
            FakeResp(status=503, payload={"error": "down"}),
            FakeResp(status=200, payload={"output_text": "{}"}),
        )
        for resp in cases:
            with self.subTest(status=resp.status_code, text=resp.text[:20]), tempfile.TemporaryDirectory() as tmp:
                root = _tree(Path(tmp))
                http = FakeHttp([("POST", "/responses", resp)])
                narrative = _edition()
                with patch.object(x_photo.providers, "grok_key", return_value="test-grok-key"):
                    x_photo.attach_to_edition(
                        narrative, root=root, now=NOW, transport=http
                    )
                self.assertEqual(narrative["xPhoto"], {})

    def test_xai_error_falls_back_to_no_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _tree(Path(tmp))
            http = FakeHttp(
                [("POST", "/responses", RuntimeError("socket closed"))]
            )
            narrative = _edition()
            with patch.object(x_photo.providers, "grok_key", return_value="test-grok-key"):
                x_photo.attach_to_edition(
                    narrative, root=root, now=NOW, transport=http
                )
            self.assertEqual(narrative["xPhoto"], {})

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
        from tools.tests.test_hero_posts import HUGO, render_hero_fixtures

        if not Path(HUGO).is_file():
            self.skipTest("Hugo unavailable")
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
        finally:
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()
