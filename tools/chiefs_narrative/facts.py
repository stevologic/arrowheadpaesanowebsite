"""Deterministic ESPN fact-check for generated edition copy.

The writer is allowed to interpret the tape. It is not allowed to invent a
final score, a player stat line, a TD/FG yardage, or a team FG/TD count
that disagrees with the ESPN box / scoring plays we already handed it.
Checks are regex-vs-box so the same wrong sentence fails every time.
Every generated section is scanned — not just lastGameReview.
"""
from __future__ import annotations

import copy
import re

from . import collect, phase as phase_mod

# Completions and similar "X-of-Y" / "X/Y" lines must not be read as scores.
_OF_LINE = re.compile(r"\d+\s*[-–]?\s*of\s*[-–]?\s*\d+", re.IGNORECASE)
_SLASH_LINE = re.compile(r"\d+\s*/\s*\d+")

# Game-score contexts only. Bare "3-0" / "6-11" (records) stay out.
# Bare "lost 18-19" is first-down volume, not a final — require game/final/it/KC.
_SCORE_PATTERNS = (
    re.compile(r"\bKC\s+(\d{1,2})\s*[–-]\s*(\d{1,2})\b", re.IGNORECASE),
    re.compile(r"\b(\d{1,2})\s*[–-]\s*(\d{1,2})\s+game\b", re.IGNORECASE),
    re.compile(
        r"\bfinal(?:\s+score)?\s+(?:KC\s+)?"
        r"(\d{1,2})\s*[–-]\s*(\d{1,2})\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:won|lost)\s+it\s+(?:KC\s+)?"
        r"(\d{1,2})\s*[–-]\s*(\d{1,2})\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:won|lost)\s+KC\s+"
        r"(\d{1,2})\s*[–-]\s*(\d{1,2})\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:up|ahead|leading|lead)\s+(\d{1,2})\s*[–-]\s*(\d{1,2})\b",
        re.IGNORECASE,
    ),
)

# Bare "won 24-10" / "lost 18-19" — check only when the pair is a known score
# (either order). An unknown pair is first downs or other volume, not a final.
_LOOSE_WON_LOST = re.compile(
    r"\b(?:won|lost)\s+(\d{1,2})\s*[–-]\s*(\d{1,2})\b",
    re.IGNORECASE,
)

# Scoring-play yardage only when the claim is a TD/FG. An "88-yard run"
# or "11-yard catch" is not a scoring claim.
_TD_YARDS = re.compile(
    r"(?:from the (\d+)\b|(\d+)[-\s]yard(?:s)?\s+(?:TD|touchdown)\b)",
    re.IGNORECASE,
)
_CLAUSE_BREAK = re.compile(
    r"[.;:!?]|—|,|\n|\b(?:then|after|before|but|and)\b",
    re.IGNORECASE,
)

# Completions only: "20/24" or "20-of-24". A bare "24-10" is a score, not a line.
_PASS_LINE = re.compile(
    r"(\d{1,2})\s*(?:/|-of-)\s*(\d{1,2})(?:\s*,\s*(\d{2,3})\s*(?:YDS|yards))?",
    re.IGNORECASE,
)

# Team rates, not a passer line. Checked around the N-of-M token.
_DOWN_CTX = re.compile(
    r"(?:third\s+down|fourth\s+down|on\s+third|red\s+zone|conversions?)",
    re.IGNORECASE,
)

# Bind the line to the player in the same clause. Prefer a miss over a
# false positive: no 96-character "vicinity" windows.
_BIND_VERBS = r"(?:went|was|finished|threw|completed|at)"

_TD_TYPES = frozenset(
    {
        "td",
        "touchdown",
        "passing touchdown",
        "rushing touchdown",
        "receiving touchdown",
        "interception touchdown",
        "fumble recovery touchdown",
        "punt return touchdown",
        "kickoff return touchdown",
    }
)

_FG_TYPES = frozenset({"fg", "field goal", "field-goal"})

_DAYTIME = frozenset({"morning", "midday", "afternoon"})

_WORD_COUNTS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}

_EDITION_KEYS = (
    "headline",
    "dek",
    "videoHook",
    "theEdge",
    "storyline",
    "lastGameReview",
    "currentState",
    "gamePlan",
    "nextGame",
    "matchups",
    "xsandos",
    "strategies",
    "spotlight",
    "coaching",
    "debates",
    "runOfShow",
    "injuries",
    "personnel",
)

_FG_YARD = re.compile(r"(\d{1,2})[-\s]yard(?:s)?\s+field goal", re.IGNORECASE)
_FG_LIST = re.compile(r"field goals?\s*\(([^)]+)\)", re.IGNORECASE)
_FG_COUNT = re.compile(
    r"\b(one|two|three|four|five|six|seven|eight|nine|ten|\d+)[ \t]+"
    r"([A-Za-z][A-Za-z ]{1,24}?)?[ \t]*field goals?\b",
    re.IGNORECASE,
)
_TD_COUNT = re.compile(
    r"\b(one|two|three|four|five|six|seven|eight|nine|ten|\d+)[ \t]+"
    r"([A-Za-z][A-Za-z ]{1,24}?)?[ \t]*touchdowns?\b",
    re.IGNORECASE,
)
_YARD_NIGHT = re.compile(
    r"\d+(?:\.\d+)?[-\s]yard(?:s)?\s+night\b",
    re.IGNORECASE,
)
_FINISHED_NIGHT = re.compile(r"\bfinished the night\b", re.IGNORECASE)
_QB_NIGHT = re.compile(r"\bquarterback night\b", re.IGNORECASE)
_NIGHT_WORD = re.compile(r"\bnights?\b", re.IGNORECASE)
_ECHO_INSTRUCTION = re.compile(
    r"\bdo not (?:call|write|invent|include)\b"
    r"|this was not a night game"
    r"|never state a kickoff"
    r"|FACT CHECK RETRY"
    r"|SENTENCE REPAIR"
    r"|\[PRIVATE",
    re.IGNORECASE,
)
_GARBLED_RECORD = re.compile(
    r"\b(?:first|second|third|fourth|1st|2nd|3rd|4th)\b"
    r"[^.!?]{0,48}\bgames?\s+of\s+\d{1,2}-\d{1,2}\b",
    re.IGNORECASE,
)
_TEAM_GROUND = re.compile(
    r"\b([A-Za-z][A-Za-z .']{1,28}?)(?:'s)?\s+"
    r"(?:was\s+)?(\d{2,3})\s+"
    r"(?:on the ground|rush(?:ing)? yards)\b",
    re.IGNORECASE,
)
_TEAM_RUSH_YARDS = re.compile(
    r"\b(\d{2,3})\s+(?:team\s+)?rush(?:ing)?\s+yards\b",
    re.IGNORECASE,
)
_TEAM_PASS_YARDS = re.compile(
    r"\b(\d{2,3})\s+(?:net\s+)?passing yards\b",
    re.IGNORECASE,
)
_ABSOLUTE_CLAIM = re.compile(
    r"\b(?:the\s+)?(?:only|lone)\s+"
    r"(?:time|play|shot|miss|completion|incompletion|throw|target|"
    r"deep[- ]middle|vertical)\b"
    r"|\bthe only\b[^.!?]{0,80}"
    r"(?:deep|vertical|miss|shot|drive|interception|incompletion)",
    re.IGNORECASE,
)
_FIRST_PLAY_CLAIM = re.compile(
    r"\bthe first\b(?!\s*-?\s*(?:down|half|quarter|and|open|clean|window|read|look))"
    r"[^.!?—]{0,60}"
    r"(?:\bTD\b|touchdown|field goal|interception|\bINT\b|fumble|"
    r"deep[- ]|vertical|completion)",
    re.IGNORECASE,
)
_AFTER_NON_PLAY = re.compile(
    r"\b(?:a|the|this|that|their)\s+"
    r"(?:win|loss|game|kickoff|season|start|week|result)\b",
    re.IGNORECASE,
)
_SEQUENCE_GAIN = re.compile(
    r"\b(\d{1,3})[-\s]yard(?:s)?\b|\b(\d{1,3})\s+yards\b",
    re.IGNORECASE,
)
_POSSESSION_CLOCK = re.compile(
    r"\b(\d{1,2}:\d{2})\b(?:\s+(?:of\s+)?(?:possession|clock))?",
    re.IGNORECASE,
)
_FIRST_DOWNS = re.compile(r"\b(\d{1,2})\s+first downs?\b", re.IGNORECASE)
_GAME_YARDS = re.compile(r"\b(\d{3})[-\s]yard(?:s)?\b", re.IGNORECASE)
_NEVER_PLAY_CLAIM = re.compile(
    r"\bnever\s+(?:threw|completed|ran|scored|allowed|asked|hit|found|"
    r"targeted|connected)\b",
    re.IGNORECASE,
)
# Name tokens stay case-sensitive so IGNORECASE cannot turn "was"/"one"/"an"
# into a player. Only the verb is case-insensitive.
_FUMBLE_PAREN = re.compile(
    r"(?i:fumble)[^.!?]{0,40}\(([A-Z][A-Za-z''-]+)\)",
)
_FUMBLE_FORCED = re.compile(
    r"\b([A-Z][A-Za-z''-]+)\s+"
    r"(?i:forced|recovered|caused|blew up)\b[^.!?]{0,48}(?i:fumble)\b"
    r"|(?i:fumble)[^.!?]{0,48}\b(?i:forced|recovered|caused)\s+by\s+"
    r"([A-Z][A-Za-z''-]+)",
)
_INT_CREDIT = re.compile(
    r"\b([A-Z][A-Za-z''-]+)\s+(?i:intercept(?:ed|ion)|INT)\b"
    r"|\b(?i:intercept(?:ed|ion)|INT)\s+by\s+([A-Z][A-Za-z''-]+)",
)
_NOT_PLAYER_TOKENS = frozenset(
    {
        "then",
        "that",
        "this",
        "those",
        "these",
        "they",
        "them",
        "their",
        "there",
        "one",
        "was",
        "were",
        "and",
        "the",
        "late",
        "deep",
        "middle",
        "an",
        "a",
        "his",
        "her",
        "who",
        "when",
        "after",
        "before",
    }
)
_GARBLED_INITIAL = re.compile(r"\b([A-Z])['’]([A-Z][a-z]{3,})\b")
_BETWEEN_TACKLES = re.compile(r"\bbetween the tackles\b", re.IGNORECASE)
_ZONE_BLITZ_INT = re.compile(
    r"\bzone[- ]blitz\b[^.!?]{0,160}\b(?:int|intercept)",
    re.IGNORECASE,
)
_SNAP_LATER = re.compile(
    r"\b(?:\d+|one|two|three|four|five|six)\s+snaps?\b[^.!?]{0,48}\blater\b"
    r"|\blater\b[^.!?]{0,48}\b(?:\d+|one|two|three|four|five|six)\s+snaps?\b",
    re.IGNORECASE,
)
_ORPHAN_OPENER = re.compile(
    r"^(?:He|She|They|It|This|That|Those|These|His|Her|Their|the same)\b",
    re.IGNORECASE,
)
MAX_REPAIR_DROPS = 2
_MAX_REPAIR_DROPS = MAX_REPAIR_DROPS

