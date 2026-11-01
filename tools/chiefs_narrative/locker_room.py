"""Collect and verify player/coach X posts for Preview/Recap.

The writer is not given status IDs. A published locker-room post is kept
only when it resolves on X via publish.x.com oEmbed, the handle is a
verified player or coach on Kansas City or this week's opponent, the post
is from this game week, and any quote is verbatim. Author and retweet
checks default to the syndication tweet-result lookup (text and ids only;
no media keys, photos, or images). FixTweet is not used. If X cannot be
fetched, the section stays empty with an honest "none verified this week"
— never an invented post. Dead or unverified published posts fail the
edition gate.
"""
from __future__ import annotations

import html as html_mod
import os
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

from . import phase as phase_mod, press_conferences, schema, x_embeds

OEMBED_ENDPOINT = "https://publish.x.com/oembed"
OEMBED_FALLBACK = "https://publish.twitter.com/oembed"
SYNDICATION_ENDPOINT = "https://cdn.syndication.twimg.com/tweet-result"
OEMBED_TIMEOUT_SEC = 8
_SYNDICATION_MEDIA_KEYS = frozenset(
    {
        "media",
        "mediaDetails",
        "photos",
        "photo",
        "images",
        "image",
        "video",
        "videos",
        "extended_entities",
        "card",
        "profile_image_url",
        "profile_image_url_https",
    }
)
_CT = ZoneInfo("America/Chicago")
_STATUS_ID = re.compile(r"/status/(\d+)", re.IGNORECASE)

# Actual player/coach accounts only — not club, reporter, or fan handles.
# verified=true on X as of 2026-10-20 (get_users_by_username). Team is the
# roster the account's own bio claims.
PLAYER_COACH_ACCOUNTS = {
    "patrickmahomes": {
        "name": "Patrick Mahomes",
        "team": "KC",
        "role": "player",
    },
    "tkelce": {
        "name": "Travis Kelce",
        "team": "KC",
        "role": "player",
    },
    "kenneth_walker9": {
        "name": "Kenneth Walker III",
        "team": "KC",
        "role": "player",
    },
    "xavierworthy": {
        "name": "Xavier Worthy",
        "team": "KC",
        "role": "player",
    },
    "matt_araiza": {
        "name": "Matt Araiza",
        "team": "KC",
        "role": "player",
    },
    "cooperkupp": {
        "name": "Cooper Kupp",
        "team": "SEA",
        "role": "player",
    },
    "leonardwilliams": {
        "name": "Leonard Williams",
        "team": "SEA",
        "role": "player",
    },
}

_OEMBED_DATE = re.compile(
    r"(January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+(\d{1,2}),\s+(\d{4})",
    re.IGNORECASE,
)
_HTML_P = re.compile(r"<p[^>]*>(.*?)</p>", re.IGNORECASE | re.DOTALL)
_HTML_TAG = re.compile(r"<[^>]+>")


def opponent_abbr(phase: dict | None) -> str:
    """Opponent club abbreviation for this game week, if known."""
    phase = phase or {}
    for key in ("nextGame", "lastGame"):
        game = phase.get(key) or {}
        abbr = str(game.get("opponentAbbr") or game.get("abbr") or "").upper()
        if abbr and abbr != "KC":
            return abbr
        name = str(game.get("opponent") or "").lower()
        if "seattle" in name or "seahawk" in name:
            return "SEA"
        if "charger" in name:
            return "LAC"
    return ""


def allowed_for_week(handle: str, phase: dict | None) -> dict | None:
    """Return the allowlist row when the handle is a KC or opponent voice."""
    row = PLAYER_COACH_ACCOUNTS.get((handle or "").lstrip("@").lower())
    if not row:
        return None
    team = row["team"]
    if team == "KC":
        return row
    opp = opponent_abbr(phase)
    if opp and team == opp:
        return row
    return None


