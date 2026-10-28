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
# Home-stadium metonym. "Arrowhead interception" is a takeaway at home,
# not a venue credited as the interceptor (the Allegiant bug).
_HOME_METONYM = frozenset({"arrowhead"})
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
_WIPE_CLAIM = re.compile(
    r"\b(?P<verb>wiped|nullified)\s+(?:a\s+play|the\s+play|plays)\b",
    re.IGNORECASE,
)
_LIST_WIPED_PLAYS = re.compile(
    r"\b(?:Penalties|Flags)\s+on\b"
    r"(?P<names>(?:[A-Z]\.|[^.!?]){0,240}?)"
    r"\b(?:that\s+)?(?:wiped(?:\s+out)?|erased)\s+plays\b",
    re.IGNORECASE,
)
_LIST_PLAYER = re.compile(
    r"(?:[A-Z]\.\s*)?[A-Z][A-Za-z''-]+(?:\s+[A-Z][A-Za-z''-]+)*"
)
_WS_IN_PATTERN = re.compile(r"(?:\\[sS][+*?]|\s)+")
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


def flex_whitespace(pattern: str) -> str:
    """Treat any whitespace run in a pattern (spaces, NBSP, newlines) as \\s+."""
    return _WS_IN_PATTERN.sub(r"\\s+", pattern or "")


def flex_literal(find: str) -> str:
    """Escape a literal pin so 'Quinton  Walker' / NBSP still match."""
    parts = re.split(r"\s+", (find or "").strip())
    return r"\s+".join(re.escape(part) for part in parts if part)


def is_hold_only_issue(item: str) -> bool:
    raw = item or ""
    return (
        raw == "replay not run"
        or "must hold, not be rewritten" in raw
        or "no ESPN penalty data" in raw
        or "no ESPN play-by-play" in raw
    )


def list_wipe_holds(text: str) -> bool:
    return bool(text and _LIST_WIPED_PLAYS.search(text))


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


def _list_named_lasts(chunk: str) -> list[str]:
    seen: list[str] = []
    for match in _LIST_PLAYER.finditer(chunk or ""):
        last = _last_name(match.group(0))
        if last and last not in seen:
            seen.append(last)
    return seen


def _enforced_not_wipe_lasts(names: list[str], recap: dict | None) -> list[str] | None:
    """Last names whose ESPN penalty was enforced but did not wipe.

    None means there is no penalty table to check.
    """
    rows = _penalties(recap)
    if not rows:
        return None
    bad: list[str] = []
    for who in names:
        hits = [
            row
            for row in rows
            if _last_name(row.get("player") or "") == who
        ]
        if hits and any(not _penalty_wiped(row) for row in hits):
            bad.append(who)
    return bad


def _list_wipe_issues(text: str, recap: dict | None) -> list[str]:
    """List-form wipe claims: flag only a named enforced-not-wipe; hold if no PBP."""
    issues: list[str] = []
    hold = rule_issue(
        "penalty-wipe-list",
        "pinned fact: list-form wipe claim has no ESPN play-by-play; must hold, not be rewritten",
    )
    wipe = rule_issue(
        "penalty-wipe",
        "pinned fact: an enforced penalty that did not wipe a play must not be described as wiping one",
    )
    for match in _LIST_WIPED_PLAYS.finditer(text):
        sentence = _sentence_at(text, match.start())
        names = _list_named_lasts(match.group("names") or "")
        bad = _enforced_not_wipe_lasts(names, recap)
        if bad is None or not names:
            issues.append(hold + f" ({match.group(0)!r})")
            continue
        if bad:
            issues.append(
                wipe + f" ({', '.join(name.title() for name in bad)}: {sentence!r})"
            )
    return issues


def check_text(text: str, recap: dict | None = None) -> list[str]:
    """Flag pinned-fact violations in edition prose."""
    issues: list[str] = []
    if not text:
        return issues
    catalog = load_known_errors()
    for row in catalog.get("replacements") or []:
        if not isinstance(row, dict):
            continue
        find = flex_literal(str(row.get("find") or ""))
        if find and re.search(find, text, re.I):
            issues.append(str(row.get("issue") or f"pinned fact: {row.get('find')}"))
    for row in catalog.get("patterns") or []:
        if not isinstance(row, dict):
            continue
        raw = flex_whitespace(str(row.get("match") or ""))
        if not raw:
            continue
        hit = re.search(raw, text, re.I)
        if hit:
            issues.append(
                str(row.get("issue") or "pinned fact: pattern")
                + f" ({hit.group(0)!r})"
            )
    issues.extend(_list_wipe_issues(text, recap))
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
        last = _last_name(name)
        if last in _HOME_METONYM:
            continue
        if last in _VENUE_CREDIT:
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
    if recap and _WIPE_CLAIM.search(text):
        for match in _WIPE_CLAIM.finditer(text):
            sentence = _sentence_at(text, match.start())
            if list_wipe_holds(sentence):
                continue
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


def _rewrite_list_form_wipes(text: str, recap: dict | None) -> str:
    """Turn a mixed list-wipe claim into 'were enforced' when ESPN has PBP.

    Remigio's fair-catch flag was enforced and did not wipe; Williams,
    Woods, Anudike-Uzomah, and Sneed did wipe. Dropping the whole list
    throws away true flags. 'were enforced' is true for every name.
    No play-by-play still holds.
    """
    if not text or not recap:
        return text

    def _repl(match: re.Match) -> str:
        names = _list_named_lasts(match.group("names") or "")
        bad = _enforced_not_wipe_lasts(names, recap)
        if not bad:
            return match.group(0)
        return re.sub(
            r"\b(?:that\s+)?(?:wiped(?:\s+out)?|erased)\s+plays\b",
            "that were enforced",
            match.group(0),
            count=1,
            flags=re.I,
        )

    return _LIST_WIPED_PLAYS.sub(_repl, text)


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
            find = flex_literal(str(row.get("find") or ""))
            repl = str(row.get("replace") or "")
            if not find:
                continue
            text = re.sub(find, repl, text, flags=re.I)
        for row in catalog.get("patterns") or []:
            if not isinstance(row, dict):
                continue
            raw = flex_whitespace(str(row.get("match") or ""))
            template = str(row.get("replace") or "")
            if not raw:
                continue
            text = _potw_replace(text, re.compile(raw, re.I), template)
        text = _fix_a_an(text)
        text = _rewrite_list_form_wipes(text, recap)
        if recap and not list_wipe_holds(text):
            def _wipe_claim(match: re.Match) -> str:
                sentence = _sentence_at(text, match.start())
                if list_wipe_holds(sentence):
                    return match.group(0)
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
                possessive = _WIPED_BY_NAME.search(sentence)
                if possessive:
                    who = _last_name(possessive.group(1))
                    named = named or [
                        row
                        for row in _penalties(recap)
                        if _last_name(row.get("player") or "") == who
                    ]
                if named and not all(_penalty_wiped(row) for row in named):
                    return (
                        "were enforced"
                        if match.group(0).lower().endswith("plays")
                        else "was enforced"
                    )
                return match.group(0)

            text = _WIPE_CLAIM.sub(_wipe_claim, text)
        if text != original:
            logs.append(f"{original} → {text}")
        return text

    for key in _EDITION_KEYS:
        if key in payload:
            payload[key] = _walk_strings(payload[key], _rewrite)
    return payload, logs
