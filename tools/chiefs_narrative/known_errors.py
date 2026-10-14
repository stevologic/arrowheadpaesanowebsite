"""Persistent known-errors / pinned-facts for every generate run.

Earlier edition salvage used to die with the repair file. The next writer
then reprinted Quinton Walker, a 85.8, Chiefs.com-as-namer, venue INT
credit, and false wipe claims. This module is the carry-forward list:
check_review flags them, salvage rewrites them, and a new edition cannot
ship a pin we already recorded.
"""
from __future__ import annotations

import copy
import json
import re
from functools import lru_cache
from pathlib import Path

KNOWN_ERRORS_JSON = Path(__file__).with_name("known_errors.json")

_VENUE_CREDIT = frozenset(
    {
        "allegiant",
        "arrowhead",
        "geha",
        "sofi",
        "lambeau",
        "empower",
        "highmark",
        "hard",
        "rock",
        "stadium",
        "field",
        "coliseum",
        "dome",
    }
)
_INT_CREDIT = re.compile(
    r"\b([A-Z][A-Za-z''-]+)\s+(?i:intercept(?:ed|ion)|INT)\b"
    r"|\b(?i:intercept(?:ed|ion)|INT)\s+by\s+([A-Z][A-Za-z''-]+)"
)
_A_NUMBER = re.compile(r"\b([Aa]n?)\s+(\d+(?:\.\d+)?)")
_WIPED_BY_NAME = re.compile(
    r"\b([A-Z][A-Za-z''.-]+(?:\s+[A-Z][A-Za-z''.-]+)?)['’]s\s+"
    r"([^.]{0,64}?)(?P<verb>wiped|nullified)\b",
    re.IGNORECASE,
)
_WIPED_A_PLAY = re.compile(
    r"\b(?P<verb>wiped|nullified)\s+a\s+play\b",
    re.IGNORECASE,
)
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


