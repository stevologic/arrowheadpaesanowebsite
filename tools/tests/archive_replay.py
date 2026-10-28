"""Offline archive replay: each edition against its own cached ESPN box."""
from __future__ import annotations

import copy
import json
import re
import subprocess
from pathlib import Path

from tools.chiefs_narrative import facts
from tools.tests.frozen_slate import frozen_schedule

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
# Regular-season rematches that share an opponent with an earlier fixture.
_EXTRA_EVENTS_BY_OPP = {
    "seattle seahawks": ("401873022",),
}
_PRIOR = {
    "401873022": "401873006",
    "401872976": "401872952",
    "401872952": "401872945",
    "401872945": "401872931",
    "401872931": "401873305",
    "401873305": "401873296",
}
# Only the Chargers prior needs a full name. Abbr-only LAC/la does not
# match "Chargers" in a 22-6 sentence, and labeling every prior city
# rebinds older Indianapolis first-down copy onto the prior KC box.
_FIXTURE_OPPONENT = {
    "401873006": ("Los Angeles Chargers", "LAC"),
}
_SCORE = re.compile(r"(\d+)\s*[–-]\s*(\d+)")


def recap_path(event_id: str) -> Path:
    return FIXTURES / f"espn_{event_id}_recap.json"


def pinned_edition_names() -> set[str]:
    """Known-bad allowlist. Unpinned committed editions still get strict replay."""
    return set(
        json.loads((FIXTURES / "archive_replay_editions.json").read_text(encoding="utf-8"))
    )


def committed_edition_names() -> set[str]:
    """Edition files tracked by git. Uncommitted generate overlays are omitted."""
    root = Path(__file__).resolve().parents[2]
    try:
        raw = subprocess.check_output(
            ["git", "ls-files", "-z", "--", "data/narrative_editions"],
            cwd=root,
        )
    except (OSError, subprocess.CalledProcessError):
        return {path.name for path in EDITIONS.glob("*.json")}
    return {Path(item.decode()).name for item in raw.split(b"\0") if item}


def load_recap(event_id: str) -> dict:
    path = recap_path(event_id)
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _label_recap(payload: dict, event_id: str) -> dict:
    """Put the ESPN opponent name on a box so prior-game copy can bind.

    Frozen slate rows for later weeks still have null scores. Abbr-only
    labels (LAC → lac/la) do not match 'Chargers' in a 22-6 sentence.
    """
    attached = dict(payload)
    name, abbr = _FIXTURE_OPPONENT.get(str(event_id), ("", ""))
    if name:
        attached["opponent"] = name
    if abbr:
        attached["oppAbbr"] = abbr
    return attached


def _all_event_ids() -> set[str]:
    extra = {event for events in _EXTRA_EVENTS_BY_OPP.values() for event in events}
    return set(_EVENT_BY_OPP.values()) | set(_PRIOR) | set(_PRIOR.values()) | extra


def recaps_by_event() -> dict[str, dict]:
    out = {}
    for event_id in _all_event_ids():
        payload = load_recap(event_id)
        if not payload:
            continue
        prior_id = _PRIOR.get(event_id)
        if prior_id:
            prior = load_recap(prior_id)
            if prior:
                payload = dict(payload)
                payload["prior"] = _label_recap(prior, prior_id)
                payload["prior"].setdefault("oppAbbr", prior.get("oppAbbr"))
        out[event_id] = payload
    return out


def _edition_score(review: dict) -> tuple[int, int] | None:
    match = _SCORE.search(str((review or {}).get("score") or ""))
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _recap_final_score(recap: dict | None) -> tuple[int, int] | None:
    plays = (recap or {}).get("scoringPlays") or []
    if not plays:
        return None
    last = plays[-1]
    try:
        return int(last["kcScore"]), int(last["oppScore"])
    except (KeyError, TypeError, ValueError):
        return None


def _events_for_opponent(opp: str) -> list[str]:
    mapped = _EVENT_BY_OPP.get(opp)
    extra = list(_EXTRA_EVENTS_BY_OPP.get(opp) or ())
    out: list[str] = []
    for event_id in [*extra, mapped]:
        if event_id and event_id not in out:
            out.append(event_id)
    return out


def last_game_for_edition(edition: dict, schedule: list) -> dict | None:
    review = edition.get("lastGameReview") or {}
    opp = (review.get("opponent") or "").strip().lower()
    if not opp:
        return None
    wanted = _edition_score(review)
    if wanted:
        for event_id in _events_for_opponent(opp):
            if _recap_final_score(load_recap(event_id)) != wanted:
                continue
            for game in schedule or []:
                if str(game.get("id")) == event_id:
                    row = dict(game)
                    row["completed"] = True
                    row["kcScore"] = wanted[0]
                    row["oppScore"] = wanted[1]
                    return row
            return {
                "id": event_id,
                "completed": True,
                "opponent": review.get("opponent"),
                "kcScore": wanted[0],
                "oppScore": wanted[1],
            }
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
    recap = recap or {}
    return facts.check_review(edition, last, recap, schedule=schedule)


def replay_all_editions() -> list[tuple[str, list[str]]]:
    schedule = frozen_schedule()
    recaps = recaps_by_event()
    committed = committed_edition_names()
    rows = []
    for path in sorted(EDITIONS.glob("*.json")):
        if path.name not in committed:
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
    schedule = frozen_schedule()
    recaps = recaps_by_event()
    committed = committed_edition_names()
    rows = []
    for path in sorted(EDITIONS.glob("*.json")):
        if path.name not in committed:
            continue
        edition = json.loads(path.read_text(encoding="utf-8"))
        rows.append((path.name, salvage_edition(edition, schedule, recaps)))
    return rows


def salvage_edition_file(name: str) -> dict:
    """Replay one on-disk edition, including unpinned published Reviews."""
    schedule = frozen_schedule()
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
