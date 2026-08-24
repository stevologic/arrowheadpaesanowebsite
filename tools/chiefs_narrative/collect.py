"""Collect live Chiefs signals: 2026 schedule + public news wires.

Only the Python standard library plus ``requests`` is required. RSS/Atom is
parsed with ``xml.etree`` so there is no ``feedparser`` dependency to install in
CI. Every network call fails soft: if a feed is down we simply skip it and keep
going, because the pipeline must never hard-fail the daily automation.
"""
from __future__ import annotations

import html
import json
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
# ESPN seasonType ids: 1 = preseason, 2 = regular, 3 = postseason.
_SEASON_TYPES = (1, 2, 3)


def normalize_iso(raw: str) -> str:
    """ESPN sends ``2026-08-15T20:00Z``; Hugo's time.AsTime needs seconds."""
    if not raw:
        return ""
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:  # noqa: BLE001
        return raw


def kickoff_label(iso: str) -> str:
    """Human kickoff in US Central, e.g. 'Sat, Aug 15 · 3:00 PM CT'."""
    if not iso:
        return ""
    try:
        from zoneinfo import ZoneInfo

        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local = dt.astimezone(ZoneInfo("America/Chicago"))
        hour = local.strftime("%I").lstrip("0") or "12"
        return f"{local.strftime('%a, %b')} {local.day} · {hour}:{local.strftime('%M %p')} CT"
    except Exception:  # noqa: BLE001
        return ""


