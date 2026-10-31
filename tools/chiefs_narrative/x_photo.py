"""Verified Chiefs photographs from X — never generated.

Selection order for a new edition:

1. Today's committed manifest at ``data/x-photo/YYYY-MM-DD.json``, if it
   passes the same URL and download checks as a live candidate.
2. The xAI Responses API ``x_search`` tool, authenticated with the existing
   ``GROK_API_KEY`` / ``XAI_API_KEY`` secret. Handles are restricted with
   ``allowed_x_handles`` from the current xAI docs.
3. No image.

A missing, rejected, rate-limited, or erroring photo never holds or fails
the edition. This module logs the exact reason (with secrets redacted) and
exits 0. There is no image-generation fallback.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import struct
import sys
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

import requests
from PIL import Image

from . import config, providers

SCHEMA_VERSION = 1
ASSET_DIR_REL = "images/x-photo"
MANIFEST_DIR_REL = "data/x-photo"
ALLOWLIST_FILE_REL = "data/x-photo/allowlist.json"
DEFAULT_HANDLES = (
    "Chiefs",
    "NFL",
    "KCStar",
    "AP_Sports",
    "arrowheadpride",
)
POST_URL_RE = re.compile(
    r"^https://(?:www\.)?x\.com/([A-Za-z0-9_]{1,15})/status/(\d{10,20})/?$",
    re.IGNORECASE,
)
MEDIA_HOST = "pbs.twimg.com"
MEDIA_PATH_RE = re.compile(r"^/media/([A-Za-z0-9_-]+)(?:\.[A-Za-z0-9]+)?$")
MEDIA_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")
HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
SYNDICATION_ENDPOINT = "https://cdn.syndication.twimg.com/tweet-result"
FIXTWEET_ENDPOINT = "https://api.fxtwitter.com"
ASSET_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+\.(?:webp|png|jpe?g)$")
REJECTED_MEDIA_HINTS = (
    "/tweet_video",
    "/ext_tw_video",
    "/amplify_video",
    "/tweet_video_thumb",
    "/amplify_video_thumb",
    "video.twimg.com",
    ".gif",
    "format=gif",
    "format=mp4",
)
GRAPHIC_RE = re.compile(
    r"\b(?:logo|wordmark|infographic|scoreboard graphic|text overlay|"
    r"sponsor board|odds board|graphic of|word mark)\b",
    re.IGNORECASE,
)
ALLOWED_CONTENT_TYPES = frozenset(
    {
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/webp",
    }
)
MIN_BYTES = 64
MAX_BYTES = 8 * 1024 * 1024
MIN_SIDE = 64
MAX_SIDE = 8000
HTTP_TIMEOUT = 20
REDACT_ENV = (
    "GROK_API_KEY",
    "XAI_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "ODDS_API_KEY",
)
_BEARER_RE = re.compile(r"(?i)(bearer\s+)\S+")
SKIP_PREFIX = "publishing without an image"


class PhotoSkip(Exception):
    """Soft skip: log the reason and continue the edition with no photo."""


def redact(text) -> str:
    """Strip API keys and bearer tokens from anything that might be logged."""
    blob = "" if text is None else str(text)
    for name in REDACT_ENV:
        value = os.environ.get(name) or ""
        if len(value) >= 4:
            blob = blob.replace(value, "[redacted]")
    blob = _BEARER_RE.sub(r"\1[redacted]", blob)
    return blob


def log(message: str, *, stream=None) -> None:
    print(redact(message), file=stream or sys.stdout)


def _s(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _handle(value) -> str:
    return _s(value).lstrip("@")


def today_utc(now=None) -> datetime:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def iso_now(now=None) -> str:
    return today_utc(now).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def allowlist(*, root=None, env=None) -> tuple[str, ...]:
    """Default handles, optional ``data/x-photo/allowlist.json``, optional env."""
    handles: list[str] = []
    seen: set[str] = set()

    def _add(raw) -> None:
        handle = _handle(raw)
        key = handle.lower()
        if not HANDLE_RE.fullmatch(handle) or key in seen:
            return
        seen.add(key)
        handles.append(handle)

    for item in DEFAULT_HANDLES:
        _add(item)
    base = Path(root or config.REPO_ROOT)
    path = base / ALLOWLIST_FILE_REL
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = {}
    if isinstance(payload, dict):
        listed = payload.get("handles") or payload.get("allowlist") or []
        if isinstance(listed, list):
            for item in listed:
                _add(item)
    environ = env if env is not None else os.environ
    extra = _s(environ.get("X_PHOTO_HANDLES") or "")
    if extra:
        for item in extra.split(","):
            _add(item)
    return tuple(handles[:20])


def allowlist_set(*, root=None, env=None) -> set[str]:
    return {item.lower() for item in allowlist(root=root, env=env)}


def parse_post_url(url: str, *, allowed: set[str] | None = None) -> tuple[str, str] | None:
    match = POST_URL_RE.match(_s(url).split("#", 1)[0])
    if not match:
        return None
    handle, post_id = match.group(1), match.group(2)
    if allowed is not None and handle.lower() not in allowed:
        return None
    return handle, post_id


def status_url(handle: str, post_id: str) -> str:
    return f"https://x.com/{handle}/status/{post_id}"


def media_url_ok(url: str) -> bool:
    raw = _s(url)
    if not raw:
        return False
    lowered = raw.lower()
    if any(hint in lowered for hint in REJECTED_MEDIA_HINTS):
        return False
    parsed = urlparse(raw)
    if parsed.scheme != "https" or parsed.netloc.lower() != MEDIA_HOST:
        return False
    path = parsed.path
    if not MEDIA_PATH_RE.fullmatch(path):
        return False
    return True


def media_key_from_url(url: str) -> str:
    parsed = urlparse(_s(url))
    match = MEDIA_PATH_RE.fullmatch(parsed.path)
    return match.group(1) if match else ""


def _declared_media_key(row: dict) -> str:
    key = _s(row.get("media_key") or row.get("mediaKey") or "")
    return key if MEDIA_KEY_RE.fullmatch(key) else ""


def graphic_rejected(alt: str, *extra: str) -> bool:
    blob = " ".join(_s(item) for item in (alt, *extra))
    return bool(GRAPHIC_RE.search(blob))


def _kind_from_magic(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "gif"
    return ""


def _png_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 24:
        return None
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    i = 2
    length = len(data)
    while i + 9 < length:
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker in {0xC0, 0xC1, 0xC2}:
            height, width = struct.unpack(">HH", data[i + 5 : i + 9])
            return width, height
        if marker in {0xD8, 0xD9}:
            i += 2
            continue
        if i + 4 > length:
            return None
        seglen = struct.unpack(">H", data[i + 2 : i + 4])[0]
        i += 2 + seglen
    return None


def image_dimensions(data: bytes, kind: str) -> tuple[int, int] | None:
    if kind == "png":
        return _png_size(data)
    if kind == "jpeg":
        return _jpeg_size(data)
    return None


def _png_has_actl(data: bytes) -> bool:
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return False
    offset = 8
    length = len(data)
    while offset + 8 <= length:
        size = struct.unpack(">I", data[offset : offset + 4])[0]
        tag = data[offset + 4 : offset + 8]
        if tag == b"acTL":
            return True
        if tag == b"IEND":
            return False
        offset += 12 + size
    return False


def content_type_ok(value: str) -> bool:
    ctype = _s(value).split(";", 1)[0].lower()
    return ctype in ALLOWED_CONTENT_TYPES


def decode_photo(data: bytes, content_type: str) -> dict:
    if not content_type_ok(content_type):
        raise PhotoSkip(f"rejected content-type {content_type!r}; {SKIP_PREFIX}")
    if not isinstance(data, (bytes, bytearray)) or len(data) < MIN_BYTES:
        raise PhotoSkip(f"image too small ({len(data) if data else 0} bytes); {SKIP_PREFIX}")
    if len(data) > MAX_BYTES:
        raise PhotoSkip(f"image too large ({len(data)} bytes); {SKIP_PREFIX}")
    blob = bytes(data)
    kind = _kind_from_magic(blob[:16])
    if kind == "gif":
        raise PhotoSkip(f"rejected GIF bytes; {SKIP_PREFIX}")
    if kind not in {"jpeg", "png", "webp"}:
        raise PhotoSkip(f"bytes are not a still photo ({kind or 'unknown'}); {SKIP_PREFIX}")
    expected = {
        "jpeg": {"image/jpeg", "image/jpg"},
        "png": {"image/png"},
        "webp": {"image/webp"},
    }[kind]
    if _s(content_type).split(";", 1)[0].lower() not in expected:
        raise PhotoSkip(
            f"content-type {content_type!r} does not match {kind} bytes; {SKIP_PREFIX}"
        )
    if kind == "png" and _png_has_actl(blob):
        raise PhotoSkip(f"rejected animated PNG; {SKIP_PREFIX}")
    try:
        image = Image.open(BytesIO(blob))
        image.load()
    except (OSError, ValueError, SyntaxError, TypeError) as exc:
        raise PhotoSkip(f"could not decode image bytes ({exc}); {SKIP_PREFIX}") from exc
    n_frames = int(getattr(image, "n_frames", 1) or 1)
    if bool(getattr(image, "is_animated", False)) or n_frames > 1:
        raise PhotoSkip(f"rejected animated image ({n_frames} frames); {SKIP_PREFIX}")
    width, height = image.size
    if not (MIN_SIDE <= width <= MAX_SIDE and MIN_SIDE <= height <= MAX_SIDE):
        raise PhotoSkip(f"image size {width}x{height} is not usable; {SKIP_PREFIX}")
    ext = {"jpeg": "jpg", "png": "png", "webp": "webp"}[kind]
    return {"kind": kind, "ext": ext, "width": width, "height": height, "bytes": blob}


def _candidate_from_mapping(row: dict, *, allowed: set[str]) -> dict | None:
    if not isinstance(row, dict):
        return None
    handle = _handle(row.get("handle") or row.get("account") or "")
    post_url = _s(row.get("post_url") or row.get("postUrl") or row.get("url") or "")
    media_url = _s(row.get("media_url") or row.get("mediaUrl") or "")
    alt = _s(row.get("alt") or row.get("mediaAlt") or "")
    parsed = parse_post_url(post_url, allowed=allowed)
    if parsed:
        url_handle, post_id = parsed
        handle = handle or url_handle
        if handle.lower() != url_handle.lower():
            return None
        post_url = status_url(url_handle, post_id)
    elif handle and _s(row.get("post_id") or row.get("postId") or row.get("id") or ""):
        post_id = _s(row.get("post_id") or row.get("postId") or row.get("id"))
        if not post_id.isdigit() or handle.lower() not in allowed:
            return None
        post_url = status_url(handle, post_id)
        parsed = (handle, post_id)
    else:
        return None
    if handle.lower() not in allowed or not media_url_ok(media_url):
        return None
    if graphic_rejected(alt, _s(row.get("text") or "")):
        return None
    url_key = media_key_from_url(media_url)
    if not url_key:
        return None
    declared = _declared_media_key(row)
    if declared and declared != url_key:
        return None
    return {
        "handle": parsed[0],
        "postId": parsed[1],
        "postUrl": post_url,
        "mediaUrl": media_url,
        "mediaKey": url_key,
        "alt": alt,
    }


def extract_candidates(payload, *, allowed: set[str]) -> list[dict]:
    """Pull post/media URL pairs from an xAI response or a manifest object."""
    found: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def _keep(row: dict | None) -> None:
        if not row:
            return
        key = (row["postId"], row["mediaUrl"])
        if key in seen:
            return
        seen.add(key)
        found.append(row)

    def _walk(node) -> None:
        if isinstance(node, dict):
            _keep(_candidate_from_mapping(node, allowed=allowed))
            for value in node.values():
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)
        elif isinstance(node, str):
            text = node.strip()
            if text.startswith("{") and text.endswith("}"):
                try:
                    parsed = json.loads(text)
                except ValueError:
                    parsed = None
                if isinstance(parsed, (dict, list)):
                    _walk(parsed)

    _walk(payload)
    index = post_media_index(payload)
    for row in found:
        row["searchMediaKeys"] = sorted(index.get(row["postId"], set()))
    return found


def _as_media_items(value) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        items: list = []
        for key in ("photos", "images", "media", "all"):
            nested = value.get(key)
            if isinstance(nested, list):
                items.extend(nested)
        if not items and (
            value.get("url")
            or value.get("media_url")
            or value.get("media_url_https")
            or value.get("media_key")
            or value.get("mediaKey")
        ):
            items.append(value)
        return items
    return []


def _media_keys_from_items(items) -> set[str]:
    keys: set[str] = set()
    for item in _as_media_items(items):
        if isinstance(item, str):
            key = media_key_from_url(item)
            if key:
                keys.add(key)
            continue
        if not isinstance(item, dict):
            continue
        declared = _s(
            item.get("media_key") or item.get("mediaKey") or item.get("key") or ""
        )
        if MEDIA_KEY_RE.fullmatch(declared):
            keys.add(declared)
        for field in ("media_url", "media_url_https", "url", "src"):
            key = media_key_from_url(item.get(field) or "")
            if key:
                keys.add(key)
    return keys


def _post_id_from_mapping(row: dict) -> str:
    post_id = _s(row.get("id_str") or row.get("postId") or row.get("post_id") or "")
    if post_id.isdigit():
        return post_id
    raw_id = row.get("id")
    if isinstance(raw_id, (int, str)) and _s(raw_id).isdigit():
        return _s(raw_id)
    for field in ("url", "post_url", "postUrl"):
        parsed = parse_post_url(_s(row.get(field) or ""))
        if parsed:
            return parsed[1]
    return ""


def post_media_index(payload) -> dict[str, set[str]]:
    """Map a post id to pbs media keys found on that post object."""
    index: dict[str, set[str]] = {}

    def _add(post_id: str, keys: set[str]) -> None:
        if post_id and keys:
            index.setdefault(post_id, set()).update(keys)

    def _walk(node) -> None:
        if isinstance(node, dict):
            post_id = _post_id_from_mapping(node)
            media = node.get("media") or node.get("photos")
            entities = node.get("entities")
            if media is None and isinstance(entities, dict):
                media = entities.get("media")
            extended = node.get("extended_entities")
            if media is None and isinstance(extended, dict):
                media = extended.get("media")
            tweet = node.get("tweet")
            if media is None and isinstance(tweet, dict):
                media = tweet.get("media") or tweet.get("photos")
            _add(post_id, _media_keys_from_items(media))
            for value in node.values():
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(payload)
    return index


def _all_pbs_media_keys(payload) -> set[str]:
    keys: set[str] = set()

    def _walk(node) -> None:
        if isinstance(node, dict):
            for value in node.values():
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)
        elif isinstance(node, str):
            key = media_key_from_url(node)
            if key:
                keys.add(key)

    _walk(payload)
    return keys


def media_keys_from_public_payload(payload, post_id: str) -> set[str]:
    keys = set(post_media_index(payload).get(post_id, set()))
    if not isinstance(payload, dict):
        return keys
    sid = _s(payload.get("id_str") or payload.get("id") or "")
    tweet = payload.get("tweet") if isinstance(payload.get("tweet"), dict) else {}
    tid = _s(tweet.get("id_str") or tweet.get("id") or "")
    if sid == post_id or tid == post_id:
        keys |= _all_pbs_media_keys(payload)
    return keys


def fetch_public_media_keys(
    post_id: str,
    handle: str,
    *,
    transport,
) -> set[str]:
    """Free public read of the post (syndication, then FixTweet)."""
    keys: set[str] = set()
    try:
        resp = transport.request(
            "GET",
            SYNDICATION_ENDPOINT,
            params={"id": post_id, "lang": "en", "token": "0"},
            timeout=HTTP_TIMEOUT,
        )
    except (requests.RequestException, OSError, TypeError, ValueError, AssertionError):
        resp = None
    status = getattr(resp, "status_code", 0) if resp is not None else 0
    if status == 200:
        try:
            payload = resp.json()
        except (ValueError, TypeError, AttributeError):
            payload = None
        if isinstance(payload, dict):
            keys |= media_keys_from_public_payload(payload, post_id)
    if keys:
        return keys
    try:
        resp = transport.request(
            "GET",
            f"{FIXTWEET_ENDPOINT}/{handle}/status/{post_id}",
            timeout=HTTP_TIMEOUT,
        )
    except (requests.RequestException, OSError, TypeError, ValueError, AssertionError):
        return keys
    if getattr(resp, "status_code", 0) != 200:
        return keys
    try:
        payload = resp.json()
    except (ValueError, TypeError, AttributeError):
        return keys
    if isinstance(payload, dict):
        keys |= media_keys_from_public_payload(payload, post_id)
    return keys


def verify_media_belongs_to_post(
    candidate: dict,
    *,
    transport,
    search_index=None,
) -> None:
    key = candidate.get("mediaKey") or media_key_from_url(candidate.get("mediaUrl") or "")
    post_id = _s(candidate.get("postId") or "")
    if not key or not post_id:
        raise PhotoSkip(f"candidate is missing a post id or pbs media key; {SKIP_PREFIX}")
    indexed = set()
    if isinstance(search_index, dict):
        indexed = set(search_index.get(post_id) or [])
    claimed = candidate.get("searchMediaKeys") or []
    if isinstance(claimed, (list, set, tuple)):
        indexed.update(str(item) for item in claimed)
    if key in indexed:
        return
    public = fetch_public_media_keys(
        post_id, candidate.get("handle") or "", transport=transport
    )
    if key in public:
        return
    raise PhotoSkip(
        f"media key {key} is not on credited post {post_id}; {SKIP_PREFIX}"
    )


def _output_text(payload: dict) -> str:
    parts: list[str] = []
    raw = payload.get("output_text")
    if isinstance(raw, str) and raw.strip():
        parts.append(raw)
    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("content"), list):
            for block in item["content"]:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    parts.append(block["text"])
        if isinstance(item.get("text"), str):
            parts.append(item["text"])
    return "\n".join(parts)


def load_manifest(day: str, *, root=None) -> dict | None:
    path = Path(root or config.REPO_ROOT) / MANIFEST_DIR_REL / f"{day}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def api_key() -> str:
    return providers.grok_key()


def x_search_payload(narrative: dict, *, now=None, root=None, env=None) -> dict:
    """Documented Responses API body for X search. No image-generation tools."""
    current = today_utc(now)
    start = (current - timedelta(days=7)).strftime("%Y-%m-%d")
    end = (current + timedelta(days=1)).strftime("%Y-%m-%d")
    handles = list(allowlist(root=root, env=env))
    opponent, players = edition_subject(narrative)
    subject = "Kansas City Chiefs"
    if opponent:
        subject += f" vs {opponent}"
    if players:
        subject += "; players: " + ", ".join(players[:8])
    prompt = (
        "Find the newest real photograph (a player, the team, a press "
        "conference, or game action) of the Kansas City Chiefs on X from "
        f"the last 7 days. Subject: {subject}. Photos only. Reject videos, "
        "GIFs, animated thumbnails, logos, wordmarks, and text-heavy graphics. "
        "Return JSON only with post_url (https://x.com/<handle>/status/<digits>), "
        "handle, media_url (https://pbs.twimg.com/media/...), and alt. "
        "Do not invent URLs. If no real photo exists, return {}."
    )
    return {
        "model": providers.grok_model(),
        "input": [{"role": "user", "content": prompt}],
        "tools": [
            {
                "type": "x_search",
                "allowed_x_handles": handles,
                "from_date": start,
                "to_date": end,
                "enable_image_understanding": False,
                "enable_video_understanding": False,
            }
        ],
        "max_turns": 2,
    }


def edition_subject(narrative: dict) -> tuple[str, list[str]]:
    phase = narrative.get("phase") if isinstance(narrative.get("phase"), dict) else {}
    mode = _s(phase.get("mode") or phase.get("type")).lower()
    opponent = ""
    if mode == "preview":
        opponent = _s((narrative.get("nextGame") or {}).get("opponent"))
    elif mode == "review":
        opponent = _s((narrative.get("lastGameReview") or {}).get("opponent"))
    players: list[str] = []
    seen: set[str] = set()

    def _add_player(name: str) -> None:
        text = _s(name)
        key = text.lower()
        if not text or key in seen:
            return
        seen.add(key)
        players.append(text)

    for row in narrative.get("injuries") or []:
        if isinstance(row, dict):
            _add_player(row.get("player") or "")
    for row in narrative.get("lockerRoom") or []:
        if isinstance(row, dict):
            _add_player(row.get("speaker") or row.get("player") or "")
    for row in narrative.get("pressConferences") or []:
        if isinstance(row, dict):
            _add_player(row.get("speaker") or "")
    return opponent, players


class HttpTransport:
    """Thin requests wrapper so tests inject a fake with no network."""

    def request(self, method: str, url: str, **kwargs):
        headers = dict(kwargs.pop("headers", None) or {})
        timeout = kwargs.pop("timeout", HTTP_TIMEOUT)
        return requests.request(
            method,
            url,
            headers=headers,
            timeout=timeout,
            **kwargs,
        )


def _http_error(status: int, body: str) -> PhotoSkip:
    snippet = redact(body)[:180]
    if status == 429:
        return PhotoSkip(f"xAI X search returned HTTP 429; {SKIP_PREFIX}")
    if status >= 500:
        return PhotoSkip(f"xAI X search returned HTTP {status}; {SKIP_PREFIX}")
    return PhotoSkip(f"xAI X search returned HTTP {status}: {snippet}; {SKIP_PREFIX}")


def search_xai(
    narrative: dict,
    *,
    now=None,
    root=None,
    env=None,
    transport=None,
) -> list[dict]:
    key = api_key()
    if not key:
        raise PhotoSkip(
            "GROK_API_KEY and XAI_API_KEY are unset; " + SKIP_PREFIX
        )
    transport = transport or HttpTransport()
    body = x_search_payload(narrative, now=now, root=root, env=env)
    url = f"{providers.grok_base().rstrip('/')}/responses"
    try:
        resp = transport.request(
            "POST",
            url,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=HTTP_TIMEOUT,
        )
    except (requests.RequestException, OSError, TypeError, ValueError) as exc:
        raise PhotoSkip(f"xAI X search request failed ({exc}); {SKIP_PREFIX}") from exc
    status = getattr(resp, "status_code", 0)
    text = getattr(resp, "text", "") or ""
    if status == 429 or status >= 400:
        raise _http_error(status, text)
    try:
        payload = resp.json()
    except (ValueError, TypeError, AttributeError) as exc:
        raise PhotoSkip(f"xAI X search returned non-JSON; {SKIP_PREFIX}") from exc
    if not isinstance(payload, dict):
        raise PhotoSkip(f"xAI X search returned an empty payload; {SKIP_PREFIX}")
    allowed = allowlist_set(root=root, env=env)
    candidates = extract_candidates(payload, allowed=allowed)
    if not candidates:
        raise PhotoSkip(f"xAI X search returned no usable photo; {SKIP_PREFIX}")
    return candidates


def download_photo(media_url: str, *, transport=None) -> dict:
    if not media_url_ok(media_url):
        raise PhotoSkip(f"rejected media URL (not https://pbs.twimg.com/media/...); {SKIP_PREFIX}")
    transport = transport or HttpTransport()
    try:
        resp = transport.request("GET", media_url, timeout=HTTP_TIMEOUT)
    except (requests.RequestException, OSError, TypeError, ValueError) as exc:
        raise PhotoSkip(f"photo download failed ({exc}); {SKIP_PREFIX}") from exc
    status = getattr(resp, "status_code", 0)
    if status != 200:
        raise PhotoSkip(f"photo download returned HTTP {status}; {SKIP_PREFIX}")
    headers = getattr(resp, "headers", {}) or {}
    ctype = ""
    if hasattr(headers, "get"):
        ctype = headers.get("Content-Type") or headers.get("content-type") or ""
    data = getattr(resp, "content", b"") or b""
    return decode_photo(data, ctype)


def verify_candidate(
    row: dict,
    *,
    allowed: set[str],
    transport=None,
    search_index=None,
) -> dict:
    candidate = _candidate_from_mapping(row, allowed=allowed)
    if not candidate:
        handle = _handle((row or {}).get("handle") if isinstance(row, dict) else "")
        post_url = _s((row or {}).get("post_url") or (row or {}).get("postUrl") or "")
        media_url = _s((row or {}).get("media_url") or (row or {}).get("mediaUrl") or "")
        if post_url and parse_post_url(post_url) and not parse_post_url(post_url, allowed=allowed):
            parsed = parse_post_url(post_url)
            raise PhotoSkip(
                f"rejected handle @{parsed[0]} (not on the photo allowlist); {SKIP_PREFIX}"
            )
        if handle and handle.lower() not in allowed:
            raise PhotoSkip(
                f"rejected handle @{handle} (not on the photo allowlist); {SKIP_PREFIX}"
            )
        if media_url and not media_url_ok(media_url):
            raise PhotoSkip(
                f"rejected media URL (not https://pbs.twimg.com/media/...); {SKIP_PREFIX}"
            )
        raise PhotoSkip(f"candidate failed URL checks; {SKIP_PREFIX}")
    if isinstance(row, dict) and row.get("searchMediaKeys") and not candidate.get("searchMediaKeys"):
        candidate["searchMediaKeys"] = list(row.get("searchMediaKeys") or [])
    verify_media_belongs_to_post(
        candidate, transport=transport or HttpTransport(), search_index=search_index
    )
    image = download_photo(candidate["mediaUrl"], transport=transport)
    alt = candidate["alt"] or (
        f"Kansas City Chiefs photograph from @{candidate['handle']} on X"
    )
    candidate.update(
        {
            "alt": alt,
            "width": image["width"],
            "height": image["height"],
            "ext": image["ext"],
            "bytes": image["bytes"],
        }
    )
    return candidate


def verify_manifest(manifest: dict, *, allowed: set[str], transport=None) -> dict:
    post_id = _s(manifest.get("post_id") or manifest.get("postId") or "")
    media_key = _declared_media_key(manifest)
    if not post_id.isdigit() or not media_key:
        raise PhotoSkip(f"manifest missing post id or media key; {SKIP_PREFIX}")
    verified = verify_candidate(manifest, allowed=allowed, transport=transport)
    if verified["postId"] != post_id or verified["mediaKey"] != media_key:
        raise PhotoSkip(
            f"manifest post id or media key does not match the media URL; {SKIP_PREFIX}"
        )
    return verified


def store_photo(
    narrative: dict,
    candidate: dict,
    *,
    root=None,
    now=None,
    source: str,
) -> dict:
    base = Path(root or config.REPO_ROOT)
    slug = _s(narrative.get("slug")) or today_utc(now).strftime("%Y-%m-%d-%H%M")
    ext = candidate["ext"]
    rel = f"{ASSET_DIR_REL}/{slug}.{ext}"
    dest = base / "public" / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(candidate["bytes"])
    record = {
        "schemaVersion": SCHEMA_VERSION,
        "postId": candidate["postId"],
        "handle": candidate["handle"],
        "postUrl": candidate["postUrl"],
        "mediaUrl": candidate["mediaUrl"],
        "mediaKey": candidate["mediaKey"],
        "asset": rel,
        "alt": candidate["alt"],
        "credit": f"Photo: @{candidate['handle']} on X",
        "width": candidate.get("width") or 0,
        "height": candidate.get("height") or 0,
        "fetchedAt": iso_now(now),
        "source": source,
    }
    return record


def existing_photo(narrative: dict, *, root=None) -> dict | None:
    row = narrative.get("xPhoto")
    if not isinstance(row, dict):
        return None
    asset = _s(row.get("asset"))
    if not asset or ".." in asset or not asset.startswith(f"{ASSET_DIR_REL}/"):
        return None
    name = Path(asset).name
    if not ASSET_NAME_RE.fullmatch(name):
        return None
    handle = _handle(row.get("handle") or "")
    post_id = _s(row.get("postId") or "")
    post_url = _s(row.get("postUrl") or "")
    parsed = parse_post_url(post_url)
    if not parsed or parsed[1] != post_id or parsed[0].lower() != handle.lower():
        return None
    path = Path(root or config.REPO_ROOT) / "public" / asset
    if not path.is_file():
        return None
    return row


def attach_to_edition(
    narrative: dict,
    *,
    root=None,
    now=None,
    env=None,
    transport=None,
) -> dict:
    """Best-effort attach. Never raises. Sets ``xPhoto`` or leaves it empty."""
    if not isinstance(narrative, dict):
        return narrative
    allowed = allowlist_set(root=root, env=env)
    current = today_utc(now)
    day = current.strftime("%Y-%m-%d")
    kept = existing_photo(narrative, root=root)
    narrative["xPhoto"] = {}

    def _commit(row: dict, source: str) -> dict:
        record = store_photo(narrative, row, root=root, now=now, source=source)
        narrative["xPhoto"] = record
        log(
            f"  [x-photo] attached @{record['handle']} {record['postUrl']} "
            f"via {source}"
        )
        return narrative

    try:
        manifest = load_manifest(day, root=root)
        if manifest:
            try:
                verified = verify_manifest(
                    manifest, allowed=allowed, transport=transport
                )
                return _commit(verified, "manifest")
            except PhotoSkip as exc:
                log(f"  [x-photo] manifest {day} rejected: {exc}")
        elif kept:
            narrative["xPhoto"] = kept
            log("  [x-photo] keeping previously verified edition photo")
            return narrative
        candidates = search_xai(
            narrative, now=now, root=root, env=env, transport=transport
        )
        last_error = "xAI X search returned no usable photo"
        for row in candidates:
            try:
                index = {row["postId"]: set(row.get("searchMediaKeys") or [])}
                verified = verify_candidate(
                    row, allowed=allowed, transport=transport, search_index=index
                )
                return _commit(verified, "xai-x-search")
            except PhotoSkip as exc:
                last_error = str(exc)
                log(f"  [x-photo] candidate rejected: {exc}")
        log(f"  [x-photo] {last_error}; {SKIP_PREFIX}")
        return narrative
    except PhotoSkip as exc:
        log(f"  [x-photo] {exc}")
        return narrative
    except Exception as exc:  # noqa: BLE001 — photo must never fail the edition
        log(f"  [x-photo] unexpected error ({exc}); {SKIP_PREFIX}")
        return narrative


def pull_published(*, root=None, now=None, env=None, transport=None) -> int:
    """Read ``data/narrative.json``, attach a photo if possible, always return 0."""
    path = Path(root or config.REPO_ROOT) / "data" / "narrative.json"
    try:
        narrative = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log(f"  [x-photo] could not read published edition ({exc}); {SKIP_PREFIX}")
        return 0
    if not isinstance(narrative, dict):
        log(f"  [x-photo] published edition is not an object; {SKIP_PREFIX}")
        return 0
    attach_to_edition(
        narrative, root=root, now=now, env=env, transport=transport
    )
    try:
        payload = json.dumps(narrative, indent=2, ensure_ascii=False) + "\n"
        path.write_text(payload, encoding="utf-8")
        slug = _s(narrative.get("slug"))
        if slug:
            editions = Path(root or config.REPO_ROOT) / "data" / "narrative_editions"
            editions.mkdir(parents=True, exist_ok=True)
            (editions / f"{slug}.json").write_text(payload, encoding="utf-8")
    except OSError as exc:
        log(f"  [x-photo] could not write edition metadata ({exc}); {SKIP_PREFIX}")
    if narrative.get("xPhoto"):
        log("  [x-photo] edition metadata updated")
    else:
        log(f"  [x-photo] {SKIP_PREFIX}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Pull a verified X photograph for the published edition."
    )
    parser.parse_args(argv)
    return pull_published()


if __name__ == "__main__":
    raise SystemExit(main())
