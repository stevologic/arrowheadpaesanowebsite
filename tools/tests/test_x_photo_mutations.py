"""Named mutants for the verified X-photo path."""
from __future__ import annotations

import importlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.chiefs_narrative import x_photo
from tools.tests.test_x_photo import (
    ALT,
    ANIMATED_WEBP,
    HANDLE,
    MEDIA_URL,
    NOW,
    PNG,
    POST_ID,
    POST_URL,
    FakeHttp,
    FakeResp,
    _edition,
    _tree,
    _write_manifest,
    _xai_payload,
    live_path,
)

ROOT = Path(__file__).resolve().parents[2]
PHOTO_SRC = ROOT / "tools" / "chiefs_narrative" / "x_photo.py"
GENERATE_SRC = ROOT / "tools" / "chiefs_narrative" / "generate.py"
MEDIA_TMPL = ROOT / "layouts" / "partials" / "nrt-hero-media.html"
BASEOF_TMPL = ROOT / "layouts" / "_default" / "baseof.html"
WORKFLOW_SRC = ROOT / ".github" / "workflows" / "narrative.yml"


def _hugo_available() -> bool:
    if shutil.which("hugo"):
        return True
    return (Path.home() / ".local" / "hugo" / "hugo").is_file()


def _require_hugo() -> None:
    if not _hugo_available():
        raise AssertionError("Hugo is required for X-photo mutation tests")


def _load_mutated(tmp: Path, name: str, filename: str, source: str):
    dest = tmp / name
    shutil.copytree(ROOT / "tools" / "chiefs_narrative", dest)
    (dest / filename).write_text(source, encoding="utf-8")
    sys.path.insert(0, str(tmp))
    try:
        return importlib.import_module(f"{name}.{filename[:-3]}")
    finally:
        sys.path.pop(0)


def _clear(name: str) -> None:
    for key in list(sys.modules):
        if key == name or key.startswith(name + "."):
            sys.modules.pop(key, None)


def _kill_wrong_credit(text: str) -> str | None:
    if 'href="{{ $photoUrl }}"' in text:
        return "survived: credit still uses the verified post URL"
    if "status/0000000000" in text:
        return None
    return "survived: credit template still looks correct"


def _kill_video(module) -> str | None:
    if module.media_url_ok("https://video.twimg.com/tweet_video/abc.mp4"):
        return None
    if module.media_url_ok("https://pbs.twimg.com/media/abc.gif"):
        return None
    return "survived: video and GIF URLs still rejected"


def _kill_hallucinated_media(module) -> str | None:
    row = module._candidate_from_mapping(
        {
            "post_url": POST_URL,
            "handle": HANDLE,
            "media_url": "https://example.test/media/made-up.jpg",
            "alt": ALT,
        },
        allowed=module.allowlist_set(root=ROOT),
    )
    if row is not None:
        return None
    if module.media_url_ok("https://example.test/media/made-up.jpg"):
        return None
    return "survived: hallucinated or non-pbs media still rejected"


def _kill_allowlist(module) -> str | None:
    parsed = module.parse_post_url(
        f"https://x.com/FanAccount/status/{POST_ID}",
        allowed=module.allowlist_set(root=ROOT),
    )
    if parsed is None:
        return "survived: off-allowlist handle still rejected"
    return None


def _kill_generation(text: str) -> str | None:
    needles = (
        "images/generations",
        "image_generation",
        "grok-imagine",
        "/v1/images",
    )
    if any(item in text for item in needles):
        return None
    return "survived: generation fallback was not reintroduced"


def _kill_missing_token_hold(module) -> str | None:
    with tempfile.TemporaryDirectory() as tmp:
        root = _tree(Path(tmp))
        live_path(root).write_text(json.dumps(_edition()), encoding="utf-8")
        try:
            with patch.object(module.providers, "grok_key", return_value=""):
                rc = module.pull_published(
                    root=root, now=NOW, transport=FakeHttp([])
                )
        except SystemExit as exc:
            if exc.code not in (0, None):
                return None
            return "survived: missing token exited 0 via SystemExit"
        if rc != 0:
            return None
        return "survived: missing token still exits 0 and does not hold"


