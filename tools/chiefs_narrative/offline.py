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

from . import config, phase as phase_mod

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


def _news_about(news: list[dict], needles: list[str]) -> list[dict]:
    hits = []
    keys = [n.lower() for n in needles if n]
    for item in news or []:
        blob = f"{item.get('title', '')} {item.get('summary', '')}".lower()
        if any(key in blob for key in keys):
            hits.append(item)
    return hits


def _opp_needles(game: dict | None) -> list[str]:
    if not game:
        return []
    return [
        game.get("opponent") or "",
        game.get("opponentShort") or "",
        game.get("opponentAbbr") or "",
    ]


def _recap_line(recap: dict) -> str:
    if not recap:
        return ""
    kc = recap.get("kc") or {}
    opp = recap.get("opp") or {}
    bits = []
    if kc.get("totalYards") or opp.get("totalYards"):
        bits.append(
            f"ESPN had KC at {kc.get('totalYards') or '—'} total yards "
            f"against {opp.get('totalYards') or '—'} for the opponent"
        )
    if kc.get("turnovers") or opp.get("turnovers"):
        bits.append(
            f"turnovers KC {kc.get('turnovers') or '—'} / opp {opp.get('turnovers') or '—'}"
        )
    if kc.get("thirdDownEff"):
        bits.append(f"KC third downs {kc['thirdDownEff']}")
    return "; ".join(bits)


def _last_game_review(signals: dict, phase: dict) -> dict:
    last = phase.get("lastGame") or {}
    if not last:
        return {}
    header = phase_mod.format_last_game(last)
    recap = signals.get("lastGameRecap") or {}
    news = _news_about(signals.get("news", []), _opp_needles(last) + ["loss", "win", "takeaway", "final"])
    opp = last.get("opponent") or "the last opponent"
    loc = "at Arrowhead" if last.get("homeAway") == "home" else f"on the road at {last.get('venue') or opp}"
    result = header.get("result") or ""
    score = header.get("score") or ""
    verb = {"W": "beat", "L": "fell to", "T": "tied"}.get(result, "played")
    ptype = phase.get("type", "")
    dress = ptype in ("preseason", "training-camp")

    score_bit = f" ({score})" if score else ""
    lede = (
        f"Kansas City {verb} {opp}{score_bit} {loc}".replace("  ", " ").strip()
        + (f" — {header.get('label')}." if header.get("label") else ".")
    )
    if dress:
        lede += " Preseason scoreboard is secondary; the tape on snaps, the bubble, and the install is not."

    recap_txt = _recap_line(recap)
    analysis = []
    if result == "L":
        analysis.append(
            f"The {opp} game ended the wrong way, and the film will not let Kansas City "
            f"file it as noise. {score or 'The final'} is the headline; the more useful "
            f"question is whether the offense stayed on schedule and whether the defense "
            f"could finish a drive. "
            + (f"The box: {recap_txt}. " if recap_txt else "")
            + "If the answer is 'almost,' the next opponent will make 'almost' expensive."
        )
    elif result == "W":
        analysis.append(
            f"A win over {opp} is still a win — even in August — because it is evidence "
            f"the script can hold when the other side punches back. "
            + (f"The box: {recap_txt}. " if recap_txt else "")
            + "The tape to keep is how they scored, not that they did."
        )
    else:
        analysis.append(
            f"The last look at {opp} is now in the book. "
            + (f"{recap_txt}. " if recap_txt else "")
            + "Treat it as a teaching tape: who earned snaps, who lost them, and which "
            "calls survived contact."
        )

    if recap.get("scoring"):
        analysis.append(
            "Scoring sequence from ESPN: " + "; ".join(recap["scoring"][:6]) + "."
        )
    if recap.get("leaders"):
        names = ", ".join(
            f"{row['player']} ({row.get('category')}: {row.get('value')})"
            for row in recap["leaders"][:3]
        )
        analysis.append(f"KC statistical leaders on the ESPN recap: {names}.")

    if news:
        analysis.append(
            f"The wire is already arguing the same game: {news[0].get('title')} "
            f"({news[0].get('publisher')}). Use that as the cited through-line, not a new rumor."
        )
    else:
        analysis.append(
            "Without a fresh cited injury or transaction from the wire, stay on the "
            "schematic questions: early-down efficiency, protection, and whether the "
            "secondary can play the call without a bust."
        )

    if dress:
        what_worked = [
            "Live snaps for the people who still have something to prove on the bubble.",
            "A chance to run the Bieniemy early-down script against a real front.",
        ]
        what_didnt = [
            "Finishing: close games in August still expose two-minute and red-zone rust.",
            "Starter-to-depth drop-off — the tape the cutdown weekend actually cares about.",
        ]
    else:
        what_worked = [
            "Any drive that stayed on schedule and let play-action stay in the call sheet.",
            "Spagnuolo looks that forced a hot throw or a checkdown instead of the shot.",
        ]
        what_didnt = [
            "Negative plays that put Mahomes (or the backup) in obvious passing downs.",
            "Missed tackles / busted leverage that turned a stop into a chunk.",
        ]

    takeaways = [
        {
            "tag": "",
            "title": "The result is the start of the tape, not the end",
            "body": (
                f"{score or 'The final'} vs. {opp} only matters if Kansas City can name "
                "the two or three plays that created it — and whether those are scheme, "
                "personnel, or execution."
            ),
        },
        {
            "title": "Who earned the next snap",
            "body": (
                "The useful review is the depth chart: which lineman, nickel, or skill "
                "player looked like a Wednesday-night keeper, and who just played themselves "
                "into a shorter leash."
            ),
        },
    ]
    if recap.get("kc", {}).get("turnovers"):
        takeaways.append(
            {
                "title": "Ball security is the quiet game",
                "body": (
                    f"ESPN charged Kansas City with {recap['kc']['turnovers']} turnover(s). "
                    "That is the first item on the correction list no matter the opponent."
                ),
            }
        )

    return {
        "opponent": header.get("opponent") or opp,
        "label": header.get("label", ""),
        "result": result,
        "score": score,
        "lede": lede,
        "analysis": analysis,
        "takeaways": takeaways,
        "whatWorked": what_worked,
        "whatDidnt": what_didnt,
    }


