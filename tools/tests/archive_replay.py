"""Offline archive replay: each edition against its own cached ESPN box."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from tools.chiefs_narrative import collect, facts

FIXTURES = Path(__file__).resolve().parent / "fixtures"
EDITIONS = Path("data") / "narrative_editions"

_EVENT_BY_OPP = {
    "tampa bay buccaneers": "401873296",
    "seattle seahawks": "401873305",
    "denver broncos": "401872931",
    "indianapolis colts": "401872945",
    "miami dolphins": "401872952",
    "las vegas raiders": "401872976",
}
_PRIOR = {
    "401872976": "401872952",
    "401872952": "401872945",
    "401872945": "401872931",
    "401872931": "401873305",
    "401873305": "401873296",
}


def recap_path(event_id: str) -> Path:
    return FIXTURES / f"espn_{event_id}_recap.json"


def pinned_edition_names() -> set[str]:
    """Frozen replay net. Today's generate overlay is not in this list."""
    return set(
        json.loads((FIXTURES / "archive_replay_editions.json").read_text(encoding="utf-8"))
    )


def load_recap(event_id: str) -> dict:
    path = recap_path(event_id)
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def recaps_by_event() -> dict[str, dict]:
    out = {}
    for event_id in set(_EVENT_BY_OPP.values()) | set(_PRIOR.values()):
        payload = load_recap(event_id)
        if not payload:
            continue
        prior_id = _PRIOR.get(event_id)
        if prior_id:
            prior = load_recap(prior_id)
            if prior:
                payload = dict(payload)
                payload["prior"] = dict(prior)
                payload["prior"].setdefault("oppAbbr", prior.get("oppAbbr"))
        out[event_id] = payload
    return out


def last_game_for_edition(edition: dict, schedule: list) -> dict | None:
    review = edition.get("lastGameReview") or {}
    opp = (review.get("opponent") or "").strip().lower()
    if not opp:
        return None
    event_id = _EVENT_BY_OPP.get(opp)
    for game in schedule or []:
        if str(game.get("id")) == event_id:
            return dict(game)
        if (game.get("opponent") or "").strip().lower() == opp and game.get(
            "completed"
        ):
            event_id = str(game.get("id") or event_id or "")
            row = dict(game)
            if event_id:
                row.setdefault("id", event_id)
            return row
    if event_id:
        return {
            "id": event_id,
            "completed": True,
            "opponent": review.get("opponent"),
            "kcScore": 1,
            "oppScore": 0,
        }
    return None


def replay_edition(edition: dict, schedule: list, recaps: dict) -> list[str]:
    last = last_game_for_edition(edition, schedule)
    recap = recaps.get(str((last or {}).get("id") or "")) if last else None
    return facts.check_review(
        edition, last, recap or {}, schedule=schedule
    )


def replay_all_editions() -> list[tuple[str, list[str]]]:
    schedule = collect.load_cached_schedule()
    recaps = recaps_by_event()
    pinned = pinned_edition_names()
    rows = []
    for path in sorted(EDITIONS.glob("*.json")):
        if path.name not in pinned:
            continue
        edition = json.loads(path.read_text(encoding="utf-8"))
        rows.append((path.name, replay_edition(edition, schedule, recaps)))
    return rows


def salvage_edition(edition: dict, schedule: list, recaps: dict) -> dict:
    last = last_game_for_edition(edition, schedule)
    recap = recaps.get(str((last or {}).get("id") or "")) if last else None
    recap = recap or {}
    issues = facts.check_review(edition, last, recap, schedule=schedule)
    fixed, logs = facts.apply_fact_corrections(
        copy.deepcopy(edition), issues, recap, last, schedule
    )
    leftover = facts.check_review(fixed, last, recap, schedule=schedule)
    repaired = (
        facts.repair_offending_copy(fixed, leftover, last, recap)
        if leftover
        else fixed
    )
    before = facts.edition_text(edition)
    after = facts.edition_text(repaired)
    return {
        "issues": issues,
        "logs": logs,
        "leftover": leftover,
        "drops": facts.dropped_sentences(edition, repaired),
        "changed": [log for log in logs if " → " in log],
        "before": before,
        "after": after,
        "before_sents": facts._split_sentences(before),
        "after_sents": facts._split_sentences(after),
    }


def salvage_all_editions() -> list[tuple[str, dict]]:
    schedule = collect.load_cached_schedule()
    recaps = recaps_by_event()
    pinned = pinned_edition_names()
    rows = []
    for path in sorted(EDITIONS.glob("*.json")):
        if path.name not in pinned:
            continue
        edition = json.loads(path.read_text(encoding="utf-8"))
        rows.append((path.name, salvage_edition(edition, schedule, recaps)))
    return rows


def salvage_edition_file(name: str) -> dict:
    """Replay one on-disk edition, including unpinned published Reviews."""
    schedule = collect.load_cached_schedule()
    recaps = recaps_by_event()
    edition = json.loads((EDITIONS / name).read_text(encoding="utf-8"))
    return salvage_edition(edition, schedule, recaps)


def quoted_snippets(issues: list[str]) -> list[str]:
    out = []
    for item in issues or []:
        hit = facts._quoted_violation(item)
        if hit:
            out.append(hit)
    return out
