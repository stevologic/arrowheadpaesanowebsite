"""A14 / B2: render-order lede through real generate.main.

Frozen ids match FROZEN-7 A14 (Kenny 8:03 AM MST Nov 13 and 8:05 AM ack).
Each row goes through generate.main. Tests do not call copy_touches /
lede_paragraph / should_hold_automerge, do not force headlineOrLedeTouched,
and do not mock both --gate-replay and --check-edition.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import tools.tests.test_score_progression_budget_frozen as _score_budget
from tools.chiefs_narrative import collect, facts, generate, phase
from tools.tests.frozen_slate import frozen_schedule

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "layouts" / "partials" / "narrative-edition.html"


def _pad(tag: str) -> str:
    return (
        f"{tag} Kansas City is 5-2 and turns to the Jets after Denver. "
        "The tape is early downs, the kick file, and the next snap. "
    ) * 80


PAD = _pad("Dek")


def _pinned_quinton() -> str:
    path = REPO / "tools" / "tests" / "fixtures" / "edition_run_38582366273.json"
    return json.loads(path.read_text(encoding="utf-8"))["pinned"]["quinton"]


def _template_line(token: str) -> int:
    for index, line in enumerate(TEMPLATE.read_text(encoding="utf-8").splitlines(), 1):
        if token in line:
            return index
    raise AssertionError(token)


def _review_first_body_field_range() -> tuple[str, int, int]:
    """First rendered body field for a Review, from the template."""
    dek = _template_line("{{ with .dek }}")
    edge = _template_line("{{ with .theEdge }}")
    review_partial = _template_line('{{ if ne .phase.mode "preview" }}')
    if not (dek < edge < review_partial):
        raise AssertionError((dek, edge, review_partial))
    return "dek", dek, edge


class Frozen7A14Lede(unittest.TestCase):
    """A14-1 … A14-9 and B2-<callsite>, in frozen order."""

    def _helper(self) -> _score_budget.ScoreProgressionBudgetExhaustion:
        return _score_budget.ScoreProgressionBudgetExhaustion(
            "test_p1_seven_invalid_claims_drop_clean"
        )

    def _claim(self) -> str:
        return self._helper()._claim("7-3")

    def _base(self, helper: _score_budget.ScoreProgressionBudgetExhaustion) -> dict:
        raw = helper._fat_edition(helper.CLEAN)
        raw["dek"] = _pad("Dek")
        raw["theEdge"] = _pad("Edge")
        raw["storyline"] = _pad("Story")
        raw["currentState"] = {
            "lede": _pad("State"),
            "workOn": [{"title": "Early downs", "body": _pad("Work")[:240]}],
            "thinkAbout": [{"title": "The Jets", "body": _pad("Think")[:240]}],
        }
        raw["gamePlan"] = {
            "lede": _pad("Plan"),
            "howTheyMatch": _pad("Match")[:240],
            "keys": [{"title": "Early downs", "body": _pad("Keys")[:240]}],
        }
        review = dict(raw.get("lastGameReview") or {})
        review["lede"] = helper.CLEAN + " " + _pad("Review")[:240]
        review["analysis"] = [helper.CLEAN]
        raw["lastGameReview"] = review
        return raw

    def _para(self, claim: str, *, lead: bool) -> str:
        if lead:
            return claim + "\n\n" + PAD
        return PAD + "\n\n" + claim

    def _allow_empty_dek_edge(self):
        real = facts.missing_required_copy

        def wrapped(narrative):
            return [key for key in real(narrative) if key not in ("dek", "theEdge")]

        return patch.object(facts, "missing_required_copy", side_effect=wrapped)

    def _empty_live_recap(self):
        """Stage 1 uses the live ESPN payload; empty recap leaves detail checks off.

        Stage 2/3 load the checkout fixture via `_recap_for_edition`, which is
        the published-edition / archive-replay caller input.
        """
        empty = {}
        return (
            patch.object(
                collect,
                "collect_all",
                return_value={
                    "schedule": frozen_schedule(),
                    "news": [],
                    "markets": {},
                    "lastGameRecap": empty,
                },
            ),
            patch.object(collect, "fetch_game_recap", return_value=empty),
        )

    def _stored_edition(self) -> dict:
        """Archived Week 3 Preview fixture (edition_2026-09-25-1552.json)."""
        path = REPO / "tools" / "tests" / "fixtures" / "edition_2026-09-25-1552.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def _fg_lede(self, helper: _score_budget.ScoreProgressionBudgetExhaustion) -> dict:
        raw = self._base(helper)
        review = dict(raw.get("lastGameReview") or {})
        review["lede"] = self._para("Butker from 99.", lead=True)
        raw["lastGameReview"] = review
        return raw

    def _through(
        self,
        raw: dict,
        *,
        extra=(),
        llm=None,
        retries: int | None = 0,
        review: bool = False,
    ):
        helper = self._helper()
        extras = list(extra)
        if retries is not None:
            extras.append(patch.object(generate, "FACT_CHECK_RETRIES", retries))
        if review:
            ph = helper._week9_phase()
            ph["mode"] = "review"
            ph["label"] = "Week 9 Review"
            ph["edition"] = "2026 Week 9 · Review"
            extras.extend(
                (
                    patch.object(phase, "detect", return_value=ph),
                    patch.object(facts, "REVIEW_REQUIRES_HUMAN", False),
                )
            )
        with tempfile.TemporaryDirectory() as tmp:
            result, repair_json = helper._through_build(
                Path(tmp), raw, llm=llm, patches=tuple(extras)
            )
            repair = json.loads(repair_json.read_text(encoding="utf-8"))
        return result, repair

    def _assert_hold(self, result, repair: dict, token: str) -> None:
        dropped = result.get("droppedSentences") or []
        corrections = result.get("correctedSentences") or []
        blob = " ".join(str(row) for row in list(dropped) + list(corrections))
        self.assertIn(token, blob, (dropped, corrections))
        self.assertTrue(
            repair.get("holdAutomerge"),
            {"repair": repair, "drops": dropped, "corrections": corrections},
        )
        self.assertTrue(
            result.get("headlineOrLedeTouched"),
            result,
        )

    def _assert_ship(self, result, repair: dict, token: str) -> None:
        dropped = result.get("droppedSentences") or []
        blob = " ".join(str(row) for row in dropped)
        self.assertIn(token, blob, dropped)
        self.assertFalse(
            repair.get("holdAutomerge"),
            {"repair": repair, "drops": dropped, "leftover": result.get("leftoverIssues")},
        )
        self.assertFalse(result.get("headlineOrLedeTouched"), result)

    def test_a14_1_dek_drop_holds(self):
        """A14-1: 1 drop in dek holds."""
        helper = self._helper()
        claim = self._claim()
        raw = self._base(helper)
        raw["dek"] = self._para(claim, lead=True)
        result, repair = self._through(raw)
        self._assert_hold(result, repair, "7-3")

    def test_a14_2_theedge_p1_no_dek_holds(self):
        """A14-2: 1 drop in theEdge p1 (no dek) holds."""
        helper = self._helper()
        claim = self._claim()
        raw = self._base(helper)
        raw["dek"] = ""
        raw["theEdge"] = self._para(claim, lead=True)
        result, repair = self._through(
            raw, extra=(self._allow_empty_dek_edge(),)
        )
        self._assert_hold(result, repair, "7-3")

    def test_a14_3_lastgamereview_lede_drop_holds(self):
        """A14-3: 1 drop in lastGameReview.lede holds."""
        helper = self._helper()
        claim = self._claim()
        raw = self._base(helper)
        review = dict(raw.get("lastGameReview") or {})
        review["lede"] = self._para(claim, lead=True)
        raw["lastGameReview"] = review
        result, repair = self._through(raw)
        self._assert_hold(result, repair, "7-3")

    def test_a14_4_next_paragraph_s1_ships(self):
        """A14-4: sentence 1 of next paragraph ships."""
        helper = self._helper()
        claim = self._claim()
        raw = self._base(helper)
        raw["dek"] = PAD
        raw["theEdge"] = self._para(claim, lead=True)
        result, repair = self._through(raw)
        self._assert_ship(result, repair, "7-3")

    def test_a14_5_dek_edge_empty_falls_through_never_none(self):
        """A14-5: dek+theEdge empty falls through to the next field, never None."""
        helper = self._helper()
        claim = self._claim()
        raw = self._base(helper)
        raw["dek"] = ""
        raw["theEdge"] = ""
        raw["currentState"] = {
            "lede": self._para(claim, lead=True),
            "workOn": [{"title": "Early downs", "body": PAD[:240]}],
            "thinkAbout": [{"title": "The Jets", "body": PAD[:240]}],
        }
        result, repair = self._through(
            raw, extra=(self._allow_empty_dek_edge(),)
        )
        self._assert_hold(result, repair, "7-3")

    def test_a14_6_no_body_field_holds(self):
        """A14-6: no body field at all holds."""
        helper = self._helper()
        raw = self._base(helper)
        raw["headline"] = "Week 9 Preview " + ("word " * 4200)
        raw["dek"] = ""
        raw["theEdge"] = ""
        raw["videoHook"] = ""
        raw["storyline"] = {"lede": "", "body": []}
        raw["currentState"] = {}
        raw["gamePlan"] = {}
        raw["lastGameReview"] = {
            "opponent": "Denver Broncos",
            "result": "L",
            "score": "KC 24–28",
            "lede": "",
            "analysis": [],
        }
        result, repair = self._through(
            raw,
            extra=(
                self._allow_empty_dek_edge(),
                patch.object(generate, "_ensure_desk_sections", lambda *a, **k: None),
            ),
        )
        self.assertTrue(repair.get("holdAutomerge"), repair)
        self.assertFalse(result.get("droppedSentences"), result.get("droppedSentences"))

    def test_a14_7_multipara_dek_p1_holds_p2_ships(self):
        """A14-7: multi-paragraph dek: p2 drop ships, p1 drop holds."""
        helper = self._helper()
        claim = self._claim()
        clean = "Kansas City turns to the Jets after Denver."
        raw_p1 = self._base(helper)
        raw_p1["dek"] = claim + "\n\n" + clean + " " + PAD
        result, repair = self._through(raw_p1)
        self._assert_hold(result, repair, "7-3")
        raw_p2 = self._base(helper)
        raw_p2["dek"] = clean + "\n\n" + claim
        result, repair = self._through(raw_p2)
        self._assert_ship(result, repair, "7-3")

    def test_a14_8_dek_correction_holds(self):
        """A14-8: a correction in the dek holds (R3)."""
        helper = self._helper()
        quinton = _pinned_quinton()
        raw = self._base(helper)
        raw["dek"] = quinton + "\n\n" + PAD
        result, repair = self._through(raw, retries=None)
        corrections = result.get("correctedSentences") or []
        self.assertTrue(
            any("Quinton Walker" in str(row) for row in corrections),
            corrections,
        )
        self.assertTrue(repair.get("holdAutomerge"), repair)
        self.assertTrue(result.get("headlineOrLedeTouched"), result)

    def test_a14_9_review_first_field_from_template_range(self):
        """A14-9: Review first rendered body field is dek at narrative-edition.html L51 (hero L47–99)."""
        field, start, end = _review_first_body_field_range()
        self.assertEqual(field, "dek")
        self.assertEqual(start, 51)
        self.assertGreaterEqual(end, 99)
        helper = self._helper()
        claim = self._claim()
        raw = self._base(helper)
        raw["dek"] = self._para(claim, lead=True)
        raw["edition"] = "2026 Week 9 · Review"
        raw["phase"] = {"type": "regular", "mode": "review", "week": 9}
        result, repair = self._through(raw, review=True)
        self._assert_hold(result, repair, "7-3")

    def test_b2_build(self):
        """B2-build: one dropped lastGameReview.lede sentence holds at head.

        Under MS-A10w this same generate.main path ships.
        """
        helper = self._helper()
        claim = self._claim()
        raw = self._base(helper)
        review = dict(raw.get("lastGameReview") or {})
        review["lede"] = self._para(claim, lead=True)
        raw["lastGameReview"] = review
        result, repair = self._through(raw)
        self._assert_hold(result, repair, "7-3")

    def test_b2_build_stage2(self):
        """B2-build stage 2: published-edition gate salvage (generate.py ~L1332).

        FROZEN-7 A14 / B2. Live recap is empty so stage 1 does not drop.
        Checkout fixture recap flags Butker from 99 in lastGameReview.lede
        and stage 2 copy_touches holds. Under MS-A10w-s2 this same
        generate.main path ships. Not a copy of test_a14_3.
        """
        helper = self._helper()
        result, repair = self._through(
            self._fg_lede(helper), extra=self._empty_live_recap()
        )
        self._assert_hold(result, repair, "Butker from 99")

    def test_b2_build_stage3(self):
        """B2-build stage 3: archive replay salvage (generate.py ~L1413).

        FROZEN-7 A14 / B2. Writer payload is the stored archived edition
        2026-09-25-1552 with lastGameReview.lede carrying Butker from 99.
        Live recap is empty so stage 1 does not drop the kick. The stored
        edition's other ESPN leftovers make published-edition salvage
        noisy-revert (generate.py L1341-1352), so archive_replay_issues
        still sees the dirty lede and stage 3 salvage runs. check_review
        and archive_replay_issues are not mocked. _salvage_until_clean is
        not patched. MS-A10w-s3 alone still holds because stage 2 already
        recorded the same lede drop; MS-A10w (all three stages) ships.
        Not a copy of test_a14_3.
        """
        raw = self._stored_edition()
        review = dict(raw.get("lastGameReview") or {})
        review["lede"] = self._para("Butker from 99.", lead=True)
        raw["lastGameReview"] = review
        result, repair = self._through(raw, extra=self._empty_live_recap())
        self._assert_hold(result, repair, "Butker from 99")

    def test_b2_known_errors(self):
        """B2-known_errors: first known_errors pass, Quinton→Quay in headline holds.

        Under MS-A10k this same generate.main path ships.
        """
        helper = self._helper()
        raw = self._base(helper)
        raw["headline"] = "Quinton Walker sets the Week 9 preview after Denver."
        result, repair = self._through(raw, retries=None)
        corrections = result.get("correctedSentences") or []
        self.assertTrue(
            any(
                "Quinton" in str(row) and "Quay" in str(row)
                for row in corrections
            ),
            corrections,
        )
        self.assertTrue(repair.get("holdAutomerge"), repair)
        self.assertTrue(result.get("headlineOrLedeTouched"), result)

    def test_b2_retry(self):
        """B2-retry: retry apply_known_errors pass, Quinton→Quay in headline holds."""
        helper = self._helper()
        first = self._base(helper)
        first["storyline"] = helper._claim("7-3") + " " + _pad("Story")
        second = self._base(helper)
        second["headline"] = "Quinton Walker sets the Week 9 preview after Denver."
        llm = Mock(side_effect=[(first, "grok")] + [(second, "grok")] * 4)
        result, repair = self._through(first, llm=llm, retries=None)
        corrections = result.get("correctedSentences") or []
        self.assertTrue(
            any(
                "Quinton" in str(row) and "Quay" in str(row)
                for row in corrections
            ),
            corrections,
        )
        self.assertTrue(repair.get("holdAutomerge"), repair)
        self.assertTrue(result.get("headlineOrLedeTouched"), result)


if __name__ == "__main__":
    unittest.main()