@lru_cache(maxsize=1)
def load_known_errors() -> dict:
    payload = json.loads(KNOWN_ERRORS_JSON.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {"replacements": [], "patterns": [], "rules": []}
    return payload


def rule_issue(rule_id: str, fallback: str) -> str:
    for row in load_known_errors().get("rules") or []:
        if isinstance(row, dict) and row.get("id") == rule_id:
            return str(row.get("issue") or fallback)
    return fallback


def number_needs_an(token: str) -> bool:
    """True when the spoken English onset of this numeral is a vowel.

    8… is "eight", 11 is "eleven", 18 is "eighteen". Bare 0 is skipped
    by the checker — sports copy uses both "a 0-2" (zero) and "an 0-2"
    (oh) — so this returns False for 0.
    """
    raw = (token or "").strip()
    if not raw or not raw[0].isdigit():
        return False
    if raw.startswith("8"):
        return True
    whole = raw.split(".", 1)[0]
    return whole in {"11", "18"}


def _skip_a_an(number: str) -> bool:
    """0 / 0-2 records are spoken as both 'oh' and 'zero'."""
    raw = (number or "").strip()
    return bool(raw) and raw.startswith("0") and "." not in raw


def _last_name(raw: str) -> str:
    parts = [p for p in re.split(r"[^A-Za-z]+", raw or "") if p]
    return parts[-1].lower() if parts else ""


def _walk_strings(value, fn):
    if isinstance(value, str):
        return fn(value)
    if isinstance(value, list):
        return [_walk_strings(item, fn) for item in value]
    if isinstance(value, dict):
        return {key: _walk_strings(item, fn) for key, item in value.items()}
    return value


def _payload_text(narrative: dict | None) -> str:
    chunks: list[str] = []

    def _keep(value: str) -> str:
        if value:
            chunks.append(value)
        return value

    payload = narrative or {}
    for key in _EDITION_KEYS:
        if key in payload:
            _walk_strings(payload[key], _keep)
    return "\n".join(chunks)


def _sentence_at(text: str, index: int) -> str:
    start = index
    while start > 0 and text[start - 1] not in ".!?\n":
        start -= 1
    end = index
    while end < len(text) and text[end] not in ".!?\n":
        end += 1
    return text[start:end].strip()


def _potw_replace(text: str, pattern: re.Pattern, template: str) -> str:
    def _repl(match: re.Match) -> str:
        out = match.expand(template)
        if match.start() == 0 or text[match.start() - 1] in ".!?\n":
            return out[:1].upper() + out[1:]
        return out

    return pattern.sub(_repl, text)


def _fix_a_an(text: str) -> str:
    def _repl(match: re.Match) -> str:
        article, number = match.group(1), match.group(2)
        if _skip_a_an(number):
            return match.group(0)
        want_an = number_needs_an(number)
        lower = article.lower()
        if want_an and lower == "a":
            return ("An" if article[0].isupper() else "an") + " " + number
        if not want_an and lower == "an":
            return ("A" if article[0].isupper() else "a") + " " + number
        return match.group(0)

    return _A_NUMBER.sub(_repl, text)


def _penalties(recap: dict | None) -> list[dict]:
    return [
        row
        for row in (recap or {}).get("penalties") or []
        if isinstance(row, dict)
    ]


def _penalty_wiped(row: dict) -> bool:
    return bool(str(row.get("wiped") or "").strip())


def check_text(text: str, recap: dict | None = None) -> list[str]:
    """Flag pinned-fact violations in edition prose."""
    issues: list[str] = []
    if not text:
        return issues
    catalog = load_known_errors()
    for row in catalog.get("replacements") or []:
        if not isinstance(row, dict):
            continue
        find = str(row.get("find") or "")
        if find and re.search(re.escape(find), text, re.I):
            issues.append(str(row.get("issue") or f"pinned fact: {find}"))
    for row in catalog.get("patterns") or []:
        if not isinstance(row, dict):
            continue
        raw = str(row.get("match") or "")
        if not raw:
            continue
        hit = re.search(raw, text, re.I)
        if hit:
            issues.append(
                str(row.get("issue") or "pinned fact: pattern")
                + f" ({hit.group(0)!r})"
            )
    for match in _A_NUMBER.finditer(text):
        article, number = match.group(1), match.group(2)
        if _skip_a_an(number):
            continue
        want_an = number_needs_an(number)
        if want_an and article.lower() == "a":
            issues.append(
                rule_issue("a-an-number", "pinned fact: use an before a vowel-sound number")
                + f" ('{article} {number}')"
            )
        if not want_an and article.lower() == "an":
            issues.append(
                rule_issue("a-an-number", "pinned fact: use an before a vowel-sound number")
                + f" ('{article} {number}')"
            )
    for match in _INT_CREDIT.finditer(text):
        name = match.group(1) or match.group(2) or ""
        if _last_name(name) in _VENUE_CREDIT:
            issues.append(
                rule_issue(
                    "venue-int-credit",
                    "pinned fact: never credit a venue with an interception",
                )
                + f" ({match.group(0)!r})"
            )
    wipe_issue = rule_issue(
        "penalty-wipe",
        "pinned fact: an enforced penalty that did not wipe a play must not be described as wiping one",
    )
    for match in _WIPED_BY_NAME.finditer(text):
        who = _last_name(match.group(1))
        clause = (match.group(2) or "").lower()
        rows = [
            row
            for row in _penalties(recap)
            if _last_name(row.get("player") or "") == who
        ]
        if not rows:
            continue
        type_hit = [
            row
            for row in rows
            if any(
                token and token in (row.get("type") or "").lower()
                for token in re.findall(r"[a-z]{4,}", clause)
            )
        ]
        bound = type_hit or rows
        if bound and not any(_penalty_wiped(row) for row in bound):
            issues.append(wipe_issue + f" ({_sentence_at(text, match.start())!r})")
    if recap and _WIPED_A_PLAY.search(text):
        for match in _WIPED_A_PLAY.finditer(text):
            sentence = _sentence_at(text, match.start())
            named = [
                row
                for row in _penalties(recap)
                if _last_name(row.get("player") or "")
                and re.search(
                    rf"\b{re.escape(_last_name(row.get('player') or ''))}\b",
                    sentence,
                    re.I,
                )
            ]
            if named and not any(_penalty_wiped(row) for row in named):
                item = wipe_issue + f" ({sentence!r})"
                if item not in issues:
                    issues.append(item)
    # Dedup
    out = []
    seen = set()
    for item in issues:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def check_payload(narrative: dict | None, recap: dict | None = None) -> list[str]:
    return check_text(_payload_text(narrative), recap)


def apply_known_errors(
    narrative: dict | None, recap: dict | None = None
) -> tuple[dict, list[str]]:
    """Rewrite pinned-fact errors in place and log each swap."""
    payload = copy.deepcopy(narrative or {})
    logs: list[str] = []
    catalog = load_known_errors()

    def _rewrite(text: str) -> str:
        if not text:
            return text
        original = text
        for row in catalog.get("replacements") or []:
            if not isinstance(row, dict):
                continue
            find = str(row.get("find") or "")
            repl = str(row.get("replace") or "")
            if not find:
                continue
            text = re.sub(re.escape(find), repl, text, flags=re.I)
        for row in catalog.get("patterns") or []:
            if not isinstance(row, dict):
                continue
            raw = str(row.get("match") or "")
            template = str(row.get("replace") or "")
            if not raw:
                continue
            text = _potw_replace(text, re.compile(raw, re.I), template)
        text = _fix_a_an(text)
        if recap:
            def _wipe_reword(match: re.Match) -> str:
                who = _last_name(match.group(1))
                rows = [
                    row
                    for row in _penalties(recap)
                    if _last_name(row.get("player") or "") == who
                ]
                if rows and not any(_penalty_wiped(row) for row in rows):
                    return match.group(0).replace(match.group("verb"), "drew a flag on")
                return match.group(0)

            text = _WIPED_BY_NAME.sub(_wipe_reword, text)

            def _wipe_play(match: re.Match) -> str:
                sentence = _sentence_at(text, match.start())
                named = [
                    row
                    for row in _penalties(recap)
                    if _last_name(row.get("player") or "")
                    and re.search(
                        rf"\b{re.escape(_last_name(row.get('player') or ''))}\b",
                        sentence,
                        re.I,
                    )
                ]
                if named and not any(_penalty_wiped(row) for row in named):
                    return match.group(0).replace(match.group("verb"), "drew a flag on")
                return match.group(0)

            text = _WIPED_A_PLAY.sub(_wipe_play, text)
        if text != original:
            logs.append(f"{original} → {text}")
        return text

    for key in _EDITION_KEYS:
        if key in payload:
            payload[key] = _walk_strings(payload[key], _rewrite)
    return payload, logs
