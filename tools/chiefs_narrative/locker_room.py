"""Collect and verify player/coach X posts for Preview/Recap.

The writer is not given status IDs. A published locker-room post is kept
only when it resolves on X (publish.x.com oEmbed, with FixTweet as a
cited fallback), the handle is a verified player or coach on Kansas City
or this week's opponent, the post is from this game week, and any quote
is verbatim. If X cannot be fetched, the section stays empty with an
honest "none verified this week" — never an invented post. Dead or
unverified published posts fail the edition gate.
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
FIXTWEET_ENDPOINT = "https://api.fxtwitter.com"
OEMBED_TIMEOUT_SEC = 8
_CT = ZoneInfo("America/Chicago")

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


def fetch_fixtweet(url: str, *, timeout: float = OEMBED_TIMEOUT_SEC) -> dict | None:
    """Cited public fallback: FixTweet (api.fxtwitter.com) status lookup."""
    embed = schema._norm_x_embed(url if isinstance(url, dict) else {"url": url})
    if not embed:
        return None
    handle = embed["account"].lstrip("@")
    status_id = embed["url"].rstrip("/").rsplit("/", 1)[-1]
    try:
        resp = requests.get(
            f"{FIXTWEET_ENDPOINT}/{handle}/status/{status_id}",
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
    tweet = payload.get("tweet") if isinstance(payload, dict) else None
    if not isinstance(tweet, dict):
        return None
    author = tweet.get("author") if isinstance(tweet.get("author"), dict) else {}
    screen = str(author.get("screen_name") or handle)
    text = str(tweet.get("text") or "")
    created = tweet.get("created_timestamp")
    date_html = ""
    if isinstance(created, (int, float)) and created > 0:
        dt = datetime.fromtimestamp(created, tz=timezone.utc).astimezone(_CT)
        date_html = dt.strftime("%B ") + str(dt.day) + dt.strftime(", %Y")
    return {
        "url": embed["url"],
        "author_name": str(author.get("name") or screen),
        "author_url": f"https://x.com/{screen}",
        "html": f"<p>{html_mod.escape(text)}</p> {date_html}",
        "provider_name": "FixTweet",
    }


def resolve_post(url: str, *, oembed_fetch=None, fallback_fetch=None) -> dict | None:
    """oEmbed first; FixTweet only when oEmbed cannot resolve the post."""
    fetch = fetch_oembed if oembed_fetch is None else oembed_fetch
    try:
        payload = fetch(url)
    except Exception:  # noqa: BLE001 — fail closed, never invent
        payload = None
    if payload:
        return payload
    alt = fetch_fixtweet if fallback_fetch is None else fallback_fetch
    if alt is None:
        return None
    try:
        return alt(url)
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
    payload = resolve_post(
        row["url"], oembed_fetch=oembed_fetch, fallback_fetch=fallback_fetch
    )
    if not payload:
        return None
    author = x_embeds.handle_from_author_url(payload.get("author_url") or "")
    if author.lower() != handle.lower():
        return None
    if author.lower() not in PLAYER_COACH_ACCOUNTS:
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
    row["handle"] = f"@{author}" if author else row["handle"]
    row["name"] = allowed["name"]
    row["team"] = allowed["team"]
    row["role"] = allowed["role"]
    if session:
        row["date"] = session.date().isoformat()
        row["dateLabel"] = session.strftime("%a %b ") + str(session.day) + session.strftime(", %Y")
    return row


def verify_all(items, *, oembed_fetch=None, fallback_fetch=None, phase=None):
    kept = []
    errors = []
    for item in items or []:
        verified = verify_post(
            item,
            oembed_fetch=oembed_fetch,
            fallback_fetch=fallback_fetch,
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
        phase=phase,
    )
    narrative["lockerRoom"] = {"posts": kept}
    return narrative


def check_posts(
    narrative: dict | None,
    *,
    oembed_fetch=None,
    fallback_fetch=None,
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
            phase=phase,
        )
        if not verified:
            issues.append(f"locker room: unverified post {item.get('url')}")
    return issues


class UnverifiedLockerRoomError(RuntimeError):
    """A published locker-room post failed resolution or allowlist checks."""
