"""Figure out where the Chiefs' season is *right now*.

The whole point of the site is that it "keeps looking ahead," so the phase drives
the framing: offseason storyline, training-camp battles, a preseason dress
rehearsal, a specific regular-season game week (preview vs. review), or the
playoffs. We derive it from the live schedule + today's date so the automation
never needs a human to flip a switch.
"""
from __future__ import annotations

from datetime import datetime, timezone

from . import config

# Rough training-camp window (Chiefs report to St. Joseph the final week of July).
CAMP_START = (7, 15)   # month, day
CAMP_END = (8, 6)      # first preseason game usually early-mid August

# A game only drives "game-week" framing once it is this close; a Week 1 game in
# September should not make late July look like a game week.
GAME_WEEK_DAYS = 9
# A just-finished game earns a "review" lean if the next one is still a ways off.
REVIEW_DAYS = 3


def _parse(dt_str: str | None):
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return None


def _md(now: datetime) -> tuple[int, int]:
    return (now.month, now.day)


def detect(schedule: list[dict], now: datetime = None) -> dict:
    """Return a phase descriptor.

    Shape::

        {
          "type": "offseason|training-camp|preseason|regular|postseason|season-complete",
          "label": "Training Camp",
          "week": 1 | None,
          "mode": "preview|review|camp|offseason",
          "edition": "2026 Training Camp",
          "nextGame": {…} | None,
          "lastGame": {…} | None,
        }
    """
    now = now or config.now_utc()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    reg = [g for g in schedule if g.get("seasonType") == "reg"]
    pre = [g for g in schedule if g.get("seasonType") == "pre"]
    post = [g for g in schedule if g.get("seasonType") == "post"]

    upcoming = []
    completed = []
    for g in schedule:
        dt = _parse(g.get("date"))
        if dt is None:
            continue
        if g.get("completed") or dt < now:
            completed.append((dt, g))
        else:
            upcoming.append((dt, g))
    upcoming.sort(key=lambda x: x[0])
    completed.sort(key=lambda x: x[0])

    next_game = upcoming[0][1] if upcoming else None
    last_game = completed[-1][1] if completed else None
    next_dt = _parse(next_game.get("date")) if next_game else None
    last_dt = _parse(last_game.get("date")) if last_game else None
    days_to_next = (next_dt - now).total_seconds() / 86400 if next_dt else None
    days_since_last = (now - last_dt).total_seconds() / 86400 if last_dt else None

    season = config.TEAM["season"]
    md = _md(now)
    in_camp_window = CAMP_START <= md <= CAMP_END

    def _mode() -> str:
        # Lean "review" only just after a game, when the next kickoff is still days away.
        if (
            days_since_last is not None
            and days_since_last <= REVIEW_DAYS
            and (days_to_next is None or days_to_next > REVIEW_DAYS)
        ):
            return "review"
        return "preview"

    # --- Game week in progress (only when the next game is actually close) --
    if next_game and days_to_next is not None and days_to_next <= GAME_WEEK_DAYS:
        st = next_game.get("seasonType")
        wk = next_game.get("week")
        if st == "post":
            return _wrap(
                "postseason", "Playoffs", wk, _mode(), f"{season} Playoffs",
                next_game, last_game,
            )
        if st == "pre":
            return _wrap(
                "preseason", "Preseason", wk, "preview", f"{season} Preseason",
                next_game, last_game,
            )
        return _wrap(
            "regular", f"Week {wk}", wk, _mode(), f"{season} · Week {wk}",
            next_game, last_game,
        )

    # --- Not close to a game: camp window wins over a distant opener --------
    # If regular-season games exist but none are upcoming and the last one is in
    # the past by the end of the schedule, the season is complete.
    if reg and not upcoming and last_game is not None:
        if last_dt and last_dt < now and last_game.get("seasonType") in ("reg", "post"):
            # Between end of season (Jan) and camp — treat as offseason, but if
            # we're in the July camp window prefer camp framing.
            if in_camp_window:
                return _wrap(
                    "training-camp", "Training Camp", None, "camp",
                    f"{season} Training Camp", reg[0] if reg else None, last_game,
                )
            return _wrap(
                "offseason", "Offseason", None, "offseason",
                f"{season} Offseason", reg[0] if reg else None, last_game,
            )

    if in_camp_window:
        return _wrap(
            "training-camp", "Training Camp", None, "camp",
            f"{season} Training Camp", next_game or (reg[0] if reg else None), last_game,
        )

    # Default: offseason, pointing at the season opener if we know it.
    opener = reg[0] if reg else next_game
    return _wrap(
        "offseason", "Offseason", None, "offseason",
        f"{season} Offseason", opener, last_game,
    )


def _wrap(ptype, label, week, mode, edition, next_game, last_game) -> dict:
    return {
        "type": ptype,
        "label": label,
        "week": week,
        "mode": mode,
        "edition": edition,
        "nextGame": next_game,
        "lastGame": last_game,
    }


def next_games(schedule: list[dict], count: int = 3, now: datetime = None) -> list[dict]:
    now = now or config.now_utc()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    out = []
    for g in schedule:
        dt = _parse(g.get("date"))
        if dt and not g.get("completed") and dt >= now:
            out.append(g)
    out.sort(key=lambda g: g.get("date") or "")
    return out[:count]
