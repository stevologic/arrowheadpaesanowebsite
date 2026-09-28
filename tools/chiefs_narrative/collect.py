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
from zoneinfo import ZoneInfo

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
        local = _central(iso)
        hour = local.strftime("%I").lstrip("0") or "12"
        return f"{local.strftime('%a, %b')} {local.day} · {hour}:{local.strftime('%M %p')} CT"
    except Exception:  # noqa: BLE001
        return ""


def _central(iso: str) -> datetime:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo("America/Chicago"))


def local_date_label(iso: str) -> str:
    """Calendar date in US Central, e.g. 'Mon Sep 14'."""
    if not iso:
        return ""
    try:
        local = _central(iso)
        return f"{local.strftime('%a')} {local.strftime('%b')} {local.day}"
    except Exception:  # noqa: BLE001
        return ""


def kickoff_prompt(iso: str) -> str:
    """Prompt-facing kickoff in CT, e.g. 'Sun Oct 4, 3:25 PM CT'."""
    if not iso:
        return ""
    try:
        local = _central(iso)
        hour = local.strftime("%I").lstrip("0") or "12"
        return f"{local.strftime('%a %b')} {local.day}, {hour}:{local.strftime('%M %p')} CT"
    except Exception:  # noqa: BLE001
        return ""


def kickoff_part_of_day(iso: str) -> str:
    """morning / midday / afternoon / evening / night from the CT kickoff hour."""
    if not iso:
        return ""
    try:
        hour = _central(iso).hour
    except Exception:  # noqa: BLE001
        return ""
    if hour < 11:
        return "morning"
    if hour < 14:
        return "midday"
    if hour < 17:
        return "afternoon"
    if hour < 19:
        return "evening"
    return "night"


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


_YARDS_RE = re.compile(r"(\d+)\s*(?:Yd|Yds|yard|yards)\b", re.IGNORECASE)


def _play_type(play: dict) -> str:
    scoring = play.get("scoringType")
    if isinstance(scoring, dict):
        label = (
            scoring.get("abbreviation")
            or scoring.get("displayName")
            or scoring.get("name")
            or ""
        )
        if label:
            return str(label).strip()
    typ = play.get("type")
    if isinstance(typ, dict):
        return str(typ.get("text") or typ.get("abbreviation") or "").strip()
    return ""


def _play_player(play: dict) -> str:
    athletes = play.get("athletesInvolved") or []
    names = [
        (a.get("displayName") or "").strip()
        for a in athletes
        if isinstance(a, dict) and (a.get("displayName") or "").strip()
    ]
    if not names:
        return ""
    typ = _play_type(play).lower()
    # Passing TDs list passer then receiver; the scorer is the receiver.
    if "pass" in typ and len(names) >= 2:
        return names[1]
    return names[0]


def _play_yards(play: dict):
    raw = play.get("statYardage")
    if raw is not None and raw != "":
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    text = play.get("text") or ""
    match = _YARDS_RE.search(text)
    return int(match.group(1)) if match else None


def _kc_home_from_summary(data: dict) -> bool | None:
    comps = ((data.get("header") or {}).get("competitions") or [])
    if comps and isinstance(comps[0], dict):
        for row in comps[0].get("competitors") or []:
            if not isinstance(row, dict):
                continue
            abbr = ((row.get("team") or {}).get("abbreviation") or "").upper()
            if abbr == "KC":
                return row.get("homeAway") == "home"
    for block in (data.get("boxscore") or {}).get("teams") or []:
        if not isinstance(block, dict):
            continue
        abbr = ((block.get("team") or {}).get("abbreviation") or "").upper()
        if abbr == "KC" and block.get("homeAway") in ("home", "away"):
            return block.get("homeAway") == "home"
    return None


