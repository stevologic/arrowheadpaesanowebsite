"""Prompt construction for the LLM writer path.

The prompt is heavily grounded: we hand the model the live schedule, the real
news wire (with URLs), the season phase, and the *exact* set of diagram concepts
it is allowed to reference. It is told to cite only the supplied sources or
widely-known facts, and to return a single JSON object matching the schema.
"""
from __future__ import annotations

import json

from . import config, diagrams


SYSTEM_PROMPT = """\
You are the lead NFL analyst and showrunner for "Arrowhead Paesano," an \
independent Kansas City Chiefs fan channel and website. Your voice is sharp, \
football-literate, and honest — film-room smart but never dry. You write for \
diehard Chiefs fans who want real X's-and-O's, matchup edges, and a clear story \
that always looks ahead to the next Sunday.

You produce ONE structured "Chiefs Narrative" edition. It must be:
- Grounded ONLY in the facts provided (schedule + last-game recap + news wire) \
plus widely-known, durable NFL knowledge. Never invent injuries, transactions, \
scores, or quotes.
- Specific and analytical: name players, coaches, concepts, and leverage points.
- Balanced: give the opponent real credit; mark honest edges.
- Built as a three-act desk: (1) review the last game with real analysis, \
(2) say where the Chiefs stand and what they must work on or think about, \
(3) look ahead to the next game with a game plan and a matchup read.

Cite sources using ONLY the provided news items (match the publisher + url). If \
a claim is general football knowledge, you do not need a citation. Do not \
fabricate URLs.

Return a SINGLE JSON object. No markdown, no prose outside the JSON.\
"""


def _schedule_brief(schedule: list[dict], next_games: list[dict]) -> str:
    def line(g):
        loc = "vs" if g.get("homeAway") == "home" else "@"
        wk = g.get("week")
        date = (g.get("date") or "")[:10]
        res = ""
        if g.get("completed") and g.get("kcScore") is not None:
            res = f" [final KC {g['kcScore']}-{g['oppScore']}]"
        tv = f" ({g['tv']})" if g.get("tv") else ""
        return f"  {g.get('seasonType','reg')} wk{wk} {date} KC {loc} {g.get('opponent')}{tv}{res}"

    lines = ["FULL 2026 SCHEDULE:"]
    lines += [line(g) for g in schedule]
    lines.append("\nNEXT UP:")
    lines += [line(g) for g in next_games] or ["  (no upcoming games — offseason/camp)"]
    return "\n".join(lines)


def _last_game_brief(signals: dict, phase: dict) -> str:
    game = (phase or {}).get("lastGame")
    if not game:
        return "LAST GAME: none on the current slate yet — skip lastGameReview."
    loc = "vs" if game.get("homeAway") == "home" else "@"
    score = ""
    if game.get("kcScore") is not None:
        score = f" [final KC {game['kcScore']}-{game['oppScore']}]"
    lines = [
        "LAST GAME (review this with real analysis; do not invent a different score):",
        f"  {game.get('seasonType','reg')} wk{game.get('week')} "
        f"{(game.get('date') or '')[:10]} KC {loc} {game.get('opponent')}"
        f"{score} at {game.get('venue') or '—'}",
    ]
    recap = (signals or {}).get("lastGameRecap") or {}
    kc = recap.get("kc") or {}
    opp = recap.get("opp") or {}
    if kc or opp:
        lines.append("  BOX (ESPN, cite as ESPN):")
        if kc:
            lines.append("    KC: " + ", ".join(f"{k}={v}" for k, v in kc.items() if v))
        if opp:
            label = recap.get("oppAbbr") or "OPP"
            lines.append(f"    {label}: " + ", ".join(f"{k}={v}" for k, v in opp.items() if v))
    for play in recap.get("scoring") or []:
        lines.append(f"  score: {play}")
    for leader in recap.get("leaders") or []:
        lines.append(
            f"  KC leader: {leader.get('player')} — {leader.get('category')} "
            f"({leader.get('value')})"
        )
    return "\n".join(lines)


