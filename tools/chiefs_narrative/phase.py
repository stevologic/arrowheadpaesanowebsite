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

# Training camp through late August (roster cutdowns), not just the first
# preseason Saturday. Mid-August is still camp/preseason, not the offseason.
CAMP_START = (7, 15)   # month, day
CAMP_END = (8, 31)

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


def _aware(now: datetime | None) -> datetime:
    now = now or config.now_utc()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now


def is_final(game: dict | None) -> bool:
    """True only when ESPN marked the game completed and both scores exist."""
    if not game or not game.get("completed"):
        return False
    return game.get("kcScore") is not None and game.get("oppScore") is not None


def is_live(game: dict | None, now: datetime = None) -> bool:
    """In progress, or past kickoff without a completed flag."""
    if not game or game.get("completed"):
        return False
    if game.get("inProgress"):
        return True
    dt = _parse(game.get("date"))
    if dt is None:
        return False
    return dt < _aware(now)


def is_upcoming(game: dict | None, now: datetime = None) -> bool:
    """True only for a slate row that has not kicked off yet."""
    if not game or game.get("completed") or is_live(game, now):
        return False
    dt = _parse(game.get("date"))
    return dt is not None and dt > _aware(now)


def any_in_progress(schedule: list[dict] | None) -> bool:
    """True when ESPN has any Chiefs game in state ``in``."""
    return any(bool(g.get("inProgress")) and not g.get("completed") for g in schedule or [])


def any_live(schedule: list[dict] | None, now: datetime = None) -> bool:
    """True when any slate row is in progress or past kickoff without a final."""
    return any(is_live(g, now) for g in schedule or [])


def format_edition(ph: dict | None) -> str:
    """Desk-written edition header. The model does not own this string.

    Regular review: ``2026 Week 4 · Week 3 Review`` (upcoming week, then
    the completed week). Regular preview: ``2026 Week N · Preview``.
    Archive grouping still uses ``phase.week`` (the upcoming / live week).
    """
    season = config.TEAM["season"]
    ph = ph or {}
    ptype = ph.get("type") or ""
    mode = ph.get("mode") or ""
    week = ph.get("week")
    last = ph.get("lastGame") or {}
    last_week = last.get("week")
    if ptype == "regular" and week:
        if mode == "review" and last_week:
            return f"{season} Week {week} · Week {last_week} Review"
        return f"{season} Week {week} · Preview"
    existing = (ph.get("edition") or "").strip()
    if existing:
        return existing
    label = (ph.get("label") or "Narrative").strip()
    return f"{season} {label}"


def _regular_edition(season, week, mode, last_game) -> str:
    last_week = (last_game or {}).get("week")
    if mode == "review" and last_week:
        return f"{season} Week {week} · Week {last_week} Review"
    return f"{season} Week {week} · Preview"


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
    now = _aware(now)

    reg = [g for g in schedule if g.get("seasonType") == "reg"]
    pre = [g for g in schedule if g.get("seasonType") == "pre"]
    post = [g for g in schedule if g.get("seasonType") == "post"]

    upcoming = []
    completed = []
    live = []
    for g in schedule:
        dt = _parse(g.get("date"))
        if dt is None:
            continue
        # Completed only when ESPN says so. Past kickoff without that flag,
        # or an in-progress row, is live — never a lastGame for review.
        if g.get("completed"):
            completed.append((dt, g))
        elif g.get("inProgress") or dt < now:
            live.append((dt, g))
        else:
            upcoming.append((dt, g))
    upcoming.sort(key=lambda x: x[0])
    completed.sort(key=lambda x: x[0])
    live.sort(key=lambda x: x[0])

    next_game = upcoming[0][1] if upcoming else None
    last_game = completed[-1][1] if completed else None
    live_game = live[-1][1] if live else None
    next_dt = _parse(next_game.get("date")) if next_game else None
    last_dt = _parse(last_game.get("date")) if last_game else None
    days_to_next = (next_dt - now).total_seconds() / 86400 if next_dt else None
    days_since_last = (now - last_dt).total_seconds() / 86400 if last_dt else None

    season = config.TEAM["season"]
    md = _md(now)
    in_camp_window = CAMP_START <= md <= CAMP_END

    def _mode() -> str:
        # Never review a live or unfinished game. Review only after a real
        # final, when the next kickoff is still a few days away.
        if live_game is not None:
            return "preview"
        if not is_final(last_game):
            return "preview"
        if (
            days_since_last is not None
            and days_since_last <= REVIEW_DAYS
            and (days_to_next is None or days_to_next > REVIEW_DAYS)
        ):
            return "review"
        return "preview"

    def _week_wrap(game, mode):
        st = game.get("seasonType")
        wk = game.get("week")
        if st == "post":
            return _wrap(
                "postseason", "Playoffs", wk, mode, f"{season} Playoffs",
                next_game, last_game, live_game, now,
            )
        if st == "pre":
            return _wrap(
                "preseason", "Preseason", wk, "preview", f"{season} Preseason",
                next_game, last_game, live_game, now,
            )
        return _wrap(
            "regular", f"Week {wk}", wk, mode,
            _regular_edition(season, wk, mode, last_game),
            next_game, last_game, live_game, now,
        )

    # A live (or past-kickoff unfinished) game owns the week label.
    if live_game:
        return _week_wrap(live_game, "preview")

    # --- Game week in progress (only when the next game is actually close) --
    if next_game and days_to_next is not None and days_to_next <= GAME_WEEK_DAYS:
        return _week_wrap(next_game, _mode())

    # Bye week (or any in-season gap longer than GAME_WEEK_DAYS): keep the
    # upcoming regular/post week instead of falling through to offseason.
    if (
        next_game
        and last_game
        and last_game.get("seasonType") in ("reg", "post")
        and next_game.get("seasonType") in ("reg", "post")
    ):
        return _week_wrap(next_game, _mode())

    # Still alive in the playoffs, but ESPN has not posted the next row
    # (divisional after a bye / wild-card win). Do not fall to offseason.
    if (
        not upcoming
        and last_game
        and last_game.get("seasonType") == "post"
        and is_final(last_game)
    ):
        kc, opp = last_game.get("kcScore"), last_game.get("oppScore")
        if kc is not None and opp is not None and kc > opp:
            return _wrap(
                "postseason", "Playoffs", last_game.get("week"), "review",
                f"{season} Playoffs", None, last_game, live_game, now,
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
                    f"{season} Training Camp", None, last_game,
                    live_game, now,
                )
            return _wrap(
                "offseason", "Offseason", None, "offseason",
                f"{season} Offseason", None, last_game,
                live_game, now,
            )

    # A remaining preseason game means we are still in the dress-rehearsal
    # stretch even if kickoff is more than GAME_WEEK_DAYS away.
    if next_game and next_game.get("seasonType") == "pre":
        wk = next_game.get("week")
        return _wrap(
            "preseason", "Preseason", wk, "preview", f"{season} Preseason",
            next_game, last_game, live_game, now,
        )

    if in_camp_window:
        return _wrap(
            "training-camp", "Training Camp", None, "camp",
            f"{season} Training Camp", next_game, last_game,
            live_game, now,
        )

    # After camp / preseason, the regular opener still owns the desk even
    # when kickoff is more than GAME_WEEK_DAYS away (Sep 1–5).
    if (
        next_game
        and next_game.get("seasonType") in ("reg", "post")
        and (last_game is None or last_game.get("seasonType") not in ("reg", "post"))
    ):
        return _week_wrap(next_game, "preview")

    # Default: offseason. Never point nextGame at a completed opener.
    return _wrap(
        "offseason", "Offseason", None, "offseason",
        f"{season} Offseason", next_game, last_game, live_game, now,
    )


