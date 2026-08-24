"""CI gates for the Chiefs Narrative engine.

Everything here runs offline — no network, no API keys — so it is a stable
merge gate. Run with:  python -m unittest discover -s tools/tests -v
"""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from tools.chiefs_narrative import (
    collect,
    diagrams,
    generate,
    odds,
    offline,
    phase,
    prompts,
    providers,
    schema,
)

# Canned inputs: the writers only use .get() lookups, so minimal dicts work.
CAMP_PHASE = {"type": "training-camp", "label": "Training Camp",
              "mode": "camp", "edition": "Test Camp Edition"}
WEEK_PHASE = {"type": "regular", "label": "Week 5", "mode": "preview",
              "edition": "Test Week Edition"}
SIGNALS = {"news": [], "markets": {}, "schedule": []}
NEXT = [{"opponent": "Denver Broncos", "homeAway": "home", "week": 1}]


class SixCardGuarantee(unittest.TestCase):
    """The GitHub Action refresh must always yield exactly six X&O cards."""

    def assert_six(self, cards):
        self.assertEqual(len(cards), 6)
        concepts = [c["concept"] for c in cards]
        self.assertEqual(len(set(concepts)), 6, f"repeated concepts: {concepts}")
        sides = [diagrams.CONCEPTS[c]["side"] for c in concepts]
        self.assertEqual(sides.count("offense"), 4)
        self.assertEqual(sides.count("defense"), 2)
        for c in cards:
            for field in ("title", "situation", "why", "coaching"):
                self.assertTrue(c.get(field), f"card missing {field}: {c}")

    def test_offline_camp_writer_emits_six(self):
        self.assert_six(offline.write(SIGNALS, CAMP_PHASE, NEXT)["xsandos"])

    def test_offline_week_writer_emits_six(self):
        self.assert_six(offline.write(SIGNALS, WEEK_PHASE, NEXT)["xsandos"])

    def test_schema_caps_at_six_and_clamps_concepts(self):
        raw = [{"title": f"t{i}", "situation": "s", "concept": "not-a-concept",
                "why": "w", "coaching": "c", "labels": {}} for i in range(9)]
        out = schema._norm_xsandos(raw)
        self.assertEqual(len(out), 6)
        for card in out:
            self.assertIn(card["concept"], diagrams.CONCEPTS)

    def test_top_up_restores_six_when_writer_under_delivers(self):
        narrative = schema.normalize(
            offline.write(SIGNALS, CAMP_PHASE, NEXT), phase=CAMP_PHASE,
            meta={"generatedAt": "2026-01-01T00:00:00+00:00",
                  "generator": "test", "record": "0-0", "markets": {}},
        )
        narrative["xsandos"] = narrative["xsandos"][:2]  # simulate a short LLM reply
        generate._ensure_six_xsandos(narrative, SIGNALS, CAMP_PHASE, NEXT)
        self.assert_six(narrative["xsandos"])

    def test_prompt_demands_exactly_six_grounded_cards(self):
        text = prompts.build_user_prompt(SIGNALS, CAMP_PHASE, NEXT)
        self.assertIn("EXACTLY 6 xsandos", text)
        for hint in ("injuries", "personnel", "coaching", "matchup"):
            self.assertIn(hint, text)
        self.assertIn("lastGameReview", text)
        self.assertIn("currentState", text)
        self.assertIn("gamePlan", text)
        self.assertIn("LAST GAME:", text)