def _news_brief(news: list[dict]) -> str:
    if not news:
        return "NEWS WIRE: (empty — rely on durable knowledge and cite nothing)"
    lines = ["NEWS WIRE (cite these by publisher + url):"]
    for i, n in enumerate(news, 1):
        lines.append(
            f"  [{i}] {n['publisher']}: {n['title']}"
            + (f"\n      {n['summary']}" if n.get("summary") else "")
            + f"\n      url: {n.get('url','')}"
        )
    return "\n".join(lines)


def _concept_menu() -> str:
    lines = ["ALLOWED DIAGRAM CONCEPTS (use the exact key):"]
    for key, spec in diagrams.CONCEPTS.items():
        lines.append(f'  "{key}" — {spec["title"]} ({spec["side"]}): {spec["blurb"]}')
    return "\n".join(lines)


def _schema_hint(phase: dict) -> str:
    return json.dumps(
        {
            "edition": "string — e.g. '2026 Training Camp · Vol. 3' or '2026 Week 5 Preview'",
            "record": config.TEAM["last_season_record"] + " or current record",
            "headline": "punchy edition headline",
            "dek": "one-sentence standfirst",
            "videoHook": "spoken cold-open line for the YouTube episode",
            "theEdge": "one-sentence thesis on where the season is trending",
            "storyline": {
                "lede": "the evolving story, one strong paragraph",
                "body": ["2-4 more paragraphs building the arc, looking ahead"],
            },
            "lastGameReview": {
                "opponent": "last opponent",
                "label": "Week N · Day Mon DD",
                "result": "W | L | T",
                "score": "KC 24–17",
                "lede": "one-paragraph recap of what actually happened",
                "analysis": [
                    "2-4 paragraphs of film-room analysis — not a box-score recitation"
                ],
                "takeaways": [{"title": "takeaway", "body": "2-3 sentences"}],
                "whatWorked": ["concrete things that held up"],
                "whatDidnt": ["concrete things that broke or lagged"],
            },
            "currentState": {
                "lede": "where the Chiefs are right now after that result",
                "record": "current relevant record",
                "workOn": [
                    {"title": "fix or install", "body": "why it matters this week"}
                ],
                "thinkAbout": [
                    {"title": "question / debate", "body": "what the staff and fans should weigh"}
                ],
            },
            "gamePlan": {
                "opponent": "next opponent",
                "lede": "the assignment and the stakes",
                "howTheyMatch": "how the two teams match up — styles, personnel, leverage",
                "keys": [
                    {"title": "game-plan key", "body": "how KC attacks or takes it away"}
                ],
                "script": ["3-6 concrete calls / adjustments for next Sunday"],
            },
            "nextGame": {
                "label": "Week N · Day Mon DD",
                "opponent": "Team",
                "at": "venue or '@ City'",
                "tv": "network",
                "note": "one line of stakes",
            },
            "spotlight": [
                {"tag": "short label", "title": "storyline title", "body": "2-3 sentences"}
            ],
            "matchups": [
                {
                    "unit": "e.g. Chiefs pass rush vs. Broncos OL",
                    "chiefs": "KC side",
                    "opponent": "opp side",
                    "edge": "KC | OPP | PUSH",
                    "note": "why",
                }
            ],
            "xsandos": [
                {
                    "title": "play/scheme title",
                    "situation": "down/distance/context vs. THIS opponent",
                    "concept": "one allowed concept key — each of the 6 cards uses a different key",
                    "why": "why this call wins NOW — tie it to named players, current "
                           "injuries/availability, personnel battles, and the matchup "
                           "edges in this edition",
                    "coaching": "the film-room coaching point, specific to the players "
                                "who will execute it",
                    "labels": {"x": "route", "z": "route", "te": "route"},
                }
            ],
            "coaching": [{"topic": "coaching matchup/staff note", "detail": "1-2 sentences"}],
            "injuries": [
                {"player": "name", "status": "e.g. PUP/Questionable/Out", "note": "context", "source": "publisher"}
            ],
            "personnel": [{"move": "roster move/battle", "detail": "1-2 sentences"}],
            "strategies": ["3-6 concrete strategies KC could deploy"],
            "debates": ["3-5 fan/viewer debate questions"],
            "runOfShow": [
                {"segment": "episode segment", "length": "e.g. 0:00-1:30", "talkTrack": "what to say"}
            ],
            "sources": [
                {"title": "headline", "publisher": "name", "url": "url", "note": "why it matters"}
            ],
        },
        indent=2,
    )


