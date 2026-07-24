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
- Grounded ONLY in the facts provided (schedule + news wire) plus widely-known, \
durable NFL knowledge. Never invent injuries, transactions, scores, or quotes.
- Specific and analytical: name players, coaches, concepts, and leverage points.
- Balanced: give the opponent real credit; mark honest edges.
- Forward-looking: the through-line is "what it means for the games ahead."

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
                    "situation": "down/distance/context",
                    "concept": "one allowed concept key",
                    "why": "why it works here",
                    "coaching": "the coaching point",
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
        "identity, rookies who must play early, and what a strong camp unlocks for Week 1.",
        "preseason": "Treat preseason as a dress rehearsal: starters' snaps, roster "
        "bubble battles, and what to watch before the opener.",
        "regular": "Build a full game-week preview: opponent tendencies, matchup edges, "
        "the X&O plan, injuries, and a prediction-shaped thesis.",
        "postseason": "Raise the stakes: playoff matchup, adjustments, and championship-level margins.",
        "offseason": "Advance the roster-building story: needs, additions, and the path to next season.",
    }.get(phase.get("type"), "Advance the Chiefs story and look ahead.")

    return "\n\n".join(
        [
            facts,
            phase_line,
            "EDITORIAL GUIDANCE: " + guidance,
            _schedule_brief(signals.get("schedule", []), next_games),
            _markets_brief(signals.get("markets", {})),
            _news_brief(signals.get("news", [])),
            _concept_menu(),
            "Return JSON with EXACTLY these keys (values are hints, replace them):\n"
            + _schema_hint(phase),
            "Rules: 3-4 xsandos max (mix offense + defense). 4-6 matchups. "
            "5-8 spotlight/strategies/debates. Every injuries[].source and every "
            "sources[] entry must correspond to a provided news item or be omitted. "
            "Output ONLY the JSON object.",
        ]
    )
