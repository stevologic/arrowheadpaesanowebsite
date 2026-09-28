"""CI gates for the Chiefs Narrative engine.

Everything here runs offline — no network, no API keys — so it is a stable
merge gate. Run with:  python -m unittest discover -s tools/tests -v
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from tools.chiefs_narrative import (
    collect,
    diagrams,
    facts,
    generate,
    odds,
    offline,
    phase,
    prompts,
    providers,
    schema,
    x_embeds,
)


def _xo_side_ok(card):
    return diagrams.CONCEPTS.get(card.get("concept"), {}).get("side")

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _fixture(name: str) -> Path:
    """Frozen edition/archive payload. Tests must not read live data/ editions."""
    return FIXTURES / name


def _load_fixture(name: str):
    return json.loads(_fixture(name).read_text(encoding="utf-8"))


def _hugo_bin() -> str | None:
    found = shutil.which("hugo")
    if found:
        return found
    fallback = Path.home() / ".local" / "hugo" / "hugo"
    if fallback.is_file():
        return str(fallback)
    return None


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

    def test_schema_drops_empty_placeholder_cards(self):
        raw = [
            {
                "title": "Under-Center Play-Action Boot",
                "situation": "",
                "concept": "play_action_boot",
                "why": "",
                "coaching": "",
            },
            {
                "title": "Walker, one cut",
                "situation": "1st-and-10",
                "concept": "inside_zone",
                "why": "Win first down.",
                "coaching": "One cut.",
            },
        ]
        out = schema._norm_xsandos(raw)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["concept"], "inside_zone")

    def test_all_offense_cards_are_topped_with_real_defense(self):
        payload = _load_fixture("edition_2026-09-28-0010_karen_qa.json")
        narrative = {"xsandos": schema._norm_xsandos(payload.get("xsandos"))}
        self.assertTrue(all(_xo_side_ok(c) == "offense" for c in narrative["xsandos"]))
        generate._ensure_six_xsandos(narrative, SIGNALS, CAMP_PHASE, NEXT)
        self.assert_six(narrative["xsandos"])
        concepts = [c["concept"] for c in narrative["xsandos"]]
        self.assertIn("cover_two", concepts)
        self.assertIn("zone_blitz", concepts)
        for card in narrative["xsandos"]:
            self.assertTrue(card.get("why") or card.get("situation"))


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

    def test_espn_override_strips_model_score_when_not_final(self):
        live = {
            "id": "live3",
            "week": 3,
            "seasonType": "reg",
            "date": "2026-09-27T17:00:00Z",
            "opponent": "Miami Dolphins",
            "completed": False,
            "inProgress": True,
            "kcScore": 14,
            "oppScore": 7,
            "kickoff": "Sun, Sep 27 · 12:00 PM CT",
        }
        narrative = {
            "lastGameReview": {
                "lede": "invented recap",
                "opponent": "Hallucinated",
                "result": "T",
                "score": "KC 7–7",
            }
        }
        generate._ensure_desk_sections(
            narrative, {"news": [], "markets": {}, "schedule": [live]},
            {"lastGame": live, "liveGame": live}, NEXT,
        )
        self.assertEqual(narrative["lastGameReview"]["result"], "")
        self.assertEqual(narrative["lastGameReview"]["score"], "")
        self.assertEqual(narrative["lastGameReview"]["opponent"], "Miami Dolphins")

    def test_espn_override_clears_scoreless_completed_row(self):
        last = {
            "id": "done",
            "week": 2,
            "seasonType": "reg",
            "date": "2026-09-21T00:20:00Z",
            "opponent": "Indianapolis Colts",
            "completed": True,
            "kcScore": None,
            "oppScore": None,
            "kickoff": "Sun, Sep 20 · 7:20 PM CT",
        }
        narrative = {
            "lastGameReview": {
                "lede": "invented recap",
                "result": "W",
                "score": "KC 7–7",
            }
        }
        generate._ensure_desk_sections(
            narrative, {"news": [], "markets": {}, "schedule": [last]},
            {"lastGame": last}, NEXT,
        )
        self.assertEqual(narrative["lastGameReview"]["result"], "")
        self.assertEqual(narrative["lastGameReview"]["score"], "")

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
            "drives": {
                "previous": [
                    {
                        "team": {"abbreviation": "KC"},
                        "result": "MISSED FG",
                        "start": {
                            "period": {"number": 4},
                            "clock": {"displayValue": "1:54"},
                        },
                        "plays": [
                            {
                                "text": "H.Butker 50 yard field goal is No Good, Wide Left"
                            }
                        ],
                    }
                ]
            },
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
        self.assertEqual(recap["scoringPlays"][0]["quarter"], 4)
        self.assertEqual(recap["scoringPlays"][0]["clock"], "0:12")
        self.assertEqual(recap["scoringPlays"][0]["yards"], 29)
        self.assertEqual(recap["scoringPlays"][0]["team"], "TB")
        self.assertEqual(recap["driveResults"][0]["team"], "KC")
        self.assertEqual(recap["driveResults"][0]["result"], "missed FG")
        self.assertEqual(recap["driveResults"][0]["clock"], "1:54")
        self.assertEqual(recap["driveResults"][0]["yards"], 50)

    def test_drive_result_uses_play_clock_not_drive_start(self):
        payload = {
            "boxscore": {"teams": []},
            "scoringPlays": [],
            "drives": {
                "previous": [
                    {
                        "team": {"abbreviation": "MIA"},
                        "result": "INT",
                        "start": {
                            "period": {"number": 4},
                            "clock": {"displayValue": "3:10"},
                        },
                        "plays": [
                            {
                                "text": "G.Karlaftis intercepted M.Willis",
                                "period": {"number": 4},
                                "clock": {"displayValue": "2:03"},
                            }
                        ],
                    }
                ]
            },
        }
        with patch.object(collect, "_get_json", return_value=payload):
            recap = collect.fetch_game_recap("401872952")
        self.assertEqual(recap["driveResults"][0]["result"], "INT")
        self.assertEqual(recap["driveResults"][0]["clock"], "2:03")
        self.assertEqual(recap["driveResults"][0]["quarter"], 4)

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
                self.assertIn('text x="16"', svg)

    def test_caption_wraps_inside_viewbox(self):
        long = (
            "Show heat, drop a lineman, rush the second level — "
            "Spagnuolo's signature."
        )
        lines = diagrams._wrap_caption(long, width=64)
        self.assertGreaterEqual(len(lines), 1)
        self.assertLessEqual(len(lines), 2)
        for line in lines:
            self.assertLessEqual(len(line), 64)

    def test_xo_grid_does_not_overflow_narrow_viewports(self):
        css = (
            Path(__file__).resolve().parents[2] / "public" / "css" / "narrative.css"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "grid-template-columns: repeat(auto-fill, minmax(min(100%, 340px), 1fr))",
            css,
        )
        self.assertNotIn(
            "grid-template-columns: repeat(auto-fill, minmax(340px, 1fr))",
            css,
        )
        self.assertIn(".nrt-xo-card {", css)
        card = css[css.find(".nrt-xo-card {") : css.find(".nrt-xo-card__board {")]
        self.assertIn("min-width: 0", card)
        self.assertIn("max-width: 100%", css[css.find(".nrt-xo-card__board img") :])

    def test_play_action_boot_comeback_label_is_not_on_the_arrowhead(self):
        svg = diagrams.render_concept("play_action_boot")[0]
        self.assertIn(">comeback</text>", svg)
        # Label sits at the stem top (LOS-120), not the return arrowhead.
        self.assertIn(f'y="{diagrams.LOS - 120 - 8}"', svg)
        self.assertNotIn(f'y="{diagrams.LOS - 96 - 4}"', svg)


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
        self.assertIn("37 9 * * *", yaml)
        self.assertIn("43 3 * * *", yaml)
        self.assertIn("20 7 * * 0,1,2", yaml)
        self.assertIn('github.event.schedule }}" = "37 9 * * *"', yaml)
        self.assertIn("--schedule-only", yaml)
        self.assertIn("python -m tools.chiefs_narrative.generate --schedule-only", yaml)
        self.assertIn("Chiefs schedule: refresh 2026 slate", yaml)
        self.assertIn("phase.any_live", yaml)
        self.assertIn("steps.live.outputs.skip", yaml)
        self.assertIn("skipping new edition and PR", yaml)
        self.assertNotIn("*/15", yaml)
        self.assertNotIn("10 11 * * *", yaml)
        self.assertNotIn("20 4 * * *", yaml)
        gate = "python -m unittest discover -s tools/tests -v"
        self.assertIn(gate, yaml)
        self.assertLess(yaml.index(gate), yaml.index("Open pull request and wait for CI gates"))
        self.assertLess(yaml.index("hugo --gc --minify"), yaml.index("Open pull request and wait for CI gates"))
        self.assertNotIn("gh pr checks ", yaml)
        self.assertNotIn("gh pr checks\n", yaml)
        self.assertNotIn("gh pr checks\"", yaml)
        self.assertIn("gh workflow run ci.yml --ref", yaml)
        self.assertIn("APPEAR_DEADLINE", yaml)
        self.assertIn("DONE_DEADLINE", yaml)
        self.assertLess(
            yaml.index("gh workflow run ci.yml --ref"),
            yaml.index('gh pr merge "$PR_URL" --squash --delete-branch'),
        )
        self.assertNotIn("|| gh pr merge", yaml)
        self.assertIn("actions: write", yaml)
        self.assertNotIn("secrets.GH_PAT", yaml)
        self.assertNotIn("secrets.PAT", yaml)

    def test_edition_prs_run_ci_gates_before_merge(self):
        """#114 pull_request died with 0 jobs; skip edition heads, dispatch gates."""
        ci = (
            Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"
        ).read_text(encoding="utf-8")
        gates = ci.split("automerge:")[0]
        concurrency = ci.split("concurrency:")[1].split("jobs:")[0]
        self.assertNotIn("inputs.checkout_ref", concurrency)
        self.assertIn("github.event.pull_request.number || github.ref", concurrency)
        self.assertIn("narrative/update-", gates)
        self.assertIn("workflow_dispatch:", ci)
        self.assertIn("required: false", ci)
        self.assertIn("narrative/update-", ci.split("automerge:")[1])
        self.assertIn("paths-ignore:", ci)
        self.assertIn("data/" + "narrative.json", ci)
        self.assertIn("data/" + "narrative_editions/**", ci)
        self.assertIn("data/schedule_2026.json", ci)
        self.assertIn("public/images/" + "narrative/**", ci)
        self.assertIn("required approval", ci)
        self.assertIn("36446331690", ci)
        self.assertNotIn("as usual", ci)
        self.assertIn('gh workflow run "Deploy Hugo site to GitHub Pages"', ci)
        self.assertIn('--repo "${GITHUB_REPOSITORY}"', ci)
        self.assertIn("--diagrams-only", ci)

    def test_edition_ci_is_dispatched_not_pr_triggered(self):
        """GITHUB_TOKEN PRs do not start pull_request workflows (run 36360998269)."""
        root = Path(__file__).resolve().parents[2] / ".github" / "workflows"
        narrative = (root / "narrative.yml").read_text(encoding="utf-8")
        ci = (root / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", ci)
        self.assertIn("workflow_call:", ci)
        self.assertIn("github.event_name == 'pull_request'", ci)
        automerge = ci.split("automerge:")[1]
        self.assertIn("github.event_name == 'pull_request'", automerge)
        self.assertIn("gh workflow run ci.yml --ref", narrative)
        self.assertIn("workflow_dispatch", narrative)
        self.assertNotIn("gh pr checks ", narrative)
        self.assertNotIn("gh pr checks\n", narrative)
        self.assertIn("sleep 5", narrative)
        self.assertIn("sleep 10", narrative)
        self.assertLess(narrative.index("DONE_DEADLINE"), narrative.index("gh pr merge"))
        self.assertNotIn("git add -A data", narrative)
        self.assertIn("data/schedule_2026.json", narrative)
        self.assertIn("--diagrams-only", narrative)

    def test_human_edition_prs_still_run_check_review(self):
        """#120 was paths-ignored; human edition edits must still get gates."""
        root = Path(__file__).resolve().parents[2] / ".github" / "workflows"
        qa = (root / "edition-qa.yml").read_text(encoding="utf-8")
        self.assertIn("github-actions[bot]", qa)
        self.assertIn("--check-edition", qa)
        self.assertIn("--diagrams-only", qa)
        self.assertIn('gh workflow run "Deploy Hugo site to GitHub Pages"', qa)
        self.assertIn('--repo "${GITHUB_REPOSITORY}"', qa)
        self.assertIn("data/schedule_2026.json", qa)
        self.assertIn("Checkout main tools", qa)
        self.assertIn("github.event.pull_request.base.sha", qa)
        self.assertIn("repository: ${{ github.repository }}", qa)
        self.assertIn(".pr-head", qa)
        self.assertIn("Overlay edition data from the PR head", qa)
        self.assertIn("path: .pr-head", qa)
        self.assertNotIn("Checkout the pull request", qa)
        header = qa.split("\non:", 1)[0]
        self.assertIn("Never execute PR code", header)
        self.assertIn("copied from the PR head", header)
        self.assertNotIn("first-time bot approval does not come back", header)
        gates = qa.split("automerge:")[0]
        self.assertIn("contents: read", gates)
        self.assertNotIn("contents: write", gates)
        automerge = qa.split("automerge:")[1]
        self.assertIn("contents: write", automerge)
        self.assertIn("head.repo.full_name == github.repository", automerge)
        self.assertIn("OWNER", automerge)
        self.assertIn("MEMBER", automerge)
        self.assertIn("COLLABORATOR", automerge)
        self.assertIn("author_association", automerge)

    def test_generate_cli_can_render_and_gate_diagrams(self):
        src = Path(generate.__file__).read_text(encoding="utf-8")
        self.assertIn("--diagrams-only", src)
        self.assertIn("--check-edition", src)
        self.assertIn("--stamp-updated", src)
        self.assertIn("check_diagram_captions", src)


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
        hugo = (root / "hugo.yaml").read_text(encoding="utf-8")
        self.assertIn('timeZone: "America/Chicago"', hugo)
        edition = (root / "layouts" / "partials" / "narrative-edition.html").read_text(encoding="utf-8")
        self.assertIn('partial "nrt-ct.html"', edition)
        self.assertIn("CT</strong>", edition)
        self.assertIn("updatedAt", edition)
        self.assertIn("$stamp", edition)
        ct = (root / "layouts" / "partials" / "nrt-ct.html").read_text(encoding="utf-8")
        self.assertIn('time.AsTime .t | time.In "America/Chicago"', ct)
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


class HeadlineUniqueness(unittest.TestCase):
    """Refuse reprinting yesterday's headline / dek / theEdge after one retry."""

    HEADLINE = (
        "Camp is about timing: Mahomes' knee, Bieniemy's identity, and a defense "
        "that has to carry the open"
    )
    DEK = (
        "A film-room read on the storylines that decide how fast Kansas City "
        "flips the 2025 script."
    )
    EDGE = (
        "If the secondary settles and the run game is real, Kansas City can win "
        "early while Mahomes ramps."
    )
    SLUG = "2026-08-29-1540"
    SNAPSHOT = {
        "generatedAt": "2026-08-29T15:40:37+00:00",
        "slug": SLUG,
        "edition": "2026 Training Camp",
        "phase": "Training Camp",
        "headline": HEADLINE,
        "theEdge": EDGE,
    }

    def _clone_raw(self):
        return {
            "headline": self.HEADLINE,
            "dek": self.DEK,
            "theEdge": self.EDGE,
            "edition": "2026 Training Camp",
        }

    def _unique_raw(self):
        return {
            "headline": "A new camp clock: PUP math, the slot job, and Denver on the horizon",
            "dek": "Today's tape is about who is closing camp jobs, not yesterday's thesis.",
            "theEdge": "Win the slot and the right tackle job this week or Week 1 starts behind.",
            "edition": "2026 Training Camp",
        }

    def _edition_payload(self, **overrides):
        payload = {
            "generatedAt": self.SNAPSHOT["generatedAt"],
            "slug": self.SLUG,
            "headline": self.HEADLINE,
            "dek": self.DEK,
            "theEdge": self.EDGE,
        }
        payload.update(overrides)
        return payload

    def _seed_archive(self, tmp: Path):
        archive = tmp / "narrative_archive.json"
        editions = tmp / "narrative_editions"
        editions.mkdir()
        archive.write_text(json.dumps([self.SNAPSHOT]) + "\n", encoding="utf-8")
        (editions / f"{self.SLUG}.json").write_text(
            json.dumps(self._edition_payload()) + "\n", encoding="utf-8"
        )
        return archive, editions

    def _generation_stack(self, *, llm, archive: Path, editions: Path, extra=None):
        stack = ExitStack()
        stack.enter_context(
            patch.object(
                collect,
                "collect_all",
                return_value={"schedule": [], "news": [], "markets": {}},
            )
        )
        stack.enter_context(patch.object(phase, "detect", return_value=CAMP_PHASE))
        stack.enter_context(patch.object(phase, "next_games", return_value=NEXT))
        stack.enter_context(patch.object(odds, "collect_markets", return_value={}))
        stack.enter_context(patch.object(providers, "generate_via_llm", llm))
        stack.enter_context(patch.object(generate, "_render_diagrams"))
        stack.enter_context(patch.object(generate, "_write_schedule"))
        stack.enter_context(patch.object(generate.config, "ARCHIVE_JSON", archive))
        stack.enter_context(patch.object(generate.config, "EDITIONS_DIR", editions))
        for item in extra or []:
            stack.enter_context(item)
        return stack

    def test_matching_headline_dek_the_edge_detected_after_normalize(self):
        previous = {
            "headline": self.HEADLINE,
            "dek": self.DEK,
            "theEdge": self.EDGE,
        }
        raw = {
            "headline": (
                "  CAMP is about timing: Mahomes' knee, Bieniemy's identity, "
                "and a defense that has to carry the open  "
            ),
            "dek": (
                "A FILM-ROOM read on the storylines that decide how fast "
                "Kansas City flips the 2025 script."
            ),
            "theEdge": (
                "If the  secondary  settles and the run game is real, Kansas "
                "City can win early while Mahomes ramps."
            ),
        }
        narrative = schema.normalize(
            raw,
            phase=CAMP_PHASE,
            meta={"generatedAt": "2026-08-30T15:23:00+00:00", "generator": "test"},
        )
        self.assertEqual(
            generate._matched_copy_fields(narrative, previous),
            ["headline", "dek", "theEdge"],
        )
        narrative["dek"] = "Fresh dek."
        narrative["theEdge"] = "Fresh edge."
        self.assertEqual(
            generate._matched_copy_fields(narrative, previous), ["headline"]
        )
        narrative["headline"] = "A brand new camp thesis"
        self.assertEqual(generate._matched_copy_fields(narrative, previous), [])
        self.assertEqual(generate._matched_copy_fields(narrative, None), [])
        self.assertTrue(generate.is_clone(self._clone_raw(), previous))
        self.assertFalse(generate.is_clone(self._unique_raw(), previous))

    def test_load_recent_editions_prefers_editions_dir_newest_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            editions = root / "narrative_editions"
            editions.mkdir()
            older = self._edition_payload(
                generatedAt="2026-08-28T21:25:56+00:00",
                slug="2026-08-28-2125",
                headline="Last tape, current roster, next dress rehearsal",
                dek="Last game on the tape, where the Chiefs stand, and the plan for who's next.",
                theEdge="Model/market read: Vegas has it KC -1.5.",
            )
            newer = self._edition_payload()
            (editions / "2026-08-28-2125.json").write_text(
                json.dumps(older) + "\n", encoding="utf-8"
            )
            (editions / f"{self.SLUG}.json").write_text(
                json.dumps(newer) + "\n", encoding="utf-8"
            )
            archive = root / "narrative_archive.json"
            archive.write_text(
                json.dumps(
                    [
                        {
                            "generatedAt": "2099-01-01T00:00:00+00:00",
                            "headline": "stale archive should not win",
                            "theEdge": "archive edge",
                        }
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            with patch.object(generate.config, "ARCHIVE_JSON", archive), \
                 patch.object(generate.config, "EDITIONS_DIR", editions):
                rows = generate._load_recent_editions()
        self.assertEqual(rows[0]["headline"], self.HEADLINE)
        self.assertEqual(rows[0]["dek"], self.DEK)
        self.assertEqual(rows[0]["theEdge"], self.EDGE)
        self.assertEqual(
            rows[1]["headline"], "Last tape, current roster, next dress rehearsal"
        )
        self.assertNotIn("stale archive should not win", [r["headline"] for r in rows])

    def test_load_recent_editions_falls_back_to_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            editions = root / "narrative_editions"
            editions.mkdir()
            archive = root / "narrative_archive.json"
            archive.write_text(json.dumps([self.SNAPSHOT]) + "\n", encoding="utf-8")
            with patch.object(generate.config, "ARCHIVE_JSON", archive), \
                 patch.object(generate.config, "EDITIONS_DIR", editions):
                rows = generate._load_recent_editions()
        self.assertEqual(rows[0]["headline"], self.HEADLINE)
        self.assertEqual(rows[0]["theEdge"], self.EDGE)

    def test_prompt_lists_recent_headlines(self):
        text = prompts.build_user_prompt(
            SIGNALS,
            CAMP_PHASE,
            NEXT,
            prior_editions=[
                {
                    "generatedAt": "2026-08-29T15:40:37+00:00",
                    "headline": self.HEADLINE,
                    "dek": self.DEK,
                    "theEdge": self.EDGE,
                },
                {
                    "generatedAt": "2026-08-28T21:25:56+00:00",
                    "headline": "Last tape, current roster, next dress rehearsal",
                    "dek": "Last game on the tape, where the Chiefs stand, and the plan for who's next.",
                    "theEdge": "Model/market read: Vegas has it KC -1.5.",
                },
            ],
        )
        self.assertIn("DO NOT REUSE these recent titles", text)
        self.assertIn(self.HEADLINE, text)
        self.assertIn(self.DEK, text)
        self.assertIn(self.EDGE, text)
        self.assertIn("Last tape, current roster, next dress rehearsal", text)

    def test_clone_retries_once_then_accepts_new_headline(self):
        llm = Mock(side_effect=[(self._clone_raw(), "grok"), (self._unique_raw(), "grok")])
        with tempfile.TemporaryDirectory() as tmp:
            archive, editions = self._seed_archive(Path(tmp))
            with self._generation_stack(llm=llm, archive=archive, editions=editions):
                result = generate.build("grok", persist_schedule=False)
        self.assertEqual(llm.call_count, 2)
        first_user = llm.call_args_list[0].args[2]
        retry_user = llm.call_args_list[1].args[2]
        self.assertIn("DO NOT REUSE these recent titles", first_user)
        self.assertIn(self.HEADLINE, first_user)
        self.assertIn(self.DEK, first_user)
        self.assertIn(self.EDGE, first_user)
        self.assertIn("yesterday's title was", retry_user)
        self.assertIn(self.HEADLINE, retry_user)
        self.assertIn("yesterday's dek was", retry_user)
        self.assertIn("yesterday's theEdge was", retry_user)
        self.assertEqual(
            result["narrative"]["headline"], self._unique_raw()["headline"]
        )
        self.assertEqual(result["narrative"]["dek"], self._unique_raw()["dek"])
        self.assertEqual(result["narrative"]["theEdge"], self._unique_raw()["theEdge"])

    def test_second_clone_fails_without_writing_files(self):
        llm = Mock(side_effect=[(self._clone_raw(), "grok"), (self._clone_raw(), "grok")])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, editions = self._seed_archive(root)
            narrative_json = root / "narrative.json"
            wire_json = root / "wire.json"
            extra = [
                patch.object(generate.config, "NARRATIVE_JSON", narrative_json),
                patch.object(generate.config, "WIRE_JSON", wire_json),
            ]
            with self._generation_stack(
                llm=llm, archive=archive, editions=editions, extra=extra
            ):
                rc = generate.main(["--provider", "grok"])
            self.assertEqual(rc, 1)
            self.assertEqual(llm.call_count, 2)
            self.assertFalse(narrative_json.exists())
            self.assertFalse(wire_json.exists())
            self.assertEqual(
                {p.name for p in editions.iterdir()}, {f"{self.SLUG}.json"}
            )
            saved = json.loads(archive.read_text(encoding="utf-8"))
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0]["headline"], self.HEADLINE)

    def test_new_headline_is_accepted_without_retry(self):
        llm = Mock(return_value=(self._unique_raw(), "grok"))
        with tempfile.TemporaryDirectory() as tmp:
            archive, editions = self._seed_archive(Path(tmp))
            with self._generation_stack(llm=llm, archive=archive, editions=editions):
                result = generate.build("grok", persist_schedule=False)
        self.assertEqual(llm.call_count, 1)
        self.assertEqual(
            result["narrative"]["headline"], self._unique_raw()["headline"]
        )
        first_user = llm.call_args.args[2]
        self.assertIn("DO NOT REUSE these recent titles", first_user)
        self.assertNotIn("yesterday's title was", first_user)
        self.assertNotIn("UNIQUENESS RETRY", first_user)

    def test_offline_clone_is_refused(self):
        llm = Mock(side_effect=AssertionError("offline path must not call the LLM"))
        with tempfile.TemporaryDirectory() as tmp:
            archive, editions = self._seed_archive(Path(tmp))
            with self._generation_stack(llm=llm, archive=archive, editions=editions):
                with self.assertRaises(generate.DuplicateNarrativeError):
                    generate.build("offline", persist_schedule=False)
        self.assertEqual(llm.call_count, 0)


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


class LiveGamePhase(unittest.TestCase):
    """A live or unfinished game must never become last week · review."""

    WEEK2 = {
        "id": "w2",
        "week": 2,
        "seasonType": "reg",
        "date": "2026-09-21T00:20:00Z",
        "opponent": "Indianapolis Colts",
        "homeAway": "home",
        "venue": "GEHA Field at Arrowhead Stadium",
        "completed": True,
        "inProgress": False,
        "kcScore": 33,
        "oppScore": 30,
        "kickoff": "Sun, Sep 20 · 7:20 PM CT",
    }
    WEEK3_LIVE = {
        "id": "w3",
        "week": 3,
        "seasonType": "reg",
        "date": "2026-09-27T17:00:00Z",
        "opponent": "Miami Dolphins",
        "homeAway": "away",
        "venue": "Hard Rock Stadium",
        "completed": False,
        "inProgress": True,
        "kcScore": 14,
        "oppScore": 7,
        "kickoff": "Sun, Sep 27 · 12:00 PM CT",
    }
    WEEK4 = {
        "id": "w4",
        "week": 4,
        "seasonType": "reg",
        "date": "2026-10-04T20:25:00Z",
        "opponent": "Las Vegas Raiders",
        "homeAway": "away",
        "venue": "Allegiant Stadium",
        "completed": False,
        "inProgress": False,
        "kcScore": None,
        "oppScore": None,
        "kickoff": "Sun, Oct 4 · 3:25 PM CT",
    }
    NOW = datetime(2026, 9, 27, 17, 37, tzinfo=timezone.utc)

    def test_in_progress_is_not_review_and_keeps_own_week(self):
        ph = phase.detect([self.WEEK2, self.WEEK3_LIVE, self.WEEK4], now=self.NOW)
        self.assertEqual(ph["week"], 3)
        self.assertEqual(ph["label"], "Week 3")
        self.assertNotEqual(ph["mode"], "review")
        self.assertEqual(ph["mode"], "preview")
        self.assertEqual(ph["liveGame"]["week"], 3)
        self.assertEqual(ph["lastGame"]["week"], 2)
        self.assertTrue(ph["lastGame"]["completed"])
        self.assertEqual(ph["nextGame"]["week"], 4)
        self.assertFalse(phase.any_in_progress([]))
        self.assertTrue(phase.any_in_progress([self.WEEK3_LIVE]))
        self.assertTrue(phase.any_live([self.WEEK3_LIVE], now=self.NOW))
        self.assertEqual(ph["edition"], "2026 Week 3 · Preview")

    def test_past_kickoff_without_completed_is_live(self):
        unfinished = dict(self.WEEK3_LIVE)
        unfinished["inProgress"] = False
        unfinished["kcScore"] = None
        unfinished["oppScore"] = None
        self.assertTrue(phase.is_live(unfinished, now=self.NOW))
        self.assertFalse(phase.is_final(unfinished))
        ph = phase.detect([self.WEEK2, unfinished, self.WEEK4], now=self.NOW)
        self.assertEqual(ph["week"], 3)
        self.assertEqual(ph["label"], "Week 3")
        self.assertNotEqual(ph["mode"], "review")
        self.assertEqual(ph["liveGame"]["week"], 3)
        self.assertFalse(phase.any_in_progress([unfinished]))

    def test_completed_with_both_scores_allows_review(self):
        now = datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc)
        week3 = {
            "id": "w3f",
            "week": 3,
            "seasonType": "reg",
            "date": "2026-09-27T17:00:00Z",
            "opponent": "Miami Dolphins",
            "completed": True,
            "inProgress": False,
            "kcScore": 24,
            "oppScore": 17,
            "kickoff": "Sun, Sep 27 · 12:00 PM CT",
        }
        self.assertTrue(phase.is_final(week3))
        ph = phase.detect([self.WEEK2, week3, self.WEEK4], now=now)
        self.assertEqual(ph["mode"], "review")
        self.assertEqual(ph["lastGame"]["week"], 3)
        self.assertEqual(ph["week"], 4)
        self.assertIsNone(ph["liveGame"])

    def test_completed_without_scores_is_not_review(self):
        now = datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc)
        week3 = {
            "id": "w3ns",
            "week": 3,
            "seasonType": "reg",
            "date": "2026-09-27T17:00:00Z",
            "opponent": "Miami Dolphins",
            "completed": True,
            "inProgress": False,
            "kcScore": None,
            "oppScore": None,
        }
        ph = phase.detect([self.WEEK2, week3, self.WEEK4], now=now)
        self.assertNotEqual(ph["mode"], "review")

    def test_in_progress_skips_publish(self):
        live = dict(self.WEEK3_LIVE)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            narrative_json = root / "narrative.json"
            wire_json = root / "wire.json"
            editions = root / "editions"
            editions.mkdir()
            archive = root / "archive.json"
            archive.write_text("[]\n", encoding="utf-8")
            with patch.object(
                collect,
                "collect_all",
                return_value={"schedule": [self.WEEK2, live, self.WEEK4], "news": [], "markets": {}},
            ), patch.object(generate, "_write_schedule"), patch.object(
                generate.config, "NARRATIVE_JSON", narrative_json
            ), patch.object(
                generate.config, "WIRE_JSON", wire_json
            ), patch.object(
                generate.config, "EDITIONS_DIR", editions
            ), patch.object(
                generate.config, "ARCHIVE_JSON", archive
            ), patch.object(
                generate, "_render_diagrams"
            ):
                rc = generate.main(["--provider", "offline"])
            self.assertEqual(rc, 0)
            self.assertFalse(narrative_json.exists())
            self.assertFalse(wire_json.exists())
            self.assertEqual(list(editions.iterdir()), [])

    def test_prompt_names_live_status_and_forbids_invented_score(self):
        ph = phase.detect([self.WEEK2, self.WEEK3_LIVE, self.WEEK4], now=self.NOW)
        text = prompts.build_user_prompt(
            {"news": [], "markets": {}, "schedule": [self.WEEK2, self.WEEK3_LIVE, self.WEEK4]},
            ph,
            [self.WEEK4],
        )
        self.assertIn("IN PROGRESS", text)
        self.assertIn("NOT FINAL", text)
        self.assertIn("Miami Dolphins", text)
        self.assertNotIn("final KC 14-7", text)

    def test_fmt_game_uses_central_calendar_date(self):
        week1 = offline._fmt_game(
            {
                "date": "2026-09-15T00:15:00Z",
                "week": 1,
                "opponent": "Denver Broncos",
                "homeAway": "home",
            }
        )
        self.assertIn("Sep 14", week1["label"])
        self.assertNotIn("Sep 15", week1["label"])
        week2 = offline._fmt_game(
            {
                "date": "2026-09-21T00:20:00Z",
                "week": 2,
                "opponent": "Indianapolis Colts",
                "homeAway": "home",
            }
        )
        self.assertIn("Sep 20", week2["label"])
        self.assertNotIn("Sep 21", week2["label"])

    def test_past_kickoff_skips_publish(self):
        unfinished = dict(self.WEEK3_LIVE)
        unfinished["inProgress"] = False
        unfinished["kcScore"] = None
        unfinished["oppScore"] = None
        self.assertTrue(phase.is_live(unfinished, now=self.NOW))
        self.assertTrue(phase.any_live([unfinished], now=self.NOW))
        self.assertFalse(phase.any_in_progress([unfinished]))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            narrative_json = root / "narrative.json"
            wire_json = root / "wire.json"
            editions = root / "editions"
            editions.mkdir()
            archive = root / "archive.json"
            archive.write_text("[]\n", encoding="utf-8")
            with patch.object(
                collect,
                "collect_all",
                return_value={
                    "schedule": [self.WEEK2, unfinished, self.WEEK4],
                    "news": [],
                    "markets": {},
                },
            ), patch.object(generate, "_write_schedule"), patch.object(
                generate.config, "NARRATIVE_JSON", narrative_json
            ), patch.object(
                generate.config, "WIRE_JSON", wire_json
            ), patch.object(
                generate.config, "EDITIONS_DIR", editions
            ), patch.object(
                generate.config, "ARCHIVE_JSON", archive
            ), patch.object(
                generate, "_render_diagrams"
            ), patch.object(
                phase, "any_live", return_value=True
            ):
                rc = generate.main(["--provider", "offline"])
            self.assertEqual(rc, 0)
            self.assertFalse(narrative_json.exists())
            self.assertFalse(wire_json.exists())
            self.assertEqual(list(editions.iterdir()), [])

    def test_review_edition_header_names_both_weeks(self):
        now = datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc)
        week3 = {
            "id": "w3f",
            "week": 3,
            "seasonType": "reg",
            "date": "2026-09-27T17:00:00Z",
            "opponent": "Miami Dolphins",
            "completed": True,
            "inProgress": False,
            "kcScore": 24,
            "oppScore": 10,
            "kickoff": "Sun, Sep 27 · 12:00 PM CT",
        }
        ph = phase.detect([self.WEEK2, week3, self.WEEK4], now=now)
        self.assertEqual(ph["mode"], "review")
        self.assertEqual(ph["week"], 4)
        self.assertEqual(ph["edition"], "2026 Week 4 · Week 3 Review")
        self.assertEqual(phase.format_edition(ph), "2026 Week 4 · Week 3 Review")

    def test_preview_edition_header(self):
        now = datetime(2026, 10, 2, 15, 0, tzinfo=timezone.utc)
        week3 = {
            "id": "w3f",
            "week": 3,
            "seasonType": "reg",
            "date": "2026-09-27T17:00:00Z",
            "opponent": "Miami Dolphins",
            "completed": True,
            "inProgress": False,
            "kcScore": 24,
            "oppScore": 10,
        }
        ph = phase.detect([self.WEEK2, week3, self.WEEK4], now=now)
        self.assertEqual(ph["mode"], "preview")
        self.assertEqual(ph["week"], 4)
        self.assertEqual(ph["edition"], "2026 Week 4 · Preview")

    def test_prompt_uses_ct_kickoff_not_utc_date(self):
        ph = phase.detect([self.WEEK2, self.WEEK4], now=datetime(2026, 9, 29, 15, 0, tzinfo=timezone.utc))
        text = prompts.build_user_prompt(
            {"news": [], "markets": {}, "schedule": [self.WEEK2, self.WEEK4]},
            ph,
            [self.WEEK4],
        )
        self.assertIn("Sun Oct 4, 3:25 PM CT", text)
        self.assertIn("KICKOFF FACT", text)
        self.assertIn("KICKOFF WINDOW", text)
        self.assertNotIn("2026-10-04 KC", text)

    def test_prompt_includes_structured_scoring_plays(self):
        last = dict(self.WEEK2)
        last["week"] = 3
        last["kcScore"] = 24
        last["oppScore"] = 10
        last["opponent"] = "Miami Dolphins"
        ph = {
            "type": "regular",
            "label": "Week 4",
            "mode": "review",
            "week": 4,
            "edition": "2026 Week 4 · Week 3 Review",
            "lastGame": last,
            "nextGame": self.WEEK4,
        }
        text = prompts.build_user_prompt(
            {
                "news": [],
                "markets": {},
                "schedule": [last, self.WEEK4],
                "lastGameRecap": {
                    "kc": {"totalYards": "280"},
                    "opp": {"totalYards": "310"},
                    "oppAbbr": "MIA",
                    "scoringPlays": [
                        {
                            "quarter": 2,
                            "clock": "2:00",
                            "type": "INT",
                            "player": "Trent McDuffie",
                            "yards": 0,
                            "team": "KC",
                            "scoreAfter": "KC 14–7",
                            "kcScore": 14,
                            "oppScore": 7,
                        },
                        {
                            "quarter": 3,
                            "clock": "8:12",
                            "type": "TD",
                            "player": "Travis Kelce",
                            "yards": 11,
                            "team": "KC",
                            "scoreAfter": "KC 21–7",
                            "kcScore": 21,
                            "oppScore": 7,
                        },
                    ],
                    "driveResults": [
                        {
                            "quarter": 4,
                            "clock": "1:54",
                            "team": "KC",
                            "result": "missed FG",
                            "yards": 50,
                            "detail": "H.Butker 50 yard field goal is No Good, Wide Left",
                        }
                    ],
                    "leaders": [],
                },
            },
            ph,
            [self.WEEK4],
        )
        self.assertIn("SCORING PLAYS", text)
        self.assertIn("Q2 2:00 KC INT — Trent McDuffie (0 yd) — KC 14–7", text)
        self.assertIn("Q3 8:12 KC TD — Travis Kelce (11 yd)", text)
        self.assertIn("SCORING TABLE", text)
        self.assertIn("KC  TD  11  Travis Kelce  Q3", text)
        self.assertIn("copy team, type, yards, player, quarter verbatim", text)
        self.assertIn("NON-SCORING DRIVE RESULTS", text)
        self.assertIn("Q4 1:54 KC missed FG — 50 yd", text)
        self.assertIn("KICKOFF WINDOW", text)

    def test_footer_shows_ct(self):
        hugo_bin = _hugo_bin()
        self.assertTrue(hugo_bin, "hugo must be on PATH (or ~/.local/hugo/hugo) for this gate")
        repo = Path(__file__).resolve().parents[2]
        edition = (repo / "layouts" / "partials" / "narrative-edition.html").read_text(
            encoding="utf-8"
        )
        hugo_cfg = (repo / "hugo.yaml").read_text(encoding="utf-8")
        self.assertIn('partial "nrt-ct.html"', edition)
        self.assertIn("CT</strong>", edition)
        self.assertIn('timeZone: "America/Chicago"', hugo_cfg)
        self.assertEqual(collect.local_date_label("2026-09-15T00:15:00Z"), "Mon Sep 14")
        self.assertEqual(collect.local_date_label("2026-09-21T00:20:00Z"), "Sun Sep 20")
        self.assertEqual(collect.kickoff_prompt("2026-10-04T20:25:00Z"), "Sun Oct 4, 3:25 PM CT")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "hugo.yaml").write_text(
                'baseURL: "/"\ntimeZone: "America/Chicago"\npublishDir: "dist"\n',
                encoding="utf-8",
            )
            layouts = root / "layouts"
            layouts.mkdir()
            partials = layouts / "partials"
            partials.mkdir()
            (partials / "nrt-ct.html").write_text(
                (repo / "layouts" / "partials" / "nrt-ct.html").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            (layouts / "index.html").write_text(
                '{{ $n := dict "generatedAt" "2026-09-27T20:20:32+00:00" }}\n'
                "FOOTER={{ partial \"nrt-ct.html\" (dict \"t\" $n.generatedAt \"layout\" \"Jan 2, 2006 · 3:04 PM\") }} CT\n"
                "HERO={{ partial \"nrt-ct.html\" (dict \"t\" $n.generatedAt \"layout\" \"Monday, Jan 2, 2006\") }}\n"
                '{{ $late := dict "generatedAt" "2026-09-28T00:20:00+00:00" }}\n'
                "LATE={{ partial \"nrt-ct.html\" (dict \"t\" $late.generatedAt \"layout\" \"Monday, Jan 2, 2006\") }}\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [hugo_bin, "--gc"],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            html = (root / "dist" / "index.html").read_text(encoding="utf-8")
        self.assertIn("Sep 27, 2026 · 3:20 PM CT", html)
        self.assertNotIn("8:20 PM", html)
        self.assertIn("Sunday, Sep 27, 2026", html)
        self.assertIn("LATE=Sunday, Sep 27, 2026", html)
        self.assertNotIn("Monday, Sep 28", html)


class FactCheck(unittest.TestCase):
    """Reject reviews that disagree with the ESPN box / scoring plays."""

    LAST = {
        "id": "w3",
        "week": 3,
        "seasonType": "reg",
        "completed": True,
        "kcScore": 24,
        "oppScore": 10,
        "opponent": "Miami Dolphins",
    }
    RECAP = {
        "kc": {"totalYards": "280"},
        "opp": {"totalYards": "310"},
        "oppAbbr": "MIA",
        "scoringPlays": [
            {
                "quarter": 1,
                "clock": "8:12",
                "type": "TD",
                "player": "Isiah Pacheco",
                "yards": 1,
                "team": "KC",
                "scoreAfter": "KC 7–0",
                "kcScore": 7,
                "oppScore": 0,
            },
            {
                "quarter": 2,
                "clock": "2:00",
                "type": "INT",
                "player": "Trent McDuffie",
                "yards": 0,
                "team": "KC",
                "scoreAfter": "KC 14–7",
                "kcScore": 14,
                "oppScore": 7,
            },
            {
                "quarter": 3,
                "clock": "10:04",
                "type": "TD",
                "player": "Travis Kelce",
                "yards": 11,
                "team": "KC",
                "scoreAfter": "KC 21–7",
                "kcScore": 21,
                "oppScore": 7,
            },
            {
                "quarter": 4,
                "clock": "6:11",
                "type": "FG",
                "player": "Harrison Butker",
                "yards": 35,
                "team": "KC",
                "scoreAfter": "KC 24–10",
                "kcScore": 24,
                "oppScore": 10,
            },
        ],
        "leaders": [
            {
                "player": "Patrick Mahomes",
                "category": "Passing Yards",
                "value": "12/18, 140 YDS",
            },
        ],
    }

    def _review(self, **fields):
        base = {
            "opponent": "Miami Dolphins",
            "result": "W",
            "score": "KC 24–10",
            "lede": "Kansas City beat Miami 24–10.",
            "analysis": ["Kelce scored on an 11-yard catch."],
            "whatWorked": ["The 11-yard Kelce score."],
            "whatDidnt": ["Third down was 3-of-7."],
        }
        base.update(fields)
        return {"lastGameReview": base}

    def test_accepts_official_final_and_score_after(self):
        narrative = self._review(
            whatDidnt=["The INT kept a 14-7 game from getting away."],
        )
        self.assertEqual(facts.check_review(narrative, self.LAST, self.RECAP), [])

    def test_rejects_wrong_in_game_score(self):
        narrative = self._review(
            whatDidnt=["The INT kept a 14-10 game alive into the fourth."],
        )
        issues = facts.check_review(narrative, self.LAST, self.RECAP)
        self.assertTrue(any("14-10" in item for item in issues))

    def test_rejects_wrong_td_yardage(self):
        narrative = self._review(
            analysis=["Kelce scored from the 12 and those two scores were the entire 24."],
        )
        issues = facts.check_review(narrative, self.LAST, self.RECAP)
        self.assertTrue(any("12" in item for item in issues))

    def test_accepts_official_td_yardage(self):
        narrative = self._review(analysis=["Kelce's 11-yard catch made it 21-7."])
        self.assertEqual(facts.check_review(narrative, self.LAST, self.RECAP), [])

    def test_ignores_records_and_completions_as_scores(self):
        narrative = self._review(
            lede="A 3-0 team that just went 20-of-24 still carries the 6-11 tape.",
            analysis=["Mahomes was 12/18, 140 YDS. Third down: 3-of-7."],
        )
        self.assertEqual(facts.check_review(narrative, self.LAST, self.RECAP), [])

    def test_rejects_wrong_passing_line(self):
        narrative = self._review(
            analysis=["Mahomes went 20-of-24 for 280 yards."],
        )
        issues = facts.check_review(narrative, self.LAST, self.RECAP)
        self.assertTrue(any("Mahomes" in item and "20" in item for item in issues))

    # ESPN box from daily run 36351314122 (event 401872952): Mahomes 20/24.
    RUN_RECAP = {
        "kc": {"totalYards": "334"},
        "opp": {"totalYards": "310"},
        "oppAbbr": "MIA",
        "scoringPlays": [
            {
                "quarter": 1,
                "type": "TD",
                "player": "Kenneth Walker III",
                "yards": 10,
                "team": "KC",
                "scoreAfter": "KC 7–0",
                "kcScore": 7,
                "oppScore": 0,
            },
            {
                "quarter": 1,
                "type": "TD",
                "player": "Ollie Gordon II",
                "yards": 3,
                "team": "MIA",
                "scoreAfter": "KC 7–7",
                "kcScore": 7,
                "oppScore": 7,
            },
            {
                "quarter": 2,
                "type": "TD",
                "player": "Kenneth Walker III",
                "yards": 5,
                "team": "KC",
                "scoreAfter": "KC 14–7",
                "kcScore": 14,
                "oppScore": 7,
            },
            {
                "quarter": 3,
                "type": "FG",
                "player": "Riley Patterson",
                "yards": 37,
                "team": "MIA",
                "scoreAfter": "KC 14–10",
                "kcScore": 14,
                "oppScore": 10,
            },
            {
                "quarter": 4,
                "type": "FG",
                "player": "Harrison Butker",
                "yards": 34,
                "team": "KC",
                "scoreAfter": "KC 17–10",
                "kcScore": 17,
                "oppScore": 10,
            },
            {
                "quarter": 4,
                "type": "TD",
                "player": "Travis Kelce",
                "yards": 11,
                "team": "KC",
                "scoreAfter": "KC 24–10",
                "kcScore": 24,
                "oppScore": 10,
            },
        ],
        "leaders": [
            {
                "player": "Patrick Mahomes",
                "category": "Passing Yards",
                "value": "20/24, 246 YDS",
            }
        ],
    }

    def test_run_lede_final_score_near_mahomes_is_not_a_pass_line(self):
        """Daily run 36351314122: '24-10' after Mahomes was read as 24-of-10.

        Draft was not persisted (fact-check aborted publish). The logged
        24-of-10 hit is this lastGameReview.lede from the live edition.
        """
        recap = self.RUN_RECAP
        narrative = self._review(
            lede=(
                "Kansas City opened Hard Rock Stadium with a 10-yard Kenneth "
                "Walker III run, answered Ollie Gordon II’s 3-yard score with "
                "a 5-yard Walker touchdown catch, then spent the middle of the "
                "game in a slog before Harrison Butker’s 34-yard field goal and "
                "Travis Kelce’s 11-yard catch from Patrick Mahomes at 2:55 of "
                "the fourth made it 24-10. It was a grind, not a coronation: "
                "334 total yards, one turnover, and a defense that won the "
                "scoreboard while losing the clock."
            ),
            analysis=["Kelce scored on an 11-yard catch."],
            whatDidnt=["The hidden game was possession, not the 24-10 final."],
        )
        issues = facts.check_review(narrative, self.LAST, recap)
        self.assertEqual(issues, [])
        self.assertFalse(any("passing line" in item for item in issues))

    def test_run_third_down_near_mahomes_is_not_a_pass_line(self):
        """Daily run 36351314122 retry: '3-of-7' next to Mahomes is third down."""
        recap = self.RUN_RECAP
        narrative = self._review(
            analysis=[
                "Kelce’s 11-yard catch from Patrick Mahomes at 2:55 of the "
                "fourth closed it. Third down at 3-of-7 is how you post "
                "25:39 of possession. That is not a Mahomes problem.",
            ],
            takeaways=[
                {
                    "title": "Efficiency without the hammer",
                    "body": (
                        "Mahomes at 20-of-24 with a 119.8 passer rating is a "
                        "winning quarterback night. It is not a winning "
                        "offensive identity if Walker is at 3.9 a carry and "
                        "the unit is 3-of-7 on third down."
                    ),
                }
            ],
            whatDidnt=[
                "Third-down offense at 3-of-7, the simplest reason the "
                "possession time died."
            ],
        )
        issues = facts.check_review(narrative, self.LAST, recap)
        self.assertEqual(issues, [])

    def test_published_review_does_not_invent_mahomes_line(self):
        """Frozen bbc7dd2 lastGameReview vs ESPN 20/24: no passing-line false positive."""
        payload = _load_fixture("bbc7dd2_last_game_review.json")
        issues = facts.check_review(payload, self.LAST, self.RUN_RECAP)
        self.assertFalse(any("passing line" in item for item in issues), issues)
        # 14-10 is Patterson's ESPN score-after; omitting that kick used to
        # make this sentence look invented.
        self.assertFalse(any("14-10" in item for item in issues), issues)

    def test_4f6add1_rejects_kc_and_miami_fg_misattribution(self):
        """Karen QA: edition 4f6add1 credited both FGs to KC and invented a second Miami kick."""
        payload = _load_fixture("edition_4f6add1_fg_misattr.json")
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        issues = facts.check_review(payload, last, recap)
        blob = " ".join(issues)
        self.assertTrue(any("37" in item and "34" in item for item in issues), issues)
        self.assertTrue(any("MIA" in item and "field-goal count" in item for item in issues), issues)
        self.assertIn("night", blob)
        # A clean review must not hide the same lie in later sections.
        clean_review = dict(payload)
        clean_review["lastGameReview"] = {
            "lede": "Kansas City won 24-10. Butker hit from 34. Patterson hit from 37.",
            "analysis": ["Kelce scored on an 11-yard catch."],
        }
        later = facts.check_review(clean_review, last, recap)
        self.assertTrue(any("37" in item and "34" in item for item in later), later)
        self.assertTrue(any("MIA" in item and "field-goal count" in item for item in later), later)

    def test_accepts_correct_fg_attribution(self):
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        narrative = self._review(
            lede=(
                "Patterson's 37-yard field goal made it 14-10. "
                "Butker answered with a 34-yard field goal. "
                "Kansas City also missed a 50-yarder wide left."
            ),
            analysis=["Kelce scored on an 11-yard catch."],
            whatWorked=["One Miami field goal and one Kansas City field goal."],
        )
        self.assertEqual(facts.check_review(narrative, last, recap), [])

    def test_noon_game_rejects_night_wording(self):
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        narrative = self._review(
            lede="The Miami night was a reminder. Walker had a 3.9-yard night.",
        )
        issues = facts.check_review(narrative, last, recap)
        self.assertTrue(any("night" in item for item in issues), issues)

    def test_kickoff_part_of_day(self):
        self.assertEqual(collect.kickoff_part_of_day("2026-09-27T17:00:00Z"), "midday")
        self.assertEqual(collect.kickoff_part_of_day("2026-10-04T20:25:00Z"), "afternoon")
        self.assertEqual(collect.kickoff_part_of_day("2026-09-21T00:20:00Z"), "night")

    def test_36358665975_attempt1_kelce_td_answered_miami_is_not_mia(self):
        """First draft: '11-yard touchdown' was bound to Miami in the same sentence."""
        payload = _load_fixture("draft_36358665975_attempt1.json")
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        issues = facts.check_review(payload, last, recap)
        self.assertEqual(issues, [])
        self.assertFalse(any("11" in item and "MIA" in item for item in issues))

    def test_36358665975_attempt2_false_positives_pass(self):
        """Retry draft: 88-yard run is not a score; mixed-team sentences keep the nearest subject."""
        payload = _load_fixture("draft_36358665975_attempt2.json")
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        issues = facts.check_review(payload, last, recap)
        self.assertEqual(issues, [])
        blob = " ".join(issues)
        self.assertNotIn("88-yard run", blob)
        self.assertNotIn("11-yard catch", blob)

    def test_20260928_0010_td_after_karlaftis_int_is_reversed(self):
        """Live edition 2026-09-28-0010 put Kelce's TD after the Karlaftis INT."""
        payload = _load_fixture("edition_2026-09-28-0010_td_after_int.json")
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        issues = facts.check_review(payload, last, recap)
        blob = " ".join(issues)
        self.assertTrue(any("play order" in item for item in issues), issues)
        self.assertIn("after the Karlaftis interception", blob)
        self.assertTrue(any("2:55" in item or "11" in item for item in issues), issues)

    def test_score_after_turnover_in_espn_order_passes(self):
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        narrative = self._review(
            lede=(
                "The Karlaftis interception after the 11-yard touchdown "
                "closed Miami's last real chance. "
                "An 11-yard touchdown following the 34-yard field goal made it 24-10."
            ),
        )
        self.assertEqual(facts.check_review(narrative, last, recap), [])

    def test_after_without_play_link_is_ignored(self):
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        narrative = self._review(
            lede="Play-action boot only after the run fake is real.",
        )
        self.assertEqual(facts.check_review(narrative, last, recap), [])

    def test_daytime_kickoff_accepts_night_idioms(self):
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        narrative = self._review(
            lede=(
                "Kansas City scored 24 points without needing a 30-point night. "
                "Bolton's 11-tackle night was the defensive headline. "
                "Can Spagnuolo keep posting 10-point nights if the takeaways dry up?"
            )
        )
        issues = facts.check_review(narrative, last, recap)
        self.assertFalse(any("night" in item for item in issues), issues)

    def test_daytime_kickoff_rejects_this_game_night(self):
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        narrative = self._review(lede="The Miami night was a reminder.")
        issues = facts.check_review(narrative, last, recap)
        self.assertTrue(any("night" in item for item in issues), issues)

    def test_echoed_writer_instruction_is_rejected(self):
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        narrative = {
            "runOfShow": [
                {
                    "talkTrack": (
                        "Do not call it a night game — it was Sun Sep 27, "
                        "12:00 PM CT, midday."
                    )
                }
            ]
        }
        issues = facts.check_review(narrative, last, recap)
        blob = " ".join(issues)
        self.assertIn("echoed writer instruction", blob)
        self.assertTrue(any("night" in item for item in issues), issues)

    def test_private_night_guidance_is_not_in_the_user_prompt(self):
        self.assertIn("[PRIVATE WRITER INSTRUCTION", prompts.SYSTEM_PROMPT)
        self.assertIn("never copy", prompts.SYSTEM_PROMPT)
        self.assertIn("never write the words night or nights", prompts.SYSTEM_PROMPT)
        self.assertIn("11-tackle game", prompts.SYSTEM_PROMPT)
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        last["kickoff"] = "Sun, Sep 27 · 12:00 PM CT"
        ph = {
            "type": "regular",
            "label": "Week 4",
            "week": 4,
            "mode": "review",
            "lastGame": last,
            "nextGame": None,
        }
        text = prompts.build_user_prompt({"news": [], "lastGameRecap": {}}, ph, [])
        self.assertIn("KICKOFF WINDOW", text)
        self.assertNotIn("This was not a night game", text)
        self.assertNotIn("do not write 'night'", text)

    def test_fumble_credit_rejects_tranquill_for_sneed(self):
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        narrative = self._review(
            lede=(
                "Drue Tranquill blew up a Willis keeper that became a fumble. "
                "Two Miami turnovers — Willis fumble (Tranquill)."
            )
        )
        issues = facts.check_review(narrative, last, recap)
        blob = " ".join(issues)
        self.assertIn("turnover credit", blob)
        self.assertIn("Tranquill", blob)

    def test_fumble_credit_accepts_sneed(self):
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        narrative = self._review(
            lede="L'Jarius Sneed forced and recovered the Willis fumble."
        )
        issues = facts.check_review(narrative, last, recap)
        self.assertFalse(any("turnover credit" in item for item in issues), issues)

    def test_only_vertical_shot_is_false(self):
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        narrative = self._review(
            lede=(
                "That is the only time Kansas City asked the vertical shot "
                "to carry a drive, and Miami took it."
            )
        )
        issues = facts.check_review(narrative, last, recap)
        self.assertTrue(any("absolute claim" in item for item in issues), issues)

    def test_only_deep_middle_miss_is_false(self):
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        narrative = {
            "matchups": [
                {
                    "note": (
                        "The Rodriguez INT was the only deep-middle miss."
                    )
                }
            ]
        }
        issues = facts.check_review(narrative, last, recap)
        self.assertTrue(any("absolute claim" in item for item in issues), issues)

    def test_unverifiable_only_claim_is_rejected(self):
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        narrative = self._review(
            lede="That was the only play Miami never solved."
        )
        issues = facts.check_review(narrative, last, recap)
        self.assertTrue(
            any("cannot be verified" in item for item in issues), issues
        )

    def test_prior_game_team_rush_rejects_walker_117(self):
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        by_team = facts.official_yards_by_team(recap, "rush")
        self.assertIn(152, by_team.get("KC", set()))
        self.assertIn(88, by_team.get("KC", set()))
        narrative = {
            "coaching": [
                {
                    "detail": (
                        "Indianapolis was 117 on the ground; Miami was 88 "
                        "and a stuff at the 12."
                    )
                }
            ]
        }
        issues = facts.check_review(narrative, last, recap)
        blob = " ".join(issues)
        self.assertIn("117", blob)
        self.assertIn("152", blob)
        self.assertFalse(any("88" in item and "disagrees" in item for item in issues), issues)

    def test_garbled_record_ordinal_is_rejected(self):
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        narrative = {"dek": "First AFC West road game of 3-0, favored -5.5."}
        issues = facts.check_review(narrative, last, recap)
        self.assertTrue(any("garbled record" in item for item in issues), issues)

    def test_karen_qa_0010_fixture_fails_each_claim(self):
        payload = _load_fixture("edition_2026-09-28-0010_karen_qa.json")
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        issues = facts.check_review(payload, last, recap)
        blob = " ".join(issues)
        self.assertIn("echoed writer instruction", blob)
        self.assertIn("turnover credit", blob)
        self.assertIn("absolute claim", blob)
        self.assertIn("117", blob)
        self.assertIn("garbled record", blob)

    def test_parse_plays_credits_sneed_not_tranquill(self):
        drives = _load_fixture("espn_fumble_drive.json")
        plays = collect.parse_plays(drives)
        self.assertEqual(len(plays), 1)
        self.assertEqual(plays[0]["kind"], "fumble")
        self.assertIn("Sneed", plays[0]["forcedBy"])
        self.assertIn("Sneed", plays[0]["recoveredBy"])
        self.assertNotIn("Tranquill", plays[0]["forcedBy"])

    def test_repair_logs_every_dropped_sentence(self):
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        narrative = {
            "headline": "Keep this title",
            "lastGameReview": {
                "lede": (
                    "Kansas City finished 24–10 against Miami. "
                    "The Miami night was a reminder."
                )
            },
        }
        issues = facts.check_review(narrative, last, recap)
        repaired = facts.repair_offending_copy(narrative, issues, last)
        dropped = facts.dropped_sentences(narrative, repaired)
        self.assertTrue(any("night" in s for s in dropped), dropped)
        self.assertEqual(facts.check_review(repaired, last, recap), [])

    def test_real_wrong_subject_still_fails(self):
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        narrative = self._review(
            lede=(
                "Kansas City's 3-yard touchdown was the opener. "
                "Butker's 37-yard field goal was the tax."
            ),
        )
        issues = facts.check_review(narrative, last, recap)
        self.assertTrue(any("3" in item and "KC" in item for item in issues), issues)
        self.assertTrue(any("37" in item and "KC" in item for item in issues), issues)

    def test_rejected_sentence_fixtures_match_labels(self):
        """Every logged rejection is a labelled fixture: accept FPs, reject real errors."""
        catalog = _load_fixture("rejected_sentences.json")
        recap = _load_fixture(catalog["recapFixture"])
        last = catalog["lastGame"]
        labels = {item["id"]: item["label"] for item in catalog["items"]}
        self.assertEqual(
            set(labels.values()),
            {"false-positive", "real-error"},
        )
        for item in catalog["items"]:
            narrative = self._review(lede=item["sentence"])
            issues = facts.check_review(narrative, last, recap)
            blob = " ".join(issues)
            if item["label"] == "false-positive":
                self.assertEqual(
                    issues,
                    [],
                    f"{item['id']} should pass: {issues}",
                )
                self.assertNotIn(item["snippet"], blob)
            else:
                self.assertTrue(
                    issues,
                    f"{item['id']} should fail check_review",
                )
                self.assertTrue(
                    item["snippet"].split()[0].lower() in blob.lower()
                    or any(
                        token in blob
                        for token in re.findall(r"\d+", item["snippet"])
                    ),
                    f"{item['id']} issues {issues} should mention {item['snippet']!r}",
                )

    def test_score_order_and_zero_zero_are_allowed(self):
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        narrative = self._review(
            lede="It was a 0-0 game, then they were leading 7-14, and they lost 10-24 on no ledger that matters.",
            whatDidnt=["The INT kept a 10-24 game from looking closer."],
        )
        self.assertEqual(facts.check_review(narrative, last, recap), [])

    def test_qualified_td_count_uses_quarter_window(self):
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        early = self._review(lede="The two early Kansas City touchdowns set the tone.")
        self.assertEqual(facts.check_review(early, last, recap), [])
        first_q = self._review(
            lede="The two Kansas City touchdowns in the first quarter never happened."
        )
        issues = facts.check_review(first_q, last, recap)
        self.assertTrue(any("touchdown count" in item and "KC" in item for item in issues), issues)

    def test_repair_drops_only_offending_sentences(self):
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        narrative = {
            "headline": "Keep this title",
            "lastGameReview": {
                "lede": (
                    "Kansas City finished 24–10 against Miami. "
                    "Kansas City closed it as an 18-19 game."
                ),
                "analysis": ["Kelce scored from the 12."],
            },
        }
        issues = facts.check_review(narrative, last, recap)
        self.assertTrue(issues)
        repaired = facts.repair_offending_copy(narrative, issues, last)
        self.assertEqual(facts.check_review(repaired, last, recap), [])
        lede = repaired["lastGameReview"]["lede"]
        self.assertIn("24", lede)
        self.assertNotIn("18-19", lede)
        self.assertEqual(repaired["lastGameReview"].get("analysis") or [], [])
        self.assertEqual(repaired["headline"], "Keep this title")

    def test_rejects_wrong_final_when_recap_empty(self):
        narrative = self._review(lede="Kansas City won it KC 24–7.")
        issues = facts.check_review(narrative, self.LAST, {})
        self.assertTrue(any("24-7" in item or "24–7" in item for item in issues))

    def test_skips_when_game_not_final(self):
        live = dict(self.LAST)
        live["completed"] = False
        narrative = self._review(whatDidnt=["The INT kept a 14-10 game alive."])
        self.assertEqual(facts.check_review(narrative, live, self.RECAP), [])

    def test_fact_check_retries_twice_then_fails_on_analysis_drop(self):
        bad = {
            "headline": "Fresh title",
            "dek": "Fresh dek",
            "theEdge": "Fresh edge",
            "lastGameReview": {
                "lede": "The INT kept a 14-10 game alive into the fourth.",
                "analysis": ["Kelce scored from the 12."],
            },
        }
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        last["kickoff"] = "Sun, Sep 27 · 12:00 PM CT"
        week4 = {
            "id": "w4",
            "week": 4,
            "seasonType": "reg",
            "date": "2026-10-04T20:25:00Z",
            "opponent": "Las Vegas Raiders",
            "completed": False,
            "inProgress": False,
            "kcScore": None,
            "oppScore": None,
            "kickoff": "Sun, Oct 4 · 3:25 PM CT",
        }
        ph = {
            "type": "regular",
            "label": "Week 4",
            "week": 4,
            "mode": "review",
            "edition": "2026 Week 4 · Week 3 Review",
            "lastGame": last,
            "nextGame": week4,
            "liveGame": None,
        }
        llm = Mock(side_effect=[(bad, "grok"), (bad, "grok"), (bad, "grok")])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            narrative_json = root / "narrative.json"
            wire_json = root / "wire.json"
            editions = root / "editions"
            editions.mkdir()
            archive = root / "archive.json"
            archive.write_text("[]\n", encoding="utf-8")
            with patch.object(
                collect,
                "collect_all",
                return_value={"schedule": [last, week4], "news": [], "markets": {}},
            ), patch.object(
                collect, "fetch_game_recap", return_value=self.RECAP
            ), patch.object(
                phase, "detect", return_value=ph
            ), patch.object(
                phase, "next_games", return_value=[week4]
            ), patch.object(
                phase, "any_live", return_value=False
            ), patch.object(
                odds, "collect_markets", return_value={}
            ), patch.object(
                providers, "generate_via_llm", llm
            ), patch.object(
                generate, "_render_diagrams"
            ), patch.object(
                generate, "_write_schedule"
            ), patch.object(
                generate.config, "NARRATIVE_JSON", narrative_json
            ), patch.object(
                generate.config, "WIRE_JSON", wire_json
            ), patch.object(
                generate.config, "EDITIONS_DIR", editions
            ), patch.object(
                generate.config, "ARCHIVE_JSON", archive
            ), patch.object(
                generate.config, "REPAIR_JSON", root / "repair.json"
            ):
                rc = generate.main(["--provider", "grok"])
            self.assertEqual(rc, 1)
            self.assertEqual(llm.call_count, 3)
            retry_user = llm.call_args_list[1].args[2]
            self.assertIn("FACT CHECK RETRY", retry_user)
            self.assertIn("FACT CHECK RETRY", llm.call_args_list[2].args[2])
            self.assertFalse(narrative_json.exists())
            self.assertEqual(list(editions.iterdir()), [])

    def test_fact_check_one_non_analysis_drop_publishes_and_holds(self):
        bad = {
            "headline": "Fresh title",
            "dek": "Fresh dek",
            "theEdge": "The INT kept a 14-10 game alive into the fourth.",
            "lastGameReview": {
                "lede": "Kansas City won 24-10.",
                "analysis": ["Kelce scored from 11 yards."],
            },
        }
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        last["kickoff"] = "Sun, Sep 27 · 12:00 PM CT"
        week4 = {
            "id": "w4",
            "week": 4,
            "seasonType": "reg",
            "date": "2026-10-04T20:25:00Z",
            "opponent": "Las Vegas Raiders",
            "completed": False,
            "inProgress": False,
            "kcScore": None,
            "oppScore": None,
            "kickoff": "Sun, Oct 4 · 3:25 PM CT",
        }
        ph = {
            "type": "regular",
            "label": "Week 4",
            "week": 4,
            "mode": "review",
            "edition": "2026 Week 4 · Week 3 Review",
            "lastGame": last,
            "nextGame": week4,
            "liveGame": None,
        }
        llm = Mock(side_effect=[(bad, "grok"), (bad, "grok"), (bad, "grok")])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            narrative_json = root / "narrative.json"
            repair_json = root / "repair.json"
            wire_json = root / "wire.json"
            editions = root / "editions"
            editions.mkdir()
            archive = root / "archive.json"
            archive.write_text("[]\n", encoding="utf-8")
            with patch.object(
                collect,
                "collect_all",
                return_value={"schedule": [last, week4], "news": [], "markets": {}},
            ), patch.object(
                collect, "fetch_game_recap", return_value=self.RECAP
            ), patch.object(
                phase, "detect", return_value=ph
            ), patch.object(
                phase, "next_games", return_value=[week4]
            ), patch.object(
                phase, "any_live", return_value=False
            ), patch.object(
                odds, "collect_markets", return_value={}
            ), patch.object(
                providers, "generate_via_llm", llm
            ), patch.object(
                generate, "_render_diagrams"
            ), patch.object(
                generate, "_write_schedule"
            ), patch.object(
                generate.config, "NARRATIVE_JSON", narrative_json
            ), patch.object(
                generate.config, "WIRE_JSON", wire_json
            ), patch.object(
                generate.config, "EDITIONS_DIR", editions
            ), patch.object(
                generate.config, "ARCHIVE_JSON", archive
            ), patch.object(
                generate.config, "REPAIR_JSON", repair_json
            ):
                rc = generate.main(["--provider", "grok"])
            self.assertEqual(rc, 0, repair_json.read_text() if repair_json.exists() else "no repair")
            written = json.loads(narrative_json.read_text(encoding="utf-8"))
            blob = facts.edition_text(written)
            self.assertNotIn("14-10", blob)
            self.assertIn("24-10", blob)
            repair = json.loads(repair_json.read_text(encoding="utf-8"))
            self.assertTrue(repair["holdAutomerge"])
            self.assertTrue(repair["droppedSentences"])

    def test_int_credit_ignores_common_words_and_passers(self):
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        narrative = self._review(
            lede=(
                "Mahomes was 20-of-24 for 246 yards, two touchdowns, and "
                "one interception. He was intercepted by J. Rodriguez. "
                "That interception sat next to a late interception scare "
                "and an interception that Karlaftis actually made."
            )
        )
        issues = facts.check_review(narrative, last, recap)
        blob = " ".join(issues)
        self.assertNotIn("was is not who ESPN", blob)
        self.assertNotIn("one is not who ESPN", blob)
        self.assertNotIn("an is not who ESPN", blob)
        self.assertNotIn("late is not who ESPN", blob)
        self.assertNotIn("that is not who ESPN", blob)
        self.assertNotIn("Mahomes is not who ESPN", blob)

    def test_eleven_yard_td_is_kc_even_next_to_miami(self):
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        narrative = self._review(
            lede=(
                "Rice's 34-yard catch to the Miami 15 finally set up the "
                "11-yard touchdown for 24-10."
            )
        )
        issues = facts.check_review(narrative, last, recap)
        self.assertFalse(any("11" in item and "MIA" in item for item in issues), issues)

    def test_missed_fifty_yard_field_goal_is_accepted(self):
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        narrative = self._review(
            lede="Harrison Butker then missed a 50-yard field goal wide left."
        )
        issues = facts.check_review(narrative, last, recap)
        self.assertFalse(any("50" in item for item in issues), issues)

    def test_split_sentences_keeps_initials(self):
        parts = facts._split_sentences(
            "Mahomes was intercepted by J. Rodriguez at the Miami 29. "
            "L. Sneed forced the fumble."
        )
        self.assertEqual(len(parts), 2)
        self.assertIn("J. Rodriguez", parts[0])
        self.assertIn("L. Sneed", parts[1])

    def test_repair_orphans_fail_loud(self):
        leftover = {
            "lastGameReview": {
                "lede": "Those are the snaps Bieniemy has to clean.",
                "analysis": ["Rodriguez at the Miami 29."],
            }
        }
        issues = facts.check_repair_orphans(
            leftover,
            ["The second-quarter self-inflicted drive deaths."],
        )
        self.assertTrue(any("orphan" in item for item in issues), issues)
        self.assertTrue(any("fragment" in item or "dangling" in item for item in issues), issues)

    def test_pr114_regression_accepts_real_copy_and_rejects_errors(self):
        catalog = _load_fixture("edition_pr114_karen_qa.json")
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        for sentence in catalog["accept"]:
            issues = facts.check_review(self._review(lede=sentence), last, recap)
            self.assertEqual(issues, [], f"should accept {sentence!r}: {issues}")
        for sentence in catalog["reject"]:
            issues = facts.check_review(self._review(lede=sentence), last, recap)
            self.assertTrue(issues, f"should reject {sentence!r}")

    def test_run_36389259508_false_positives_pass_and_writer_errors_fail(self):
        catalog = _load_fixture("edition_run_36389259508.json")
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        for sentence in catalog["accept"]:
            issues = facts.check_review(self._review(lede=sentence), last, recap)
            self.assertEqual(issues, [], f"should accept {sentence!r}: {issues}")
        for sentence in catalog["reject"]:
            issues = facts.check_review(self._review(lede=sentence), last, recap)
            self.assertTrue(issues, f"should reject {sentence!r}")

    def test_play_order_binds_after_to_named_gain(self):
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        narrative = self._review(
            lede=(
                "Kenneth Walker III finished a 10-yard run after Mahomes "
                "hit Kelce for 48 yards on the first snap."
            )
        )
        issues = facts.check_review(narrative, last, recap)
        self.assertFalse(any("play order" in item for item in issues), issues)

    def test_miami_interception_is_team_credit(self):
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        narrative = self._review(lede="The Miami interception flipped the field.")
        issues = facts.check_review(narrative, last, recap)
        self.assertFalse(any("turnover credit" in item for item in issues), issues)

    def test_night_snippet_does_not_drop_nightmare(self):
        narrative = {
            "lastGameReview": {
                "lede": (
                    "It was a 382-yard night in Miami. "
                    "The Chargers' nightmare start is a different story."
                )
            }
        }
        repaired = facts.repair_offending_copy(
            narrative, ["kickoff is midday; do not write 'night' about the game"]
        )
        blob = facts.edition_text(repaired)
        self.assertNotIn("382-yard night", blob)
        self.assertIn("nightmare", blob)

    def test_retry_instruction_lists_rejections_and_per_game_table(self):
        recap = _load_fixture("espn_401872952_recap.json")
        text = facts.retry_instruction(
            ["team rushing 18 disagrees with ESPN 88 for KC ('18 team rushing yards')"],
            recap,
        )
        self.assertIn("18 team rushing yards", text)
        self.assertIn("PER-GAME STAT TABLE", text)
        self.assertIn("88", text)
        self.assertIn("do not repeat the flagged wording", text)

    def test_run_36444997578_touch_penalty_look_and_clock_fail(self):
        catalog = _load_fixture("edition_run_36444997578.json")
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        for sentence in catalog["accept"]:
            issues = facts.check_review(self._review(lede=sentence), last, recap)
            self.assertEqual(issues, [], f"should accept {sentence!r}: {issues}")
        for sentence in catalog["reject"]:
            issues = facts.check_review(self._review(lede=sentence), last, recap)
            self.assertTrue(issues, f"should reject {sentence!r}")
        retry = facts.retry_instruction(
            facts.check_review(
                self._review(lede=catalog["reject"][0]), last, recap
            ),
            recap,
        )
        self.assertIn("illegal-use", retry.lower())
        self.assertIn("do not repeat the flagged wording", retry)

    def test_v8_word_numbers_sacks_hits_and_sentence_split(self):
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        must_reject = [
            "Mahomes threw 40 passes.",
            "Mahomes was sacked three times.",
            "Mahomes was hit 12 times.",
            "Nourzad was eligible on both snaps.",
            "Kansas City scored in the first 90 seconds.",
        ]
        for sentence in must_reject:
            issues = facts.check_review(self._review(lede=sentence), last, recap)
            self.assertTrue(issues, f"should reject {sentence!r}")
        recap_18 = json.loads(json.dumps(recap))
        recap_18["touches"][0]["touches"] = 18
        twenty = facts.check_review(
            self._review(lede="Walker already handled twenty touches."),
            last,
            recap_18,
        )
        self.assertTrue(twenty, "spelled-out twenty touches must compare to ESPN")
        self.assertEqual(
            facts.check_review(
                self._review(lede="Walker already handled twenty touches."),
                last,
                recap,
            ),
            [],
        )
        live_143 = (
            "Walker’s 10-yard touchdown came with J. Moore eligible; of the "
            "two stuffed snaps at the Miami 12, only the Q2 6:15 snap had "
            "H. Nourzad reported eligible."
        )
        self.assertIn(
            "H. Nourzad",
            facts._sentence_at(live_143, live_143.index("snaps")),
        )
        self.assertEqual(
            facts.check_review(self._review(lede=live_143), last, recap),
            [],
        )

    def test_xo_visible_caption_must_match_why(self):
        svg, _ = diagrams.render_concept(
            "play_action_boot",
            title="Boot",
            blurb="The Miami passing tape still produced the 48- and 24-yard shots.",
        )
        self.assertIn("48- and 24-yard shots", diagrams.visible_caption(svg))
        self.assertNotIn("too much punishment", diagrams.visible_caption(svg))
        self.assertTrue(
            diagrams.caption_matches_why(
                diagrams.visible_caption(svg),
                "The Miami passing tape still produced the 48- and 24-yard shots.",
            )
        )
        stale = svg.replace(
            "48- and 24-yard shots",
            "Play-action was the cleanest part and too much punishment",
        )
        self.assertFalse(
            diagrams.caption_matches_why(
                diagrams.visible_caption(stale),
                "The Miami passing tape still produced the 48- and 24-yard shots.",
            )
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "xo-play_action_boot.svg"
            path.write_text(stale, encoding="utf-8")
            narrative = {
                "xsandos": [
                    {
                        "concept": "play_action_boot",
                        "why": "The Miami passing tape still produced the 48- and 24-yard shots.",
                        "diagram": path.name,
                    }
                ]
            }
            with patch.object(facts.config, "PUBLIC_DIR", Path(tmp)):
                issues = facts.check_diagram_captions(narrative)
            self.assertTrue(issues)

    def test_parse_usage_penalty_and_eligible(self):
        drives = {
            "previous": [
                {
                    "team": {"abbreviation": "MIA"},
                    "plays": [
                        {
                            "period": {"number": 4},
                            "clock": {"displayValue": "0:47"},
                            "text": (
                                "(Shotgun) M.Willis pass short right intended "
                                "for G.Dulcich INTERCEPTED by C.Roland-Wallace "
                                "at MIA 42. PENALTY on KC-G.Karlaftis, Illegal "
                                "Use of Hands, 5 yards, enforced at MIA 40 - "
                                "No Play."
                            ),
                        }
                    ],
                },
                {
                    "team": {"abbreviation": "KC"},
                    "plays": [
                        {
                            "period": {"number": 2},
                            "clock": {"displayValue": "6:15"},
                            "text": (
                                "H.Nourzad reported in as eligible.  "
                                "K.Walker up the middle to MIA 12 for no gain."
                            ),
                        }
                    ],
                },
            ]
        }
        pens = collect.parse_penalties(drives)
        self.assertEqual(len(pens), 1)
        self.assertEqual(pens[0]["player"], "G.Karlaftis")
        self.assertIn("Roland-Wallace", pens[0]["wiped"])
        elig = collect.parse_eligible_reports(drives)
        self.assertEqual(elig[0]["player"], "H.Nourzad")
        self.assertEqual(elig[0]["clock"], "6:15")

    def test_run_36394515450_false_positives_pass_and_writer_errors_fail(self):
        catalog = _load_fixture("edition_run_36394515450.json")
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        for sentence in catalog["accept"]:
            issues = facts.check_review(self._review(lede=sentence), last, recap)
            self.assertEqual(issues, [], f"should accept {sentence!r}: {issues}")
        for sentence in catalog["reject"]:
            issues = facts.check_review(self._review(lede=sentence), last, recap)
            self.assertTrue(issues, f"should reject {sentence!r}")
        retry = facts.retry_instruction(
            facts.check_review(
                self._review(lede=catalog["reject"][0]), last, recap
            ),
            recap,
        )
        self.assertIn("play order", retry)

    def test_xsandos_first_lb_is_not_an_absolute_claim(self):
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        narrative = {
            "lastGameReview": {
                "lede": "Kansas City beat Miami 24-10.",
                "analysis": ["Kelce scored on an 11-yard catch."],
            },
            "xsandos": [
                {
                    "title": "Clear-out vertical",
                    "situation": "1st-and-10 vs Las Vegas",
                    "why": (
                        "Throw a clear-out vertical and wall the first LB "
                        "versus a Cover-2 shell on 1st-and-10."
                    ),
                    "coaching": (
                        "The Rodriguez INT was the only deep-middle miss."
                    ),
                }
            ],
        }
        issues = facts.check_review(narrative, last, recap)
        self.assertFalse(any("first-claim" in item for item in issues), issues)
        self.assertFalse(any("absolute claim" in item for item in issues), issues)

    def test_prompt_allowed_facts_lists_box_and_scoring_order(self):
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        last["kickoff"] = "Sun, Sep 27 · 12:00 PM CT"
        ph = {
            "type": "regular",
            "label": "Week 4",
            "week": 4,
            "mode": "review",
            "lastGame": last,
            "nextGame": None,
        }
        text = prompts.build_user_prompt({"news": [], "lastGameRecap": recap}, ph, [])
        self.assertIn("ALLOWED FACTS", text)
        self.assertIn("firstDowns=18", text)
        self.assertIn("firstDowns=19", text)
        self.assertIn("rushingYards=88", text)
        self.assertIn("rushingYards=119", text)
        self.assertIn("possessionTime=25:39", text)
        self.assertIn("possessionTime=34:21", text)
        self.assertIn("SCORING PLAYS IN ORDER", text)
        self.assertIn("Kenneth Walker III", text)
        self.assertIn("Ollie Gordon II", text)
        self.assertLess(text.index("ALLOWED FACTS"), text.index("LAST-GAME BOX"))
        self.assertLess(
            text.index("SCORING PLAYS IN ORDER"), text.index("LAST-GAME BOX")
        )
        self.assertIn("PLAYER TOUCHES", text)
        self.assertIn("20 touches", text)
        self.assertIn("PASS ATTEMPTS / SACKS", text)
        self.assertIn("0 sacks", text)
        self.assertIn("PENALTIES", text)
        self.assertIn("G.Karlaftis", text)
        self.assertIn("ELIGIBLE-PLAYER REPORTS", text)
        self.assertIn("H.Nourzad", text)
        self.assertIn("Do not write too many hits or punishment", text)
        self.assertIn("play-action", prompts.SYSTEM_PROMPT)
        self.assertIn("survival tape", prompts.SYSTEM_PROMPT)
        self.assertIn("correction-note", prompts.SYSTEM_PROMPT)

    def test_narrative_pr_lists_drops_and_holds_automerge(self):
        yaml = (
            Path(__file__).resolve().parents[2]
            / ".github"
            / "workflows"
            / "narrative.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("narrative_repair.json", yaml)
        self.assertIn("Dropped sentences", yaml)
        self.assertIn("HOLD_MERGE", yaml)
        self.assertIn("Holding automerge", yaml)
        self.assertLess(yaml.index("HOLD_MERGE"), yaml.index("gh pr merge"))


class ArchiveDates(unittest.TestCase):
    """Stored teaser dates must be CT calendar dates, not UTC rollover."""

    def test_archive_and_edition_labels_use_ct_dates(self):
        archive = _fixture("archive_ct_dates.json").read_text(encoding="utf-8")
        self.assertNotIn("Tue Sep 15", archive)
        self.assertNotIn("Mon Sep 21", archive)
        self.assertNotIn("Sun Sep 21", archive)
        self.assertNotIn("Mon Sep 15", archive)
        blob = archive
        for name in (
            "edition_2026-09-18-1502.json",
            "edition_2026-09-14-1701.json",
        ):
            blob += _fixture(name).read_text(encoding="utf-8")
        self.assertNotIn("Tue Sep 15", blob)
        self.assertNotIn("Week 2 · Mon Sep 21", blob)
        self.assertNotIn("Week 2 · Sun Sep 21", blob)
        self.assertNotIn("Week 1 · Tue Sep 15", blob)
        self.assertNotIn("Week 1 · Mon Sep 15", blob)
        self.assertIn("Mon Sep 14", blob)
        self.assertIn("Sun Sep 20", blob)

    def test_tests_do_not_read_live_edition_json(self):
        """A fresh daily edition must not be able to break the gates.

        Temp files named narrative.json are fine. Only live data/ edition
        paths (and DATA_DIR joins that resolve to them) are forbidden.
        """
        tests_dir = Path(__file__).resolve().parent
        slug = "narrative"
        needles = (
            f"data/{slug}.json",
            f"data/{slug}_archive.json",
            f"data/{slug}_editions",
            f'/ "data" / "{slug}.json"',
            f"/ 'data' / '{slug}.json'",
            f'/ "data" / "{slug}_archive.json"',
            f"/ 'data' / '{slug}_archive.json'",
            f'/ "data" / "{slug}_editions"',
            f"/ 'data' / '{slug}_editions'",
            f'DATA_DIR / "{slug}.json"',
            f"DATA_DIR / '{slug}.json'",
            f'DATA_DIR / "{slug}_archive.json"',
            f"DATA_DIR / '{slug}_archive.json'",
            f'DATA_DIR / "{slug}_editions"',
            f"DATA_DIR / '{slug}_editions'",
        )
        offenders = []
        for path in tests_dir.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for needle in needles:
                if needle in text:
                    offenders.append(f"{path.name}: {needle}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()


class StreamingAndOfflineDekTests(unittest.TestCase):
    """Sep 2026 outage: grok-4.6 non-streamed calls idled out, offline dek was fixed."""

    def test_read_stream_accumulates_sse_content(self):
        from tools.chiefs_narrative import providers as prov

        class FakeResp:
            encoding = None

            def iter_lines(self, decode_unicode=True):
                assert self.encoding == "utf-8"
                yield ": keep-alive"
                yield 'data: {"choices":[{"delta":{"content":"{\\"a\\":"}}]}'
                yield ""
                yield 'data: {"choices":[{"delta":{"content":"1}"}}]}'
                yield "data: [DONE]"

            def close(self):
                pass

        self.assertEqual(prov._read_stream("Grok", FakeResp()), '{"a":1}')

    def test_read_stream_empty_raises(self):
        from tools.chiefs_narrative import providers as prov

        class FakeResp:
            encoding = None

            def iter_lines(self, decode_unicode=True):
                yield "data: [DONE]"

            def close(self):
                pass

        with self.assertRaises(prov.ProviderError):
            prov._read_stream("Grok", FakeResp())

    def test_offline_dek_is_not_the_old_fixed_string(self):
        raw = offline.write(SIGNALS, WEEK_PHASE, NEXT)
        self.assertNotEqual(
            raw["dek"],
            "Last game on the tape, where the Chiefs stand, and the plan for who's next.",
        )
        self.assertIn("desk:", raw["dek"])


class XEmbedSlots(unittest.TestCase):
    """Official X embed slots: zero-embed layout stays tight; real URLs persist."""

    ROOT = Path(__file__).resolve().parents[2]
    REAL_KELCE = "https://x.com/Chiefs/status/2101838746643812537"
    REAL_TAYLOR = "https://x.com/NFL/status/2101849509382738110"

    def _normalize(self, raw):
        return schema.normalize(
            raw,
            phase=WEEK_PHASE,
            meta={
                "generatedAt": "2026-09-26T13:55:00+00:00",
                "generator": "test",
                "record": "2-0",
                "markets": {},
            },
        )

    def test_schema_zero_embeds_omits_empty_slots(self):
        raw = offline.write(
            {
                "news": [],
                "markets": {},
                "schedule": [DeskSections.LAST],
                "lastGameRecap": {
                    "kc": {"totalYards": "280"},
                    "opp": {"totalYards": "310"},
                    "oppAbbr": "TB",
                    "scoring": [],
                    "leaders": [],
                },
            },
            DeskSections.PHASE,
            NEXT,
        )
        narrative = schema.normalize(
            raw,
            phase=DeskSections.PHASE,
            meta={
                "generatedAt": "2026-09-26T13:55:00+00:00",
                "generator": "test",
                "record": "0-1",
                "markets": {},
            },
        )
        self.assertEqual(narrative["playerEmbeds"], [])
        analysis = narrative["lastGameReview"]["analysis"]
        self.assertTrue(analysis)
        for para in analysis:
            self.assertIn("body", para)
            self.assertNotIn("embed", para)

    def test_schema_keeps_two_player_embeds_and_key_play_url(self):
        narrative = self._normalize(
            {
                "headline": "Tape",
                "player_embeds": [
                    {
                        "url": self.REAL_KELCE,
                        "account": "Chiefs",
                        "label": "Travis Kelce",
                    },
                    "https://twitter.com/Chiefs/status/2101899387639115937",
                    {
                        "url": "https://x.com/Chiefs/status/2101846672024191421",
                        "account": "@Chiefs",
                    },
                ],
                "lastGameReview": {
                    "opponent": "Indianapolis Colts",
                    "result": "W",
                    "score": "KC 33–30",
                    "lede": "Overtime at Arrowhead.",
                    "analysis": [
                        "Walker set the early-down identity.",
                        {
                            "body": "Kelce was the adult in the room.",
                            "embed": {
                                "url": "https://twitter.com/Chiefs/status/2101835926779347261",
                                "account": "@Chiefs",
                            },
                        },
                    ],
                    "key_play_embeds": [
                        None,
                        None,
                    ],
                },
            }
        )
        players = narrative["playerEmbeds"]
        self.assertEqual(len(players), 2)
        self.assertEqual(players[0]["url"], self.REAL_KELCE)
        self.assertEqual(players[0]["account"], "@Chiefs")
        self.assertEqual(players[0]["label"], "Travis Kelce")
        self.assertEqual(
            players[1]["url"],
            "https://x.com/Chiefs/status/2101899387639115937",
        )
        analysis = narrative["lastGameReview"]["analysis"]
        self.assertNotIn("embed", analysis[0])
        self.assertEqual(
            analysis[1]["embed"]["url"],
            "https://x.com/Chiefs/status/2101835926779347261",
        )
        self.assertEqual(analysis[1]["embed"]["account"], "@Chiefs")

    def test_schema_key_play_embeds_align_by_index(self):
        narrative = self._normalize(
            {
                "lastGameReview": {
                    "lede": "Recap",
                    "analysis": [
                        "First paragraph, no clip.",
                        "Taylor popped the 24-yard touchdown.",
                    ],
                    "keyPlayEmbeds": [
                        "",
                        {"url": self.REAL_TAYLOR, "account": "@Colts"},
                    ],
                }
            }
        )
        analysis = narrative["lastGameReview"]["analysis"]
        self.assertNotIn("embed", analysis[0])
        self.assertEqual(analysis[1]["embed"]["url"], self.REAL_TAYLOR)
        self.assertEqual(analysis[1]["embed"]["account"], "@NFL")

    def test_schema_drops_invalid_non_status_and_invented_hosts(self):
        narrative = self._normalize(
            {
                "playerEmbeds": [
                    "https://example.com/status/1",
                    "https://x.com/Chiefs/photo/123",
                    "https://x.com/i/status/2101838746643812537",
                    "/Chiefs/status/2101838746643812537",
                    {"url": "not-a-url", "account": "@Chiefs"},
                ],
                "lastGameReview": {
                    "lede": "Recap",
                    "analysis": [
                        {
                            "body": "A paragraph",
                            "embed": {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
                        }
                    ],
                },
            }
        )
        self.assertEqual(narrative["playerEmbeds"], [])
        self.assertNotIn("embed", narrative["lastGameReview"]["analysis"][0])

    def test_template_zero_embed_path_has_no_unconditional_boxes(self):
        edition = (self.ROOT / "layouts" / "partials" / "narrative-edition.html").read_text(
            encoding="utf-8"
        )
        self.assertIn('{{ with .playerEmbeds }}', edition)
        self.assertIn("nrt-player-embeds", edition)
        self.assertIn("nrt-keyplay", edition)
        self.assertIn("reflect.IsMap", edition)
        self.assertIn("{{ if and $body $embed }}", edition)
        self.assertIn("{{ else if $body }}", edition)
        self.assertIn('partial "nrt-x-embed.html"', edition)
        # Embed markup is gated — a missing URL must not leave a reserved frame.
        self.assertNotRegex(edition, r"nrt-x-embed__frame(?![^<]*\{\{)")

    def test_embed_partial_is_official_blockquote_with_visible_credit(self):
        partial = (self.ROOT / "layouts" / "partials" / "nrt-x-embed.html").read_text(
            encoding="utf-8"
        )
        self.assertIn('class="twitter-tweet"', partial)
        self.assertIn("nrt-x-embed__credit", partial)
        self.assertIn("nrt-x-embed__fallback", partial)
        self.assertIn("View {{ $account | default \"X\" }} post on X", partial)
        self.assertIn("Source:", partial)
        self.assertIn("data-nrt-x-embed", partial)
        self.assertNotIn("widgets.js", partial)
        self.assertNotIn("pbs.twimg.com", partial)
        self.assertNotIn("video.twimg.com", partial)

    def test_js_lazy_loads_widgets_once_near_viewport(self):
        js = (self.ROOT / "public" / "js" / "main.js").read_text(encoding="utf-8")
        self.assertIn("function initNarrativeXEmbeds", js)
        self.assertIn("IntersectionObserver", js)
        self.assertIn("platform.twitter.com/widgets.js", js)
        self.assertIn("function ensureTwitterWidgets", js)
        self.assertIn("initNarrativeXEmbeds(root)", js)
        self.assertIn("NRT_X_FALLBACK_MS = 5000", js)
        self.assertIn("nrtXMarkFallback", js)
        self.assertIn("is-fallback", js)
        self.assertIn("script.onerror", js)
        self.assertIn("events?.bind?.('rendered'", js)
        self.assertIn("nrtXWidgetFromRendered", js)
        self.assertIn("event.target", js)
        ready = js[js.find("function nrtXMarkReady"):js.find("function nrtXMarkFallback")]
        self.assertIn("is-fallback", ready)
        self.assertIn("fallback.hidden = true", ready)
        self.assertLess(js.index("ensureTwitterWidgets"), js.index("initNarrativeXEmbeds"))

    def test_late_widget_recovery_sequence(self):
        script = self.ROOT / "tools" / "tests" / "nrt_x_embed_recovery.js"
        result = subprocess.run(
            ["node", str(script)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ok", result.stdout)

    def test_css_reserves_space_only_while_loading(self):
        css = (self.ROOT / "public" / "css" / "narrative.css").read_text(encoding="utf-8")
        self.assertIn(".nrt-x-embed__frame", css)
        self.assertIn(".nrt-x-embed.is-loading .nrt-x-embed__frame", css)
        frame = css[css.find(".nrt-x-embed__frame {"):css.find(".nrt-x-embed.is-loading")]
        self.assertIn("min-height: 0", frame)
        self.assertNotIn("min-height: 320px", frame)
        self.assertIn("min-height: 320px", css[css.find(".nrt-x-embed.is-loading"):])
        self.assertIn(".nrt-x-embed.is-fallback:not(.is-ready) .nrt-x-embed__frame", css)
        self.assertIn(".nrt-x-embed.is-ready .nrt-x-embed__fallback", css)
        self.assertIn("max-width: 100%", css)
        self.assertIn("overflow-x: hidden", css)
        self.assertIn("@media (min-width: 901px)", css)
        self.assertIn("grid-template-columns: minmax(0, 1fr) minmax(240px, 22rem)", css)
        label = css[css.find(".nrt-x-embed__label {"):css.find(".nrt-x-embed__label::before")]
        self.assertIn("color: #5c5660", label)
        self.assertNotIn("#8a8490", label)

    def test_latest_backfill_uses_verified_status_urls(self):
        payload = _load_fixture("edition_2026-09-26-1355.json")
        players = payload["playerEmbeds"]
        self.assertEqual(len(players), 2)
        for item in players:
            self.assertIsNotNone(schema._norm_x_embed(item))
            self.assertTrue(item["account"].startswith("@"))
        analysis = payload["lastGameReview"]["analysis"]
        self.assertIsInstance(analysis[0], str)
        self.assertIsInstance(analysis[3], str)
        self.assertEqual(analysis[1]["embed"]["account"], "@Chiefs")
        self.assertEqual(analysis[2]["embed"]["account"], "@NFL")
        self.assertEqual(
            analysis[2]["embed"]["url"],
            "https://x.com/NFL/status/2101849509382738110",
        )
        self.assertEqual(analysis[2]["embed"]["label"], "Jonathan Taylor touchdown")
        self.assertIsNotNone(schema._norm_x_embed(analysis[1]["embed"]))
        self.assertIsNotNone(schema._norm_x_embed(analysis[2]["embed"]))

    def test_older_edition_stays_on_zero_embed_path(self):
        older = _load_fixture("edition_2026-09-25-1552.json")
        self.assertFalse(older.get("playerEmbeds"))
        for para in older["lastGameReview"]["analysis"]:
            self.assertIsInstance(para, str)

    def test_prompt_does_not_ask_the_model_for_status_urls(self):
        text = prompts.build_user_prompt(SIGNALS, WEEK_PHASE, NEXT)
        self.assertNotIn("keyPlayEmbeds", text)
        self.assertNotIn("x.com/OfficialAccount", text)
        self.assertNotIn("NEVER invent a status ID", text)
        self.assertIn("Do not invent or include X/Twitter status URLs", text)
        hint = text[text.find("Return JSON with EXACTLY these keys"):]
        self.assertNotIn('"playerEmbeds"', hint)

    def test_generated_embeds_are_stripped_unless_oembed_allowlisted(self):
        fake_id = "https://x.com/Chiefs/status/9999999999999999999"
        raw = {
            "headline": "Tape",
            "playerEmbeds": [
                {"url": fake_id, "account": "@Chiefs", "label": "Hallucinated"},
                {
                    "url": self.REAL_KELCE,
                    "account": "@Chiefs",
                    "label": "Travis Kelce",
                },
            ],
            "lastGameReview": {
                "lede": "Recap",
                "analysis": [
                    {
                        "body": "Taylor popped the 24-yard touchdown.",
                        "embed": {
                            "url": self.REAL_TAYLOR,
                            "account": "@NFL",
                        },
                    },
                    {
                        "body": "A random invented clip.",
                        "embed": {"url": fake_id, "account": "@Chiefs"},
                    },
                ],
            },
        }

        def fake_oembed(url):
            if "9999999999999999999" in url:
                return None
            if "2101838746643812537" in url:
                return {"author_url": "https://x.com/Chiefs", "html": "<blockquote>"}
            if "2101849509382738110" in url:
                return {"author_url": "https://x.com/NFL", "html": "<blockquote>"}
            if "randomfan" in url:
                return {"author_url": "https://x.com/randomfan", "html": "<blockquote>"}
            return None

        narrative = self._normalize(raw)
        self.assertEqual(len(narrative["playerEmbeds"]), 2)
        x_embeds.strip_unverified_embeds(narrative, oembed_fetch=fake_oembed)
        self.assertEqual(len(narrative["playerEmbeds"]), 1)
        self.assertEqual(narrative["playerEmbeds"][0]["url"], self.REAL_KELCE)
        analysis = narrative["lastGameReview"]["analysis"]
        self.assertEqual(analysis[0]["embed"]["url"], self.REAL_TAYLOR)
        self.assertEqual(analysis[0]["embed"]["account"], "@NFL")
        self.assertNotIn("embed", analysis[1])

        narrative = self._normalize(
            {
                "playerEmbeds": [
                    {"url": "https://x.com/randomfan/status/2101838746643812538"},
                ]
            }
        )
        x_embeds.strip_unverified_embeds(narrative, oembed_fetch=fake_oembed)
        self.assertEqual(narrative["playerEmbeds"], [])

        narrative = self._normalize(
            {"playerEmbeds": [{"url": self.REAL_KELCE, "account": "@Chiefs"}]}
        )
        x_embeds.strip_unverified_embeds(
            narrative,
            oembed_fetch=lambda url: (_ for _ in ()).throw(RuntimeError("no net")),
        )
        self.assertEqual(narrative["playerEmbeds"], [])

        narrative = self._normalize(
            {"playerEmbeds": [{"url": self.REAL_KELCE, "account": "@Chiefs"}]}
        )
        x_embeds.strip_unverified_embeds(narrative, oembed_fetch=lambda url: None)
        self.assertEqual(narrative["playerEmbeds"], [])

    def test_assemble_strips_unverified_embeds_by_default(self):
        raw = {
            "headline": "Tape",
            "playerEmbeds": [
                {
                    "url": "https://x.com/Chiefs/status/9999999999999999999",
                    "account": "@Chiefs",
                }
            ],
        }
        with patch.object(x_embeds, "fetch_oembed", return_value=None):
            narrative = generate._assemble_narrative(
                raw,
                WEEK_PHASE,
                {
                    "generatedAt": "2026-09-26T13:55:00+00:00",
                    "generator": "test",
                    "record": "2-0",
                    "markets": {},
                },
                SIGNALS,
                NEXT,
            )
        self.assertEqual(narrative["playerEmbeds"], [])

    def test_official_allowlist_is_exactly_nfl_and_32_teams(self):
        verified = frozenset(
            {
                "nfl",
                "azcardinals",
                "atlantafalcons",
                "ravens",
                "buffalobills",
                "panthers",
                "chicagobears",
                "bengals",
                "browns",
                "dallascowboys",
                "broncos",
                "lions",
                "packers",
                "houstontexans",
                "colts",
                "jaguars",
                "chiefs",
                "raiders",
                "chargers",
                "ramsnfl",
                "miamidolphins",
                "vikings",
                "patriots",
                "saints",
                "giants",
                "nyjets",
                "eagles",
                "steelers",
                "49ers",
                "seahawks",
                "buccaneers",
                "titans",
                "commanders",
            }
        )
        self.assertEqual(x_embeds.OFFICIAL_X_ACCOUNTS, verified)
        self.assertEqual(len(x_embeds.OFFICIAL_X_ACCOUNTS), 33)
        rejected = {
            "bills",
            "cardinals",
            "rams",
            "texans",
            "tennesseetitans",
            "buffalobillsnfl",
            "byherbie",
            "adamteicher",
            "arrowheadpride",
            "nflnetwork",
            "espnnfl",
        }
        self.assertTrue(rejected.isdisjoint(x_embeds.OFFICIAL_X_ACCOUNTS))

    def test_verify_matches_author_url_handle_not_author_name(self):
        def fake_oembed(_url):
            return {
                "author_name": "NFL",
                "author_url": "https://x.com/randomfan",
                "html": "<blockquote>",
            }

        kept = x_embeds.verify_x_embed(
            {"url": self.REAL_KELCE, "account": "@Chiefs"},
            oembed_fetch=fake_oembed,
        )
        self.assertIsNone(kept)

        def official_url_spoofed_name(_url):
            return {
                "author_name": "Not the Chiefs",
                "author_url": "https://x.com/Chiefs",
                "html": "<blockquote>",
            }

        kept = x_embeds.verify_x_embed(
            {"url": self.REAL_KELCE},
            oembed_fetch=official_url_spoofed_name,
        )
        self.assertIsNotNone(kept)
        self.assertEqual(kept["account"], "@Chiefs")

    def test_workflow_schedule_untouched(self):
        yaml = (self.ROOT / ".github" / "workflows" / "narrative.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("37 9 * * *", yaml)
        self.assertIn("43 3 * * *", yaml)
        self.assertIn("20 7 * * 0,1,2", yaml)