def _kill_manifest_precedence(module) -> str | None:
    with tempfile.TemporaryDirectory() as tmp:
        root = _tree(Path(tmp))
        _write_manifest(root)
        http = FakeHttp(
            [
                ("GET", "pbs.twimg.com/media/", FakeResp(content=PNG, content_type="image/png")),
                ("POST", "/responses", FakeResp(payload=_xai_payload({
                    "post_url": f"https://x.com/NFL/status/{POST_ID}",
                    "handle": "NFL",
                    "media_url": MEDIA_URL,
                    "alt": ALT,
                }))),
            ]
        )
        narrative = _edition()
        with patch.object(module.providers, "grok_key", return_value="test-grok-key"):
            module.attach_to_edition(narrative, root=root, now=NOW, transport=http)
        source = (narrative.get("xPhoto") or {}).get("source")
        handle = (narrative.get("xPhoto") or {}).get("handle")
        if source == "manifest" and handle == HANDLE:
            return "survived: manifest still wins"
        return None


def _kill_xai_error_hold(module) -> str | None:
    with tempfile.TemporaryDirectory() as tmp:
        root = _tree(Path(tmp))
        live_path(root).write_text(json.dumps(_edition()), encoding="utf-8")
        http = FakeHttp([("POST", "/responses", RuntimeError("boom"))])
        try:
            with patch.object(module.providers, "grok_key", return_value="test-grok-key"):
                rc = module.pull_published(root=root, now=NOW, transport=http)
        except Exception:
            return None
        if rc != 0:
            return None
        saved = json.loads(live_path(root).read_text(encoding="utf-8"))
        if saved.get("xPhoto"):
            return "survived: xAI error still attached a photo"
        return "survived: xAI error still exits 0"


def _kill_illustration_fallback(text: str) -> str | None:
    if "nrt-event-illustration" in text or "article-illustration" in text:
        return None
    return "survived: illustration fallback was not restored"


def _kill_scene_partial(text: str) -> str | None:
    if "nrt-hero-scene.html" in text:
        return None
    return "survived: scene partial was not re-included"


def _kill_og_generated(text: str) -> str | None:
    if "hero-scenes" in text or "preview-fallback.svg" in text:
        return None
    return "survived: og:image still avoids generated art"


def _kill_unrelated_media(module) -> str | None:
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
                    FakeResp(payload=_xai_payload(fake, search_keys=["GxPhotoFixture01"])),
                )
            ],
            post_media_keys=["GxPhotoFixture01"],
        )
        narrative = _edition()
        with patch.object(module.providers, "grok_key", return_value="test-grok-key"):
            module.attach_to_edition(narrative, root=root, now=NOW, transport=http)
        if narrative.get("xPhoto"):
            return None
        return "survived: unrelated pbs media still rejected"


def _kill_animated(module) -> str | None:
    try:
        module.decode_photo(ANIMATED_WEBP, "image/webp")
    except module.PhotoSkip:
        return "survived: animated WebP still rejected"
    return None


def _kill_workflow_token(text: str) -> str | None:
    if "X_BEARER_TOKEN" in text or "secrets.X_BEARER_TOKEN" in text:
        return None
    if "--pull-photo" not in text or "secrets.GROK_API_KEY" not in text:
        return None
    return "survived: photo step still uses GROK_API_KEY and no X bearer"