def _markets_brief(markets: dict) -> str:
    if not markets:
        return ""
    lines = ["MARKETS & PREDICTIONS (reference these; cite the named source):"]
    model = markets.get("model")
    if model:
        lines.append(
            f"  Model: {model['label']} has KC {model['kcWin']}% to win the next game"
            + (f" (opp {model['oppWin']}%)" if model.get("oppWin") else "")
            + f" — source {model['source']}."
        )
    vegas = markets.get("vegas")
    if vegas:
        lines.append(
            f"  Vegas ({vegas.get('provider','book')}): {vegas.get('spreadDetail','')}, "
            f"O/U {vegas.get('overUnder')}, KC ML per book — source {vegas['source']}."
        )
    for fut in markets.get("futures", []) or []:
        lines.append(
            f"  Prediction market: {fut['label']} — Chiefs {fut['chiefsPct']}% (Polymarket)."
        )
    lines.append(
        "  Use these to frame expectations honestly (favored/underdog, value vs. narrative). "
        "Do not overstate certainty."
    )
    return "\n".join(lines)


def build_user_prompt(signals: dict, phase: dict, next_games: list[dict]) -> str:
    team = config.TEAM
    facts = (
        f"TEAM FACTS: {team['name']} — HC {team['head_coach']}, OC "
        f"{team['offensive_coordinator']}, DC {team['defensive_coordinator']}, "
        f"QB {team['quarterback']}. 2025 record {team['last_season_record']}. "
        f"Camp at {team['camp_site']}. Home: {team['stadium']}."
    )
    phase_line = (
        f"SEASON PHASE: {phase.get('label')} (type={phase.get('type')}, "
        f"mode={phase.get('mode')}, week={phase.get('week')}). "
        f"Frame the edition for this phase and always look ahead."
    )
    guidance = {
        "training-camp": "Focus on camp battles, Mahomes' rehab ramp, install/scheme "
        "identity, rookies who must play early, and what a strong camp unlocks for Week 1. "
        "If a preseason or prior game is on the slate, review it before looking ahead.",
        "preseason": "Treat preseason as a dress rehearsal: review the last result, "
        "name the roster-bubble and install issues it exposed, then game-plan the next "
        "exhibition and how that opponent matches up.",
        "regular": "Open with a real last-game film review, then the current state of "
        "the season (what to fix), then a full next-game game plan and matchup thesis.",
        "postseason": "Raise the stakes: review the last playoff/regular result, the "
        "current state of the roster, then the next opponent's game plan and matchups.",
        "offseason": "Advance the roster-building story: what last season/last game "
        "proved, what still needs answering, and how the pieces point toward the opener.",
    }.get(phase.get("type"), "Advance the Chiefs story and look ahead.")

    return "\n\n".join(
        [
            facts,
            phase_line,
            "EDITORIAL GUIDANCE: " + guidance,
            _last_game_brief(signals, phase),
            _schedule_brief(signals.get("schedule", []), next_games),
            _markets_brief(signals.get("markets", {})),
            _news_brief(signals.get("news", [])),
            _concept_menu(),
            "Return JSON with EXACTLY these keys (values are hints, replace them):\n"
            + _schema_hint(phase),
            "Rules: Always fill lastGameReview (unless LAST GAME says none), "
            "currentState, and gamePlan with specific, non-generic analysis. "
            "EXACTLY 6 xsandos cards — four offense, two defense — each "
            "using a DIFFERENT allowed concept key. Every card must earn its "
            "place in this edition: ground the situation, 'why', and coaching "
            "point in the current facts above — the upcoming opponent, injuries "
            "and availability, personnel/camp battles, coaching tendencies, and "
            "the strengths and weaknesses driving your matchup edges — and name "
            "the specific players or coaches involved. No generic filler that "
            "could run any week. 4-6 matchups. "
            "5-8 spotlight/strategies/debates. Every injuries[].source and every "
            "sources[] entry must correspond to a provided news item or be omitted. "
            "Output ONLY the JSON object.",
        ]
    )