def parse_scoring_plays(plays: list, kc_home: bool | None = None) -> list[dict]:
    """Structured ESPN scoring plays for the prompt and the fact-check."""
    out = []
    for play in plays or []:
        if not isinstance(play, dict):
            continue
        period = (play.get("period") or {}).get("number")
        clock = (play.get("clock") or {}).get("displayValue") or ""
        team = ((play.get("team") or {}).get("abbreviation") or "").upper()
        away = _to_int(play.get("awayScore"))
        home = _to_int(play.get("homeScore"))
        kc_score = opp_score = None
        if kc_home is True:
            kc_score, opp_score = home, away
        elif kc_home is False:
            kc_score, opp_score = away, home
        score_after = ""
        if kc_score is not None and opp_score is not None:
            score_after = f"KC {kc_score}–{opp_score}"
        row = {
            "quarter": period,
            "clock": clock,
            "type": _play_type(play),
            "player": _play_player(play),
            "yards": _play_yards(play),
            "team": team,
            "scoreAfter": score_after,
            "kcScore": kc_score,
            "oppScore": opp_score,
        }
        if row["type"] or row["player"] or (play.get("text") or "").strip() or score_after:
            out.append(row)
    return out


_DRIVE_RESULTS = {
    "MISSED FG": "missed FG",
    "MISS FG": "missed FG",
    "INT": "INT",
    "INTERCEPTION": "INT",
    "FUMBLE": "fumble",
    "DOWNS": "turnover on downs",
    "TURNOVER ON DOWNS": "turnover on downs",
}


def _drive_period_clock(drive: dict) -> tuple:
    start = drive.get("start") if isinstance(drive.get("start"), dict) else {}
    period = (start.get("period") or {}).get("number")
    clock = (start.get("clock") or {}).get("displayValue") or ""
    return period, clock


def _play_clock_display(play: dict) -> str:
    clock = play.get("clock")
    if isinstance(clock, dict):
        return (clock.get("displayValue") or "").strip()
    return str(clock or "").strip()


def _play_period_number(play: dict):
    period = play.get("period")
    if isinstance(period, dict):
        return period.get("number")
    return period


def _drive_detail(drive: dict, result: str):
    """Last useful play text, yardage, and the play's own clock when present."""
    plays = drive.get("plays") or []
    needles = {
        "missed FG": ("no good", "missed", "wide"),
        "INT": ("intercept",),
        "fumble": ("fumble",),
        "turnover on downs": ("turnover on downs", "on downs"),
    }.get(result, ())
    chosen = ""
    chosen_play = None
    for play in reversed(plays):
        if not isinstance(play, dict):
            continue
        text = (play.get("text") or "").strip()
        if not text:
            continue
        low = text.lower()
        if "timeout" in low or "two-minute" in low:
            continue
        if needles and any(n in low for n in needles):
            chosen = text
            chosen_play = play
            break
        if not chosen:
            chosen = text
            chosen_play = play
    if not chosen:
        chosen = (drive.get("description") or "").strip()
    yards = None
    match = _YARDS_RE.search(chosen)
    if match:
        try:
            yards = int(match.group(1))
        except (TypeError, ValueError):
            yards = None
    # Keep the prompt line short: drop the long official-play recitation.
    detail = re.sub(r"\s+", " ", chosen)
    if len(detail) > 96:
        detail = detail[:93].rstrip() + "…"
    clock = _play_clock_display(chosen_play) if chosen_play else ""
    period = _play_period_number(chosen_play) if chosen_play else None
    return detail, yards, clock, period


_PLAY_FORCED = re.compile(r"FUMBLES\s*\(([^)]+)\)", re.IGNORECASE)
_PLAY_RECOVERED = re.compile(
    r"RECOVERED by\s+[A-Z]+-([A-Za-z.\-']+)", re.IGNORECASE
)
_PLAY_INT_BY = re.compile(r"INTERCEPTED by\s+([A-Za-z.\-']+)", re.IGNORECASE)
_PLAY_TARGET = re.compile(
    r"(?:pass\s+(?:incomplete\s+)?(?:deep|short)\s+\w+\s+)?"
    r"(?:to|intended for)\s+([A-Z]\.[A-Za-z\-']+)",
    re.IGNORECASE,
)
_PLAY_DIRECTION = re.compile(
    r"\b(deep middle|deep left|deep right|short middle|short left|short right)\b",
    re.IGNORECASE,
)


def _drive_team(drive: dict) -> str:
    return ((drive.get("team") or {}).get("abbreviation") or "").upper()


def _play_kind_from_text(text: str) -> str:
    low = (text or "").lower()
    if "intercept" in low:
        return "int"
    if "fumble" in low:
        return "fumble"
    if "incomplete" in low:
        return "incompletion"
    if "pass" in low and "complete" in low:
        return "completion"
    if "pass" in low and " to " in low and "incomplete" not in low:
        return "completion"
    if "pass" in low:
        return "pass"
    return "play"


