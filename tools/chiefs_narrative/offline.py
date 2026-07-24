"""Deterministic, no-key writer.

When no LLM provider is available (or ``CHIEFS_PROVIDER=offline``), this builds a
complete, source-cited Chiefs Narrative from the live signals plus phase-aware
editorial scaffolding. It is genuinely useful — not a stub — so the daily
automation always ships a real edition and the site keeps looking ahead.

The scaffolding is intentionally light on hard claims: durable facts (coaches,
schedule, phase) come from :mod:`config`/live data, breaking specifics come from
the news wire with citations, and analysis is framed as questions/leverage
points rather than invented events.
"""
from __future__ import annotations

from datetime import datetime

from . import config

MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _fmt_game(game: dict | None) -> dict:
    if not game:
        return {}
    loc = "vs" if game.get("homeAway") == "home" else "@"
    opp = game.get("opponent", "TBD")
    date = game.get("date")
    label = ""
    at = game.get("venue", "")
    if game.get("homeAway") == "away":
        at = "@ " + (game.get("opponentShort") or opp)
    if date:
        try:
            dt = datetime.fromisoformat(date.replace("Z", "+00:00"))
            wk = game.get("week")
            wk_txt = f"Week {wk} · " if wk else ""
            label = f"{wk_txt}{DAYS[dt.weekday()]} {MONTHS[dt.month]} {dt.day}"
        except Exception:  # noqa: BLE001
            label = f"Week {game.get('week','')}".strip()
    return {
        "label": label,
        "opponent": f"{loc} {opp}".strip(),
        "at": at,
        "tv": game.get("tv", ""),
        "note": "",
    }


def _sources_from_news(news: list[dict], limit=8) -> list[dict]:
    out = []
    for n in news[:limit]:
        if not n.get("url"):
            continue
        out.append(
            {
                "title": n.get("title", ""),
                "publisher": n.get("publisher", ""),
                "url": n.get("url", ""),
                "note": n.get("summary", "")[:140],
            }
        )
    return out


def _injuries_from_news(news: list[dict]) -> list[dict]:
    hint = ("injur", "acl", "pup", "hamstring", "questionable", "return", "rehab", "surgery")
    out = []
    for n in news:
        blob = f"{n.get('title','')} {n.get('summary','')}".lower()
        if any(h in blob for h in hint):
            out.append(
                {
                    "player": "",
                    "status": "See report",
                    "note": n.get("title", ""),
                    "source": n.get("publisher", ""),
                }
            )
        if len(out) >= 4:
            break
    return out


def _markets_note(markets: dict) -> str:
    if not markets:
        return ""
    parts = []
    if markets.get("model"):
        parts.append(f"the model gives KC {markets['model']['kcWin']}%")
    if markets.get("vegas"):
        parts.append(f"Vegas has it {markets['vegas'].get('spreadDetail','')}")
    fut = markets.get("futures") or []
    sb = next((f for f in fut if "champion" in f["label"].lower() and "afc" not in f["label"].lower()), None)
    if sb:
        parts.append(f"Polymarket prices the Chiefs at {sb['chiefsPct']}% to win it all")
    return "; ".join(parts)


