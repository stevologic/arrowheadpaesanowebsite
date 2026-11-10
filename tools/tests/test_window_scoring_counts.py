"""Window-bound TD/FG/score counts vs ESPN scoring plays (Karen B1)."""
from __future__ import annotations

import copy
import unittest

from tools.chiefs_narrative import facts
from tools.tests.archive_replay import load_recap

KAREN_FIVE = (
    "Five Kansas City touchdowns after a 14-12 halftime, "
    "including Johnson’s 6-yard score"
)
THREE = (
    "Three Kansas City touchdowns after a 14-12 halftime, "
    "including Johnson’s 6-yard score"
)
SECOND_HALF_THREE = (
    "The second half was three Kansas City touchdowns and a defense "
    "that kept Geno Smith off the scoreboard."
)

LAST_JETS = {
    "id": "401873045",
    "opponent": "New York Jets",
    "opponentAbbr": "NYJ",
    "opponentShort": "Jets",
    "completed": True,
    "kcScore": 35,
    "oppScore": 12,
    "date": "2026-11-08T18:00:00Z",
}


def _review(what_worked: str, lede: str | None = None) -> dict:
    return {
        "lastGameReview": {
            "opponent": "New York Jets",
            "result": "W",
            "score": "KC 35–12",
            "lede": lede
            or (
                "Kansas City beat the Jets 35-12 after trailing 6-0 "
                "and going to halftime up 14-12."
            ),
            "analysis": ["Kelce scored on a 1-yard rush."],
            "whatWorked": [what_worked],
            "whatDidnt": ["Two turnovers."],
        }
    }


class WindowScoringCounts(unittest.TestCase):
    def setUp(self):
        self.recap = load_recap("401873045")
        self.recap = dict(self.recap)
        self.recap["opponent"] = "New York Jets"
        self.recap["oppAbbr"] = "NYJ"

    def _issues(self, narrative: dict, recap=None):
        return facts.check_review(
            narrative, LAST_JETS, recap if recap is not None else self.recap
        )

    def _window(self, narrative: dict, recap=None):
        text = facts.edition_text(narrative)
        return facts._check_window_scoring_counts(
            text, LAST_JETS, recap if recap is not None else self.recap
        )

    def test_karen_five_after_halftime_flags(self):
        narrative = _review(KAREN_FIVE)
        window = self._window(narrative)
        self.assertTrue(
            any("window-bound" in item and "5" in item for item in window),
            window,
        )
        issues = self._issues(narrative)
        self.assertTrue(
            any("window-bound" in item and "5" in item for item in issues),
            issues,
        )

    def test_three_after_halftime_passes(self):
        narrative = _review(THREE)
        self.assertEqual(self._window(narrative), [])
        self.assertFalse(
            any("window-bound" in item for item in self._issues(narrative))
        )

    def test_second_half_was_three_passes(self):
        narrative = _review(
            "Walker at 22 touches and 102 rushing yards",
            lede=SECOND_HALF_THREE,
        )
        self.assertEqual(self._window(narrative), [])
        self.assertFalse(
            any("window-bound" in item for item in self._issues(narrative))
        )

    def test_unverifiable_missing_scoring_plays_flags(self):
        recap = {"oppAbbr": "NYJ", "kc": {"totalYards": "391"}}
        narrative = _review("Five Kansas City touchdowns after halftime.")
        window = self._window(narrative, recap=recap)
        self.assertTrue(
            any("cannot be verified" in item for item in window),
            window,
        )
        issues = self._issues(narrative, recap=recap)
        self.assertTrue(
            any("cannot be verified" in item for item in issues),
            issues,
        )

    def test_unverifiable_missing_quarters_flags(self):
        recap = {
            "oppAbbr": "NYJ",
            "scoringPlays": [{"type": "TD", "team": "KC", "yards": 6}],
        }
        narrative = _review("Five Kansas City touchdowns after the break.")
        window = self._window(narrative, recap=recap)
        self.assertTrue(
            any("cannot be verified" in item for item in window),
            window,
        )

    def test_after_a_score_unmapped_flags(self):
        narrative = _review("Five Kansas City touchdowns after a score.")
        window = self._window(narrative)
        self.assertTrue(
            any("cannot be verified" in item for item in window),
            window,
        )

    def test_whole_game_five_without_window_is_not_this_guard(self):
        narrative = _review(
            "Five Kansas City touchdowns, four New York field goals, "
            "no Jets touchdown."
        )
        self.assertEqual(self._window(narrative), [])

    def test_window_plays_verifiable_on_jets_fixture(self):
        self.assertTrue(facts._scoring_plays_verifiable(self.recap))
        self.assertFalse(facts._scoring_plays_verifiable({}))
        self.assertFalse(facts._scoring_plays_verifiable(None))
        missing_q = copy.deepcopy(self.recap)
        missing_q["scoringPlays"][0].pop("quarter")
        self.assertFalse(facts._scoring_plays_verifiable(missing_q))


if __name__ == "__main__":
    unittest.main()
