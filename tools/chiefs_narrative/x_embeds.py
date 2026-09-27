"""Verify official X status embeds before a generated edition can keep them.

Schema normalize only checks URL shape. Generation must not publish a
hallucinated status ID: every embed is stripped unless publish.twitter.com
oEmbed returns 200 and the author handle is on the official allowlist.
Default is to strip (network errors, non-200, unknown authors).
"""
from __future__ import annotations

from urllib.parse import urlparse

import requests

from . import schema

OEMBED_ENDPOINT = "https://publish.twitter.com/oembed"
OEMBED_TIMEOUT_SEC = 8

# Official team, league, and Chiefs-beat reporter handles. Compared lowercase
# without the leading @. Opponent clubs are included so a verified road-game
# clip can survive; random accounts cannot.
OFFICIAL_X_ACCOUNTS = frozenset(
    {
        # League
        "nfl",
        "nflnetwork",
        "nflonfox",
        "nfloncbs",
        "espnnfl",
        # 32 clubs
        "cardinals",
        "atlantafalcons",
        "ravens",
        "buffalobills",
        "panthers",
        "chicagobears",
        "bengals",
        "browns",
        "dallascowboys",
        "broncos",
        "lions",
        "packers",
        "texans",
        "colts",
        "jaguars",
        "chiefs",
        "raiders",
        "chargers",
        "rams",
        "miamidolphins",
        "vikings",
        "patriots",
        "saints",
        "giants",
        "nyjets",
        "eagles",
        "steelers",
        "49ers",
        "seahawks",
        "buccaneers",
        "tennesseetitans",
        "commanders",
        # Chiefs beat / club-adjacent reporters
        "byherbie",
        "adamteicher",
        "mattderrick",
        "nate_taylor",
        "arrowheadpride",
        "arrowheadaddict",
        "chiefsreporter",
        "petesweeney",
        "charlesgoldman",
    }
)


def handle_from_author_url(author_url: str) -> str:
    path = urlparse(author_url or "").path.strip("/")
    handle = path.split("/")[0] if path else ""
    return handle.lstrip("@").lower()


def fetch_oembed(url: str, *, timeout: float = OEMBED_TIMEOUT_SEC) -> dict | None:
    """Return the oEmbed JSON object or None. Never raises to callers."""
    try:
        resp = requests.get(
            OEMBED_ENDPOINT,
            params={"url": url, "omit_script": "true"},
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
    return payload if isinstance(payload, dict) else None


def verify_x_embed(value, *, oembed_fetch=None) -> dict | None:
    """Keep an embed only when oEmbed 200s and the author is allowlisted."""
    embed = schema._norm_x_embed(value)
    if not embed:
        return None
    fetch = fetch_oembed if oembed_fetch is None else oembed_fetch
    try:
        payload = fetch(embed["url"])
    except Exception:  # noqa: BLE001 — default is strip, never fail generation
        return None
    if not payload:
        return None
    handle = handle_from_author_url(payload.get("author_url") or "")
    if handle not in OFFICIAL_X_ACCOUNTS:
        return None
    embed["account"] = f"@{handle}" if handle else embed["account"]
    return embed


def strip_unverified_embeds(narrative: dict, *, oembed_fetch=None) -> dict:
    """Drop player and key-play embeds that fail oEmbed + allowlist checks.

    Default ``oembed_fetch`` hits the live endpoint. Pass a stub in tests.
    Missing or failed verification strips the field — never invents a URL.
    """
    if not isinstance(narrative, dict):
        return narrative
    kept = []
    for item in narrative.get("playerEmbeds") or []:
        verified = verify_x_embed(item, oembed_fetch=oembed_fetch)
        if verified:
            kept.append(verified)
    narrative["playerEmbeds"] = kept

    review = narrative.get("lastGameReview")
    if isinstance(review, dict) and isinstance(review.get("analysis"), list):
        cleaned = []
        for para in review["analysis"]:
            if not isinstance(para, dict):
                cleaned.append(para)
                continue
            row = dict(para)
            embed = row.pop("embed", None)
            if embed:
                verified = verify_x_embed(embed, oembed_fetch=oembed_fetch)
                if verified:
                    row["embed"] = verified
            cleaned.append(row)
        review["analysis"] = cleaned
    return narrative