# ---------------------------------------------------------------------------
# Phase writers
# ---------------------------------------------------------------------------
def _camp(signals, phase, next_games) -> dict:
    t = config.TEAM
    opener = next_games[0] if next_games else (phase.get("nextGame") or {})
    opener_fmt = _fmt_game(opener)
    opp = opener.get("opponent", "the opener")
    mkt = _markets_note(signals.get("markets", {}))

    storyline_body = [
        f"The story of this camp is timing. {t['quarterback']} is rebuilding from a "
        f"torn ACL/LCL suffered last December, and every rep in St. Joseph is a "
        f"referendum on how close he is to full strength for Week 1. Andy Reid has "
        f"treated the ramp with caution, because the moment Mahomes practices in "
        f"earnest, the PUP contingency comes off the table.",
        f"On offense, {t['offensive_coordinator']} is back as coordinator with a "
        f"clear mandate: harder edges, cleaner fundamentals, and an identity that "
        f"doesn't ask Mahomes to be the answer on every third-and-long. Expect more "
        f"under-center looks, play-action off a real run threat, and 'easy offense' "
        f"— screens and spacing — to protect a knee that's still getting its legs.",
        f"On defense, {t['defensive_coordinator']} is again the steadying hand. The "
        f"camp battles that matter most are on the back end: who wins the outside "
        f"corner job and, especially, the slot. If the young secondary holds up, the "
        f"pass rush can tee off and this defense can carry the early weeks while the "
        f"offense finds its timing.",
        f"Everything points forward to {opener_fmt.get('label','Week 1')} "
        f"{opener_fmt.get('opponent','')}. "
        + (f"For context, {mkt}. " if mkt else "")
        + "The question every practice answers a little more: is Kansas City ready "
        "to flip the 2025 script?",
    ]

    spotlight = [
        {"tag": "QB1", "title": "Mahomes' rehab ramp vs. PUP math",
         "body": "The Chiefs can't treat camp like a normal build-up: once Mahomes "
                 "practices fully, he can't be placed on PUP. Watch how they meter his "
                 "team reps and whether he sees any preseason snaps."},
        {"tag": "Scheme", "title": "Bieniemy-ball: identity over hero-ball",
         "body": "More under center, more play-action, fewer 3rd-and-forever downs. "
                 "The install is about winning early downs so the offense functions "
                 "even on a managed snap count."},
        {"tag": "Trenches", "title": "Right tackle & the protection plan",
         "body": "The line's job is to keep a rehabbing QB clean. The open tackle "
                 "competition and the interior's veteran core set the ceiling for how "
                 "aggressive KC can be down the field."},
        {"tag": "Secondary", "title": "The slot corner is the swing job",
         "body": "In Spagnuolo's defense the nickel is a starter. Whoever wins the "
                 "slot determines how much disguise and pressure KC can throw at "
                 "opposing quarterbacks."},
        {"tag": "Rookies", "title": "Which draft pick has to play now",
         "body": "Premium picks were spent on defense. The Chiefs need at least one "
                 "rookie contributing immediately to look like themselves again."},
        {"tag": "Backfield", "title": "A real run game as a pressure valve",
         "body": "An early-down run threat is what makes play-action honest and keeps "
                 "the offense on schedule while Mahomes ramps up."},
    ]

    strategies = [
        "Open every practice report with the timeline board: Mahomes' rehab checkpoints and the countdown to Week 1.",
        "Keep a running 'PUP explainer' so OTA/camp speculation stays grounded and useful.",
        "Frame Bieniemy + the backfield as an identity change: under center, early-down efficiency, play-action.",
        "Turn the defensive draft class into a snap plan — who plays early and how it protects the veteran core.",
        "Script 'easy offense' installs (screens, spacing, boots) as the knee-management plan.",
        "Track the slot-corner battle daily; it's the tell for how aggressive Spagnuolo can be.",
    ]

    debates = [
        "Hold Mahomes out of preseason entirely, or get him one live series to shake off the rust?",
        "What does 'Bieniemy-ball' actually look like in 2026 — more under center, more play-action, or just cleaner details?",
        "Which rookie MUST be ready fast for this team to flip its 2025 script?",
        "Is a real run game the missing piece, or is it still Mahomes-or-bust?",
        "On a scale of nervous to confident, where are you on the Week 1 knee?",
    ]

    injuries = [
        {"player": config.TEAM["quarterback"], "status": "Rehab / camp ramp",
         "note": "Returning from a torn ACL/LCL (Dec. 2025); targeting Week 1, snaps carefully managed.",
         "source": "ESPN"},
    ] + _injuries_from_news(signals.get("news", []))

    personnel = [
        {"move": "OC change", "detail": f"{config.TEAM['offensive_coordinator']} returns to run the offense."},
        {"move": "Open competition at tackle", "detail": "The protection plan for a rehabbing QB starts up front."},
        {"move": "Secondary reshuffle", "detail": "Outside corner and slot are the camp's most important battles."},
        {"move": "Defense-first draft", "detail": "Premium capital was spent on pass rush and coverage flexibility."},
    ]

    xsandos = [
        {"concept": "play_action_boot",
         "title": "Under-center play-action boot",
         "situation": "1st & 10, early down — knee-management down",
         "why": "Sells the run, moves the launch point off the rush, and gives Mahomes "
                "a clean half-field read without asking the knee to climb a muddy pocket.",
         "coaching": "Marry the boot to the actual inside-zone look you're running so "
                     "the linebackers have to honor the fake.",
         "labels": {"x": "post", "z": "comeback", "te": "flat"}},
        {"concept": "spacing_screen",
         "title": "RB screen / easy offense",
         "situation": "2nd & long or vs. heavy pressure",
         "why": "Turns an aggressive rush into a negative for the defense and gets the "
                "ball out of Mahomes' hands before the knee is ever tested.",
         "coaching": "Sell max-protect for a beat, then release the linemen — timing is "
                     "everything on the slip.",
         "labels": {}},
        {"concept": "inside_zone",
         "title": "Under-center inside zone",
         "situation": "1st down, establish identity",
         "why": "The early-down run that makes the whole play-action menu honest and "
                "keeps the offense on schedule.",
         "coaching": "One cut, get downhill — let the double-teams create the crease.",
         "labels": {}},
        {"concept": "zone_blitz",
         "title": "Spagnuolo simulated pressure",
         "situation": "3rd & 5+, obvious passing down",
         "why": "Shows heat, drops a lineman into a hook, and rushes the second level to "
                "confuse protection while the coverage stays sound behind it.",
         "coaching": "Disguise is the point: the picture pre-snap must look like man "
                     "pressure even when it's zone behind it.",
         "labels": {}},
    ]

    coaching = [
        {"topic": "Reid + Bieniemy install", "detail": "Accountability and fundamentals "
         "after a 2025 that turned on one-score games; the staff wants faster starts."},
        {"topic": "Spagnuolo's disguise menu", "detail": "Simulated pressures and "
         "two-high shells to protect a young secondary while the pass rush develops."},
        {"topic": "Knee-management game plan", "detail": "Script early-down runs, boots, "
         "and screens so the offense doesn't lean on hero-ball while Mahomes ramps."},
    ]

    run_of_show = [
        {"segment": "Cold open — the timeline board", "length": "0:00–1:15",
         "talkTrack": "Set the stakes: Mahomes' rehab checkpoints and the Week 1 countdown."},
        {"segment": "Mahomes watch + PUP explainer", "length": "1:15–4:00",
         "talkTrack": "What full participation would mean and why the PUP math matters."},
        {"segment": "Bieniemy-ball film breakdown", "length": "4:00–8:00",
         "talkTrack": "Walk the under-center boot and screen diagrams; explain the identity shift."},
        {"segment": "Camp battles — trenches & secondary", "length": "8:00–11:30",
         "talkTrack": "Right tackle and slot corner; who's rising, who's on the bubble."},
        {"segment": "Defensive X&O", "length": "11:30–14:00",
         "talkTrack": "Spagnuolo's simulated pressure and how it protects the young DBs."},
        {"segment": "Look ahead + fan debates", "length": "14:00–16:00",
         "talkTrack": "Tee up the opener and read the top viewer debates on screen."},
    ]

    return {
        "edition": phase.get("edition", "2026 Training Camp"),
        "headline": "Camp is about timing: Mahomes' knee, Bieniemy's identity, and a defense that has to carry the open",
        "dek": "A film-room read on the storylines that decide how fast Kansas City flips the 2025 script.",
        "videoHook": "The Chiefs' season isn't one headline — it's a countdown. How fast does the knee get right, "
                     "and can the identity change hold up when the lights come on?",
        "theEdge": "If the secondary settles and the run game is real, Kansas City can win early while Mahomes ramps.",
        "storyline": {
            "lede": f"Chiefs camp opened at {t['camp_site']} with one question under "
                    f"everything: how ready is {t['quarterback']}'s knee — and how much "
                    f"can a retooled identity carry until it is?",
            "body": storyline_body,
        },
        "nextGame": {**opener_fmt, "note": "The countdown clock for everything camp is building toward."},
        "spotlight": spotlight,
        "matchups": _camp_matchups(opp, signals.get("markets", {})),
        "xsandos": xsandos,
        "coaching": coaching,
        "injuries": injuries,
        "personnel": personnel,
        "strategies": strategies,
        "debates": debates,
        "runOfShow": run_of_show,
        "sources": _sources_from_news(signals.get("news", [])),
    }


