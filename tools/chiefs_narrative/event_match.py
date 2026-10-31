"""Match an edition to week, opponent, venue, and home/away.

Used by the unofficial X-pin sidecar. This module does not generate,
select, or render images.
"""
from __future__ import annotations

import re

TEAM_NAMES = {
    "ARI": "Arizona Cardinals",
    "ATL": "Atlanta Falcons",
    "BUF": "Buffalo Bills",
    "CIN": "Cincinnati Bengals",
    "DEN": "Denver Broncos",
    "IND": "Indianapolis Colts",
    "LAC": "Los Angeles Chargers",
    "LAR": "Los Angeles Rams",
    "LV": "Las Vegas Raiders",
    "MIA": "Miami Dolphins",
    "NE": "New England Patriots",
    "NYJ": "New York Jets",
    "SEA": "Seattle Seahawks",
    "SF": "San Francisco 49ers",
    "TB": "Tampa Bay Buccaneers",
}
VENUE_KEYS = {
    "arrowhead stadium": "arrowhead",
    "geha field at arrowhead stadium": "arrowhead",
    "empower field at mile high": "empower-field",
    "empower field": "empower-field",
    "mile high": "empower-field",
    "lumen field": "lumen-field",
    "hard rock stadium": "hard-rock",
    "wembley stadium": "wembley",
    "wembley": "wembley",
    "tottenham hotspur stadium": "wembley",
    "london stadium": "wembley",
    "estadio azteca": "mexico-city",
    "azteca stadium": "mexico-city",
    "mexico city": "mexico-city",
    "azteca": "mexico-city",
    "levi's stadium": "super-bowl",
    "levis stadium": "super-bowl",
    "levi stadium": "super-bowl",
    "super bowl": "super-bowl",
    "allegiant stadium": "allegiant",
    "raymond james stadium": "raymond-james",
    "mercedes-benz stadium": "mercedes-benz",
    "mercedes benz stadium": "mercedes-benz",
    "highmark stadium": "highmark",
    "sofi stadium": "sofi",
    "paycor stadium": "paycor",
}
NEUTRAL_VENUES = frozenset({"wembley", "super-bowl", "mexico-city"})
PHASE_MODES = ("preview", "review", "camp", "offseason", "bye")
_WEEK_RE = re.compile(r"\bweek\s+(\d+)\b", re.I)
_AT_RE = re.compile(r"^@\s*")


def _norm(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def aliases() -> dict[str, str]:
    rows = {
        "kansas city chiefs": "KC",
        "kansas city": "KC",
        "chiefs": "KC",
        "kc": "KC",
    }
    for abbr, name in TEAM_NAMES.items():
        rows[_norm(name)] = abbr
        rows[_norm(name.split()[-1])] = abbr
        rows[_norm(abbr)] = abbr
        if name.endswith("s"):
            rows[_norm(name[:-1])] = abbr
    rows["49ers"] = "SF"
    rows["niners"] = "SF"
    rows["bucs"] = "TB"
    rows["buccaneer"] = "TB"
    return rows


def venue_key(text) -> str:
    blob = _norm(text)
    if not blob:
        return ""
    blob = _AT_RE.sub("", blob)
    for hint, key in sorted(VENUE_KEYS.items(), key=lambda item: -len(item[0])):
        if hint in blob:
            return key
    return ""


def season_code(phase_type: str) -> str:
    mapping = {
        "preseason": "pre",
        "pre": "pre",
        "regular": "reg",
        "reg": "reg",
        "postseason": "post",
        "post": "post",
    }
    return mapping.get(_norm(phase_type), "")


def mode_of(edition: dict) -> str:
    phase = edition.get("phase") or {}
    raw = _norm(phase.get("mode") or phase.get("type"))
    if raw in PHASE_MODES:
        return raw
    if raw in {"training-camp", "camp"}:
        return "camp"
    if raw in {"season-complete"}:
        return "offseason"
    return raw or "preview"


def event_of(edition: dict, mode: str) -> dict:
    if mode == "preview":
        return edition.get("nextGame") or {}
    if mode == "review":
        return edition.get("lastGameReview") or {}
    return {}


def opponent_abbr(text, alias_map=None) -> str:
    alias_map = alias_map or aliases()
    key = _norm(text)
    if key in alias_map:
        return alias_map[key]
    for chunk in (key, key.split("(")[0].strip()):
        if chunk in alias_map:
            return alias_map[chunk]
        for part in reversed(chunk.split()):
            if part in alias_map:
                return alias_map[part]
    return ""


def venue_blob(edition: dict, event: dict) -> str:
    parts = [
        event.get("at"),
        event.get("venue"),
        event.get("label"),
        event.get("lede"),
        event.get("note"),
        ((edition.get("markets") or {}).get("game") or {}).get("label"),
        edition.get("headline"),
        edition.get("dek"),
    ]
    return " ".join(str(part) for part in parts if part)


def home_away(edition: dict, event: dict, mode: str, venue: str) -> str:
    at = str(event.get("at") or event.get("venue") or "")
    at_venue = venue_key(at)
    event_ha = _norm(event.get("homeAway"))
    if event_ha in {"home", "away", "neutral"}:
        return event_ha
    if venue in NEUTRAL_VENUES or at_venue in NEUTRAL_VENUES:
        return "neutral"
    if at.lstrip().startswith("@"):
        return "away"
    if at_venue == "arrowhead":
        return "home"
    if mode == "preview":
        market = ((edition.get("markets") or {}).get("game") or {})
        ha = _norm(market.get("homeAway"))
        if ha in {"home", "away", "neutral"}:
            market_abbr = opponent_abbr(market.get("opponent"))
            event_abbr = opponent_abbr(event.get("opponent"))
            if not market_abbr or not event_abbr or market_abbr == event_abbr:
                return ha
    if venue == "arrowhead":
        return "home"
    if venue:
        return "away"
    return ""