class DeskSections(unittest.TestCase):
    """Last-game review, current state, and next-game plan are first-class."""

    LAST = {
        "id": "401873296",
        "week": 3,
        "seasonType": "pre",
        "date": "2026-08-22T23:30:00Z",
        "opponent": "Tampa Bay Buccaneers",
        "opponentAbbr": "TB",
        "opponentShort": "Buccaneers",
        "homeAway": "away",
        "venue": "Raymond James Stadium",
        "completed": True,
        "kcScore": 15,
        "oppScore": 16,
        "kickoff": "Sat, Aug 22 · 6:30 PM CT",
    }
    PHASE = {
        "type": "preseason",
        "label": "Preseason",
        "mode": "preview",
        "edition": "2026 Preseason",
        "lastGame": LAST,
        "nextGame": {
            "opponent": "Seattle Seahawks",
            "homeAway": "home",
            "week": 4,
            "venue": "Arrowhead Stadium",
            "seasonType": "pre",
        },
    }

    def test_offline_week_writer_emits_three_act_desk(self):
        signals = {
            "news": [
                {
                    "title": "Chiefs vs. Buccaneers Final Score: Chiefs lose 16-15",
                    "summary": "Kansas City fell 16-15 in Tampa.",
                    "publisher": "Arrowhead Pride",
                    "url": "https://example.com/tb",
                }
            ],
            "markets": {},
            "schedule": [self.LAST],
            "lastGameRecap": {
                "kc": {"totalYards": "280", "turnovers": "1"},
                "opp": {"totalYards": "310", "turnovers": "0"},
                "oppAbbr": "TB",
                "scoring": ["Q4 0:12 — TB 29 Yd Field Goal"],
                "leaders": [],
            },
        }
        raw = offline.write(
            signals, self.PHASE, [{"opponent": "Seattle Seahawks", "homeAway": "home"}]
        )
        review = raw["lastGameReview"]
        self.assertEqual(review["result"], "L")
        self.assertIn("15", review["score"])
        self.assertIn("Tampa", review["lede"])
        self.assertGreaterEqual(len(review["analysis"]), 2)
        self.assertTrue(review["whatWorked"])
        self.assertTrue(review["whatDidnt"])
        state = raw["currentState"]
        self.assertTrue(state["lede"])
        self.assertTrue(state["workOn"])
        self.assertTrue(state["thinkAbout"])
        plan = raw["gamePlan"]
        self.assertIn("Seattle", plan["opponent"] + plan["lede"] + plan["howTheyMatch"])
        self.assertTrue(plan["keys"])
        self.assertTrue(plan["script"])

    def test_schema_keeps_desk_and_drops_empty(self):
        raw = offline.write(
            {"news": [], "markets": {}, "schedule": [self.LAST]},
            self.PHASE,
            NEXT,
        )
        narrative = schema.normalize(
            raw,
            phase=self.PHASE,
            meta={"generatedAt": "2026-08-24T00:00:00+00:00", "generator": "test",
                  "record": "6-11", "markets": {}},
        )
        self.assertEqual(narrative["lastGameReview"]["result"], "L")
        self.assertTrue(narrative["currentState"]["workOn"])
        self.assertTrue(narrative["gamePlan"]["howTheyMatch"])
        empty = schema.normalize(
            {"headline": "x"},
            phase={"type": "offseason", "label": "Offseason", "mode": "offseason"},
            meta={"generatedAt": "2026-01-01T00:00:00+00:00", "generator": "test"},
        )
        self.assertEqual(empty["lastGameReview"], {})
        self.assertEqual(empty["currentState"], {})
        self.assertEqual(empty["gamePlan"], {})

    def test_ensure_desk_fills_skipped_writer_sections(self):
        narrative = schema.normalize(
            {"headline": "thin"},
            phase=self.PHASE,
            meta={"generatedAt": "2026-08-24T00:00:00+00:00", "generator": "test"},
        )
        generate._ensure_desk_sections(
            narrative,
            {"news": [], "markets": {}, "schedule": [self.LAST]},
            self.PHASE,
            NEXT,
        )
        self.assertTrue(narrative["lastGameReview"]["lede"])
        self.assertEqual(narrative["lastGameReview"]["score"], "KC 15–16")
        self.assertTrue(narrative["currentState"]["lede"])
        self.assertTrue(narrative["gamePlan"]["lede"])

    def test_slate_record_and_game_result(self):
        self.assertEqual(phase.slate_record([self.LAST], "pre"), "0-1")
        self.assertEqual(phase.game_result(self.LAST)["result"], "L")
        card = phase.format_last_game(self.LAST)
        self.assertEqual(card["result"], "L")
        self.assertIn("Buccaneers", card["opponent"])

    def test_prompt_includes_last_game_box(self):
        phase_with_last = dict(CAMP_PHASE)
        phase_with_last["lastGame"] = self.LAST
        text = prompts.build_user_prompt(
            {
                "news": [],
                "markets": {},
                "schedule": [],
                "lastGameRecap": {
                    "kc": {"totalYards": "280"},
                    "opp": {"totalYards": "310"},
                    "oppAbbr": "TB",
                    "scoring": [],
                    "leaders": [],
                },
            },
            phase_with_last,
            NEXT,
        )
        self.assertIn("Tampa Bay Buccaneers", text)
        self.assertIn("final KC 15-16", text)
        self.assertIn("totalYards=280", text)

    def test_fetch_game_recap_reads_espn_summary(self):
        payload = {
            "boxscore": {
                "teams": [
                    {
                        "team": {"abbreviation": "KC"},
                        "statistics": [
                            {"name": "totalYards", "displayValue": "280"},
                            {"name": "turnovers", "displayValue": "1"},
                        ],
                    },
                    {
                        "team": {"abbreviation": "TB"},
                        "statistics": [
                            {"name": "totalYards", "displayValue": "310"},
                        ],
                    },
                ]
            },
            "scoringPlays": [
                {
                    "team": {"abbreviation": "TB"},
                    "text": "29 Yd Field Goal",
                    "period": {"number": 4},
                    "clock": {"displayValue": "0:12"},
                }
            ],
            "leaders": [
                {
                    "team": {"abbreviation": "KC"},
                    "leaders": [
                        {
                            "displayName": "Passing Yards",
                            "leaders": [
                                {
                                    "athlete": {"displayName": "Patrick Mahomes"},
                                    "displayValue": "12/18, 140 YDS",
                                }
                            ],
                        }
                    ],
                }
            ],
        }
        with patch.object(collect, "_get_json", return_value=payload):
            recap = collect.fetch_game_recap("401873296")
        self.assertEqual(recap["kc"]["totalYards"], "280")
        self.assertEqual(recap["oppAbbr"], "TB")
        self.assertTrue(recap["scoring"][0].startswith("Q4"))
        self.assertEqual(recap["leaders"][0]["player"], "Patrick Mahomes")

    def test_fetch_game_recap_empty_on_blank_payload(self):
        with patch.object(collect, "_get_json", return_value={"boxscore": {}}):
            self.assertEqual(collect.fetch_game_recap("1"), {})
        self.assertEqual(collect.fetch_game_recap(""), {})