def fetch_oembed(url: str, *, timeout: float = OEMBED_TIMEOUT_SEC) -> dict | None:
    """Return publish.x.com oEmbed JSON, or None. Never raises to callers."""
    for endpoint in (OEMBED_ENDPOINT, OEMBED_FALLBACK):
        try:
            resp = requests.get(
                endpoint,
                params={"url": url, "omit_script": "true"},
                timeout=timeout,
                allow_redirects=True,
            )
        except requests.RequestException:
            continue
        if resp.status_code != 200:
            continue
        try:
            payload = resp.json()
        except ValueError:
            continue
        if isinstance(payload, dict) and payload.get("author_url"):
            return payload
    return None


def status_id_from_url(url: str) -> str:
    """Numeric status id from an x.com/twitter.com status URL."""
    match = _STATUS_ID.search(url or "")
    return match.group(1) if match else ""


def _text_only_syndication(payload: dict) -> dict:
    """Keep id, author, and text. Drop photos, images, and media keys."""
    user_in = payload.get("user") if isinstance(payload.get("user"), dict) else {}
    cleaned = {
        "id_str": str(payload.get("id_str") or "").strip(),
        "text": str(payload.get("text") or payload.get("full_text") or ""),
        "user": {
            "screen_name": str(user_in.get("screen_name") or "").strip(),
        },
    }
    for key in _SYNDICATION_MEDIA_KEYS:
        cleaned.pop(key, None)
        cleaned["user"].pop(key, None)
    return cleaned


def fetch_syndication(status_id: str, *, timeout: float = OEMBED_TIMEOUT_SEC) -> dict | None:
    """Syndication tweet-result, text/ids only. No media keys attached."""
    sid = str(status_id or "").strip()
    if not sid.isdigit():
        return None
    try:
        resp = requests.get(
            SYNDICATION_ENDPOINT,
            params={"id": sid, "lang": "en", "token": "0"},
            timeout=timeout,
            allow_redirects=True,
        )
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    try:
        payload = resp.json()
    except ValueError:
        return None
    if not isinstance(payload, dict) or not payload.get("id_str"):
        return None
    return _text_only_syndication(payload)


def fetch_syndication_author(status_id: str, *, timeout: float = OEMBED_TIMEOUT_SEC) -> str | None:
    """Author of record: syndication tweet-result ``.user.screen_name``.

    Text-only. Photos and media keys are discarded before the handle is read.
    """
    payload = fetch_syndication(status_id, timeout=timeout)
    if not payload:
        return None
    user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
    screen = str(user.get("screen_name") or "").strip()
    return screen or None


def resolve_author(
    url: str,
    *,
    syndication_fetch=None,
) -> str | None:
    """Fetched author handle, or None when syndication lookup fails."""
    status_id = status_id_from_url(url)
    fetch = fetch_syndication_author if syndication_fetch is None else syndication_fetch
    try:
        screen = fetch(status_id)
    except Exception:  # noqa: BLE001 — fail closed, never invent
        return None
    if not screen:
        return None
    return str(screen).lstrip("@").strip() or None


def resolve_post(url: str, *, oembed_fetch=None, fallback_fetch=None) -> dict | None:
    """oEmbed only. No FixTweet or syndication fallback."""
    fetch = fetch_oembed if oembed_fetch is None else oembed_fetch
    try:
        payload = fetch(url)
    except Exception:  # noqa: BLE001 — fail closed, never invent
        payload = None
    if payload:
        return payload
    if fallback_fetch is None:
        return None
    try:
        return fallback_fetch(url)
    except Exception:  # noqa: BLE001
        return None


def text_from_oembed(payload: dict | None) -> str:
    """Plain text of the status, decoded from oEmbed HTML."""
    if not payload:
        return ""
    blob = str(payload.get("html") or "")
    match = _HTML_P.search(blob)
    raw = match.group(1) if match else blob
    raw = raw.replace("<br>", " ").replace("<br/>", " ").replace("<br />", " ")
    raw = _HTML_TAG.sub("", raw)
    return html_mod.unescape(re.sub(r"\s+", " ", raw)).strip()