def _current_state(signals: dict, phase: dict) -> dict:
    t = config.TEAM
    schedule = signals.get("schedule") or []
    ptype = phase.get("type", "offseason")
    last = phase.get("lastGame") or {}
    next_g = phase.get("nextGame") or {}
    pre_rec = phase_mod.slate_record(schedule, "pre")
    reg_rec = phase_mod.slate_record(schedule, "reg")
    record = reg_rec or pre_rec or t["last_season_record"]
    last_line = ""
    if last:
        header = phase_mod.format_last_game(last)
        if header.get("score"):
            last_line = f" Coming off {header['result']} {header['score']} vs. {last.get('opponent')}."

    if ptype == "training-camp":
        lede = (
            f"Camp at {t['camp_site']} is still the real season clock: "
            f"{t['quarterback']}'s ramp, {t['offensive_coordinator']}'s identity, "
            f"and whether {t['defensive_coordinator']}'s young secondary can hold. "
            f"{last_line}"
        ).strip()
        work = [
            {"title": "Mahomes' timing vs. the PUP calendar",
             "body": "Every full-speed team period answers whether Week 1 is a plan or a hope."},
            {"title": "An early-down identity that is not hero-ball",
             "body": "Inside zone, boots, and screens have to look like a system before Denver."},
            {"title": "Nickel and tackle — the two jobs that unlock the rest",
             "body": "If those settle, Spagnuolo can pressure and Bieniemy can stay on schedule."},
        ]
        think = [
            {"title": "How many live snaps does the knee actually need?",
             "body": "Preseason series vs. total rest is the argument in the building and in the comments."},
            {"title": "Which rookie is a Week 1 player, not a redshirt?",
             "body": "The draft capital on defense only pays if someone plays early."},
        ]
    elif ptype == "preseason":
        lede = (
            f"Dress-rehearsal football: results are a footnote, but the depth chart is not."
            f"{last_line} Next up is {next_g.get('opponent') or 'the next exhibition'} "
            f"and then the real opener."
        )
        work = [
            {"title": "Starter snaps that actually test the install",
             "body": "A series that looks like September — not a seven-man skeleton walkthrough."},
            {"title": "The roster bubble, by position",
             "body": "Tackle, nickel, receiver, and the back end of the D-line will decide cutdown weekend."},
            {"title": "Red zone and two-minute without the regulars bailing it out",
             "body": "August nights turn on those two scripts; so do September ones."},
        ]
        think = [
            {"title": "Who is actually playing themselves onto the 53?",
             "body": "Name the winners and losers off the last tape, then watch if it repeats."},
            {"title": "How much of the Week 1 call sheet is already in?",
             "body": "If the next exhibition still looks like camp, the opener will too."},
        ]
        if pre_rec:
            record = f"Preseason {pre_rec}"
    elif ptype in ("regular", "postseason"):
        lede = (
            f"The 2026 Chiefs are {record or 'still writing the record'} after the last "
            f"kickoff.{last_line} The next assignment is {next_g.get('opponent') or 'on the slate'}."
        )
        work = [
            {"title": "Correct the last-game problems before they become identity",
             "body": "Whatever lost (or nearly lost) the previous Sunday has to have a named fix by Wednesday."},
            {"title": "Stay on schedule so the play-action menu stays live",
             "body": "Third-and-long is where this roster still looks most 2025."},
            {"title": "Protect the quarterback and the secondary at the same time",
             "body": "If the nickel is a mismatch, Spagnuolo cannot send the look he wants."},
        ]
        think = [
            {"title": "Is the film saying scheme or personnel?",
             "body": "The staff has to decide what is a call-sheet problem and what is a player problem."},
            {"title": "How much of the next opponent is a style clash vs. a talent gap?",
             "body": "Game-plan honesty starts there — not with the Vegas number."},
        ]
        if reg_rec:
            record = f"{reg_rec}"
    else:
        lede = (
            f"Offseason work is still about flipping {t['last_season_record']} from "
            f"{t['last_season']}.{last_line} The opener is the finish line for every addition."
        )
        work = [
            {"title": "The holes that last season actually proved",
             "body": "Protection, the run game, and a secondary that can play without help over the top."},
            {"title": "Mahomes' rehab as a roster-building constraint",
             "body": "You do not build a hero-ball offense while the knee is still a question."},
        ]
        think = [
            {"title": "What still has to be true by camp?",
             "body": "A starting tackle plan, a nickel, and a backfield that makes play-action honest."},
        ]

    return {
        "lede": lede.strip(),
        "record": record,
        "workOn": work,
        "thinkAbout": think,
    }