def load_cached_schedule() -> list[dict]:
    """Last good slate written to data/schedule_2026.json."""
    try:
        data = json.loads(config.SCHEDULE_JSON.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(data, list):
        return []
    return [enrich_game(g) for g in data if isinstance(g, dict)]


def enrich_game(game: dict) -> dict:
    """Fill derived display fields on a normalized game dict."""
    if not isinstance(game, dict):
        return {}
    out = dict(game)
    if out.get("date"):
        out["date"] = normalize_iso(out["date"])
        if not out.get("kickoff"):
            label = kickoff_label(out["date"])
            if label:
                out["kickoff"] = label
    out.setdefault("inProgress", False)
    return out


def _to_int(value):
    """Parse an ESPN score. Never invent: missing/unreadable values stay None.

    The web API sends ``{"value": 12.0, "displayValue": "12"}``; older payloads
    send a bare string or int. A dict we cannot read is not a 0.
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, dict):
        if value.get("value") is not None and value.get("value") != "":
            return _to_int(value.get("value"))
        return _to_int(value.get("displayValue"))
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def _broadcast_name(comp: dict) -> str:
    broadcasts = comp.get("broadcasts") or []
    if not broadcasts:
        # Some web-API payloads put the call on geoBroadcasts / media.
        geos = comp.get("geoBroadcasts") or []
        if geos:
            media = geos[0].get("media") or {}
            return media.get("shortName") or media.get("name") or ""
        return ""
    media = broadcasts[0]
    tv = ""
    if isinstance(media, dict):
        inner = media.get("media") if isinstance(media.get("media"), dict) else {}
        tv = inner.get("shortName") or media.get("shortName") or ""
        if not tv and media.get("names"):
            tv = ", ".join(media["names"])
    return tv


def parse_event(event: dict) -> dict | None:
    """Normalize one ESPN event into the slate shape the site and phase use."""
    try:
        comp = event["competitions"][0]
        competitors = comp["competitors"]
        home = next(c for c in competitors if c["homeAway"] == "home")
        away = next(c for c in competitors if c["homeAway"] == "away")
        kc_is_home = home["team"].get("abbreviation") == "KC"
        kc = home if kc_is_home else away
        opp = away if kc_is_home else home
        status = (comp.get("status") or {}).get("type") or {}
        date = event.get("date") or comp.get("date")
        state = (status.get("state") or "").lower()
        game = {
            "id": event.get("id"),
            "week": (event.get("week") or {}).get("number"),
            "seasonType": (event.get("seasonType") or {}).get("abbreviation", "reg"),
            "date": date,
            "opponent": opp["team"].get("displayName"),
            "opponentAbbr": opp["team"].get("abbreviation"),
            "opponentShort": opp["team"].get("shortDisplayName")
            or opp["team"].get("name"),
            "homeAway": "home" if kc_is_home else "away",
            "venue": (comp.get("venue") or {}).get("fullName", ""),
            "tv": _broadcast_name(comp),
            "completed": bool(status.get("completed")),
            "inProgress": state == "in" and not status.get("completed"),
            "kcScore": _to_int(kc.get("score")),
            "oppScore": _to_int(opp.get("score")),
        }
        return enrich_game(game)
    except Exception as exc:  # noqa: BLE001 - skip malformed events
        print(f"  [collect] warning: skipped a schedule event: {exc}")
        return None


def fetch_schedule(season: int = None) -> list[dict]:
    """Return a normalized list of the Chiefs' games for the season.

    Pulls preseason, regular season, and postseason from ESPN's web API and
    merges them. Each game: {week, seasonType, date (ISO), kickoff, opponent,
    opponentAbbr, homeAway, venue, tv, completed, kcScore, oppScore}.
    """
    season = season or config.TEAM["season"]
    team_id = config.TEAM["espn_id"]
    games: list[dict] = []
    seen: set[str] = set()
    for stype in _SEASON_TYPES:
        url = config.ESPN_SCHEDULE.format(
            espn_id=team_id, season=season, stype=stype
        )
        data = _get_json(url)
        if not data:
            continue
        for event in data.get("events") or []:
            game = parse_event(event)
            if not game:
                continue
            key = str(game.get("id") or game.get("date") or "")
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            games.append(game)

    games.sort(key=lambda g: g.get("date") or "")
    return games


def merge_cached_scores(live: list[dict], cached: list[dict]) -> list[dict]:
    """Keep a previously published final if ESPN omits the score this fetch.

    Copies scores only. Never invents kickoffs, networks, or results.
    """
    prev = {str(g.get("id")): g for g in cached if g.get("id")}
    merged: list[dict] = []
    for game in live:
        out = dict(game)
        old = prev.get(str(out.get("id") or ""))
        if old and out.get("kcScore") is None and old.get("kcScore") is not None:
            out["kcScore"] = old["kcScore"]
            if out.get("oppScore") is None:
                out["oppScore"] = old.get("oppScore")
        merged.append(out)
    return merged


def resolve_schedule(season: int = None) -> list[dict]:
    """Live ESPN slate, or the last checked-in slate if ESPN is down."""
    cached = load_cached_schedule()
    live = fetch_schedule(season)
    if live:
        return merge_cached_scores(live, cached)
    if cached:
        print(f"  [collect] live schedule empty; using cached {len(cached)} games")
        return cached
    print("  [collect] warning: no live or cached schedule")
    return []


def write_schedule(games: list[dict]) -> bool:
    """Persist the slate the site reads. Refuse to overwrite with an empty list."""
    if not games:
        return False
    config.ensure_dirs()
    config.SCHEDULE_JSON.write_text(
        json.dumps(games, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return True


def refresh_schedule(season: int = None) -> list[dict]:
    """Fetch (or fall back) and write ``data/schedule_2026.json``."""
    games = resolve_schedule(season)
    if write_schedule(games):
        print(f"  [collect] wrote {len(games)} games to {config.SCHEDULE_JSON.name}")
    else:
        print("  [collect] warning: no slate to write; left last good file in place")
    return games


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


# ---------------------------------------------------------------------------
# Last-game recap (ESPN summary — fail soft)
# ---------------------------------------------------------------------------
_RECAP_STAT_NAMES = (
    "totalYards",
    "netPassingYards",
    "rushingYards",
    "turnovers",
    "firstDowns",
    "thirdDownEff",
    "possessionTime",
    "sacks",
)


def _box_stats(team_block: dict) -> dict:
    stats = {}
    for row in team_block.get("statistics") or []:
        if not isinstance(row, dict):
            continue
        name = row.get("name") or ""
        if name in _RECAP_STAT_NAMES:
            stats[name] = str(row.get("displayValue") or row.get("value") or "").strip()
    return stats


def _scoring_lines(plays: list, limit: int = 8) -> list[str]:
    out = []
    for play in plays or []:
        if not isinstance(play, dict):
            continue
        team = ((play.get("team") or {}).get("abbreviation")) or ""
        text = (play.get("text") or "").strip()
        if not text:
            continue
        period = (play.get("period") or {}).get("number")
        clock = (play.get("clock") or {}).get("displayValue") or ""
        q = f"Q{period}" if period else ""
        stamp = " ".join(p for p in (q, clock) if p)
        prefix = f"{team} " if team else ""
        line = f"{prefix}{text}"
        if stamp:
            line = f"{stamp} — {line}"
        out.append(line)
        if len(out) >= limit:
            break
    return out


def fetch_game_recap(event_id: str) -> dict:
    """Box-score snapshot + scoring plays for one ESPN event.

    Used to ground last-game analysis. Missing or partial payloads become {}.
    Never invents a score that ESPN did not send.
    """
    if not event_id:
        return {}
    data = _get_json(config.ESPN_SUMMARY.format(event=event_id))
    if not isinstance(data, dict):
        return {}

    recap = {"eventId": str(event_id), "kc": {}, "opp": {}, "scoring": [], "leaders": []}
    box = data.get("boxscore") or {}
    for block in box.get("teams") or []:
        if not isinstance(block, dict):
            continue
        abbr = ((block.get("team") or {}).get("abbreviation") or "").upper()
        stats = _box_stats(block)
        if abbr == "KC":
            recap["kc"] = stats
        elif abbr:
            recap["opp"] = stats
            recap["oppAbbr"] = abbr

    recap["scoring"] = _scoring_lines(data.get("scoringPlays") or [])

    for group in data.get("leaders") or []:
        if not isinstance(group, dict):
            continue
        team = ((group.get("team") or {}).get("abbreviation") or "").upper()
        if team != "KC":
            continue
        for leader in group.get("leaders") or []:
            if not isinstance(leader, dict):
                continue
            people = leader.get("leaders") or []
            if not people or not isinstance(people[0], dict):
                continue
            athlete = people[0].get("athlete") or {}
            name = athlete.get("displayName") or ""
            value = people[0].get("displayValue") or ""
            category = leader.get("displayName") or leader.get("name") or ""
            if name and value:
                recap["leaders"].append(
                    {"player": name, "category": category, "value": value}
                )
            if len(recap["leaders"]) >= 4:
                break
        break

    if not recap["kc"] and not recap["opp"] and not recap["scoring"] and not recap["leaders"]:
        return {}
    return recap


def collect_all(season: int = None) -> dict:
    """Gather every live signal into one bundle for the writer + phase logic."""
    print("  [collect] fetching 2026 schedule…")
    schedule = resolve_schedule(season)
    print(f"  [collect] {len(schedule)} games loaded")
    print("  [collect] fetching news wires…")
    news = fetch_news()
    print(f"  [collect] {len(news)} Chiefs headlines loaded")
    return {
        "collectedAt": config.iso_now(),
        "schedule": schedule,
        "news": news,
    }