def _camp_matchups(opp: str, markets: dict) -> list[dict]:
    edge_note = ""
    if markets.get("vegas"):
        edge_note = f"Opening line: {markets['vegas'].get('spreadDetail','')}."
    return [
        {"unit": "Chiefs pass rush vs. protection schemes",
         "chiefs": "Chris Jones + edge rotation", "opponent": "Opposing offensive lines",
         "edge": "KC", "note": "The engine that lets a young secondary play aggressive."},
        {"unit": "Chiefs secondary vs. vertical passing",
         "chiefs": "New-look corner room", "opponent": f"{opp} skill players",
         "edge": "PUSH", "note": "The slot battle is the swing; disguise buys time."},
        {"unit": "Chiefs run game vs. loaded boxes",
         "chiefs": "Under-center run identity", "opponent": "Front seven",
         "edge": "PUSH", "note": "Early-down efficiency is the whole plan. " + edge_note},
        {"unit": "Mahomes' mobility vs. his own rehab clock",
         "chiefs": "Managed snaps, quick game", "opponent": "The calendar",
         "edge": "PUSH", "note": "The one matchup no opponent controls."},
    ]


def _generic(signals, phase, next_games) -> dict:
    """Preview/review/offseason/preseason/playoffs — driven by live data."""
    t = config.TEAM
    ng = next_games[0] if next_games else (phase.get("nextGame") or {})
    ng_fmt = _fmt_game(ng)
    opp = ng.get("opponent", "the next opponent")
    ptype = phase.get("type", "offseason")
    mode = phase.get("mode", "preview")
    markets = signals.get("markets", {})
    mkt = _markets_note(markets)

    is_review = mode == "review"
    last = phase.get("lastGame") or {}

    if ptype in ("regular", "postseason"):
        head = f"{ng_fmt.get('label','Next up')}: {t['abbr']} {ng_fmt.get('opponent','')}"
        lede = (
            f"Kansas City turns the page to {opp}. "
            + (f"{mkt}. " if mkt else "")
            + "Here's the film-room read: the matchups that decide it, the X&O plan, "
            "and where the honest edges are."
        )
        edition = phase.get("edition")
    elif ptype == "preseason":
        head = "Preseason dress rehearsal: what actually matters"
        lede = ("Results won't matter much, but reps will. Track starter snaps, the "
                "roster-bubble battles, and the install carrying into the opener.")
        edition = phase.get("edition")
    else:  # offseason
        head = "Offseason build: needs, additions, and the path to next season"
        lede = ("The roster story keeps moving. Here's where the Chiefs stand, what "
                "still needs answering, and how the pieces point toward the season ahead.")
        edition = phase.get("edition")

    xsandos = [
        {"concept": "four_verts", "title": "4 verticals vs. Cover 3",
         "situation": "2nd & medium, spread look",
         "why": "Stretches the deep zones and forces the safety to choose — a staple "
                "answer when the defense sits back.",
         "coaching": "Bend the seams to the hash and read the safety's leverage.",
         "labels": {}},
        {"concept": "mesh", "title": "Mesh vs. man",
         "situation": "3rd & short/medium",
         "why": "Rub-heavy crossers beat man coverage and give a clean, quick answer "
                "with an RB checkdown as the outlet.",
         "coaching": "Set the mesh depth tight so the pick is legal and natural.",
         "labels": {"x": "corner", "z": "sit"}},
        {"concept": "cover_two", "title": "Spagnuolo Cover-2 shell",
         "situation": "Protect a lead / obvious passing down",
         "why": "Takes the top off, rallies to tackle, and dares the offense to be "
                "patient — the situational change-up.",
         "coaching": "Sink the corners, funnel everything to the deep halves.",
         "labels": {}},
    ]

    matchups = [
        {"unit": f"Chiefs offense vs. {opp} defense",
         "chiefs": "Reid/Bieniemy game plan", "opponent": f"{opp} front & coverage",
         "edge": "PUSH", "note": "Early-down efficiency sets up everything after."},
        {"unit": f"Chiefs defense vs. {opp} offense",
         "chiefs": "Spagnuolo pressure package", "opponent": f"{opp} playmakers",
         "edge": "PUSH", "note": "Disguise and rush to force the mistake."},
        {"unit": "Special teams & field position",
         "chiefs": "Hidden-yard battle", "opponent": opp,
         "edge": "PUSH", "note": "In tight games the margins live here."},
        {"unit": "Coaching & adjustments",
         "chiefs": f"{t['head_coach']} staff", "opponent": f"{opp} staff",
         "edge": "KC", "note": "Halftime adjustments are a Chiefs advantage."},
    ]

    review_block = []
    if is_review and last:
        review_block = [
            {"tag": "Recap", "title": "What the tape said",
             "body": f"A quick review of the last result before we look ahead to {opp}."},
        ]

    return {
        "edition": edition,
        "headline": head,
        "dek": "The evolving Chiefs story, always looking ahead to the next Sunday.",
        "videoHook": f"Let's get into it — {t['abbr']} and what's next, the X's, the O's, and the edges.",
        "theEdge": (f"Model/market read: {mkt}." if mkt else
                    "The margins are in the trenches and on early downs."),
        "storyline": {"lede": lede, "body": [
            "This edition tracks the through-line week to week: what changed, what it "
            "means, and how it shapes the next game.",
        ]},
        "nextGame": {**ng_fmt, "note": "The next chapter of the season."},
        "spotlight": (review_block + [
            {"tag": "Preview", "title": f"The {opp} problem",
             "body": "What the opponent does best, and the Chiefs' plan to take it away."},
            {"tag": "Key", "title": "The swing matchup",
             "body": "The one-on-one that most likely decides the outcome."},
            {"tag": "Watch", "title": "X-factor to monitor",
             "body": "A player or wrinkle that could tilt the margin."},
        ]),
        "matchups": matchups,
        "xsandos": xsandos,
        "coaching": [
            {"topic": "Game-plan identity", "detail": "Win early downs, stay on schedule, "
             "and let the play-action menu do the heavy lifting."},
            {"topic": "Pressure vs. protection", "detail": "Spagnuolo's disguise against "
             "the opponent's answers is the chess match to watch."},
        ],
        "injuries": _injuries_from_news(signals.get("news", [])) or [
            {"player": "", "status": "Check the wire", "note": "See the latest reports below.", "source": ""}
        ],
        "personnel": [
            {"move": "Roster watch", "detail": "Transactions and depth-chart moves that shape the plan."},
        ],
        "strategies": [
            "Win first down — stay out of obvious passing situations.",
            "Use motion and play-action to simplify the reads.",
            "Let the pass rush dictate; disguise the coverage behind it.",
            "Steal a possession on special teams / field position.",
            "Script the opening drive to set an early tone.",
        ],
        "debates": [
            f"What's the single biggest key against {opp}?",
            "Which matchup are you most worried about?",
            "Trust the model, the market, or your gut this week?",
        ],
        "runOfShow": [
            {"segment": "Cold open — the stakes", "length": "0:00–1:00",
             "talkTrack": f"Frame the {opp} matchup and what's on the line."},
            {"segment": "Matchups that decide it", "length": "1:00–5:00",
             "talkTrack": "Walk the key one-on-ones."},
            {"segment": "X&O film room", "length": "5:00–9:00",
             "talkTrack": "Break down the diagrams on screen."},
            {"segment": "Model, Vegas & markets", "length": "9:00–11:00",
             "talkTrack": "Share the numbers and give the honest read."},
            {"segment": "Prediction + fan debates", "length": "11:00–13:00",
             "talkTrack": "Make the call and read the debates."},
        ],
        "sources": _sources_from_news(signals.get("news", [])),
    }


def write(signals: dict, phase: dict, next_games: list[dict]) -> dict:
    if phase.get("type") == "training-camp":
        return _camp(signals, phase, next_games)
    return _generic(signals, phase, next_games)
