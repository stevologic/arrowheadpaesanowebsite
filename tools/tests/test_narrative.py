"""CI gates for the Chiefs Narrative engine.

Everything here runs offline — no network, no API keys — so it is a stable
merge gate. Run with:  python -m unittest discover -s tools/tests -v
"""
from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import yaml

from tools.chiefs_narrative import (
    check_workflows,
    collect,
    config,
    diagrams,
    drop_body,
    edition_overlay,
    facts,
    generate,
    odds,
    offline,
    phase,
    prompts,
    providers,
    review_gate,
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


def _workflow_run_scripts(parsed) -> list[tuple[str, str, str]]:
    """(job, step, run script) for every run step in every job."""
    scripts: list[tuple[str, str, str]] = []
    jobs = parsed.get("jobs") if isinstance(parsed, dict) else None
    if not isinstance(jobs, dict):
        return scripts
    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            continue
        for index, step in enumerate(job.get("steps") or []):
            if not isinstance(step, dict):
                continue
            script = step.get("run")
            if isinstance(script, str):
                label = step.get("name") or f"step-{index}"
                scripts.append((str(job_name), str(label), script))
    return scripts


def _quoted_py_heredoc_bodies(script: str) -> list[str]:
    """Bodies of <<'PY' ... PY heredocs. Terminator must be column 0."""
    bodies: list[str] = []
    lines = script.splitlines(keepends=True)
    i = 0
    while i < len(lines):
        if "<<'PY'" in lines[i]:
            chunk: list[str] = []
            i += 1
            closed = False
            while i < len(lines):
                raw = lines[i]
                if raw == "PY\n" or raw == "PY":
                    closed = True
                    break
                chunk.append(raw)
                i += 1
            if not closed:
                raise ValueError("unterminated <<'PY' heredoc")
            bodies.append("".join(chunk))
        i += 1
    return bodies


def _ed25519_keypair(tmp: Path) -> tuple[Path, Path]:
    """Throwaway test keypair. Never committed."""
    priv = Path(tmp) / "test_ed25519_priv.pem"
    pub = Path(tmp) / "test_ed25519_pub.pem"
    subprocess.check_call(
        ["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(priv)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.check_call(
        ["openssl", "pkey", "-in", str(priv), "-pubout", "-out", str(pub)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return priv, pub


def _sign_review_edition(priv: Path, slug: str, edition_bytes: bytes, signoff_dir: Path) -> None:
    sha = hashlib.sha256(edition_bytes).hexdigest()
    payload = f'{{"slug":"{slug}","verdict":"PASS","sha":"{sha}"}}'
    signoff_dir.mkdir(parents=True, exist_ok=True)
    msg = signoff_dir / f"{slug}.msg"
    sigbin = signoff_dir / f"{slug}.sigbin"
    msg.write_bytes(payload.encode("ascii"))
    subprocess.check_call(
        [
            "openssl",
            "pkeyutl",
            "-sign",
            "-inkey",
            str(priv),
            "-rawin",
            "-in",
            str(msg),
            "-out",
            str(sigbin),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    (signoff_dir / f"{slug}.json").write_bytes(payload.encode("ascii"))
    (signoff_dir / f"{slug}.sig").write_text(
        base64.b64encode(sigbin.read_bytes()).decode("ascii"),
        encoding="ascii",
    )
    msg.unlink()
    sigbin.unlink()


def _sign_message(priv: Path, message: bytes) -> bytes:
    """Raw 64-byte ed25519 signature over exact message bytes."""
    with tempfile.TemporaryDirectory() as tmp:
        msg = Path(tmp) / "msg"
        sigbin = Path(tmp) / "sig"
        msg.write_bytes(message)
        subprocess.check_call(
            [
                "openssl",
                "pkeyutl",
                "-sign",
                "-inkey",
                str(priv),
                "-rawin",
                "-in",
                str(msg),
                "-out",
                str(sigbin),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return sigbin.read_bytes()


def _job_step_script(parsed, job: str, *, name=None, step_id=None) -> str:
    job_doc = ((parsed or {}).get("jobs") or {}).get(job) or {}
    for step in job_doc.get("steps") or []:
        if not isinstance(step, dict):
            continue
        if name and step.get("name") == name:
            return str(step.get("run") or "")
        if step_id and step.get("id") == step_id:
            return str(step.get("run") or "")
    return ""


def _narrative_review_hold_wiring(parsed) -> dict:
    """Structured review-hold wiring from test metadata + the publish step."""
    meta = _job_step_script(
        parsed, "test", name="Record publish metadata"
    )
    script = _job_step_script(
        parsed, "update", name="Open pull request and wait for CI gates"
    ) or _job_step_script(parsed, "update", step_id="publish")
    script = meta + "\n" + script
    review_cmp = re.search(r'if \[ "\$REVIEW_HOLD" = "([^"]+)" \]', script)
    hold_assign = re.search(
        r'if \[ "\$REVIEW_HOLD" = "[^"]+" \]; then\s+HOLD_MERGE="([^"]+)"',
        script,
    )
    merge_cmp = re.search(r'if \[ "\$HOLD_MERGE" = "([^"]+)" \]', script)
    hold_idx = script.find('if [ "$HOLD_MERGE" = ')
    merge_idx = script.find("gh pr merge")
    hold_block = (
        script[hold_idx:merge_idx] if hold_idx >= 0 and merge_idx > hold_idx else ""
    )
    return {
        "script": script,
        "uses_review_requires_human": "facts.review_requires_human" in script,
        "review_hold_equals": review_cmp.group(1) if review_cmp else "",
        "hold_merge_assign": hold_assign.group(1) if hold_assign else "",
        "hold_merge_equals": merge_cmp.group(1) if merge_cmp else "",
        "hold_before_merge": hold_idx >= 0 and merge_idx > hold_idx,
        "hold_sets_publish_false": 'echo "publish=false"' in hold_block,
        "hold_adds_label": "--add-label hold" in hold_block,
        "hold_exits": "exit 0" in hold_block,
    }


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

    def test_parse_player_usage_emits_passing_touchdowns(self):
        box = {
            "players": [
                {
                    "team": {"abbreviation": "KC"},
                    "statistics": [
                        {
                            "name": "passing",
                            "keys": [
                                "completions/passingAttempts",
                                "passingYards",
                                "passingTouchdowns",
                                "sacks-sackYardsLost",
                            ],
                            "athletes": [
                                {
                                    "athlete": {"displayName": "Patrick Mahomes"},
                                    "stats": ["20/24", "246", "2", "0-0"],
                                }
                            ],
                        }
                    ],
                }
            ]
        }
        usage = collect.parse_player_usage(box)
        self.assertEqual(usage["passing"][0]["touchdowns"], 2)
        self.assertEqual(usage["passing"][0]["completions"], 20)
        self.assertEqual(usage["passing"][0]["attempts"], 24)


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
        decide = yaml.split("Decide desk mode")[1].split(
            "Refresh the published slate"
        )[0]
        self.assertIn('MODE="schedule"', decide)
        self.assertIn("37 9 * * *", decide)
        self.assertNotIn("43 3", decide)
        self.assertNotIn("20 7", decide)
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
        self.assertIn("python -m tools.chiefs_narrative.check_workflows", ci)
        self.assertLess(
            ci.index("python -m tools.chiefs_narrative.check_workflows"),
            ci.index("python -m unittest discover"),
        )
        self.assertIn("--check-edition", ci)
        self.assertIn("--diagrams-only", ci)
        self.assertLess(ci.index("--check-edition"), ci.index("--diagrams-only"))
        self.assertIn("git diff --exit-code -- public/images/narrative", ci)
        self.assertLess(
            ci.index("--diagrams-only"),
            ci.index("git diff --exit-code -- public/images/narrative"),
        )

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
        self.assertIn("head.repo.full_name == github.repository", automerge)
        self.assertIn("--match-head-commit", automerge)
        self.assertIn("gh workflow run ci.yml --ref", narrative)
        self.assertIn("workflow_dispatch", narrative)
        self.assertNotIn("gh pr checks ", narrative)
        self.assertNotIn("gh pr checks\n", narrative)
        self.assertIn("sleep 5", narrative)
        self.assertIn("sleep 10", narrative)
        self.assertLess(narrative.index("DONE_DEADLINE"), narrative.index("gh pr merge"))
        self.assertNotIn("git add -A data", narrative)
        self.assertIn("data/schedule_2026.json", narrative)
        self.assertIn("--check-edition", narrative)
        self.assertIn("--diagrams-only", narrative)
        self.assertLess(
            narrative.index("--check-edition"),
            narrative.index("--diagrams-only"),
        )
        self.assertIn("git diff --exit-code -- public/images/narrative", narrative)

    def test_human_edition_prs_still_run_check_review(self):
        """#120 was paths-ignored; human edition edits must still get gates."""
        root = Path(__file__).resolve().parents[2] / ".github" / "workflows"
        qa = (root / "edition-qa.yml").read_text(encoding="utf-8")
        self.assertIn("github-actions[bot]", qa)
        self.assertIn("--check-edition", qa)
        self.assertIn("--diagrams-only", qa)
        self.assertLess(qa.index("--check-edition"), qa.index("--diagrams-only"))
        self.assertIn("git add -A -- data public/images/narrative", qa)
        self.assertLess(
            qa.index("git add -A -- data public/images/narrative"),
            qa.index("--diagrams-only"),
        )
        self.assertIn(
            "git diff --quiet -- public/images/narrative && test -z \"$(git ls-files --others --exclude-standard -- public/images/narrative)\"",
            qa,
        )
        self.assertLess(
            qa.index("--diagrams-only"),
            qa.index("git diff --quiet -- public/images/narrative"),
        )
        self.assertNotIn("git diff --exit-code -- public/images/narrative", qa)
        self.assertIn("persist-credentials: false", qa)
        self.assertGreaterEqual(qa.count("persist-credentials: false"), 3)
        self.assertIn("--match-head-commit", qa)
        self.assertIn("edition_overlay", qa)
        self.assertIn('gh workflow run "Deploy Hugo site to GitHub Pages"', qa)
        self.assertIn('--repo "${GITHUB_REPOSITORY}"', qa)
        self.assertIn("data/schedule_2026.json", qa)
        self.assertIn("Checkout main tools", qa)
        self.assertIn("github.event.pull_request.base.sha", qa)
        self.assertIn("repository: ${{ github.repository }}", qa)
        self.assertIn(".pr-head", qa)
        self.assertIn("Overlay edition data from the PR head", qa)
        self.assertIn("path: .pr-head", qa)
        self.assertIn("python3 -B", qa)
        self.assertIn("PYTHONDONTWRITEBYTECODE", qa)
        self.assertIn("git fetch", qa)
        self.assertIn("--base", qa)
        self.assertIn("--head", qa)
        self.assertIn("fetch-depth: 0", qa)
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

    def test_edition_qa_svg_gate_uses_overlay_index(self):
        """Staged overlay is the baseline: caption edits pass, tampered SVG fails."""
        slug = "2026-09-28-1547"
        rel = Path("public") / "images" / "narrative" / slug / "xo-play_action_boot.svg"
        main_svg = "<svg><text>old caption</text></svg>\n"
        pr_svg = "<svg><text>new caption</text></svg>\n"
        tampered = "<svg><rect id=\"pwn\"/><text>new caption</text></svg>\n"
        data_rel = Path("data") / ("narrative" + ".json")
        edition = (
            '{"slug":"%s","xsandos":[{"concept":"play_action_boot"}]}\n' % slug
        )

        def git(cwd, *args, check=True):
            return subprocess.run(
                ["git", *args],
                cwd=cwd,
                check=check,
                capture_output=True,
                text=True,
            )

        def write_tree(root: Path, files: dict[str, str]) -> None:
            for rel_path, content in files.items():
                dest = root / rel_path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(content, encoding="utf-8")

        def stage_and_gate(repo: Path, overlay_files: dict[str, str], rendered: str) -> int:
            src = repo / "pr-head"
            if src.exists():
                shutil.rmtree(src)
            write_tree(src, overlay_files)
            copied = edition_overlay.overlay_edition_data(src, repo)
            self.assertGreater(copied, 0)
            git(repo, "add", "-A", "--", "data", "public/images/narrative")
            (repo / rel).write_text(rendered, encoding="utf-8")
            diff = git(repo, "diff", "--quiet", "--", "public/images/narrative", check=False)
            extra = git(
                repo,
                "ls-files",
                "--others",
                "--exclude-standard",
                "--",
                "public/images/narrative",
            )
            if diff.returncode != 0 or extra.stdout.strip():
                return 1
            return 0

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            git(repo, "init")
            git(repo, "config", "user.email", "ci@example.com")
            git(repo, "config", "user.name", "CI")
            write_tree(repo, {rel.as_posix(): main_svg, data_rel.as_posix(): edition})
            git(repo, "add", "-A")
            git(repo, "commit", "-m", "main")

            legit = stage_and_gate(
                repo,
                {rel.as_posix(): pr_svg, data_rel.as_posix(): edition},
                pr_svg,
            )
            self.assertEqual(legit, 0, "caption edit + matching render must pass")

            git(repo, "reset", "--hard", "HEAD")
            git(repo, "clean", "-fd")
            tamper = stage_and_gate(
                repo,
                {rel.as_posix(): tampered, data_rel.as_posix(): edition},
                pr_svg,
            )
            self.assertEqual(tamper, 1, "tampered drawing must fail byte-match")

    def test_edition_overlay_fails_closed_on_disallowed_paths(self):
        slug = "2026-09-28-1547"
        data_rel = Path("data") / ("narrative" + ".json")
        current = Path("public") / "images" / "narrative" / slug / "xo-play_action_boot.svg"
        stray = Path("public") / "images" / "narrative" / "2099-01-01-0000" / "xo-mesh.svg"
        older = Path("public") / "images" / "narrative" / "2026-09-28-0142" / "xo-zone_blitz.svg"
        html = Path("public") / "images" / "narrative" / "evil.html"
        notes = Path("public") / "images" / "narrative" / "notes.txt"
        extra = Path("public") / "images" / "narrative" / slug / "xo-extra.svg"
        editions_txt = Path("data") / "narrative_editions" / "notes.txt"
        safe = "<svg><text>ok</text></svg>\n"
        edition = (
            '{"slug":"%s","xsandos":[{"concept":"play_action_boot"}]}\n' % slug
        )

        def overlay_of(files: dict[str, str]):
            with tempfile.TemporaryDirectory() as tmp:
                src = Path(tmp) / "src"
                dst = Path(tmp) / "dst"
                (dst / data_rel).parent.mkdir(parents=True, exist_ok=True)
                (dst / data_rel).write_text(edition, encoding="utf-8")
                for rel_path, content in files.items():
                    dest = src / rel_path
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_text(content, encoding="utf-8")
                return edition_overlay.overlay_edition_data(src, dst)

        with self.assertRaises(ValueError):
            overlay_of({stray.as_posix(): safe, data_rel.as_posix(): edition})
        with self.assertRaises(ValueError):
            overlay_of({older.as_posix(): safe, data_rel.as_posix(): edition})
        with self.assertRaises(ValueError):
            overlay_of({html.as_posix(): "<p>x</p>\n", data_rel.as_posix(): edition})
        with self.assertRaises(ValueError):
            overlay_of({notes.as_posix(): "notes\n", data_rel.as_posix(): edition})
        with self.assertRaises(ValueError):
            overlay_of(
                {
                    data_rel.as_posix(): edition,
                    "tools/chiefs_narrative/facts.py": "broken = True\n",
                }
            )
        with self.assertRaises(ValueError):
            overlay_of({extra.as_posix(): safe, data_rel.as_posix(): edition})
        with self.assertRaises(ValueError):
            overlay_of(
                {
                    editions_txt.as_posix(): "notes\n",
                    data_rel.as_posix(): edition,
                }
            )
        copied = overlay_of({current.as_posix(): safe, data_rel.as_posix(): edition})
        self.assertEqual(copied, 2)
        missing_cards = (
            '{"slug":"%s","xsandos":[{"concept":"play_action_boot"},'
            '{"concept":"cover_two"}]}\n' % slug
        )
        with self.assertRaises(ValueError):
            overlay_of({current.as_posix(): safe, data_rel.as_posix(): missing_cards})

    def test_edition_overlay_rejects_svg_attack_vectors(self):
        slug = "2026-09-28-1547"
        rel = Path("public") / "images" / "narrative" / slug / "xo-mesh.svg"
        edition = '{"slug":"%s","xsandos":[{"concept":"mesh"}]}\n' % slug
        data_rel = Path("data") / ("narrative" + ".json")
        huge = "<svg><text>" + ("x" * (256 * 1024)) + "</text></svg>"
        vectors = {
            "script": "<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
            "svg-script": (
                "<svg xmlns='http://www.w3.org/2000/svg' "
                "xmlns:svg='http://www.w3.org/2000/svg'>"
                "<svg:script>alert(1)</svg:script></svg>"
            ),
            "foreignObject": (
                "<svg xmlns='http://www.w3.org/2000/svg'>"
                "<foreignObject><p>x</p></foreignObject></svg>"
            ),
            "iframe": "<svg xmlns='http://www.w3.org/2000/svg'><iframe href='x'/></svg>",
            "set-on": (
                "<svg xmlns='http://www.w3.org/2000/svg'>"
                "<set attributeName='onclick' to='alert(1)'/></svg>"
            ),
            "animate-on": (
                "<svg xmlns='http://www.w3.org/2000/svg'>"
                "<animate attributeName='onload' to='1'/></svg>"
            ),
            "javascript-href": (
                "<svg xmlns='http://www.w3.org/2000/svg'>"
                "<a href='javascript:alert(1)'/></svg>"
            ),
            "onclick": "<svg onclick='alert(1)'></svg>",
            "pi": (
                "<?xml-stylesheet type='text/xsl' href='x'?>"
                "<svg xmlns='http://www.w3.org/2000/svg'><text>ok</text></svg>"
            ),
            "doctype": (
                "<!DOCTYPE svg PUBLIC '-//W3C//DTD SVG 1.1//EN' "
                "'http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd'>"
                "<svg xmlns='http://www.w3.org/2000/svg'><text>ok</text></svg>"
            ),
            "huge": huge,
        }
        for name, payload in vectors.items():
            self.assertTrue(
                edition_overlay.svg_payload_rejected(payload),
                f"{name} must be rejected",
            )
            with tempfile.TemporaryDirectory() as tmp:
                src = Path(tmp) / "src"
                dst = Path(tmp) / "dst"
                (src / data_rel).parent.mkdir(parents=True, exist_ok=True)
                (src / data_rel).write_text(edition, encoding="utf-8")
                (src / rel).parent.mkdir(parents=True, exist_ok=True)
                (src / rel).write_text(payload, encoding="utf-8")
                with self.assertRaises(ValueError, msg=name):
                    edition_overlay.overlay_edition_data(src, dst)
        self.assertFalse(
            edition_overlay.svg_payload_rejected("<svg><text>ok</text></svg>")
        )
        self.assertFalse(
            edition_overlay.svg_payload_rejected(
                '<?xml version="1.0"?><svg><text>ok</text></svg>'
            )
        )

    def test_edition_overlay_ci_module_uses_git_name_status(self):
        """CI runs the module in the main checkout; pycache and stale files must not fail."""
        overlay_src = Path(edition_overlay.__file__).read_text(encoding="utf-8")
        init_src = (
            Path(edition_overlay.__file__).parent / "__init__.py"
        ).read_text(encoding="utf-8")
        slug = "2026-09-28-1547"
        data_rel = Path("data") / ("narrative" + ".json")
        svg_rel = Path("public") / "images" / "narrative" / slug / "xo-mesh.svg"
        wire_rel = Path("data") / "wire.json"
        edition_v1 = '{"slug":"%s","xsandos":[{"concept":"mesh"}]}\n' % slug
        edition_v2 = (
            '{"slug":"%s","xsandos":[{"concept":"mesh"}],"headline":"pr"}\n' % slug
        )
        safe = "<svg><text>ok</text></svg>\n"

        def git(cwd, *args, check=True):
            return subprocess.run(
                ["git", *args],
                cwd=cwd,
                check=check,
                capture_output=True,
                text=True,
            )

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "main"
            repo.mkdir()
            git(repo, "init", "-b", "main")
            git(repo, "config", "user.email", "ci@example.com")
            git(repo, "config", "user.name", "CI")
            pkg = repo / "tools" / "chiefs_narrative"
            pkg.mkdir(parents=True)
            (repo / "tools" / "__init__.py").write_text("", encoding="utf-8")
            (pkg / "__init__.py").write_text(init_src, encoding="utf-8")
            (pkg / "edition_overlay.py").write_text(overlay_src, encoding="utf-8")
            (repo / data_rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / data_rel).write_text(edition_v1, encoding="utf-8")
            (repo / svg_rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / svg_rel).write_text(safe, encoding="utf-8")
            (repo / wire_rel).write_text('{"fresh":false}\n', encoding="utf-8")
            git(repo, "add", "-A")
            git(repo, "commit", "-m", "fork-point")
            git(repo, "checkout", "-b", "pr")
            (repo / data_rel).write_text(edition_v2, encoding="utf-8")
            git(repo, "add", "-A")
            git(repo, "commit", "-m", "pr data")
            head = git(repo, "rev-parse", "HEAD").stdout.strip()
            pr_head = Path(tmp) / "pr-head"
            shutil.copytree(
                repo, pr_head, ignore=shutil.ignore_patterns(".git")
            )
            git(repo, "checkout", "-f", "main")
            (repo / wire_rel).write_text('{"fresh":true}\n', encoding="utf-8")
            (repo / "tools" / "chiefs_narrative" / "README_QA.txt").write_text(
                "stale leftover\n", encoding="utf-8"
            )
            git(repo, "add", "-A")
            git(repo, "commit", "-m", "main moved")
            base = git(repo, "rev-parse", "HEAD").stdout.strip()
            env = {
                key: value
                for key, value in os.environ.items()
                if key != "PYTHONDONTWRITEBYTECODE"
            }
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tools.chiefs_narrative.edition_overlay",
                    str(pr_head),
                    str(repo),
                    "--base",
                    base,
                    "--head",
                    head,
                ],
                cwd=repo,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("overlaid 1 edition", proc.stdout)
            self.assertIn("pr", (repo / data_rel).read_text(encoding="utf-8"))
            self.assertIn("true", (repo / wire_rel).read_text(encoding="utf-8"))
            self.assertNotIn("stale", (repo / wire_rel).read_text(encoding="utf-8"))

    def test_edition_overlay_rejects_deletes_renames_and_symlinks(self):
        slug = "2026-09-28-1547"
        data_rel = Path("data") / ("narrative" + ".json")
        svg_rel = Path("public") / "images" / "narrative" / slug / "xo-mesh.svg"
        wire_rel = Path("data") / "wire.json"
        edition = '{"slug":"%s","xsandos":[{"concept":"mesh"}]}\n' % slug
        safe = "<svg><text>ok</text></svg>\n"

        def git(cwd, *args, check=True):
            return subprocess.run(
                ["git", *args],
                cwd=cwd,
                check=check,
                capture_output=True,
                text=True,
            )

        def commit_tree(root: Path, message: str) -> str:
            git(root, "add", "-A")
            git(root, "commit", "-m", message)
            return git(root, "rev-parse", "HEAD").stdout.strip()

        def overlay_from(base_files, mutate) -> None:
            with tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp) / "repo"
                repo.mkdir()
                git(repo, "init", "-b", "main")
                git(repo, "config", "user.email", "ci@example.com")
                git(repo, "config", "user.name", "CI")
                for rel_path, content in base_files.items():
                    dest = repo / rel_path
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_text(content, encoding="utf-8")
                base = commit_tree(repo, "base")
                mutate(repo)
                head = commit_tree(repo, "head")
                src = Path(tmp) / "src"
                shutil.copytree(repo, src, ignore=shutil.ignore_patterns(".git"))
                git(repo, "checkout", "-f", base)
                edition_overlay.overlay_edition_data(
                    src, repo, base_sha=base, head_sha=head, repo=repo
                )

        base_files = {
            data_rel.as_posix(): edition,
            svg_rel.as_posix(): safe,
            wire_rel.as_posix(): "{}\n",
        }
        def delete_wire(repo: Path) -> None:
            (repo / wire_rel).unlink()

        def delete_svg(repo: Path) -> None:
            (repo / svg_rel).unlink()

        def rename_wire(repo: Path) -> None:
            git(repo, "mv", wire_rel.as_posix(), "data/wire.renamed.json")

        def rename_svg(repo: Path) -> None:
            git(
                repo,
                "mv",
                svg_rel.as_posix(),
                (svg_rel.parent / "xo-mesh-old.svg").as_posix(),
            )

        with self.assertRaises(ValueError):
            overlay_from(base_files, delete_wire)
        with self.assertRaises(ValueError):
            overlay_from(base_files, delete_svg)
        with self.assertRaises(ValueError):
            overlay_from(base_files, rename_wire)
        with self.assertRaises(ValueError):
            overlay_from(base_files, rename_svg)
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            dst = Path(tmp) / "dst"
            (src / data_rel).parent.mkdir(parents=True, exist_ok=True)
            (src / data_rel).write_text(edition, encoding="utf-8")
            (src / svg_rel).parent.mkdir(parents=True, exist_ok=True)
            target = src / "payload.svg"
            target.write_text(safe, encoding="utf-8")
            (src / svg_rel).symlink_to(target.name)
            (dst / data_rel).parent.mkdir(parents=True, exist_ok=True)
            (dst / data_rel).write_text(edition, encoding="utf-8")
            with self.assertRaises(ValueError):
                edition_overlay.overlay_edition_data(src, dst)

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

    @staticmethod
    def week4_preview_slate():
        """Pinned pre-kickoff Week 4 slate. Do not read live schedule_2026.json."""
        return [
            {
                "id": "401872952",
                "week": 3,
                "seasonType": "reg",
                "date": "2026-09-27T17:00:00Z",
                "opponent": "Miami Dolphins",
                "opponentAbbr": "MIA",
                "completed": True,
                "inProgress": False,
                "kcScore": 24,
                "oppScore": 10,
            },
            {
                "id": "401872976",
                "week": 4,
                "seasonType": "reg",
                "date": "2026-10-04T20:25:00Z",
                "opponent": "Las Vegas Raiders",
                "opponentAbbr": "LV",
                "completed": False,
                "inProgress": False,
                "kcScore": None,
                "oppScore": None,
            },
            {
                "id": "401873006",
                "week": 6,
                "seasonType": "reg",
                "date": "2026-10-18T20:25:00Z",
                "opponent": "Los Angeles Chargers",
                "opponentAbbr": "LAC",
                "completed": False,
                "inProgress": False,
                "kcScore": None,
                "oppScore": None,
            },
        ]

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

    def test_bye_week_on_prod_slate_after_lv_final(self):
        """Week 5 bye must stay in-season: LV review, then LAC preview."""
        slate = self.week4_preview_slate()
        lv = None
        for game in slate:
            if game.get("id") == "401872976":
                game["completed"] = True
                game["kcScore"] = 27
                game["oppScore"] = 17
                lv = game
                break
        self.assertIsNotNone(lv)
        for stamp, edition, mode, week in (
            (datetime(2026, 10, 5, 3, 43, tzinfo=timezone.utc),
             "2026 Week 4 · Review", "review", 4),
            (datetime(2026, 10, 7, 3, 43, tzinfo=timezone.utc),
             "2026 Week 4 · Review", "review", 4),
            (datetime(2026, 10, 8, 3, 43, tzinfo=timezone.utc),
             "2026 Week 6 · Preview", "preview", 6),
        ):
            ph = phase.detect(slate, now=stamp)
            self.assertEqual(ph["type"], "regular", stamp)
            self.assertEqual(ph["mode"], mode, stamp)
            self.assertEqual(ph["edition"], edition, stamp)
            self.assertEqual(ph["week"], week, stamp)
            self.assertEqual(ph["label"], f"Week {week}", stamp)
            self.assertEqual(ph["lastGame"]["opponent"], "Las Vegas Raiders")
            self.assertEqual(ph["nextGame"]["opponent"], "Los Angeles Chargers")
            self.assertNotIn("Denver", ph["nextGame"]["opponent"])
            self.assertTrue(phase.is_upcoming(ph["nextGame"], stamp))
            self.assertFalse(phase.is_upcoming(ph["lastGame"], stamp))

    def test_review_edition_holds_even_when_checker_is_clean(self):
        """Oct 5 Review never automerges; Oct 4 Preview still can."""
        fat = " ".join(["Chiefs tape review word"] * 800)

        def fat_edition(ph):
            return {
                "headline": fat,
                "dek": fat,
                "theEdge": fat,
                "storyline": fat,
                "currentState": fat,
                "gamePlan": fat,
                "edition": ph["edition"],
                "phase": {
                    "type": ph["type"],
                    "mode": ph["mode"],
                    "edition": ph["edition"],
                    "week": ph["week"],
                    "lastGame": ph.get("lastGame"),
                    "nextGame": ph.get("nextGame"),
                },
                "lastGameReview": {"lede": fat, "analysis": [fat]},
            }

        preview_slate = self.week4_preview_slate()
        preview_ph = phase.detect(
            preview_slate,
            now=datetime(2026, 10, 4, 9, 37, tzinfo=timezone.utc),
        )
        self.assertEqual(preview_ph["mode"], "preview")
        self.assertEqual(preview_ph["edition"], "2026 Week 4 · Preview")
        preview = fat_edition(preview_ph)
        self.assertGreaterEqual(
            facts.edition_word_count(preview), facts.PUBLISH_WORD_FLOOR
        )
        self.assertFalse(facts.is_review_edition(preview))
        self.assertFalse(facts.review_requires_human(preview))
        self.assertFalse(facts.should_hold_automerge([], preview))

        review_slate = copy.deepcopy(preview_slate)
        for game in review_slate:
            if game.get("id") == "401872976":
                game["completed"] = True
                game["kcScore"] = 27
                game["oppScore"] = 17
        review_ph = phase.detect(
            review_slate,
            now=datetime(2026, 10, 5, 9, 37, tzinfo=timezone.utc),
        )
        self.assertEqual(review_ph["mode"], "review")
        self.assertEqual(review_ph["edition"], "2026 Week 4 · Review")
        self.assertEqual(review_ph["week"], 4)
        self.assertEqual(review_ph["label"], "Week 4")
        review = fat_edition(review_ph)
        self.assertGreaterEqual(
            facts.edition_word_count(review), facts.PUBLISH_WORD_FLOOR
        )
        self.assertTrue(facts.REVIEW_REQUIRES_HUMAN)
        self.assertTrue(facts.is_review_edition(review))
        self.assertTrue(facts.review_requires_human(review))
        self.assertTrue(facts.should_hold_automerge([], review))
        with patch.object(facts, "REVIEW_REQUIRES_HUMAN", False):
            self.assertFalse(facts.review_requires_human(review))
            self.assertFalse(facts.should_hold_automerge([], review))

    def test_sep_1_to_5_is_week1_preview(self):
        """Sep 1–5 is after camp and before GAME_WEEK_DAYS of the opener."""
        pre = dict(self.RAMS)
        pre["completed"] = True
        pre["kcScore"] = 21
        pre["oppScore"] = 17
        slate = [pre, dict(self.DEN)]
        for day in (1, 3, 5):
            now = datetime(2026, 9, day, 12, 0, tzinfo=timezone.utc)
            ph = phase.detect(slate, now=now)
            self.assertEqual(ph["type"], "regular", day)
            self.assertEqual(ph["mode"], "preview", day)
            self.assertEqual(ph["edition"], "2026 Week 1 · Preview", day)
            self.assertEqual(ph["week"], 1, day)
            self.assertEqual(ph["nextGame"]["opponent"], "Denver Broncos", day)
            self.assertNotIn("Offseason", ph["edition"])
            self.assertNotEqual(ph["type"], "offseason")

    def test_playoff_bye_before_divisional_is_not_offseason(self):
        """A playoff win with no ESPN next row stays postseason and holds."""
        wild = {
            "id": "wc",
            "week": 1,
            "seasonType": "post",
            "date": "2027-01-16T21:00:00Z",
            "opponent": "Houston Texans",
            "opponentAbbr": "HOU",
            "completed": True,
            "kcScore": 31,
            "oppScore": 14,
        }
        slate = [
            {
                "id": "w18",
                "week": 18,
                "seasonType": "reg",
                "date": "2027-01-03T18:00:00Z",
                "opponent": "Denver Broncos",
                "completed": True,
                "kcScore": 24,
                "oppScore": 17,
            },
            wild,
        ]
        now = datetime(2027, 1, 18, 12, 0, tzinfo=timezone.utc)
        ph = phase.detect(slate, now=now)
        self.assertEqual(ph["type"], "postseason")
        self.assertEqual(ph["mode"], "review")
        self.assertEqual(ph["edition"], "2026 Playoffs")
        self.assertIsNone(ph["nextGame"])
        self.assertNotEqual(ph["type"], "offseason")
        fat = " ".join(["Chiefs tape review word"] * 800)
        narrative = {
            "headline": fat,
            "dek": fat,
            "theEdge": fat,
            "storyline": fat,
            "phase": {
                "type": ph["type"],
                "mode": ph["mode"],
                "lastGame": wild,
            },
        }
        issues = facts.check_review(narrative, wild, None, schedule=slate)
        self.assertTrue(
            any("playoff next game is unknown" in item for item in issues),
            issues,
        )
        self.assertTrue(
            facts.should_hold_automerge([], narrative, leftover=issues, schedule=slate)
        )
        self.assertTrue(facts.should_hold_automerge([], narrative, schedule=slate))

        lost = dict(wild)
        lost["kcScore"] = 14
        lost["oppScore"] = 31
        slate_lost = [slate[0], lost]
        done = phase.detect(slate_lost, now=now)
        self.assertEqual(done["type"], "offseason")
        closed = {
            "headline": fat,
            "dek": fat,
            "theEdge": fat,
            "storyline": fat,
            "phase": {"type": "offseason", "mode": "offseason", "lastGame": lost},
        }
        self.assertEqual(
            facts.check_review(closed, lost, None, schedule=slate_lost), []
        )
        self.assertFalse(
            facts.should_hold_automerge([], closed, schedule=slate_lost)
        )

    def test_sunday_pregame_runs_stay_week4_preview(self):
        slate = self.week4_preview_slate()
        for hour, minute in ((3, 43), (7, 20), (9, 37)):
            now = datetime(2026, 10, 4, hour, minute, tzinfo=timezone.utc)
            ph = phase.detect(slate, now=now)
            self.assertEqual(ph["edition"], "2026 Week 4 · Preview", now)
            self.assertEqual(ph["mode"], "preview", now)
            self.assertEqual(ph["nextGame"]["opponent"], "Las Vegas Raiders")
            self.assertTrue(phase.is_upcoming(ph["nextGame"], now))

    def test_offseason_phase_holds_when_slate_still_has_games(self):
        slate = collect.load_cached_schedule()
        fat = " ".join(["Chiefs tape review word"] * 800)
        off = {
            "headline": fat,
            "dek": fat,
            "theEdge": fat,
            "storyline": fat,
            "phase": {"type": "offseason", "mode": "offseason"},
        }
        issues = facts.check_review(off, None, None, schedule=slate)
        self.assertTrue(
            any("offseason phase" in item for item in issues),
            issues,
        )
        self.assertTrue(facts.should_hold_automerge([], off, schedule=slate))
        closed = [dict(game, completed=True) for game in slate]
        done = {
            "headline": fat,
            "dek": fat,
            "theEdge": fat,
            "storyline": fat,
            "phase": {"type": "offseason", "mode": "offseason"},
        }
        self.assertEqual(facts.check_review(done, None, None, schedule=closed), [])
        self.assertFalse(facts.should_hold_automerge([], done, schedule=closed))

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
        self.assertIn('partial "narrative-edition.html"', index)
        self.assertIn("isCurrent", index)
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
        self.assertIn('partial "season-slate.html"', edition)
        self.assertNotIn("schedule/", slate)
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
        self.assertIn("The Chiefs Narrative", base)
        self.assertIn("youtubeUrl", base)
        self.assertNotIn("social/", base)
        self.assertNotIn("sources/", base)
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
        slate = (root / "layouts" / "partials" / "season-slate.html").read_text(encoding="utf-8")
        nav = (root / "hugo.yaml").read_text(encoding="utf-8")
        self.assertIn("site.Data.schedule_2026", slate)
        self.assertIn("kcScore", slate)
        self.assertNotIn("schedule/", slate)
        self.assertNotIn('href: "schedule/"', nav)
        self.assertIn('href: ""', nav)
        self.assertIn('active: "narrative"', nav)
        self.assertFalse((root / "content" / "schedule.md").exists())

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


RETIRED_PATHS = (
    "about",
    "focus",
    "schedule",
    "shop",
    "social",
    "sources",
    "youtube",
)


class NarrativeHomepage(unittest.TestCase):
    """arrowheadpaesano.com is the daily Chiefs Narrative only."""

    def test_homepage_aliases_cover_retired_pages(self):
        root = Path(__file__).resolve().parents[2]
        home = (root / "content" / "_index.md").read_text(encoding="utf-8")
        for slug in RETIRED_PATHS:
            self.assertIn(f"/{slug}/", home)
        alias = (root / "layouts" / "alias.html").read_text(encoding="utf-8")
        self.assertIn('http-equiv="refresh"', alias)
        self.assertIn('rel="canonical"', alias)
        self.assertIn("location.replace", alias)
        gone = (root / "layouts" / "404.html").read_text(encoding="utf-8")
        self.assertIn('http-equiv="refresh"', gone)
        self.assertIn("location.replace", gone)
        robots = (root / "layouts" / "robots.txt").read_text(encoding="utf-8")
        self.assertIn("Sitemap:", robots)
        hugo = (root / "hugo.yaml").read_text(encoding="utf-8")
        self.assertIn("enableRobotsTXT: true", hugo)
        self.assertIn('youtubeUrl: "https://www.youtube.com/@arrowheadpaesano"', hugo)
        self.assertNotIn('href: "schedule/"', hugo)
        self.assertNotIn('href: "youtube/"', hugo)
        self.assertNotIn('href: "shop/"', hugo)
        for name in RETIRED_PATHS:
            self.assertFalse((root / "content" / f"{name}.md").exists(), name)
        self.assertFalse((root / "public" / "js" / "shopify-storefront.js").exists())
        for name in (
            "amazon_finds",
            "channel_feed",
            "merch",
            "social_feeds",
            "video_archive",
            "weekly_focus",
        ):
            self.assertFalse((root / "data" / f"{name}.json").exists(), name)
        main = (root / "public" / "js" / "main.js").read_text(encoding="utf-8")
        self.assertNotIn("data-youtube-grid", main)
        self.assertNotIn("data-shop-grid", main)
        self.assertIn("function initNarrativeXEmbeds", main)
        sitemap_tmpl = (root / "layouts" / "_default" / "sitemap.xml").read_text(encoding="utf-8")
        self.assertIn('(ne .Kind "section")', sitemap_tmpl)

    def test_build_publishes_narrative_and_redirects(self):
        hugo_bin = _hugo_bin()
        if not hugo_bin:
            self.skipTest("hugo is not on PATH or ~/.local/hugo/hugo")
        repo = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "dist"
            result = subprocess.run(
                [hugo_bin, "--gc", "--minify", "--destination", str(dest)],
                cwd=repo,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            home = (dest / "index.html").read_text(encoding="utf-8")
            narrative = (dest / "narrative" / "index.html").read_text(encoding="utf-8")
            self.assertIn("nrt-headline", home)
            self.assertIn("nrt-headline", narrative)
            self.assertIn("The Chiefs Narrative", home)
            self.assertIn("https://www.youtube.com/@arrowheadpaesano", home)
            self.assertIn("canonical", home)
            self.assertIn("https://arrowheadpaesano.com/", home)
            self.assertIn("og:title", home)
            self.assertIn("og:description", home)
            sitemap = (dest / "sitemap.xml").read_text(encoding="utf-8")
            self.assertIn("https://arrowheadpaesano.com/</loc>", sitemap)
            self.assertNotIn("https://arrowheadpaesano.com/narrative/</loc>", sitemap)
            robots = (dest / "robots.txt").read_text(encoding="utf-8")
            self.assertIn("sitemap.xml", robots.lower())
            rss = (dest / "index.xml").read_text(encoding="utf-8")
            self.assertIn("The Chiefs Narrative", rss)
            for slug in RETIRED_PATHS:
                self.assertNotIn(f"/{slug}/", sitemap)
                self.assertNotIn(f"/{slug}/", rss)
                stub = dest / slug / "index.html"
                self.assertTrue(stub.is_file(), slug)
                html = stub.read_text(encoding="utf-8")
                self.assertIn("refresh", html)
                self.assertIn("canonical", html)
                self.assertIn("location.replace", html)
                self.assertNotIn("location.replace('\"", html)
                self.assertNotIn('location.replace("\'', html)
            gone = (dest / "404.html").read_text(encoding="utf-8")
            self.assertIn("location.replace", gone)
            editions = dest / "narrative"
            archived = [p for p in editions.iterdir() if p.is_dir() and (p / "index.html").is_file()]
            self.assertGreater(len(archived), 10)
            sample = next(iter(archived))
            self.assertIn(f"/narrative/{sample.name}/", sitemap)


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

    def test_same_day_replacement_skips_today_and_overwrites_slug(self):
        """A same-day regen must replace today's flawed edition, not clone-fail it.

        Run 36612663271 died on theEdge vs 2026-09-29-1734 (6-11 / Tuesday desk),
        the edition the re-run was trying to overwrite. Prior-day copy still fails.
        """
        now = "2026-09-29T18:45:00+00:00"
        today_slug = "2026-09-29-1734"
        today_edge = (
            "Model/market read: the model gives KC 70.0%; Vegas has it KC -4.5; "
            "Polymarket prices the Chiefs at 8.6% to win it all."
        )
        today = self._edition_payload(
            generatedAt="2026-09-29T17:34:02+00:00",
            slug=today_slug,
            headline="Week 4 · Sun Oct 4: KC @ Las Vegas Raiders — Tuesday desk",
            dek="Tuesday desk leftover.",
            theEdge=today_edge,
        )
        yesterday = self._edition_payload(
            generatedAt="2026-09-28T15:47:56+00:00",
            slug="2026-09-28-1547",
            headline="20-of-24 beat Miami. Crosby is the next snap.",
            dek="Yesterday's dek must still be unique.",
            theEdge="Yesterday's theEdge must still be unique.",
        )
        replacement = {
            "headline": "Fresh Week 4 title after the 6-11 leftover",
            "dek": "A same-day replacement, not a second Tuesday desk.",
            "theEdge": today_edge,
            "edition": "2026 Training Camp",
        }
        self.assertEqual(
            generate._edition_calendar_day(today),
            generate._edition_calendar_day({"generatedAt": now}),
        )
        self.assertEqual(
            [ed["slug"] for ed in generate._prior_day_editions([today, yesterday], "2026-09-29")],
            ["2026-09-28-1547"],
        )

        llm = Mock(return_value=(replacement, "offline"))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "narrative_archive.json"
            editions = root / "narrative_editions"
            editions.mkdir()
            archive.write_text(
                json.dumps([today, yesterday]) + "\n", encoding="utf-8"
            )
            (editions / f"{today_slug}.json").write_text(
                json.dumps(today) + "\n", encoding="utf-8"
            )
            (editions / "2026-09-28-1547.json").write_text(
                json.dumps(yesterday) + "\n", encoding="utf-8"
            )
            extra = [
                patch.object(generate.config, "NARRATIVE_JSON", root / "narrative.json"),
                patch.object(generate.config, "WIRE_JSON", root / "wire.json"),
                patch.object(generate.config, "REPAIR_JSON", root / "repair.json"),
                patch.object(generate.config, "iso_now", return_value=now),
            ]
            with self._generation_stack(
                llm=llm, archive=archive, editions=editions, extra=extra
            ):
                rc = generate.main(["--provider", "grok"])
            self.assertEqual(rc, 0)
            self.assertEqual(llm.call_count, 1)
            prompt = llm.call_args.args[2]
            self.assertNotIn("Tuesday desk", prompt)
            self.assertIn(yesterday["headline"], prompt)
            self.assertEqual(
                {p.name for p in editions.iterdir()},
                {f"{today_slug}.json", "2026-09-28-1547.json"},
            )
            live = json.loads((root / "narrative.json").read_text(encoding="utf-8"))
            self.assertEqual(live["slug"], today_slug)
            self.assertEqual(live["headline"], replacement["headline"])
            self.assertEqual(live["theEdge"], today_edge)
            saved = json.loads(archive.read_text(encoding="utf-8"))
            today_rows = [
                row
                for row in saved
                if generate._edition_calendar_day(row) == "2026-09-29"
            ]
            self.assertEqual(len(today_rows), 1)
            self.assertEqual(today_rows[0]["slug"], today_slug)
            self.assertEqual(today_rows[0]["headline"], replacement["headline"])

        clone_yesterday = {
            "headline": yesterday["headline"],
            "dek": yesterday["dek"],
            "theEdge": yesterday["theEdge"],
            "edition": "2026 Training Camp",
        }
        llm_clone = Mock(
            side_effect=[(clone_yesterday, "grok"), (clone_yesterday, "grok")]
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "narrative_archive.json"
            editions = root / "narrative_editions"
            editions.mkdir()
            archive.write_text(
                json.dumps([today, yesterday]) + "\n", encoding="utf-8"
            )
            (editions / f"{today_slug}.json").write_text(
                json.dumps(today) + "\n", encoding="utf-8"
            )
            (editions / "2026-09-28-1547.json").write_text(
                json.dumps(yesterday) + "\n", encoding="utf-8"
            )
            extra = [
                patch.object(generate.config, "iso_now", return_value=now),
            ]
            with self._generation_stack(
                llm=llm_clone, archive=archive, editions=editions, extra=extra
            ):
                with self.assertRaises(generate.DuplicateNarrativeError) as ctx:
                    generate.build("grok", persist_schedule=False)
            self.assertIn("2026-09-28-1547", str(ctx.exception))
            self.assertNotIn(today_slug, str(ctx.exception))


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
        self.assertEqual(ph["week"], 3)
        self.assertEqual(ph["label"], "Week 3")
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
        self.assertTrue(phase.completed_without_score(week3))
        self.assertTrue(phase.is_live(week3, now=now))
        self.assertFalse(phase.is_final(week3))
        ph = phase.detect([self.WEEK2, week3, self.WEEK4], now=now)
        self.assertNotEqual(ph["mode"], "review")
        self.assertNotIn("Review", ph.get("edition") or "")
        self.assertTrue(phase.any_live([self.WEEK2, week3, self.WEEK4], now=now))

    def test_oct5_completed_without_score_skips_generate(self):
        """ESPN completed + no scores must not mint a Preview PR."""
        slate = copy.deepcopy(collect.load_cached_schedule())
        lv = None
        for game in slate:
            if str(game.get("id")) == "401872976":
                game["completed"] = True
                game["inProgress"] = False
                game["kcScore"] = None
                game["oppScore"] = None
                lv = game
                break
        self.assertIsNotNone(lv)
        now = datetime(2026, 10, 5, 9, 37, tzinfo=timezone.utc)
        self.assertTrue(phase.completed_without_score(lv))
        self.assertTrue(phase.is_live(lv, now=now))
        self.assertTrue(phase.any_live(slate, now=now))
        self.assertFalse(phase.is_final(lv))
        ph = phase.detect(slate, now=now)
        self.assertNotEqual(ph["mode"], "review")
        self.assertNotIn("Review", ph.get("edition") or "")
        # G20: detect must use is_final, not the completed flag. A
        # completed-without-score game is liveGame, never lastGame.
        self.assertEqual(str((ph.get("liveGame") or {}).get("id")), "401872976")
        self.assertNotEqual(str((ph.get("lastGame") or {}).get("id")), "401872976")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            narrative_json = root / "narrative.json"
            wire_json = root / "wire.json"
            editions = root / "editions"
            editions.mkdir()
            archive = root / "archive.json"
            archive.write_text("[]\n", encoding="utf-8")
            with patch.object(config, "now_utc", return_value=now), patch.object(
                collect,
                "collect_all",
                return_value={"schedule": slate, "news": [], "markets": {}},
            ), patch.object(generate, "_write_schedule"), patch.object(
                generate.config, "NARRATIVE_JSON", narrative_json
            ), patch.object(
                generate.config, "WIRE_JSON", wire_json
            ), patch.object(
                generate.config, "EDITIONS_DIR", editions
            ), patch.object(
                generate.config, "ARCHIVE_JSON", archive
            ), patch.object(generate, "_render_diagrams"):
                rc = generate.main(["--provider", "offline"])
            self.assertEqual(rc, 0)
            self.assertFalse(narrative_json.exists())
            self.assertFalse(wire_json.exists())
            self.assertEqual(list(editions.iterdir()), [])

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

    def test_signed_review_skips_remint_after_edition_lands(self):
        """A signed Week 4 Review must not be overwritten on the next daily run."""
        live = {
            "slug": "2026-10-05-0747",
            "edition": "2026 Week 4 · Review",
            "phase": {"type": "regular", "mode": "review", "week": 4},
            "lastGameReview": {"opponent": "Las Vegas Raiders"},
        }
        ph = {
            "mode": "review",
            "label": "Week 4",
            "type": "regular",
            "lastGame": {"opponent": "Las Vegas Raiders", "id": "401872976"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            narrative_json = root / "narrative.json"
            narrative_json.write_text(json.dumps(live) + "\n", encoding="utf-8")
            with patch.object(generate.config, "NARRATIVE_JSON", narrative_json), \
                patch.object(review_gate, "review_signed_off", return_value=True):
                self.assertTrue(generate._signed_review_already_published(ph))
            with patch.object(generate.config, "NARRATIVE_JSON", narrative_json), \
                patch.object(review_gate, "review_signed_off", return_value=False):
                self.assertFalse(generate._signed_review_already_published(ph))
            preview_ph = dict(ph)
            preview_ph["mode"] = "preview"
            with patch.object(generate.config, "NARRATIVE_JSON", narrative_json), \
                patch.object(review_gate, "review_signed_off", return_value=True):
                self.assertFalse(generate._signed_review_already_published(preview_ph))

        week4 = {
            "id": "401872976",
            "week": 4,
            "seasonType": "reg",
            "date": "2026-10-04T20:25:00Z",
            "opponent": "Las Vegas Raiders",
            "completed": True,
            "inProgress": False,
            "kcScore": 30,
            "oppScore": 27,
        }
        review_ph = {
            "type": "regular",
            "label": "Week 4",
            "week": 4,
            "mode": "review",
            "edition": "2026 Week 4 · Review",
            "lastGame": week4,
            "nextGame": {
                "id": "401873006",
                "opponent": "Los Angeles Chargers",
                "completed": False,
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            narrative_json = root / "narrative.json"
            wire_json = root / "wire.json"
            editions = root / "editions"
            editions.mkdir()
            archive = root / "archive.json"
            archive.write_text("[]\n", encoding="utf-8")
            narrative_json.write_text(json.dumps(live) + "\n", encoding="utf-8")
            with patch.object(
                collect,
                "collect_all",
                return_value={"schedule": [week4], "news": [], "markets": {}},
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
                review_gate, "review_signed_off", return_value=True
            ), patch.object(
                phase, "any_live", return_value=False
            ), patch.object(
                phase, "detect", return_value=review_ph
            ):
                rc = generate.main(["--provider", "offline"])
            self.assertEqual(rc, 0)
            self.assertEqual(
                json.loads(narrative_json.read_text(encoding="utf-8"))["slug"],
                "2026-10-05-0747",
            )
            self.assertFalse(wire_json.exists())
            self.assertEqual(list(editions.iterdir()), [])

    def test_review_edition_header_uses_completed_week(self):
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
        self.assertEqual(ph["week"], 3)
        self.assertEqual(ph["label"], "Week 3")
        self.assertEqual(ph["edition"], "2026 Week 3 · Review")
        self.assertEqual(phase.format_edition(ph), "2026 Week 3 · Review")
        stale = dict(ph)
        stale["week"] = 4
        stale["label"] = "Week 4"
        self.assertEqual(phase.format_edition(stale), "2026 Week 3 · Review")

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

    def test_footer_hugo_renders_ct(self):
        hugo_bin = _hugo_bin()
        if not hugo_bin:
            self.skipTest("hugo is not on PATH or ~/.local/hugo/hugo")
        repo = Path(__file__).resolve().parents[2]
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

    def _lv5_review(self, lede):
        return {
            "lastGameReview": {
                "opponent": "Las Vegas Raiders",
                "result": "W",
                "score": "KC 27–17",
                "lede": lede,
                "analysis": ["The Chiefs won the line of scrimmage."],
                "whatWorked": ["The run game."],
                "whatDidnt": ["Third down was 3-of-7."],
            }
        }

    def test_total_yards_resolve_lv_with_empty_opponent(self):
        """Production recaps leave opponent empty; still bind Raiders / LV / KC."""
        from tools.tests import karen_matrices

        recap, last, slate = karen_matrices.ctx("LV5")
        recap = copy.deepcopy(recap)
        recap["opponent"] = ""
        last = dict(last)
        last["opponent"] = "Las Vegas Raiders"

        def issues(lede):
            return facts.check_review(
                self._lv5_review(lede),
                last,
                recap,
                schedule=slate,
            )

        raiders_275 = issues("The Raiders gained 275 total yards.")
        self.assertTrue(
            any("total yards 275" in item and "LV" in item for item in raiders_275),
            raiders_275,
        )
        self.assertFalse(any("for KC" in item for item in raiders_275), raiders_275)
        self.assertEqual(issues("Las Vegas gained 305 total yards."), [])
        self.assertEqual(issues("The Raiders finished with 305 total yards."), [])
        kc_swap = issues("Kansas City gained 305 total yards.")
        self.assertTrue(
            any("total yards 305" in item and "KC" in item for item in kc_swap),
            kc_swap,
        )
        located = issues("Kansas City had 305 total yards in Las Vegas.")
        self.assertTrue(
            any("total yards 305" in item and "KC" in item for item in located),
            located,
        )

    def test_p2_final_and_in_game_marks_and_td_verbs(self):
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        slate = collect.load_cached_schedule()

        def issues(lede, analysis=None):
            return facts.check_review(
                self._review(
                    lede=lede,
                    analysis=analysis or ["Kelce scored on an 11-yard catch."],
                ),
                last,
                recap,
                schedule=slate,
            )

        self.assertTrue(issues("Kansas City edged Miami 17-10."))
        self.assertTrue(issues("Kansas City held off Miami 17-10."))
        self.assertTrue(issues("Kansas City outscored Miami 17-10."))
        self.assertTrue(issues("Miami fell 17-10."))
        self.assertEqual(issues("Kansas City edged Miami 24-10."), [])
        self.assertTrue(issues("A week after the 30-27 win over Indy, Kansas City hosted Miami."))
        self.assertTrue(issues("Kansas City won the opener 28-10."))
        self.assertEqual(issues("Kansas City won the opener 31-10."), [])
        self.assertTrue(issues("Kelce made it 21-7."))
        self.assertTrue(issues("Kansas City led 17-10 at the half."))
        self.assertEqual(issues("Kansas City led 14-7 at the half."), [])
        self.assertTrue(issues("Kansas City took a 21-3 lead into halftime."))
        self.assertTrue(issues("Kansas City went up 21-3 before the break."))
        self.assertTrue(issues("Walker bulled in from the 15."))
        self.assertTrue(issues("Walker dove in from the 15."))
        self.assertTrue(issues("Walker scampered in from the 15."))
        self.assertTrue(issues("Walker scored on a 15-yard run."))
        self.assertTrue(issues("Walker powered in from the 15."))
        self.assertTrue(issues("Walker rumbled in from the 15."))
        self.assertEqual(issues("Walker scored on a 10-yard run."), [])
        self.assertTrue(issues("Kansas City won by 7."))
        self.assertEqual(issues("Kansas City won by 14."), [])
        self.assertTrue(issues("Kansas City beat the Dolphins by 7."))
        self.assertTrue(issues("Kansas City won in Miami, 17-10."))
        self.assertEqual(issues("Kansas City won in Miami, 24-10."), [])
        self.assertTrue(issues("Kansas City topped Miami 17-10."))
        self.assertTrue(issues("Kansas City dispatched Miami 17-10."))
        self.assertTrue(issues("Kansas City got past Miami 17-10."))
        self.assertEqual(issues("Kansas City topped Miami 24-10."), [])
        self.assertTrue(issues("Kansas City gained 329 total yards in Miami."))
        self.assertEqual(issues("Kansas City gained 334 total yards in Miami."), [])

    def test_walker_carried_line_binds_last_game(self):
        from tools.tests import karen_matrices

        recap, last, slate = karen_matrices.ctx("LV5")
        recap = copy.deepcopy(recap)
        recap["opponent"] = ""
        wrong = facts.check_review(
            self._lv5_review("Walker carried 18 times for 70 yards."),
            last,
            recap,
            schedule=slate,
        )
        self.assertTrue(
            any("carries" in item or "rushing" in item for item in wrong),
            wrong,
        )
        mia = _load_fixture("espn_401872952_recap.json")
        mia_last = dict(self.LAST)
        ok = facts.check_review(
            self._review(
                lede="Walker carried 18 times for 70 yards.",
                analysis=["Kelce scored on an 11-yard catch."],
            ),
            mia_last,
            mia,
        )
        self.assertEqual(ok, [])
        for lede in (
            "Walker finished with 70 yards on 18 carries.",
            "Walker had 18 carries for 70 yards.",
            "Walker ran 18 times for 70 yards.",
        ):
            wrong_form = facts.check_review(
                self._lv5_review(lede),
                last,
                recap,
                schedule=slate,
            )
            self.assertTrue(
                any("carries" in item or "rushing" in item for item in wrong_form),
                (lede, wrong_form),
            )
            ok_form = facts.check_review(
                self._review(
                    lede=lede,
                    analysis=["Kelce scored on an 11-yard catch."],
                ),
                mia_last,
                mia,
            )
            self.assertEqual(ok_form, [], lede)

    def test_td_yardage_binds_to_scorer_team_not_unique_distance(self):
        """Production LV 27-17: LV owns 15/4; Walker is KC via touches."""
        from tools.tests import karen_matrices

        recap, last, slate = karen_matrices.ctx("LV5")
        recap = copy.deepcopy(recap)
        recap["opponent"] = ""
        recap["scoringPlays"] = [
            {
                "quarter": 1,
                "type": "TD",
                "yards": 6,
                "team": "KC",
                "kcScore": 7,
                "oppScore": 0,
            },
            {
                "quarter": 1,
                "type": "TD",
                "yards": 15,
                "team": "LV",
                "kcScore": 7,
                "oppScore": 7,
            },
            {
                "quarter": 2,
                "type": "TD",
                "yards": 4,
                "team": "LV",
                "kcScore": 7,
                "oppScore": 14,
            },
            {
                "quarter": 3,
                "type": "TD",
                "yards": 1,
                "team": "KC",
                "kcScore": 14,
                "oppScore": 14,
            },
        ]
        for play in recap["scoringPlays"]:
            play.pop("player", None)

        def issues(lede):
            return facts.check_review(
                self._lv5_review(lede),
                last,
                recap,
                schedule=slate,
            )

        for lede in (
            "Walker bulled in from the 15.",
            "Walker scored on a 15-yard run.",
            "Walker dove in from the 4.",
            "Walker powered in from the 15.",
            "Walker rumbled in from the 4.",
        ):
            hit = issues(lede)
            self.assertTrue(
                any("scoring yardage" in item and "KC" in item for item in hit),
                (lede, hit),
            )
        self.assertTrue(issues("Kansas City won by 7."))
        self.assertEqual(issues("Kansas City won by 10."), [])
        self.assertTrue(issues("Kansas City beat the Raiders by 7."))
        self.assertTrue(issues("Kansas City won in Las Vegas, 24-17."))
        self.assertEqual(issues("Kansas City won in Las Vegas, 27-17."), [])
        self.assertTrue(issues("Kansas City topped Las Vegas 24-17."))
        self.assertTrue(issues("Kansas City dispatched Las Vegas 24-17."))
        self.assertTrue(issues("Kansas City got past Las Vegas 24-17."))
        self.assertTrue(issues("Kansas City took a 21-3 lead into halftime."))
        self.assertEqual(issues("Kansas City led 7-14 at the half."), [])

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
        self.assertNotIn("kickoff is midday", blob)
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
            lede="Sunday night in Miami was a reminder. It was a night game.",
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
        narrative = self._review(lede="Sunday night in Miami was a reminder.")
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
        self.assertFalse(any("kickoff is midday" in item for item in issues), issues)

    def test_week4_review_mid_scores_and_cousins_hits_are_espn_backed(self):
        """Run 37358660165: 17-13 / 30-19 are LV score-afters; 10 hits is Cousins."""
        recap = _load_fixture("espn_401872976_recap.json")
        last = {
            "id": "401872976",
            "completed": True,
            "opponent": "Las Vegas Raiders",
            "opponentAbbr": "LV",
            "kcScore": 30,
            "oppScore": 27,
            "date": "2026-10-04T20:25:00Z",
        }
        narrative = {
            "lastGameReview": {
                "opponent": "Las Vegas Raiders",
                "result": "W",
                "score": "KC 30–27",
                "lede": (
                    "An 8-yard Chiefs touchdown made it 17-13, then Walker "
                    "took right end 22 yards to make it 30-19."
                ),
                "analysis": [
                    {
                        "body": (
                            "Kansas City trailed 17–19 after Las Vegas field "
                            "goals of 44 and 48 yards. Cousins was hit 10 "
                            "times and never sacked."
                        )
                    }
                ],
            }
        }
        self.assertEqual(facts.check_review(narrative, last, recap), [])
        echo = {
            "runOfShow": [
                {"talkTrack": "Do not invent a Chargers score. Look ahead."}
            ]
        }
        echo_issues = facts.check_review(echo, last, recap)
        self.assertTrue(
            any("echoed writer instruction" in item for item in echo_issues),
            echo_issues,
        )

    def test_live_week4_review_edition_is_clean_against_lv_recap(self):
        """Published 2026-10-05-0747 must stay green once the LV box is wired."""
        from tools.tests.archive_replay import salvage_edition_file

        row = salvage_edition_file("2026-10-05-0747.json")
        self.assertEqual(row["issues"], [])
        self.assertEqual(row["before"], row["after"])
        self.assertEqual(
            generate._recap_for_edition(
                {
                    "lastGameReview": {
                        "opponent": "Las Vegas Raiders",
                        "score": "KC 30–27",
                    }
                }
            ).get("eventId"),
            "401872976",
        )

    def test_run_37812217166_allowed_lv_box_and_cousins_sacks_pass(self):
        """Run 37812217166: KC allowed LV's 28/35:42; zero sacks is Cousins.

        ESPN 401872976: LV 28 first downs and 35:42 TOP, Cousins 0 sacks
        taken, KC recorded 10 hits. 'Kansas City just allowed …' is the
        opponent column, not KC's 18 / 24:18. Wrong-subject copy still
        fails, and a held leftover overlay must not fail the frozen net.
        """
        from tools.tests.archive_replay import (
            pinned_edition_names,
            salvage_all_editions,
        )

        recap = _load_fixture("espn_401872976_recap.json")
        last = {
            "id": "401872976",
            "completed": True,
            "opponent": "Las Vegas Raiders",
            "opponentAbbr": "LV",
            "kcScore": 30,
            "oppScore": 27,
            "date": "2026-10-04T20:25:00Z",
        }
        accept = [
            "Kansas City just allowed 28 first downs and 35:42.",
            "Kansas City allowed 28 first downs.",
            "Kansas City allowed 35:42 of possession.",
            "Third down 5-of-13, zero sacks on Cousins, 10 hits.",
            "Zero sacks on Cousins.",
            "Ten quarterback hits and zero sacks is a coverage defense.",
            "Las Vegas posted 437 yards with zero sacks allowed.",
        ]
        for sentence in accept:
            issues = facts.check_review(
                {"lastGameReview": {"lede": sentence, "opponent": "Las Vegas Raiders"}},
                last,
                recap,
            )
            self.assertEqual(issues, [], f"should accept {sentence!r}: {issues}")
        reject = [
            "Kansas City had 28 first downs and 35:42.",
            "Kansas City just posted 28 first downs and 35:42.",
            "Mahomes had zero sacks.",
            "Patrick was sacked zero times.",
        ]
        for sentence in reject:
            issues = facts.check_review(
                {"lastGameReview": {"lede": sentence, "opponent": "Las Vegas Raiders"}},
                last,
                recap,
            )
            self.assertTrue(issues, f"should reject {sentence!r}")
        pinned = pinned_edition_names()
        slugs = [slug for slug, _ in salvage_all_editions()]
        self.assertEqual(set(slugs), pinned)
        self.assertNotIn("2026-10-08-1657.json", pinned)

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
                    "Sunday night in Miami was a reminder."
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

    def test_fact_check_retries_twice_then_drops_analysis_and_publishes(self):
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
            self.assertEqual(rc, 0)
            self.assertEqual(llm.call_count, 3)
            retry_user = llm.call_args_list[1].args[2]
            self.assertIn("FACT CHECK RETRY", retry_user)
            self.assertIn("FACT CHECK RETRY", llm.call_args_list[2].args[2])
            written = json.loads(narrative_json.read_text(encoding="utf-8"))
            blob = facts.edition_text(written)
            self.assertNotIn("14-10", blob)
            self.assertNotIn("from the 12", blob)

    def test_fact_check_analysis_drops_publish_when_word_floor_holds(self):
        bad = {
            "headline": "Fresh title",
            "dek": "Fresh dek",
            "theEdge": "Fresh edge about the 24-10 tape.",
            "lastGameReview": {
                "lede": "Kansas City won 24-10.",
                "analysis": [
                    "Kelce scored from the 12.",
                    "Pacheco scored from the 9.",
                    "Butker hit a 50-yard field goal.",
                ],
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
            self.assertEqual(
                rc, 0, repair_json.read_text() if repair_json.exists() else "no repair"
            )
            written = json.loads(narrative_json.read_text(encoding="utf-8"))
            blob = facts.edition_text(written)
            self.assertNotIn("from the 12", blob)
            self.assertNotIn("from the 9", blob)
            self.assertNotIn("50-yard", blob)
            repair = json.loads(repair_json.read_text(encoding="utf-8"))
            self.assertGreater(len(repair["droppedSentences"]), facts.MAX_REPAIR_DROPS)
            self.assertTrue(repair["holdAutomerge"])

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

    def test_fact_check_orphan_after_40_drop_publishes(self):
        recap = _load_fixture("espn_401872952_recap.json")
        orphan = (
            "That is how you finish with 25:39 even after hitting explosives."
        )
        bad = {
            "headline": "Fresh title",
            "dek": "Fresh dek",
            "theEdge": "Fresh edge about the 24-10 tape.",
            "lastGameReview": {
                "lede": (
                    "Kansas City finished 24–10 against Miami. "
                    "Twenty of 24 for 246, two touchdowns, one interception, "
                    "and a 119.8 rating is a control tape, not a 40-dropback "
                    "scramble. "
                    + orphan
                ),
            },
        }
        last = dict(self.LAST)
        last["id"] = "401872952"
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
                collect, "fetch_game_recap", return_value=recap
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
            self.assertEqual(
                rc, 0, repair_json.read_text() if repair_json.exists() else "no repair"
            )
            written = json.loads(narrative_json.read_text(encoding="utf-8"))
            blob = facts.edition_text(written)
            self.assertNotIn("24-dropback", blob)
            self.assertIn(orphan, blob)
            repair = json.loads(repair_json.read_text(encoding="utf-8"))
            self.assertFalse(
                any(orphan in item for item in repair["droppedSentences"]),
                repair["droppedSentences"],
            )

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

    def test_repair_drops_orphan_opener_left_by_40_drop(self):
        """A negated 40-dropback is not a box claim, so salvage leaves it."""
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        follow = (
            "That is how you finish with 25:39 even after hitting explosives."
        )
        lede = (
            "Kansas City finished 24–10 against Miami. "
            "Twenty of 24 for 246, two touchdowns, one interception, "
            "and a 119.8 rating is a control tape, not a 40-dropback "
            "scramble. "
            + follow
        )
        narrative = {
            "headline": "Keep this title",
            "lastGameReview": {"lede": lede},
        }
        issues = facts.check_review(narrative, last, recap)
        self.assertFalse(
            any("40-drop" in item or "pass attempts" in item for item in issues),
            issues,
        )
        repaired = facts.repair_offending_copy(narrative, issues, last)
        blob = facts.edition_text(repaired)
        self.assertIn("40-dropback", blob)
        self.assertIn(follow, blob)
        self.assertEqual(facts.dropped_sentences(narrative, repaired), [])
        self.assertEqual(facts.check_review(repaired, last, recap), [])
        self.assertEqual(facts.check_repair_orphans(repaired, []), [])
        self.assertEqual(facts.repair_publish_blockers([], repaired, []), [])

    def test_kc_possession_of_miami_clock_still_fails_and_is_dropped(self):
        """Run 36741379345: 34:21 is Miami's TOP. Do not publish it as KC."""
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        wrong = self._review(
            lede="Kansas City held the ball for 34:21 of possession."
        )
        issues = facts.check_review(wrong, last, recap)
        self.assertTrue(
            any("not KC" in item and "34:21" in item for item in issues),
            issues,
        )
        # Apostrophe in an older 'Miami's clock' note must not hide 34:21.
        poisoned = (
            "possession 34:21 is Miami's clock, not KC "
            "('34:21 of possession')"
        )
        self.assertTrue(
            any("34:21" in s for s in facts.violation_snippets([poisoned])),
            facts.violation_snippets([poisoned]),
        )
        self.assertTrue(
            any("34:21" in s for s in facts.violation_snippets(issues)),
            facts.violation_snippets(issues),
        )
        repaired = facts.repair_offending_copy(wrong, issues, last)
        self.assertNotIn("34:21", facts.edition_text(repaired))
        self.assertEqual(facts.check_review(repaired, last, recap), [])
        miami = self._review(
            lede="Miami held the ball for 34:21 of possession."
        )
        self.assertEqual(facts.check_review(miami, last, recap), [])
        leftover = facts.repair_publish_blockers(
            ["possession 34:21 is MIA clock, not KC ('34:21')"],
            wrong,
            [],
        )
        self.assertTrue(any("34:21" in item for item in leftover), leftover)

    def test_prior_week_possession_binds_to_kc_not_indianapolis(self):
        """Week 2 ESPN 401872945: KC 37:00, IND 33:00. Do not flip them."""
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        last["id"] = "401872952"
        slate = [
            {
                "id": "401872945",
                "week": 2,
                "opponent": "Indianapolis Colts",
                "opponentAbbr": "IND",
                "completed": True,
                "kcScore": 33,
                "oppScore": 30,
            },
            {
                "id": "401872952",
                "week": 3,
                "opponent": "Miami Dolphins",
                "opponentAbbr": "MIA",
                "completed": True,
                "kcScore": 24,
                "oppScore": 10,
            },
        ]
        flipped = self._review(
            lede="Indianapolis held the ball for 37:00 of possession."
        )
        issues = facts.check_review(flipped, last, recap, schedule=slate)
        self.assertTrue(
            any(
                "37:00" in item and "prior KC" in item and "IND" in item
                for item in issues
            ),
            issues,
        )
        stays = self._review(
            lede="The prior-week 37:00 of possession stays on Indianapolis."
        )
        stays_issues = facts.check_review(stays, last, recap, schedule=slate)
        self.assertTrue(
            any("37:00" in item and "not IND" in item for item in stays_issues),
            stays_issues,
        )
        chiefs = self._review(
            lede=(
                "Against Indianapolis the Chiefs posted 29 first downs "
                "and 37:00 of possession."
            )
        )
        self.assertEqual(
            facts.check_review(chiefs, last, recap, schedule=slate), []
        )
        colts = self._review(
            lede="Indianapolis held the ball for 33:00 of possession."
        )
        self.assertEqual(
            facts.check_review(colts, last, recap, schedule=slate), []
        )
        kc_miami = self._review(
            lede="Kansas City held the ball for 34:21 of possession."
        )
        miami_issues = facts.check_review(kc_miami, last, recap, schedule=slate)
        self.assertTrue(
            any("not KC" in item and "34:21" in item for item in miami_issues),
            miami_issues,
        )

    def test_total_drives_from_espn_box_are_checked(self):
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        ok = self._review(
            lede=(
                "Reid said after the game the offense did not have a ton of "
                "plays, and the box backs him up: nine drives, 18 first downs, "
                "3-of-7 on third down, 25:39 of possession."
            )
        )
        self.assertEqual(facts.check_review(ok, last, recap), [])
        both = self._review(lede="Kansas City and Miami each had 9 total drives.")
        self.assertEqual(facts.check_review(both, last, recap), [])
        wrong = self._review(lede="Kansas City finished with 15 drives.")
        issues = facts.check_review(wrong, last, recap)
        self.assertTrue(
            any("total drives 15" in item and "9" in item for item in issues),
            issues,
        )

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
        self.assertIn("Possession clocks stay with the team on that box line", text)

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

    def test_v9_hit_sack_same_look_and_hyphen_counts(self):
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        must_reject = [
            "Miami hit Mahomes 12 times.",
            "Miami hit Mahomes twelve times.",
            "Nourzad was eligible on both stuffed snaps.",
            "Kansas City scored within 90 seconds.",
            "Kansas City scored inside two minutes.",
            "Mahomes was sacked twice.",
            "Mahomes took 7 hits.",
            "Walker already handled twenty-two touches.",
        ]
        for sentence in must_reject:
            issues = facts.check_review(self._review(lede=sentence), last, recap)
            self.assertTrue(issues, f"should reject {sentence!r}")
        hyphen = facts.check_review(
            self._review(lede="Walker already handled twenty-two touches."),
            last,
            recap,
        )
        self.assertTrue(any("22" in item for item in hyphen), hyphen)
        self.assertFalse(any("touches 2 " in item for item in hyphen), hyphen)
        twenty_two = facts._TOUCH_COUNT.search("Walker already handled twenty-two touches.")
        self.assertIsNotNone(twenty_two)
        self.assertEqual(facts._parse_count(twenty_two.group(1)), 22)
        two_only = facts._TOUCH_COUNT.search("twenty-two touches.")
        self.assertEqual(facts._match_count(two_only), 22)
        self.assertEqual(
            facts.check_review(
                self._review(lede="Walker already handled twenty touches."),
                last,
                recap,
            ),
            [],
        )

    def test_v10_pressure_subject_clock_and_usage_variants(self):
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        must_pass = [
            "Chris Jones had two hits on Willis",
            "Karlaftis had two sacks of Willis",
            "Crosby has two sacks this year",
            "Crosby posted 12 QB hits last season",
            "Kelce's 48-yarder came within two minutes of kickoff",
        ]
        for sentence in must_pass:
            issues = facts.check_review(self._review(lede=sentence), last, recap)
            self.assertEqual(issues, [], f"should accept {sentence!r}: {issues}")
        must_reject = [
            "Miami sacked Mahomes twice",
            "Kansas City scored in under two minutes",
            "Kansas City scored in less than two minutes",
            "Nourzad was eligible on each stuffed snap",
            "Nourzad was eligible on both of the stuffed snaps",
            "touched the ball 18 times",
            "attempted 30 passes",
        ]
        for sentence in must_reject:
            issues = facts.check_review(self._review(lede=sentence), last, recap)
            self.assertTrue(issues, f"should reject {sentence!r}")

    def test_v11_team_pressure_variants_and_low_misses(self):
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        must_pass = [
            "Crosby already has 12 QB hits and two sacks through three games.",
            "Kansas City had 3 hits on Willis in the fourth quarter.",
            "Sneed had one hit and a forced fumble.",
        ]
        for sentence in must_pass:
            issues = facts.check_review(self._review(lede=sentence), last, recap)
            self.assertEqual(issues, [], f"should accept {sentence!r}: {issues}")
        must_reject = [
            "Miami recorded two sacks.",
            "Mahomes went down twice for sacks.",
            "Mahomes was taken down three times.",
            "Nourzad was eligible for both stuffed runs at the 12.",
            "Nourzad lined up eligible on both goal-line snaps.",
            "Kansas City scored inside of 90 seconds.",
            "The Chiefs scored within the opening two minutes.",
            "Kansas City found the end zone in less than 90 seconds.",
            "Walker handled the ball twenty-two times.",
            "Walker's 10-yard touchdown came with H. Nourzad eligible.",
            "Karlaftis intercepted Willis at Q4 0:47.",
            "The opening drive took 4:06.",
            "Mahomes went 20-of-24 with three touchdowns.",
            "Rodriguez intercepted Mahomes at Q2 6:15.",
            "Mahomes threw 24 touchdown passes.",
        ]
        for sentence in must_reject:
            issues = facts.check_review(self._review(lede=sentence), last, recap)
            self.assertTrue(issues, f"should reject {sentence!r}")

    def test_v12_team_defense_adverb_and_td_variants(self):
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        must_pass = [
            "Crosby's two sacks in Week 3 came against the Commanders.",
        ]
        for sentence in must_pass:
            issues = facts.check_review(self._review(lede=sentence), last, recap)
            self.assertEqual(issues, [], f"should accept {sentence!r}: {issues}")
        must_reject = [
            "The Miami defense recorded two sacks.",
            "Miami notched a sack.",
            "Miami quietly recorded two sacks.",
            "Kansas City scored barely 90 seconds in.",
            "Kansas City scored in fewer than two minutes.",
            "Mahomes was repeatedly sacked, three times in all.",
            "20-of-24 with three touchdowns",
            "24 touchdown passes",
        ]
        for sentence in must_reject:
            issues = facts.check_review(self._review(lede=sentence), last, recap)
            self.assertTrue(issues, f"should reject {sentence!r}")

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
        self.assertIn("totalDrives=9", text)
        self.assertIn("KC possession is 25:39", text)
        self.assertIn("MIA possession is 34:21", text)
        self.assertIn("KC total drives are 9", text)
        self.assertIn("MIA total drives are 9", text)
        self.assertIn("KC possession is 37:00", text)
        self.assertIn("IND possession is 33:00", text)
        self.assertNotIn("stays on Indianapolis", text)
        self.assertIn("Never write the opponent clock as Kansas City's", text)
        self.assertIn("Possession time belongs to the team", prompts.SYSTEM_PROMPT)
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
        self.assertIn("rush gap", prompts.SYSTEM_PROMPT)
        self.assertIn("blitz type", prompts.SYSTEM_PROMPT)

    def test_touch_counts_bind_nearest_player_not_recap_order(self):
        recap = json.loads(json.dumps(_load_fixture("espn_401872952_recap.json")))
        recap["touches"] = sorted(
            recap["touches"]
            + [
                {
                    "player": "Emmett Johnson",
                    "team": "OPP",
                    "rushes": 6,
                    "catches": 0,
                    "touches": 6,
                },
                {
                    "player": "Malik Willis",
                    "team": "OPP",
                    "rushes": 9,
                    "catches": 0,
                    "touches": 9,
                },
                {
                    "player": "Ollie Gordon II",
                    "team": "OPP",
                    "rushes": 17,
                    "catches": 3,
                    "touches": 20,
                },
            ],
            key=lambda row: (row.get("player") or "", row.get("team") or ""),
        )
        self.assertEqual(recap["touches"][0]["player"], "Emmett Johnson")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        compound = (
            "Ollie Gordon II matched Walker at 20 touches "
            "(17 rushes, 3 catches), Malik Willis added 9 rushes, "
            "and Emmett Johnson had 6 rushes."
        )
        self.assertEqual(
            facts._check_touch_counts(compound, recap),
            [],
            "Walker's 20 touches must not bind to Johnson or Willis",
        )
        self.assertEqual(
            facts.check_review(self._review(lede=compound), last, recap),
            [],
        )
        semicolon = (
            "Walker handled 18 rushes and 2 catches (20 touches) in Miami; "
            "Emmett Johnson had 6 rushes."
        )
        self.assertEqual(facts._check_touch_counts(semicolon, recap), [])
        wrong = facts._check_touch_counts(
            "Emmett Johnson had 20 touches.", recap
        )
        self.assertTrue(wrong, "Johnson at 20 must still fail against ESPN 6")
        self.assertTrue(
            any("Emmett Johnson" in item and "touches 20" in item for item in wrong),
            wrong,
        )
        self.assertTrue(
            facts.check_review(
                self._review(lede="Emmett Johnson had 20 touches."),
                last,
                recap,
            )
        )

    def _week3_usage_recap(self):
        recap = json.loads(json.dumps(_load_fixture("espn_401872952_recap.json")))
        recap["touches"] = sorted(
            recap["touches"]
            + [
                {
                    "player": "Emmett Johnson",
                    "team": "OPP",
                    "rushes": 6,
                    "catches": 0,
                    "touches": 6,
                },
                {
                    "player": "Malik Willis",
                    "team": "OPP",
                    "rushes": 9,
                    "catches": 0,
                    "touches": 9,
                },
                {
                    "player": "Ollie Gordon II",
                    "team": "OPP",
                    "rushes": 17,
                    "catches": 3,
                    "touches": 20,
                },
            ],
            key=lambda row: (row.get("player") or "", row.get("team") or ""),
        )
        recap["passing"] = list(recap.get("passing") or []) + [
            {
                "player": "Malik Willis",
                "team": "MIA",
                "completions": 20,
                "attempts": 36,
                "sacks": 1,
                "sackYards": 8,
                "touchdowns": 1,
            }
        ]
        return recap

    def test_run_36610306700_clause_binding_accepts_box_and_rejects_errors(self):
        catalog = _load_fixture("edition_run_36610306700.json")
        recap = self._week3_usage_recap()
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        for sentence in catalog["accept"]:
            issues = facts.check_review(self._review(lede=sentence), last, recap)
            self.assertEqual(issues, [], f"should accept {sentence!r}: {issues}")
        for sentence in catalog["reject"]:
            issues = facts.check_review(self._review(lede=sentence), last, recap)
            self.assertTrue(issues, f"should reject {sentence!r}")
        forty = facts.check_review(
            self._review(lede=catalog["reject"][0]), last, recap
        )
        self.assertTrue(any("40" in item and "pass attempts" in item for item in forty), forty)
        poss = facts.check_review(
            self._review(lede=catalog["reject"][1]), last, recap
        )
        self.assertTrue(any("8:42" in item for item in poss), poss)
        team_70 = facts.check_review(
            self._review(lede=catalog["reject"][2]), last, recap
        )
        self.assertTrue(
            any("team rushing 70" in item for item in team_70), team_70
        )
        mia_70 = facts.check_review(
            self._review(
                lede=(
                    "Willis went 20-of-36, and Miami still posted "
                    "70 rushing yards on 31 attempts."
                )
            ),
            last,
            recap,
        )
        self.assertTrue(
            any("team rushing 70" in item and "MIA" in item for item in mia_70),
            mia_70,
        )
        willis_40 = facts.check_review(
            self._review(lede="Willis threw 40 passes."), last, recap
        )
        self.assertTrue(
            any("40" in item and "Malik Willis" in item for item in willis_40),
            willis_40,
        )

    def test_run_36615274992_prior_game_and_newline_binding(self):
        catalog = _load_fixture("edition_run_36615274992.json")
        recap = self._week3_usage_recap()
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        last["id"] = "401872952"
        slate = [
            {
                "id": "401772900",
                "week": 1,
                "opponent": "Denver Broncos",
                "opponentAbbr": "DEN",
                "completed": True,
                "kcScore": 31,
                "oppScore": 10,
            },
            {
                "id": "401872945",
                "week": 2,
                "opponent": "Indianapolis Colts",
                "opponentAbbr": "IND",
                "completed": True,
                "kcScore": 33,
                "oppScore": 30,
            },
            {
                "id": "401872952",
                "week": 3,
                "opponent": "Miami Dolphins",
                "opponentAbbr": "MIA",
                "completed": True,
                "kcScore": 24,
                "oppScore": 10,
            },
        ]
        for sentence in catalog["accept"]:
            issues = facts.check_review(
                self._review(lede=sentence), last, recap, schedule=slate
            )
            self.assertEqual(issues, [], f"should accept {sentence!r}: {issues}")
        for sentence in catalog["reject"]:
            issues = facts.check_review(
                self._review(lede=sentence), last, recap, schedule=slate
            )
            self.assertTrue(issues, f"should reject {sentence!r}")
        team_70 = facts.check_review(
            self._review(lede=catalog["reject"][0]), last, recap, schedule=slate
        )
        self.assertTrue(
            any("team rushing 70" in item for item in team_70), team_70
        )
        mia_5 = facts.check_review(
            self._review(lede=catalog["reject"][1]), last, recap, schedule=slate
        )
        self.assertTrue(
            any("5" in item and "MIA" in item for item in mia_5), mia_5
        )
        nourzad = facts.check_review(
            self._review(lede=catalog["reject"][2]), last, recap, schedule=slate
        )
        self.assertTrue(any("eligible" in item.lower() for item in nourzad), nourzad)
        indy_wrong = facts.check_review(
            self._review(
                lede=(
                    "Against Indianapolis the Chiefs posted 18 first downs "
                    "and 37:00 of possession."
                )
            ),
            last,
            recap,
            schedule=slate,
        )
        self.assertTrue(
            any("first downs 18" in item and "prior" in item for item in indy_wrong),
            indy_wrong,
        )

    def test_run_36617439949_windowed_touches_are_not_walker_game_total(self):
        catalog = _load_fixture("edition_run_36617439949.json")
        recap = self._week3_usage_recap()
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        walker_only = json.loads(json.dumps(_load_fixture("espn_401872952_recap.json")))
        for sentence in catalog["accept"]:
            issues = facts.check_review(self._review(lede=sentence), last, recap)
            self.assertEqual(issues, [], f"should accept {sentence!r}: {issues}")
            lone = facts.check_review(
                self._review(lede=sentence), last, walker_only
            )
            self.assertEqual(lone, [], f"Walker-only recap must skip {sentence!r}: {lone}")
        for sentence in catalog["reject"]:
            issues = facts.check_review(self._review(lede=sentence), last, recap)
            self.assertTrue(issues, f"should reject {sentence!r}")
        walker_1 = facts.check_review(
            self._review(lede=catalog["reject"][0]), last, recap
        )
        self.assertTrue(
            any("touches 1" in item and "Walker" in item for item in walker_1),
            walker_1,
        )
        walker_6 = facts.check_review(
            self._review(lede=catalog["reject"][1]), last, recap
        )
        self.assertTrue(
            any("touches 6" in item and "Walker" in item for item in walker_6),
            walker_6,
        )
        recap["touches"] = list(recap["touches"]) + [
            {
                "player": "Hollywood Brown",
                "team": "KC",
                "rushes": 0,
                "catches": 1,
                "touches": 1,
            },
            {
                "player": "Travis Kelce",
                "team": "KC",
                "rushes": 0,
                "catches": 6,
                "touches": 6,
            },
        ]
        recap["touches"].sort(key=lambda row: (row.get("player") or ""))
        self.assertEqual(recap["touches"][0]["player"], "Emmett Johnson")
        raw = offline.write(
            {
                "news": [],
                "markets": {},
                "lastGameRecap": recap,
            },
            {
                "type": "regular",
                "label": "Week 4",
                "week": 4,
                "mode": "review",
                "lastGame": last,
                "nextGame": {
                    "opponent": "Las Vegas Raiders",
                    "week": 4,
                },
            },
            [{"opponent": "Las Vegas Raiders", "week": 4}],
        )
        text = facts.edition_text(
            schema.normalize(
                raw,
                phase={"type": "regular", "label": "Week 4", "mode": "review"},
                meta={"generatedAt": "2026-09-29T19:11:00+00:00", "generator": "offline"},
            )
        )
        self.assertIn("20 touches", text)
        self.assertNotIn("1 touches", text)
        self.assertNotIn("6 touches", text)
        self.assertIn("Kenneth Walker III", text)

    def test_run_36619750276_scheme_drop_and_attempt_binding(self):
        catalog = _load_fixture("edition_run_36619750276.json")
        recap = self._week3_usage_recap()
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        for sentence in catalog["accept"]:
            issues = facts.check_review(self._review(lede=sentence), last, recap)
            self.assertEqual(issues, [], f"should accept {sentence!r}: {issues}")
        for sentence in catalog["reject"]:
            issues = facts.check_review(self._review(lede=sentence), last, recap)
            self.assertTrue(issues, f"should reject {sentence!r}")
        three_oh = facts.check_review(
            self._review(lede=catalog["accept"][0]), last, recap
        )
        self.assertEqual(three_oh, [])
        twenty = facts.check_review(
            self._review(lede=catalog["accept"][1]), last, recap
        )
        self.assertFalse(
            any("pass attempts 20" in item for item in twenty), twenty
        )
        drops = facts.check_review(
            self._review(lede=catalog["accept"][4]), last, recap
        )
        self.assertFalse(any("40" in item and "pass attempts" in item for item in drops), drops)
        five_hits = facts.check_review(
            self._review(lede=catalog["accept"][5]), last, recap
        )
        self.assertEqual(five_hits, [])
        kc_recorded = facts.check_review(
            self._review(lede=catalog["reject"][3]), last, recap
        )
        self.assertTrue(
            any("QB hits 5" in item and "1" in item for item in kc_recorded),
            kc_recorded,
        )
        team_70 = facts.check_review(
            self._review(lede=catalog["reject"][0]), last, recap
        )
        self.assertTrue(
            any("team rushing 70" in item for item in team_70), team_70
        )
        forty = facts.check_review(
            self._review(lede=catalog["reject"][4]), last, recap
        )
        self.assertTrue(
            any("40" in item and "pass attempts" in item for item in forty), forty
        )
        btt = "Walker had 70 of it between the tackles."
        zone = (
            "Zone blitz Jones/Karlaftis with a dropping end on second-and-long "
            "— the look that helped produce the Willis interception."
        )
        narrative = {
            "headline": "Keep this title",
            "lastGameReview": {
                "lede": "Kansas City finished 24–10 against Miami.",
                "analysis": [btt, zone],
            },
        }
        issues = facts.check_review(narrative, last, recap)
        snippets = facts.violation_snippets(issues)
        self.assertTrue(
            any("between the tackles" in s.lower() for s in snippets), snippets
        )
        self.assertTrue(
            any("zone" in s.lower() and "blitz" in s.lower() for s in snippets),
            snippets,
        )
        repaired = facts.repair_offending_copy(narrative, issues, last)
        self.assertEqual(facts.check_review(repaired, last, recap), [])
        dropped = facts.dropped_sentences(narrative, repaired)
        self.assertTrue(any("between the tackles" in s for s in dropped), dropped)
        self.assertTrue(any("zone blitz" in s.lower() for s in dropped), dropped)
        note = {
            "lastGameReview": {
                "lede": "Kansas City finished 24–10 against Miami.",
                "analysis": [
                    "Walker also had end runs; do not write that the 70 were "
                    "between the tackles.",
                    "play-by-play does not back a zone blitz producing the "
                    "interception.",
                ],
            }
        }
        note_issues = facts.check_review(note, last, recap)
        self.assertFalse(
            any("between the tackles" in item and "do not write" in item for item in note_issues),
            note_issues,
        )
        self.assertFalse(
            any("zone blitz producing" in item for item in note_issues),
            note_issues,
        )
        echo_only = [item for item in note_issues if "echoed writer instruction" in item]
        if echo_only:
            cleaned = facts.repair_offending_copy(note, note_issues, last)
            self.assertEqual(facts.check_review(cleaned, last, recap), [])
        text = prompts.build_user_prompt(
            {"news": [], "lastGameRecap": recap},
            {
                "type": "regular",
                "label": "Week 4",
                "week": 4,
                "mode": "review",
                "lastGame": last,
                "nextGame": None,
            },
            [],
        )
        self.assertIn("SCHEME LIMITS", text)
        self.assertIn("rush gap", text)
        self.assertIn("blitz type", text)
        self.assertIn("40-dropback", prompts.SYSTEM_PROMPT)

    def test_repair_publish_blockers_use_word_floor_not_drop_count(self):
        fat = " ".join(["Chiefs tape review word"] * 400)
        repaired = {
            "headline": fat,
            "dek": fat,
            "theEdge": fat,
            "storyline": fat,
            "currentState": fat,
            "gamePlan": fat,
            "lastGameReview": {"lede": fat, "analysis": [fat]},
        }
        self.assertGreaterEqual(
            facts.edition_word_count(repaired), facts.OFFLINE_WORD_FLOOR
        )
        self.assertEqual(facts.repair_publish_blockers([], repaired, []), [])
        self.assertEqual(
            facts.repair_publish_blockers([], repaired, None),
            [],
        )
        thin = {"headline": "Short", "lastGameReview": {"lede": "Kansas City won."}}
        blockers = facts.repair_publish_blockers([], thin, [], before=repaired)
        self.assertTrue(blockers)
        self.assertTrue(any("floor" in item for item in blockers), blockers)
        self.assertEqual(
            facts.repair_publish_blockers([], thin, [], before=thin),
            [],
        )
        leftover = facts.repair_publish_blockers(
            ["first downs 29 disagrees with ESPN 18 for KC"],
            repaired,
            [],
        )
        self.assertTrue(any("29" in item for item in leftover), leftover)
        orphans = facts.repair_publish_blockers(
            [], repaired, ["orphan opener after repair"]
        )
        self.assertTrue(any("fragments" in item for item in orphans), orphans)

    def test_narrative_pr_lists_drops_and_holds_automerge(self):
        workflow = (
            Path(__file__).resolve().parents[2]
            / ".github"
            / "workflows"
            / "narrative.yml"
        ).read_text(encoding="utf-8")
        parsed = yaml.safe_load(workflow)
        self.assertEqual(parsed["name"], "Chiefs Narrative (daily)")
        self.assertIn("narrative_repair.json", workflow)
        self.assertIn("drop_body.drop_body", workflow)
        update_scripts = "\n".join(
            str(step.get("run") or "")
            for step in ((parsed.get("jobs") or {}).get("update") or {}).get(
                "steps"
            )
            or []
            if isinstance(step, dict)
        )
        self.assertNotIn("python -m tools.chiefs_narrative.drop_body", update_scripts)
        self.assertNotIn("from tools.chiefs_narrative import", update_scripts)
        self.assertNotIn("\nimport json, sys\n", workflow)
        self.assertIn("HOLD_MERGE", workflow)
        self.assertIn("holdAutomerge", workflow)
        self.assertIn("review_requires_human", workflow)
        self.assertIn("Holding automerge", workflow)
        self.assertIn(
            "--check-edition || check_rc=$?",
            workflow,
        )
        self.assertIn(
            'if [ "$check_rc" -ne 0 ]; then',
            workflow,
        )
        for job_name, step_name, script in _workflow_run_scripts(parsed):
            checked = subprocess.run(["bash", "-n"], input=script, capture_output=True, text=True)
            self.assertEqual(
                checked.returncode,
                0,
                f"{job_name}/{step_name}: {checked.stderr}",
            )
            for body in _quoted_py_heredoc_bodies(script):
                compile(body, f"{job_name}/{step_name}", "exec")
        self.assertIn("needs.update.outputs.publish", workflow)
        self.assertLess(workflow.index("HOLD_MERGE"), workflow.index("gh pr merge"))
        self.assertLess(workflow.index("HOLD_MERGE"), workflow.index('echo "publish=true"'))
        hold_block = workflow[workflow.index("HOLD_MERGE") : workflow.index("gh pr merge")]
        self.assertIn("publish=false", hold_block)

    def test_drop_body_renders_held_and_quiet_salvage(self):
        self.assertEqual(drop_body.drop_body({}), "")
        self.assertEqual(drop_body.drop_body({"droppedSentences": []}), "")
        held = drop_body.drop_body(
            {
                "holdAutomerge": True,
                "droppedSentences": ["Kelce scored from the 12."],
            }
        )
        self.assertIn("## Dropped sentences", held)
        self.assertIn("Automerge is held.", held)
        self.assertIn("- Kelce scored from the 12.", held)
        quiet = drop_body.drop_body(
            {
                "holdAutomerge": False,
                "droppedSentences": ["One leftover."],
            }
        )
        self.assertIn("Fact-check salvage removed these lines.", quiet)
        self.assertNotIn("Automerge is held.", quiet)
        rewritten = drop_body.drop_body(
            {
                "holdAutomerge": True,
                "correctedSentences": [
                    "Indianapolis was 29 first downs → Against Indianapolis, Kansas City had 29 first downs"
                ],
            }
        )
        self.assertIn("## Corrected sentences", rewritten)
        self.assertIn("29 first downs", rewritten)
        self.assertIn("Automerge is held.", rewritten)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "narrative_repair.json"
            path.write_text(
                json.dumps({"droppedSentences": ["One leftover."]}),
                encoding="utf-8",
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = drop_body.main([str(path)])
            self.assertEqual(rc, 0)
            self.assertIn("- One leftover.", buf.getvalue())

    def test_workflow_yaml_gate_parses_every_actions_file(self):
        loaded = check_workflows.load_workflows()
        self.assertTrue(loaded)
        names = {path.name: doc.get("name") for path, doc in loaded.items()}
        self.assertEqual(names["narrative.yml"], "Chiefs Narrative (daily)")
        self.assertEqual(names["ci.yml"], "CI gates")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            broken = root / ".github" / "workflows"
            broken.mkdir(parents=True)
            broken.joinpath("broken.yml").write_text(
                "name: Broken\njobs:\nimport json, sys\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                check_workflows.load_workflows(root)

    def test_narrative_review_hold_wiring_is_load_bearing(self):
        """S3–S5 must go red: parse the publish step, not a string grep."""
        root = Path(__file__).resolve().parents[2] / ".github" / "workflows"
        parsed = yaml.safe_load((root / "narrative.yml").read_text(encoding="utf-8"))
        wiring = _narrative_review_hold_wiring(parsed)
        self.assertTrue(wiring["uses_review_requires_human"])
        self.assertEqual(wiring["review_hold_equals"], "true")
        self.assertEqual(wiring["hold_merge_assign"], "true")
        self.assertEqual(wiring["hold_merge_equals"], "true")
        self.assertTrue(wiring["hold_before_merge"])
        self.assertTrue(wiring["hold_sets_publish_false"])
        self.assertTrue(wiring["hold_adds_label"])
        self.assertTrue(wiring["hold_exits"])
        mutated = dict(wiring)
        mutated["hold_merge_assign"] = "$HOLD_MERGE"
        self.assertNotEqual(mutated["hold_merge_assign"], "true")
        mutated["review_hold_equals"] = "yes"
        self.assertNotEqual(mutated["review_hold_equals"], "true")
        mutated["hold_merge_equals"] = "never"
        self.assertNotEqual(mutated["hold_merge_equals"], "true")

    def test_automerge_and_pages_refuse_unsigned_reviews(self):
        """G1–G3 / G6–G8: YAML-parsed wiring. Named mutations in the PR body."""
        root = Path(__file__).resolve().parents[2] / ".github" / "workflows"
        ci = yaml.safe_load((root / "ci.yml").read_text(encoding="utf-8"))
        qa = yaml.safe_load((root / "edition-qa.yml").read_text(encoding="utf-8"))
        pages = yaml.safe_load((root / "pages.yml").read_text(encoding="utf-8"))
        narrative = yaml.safe_load((root / "narrative.yml").read_text(encoding="utf-8"))
        # G1: drop the ci automerge gate step.
        # G2: drop the edition-qa automerge gate step.
        for parsed, job in ((ci, "automerge"), (qa, "automerge")):
            scripts = "\n".join(
                str(step.get("run") or "")
                for step in ((parsed.get("jobs") or {}).get(job) or {}).get("steps")
                or []
                if isinstance(step, dict)
            )
            self.assertIn("GATE_BASE", scripts, job)
            self.assertIn("git worktree add", scripts, job)
            self.assertIn('cd "$GATE_BASE"', scripts, job)
            self.assertIn(
                "python -P -m tools.chiefs_narrative.review_gate --automerge",
                scripts,
                job,
            )
            self.assertIn('--root "${GITHUB_WORKSPACE}"', scripts, job)
            self.assertNotRegex(
                scripts,
                r'PYTHONPATH="\$GATE_BASE" python -m tools\.chiefs_narrative\.review_gate',
            )
            checkouts = [
                step
                for step in ((parsed.get("jobs") or {}).get(job) or {}).get(
                    "steps"
                )
                or []
                if isinstance(step, dict)
                and "actions/checkout" in str(step.get("uses") or "")
            ]
            self.assertTrue(checkouts, job)
            for step in checkouts:
                self.assertEqual(
                    (step.get("with") or {}).get("persist-credentials"),
                    False,
                    job,
                )
            self.assertLess(
                scripts.index("review_gate --automerge"),
                scripts.index("gh pr merge"),
            )
            # G8: `review_gate --automerge || true`
            self.assertIsNone(
                re.search(r"review_gate --automerge[^\n]*\|\|\s*true", scripts),
                job,
            )
            self.assertNotIn("--labels", scripts)
            self.assertNotIn("qa-pass", scripts)
        pages_scripts = "\n".join(
            str(step.get("run") or "")
            for step in ((pages.get("jobs") or {}).get("build-deploy") or {}).get(
                "steps"
            )
            or []
            if isinstance(step, dict)
        )
        self.assertIn(
            "python -m tools.chiefs_narrative.review_gate --pages --dist dist",
            pages_scripts,
        )
        self.assertLess(
            pages_scripts.index("hugo --gc --minify"),
            pages_scripts.index("review_gate --pages --dist dist"),
        )
        # G7: `review_gate --pages || true`
        self.assertIsNone(
            re.search(r"review_gate --pages[^\n]*\|\|\s*true", pages_scripts)
        )
        pages_gate = _job_step_script(
            pages, "build-deploy", name="Skip unsigned Review editions"
        ) or _job_step_script(pages, "build-deploy", step_id="review")
        self.assertIn("review_gate --pages --dist dist", pages_gate)
        blocked = pages_gate[pages_gate.find("else") :]
        # G6: the pages blocked branch writes skip=false.
        self.assertIn('echo "skip=true"', blocked)
        self.assertNotIn("skip=false", blocked)
        publish = next(
            step
            for step in ((pages.get("jobs") or {}).get("build-deploy") or {}).get(
                "steps"
            )
            or []
            if isinstance(step, dict) and step.get("name") == "Publish to gh-pages"
        )
        # G3: remove the pages Publish `if`.
        self.assertEqual(publish.get("if"), "steps.review.outputs.skip != 'true'")
        narr_gate = _job_step_script(
            narrative, "deploy", name="Skip unsigned Review editions"
        ) or _job_step_script(narrative, "deploy", step_id="review")
        self.assertIn("review_gate --pages", narr_gate)
        self.assertIn("--dist", narr_gate)
        self.assertIsNone(
            re.search(r"review_gate --pages[^\n]*\|\|\s*true", narr_gate)
        )
        narr_blocked = narr_gate[narr_gate.find("else") :]
        self.assertIn('echo "skip=true"', narr_blocked)
        self.assertNotIn("skip=false", narr_blocked)
        narr_scripts = "\n".join(
            str(step.get("run") or "")
            for step in ((narrative.get("jobs") or {}).get("deploy") or {}).get(
                "steps"
            )
            or []
            if isinstance(step, dict)
        )
        self.assertLess(
            narr_scripts.index("hugo --gc --minify"),
            narr_scripts.index("review_gate --pages"),
        )
        narr_publish = next(
            step
            for step in ((narrative.get("jobs") or {}).get("deploy") or {}).get(
                "steps"
            )
            or []
            if isinstance(step, dict) and step.get("name") == "Publish to gh-pages"
        )
        self.assertEqual(
            narr_publish.get("if"), "steps.review.outputs.skip != 'true'"
        )
        ci_gates = "\n".join(
            str(step.get("run") or "")
            for step in ((ci.get("jobs") or {}).get("gates") or {}).get("steps")
            or []
            if isinstance(step, dict)
        )
        self.assertIn("pages.yml:", ci_gates)
        self.assertIn("narrative.yml:", ci_gates)
        self.assertIn(
            "python -m tools.chiefs_narrative.review_gate --pages --dist dist",
            ci_gates,
        )
        self.assertLess(
            ci_gates.index("hugo --gc --minify"),
            ci_gates.index("review_gate --pages --dist dist"),
        )
        self.assertIn('echo "skip=false"', ci_gates)
        self.assertIn('grep -qx "skip=false"', ci_gates)
        # B3: workflow is read-only; write lives only on automerge.
        self.assertEqual(ci.get("permissions"), {"contents": "read"})
        self.assertNotIn("contents: write", yaml.dump(ci.get("jobs", {}).get("gates") or {}))
        self.assertEqual(
            ((ci.get("jobs") or {}).get("automerge") or {}).get("permissions"),
            {
                "contents": "write",
                "pull-requests": "write",
                "actions": "write",
            },
        )
        gates_checkouts = [
            step
            for step in ((ci.get("jobs") or {}).get("gates") or {}).get("steps")
            or []
            if isinstance(step, dict)
            and "actions/checkout" in str(step.get("uses") or "")
        ]
        self.assertTrue(gates_checkouts)
        for step in gates_checkouts:
            self.assertEqual(
                (step.get("with") or {}).get("persist-credentials"),
                False,
            )
        pages_job = (pages.get("jobs") or {}).get("build-deploy") or {}
        self.assertEqual(pages_job.get("if"), "github.ref == 'refs/heads/main'")
        pages_checkouts = [
            step
            for step in pages_job.get("steps") or []
            if isinstance(step, dict)
            and "actions/checkout" in str(step.get("uses") or "")
        ]
        self.assertTrue(pages_checkouts)
        for step in pages_checkouts:
            self.assertEqual(
                (step.get("with") or {}).get("persist-credentials"),
                False,
            )
        narr_job = (narrative.get("jobs") or {}).get("deploy") or {}
        self.assertIn("github.ref == 'refs/heads/main'", str(narr_job.get("if")))
        self.assertEqual(narrative.get("permissions"), {"contents": "read"})
        for job_name in ("generate", "test", "update", "deploy"):
            narr_checkouts = [
                step
                for step in ((narrative.get("jobs") or {}).get(job_name) or {}).get(
                    "steps"
                )
                or []
                if isinstance(step, dict)
                and "actions/checkout" in str(step.get("uses") or "")
            ]
            self.assertTrue(narr_checkouts, job_name)
            for step in narr_checkouts:
                self.assertEqual(
                    (step.get("with") or {}).get("persist-credentials"),
                    False,
                    job_name,
                )

    def test_review_gate_blocks_unsigned_review_json(self):
        gate_src = (
            Path(__file__).resolve().parents[2]
            / "tools"
            / "chiefs_narrative"
            / "review_gate.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("import facts", gate_src)
        self.assertNotIn("from tools.chiefs_narrative import config, facts", gate_src)
        self.assertEqual(review_gate.SIGNOFF_DIR, "signoff/review")
        self.assertNotIn('SIGNOFF_DIR = "data/review_signoff"', gate_src)
        self.assertNotIn("HUMAN_MERGE_PREFIXES", gate_src)
        self.assertNotIn("HUMAN_MERGE_PATHS", gate_src)
        self.assertIn("AUTOMERGE_ALLOWED_PATHS", gate_src)
        self.assertIn("AUTOMERGE_ALLOWED_PREFIXES = ()", gate_src)
        self.assertNotIn('"tools/tests/"', gate_src)
        self.assertNotIn("tools/requirements.txt", gate_src)
        self.assertIn("hugo config", gate_src)
        self.assertIn("staticdir", gate_src)
        self.assertIn("contentdir", gate_src)
        self.assertIn("datadir", gate_src)
        self.assertIn("layoutdir", gate_src)
        self.assertIn("mounts", gate_src)
        self.assertIn("output_blocked", gate_src)
        self.assertNotIn("qa-pass", gate_src)
        self.assertNotIn("QA_PASS", gate_src)
        self.assertNotIn('"pass" in', gate_src)
        self.assertNotIn("'pass' in", gate_src)
        preview = {
            "slug": "2026-10-03-1538",
            "edition": "2026 Week 4 · Preview",
            "phase": {"mode": "preview"},
        }
        review = {
            "slug": "2026-10-05-0937",
            "edition": "2026 Week 6 · Week 4 Review",
            "phase": {"mode": "review"},
        }
        mode_only = {
            "slug": "2026-10-05-0937",
            "edition": "2026 Week 6 · Preview",
            "phase": {"mode": "review"},
        }
        title_only = {
            "slug": "2026-10-05-0937",
            "edition": "2026 Week 6 · Week 4 Review",
            "phase": {"mode": "preview"},
        }
        recap_label = {
            "slug": "2026-10-05-0937",
            "edition": "2026 Week 5 Review",
            "phase": {"mode": "recap"},
        }
        postgame_label = {
            "slug": "2026-10-05-0937",
            "edition": "2026 Week 5 · Review",
            "phase": {"mode": "postgame"},
        }
        endash_preview = {
            "slug": "2026-10-05-0937",
            "edition": "2026 Week 5 – Week 4 Review",
            "phase": {"mode": "preview"},
        }
        phase_string = {
            "slug": "2026-10-05-0937",
            "edition": "",
            "phase": "Week 5 Review",
        }
        middot_label = {
            "slug": "2026-10-05-0937",
            "edition": "2026 Week 5 &middot; Review",
            "phase": {"mode": "preview"},
        }
        final_only = {
            "slug": "2026-10-05-0937",
            "edition": "desk notes",
            "phase": {},
            "lastGame": {
                "completed": True,
                "kcScore": 27,
                "oppScore": 17,
            },
        }
        recap_mode_only = {
            "slug": "2026-10-05-0937",
            "edition": "desk notes",
            "phase": {"mode": "recap"},
        }
        postgame_mode_only = {
            "slug": "2026-10-05-0937",
            "edition": "desk notes",
            "phase": {"mode": "postgame"},
        }
        body_score_only = {
            "slug": "2026-10-05-0937",
            "edition": "desk notes",
            "phase": {},
            "lastGameReview": {"lede": "Kansas City finished 27-17."},
        }
        preview_with_final = {
            "slug": "2026-10-03-1538",
            "edition": "2026 Week 4 · Preview",
            "phase": {"mode": "preview"},
            "lastGame": {
                "completed": True,
                "kcScore": 24,
                "oppScore": 10,
            },
        }
        self.assertFalse(review_gate.should_block_review(preview))
        self.assertTrue(review_gate.should_block_review(review))
        # G18: drop the phase.mode check.
        self.assertTrue(review_gate.should_block_review(mode_only))
        self.assertTrue(review_gate.should_block_review(title_only))
        # C2 / C3 / C4 / C6 / C10: mode or Review label, not the middot form.
        self.assertTrue(review_gate.payload_is_review(recap_label))
        self.assertTrue(review_gate.payload_is_review(postgame_label))
        self.assertTrue(review_gate.payload_is_review(endash_preview))
        self.assertTrue(review_gate.payload_is_review(phase_string))
        self.assertTrue(review_gate.payload_is_review(middot_label))
        self.assertTrue(review_gate.payload_is_review(final_only))
        self.assertFalse(review_gate.payload_is_review(preview_with_final))
        # C_drop_recap_postgame: mode alone, no score and no Review label.
        self.assertEqual(
            review_gate._REVIEW_MODES, frozenset({"review", "recap", "postgame"})
        )
        self.assertEqual(
            facts._REVIEW_MODES, frozenset({"review", "recap", "postgame"})
        )
        self.assertTrue(review_gate.payload_is_review(recap_mode_only))
        self.assertTrue(review_gate.payload_is_review(postgame_mode_only))
        self.assertTrue(review_gate.payload_is_review(body_score_only))
        self.assertTrue(facts.is_review_edition(recap_label))
        self.assertTrue(facts.is_review_edition(postgame_label))
        self.assertTrue(facts.is_review_edition(endash_preview))
        self.assertTrue(facts.is_review_edition(phase_string))
        self.assertTrue(facts.is_review_edition(middot_label))
        self.assertTrue(facts.is_review_edition(final_only))
        self.assertTrue(facts.is_review_edition(recap_mode_only))
        self.assertTrue(facts.is_review_edition(postgame_mode_only))
        self.assertTrue(facts.is_review_edition(body_score_only))
        self.assertFalse(facts.is_review_edition(preview_with_final))
        # G17: any label counts as qa-pass. Labels are gone.
        self.assertTrue(review_gate.should_block_review(review))
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            priv, pub = _ed25519_keypair(tmp_path)
            env = {"REVIEW_SIGNOFF_PUBKEY": str(pub)}
            root = tmp_path / "signed"
            unsigned = tmp_path / "unsigned"
            slug = "narrative"
            editions_name = slug + "_editions"
            live_name = slug + ".json"
            review_rel = "data/" + editions_name + "/2026-10-05-0937.json"
            for tree in (root, unsigned):
                editions = tree / "data" / editions_name
                editions.mkdir(parents=True)
            review_bytes = (json.dumps(review) + "\n").encode("utf-8")
            preview_bytes = (json.dumps(preview) + "\n").encode("utf-8")
            (root / "data" / editions_name / "2026-10-05-0937.json").write_bytes(
                review_bytes
            )
            (root / "data" / live_name).write_bytes(preview_bytes)
            (unsigned / "data" / live_name).write_bytes(review_bytes)
            (unsigned / "data" / editions_name / "2026-10-05-0937.json").write_bytes(
                review_bytes
            )
            with patch.dict(os.environ, env, clear=False):
                self.assertTrue(
                    review_gate.automerge_blocked([review_rel], root=root)
                )
                self.assertTrue(
                    review_gate.automerge_blocked(
                        ["tools/chiefs_narrative/facts.py"], root=root
                    )
                )
                self.assertTrue(
                    review_gate.requires_human_merge(
                        ["tools/chiefs_narrative/review_gate.py"]
                    )
                )
                self.assertTrue(
                    review_gate.requires_human_merge(
                        ["tools/chiefs_narrative/config.py"]
                    )
                )
                self.assertTrue(
                    review_gate.requires_human_merge(
                        ["tools/chiefs_narrative/__init__.py"]
                    )
                )
                self.assertTrue(
                    review_gate.requires_human_merge(["tools/__init__.py"])
                )
                self.assertTrue(
                    review_gate.requires_human_merge(
                        ["layouts/narrative/single.html"]
                    )
                )
                self.assertTrue(
                    review_gate.requires_human_merge(["hugo.yaml"])
                )
                self.assertTrue(
                    review_gate.requires_human_merge(["hugo.toml"])
                )
                self.assertTrue(
                    review_gate.requires_human_merge(["hugo.json"])
                )
                self.assertTrue(
                    review_gate.requires_human_merge(
                        ["config/_default/hugo.yaml"]
                    )
                )
                self.assertTrue(
                    review_gate.requires_human_merge(
                        ["static/narrative/x/index.html"]
                    )
                )
                self.assertTrue(
                    review_gate.requires_human_merge(["assets/css/x.css"])
                )
                self.assertTrue(
                    review_gate.requires_human_merge(
                        ["content/narrative/_content.gotmpl"]
                    )
                )
                self.assertTrue(
                    review_gate.requires_human_merge(
                        ["scripts/review_signoff_pubkey.pem"]
                    )
                )
                self.assertTrue(
                    review_gate.requires_human_merge(
                        ["scripts/review_legacy_editions.txt"]
                    )
                )
                self.assertTrue(
                    review_gate.requires_human_merge(
                        ["signoff/review/2026-10-05-0937.json"]
                    )
                )
                self.assertTrue(
                    review_gate.requires_human_merge([".github/workflows/ci.yml"])
                )
                self.assertTrue(
                    review_gate.requires_human_merge(["CODEOWNERS"])
                )
                live_rel = "data/" + live_name
                preview_rel = "data/" + editions_name + "/2026-10-03-1538.json"
                (root / "data" / editions_name / "2026-10-03-1538.json").write_bytes(
                    preview_bytes
                )
                self.assertFalse(review_gate.requires_human_merge([live_rel]))
                self.assertFalse(review_gate.requires_human_merge([preview_rel]))
                self.assertTrue(
                    review_gate.requires_human_merge(
                        ["tools/tests/test_narrative.py"]
                    )
                )
                self.assertTrue(
                    review_gate.requires_human_merge(["tools/requirements.txt"])
                )
                self.assertTrue(
                    review_gate.requires_human_merge(["tools/run_local.sh"])
                )
                self.assertTrue(
                    review_gate.requires_human_merge(["tools/run_local.ps1"])
                )
                self.assertTrue(
                    review_gate.requires_human_merge(["tools/.env.example"])
                )
                self.assertTrue(
                    review_gate.automerge_blocked(
                        ["tools/tests/test_evil.py"], root=root
                    )
                )
                self.assertTrue(
                    review_gate.requires_human_merge(
                        ["public/narrative/2026-10-05-0937/index.html"]
                    )
                )
                self.assertTrue(review_gate.requires_human_merge(["README.md"]))
                self.assertFalse(review_gate.automerge_allowlisted("public/x.html"))
                self.assertTrue(
                    review_gate.automerge_blocked(
                        ["tools/chiefs_narrative/review_gate.py"], root=root
                    )
                )
                self.assertFalse(
                    review_gate.automerge_blocked([live_rel], root=root)
                )
                self.assertFalse(
                    review_gate.automerge_blocked([preview_rel], root=root)
                )
                self.assertEqual(
                    review_gate.main(
                        ["--automerge", "--root", str(root), review_rel]
                    ),
                    1,
                )
                # G14: any sign-off file counting.
                fake = root / "signoff" / "review"
                fake.mkdir(parents=True)
                (fake / "2026-10-05-0937.json").write_text(
                    json.dumps({"result": "PASS", "by": "Karen"}) + "\n",
                    encoding="utf-8",
                )
                self.assertTrue(
                    review_gate.should_block_review(
                        review, root=root, edition_file=root / review_rel
                    )
                )
                (fake / "2026-10-05-0937.json").write_bytes(
                    review_gate.signoff_canonical_bytes(
                        "2026-10-05-0937",
                        hashlib.sha256(review_bytes).hexdigest(),
                    )
                )
                (fake / "2026-10-05-0937.sig").write_text(
                    "not-a-signature\n", encoding="ascii"
                )
                self.assertTrue(
                    review_gate.should_block_review(
                        review, root=root, edition_file=root / review_rel
                    )
                )
                _sign_review_edition(
                    priv, "2026-10-05-0937", review_bytes, fake
                )
                self.assertFalse(
                    review_gate.should_block_review(
                        review, root=root, edition_file=root / review_rel
                    )
                )
                other = review_gate.signoff_canonical_bytes(
                    "2026-10-03-1538",
                    hashlib.sha256(preview_bytes).hexdigest(),
                )
                (fake / "2026-10-05-0937.json").write_bytes(other)
                self.assertTrue(
                    review_gate.should_block_review(
                        review, root=root, edition_file=root / review_rel
                    )
                )
                _sign_review_edition(
                    priv, "2026-10-05-0937", review_bytes, fake
                )
                # G13: pages_blocked returns False when files changed.
                self.assertTrue(
                    review_gate.pages_blocked(["README.md"], root=unsigned)
                )
                self.assertTrue(
                    review_gate.pages_blocked(
                        ["data/schedule_2026.json"], root=unsigned
                    )
                )
                self.assertTrue(review_gate.pages_blocked(root=unsigned))
                self.assertFalse(
                    review_gate.pages_blocked(["README.md"], root=root)
                )
                self.assertFalse(review_gate.pages_blocked(root=root))
                self.assertEqual(
                    review_gate.main(
                        ["--pages", "--root", str(unsigned), "README.md"]
                    ),
                    2,
                )
                self.assertEqual(
                    review_gate.main(
                        ["--pages", "--root", str(root), "README.md"]
                    ),
                    0,
                )
                self.assertEqual(
                    review_gate.main(
                        ["--automerge", "--root", str(root), review_rel]
                    ),
                    1,
                )
                self.assertTrue(
                    review_gate.automerge_blocked([review_rel], root=root)
                )
                self.assertEqual(
                    review_gate.main(
                        ["--automerge", "--root", str(root), preview_rel]
                    ),
                    0,
                )
                self.assertEqual(
                    review_gate.main(["--automerge", "--root", str(tmp_path)]),
                    1,
                )
                self.assertEqual(
                    review_gate.main(["--pages", "--root", str(tmp_path)]),
                    2,
                )

    def test_review_gate_signature_mutations_are_red(self):
        """X1–X13: pin the crypto, slug, and published-edition scan."""
        review = {
            "slug": "2026-10-05-0937",
            "edition": "2026 Week 6 · Week 4 Review",
            "phase": {"mode": "review"},
        }
        preview = {
            "slug": "2026-10-03-1538",
            "edition": "2026 Week 4 · Preview",
            "phase": {"mode": "preview"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "a").mkdir()
            (tmp_path / "b").mkdir()
            priv_a, pub_a = _ed25519_keypair(tmp_path / "a")
            priv_b, pub_b = _ed25519_keypair(tmp_path / "b")
            del pub_b
            root = tmp_path / "tree"
            live = "narrative"
            editions_name = live + "_editions"
            live_name = live + ".json"
            editions = root / "data" / editions_name
            signoff = root / "signoff" / "review"
            editions.mkdir(parents=True)
            signoff.mkdir(parents=True)
            review_bytes = (json.dumps(review) + "\n").encode("utf-8")
            preview_bytes = (json.dumps(preview) + "\n").encode("utf-8")
            (root / "data" / live_name).write_bytes(preview_bytes)
            (editions / "2026-10-05-0937.json").write_bytes(review_bytes)
            sha = hashlib.sha256(review_bytes).hexdigest()
            slug = "2026-10-05-0937"
            env_a = {"REVIEW_SIGNOFF_PUBKEY": str(pub_a)}

            def _write_sig(message: bytes, priv: Path, name: str = slug) -> None:
                (signoff / f"{name}.json").write_bytes(message)
                (signoff / f"{name}.sig").write_text(
                    base64.b64encode(_sign_message(priv, message)).decode(
                        "ascii"
                    ),
                    encoding="ascii",
                )

            with patch.dict(os.environ, env_a, clear=False):
                # X13: missing narrative.json must block, not deploy.
                missing = tmp_path / "missing"
                missing.mkdir()
                self.assertTrue(review_gate.pages_blocked(root=missing))

                # Archive-only unsigned Review must block (P7/P8/P9).
                self.assertTrue(
                    review_gate.pages_blocked(["README.md"], root=root)
                )
                _sign_review_edition(priv_a, slug, review_bytes, signoff)
                self.assertFalse(
                    review_gate.pages_blocked(["README.md"], root=root)
                )

                # X1 / X2b / X2c: any 64-byte signature is not enough.
                junk = base64.b64encode(os.urandom(64)).decode("ascii")
                (signoff / f"{slug}.json").write_bytes(
                    review_gate.signoff_canonical_bytes(slug, sha)
                )
                (signoff / f"{slug}.sig").write_text(junk + "\n", encoding="ascii")
                self.assertTrue(
                    review_gate.should_block_review(
                        review,
                        root=root,
                        edition_file=editions / f"{slug}.json",
                    )
                )

                # X2: wrong-key 64-byte signature.
                _write_sig(review_gate.signoff_canonical_bytes(slug, sha), priv_b)
                self.assertTrue(
                    review_gate.should_block_review(
                        review,
                        root=root,
                        edition_file=editions / f"{slug}.json",
                    )
                )

                # X4b / sha-only mismatch: valid crypto over the wrong sha.
                wrong_sha = "ab" * 32
                _write_sig(
                    review_gate.signoff_canonical_bytes(slug, wrong_sha), priv_a
                )
                self.assertTrue(
                    review_gate.should_block_review(
                        review,
                        root=root,
                        edition_file=editions / f"{slug}.json",
                    )
                )

                # X3: case-insensitive sha compare must not pass.
                _write_sig(
                    review_gate.signoff_canonical_bytes(slug, sha.upper()),
                    priv_a,
                )
                self.assertTrue(
                    review_gate.should_block_review(
                        review,
                        root=root,
                        edition_file=editions / f"{slug}.json",
                    )
                )

                # X4: prefix sha compare must not pass.
                _write_sig(
                    review_gate.signoff_canonical_bytes(slug, sha[:8]), priv_a
                )
                self.assertTrue(
                    review_gate.should_block_review(
                        review,
                        root=root,
                        edition_file=editions / f"{slug}.json",
                    )
                )

                # X8: exact-bytes JSON check. Pretty JSON is a different message.
                pretty = (
                    json.dumps(
                        {"slug": slug, "verdict": "PASS", "sha": sha},
                        indent=2,
                    )
                    + "\n"
                ).encode("ascii")
                _write_sig(pretty, priv_a)
                self.assertTrue(
                    review_gate.should_block_review(
                        review,
                        root=root,
                        edition_file=editions / f"{slug}.json",
                    )
                )

                # X11: slug traversal is never a valid sign-off target.
                for bad_slug in ("../narrative", "2026-10-05/0937", ".."):
                    payload = dict(review)
                    payload["slug"] = bad_slug
                    self.assertFalse(review_gate._safe_signoff_slug(bad_slug))
                    self.assertFalse(
                        review_gate.verify_signoff(
                            bad_slug, review_bytes, root=root
                        )
                    )
                    self.assertTrue(
                        review_gate.should_block_review(
                            payload,
                            root=root,
                            edition_file=editions / f"{slug}.json",
                        )
                    )

                # X6: malformed editions JSON is not "not a Review".
                broken = tmp_path / "broken"
                (broken / "data" / editions_name).mkdir(parents=True)
                (broken / "data" / live_name).write_bytes(preview_bytes)
                (broken / "data" / editions_name / "2026-10-05-0937.json").write_text(
                    "{not-json", encoding="utf-8"
                )
                with self.assertRaises(review_gate.GateError):
                    review_gate.pages_blocked(root=broken)
                self.assertEqual(
                    review_gate.main(
                        ["--pages", "--root", str(broken), "README.md"]
                    ),
                    1,
                )

            # X2 / real committed pubkey: REVIEW_SIGNOFF_PUBKEY unset.
            env_clear = {
                key: value
                for key, value in os.environ.items()
                if key != "REVIEW_SIGNOFF_PUBKEY"
            }
            with patch.dict(os.environ, env_clear, clear=True):
                self.assertIsNone(os.environ.get("REVIEW_SIGNOFF_PUBKEY"))
                real_pub = review_gate.public_key_path()
                self.assertEqual(
                    real_pub,
                    Path(__file__).resolve().parents[2]
                    / "scripts"
                    / "review_signoff_pubkey.pem",
                )
                self.assertTrue(real_pub.is_file())
                planted_pub = root / "scripts"
                planted_pub.mkdir(parents=True)
                shutil.copy(pub_a, planted_pub / "review_signoff_pubkey.pem")
                _sign_review_edition(priv_a, slug, review_bytes, signoff)
                # Throwaway-signed edition must fail under Karen's real key,
                # even if the evaluated tree planted a matching pubkey.
                self.assertTrue(
                    review_gate.should_block_review(
                        review,
                        root=root,
                        edition_file=editions / f"{slug}.json",
                    )
                )
                (signoff / f"{slug}.json").write_bytes(
                    review_gate.signoff_canonical_bytes(slug, sha)
                )
                (signoff / f"{slug}.sig").write_text(
                    base64.b64encode(os.urandom(64)).decode("ascii") + "\n",
                    encoding="ascii",
                )
                self.assertFalse(
                    review_gate.verify_signoff(slug, review_bytes, root=root)
                )

    def test_pages_blocked_allows_current_repo_tree(self):
        """Live deployable tree is origin/main, not a PR or overlay checkout.

        narrative.yml overlays generated data before unittest. An unsigned
        Review in the working tree is supposed to block pages; this test
        must still pass against the main tip so a post-game run can open
        a held PR. HEAD may itself be that held unsigned Review.
        """
        repo = Path(__file__).resolve().parents[2]
        pins = review_gate.load_legacy_pins(repo)
        self.assertEqual(len(pins), 8)
        slugs = [review_gate._legacy_slug_from_path(path) for path in pins]
        self.assertTrue(all(review_gate._legacy_date_ok(slug) for slug in slugs))
        editions_rel = "data/" + "narrative" + "_editions/"
        self.assertTrue(all(path.startswith(editions_rel) for path in pins))
        self.assertTrue(all("/2026-10-" not in path for path in pins))
        with tempfile.TemporaryDirectory() as tmp:
            clean = Path(tmp) / "main"
            tip = "origin/main"
            if subprocess.run(
                ["git", "rev-parse", "--verify", tip],
                cwd=repo,
                capture_output=True,
                text=True,
            ).returncode != 0:
                fetched = subprocess.run(
                    ["git", "fetch", "--no-tags", "origin", "main"],
                    cwd=repo,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(fetched.returncode, 0, fetched.stderr)
            added = subprocess.run(
                ["git", "worktree", "add", "--detach", str(clean), tip],
                cwd=repo,
                capture_output=True,
                text=True,
            )
            self.assertEqual(added.returncode, 0, added.stderr)
            try:
                self.assertFalse(review_gate.pages_blocked(root=clean))
                self.assertEqual(
                    review_gate.main(["--pages", "--root", str(clean)]), 0
                )
                hugo_bin = _hugo_bin()
                if hugo_bin:
                    cfg = review_gate.load_hugo_config(clean)
                    self.assertEqual(cfg["staticdir"], ["public"])
                    self.assertEqual(cfg["contentdir"], "content")
                    self.assertEqual(cfg["datadir"], "data")
                    self.assertEqual(cfg["layoutdir"], "layouts")
                    self.assertTrue(
                        any(
                            isinstance(mount, dict)
                            and mount.get("source") == "public"
                            and str(mount.get("target") or "").startswith("static")
                            for mount in (cfg.get("module") or {}).get("mounts")
                            or []
                        )
                    )
                    dest = Path(tmp) / "dist"
                    result = subprocess.run(
                        [hugo_bin, "--gc", "--minify", "--destination", str(dest)],
                        cwd=clean,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertEqual(
                        result.returncode, 0, result.stdout + result.stderr
                    )
                    self.assertFalse(review_gate.output_blocked(dest, root=clean))
                    self.assertEqual(
                        review_gate.main(
                            ["--pages", "--root", str(clean), "--dist", str(dest)]
                        ),
                        0,
                    )
            finally:
                subprocess.run(
                    ["git", "worktree", "remove", "--force", str(clean)],
                    cwd=repo,
                    capture_output=True,
                    text=True,
                )

    def test_legacy_review_manifest_mutations_are_red(self):
        """L1 unlisted / L2 edited bytes / L3 empty manifest all block."""
        preview = {
            "slug": "2026-10-03-1538",
            "edition": "2026 Week 4 · Preview",
            "phase": {"mode": "preview"},
        }
        listed = {
            "slug": "2026-09-29-1734",
            "edition": "2026 Week 4 · Week 3 Review",
            "phase": {"mode": "review"},
        }
        unlisted = {
            "slug": "2026-09-15-1200",
            "edition": "2026 Week 2 · Week 1 Review",
            "phase": {"mode": "review"},
        }
        live = "narrative"
        editions_name = live + "_editions"
        live_name = live + ".json"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            editions = root / "data" / editions_name
            scripts = root / "scripts"
            editions.mkdir(parents=True)
            scripts.mkdir(parents=True)
            preview_bytes = (json.dumps(preview) + "\n").encode("utf-8")
            listed_bytes = (json.dumps(listed) + "\n").encode("utf-8")
            unlisted_bytes = (json.dumps(unlisted) + "\n").encode("utf-8")
            (root / "data" / live_name).write_bytes(preview_bytes)
            listed_sha = hashlib.sha256(listed_bytes).hexdigest()
            manifest = scripts / "review_legacy_editions.txt"

            def _write_pin(text: str) -> None:
                manifest.write_text(text, encoding="ascii")

            # Listed + matching bytes: allowed.
            (editions / "2026-09-29-1734.json").write_bytes(listed_bytes)
            editions_rel = "data/" + "narrative" + "_editions"
            _write_pin(
                f"{editions_rel}/2026-09-29-1734.json {listed_sha}\n"
            )
            self.assertFalse(review_gate.pages_blocked(root=root))

            # L1: unlisted legacy Review blocks.
            (editions / "2026-09-15-1200.json").write_bytes(unlisted_bytes)
            self.assertTrue(review_gate.pages_blocked(root=root))
            (editions / "2026-09-15-1200.json").unlink()
            self.assertFalse(review_gate.pages_blocked(root=root))

            # L2: listed slug whose bytes no longer match the pin blocks.
            (editions / "2026-09-29-1734.json").write_bytes(
                listed_bytes + b" "
            )
            self.assertTrue(review_gate.pages_blocked(root=root))
            (editions / "2026-09-29-1734.json").write_bytes(listed_bytes)
            self.assertFalse(review_gate.pages_blocked(root=root))

            # L3: empty manifest blocks the unsigned listed Review.
            _write_pin("")
            self.assertEqual(review_gate.load_legacy_pins(root), {})
            self.assertTrue(review_gate.pages_blocked(root=root))
            _write_pin("# comments only\n")
            self.assertEqual(review_gate.load_legacy_pins(root), {})
            self.assertTrue(review_gate.pages_blocked(root=root))

            # M1: same bytes as narrative.json do not inherit the editions pin.
            live_as_legacy = root / "data" / live_name
            live_as_legacy.write_bytes(listed_bytes)
            editions_rel = "data/" + "narrative" + "_editions"
            _write_pin(
                f"{editions_rel}/2026-09-29-1734.json {listed_sha}\n"
            )
            (editions / "2026-09-29-1734.json").write_bytes(listed_bytes)
            self.assertTrue(
                review_gate.should_block_review(
                    listed, root=root, edition_file=live_as_legacy
                )
            )
            live_as_legacy.write_bytes(preview_bytes)

            # M2: same bytes under a new slug path do not inherit the pin.
            (editions / "2026-09-20-1200.json").write_bytes(listed_bytes)
            self.assertTrue(
                review_gate.should_block_review(
                    {
                        "slug": "2026-09-20-1200",
                        "edition": "2026 Week 3 · Week 2 Review",
                        "phase": {"mode": "review"},
                    },
                    root=root,
                    edition_file=editions / "2026-09-20-1200.json",
                )
            )
            (editions / "2026-09-20-1200.json").unlink()

            # N3 / N3b: prefix sha compare must not pass.
            self.assertFalse(
                review_gate._legacy_sha_matches(listed_sha, listed_sha[:12])
            )
            self.assertFalse(
                review_gate._legacy_sha_matches(listed_sha, listed_sha[:63])
            )
            self.assertTrue(
                review_gate._legacy_sha_matches(listed_sha, listed_sha)
            )
            gate_src = (
                Path(__file__).resolve().parents[2]
                / "tools"
                / "chiefs_narrative"
                / "review_gate.py"
            ).read_text(encoding="utf-8")
            self.assertIn("return actual == pinned", gate_src)
            self.assertNotIn("actual.startswith(pinned)", gate_src)
            self.assertNotIn("actual[:12]", gate_src)
            self.assertNotIn("actual[:63]", gate_src)
            self.assertIn("_legacy_sha_matches(", gate_src)

            # N4: date cutoff removed must fail — a 2026-10-01+ pin is refused.
            later = "ab" * 32
            _write_pin(
                f"{'data/' + 'narrative' + '_editions'}/2026-10-05-0937.json {later}\n"
            )
            with self.assertRaises(review_gate.GateError):
                review_gate.load_legacy_pins(root)
            self.assertIn("LEGACY_CUTOFF", gate_src)
            self.assertIn("_legacy_date_ok", gate_src)

            # N7: malformed manifest lines fail closed, they are not skipped.
            _write_pin("not-a-valid-pin-line\n")
            with self.assertRaises(review_gate.GateError):
                review_gate.load_legacy_pins(root)
            _write_pin("too many parts on this line\n")
            with self.assertRaises(review_gate.GateError):
                review_gate.load_legacy_pins(root)
            editions_rel = "data/" + "narrative" + "_editions"
            _write_pin(
                f"{editions_rel}/2026-09-29-1734.json {listed_sha}\n"
            )
            self.assertFalse(review_gate.pages_blocked(root=root))

    def test_extra_publish_surface_fails_closed(self):
        """S1–S6: yaml/toml/.JSON under data/ and extra Hugo config block."""
        preview = {
            "slug": "2026-10-03-1538",
            "edition": "2026 Week 4 · Preview",
            "phase": {"mode": "preview"},
        }
        live = "narrative"
        editions_name = live + "_editions"
        live_name = live + ".json"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            editions = root / "data" / editions_name
            editions.mkdir(parents=True)
            (root / "data" / live_name).write_text(
                json.dumps(preview) + "\n", encoding="utf-8"
            )
            self.assertFalse(review_gate.extra_publish_surface(root))
            self.assertFalse(review_gate.pages_blocked(root=root))

            yaml_home = root / "data" / (live + ".yaml")
            yaml_home.write_text("headline: HOMEYAML\n", encoding="utf-8")
            self.assertTrue(review_gate.extra_publish_path("data/narrative.yaml"))
            self.assertTrue(review_gate.extra_publish_surface(root))
            self.assertTrue(review_gate.pages_blocked(root=root))
            self.assertTrue(
                review_gate.automerge_blocked(
                    ["data/narrative.yaml"], root=root
                )
            )
            yaml_home.unlink()
            self.assertFalse(review_gate.pages_blocked(root=root))

            for suffix in (".yml", ".toml", ".JSON"):
                extra = editions / f"2026-10-05-0937{suffix}"
                extra.write_text("headline: COLLIDE\n", encoding="utf-8")
                self.assertTrue(review_gate.extra_publish_surface(root), suffix)
                self.assertTrue(review_gate.pages_blocked(root=root), suffix)
                extra.unlink()

            cased = root / "data" / "Narrative_Editions"
            cased.mkdir()
            (cased / "2026-10-05-0937.json").write_text(
                json.dumps(
                    {
                        "slug": "2026-10-05-0937",
                        "edition": "2026 Week 6 · Week 4 Review",
                        "phase": {"mode": "review"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            self.assertTrue(review_gate.pages_blocked(root=root))
            shutil.rmtree(cased)

            (root / "hugo.toml").write_text("baseURL = '/'\\n", encoding="utf-8")
            self.assertTrue(review_gate.extra_publish_path("hugo.toml"))
            self.assertTrue(review_gate.pages_blocked(root=root))
            (root / "hugo.toml").unlink()

            (root / "hugo.json").write_text("{}\n", encoding="utf-8")
            self.assertTrue(review_gate.pages_blocked(root=root))
            (root / "hugo.json").unlink()

            nested = root / "config" / "_default"
            nested.mkdir(parents=True)
            (nested / "hugo.yaml").write_text("baseURL: /\n", encoding="utf-8")
            self.assertTrue(review_gate.extra_publish_path("config/_default/hugo.yaml"))
            self.assertTrue(review_gate.pages_blocked(root=root))
            shutil.rmtree(root / "config")
            self.assertFalse(review_gate.pages_blocked(root=root))

    def test_hugo_build_allows_signed_review_outside_data(self):
        """B1: a .sig under signoff/review must not break `hugo`."""
        hugo_bin = _hugo_bin()
        if not hugo_bin:
            self.skipTest("hugo is not on PATH or ~/.local/hugo/hugo")
        review = {
            "slug": "2026-10-05-0937",
            "edition": "2026 Week 6 · Week 4 Review",
            "phase": {"mode": "review"},
            "headline": "Signed review",
            "dek": "Gate sign-off lives outside data/.",
            "generatedAt": "2026-10-05T09:37:00+00:00",
        }
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            priv, pub = _ed25519_keypair(tmp_path)
            root = tmp_path / "site"
            (root / "layouts").mkdir(parents=True)
            live = "narrative"
            editions_name = live + "_editions"
            live_name = live + ".json"
            (root / "data" / editions_name).mkdir(parents=True)
            (root / "hugo.yaml").write_text(
                'baseURL: "/"\npublishDir: "dist"\n',
                encoding="utf-8",
            )
            (root / "layouts" / "index.html").write_text(
                "{{ with .Site.Data.narrative }}{{ .headline }}{{ end }}\n",
                encoding="utf-8",
            )
            review_bytes = (json.dumps(review) + "\n").encode("utf-8")
            (root / "data" / live_name).write_bytes(review_bytes)
            (root / "data" / editions_name / "2026-10-05-0937.json").write_bytes(
                review_bytes
            )
            signoff = root / "signoff" / "review"
            _sign_review_edition(priv, "2026-10-05-0937", review_bytes, signoff)
            env = {"REVIEW_SIGNOFF_PUBKEY": str(pub)}
            with patch.dict(os.environ, env, clear=False):
                self.assertFalse(
                    review_gate.pages_blocked(root=root),
                )
            result = subprocess.run(
                [hugo_bin, "--gc", "--minify"],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn(
                "Signed review",
                (root / "dist" / "index.html").read_text(encoding="utf-8"),
            )
            leftover = root / "data" / "review_signoff"
            leftover.mkdir(parents=True)
            shutil.copy(signoff / "2026-10-05-0937.sig", leftover / "2026-10-05-0937.sig")
            broken = subprocess.run(
                [hugo_bin, "--gc", "--minify"],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(broken.returncode, 0, broken.stdout + broken.stderr)
            self.assertIn("unmarshal", (broken.stdout + broken.stderr).lower())

    def test_public_staticdir_review_html_fails_output_gate(self):
        """B2': Karen's public/ PR attack must fail a real hugo build."""
        hugo_bin = _hugo_bin()
        if not hugo_bin:
            self.skipTest("hugo is not on PATH or ~/.local/hugo/hugo")
        preview = {
            "slug": "2026-10-03-1538",
            "edition": "2026 Week 4 · Preview",
            "phase": {"mode": "preview"},
            "headline": "Preview headline",
            "dek": "Preview dek",
            "generatedAt": "2026-10-03T15:38:00+00:00",
        }
        attack_slug = "2026-10-05-0937"
        attack_html = (
            "<html><body><h1>Week 5 Review</h1>"
            "<p>PUBLICREVIEW 27-17 unsigned</p></body></html>\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "site"
            live = "narrative"
            editions_name = live + "_editions"
            live_name = live + ".json"
            (root / "data" / editions_name).mkdir(parents=True)
            (root / "layouts").mkdir(parents=True)
            (root / "public" / "narrative" / attack_slug).mkdir(parents=True)
            (root / "hugo.yaml").write_text(
                "baseURL: /\n"
                "publishDir: dist\n"
                "staticDir:\n"
                "  - public\n",
                encoding="utf-8",
            )
            (root / "layouts" / "index.html").write_text(
                "{{ with .Site.Data.narrative }}{{ .headline }}{{ end }}\n",
                encoding="utf-8",
            )
            (root / "data" / live_name).write_text(
                json.dumps(preview) + "\n", encoding="utf-8"
            )
            (root / "data" / editions_name / "2026-10-03-1538.json").write_text(
                json.dumps(preview) + "\n", encoding="utf-8"
            )
            attack_rel = f"public/narrative/{attack_slug}/index.html"
            (root / attack_rel).write_text(attack_html, encoding="utf-8")
            self.assertFalse(review_gate.automerge_allowlisted(attack_rel))
            self.assertTrue(review_gate.requires_human_merge([attack_rel]))
            self.assertTrue(
                review_gate.automerge_blocked([attack_rel], root=root)
            )
            # Source-only pages scan still misses staticDir copies — the
            # post-build census is the close. Keep that split load-bearing.
            self.assertFalse(review_gate.pages_blocked(root=root))
            result = subprocess.run(
                [hugo_bin, "--gc", "--minify"],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            built = (
                root / "dist" / "narrative" / attack_slug / "index.html"
            )
            self.assertTrue(built.is_file())
            self.assertIn("PUBLICREVIEW 27-17 unsigned", built.read_text(encoding="utf-8"))
            self.assertTrue(review_gate.output_blocked(root / "dist", root=root))
            self.assertTrue(
                review_gate.pages_blocked(root=root, dist=root / "dist")
            )
            self.assertEqual(
                review_gate.main(
                    [
                        "--pages",
                        "--root",
                        str(root),
                        "--dist",
                        "dist",
                    ]
                ),
                2,
            )
            overlay = root / "public" / "narrative" / "2026-10-03-1538"
            overlay.mkdir(parents=True)
            (overlay / "index.html").write_text(
                attack_html, encoding="utf-8"
            )
            overlay_build = subprocess.run(
                [hugo_bin, "--gc", "--minify"],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(
                overlay_build.returncode, 0, overlay_build.stdout + overlay_build.stderr
            )
            self.assertTrue(review_gate.output_blocked(root / "dist", root=root))
            (root / attack_rel).unlink()
            shutil.rmtree(overlay)
            shutil.rmtree(root / "dist", ignore_errors=True)
            clean = subprocess.run(
                [hugo_bin, "--gc", "--minify"],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(clean.returncode, 0, clean.stdout + clean.stderr)
            self.assertFalse(review_gate.output_blocked(root / "dist", root=root))
            self.assertEqual(
                review_gate.main(
                    ["--pages", "--root", str(root), "--dist", "dist"]
                ),
                0,
            )

    def test_output_gate_reads_hugo_config_not_hardcoded_dirs(self):
        """Custom staticDir/dataDir/mounts must drive the post-build census."""
        hugo_bin = _hugo_bin()
        if not hugo_bin:
            self.skipTest("hugo is not on PATH or ~/.local/hugo/hugo")
        preview = {
            "slug": "2026-10-03-1538",
            "edition": "2026 Week 4 · Preview",
            "phase": {"mode": "preview"},
            "headline": "Preview headline",
            "generatedAt": "2026-10-03T15:38:00+00:00",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "site"
            (root / "payloads" / "narrative_editions").mkdir(parents=True)
            (root / "views").mkdir(parents=True)
            (root / "pages").mkdir(parents=True)
            (root / "cdn" / "narrative" / "2026-10-03-1538").mkdir(parents=True)
            (root / "hugo.yaml").write_text(
                "baseURL: /\n"
                "publishDir: out\n"
                "staticDir:\n"
                "  - cdn\n"
                "contentDir: pages\n"
                "dataDir: payloads\n"
                "layoutDir: views\n",
                encoding="utf-8",
            )
            (root / "views" / "index.html").write_text(
                "{{ with .Site.Data.narrative }}{{ .headline }}{{ end }}\n",
                encoding="utf-8",
            )
            (root / "payloads" / "narrative.json").write_text(
                json.dumps(preview) + "\n", encoding="utf-8"
            )
            (
                root / "payloads" / "narrative_editions" / "2026-10-03-1538.json"
            ).write_text(json.dumps(preview) + "\n", encoding="utf-8")
            (root / "cdn" / "narrative" / "2026-10-03-1538" / "index.html").write_text(
                "<html><body>PUBLICREVIEW 27-17 unsigned</body></html>\n",
                encoding="utf-8",
            )
            cfg = review_gate.load_hugo_config(root)
            self.assertEqual(cfg["staticdir"], ["cdn"])
            self.assertEqual(cfg["contentdir"], "pages")
            self.assertEqual(cfg["datadir"], "payloads")
            self.assertEqual(cfg["layoutdir"], "views")
            self.assertEqual(cfg["publishdir"], "out")
            surface = review_gate.resolve_hugo_surface(root)
            self.assertIn("cdn", surface["static"])
            self.assertIn("pages", surface["content"])
            self.assertIn("payloads", surface["data"])
            self.assertIn("views", surface["layouts"])
            result = subprocess.run(
                [hugo_bin, "--gc", "--minify"],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue((root / "out" / "index.html").is_file())
            self.assertTrue(
                review_gate.output_blocked(root / "out", root=root)
            )
            shutil.rmtree(root / "cdn" / "narrative")
            shutil.rmtree(root / "out", ignore_errors=True)
            clean = subprocess.run(
                [hugo_bin, "--gc", "--minify"],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(clean.returncode, 0, clean.stdout + clean.stderr)
            self.assertFalse(
                review_gate.output_blocked(root / "out", root=root)
            )

    def test_hugo_config_fails_closed_when_hugo_missing(self):
        """MY4: missing hugo must raise, never fall back to hardcoded dirs."""
        gate_src = (
            Path(__file__).resolve().parents[2]
            / "tools"
            / "chiefs_narrative"
            / "review_gate.py"
        ).read_text(encoding="utf-8")
        self.assertIn("hugo is required to resolve", gate_src)
        self.assertNotRegex(
            gate_src, r"if not binary:\s*\n\s*return\s*\{"
        )
        self.assertNotRegex(
            gate_src, r'if not binary:\s*\n\s*cfg\s*='
        )
        self.assertNotIn('"staticdir": ["public"]', gate_src)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "dist").mkdir()
            (root / "dist" / "index.html").write_text("x\n", encoding="utf-8")
            with patch.object(review_gate, "_hugo_bin", return_value=None):
                with self.assertRaises(review_gate.GateError) as ctx:
                    review_gate.load_hugo_config(root)
                self.assertIn("hugo is required", str(ctx.exception))
                with self.assertRaises(review_gate.GateError):
                    review_gate.resolve_hugo_surface(root)
                with self.assertRaises(review_gate.GateError):
                    review_gate.output_blocked(root / "dist", root=root)
            with patch.object(review_gate, "_hugo_bin", return_value="hugo"):
                with patch.object(
                    subprocess, "check_output", return_value="not-json"
                ):
                    with self.assertRaises(review_gate.GateError) as ctx:
                        review_gate.load_hugo_config(root)
                    self.assertIn("not JSON", str(ctx.exception))

    def test_write_token_jobs_refuse_unittest_and_pip(self):
        """Write-token jobs must not run unittest or pip install -r."""
        errors = check_workflows.audit_write_jobs()
        self.assertEqual(errors, [])
        self.assertEqual(check_workflows.main(), 0)
        root = Path(__file__).resolve().parents[2] / ".github" / "workflows"
        narrative = yaml.safe_load((root / "narrative.yml").read_text(encoding="utf-8"))
        self.assertEqual(narrative.get("permissions"), {"contents": "read"})
        jobs = narrative.get("jobs") or {}
        self.assertEqual((jobs.get("generate") or {}).get("permissions"), {"contents": "read"})
        self.assertEqual((jobs.get("test") or {}).get("permissions"), {"contents": "read"})
        self.assertEqual(
            (jobs.get("update") or {}).get("permissions"),
            {
                "contents": "write",
                "pull-requests": "write",
                "actions": "write",
            },
        )
        self.assertEqual((jobs.get("deploy") or {}).get("permissions"), {"contents": "write"})
        for job_name in ("generate", "test"):
            blob = "\n".join(
                str(step.get("run") or "")
                for step in (jobs.get(job_name) or {}).get("steps") or []
                if isinstance(step, dict)
            )
            self.assertIn("pip install -r", blob, job_name)
        for job_name in ("update", "deploy"):
            blob = "\n".join(
                str(step.get("run") or "")
                for step in (jobs.get(job_name) or {}).get("steps") or []
                if isinstance(step, dict)
            )
            self.assertNotIn("unittest", blob, job_name)
            self.assertNotIn("pip install -r", blob, job_name)
            self.assertIn("GATE_BASE", blob, job_name)
            self.assertIn("python -P -m tools.chiefs_narrative.review_gate", blob, job_name)
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp)
            wf = scratch / ".github" / "workflows"
            wf.mkdir(parents=True)
            (wf / "evil.yml").write_text(
                "name: Evil\npermissions:\n  contents: write\njobs:\n"
                "  evil:\n    runs-on: ubuntu-latest\n    steps:\n"
                "      - uses: actions/checkout@v6\n"
                "      - run: python -m unittest discover -s tools/tests -v\n"
                "      - run: python -m pip install -r tools/requirements.txt\n",
                encoding="utf-8",
            )
            found = check_workflows.audit_write_jobs(scratch)
            self.assertTrue(any("unittest" in item for item in found), found)
            self.assertTrue(any("pip" in item for item in found), found)
            self.assertTrue(
                any("persist-credentials" in item for item in found), found
            )

    def test_public_staticdir_census_misses_are_must_fail(self):
        """Karen's leftover dist surfaces must fail a real hugo build."""
        hugo_bin = _hugo_bin()
        if not hugo_bin:
            self.skipTest("hugo is not on PATH or ~/.local/hugo/hugo")
        preview = {
            "slug": "2026-10-03-1538",
            "edition": "2026 Week 4 · Preview",
            "phase": {"mode": "preview"},
            "headline": "Preview headline",
            "dek": "Preview dek",
            "generatedAt": "2026-10-03T15:38:00+00:00",
        }
        attack_html = (
            "<html><body><h1>Week 5 Review</h1>"
            "<p>PUBLICREVIEW 27-17 unsigned</p></body></html>\n"
        )
        attack_xml = (
            '<?xml version="1.0"?><rss><channel><title>Week 5 Review'
            "</title><item>PUBLICREVIEW 27-17 unsigned</item></channel></rss>\n"
        )
        rows = (
            ("public/narrative/index.html", attack_html),
            ("public/index.xml", attack_xml),
            ("public/narrative/index.xml", attack_xml),
            ("public/sitemap.xml", attack_xml),
            ("public/narrative/2026-10-03-1538/amp.html", attack_html),
            ("public/narrative/2026-10-05-0937.html", attack_html),
            ("public/intel/review.html", attack_html),
            ("public/404.html", attack_html),
            ("public/notes.md", attack_html),
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "site"
            live = "narrative"
            editions_name = live + "_editions"
            live_name = live + ".json"
            (root / "data" / editions_name).mkdir(parents=True)
            (root / "layouts").mkdir(parents=True)
            (root / "hugo.yaml").write_text(
                "baseURL: /\n"
                "publishDir: dist\n"
                "staticDir:\n"
                "  - public\n",
                encoding="utf-8",
            )
            (root / "layouts" / "index.html").write_text(
                "{{ with .Site.Data.narrative }}{{ .headline }}{{ end }}\n",
                encoding="utf-8",
            )
            (root / "data" / live_name).write_text(
                json.dumps(preview) + "\n", encoding="utf-8"
            )
            (root / "data" / editions_name / "2026-10-03-1538.json").write_text(
                json.dumps(preview) + "\n", encoding="utf-8"
            )
            for rel, body in rows:
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(body, encoding="utf-8")
                self.assertTrue(
                    review_gate.requires_human_merge([rel]), rel
                )
                self.assertTrue(
                    review_gate.automerge_blocked([rel], root=root), rel
                )
                result = subprocess.run(
                    [hugo_bin, "--gc", "--minify"],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(
                    result.returncode, 0, rel + result.stdout + result.stderr
                )
                self.assertTrue(
                    review_gate.output_blocked(root / "dist", root=root),
                    rel,
                )
                path.unlink()
                shutil.rmtree(root / "dist", ignore_errors=True)
            clean = subprocess.run(
                [hugo_bin, "--gc", "--minify"],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(clean.returncode, 0, clean.stdout + clean.stderr)
            self.assertFalse(review_gate.output_blocked(root / "dist", root=root))

    def _week3_slate(self):
        return [
            {
                "id": "401872945",
                "week": 2,
                "opponent": "Indianapolis Colts",
                "opponentAbbr": "IND",
                "completed": True,
                "kcScore": 33,
                "oppScore": 30,
            },
            {
                "id": "401872952",
                "week": 3,
                "opponent": "Miami Dolphins",
                "opponentAbbr": "MIA",
                "completed": True,
                "kcScore": 24,
                "oppScore": 10,
            },
        ]

    def _prod_slate(self):
        return collect.load_cached_schedule()

    def test_zone_blitz_int_needs_word_boundary(self):
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        interior = self._review(
            lede=(
                "Spagnuolo can zone blitz to stress the interior and still "
                "keep a linebacker on Bowers."
            )
        )
        into = self._review(
            lede=(
                "Cover-2 and the zone blitz are how Kansas City keeps Bowers "
                "from turning Allegiant into another long afternoon."
            )
        )
        self.assertEqual(facts.check_review(interior, last, recap), [])
        self.assertEqual(facts.check_review(into, last, recap), [])
        real = self._review(
            lede=(
                "Zone blitz Jones/Karlaftis with a dropping end on "
                "second-and-long — the look that helped produce the Willis "
                "interception."
            )
        )
        issues = facts.check_review(real, last, recap)
        self.assertTrue(
            any("zone blitz producing the interception" in item for item in issues),
            issues,
        )

    def test_one_zone_blitz_hit_does_not_strip_every_call(self):
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        keep = (
            "Cover-2 and the zone blitz are on the call sheet because Las "
            "Vegas will keep the football if Kansas City lets another offense "
            "live on third-and-medium."
        )
        drop = (
            "Zone blitz Jones/Karlaftis with a dropping end on second-and-long "
            "— the look that helped produce the Willis interception."
        )
        narrative = {
            "headline": "Keep this title",
            "lastGameReview": {
                "lede": "Kansas City finished 24–10 against Miami.",
                "analysis": [keep, drop],
            },
            "strategies": [
                "Cover-2 on third-and-long to take away Bowers; zone blitz "
                "on second-and-long to steal the down."
            ],
        }
        issues = facts.check_review(narrative, last, recap)
        snippets = facts.violation_snippets(issues)
        self.assertFalse(
            any(s.lower() == "zone blitz" for s in snippets), snippets
        )
        repaired = facts.repair_offending_copy(narrative, issues, last)
        blob = facts.edition_text(repaired)
        self.assertNotIn("produce the Willis interception", blob)
        self.assertIn("zone blitz are on the call sheet", blob)
        self.assertIn("steal the down", blob)

    def test_qb_hits_bind_to_the_team_that_recorded_them(self):
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        both = self._review(
            lede=(
                "He was not running for his life — zero sacks — but Miami "
                "did record 5 QB hits, and Kansas City recorded 1."
            )
        )
        self.assertEqual(facts.check_review(both, last, recap), [])
        kc_only = self._review(lede="The Chiefs recorded 5 QB hits.")
        issues = facts.check_review(kc_only, last, recap)
        self.assertTrue(
            any("QB hits 5" in item and "1" in item for item in issues), issues
        )

    def test_first_downs_bind_to_the_team_that_owns_them(self):
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        last["id"] = "401872952"
        slate = self._week3_slate()
        tape = self._review(
            lede=(
                "The Indianapolis tape is a different movie and has to stay "
                "in its own box: 523 yards, 29 first downs, 371 net passing, "
                "152 rushing, 37:00 of possession in a 33-30 overtime win."
            )
        )
        self.assertEqual(
            facts.check_review(tape, last, recap, schedule=slate), []
        )
        chiefs = self._review(
            lede=(
                "Against Indianapolis the Chiefs posted 29 first downs "
                "and 37:00 of possession."
            )
        )
        self.assertEqual(
            facts.check_review(chiefs, last, recap, schedule=slate), []
        )
        flipped = self._review(
            lede="Indianapolis was 523 yards, 29 first downs, 37:00 of possession."
        )
        issues = facts.check_review(flipped, last, recap, schedule=slate)
        self.assertTrue(
            any("first downs 29" in item and "IND" in item for item in issues)
            or any("523" in item for item in issues),
            issues,
        )

    def test_possession_binding_works_both_ways(self):
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        last["id"] = "401872952"
        slate = self._week3_slate()
        miami_had = self._review(lede="Miami had 25:39 of possession.")
        miami_issues = facts.check_review(miami_had, last, recap, schedule=slate)
        self.assertTrue(
            any("25:39" in item and "KC" in item for item in miami_issues),
            miami_issues,
        )
        dolphins = self._review(lede="The Dolphins held the ball for 25:39.")
        dolph_issues = facts.check_review(dolphins, last, recap, schedule=slate)
        self.assertTrue(
            any("25:39" in item for item in dolph_issues), dolph_issues
        )
        kc_prior = self._review(
            lede="Kansas City held the ball for 33:00 against Indianapolis."
        )
        prior_issues = facts.check_review(kc_prior, last, recap, schedule=slate)
        self.assertTrue(
            any("33:00" in item and "not KC" in item for item in prior_issues),
            prior_issues,
        )
        list_form = self._review(lede="Miami was 334, 18, 25:39.")
        list_issues = facts.check_review(list_form, last, recap, schedule=slate)
        self.assertTrue(
            any("25:39" in item and "KC" in item for item in list_issues),
            list_issues,
        )
        squeezed = self._review(
            lede=(
                "Miami squeezed that same offense to 88 on the ground and "
                "25:39 with the football."
            )
        )
        self.assertEqual(
            facts.check_review(squeezed, last, recap, schedule=slate), []
        )

    def test_run_37042175518_possession_binding_and_salvage(self):
        """Run 37042175518: 34:21 bound to KC on correct Miami copy, then
        salvage dropped ~25 good sentences and left heading fragments.
        """
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        last["id"] = "401872952"
        slate = self._week3_slate()
        accept = [
            "Miami held the ball 34:21.",
            "Possession is 25:39 for Kansas City, 34:21 for Miami.",
            "The Chiefs still lost the possession fight, 25:39 to Miami's 34:21.",
            "Miami held 19 first downs and 34:21",
            "The Chiefs answered with 18 first downs, 3-of-7 on third down, "
            "88 rushing yards, and 25:39.",
            "The Dolphins held 19 first downs, converted 8-of-16 on third "
            "down, ran it 31 times for 119 yards, and sat on the football "
            "for 34:21.",
            "That is how you post 246 net passing yards on 20-of-24.",
            "Walker’s own 70 rushing yards were the feature.",
            "No invented window.",
            "Look ahead only to Sun Oct 4, 3:25 PM CT.",
            "That split is the film Las Vegas will put on the projector.",
        ]
        more_clocks = [
            "The tape says Miami just held the ball 34:21 and posted 19 first downs.",
            "Walker and Benson got the month; the Miami tape gave the opponent "
            "19 first downs and 34:21.",
        ]
        reject = [
            "Kansas City finished with 18 team rushing yards.",
            "Mahomes was not a 40 dropbacks scramble.",
            "There were four sacks.",
            "Kansas City had 70 rushing yards.",
            (
                "Walk through the Hard Rock tape: Walker’s 10-yard score "
                "2:06 in with J. Moore eligible, Kelce’s 48-yarder, "
                "Rice’s 7-for-88, the stuff at the 12 with Nourzad eligible, "
                "the Rodriguez interception, Sneed’s forced-and-recovered "
                "fumble, Karlaftis’ pick, Bolton’s 11 tackles, 0 sacks, "
                "1 Chiefs QB hit, 5 hits on Mahomes"
            ),
        ]
        for sentence in accept + more_clocks:
            issues = facts.check_review(
                self._review(lede=sentence), last, recap, schedule=slate
            )
            self.assertEqual(issues, [], f"should accept {sentence!r}: {issues}")
        reject_needles = (
            ("18 team rushing", "18"),
            ("pass attempts", "40"),
            ("sacks 4", "four sacks"),
            ("team rushing 70", "70 rushing yards"),
            ("eligible", "Nourzad"),
        )
        for sentence, (needle, _token) in zip(reject, reject_needles):
            issues = facts.check_review(
                self._review(lede=sentence), last, recap, schedule=slate
            )
            self.assertTrue(
                any(needle in item for item in issues),
                f"should reject {sentence!r}: {issues}",
            )

        narrative = {
            "headline": "Keep this title",
            "lastGameReview": {
                "lede": (
                    "Kansas City finished 24–10 against Miami. "
                    + " ".join(accept[:4])
                ),
                "analysis": [
                    {
                        "title": "3-0",
                        "body": (
                            accept[4] + " "
                            + reject[0] + " "
                            + accept[5] + " "
                            + accept[6]
                        ),
                    },
                    {
                        "title": "Hard Rock film",
                        "body": reject[4].rstrip(".") + ".",
                    },
                ],
                "takeaways": [
                    {
                        "title": "Hard Rock film",
                        "body": reject[3],
                    },
                ],
                "whatWorked": [accept[7]],
                "whatDidnt": [accept[8]],
            },
            "currentState": {
                "lede": accept[9],
            },
            "storyline": {
                "body": [accept[10] + " " + reject[1]],
            },
            "runOfShow": [
                {
                    "segment": "Where 3-0 actually stands",
                    "talkTrack": reject[2],
                },
                {
                    "segment": "Look-ahead",
                    "talkTrack": accept[9],
                },
            ],
        }
        issues = facts.check_review(narrative, last, recap, schedule=slate)
        self.assertTrue(
            any("18 team rushing" in item for item in issues), issues
        )
        self.assertTrue(
            any("34:21" in item and "not KC" in item for item in issues) is False,
            issues,
        )
        repaired = facts.repair_offending_copy(narrative, issues, last, recap)
        blob = facts.edition_text(repaired)
        for sentence in accept:
            self.assertIn(
                sentence.rstrip("."),
                blob,
                f"salvage dropped good copy {sentence!r}",
            )
        self.assertNotIn("18 team rushing yards", blob)
        self.assertNotIn("40 dropbacks", blob)
        self.assertNotIn("four sacks", blob)
        self.assertNotIn("Kansas City had 70 rushing yards", blob)
        self.assertNotIn("Nourzad eligible", blob)
        headings = " ".join(
            [
                str((card or {}).get("title") or "")
                for card in (repaired.get("lastGameReview") or {}).get("analysis")
                or []
            ]
            + [
                str((card or {}).get("title") or "")
                for card in (repaired.get("lastGameReview") or {}).get("takeaways")
                or []
            ]
            + [
                str((card or {}).get("segment") or "")
                for card in repaired.get("runOfShow") or []
            ]
        )
        # Emptied heading-only cards are gone. Cards that still have
        # correct body keep their title.
        self.assertNotIn("Where 3-0 actually stands", blob)
        leftover = facts.check_review(repaired, last, recap, schedule=slate)
        self.assertEqual(leftover, [], leftover)
        dropped = facts.dropped_sentences(narrative, repaired)
        orphans = facts.check_repair_orphans(
            repaired, dropped, before=narrative
        )
        self.assertFalse(
            any("3-0" in item or "Hard Rock film" in item for item in orphans),
            orphans,
        )
        self.assertEqual(
            facts.repair_publish_blockers(leftover, repaired, orphans, before=narrative),
            [],
        )
        # Titles that still have a body (3-0 / Hard Rock film over
        # correct remaining sentences) may stay. The emptied run-of-show
        # heading must not.
        self.assertNotIn("Where 3-0 actually stands", headings)

    def test_run_37099023312_penalty_list_and_two_touches(self):
        """Run 37099023312: list binding, Walker-only FP, correct-in-place.

        Tonight's two gate flags were false positives. Walker
        game-total 'two touches' is held, not rewritten. A Sneed
        illegal-use stays on the penalty list; leftover that cannot
        be swapped is dropped.
        """
        catalog = _load_fixture("edition_run_37099023312.json")
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        last["id"] = "401872952"
        for sentence in catalog["accept"]:
            issues = facts.check_review(self._review(lede=sentence), last, recap)
            self.assertEqual(issues, [], f"should accept {sentence!r}: {issues}")
        reject_needles = (
            ("touches 2", "Walker"),
            ("illegal-use", "sneed"),
            ("team rushing 18", "18"),
            ("pass attempts", "40"),
            ("sacks 4", "four sacks"),
            ("team rushing 70", "70 rushing yards"),
            ("eligible", "Nourzad"),
            ("possession", "34:21"),
        )
        for sentence, (needle, token) in zip(catalog["reject"], reject_needles):
            issues = facts.check_review(self._review(lede=sentence), last, recap)
            self.assertTrue(
                any(needle in item and token in item for item in issues),
                f"should reject {sentence!r}: {issues}",
            )

        list_sentence = catalog["accept"][0]
        logged_list = (
            "Miami wiped its own plays with holding, an ineligible downfield, "
            "and illegal contact; Kansas City took illegal use of hands on "
            "N. Williams, defensive holding on L. Sneed, defensive offside "
            "on R. Thomas, offensive holding on Rice, illegal use of hands "
            "on Tr"
        )
        self.assertEqual(
            facts.check_review(self._review(lede=logged_list), last, recap),
            [],
        )
        two_next_to_walker = catalog["accept"][1]
        self.assertEqual(facts._check_touch_counts(two_next_to_walker, recap), [])
        walker_two = catalog["reject"][0]
        walker_hits = facts._check_touch_counts(walker_two, recap)
        self.assertTrue(
            any("touches 2" in item and "Walker" in item for item in walker_hits),
            walker_hits,
        )

        narrative = {
            "headline": "Keep this title",
            "lastGameReview": {
                "opponent": "Miami Dolphins",
                "result": "W",
                "score": "KC 24–10",
                "lede": (
                    "Kansas City finished 24–10 against Miami. "
                    + list_sentence
                    + " "
                    + walker_two
                    + " "
                    + two_next_to_walker
                ),
            },
        }
        gate_last = generate._last_game_from_edition(narrative)
        gate_recap = generate._recap_for_edition(narrative)
        self.assertTrue(gate_recap.get("touches") or gate_recap.get("penalties"))
        gate_issues = facts.check_review(narrative, gate_last, gate_recap)
        self.assertTrue(
            any("touches 2" in item and "Walker" in item for item in gate_issues),
            gate_issues,
        )
        self.assertFalse(
            any("illegal-use" in item for item in gate_issues),
            gate_issues,
        )
        corrected, logs = facts.apply_fact_corrections(narrative, gate_issues)
        self.assertFalse(any("twenty touches" in line for line in logs), logs)
        blob = facts.edition_text(corrected)
        self.assertIn("Kenneth Walker III handled two touches", blob)
        self.assertNotIn("Kenneth Walker III handled twenty touches", blob)
        self.assertIn("defensive holding on L. Sneed", blob)
        self.assertIn("Kelce at two touches", blob)
        leftover = facts.check_review(corrected, gate_last, gate_recap)
        self.assertTrue(
            any("touches 2" in item and "Walker" in item for item in leftover),
            leftover,
        )

        sneed_flag = "Sneed drew the illegal-use flag at Q4 0:47."
        sneed_nar = self._review(lede=sneed_flag)
        sneed_issues = facts.check_review(sneed_nar, last, recap)
        self.assertTrue(any("illegal-use" in item for item in sneed_issues), sneed_issues)
        sneed_fixed, sneed_logs = facts.apply_fact_corrections(
            sneed_nar, sneed_issues
        )
        self.assertTrue(any("Karlaftis drew" in line for line in sneed_logs), sneed_logs)
        self.assertEqual(facts.check_review(sneed_fixed, last, recap), [])

        unfixable = catalog["reject"][1]
        messy = self._review(lede=unfixable)
        messy_issues = facts.check_review(messy, last, recap)
        messy_fixed, _logs = facts.apply_fact_corrections(messy, messy_issues)
        messy_left = facts.check_review(messy_fixed, last, recap)
        self.assertTrue(messy_left, messy_left)
        cleaned = facts.repair_offending_copy(
            messy_fixed, messy_left, last, recap
        )
        self.assertEqual(facts.check_review(cleaned, last, recap), [])
        self.assertTrue(facts.should_hold_automerge([], cleaned, leftover=messy_left))

    def test_run_37129473980_keeps_correct_copy_and_ind_rush(self):
        """Run 37129473980: no mass-drop, no 152→119, no 40-drop vacuum swap."""
        catalog = _load_fixture("edition_run_37129473980.json")
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        last["id"] = "401872952"
        slate = self._week3_slate()
        keep = catalog["keep"]
        rush = catalog["rushing_sentence"]
        vacuum = catalog["vacuum_sentence"]
        poison = "Mahomes sat through 8:42 of possession on the opening series."
        narrative = {
            "phase": {"type": "regular", "label": "Week 4", "mode": "preview"},
            "record": "3-0",
            "headline": catalog["headline"],
            "dek": keep[1],
            "theEdge": keep[2],
            "lastGameReview": {
                "opponent": "Miami Dolphins",
                "result": "W",
                "score": "KC 24–10",
                "lede": rush + " " + poison,
                "analysis": [{"title": keep[13], "body": sent} for sent in keep[3:13]],
            },
            "currentState": {"lede": keep[14], "body": keep[15:20]},
            "gamePlan": {
                "script": keep[20:24],
                "matchups": [{"unit": "QB", "note": keep[24]}],
            },
            "nextGame": {"lede": keep[25], "body": keep[26:]},
            "storyline": {"lede": vacuum, "body": []},
        }
        for sentence in keep + [rush, vacuum]:
            issues = facts.check_review(
                {"phase": {"type": "regular"}, "lastGameReview": {
                    "opponent": "Miami Dolphins",
                    "result": "W",
                    "score": "KC 24–10",
                    "lede": sentence,
                }},
                last,
                recap,
                schedule=slate,
            )
            self.assertEqual(issues, [], f"should accept {sentence!r}: {issues}")

        clock_note = (
            "possession 8:42 is not on the ESPN box "
            "('8:42 of possession'; official ['25:39', '33:00', '34:21', '37:00'])"
        )
        snippets = facts.violation_snippets([clock_note])
        self.assertTrue(any("8:42" in item for item in snippets), snippets)
        self.assertFalse(any(item == "25:39" for item in snippets), snippets)
        self.assertFalse(any(item == "34:21" for item in snippets), snippets)

        fake_152 = [
            "game yards 152 disagrees with ESPN 523 for prior KC ('152 yards')"
        ]
        rush_fixed, rush_logs = facts.apply_fact_corrections(
            {"lastGameReview": {"lede": rush}}, fake_152, recap
        )
        self.assertEqual(rush_logs, [])
        self.assertIn("152 rushing yards", facts.edition_text(rush_fixed))
        self.assertNotIn("119 rushing yards", facts.edition_text(rush_fixed))

        team_rush_wrong = [
            "team rushing 152 disagrees with ESPN 119 for MIA "
            "('152 rushing yards')"
        ]
        rush_fixed, rush_logs = facts.apply_fact_corrections(
            {"lastGameReview": {"lede": rush}}, team_rush_wrong, recap
        )
        self.assertEqual(rush_logs, [])
        self.assertIn("152 rushing yards", facts.edition_text(rush_fixed))

        vac_issues = [
            "pass attempts 40 disagrees with ESPN 24 for Patrick Mahomes "
            "('40-drop')"
        ]
        vac_fixed, vac_logs = facts.apply_fact_corrections(
            {"lastGameReview": {"lede": vacuum}}, vac_issues, recap
        )
        self.assertEqual(vac_logs, [])
        self.assertIn("40-drop vacuum", facts.edition_text(vac_fixed))

        issues = facts.check_review(
            narrative, last, recap, schedule=slate, copy_gates=True
        )
        self.assertTrue(any("8:42" in item for item in issues), issues)
        repaired = facts.repair_offending_copy(
            narrative, issues, last, recap
        )
        blob = facts.edition_text(repaired)
        self.assertEqual(repaired.get("headline"), catalog["headline"])
        self.assertEqual(repaired.get("dek"), keep[1])
        self.assertEqual(repaired.get("theEdge"), keep[2])
        self.assertEqual(
            ((repaired.get("gamePlan") or {}).get("matchups") or [{}])[0].get("unit"),
            "QB",
        )
        for sentence in keep:
            self.assertIn(sentence, blob, sentence)
        self.assertIn("152 rushing yards", blob)
        self.assertIn("40-drop vacuum", blob)
        self.assertNotIn("8:42", blob)
        leftover = facts.check_review(repaired, last, recap, schedule=slate)
        self.assertEqual(leftover, [], leftover)

    def test_run_37133101941_targets_flagged_sentence_and_rewords_box(self):
        """Run 37133101941: do not retarget 29/18, reword opponent-was-box."""
        catalog = _load_fixture("edition_run_37133101941.json")
        recap = _load_fixture("espn_401872952_recap.json")
        recap["prior"]["kc"]["netPassingYards"] = "371"
        recap["prior"]["kc"]["totalYards"] = "523"
        recap["prior"]["opp"]["firstDowns"] = "24"
        recap["prior"]["opp"]["rushingYards"] = "119"
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        last["id"] = "401872952"
        keep_colts, keep_miami = catalog["keep"]
        was_ind, was_mia = catalog["reword"]
        narrative = {
            "phase": {"type": "regular"},
            "headline": "Week 4",
            "dek": "Preview",
            "theEdge": "Allegiant",
            "storyline": {"body": [keep_miami]},
            "lastGameReview": {
                "opponent": "Miami Dolphins",
                "result": "W",
                "score": "KC 24–10",
                "lede": f"{keep_colts} {was_ind} {was_mia}",
            },
        }
        issues = facts.check_review(narrative, last, recap)
        self.assertTrue(any("29" in item and "IND" in item for item in issues), issues)
        self.assertTrue(any("18" in item and "MIA" in item for item in issues), issues)
        self.assertFalse(any("371" in item and "246" in item for item in issues), issues)

        flagged_ind = facts._flagged_sentence(facts.edition_text(narrative), issues[0], last, recap)
        self.assertEqual(flagged_ind, was_ind)
        self.assertNotEqual(flagged_ind, keep_colts)

        fixed, logs = facts.apply_fact_corrections(
            narrative, issues, recap=recap, last_game=last
        )
        blob = facts.edition_text(fixed)
        self.assertIn(keep_colts, blob)
        self.assertIn("posted 29 first downs", blob)
        self.assertNotIn("posted 24 first downs", blob)
        self.assertIn(keep_miami, blob)
        self.assertIn("18 first downs, 334", blob)
        self.assertNotIn("19 first downs, 334", blob)
        self.assertIn("Against Indianapolis, Kansas City had 29 first downs", blob)
        self.assertIn("Against Miami, Kansas City had 18 first downs", blob)
        self.assertNotIn(was_ind, blob)
        self.assertNotIn(was_mia, blob)
        self.assertEqual(len(logs), 2, logs)
        self.assertEqual(facts.check_review(fixed, last, recap), [])
        self.assertTrue(facts.should_hold_automerge([], fixed, corrections=logs))

        poison = [
            "first downs 29 disagrees with ESPN 24 for prior IND "
            "('29 first downs')"
        ]
        untouched, poison_logs = facts.apply_fact_corrections(
            {
                "lastGameReview": {
                    "lede": keep_colts + " " + was_ind,
                }
            },
            poison,
            recap=recap,
            last_game=last,
        )
        poison_blob = facts.edition_text(untouched)
        self.assertIn(keep_colts, poison_blob)
        self.assertNotIn("posted 24 first downs", poison_blob)
        self.assertTrue(
            any("Against Indianapolis" in line for line in poison_logs)
            or keep_colts in poison_blob
        )

    def test_karen_155_tie_mixed_box_and_kc_owned_box(self):
        """#155 gaps: skip ties, require a full KC box, bind KC's box to KC."""
        catalog = _load_fixture("edition_run_karen_155_gaps.json")
        recap = _load_fixture("espn_401872952_recap.json")
        recap["prior"]["opp"]["firstDowns"] = "24"
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        last["id"] = "401872952"
        slate = self._prod_slate()

        def _lede(sentence):
            return {
                "phase": {"type": "regular"},
                "lastGameReview": {
                    "opponent": "Miami Dolphins",
                    "result": "W",
                    "score": "KC 24–10",
                    "lede": sentence,
                },
            }

        keep_a, keep_b = catalog["tie_keep"]
        for sentence in (keep_a, keep_b):
            self.assertEqual(
                facts.check_review(_lede(sentence), last, recap, schedule=slate),
                [],
                sentence,
            )
        tied = {
            "lastGameReview": {"lede": f"{keep_a} {keep_b}"},
        }
        fake = [
            "first downs 29 disagrees with ESPN 24 for prior IND "
            "('29 first downs')"
        ]
        tied_fixed, tied_logs = facts.apply_fact_corrections(
            tied, fake, recap=recap, last_game=last, schedule=slate
        )
        tied_blob = facts.edition_text(tied_fixed)
        self.assertIn(keep_a, tied_blob)
        self.assertIn(keep_b, tied_blob)
        self.assertNotIn("posted 24 first downs", tied_blob)
        self.assertTrue(any("ambiguous snippet" in line for line in tied_logs), tied_logs)
        self.assertTrue(facts.should_hold_automerge([], tied_fixed, corrections=tied_logs))

        mixed = catalog["mixed_opp_box"]
        self.assertIsNone(
            facts._reword_misattributed_box(mixed, recap, last, slate)
        )
        mixed_issues = facts.check_review(_lede(mixed), last, recap, schedule=slate)
        mixed_fixed, mixed_logs = facts.apply_fact_corrections(
            _lede(mixed), mixed_issues, recap=recap, last_game=last, schedule=slate
        )
        self.assertIn(mixed, facts.edition_text(mixed_fixed))
        self.assertFalse(
            any("Against Miami, Kansas City had 18 first downs, 119" in line for line in mixed_logs),
            mixed_logs,
        )

        wrong = catalog["kc_box_wrong"]
        right = catalog["kc_box_right"]
        plain = catalog["kc_box_plain"]
        wrong_issues = facts.check_review(_lede(wrong), last, recap, schedule=slate)
        self.assertTrue(any("19" in item and "18" in item for item in wrong_issues), wrong_issues)
        self.assertEqual(facts.check_review(_lede(right), last, recap, schedule=slate), [])
        plain_issues = facts.check_review(_lede(plain), last, recap, schedule=slate)
        self.assertTrue(any("19" in item and "18" in item for item in plain_issues), plain_issues)
        self.assertFalse(
            facts._is_multi_stat_box(
                "The rush will decide it after the 3:05 kickoff window."
            )
        )
        self.assertFalse(
            facts._is_multi_stat_box(
                "Twenty of 24 for 246 is a control tape, not a 40-dropback scramble."
            )
        )

    def test_karen_157_score_label_subjects_and_published_edition(self):
        """#157 r3: lost 24-10, Miami had, let-the-box, slate aliases, clocks."""
        catalog = _load_fixture("edition_run_karen_157_gaps.json")
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        last["id"] = "401872952"
        last["opponent"] = "Miami Dolphins"
        last["opponentAbbr"] = "MIA"
        last["opponentShort"] = "Dolphins"
        slate = self._prod_slate()

        def _lede(sentence):
            return {
                "phase": {"type": "regular"},
                "lastGameReview": {
                    "opponent": "Miami Dolphins",
                    "result": "W",
                    "score": "KC 24–10",
                    "lede": sentence,
                },
            }

        held = catalog["held_clock"]
        self.assertEqual(
            facts.check_review(_lede(held), last, recap, schedule=slate), []
        )
        self.assertEqual(
            facts.check_review(
                _lede(held + " " + catalog["orphan_after_held"]),
                last,
                recap,
                schedule=slate,
            ),
            [],
        )
        self.assertEqual(
            facts.check_review(_lede(catalog["miami_had"]), last, recap, schedule=slate),
            [],
        )
        had_fixed, had_logs = facts.apply_fact_corrections(
            _lede(catalog["miami_had"]),
            [
                "first downs 19 disagrees with ESPN 18 for KC "
                "('Miami had 19 first downs')"
            ],
            recap=recap,
            last_game=last,
            schedule=slate,
        )
        self.assertIn(catalog["miami_had"], facts.edition_text(had_fixed))
        self.assertFalse(any("Miami had 18" in line for line in had_logs), had_logs)
        self.assertEqual(
            facts.check_review(
                _lede(catalog["let_miami_box"]), last, recap, schedule=slate
            ),
            [],
        )
        self.assertTrue(
            facts.check_review(
                _lede(catalog["kc_box_wrong"]), last, recap, schedule=slate
            )
        )
        self.assertEqual(
            facts.check_review(
                _lede(catalog["walker_again"]), last, recap, schedule=slate
            ),
            [],
        )
        self.assertEqual(facts._claimed_box_stats(catalog["clock_not_rush"]), [])
        self.assertFalse(facts._is_multi_stat_box(catalog["clock_not_rush"]))

        swap_issues = facts.check_review(
            _lede(catalog["single_number_swap"]), last, recap, schedule=slate
        )
        self.assertTrue(swap_issues, swap_issues)
        swap_fixed, swap_logs = facts.apply_fact_corrections(
            _lede(catalog["single_number_swap"]),
            swap_issues,
            recap=recap,
            last_game=last,
            schedule=slate,
        )
        self.assertIn(catalog["single_number_ok"], facts.edition_text(swap_fixed))
        self.assertTrue(swap_logs, swap_logs)
        self.assertEqual(
            facts.check_review(
                _lede(catalog["single_number_ok"]), last, recap, schedule=slate
            ),
            [],
        )

        sea = catalog["seattle_was"]
        sea_issues = facts.check_review(_lede(sea), last, recap, schedule=slate)
        self.assertTrue(any("Seattle" in item for item in sea_issues), sea_issues)
        sea_fixed, sea_logs = facts.apply_fact_corrections(
            _lede(sea), sea_issues, recap=recap, last_game=last, schedule=slate
        )
        self.assertIn(sea, facts.edition_text(sea_fixed))
        self.assertTrue(
            any("unverifiable opponent Seattle" in line for line in sea_logs),
            sea_logs,
        )
        self.assertTrue(facts.should_hold_automerge([], sea_fixed, corrections=sea_logs))
        for key in ("las_vegas_was", "new_york_was", "green_bay_was"):
            issues = facts.check_review(
                _lede(catalog[key]), last, recap, schedule=slate
            )
            self.assertTrue(issues, key)
            self.assertTrue(any("unverifiable" in item for item in issues), issues)

        self.assertEqual(
            facts.check_review(
                _lede(catalog["two_team_semi"]), last, recap, schedule=slate
            ),
            [],
        )
        self.assertEqual(
            facts.check_review(
                _lede(catalog["two_team_and"]), last, recap, schedule=slate
            ),
            [],
        )
        clock_issues = facts.check_review(
            _lede(catalog["kc_clock_even_though"]), last, recap, schedule=slate
        )
        self.assertTrue(
            any("34:21" in item and "KC" in item for item in clock_issues),
            clock_issues,
        )

        lac_recap = _load_fixture("espn_401872952_recap.json")
        lac_recap["oppAbbr"] = "LAC"
        last_lac = dict(last)
        last_lac["opponent"] = "Los Angeles Chargers"
        last_lac["opponentAbbr"] = "LAC"
        last_lac["opponentShort"] = "Chargers"
        la_out = facts._reword_misattributed_box(
            catalog["los_angeles_was"], lac_recap, last_lac, slate
        )
        self.assertEqual(
            la_out,
            "Against Los Angeles, Kansas City had 18 first downs, 88 rush, "
            "246 net pass, 25:39.",
        )
        ch_out = facts._reword_misattributed_box(
            catalog["chargers_was"], lac_recap, last_lac, slate
        )
        self.assertEqual(
            ch_out,
            "Against the Chargers, Kansas City had 18 first downs, 88 rush, "
            "246 net pass, 25:39.",
        )

        edition = _load_fixture("edition_2026-10-03-1538.json")
        blob = facts.edition_text(edition)
        self.assertIn("Walker scored again", blob)
        self.assertNotIn("Walker scored twice", blob)
        self.assertEqual(
            facts.check_review(edition, last, recap, schedule=slate), []
        )

    def test_karen_160_multiword_city_bind_and_nits(self):
        """#160 r5: Los Angeles / New York / Tampa Bay / Vegas bind on prod slate."""
        recap_src = _load_fixture("espn_401872952_recap.json")
        slate = self._prod_slate()
        self.assertTrue(
            any(
                game.get("opponentAbbr") in {"LAC", "NYJ", "TB", "LV"}
                for game in slate
            ),
            "schedule_2026.json must include Chargers, Jets, Buccaneers, Raiders",
        )

        def _lede(sentence, opponent):
            return {
                "phase": {"type": "regular"},
                "lastGameReview": {
                    "opponent": opponent,
                    "result": "W",
                    "score": "KC 24–10",
                    "lede": sentence,
                },
            }

        def _city_last(opponent, abbr, short):
            payload = dict(recap_src)
            payload["oppAbbr"] = abbr
            last = dict(self.LAST)
            last["date"] = "2026-09-27T17:00:00Z"
            last["id"] = "401872952"
            last["opponent"] = opponent
            last["opponentAbbr"] = abbr
            last["opponentShort"] = short
            return last, payload

        kc_was = "{} was 18 first downs, 88 rush, 246 net pass, 25:39."
        kc_had = "{} had 18 first downs and 25:39."
        kc_held = "{} held the ball 25:39."
        opp_was = "{} was 19 first downs, 119 rush, 210 net pass, 34:21."
        opp_had = "{} had 19 first downs and 34:21."
        opp_held = "{} held the ball 34:21."
        recaps = (
            ("Los Angeles Chargers", "LAC", "Chargers", "Los Angeles"),
            ("New York Jets", "NYJ", "Jets", "New York"),
            ("Tampa Bay Buccaneers", "TB", "Buccaneers", "Tampa Bay"),
            ("Las Vegas Raiders", "LV", "Raiders", "Vegas"),
        )
        for opponent, abbr, short, city in recaps:
            last, recap = _city_last(opponent, abbr, short)
            self.assertEqual(facts._alias_team(city, facts._team_aliases(last, recap, slate)), abbr)
            for sentence in (kc_was.format(city), kc_had.format(city), kc_held.format(city)):
                issues = facts.check_review(
                    _lede(sentence, opponent), last, recap, schedule=slate
                )
                self.assertTrue(
                    any(
                        ("18" in item and "19" in item)
                        or ("25:39" in item and abbr in item)
                        for item in issues
                    ),
                    (city, sentence, issues),
                )
            for sentence in (opp_was.format(city), opp_had.format(city), opp_held.format(city)):
                self.assertEqual(
                    facts.check_review(
                        _lede(sentence, opponent), last, recap, schedule=slate
                    ),
                    [],
                    (city, sentence),
                )

        last, recap = _city_last("Miami Dolphins", "MIA", "Dolphins")
        swapped = "Kansas City had 19 first downs; Miami had 18 first downs."
        swap_issues = facts.check_review(
            _lede(swapped, "Miami Dolphins"), last, recap, schedule=slate
        )
        self.assertTrue(swap_issues, swap_issues)
        swap_fixed, swap_logs = facts.apply_fact_corrections(
            _lede(swapped, "Miami Dolphins"),
            swap_issues,
            recap=recap,
            last_game=last,
            schedule=slate,
        )
        self.assertIn(swapped, facts.edition_text(swap_fixed))
        self.assertFalse(any("→" in line for line in swap_logs), swap_logs)
        leftover = facts.check_review(swap_fixed, last, recap, schedule=slate)
        dropped = facts.repair_offending_copy(swap_fixed, leftover, last, recap)
        self.assertNotIn(swapped, facts.edition_text(dropped))

        pair = "Kansas City and Miami were 19 and 18 first downs."
        pair_issues = facts.check_review(
            _lede(pair, "Miami Dolphins"), last, recap, schedule=slate
        )
        self.assertTrue(pair_issues, pair_issues)
        pair_fixed, pair_logs = facts.apply_fact_corrections(
            _lede(pair, "Miami Dolphins"),
            pair_issues,
            recap=recap,
            last_game=last,
            schedule=slate,
        )
        blob = facts.edition_text(pair_fixed)
        self.assertIn(pair, blob)
        self.assertNotIn("19 and 19", blob)
        self.assertFalse(any("→" in line for line in pair_logs), pair_logs)
        pair_left = facts.check_review(pair_fixed, last, recap, schedule=slate)
        self.assertTrue(pair_left, pair_left)
        self.assertTrue(facts.should_hold_automerge([], pair_fixed, leftover=pair_left))

        leading = "Even though Miami had 19, Kansas City held the ball 34:21."
        clock_issues = facts.check_review(
            _lede(leading, "Miami Dolphins"), last, recap, schedule=slate
        )
        self.assertTrue(
            any("34:21" in item and "KC" in item for item in clock_issues),
            clock_issues,
        )

    def test_karen_161_short_name_aliases_and_were_box(self):
        """#161 r6: LA/L.A./NY/N.Y./Tampa/Bucs/Fins bind; were-box; compound clock."""
        catalog = _load_fixture("edition_run_karen_161_short_aliases.json")
        recap_src = _load_fixture("espn_401872952_recap.json")
        slate = self._prod_slate()

        def _lede(sentence, opponent):
            return {
                "phase": {"type": "regular"},
                "lastGameReview": {
                    "opponent": opponent,
                    "result": "W",
                    "score": "KC 24–10",
                    "lede": sentence,
                },
            }

        def _city_last(opponent, abbr, short):
            payload = dict(recap_src)
            payload["oppAbbr"] = abbr
            last = dict(self.LAST)
            last["date"] = "2026-09-27T17:00:00Z"
            last["id"] = "401872952"
            last["opponent"] = opponent
            last["opponentAbbr"] = abbr
            last["opponentShort"] = short
            return last, payload

        def _issues(sentence, last, recap, opponent):
            return facts.check_review(
                _lede(sentence, opponent), last, recap, schedule=slate
            )

        def _kc_box_flagged(issues, abbr):
            return any(
                ("18" in item and "19" in item)
                or ("25:39" in item and abbr in item)
                for item in issues
            )

        recaps = (
            ("Los Angeles Chargers", "LAC", "Chargers",
             ("la_had_kc", "la_held_kc", "la_was_kc", "l_a_had_kc", "l_a_held_kc", "l_a_was_kc"),
             ("la_was_opp", "la_had_opp", "la_held_opp", "l_a_was_opp")),
            ("New York Jets", "NYJ", "Jets",
             ("ny_had_kc", "ny_held_kc", "ny_was_kc", "n_y_had_kc", "n_y_was_kc"),
             ("ny_was_opp", "n_y_was_opp")),
            ("Tampa Bay Buccaneers", "TB", "Buccaneers",
             ("tampa_had_kc", "tampa_held_kc", "tampa_was_kc", "bucs_had_kc", "bucs_held_kc", "bucs_was_kc"),
             ("tampa_was_opp", "bucs_was_opp")),
            ("Miami Dolphins", "MIA", "Dolphins",
             ("fins_had_kc", "fins_held_kc", "fins_was_kc"),
             ("fins_was_opp",)),
        )
        for opponent, abbr, short, kc_keys, opp_keys in recaps:
            last, recap = _city_last(opponent, abbr, short)
            for key in kc_keys:
                issues = _issues(catalog[key], last, recap, opponent)
                self.assertTrue(_kc_box_flagged(issues, abbr), (key, issues))
            for key in opp_keys:
                self.assertEqual(
                    _issues(catalog[key], last, recap, opponent),
                    [],
                    (key, _issues(catalog[key], last, recap, opponent)),
                )

        last_lv, recap_lv = _city_last("Las Vegas Raiders", "LV", "Raiders")
        were_kc = _issues(catalog["the_raiders_were_kc"], last_lv, recap_lv, "Las Vegas Raiders")
        self.assertTrue(_kc_box_flagged(were_kc, "LV"), were_kc)
        self.assertEqual(
            _issues(catalog["the_raiders_were_opp"], last_lv, recap_lv, "Las Vegas Raiders"),
            [],
        )
        swap = _issues(catalog["compound_clock_swap"], last_lv, recap_lv, "Las Vegas Raiders")
        self.assertTrue(
            any("34:21" in item or "25:39" in item for item in swap),
            swap,
        )

        last_mia, recap_mia = _city_last("Miami Dolphins", "MIA", "Dolphins")
        sea = _issues(catalog["seattle_had_kc"], last_mia, recap_mia, "Miami Dolphins")
        self.assertTrue(any("unverifiable" in item for item in sea), sea)
        for key in ("la_had_kc", "ny_had_kc"):
            issues = _issues(catalog[key], last_mia, recap_mia, "Miami Dolphins")
            self.assertTrue(
                any("unverifiable" in item for item in issues),
                (key, issues),
            )

    def test_karen_162_clock_bind_preview_rate_and_phase(self):
        """#162 r7: opp-subject KC clocks; preview rate hold; no average swap."""
        catalog = _load_fixture("edition_run_karen_162_clock_phase.json")
        recap_src = _load_fixture("espn_401872952_recap.json")
        slate = self._prod_slate()

        def _lede(sentence, opponent):
            return {
                "phase": {"type": "regular"},
                "lastGameReview": {
                    "opponent": opponent,
                    "result": "W",
                    "score": "KC 24–10",
                    "lede": sentence,
                },
            }

        def _city_last(opponent, abbr, short):
            payload = dict(recap_src)
            payload["oppAbbr"] = abbr
            last = dict(self.LAST)
            last["date"] = "2026-09-27T17:00:00Z"
            last["id"] = "401872952"
            last["opponent"] = opponent
            last["opponentAbbr"] = abbr
            last["opponentShort"] = short
            return last, payload

        last_lv, recap_lv = _city_last("Las Vegas Raiders", "LV", "Raiders")
        last_mia, recap_mia = _city_last("Miami Dolphins", "MIA", "Dolphins")

        raiders = catalog["raiders_compound"]
        self.assertEqual(
            raiders,
            "The Raiders had 19 first downs and 25:39 to the Chiefs’ 18 and 34:21.",
        )
        raiders_issues = facts.check_review(
            _lede(raiders, "Las Vegas Raiders"), last_lv, recap_lv, schedule=slate
        )
        self.assertTrue(
            any("25:39" in item and "LV" in item for item in raiders_issues),
            raiders_issues,
        )
        self.assertTrue(
            any("34:21" in item and "KC" in item for item in raiders_issues),
            raiders_issues,
        )

        vegas = catalog["las_vegas_had_kc_clock"]
        self.assertEqual(vegas, "Las Vegas had 19 first downs and 25:39.")
        vegas_issues = facts.check_review(
            _lede(vegas, "Las Vegas Raiders"), last_lv, recap_lv, schedule=slate
        )
        self.assertTrue(
            any("25:39" in item and "LV" in item for item in vegas_issues),
            vegas_issues,
        )

        miami = catalog["miami_finished_kc_clock"]
        self.assertEqual(
            miami, "Miami finished with 19 first downs and 25:39 of possession."
        )
        miami_issues = facts.check_review(
            _lede(miami, "Miami Dolphins"), last_mia, recap_mia, schedule=slate
        )
        self.assertTrue(
            any("25:39" in item and "MIA" in item for item in miami_issues),
            miami_issues,
        )

        reverse = "Kansas City had 18 first downs and 34:21."
        reverse_issues = facts.check_review(
            _lede(reverse, "Las Vegas Raiders"), last_lv, recap_lv, schedule=slate
        )
        self.assertTrue(
            any("34:21" in item and "KC" in item for item in reverse_issues),
            reverse_issues,
        )

        for key in (
            "preview_per_game",
            "preview_against_denver",
            "preview_denver_week1",
        ):
            sentence = catalog[key]
            issues = facts.check_review(
                _lede(sentence, "Miami Dolphins"),
                last_mia,
                recap_mia,
                schedule=slate,
            )
            self.assertFalse(
                any("unverifiable" in item for item in issues),
                (key, sentence, issues),
            )
            self.assertFalse(
                any("first downs" in item and "disagrees" in item for item in issues),
                (key, sentence, issues),
            )

        averaged = catalog["averaged_coming_in"]
        self.assertEqual(
            averaged,
            "Las Vegas averaged 22 first downs a game coming in, and had 19 on Sunday",
        )
        avg_nar = _lede(averaged, "Las Vegas Raiders")
        avg_issues = facts.check_review(avg_nar, last_lv, recap_lv, schedule=slate)
        self.assertFalse(
            any("first downs 22" in item for item in avg_issues),
            avg_issues,
        )
        avg_fixed, avg_logs = facts.apply_fact_corrections(
            avg_nar,
            avg_issues
            + [
                "first downs 22 disagrees with ESPN 19 for LV "
                f"({averaged!r})"
            ],
            recap=recap_lv,
            last_game=last_lv,
            schedule=slate,
        )
        blob = facts.edition_text(avg_fixed)
        self.assertIn("averaged 22", blob)
        self.assertNotIn("averaged 19", blob)
        self.assertFalse(any("averaged 22" in line and "→" in line for line in avg_logs), avg_logs)

    def test_karen_164_week_and_season_lows_still_flag(self):
        """#164 r8: Week N / season-low boxes flag; rate and against-other skip."""
        catalog = _load_fixture("edition_run_karen_164_rate_week.json")
        recap_src = _load_fixture("espn_401872952_recap.json")
        slate = self._prod_slate()

        def _lede(sentence, opponent):
            return {
                "phase": {"type": "regular"},
                "lastGameReview": {
                    "opponent": opponent,
                    "result": "W",
                    "score": "KC 24–10",
                    "lede": sentence,
                },
            }

        def _city_last(opponent, abbr, short, week):
            payload = dict(recap_src)
            payload["oppAbbr"] = abbr
            last = dict(self.LAST)
            last["date"] = "2026-09-27T17:00:00Z"
            last["id"] = "401872952"
            last["week"] = week
            last["opponent"] = opponent
            last["opponentAbbr"] = abbr
            last["opponentShort"] = short
            return last, payload

        last_lv, recap_lv = _city_last("Las Vegas Raiders", "LV", "Raiders", 4)
        last_mia, recap_mia = _city_last("Miami Dolphins", "MIA", "Dolphins", 3)

        def _issues(sentence, last, recap, opponent):
            return facts.check_review(
                _lede(sentence, opponent), last, recap, schedule=slate
            )

        for key in ("lv_week4_lead", "lv_week4_tail", "lv_fewest"):
            self.assertEqual(
                catalog[key],
                {
                    "lv_week4_lead": "In Week 4, Las Vegas had 18 first downs.",
                    "lv_week4_tail": "Las Vegas had 18 first downs in Week 4.",
                    "lv_fewest": "The Raiders had 18 first downs, their fewest of the season.",
                }[key],
            )
            issues = _issues(catalog[key], last_lv, recap_lv, "Las Vegas Raiders")
            self.assertTrue(
                any("first downs 18" in item and "19" in item for item in issues),
                (key, issues),
            )

        self.assertEqual(
            catalog["kc_season_low"],
            "Kansas City’s 19 first downs were a season low.",
        )
        kc_low = _issues(
            catalog["kc_season_low"], last_lv, recap_lv, "Las Vegas Raiders"
        )
        self.assertTrue(
            any("first downs 19" in item and "18" in item for item in kc_low),
            kc_low,
        )

        self.assertEqual(
            catalog["mia_week3"],
            "In Week 3 at Miami, the Dolphins had 18 first downs.",
        )
        mia_week = _issues(
            catalog["mia_week3"], last_mia, recap_mia, "Miami Dolphins"
        )
        self.assertTrue(
            any("first downs 18" in item and "19" in item for item in mia_week),
            mia_week,
        )

        self.assertEqual(
            catalog["ind_week2"],
            "Indianapolis had 29 first downs against Kansas City in Week 2.",
        )
        ind = _issues(catalog["ind_week2"], last_mia, recap_mia, "Miami Dolphins")
        self.assertTrue(
            any("first downs 29" in item and "24" in item for item in ind),
            ind,
        )
        self.assertEqual(
            catalog["kc_week2"],
            "In Week 2, Kansas City had 24 first downs against Indianapolis.",
        )
        kc_w2 = _issues(catalog["kc_week2"], last_mia, recap_mia, "Miami Dolphins")
        self.assertTrue(
            any("first downs 24" in item and "29" in item for item in kc_w2),
            kc_w2,
        )

        comma = _issues(
            catalog["comma_clock"], last_mia, recap_mia, "Miami Dolphins"
        )
        self.assertTrue(
            any("25:39" in item and "MIA" in item for item in comma),
            comma,
        )
        chiefs_tail = _issues(
            catalog["tail_chiefs_fd"], last_lv, recap_lv, "Las Vegas Raiders"
        )
        self.assertTrue(
            any("first downs 19" in item and "18" in item for item in chiefs_tail),
            chiefs_tail,
        )
        miami_tail = _issues(
            catalog["tail_miami_fd"], last_mia, recap_mia, "Miami Dolphins"
        )
        self.assertTrue(
            any("first downs 18" in item and "19" in item for item in miami_tail),
            miami_tail,
        )
        neither = _issues(
            catalog["neither_clock"], last_lv, recap_lv, "Las Vegas Raiders"
        )
        self.assertTrue(any("31:05" in item for item in neither), neither)

        against = _issues(
            catalog["lv_against_denver"], last_lv, recap_lv, "Las Vegas Raiders"
        )
        self.assertFalse(
            any("first downs" in item and "disagrees" in item for item in against),
            against,
        )
        self.assertFalse(any("unverifiable" in item for item in against), against)

        finished = catalog["miami_finished_18"]
        self.assertEqual(finished, "Miami finished with 18 first downs")
        fin_issues = _issues(finished, last_mia, recap_mia, "Miami Dolphins")
        self.assertTrue(
            any("first downs 18" in item and "19" in item for item in fin_issues),
            fin_issues,
        )
        fin_fixed, fin_logs = facts.apply_fact_corrections(
            _lede(finished, "Miami Dolphins"),
            fin_issues,
            recap=recap_mia,
            last_game=last_mia,
            schedule=slate,
        )
        self.assertIn("Miami finished with 19 first downs", facts.edition_text(fin_fixed))
        self.assertTrue(any("→" in line for line in fin_logs), fin_logs)

        r7 = _load_fixture("edition_run_karen_162_clock_phase.json")
        for key in (
            "raiders_compound",
            "las_vegas_had_kc_clock",
            "miami_finished_kc_clock",
        ):
            last, recap, opp = (
                (last_lv, recap_lv, "Las Vegas Raiders")
                if "miami" not in key
                else (last_mia, recap_mia, "Miami Dolphins")
            )
            issues = _issues(r7[key], last, recap, opp)
            self.assertTrue(
                any("25:39" in item or "34:21" in item for item in issues),
                (key, issues),
            )
        for key in (
            "preview_per_game",
            "preview_against_denver",
            "preview_denver_week1",
        ):
            issues = _issues(r7[key], last_mia, recap_mia, "Miami Dolphins")
            self.assertFalse(
                any("unverifiable" in item for item in issues),
                (key, issues),
            )
        avg_fixed, avg_logs = facts.apply_fact_corrections(
            _lede(r7["averaged_coming_in"], "Las Vegas Raiders"),
            [
                "first downs 22 disagrees with ESPN 19 for LV "
                f"({r7['averaged_coming_in']!r})"
            ],
            recap=recap_lv,
            last_game=last_lv,
            schedule=slate,
        )
        self.assertIn("averaged 22", facts.edition_text(avg_fixed))
        self.assertNotIn("averaged 19", facts.edition_text(avg_fixed))
        self.assertFalse(
            any("averaged 22" in line and "→" in line for line in avg_logs),
            avg_logs,
        )

    def test_karen_165_possession_game_clocks_and_archive_replay(self):
        """#165 r9: game clocks / run-of-show ranges are not TOP; r8 flags stay."""
        catalog = _load_fixture("edition_run_karen_165_possession.json")
        r8 = _load_fixture("edition_run_karen_164_rate_week.json")
        r7 = _load_fixture("edition_run_karen_162_clock_phase.json")
        aliases = _load_fixture("edition_run_karen_161_short_aliases.json")
        recap_src = _load_fixture("espn_401872952_recap.json")
        slate = self._prod_slate()

        def _lede(sentence, opponent):
            return {
                "phase": {"type": "regular"},
                "lastGameReview": {
                    "opponent": opponent,
                    "result": "W",
                    "score": "KC 24–10",
                    "lede": sentence,
                },
            }

        def _city_last(opponent, abbr, short, week):
            payload = dict(recap_src)
            payload["oppAbbr"] = abbr
            last = dict(self.LAST)
            last["date"] = "2026-09-27T17:00:00Z"
            last["id"] = "401872952"
            last["week"] = week
            last["opponent"] = opponent
            last["opponentAbbr"] = abbr
            last["opponentShort"] = short
            return last, payload

        last_lv, recap_lv = _city_last("Las Vegas Raiders", "LV", "Raiders", 4)
        last_mia, recap_mia = _city_last("Miami Dolphins", "MIA", "Dolphins", 3)

        def _issues(sentence, last, recap, opponent, extra=None):
            payload = _lede(sentence, opponent)
            if extra:
                payload.update(extra)
            return facts.check_review(payload, last, recap, schedule=slate)

        def _possession_not_on_box(issues):
            return [
                item
                for item in issues
                if re.search(
                    r"possession \d{1,2}:\d{2} is not on the ESPN box",
                    item,
                )
            ]

        n4 = catalog["week3_review_lede"]
        self.assertIn("2:06 into the first quarter", n4)
        self.assertIn("kept the ball", n4)
        n4_issues = _issues(n4, last_mia, recap_mia, "Miami Dolphins")
        self.assertFalse(
            any("2:06" in item and "possession" in item for item in n4_issues),
            n4_issues,
        )
        full = catalog["week3_review_lede_full"]
        full_issues = _issues(full, last_mia, recap_mia, "Miami Dolphins")
        self.assertFalse(
            any(
                clock in item and "possession" in item
                for clock in ("2:06", "2:55")
                for item in full_issues
            ),
            full_issues,
        )

        live_shape = catalog["n4_elapsed"]
        live_issues = _issues(live_shape, last_mia, recap_mia, "Miami Dolphins")
        self.assertFalse(
            any("2:06" in item and "possession" in item for item in live_issues),
            live_issues,
        )

        c4 = catalog["c4_scoring_clock"]
        c4_issues = _issues(c4, last_mia, recap_mia, "Miami Dolphins")
        self.assertFalse(
            any("12:54" in item and "possession" in item for item in c4_issues),
            c4_issues,
        )
        across = _issues(
            catalog["c4_across_sentences"], last_mia, recap_mia, "Miami Dolphins"
        )
        self.assertFalse(
            any("12:54" in item and "possession" in item for item in across),
            across,
        )

        ros = catalog["sep27_run_of_show"]
        ros_issues = _issues(ros, last_mia, recap_mia, "Miami Dolphins")
        self.assertFalse(_possession_not_on_box(ros_issues), ros_issues)
        ros_nar = {
            "phase": {"type": "regular"},
            "lastGameReview": {
                "opponent": "Miami Dolphins",
                "result": "W",
                "score": "KC 24–10",
                "lede": "Kansas City won 24-10.",
            },
            "runOfShow": [
                {
                    "length": "0:00-1:20",
                    "talkTrack": (
                        "We are 3-0. We won 24-10. Miami held the ball "
                        "34 minutes and we went 3-of-7 on third down."
                    ),
                }
            ],
        }
        ros_struct = facts.check_review(
            ros_nar, last_mia, recap_mia, schedule=slate
        )
        self.assertFalse(_possession_not_on_box(ros_struct), ros_struct)

        for key in (
            "la_held_kc",
            "la_held_opp",
            "fins_held_kc",
            "compound_clock_swap",
        ):
            wrapped = f"[0:00-1:20] {aliases[key]}"
            issues = _issues(wrapped, last_mia, recap_mia, "Miami Dolphins")
            self.assertFalse(
                any(
                    clock in item and "possession" in item and "not on the ESPN box" in item
                    for clock in ("0:00", "1:20")
                    for item in issues
                ),
                (key, issues),
            )

        for key in (
            "lv_week4_lead",
            "lv_week4_tail",
            "lv_fewest",
            "kc_season_low",
            "mia_week3",
            "ind_week2",
            "kc_week2",
        ):
            last, recap, opp = (
                (last_lv, recap_lv, "Las Vegas Raiders")
                if key.startswith(("lv_", "kc_season"))
                else (last_mia, recap_mia, "Miami Dolphins")
            )
            if key == "kc_week2":
                last, recap, opp = last_mia, recap_mia, "Miami Dolphins"
            issues = _issues(r8[key], last, recap, opp)
            self.assertTrue(
                any("first downs" in item and "disagrees" in item for item in issues),
                (key, issues),
            )

        comma = _issues(r8["comma_clock"], last_mia, recap_mia, "Miami Dolphins")
        self.assertTrue(
            any("25:39" in item and "MIA" in item for item in comma),
            comma,
        )
        chiefs_tail = _issues(
            r8["tail_chiefs_fd"], last_lv, recap_lv, "Las Vegas Raiders"
        )
        self.assertTrue(
            any("first downs 19" in item and "18" in item for item in chiefs_tail),
            chiefs_tail,
        )
        miami_tail = _issues(
            r8["tail_miami_fd"], last_mia, recap_mia, "Miami Dolphins"
        )
        self.assertTrue(
            any("first downs 18" in item and "19" in item for item in miami_tail),
            miami_tail,
        )
        neither = _issues(
            r8["neither_clock"], last_lv, recap_lv, "Las Vegas Raiders"
        )
        self.assertTrue(any("31:05" in item for item in neither), neither)

        for key in (
            "raiders_compound",
            "las_vegas_had_kc_clock",
            "miami_finished_kc_clock",
        ):
            last, recap, opp = (
                (last_lv, recap_lv, "Las Vegas Raiders")
                if "miami" not in key
                else (last_mia, recap_mia, "Miami Dolphins")
            )
            issues = _issues(r7[key], last, recap, opp)
            self.assertTrue(
                any("25:39" in item or "34:21" in item for item in issues),
                (key, issues),
            )

        rush_swap = _issues(
            catalog["comma_rush_swap"], last_mia, recap_mia, "Miami Dolphins"
        )
        self.assertTrue(
            any("team rushing 88" in item and "119" in item for item in rush_swap),
            rush_swap,
        )

        for key, last, recap, opp in (
            ("lv_averaged_week4", last_lv, recap_lv, "Las Vegas Raiders"),
            ("mia_averaged_week3", last_mia, recap_mia, "Miami Dolphins"),
        ):
            issues = _issues(catalog[key], last, recap, opp)
            self.assertTrue(
                any("first downs 18" in item and "19" in item for item in issues),
                (key, issues),
            )

        sunday = _issues(
            catalog["averaged_then_sunday"], last_lv, recap_lv, "Las Vegas Raiders"
        )
        self.assertFalse(
            any("first downs 22" in item for item in sunday),
            sunday,
        )
        self.assertTrue(
            any("first downs 18" in item and "19" in item for item in sunday),
            sunday,
        )

        editions_dir = Path("data") / "narrative_editions"
        self.assertTrue(editions_dir.is_dir(), editions_dir)
        for path in sorted(editions_dir.glob("*.json")):
            edition = json.loads(path.read_text(encoding="utf-8"))
            issues = facts.check_review(
                edition, last_mia, recap_mia, schedule=slate
            )
            blob = facts.edition_text(edition)
            for item in _possession_not_on_box(issues):
                clock_hit = re.search(
                    r"possession (\d{1,2}:\d{2}) is not on the ESPN box",
                    item,
                )
                self.assertIsNotNone(clock_hit, item)
                clock = clock_hit.group(1)
                for match in facts._POSSESSION_CLOCK.finditer(blob):
                    if match.group(1) != clock:
                        continue
                    self.assertFalse(
                        facts._possession_clock_is_game_or_segment(blob, match),
                        (path.name, item),
                    )

    def test_write_archive_survives_missing_headline(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "archive.json"
            path.write_text("[]\n", encoding="utf-8")
            with patch.object(generate.config, "ARCHIVE_JSON", path):
                generate._write_archive(
                    {
                        "generatedAt": "2026-10-03T14:40:00Z",
                        "edition": "2026 Week 4 · Preview",
                        "phase": {"label": "Week 4"},
                    }
                )
            rows = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(rows[0]["headline"], "2026 Week 4 · Preview")
        self.assertEqual(rows[0]["edition"], "2026 Week 4 · Preview")

    def test_generate_survives_headline_keyerror_from_37129473980(self):
        """Oct 3 37 9 crashed in _write_archive after salvage dropped the title."""
        result = {
            "narrative": {
                "edition": "2026 Week 4 · Preview",
                "slug": "2026-10-03-1538",
                "generatedAt": "2026-10-03T14:40:00Z",
            },
            "schedule": [],
            "news": [],
            "droppedSentences": ["88 yards, 25:39, and a 3-0 Raiders problem"],
            "correctedSentences": [],
            "leftoverIssues": ["possession leftover"],
        }
        with patch.object(generate, "build", return_value=result):
            rc = generate.main(["--dry-run"])
        self.assertEqual(rc, 0)
        self.assertEqual(result["narrative"]["headline"], "2026 Week 4 · Preview")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "archive.json"
            path.write_text("[]\n", encoding="utf-8")
            with patch.object(generate.config, "ARCHIVE_JSON", path):
                generate._write_archive(dict(result["narrative"]))
            rows = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(rows[0]["headline"], "2026 Week 4 · Preview")

    def test_generate_holds_when_headline_missing_without_a_drop(self):
        """A missing title with no recorded drop must hold, not automerge."""
        fat = "Kansas City " + ("won in Miami. " * 400)
        result = {
            "narrative": {
                "edition": "2026 Week 4 · Preview",
                "slug": "2026-10-03-1538",
                "generatedAt": "2026-10-03T14:40:00Z",
                "generator": "offline",
                "dek": fat,
                "theEdge": fat,
                "storyline": fat,
                "currentState": fat,
                "gamePlan": fat,
                "lastGameReview": {"lede": fat, "analysis": [fat]},
            },
            "schedule": [],
            "news": [],
            "droppedSentences": [],
            "correctedSentences": [],
            "leftoverIssues": [],
        }
        self.assertGreaterEqual(
            facts.edition_word_count(result["narrative"]),
            facts.PUBLISH_WORD_FLOOR,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repair_json = root / "repair.json"
            narrative_json = root / "narrative.json"
            archive = root / "archive.json"
            archive.write_text("[]\n", encoding="utf-8")
            editions = root / "editions"
            editions.mkdir()
            wire_json = root / "wire.json"
            with patch.object(generate, "build", return_value=result), patch.object(
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
            ), patch.object(
                generate.config, "ensure_dirs", lambda: None
            ):
                rc = generate.main(["--provider", "offline"])
            self.assertEqual(rc, 0)
            repair = json.loads(repair_json.read_text(encoding="utf-8"))
        self.assertTrue(repair["holdAutomerge"])
        self.assertEqual(repair["droppedSentences"], [])
        self.assertEqual(result["narrative"]["headline"], "2026 Week 4 · Preview")

    def test_generate_keeps_headline_and_ind_rush_from_37129473980(self):
        catalog = _load_fixture("edition_run_37129473980.json")
        recap = _load_fixture("espn_401872952_recap.json")
        draft = {
            "headline": catalog["headline"],
            "dek": catalog["keep"][1],
            "theEdge": catalog["keep"][2],
            "lastGameReview": {
                "lede": (
                    catalog["rushing_sentence"]
                    + " Mahomes sat through 8:42 of possession."
                ),
                "analysis": [catalog["keep"][8], catalog["keep"][9]],
            },
            "storyline": {"lede": catalog["vacuum_sentence"], "body": []},
        }
        last = dict(self.LAST)
        last["id"] = "401872952"
        last["date"] = "2026-09-27T17:00:00Z"
        last["kickoff"] = "Sun, Sep 27 · 12:00 PM CT"
        indy = {
            "id": "401872945",
            "week": 2,
            "seasonType": "reg",
            "date": "2026-09-20T17:00:00Z",
            "opponent": "Indianapolis Colts",
            "opponentAbbr": "IND",
            "completed": True,
            "kcScore": 33,
            "oppScore": 30,
        }
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
            "mode": "preview",
            "edition": "2026 Week 4 · Preview",
            "lastGame": last,
            "nextGame": week4,
            "liveGame": None,
        }
        llm = Mock(side_effect=[(draft, "grok"), (draft, "grok"), (draft, "grok")])

        def _recap_for(event_id):
            if str(event_id) == "401872945":
                return recap.get("prior") or {}
            return recap

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
                return_value={"schedule": [indy, last, week4], "news": [], "markets": {}},
            ), patch.object(
                collect, "fetch_game_recap", side_effect=_recap_for
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
            self.assertEqual(
                rc, 0, repair_json.read_text() if repair_json.exists() else "no repair"
            )
            written = json.loads(narrative_json.read_text(encoding="utf-8"))
            blob = facts.edition_text(written)
            self.assertEqual(written["headline"], catalog["headline"])
            self.assertIn("152 rushing yards", blob)
            self.assertNotIn("119 rushing yards", blob)
            self.assertIn("40-drop vacuum", blob)
            self.assertNotIn("8:42", blob)
            archive_rows = json.loads(archive.read_text(encoding="utf-8"))
            self.assertEqual(archive_rows[0]["headline"], catalog["headline"])

    def test_salvage_cleans_every_section_and_empty_cards(self):
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        drop = (
            "Zone blitz Jones/Karlaftis with a dropping end on "
            "second-and-long — the look that helped produce the Willis "
            "interception."
        )
        narrative = {
            "headline": "Keep this title",
            "lastGameReview": {
                "lede": "Kansas City finished 24–10 against Miami.",
            },
            "storyline": {
                "body": [drop + " That is leftover after the cut."]
            },
            "gamePlan": {
                "script": [
                    "Second-and-long vs Las Vegas: " + drop + " Steal the down; "
                    "do not hunt a takeaway that is not there."
                ]
            },
            "matchups": [
                {
                    "unit": "Spagnuolo vs. Kubiak / Bowers",
                    "note": drop + " Miami converted 8-of-16.",
                }
            ],
            "xsandos": [
                {
                    "title": drop,
                    "why": drop,
                    "coaching": "Hunt a hurried throw.",
                    "concept": "zone_blitz",
                }
            ],
        }
        issues = facts.check_review(narrative, last, recap)
        repaired = facts.repair_offending_copy(narrative, issues, last)
        blob = facts.edition_text(repaired)
        self.assertNotIn("That is leftover after the cut", blob)
        self.assertTrue(
            any(
                isinstance(card, dict)
                and "produce the Willis interception" in str(card.get("title") or "")
                for card in (repaired.get("xsandos") or [])
            )
        )
        script = ((repaired.get("gamePlan") or {}).get("script") or [])
        self.assertTrue(
            all("Steal the down" not in str(item) for item in script), script
        )
        cards = repaired.get("xsandos") or []
        self.assertFalse(
            any(
                isinstance(card, dict) and not (card.get("title") or "").strip()
                for card in cards
            ),
            cards,
        )
        matchups = repaired.get("matchups") or []
        self.assertFalse(
            any(
                "8-of-16" in str((card or {}).get("note") or "")
                for card in matchups
            ),
            matchups,
        )
        emptied = facts._drop_value(
            {"title": drop, "why": "Keep the rest of this card."},
            facts.violation_snippets(issues),
        )
        self.assertEqual(emptied.get("title"), drop)
        self.assertIn("why", emptied)

    def test_salvage_drops_walker_q1_after_q2_play_order_splice(self):
        """Run 37030574826: salvage left an inverted Q1-after-Q2 claim.

        The quoted snippet glued 'nobodyafter', so the drop pass missed
        Walker's 10-yard Q1 12:54 'after' a 5-yard TD at Q2 12:58 and
        refused publish. The whole play-order sentence must go; a valid
        later sequence and the score lede stay.
        """
        recap = _load_fixture("espn_401872952_recap.json")
        last = dict(self.LAST)
        last["date"] = "2026-09-27T17:00:00Z"
        inverted = (
            "Walker’s 10-yard run at Q1 12:54, trailed nobody after a "
            "5-yard touchdown at Q2 12:58 made it 14–7."
        )
        keeper_order = (
            "An 11-yard touchdown following the 34-yard field goal made it 24-10."
        )
        rushing = "Kansas City had 70 rushing yards."
        narrative = {
            "headline": "Keep this title",
            "lastGameReview": {
                "lede": (
                    "Kansas City finished 24–10 against Miami. "
                    + inverted
                    + " "
                    + keeper_order
                ),
                "analysis": [rushing],
            },
        }
        issues = facts.check_review(narrative, last, recap)
        self.assertTrue(any("play order" in item for item in issues), issues)
        self.assertTrue(
            any("12:54" in item and "12:58" in item for item in issues),
            issues,
        )
        snippets = facts.violation_snippets(issues)
        self.assertFalse(any("nobodyafter" in item for item in snippets), snippets)
        play_snips = [
            item
            for item in snippets
            if "12:54" in item or "nobody after" in item.lower()
        ]
        self.assertTrue(play_snips, snippets)
        self.assertTrue(
            any(item in inverted for item in play_snips),
            play_snips,
        )

        repaired = facts.repair_offending_copy(narrative, issues, last)
        blob = facts.edition_text(repaired)
        self.assertNotIn("12:54", blob)
        self.assertNotIn("nobody after", blob.lower())
        self.assertNotIn("70 rushing yards", blob)
        self.assertIn("24", blob)
        self.assertIn("11-yard touchdown following the 34-yard", blob)
        leftover = facts.check_review(repaired, last, recap)
        self.assertFalse(any("play order" in item for item in leftover), leftover)
        self.assertEqual(facts.repair_publish_blockers(leftover, repaired, []), [])

        # Historical glued quote from the failed daily run. Snippet miss
        # must not leave the inverted claim when ESPN chronology is re-checked.
        glued = (
            "play order: td 10yd td Q1 12:54 is not after td 5yd td Q2 12:58 "
            "('Walker’s 10-yard run at Q1 12:54, trailed nobodyafter a "
            "5-yard touchdown at Q2 12:58 made it 14–7')"
        )
        self.assertIn(
            "nobody after",
            " ".join(facts.violation_snippets([glued])).lower(),
        )
        glued_only = facts.repair_offending_copy(narrative, [glued], last)
        glued_blob = facts.edition_text(glued_only)
        self.assertNotIn("12:54", glued_blob)
        self.assertNotIn("nobody after", glued_blob.lower())
        self.assertIn("11-yard touchdown following the 34-yard", glued_blob)

        missed = facts.repair_offending_copy(
            narrative,
            ["play order: glued miss ('nobodyafter')"],
            last,
            recap,
        )
        missed_blob = facts.edition_text(missed)
        self.assertNotIn("12:54", missed_blob)
        self.assertNotIn("nobody after", missed_blob.lower())
        self.assertIn("11-yard touchdown following the 34-yard", missed_blob)
        self.assertFalse(
            any(
                "play order" in item
                for item in facts.check_review(missed, last, recap)
            )
        )

    def test_hold_automerge_on_noisy_salvage_or_short_desk(self):
        fat = " ".join(["Chiefs tape review word"] * 800)
        fat_edition = {
            "headline": fat,
            "dek": fat,
            "theEdge": fat,
            "storyline": fat,
            "currentState": fat,
            "gamePlan": fat,
            "lastGameReview": {"lede": fat, "analysis": [fat]},
        }
        self.assertGreaterEqual(
            facts.edition_word_count(fat_edition), facts.PUBLISH_WORD_FLOOR
        )
        self.assertTrue(
            facts.should_hold_automerge(["one drop"], fat_edition)
        )
        self.assertTrue(
            facts.should_hold_automerge(["a", "b", "c"], fat_edition)
        )
        self.assertTrue(
            facts.should_hold_automerge(["a", "b", "c", "d"], fat_edition)
        )
        self.assertTrue(
            facts.should_hold_automerge(
                [], fat_edition, corrections=["29 → 24"]
            )
        )
        thin = {"headline": "Short", "lastGameReview": {"lede": "Kansas City won."}}
        self.assertTrue(facts.should_hold_automerge([], thin))
        self.assertLess(
            facts.edition_word_count(thin), facts.PUBLISH_WORD_FLOOR
        )
        untitled = dict(fat_edition)
        untitled["headline"] = ""
        self.assertTrue(facts.should_hold_automerge([], untitled))
        self.assertTrue(
            facts.should_hold_automerge([], fat_edition, missing_headline=True)
        )

    def _karen_ctx(self, name):
        recap_mia = _load_fixture("espn_401872952_recap.json")
        recap_lv5 = _load_fixture("espn_lv5_synthetic_recap.json")
        recap_sea = _load_fixture("espn_401873305_recap.json")
        recap_ind = _load_fixture("espn_401872945_recap.json")
        slate = self._prod_slate()
        last_mia = dict(self.LAST)
        last_mia.update(
            {
                "date": "2026-09-27T17:00:00Z",
                "id": "401872952",
                "week": 3,
                "opponent": "Miami Dolphins",
                "opponentAbbr": "MIA",
                "opponentShort": "Dolphins",
            }
        )
        last_lv = dict(last_mia)
        last_lv.update(
            {
                "id": "401872976",
                "week": 4,
                "opponent": "Las Vegas Raiders",
                "opponentAbbr": "LV",
                "opponentShort": "Raiders",
                "kcScore": 27,
                "oppScore": 17,
            }
        )
        last_tb = dict(last_lv)
        last_tb.update(
            {
                "opponent": "Tampa Bay Buccaneers",
                "opponentAbbr": "TB",
                "opponentShort": "Buccaneers",
                "kcScore": 15,
                "oppScore": 16,
            }
        )
        last_sea = dict(last_mia)
        last_sea.update(
            {
                "id": "401873305",
                "week": 0,
                "opponent": "Seattle Seahawks",
                "opponentAbbr": "SEA",
                "opponentShort": "Seahawks",
                "seasonType": "pre",
                "kcScore": 9,
                "oppScore": 9,
            }
        )
        last_ind = dict(last_mia)
        last_ind.update(
            {
                "id": "401872945",
                "week": 2,
                "opponent": "Indianapolis Colts",
                "opponentAbbr": "IND",
                "opponentShort": "Colts",
                "kcScore": 33,
                "oppScore": 30,
            }
        )
        recap_tb = dict(recap_lv5)
        recap_tb["oppAbbr"] = "TB"
        recap_relabel = dict(recap_mia)
        recap_relabel["oppAbbr"] = "LV"
        table = {
            "mia": (last_mia, recap_mia),
            "lv5": (last_lv, recap_lv5),
            "tb5": (last_tb, recap_tb),
            "lv_relabel": (last_lv, recap_relabel),
            "sea": (last_sea, recap_sea),
            "ind": (last_ind, recap_ind),
        }
        last, recap = table[name]
        return last, recap, slate

    def _run_karen_matrix(self, name, builder):
        from tools.tests import karen_matrices

        cases = builder()
        expected = karen_matrices.MATRIX_COUNTS[name]
        self.assertEqual(len(cases), expected, f"{name} case count")
        misses = []
        for case in cases:
            with self.subTest(matrix=name, ctx=case.get("ctx"), sentence=case["sentence"][:80]):
                result = karen_matrices.evaluate(case)
                if not result["ok"]:
                    misses.append(
                        f"{name} {case.get('ctx')} exp={case['expect_flag']} "
                        f"got={result['flagged']} {case['sentence'][:120]}"
                    )
        self.assertEqual(misses, [], f"{name}: {len(misses)}/{len(cases)} {misses[:8]}")

    def test_karen_archive_replay(self):
        """Pinned historical salvage plus strict checks on every added edition."""
        from tools.tests.archive_replay import salvage_all_editions

        known = _load_fixture("archive_replay_known_bad.json")
        rows = salvage_all_editions()
        self._assert_karen_archive_replay(rows, known)

    def _assert_karen_archive_replay(self, rows, known):
        from tools.tests.archive_replay import quoted_snippets

        pinned = _load_fixture("archive_replay_editions.json")
        self.assertEqual(len(pinned), known["editions"])
        self.assertEqual(len(set(pinned)), len(pinned), "duplicate pinned edition")
        slugs = [slug for slug, _ in rows]
        self.assertEqual(len(set(slugs)), len(slugs), "duplicate replay edition")
        self.assertEqual(set(pinned) - set(slugs), set(), "missing historical edition")
        for field in ("issues", "drops", "changed"):
            self.assertTrue(set(known.get(field, {})) <= set(pinned), field)
        unexpected = []
        missing = []
        rewritten = []
        drop_miss = []
        for slug, row in rows:
            allowed = list(known["issues"].get(slug) or [])
            for item in row["issues"]:
                if item not in allowed:
                    unexpected.append(f"{slug}: {item}")
            for item in allowed:
                if item not in row["issues"]:
                    missing.append(f"{slug}: {item}")
            allowed_snips = quoted_snippets(allowed)
            if not allowed:
                if row["before"] != row["after"]:
                    rewritten.append(f"{slug}: clean edition text changed")
            for log in row["logs"]:
                if " → " in log:
                    old, new = log.split(" → ", 1)
                    if any(snip and snip in old for snip in allowed_snips):
                        continue
                    rewritten.append(f"{slug}: {log}")
            for sentence in row["after_sents"]:
                if sentence in row["before_sents"]:
                    continue
                if sentence == facts.safe_score_lede(None) or re.match(
                    r"^Kansas City finished \d+[–-]\d+ against .+\.$",
                    sentence,
                ):
                    continue
                rewritten.append(f"{slug}: invented {sentence[:160]!r}")
            if slug in known.get("drops", {}):
                got = list(row.get("drops") or [])
                want = list(known["drops"][slug])
                if got != want:
                    drop_miss.append(f"{slug}: drops {got} != {want}")
            if slug in known.get("changed", {}):
                got = list(row.get("changed") or [])
                want = list(known["changed"][slug])
                if got != want:
                    drop_miss.append(f"{slug}: changed {got} != {want}")
        self.assertEqual(unexpected, [], unexpected[:8])
        self.assertEqual(missing, [], missing[:8])
        self.assertEqual(rewritten, [], rewritten[:8])
        self.assertEqual(drop_miss, [], drop_miss[:8])

    def test_karen_archive_growth(self):
        from tools.tests.archive_replay import salvage_all_editions

        known = _load_fixture("archive_replay_known_bad.json")
        rows = salvage_all_editions()
        clean = next(row for _, row in rows if not row["issues"] and row["before"] == row["after"])
        added = ("2099-01-01-0000.json", copy.deepcopy(clean))
        self._assert_karen_archive_replay(rows + [added], known)
        # Equal count cannot conceal removal/renaming of a clean pinned edition.
        clean_slug = next(slug for slug, row in rows if row is clean)
        with self.assertRaisesRegex(AssertionError, "missing historical edition"):
            self._assert_karen_archive_replay(
                [(slug, row) for slug, row in rows if slug != clean_slug] + [added], known
            )
        with self.assertRaisesRegex(AssertionError, "duplicate replay edition"):
            self._assert_karen_archive_replay(rows + [rows[0]], known)

    def test_karen_archive_growth_rejects_issues_and_rewrites(self):
        from tools.tests.archive_replay import salvage_all_editions

        known = _load_fixture("archive_replay_known_bad.json")
        rows = salvage_all_editions()
        clean = next(row for _, row in rows if not row["issues"] and row["before"] == row["after"])
        for mutation in ("issue", "rewrite", "invented"):
            with self.subTest(mutation=mutation):
                added = copy.deepcopy(clean)
                if mutation == "issue":
                    added["issues"] = ["unallowlisted factual regression"]
                elif mutation == "rewrite":
                    added["after"] += " Silent rewrite."
                else:
                    added["after_sents"].append("Invented historical claim.")
                with self.assertRaises(AssertionError):
                    self._assert_karen_archive_replay(
                        rows + [("2099-01-01-0000.json", added)], known
                    )

    def test_karen_matrix_mw7(self):
        from tools.tests import karen_matrices
        self._run_karen_matrix("mw7", karen_matrices.mw7_cases)

    def test_karen_matrix_mx8(self):
        from tools.tests import karen_matrices
        self._run_karen_matrix("mx8", karen_matrices.mx8_cases)

    def test_karen_matrix_t32(self):
        from tools.tests import karen_matrices
        self._run_karen_matrix("t32", karen_matrices.t32_cases)

    def test_karen_matrix_probes(self):
        from tools.tests import karen_matrices
        self._run_karen_matrix("probes", karen_matrices.probe_cases)

    def test_karen_matrix_overreach7(self):
        from tools.tests import karen_matrices
        self._run_karen_matrix("overreach7", karen_matrices.overreach_cases)

    def test_karen_matrix_ov2_7(self):
        from tools.tests import karen_matrices
        self._run_karen_matrix("ov2_7", karen_matrices.ov2_cases)

    def test_karen_matrix_pv10(self):
        from tools.tests import karen_matrices
        self._run_karen_matrix("pv10", karen_matrices.pv10_cases)

    def test_karen_matrix_pv11a(self):
        from tools.tests import karen_matrices
        self._run_karen_matrix("pv11a", karen_matrices.pv11a_cases)

    def test_karen_matrix_pv11b(self):
        from tools.tests import karen_matrices
        self._run_karen_matrix("pv11b", karen_matrices.pv11b_cases)

    def test_karen_matrix_pv8(self):
        from tools.tests import karen_matrices
        self._run_karen_matrix("pv8", karen_matrices.pv8_cases)

    def test_karen_matrix_pv8b(self):
        from tools.tests import karen_matrices
        self._run_karen_matrix("pv8b", karen_matrices.pv8b_cases)

    def test_karen_matrix_pv9(self):
        from tools.tests import karen_matrices
        self._run_karen_matrix("pv9", karen_matrices.pv9_cases)

    def test_karen_matrix_pv11c(self):
        from tools.tests import karen_matrices
        self._run_karen_matrix("pv11c", karen_matrices.pv11c_cases)

    def test_karen_matrix_pv12(self):
        from tools.tests import karen_matrices
        self._run_karen_matrix("pv12", karen_matrices.pv12_cases)

    def test_karen_matrix_pv12x(self):
        from tools.tests import karen_matrices
        self._run_karen_matrix("pv12x", karen_matrices.pv12x_cases)

    def test_karen_matrix_r18(self):
        from tools.tests import karen_matrices
        self._run_karen_matrix("r18", karen_matrices.r18_cases)

    def test_karen_matrix_r19(self):
        from tools.tests import karen_matrices
        self._run_karen_matrix("r19", karen_matrices.r19_cases)

    def test_karen_matrix_rs9(self):
        from tools.tests import karen_matrices
        self._run_karen_matrix("rs9", karen_matrices.rs9_cases)

    def test_karen_matrix_replay163(self):
        from tools.tests import karen_matrices
        self._run_karen_matrix("replay163", karen_matrices.replay163_cases)

    def test_karen_certainty_guards(self):
        """Each salvage-certainty guard is load-bearing (Karen M4/M6/M7/M8)."""
        from tools.tests import karen_matrices

        recap, last, slate = karen_matrices.ctx("MIA")

        def salvage(sentence):
            payload = karen_matrices.story(sentence)
            issues = facts.check_review(payload, last, recap, schedule=slate)
            fixed, logs = facts.apply_fact_corrections(
                payload, issues, recap, last, slate
            )
            return facts.edition_text(fixed), issues, logs

        walker_led = "Kansas City had 70 rushing yards, and Walker was the feature back."
        text, issues, logs = salvage(walker_led)
        self.assertIn(walker_led, text)
        self.assertNotIn("Kansas City had 152 rushing yards, and Walker was the feature back.", text)
        self.assertFalse(any("152 rushing yards, and Walker" in log for log in logs))

        ended = "Kansas City ended up with 19 first downs."
        text, issues, logs = salvage(ended)
        self.assertIn(ended, text)
        self.assertTrue(issues)

        carries = "Kansas City had 19 first downs while Walker had 18 carries."
        text, issues, logs = salvage(carries)
        self.assertIn("19 first downs", text)
        self.assertNotIn("Kansas City had 18 first downs while Walker had 18 carries.", text)

        feature = "Kansas City had 18 on a feature-back afternoon."
        direct = facts._correct_sentence(
            feature,
            "pass attempts 18 disagrees with ESPN 24 for Patrick Mahomes ('18')",
            recap,
            last,
        )
        self.assertIsNone(direct)
        self.assertNotEqual(
            "Kansas City had 24 on a feature-back afternoon.",
            direct,
        )

        bare = "The unit had 19 first downs."
        direct = facts._correct_sentence(
            bare,
            "first downs 19 disagrees with ESPN 18 for KC ('19 first downs')",
            recap,
            last,
        )
        self.assertIsNone(direct)

        opening = "Kansas City had 3 first downs on the opening drive."
        text, issues, logs = salvage(opening)
        self.assertIn(opening, text)
        self.assertNotIn("18 first downs on the opening drive", text)

        hunt_alone = "Kansas City had 70 rushing yards from Hunt alone."
        text, issues, logs = salvage(hunt_alone)
        self.assertIn(hunt_alone, text)
        self.assertNotIn("Kansas City had 88 rushing yards from Hunt alone.", text)
        self.assertFalse(any("88 rushing yards from Hunt" in log for log in logs))

        ground = "Kansas City posted 70 on the ground."
        direct = facts._correct_sentence(
            ground,
            "first downs 70 disagrees with ESPN 18 for KC ('70 on the ground')",
            recap,
            last,
        )
        self.assertIsNone(direct)
        self.assertEqual(
            facts._sentence_stat_kind(ground, 70),
            "team rushing",
        )
        self.assertEqual(
            facts._sentence_stat_kind("The Chiefs managed only 70 yards on the ground.", 70),
            "team rushing",
        )

    def test_karen_phrase_binding_must_flag(self):
        from tools.tests import karen_matrices

        cases = (
            ("MIA", "Kansas City had 19 first downs, and Miami spent the day chasing."),
            ("MIA", "Kansas City managed just 70 rushing yards."),
            ("MIA", "Like the preseason, Kansas City never found the end zone in Miami."),
            ("SEA", "Seattle never found the end zone."),
            ("MIA", "Walker scored from the 15 on second-and-goal."),
            ("MIA", "Walker's red-zone touchdown came from the 15."),
            ("MIA", "Kansas City held the ball 34:21 last week against Miami."),
            ("LV5", "Las Vegas held the ball 31:12 last week against Kansas City."),
            ("LV5", "Kansas City held the ball 28:48 last week against Las Vegas."),
            ("MIA", "Kansas City needed only 30 attempts because Walker had 18 carries."),
        )
        for code, sentence in cases:
            recap, last, slate = karen_matrices.ctx(code)
            issues = facts.check_review(
                karen_matrices.story(sentence), last, recap, schedule=slate
            )
            self.assertTrue(issues, f"must flag: {sentence} {issues}")

    def test_karen_scoped_stats_are_not_rewritten(self):
        from tools.tests import karen_matrices

        recap, last, slate = karen_matrices.ctx("MIA")
        for sentence in (
            "Kansas City had 3 first downs on the opening drive.",
            "Kansas City had 20 first downs on its third drive.",
            "Kansas City had 9 first downs in the first half.",
            "Kansas City had 70 rushing yards from Walker alone.",
        ):
            payload = karen_matrices.story(sentence)
            issues = facts.check_review(payload, last, recap, schedule=slate)
            fixed, logs = facts.apply_fact_corrections(
                payload, issues, recap, last, slate
            )
            self.assertEqual(
                fixed["storyline"]["body"][0],
                sentence,
                logs,
            )

    def test_karen_night_entire_24_and_complementary(self):
        from tools.tests import karen_matrices
        from tools.tests.archive_replay import salvage_all_editions

        recap, last, slate = karen_matrices.ctx("MIA")
        true_line = (
            "It is not a winning offensive identity if Walker is at 3.9 a carry "
            "and the unit is 3-of-7 on third down."
        )
        issues = facts.check_review(
            karen_matrices.story(
                "Mahomes at 20-of-24 with a 119.8 passer rating is a winning "
                "quarterback night. " + true_line
            ),
            last,
            recap,
            schedule=slate,
        )
        blob = " ".join(issues)
        self.assertNotIn("night", blob.lower())
        self.assertFalse(
            any("3.9" in item for item in issues),
            issues,
        )

        entire = "Those two scores were the entire 24 in Miami."
        issues = facts.check_review(
            karen_matrices.story(entire), last, recap, schedule=slate
        )
        self.assertTrue(any("entire" in item for item in issues), issues)

        complementary = (
            "That is complementary football by takeaway, not by first-down defense."
        )
        row = next(
            salvage
            for slug, salvage in salvage_all_editions()
            if slug.startswith("2026-09-28-0010")
        )
        self.assertIn(complementary, row["after"])
        self.assertNotIn(complementary, row["drops"])

    def test_scope_lookback_stays_inside_sentence(self):
        from tools.tests import karen_matrices

        lede_1806 = _load_fixture("edition_1806_storyline_lede.json")[
            "storyline_body_0"
        ]
        lv_prefixes = _load_fixture("edition_lv_review_scope_lede.json")
        injected = (
            "Kansas City managed just 70 rushing yards.",
            "Kansas City had 19 first downs.",
            "Kansas City had 20 first downs on its third drive.",
            "Kansas City held the ball 34:21.",
        )
        cases = (
            ("MIA", lede_1806),
            ("MIA", lv_prefixes["las_vegas_circle"]),
            ("LV5", lv_prefixes["chargers_circle"]),
            ("LV5", lv_prefixes["los_angeles_watching"]),
            ("LV5", lv_prefixes["las_vegas_circle"]),
        )
        for code, prefix in cases:
            recap, last, slate = karen_matrices.ctx(code)
            for line in injected:
                sentence = f"{prefix} {line}"
                issues = facts.check_review(
                    karen_matrices.story(sentence), last, recap, schedule=slate
                )
                self.assertTrue(
                    issues,
                    f"{code} must flag after {prefix!r}: {line} {issues}",
                )

    def test_review_binds_last_game_only(self):
        from tools.tests import karen_matrices

        recap, last, slate = karen_matrices.ctx("LV5")
        must_flag = (
            "Kansas City held the ball 25:39.",
            "Kansas City ran for 88 yards.",
            "Walker ran for 70 yards.",
            "Kansas City beat the Raiders 31-3.",
            "Kansas City won 24-10 in Las Vegas.",
            "Kansas City ended 24-10.",
        )
        for sentence in must_flag:
            issues = facts.check_review(
                karen_matrices.story(sentence), last, recap, schedule=slate
            )
            self.assertTrue(issues, f"must flag in LV review: {sentence} {issues}")

        colts = "the Colts game's 152 rushing yards and 37:00 still sit on that box."
        issues = facts.check_review(
            karen_matrices.story(colts), last, recap, schedule=slate
        )
        self.assertFalse(issues, f"named older game must pass: {issues}")

        for sentence in (
            "Kansas City beat the Colts 33-30 in overtime.",
            "The Colts game ended 33-30.",
            "Kansas City beat Indianapolis 33-30 in Week 2.",
        ):
            issues = facts.check_review(
                karen_matrices.story(sentence), last, recap, schedule=slate
            )
            self.assertFalse(issues, f"named Colts final must pass: {sentence} {issues}")

        two_tds = "Kansas City scored 2 touchdowns."
        issues = facts.check_review(
            karen_matrices.story(two_tds), last, recap, schedule=slate
        )
        self.assertTrue(issues, f"must flag wrong TD count: {two_tds} {issues}")

        lv_finals = (
            "Kansas City won 24-10 in Las Vegas.",
            "Kansas City beat Las Vegas 24-10.",
            "The final was 14-10.",
            "Kansas City won 31-3.",
        )
        for sentence in lv_finals:
            issues = facts.check_review(
                karen_matrices.story(sentence), last, recap, schedule=slate
            )
            self.assertTrue(issues, f"must flag LV final: {sentence} {issues}")
        led = "Kansas City led 14-10 at halftime."
        issues = facts.check_review(
            karen_matrices.story(led), last, recap, schedule=slate
        )
        self.assertTrue(issues, f"must flag wrong LV halftime: {led} {issues}")
        led_ok = "Kansas City led 17-7 at the half."
        issues = facts.check_review(
            karen_matrices.story(led_ok), last, recap, schedule=slate
        )
        self.assertFalse(issues, f"real LV halftime must pass: {issues}")

        recap, last, slate = karen_matrices.ctx("MIA")
        for sentence in (
            "Kansas City beat the Colts 33-30 in overtime.",
            "The Colts game ended 33-30.",
            "Kansas City beat Indianapolis 33-30 in Week 2.",
        ):
            issues = facts.check_review(
                karen_matrices.story(sentence), last, recap, schedule=slate
            )
            self.assertFalse(
                issues, f"named Colts final must pass in Preview: {sentence} {issues}"
            )
        for sentence in (
            "Walker plunged in from the 15.",
            "Walker ran it in from the 15.",
            "Walker went in from the 15.",
            "Walker found the end zone from the 15.",
            "Walker crossed the goal line from the 15.",
            "Walker capped the drive from the 15.",
            "Walker walked in from the 15.",
        ):
            issues = facts.check_review(
                karen_matrices.story(sentence), last, recap, schedule=slate
            )
            self.assertTrue(issues, f"must flag TD-from-the-N: {sentence} {issues}")
        mia_flag = (
            "The Chiefs managed only 70 yards on the ground.",
            "Walker punched it in from the 15 on second-and-goal.",
            "Mahomes needed only 18 attempts because Walker had 18 carries.",
            "Those two scores were all of Kansas City's 24 in Miami.",
            "The Chiefs' prime-time night in Miami ended 24-10.",
            "If you want the tape, Walker scored from the 15.",
        )
        for sentence in mia_flag:
            issues = facts.check_review(
                karen_matrices.story(sentence), last, recap, schedule=slate
            )
            self.assertTrue(issues, f"must flag: {sentence} {issues}")

        true_from_walker = "Kansas City got 70 rushing yards from Walker."
        issues = facts.check_review(
            karen_matrices.story(true_from_walker), last, recap, schedule=slate
        )
        self.assertFalse(issues, issues)

    def test_scoped_rewrite_is_generic_and_touches_hold(self):
        from tools.tests import karen_matrices

        recap, last, slate = karen_matrices.ctx("MIA")
        for sentence in (
            "Kansas City had 3 first downs on its opening possession.",
            "Kansas City had 6 first downs before halftime.",
            "Kansas City had 9 first downs after the break.",
            "Walker had 25 touches.",
        ):
            payload = karen_matrices.story(sentence)
            issues = facts.check_review(payload, last, recap, schedule=slate)
            fixed, logs = facts.apply_fact_corrections(
                payload, issues, recap, last, slate
            )
            self.assertEqual(
                fixed["storyline"]["body"][0],
                sentence,
                logs,
            )
            leftover = facts.check_review(fixed, last, recap, schedule=slate)
            self.assertTrue(leftover, f"must hold: {sentence} {leftover}")

    def test_review_mentions_bye_instead_of_this_week(self):
        from tools.tests import karen_matrices

        slate = copy.deepcopy(collect.load_cached_schedule())
        for game in slate:
            if game.get("id") == "401872976":
                game["completed"] = True
                game["kcScore"] = 27
                game["oppScore"] = 17
        now = datetime(2026, 10, 5, 9, 37, tzinfo=timezone.utc)
        with patch.object(config, "now_utc", return_value=now):
            ph = phase.detect(slate, now=now)
            upcoming = phase.next_games(slate, now=now)
            raw = offline.write(
                {
                    "schedule": slate,
                    "lastGameRecap": karen_matrices.lv5_box(),
                    "news": [],
                    "markets": {},
                },
                ph,
                upcoming,
            )
        blob = json.dumps(raw)
        self.assertIn("Week 5 is a bye", blob)
        self.assertNotIn("this week", blob.lower())

    def test_karen_r6_r10_corpus(self):
        """Pinned r6–r11 must-pass / must-flag sentences."""
        fixtures = {
            "157": _load_fixture("edition_run_karen_157_gaps.json"),
            "161": _load_fixture("edition_run_karen_161_short_aliases.json"),
            "162": _load_fixture("edition_run_karen_162_clock_phase.json"),
            "164": _load_fixture("edition_run_karen_164_rate_week.json"),
            "165": _load_fixture("edition_run_karen_165_possession.json"),
            "166": _load_fixture("edition_run_karen_166_regression.json"),
        }
        corpus = _load_fixture("edition_run_karen_r6_r10_corpus.json")

        def _lede(sentence, last):
            kc = last.get("kcScore")
            opp = last.get("oppScore")
            score = (
                f"KC {kc}–{opp}"
                if kc is not None and opp is not None
                else "KC 24–10"
            )
            return {
                "phase": {"type": "regular"},
                "lastGameReview": {
                    "opponent": last.get("opponent") or "Miami Dolphins",
                    "result": "W",
                    "score": score,
                    "lede": "Kansas City won.",
                    "whatWorked": [sentence],
                },
            }

        for case in corpus["must_pass"]:
            sentence = fixtures[case["src"]][case["key"]]
            last, recap, slate = self._karen_ctx(case["ctx"])
            issues = facts.check_review(
                _lede(sentence, last),
                last,
                recap,
                schedule=slate,
            )
            self.assertFalse(
                issues,
                f"must-pass {case['src']}:{case['key']} {issues}",
            )
            if case["src"] == "166" and case["key"] in {
                "lv_averaged_coming_in",
                "lv_averaged_18_coming_in",
            }:
                payload = _lede(sentence, last)
                corrected, logs = facts.apply_fact_corrections(
                    payload, issues, recap, last, slate
                )
                self.assertEqual(
                    corrected["lastGameReview"]["whatWorked"][0],
                    sentence,
                    logs,
                )

        for case in corpus["must_flag"]:
            sentence = fixtures[case["src"]][case["key"]]
            last, recap, slate = self._karen_ctx(case["ctx"])
            issues = facts.check_review(
                _lede(sentence, last),
                last,
                recap,
                schedule=slate,
            )
            blob = " ".join(issues)
            self.assertTrue(
                issues,
                f"must-flag {case['src']}:{case['key']} was clean",
            )
            self.assertIn(
                case["needle"],
                blob,
                f"must-flag {case['src']}:{case['key']} {issues}",
            )

        r10 = fixtures["166"]
        last_mia, recap_mia, slate = self._karen_ctx("mia")
        for sentence in (
            r10["held_it_compound"],
            r10["held_elliptical"],
            r10["held_it_for"],
            "Miami held the ball 34:21 and Kansas City held it 28:55.",
            "Kansas City held it for 28:55.",
        ):
            issues = facts.check_review(
                _lede(sentence, last_mia),
                last_mia,
                recap_mia,
                schedule=slate,
            )
            self.assertTrue(
                any("28:55" in item for item in issues),
                (sentence, issues),
            )


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
        # archive_replay.py is the r10 regression net: it walks the frozen
        # published editions against cached ESPN boxes, not today's draft.
        skip = {"archive_replay.py"}
        for path in tests_dir.rglob("*.py"):
            if path.name in skip:
                continue
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
        self.assertNotIn("desk", raw["dek"].lower())
        self.assertIn("the Denver Broncos", raw["dek"])


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


class Week3MiamiWeek4Raiders(unittest.TestCase):
    """Regression for the 2026-09-29 offline edition (KC 24-10 MIA, next @ LV)."""

    LAST = {
        "id": "401872952",
        "week": 3,
        "seasonType": "reg",
        "date": "2026-09-27T17:00:00Z",
        "opponent": "Miami Dolphins",
        "opponentAbbr": "MIA",
        "opponentShort": "Dolphins",
        "homeAway": "away",
        "venue": "Hard Rock Stadium",
        "tv": "CBS",
        "completed": True,
        "inProgress": False,
        "kcScore": 24,
        "oppScore": 10,
        "kickoff": "Sun, Sep 27 · 12:00 PM CT",
    }
    NEXT_GAME = {
        "id": "401872976",
        "week": 4,
        "seasonType": "reg",
        "date": "2026-10-04T20:25:00Z",
        "opponent": "Las Vegas Raiders",
        "opponentAbbr": "LV",
        "opponentShort": "Raiders",
        "homeAway": "away",
        "venue": "Allegiant Stadium",
        "tv": "CBS",
        "completed": False,
        "inProgress": False,
        "kcScore": None,
        "oppScore": None,
        "kickoff": "Sun, Oct 4 · 3:25 PM CT",
    }
    WEEK1 = {
        "id": "401872931",
        "week": 1,
        "seasonType": "reg",
        "date": "2026-09-15T00:15:00Z",
        "opponent": "Denver Broncos",
        "opponentAbbr": "DEN",
        "opponentShort": "Broncos",
        "homeAway": "home",
        "venue": "Arrowhead Stadium",
        "completed": True,
        "kcScore": 31,
        "oppScore": 10,
        "kickoff": "Mon, Sep 14 · 7:15 PM CT",
    }
    WEEK2 = {
        "id": "401872945",
        "week": 2,
        "seasonType": "reg",
        "date": "2026-09-21T00:20:00Z",
        "opponent": "Indianapolis Colts",
        "opponentAbbr": "IND",
        "opponentShort": "Colts",
        "homeAway": "home",
        "venue": "Arrowhead Stadium",
        "completed": True,
        "kcScore": 33,
        "oppScore": 30,
        "kickoff": "Sun, Sep 20 · 7:20 PM CT",
    }
    PHASE = {
        "type": "regular",
        "label": "Week 4",
        "week": 4,
        "mode": "review",
        "edition": "2026 Week 4 · Week 3 Review",
        "lastGame": LAST,
        "nextGame": NEXT_GAME,
    }

    def _schedule(self):
        return [self.WEEK1, self.WEEK2, self.LAST, self.NEXT_GAME]

    def _signals(self):
        return {
            "news": [
                {
                    "title": "NFL Power Rankings Week 4 Roundup: Chiefs’ hot start",
                    "summary": "Kansas City is 3-0 after Miami.",
                    "publisher": "Arrowhead Pride",
                    "url": "https://example.com/rankings",
                }
            ],
            "markets": {
                "model": {"label": "FPI", "kcWin": 70.0, "source": "ESPN"},
                "vegas": {
                    "spreadDetail": "KC -4.5",
                    "provider": "DraftKings",
                    "overUnder": 47.5,
                    "source": "ESPN",
                },
                "futures": [
                    {"label": "Super Bowl champion", "chiefsPct": 8.6},
                ],
            },
            "schedule": self._schedule(),
            "lastGameRecap": _load_fixture("espn_401872952_recap.json"),
        }

    def _offline(self):
        return offline.write(self._signals(), self.PHASE, [self.NEXT_GAME])

    def _normalized(self, raw=None, record="3-0"):
        return schema.normalize(
            raw or self._offline(),
            phase=self.PHASE,
            meta={
                "generatedAt": "2026-09-29T17:34:02+00:00",
                "generator": "offline",
                "record": record,
                "markets": self._signals()["markets"],
            },
        )

    def test_slate_record_is_3_0_not_last_season(self):
        self.assertEqual(phase.slate_record(self._schedule(), "reg"), "3-0")
        self.assertEqual(phase.current_record(self._schedule(), self.PHASE), "3-0")
        self.assertNotEqual(phase.current_record(self._schedule(), self.PHASE), "6-11")

    def test_schema_does_not_default_to_last_season_record(self):
        narrative = schema.normalize(
            {"headline": "x"},
            phase=self.PHASE,
            meta={"generatedAt": "2026-09-29T17:34:02+00:00", "generator": "test"},
        )
        self.assertEqual(narrative["record"], "")

    def test_offline_and_meta_record_are_3_0(self):
        raw = self._offline()
        self.assertEqual(raw["record"], "3-0")
        narrative = self._normalized(raw)
        self.assertEqual(narrative["record"], "3-0")
        self.assertEqual(narrative["currentState"]["record"], "3-0")

    def test_prompt_uses_current_slate_record(self):
        text = prompts.build_user_prompt(self._signals(), self.PHASE, [self.NEXT_GAME])
        self.assertIn("CURRENT 2026 slate record: 3-0", text)
        self.assertNotIn("2025 record 6-11", text)
        self.assertIn("history, not the live record", text)
        self.assertIn('"record": "3-0"', text)

    def test_copy_gates_fail_stale_record_august_and_thin_offline(self):
        narrative = self._normalized()
        narrative["record"] = "6-11"
        issues = facts.check_review(
            narrative, self.LAST, self._signals()["lastGameRecap"],
            schedule=self._schedule(), copy_gates=True,
        )
        self.assertTrue(any("schedule record 3-0" in item for item in issues), issues)

        thin = {
            "generator": "offline",
            "phase": {"type": "regular", "label": "Week 4"},
            "record": "3-0",
            "headline": "Thin",
            "dek": "A win over Miami Dolphins is still a win — even in August — leftover.",
            "storyline": {"lede": "preseason vibes in the regular season", "body": []},
        }
        stale = facts.check_copy_gates(thin, self._schedule())
        self.assertTrue(any("August" in item for item in stale), stale)
        self.assertTrue(any("preseason" in item for item in stale), stale)
        self.assertTrue(any("floor" in item for item in stale), stale)
        compare = {
            "generator": "grok",
            "phase": {"type": "regular", "label": "Week 4"},
            "record": "3-0",
            "headline": "Road week",
            "dek": "Cousins has been better than the preseason script.",
            "storyline": {
                "lede": "Las Vegas is playing ahead of the preseason forecast.",
                "body": ["Cousins is the problem the preseason did not advertise."],
            },
        }
        self.assertEqual(facts.check_copy_gates(compare, self._schedule()), [])

        season = dict(thin)
        season["dek"] = "The 2025 Chiefs are still rolling."
        season["storyline"] = {"lede": "looks most 2025 on third down", "body": []}
        season_issues = facts.check_copy_gates(season, self._schedule())
        self.assertTrue(
            any("2025" in item for item in season_issues), season_issues
        )

    def test_copy_gates_fail_duplicated_paragraphs(self):
        blob = (
            "The matchup is a style question first: can Kansas City stay on schedule "
            "against what the Las Vegas Raiders do best, and can Steve Spagnuolo make "
            "the Las Vegas Raiders play left-handed? The market sits in the edge line."
        )
        narrative = {
            "generator": "offline",
            "phase": {"type": "regular"},
            "record": "3-0",
            "storyline": {"lede": blob, "body": [blob]},
            "gamePlan": {"howTheyMatch": blob},
            "headline": "x " * 400,
            "dek": "y " * 400,
        }
        issues = facts.check_copy_gates(narrative, self._schedule())
        self.assertTrue(any("duplicated paragraph" in item for item in issues), issues)

    def test_offline_week3_copy_and_box_facts(self):
        raw = self._offline()
        narrative = self._normalized(raw)
        text = facts.edition_text(narrative)
        self.assertGreaterEqual(facts.edition_word_count(narrative), facts.OFFLINE_WORD_FLOOR)
        self.assertNotIn("even in August", text)
        self.assertNotIn("looks most 2025", text)
        self.assertNotIn("Whatever lost (or nearly lost)", text)
        self.assertNotIn("turnover(s)", text)
        self.assertNotIn("Mahomes (or the backup)", text)
        self.assertNotIn("Wednesday-night keeper", text)
        self.assertNotIn("Raiders's", text)
        self.assertNotIn("15-play openers", text)
        self.assertIn("15-play opening script", text)
        self.assertIn("the Miami Dolphins", text)
        self.assertIn("the Las Vegas Raiders", text)
        self.assertNotIn("desk", narrative["headline"].lower())
        self.assertNotIn("desk", narrative["dek"].lower())
        self.assertNotIn("Let's get into it", narrative["videoHook"])
        self.assertIn("1 turnover", text)
        self.assertIn("25 rushing attempts", text)
        self.assertIn("88", text)
        self.assertIn("first downs KC 18", text)
        self.assertIn("drives KC 9", text)
        self.assertIn("25:39", text)
        self.assertIn("rating 119.8", text)
        self.assertIn("20 touches", text)
        self.assertNotIn("1 touches", text)
        self.assertNotIn("6 touches", text)
        self.assertTrue(
            "QB hits" in text and ("5" in text),
            "expected Miami's 5 QB hits in the box copy",
        )
        nxt = narrative["nextGame"]
        self.assertIn("Las Vegas", nxt.get("opponent") or "")
        self.assertIn("Week 4", nxt.get("label") or "")
        self.assertIn("Oct 4", nxt.get("label") or "")
        generate._ensure_desk_sections(
            narrative, self._signals(), self.PHASE, [self.NEXT_GAME]
        )
        self.assertIn("Allegiant", narrative["nextGame"].get("at") or "")
        personnel = " ".join(
            f"{row.get('move')} {row.get('detail')}"
            for row in narrative.get("personnel") or []
        )
        self.assertNotIn("Roster watch", personnel)
        self.assertNotIn(
            "Transactions and depth-chart moves that shape the plan", personnel
        )
        edges = {row.get("edge") for row in narrative.get("matchups") or []}
        self.assertNotEqual(edges, {"PUSH"})
        story = " ".join(narrative["storyline"].get("body") or [])
        review_lede = narrative["lastGameReview"]["lede"]
        self.assertNotIn(review_lede, story)
        issues = facts.check_review(
            narrative, self.LAST, self._signals()["lastGameRecap"],
            schedule=self._schedule(), copy_gates=True,
        )
        self.assertEqual(issues, [])

    def test_generate_forces_schedule_record_for_both_providers(self):
        raw = self._offline()
        raw["record"] = "6-11"
        llm = Mock(return_value=(raw, "grok"))
        with tempfile.TemporaryDirectory() as tmp:
            editions = Path(tmp) / "editions"
            editions.mkdir()
            archive = Path(tmp) / "archive.json"
            archive.write_text("[]\n", encoding="utf-8")
            with patch.object(
                collect, "collect_all",
                return_value={"schedule": self._schedule(), "news": []},
            ), patch.object(phase, "detect", return_value=self.PHASE), patch.object(
                phase, "next_games", return_value=[self.NEXT_GAME]
            ), patch.object(
                collect, "fetch_game_recap",
                side_effect=lambda event_id: (
                    self._signals()["lastGameRecap"]
                    if str(event_id) == "401872952"
                    else {}
                ),
            ), patch.object(
                odds, "collect_markets", return_value=self._signals()["markets"]
            ), patch.object(
                providers, "generate_via_llm", llm
            ), patch.object(
                generate, "_render_diagrams"
            ), patch.object(
                generate, "_write_schedule"
            ), patch.object(
                generate.config, "ARCHIVE_JSON", archive
            ), patch.object(
                generate.config, "EDITIONS_DIR", editions
            ), patch.object(
                generate, "_load_recent_editions", return_value=[]
            ), patch.object(
                phase, "any_live", return_value=False
            ):
                grok = generate.build("grok", persist_schedule=False)
                offline_run = generate.build("offline", persist_schedule=False)
        self.assertEqual(grok["narrative"]["record"], "3-0")
        self.assertEqual(offline_run["narrative"]["record"], "3-0")
        self.assertEqual(offline_run["narrative"]["generator"], "offline")

    def test_in_season_empty_schedule_fails_loudly(self):
        with patch.object(
            collect, "collect_all", return_value={"schedule": [], "news": []}
        ), patch.object(phase, "detect", return_value=self.PHASE), patch.object(
            phase, "next_games", return_value=[self.NEXT_GAME]
        ), patch.object(
            collect, "fetch_game_recap", return_value={}
        ), patch.object(odds, "collect_markets", return_value={}), patch.object(
            generate, "_render_diagrams"
        ):
            with self.assertRaises(generate.FactCheckError) as ctx:
                generate.build("offline", persist_schedule=False)
        self.assertIn("last-season fallback", str(ctx.exception))

    def test_fetch_recap_keeps_rushing_drives_and_rating(self):
        payload = {
            "boxscore": {
                "teams": [
                    {
                        "team": {"abbreviation": "KC"},
                        "statistics": [
                            {"name": "rushingAttempts", "displayValue": "25"},
                            {"name": "rushingYards", "displayValue": "88"},
                            {"name": "rushingTouchdowns", "displayValue": "1"},
                            {"name": "firstDowns", "displayValue": "18"},
                            {"name": "possessionTime", "displayValue": "25:39"},
                            {"name": "totalDrives", "displayValue": "9"},
                            {"name": "sacks", "displayValue": "0-0"},
                        ],
                    },
                    {
                        "team": {"abbreviation": "MIA"},
                        "statistics": [
                            {"name": "rushingAttempts", "displayValue": "31"},
                            {"name": "rushingYards", "displayValue": "119"},
                        ],
                    },
                ],
                "players": [
                    {
                        "team": {"abbreviation": "KC"},
                        "statistics": [
                            {
                                "name": "passing",
                                "keys": [
                                    "completions/passingAttempts",
                                    "passingYards",
                                    "QBRating",
                                    "sacks-sackYardsLost",
                                ],
                                "athletes": [
                                    {
                                        "athlete": {"displayName": "Patrick Mahomes"},
                                        "stats": ["20/24", "246", "119.8", "0-0"],
                                    }
                                ],
                            }
                        ],
                    }
                ],
            },
            "scoringPlays": [],
            "drives": {
                "previous": [
                    {"team": {"abbreviation": "KC"}, "result": "TD", "plays": []},
                    {"team": {"abbreviation": "MIA"}, "result": "PUNT", "plays": []},
                    {"team": {"abbreviation": "KC"}, "result": "PUNT", "plays": []},
                ]
            },
        }
        with patch.object(collect, "_get_json", return_value=payload):
            recap = collect.fetch_game_recap("401872952")
        self.assertEqual(recap["kc"]["rushingAttempts"], "25")
        self.assertEqual(recap["kc"]["rushingYards"], "88")
        self.assertEqual(recap["kc"]["firstDowns"], "18")
        self.assertEqual(recap["kc"]["totalDrives"], "9")
        self.assertEqual(recap["drives"]["KC"], 2)
        self.assertEqual(recap["drives"]["OPP"], 1)
        self.assertEqual(recap["passing"][0]["rating"], "119.8")
        self.assertEqual(recap["passing"][0]["sacks"], 0)
