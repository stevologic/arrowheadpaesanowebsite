"""In-memory comment store with the same contract as the Cloudflare Worker.

Kept offline (no network) so CI can exercise posting, the empty list,
honeypot/rate-limit rejection, and hide/delete without keys.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
HONEYPOT_FIELD = "nrt_hp_x7"
MAX_NAME = 40
MIN_NAME = 2
MAX_BODY = 1000
MIN_BODY = 2
RATE_WINDOW_SEC = 10 * 60
RATE_MAX = 5
RATE_MIN_INTERVAL_SEC = 20
AUTH_FAIL_MAX = 8
AUTH_FAIL_WINDOW_SEC = 5 * 60
THREAD_LIST_LIMIT = 50
EMPTY_STATE = "No comments yet. Be the first."
NOT_CONNECTED_COPY = "Comments are not connected yet."

# Offline Turnstile stand-ins. Production Worker always calls siteverify.
TURNSTILE_PASS_TOKEN = "test-pass"
TURNSTILE_FAIL_TOKEN = "test-fail"


class CommentError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def is_comments_ui_enabled(api_url: str | None, turnstile_site_key: str | None) -> bool:
    """Public comment block renders only when both free-tier settings are set."""
    return bool(str(api_url or "").strip() and str(turnstile_site_key or "").strip())


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _clean(value: Any, limit: int) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value or ""))
    text = re.sub(r"[<>]", "", text)
    return text.strip()[:limit]


def _expand_ipv6(ip: str) -> list[str]:
    bare = ip.split("%", 1)[0].lower()
    head, _, tail = bare.partition("::")
    head_parts = [part for part in head.split(":") if part] if head else []
    tail_parts = [part for part in tail.split(":") if part] if tail else []
    missing = max(8 - len(head_parts) - len(tail_parts), 0)
    parts = head_parts + (["0"] * missing) + tail_parts
    return [part.lstrip("0") or "0" for part in parts[:8]]


def rate_key(ip: str) -> str:
    raw = (ip or "unknown").strip() or "unknown"
    if raw == "unknown":
        return raw
    bare = raw.split("%", 1)[0]
    if "." in bare and ":" in bare:
        return bare.rsplit(":", 1)[-1]
    if ":" in bare:
        return ":".join(_expand_ipv6(bare)[:4]) + "::/64"
    return bare


def load_known_slugs(root: Path | None = None) -> set[str]:
    base = root or Path(__file__).resolve().parents[2]
    slugs: set[str] = set()
    editions = base / "data" / "narrative_editions"
    if editions.is_dir():
        slugs.update(path.stem for path in editions.glob("*.json"))
    current = base / "data" / "narrative.json"
    if current.is_file():
        try:
            payload = json.loads(current.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
        slug = str(payload.get("slug") or "").strip()
        if slug:
            slugs.add(slug)
    return slugs


def _timing_safe_equal(left: str, right: str) -> bool:
    digest_left = hashlib.sha256(left.encode("utf-8")).digest()
    digest_right = hashlib.sha256(right.encode("utf-8")).digest()
    return hmac.compare_digest(digest_left, digest_right)


def validate_turnstile(token: str, mode: str = "offline") -> None:
    """CAPTCHA-less gate. Offline mode never hits the network."""
    token = str(token or "").strip()
    if mode == "offline":
        if not token or token == TURNSTILE_FAIL_TOKEN:
            raise CommentError("Spam check failed.", 400)
        return
    raise CommentError("Turnstile verification is handled by the Worker.", 500)


def public_comment(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "slug": row["slug"],
        "name": row["name"],
        "body": row["body"],
        "createdAt": row["createdAt"],
        "hidden": bool(row.get("hidden")),
    }


@dataclass
class CommentStore:
    admin_token: str
    turnstile_mode: str = "offline"
    rate_window_sec: int = RATE_WINDOW_SEC
    rate_max: int = RATE_MAX
    rate_min_interval_sec: int = RATE_MIN_INTERVAL_SEC
    known_slugs: set[str] | None = None
    _comments: dict[str, dict[str, Any]] = field(default_factory=dict)
    _hits: dict[str, list[float]] = field(default_factory=dict)
    _auth_fails: dict[str, list[float]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.known_slugs is None:
            self.known_slugs = load_known_slugs()

    def empty_state(self) -> str:
        return EMPTY_STATE

    def _assert_slug(self, slug: str) -> str:
        slug = _clean(slug, 80)
        if not SLUG_RE.match(slug) or slug not in (self.known_slugs or set()):
            raise CommentError("Unknown story.", 404)
        return slug

    def list_public(
        self,
        slug: str,
        *,
        limit: int = THREAD_LIST_LIMIT,
        after: str = "",
    ) -> dict[str, Any]:
        slug = self._assert_slug(slug)
        rows = [
            public_comment(row)
            for row in self._comments.values()
            if row["slug"] == slug and not row.get("hidden")
        ]
        rows.sort(key=lambda row: (row["createdAt"], row["id"]), reverse=True)
        if after and "|" in after:
            created, ident = after.split("|", 1)
            rows = [
                row
                for row in rows
                if (row["createdAt"], row["id"]) < (created, ident)
            ]
        cap = THREAD_LIST_LIMIT
        page = rows[:cap]
        nxt = None
        if len(rows) > cap and page:
            last = page[-1]
            nxt = f"{last['createdAt']}|{last['id']}"
        return {"comments": page, "next": nxt}

    def list_admin(self, slug: str | None = None) -> list[dict[str, Any]]:
        rows = [
            public_comment(row)
            for row in self._comments.values()
            if slug is None or row["slug"] == slug
        ]
        rows.sort(key=lambda row: row["createdAt"], reverse=True)
        return rows

    def post(
        self,
        payload: dict[str, Any],
        *,
        ip: str,
        turnstile_token: str,
    ) -> dict[str, Any] | None:
        request_id = str(payload.get("requestId") or payload.get("request_id") or "").strip()
        if request_id:
            for existing in self._comments.values():
                if existing.get("requestId") == request_id:
                    return public_comment(existing)
        validate_turnstile(turnstile_token, self.turnstile_mode)
        if str(payload.get(HONEYPOT_FIELD) or "").strip():
            # Pretend success so bots do not retry, but do not store.
            return None

        slug = self._assert_slug(payload.get("slug"))
        name = _clean(payload.get("name"), MAX_NAME)
        body = _clean(payload.get("body"), MAX_BODY)
        if len(name) < MIN_NAME:
            raise CommentError("Name is required.")
        if len(body) < MIN_BODY:
            raise CommentError("Comment is too short.")

        self._enforce_rate_limit(ip)
        row = {
            "id": f"{slug}~{uuid.uuid4().hex}",
            "slug": slug,
            "name": name,
            "body": body,
            "createdAt": _utc_now(),
            "hidden": False,
            "requestId": request_id or str(uuid.uuid4()),
        }
        self._comments[row["id"]] = row
        return public_comment(row)

    def hide(self, comment_id: str, hidden: bool = True) -> dict[str, Any]:
        row = self._comments.get(comment_id)
        if not row:
            raise CommentError("Comment not found.", 404)
        row["hidden"] = bool(hidden)
        return public_comment(row)

    def delete(self, comment_id: str) -> None:
        if comment_id not in self._comments:
            raise CommentError("Comment not found.", 404)
        del self._comments[comment_id]

    def require_admin(self, provided: str | None, ip: str = "unknown") -> None:
        key = rate_key(ip)
        now = time.time()
        fails = [
            stamp
            for stamp in self._auth_fails.get(key, [])
            if now - stamp < AUTH_FAIL_WINDOW_SEC
        ]
        self._auth_fails[key] = fails
        token = (provided or "").removeprefix("Bearer ").strip()
        expected = (self.admin_token or "").strip()
        valid = bool(expected) and bool(token) and _timing_safe_equal(token, expected)
        if len(fails) >= AUTH_FAIL_MAX:
            raise CommentError("Too many failed sign-in attempts. Try again later.", 429)
        if not expected:
            raise CommentError("Moderation is not configured.", 503)
        if not valid:
            fails.append(now)
            self._auth_fails[key] = fails
            raise CommentError("Unauthorized.", 401)

    def _enforce_rate_limit(self, ip: str) -> None:
        key = rate_key(ip)
        now = time.time()
        hits = [stamp for stamp in self._hits.get(key, []) if now - stamp < self.rate_window_sec]
        if hits and now - hits[-1] < self.rate_min_interval_sec:
            raise CommentError("Please wait a moment before commenting again.", 429)
        if len(hits) >= self.rate_max:
            raise CommentError("Too many comments. Try again later.", 429)
        hits.append(now)
        self._hits[key] = hits