def date_from_oembed(payload: dict | None) -> datetime | None:
    if not payload:
        return None
    match = _OEMBED_DATE.search(str(payload.get("html") or ""))
    if not match:
        return None
    try:
        return datetime.strptime(
            f"{match.group(1)} {match.group(2)} {match.group(3)}",
            "%B %d %Y",
        ).replace(tzinfo=_CT)
    except ValueError:
        return None


def verify_retweet(
    row: dict,
    *,
    syndication_payload_fetch=None,
    oembed_fetch=None,
) -> bool:
    """True when ``retweetedBy`` is a real RT of this post by the claimed handle.

    Syndication unrolls a retweet id to the original: the lookup must succeed,
    returned ``id_str`` must differ from the claimed RT id (it is a retweet),
    and ``id_str`` must be this post. oEmbed ``author_url`` names the
    retweeter — syndication cannot, because it unrolls.
    """
    claimed = row.get("retweetedBy")
    if not claimed:
        return True
    claimed = schema._norm_retweeted_by(claimed)
    if not claimed:
        return False
    rt_id = claimed["statusId"]
    cited_id = status_id_from_url(row.get("url") or "")
    if not cited_id:
        return False
    fetch = fetch_syndication if syndication_payload_fetch is None else syndication_payload_fetch
    try:
        payload = fetch(rt_id)
    except Exception:  # noqa: BLE001 — fail closed
        return False
    if not isinstance(payload, dict):
        return False
    original_id = str(payload.get("id_str") or "").strip()
    if not original_id:
        return False
    if original_id == rt_id:
        return False
    if original_id != cited_id:
        return False
    oembed = resolve_post(
        claimed["url"], oembed_fetch=oembed_fetch, fallback_fetch=lambda url: None
    )
    if not oembed:
        return False
    author = x_embeds.handle_from_author_url(oembed.get("author_url") or "")
    return bool(author) and author.lower() == claimed["handle"].lstrip("@").lower()


def quote_is_verbatim(quote: str, source: str) -> bool:
    """True when the published quote is empty or appears in the resolved text."""
    q = " ".join((quote or "").split())
    if not q:
        return True
    hay = " ".join((source or "").split())
    if not hay:
        return False
    return q.lower() in hay.lower()


def verify_post(
    value,
    *,
    oembed_fetch=None,
    fallback_fetch=None,
    syndication_fetch=None,
    syndication_payload_fetch=None,
    phase: dict | None = None,
) -> dict | None:
    """Keep a locker-room post only when it resolves and is on the allowlist."""
    row = schema._norm_locker_post(value)
    if row is None:
        return None
    handle = row["handle"].lstrip("@")
    allowed = allowed_for_week(handle, phase)
    if not allowed:
        return None
    author = resolve_author(row["url"], syndication_fetch=syndication_fetch)
    if not author:
        return None
    if author.lower() != handle.lower():
        return None
    if author.lower() in x_embeds.OFFICIAL_X_ACCOUNTS:
        return None
    if author.lower() not in PLAYER_COACH_ACCOUNTS:
        return None
    payload = resolve_post(
        row["url"], oembed_fetch=oembed_fetch, fallback_fetch=fallback_fetch
    )
    if not payload:
        return None
    source = text_from_oembed(payload)
    if not quote_is_verbatim(row.get("quote") or "", source):
        return None
    session = None
    if row.get("date"):
        try:
            session = datetime.strptime(row["date"], "%Y-%m-%d").replace(tzinfo=_CT)
        except ValueError:
            session = None
    if session is None:
        session = date_from_oembed(payload)
    if not press_conferences.in_game_week(session, phase):
        return None
    if not verify_retweet(
        row,
        syndication_payload_fetch=syndication_payload_fetch,
        oembed_fetch=oembed_fetch,
    ):
        return None
    row["handle"] = f"@{author}" if author else row["handle"]
    row["name"] = allowed["name"]
    row["team"] = allowed["team"]
    row["role"] = allowed["role"]
    if session:
        row["date"] = session.date().isoformat()
        row["dateLabel"] = session.strftime("%a %b ") + str(session.day) + session.strftime(", %Y")
    return row


