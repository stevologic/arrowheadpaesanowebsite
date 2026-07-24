"""Shared configuration, paths, and small helpers."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Repository paths
# ---------------------------------------------------------------------------
# tools/chiefs_narrative/config.py  ->  repo root is two parents up.
REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
PUBLIC_DIR = REPO_ROOT / "public"
DIAGRAM_DIR = PUBLIC_DIR / "images" / "narrative"

NARRATIVE_JSON = DATA_DIR / "narrative.json"
ARCHIVE_JSON = DATA_DIR / "narrative_archive.json"
SCHEDULE_JSON = DATA_DIR / "schedule_2026.json"

SCHEMA_VERSION = 1

# ---------------------------------------------------------------------------
# Team facts (stable across a season; safe defaults for the writer to lean on)
# ---------------------------------------------------------------------------
TEAM = {
    "name": "Kansas City Chiefs",
    "abbr": "KC",
    "espn_id": "12",
    "head_coach": "Andy Reid",
    "offensive_coordinator": "Eric Bieniemy",
    "defensive_coordinator": "Steve Spagnuolo",
    "quarterback": "Patrick Mahomes",
    "stadium": "GEHA Field at Arrowhead Stadium",
    "camp_site": "Missouri Western State University, St. Joseph, MO",
    "season": 2026,
    "last_season_record": "6-11",
    "last_season": 2025,
}

# ESPN public (unofficial) endpoints — no key required.
ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/kc"
ESPN_SCHEDULE = ESPN_BASE + "/schedule?season={season}"

# News wires we are allowed to read and cite. Every one is a real, public RSS
# feed. Keep the "publisher" label human-friendly; the writer cites it verbatim.
NEWS_FEEDS = [
    {"publisher": "Chiefs.com", "url": "https://www.chiefs.com/rss/news"},
    {"publisher": "Arrowhead Pride", "url": "https://www.arrowheadpride.com/rss/index.xml"},
    {"publisher": "Arrowhead Addict", "url": "https://arrowheadaddict.com/feed/"},
    {"publisher": "ESPN NFL", "url": "https://www.espn.com/espn/rss/nfl/news"},
]

USER_AGENT = "ArrowheadPaesanoNarrativeBot/1.0 (+https://arrowheadpaesano.com)"
HTTP_TIMEOUT = 25


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return now_utc().replace(microsecond=0).isoformat()


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DIAGRAM_DIR.mkdir(parents=True, exist_ok=True)
