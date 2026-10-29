"""Topic-matched original hero illustrations, selected from edition fields.

Edition PRs stay data-only. Hugo picks a committed SVG at render time from
phase, opponent, venue, and home/away. This module is the catalog, the
selector, and the deterministic SVG generator.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import config

W, H = 660, 260
ASSET_DIR = "images/hero-scenes"
SCENE_DIR = config.PUBLIC_DIR / "images" / "hero-scenes"
CATALOG_PATH = config.DATA_DIR / "hero_scenes.json"

# Team colors only — no wordmarks, logos, or likenesses.
KC = {"primary": "#E31837", "secondary": "#FFB81C", "dark": "#6B1020", "shell": "#C8102E"}
TEAMS = {
    "ARI": {"name": "Arizona Cardinals", "primary": "#97233F", "secondary": "#FFFFFF", "dark": "#3D0C16", "shell": "#97233F", "colors": "red and white"},
    "ATL": {"name": "Atlanta Falcons", "primary": "#A71930", "secondary": "#000000", "dark": "#1A0508", "shell": "#A71930", "colors": "red and black"},
    "BUF": {"name": "Buffalo Bills", "primary": "#00338D", "secondary": "#C60C30", "dark": "#001433", "shell": "#00338D", "colors": "royal blue and red"},
    "CIN": {"name": "Cincinnati Bengals", "primary": "#FB4F14", "secondary": "#000000", "dark": "#1A0A00", "shell": "#FB4F14", "colors": "orange and black"},
    "DEN": {"name": "Denver Broncos", "primary": "#FB4F14", "secondary": "#002244", "dark": "#00101F", "shell": "#FB4F14", "colors": "orange and navy"},
    "IND": {"name": "Indianapolis Colts", "primary": "#002C5F", "secondary": "#FFFFFF", "dark": "#001226", "shell": "#002C5F", "colors": "blue and white"},
    "LAC": {"name": "Los Angeles Chargers", "primary": "#0080C6", "secondary": "#FFC20E", "dark": "#00314D", "shell": "#0080C6", "colors": "powder blue and gold"},
    "LAR": {"name": "Los Angeles Rams", "primary": "#003594", "secondary": "#FFA300", "dark": "#001433", "shell": "#003594", "colors": "blue and gold"},
    "LV": {"name": "Las Vegas Raiders", "primary": "#000000", "secondary": "#A5ACAF", "dark": "#111111", "shell": "#2A2A2A", "colors": "black and silver"},
    "MIA": {"name": "Miami Dolphins", "primary": "#008E97", "secondary": "#FC4C02", "dark": "#00383C", "shell": "#008E97", "colors": "aqua and orange"},
    "NE": {"name": "New England Patriots", "primary": "#002244", "secondary": "#C60C30", "dark": "#000C18", "shell": "#002244", "colors": "navy and red"},
    "NYJ": {"name": "New York Jets", "primary": "#125740", "secondary": "#FFFFFF", "dark": "#062419", "shell": "#125740", "colors": "green and white"},
    "SEA": {"name": "Seattle Seahawks", "primary": "#002244", "secondary": "#69BE28", "dark": "#000C18", "shell": "#002244", "colors": "navy and green"},
    "SF": {"name": "San Francisco 49ers", "primary": "#AA0000", "secondary": "#B3995D", "dark": "#3D0000", "shell": "#AA0000", "colors": "red and gold"},
    "TB": {"name": "Tampa Bay Buccaneers", "primary": "#D50A0A", "secondary": "#34302B", "dark": "#1A1714", "shell": "#D50A0A", "colors": "red and pewter"},
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

VENUE_LABELS = {
    "arrowhead": "an open home stadium on the Missouri river bluffs",
    "empower-field": "an open mile-high stadium with mountain ridges",
    "lumen-field": "a covered Pacific Northwest stadium",
    "hard-rock": "an open coastal stadium with palms",
    "wembley": "a neutral-site stadium under a steel arch",
    "mexico-city": "a neutral-site stadium in Mexico City",
    "super-bowl": "a neutral championship stadium under night lights",
    "allegiant": "a black dome in the desert",
    "raymond-james": "an open Gulf-coast stadium",
    "mercedes-benz": "a modern retractable-roof stadium",
    "highmark": "an open cold-weather stadium",
    "sofi": "a swooping modern stadium at dusk",
    "paycor": "an open riverfront stadium",
}

PHASE_MODES = ("preview", "review", "camp", "offseason", "bye")
FALLBACK_IDS = {mode: f"{mode}-fallback" for mode in PHASE_MODES}
NEUTRAL_VENUES = frozenset({"wembley", "super-bowl", "mexico-city"})
# Not on the 2026 slate: London vs MIA, Super Bowl vs SF, postseason home vs BUF.
EXTRA_MATCHUPS = (
    {
        "id": "neutral-mia-london",
        "week": None,
        "seasonType": "reg",
        "opponent": "Miami Dolphins",
        "opponentAbbr": "MIA",
        "homeAway": "neutral",
        "venue": "Wembley Stadium",
    },
    {
        "id": "sb-sf",
        "week": None,
        "seasonType": "post",
        "opponent": "San Francisco 49ers",
        "opponentAbbr": "SF",
        "homeAway": "neutral",
        "venue": "Super Bowl",
    },
    {
        "id": "post-home-buf",
        "week": None,
        "seasonType": "post",
        "opponent": "Buffalo Bills",
        "opponentAbbr": "BUF",
        "homeAway": "home",
        "venue": "Arrowhead Stadium",
    },
)

# Words that would make an asset look like a logo, wordmark, or photo claim.
FORBIDDEN_ASSET_TEXT = (
    "nfl", "afc", "nfc", "cbs", "nbc", "espn", "fox", "prime",
    "chiefs", "broncos", "seahawks", "raiders", "chargers", "rams",
    "colts", "dolphins", "buccaneers", "jets", "falcons", "cardinals",
    "bills", "bengals", "patriots", "49ers", "kansas city", "temporary",
    "mahomes", "reid", "arrowhead", "lumen", "empower", "allegiant",
    "sofi", "highmark", "paycor", "mercedes",
)

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
    for abbr, team in TEAMS.items():
        name = team["name"]
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
    mapping = {"preseason": "pre", "pre": "pre", "regular": "reg", "reg": "reg",
               "postseason": "post", "post": "post"}
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


def _week(edition: dict, event: dict) -> int | None:
    phase = edition.get("phase") or {}
    for candidate in (phase.get("week"),):
        if isinstance(candidate, int) and candidate > 0:
            return candidate
        if isinstance(candidate, str) and candidate.isdigit():
            return int(candidate)
    for blob in (event.get("label"), (edition.get("edition") or "")):
        match = _WEEK_RE.search(str(blob or ""))
        if match:
            return int(match.group(1))
    return None


def _home_away(edition: dict, event: dict, mode: str, venue: str) -> str:
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


def _search_blob(edition: dict, event: dict) -> str:
    parts = [
        event.get("at"), event.get("venue"), event.get("label"), event.get("lede"),
        event.get("note"), event.get("opponent"),
        ((edition.get("markets") or {}).get("game") or {}).get("label"),
        edition.get("headline"), edition.get("dek"),
    ]
    return " ".join(str(part) for part in parts if part)


def _venue_blob(edition: dict, event: dict) -> str:
    """Venue text only — opponent names must not become venue hints."""
    parts = [
        event.get("at"), event.get("venue"), event.get("label"), event.get("lede"),
        event.get("note"),
        ((edition.get("markets") or {}).get("game") or {}).get("label"),
        edition.get("headline"), edition.get("dek"),
    ]
    return " ".join(str(part) for part in parts if part)


def _match_schedule(edition: dict, event: dict, schedule: list[dict], mode: str) -> dict | None:
    abbr = opponent_abbr(event.get("opponent"))
    if not abbr:
        return None
    week = _week(edition, event)
    phase_season = season_code((edition.get("phase") or {}).get("type") or "")
    venue = venue_key(_search_blob(edition, event))
    home_away = _home_away(edition, event, mode, venue)
    ranked: list[tuple[int, dict]] = []
    for game in schedule:
        if opponent_abbr(game.get("opponent") or game.get("opponentAbbr")) != abbr:
            continue
        if phase_season and game.get("seasonType") != phase_season:
            continue
        if home_away and game.get("homeAway") and game.get("homeAway") != home_away:
            continue
        score = 1
        if week and game.get("week") == week:
            score += 8
        if phase_season and game.get("seasonType") == phase_season:
            score += 3
        if home_away and game.get("homeAway") == home_away:
            score += 4
        if venue and venue_key(game.get("venue")) == venue:
            score += 4
        if mode == "review" and game.get("completed"):
            score += 1
        if mode == "preview" and not game.get("completed"):
            score += 1
        ranked.append((score, game))
    if not ranked:
        return None
    ranked.sort(key=lambda item: (-item[0], item[1].get("week") or 0))
    return ranked[0][1]


def scene_id(mode: str, home_away: str, abbr: str) -> str:
    return f"{mode}-{home_away}-{abbr.lower()}"


def _scene_alt(mode: str, venue: str, home_away: str, team: dict | None) -> str:
    place = VENUE_LABELS.get(venue, "a football stadium")
    lighting = "night recap light" if mode == "review" else "game-day light"
    weather = {
        "lumen-field": "driving rain and evergreens" if mode == "review" else "overcast northwest light",
        "empower-field": "mountain ridges and thin high-altitude air",
        "highmark": "snow flurries and a steel sky",
        "hard-rock": "palm silhouettes and coastal water",
        "allegiant": "desert mesas around a black dome",
        "sofi": "a swooping roof against a dusk sky",
    }.get(venue, "clear stadium geometry")
    colors = "red and gold versus a visiting color set"
    if team:
        colors = f"red and gold versus {team['colors']}"
    if home_away == "home":
        side = "a home bowl"
    elif home_away == "neutral":
        side = "a neutral site"
    else:
        side = "a road venue"
    return (
        f"Original illustration of a {lighting} scene at {place} ({side}), "
        f"with {weather}, a stadium silhouette, field shapes, and two helmet silhouettes in {colors}. "
        "Not a photograph."
    )


def _scene_caption(mode: str, venue: str) -> str:
    place = VENUE_LABELS.get(venue, "a football stadium")
    if mode == "review":
        return f"Night review illustration · {place}"
    if mode == "preview":
        return f"Game-day preview illustration · {place}"
    if mode == "camp":
        return "Training-camp illustration · practice field, not a photograph"
    if mode == "bye":
        return "Bye-week illustration · empty practice field"
    if mode == "offseason":
        return "Offseason illustration · quiet stadium dawn"
    return "Article illustration · not a photograph"


def _fallback_scene(mode: str) -> dict:
    mode = mode if mode in FALLBACK_IDS else "preview"
    return {
        "id": FALLBACK_IDS[mode],
        "phase": mode,
        "homeAway": "",
        "opponentAbbr": "",
        "opponent": "",
        "venue": "",
        "venueKey": "",
        "asset": f"{ASSET_DIR}/{FALLBACK_IDS[mode]}.svg",
        "alt": {
            "preview": "Original illustration of a sunlit practice-and-preview field with a red-and-gold helmet silhouette and a generic stadium bowl. Not a photograph.",
            "review": "Original illustration of a night stadium bowl with lights, a red-and-gold helmet silhouette, and a darkened field. Not a photograph.",
            "camp": "Original illustration of a training-camp practice field under a hard sun, with sled shapes and a red-and-gold helmet silhouette. Not a photograph.",
            "bye": "Original illustration of an empty practice field and a single red-and-gold helmet silhouette during a quiet week. Not a photograph.",
            "offseason": "Original illustration of a quiet dawn stadium bowl in red and gold, with no crowd and no game action. Not a photograph.",
        }[mode],
        "caption": _scene_caption(mode, ""),
        "mapped": False,
    }


def matchup_scenes(game: dict) -> list[dict]:
    abbr = game["opponentAbbr"]
    team = TEAMS[abbr]
    home_away = game["homeAway"]
    venue = venue_key(game.get("venue")) or ("arrowhead" if home_away == "home" else "")
    rows = []
    for mode in ("preview", "review"):
        sid = scene_id(mode, home_away, abbr)
        rows.append({
            "id": sid,
            "phase": mode,
            "homeAway": home_away,
            "opponentAbbr": abbr,
            "opponent": team["name"],
            "venue": game.get("venue") or "",
            "venueKey": venue,
            "asset": f"{ASSET_DIR}/{sid}.svg",
            "alt": _scene_alt(mode, venue, home_away, team),
            "caption": _scene_caption(mode, venue),
            "mapped": True,
            "week": game.get("week"),
            "seasonType": game.get("seasonType"),
            "gameId": game.get("id"),
        })
    return rows


def load_schedule(root=None) -> list[dict]:
    path = Path(root or config.REPO_ROOT) / "data" / "schedule_2026.json"
    return json.loads(path.read_text(encoding="utf-8"))


def build_catalog(schedule: list[dict] | None = None) -> dict:
    schedule = schedule if schedule is not None else load_schedule()
    scenes: dict[str, dict] = {}
    by_week: dict[str, str] = {}
    by_ha: dict[str, str] = {}
    by_venue: dict[str, str] = {}
    matchups = []
    for game in schedule:
        abbr = game.get("opponentAbbr")
        if abbr not in TEAMS:
            continue
        pair = matchup_scenes(game)
        for scene in pair:
            scenes[scene["id"]] = scene
            season = game.get("seasonType") or "reg"
            week = game.get("week")
            if week:
                by_week[f"{season}-{week}-{scene['phase']}"] = scene["id"]
            by_ha[f"{abbr.lower()}-{scene['homeAway']}-{scene['phase']}"] = scene["id"]
            if scene["venueKey"]:
                by_venue[f"{abbr.lower()}-{scene['venueKey']}-{scene['phase']}"] = scene["id"]
        preview, review = pair
        matchups.append({
            "id": game.get("id"),
            "week": game.get("week"),
            "seasonType": game.get("seasonType"),
            "opponent": game.get("opponent"),
            "opponentAbbr": abbr,
            "homeAway": game.get("homeAway"),
            "venue": game.get("venue"),
            "venueKey": preview["venueKey"],
            "previewScene": preview["id"],
            "reviewScene": review["id"],
        })
    for extra in EXTRA_MATCHUPS:
        abbr = extra.get("opponentAbbr")
        if abbr not in TEAMS:
            continue
        pair = matchup_scenes(extra)
        for scene in pair:
            scenes[scene["id"]] = scene
            by_ha[f"{abbr.lower()}-{scene['homeAway']}-{scene['phase']}"] = scene["id"]
            if scene["venueKey"]:
                by_venue[f"{abbr.lower()}-{scene['venueKey']}-{scene['phase']}"] = scene["id"]
        preview, review = pair
        matchups.append({
            "id": extra.get("id"),
            "week": extra.get("week"),
            "seasonType": extra.get("seasonType"),
            "opponent": extra.get("opponent"),
            "opponentAbbr": abbr,
            "homeAway": extra.get("homeAway"),
            "venue": extra.get("venue"),
            "venueKey": preview["venueKey"],
            "previewScene": preview["id"],
            "reviewScene": review["id"],
        })
    for mode in PHASE_MODES:
        scenes[FALLBACK_IDS[mode]] = _fallback_scene(mode)
    return {
        "schemaVersion": 1,
        "byWeek": by_week,
        "byOpponentHomeAway": by_ha,
        "byOpponentVenue": by_venue,
        "aliases": aliases(),
        "venueHints": dict(VENUE_KEYS),
        "neutralVenues": sorted(NEUTRAL_VENUES),
        "fallbacks": dict(FALLBACK_IDS),
        "scenes": scenes,
        "matchups": matchups,
    }


def resolve(edition: dict, *, schedule=None, catalog=None) -> dict:
    """Pick a scene from edition fields already present on every narrative.

    A week key is used only when it agrees with the stated opponent (and
    home/away when that is known). A week/opponent mismatch falls through
    to opponent + venue, then opponent + home/away.
    """
    if schedule is None:
        schedule = load_schedule()
    catalog = catalog or build_catalog(schedule)
    mode = mode_of(edition)
    event = event_of(edition, mode)
    scenes = catalog["scenes"]
    fallback = scenes.get(catalog["fallbacks"].get(mode) or "", scenes[FALLBACK_IDS["preview"]])
    if mode not in {"preview", "review"}:
        return dict(fallback)

    abbr = opponent_abbr(event.get("opponent"), catalog.get("aliases"))
    venue = venue_key(_venue_blob(edition, event))
    home_away = _home_away(edition, event, mode, venue)
    week = _week(edition, event)
    season = season_code((edition.get("phase") or {}).get("type") or "")
    if week and season:
        sid = catalog["byWeek"].get(f"{season}-{week}-{mode}")
        if sid in scenes:
            scene = scenes[sid]
            opp_ok = not abbr or scene.get("opponentAbbr") == abbr
            ha_ok = not home_away or scene.get("homeAway") == home_away
            if opp_ok and ha_ok:
                return dict(scene)

    if abbr and home_away:
        sid = catalog["byOpponentHomeAway"].get(f"{abbr.lower()}-{home_away}-{mode}")
        if sid in scenes:
            return dict(scenes[sid])
    if abbr and venue:
        sid = catalog["byOpponentVenue"].get(f"{abbr.lower()}-{venue}-{mode}")
        if sid in scenes:
            return dict(scenes[sid])
    game = _match_schedule(edition, event, schedule, mode)
    if game:
        sid = scene_id(mode, game.get("homeAway") or home_away, game.get("opponentAbbr") or abbr)
        if sid in scenes:
            return dict(scenes[sid])
    return dict(fallback)


def edition_alt(edition: dict, scene: dict) -> str:
    topic = (edition.get("headline") or "").strip()
    label = (event_of(edition, scene.get("phase") or mode_of(edition)).get("label")
             or (edition.get("phase") or {}).get("label") or "")
    extra = " ".join(part for part in (topic, label) if part)
    if extra:
        return f"{scene['alt']} Edition topic: {extra}."
    return scene["alt"]


# --- SVG generator ----------------------------------------------------------

def _grad(sid: str, stops, x1="0", y1="0", x2="0", y2="1") -> str:
    bits = [f'<linearGradient id="{sid}" x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}">']
    bits.extend(f'<stop offset="{off}" stop-color="{color}"/>' for off, color in stops)
    bits.append("</linearGradient>")
    return "".join(bits)


_HELMET_X_RE = re.compile(r'<g transform="translate\(([-\d.]+),')


def helmet_xs(svg: str) -> list[float]:
    return [float(match) for match in _HELMET_X_RE.findall(svg)]


def helmets_in_viewbox(svg: str, margin: float = 28) -> bool:
    xs = helmet_xs(svg)
    return bool(xs) and all(margin <= x <= W - margin for x in xs)


def _helmet(x: float, y: float, colors: dict, facing: str) -> str:
    """Generic helmet silhouette — oval shell and facemask bars, no decal.

    Translate to the on-field point first, then flip about that point.
    Negating x before translate puts a left-facing helmet off-canvas.
    """
    flip = "scale(-1,1)" if facing == "left" else "scale(1,1)"
    mask = colors.get("secondary", "#C8C8C8")
    return (
        f'<g transform="translate({x},{y}) {flip}">'
        f'<ellipse cx="0" cy="0" rx="28" ry="20" fill="{colors["shell"]}"/>'
        f'<path d="M-6-16c12-2 24 6 26 16c-8 4-18 6-28 4c-2-8 0-16 2-20z" fill="{colors["primary"]}"/>'
        f'<path d="M-4-8h22" stroke="{colors["dark"]}" stroke-width="3" fill="none"/>'
        f'<path d="M18-2c8 2 10 8 6 14c-6 2-14 2-22 0" fill="none" stroke="{mask}" stroke-width="3"/>'
        f'<path d="M16 2h12M16 7h11M16 12h9" stroke="{mask}" stroke-width="2"/>'
        f'<circle cx="-10" cy="-4" r="3" fill="{colors["dark"]}"/>'
        f"</g>"
    )


def _field(uid: str, grass_a: str, grass_b: str, hash_color: str) -> str:
    lines = [
        f'<rect x="0" y="188" width="{W}" height="72" fill="url(#{uid}-field)"/>',
        f'<path d="M0 188h{W}" stroke="{hash_color}" stroke-width="2"/>',
    ]
    for x in range(40, 640, 28):
        lines.append(f'<path d="M{x} 198v10" stroke="{hash_color}" stroke-width="2" opacity=".55"/>')
    lines.append(f'<ellipse cx="330" cy="228" rx="18" ry="10" fill="#6B4A22" stroke="#C4A36A" stroke-width="2"/>')
    return "".join(lines)


def _stadium_bowl(x: float, y: float, w: float, h: float, fill: str, lights=False, roof=False) -> str:
    left, right = int(x), int(x + w)
    mid = int(x + w / 2)
    parts = [
        f'<path d="M{left},{y+h} L{left+18},{y} H{right-18} L{right},{y+h} Z" fill="{fill}"/>',
        f'<path d="M{left+28},{y+14} H{right-28}" stroke="#000" opacity=".18" stroke-width="6"/>',
    ]
    if roof:
        parts.append(
            f'<path d="M{left+10},{y+8} Q{mid},{y-28} {right-10},{y+8}" fill="none" stroke="#1B1E24" stroke-width="10"/>'
        )
    if lights:
        for lx in (left + 24, mid, right - 24):
            parts.append(f'<path d="M{lx},{y} v-16" stroke="#D6C27A" stroke-width="3"/>')
            parts.append(f'<circle cx="{lx}" cy="{y-20}" r="7" fill="#F8E7A0" opacity=".85"/>')
    return "".join(parts)


def _mountains(fill_back: str, fill_front: str, snow="#F4F0E6") -> str:
    return (
        f'<path d="M0 150 L70 78 L120 128 L190 48 L260 120 L330 62 L410 132 L480 40 L560 118 L660 70 V160 H0Z" fill="{fill_back}"/>'
        f'<path d="M0 168 L90 102 L150 148 L230 88 L310 150 L400 96 L490 156 L580 90 L660 140 V190 H0Z" fill="{fill_front}"/>'
        f'<path d="M176 62 L190 48 L204 64 L190 58Z" fill="{snow}" opacity=".8"/>'
        f'<path d="M468 54 L480 40 L494 58 L480 50Z" fill="{snow}" opacity=".8"/>'
    )


def _palms(color: str) -> str:
    fronds = []
    for x in (36, 92, 568, 624):
        fronds.append(f'<path d="M{x} 188v-70" stroke="{color}" stroke-width="5"/>')
        fronds.append(
            f'<path d="M{x} 118c-22-8-28-22-18-28c12 8 18 8 18 8c-4-18 6-30 16-22c-2 14 0 20 0 20c16-10 30 2 22 14c-10 2-16 6-20 8c18 4 22 16 12 22c-10-6-18-8-30-8z" fill="{color}"/>'
        )
    return "".join(fronds)


def _pines(color: str) -> str:
    trees = []
    for x, s in ((48, 1.0), (86, 0.8), (574, 0.9), (618, 1.1)):
        half = round(18 * s, 1)
        height = round(52 * s, 1)
        trees.append(
            f'<path d="M{x} 190 l{-half} 0 l{half} {-height} l{half} {height}z" fill="{color}"/>'
        )
    return "".join(trees)


def _rain(uid: str) -> str:
    drops = [f'<g id="{uid}-rain" stroke="#D5E6F2" stroke-width="2" opacity=".75">']
    for i, x in enumerate(range(12, 650, 18)):
        y = 8 + (i * 13) % 40
        drops.append(f'<path d="M{x} {y} l8 22"/>')
        drops.append(f'<path d="M{x+9} {y+70} l8 22"/>')
        drops.append(f'<path d="M{x+4} {y+130} l8 22"/>')
    drops.append("</g>")
    return "".join(drops)


def _snow() -> str:
    flakes = []
    for i, x in enumerate(range(16, 650, 22)):
        y = 12 + (i * 17) % 90
        flakes.append(f'<circle cx="{x}" cy="{y}" r="2.2" fill="#F2F6FA" opacity=".8"/>')
        flakes.append(f'<circle cx="{x+8}" cy="{y+70}" r="1.6" fill="#F2F6FA" opacity=".65"/>')
    return "".join(flakes)


def _sun(cx: float, cy: float, color: str) -> str:
    return f'<circle cx="{cx}" cy="{cy}" r="22" fill="{color}"/>'


def _skyline(color: str) -> str:
    return (
        f'<path d="M40 160h28v-46h18v46h22v-70h26v70h16v-36h20v36h30v-54h24v54h18v-28h22v28h40v-60h28v60'
        f'h26v-40h20v40h36v-76h30v76h24v-32h18v32h40v-50h26v50h34v-22h16v22H40z" fill="{color}"/>'
    )


def _desert(color: str) -> str:
    return (
        f'<path d="M0 176 C80 150 140 166 220 158 C300 150 360 170 460 154 C540 142 600 160 660 148 V200 H0Z" fill="{color}"/>'
        f'<path d="M86 168c0-22 10-40 10-40s10 18 10 40h-20z" fill="#7A5A38"/>'
        f'<path d="M118 160c0-16 6-26 6-26s6 10 6 26h-12z" fill="#7A5A38"/>'
    )


def _river(color: str) -> str:
    return f'<path d="M0 176 C120 166 220 190 330 174 C440 158 540 186 660 168 V210 H0Z" fill="{color}" opacity=".85"/>'


def _lights_wash() -> str:
    return (
        '<ellipse cx="180" cy="52" rx="80" ry="28" fill="#F6E7A2" opacity=".35"/>'
        '<ellipse cx="330" cy="40" rx="110" ry="34" fill="#F8E7A0" opacity=".42"/>'
        '<ellipse cx="490" cy="52" rx="80" ry="28" fill="#F6E7A2" opacity=".35"/>'
    )


def _palette(mode: str, venue: str, _away: dict | None) -> dict:
    night = mode == "review"
    if venue == "empower-field":
        sky = [("#14325C", "#3E6FA3"), ("#F3B267", "#E06A2C")][0 if night else 1]
        return {"sky": [("0%", "#1B3358" if night else "#7EB6E8"), ("100%", "#3A1D48" if night else "#F4C27A")],
                "grass": ("#1A4A2C" if night else "#2F7A3E", "#123420" if night else "#246433"),
                "bowl": "#1B2433" if night else "#2A3344", "hash": "#D9C27A"}
    if venue == "lumen-field":
        return {"sky": [("0%", "#1A2A40"), ("100%", "#2A3A50")] if night else [("0%", "#4A6578"), ("100%", "#8FA8B8")],
                "grass": ("#1A4A32", "#143828"), "bowl": "#243044", "hash": "#C6D6B0"}
    if venue == "hard-rock":
        return {"sky": [("0%", "#2A1540"), ("100%", "#C45A3A")] if night else [("0%", "#49B7D6"), ("100%", "#F7C56A")],
                "grass": ("#1C5A38", "#14482C"), "bowl": "#2A2F38", "hash": "#E6D39A"}
    if venue == "wembley":
        return {"sky": [("0%", "#1A2030"), ("100%", "#4A5A6A")] if night else [("0%", "#6A8AAA"), ("100%", "#D4DEE8")],
                "grass": ("#1A4A32", "#123826"), "bowl": "#2A3038", "hash": "#D0D6DC"}
    if venue == "mexico-city":
        return {"sky": [("0%", "#1A2038"), ("100%", "#C45A28")] if night else [("0%", "#4A7CB0"), ("100%", "#E8B36A")],
                "grass": ("#1A4A32", "#123826"), "bowl": "#2A3038", "hash": "#E6D2A0"}
    if venue == "super-bowl":
        return {"sky": [("0%", "#140828"), ("100%", "#6A2048")] if night else [("0%", "#2A1848"), ("100%", "#C45A88")],
                "grass": ("#1A4A32", "#123826"), "bowl": "#2A1838", "hash": "#E6C36A"}
    if venue == "allegiant":
        return {"sky": [("0%", "#0A0A10"), ("100%", "#2A1840")] if night else [("0%", "#3A2A48"), ("100%", "#E0A36A")],
                "grass": ("#1A1A1A", "#111111"), "bowl": "#0A0A0A", "hash": "#A5ACAF"}
    if venue == "highmark":
        return {"sky": [("0%", "#1A2430"), ("100%", "#4A5A68")] if night else [("0%", "#8AA4B8"), ("100%", "#D7E4EE")],
                "grass": ("#1A3A28", "#122818"), "bowl": "#2A3038", "hash": "#E8EEF4"}
    if venue == "sofi":
        return {"sky": [("0%", "#1B1038"), ("100%", "#C45A88")] if night else [("0%", "#5B7FCF"), ("100%", "#F0B27A")],
                "grass": ("#1A4A32", "#123826"), "bowl": "#2A2438", "hash": "#E6C36A"}
    if venue == "mercedes-benz":
        return {"sky": [("0%", "#1A2030"), ("100%", "#C46A4A")] if night else [("0%", "#6AA7D8"), ("100%", "#F3C8A0")],
                "grass": ("#1A4630", "#123424"), "bowl": "#2A2A30", "hash": "#E8D2A0"}
    if venue == "paycor":
        return {"sky": [("0%", "#1A2230"), ("100%", "#C45A28")] if night else [("0%", "#5B8FBE"), ("100%", "#F0C48A")],
                "grass": ("#1A4A2C", "#123820"), "bowl": "#2A241C", "hash": "#E6C27A"}
    if venue == "raymond-james":
        return {"sky": [("0%", "#1A2038"), ("100%", "#C45A40")] if night else [("0%", "#4EB0D8"), ("100%", "#F6D08A")],
                "grass": ("#1C5A34", "#144628"), "bowl": "#2A2824", "hash": "#E6D2A0"}
    # Arrowhead / default
    return {"sky": [("0%", "#2A1020"), ("100%", "#6B1020")] if night else [("0%", "#E07A3A"), ("100%", "#F3C46A")],
            "grass": ("#1A3A24" if night else "#2A6A38", "#102818" if night else "#1E522C"),
            "bowl": "#4A1420" if night else "#6B1A28", "hash": "#FFB81C"}


def _landmarks(venue: str, mode: str, pal: dict, uid: str) -> str:
    night = mode == "review"
    if venue == "empower-field":
        return _mountains("#2A4060" if night else "#5A6E88", "#1B2A40" if night else "#3A4A5C") + (
            "" if night else _sun(560, 48, "#F6E27A")
        )
    if venue == "lumen-field":
        return _skyline("#1A2430") + _pines("#1A4A30") + _rain(uid) + (_lights_wash() if night else "")
    if venue == "hard-rock":
        water = "#0E4A58" if night else "#2A8AA0"
        return (
            f'<rect x="0" y="150" width="{W}" height="40" fill="{water}" opacity=".55"/>'
            + _palms("#0E3A28" if night else "#1A5A38")
            + ("" if night else _sun(88, 44, "#F7E08A"))
        )
    if venue == "wembley":
        return (
            f'<path d="M70 180 Q330 8 590 180" fill="none" stroke="#8A96A4" stroke-width="12"/>'
            f'<path d="M70 180 Q330 28 590 180" fill="none" stroke="#C8D0D8" stroke-width="4"/>'
            + ("" if night else _sun(96, 46, "#F4E2A0"))
        )
    if venue == "mexico-city":
        return (
            f'<path d="M40 168 L90 110 L140 168 L200 96 L270 168 L340 88 L410 168 L490 104 L560 168 L620 120 V176 H0Z" fill="#6A7A88"/>'
            + ("" if night else _sun(88, 44, "#F6D48A"))
        )
    if venue == "super-bowl":
        return _lights_wash() + (
            '<ellipse cx="330" cy="36" rx="140" ry="22" fill="#F8E7A0" opacity=".28"/>'
        )
    if venue == "allegiant":
        return _desert("#3A2A22" if night else "#C4A078") + (
            f'<ellipse cx="330" cy="150" rx="210" ry="36" fill="#111"/>'
            f'<path d="M140 150 Q330 88 520 150" fill="#1A1A1A"/>'
        )
    if venue == "highmark":
        return _snow() + (
            f'<path d="M0 150 L80 110 L160 150 L240 96 L340 150 L440 104 L540 150 L660 112 V176 H0Z" fill="#6A7A88"/>'
        )
    if venue == "sofi":
        return (
            _palms("#1A3A28")
            + f'<path d="M80 168 Q330 40 580 168" fill="none" stroke="#2A2438" stroke-width="16"/>'
            + f'<path d="M110 168 Q330 70 550 168" fill="#201A2C" opacity=".9"/>'
        )
    if venue == "mercedes-benz":
        return _skyline("#2A3038") + (
            f'<ellipse cx="330" cy="156" rx="200" ry="40" fill="#2A2A30"/>'
            f'<path d="M140 156 Q330 96 520 156" fill="#3A3A42"/>'
        )
    if venue == "paycor":
        return _river("#2A5A78" if night else "#4A8AAA") + (
            f'<path d="M0 150 L120 108 L220 150 L360 96 L500 150 L660 118 V176 H0Z" fill="#3A4A38"/>'
        )
    if venue == "raymond-james":
        return _palms("#1A4A30") + ("" if night else _sun(560, 42, "#F6E08A"))
    # Arrowhead bowl on the bluffs
    return (
        f'<path d="M0 158 C80 140 160 150 260 142 C360 134 460 152 660 138 V190 H0Z" fill="#3A2A20"/>'
        + ("" if night else _sun(86, 46, "#F6D56A"))
        + (_lights_wash() if night else "")
    )


def render_svg(scene: dict) -> str:
    sid = scene["id"]
    mode = scene.get("phase") or "preview"
    venue = scene.get("venueKey") or "arrowhead"
    away = TEAMS.get(scene.get("opponentAbbr") or "")
    pal = _palette(mode, venue, away)
    uid = sid.replace("-", "")
    night = mode == "review"
    defs = [
        _grad(f"{uid}-sky", pal["sky"]),
        _grad(f"{uid}-field", [("0%", pal["grass"][0]), ("100%", pal["grass"][1])]),
    ]
    body = [
        f'<rect width="{W}" height="{H}" fill="url(#{uid}-sky)"/>',
        _landmarks(venue, mode, pal, uid),
        _stadium_bowl(150, 118, 360, 78, pal["bowl"], lights=night or venue == "arrowhead",
                      roof=venue in {"lumen-field", "sofi", "mercedes-benz", "allegiant"}),
        _field(uid, pal["grass"][0], pal["grass"][1], pal["hash"]),
        _helmet(168, 214, KC, "right"),
    ]
    if away:
        body.append(_helmet(492, 214, away, "left"))
    elif mode == "camp":
        body.append(f'<rect x="300" y="200" width="70" height="22" rx="4" fill="#5A4630"/>')
        body.append(f'<rect x="308" y="188" width="16" height="18" fill="#8A6A40"/>')
        body.append(f'<rect x="346" y="188" width="16" height="18" fill="#8A6A40"/>')
    # Split color stripe in the turf so the matchup reads without wordmarks.
    if away:
        body.append(f'<path d="M0 250h330" stroke="{KC["primary"]}" stroke-width="8" opacity=".7"/>')
        body.append(f'<path d="M330 250h330" stroke="{away["primary"]}" stroke-width="8" opacity=".7"/>')
    else:
        body.append(f'<path d="M0 250h{W}" stroke="{KC["secondary"]}" stroke-width="8" opacity=".7"/>')
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
        f'width="{W}" height="{H}" class="nrt-event-illustration" role="img">'
        f"<defs>{''.join(defs)}</defs>"
        f"{''.join(body)}"
        f"</svg>\n"
    )


def catalog_for_hugo(catalog: dict) -> dict:
    """Drop generator-only fields so the Hugo file stays selection data."""
    scenes = {}
    for key, scene in catalog["scenes"].items():
        scenes[key] = {k: scene[k] for k in (
            "id", "phase", "homeAway", "opponentAbbr", "opponent", "venue",
            "venueKey", "asset", "alt", "caption", "mapped",
        ) if k in scene}
    return {
        "schemaVersion": 1,
        "byWeek": catalog["byWeek"],
        "byOpponentHomeAway": catalog["byOpponentHomeAway"],
        "byOpponentVenue": catalog["byOpponentVenue"],
        "aliases": catalog["aliases"],
        "venueHints": catalog["venueHints"],
        "neutralVenues": sorted(NEUTRAL_VENUES),
        "fallbacks": catalog["fallbacks"],
        "scenes": scenes,
        "matchups": catalog["matchups"],
    }


def write_assets(root=None) -> dict:
    root = Path(root or config.REPO_ROOT)
    catalog = build_catalog(load_schedule(root))
    scene_dir = root / "public" / "images" / "hero-scenes"
    scene_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for sid, scene in catalog["scenes"].items():
        svg = render_svg(scene)
        path = scene_dir / f"{sid}.svg"
        path.write_text(svg, encoding="utf-8")
        written.append(sid)
    payload = catalog_for_hugo(catalog)
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    (root / "data" / "hero_scenes.json").write_text(text, encoding="utf-8")
    return {"scenes": sorted(written), "count": len(written)}


def asset_text_violations(svg: str) -> list[str]:
    blob = svg.lower()
    hits = []
    if re.search(r"<text\b", blob):
        hits.append("text-element")
    for word in FORBIDDEN_ASSET_TEXT:
        if word in blob:
            hits.append(word)
    return hits


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Write topic-matched hero illustration assets")
    parser.add_argument("--write", action="store_true", help="Write SVG files and data/hero_scenes.json")
    args = parser.parse_args(argv)
    if args.write:
        info = write_assets()
        print(f"wrote {info['count']} hero scenes")
        return 0
    catalog = build_catalog()
    print(json.dumps({"count": len(catalog["scenes"]), "ids": sorted(catalog["scenes"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