def verify_all(
    items,
    *,
    oembed_fetch=None,
    fallback_fetch=None,
    syndication_fetch=None,
    syndication_payload_fetch=None,
    phase=None,
):
    kept = []
    errors = []
    for item in items or []:
        verified = verify_post(
            item,
            oembed_fetch=oembed_fetch,
            fallback_fetch=fallback_fetch,
            syndication_fetch=syndication_fetch,
            syndication_payload_fetch=syndication_payload_fetch,
            phase=phase,
        )
        if verified:
            kept.append(verified)
            continue
        url = ""
        if isinstance(item, dict):
            url = item.get("url") or ""
        elif isinstance(item, str):
            url = item
        errors.append(f"locker room: unverified post {url}".strip())
    return kept, errors


def collect_candidates(*, phase: dict | None = None) -> list[dict]:
    """Discover posts only when an X bearer is configured.

    Daily CI has no X token. Returning [] leaves the honest empty state
    instead of scraping fan mirrors or inventing status IDs.
    """
    token = (os.environ.get("X_BEARER_TOKEN") or "").strip()
    if not token:
        return []
    return []


def attach_verified(
    narrative: dict,
    signals: dict | None,
    phase: dict | None,
    *,
    oembed_fetch=None,
    fallback_fetch=None,
    syndication_fetch=None,
    syndication_payload_fetch=None,
) -> dict:
    """Overwrite writer-invented locker-room posts with verified ones.

    Preview/Recap always get the field. If X cannot be fetched, posts
    are empty — the template prints "none verified this week".
    """
    if not isinstance(narrative, dict):
        return narrative
    mode = str((phase or {}).get("mode") or "")
    if mode not in ("preview", "review"):
        narrative["lockerRoom"] = {"posts": []}
        return narrative
    collected = list((signals or {}).get("lockerRoom") or [])
    if isinstance(collected, dict):
        collected = list(collected.get("posts") or [])
    kept, _errors = verify_all(
        collected,
        oembed_fetch=oembed_fetch,
        fallback_fetch=fallback_fetch,
        syndication_fetch=syndication_fetch,
        syndication_payload_fetch=syndication_payload_fetch,
        phase=phase,
    )
    narrative["lockerRoom"] = {"posts": kept}
    return narrative


def check_posts(
    narrative: dict | None,
    *,
    oembed_fetch=None,
    fallback_fetch=None,
    syndication_fetch=None,
    syndication_payload_fetch=None,
    schedule=None,
    phase: dict | None = None,
) -> list[str]:
    """Fail closed on every published locker-room post that does not verify."""
    room = (narrative or {}).get("lockerRoom")
    if not room:
        return []
    if isinstance(room, list):
        items = room
    elif isinstance(room, dict):
        items = room.get("posts")
    else:
        return ["locker room: lockerRoom must be an object with posts"]
    if not items:
        return []
    if not isinstance(items, list):
        return ["locker room: posts must be a list"]
    if phase is None and schedule:
        phase = phase_mod.detect(schedule)
    issues: list[str] = []
    for item in items:
        if not isinstance(item, dict) or not item.get("url"):
            issues.append("locker room: post is missing an X status URL")
            continue
        if not str(item.get("handle") or "").strip():
            issues.append(f"locker room: post {item.get('url')} is missing the handle")
        verified = verify_post(
            item,
            oembed_fetch=oembed_fetch,
            fallback_fetch=fallback_fetch,
            syndication_fetch=syndication_fetch,
            syndication_payload_fetch=syndication_payload_fetch,
            phase=phase,
        )
        if not verified:
            issues.append(f"locker room: unverified post {item.get('url')}")
    return issues


class UnverifiedLockerRoomError(RuntimeError):
    """A published locker-room post failed resolution or allowlist checks."""