MUTANTS = (
    {
        "name": "credit-wrong-id",
        "path": MEDIA_TMPL,
        "old": 'href="{{ $photoUrl }}"',
        "new": 'href="https://x.com/Chiefs/status/0000000000"',
        "killer": "credit link must use the verified post id",
        "kind": "hugo",
        "check": _kill_wrong_credit,
    },
    {
        "name": "video-accepted",
        "path": PHOTO_SRC,
        "old": "def media_url_ok(url: str) -> bool:\n    raw = _s(url)\n    if not raw:\n        return False\n",
        "new": "def media_url_ok(url: str) -> bool:\n    return True\n    raw = _s(url)\n    if not raw:\n        return False\n",
        "killer": "video and GIF media must stay rejected",
        "kind": "python",
        "check": _kill_video,
    },
    {
        "name": "allowlist-bypassed",
        "path": PHOTO_SRC,
        "old": "    if allowed is not None and handle.lower() not in allowed:\n        return None\n",
        "new": "    if allowed is not None and handle.lower() not in allowed:\n        pass\n",
        "killer": "off-allowlist authors must stay rejected",
        "kind": "python",
        "check": _kill_allowlist,
    },
    {
        "name": "hallucinated-media-accepted",
        "path": PHOTO_SRC,
        "old": "    if handle.lower() not in allowed or not media_url_ok(media_url):\n        return None\n",
        "new": "    if handle.lower() not in allowed:\n        return None\n",
        "killer": "hallucinated or non-pbs media URLs must stay rejected",
        "kind": "python",
        "check": _kill_hallucinated_media,
    },
    {
        "name": "generation-fallback",
        "path": PHOTO_SRC,
        "old": '        log(f"  [x-photo] {last_error}; {SKIP_PREFIX}")\n        return narrative\n',
        "new": (
            '        log(f"  [x-photo] {last_error}; falling back to images/generations")\n'
            "        narrative[\"xPhoto\"] = {\"source\": \"image_generation\"}\n"
            "        return narrative\n"
        ),
        "killer": "a failed search must not fall back to image generation",
        "kind": "text",
        "check": _kill_generation,
    },
    {
        "name": "missing-token-hold",
        "path": PHOTO_SRC,
        "old": '        raise PhotoSkip(\n            "GROK_API_KEY and XAI_API_KEY are unset; " + SKIP_PREFIX\n        )\n',
        "new": "        raise SystemExit(1)\n",
        "killer": "a missing Grok key must publish without an image, not hold",
        "kind": "python",
        "check": _kill_missing_token_hold,
    },
    {
        "name": "manifest-precedence-dropped",
        "path": PHOTO_SRC,
        "old": "        manifest = load_manifest(day, root=root)\n        if manifest:\n",
        "new": "        manifest = None\n        if manifest:\n",
        "killer": "today's verified manifest must take precedence over xAI",
        "kind": "python",
        "check": _kill_manifest_precedence,
    },
    {
        "name": "xai-error-nonzero",
        "path": PHOTO_SRC,
        "old": '        log(f"  [x-photo] unexpected error ({exc}); {SKIP_PREFIX}")\n        return narrative\n',
        "new": "        raise\n",
        "killer": "an xAI error must publish without an image and exit 0",
        "kind": "python",
        "check": _kill_xai_error_hold,
    },
    {
        "name": "workflow-bearer-secret",
        "path": WORKFLOW_SRC,
        "old": "          GROK_API_KEY: ${{ secrets.GROK_API_KEY }}\n",
        "new": "          GROK_API_KEY: ${{ secrets.GROK_API_KEY }}\n          X_BEARER_TOKEN: ${{ secrets.X_BEARER_TOKEN }}\n",
        "killer": "the photo step must not grow an X bearer secret",
        "kind": "text",
        "check": _kill_workflow_token,
    },
    {
        "name": "illustration-fallback-restored",
        "path": MEDIA_TMPL,
        "old": "{{- else if $licensed -}}\n",
        "new": (
            "{{- $illustration := partial \"nrt-event-illustration.html\" "
            "(dict \"n\" .n \"site\" .site) -}}\n"
            "{{- else if $licensed -}}\n"
        ),
        "killer": "the hero must not restore an illustration fallback",
        "kind": "text",
        "check": _kill_illustration_fallback,
    },
    {
        "name": "scene-partial-reincluded",
        "path": MEDIA_TMPL,
        "old": '{{- $pin := partial "nrt-hero-post.html" (dict "n" .n "site" .site) -}}\n',
        "new": (
            '{{- $scene := partial "nrt-hero-scene.html" (dict "n" .n "site" .site) -}}\n'
            '{{- $pin := partial "nrt-hero-post.html" (dict "n" .n "site" .site) -}}\n'
        ),
        "killer": "the hero must not re-include the scene partial",
        "kind": "text",
        "check": _kill_scene_partial,
    },
    {
        "name": "og-image-generated-art",
        "path": BASEOF_TMPL,
        "old": '{{ $ogImage := "og.png" | absURL }}',
        "new": '{{ $ogImage := "images/hero-scenes/preview-fallback.svg" | absURL }}',
        "killer": "og:image must not point at generated art",
        "kind": "text",
        "check": _kill_og_generated,
    },
    {
        "name": "unrelated-media-credited",
        "path": PHOTO_SRC,
        "old": "    if key in indexed:\n        return\n",
        "new": "    if True:\n        return\n",
        "killer": "a pbs image must belong to the credited post",
        "kind": "python",
        "check": _kill_unrelated_media,
    },
    {
        "name": "animated-webp-accepted",
        "path": PHOTO_SRC,
        "old": "    if bool(getattr(image, \"is_animated\", False)) or n_frames > 1:\n        raise PhotoSkip(f\"rejected animated image ({n_frames} frames); {SKIP_PREFIX}\")\n",
        "new": "    if False:\n        raise PhotoSkip(f\"rejected animated image ({n_frames} frames); {SKIP_PREFIX}\")\n",
        "killer": "animated images must stay rejected",
        "kind": "python",
        "check": _kill_animated,
    },
)