class Diagrams(unittest.TestCase):
    def test_every_concept_renders_valid_svg(self):
        with tempfile.TemporaryDirectory() as tmp:
            for key in diagrams.concept_keys():
                info = diagrams.write_diagram(tmp, f"xo-{key}", key)
                svg = (Path(tmp) / f"xo-{key}.svg").read_text(encoding="utf-8")
                self.assertTrue(svg.startswith("<svg"))
                self.assertTrue(svg.rstrip().endswith("</svg>"))
                self.assertEqual(info["side"], diagrams.CONCEPTS[key]["side"])


class EditionSlugs(unittest.TestCase):
    def test_slug_is_derived_from_timestamp(self):
        slug = generate._edition_slug({"generatedAt": "2026-07-25T19:30:00+00:00"})
        self.assertEqual(slug, "2026-07-25-1930")


class GrokModelSelection(unittest.TestCase):
    """GROK_MODEL (or XAI_MODEL) must resolve to grok-4.6 unless overridden."""

    def test_default_is_grok_4_6(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(providers.GROK_DEFAULT_MODEL, "grok-4.6")
            self.assertEqual(providers.grok_model(), "grok-4.6")

    def test_grok_model_env_wins(self):
        with patch.dict("os.environ", {"GROK_MODEL": "grok-4.6", "XAI_MODEL": "ignored"}, clear=True):
            self.assertEqual(providers.grok_model(), "grok-4.6")

    def test_xai_model_used_when_grok_model_unset(self):
        with patch.dict("os.environ", {"XAI_MODEL": "grok-4.6"}, clear=True):
            self.assertEqual(providers.grok_model(), "grok-4.6")

    def test_blank_grok_model_falls_back_to_default(self):
        with patch.dict("os.environ", {"GROK_MODEL": "  ", "XAI_MODEL": ""}, clear=True):
            self.assertEqual(providers.grok_model(), "grok-4.6")

    def test_workflow_passes_repository_variable(self):
        yaml = (Path(__file__).resolve().parents[2] / ".github" / "workflows" / "narrative.yml").read_text(encoding="utf-8")
        self.assertIn("GROK_MODEL: ${{ vars.GROK_MODEL }}", yaml)

    def test_workflow_refreshes_slate_beyond_narrative_text(self):
        yaml = (Path(__file__).resolve().parents[2] / ".github" / "workflows" / "narrative.yml").read_text(encoding="utf-8")
        self.assertIn("10 11 * * *", yaml)
        self.assertIn("20 4 * * *", yaml)
        self.assertIn("20 7 * * 0,1,2", yaml)
        self.assertIn("--schedule-only", yaml)
        self.assertIn("python -m tools.chiefs_narrative.generate --schedule-only", yaml)
        self.assertIn("Chiefs schedule: refresh 2026 slate", yaml)
        self.assertNotIn("*/15", yaml)


def _espn_event(
    event_id,
    date,
    abbr,
    season_type,
    week,
    home=True,
    venue="GEHA Field at Arrowhead Stadium",
    kc_score=None,
    opp_score=None,
    completed=False,
    state="pre",
):
    kc = {
        "homeAway": "home" if home else "away",
        "team": {"abbreviation": "KC", "displayName": "Kansas City Chiefs", "shortDisplayName": "Chiefs"},
        "score": kc_score,
    }
    opp = {
        "homeAway": "away" if home else "home",
        "team": {"abbreviation": abbr, "displayName": f"{abbr} Team", "shortDisplayName": abbr, "name": abbr},
        "score": opp_score,
    }
    return {
        "id": event_id,
        "date": date,
        "week": {"number": week},
        "seasonType": {"abbreviation": season_type},
        "competitions": [{
            "competitors": [kc, opp],
            "venue": {"fullName": venue},
            "broadcasts": [{"names": ["NFL Network"], "media": {"shortName": "NFLN"}}],
            "status": {"type": {"completed": completed, "state": state}},
        }],
    }


class SeasonClock(unittest.TestCase):
    """August 2026 is still camp/preseason. An ESPN outage must not wipe the slate."""

    RAMS = {
        "id": "401873283",
        "week": 2,
        "seasonType": "pre",
        "date": "2026-08-15T20:00Z",
        "opponent": "Los Angeles Rams",
        "homeAway": "home",
        "venue": "GEHA Field at Arrowhead Stadium",
        "tv": "NFLN",
        "completed": False,
    }
    DEN = {
        "id": "401872931",
        "week": 1,
        "seasonType": "reg",
        "date": "2026-09-15T00:15Z",
        "opponent": "Denver Broncos",
        "homeAway": "home",
        "venue": "GEHA Field at Arrowhead Stadium",
        "completed": False,
    }

    def test_mid_august_with_preseason_is_preseason(self):
        now = datetime(2026, 8, 12, 18, 0, tzinfo=timezone.utc)
        ph = phase.detect([self.RAMS, self.DEN], now=now)
        self.assertEqual(ph["type"], "preseason")
        self.assertEqual(ph["nextGame"]["opponent"], "Los Angeles Rams")

    def test_empty_schedule_in_august_is_still_camp(self):
        now = datetime(2026, 8, 12, 18, 0, tzinfo=timezone.utc)
        ph = phase.detect([], now=now)
        self.assertEqual(ph["type"], "training-camp")

    def test_june_without_games_is_offseason(self):
        now = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
        ph = phase.detect([], now=now)
        self.assertEqual(ph["type"], "offseason")

    def test_format_next_game_uses_central_kickoff(self):
        game = dict(self.RAMS)
        game["kickoff"] = collect.kickoff_label(game["date"])
        card = phase.format_next_game(game)
        self.assertEqual(card["opponent"], "Los Angeles Rams")
        self.assertIn("Preseason Week 2", card["label"])
        self.assertIn("Aug 15", card["label"])
        self.assertEqual(card["at"], "GEHA Field at Arrowhead Stadium")

    def test_monday_night_kickoff_stays_monday_in_central(self):
        label = collect.kickoff_label("2026-09-15T00:15Z")
        self.assertIn("Sep 14", label)
        self.assertIn("7:15 PM", label)

    def test_espn_dates_gain_seconds_for_hugo(self):
        self.assertEqual(collect.normalize_iso("2026-08-15T20:00Z"), "2026-08-15T20:00:00Z")

    def test_fetch_schedule_hits_espn_web_api_for_each_season_type(self):
        seen = []

        def fake(url):
            seen.append(url)
            return {"events": []}

        with patch.object(collect, "_get_json", side_effect=fake):
            collect.fetch_schedule(2026)
        joined = "\n".join(seen)
        self.assertIn("site.web.api.espn.com", joined)
        self.assertIn("seasontype=1", joined)
        self.assertIn("seasontype=2", joined)
        self.assertIn("seasontype=3", joined)
        self.assertNotIn("site.api.espn.com/apis", joined)

    def test_fetch_schedule_merges_preseason_and_regular(self):
        def fake(url):
            if "seasontype=1" in url:
                return {"events": [_espn_event("p1", "2026-08-15T20:00Z", "LAR", "pre", 2)]}
            if "seasontype=2" in url:
                return {"events": [_espn_event("r1", "2026-09-15T00:15Z", "DEN", "reg", 1)]}
            return {"events": []}

        with patch.object(collect, "_get_json", side_effect=fake):
            games = collect.fetch_schedule(2026)
        self.assertEqual([g["seasonType"] for g in games], ["pre", "reg"])
        self.assertEqual(games[0]["opponentAbbr"], "LAR")
        self.assertEqual(games[1]["opponentAbbr"], "DEN")
        self.assertEqual(games[0]["tv"], "NFLN")
        self.assertIsNone(games[0]["kcScore"])
        self.assertFalse(games[0]["completed"])

    def test_resolve_schedule_falls_back_to_checked_in_slate(self):
        cached = [dict(self.DEN)]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "schedule.json"
            path.write_text(json.dumps(cached), encoding="utf-8")
            with patch.object(collect.config, "SCHEDULE_JSON", path), \
                 patch.object(collect, "fetch_schedule", return_value=[]):
                games = collect.resolve_schedule()
        self.assertEqual(games[0]["opponent"], "Denver Broncos")
        self.assertTrue(games[0].get("kickoff"))

    def test_odds_summary_uses_espn_web_api(self):
        self.assertIn("site.web.api.espn.com", odds.ESPN_SUMMARY)

    def test_implied_win_pct_from_favorite_moneyline(self):
        self.assertEqual(odds.implied_win_pct(-135), 57.4)
        self.assertEqual(odds.implied_win_pct(114), 46.7)

    def test_collect_markets_builds_upcoming_game_card(self):
        next_game = {
            "id": "401873283",
            "opponent": "Los Angeles Rams",
            "homeAway": "home",
            "kickoff": "Sat, Aug 15 · 3:00 PM CT",
        }
        pred = {
            "model": None,
            "vegas": {
                "spreadDetail": "KC -2.5",
                "overUnder": 36.5,
                "homeMoneyline": -135,
                "awayMoneyline": 114,
                "source": "ESPN / Draft Kings",
            },
        }
        with patch.object(odds, "fetch_game_prediction", return_value=pred), \
             patch.object(odds, "fetch_polymarket_futures", return_value=[]), \
             patch.object(odds, "fetch_odds_api_consensus", return_value=None):
            markets = odds.collect_markets(next_game)
        game = markets["game"]
        self.assertEqual(game["opponent"], "Los Angeles Rams")
        self.assertEqual(game["spreadDetail"], "KC -2.5")
        self.assertEqual(game["kcWin"], 57.4)
        self.assertIn("implied", game["source"].lower())

    def test_writer_skipping_next_game_is_filled_from_schedule(self):
        narrative = schema.normalize(
            {"headline": "Camp", "nextGame": {}},
            phase={"type": "preseason", "label": "Preseason", "mode": "preview", "edition": "Pre"},
            meta={"generatedAt": "2026-08-12T00:00:00+00:00", "generator": "test", "record": "6-11"},
        )
        generate._ensure_next_game(narrative, {"nextGame": self.RAMS})
        self.assertEqual(narrative["nextGame"]["opponent"], "Los Angeles Rams")
        self.assertIn("Preseason", narrative["nextGame"]["label"])

    def test_write_wire_publishes_named_headlines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wire.json"
            with patch.object(generate.config, "WIRE_JSON", path):
                generate._write_wire([
                    {"title": "Spags talks tackling", "url": "https://example.com/a", "publisher": "Arrowhead Pride"},
                    {"title": "missing url", "publisher": "Nope"},
                ])
            payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(payload["headlines"]), 1)
        self.assertEqual(payload["headlines"][0]["publisher"], "Arrowhead Pride")
        self.assertTrue(payload["updatedAt"])

    def test_checked_in_slate_is_hugo_parseable(self):
        games = json.loads(
            (Path(__file__).resolve().parents[2] / "data" / "schedule_2026.json").read_text(encoding="utf-8")
        )
        self.assertGreaterEqual(len(games), 17)
        self.assertEqual(games[0]["seasonType"], "pre")
        self.assertEqual(games[0]["opponentAbbr"], "LAR")
        for game in games:
            self.assertRegex(game["date"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
            self.assertTrue(game.get("kickoff"))

    def test_templates_render_slate_and_wire(self):
        root = Path(__file__).resolve().parents[2]
        index = (root / "layouts" / "index.html").read_text(encoding="utf-8")
        self.assertIn("Read the latest Narrative", index)
        self.assertIn("press-hero__actions", index)
        hero_actions = index[index.find("press-hero__actions"):index.find("press-hero__proof")]
        self.assertIn("narrative/", hero_actions)
        edition = (root / "layouts" / "partials" / "narrative-edition.html").read_text(encoding="utf-8")
        slate = (root / "layouts" / "partials" / "season-slate.html").read_text(encoding="utf-8")
        wire = (root / "layouts" / "partials" / "wire-headlines.html").read_text(encoding="utf-8")
        self.assertIn('partial "season-slate.html"', index)
        self.assertIn('dict "limit" 7', index)
        self.assertIn('partial "wire-headlines.html"', index)
        self.assertIn('partial "season-slate.html"', edition)
        self.assertIn(".game", edition)
        self.assertIn("Upcoming game", edition)
        story = edition.find("Always looking ahead")
        xo = edition.find('id="xo"')
        last_game = edition.find('id="last-game"')
        state = edition.find('id="current-state"')
        plan = edition.find('id="game-plan"')
        self.assertGreater(story, -1)
        self.assertGreater(xo, -1)
        self.assertLess(story, xo, "Always looking ahead must render above the X's & O's")
        self.assertLess(last_game, state)
        self.assertLess(state, plan)
        self.assertLess(plan, xo, "Next-game plan must render above the X's & O's")
        self.assertIn("What the tape said", edition)
        self.assertIn("Where we stand", edition)
        self.assertIn("The next-game plan", edition)
        self.assertIn("How they match up", edition)
        watch = (root / "layouts" / "youtube" / "single.html").read_text(encoding="utf-8")
        self.assertIn("watch-onair", watch)
        self.assertIn("watch-monitor", watch)
        watch_css = (root / "public" / "css" / "v2.css").read_text(encoding="utf-8")
        stage = watch_css[watch_css.find(".yt-stage--watch"):watch_css.find(".yt-stage--watch") + 220]
        self.assertNotIn("18px 20px 0 var(--ap-red)", stage)
        cta = watch_css[watch_css.find(".watch-final-cta {"):watch_css.find(".watch-final-cta {") + 420]
        self.assertNotIn("var(--ap-red)", cta)
        self.assertIn("site.Data.schedule_2026", slate)
        self.assertIn("site.Data.wire", wire)
        self.assertIn("slate-game__ha", slate)
        self.assertIn("slate-game__score", slate)
        self.assertIn("kcScore", slate)
        self.assertIn("slate-game--done", slate)
        self.assertIn("In progress", slate)
        self.assertIn("wire-item--lead", wire)
        self.assertIn("first 8 .headlines", wire)
        base = (root / "layouts" / "_default" / "baseof.html").read_text(encoding="utf-8")
        self.assertIn("Source wire", base)
        self.assertIn("stripe-item__name", base)
        self.assertNotIn("--accent:", base)
        v2 = (root / "public" / "css" / "v2.css").read_text(encoding="utf-8")
        start = v2.find(".hero-stripe--top {")
        self.assertGreater(start, -1)
        block = v2[start:start + 500]
        self.assertNotIn("var(--ap-red)", block)
        self.assertIn("align-items: center", block)
        v2 = (root / "public" / "css" / "v2.css").read_text(encoding="utf-8")
        start = v2.find(".shorts-section {")
        self.assertGreater(start, -1)
        block = v2[start:start + 280]
        self.assertNotIn("var(--ap-red)", block)
        self.assertIn("var(--ap-cream)", block)

    def test_schedule_page_lists_preseason_and_regular(self):
        root = Path(__file__).resolve().parents[2]
        page = (root / "layouts" / "schedule" / "single.html").read_text(encoding="utf-8")
        row = (root / "layouts" / "partials" / "schedule-row.html").read_text(encoding="utf-8")
        nav = (root / "hugo.yaml").read_text(encoding="utf-8")
        md = (root / "content" / "schedule.md").read_text(encoding="utf-8")
        self.assertIn('where $all "seasonType" "pre"', page)
        self.assertIn('where $all "seasonType" "reg"', page)
        self.assertIn("Bye", page)
        self.assertIn(".opponent", row)
        self.assertIn("sched-row__score", row)
        self.assertIn("kcScore", row)
        self.assertIn("Score", page)
        self.assertIn("sched-row__score", page)
        self.assertIn('href: "schedule/"', nav)
        self.assertIn("active: \"schedule\"", md)

        games = json.loads((root / "data" / "schedule_2026.json").read_text(encoding="utf-8"))
        pre = [g for g in games if g.get("seasonType") == "pre"]
        reg = [g for g in games if g.get("seasonType") == "reg"]
        self.assertEqual(len(pre), 3)
        self.assertGreaterEqual(len(reg), 17)
        self.assertTrue(any(g.get("opponentAbbr") == "LAR" for g in pre))
        self.assertTrue(any(g.get("week") == 1 and g.get("opponentAbbr") == "DEN" for g in reg))
        self.assertNotIn(5, {g.get("week") for g in reg})
        rams = next(g for g in pre if g.get("opponentAbbr") == "LAR")
        if rams.get("completed"):
            self.assertIsNotNone(rams.get("kcScore"), "completed Rams game must keep a real ESPN score")
            self.assertIsNotNone(rams.get("oppScore"))


class ScheduleScores(unittest.TestCase):
    """Normalize ESPN scores without inventing kickoffs, networks, or results."""

    def test_to_int_reads_espn_web_score_object(self):
        self.assertEqual(collect._to_int({"value": 12.0, "displayValue": "12"}), 12)
        self.assertEqual(collect._to_int({"displayValue": "20"}), 20)
        self.assertEqual(collect._to_int("17"), 17)
        self.assertEqual(collect._to_int(7), 7)
        self.assertEqual(collect._to_int(3.0), 3)

    def test_to_int_does_not_invent(self):
        self.assertIsNone(collect._to_int(None))
        self.assertIsNone(collect._to_int(""))
        self.assertIsNone(collect._to_int({}))
        self.assertIsNone(collect._to_int({"value": None}))
        self.assertIsNone(collect._to_int("TBD"))
        self.assertIsNone(collect._to_int(True))

    def test_parse_event_keeps_final_from_espn_dict(self):
        event = _espn_event(
            "401873283",
            "2026-08-15T20:00Z",
            "LAR",
            "pre",
            2,
            kc_score={"value": 12.0, "displayValue": "12"},
            opp_score={"value": 20.0, "displayValue": "20"},
            completed=True,
            state="post",
        )
        game = collect.parse_event(event)
        self.assertTrue(game["completed"])
        self.assertFalse(game["inProgress"])
        self.assertEqual(game["kcScore"], 12)
        self.assertEqual(game["oppScore"], 20)
        self.assertEqual(game["tv"], "NFLN")
        self.assertIn("Aug 15", game["kickoff"])
        self.assertIn("CT", game["kickoff"])

    def test_parse_event_leaves_blank_when_espn_has_no_score(self):
        event = _espn_event(
            "401873283",
            "2026-08-15T20:00Z",
            "LAR",
            "pre",
            2,
            completed=True,
            state="post",
        )
        game = collect.parse_event(event)
        self.assertTrue(game["completed"])
        self.assertIsNone(game["kcScore"])
        self.assertIsNone(game["oppScore"])

    def test_in_progress_can_show_espn_score_without_marking_final(self):
        event = _espn_event(
            "live1",
            "2026-08-22T23:30Z",
            "TB",
            "pre",
            3,
            home=False,
            kc_score={"value": 10.0, "displayValue": "10"},
            opp_score={"value": 7.0, "displayValue": "7"},
            completed=False,
            state="in",
        )
        game = collect.parse_event(event)
        self.assertFalse(game["completed"])
        self.assertTrue(game["inProgress"])
        self.assertEqual(game["kcScore"], 10)
        self.assertEqual(game["oppScore"], 7)

    def test_merge_keeps_cached_final_when_live_omits_score(self):
        live = [{
            "id": "401873283",
            "completed": True,
            "kcScore": None,
            "oppScore": None,
            "tv": "NFL Net",
        }]
        cached = [{
            "id": "401873283",
            "completed": True,
            "kcScore": 12,
            "oppScore": 20,
            "tv": "NFL Net",
        }]
        merged = collect.merge_cached_scores(live, cached)
        self.assertEqual(merged[0]["kcScore"], 12)
        self.assertEqual(merged[0]["oppScore"], 20)

    def test_merge_does_not_invent_when_neither_side_has_a_score(self):
        live = [{"id": "x", "completed": True, "kcScore": None, "oppScore": None}]
        cached = [{"id": "x", "completed": True, "kcScore": None, "oppScore": None}]
        merged = collect.merge_cached_scores(live, cached)
        self.assertIsNone(merged[0]["kcScore"])
        self.assertIsNone(merged[0]["oppScore"])

    def test_write_schedule_refuses_to_clobber_with_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "schedule.json"
            path.write_text('[{"id": "keep"}]\n', encoding="utf-8")
            with patch.object(collect.config, "SCHEDULE_JSON", path):
                self.assertFalse(collect.write_schedule([]))
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))[0]["id"], "keep")

    def test_schedule_only_writes_slate_without_narrative(self):
        event = _espn_event(
            "401873283",
            "2026-08-15T20:00Z",
            "LAR",
            "pre",
            2,
            kc_score={"value": 12.0, "displayValue": "12"},
            opp_score={"value": 20.0, "displayValue": "20"},
            completed=True,
            state="post",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "schedule.json"
            with patch.object(collect.config, "SCHEDULE_JSON", path), \
                 patch.object(collect, "fetch_schedule", return_value=[collect.parse_event(event)]):
                rc = generate.main(["--schedule-only"])
            self.assertEqual(rc, 0)
            games = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(games[0]["kcScore"], 12)
        self.assertEqual(games[0]["oppScore"], 20)
        self.assertTrue(games[0]["completed"])


if __name__ == "__main__":
    unittest.main()
