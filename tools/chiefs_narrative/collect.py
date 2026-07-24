"""Collect live Chiefs signals: 2026 schedule + public news wires.

Only the Python standard library plus ``requests`` is required. RSS/Atom is
parsed with ``xml.etree`` so there is no ``feedparser`` dependency to install in
CI. Every network call fails soft: if a feed is down we simply skip it and keep
going, because the pipeline must never hard-fail the daily automation.
"""
from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

import requests

from . import config


def _get(url: str) -> str | None:
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": config.USER_AGENT, "Accept": "*/*"},
            timeout=config.HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.text
    except Exception as exc:  # noqa: BLE001 - fail soft on any network error
        print(f"  [collect] warning: could not fetch {url}: {exc}")
        return None


def _get_json(url: str):
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": config.USER_AGENT, "Accept": "application/json"},
            timeout=config.HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        print(f"  [collect] warning: could not fetch JSON {url}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------
def fetch_schedule(season: int = None) -> list[dict]:
    """Return a normalized list of the Chiefs' games for the season.

    Each game: {week, seasonType, date (ISO), opponent, opponentAbbr, homeAway,
    venue, tv, completed, kcScore, oppScore}.
    """
    season = season or config.TEAM["season"]
    data = _get_json(config.ESPN_SCHEDULE.format(season=season))
    games: list[dict] = []
    if not data or "events" not in data:
        return games

    for event in data.get("events", []):
        try:
            comp = event["competitions"][0]
            competitors = comp["competitors"]
            home = next(c for c in competitors if c["homeAway"] == "home")
            away = next(c for c in competitors if c["homeAway"] == "away")
            kc_is_home = home["team"].get("abbreviation") == "KC"
            kc = home if kc_is_home else away
            opp = away if kc_is_home else home
            broadcasts = comp.get("broadcasts") or []
            tv = ""
            if broadcasts:
                media = broadcasts[0]
                tv = media.get("media", {}).get("shortName") or ""
                if not tv and media.get("names"):
                    tv = ", ".join(media["names"])
            status = comp.get("status", {}).get("type", {})
            completed = bool(status.get("completed"))
            games.append(
                {
                    "id": event.get("id"),
                    "week": event.get("week", {}).get("number"),
                    "seasonType": event.get("seasonType", {}).get("abbreviation", "reg"),
                    "date": event.get("date"),
                    "opponent": opp["team"].get("displayName"),
                    "opponentAbbr": opp["team"].get("abbreviation"),
                    "opponentShort": opp["team"].get("shortDisplayName")
                    or opp["team"].get("name"),
                    "homeAway": "home" if kc_is_home else "away",
                    "venue": comp.get("venue", {}).get("fullName", ""),
                    "tv": tv,
                    "completed": completed,
                    "kcScore": _to_int(kc.get("score")),
                    "oppScore": _to_int(opp.get("score")),
                }
            )
        except Exception as exc:  # noqa: BLE001 - skip malformed events
            print(f"  [collect] warning: skipped a schedule event: {exc}")
            continue

    games.sort(key=lambda g: g.get("date") or "")
    return games


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# News wires (RSS / Atom)
# ---------------------------------------------------------------------------
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_CHIEFS_HINT = re.compile(
    r"chiefs|mahomes|kansas city|reid|kelce|spagnuolo|bieniemy|arrowhead|worthy|"
    r"pacheco|rice|kingdom|chris jones|karlaftis|smith|butker",
    re.IGNORECASE,
)


def _clean(text: str, limit: int = 320) -> str:
    if not text:
        return ""
    text = html.unescape(text)
    text = _TAG_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def _parse_date(raw: str | None):
    if not raw:
        return None
    raw = raw.strip()
    for parser in (_parse_rfc822, _parse_iso):
        dt = parser(raw)
        if dt:
            return dt
    return None


def _parse_rfc822(raw: str):
    try:
        dt = parsedate_to_datetime(raw)
        if dt and dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:  # noqa: BLE001
        return None


def _parse_iso(raw: str):
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:  # noqa: BLE001
        return None


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _parse_feed(xml_text: str, publisher: str) -> list[dict]:
    items: list[dict] = []
    try:
        root = ET.fromstring(xml_text)
    except Exception as exc:  # noqa: BLE001
        print(f"  [collect] warning: could not parse feed for {publisher}: {exc}")
        return items

    # Works for both RSS (<item>) and Atom (<entry>).
    nodes = [n for n in root.iter() if _strip_ns(n.tag) in ("item", "entry")]
    for node in nodes:
        fields: dict[str, str] = {}
        link = ""
        for child in node:
            name = _strip_ns(child.tag)
            if name == "link":
                # Atom links carry the URL in an attribute.
                href = child.get("href")
                link = href or (child.text or "").strip() or link
            elif name in ("title", "description", "summary", "content", "pubDate", "published", "updated"):
                fields.setdefault(name, (child.text or "").strip())
        title = _clean(fields.get("title", ""), 200)
        if not title:
            continue
        summary = _clean(
            fields.get("description") or fields.get("summary") or fields.get("content", ""),
            320,
        )
        published = (
            fields.get("pubDate")
            or fields.get("published")
            or fields.get("updated")
        )
        dt = _parse_date(published)
        items.append(
            {
                "title": title,
                "summary": summary,
                "url": link,
                "publisher": publisher,
                "published": dt.isoformat() if dt else None,
                "_sort": dt.timestamp() if dt else 0.0,
            }
        )
    return items


def fetch_news(max_per_feed: int = 8, max_total: int = 24) -> list[dict]:
    """Fetch and Chiefs-filter recent headlines across all configured feeds."""
    collected: list[dict] = []
    for feed in config.NEWS_FEEDS:
        xml_text = _get(feed["url"])
        if not xml_text:
            continue
        items = _parse_feed(xml_text, feed["publisher"])
        # For general NFL feeds keep only Chiefs-relevant items; team feeds pass through.
        team_feed = feed["publisher"] in ("Chiefs.com", "Arrowhead Pride", "Arrowhead Addict")
        kept = []
        for it in items:
            blob = f"{it['title']} {it['summary']}"
            if team_feed or _CHIEFS_HINT.search(blob):
                kept.append(it)
            if len(kept) >= max_per_feed:
                break
        collected.extend(kept)

    # De-duplicate by title, newest first.
    seen: set[str] = set()
    unique: list[dict] = []
    for it in sorted(collected, key=lambda x: x["_sort"], reverse=True):
        key = it["title"].lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append({k: v for k, v in it.items() if k != "_sort"})
        if len(unique) >= max_total:
            break
    return unique


def collect_all(season: int = None) -> dict:
    """Gather every live signal into one bundle for the writer + phase logic."""
    print("  [collect] fetching 2026 schedule…")
    schedule = fetch_schedule(season)
    print(f"  [collect] {len(schedule)} games loaded")
    print("  [collect] fetching news wires…")
    news = fetch_news()
    print(f"  [collect] {len(news)} Chiefs headlines loaded")
    return {
        "collectedAt": config.iso_now(),
        "schedule": schedule,
        "news": news,
    }
