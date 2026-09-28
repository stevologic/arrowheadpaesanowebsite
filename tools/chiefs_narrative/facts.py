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


def _team_aliases(last_game: dict | None, recap: dict | None) -> dict[str, str]:
    aliases = {
        "kc": "KC",
        "chiefs": "KC",
        "kansas city": "KC",
    }
    opp = ((recap or {}).get("oppAbbr") or "").strip().upper()
    if opp:
        aliases[opp.lower()] = opp
    name = ((last_game or {}).get("opponent") or "").strip()
    tokens = [t for t in re.findall(r"[A-Za-z]+", name) if t.lower() not in ("the",)]
    for token in tokens:
        aliases[token.lower()] = opp or token[:3].upper()
    if len(tokens) >= 2:
        aliases[name.lower()] = opp or tokens[-1][:3].upper()
    return aliases


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


def _subject_clause(text: str, start: int) -> str:
    """Prose from the start of this clause up to the claim — the subject side."""
    clause_start = 0
    for hit in _CLAUSE_BREAK.finditer(text[:start]):
        clause_start = hit.end()
    return text[clause_start:start]


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


def official_yards_by_team(recap: dict | None, kind: str) -> dict[str, set[int]]:
    by_team: dict[str, set[int]] = {}
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
        team = _bound_team(text, match.start(), match.end(), aliases, players)
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
        team = _bound_team(text, match.start(), match.end(), aliases, players)
        owners = []
        for value in yards:
            hit = [abbr for abbr, bag in by_team.items() if value in bag]
            owners.append(set(hit))
        shared = owners[0]
        for bag in owners[1:]:
            shared &= bag
        if team:
            team_fg = by_team.get(team, set())
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
        team = _bound_team(text, match.start(), match.end(), aliases, players)
        all_fg = set().union(*by_team.values()) if by_team else set()
        if team:
            if yards not in by_team.get(team, set()):
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


def _check_part_of_day(text: str, last_game: dict | None) -> list[str]:
    part = collect.kickoff_part_of_day((last_game or {}).get("date") or "")
    if part not in _DAYTIME or not text:
        return []
    aliases = _team_aliases(last_game, None)
    issues = []
    for alias in sorted(aliases, key=len, reverse=True):
        if len(alias) < 3:
            continue
        rx = re.compile(rf"\b{re.escape(alias)}\s+night\b", re.IGNORECASE)
        match = rx.search(text)
        if match:
            issues.append(
                f"kickoff is {part}; do not call the game a night "
                f"({match.group(0)!r})"
            )
            break
    if _YARD_NIGHT.search(text):
        issues.append(
            f"kickoff is {part}; do not write a 'yard night' "
            f"({_YARD_NIGHT.search(text).group(0)!r})"
        )
    if _FINISHED_NIGHT.search(text):
        issues.append(
            f"kickoff is {part}; do not write 'finished the night'"
        )
    if _QB_NIGHT.search(text):
        issues.append(
            f"kickoff is {part}; do not write 'quarterback night'"
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
    issues.extend(_check_part_of_day(text, last_game))
    if recap.get("scoringPlays") or recap.get("leaders") or recap.get("kc"):
        issues.extend(_check_td_yards(text, recap, last_game))
        issues.extend(_check_fg_claims(text, recap, last_game))
        issues.extend(_check_stat_lines(text, recap, last_game))
    # Dedup while keeping order.
    out = []
    seen = set()
    for item in issues:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def retry_instruction(violations: list[str]) -> str:
    bullets = "\n".join(f"- {v}" for v in violations)
    return (
        "FACT CHECK RETRY: the generated edition disagrees with the ESPN box "
        "and scoring plays we supplied:\n"
        f"{bullets}\n"
        "Rewrite every section (lastGameReview, storyline, currentState, "
        "gamePlan, xsandos, matchups, strategies) so every final score, "
        "in-game score, player stat line, TD/FG yardage, and FG/TD count "
        "matches those ESPN facts by team. Credit the kicking team. Do not "
        "call a morning/midday/afternoon kickoff a night."
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
    parts = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    return [p for p in parts if p.strip()]


def _drop_text(text: str, snippets: list[str]) -> str:
    lowered = [s.lower() for s in snippets if s]
    if not text or not lowered or not any(s in text.lower() for s in lowered):
        return text
    sentences = _split_sentences(text)
    if len(sentences) <= 1:
        return ""
    kept = [s for s in sentences if not any(snip in s.lower() for snip in lowered)]
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
