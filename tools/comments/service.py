"""In-memory comment store with the same contract as the Cloudflare Worker.

Kept offline (no network) so CI can exercise posting, the empty list,
honeypot/rate-limit rejection, and hide/delete without keys.
"""
from __future__ import annotations

import hmac
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
AUTH_FAIL_WINDOW_SEC = 15 * 60
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
    _comments: dict[str, dict[str, Any]] = field(default_factory=dict)
    _hits: dict[str, list[float]] = field(default_factory=dict)
    _auth_fails: dict[str, list[float]] = field(default_factory=dict)

    def empty_state(self) -> str:
        return EMPTY_STATE

    def list_public(self, slug: str) -> list[dict[str, Any]]:
        slug = _clean(slug, 80)
        rows = [
            public_comment(row)
            for row in self._comments.values()
            if row["slug"] == slug and not row.get("hidden") and not row.get("deleted")
        ]
        rows.sort(key=lambda row: row["createdAt"])
        return rows

    def list_admin(self, slug: str | None = None) -> list[dict[str, Any]]:
        rows = [
            public_comment(row)
            for row in self._comments.values()
            if (slug is None or row["slug"] == slug) and not row.get("deleted")
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
        validate_turnstile(turnstile_token, self.turnstile_mode)
        if str(payload.get(HONEYPOT_FIELD) or "").strip():
            # Pretend success so bots do not retry, but do not store.
            return None

        slug = _clean(payload.get("slug"), 80)
        name = _clean(payload.get("name"), MAX_NAME)
        body = _clean(payload.get("body"), MAX_BODY)
        if not SLUG_RE.match(slug):
            raise CommentError("Unknown story.")
        if len(name) < MIN_NAME:
            raise CommentError("Name is required.")
        if len(body) < MIN_BODY:
            raise CommentError("Comment is too short.")

        self._enforce_rate_limit(ip)
        row = {
            "id": uuid.uuid4().hex,
            "slug": slug,
            "name": name,
            "body": body,
            "createdAt": _utc_now(),
            "hidden": False,
            "deleted": False,
        }
        self._comments[row["id"]] = row
        return public_comment(row)

    def hide(self, comment_id: str, hidden: bool = True) -> dict[str, Any]:
        row = self._comments.get(comment_id)
        if not row or row.get("deleted"):
            raise CommentError("Comment not found.", 404)
        row["hidden"] = bool(hidden)
        return public_comment(row)

    def delete(self, comment_id: str) -> None:
        row = self._comments.get(comment_id)
        if not row or row.get("deleted"):
            raise CommentError("Comment not found.", 404)
        row["deleted"] = True

    def require_admin(self, provided: str | None, ip: str = "unknown") -> None:
        now = time.time()
        fails = [stamp for stamp in self._auth_fails.get(ip, []) if now - stamp < AUTH_FAIL_WINDOW_SEC]
        if len(fails) >= AUTH_FAIL_MAX:
            raise CommentError("Too many failed sign-in attempts. Try again later.", 429)
        token = (provided or "").removeprefix("Bearer ").strip()
        expected = (self.admin_token or "").strip()
        if not expected:
            raise CommentError("Moderation is not configured.", 503)
        if not token or not hmac.compare_digest(token, expected):
            fails.append(now)
            self._auth_fails[ip] = fails
            raise CommentError("Unauthorized.", 401)

    def _enforce_rate_limit(self, ip: str) -> None:
        key = (ip or "unknown").strip() or "unknown"
        now = time.time()
        hits = [stamp for stamp in self._hits.get(key, []) if now - stamp < self.rate_window_sec]
        if hits and now - hits[-1] < self.rate_min_interval_sec:
            raise CommentError("Please wait a moment before commenting again.", 429)
        if len(hits) >= self.rate_max:
            raise CommentError("Too many comments. Try again later.", 429)
        hits.append(now)
        self._hits[key] = hits