# Qualified counts are not whole-game totals. Compare against the window
# when quarter data can compute it; otherwise skip.
_COUNT_SCOPE = re.compile(
    r"\b("
    r"early|opening|late|"
    r"first[- ]half|second[- ]half|"
    r"(?:in\s+the\s+)?(?:first|second|third|fourth|opening|final)\s+quarter|"
    r"q\s*[1-4]|"
    r"in\s+the\s+game|all\s+game|on\s+the\s+day|overall|\btotal\b"
    r")\b",
    re.IGNORECASE,
)
_WHOLE_GAME_SCOPES = frozenset(
    {"in the game", "all game", "on the day", "overall", "total"}
)
_SCOPE_QUARTERS = {
    "early": frozenset({1, 2}),
    "opening": frozenset({1, 2}),
    "late": frozenset({3, 4}),
    "first-half": frozenset({1, 2}),
    "first half": frozenset({1, 2}),
    "second-half": frozenset({3, 4}),
    "second half": frozenset({3, 4}),
    "first quarter": frozenset({1}),
    "opening quarter": frozenset({1}),
    "second quarter": frozenset({2}),
    "third quarter": frozenset({3}),
    "fourth quarter": frozenset({4}),
    "final quarter": frozenset({4}),
    "q1": frozenset({1}),
    "q2": frozenset({2}),
    "q3": frozenset({3}),
    "q4": frozenset({4}),
}

_AFTER_FOLLOWING = re.compile(
    r"\b(?P<rel>after|following)\s+(?:the\s+)?",
    re.IGNORECASE,
)
_SEQUENCE_SCORE = re.compile(
    r"(?:from the (\d+)\b|(\d+)[-\s]yard(?:s)?\s+(?:TD|touchdown|field goal)\b"
    r"|(\d{1,2})\s*[–-]\s*(\d{1,2}))",
    re.IGNORECASE,
)
_SEQUENCE_TURNOVER = re.compile(
    r"\b(interception|int|fumble|missed(?:\s+\d+[-\s]yard(?:er)?)?|"
    r"turnover on downs)\b",
    re.IGNORECASE,
)
_SEQUENCE_NAME = re.compile(r"[A-Za-z][A-Za-z''-]{3,}")
_SEQUENCE_STOP = frozenset(
    {
        "after",
        "following",
        "that",
        "this",
        "with",
        "from",
        "made",
        "make",
        "then",
        "when",
        "into",
        "touchdown",
        "field",
        "goal",
        "interception",
        "fumble",
        "missed",
        "yard",
        "yards",
        "score",
        "scoring",
        "play",
        "game",
        "quarter",
        "half",
        "first",
        "second",
        "third",
        "fourth",
        "kansas",
        "city",
        "miami",
        "dolphins",
        "chiefs",
        "football",
        "the",
        "and",
        "for",
    }
)

_PROTECTED_KEYS = frozenset(
    {
        "score",
        "result",
        "opponent",
        "label",
        "id",
        "at",
        "tv",
        "generatedAt",
        "slug",
        "generator",
        "edition",
        "record",
    }
)


class FactCheckError(RuntimeError):
    """Raised when the review still disagrees with ESPN after one retry."""


def review_text(narrative: dict | None) -> str:
    """Flatten lastGameReview prose for regex checks."""
    review = (narrative or {}).get("lastGameReview") or {}
    if not isinstance(review, dict):
        return ""
    parts: list[str] = []
    for key in ("lede", "score", "label", "opponent"):
        val = review.get(key)
        if val:
            parts.append(str(val))
    for para in review.get("analysis") or []:
        if isinstance(para, dict):
            parts.append(str(para.get("body") or ""))
        elif para:
            parts.append(str(para))
    for take in review.get("takeaways") or []:
        if isinstance(take, dict):
            parts.append(str(take.get("title") or ""))
            parts.append(str(take.get("body") or ""))
        elif take:
            parts.append(str(take))
    for key in ("whatWorked", "whatDidnt"):
        for item in review.get(key) or []:
            if item:
                parts.append(str(item))
    return "\n".join(p for p in parts if p)


def _walk_prose(value, parts: list[str]) -> None:
    if value is None or isinstance(value, (int, float, bool)):
        return
    if isinstance(value, str):
        if value.strip():
            parts.append(value)
        return
    if isinstance(value, dict):
        for item in value.values():
            _walk_prose(item, parts)
        return
    if isinstance(value, list):
        for item in value:
            _walk_prose(item, parts)


def edition_text(narrative: dict | None) -> str:
    """Flatten every generated section the writer can put a score into."""
    payload = narrative or {}
    parts: list[str] = []
    for key in _EDITION_KEYS:
        _walk_prose(payload.get(key), parts)
    return "\n".join(p for p in parts if p)


def _play_team(play: dict) -> str:
    return (play.get("team") or "").strip().upper()


def _play_kind(play: dict) -> str:
    typ = (play.get("type") or "").strip().lower()
    if typ in _FG_TYPES or typ == "fg":
        return "fg"
    if typ in _TD_TYPES or "touchdown" in typ or typ == "td":
        return "td"
    return ""


def _play_yards(play: dict):
    value = play.get("yards")
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _add_opponent_aliases(aliases: dict[str, str], name: str, abbr: str) -> None:
    tokens = [t for t in re.findall(r"[A-Za-z]+", name or "") if t.lower() not in ("the",)]
    if abbr:
        aliases[abbr.lower()] = abbr
    for token in tokens:
        aliases[token.lower()] = abbr or token[:3].upper()
    if name and len(tokens) >= 2:
        aliases[name.lower()] = abbr or tokens[-1][:3].upper()


def _team_aliases(last_game: dict | None, recap: dict | None) -> dict[str, str]:
    aliases = {
        "kc": "KC",
        "chiefs": "KC",
        "kansas city": "KC",
    }
    opp = ((recap or {}).get("oppAbbr") or "").strip().upper()
    if opp:
        aliases[opp.lower()] = opp
    _add_opponent_aliases(aliases, ((last_game or {}).get("opponent") or "").strip(), opp)
    prior = (recap or {}).get("prior") or {}
    prior_abbr = (prior.get("oppAbbr") or "").strip().upper()
    _add_opponent_aliases(
        aliases,
        (prior.get("opponent") or "").strip(),
        prior_abbr,
    )
    return aliases


def _prior_game_labels(recap: dict | None) -> set[str]:
    prior = (recap or {}).get("prior") or {}
    labels = set()
    abbr = (prior.get("oppAbbr") or "").strip().lower()
    if abbr:
        labels.add(abbr)
    for token in re.findall(r"[A-Za-z]+", prior.get("opponent") or ""):
        if token.lower() not in ("the",) and len(token) >= 3:
            labels.add(token.lower())
    return labels


def _last_game_labels(last_game: dict | None, recap: dict | None) -> set[str]:
    labels = set()
    opp = ((recap or {}).get("oppAbbr") or "").strip().lower()
    if opp:
        labels.add(opp)
    for token in re.findall(r"[A-Za-z]+", (last_game or {}).get("opponent") or ""):
        if token.lower() not in ("the",) and len(token) >= 3:
            labels.add(token.lower())
    return labels