def _wrap(
    ptype, label, week, mode, edition, next_game, last_game, live_game=None, now=None
) -> dict:
    return {
        "type": ptype,
        "label": label,
        "week": week,
        "mode": mode,
        "edition": edition,
        "nextGame": next_game if is_upcoming(next_game, now) else None,
        "lastGame": last_game,
        "liveGame": live_game,
    }


def slate_record(schedule: list[dict], season_type: str | None = None) -> str:
    """W-L (-T) from completed games that actually have scores."""
    wins = losses = ties = 0
    for game in schedule or []:
        if season_type and game.get("seasonType") != season_type:
            continue
        if not game.get("completed"):
            continue
        kc, opp = game.get("kcScore"), game.get("oppScore")
        if kc is None or opp is None:
            continue
        if kc > opp:
            wins += 1
        elif kc < opp:
            losses += 1
        else:
            ties += 1
    if not (wins or losses or ties):
        return ""
    if ties:
        return f"{wins}-{losses}-{ties}"
    return f"{wins}-{losses}"


def current_record(schedule: list[dict], phase: dict | None = None) -> str:
    """Current-season W-L from the slate. Never last season's leftover.

    Regular/postseason use completed regular-season scores. Preseason uses
    the exhibition slate. Camp/offseason may still show a slate record if
    games exist; otherwise return empty so callers fail loudly instead of
    substituting ``TEAM['last_season_record']``.
    """
    ptype = (phase or {}).get("type") or ""
    if ptype in ("regular", "postseason"):
        return slate_record(schedule, "reg")
    if ptype == "preseason":
        return slate_record(schedule, "pre")
    return slate_record(schedule, "reg") or slate_record(schedule, "pre")


def game_result(game: dict | None) -> dict:
    """Scoreboard tokens for a completed (or in-progress) slate row."""
    if not game:
        return {}
    kc, opp = game.get("kcScore"), game.get("oppScore")
    result = ""
    score = ""
    if kc is not None and opp is not None:
        if kc > opp:
            result = "W"
        elif kc < opp:
            result = "L"
        else:
            result = "T"
        score = f"KC {kc}–{opp}"
    return {"result": result, "score": score}


def format_last_game(game: dict | None) -> dict:
    """Turn a completed slate row into the last-game review header."""
    card = format_next_game(game)
    if not card:
        return {}
    card.update(game_result(game))
    if game.get("id"):
        card["id"] = str(game["id"])
    return card


def format_next_game(game: dict | None) -> dict:
    """Turn a schedule row into the narrative nextGame card."""
    if not game:
        return {}
    kickoff = game.get("kickoff") or ""
    st = game.get("seasonType")
    week = game.get("week")
    if st == "pre":
        prefix = f"Preseason Week {week}" if week else "Preseason"
    elif st == "post":
        prefix = "Playoffs"
    elif week:
        prefix = f"Week {week}"
    else:
        prefix = ""
    if prefix and kickoff:
        label = f"{prefix} · {kickoff}"
    else:
        label = prefix or kickoff
    venue = game.get("venue") or ""
    if game.get("homeAway") == "away" and venue:
        venue = f"@ {venue}"
    return {
        "label": label,
        "opponent": game.get("opponent") or "",
        "at": venue,
        "tv": game.get("tv") or "",
        "note": "",
    }


def next_games(schedule: list[dict], count: int = 3, now: datetime = None) -> list[dict]:
    now = _aware(now)
    out = []
    for g in schedule:
        dt = _parse(g.get("date"))
        if not dt or g.get("completed") or is_live(g, now):
            continue
        out.append(g)
    out.sort(key=lambda g: g.get("date") or "")
    return out[:count]
