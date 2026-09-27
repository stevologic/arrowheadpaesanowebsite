"""Deterministic ESPN fact-check for last-game review copy.

The writer is allowed to interpret the tape. It is not allowed to invent a
final score, a player stat line, or a TD yardage that disagrees with the
ESPN box / scoring plays we already handed it. Checks are regex-vs-box so
the same wrong sentence fails every time.
"""
from __future__ import annotations

import re

from . import phase as phase_mod

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

_PASS_LINE = re.compile(
    r"(\d+)\s*(?:/|-of-|–of–|-)\s*(\d+)(?:\s*,\s*(\d+)\s*(?:YDS|yards))?",
    re.IGNORECASE,
)

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


def _check_td_yards(text: str, recap: dict) -> list[str]:
    allowed = official_score_yards(recap)
    if not allowed:
        return []
    issues = []
    seen: set[int] = set()
    for match in _TD_YARDS.finditer(text):
        raw = match.group(1) or match.group(2)
        try:
            yards = int(raw)
        except (TypeError, ValueError):
            continue
        if yards in seen:
            continue
        seen.add(yards)
        if yards not in allowed:
            issues.append(
                f"scoring yardage {yards} is not on the ESPN scoring-play list "
                f"({match.group(0)!r}; official yards {sorted(allowed)})"
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


def _check_stat_lines(text: str, recap: dict) -> list[str]:
    issues = []
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
        window_rx = re.compile(
            re.escape(last) + r".{0,96}",
            re.IGNORECASE | re.DOTALL,
        )
        for window in window_rx.finditer(text):
            claimed = _parse_pass_line(window.group(0))
            if not claimed:
                continue
            if (claimed[0], claimed[1]) != (official[0], official[1]):
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
    """Return human-readable violations, or an empty list when the review is clean.

    If the recap is empty, only final-score contexts are checked against
    ``lastGame`` scores. Completions (3-of-7), records (6-11, 3-0), and
    similar non-score numbers are ignored.
    """
    if not last_game or not phase_mod.is_final(last_game):
        return []
    review = (narrative or {}).get("lastGameReview") or {}
    if not isinstance(review, dict) or not review:
        return []
    text = review_text(narrative)
    if not text.strip():
        return []
    recap = recap or {}
    issues = _check_scores(text, last_game, recap)
    if recap.get("scoringPlays") or recap.get("leaders") or recap.get("kc"):
        issues.extend(_check_td_yards(text, recap))
        issues.extend(_check_stat_lines(text, recap))
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
        "FACT CHECK RETRY: the last-game review disagrees with the ESPN box "
        "and scoring plays we supplied:\n"
        f"{bullets}\n"
        "Rewrite lastGameReview so every final score, in-game score, player "
        "stat line, and TD yardage matches those ESPN facts exactly. Do not "
        "invent a different score, a different scorer, or a different yardage."
    )