def parse_plays(drives) -> list[dict]:
    """Notable ESPN play-by-play rows: turnovers and pass results.

    Forced/recovered/intercepted names come from the official play text so
    a writer cannot credit a tackler who was only in the original call.
    """
    if isinstance(drives, dict):
        rows = drives.get("previous") or []
    elif isinstance(drives, list):
        rows = drives
    else:
        rows = []
    out = []
    for drive in rows:
        if not isinstance(drive, dict):
            continue
        team = _drive_team(drive)
        for play in drive.get("plays") or []:
            if not isinstance(play, dict):
                continue
            text = (play.get("text") or "").strip()
            if not text:
                continue
            low = text.lower()
            if "timeout" in low or "two-minute" in low or "official timeout" in low:
                continue
            kind = _play_kind_from_text(text)
            interesting = kind in {
                "int",
                "fumble",
                "incompletion",
                "completion",
                "pass",
            }
            if not interesting and "deep" not in low:
                continue
            direction = ""
            hit = _PLAY_DIRECTION.search(text)
            if hit:
                direction = hit.group(1).lower()
            forced = _PLAY_FORCED.search(text)
            recovered = _PLAY_RECOVERED.search(text)
            intercepted = _PLAY_INT_BY.search(text)
            target = _PLAY_TARGET.search(text)
            row = {
                "quarter": _play_period_number(play),
                "clock": _play_clock_display(play),
                "team": team,
                "text": re.sub(r"\s+", " ", text),
                "kind": kind,
                "direction": direction,
                "yards": _play_yards(play),
                "forcedBy": (forced.group(1) if forced else "").strip(),
                "recoveredBy": (recovered.group(1) if recovered else "").strip(),
                "interceptedBy": (intercepted.group(1) if intercepted else "").strip(),
                "target": (target.group(1) if target else "").strip(),
            }
            out.append(row)
    return out


def prior_completed_game(schedule: list, last_game: dict | None) -> dict | None:
    """The completed slate row immediately before ``last_game``."""
    last = last_game or {}
    last_id = str(last.get("id") or "")
    last_date = last.get("date") or ""
    prior = None
    for game in schedule or []:
        if not isinstance(game, dict) or not game.get("completed"):
            continue
        if last_id and str(game.get("id") or "") == last_id:
            continue
        if last_date and (game.get("date") or "") > last_date:
            continue
        if prior is None or (game.get("date") or "") > (prior.get("date") or ""):
            prior = game
    return prior


def parse_drive_results(drives) -> list[dict]:
    """Missed FG / INT / fumble / turnover-on-downs with team, quarter, clock."""
    if isinstance(drives, dict):
        rows = drives.get("previous") or []
    elif isinstance(drives, list):
        rows = drives
    else:
        rows = []
    out = []
    for drive in rows:
        if not isinstance(drive, dict):
            continue
        raw = (drive.get("result") or drive.get("displayResult") or "").strip()
        label = _DRIVE_RESULTS.get(raw.upper())
        if not label:
            continue
        team = ((drive.get("team") or {}).get("abbreviation") or "").upper()
        period, clock = _drive_period_clock(drive)
        detail, yards, play_clock, play_period = _drive_detail(drive, label)
        if play_clock:
            clock = play_clock
        if play_period not in (None, ""):
            period = play_period
        out.append(
            {
                "quarter": period,
                "clock": clock,
                "team": team,
                "result": label,
                "yards": yards,
                "detail": detail,
            }
        )
    return out


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

    recap = {
        "eventId": str(event_id),
        "kc": {},
        "opp": {},
        "scoring": [],
        "scoringPlays": [],
        "driveResults": [],
        "plays": [],
        "leaders": [],
    }
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

    raw_plays = data.get("scoringPlays") or []
    recap["scoring"] = _scoring_lines(raw_plays)
    recap["scoringPlays"] = parse_scoring_plays(raw_plays, _kc_home_from_summary(data))
    recap["driveResults"] = parse_drive_results(data.get("drives"))
    recap["plays"] = parse_plays(data.get("drives"))

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

    if (
        not recap["kc"]
        and not recap["opp"]
        and not recap["scoring"]
        and not recap["scoringPlays"]
        and         not recap["driveResults"]
        and not recap["plays"]
        and not recap["leaders"]
    ):
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
