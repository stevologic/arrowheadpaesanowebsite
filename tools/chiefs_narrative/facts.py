"""Deterministic ESPN fact-check for generated edition copy.

The writer is allowed to interpret the tape. It is not allowed to invent a
final score, a player stat line, a TD/FG yardage, or a team FG/TD count
that disagrees with the ESPN box / scoring plays we already handed it.
Checks are regex-vs-box so the same wrong sentence fails every time.
Every generated section is scanned — not just lastGameReview.
"""
from __future__ import annotations

import re

from . import collect, phase as phase_mod

# Completions and similar "X-of-Y" / "X/Y" lines must not be read as scores.
_OF_LINE = re.compile(r"\d+\s*[-–]?\s*of\s*[-–]?\s*\d+", re.IGNORECASE)
_SLASH_LINE = re.compile(r"\d+\s*/\s*\d+")

# Game-score contexts only. Bare "3-0" / "6-11" (records) stay out.
_SCORE_PATTERNS = (
    re.compile(r"\bKC\s+(\d{1,2})\s*[–-]\s*(\d{1,2})\b", re.IGNORECASE),
    re.compile(r"\b(\d{1,2})\s*[–-]\s*(\d{1,2})\s+game\b", re.IGNORECASE),
    re.compile(
        r"\b(?:final(?:\s+score)?|won|lost)\s+(?:it\s+)?(?:KC\s+)?"
        r"(\d{1,2})\s*[–-]\s*(\d{1,2})\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:up|ahead|leading|lead)\s+(\d{1,2})\s*[–-]\s*(\d{1,2})\b",
        re.IGNORECASE,
    ),
)

# "from the 12" / "12-yard TD" / "12-yard catch" — scoring-play yardage.
_TD_YARDS = re.compile(
    r"(?:from the (\d+)\b|(\d+)[-\s]yard(?:s)?\s+"
    r"(?:TD|touchdown|score|catch|run)\b)",
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
    r"\b(one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+"
    r"([A-Za-z][A-Za-z ]{1,24}?)?\s*field goals?\b",
    re.IGNORECASE,
)
_TD_COUNT = re.compile(
    r"\b(one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+"
    r"([A-Za-z][A-Za-z ]{1,24}?)?\s*touchdowns?\b",
    re.IGNORECASE,
)
_YARD_NIGHT = re.compile(
    r"\d+(?:\.\d+)?[-\s]yard(?:s)?\s+night\b",
    re.IGNORECASE,
)
_FINISHED_NIGHT = re.compile(r"\bfinished the night\b", re.IGNORECASE)
_QB_NIGHT = re.compile(r"\bquarterback night\b", re.IGNORECASE)


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


def _bound_team(
    text: str,
    start: int,
    end: int,
    aliases: dict[str, str],
    player_teams: dict[str, str],
) -> str:
    window_start = max(0, start - 96)
    window = text[window_start : min(len(text), end + 40)]
    best = ""
    best_dist = None
    for name in sorted(player_teams, key=len, reverse=True):
        for hit in re.finditer(rf"\b{re.escape(name)}\b", window, re.IGNORECASE):
            abs_pos = window_start + hit.start()
            dist = min(abs(start - abs_pos), abs(end - (window_start + hit.end())))
            if best_dist is None or dist < best_dist:
                best_dist = dist
                best = player_teams[name]
    if best:
        return best
    for alias in sorted(aliases, key=len, reverse=True):
        if len(alias) < 3:
            continue
        if re.search(rf"\b{re.escape(alias)}\b", window, re.IGNORECASE):
            return aliases[alias]
    return ""


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


def official_count_by_team(recap: dict | None, kind: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for play in (recap or {}).get("scoringPlays") or []:
        if not isinstance(play, dict):
            continue
        if _play_kind(play) != kind:
            continue
        team = _play_team(play) or "?"
        counts[team] = counts.get(team, 0) + 1
    return counts


def _mask_stat_lookalikes(text: str) -> str:
    masked = _OF_LINE.sub("X-of-Y", text)
    return _SLASH_LINE.sub("X/Y", masked)


def _int_pair(a, b):
    try:
        return int(a), int(b)
    except (TypeError, ValueError):
        return None


def allowed_score_pairs(last_game: dict | None, recap: dict | None) -> set[tuple[int, int]]:
    """Official final plus every ESPN score-after. Both orders are allowed."""
    pairs: set[tuple[int, int]] = set()
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
    for rx in _SCORE_PATTERNS:
        for match in rx.finditer(masked):
            pair = _int_pair(match.group(1), match.group(2))
            if not pair or pair in seen:
                continue
            seen.add(pair)
            if pair not in pairs:
                issues.append(
                    f"score {pair[0]}-{pair[1]} is not the official final "
                    f"or an ESPN score-after ({match.group(0)!r})"
                )
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
    counts = official_count_by_team(recap, "fg")
    td_counts = official_count_by_team(recap, "td")
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
        claimed = _parse_count_word(match.group(1))
        team = _alias_team(match.group(2) or "", aliases)
        if claimed is None or not team:
            continue
        official = counts.get(team, 0)
        if claimed != official:
            issues.append(
                f"{team} field-goal count {claimed} disagrees with ESPN "
                f"{official} ({match.group(0)!r})"
            )

    for match in _TD_COUNT.finditer(text):
        claimed = _parse_count_word(match.group(1))
        team = _alias_team(match.group(2) or "", aliases)
        if claimed is None or not team:
            continue
        official = td_counts.get(team, 0)
        if claimed != official:
            issues.append(
                f"{team} touchdown count {claimed} disagrees with ESPN "
                f"{official} ({match.group(0)!r})"
            )
    return issues


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