def _game_plan(signals: dict, phase: dict, next_games: list[dict]) -> dict:
    t = config.TEAM
    nxt = (next_games[0] if next_games else None) or phase.get("nextGame") or {}
    if not nxt:
        return {}
    opp = nxt.get("opponent") or "the next opponent"
    opp_short = nxt.get("opponentShort") or opp
    loc = "at Arrowhead" if nxt.get("homeAway") == "home" else f"at {nxt.get('venue') or opp}"
    markets = signals.get("markets") or {}
    mkt = _markets_note(markets)
    ptype = phase.get("type", "")
    last = phase.get("lastGame") or {}

    if ptype == "preseason":
        lede = (
            f"{opp} {loc} is the next dress rehearsal — last chance to settle snaps "
            f"before the roster math gets real."
        )
        how = (
            f"{opp} will not game-plan Kansas City like a Week 1 opponent, and KC should "
            f"not treat them like a bye. Use the look to stress the same things Denver "
            f"will: early-down run fits, a boot that looks like the run, and a nickel who "
            f"can live in the slot. "
            + (f"Market context: {mkt}." if mkt else "The number is not the story; the tape is.")
        )
        keys = [
            {"title": "Starter series that look like September",
             "body": f"If the starters sit, the ones who play have to run the real call sheet vs. {opp}."},
            {"title": "Win the bubble, not the scoreboard",
             "body": "Tackle, nickel, and the last receiver/DB snaps are the actual assignment."},
            {"title": "Red-zone answers without improvisation",
             "body": f"{opp} will load the box in August too — the script has to have a throw and a run that do not need a miracle."},
        ]
        script = [
            f"Open with inside zone and a boot that matches it so the {opp_short} have to honor both.",
            "Get the nickel a live series against their slot — that is the Week 1 tell.",
            "Script one easy-offense screen before a third-and-long hero ball.",
            "Finish the half with a two-minute look; August nights still keep score.",
        ]
    elif ptype in ("regular", "postseason"):
        lede = (
            f"{opp} {loc} is the next real one. "
            + (f"Last week is closed ({last.get('opponent')}); this week is a new call sheet." if last else "")
        )
        how = (
            f"The matchup is a style question first: can Kansas City stay on schedule "
            f"against what {opp} does best, and can {t['defensive_coordinator']} make "
            f"{opp} play left-handed? "
            + (f"The market: {mkt}. " if mkt else "")
            + "Give them credit in the trenches and on early downs; mark KC edges only "
            "where the personnel actually says so."
        )
        keys = [
            {"title": f"Take away {opp}'s first answer",
             "body": "Whatever they want on early downs — the run, the shot, the glance — has to be the first paragraph of the plan."},
            {"title": "Make them tackle in space, then hit play-action",
             "body": "If the run is real, the boot and verts come off it. If it is not, it is third-and-forever again."},
            {"title": "Pressure picture vs. their protection",
             "body": f"Spagnuolo's simulated heat only works if the nickel and the dropper land where {opp} wants to throw the hot ball."},
            {"title": "Hidden yards",
             "body": "Field position and the two-minute / four-minute bits decide one-score games."},
        ]
        script = [
            f"15-play openers that test {opp}'s run fits, then a boot on the same look.",
            "Motion to declare coverage before the money down.",
            "One max-protect shot if they sit in two-high; one screen if they climb the pocket.",
            "A two-minute package that does not require a scramble drill.",
        ]
    else:
        lede = f"Everything camp is building still points at {opp} {loc}."
        how = (
            f"{opp} is the first real stress test of the new identity. If the run and "
            f"the nickel are not ready, {opp} will make Kansas City play the 2025 game. "
            + (f"For context, {mkt}." if mkt else "")
        )
        keys = [
            {"title": "Early-down identity under live bullets",
             "body": f"Inside zone and play-action have to survive {opp}'s front, not just a camp period."},
            {"title": "Mahomes' snap count as a game plan",
             "body": "Script easy offense so the knee is not the entire offense."},
            {"title": "Secondary vs. their skill people",
             "body": f"The slot battle is the tell for how aggressive KC can be against {opp}."},
        ]
        script = [
            "Open under center. Make the first six plays look like the identity, not a rescue.",
            f"Have a protection plan for {opp}'s best rusher before the first third down.",
            "Keep a two-high answer ready if the nickel is still a question.",
        ]

    return {
        "opponent": opp,
        "lede": lede.strip(),
        "howTheyMatch": how.strip(),
        "keys": keys,
        "script": script,
    }


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
        {"concept": "four_verts",
         "title": "Play-action four verts — the timing test",
         "situation": "2nd & 6 vs. single-high — the scheduled deep shot",
         "why": "The rehab ramp isn't real until the deep timing is: verts off a run "
                "fake test Mahomes' rhythm and the seam benders against the single-high "
                "looks Denver leans on, with the linebackers held by the fake.",
         "coaching": "Throw on schedule from the top of the drop — if the post safety "
                     "takes the seam, the ball comes out to the bender. No second "
                     "hitch; the knee never has to reset.",
         "labels": {"x": "boundary go", "z": "field go", "te": "seam/bender"}},
        {"concept": "zone_blitz",
         "title": "Spagnuolo simulated pressure",
         "situation": "3rd & 5+, obvious passing down",
         "why": "Shows heat, drops a lineman into a hook, and rushes the second level to "
                "confuse protection while the coverage stays sound behind it.",
         "coaching": "Disguise is the point: the picture pre-snap must look like man "
                     "pressure even when it's zone behind it.",
         "labels": {}},
        {"concept": "cover_two",
         "title": "Two-high insurance for a young secondary",
         "situation": "Shot-play downs while the nickel job is unsettled",
         "why": "Until the slot battle resolves, two deep halves cap the vertical game "
                "and let Chris Jones and the front four win the down — no freebies "
                "over first-year corners in September.",
         "coaching": "Corners sink hard and funnel inside; safeties overlap the post. "
                     "Give up the checkdown, rally and tackle — never the shot.",
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
        "lastGameReview": _last_game_review(signals, phase),
        "currentState": _current_state(signals, phase),
        "gamePlan": _game_plan(signals, phase, next_games),
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
    markets = signals.get("markets", {})
    mkt = _markets_note(markets)

    if ptype in ("regular", "postseason"):
        head = f"{ng_fmt.get('label','Next up')}: {t['abbr']} {ng_fmt.get('opponent','')}"
        lede = (
            f"Kansas City turns the page to {opp}. "
            + (f"{mkt}. " if mkt else "")
            + "Last game on the tape, the current state of the roster, then the "
            "game plan and matchup for who's next."
        )
        edition = phase.get("edition")
    elif ptype == "preseason":
        head = "Last tape, current roster, next dress rehearsal"
        lede = ("Review the last exhibition, name what still has to get cleaned up, "
                "then game-plan the next opponent — snaps, bubble, and how they match up.")
        edition = phase.get("edition")
    else:  # offseason
        head = "Offseason build: needs, additions, and the path to next season"
        lede = ("The roster story keeps moving. Here's where the Chiefs stand, what "
                "still needs answering, and how the pieces point toward the season ahead.")
        edition = phase.get("edition")

    xsandos = [
        {"concept": "inside_zone", "title": "Inside zone — stay on schedule",
         "situation": "1st down / 2nd & short",
         "why": "Early-down efficiency keeps the play-action and boot menu honest and "
                "the offense out of obvious passing downs — where opponents want it.",
         "coaching": "One cut, downhill — let the double-teams move the line and take "
                     "the four yards every time.",
         "labels": {}},
        {"concept": "play_action_boot", "title": "Under-center boot — the early-down answer",
         "situation": f"1st & 10 off a run look vs. {opp}",
         "why": "Moves the launch point away from the rush and gives a defined "
                "half-field read — the cheapest explosive in the menu once the run "
                "game has landed.",
         "coaching": "The fake has to match the run action on tape or the backside "
                     "end never bites.",
         "labels": {"x": "deep cross", "z": "flat", "te": "hinge-climb"}},
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
        {"concept": "zone_blitz", "title": "Simulated pressure on the money down",
         "situation": "3rd & 5-8, protection stressed",
         "why": "Five-man picture, four-man rush: force the hot throw into a capped "
                "window without selling out the coverage behind it.",
         "coaching": "The dropper must land in the throwing lane the protection "
                     "leaves open — that's where the hot ball goes.",
         "labels": {}},
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

    last_review = _last_game_review(signals, phase)
    state = _current_state(signals, phase)
    plan = _game_plan(signals, phase, next_games)

    review_block = []
    if last_review.get("lede"):
        review_block = [
            {"tag": "Recap", "title": "What the last tape said",
             "body": last_review["lede"]},
        ]

    story_body = [
        "This edition runs the weekly desk in order: last game, current state, next opponent.",
    ]
    if last_review.get("analysis"):
        story_body.append(last_review["analysis"][0])
    if state.get("lede"):
        story_body.append(state["lede"])
    if plan.get("howTheyMatch"):
        story_body.append(plan["howTheyMatch"])

    return {
        "edition": edition,
        "headline": head,
        "dek": "Last game on the tape, where the Chiefs stand, and the plan for who's next.",
        "videoHook": f"Let's get into it — {t['abbr']}: the last game, where we stand, and {opp}.",
        "theEdge": (f"Model/market read: {mkt}." if mkt else
                    "The margins are in the trenches and on early downs."),
        "storyline": {"lede": lede, "body": story_body},
        "lastGameReview": last_review,
        "currentState": state,
        "gamePlan": plan,
        "nextGame": {**ng_fmt, "note": plan.get("lede") or "The next chapter of the season."},
        "spotlight": (review_block + [
            {"tag": "Preview", "title": f"The {opp} problem",
             "body": plan.get("howTheyMatch") or "What the opponent does best, and the Chiefs' plan to take it away."},
            {"tag": "Key", "title": "The swing matchup",
             "body": (plan.get("keys") or [{}])[0].get("body") or "The one-on-one that most likely decides the outcome."},
            {"tag": "State", "title": "What has to get fixed",
             "body": (state.get("workOn") or [{}])[0].get("body") or "The work list after the last tape."},
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
            {"segment": "Cold open — last game, then the next one", "length": "0:00–1:15",
             "talkTrack": f"Open on the last result, then pivot to {opp}."},
            {"segment": "Last-game tape", "length": "1:15–5:00",
             "talkTrack": "What worked, what didn't, and the two or three plays that created the final."},
            {"segment": "Current state — the work list", "length": "5:00–7:30",
             "talkTrack": "Where the roster stands and what has to be fixed before kickoff."},
            {"segment": "Next-game plan & matchups", "length": "7:30–11:00",
             "talkTrack": f"How KC matches up with {opp} and the script for early downs."},
            {"segment": "X&O film room", "length": "11:00–14:00",
             "talkTrack": "Break down the diagrams on screen."},
            {"segment": "Numbers + fan debates", "length": "14:00–16:00",
             "talkTrack": "Model, Vegas, and the comment-section arguments."},
        ],
        "sources": _sources_from_news(signals.get("news", [])),
    }


def write(signals: dict, phase: dict, next_games: list[dict]) -> dict:
    if phase.get("type") == "training-camp":
        return _camp(signals, phase, next_games)
    return _generic(signals, phase, next_games)