def _player_teams(recap: dict | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for play in (recap or {}).get("scoringPlays") or []:
        if not isinstance(play, dict):
            continue
        team = _play_team(play)
        player = (play.get("player") or "").strip()
        if not team or not player:
            continue
        out[player.lower()] = team
        last = player.split()[-1]
        if len(last) >= 3:
            out[last.lower()] = team
    return out


_YARD_LINE_TEAM = re.compile(
    r"\b(?:the\s+)?(?:kc|chiefs|kansas city|mia|miami|dolphins)\s+\d{1,2}\b",
    re.IGNORECASE,
)


def _subject_clause(text: str, start: int) -> str:
    """Prose from the start of this clause up to the claim — the subject side."""
    clause_start = 0
    for hit in _CLAUSE_BREAK.finditer(text[:start]):
        clause_start = hit.end()
    return _YARD_LINE_TEAM.sub(" ", text[clause_start:start])


def _last_name_in(span: str, names: dict[str, str]) -> str:
    best = ""
    best_pos = -1
    for name in sorted(names, key=len, reverse=True):
        if len(name) < 2:
            continue
        for hit in re.finditer(rf"\b{re.escape(name)}\b", span, re.IGNORECASE):
            if hit.start() >= best_pos:
                best_pos = hit.start()
                best = names[name]
    return best


def _bound_team(
    text: str,
    start: int,
    end: int,
    aliases: dict[str, str],
    player_teams: dict[str, str],
) -> str:
    """Bind a scoring claim to the nearest subject before it, not any team in the sentence.

    'Kelce's 11-yard touchdown answered Miami' is KC. A team named after
    the claim (the opponent being answered) does not win.
    """
    del end  # subject is always before the claim
    subject = _subject_clause(text, start)
    player = _last_name_in(subject, player_teams)
    if player:
        return player
    return _last_name_in(subject, aliases)


def _score_team_for_yards(recap: dict | None, kind: str, yards: int) -> str:
    """Team that owns this scoring-play distance on the ESPN list.

    When only one club has an 11-yard TD, that play's team wins — do not
    bind the claim to Miami just because Miami is named nearby.
    """
    owners = [
        team
        for team, bag in official_yards_by_team(recap, kind).items()
        if yards in bag
    ]
    if len(owners) == 1:
        return owners[0]
    return ""


def official_missed_fg_yards(recap: dict | None) -> set[int]:
    out: set[int] = set()
    for row in (recap or {}).get("driveResults") or []:
        if not isinstance(row, dict):
            continue
        if (row.get("result") or "").lower() != "missed fg":
            continue
        yards = row.get("yards")
        try:
            if yards not in (None, ""):
                out.add(int(yards))
        except (TypeError, ValueError):
            continue
    for play in _plays(recap):
        text = (play.get("text") or "").lower()
        if "no good" not in text and "wide" not in text and "miss" not in text:
            continue
        if "field goal" not in text and "yarder" not in text:
            continue
        yards = play.get("yards")
        try:
            if yards not in (None, ""):
                out.add(int(yards))
        except (TypeError, ValueError):
            continue
    return out


def _parse_count_word(raw: str):
    token = (raw or "").strip().lower()
    if token in _WORD_COUNTS:
        return _WORD_COUNTS[token]
    try:
        return int(token)
    except (TypeError, ValueError):
        return None


def _alias_team(phrase: str, aliases: dict[str, str]) -> str:
    token = " ".join((phrase or "").lower().split())
    if not token:
        return ""
    if token in aliases:
        return aliases[token]
    for alias in sorted(aliases, key=len, reverse=True):
        if len(alias) < 3:
            continue
        if re.search(rf"\b{re.escape(alias)}\b", token, re.IGNORECASE):
            return aliases[alias]
    return ""


def _box_yards_int(raw) -> int | None:
    token = str(raw or "").replace(",", "").strip()
    if not token:
        return None
    try:
        return int(token)
    except (TypeError, ValueError):
        return None


def official_box_yards_by_team(
    recap: dict | None, kind: str, game: str = "last"
) -> dict[str, int]:
    """Team rushing / passing totals from the last or prior-game box."""
    payload = recap or {}
    if game == "prior":
        payload = payload.get("prior") or {}
    field = "rushingYards" if kind == "rush" else "netPassingYards"
    out: dict[str, int] = {}
    kc = _box_yards_int((payload.get("kc") or {}).get(field))
    if kc is not None:
        out["KC"] = kc
    opp_abbr = (payload.get("oppAbbr") or "").strip().upper()
    opp = _box_yards_int((payload.get("opp") or {}).get(field))
    if opp is not None and opp_abbr:
        out[opp_abbr] = opp
    return out


def official_yards_by_team(recap: dict | None, kind: str) -> dict[str, set[int]]:
    """Scoring-play yards, or team box yards when kind is rush/pass.

    Rush/pass look at the last-game recap and the prior-game recap so a
    Colts-week rushing total cannot be swapped for Walker's line.
    """
    if kind in {"rush", "pass"}:
        by_team: dict[str, set[int]] = {}
        for game in ("last", "prior"):
            for team, yards in official_box_yards_by_team(recap, kind, game).items():
                by_team.setdefault(team, set()).add(yards)
        return by_team
    by_team = {}
    for play in (recap or {}).get("scoringPlays") or []:
        if not isinstance(play, dict):
            continue
        if kind and _play_kind(play) != kind:
            continue
        yards = _play_yards(play)
        if yards is None:
            continue
        team = _play_team(play) or "?"
        by_team.setdefault(team, set()).add(yards)
    return by_team


def _play_quarter(play: dict):
    try:
        return int(play.get("quarter"))
    except (TypeError, ValueError):
        return None


def official_count_by_team(
    recap: dict | None, kind: str, quarters: frozenset | None = None
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for play in (recap or {}).get("scoringPlays") or []:
        if not isinstance(play, dict):
            continue
        if _play_kind(play) != kind:
            continue
        if quarters is not None:
            qtr = _play_quarter(play)
            if qtr not in quarters:
                continue
        team = _play_team(play) or "?"
        counts[team] = counts.get(team, 0) + 1
    return counts


def _normalize_scope_token(raw: str) -> str:
    token = re.sub(r"\s+", " ", (raw or "").lower().replace("–", "-"))
    token = re.sub(r"^in the ", "", token)
    token = token.replace("q ", "q")
    return token


def _count_scope(text: str, start: int, end: int):
    """Return None (whole game), a quarter frozenset, or 'skip'."""
    window = text[max(0, start - 72) : min(len(text), end + 72)]
    match = _COUNT_SCOPE.search(window)
    if not match:
        return None
    token = _normalize_scope_token(match.group(1))
    if token in _WHOLE_GAME_SCOPES:
        return None
    if token in _SCOPE_QUARTERS:
        return _SCOPE_QUARTERS[token]
    return "skip"


def _pair_allowed(pair: tuple[int, int], pairs: set[tuple[int, int]]) -> bool:
    """Score order is irrelevant: 24-10 and 10-24 are the same pair."""
    return pair in pairs or (pair[1], pair[0]) in pairs


def _mask_stat_lookalikes(text: str) -> str:
    masked = _OF_LINE.sub("X-of-Y", text)
    return _SLASH_LINE.sub("X/Y", masked)


def _int_pair(a, b):
    try:
        return int(a), int(b)
    except (TypeError, ValueError):
        return None


def allowed_score_pairs(last_game: dict | None, recap: dict | None) -> set[tuple[int, int]]:
    """Official final plus every ESPN score-after. Both orders are allowed.

    0-0 is always a real game state (kickoff, or any tie at the start).
    """
    pairs: set[tuple[int, int]] = {(0, 0)}
    last = last_game or {}
    kc, opp = last.get("kcScore"), last.get("oppScore")
    pair = _int_pair(kc, opp)
    if pair:
        pairs.add(pair)
        pairs.add((pair[1], pair[0]))
    for play in (recap or {}).get("scoringPlays") or []:
        if not isinstance(play, dict):
            continue
        play_pair = _int_pair(play.get("kcScore"), play.get("oppScore"))
        if play_pair:
            pairs.add(play_pair)
            pairs.add((play_pair[1], play_pair[0]))
        after = play.get("scoreAfter") or ""
        match = re.search(r"(\d+)\s*[–-]\s*(\d+)", after)
        if match:
            after_pair = _int_pair(match.group(1), match.group(2))
            if after_pair:
                pairs.add(after_pair)
                pairs.add((after_pair[1], after_pair[0]))
    return pairs


def official_score_yards(recap: dict | None) -> set[int]:
    """Yardage numbers on ESPN scoring plays (TD and FG)."""
    yards: set[int] = set()
    for play in (recap or {}).get("scoringPlays") or []:
        if not isinstance(play, dict):
            continue
        value = play.get("yards")
        if value is None or value == "":
            continue
        try:
            yards.add(int(value))
        except (TypeError, ValueError):
            continue
    return yards


def official_td_yards(recap: dict | None) -> set[int]:
    yards: set[int] = set()
    for play in (recap or {}).get("scoringPlays") or []:
        if not isinstance(play, dict):
            continue
        typ = (play.get("type") or "").strip().lower()
        if typ not in _TD_TYPES and "touchdown" not in typ and typ != "td":
            continue
        value = play.get("yards")
        if value is None or value == "":
            continue
        try:
            yards.add(int(value))
        except (TypeError, ValueError):
            continue
    return yards


def _check_scores(text: str, last_game: dict, recap: dict) -> list[str]:
    pairs = allowed_score_pairs(last_game, recap)
    if not pairs:
        return []
    masked = _mask_stat_lookalikes(text)
    issues = []
    seen: set[tuple[int, int]] = set()

    def _consider(match) -> None:
        pair = _int_pair(match.group(1), match.group(2))
        if not pair or pair in seen:
            return
        if _pair_allowed(pair, pairs):
            seen.add(pair)
            return
        seen.add(pair)
        issues.append(
            f"score {pair[0]}-{pair[1]} is not the official final "
            f"or an ESPN score-after ({match.group(0)!r})"
        )

    for rx in _SCORE_PATTERNS:
        for match in rx.finditer(masked):
            _consider(match)
    # Bare won/lost is a score claim only when the pair is a known score
    # (either order). Unknown pairs stay out — "lost 18-19" is first downs.
    for match in _LOOSE_WON_LOST.finditer(masked):
        pair = _int_pair(match.group(1), match.group(2))
        if not pair or pair in seen:
            continue
        if _pair_allowed(pair, pairs):
            seen.add(pair)
    return issues


def _check_td_yards(
    text: str, recap: dict, last_game: dict | None = None
) -> list[str]:
    allowed = official_score_yards(recap)
    if not allowed:
        return []
    by_team = official_yards_by_team(recap, "td")
    aliases = _team_aliases(last_game, recap)
    players = _player_teams(recap)
    issues = []
    seen: set[tuple[str, int]] = set()
    for match in _TD_YARDS.finditer(text):
        raw = match.group(1) or match.group(2)
        try:
            yards = int(raw)
        except (TypeError, ValueError):
            continue
        if match.group(1):
            window = text[match.start() : min(len(text), match.end() + 56)]
            if re.search(
                r"field goal|instead of a touchdown|rush(?:ing)? yards|passing yards",
                window,
                re.I,
            ):
                continue
        bound = _bound_team(text, match.start(), match.end(), aliases, players)
        team = bound or _score_team_for_yards(recap, "td", yards)
        key = (team or "*", yards)
        if key in seen:
            continue
        seen.add(key)
        if team:
            team_td = by_team.get(team, set())
            if yards not in team_td:
                issues.append(
                    f"scoring yardage {yards} is not a {team} ESPN touchdown "
                    f"({match.group(0)!r}; {team} TD yards "
                    f"{sorted(team_td) or 'none'})"
                )
            continue
        if yards not in allowed:
            issues.append(
                f"scoring yardage {yards} is not on the ESPN scoring-play list "
                f"({match.group(0)!r}; official yards {sorted(allowed)})"
            )
    return issues


def _check_fg_claims(
    text: str, recap: dict, last_game: dict | None = None
) -> list[str]:
    by_team = official_yards_by_team(recap, "fg")
    if not by_team:
        return []
    aliases = _team_aliases(last_game, recap)
    players = _player_teams(recap)
    issues = []

    for match in _FG_LIST.finditer(text):
        yards = []
        for raw in re.findall(r"\d{1,2}", match.group(1) or ""):
            try:
                yards.append(int(raw))
            except (TypeError, ValueError):
                continue
        if not yards:
            continue
        missed = official_missed_fg_yards(recap)
        team = _bound_team(text, match.start(), match.end(), aliases, players)
        owners = []
        for value in yards:
            hit = [abbr for abbr, bag in by_team.items() if value in bag]
            if value in missed:
                hit.append("MISS")
            owners.append(set(hit))
        shared = owners[0]
        for bag in owners[1:]:
            shared &= bag
        if team:
            team_fg = by_team.get(team, set()) | missed
            bad = [v for v in yards if v not in team_fg]
            if bad:
                issues.append(
                    f"field goals {yards} credited to {team} are not that "
                    f"team's ESPN kicks ({match.group(0)!r}; {team} FG yards "
                    f"{sorted(team_fg) or 'none'})"
                )
        elif not shared:
            issues.append(
                f"field-goal yardage {yards} mixes teams or is not on the "
                f"ESPN FG list ({match.group(0)!r})"
            )

    for match in _FG_YARD.finditer(text):
        try:
            yards = int(match.group(1))
        except (TypeError, ValueError):
            continue
        bound = _bound_team(text, match.start(), match.end(), aliases, players)
        team = bound or _score_team_for_yards(recap, "fg", yards)
        all_fg = set().union(*by_team.values()) if by_team else set()
        missed = official_missed_fg_yards(recap)
        all_fg |= missed
        if yards in missed:
            continue
        if team:
            if yards not in by_team.get(team, set()) and yards not in missed:
                issues.append(
                    f"field-goal yardage {yards} is not a {team} ESPN kick "
                    f"({match.group(0)!r})"
                )
        elif yards not in all_fg:
            issues.append(
                f"field-goal yardage {yards} is not on the ESPN FG list "
                f"({match.group(0)!r})"
            )

    for match in _FG_COUNT.finditer(text):
        issue = _count_issue(
            text, match, recap, "fg", aliases, "field-goal count"
        )
        if issue:
            issues.append(issue)

    for match in _TD_COUNT.finditer(text):
        issue = _count_issue(
            text, match, recap, "td", aliases, "touchdown count"
        )
        if issue:
            issues.append(issue)
    return issues


def _count_issue(
    text: str,
    match,
    recap: dict,
    kind: str,
    aliases: dict[str, str],
    label: str,
):
    claimed = _parse_count_word(match.group(1))
    team = _alias_team(match.group(2) or "", aliases)
    if claimed is None or not team:
        return None
    scope = _count_scope(text, match.start(), match.end())
    if scope == "skip":
        return None
    official = official_count_by_team(recap, kind, quarters=scope).get(team, 0)
    if claimed == official:
        return None
    return (
        f"{team} {label} {claimed} disagrees with ESPN "
        f"{official} ({match.group(0)!r})"
    )


def _check_part_of_day(
    text: str, last_game: dict | None, recap: dict | None = None
) -> list[str]:
    part = collect.kickoff_part_of_day((last_game or {}).get("date") or "")
    if part not in _DAYTIME or not text:
        return []
    prior_labels = _prior_game_labels(recap)
    issues = []
    seen: set[str] = set()
    for match in _NIGHT_WORD.finditer(text):
        sentence = _sentence_at(text, match.start())
        low = sentence.lower()
        if "nightmare" in low:
            continue
        if prior_labels and any(
            re.search(rf"\b{re.escape(lab)}\b", low) for lab in prior_labels
        ):
            continue
        if _foreign_team_sentence(sentence, last_game, recap):
            continue
        snippet = match.group(0)
        key = snippet.lower()
        if key in seen:
            continue
        seen.add(key)
        issues.append(
            f"kickoff is {part}; do not write {snippet!r} about the game"
        )
    return issues


_FOREIGN_TEAMS = frozenset(
    {
        "chargers",
        "lac",
        "bills",
        "ravens",
        "bengals",
        "browns",
        "steelers",
        "texans",
        "colts",
        "jaguars",
        "titans",
        "broncos",
        "raiders",
        "patriots",
        "jets",
        "dolphins",
        "eagles",
        "cowboys",
        "giants",
        "commanders",
        "bears",
        "lions",
        "packers",
        "vikings",
        "falcons",
        "panthers",
        "saints",
        "buccaneers",
        "cardinals",
        "rams",
        "49ers",
        "seahawks",
    }
)


def _foreign_team_sentence(
    sentence: str, last_game: dict | None, recap: dict | None
) -> bool:
    """True when the sentence is about some other club, not this tape."""
    low = (sentence or "").lower()
    known = {"kc", "chiefs", "kansas"}
    for token in re.findall(r"[A-Za-z]+", (last_game or {}).get("opponent") or ""):
        if len(token) >= 3:
            known.add(token.lower())
    opp = ((recap or {}).get("oppAbbr") or "").strip().lower()
    if opp:
        known.add(opp)
    known |= _prior_game_labels(recap)
    hits = [name for name in _FOREIGN_TEAMS if re.search(rf"\b{re.escape(name)}\b", low)]
    if not hits:
        return False
    return not any(name in known for name in hits)


def _check_echoed_instructions(text: str) -> list[str]:
    if not text:
        return []
    issues = []
    seen: set[str] = set()
    for match in _ECHO_INSTRUCTION.finditer(text):
        snippet = match.group(0)
        key = snippet.lower()
        if key in seen:
            continue
        seen.add(key)
        issues.append(f"echoed writer instruction ({snippet!r})")
    return issues


def _check_record_phrasing(text: str) -> list[str]:
    if not text:
        return []
    issues = []
    for match in _GARBLED_RECORD.finditer(text):
        issues.append(
            f"garbled record/ordinal phrasing ({match.group(0)!r})"
        )
    return issues


def _parse_pass_line(value: str):
    match = _PASS_LINE.search(value or "")
    if not match:
        return None
    try:
        comp, att = int(match.group(1)), int(match.group(2))
    except (TypeError, ValueError):
        return None
    yds = None
    if match.group(3):
        try:
            yds = int(match.group(3))
        except (TypeError, ValueError):
            yds = None
    return comp, att, yds


def _final_score_pairs(last_game: dict | None) -> set[tuple[int, int]]:
    pair = _int_pair((last_game or {}).get("kcScore"), (last_game or {}).get("oppScore"))
    if not pair:
        return set()
    return {pair, (pair[1], pair[0])}


def _down_context(text: str, start: int, end: int, pad: int = 48) -> bool:
    window = text[max(0, start - pad) : min(len(text), end + pad)]
    return bool(_DOWN_CTX.search(window))


def _bound_pass_matches(text: str, last_name: str):
    """Yield (match, comp, att, yds) only when the line is bound to last_name."""
    if not last_name or not text:
        return
    name = re.escape(last_name)
    token = (
        r"(\d{1,2})\s*(?:/|-of-)\s*(\d{1,2})"
        r"(?:\s*,\s*(\d{2,3})\s*(?:YDS|yards))?"
    )
    patterns = (
        # "Mahomes went 20-of-24" / "Mahomes was 20/24" / "Mahomes' 20-of-24"
        rf"{name}(?:['’]s)?(?:\s+{_BIND_VERBS})?\s+{token}",
        # "Mahomes (20/24, 246 YDS)"
        rf"{name}\s*\(\s*{token}",
        # "20-of-24 from Mahomes" / "20-of-24, Mahomes finished"
        rf"{token}\s+(?:from\s+)?(?:{_BIND_VERBS}\s+)?{name}",
    )
    for raw in patterns:
        rx = re.compile(raw, re.IGNORECASE)
        for match in rx.finditer(text):
            claimed = _parse_pass_line(match.group(0))
            if claimed:
                yield match, claimed


def _check_stat_lines(text: str, recap: dict, last_game: dict | None = None) -> list[str]:
    issues = []
    finals = _final_score_pairs(last_game)
    for leader in (recap or {}).get("leaders") or []:
        if not isinstance(leader, dict):
            continue
        player = (leader.get("player") or "").strip()
        value = leader.get("value") or ""
        last = player.split()[-1] if player else ""
        if len(last) < 3:
            continue
        official = _parse_pass_line(value)
        if not official:
            continue
        seen: set[tuple[int, int]] = set()
        for match, claimed in _bound_pass_matches(text, last):
            if _down_context(text, match.start(), match.end()):
                continue
            pair = (claimed[0], claimed[1])
            if pair in finals:
                continue
            if pair in seen:
                continue
            seen.add(pair)
            if pair != (official[0], official[1]):
                issues.append(
                    f"{player} passing line {claimed[0]}-of-{claimed[1]} "
                    f"disagrees with ESPN {official[0]}/{official[1]}"
                )
            if (
                claimed[2] is not None
                and official[2] is not None
                and claimed[2] != official[2]
            ):
                issues.append(
                    f"{player} passing yards {claimed[2]} disagree with "
                    f"ESPN {official[2]}"
                )
    return issues


def _clock_seconds(raw) -> int:
    text = str(raw or "0:00")
    parts = [p for p in text.replace(".", ":").split(":") if p != ""]
    try:
        if len(parts) >= 2:
            return int(parts[0]) * 60 + int(parts[1])
        return int(parts[0])
    except (TypeError, ValueError):
        return 0


def _event_kind(raw: str) -> str:
    token = (raw or "").strip().lower()
    if token in _TD_TYPES or token == "td" or "touchdown" in token:
        return "td"
    if token in _FG_TYPES or "field goal" in token:
        return "fg"
    if token in {"int", "interception"} or "intercept" in token:
        return "int"
    if "fumble" in token:
        return "fumble"
    if "miss" in token:
        return "missed fg"
    if "down" in token:
        return "turnover on downs"
    return token


def _event_sort_key(event: dict) -> tuple:
    try:
        quarter = int(event.get("quarter") or 0)
    except (TypeError, ValueError):
        quarter = 0
    return (quarter, -_clock_seconds(event.get("clock")))


def _timeline(recap: dict | None) -> list[dict]:
    events: list[dict] = []
    for play in (recap or {}).get("scoringPlays") or []:
        if not isinstance(play, dict):
            continue
        kind = _event_kind(_play_kind(play) or play.get("type") or "")
        if not kind:
            continue
        pair = _int_pair(play.get("kcScore"), play.get("oppScore"))
        after = play.get("scoreAfter") or ""
        match = re.search(r"(\d+)\s*[–-]\s*(\d+)", after)
        if not pair and match:
            pair = _int_pair(match.group(1), match.group(2))
        player = (play.get("player") or "").strip()
        events.append(
            {
                "kind": kind,
                "yards": _play_yards(play),
                "player": player,
                "quarter": play.get("quarter"),
                "clock": play.get("clock") or "",
                "pair": pair,
                "blob": " ".join(
                    p for p in (player, kind, str(play.get("yards") or ""), after)
                    if p
                ).lower(),
                "label": (
                    f"{player or kind} {play.get('yards') or ''}yd {kind} "
                    f"Q{play.get('quarter')} {play.get('clock') or ''}"
                ).strip(),
            }
        )
    for row in (recap or {}).get("driveResults") or []:
        if not isinstance(row, dict):
            continue
        kind = _event_kind(row.get("result") or "")
        if not kind:
            continue
        player = (row.get("player") or "").strip()
        detail = (row.get("detail") or "").strip()
        events.append(
            {
                "kind": kind,
                "yards": row.get("yards"),
                "player": player,
                "quarter": row.get("quarter"),
                "clock": row.get("clock") or "",
                "pair": None,
                "blob": " ".join(p for p in (player, detail, kind) if p).lower(),
                "label": (
                    f"{player or detail or kind} {kind} "
                    f"Q{row.get('quarter')} {row.get('clock') or ''}"
                ).strip(),
            }
        )
    for play in _plays(recap):
        kind = _event_kind(play.get("kind") or "")
        text = play.get("text") or ""
        if not kind:
            if "pass" in text.lower() and "incomplete" not in text.lower():
                kind = "completion"
            elif play.get("kind") == "rush" or "end" in (play.get("direction") or ""):
                kind = "rush"
            else:
                continue
        player = (
            play.get("target")
            or play.get("interceptedBy")
            or play.get("forcedBy")
            or ""
        )
        events.append(
            {
                "kind": kind,
                "yards": play.get("yards"),
                "player": player,
                "quarter": play.get("quarter"),
                "clock": play.get("clock") or "",
                "pair": None,
                "blob": " ".join(
                    p
                    for p in (player, text, kind, str(play.get("yards") or ""))
                    if p
                ).lower(),
                "label": (
                    f"{player or text[:48] or kind} {play.get('yards') or ''}yd "
                    f"{kind} Q{play.get('quarter')} {play.get('clock') or ''}"
                ).strip(),
            }
        )
    events.sort(key=_event_sort_key)
    deduped = []
    seen: set[tuple] = set()
    for event in events:
        key = (
            event.get("kind"),
            event.get("quarter"),
            event.get("clock"),
            event.get("yards"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(event)
    return deduped


def _span_names(span: str) -> list[str]:
    names = []
    for hit in _SEQUENCE_NAME.finditer(span or ""):
        token = hit.group(0).lower().replace("’", "'")
        if token in _SEQUENCE_STOP:
            continue
        names.append(token)
    return names


def _resolve_timeline_event(span: str, events: list[dict], prefer_last: bool = False):
    """Map a prose span to one ESPN timeline event, or None if ambiguous."""
    if not span or not events:
        return None
    yards = None
    want_kind = ""
    pair = None
    score = None
    if prefer_last:
        for hit in _SEQUENCE_SCORE.finditer(span):
            score = hit
    else:
        score = _SEQUENCE_SCORE.search(span)
    if score:
        raw_yards = score.group(1) or score.group(2)
        if raw_yards:
            try:
                yards = int(raw_yards)
            except (TypeError, ValueError):
                yards = None
            low = score.group(0).lower()
            if "field goal" in low:
                want_kind = "fg"
            elif "td" in low or "touchdown" in low or score.group(1):
                want_kind = "td"
        elif score.group(3) and score.group(4):
            pair = _int_pair(score.group(3), score.group(4))
        else:
            pair = None
    else:
        pair = None
    if yards is None:
        gain = None
        if prefer_last:
            for hit in _SEQUENCE_GAIN.finditer(span):
                gain = hit
        else:
            gain = _SEQUENCE_GAIN.search(span)
        if gain:
            try:
                yards = int(gain.group(1) or gain.group(2))
            except (TypeError, ValueError):
                yards = None
    turn = _SEQUENCE_TURNOVER.search(span)
    # A described gain (Kelce 48) is not the nearest turnover just because
    # a passer name also appears on an INT play.
    if turn and yards is None:
        want_kind = _event_kind(turn.group(1))
        pair = None
    names = _span_names(span)
    hits = []
    for event in events:
        if want_kind and event["kind"] != want_kind:
            continue
        if yards is not None and event.get("yards") != yards:
            continue
        if pair and event.get("pair"):
            if not _pair_allowed(pair, {event["pair"], (event["pair"][1], event["pair"][0])}):
                continue
        elif pair and not event.get("pair"):
            continue
        if names and not any(name in event["blob"] for name in names):
            # Scoring yardage / score-after can stand alone.
            if not (yards is not None or (pair and event.get("pair"))):
                continue
        hits.append(event)
    if len(hits) == 1:
        return hits[0]
    if names:
        named = [event for event in hits if any(name in event["blob"] for name in names)]
        if len(named) == 1:
            return named[0]
    return None


def _right_span(text: str, start: int) -> str:
    chunk = text[start : min(len(text), start + 80)]
    return re.split(r"[.;,!?]|—", chunk, maxsplit=1)[0].strip()


def _left_span(text: str, start: int) -> str:
    chunk = text[max(0, start - 120) : start]
    return re.split(r"[.;!?]|—", chunk)[-1].strip()


def _check_play_sequence(text: str, recap: dict | None) -> list[str]:
    """'A after B' / 'A following B' must match ESPN play order."""
    events = _timeline(recap)
    if len(events) < 2 or not text:
        return []
    issues = []
    seen: set[str] = set()
    for match in _AFTER_FOLLOWING.finditer(text):
        left = _left_span(text, match.start())
        right = _right_span(text, match.end())
        if _AFTER_NON_PLAY.search(right):
            continue
        first = _resolve_timeline_event(left, events, prefer_last=True)
        second = _resolve_timeline_event(right, events)
        if not first or not second or first is second:
            continue
        # One side must be a scoring play; the other a turnover or another play.
        kinds = {first["kind"], second["kind"]}
        if not kinds & {"td", "fg"}:
            continue
        if _event_sort_key(first) > _event_sort_key(second):
            continue
        snippet = (left[-48:] + match.group(0) + right).strip()
        snippet = re.sub(r"\s+", " ", snippet)
        key = snippet.lower()
        if key in seen:
            continue
        seen.add(key)
        issues.append(
            f"play order: {first['label']} is not after {second['label']} "
            f"({snippet!r})"
        )
    return issues


def _last_name_token(raw: str) -> str:
    token = (raw or "").strip()
    if not token:
        return ""
    token = token.replace(".", " ").replace("'", "")
    parts = [p for p in re.split(r"[^A-Za-z]+", token) if p]
    if not parts:
        return ""
    return parts[-1].lower()


def _plays(recap: dict | None) -> list[dict]:
    out = []
    for play in (recap or {}).get("plays") or []:
        if isinstance(play, dict):
            out.append(play)
    return out


def _official_names(plays: list[dict], *fields: str) -> set[str]:
    names: set[str] = set()
    for play in plays:
        for field in fields:
            token = _last_name_token(play.get(field) or "")
            if token:
                names.add(token)
        blob = (play.get("text") or "").lower()
        for field in fields:
            raw = play.get(field) or ""
            last = _last_name_token(raw)
            if last and last in blob:
                names.add(last)
    return names


def _sentence_at(text: str, index: int) -> str:
    start = text.rfind(".", 0, index) + 1
    end = text.find(".", index)
    if end < 0:
        end = len(text)
    return text[start:end].strip()


def _check_turnover_credit(text: str, recap: dict | None) -> list[str]:
    """Credit for forcing/recovering a turnover must match the play text."""
    plays = _plays(recap)
    fumbles = [p for p in plays if p.get("kind") == "fumble" or "fumble" in (p.get("text") or "").lower()]
    ints = [p for p in plays if p.get("kind") == "int" or "intercept" in (p.get("text") or "").lower()]
    if not text or (not fumbles and not ints):
        return []
    official_force = _official_names(fumbles, "forcedBy", "recoveredBy")
    official_int = _official_names(ints, "interceptedBy")
    team_names = {
        "kc": "KC",
        "chiefs": "KC",
        "kansas": "KC",
        "mia": "MIA",
        "miami": "MIA",
        "dolphins": "MIA",
    }
    opp = ((recap or {}).get("oppAbbr") or "").strip().upper()
    if opp:
        team_names[opp.lower()] = opp
    int_teams = set()
    for play in ints:
        offense = (play.get("team") or "").upper()
        if offense == "KC":
            int_teams.add(opp or "OPP")
        elif offense:
            int_teams.add("KC")
        who = _last_name_token(play.get("interceptedBy") or "")
        if who:
            official_int.add(who)
    fumblers = set()
    for play in fumbles:
        head = re.split(r"FUMBLES", play.get("text") or "", maxsplit=1, flags=re.I)[0]
        names = re.findall(r"\b([A-Z]\.[A-Za-z\-']+)\b", head)
        if names:
            fumblers.add(_last_name_token(names[0]))
    issues = []
    seen: set[str] = set()

    passers = set()
    for play in ints:
        head = re.split(
            r"INTERCEPTED", play.get("text") or "", maxsplit=1, flags=re.I
        )[0]
        names = re.findall(r"\b([A-Z]\.[A-Za-z\-']+)\b", head)
        if names:
            passers.add(_last_name_token(names[0]))

    def _reject(name: str, role: str, snippet: str) -> None:
        last = _last_name_token(name)
        if (
            not last
            or last in fumblers
            or last in passers
            or last in _NOT_PLAYER_TOKENS
        ):
            return
        key = f"{role}:{last}:{snippet.lower()}"
        if key in seen:
            return
        seen.add(key)
        allowed = official_force if role == "fumble" else official_int
        if last in allowed:
            return
        if role == "int" and last in team_names:
            if team_names[last] in int_teams:
                return
            issues.append(
                f"turnover credit: {name} is not the team that intercepted "
                f"({snippet!r})"
            )
            return
        issues.append(
            f"turnover credit: {name} is not who ESPN lists as "
            f"{'forcing/recovering the fumble' if role == 'fumble' else 'the interceptor'} "
            f"({snippet!r})"
        )

    for match in _FUMBLE_PAREN.finditer(text):
        _reject(match.group(1), "fumble", match.group(0))
    for match in _FUMBLE_FORCED.finditer(text):
        name = match.group(1) or match.group(2)
        _reject(name, "fumble", match.group(0))
    for match in _INT_CREDIT.finditer(text):
        name = match.group(1) or match.group(2)
        if (name or "").lower() in {"int", "interception", "intercepted"}:
            continue
        _reject(name, "int", match.group(0))
    return issues


def _deep_plays(recap: dict | None, team: str = "KC") -> list[dict]:
    out = []
    for play in _plays(recap):
        if (play.get("team") or "").upper() != team:
            continue
        direction = (play.get("direction") or "").lower()
        text = (play.get("text") or "").lower()
        if "deep" not in direction and "deep" not in text:
            continue
        out.append(play)
    return out


def _is_incompletion(play: dict) -> bool:
    kind = (play.get("kind") or "").lower()
    text = (play.get("text") or "").lower()
    return kind in {"incompletion", "int"} or "incomplete" in text or "intercept" in text


def _is_completion(play: dict) -> bool:
    if _is_incompletion(play):
        return False
    kind = (play.get("kind") or "").lower()
    text = (play.get("text") or "").lower()
    return kind == "completion" or ("pass" in text and "incomplete" not in text)


def _check_absolute_claims(text: str, recap: dict | None) -> list[str]:
    """only/first/never/lone game-event claims must match the play-by-play.

    Unverifiable uniqueness claims are rejected rather than waved through.
    """
    if not text:
        return []
    plays = _plays(recap)
    if not plays:
        issues = []
        for rx in (_ABSOLUTE_CLAIM, _FIRST_PLAY_CLAIM, _NEVER_PLAY_CLAIM):
            for match in rx.finditer(text):
                issues.append(
                    f"absolute claim cannot be verified against the play-by-play "
                    f"({_sentence_at(text, match.start())!r})"
                )
        return issues
    issues = []
    seen: set[str] = set()
    deep = _deep_plays(recap, "KC")
    deep_comp = [p for p in deep if _is_completion(p)]
    deep_middle_miss = [
        p
        for p in deep
        if _is_incompletion(p)
        and "middle" in ((p.get("direction") or "") + " " + (p.get("text") or "")).lower()
    ]

    def _add(snippet: str, reason: str) -> None:
        key = snippet.lower()
        if key in seen:
            return
        seen.add(key)
        issues.append(f"{reason} ({snippet!r})")

    for match in _ABSOLUTE_CLAIM.finditer(text):
        sentence = _sentence_at(text, match.start())
        low = sentence.lower()
        if re.search(r"\bonly\s+\d", low) or re.search(r"\bonly\s+after\b", low) or re.search(r"\bonly\s+if\b", low):
            continue
        if "vertical" in low or (
            "deep" in low and ("carry" in low or "drive" in low or "shot" in low)
            and "miss" not in low and "intercept" not in low
        ):
            if len(deep_comp) != 1:
                _add(
                    sentence,
                    f"absolute claim: ESPN has {len(deep_comp)} KC deep/vertical "
                    f"completions, not a unique one",
                )
            continue
        if "deep" in low and (
            "miss" in low or "incomplete" in low or "intercept" in low
        ):
            if len(deep_middle_miss) != 1:
                _add(
                    sentence,
                    f"absolute claim: ESPN has {len(deep_middle_miss)} KC "
                    f"deep-middle misses, not a unique one",
                )
            continue
        _add(sentence, "absolute claim cannot be verified against the play-by-play")

    for match in _FIRST_PLAY_CLAIM.finditer(text):
        sentence = _sentence_at(text, match.start())
        _add(sentence, "absolute first-claim cannot be verified against the play-by-play")

    for match in _NEVER_PLAY_CLAIM.finditer(text):
        sentence = _sentence_at(text, match.start())
        _add(sentence, "absolute never-claim cannot be verified against the play-by-play")
    return issues


def _check_team_yards(
    text: str, recap: dict | None, last_game: dict | None
) -> list[str]:
    """Team rushing/passing totals vs last-game and prior-game boxes."""
    if not text or not recap:
        return []
    last_rush = official_box_yards_by_team(recap, "rush", "last")
    prior_rush = official_box_yards_by_team(recap, "rush", "prior")
    last_pass = official_box_yards_by_team(recap, "pass", "last")
    prior_pass = official_box_yards_by_team(recap, "pass", "prior")
    if not last_rush and not prior_rush and not last_pass and not prior_pass:
        return []
    prior_labels = _prior_game_labels(recap)
    last_labels = _last_game_labels(last_game, recap)
    aliases = _team_aliases(last_game, recap)
    issues = []

    def _expected_rush(subject: str) -> tuple[str, int | None]:
        token = " ".join((subject or "").lower().split()).strip(" '")
        if token in prior_labels or any(
            re.search(rf"\b{re.escape(lab)}\b", token) for lab in prior_labels
        ):
            return "prior KC", prior_rush.get("KC")
        if token in last_labels or any(
            re.search(rf"\b{re.escape(lab)}\b", token) for lab in last_labels
        ):
            return "last KC", last_rush.get("KC")
        team = _alias_team(token, aliases)
        if team == "KC":
            return "KC", last_rush.get("KC")
        if team and team in last_rush:
            return team, last_rush.get(team)
        if team and team in prior_rush:
            return f"prior {team}", prior_rush.get(team)
        return token or "team", None

    def _expected_pass(subject: str) -> tuple[str, int | None]:
        token = " ".join((subject or "").lower().split()).strip(" '")
        team = _alias_team(token, aliases)
        if token in prior_labels:
            return "prior KC", prior_pass.get("KC")
        if token in last_labels:
            return "last KC", last_pass.get("KC")
        if team == "KC":
            return "KC", last_pass.get("KC")
        if team and team in last_pass:
            return team, last_pass.get(team)
        if team and team in prior_pass:
            return f"prior {team}", prior_pass.get(team)
        return token or "team", None

    for match in _TEAM_GROUND.finditer(text):
        try:
            yards = int(match.group(2))
        except (TypeError, ValueError):
            continue
        label, official = _expected_rush(match.group(1))
        if official is None:
            continue
        if yards != official:
            issues.append(
                f"team rushing {yards} disagrees with ESPN {official} for "
                f"{label} ({match.group(0)!r})"
            )

    for match in _TEAM_RUSH_YARDS.finditer(text):
        try:
            yards = int(match.group(1))
        except (TypeError, ValueError):
            continue
        subject = _subject_clause(text, match.start())
        label, official = _expected_rush(subject)
        if official is None:
            team = _bound_team(text, match.start(), match.end(), aliases, {})
            if team and team != "KC" and team in last_rush:
                official = last_rush[team]
                label = team
            elif team and team in prior_rush and team != "KC":
                official = prior_rush[team]
                label = f"prior {team}"
            else:
                official = last_rush.get("KC")
                label = "KC"
        if official is None:
            continue
        if yards == official:
            continue
        # A number that is the opponent's official total is bound to them.
        if yards in last_rush.values() or yards in prior_rush.values():
            continue
        issues.append(
            f"team rushing {yards} disagrees with ESPN {official} for "
            f"{label} ({match.group(0)!r})"
        )

    for match in _TEAM_PASS_YARDS.finditer(text):
        try:
            yards = int(match.group(1))
        except (TypeError, ValueError):
            continue
        # Player passing lines ("246 yards") sit next to a completion mark.
        window = text[max(0, match.start() - 40) : match.end()]
        if _PASS_LINE.search(window) or re.search(r"\d+\s*-\s*of\s*-\s*\d+", window, re.I):
            continue
        subject = _subject_clause(text, match.start())
        label, official = _expected_pass(subject)
        if official is None:
            team = _bound_team(text, match.start(), match.end(), aliases, {})
            if team and team in last_pass:
                official = last_pass[team]
                label = team
            else:
                official = last_pass.get("KC")
                label = "KC"
        if official is None or yards == official:
            continue
        if yards in last_pass.values() or yards in prior_pass.values():
            continue
        issues.append(
            f"team passing {yards} disagrees with ESPN {official} for "
            f"{label} ({match.group(0)!r})"
        )
    return issues


def _box_int(block: dict | None, key: str):
    raw = (block or {}).get(key) or ""
    match = re.search(r"\d+", str(raw))
    if not match:
        return None
    try:
        return int(match.group(0))
    except (TypeError, ValueError):
        return None


def _box_clocks(recap: dict | None) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {"KC": set(), "OPP": set(), "prior KC": set()}
    kc = (recap or {}).get("kc") or {}
    opp = (recap or {}).get("opp") or {}
    prior = ((recap or {}).get("prior") or {}).get("kc") or {}
    if kc.get("possessionTime"):
        out["KC"].add(str(kc["possessionTime"]).strip())
    if opp.get("possessionTime"):
        out["OPP"].add(str(opp["possessionTime"]).strip())
    if prior.get("possessionTime"):
        out["prior KC"].add(str(prior["possessionTime"]).strip())
    return out


def _check_box_clocks(
    text: str, recap: dict | None, last_game: dict | None
) -> list[str]:
    """Possession, first downs, and game-yard totals vs the right box."""
    if not text:
        return []
    issues = []
    clocks = _box_clocks(recap)
    allowed_clocks = set().union(*clocks.values()) if clocks else set()
    aliases = _team_aliases(last_game, recap)
    prior_labels = _prior_game_labels(recap)
    last_labels = _last_game_labels(last_game, recap)
    kc = (recap or {}).get("kc") or {}
    prior = ((recap or {}).get("prior") or {}).get("kc") or {}

    for match in _POSSESSION_CLOCK.finditer(text):
        clock = match.group(1)
        if not allowed_clocks:
            continue
        if clock not in allowed_clocks and clock.replace(":", ".") not in allowed_clocks:
            # Bare clock that is nobody's TOP — only flag when it claims possession.
            window = text[match.start() : min(len(text), match.end() + 24)]
            if re.search(r"possession|clock", window, re.I):
                issues.append(
                    f"possession {clock} is not on the ESPN box "
                    f"({match.group(0)!r}; official {sorted(allowed_clocks)})"
                )
            continue
        team = _bound_team(text, match.start(), match.end(), aliases, {})
        if team == "KC" and clock not in clocks["KC"] and clock in clocks["OPP"]:
            issues.append(
                f"possession {clock} is Miami's clock, not KC "
                f"({match.group(0)!r})"
            )

    kc_fd = _box_int(kc, "firstDowns")
    prior_fd = _box_int(prior, "firstDowns")
    for match in _FIRST_DOWNS.finditer(text):
        try:
            claimed = int(match.group(1))
        except (TypeError, ValueError):
            continue
        subject = _subject_clause(text, match.start()).lower()
        official = None
        label = "KC"
        if any(re.search(rf"\b{re.escape(lab)}\b", subject) for lab in prior_labels):
            official, label = prior_fd, "prior KC"
        elif any(re.search(rf"\b{re.escape(lab)}\b", subject) for lab in last_labels):
            official, label = kc_fd, "KC"
        else:
            official = kc_fd
        if official is None or claimed == official:
            continue
        if claimed in {kc_fd, prior_fd}:
            continue
        issues.append(
            f"first downs {claimed} disagrees with ESPN {official} for "
            f"{label} ({match.group(0)!r})"
        )

    kc_total = _box_int(kc, "totalYards")
    prior_total = _box_int(prior, "totalYards")
    prior_pass = _box_int(prior, "netPassingYards")
    kc_pass = _box_int(kc, "netPassingYards")
    for match in _GAME_YARDS.finditer(text):
        try:
            yards = int(match.group(1))
        except (TypeError, ValueError):
            continue
        sentence = _sentence_at(text, match.start()).lower()
        if "passing" in sentence or "rush" in sentence:
            continue
        if any(re.search(rf"\b{re.escape(lab)}\b", sentence) for lab in prior_labels):
            official = prior_total
            label = "prior KC"
            # 382 is Indy passing, not the game total.
            if official and yards != official and yards == prior_pass:
                issues.append(
                    f"game yards {yards} is prior passing, not the "
                    f"{official}-yard Indianapolis total ({match.group(0)!r})"
                )
            elif official and yards != official and yards not in {kc_total, kc_pass, prior_pass}:
                issues.append(
                    f"game yards {yards} disagrees with ESPN {official} for "
                    f"{label} ({match.group(0)!r})"
                )
    return issues


def check_review(
    narrative: dict | None,
    last_game: dict | None,
    recap: dict | None,
) -> list[str]:
    """Return human-readable violations, or an empty list when the edition is clean.

    Scans every generated section (review, story, currentState, gamePlan,
    xsandos, matchups, strategies, …). If the recap is empty, only
    final-score contexts are checked against ``lastGame`` scores.
    Completions (3-of-7), records (6-11, 3-0), and similar non-score
    numbers are ignored.
    """
    if not last_game or not phase_mod.is_final(last_game):
        return []
    text = edition_text(narrative)
    if not text.strip():
        text = review_text(narrative)
    if not text.strip():
        return []
    recap = recap or {}
    issues = _check_scores(text, last_game, recap)
    issues.extend(_check_part_of_day(text, last_game, recap))
    issues.extend(_check_echoed_instructions(text))
    issues.extend(_check_record_phrasing(text))
    if recap.get("scoringPlays") or recap.get("leaders") or recap.get("kc") or recap.get("plays"):
        issues.extend(_check_td_yards(text, recap, last_game))
        issues.extend(_check_fg_claims(text, recap, last_game))
        issues.extend(_check_stat_lines(text, recap, last_game))
        issues.extend(_check_play_sequence(text, recap))
        issues.extend(_check_turnover_credit(text, recap))
        issues.extend(_check_absolute_claims(text, recap))
        issues.extend(_check_team_yards(text, recap, last_game))
        issues.extend(_check_garbled_initials(text, recap))
        issues.extend(_check_scheme_claims(text, recap))
        issues.extend(_check_box_clocks(text, recap, last_game))
    # Dedup while keeping order.
    out = []
    seen = set()
    for item in issues:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _retry_stat_table(recap: dict | None) -> str:
    recap = recap or {}
    kc = recap.get("kc") or {}
    opp = recap.get("opp") or {}
    prior = recap.get("prior") or {}
    lines = [
        "PER-GAME STAT TABLE (do not blend last-game and prior-game numbers):",
        "  LAST vs "
        + (recap.get("oppAbbr") or "OPP")
        + f": KC rush={kc.get('rushingYards') or '—'} pass={kc.get('netPassingYards') or '—'} "
        f"total={kc.get('totalYards') or '—'} firstDowns={kc.get('firstDowns') or '—'} "
        f"thirdDown={kc.get('thirdDownEff') or '—'} poss={kc.get('possessionTime') or '—'}; "
        f"OPP rush={opp.get('rushingYards') or '—'} poss={opp.get('possessionTime') or '—'}.",
    ]
    if prior.get("kc"):
        pk = prior["kc"]
        lines.append(
            "  PRIOR vs "
            + (prior.get("oppAbbr") or "prior")
            + f": KC rush={pk.get('rushingYards') or '—'} pass={pk.get('netPassingYards') or '—'} "
            f"total={pk.get('totalYards') or '—'} firstDowns={pk.get('firstDowns') or '—'} "
            f"thirdDown={pk.get('thirdDownEff') or '—'} poss={pk.get('possessionTime') or '—'}."
        )
    return "\n".join(lines)


def retry_instruction(violations: list[str], recap: dict | None = None) -> str:
    bullets = "\n".join(f"- {v}" for v in violations)
    return (
        "FACT CHECK RETRY: the generated edition disagrees with the ESPN box "
        "and scoring plays we supplied. Each bullet is a specific rejection "
        "you must fix before rewriting — do not repeat the flagged wording:\n"
        f"{bullets}\n"
        f"{_retry_stat_table(recap)}\n"
        "Rewrite every section (lastGameReview, storyline, currentState, "
        "gamePlan, xsandos, matchups, strategies) so every final score, "
        "in-game score, player stat line, TD/FG yardage, and FG/TD count "
        "matches those ESPN facts by team. Credit the kicking team. Do not "
        "call a morning/midday/afternoon kickoff a night unless you are "
        "writing about a prior night game by name. Credit only the "
        "player ESPN lists as forcing or recovering a fumble. Team rushing "
        "is 88 in Miami, not 18. Use last names (Karlaftis, not George). "
        "Do not write only/first/never/lone play claims the play-by-play "
        "cannot support. A score 'after' or 'following' a play must bind "
        "to the play that clause actually names."
    )


def sentence_repair_instruction(violations: list[str]) -> str:
    bullets = "\n".join(f"- {v}" for v in violations)
    return (
        "SENTENCE REPAIR: do not rewrite the edition. Replace only the "
        "sentences that triggered these violations. Drop a sentence if you "
        "cannot make it agree with the ESPN scoring table.\n"
        f"{bullets}"
    )


def violation_snippets(violations: list[str]) -> list[str]:
    """Quoted match text from a violation, e.g. 'lost 18-19'."""
    out: list[str] = []
    seen: set[str] = set()
    for item in violations or []:
        for hit in re.findall(r"'([^']+)'", item):
            snippet = hit.strip()
            if snippet and snippet not in seen:
                seen.add(snippet)
                out.append(snippet)
    return out


def _split_sentences(text: str) -> list[str]:
    """Split on sentence punctuation, but keep initials like 'J. Rodriguez'."""
    chunks = []
    for block in re.split(r"\n+", (text or "").strip()):
        protected = re.sub(r"\b([A-Z])\.\s+", r"\1.<INI> ", block.strip())
        parts = re.split(r"(?<=[.!?])\s+", protected)
        chunks.extend(p.replace(".<INI> ", ". ") for p in parts if p.strip())
    return chunks


def analysis_sentences(narrative: dict | None) -> list[str]:
    review = (narrative or {}).get("lastGameReview") or {}
    out: list[str] = []
    for para in review.get("analysis") or []:
        if isinstance(para, dict):
            out.extend(_split_sentences(str(para.get("body") or "")))
        elif para:
            out.extend(_split_sentences(str(para)))
    return [s for s in out if s]


def _review_prose(narrative: dict | None) -> str:
    review = (narrative or {}).get("lastGameReview") or {}
    parts = [
        review.get("lede") or "",
    ]
    for para in review.get("analysis") or []:
        parts.append(para.get("body") if isinstance(para, dict) else str(para or ""))
    for key in ("whatWorked", "whatDidnt"):
        for item in review.get(key) or []:
            parts.append(str(item or ""))
    return "\n".join(p for p in parts if p)


def check_repair_orphans(narrative: dict | None, dropped: list[str]) -> list[str]:
    """Fail leftovers that lost their antecedent in a salvage drop."""
    if not dropped:
        return []
    issues = []
    for sentence in _split_sentences(_review_prose(narrative)):
        words = sentence.split()
        if not words:
            continue
        if _ORPHAN_OPENER.match(sentence):
            issues.append(f"orphan opener after repair ({sentence!r})")
        if re.match(r"^[A-Z][a-z]+(?:\s+at\b|\s+took\b).{0,48}$", sentence):
            issues.append(f"dangling name after repair ({sentence!r})")
        if len(words) <= 4 and not re.search(
            r"\b(?:is|was|were|are|won|lost|had|has|scored|made|hit)\b",
            sentence,
            re.I,
        ):
            issues.append(f"fragment after repair ({sentence!r})")
    return issues


def _check_garbled_initials(text: str, recap: dict | None) -> list[str]:
    """'L’Sneed' is a broken initial; 'L. Sneed' / 'L'Jarius Sneed' are fine."""
    if not text:
        return []
    last_names = set()
    for play in _plays(recap):
        for field in ("forcedBy", "recoveredBy", "interceptedBy", "target"):
            token = _last_name_token(play.get(field) or "")
            if token:
                last_names.add(token)
    if not last_names:
        return []
    issues = []
    for match in _GARBLED_INITIAL.finditer(text):
        last = match.group(2).lower()
        if last in last_names:
            issues.append(
                f"garbled player initial ({match.group(0)!r}; use "
                f"{match.group(1)}. {match.group(2)} or the full first name)"
            )
    return issues


def _check_scheme_claims(text: str, recap: dict | None) -> list[str]:
    """Unverifiable scheme color must be rewritten, not salvaged later."""
    if not text:
        return []
    issues = []
    rushes = [
        p
        for p in _plays(recap)
        if "end" in ((p.get("direction") or "") + " " + (p.get("text") or "")).lower()
        and "walker" in (p.get("text") or "").lower()
    ]
    if rushes and _BETWEEN_TACKLES.search(text):
        issues.append(
            "Walker also had end runs; do not write that the 70 were "
            "between the tackles"
        )
    int_texts = " ".join(
        (p.get("text") or "")
        for p in _plays(recap)
        if p.get("kind") == "int" or "intercept" in (p.get("text") or "").lower()
    ).lower()
    if _ZONE_BLITZ_INT.search(text) and "blitz" not in int_texts:
        issues.append(
            "play-by-play does not back a zone blitz producing the interception"
        )
    if _SNAP_LATER.search(text):
        issues.append(
            "snap-count later-claim cannot be verified against the play-by-play"
        )
    return issues


def _drop_text(text: str, snippets: list[str]) -> str:
    lowered = [s.lower() for s in snippets if s]
    if not text or not lowered or not any(s in text.lower() for s in lowered):
        return text
    sentences = _split_sentences(text)
    if len(sentences) <= 1:
        return ""
    kept = [
        s
        for s in sentences
        if not any(
            re.search(rf"(?<!\w){re.escape(snip)}(?!\w)", s, re.I) for snip in lowered
        )
    ]
    return " ".join(kept).strip()


def _drop_value(value, snippets: list[str]):
    if isinstance(value, str):
        return _drop_text(value, snippets)
    if isinstance(value, list):
        out = []
        for item in value:
            kept = _drop_value(item, snippets)
            if kept in (None, "", [], {}):
                continue
            out.append(kept)
        return out
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key in _PROTECTED_KEYS:
                out[key] = item
                continue
            kept = _drop_value(item, snippets)
            if kept not in (None, "", [], {}):
                out[key] = kept
            elif key in ("lede", "body", "title", "why", "coaching", "note"):
                out[key] = kept if isinstance(kept, str) else ""
        return out
    return value


def safe_score_lede(last_game: dict | None) -> str:
    last = last_game or {}
    opp = (last.get("opponent") or "the opponent").strip()
    pair = _int_pair(last.get("kcScore"), last.get("oppScore"))
    if not pair:
        return f"Kansas City played {opp}."
    return f"Kansas City finished {pair[0]}–{pair[1]} against {opp}."


def repair_offending_copy(
    narrative: dict | None,
    violations: list[str],
    last_game: dict | None = None,
) -> dict:
    """Drop or blank only the sentences that triggered the violations."""
    payload = copy.deepcopy(narrative or {})
    snippets = violation_snippets(violations)
    if not snippets:
        return payload
    for key in _EDITION_KEYS:
        if key in payload:
            payload[key] = _drop_value(payload[key], snippets)
    review = payload.get("lastGameReview")
    if isinstance(review, dict) and not (review.get("lede") or "").strip():
        review["lede"] = safe_score_lede(last_game)
        payload["lastGameReview"] = review
    return payload


def dropped_sentences(before, after) -> list[str]:
    """Sentences present in ``before`` that are gone after a repair."""
    old = _split_sentences(edition_text(before))
    new = {s.strip() for s in _split_sentences(edition_text(after))}
    out = []
    seen: set[str] = set()
    for sentence in old:
        key = sentence.strip()
        if not key or key in new or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out