class XPhotoMutations(unittest.TestCase):
    def test_named_mutants_are_killed(self):
        if any(mutant["kind"] == "hugo" for mutant in MUTANTS):
            _require_hugo()
        survivors = []
        killed = 0
        for mutant in MUTANTS:
            source = mutant["path"].read_text(encoding="utf-8")
            self.assertIn(mutant["old"], source, mutant["name"])
            mutated = source.replace(mutant["old"], mutant["new"], 1)
            self.assertNotEqual(mutated, source, mutant["name"])
            reason = None
            if mutant["kind"] == "python":
                with tempfile.TemporaryDirectory() as tmp:
                    name = f"mutant_{mutant['name'].replace('-', '_')}"
                    try:
                        module = _load_mutated(Path(tmp), name, "x_photo.py", mutated)
                        reason = mutant["check"](module)
                    finally:
                        _clear(name)
            elif mutant["kind"] == "hugo":
                reason = mutant["check"](mutated)
            else:
                reason = mutant["check"](mutated)
            if reason is None:
                killed += 1
            else:
                survivors.append(f"{mutant['name']}: {reason}")
        self.assertEqual(survivors, [], json.dumps(survivors, indent=2))
        self.assertGreaterEqual(killed, 11)

    def test_live_invariants(self):
        src = PHOTO_SRC.read_text(encoding="utf-8")
        self.assertIn("allowed_x_handles", src)
        self.assertIn("/responses", src)
        self.assertIn("GROK_API_KEY and XAI_API_KEY are unset", src)
        self.assertNotIn("images/generations", src)
        self.assertNotIn("X_BEARER_TOKEN", src)
        workflow = WORKFLOW_SRC.read_text(encoding="utf-8")
        self.assertIn("--pull-photo", workflow)
        self.assertNotIn("X_BEARER_TOKEN", workflow)
        generate = GENERATE_SRC.read_text(encoding="utf-8")
        self.assertIn("x_photo.attach_to_edition(narrative)", generate)
        self.assertIn("--pull-photo", generate)
        media = MEDIA_TMPL.read_text(encoding="utf-8")
        self.assertIn('href="{{ $photoUrl }}"', media)
        self.assertIn("Photo: @{{ $photoHandle }} on X", media)
        self.assertNotIn("nrt-event-illustration", media)
        self.assertNotIn("nrt-hero-scene.html", media)
        baseof = BASEOF_TMPL.read_text(encoding="utf-8")
        self.assertIn('"og.png" | absURL', baseof)
        self.assertNotIn("hero-scenes", baseof)
