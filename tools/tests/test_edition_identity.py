"""Hermetic slug, archive, and Chargers-review salvage gates.

Keep these out of test_narrative.py so they do not collide with other
agents editing that file. Live data/schedule_2026.json is never read.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from tools.chiefs_narrative import edition_overlay, facts, generate, odds
from tools.tests.frozen_slate import frozen_schedule

FIXTURES = Path(__file__).resolve().parent / "fixtures"

LAST_LAC = {
    "id": "401873006",
    "week": 6,
    "seasonType": "reg",
    "opponent": "Los Angeles Chargers",
    "opponentAbbr": "LAC",
    "opponentShort": "Chargers",
    "completed": True,
    "inProgress": False,
    "kcScore": 22,
    "oppScore": 6,
    "date": "2026-10-18T20:25:00Z",
}

SCORE_SEQUENCE = (
    "Walker's 2-yard touchdown at Q2 1:49 made it 16-3, Butker's 50-yard "
    "field goal at Q2 0:02 made it 19-3, a Los Angeles 30-yard field goal "
    "at Q3 10:44 made it 19-6, and a Kansas City 30-yard field goal at "
    "Q4 3:52 closed it at 22-6."
)

FOUR_SACKS = (
    "Justin Herbert went 22-of-37 with four sacks, a 63.3 rating, and "
    "nine quarterback hits from Kansas City."
)
FOUR_SACKS_TAKEAWAY = (
    "Four sacks and nine hits turned Herbert into a 63.3 rating."
)


def _load_lac_recap() -> dict:
    return json.loads(
        (FIXTURES / "espn_401873006_recap.json").read_text(encoding="utf-8")
    )


def _review(*sentences: str) -> dict:
    return {
        "phase": {"type": "regular", "mode": "review", "week": 6},
        "edition": "2026 Week 6 · Review",
        "lastGameReview": {
            "opponent": "Los Angeles Chargers",
            "result": "W",
            "score": "KC 22–6",
            "lede": " ".join(sentences),
            "whatWorked": [FOUR_SACKS_TAKEAWAY],
        },
    }


class FrozenSlateTests(unittest.TestCase):
    def test_frozen_slate_is_pre_chargers_final(self):
        slate = frozen_schedule()
        lac = next(game for game in slate if str(game.get("id")) == "401873006")
        self.assertFalse(lac.get("completed"))
        self.assertIsNone(lac.get("kcScore"))
        lv = next(game for game in slate if str(game.get("id")) == "401872976")
        self.assertTrue(lv.get("completed"))
        self.assertEqual(lv.get("kcScore"), 30)


class UniqueEditionSlugTests(unittest.TestCase):
    def test_mint_bumps_when_edition_file_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            editions = root / "editions"
            images = root / "images"
            editions.mkdir()
            images.mkdir()
            (editions / "2026-10-18-1341.json").write_text("{}\n", encoding="utf-8")
            narrative = {"generatedAt": "2026-10-18T13:41:11+00:00"}
            with patch.object(generate.config, "EDITIONS_DIR", editions), patch.object(
                generate.config, "DIAGRAM_DIR", images
            ):
                slug = generate._mint_unique_slug(narrative)
            self.assertEqual(slug, "2026-10-18-1342")
            self.assertEqual(narrative["slug"], "2026-10-18-1342")

    def test_mint_bumps_when_image_dir_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            editions = root / "editions"
            images = root / "images"
            editions.mkdir()
            taken = images / "2026-10-18-1341"
            taken.mkdir(parents=True)
            (taken / "xo-mesh.svg").write_text("<svg/>\n", encoding="utf-8")
            narrative = {"generatedAt": "2026-10-18T13:41:11+00:00"}
            with patch.object(generate.config, "EDITIONS_DIR", editions), patch.object(
                generate.config, "DIAGRAM_DIR", images
            ):
                slug = generate._mint_unique_slug(narrative)
            self.assertEqual(slug, "2026-10-18-1342")

    def test_write_archive_keeps_a_different_same_day_edition(self):
        preview = {
            "generatedAt": "2026-10-18T13:41:11+00:00",
            "slug": "2026-10-18-1341",
            "edition": "2026 Week 6 · Preview",
            "phase": {"label": "Week 6"},
            "headline": "Four Raiders field goals, 10 hits, and a 30-27 escape tape",
            "theEdge": "preview edge",
        }
        review = {
            "generatedAt": "2026-10-19T02:03:02+00:00",
            "slug": "2026-10-19-0203",
            "edition": "2026 Week 6 · Review",
            "phase": {"label": "Week 6"},
            "headline": "422 yards cashed 22. Seattle gets the underdog tape.",
            "theEdge": "review edge",
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "archive.json"
            path.write_text(json.dumps([preview]) + "\n", encoding="utf-8")
            with patch.object(generate.config, "ARCHIVE_JSON", path):
                generate._write_archive(review)
            saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual([row["slug"] for row in saved], ["2026-10-19-0203", "2026-10-18-1341"])
        self.assertEqual(saved[1]["headline"], preview["headline"])
        self.assertEqual(saved[0]["headline"], review["headline"])

    def test_write_archive_replaces_only_the_same_slug(self):
        first = {
            "generatedAt": "2026-10-19T02:03:02+00:00",
            "slug": "2026-10-19-0203",
            "edition": "2026 Week 6 · Review",
            "phase": {"label": "Week 6"},
            "headline": "first draft",
        }
        second = dict(first)
        second["headline"] = "revised draft"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "archive.json"
            path.write_text("[]\n", encoding="utf-8")
            with patch.object(generate.config, "ARCHIVE_JSON", path):
                generate._write_archive(first)
                generate._write_archive(second)
            saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["headline"], "revised draft")

    def test_write_archive_does_not_drop_older_editions(self):
        """A 30-row slice dropped 2026-09-07-1631 and 2026-09-06-1417 (#244)."""
        rows = []
        for day in range(6, 36):
            rows.append(
                {
                    "generatedAt": f"2026-09-{day:02d}T12:00:00+00:00",
                    "slug": f"2026-09-{day:02d}-1200",
                    "edition": "2026 Week 1",
                    "phase": "Week 1",
                    "headline": f"day {day}",
                    "theEdge": "edge",
                }
            )
        rows[0]["slug"] = "2026-09-06-1417"
        rows[1]["slug"] = "2026-09-07-1631"
        newest = {
            "generatedAt": "2026-10-19T21:56:11+00:00",
            "slug": "2026-10-19-2156",
            "edition": "2026 Week 7 · Preview",
            "phase": {"label": "Week 7"},
            "headline": "Walker scored both. Seattle still lays 2.5 at Lumen Field.",
            "theEdge": "edge",
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "archive.json"
            path.write_text(json.dumps(rows) + "\n", encoding="utf-8")
            with patch.object(generate.config, "ARCHIVE_JSON", path):
                generate._write_archive(newest)
            saved = json.loads(path.read_text(encoding="utf-8"))
        slugs = [row["slug"] for row in saved]
        self.assertEqual(len(saved), 31)
        self.assertEqual(slugs[0], "2026-10-19-2156")
        self.assertIn("2026-09-07-1631", slugs)
        self.assertIn("2026-09-06-1417", slugs)
        self.assertEqual(generate._keep_archive_rows(saved), saved)

    def test_keep_archive_rows_does_not_prune(self):
        rows = [{"slug": f"s{i}"} for i in range(32)]
        kept = generate._keep_archive_rows(rows)
        self.assertIs(kept, rows)
        self.assertEqual(len(kept), 32)
        self.assertNotIn("Bound", generate._keep_archive_rows.__doc__ or "")
        self.assertIn("Unlimited growth is intended for now", generate._keep_archive_rows.__doc__ or "")


class OddsSourceTests(unittest.TestCase):
    def test_game_card_source_splits_fpi_and_draftkings_spread(self):
        """SEA -2.5 is the DraftKings spread, not an FPI projection."""
        card = odds.build_game_card(
            {
                "opponent": "Seattle Seahawks",
                "id": "401873022",
                "homeAway": "away",
                "kickoff": "Sun, Oct 25 · 7:20 PM CT",
            },
            {
                "model": {"kcWin": 52.2, "oppWin": 47.4},
                "vegas": {
                    "spreadDetail": "SEA -2.5",
                    "overUnder": 42.5,
                    "source": "ESPN / DraftKings",
                },
            },
        )
        self.assertIsNotNone(card)
        self.assertEqual(
            card["source"],
            "ESPN FPI win probability; ESPN / DraftKings spread",
        )
        self.assertEqual(card["spreadDetail"], "SEA -2.5")
        self.assertEqual(card["overUnder"], 42.5)
        self.assertEqual(card["kcWin"], 52.2)


class ChargersReviewMustPassTests(unittest.TestCase):
    """Run 39187378943 salvage dropped a true scoring sequence and held Four sacks."""

    def test_scoring_sequence_must_pass_and_must_not_drop(self):
        recap = _load_lac_recap()
        slate = frozen_schedule()
        payload = _review(SCORE_SEQUENCE)
        issues = facts.check_review(payload, LAST_LAC, recap, schedule=slate)
        self.assertEqual(issues, [], issues)
        fixed, logs = facts.apply_fact_corrections(
            payload, issues, recap, LAST_LAC, slate
        )
        leftover = facts.check_review(fixed, LAST_LAC, recap, schedule=slate)
        repaired = (
            facts.repair_offending_copy(fixed, leftover, LAST_LAC, recap)
            if leftover
            else fixed
        )
        blob = facts.edition_text(repaired)
        self.assertIn("16-3", blob)
        self.assertIn("19-3", blob)
        self.assertIn("19-6", blob)
        self.assertIn("22-6", blob)
        self.assertNotIn(SCORE_SEQUENCE, facts.dropped_sentences(payload, repaired))
        self.assertFalse(any("ambiguous snippet" in line for line in logs), logs)

    def test_four_sacks_must_pass_and_must_not_hold_ambiguous(self):
        recap = _load_lac_recap()
        slate = frozen_schedule()
        payload = _review(FOUR_SACKS, FOUR_SACKS_TAKEAWAY)
        issues = facts.check_review(payload, LAST_LAC, recap, schedule=slate)
        self.assertEqual(issues, [], issues)
        fixed, logs = facts.apply_fact_corrections(
            payload, issues, recap, LAST_LAC, slate
        )
        leftover = facts.check_review(fixed, LAST_LAC, recap, schedule=slate)
        self.assertEqual(leftover, [], leftover)
        self.assertFalse(
            any("ambiguous snippet 'Four sacks'" in line for line in logs),
            logs,
        )
        blob = facts.edition_text(fixed)
        self.assertIn("four sacks", blob.lower())


class EditionAllowListTests(unittest.TestCase):
    def test_narrative_workflow_git_add_is_edition_allowlisted(self):
        """The daily update job may only stage overlay-allow-listed paths."""
        workflow = (
            Path(__file__).resolve().parents[2]
            / ".github"
            / "workflows"
            / "narrative.yml"
        ).read_text(encoding="utf-8")
        parsed = yaml.safe_load(workflow)
        live = "narrative"
        staged = {
            f"data/{live}.json",
            f"data/{live}_archive.json",
            f"data/{live}_repair.json",
            f"data/{live}_editions",
            "data/wire.json",
            "data/schedule_2026.json",
            f"public/images/{live}",
        }
        dir_ok = {f"data/{live}_editions", f"public/images/{live}"}
        for path in staged:
            self.assertIn(path, workflow)
            posix = Path(path)
            self.assertTrue(
                edition_overlay.is_allowed_rel(posix) or path in dir_ok,
                path,
            )
        update = ((parsed.get("jobs") or {}).get("update") or {}).get("steps") or []
        joined = "\n".join(
            str(step.get("run") or "") for step in update if isinstance(step, dict)
        )
        self.assertIn(f"data/{live}_repair.json", joined)


if __name__ == "__main__":
    unittest.main()
