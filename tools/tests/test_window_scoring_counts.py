"""Window-bound TD/FG/score counts vs ESPN scoring plays (Karen B1).

Isolating tests are named so a drop-one mutant of a single inventory
member changes only that test's result. I6 asserts the full issue list.
"""
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
WALKER_TURNED = (
    "Walker's 102 yards on 18 carries, plus Johnson's six-yard "
    "touchdown, turned a 14-12 halftime into 35-12"
)
FLIPPED = "Eighteen carries and 102 yards just flipped a game that was 14-12"

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

# Head expected: FLAG means a window-bound issue, leftover means _count_issue,
# pass means check_review issues for that sentence's window/count are clean
# of both. develop_flag documents the unpatched tree (no window guard;
# leftover only when whole-game _count_issue fires).
ADVERSARIAL_ROWS = (
    {
        "id": "r1_five_after_14_12",
        "sentence": KAREN_FIVE,
        "head": "FLAG",
        "develop": "pass",
        "note": "#331 r1; ESPN 401873045 has three 2H TDs; head must FLAG",
    },
    {
        "id": "r2_three_second_half",
        "sentence": (
            "Three Kansas City touchdowns in the second half, "
            "including Johnson's 6-yard score"
        ),
        "head": "pass",
        "develop": "pass",
        "note": "#331 r2; BASE second-half scope already 3=3",
    },
    {
        "id": "three_after_14_12",
        "sentence": THREE,
        "head": "pass",
        "develop": "leftover",
        "note": "develop leftover 3 vs 5; head pass is the I6 fix, not a miss",
    },
    {
        "id": "lede_second_half_three",
        "sentence": SECOND_HALF_THREE,
        "head": "pass",
        "develop": "pass",
        "note": "lede The second half was three...",
    },
    {
        "id": "whole_game_five_no_window",
        "sentence": (
            "Five Kansas City touchdowns, four New York field goals, "
            "no Jets touchdown."
        ),
        "head": "pass",
        "develop": "pass",
        "note": "true-negative: no window word, 5=5 whole game",
    },
    {
        "id": "whole_game_three_no_window",
        "sentence": "Three Kansas City touchdowns came before the break.",
        "head": "leftover",
        "develop": "leftover",
        "note": "true-negative for the window guard; leftover 3 vs 5 is whole-game",
    },
    {
        "id": "walker_turned",
        "sentence": WALKER_TURNED,
        "head": "FLAG",
        "develop": "pass",
        "note": "I5/H1: yards bound to turned-halftime is fail-closed",
    },
    {
        "id": "flipped_14_12",
        "sentence": FLIPPED,
        "head": "FLAG",
        "develop": "pass",
        "note": "I5/H1: yards bound to flipped 14-12 is fail-closed",
    },
    {
        "id": "following_halftime",
        "sentence": "Five Kansas City touchdowns following halftime.",
        "head": "FLAG",
        "develop": "pass",
        "note": "following = after",
    },
    {
        "id": "after_the_half",
        "sentence": "Five Kansas City touchdowns after the half.",
        "head": "FLAG",
        "develop": "pass",
        "note": "after the half",
    },
    {
        "id": "in_the_second_half_five",
        "sentence": "Five Kansas City touchdowns in the second half.",
        "head": "FLAG",
        "develop": "leftover",
        "note": "develop leftover via second-half scope 5 vs 3; head also window-FLAG",
    },
    {
        "id": "endash_14_12",
        "sentence": "Five Kansas City touchdowns after a 14–12 halftime.",
        "head": "FLAG",
        "develop": "pass",
        "note": "Unicode en dash U+2013",
    },
    {
        "id": "emdash_14_12",
        "sentence": "Five Kansas City touchdowns after a 14—12 halftime.",
        "head": "FLAG",
        "develop": "pass",
        "note": "Unicode em dash U+2014 folded to hyphen",
    },
    {
        "id": "minus_14_12",
        "sentence": "Five Kansas City touchdowns after a 14−12 halftime.",
        "head": "FLAG",
        "develop": "pass",
        "note": "U+2212 minus folded to hyphen",
    },
    {
        "id": "soft_hyphen_14_12",
        "sentence": "Five Kansas City touchdowns after a 14\u00ad-\u00ad12 halftime.",
        "head": "FLAG",
        "develop": "pass",
        "note": "soft hyphen U+00AD stripped/folded",
    },
    {
        "id": "zwsp_14_12",
        "sentence": "Five Kansas City touchdowns after a 14\u200b-\u200b12 halftime.",
        "head": "FLAG",
        "develop": "pass",
        "note": "ZWSP U+200B stripped",
    },
    {
        "id": "sep_ascii_hyphen",
        "sentence": "Five Kansas City touchdowns after a 14-12 halftime.",
        "head": "FLAG",
        "develop": "pass",
        "note": "14-12",
    },
    {
        "id": "sep_en_dash",
        "sentence": "Five Kansas City touchdowns after a 14–12 halftime.",
        "head": "FLAG",
        "develop": "pass",
        "note": "14–12",
    },
    {
        "id": "sep_spaced_hyphen",
        "sentence": "Five Kansas City touchdowns after a 14 - 12 halftime.",
        "head": "FLAG",
        "develop": "pass",
        "note": "14 - 12",
    },
    {
        "id": "negation_never_five",
        "sentence": "Kansas City never five touchdowns after halftime.",
        "head": "pass",
        "develop": "pass",
        "note": "negation: skip the count",
    },
    {
        "id": "i12_figure_dash",
        "sentence": "Five Kansas City touchdowns after a 14\u201212 halftime.",
        "head": "FLAG",
        "develop": "pass",
        "note": "U+2012 figure dash folded to hyphen",
    },
    {
        "id": "i12_nbsp_reached",
        "sentence": (
            "Kansas City scored five touchdowns after the score reached "
            "14\u00a0–\u00a012."
        ),
        "head": "FLAG",
        "develop": "pass",
        "note": "NBSP inside 14–12",
    },
    {
        "id": "i12_non_after_reached",
        "sentence": "Five Kansas City touchdowns non-after the score reached 14-12.",
        "head": "pass",
        "develop": "pass",
        "note": "un/non lookbehind on after the score reached",
    },
    {
        "id": "i12_following_the_score_reached",
        "sentence": "Five Kansas City touchdowns following the score reached 14-12.",
        "head": "pass",
        "develop": "pass",
        "note": "following the score reached is not after",
    },
    {
        "id": "i12_had_reached",
        "sentence": "Five Kansas City touchdowns after the score had reached 14-12.",
        "head": "pass",
        "develop": "pass",
        "note": "had reached is not the bind",
    },
    {
        "id": "i12_falling_behind",
        "sentence": "Five Kansas City touchdowns after falling behind 0-6.",
        "head": "pass",
        "develop": "pass",
        "note": "falling behind is not trailing",
    },
    {
        "id": "i12_once_trailing",
        "sentence": "Five Kansas City touchdowns once trailing 6-0.",
        "head": "pass",
        "develop": "pass",
        "note": "once trailing is not after trailing",
    },
    {
        "id": "i12_never_trailed",
        "sentence": "Kansas City never trailed 6-0 after the score reached 14-12.",
        "head": "pass",
        "develop": "pass",
        "note": "negation / no count noun",
    },
    {
        "id": "i12_digit_five_reached",
        "sentence": "Kansas City scored 5 touchdowns after the score reached 14-12.",
        "head": "FLAG",
        "develop": "pass",
        "note": "digit 5 vs word five on the new score window",
    },
    {
        "id": "i12_td_singular_reached",
        "sentence": "Kansas City scored 5 touchdown after the score reached 14-12.",
        "head": "FLAG",
        "develop": "pass",
        "note": "singular touchdown on the new score window",
    },
    {
        "id": "digit_5",
        "sentence": "5 Kansas City touchdowns after halftime.",
        "head": "FLAG",
        "develop": "pass",
        "note": "5 vs Five",
    },
    {
        "id": "word_five",
        "sentence": "Five Kansas City touchdowns after halftime.",
        "head": "FLAG",
        "develop": "pass",
        "note": "Five vs 5",
    },
    {
        "id": "five_tds_after_the_half",
        "sentence": "Five Kansas City TDs after the half.",
        "head": "FLAG",
        "develop": "pass",
        "note": "TDs noun",
    },
    {
        "id": "two_field_goals_after_the_break",
        "sentence": "Two Kansas City field goals after the break.",
        "head": "FLAG",
        "develop": "leftover",
        "note": "0 second-half FGs; develop leftover via after-the-break scope",
    },
    {
        "id": "three_scores_since_halftime",
        "sentence": "Three Kansas City scores since halftime.",
        "head": "pass",
        "develop": "pass",
        "note": "3 scores = 3 TDs + 0 FG in 2H",
    },
    {
        "id": "sacks_after_halftime",
        "sentence": "Five Kansas City sacks after halftime.",
        "head": "FLAG",
        "develop": "pass",
        "note": "fail-closed: no per-window ESPN sacks",
    },
    {
        "id": "points_after_halftime",
        "sentence": "Twenty-one Kansas City points after halftime.",
        "head": "pass",
        "develop": "pass",
        "note": "twenty-one is not a number-word/digit member; not this guard",
    },
    {
        "id": "points_digit_after_halftime",
        "sentence": "21 Kansas City points after halftime.",
        "head": "FLAG",
        "develop": "pass",
        "note": "fail-closed: no per-window ESPN points",
    },
    {
        "id": "yards_after_halftime",
        "sentence": "102 Kansas City yards after halftime.",
        "head": "FLAG",
        "develop": "pass",
        "note": "fail-closed: no per-window ESPN yards",
    },
    {
        "id": "takeaways_after_halftime",
        "sentence": "Three Kansas City takeaways after halftime.",
        "head": "FLAG",
        "develop": "pass",
        "note": "fail-closed: no per-window ESPN takeaways",
    },
    {
        "id": "fourth_quarter_five",
        "sentence": "Five Kansas City touchdowns in the fourth quarter.",
        "head": "FLAG",
        "develop": "leftover",
        "note": "Q4 has 1 KC TD; develop leftover via fourth-quarter scope",
    },
    {
        "id": "non_second_half",
        "sentence": "Five Kansas City touchdowns in the non-second-half.",
        "head": "pass",
        "develop": "leftover",
        "note": "un/non prefix must not bind second-half; develop leftover via second-half",
    },
    {
        "id": "one_sack_singular",
        "sentence": "One Kansas City sack after halftime.",
        "head": "FLAG",
        "develop": "pass",
        "note": "fail-closed singular sack",
    },
    {
        "id": "one_yard_singular",
        "sentence": "One Kansas City yard after halftime.",
        "head": "FLAG",
        "develop": "pass",
        "note": "fail-closed singular yard",
    },
    {
        "id": "one_point_singular",
        "sentence": "One Kansas City point after halftime.",
        "head": "FLAG",
        "develop": "pass",
        "note": "fail-closed singular point",
    },
    {
        "id": "one_takeaway_singular",
        "sentence": "One Kansas City takeaway after halftime.",
        "head": "FLAG",
        "develop": "pass",
        "note": "fail-closed singular takeaway",
    },
    {
        "id": "three_following_the_half",
        "sentence": "Three Kansas City touchdowns following the half.",
        "head": "pass",
        "develop": "leftover",
        "note": "I6: true 2H count; develop leftover 3 vs 5",
    },
    {
        "id": "three_following_the_break",
        "sentence": "Three Kansas City touchdowns following the break.",
        "head": "pass",
        "develop": "leftover",
        "note": "I6: true 2H count; develop leftover 3 vs 5",
    },
    {
        "id": "three_following_a_nn",
        "sentence": "Three Kansas City touchdowns following a 14-12 halftime.",
        "head": "pass",
        "develop": "leftover",
        "note": "I6: true 2H count; develop leftover 3 vs 5",
    },
    {
        "id": "three_turned",
        "sentence": "Three Kansas City touchdowns turned a 14-12 halftime into 35-12.",
        "head": "pass",
        "develop": "leftover",
        "note": "I6: true 2H count; develop leftover 3 vs 5",
    },
    {
        "id": "three_flipped",
        "sentence": "Three Kansas City touchdowns flipped a game that was 14-12.",
        "head": "pass",
        "develop": "leftover",
        "note": "I6: true 2H count; develop leftover 3 vs 5",
    },
    {
        "id": "one_catch_singular",
        "sentence": "One Kansas City catch after halftime.",
        "head": "FLAG",
        "develop": "pass",
        "note": "fail-closed singular catch",
    },
    {
        "id": "two_catches",
        "sentence": "Two Kansas City catches after halftime.",
        "head": "FLAG",
        "develop": "pass",
        "note": "fail-closed plural catches",
    },
    {
        "id": "one_carry_singular",
        "sentence": "One Kansas City carry after halftime.",
        "head": "FLAG",
        "develop": "pass",
        "note": "fail-closed singular carry",
    },
    {
        "id": "two_carries",
        "sentence": "Two Kansas City carries after halftime.",
        "head": "FLAG",
        "develop": "pass",
        "note": "fail-closed plural carries",
    },
    {
        "id": "one_interception_singular",
        "sentence": "One Kansas City interception after halftime.",
        "head": "FLAG",
        "develop": "pass",
        "note": "fail-closed singular interception",
    },
    {
        "id": "two_interceptions",
        "sentence": "Two Kansas City interceptions after halftime.",
        "head": "FLAG",
        "develop": "pass",
        "note": "fail-closed plural interceptions",
    },
    {
        "id": "one_fumble_singular",
        "sentence": "One Kansas City fumble after halftime.",
        "head": "FLAG",
        "develop": "pass",
        "note": "fail-closed singular fumble",
    },
    {
        "id": "two_fumbles",
        "sentence": "Two Kansas City fumbles after halftime.",
        "head": "FLAG",
        "develop": "pass",
        "note": "fail-closed plural fumbles",
    },
    {
        "id": "one_turnover_singular",
        "sentence": "One Kansas City turnover after halftime.",
        "head": "FLAG",
        "develop": "pass",
        "note": "fail-closed singular turnover",
    },
    {
        "id": "two_turnovers",
        "sentence": "Two Kansas City turnovers after halftime.",
        "head": "FLAG",
        "develop": "pass",
        "note": "fail-closed plural turnovers",
    },
    {
        "id": "one_reception_singular",
        "sentence": "One Kansas City reception after halftime.",
        "head": "FLAG",
        "develop": "pass",
        "note": "fail-closed singular reception",
    },
    {
        "id": "two_receptions",
        "sentence": "Two Kansas City receptions after halftime.",
        "head": "FLAG",
        "develop": "pass",
        "note": "fail-closed plural receptions",
    },
    {
        "id": "one_rush_singular",
        "sentence": "One Kansas City rush after halftime.",
        "head": "FLAG",
        "develop": "pass",
        "note": "fail-closed singular rush",
    },
    {
        "id": "two_rushes",
        "sentence": "Two Kansas City rushes after halftime.",
        "head": "FLAG",
        "develop": "pass",
        "note": "fail-closed plural rushes",
    },
    {
        "id": "one_completion_singular",
        "sentence": "One Kansas City completion after halftime.",
        "head": "FLAG",
        "develop": "pass",
        "note": "fail-closed singular completion",
    },
    {
        "id": "two_completions",
        "sentence": "Two Kansas City completions after halftime.",
        "head": "FLAG",
        "develop": "pass",
        "note": "fail-closed plural completions",
    },
)


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


def _window_issues(issues: list[str]) -> list[str]:
    return [item for item in issues if "window-bound" in item]


def _leftover_issues(issues: list[str]) -> list[str]:
    return [
        item
        for item in issues
        if "window-bound" not in item and "disagrees with ESPN" in item
    ]


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

    def _window(self, narrative: dict, recap="default"):
        text = facts.edition_text(narrative)
        payload = self.recap if recap == "default" else recap
        return facts._check_window_scoring_counts(text, LAST_JETS, payload)

    def _classify(self, sentence: str) -> str:
        narrative = _review(sentence)
        issues = self._issues(narrative)
        if _window_issues(issues):
            return "FLAG"
        if _leftover_issues(issues):
            return "leftover"
        return "pass"

    def _assert_window_flag(self, sentence: str):
        window = self._window(_review(sentence))
        self.assertTrue(window, window)

    def _assert_window_clean(self, sentence: str):
        window = self._window(_review(sentence))
        self.assertEqual(window, [])

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
        # I6: a correct window count must not fall through to a whole-game
        # leftover. Assert the full issue list, not a substring absence.
        issues = self._issues(_review(THREE))
        self.assertEqual(issues, [])

    def test_second_half_three_passes(self):
        issues = self._issues(_review(SECOND_HALF_THREE, lede=SECOND_HALF_THREE))
        self.assertEqual(_window_issues(issues), [])
        self.assertEqual(
            [item for item in issues if "touchdown count 3 disagrees" in item],
            [],
        )

    def test_missing_scoring_plays_flags(self):
        window = self._window(_review(KAREN_FIVE), recap={"oppAbbr": "NYJ"})
        self.assertTrue(any("cannot be verified" in item for item in window), window)

    def test_missing_quarters_flags(self):
        recap = copy.deepcopy(self.recap)
        for play in recap["scoringPlays"]:
            play.pop("quarter", None)
        window = self._window(_review(KAREN_FIVE), recap=recap)
        self.assertTrue(any("cannot be verified" in item for item in window), window)

    def test_after_a_score_flags(self):
        window = self._window(
            _review("Five Kansas City touchdowns after a score.")
        )
        self.assertTrue(any("cannot be verified" in item for item in window), window)

    def test_whole_game_five_not_this_guard(self):
        window = self._window(_review("Five Kansas City touchdowns sealed it."))
        self.assertEqual(window, [])

    def test_verifiable_on_jets_fixture(self):
        self.assertTrue(facts._scoring_plays_verifiable(self.recap))

    # --- I1: each _SCORING_WINDOW / _WINDOW_SPECS member ---

    def test_win_after_halftime(self):
        self._assert_window_flag("Five Kansas City touchdowns after halftime.")

    def test_win_after_the_half(self):
        self._assert_window_flag("Five Kansas City touchdowns after the half.")

    def test_win_after_the_break(self):
        self._assert_window_flag("Five Kansas City touchdowns after the break.")

    def test_win_after_a_nn_hyphen(self):
        self._assert_window_flag(
            "Five Kansas City touchdowns after a 14-12 halftime."
        )

    def test_win_after_a_nn_endash(self):
        self._assert_window_flag(
            "Five Kansas City touchdowns after a 14–12 halftime."
        )

    def test_win_since_halftime(self):
        self._assert_window_flag("Five Kansas City touchdowns since halftime.")

    def test_win_since_the_half(self):
        self._assert_window_flag("Five Kansas City touchdowns since the half.")

    def test_win_since_the_break(self):
        self._assert_window_flag("Five Kansas City touchdowns since the break.")

    def test_win_in_the_second_half(self):
        self._assert_window_flag(
            "Five Kansas City touchdowns in the second half."
        )

    def test_win_the_second_half(self):
        self._assert_window_flag(
            "The second half was five Kansas City touchdowns."
        )

    def test_win_second_half(self):
        self._assert_window_flag(
            "A second-half five Kansas City touchdowns closed it."
        )

    def test_win_following_halftime(self):
        self._assert_window_flag(
            "Five Kansas City touchdowns following halftime."
        )

    def test_win_following_the_half(self):
        self._assert_window_flag(
            "Five Kansas City touchdowns following the half."
        )

    def test_win_following_the_break(self):
        self._assert_window_flag(
            "Five Kansas City touchdowns following the break."
        )

    def test_win_following_a_nn(self):
        self._assert_window_flag(
            "Five Kansas City touchdowns following a 14-12 halftime."
        )

    def test_win_turned_halftime(self):
        self._assert_window_flag(
            "Five Kansas City touchdowns turned a 14-12 halftime into 35-12."
        )

    def test_win_flipped_score(self):
        self._assert_window_flag(
            "Five Kansas City touchdowns flipped a game that was 14-12."
        )

    def test_win_fourth_quarter(self):
        window = self._window(
            _review("Five Kansas City touchdowns in the fourth quarter.")
        )
        self.assertTrue(
            any("window-bound" in item and "5" in item for item in window),
            window,
        )
        self.assertTrue(
            any("fourth quarter" in item for item in window),
            window,
        )

    def test_win_after_a_score(self):
        self.test_after_a_score_flags()

    def test_win_after_score_reached(self):
        self._assert_window_flag(
            "Kansas City scored five touchdowns after the score reached 14-12."
        )

    def test_win_after_score_reached_endash(self):
        self._assert_window_flag(
            "Kansas City scored five touchdowns after the score reached 14–12."
        )

    def test_win_after_trailing(self):
        self._assert_window_flag(
            "Kansas City scored four touchdowns after trailing 6-0."
        )

    def test_win_after_trailing_endash(self):
        self._assert_window_flag(
            "Kansas City scored four touchdowns after trailing 6–0."
        )

    def test_win_after_a_start(self):
        self._assert_window_flag(
            "Kansas City scored four touchdowns after a 0-6 start."
        )

    def test_win_after_a_start_endash(self):
        self._assert_window_flag(
            "Kansas City scored four touchdowns after a 0–6 start."
        )

    def test_i2_swapped_pair_12_14(self):
        window = self._window(
            _review(
                "Kansas City scored five touchdowns after the score reached 12-14."
            )
        )
        self.assertTrue(
            any("disagrees" in item and "3" in item for item in window),
            window,
        )

    def test_i2_off_by_one_includes_state_play(self):
        window = self._window(
            _review(
                "Kansas City scored three touchdowns after the score reached 21-12."
            )
        )
        self.assertTrue(
            any("disagrees" in item and "3" in item and "2" in item for item in window),
            window,
        )

    def test_i2_trailing_skips_kc_ahead(self):
        window = self._window(
            _review(
                "Kansas City scored five touchdowns after trailing 14-12."
            )
        )
        self.assertTrue(
            any("cannot be verified" in item for item in window),
            window,
        )

    def test_i2_start_mode_skips_kc_ahead(self):
        window = self._window(
            _review(
                "Kansas City scored three touchdowns after a 14-12 start."
            )
        )
        self.assertTrue(
            any("cannot be verified" in item for item in window),
            window,
        )

    def test_i2_missing_score_state_unverifiable(self):
        window = self._window(
            _review(
                "Kansas City scored five touchdowns after the score reached 99-99."
            )
        )
        self.assertTrue(
            any("cannot be verified" in item for item in window),
            window,
        )

    def test_i4_digit_five_after_reached(self):
        self._assert_window_flag(
            "Kansas City scored 5 touchdowns after the score reached 14-12."
        )

    def test_i4_singular_touchdown_after_reached(self):
        self._assert_window_flag(
            "Kansas City scored 4 touchdown after trailing 0-6."
        )

    def test_win_conjunct_after_score_reached(self):
        self._assert_window_flag(
            "Five Kansas City touchdowns and no need for a made field goal "
            "after the score reached 14–12."
        )

    def test_m_after_check_review_flags(self):
        issues = self._issues(
            _review(
                "Kansas City scored five touchdowns after the score reached 14–12."
            )
        )
        self.assertTrue(
            any(
                "window-bound" in item
                and "5" in item
                and "3" in item
                for item in issues
            ),
            issues,
        )

    def test_five_after_0_6_start_passes(self):
        issues = self._issues(
            _review("Kansas City scored five touchdowns after a 0-6 start.")
        )
        self.assertEqual(
            [item for item in issues if "window-bound" in item],
            [],
        )

    def test_whole_game_five_still_passes(self):
        issues = self._issues(_review("Five Kansas City touchdowns."))
        self.assertEqual(
            [item for item in issues if "touchdown count 5 disagrees" in item],
            [],
        )

    # --- I1 unicode fold members ---

    def test_fold_em_dash(self):
        self._assert_window_flag(
            "Five Kansas City touchdowns after a 14—12 halftime."
        )

    def test_fold_minus(self):
        self._assert_window_flag(
            "Five Kansas City touchdowns after a 14−12 halftime."
        )

    def test_fold_figure_dash(self):
        self._assert_window_flag(
            "Five Kansas City touchdowns after a 14\u201212 halftime."
        )

    def test_fold_nbsp_in_score(self):
        self._assert_window_flag(
            "Kansas City scored five touchdowns after the score reached "
            "14\u00a0–\u00a012."
        )

    def test_fold_soft_hyphen(self):
        self._assert_window_flag(
            "Five Kansas City touchdowns after a 14\u00ad-\u00ad12 halftime."
        )

    def test_fold_zwsp(self):
        self._assert_window_flag(
            "Five Kansas City touchdowns after a 14\u200b-\u200b12 halftime."
        )

    # --- I2 number words + digit ---

    def test_num_one(self):
        self._assert_window_flag("One Kansas City field goal after halftime.")

    def test_num_two(self):
        self._assert_window_flag("Two Kansas City field goals after halftime.")

    def test_num_three(self):
        self._assert_window_flag("Three Kansas City field goals after halftime.")

    def test_num_four(self):
        self._assert_window_flag("Four Kansas City field goals after halftime.")

    def test_num_five(self):
        self._assert_window_flag("Five Kansas City field goals after halftime.")

    def test_num_six(self):
        self._assert_window_flag("Six Kansas City field goals after halftime.")

    def test_num_seven(self):
        self._assert_window_flag("Seven Kansas City field goals after halftime.")

    def test_num_eight(self):
        self._assert_window_flag("Eight Kansas City field goals after halftime.")

    def test_num_nine(self):
        self._assert_window_flag("Nine Kansas City field goals after halftime.")

    def test_num_ten(self):
        self._assert_window_flag("Ten Kansas City field goals after halftime.")

    def test_num_digit(self):
        self._assert_window_flag("8 Kansas City field goals after halftime.")

    # --- I2 count nouns ---

    def test_noun_touchdown(self):
        self._assert_window_flag("One Kansas City touchdown after halftime.")

    def test_noun_touchdowns(self):
        self._assert_window_flag("Five Kansas City touchdowns after halftime.")

    def test_noun_td(self):
        self._assert_window_flag("Five Kansas City TD after the half.")

    def test_noun_tds(self):
        self._assert_window_flag("Five Kansas City TDs after the half.")

    def test_noun_field_goal(self):
        self._assert_window_flag("One Kansas City field goal after the break.")

    def test_noun_field_goals(self):
        self._assert_window_flag("Two Kansas City field goals after the break.")

    def test_noun_score(self):
        self._assert_window_flag("One Kansas City score after halftime.")

    def test_noun_scores(self):
        self._assert_window_flag("Five Kansas City scores after halftime.")

    def test_noun_yards_fail_closed(self):
        window = self._window(_review(WALKER_TURNED))
        self.assertTrue(
            any("cannot be verified" in item and "yards" in item for item in window),
            window,
        )

    def test_noun_yard_fail_closed(self):
        window = self._window(
            _review("One Kansas City yard after halftime.")
        )
        self.assertTrue(
            any("cannot be verified" in item and "yard" in item for item in window),
            window,
        )

    def test_noun_sacks_fail_closed(self):
        window = self._window(
            _review("Five Kansas City sacks after halftime.")
        )
        self.assertTrue(
            any("cannot be verified" in item and "sacks" in item for item in window),
            window,
        )

    def test_noun_sack_fail_closed(self):
        window = self._window(
            _review("One Kansas City sack after halftime.")
        )
        self.assertTrue(
            any("cannot be verified" in item and "sack" in item for item in window),
            window,
        )

    def test_noun_points_fail_closed(self):
        window = self._window(
            _review("21 Kansas City points after halftime.")
        )
        self.assertTrue(
            any("cannot be verified" in item and "points" in item for item in window),
            window,
        )

    def test_noun_point_fail_closed(self):
        window = self._window(
            _review("One Kansas City point after halftime.")
        )
        self.assertTrue(
            any("cannot be verified" in item and "point" in item for item in window),
            window,
        )

    def test_noun_takeaways_fail_closed(self):
        window = self._window(
            _review("Three Kansas City takeaways after halftime.")
        )
        self.assertTrue(
            any(
                "cannot be verified" in item and "takeaways" in item
                for item in window
            ),
            window,
        )

    def test_noun_takeaway_fail_closed(self):
        window = self._window(
            _review("One Kansas City takeaway after halftime.")
        )
        self.assertTrue(
            any(
                "cannot be verified" in item and "takeaway" in item
                for item in window
            ),
            window,
        )

    def _assert_fail_closed_noun(self, sentence: str, noun: str):
        window = self._window(_review(sentence))
        self.assertTrue(
            any("cannot be verified" in item and noun in item for item in window),
            window,
        )

    def test_noun_catch_fail_closed(self):
        self._assert_fail_closed_noun(
            "One Kansas City catch after halftime.", "catch"
        )

    def test_noun_catches_fail_closed(self):
        self._assert_fail_closed_noun(
            "Two Kansas City catches after halftime.", "catches"
        )

    def test_noun_carry_fail_closed(self):
        self._assert_fail_closed_noun(
            "One Kansas City carry after halftime.", "carry"
        )

    def test_noun_carries_fail_closed(self):
        self._assert_fail_closed_noun(
            "Two Kansas City carries after halftime.", "carries"
        )

    def test_noun_interception_fail_closed(self):
        self._assert_fail_closed_noun(
            "One Kansas City interception after halftime.", "interception"
        )

    def test_noun_interceptions_fail_closed(self):
        self._assert_fail_closed_noun(
            "Two Kansas City interceptions after halftime.", "interceptions"
        )

    def test_noun_fumble_fail_closed(self):
        self._assert_fail_closed_noun(
            "One Kansas City fumble after halftime.", "fumble"
        )

    def test_noun_fumbles_fail_closed(self):
        self._assert_fail_closed_noun(
            "Two Kansas City fumbles after halftime.", "fumbles"
        )

    def test_noun_turnover_fail_closed(self):
        self._assert_fail_closed_noun(
            "One Kansas City turnover after halftime.", "turnover"
        )

    def test_noun_turnovers_fail_closed(self):
        self._assert_fail_closed_noun(
            "Two Kansas City turnovers after halftime.", "turnovers"
        )

    def test_noun_reception_fail_closed(self):
        self._assert_fail_closed_noun(
            "One Kansas City reception after halftime.", "reception"
        )

    def test_noun_receptions_fail_closed(self):
        self._assert_fail_closed_noun(
            "Two Kansas City receptions after halftime.", "receptions"
        )

    def test_noun_rush_fail_closed(self):
        self._assert_fail_closed_noun(
            "One Kansas City rush after halftime.", "rush"
        )

    def test_noun_rushes_fail_closed(self):
        self._assert_fail_closed_noun(
            "Two Kansas City rushes after halftime.", "rushes"
        )

    def test_noun_completion_fail_closed(self):
        self._assert_fail_closed_noun(
            "One Kansas City completion after halftime.", "completion"
        )

    def test_noun_completions_fail_closed(self):
        self._assert_fail_closed_noun(
            "Two Kansas City completions after halftime.", "completions"
        )

    def test_flipped_fail_closed(self):
        window = self._window(_review(FLIPPED))
        self.assertTrue(
            any("cannot be verified" in item and "yards" in item for item in window),
            window,
        )

    # --- I6/I7 _COUNT_SCOPE + _SCOPE_QUARTERS + rewrite ---

    def test_scope_score_reached_three_empty_issues(self):
        self.assertEqual(
            self._issues(
                _review(
                    "Three Kansas City touchdowns after the score reached 14-12."
                )
            ),
            [],
        )

    def test_scope_trailing_five_empty_window_leftover(self):
        issues = self._issues(
            _review("Five Kansas City touchdowns after trailing 0-6.")
        )
        self.assertEqual(_window_issues(issues), [])

    def test_scope_start_five_empty_issues(self):
        self.assertEqual(
            self._issues(
                _review("Five Kansas City touchdowns after a 0-6 start.")
            ),
            [],
        )

    def test_i3_scope_trailing_three_empty_leftover(self):
        leftovers = _leftover_issues(
            self._issues(
                _review("Three Kansas City touchdowns after trailing 0-6.")
            )
        )
        self.assertEqual(leftovers, [])

    def test_i3_scope_start_three_empty_leftover(self):
        leftovers = _leftover_issues(
            self._issues(
                _review("Three Kansas City touchdowns after a 0-6 start.")
            )
        )
        self.assertEqual(leftovers, [])

    def test_i3_skip_branch_endash_start(self):
        leftovers = _leftover_issues(
            self._issues(
                _review("Three Kansas City touchdowns after a 0–6 start.")
            )
        )
        self.assertEqual(leftovers, [])

    def test_scope_after_halftime_three_empty_issues(self):
        self.assertEqual(
            self._issues(_review("Three Kansas City touchdowns after halftime.")),
            [],
        )

    def test_scope_after_the_half_three_empty_issues(self):
        self.assertEqual(
            self._issues(
                _review("Three Kansas City touchdowns after the half.")
            ),
            [],
        )

    def test_scope_after_the_break_three_empty_issues(self):
        self.assertEqual(
            self._issues(
                _review("Three Kansas City touchdowns after the break.")
            ),
            [],
        )

    def test_scope_since_halftime_three_empty_issues(self):
        self.assertEqual(
            self._issues(
                _review("Three Kansas City touchdowns since halftime.")
            ),
            [],
        )

    def test_scope_since_the_half_three_empty_issues(self):
        self.assertEqual(
            self._issues(
                _review("Three Kansas City touchdowns since the half.")
            ),
            [],
        )

    def test_scope_since_the_break_three_empty_issues(self):
        self.assertEqual(
            self._issues(
                _review("Three Kansas City touchdowns since the break.")
            ),
            [],
        )

    def test_scope_following_halftime_three_empty_issues(self):
        self.assertEqual(
            self._issues(
                _review("Three Kansas City touchdowns following halftime.")
            ),
            [],
        )

    def test_scope_following_the_half_three_empty_issues(self):
        self.assertEqual(
            self._issues(
                _review("Three Kansas City touchdowns following the half.")
            ),
            [],
        )

    def test_scope_following_the_break_three_empty_issues(self):
        self.assertEqual(
            self._issues(
                _review("Three Kansas City touchdowns following the break.")
            ),
            [],
        )

    def test_scope_following_a_nn_three_empty_issues(self):
        self.assertEqual(
            self._issues(
                _review(
                    "Three Kansas City touchdowns following a 14-12 halftime."
                )
            ),
            [],
        )

    def test_scope_turned_three_empty_issues(self):
        self.assertEqual(
            self._issues(
                _review(
                    "Three Kansas City touchdowns turned a 14-12 "
                    "halftime into 35-12."
                )
            ),
            [],
        )

    def test_scope_flipped_three_empty_issues(self):
        self.assertEqual(
            self._issues(
                _review(
                    "Three Kansas City touchdowns flipped a game that was 14-12."
                )
            ),
            [],
        )

    def _scope_of(self, sentence: str):
        match = facts._TD_COUNT.search(sentence) or facts._WINDOW_SCORING_COUNT.search(
            sentence
        )
        self.assertIsNotNone(match)
        return facts._count_scope(sentence, match.start(), match.end())

    def test_scope_quarters_after_halftime(self):
        self.assertEqual(
            self._scope_of("Three Kansas City touchdowns after halftime."),
            frozenset({3, 4}),
        )

    def test_scope_quarters_after_the_half(self):
        self.assertEqual(
            self._scope_of("Three Kansas City touchdowns after the half."),
            frozenset({3, 4}),
        )

    def test_scope_quarters_after_the_break(self):
        self.assertEqual(
            self._scope_of("Three Kansas City touchdowns after the break."),
            frozenset({3, 4}),
        )

    def test_scope_quarters_since_halftime(self):
        self.assertEqual(
            self._scope_of("Three Kansas City touchdowns since halftime."),
            frozenset({3, 4}),
        )

    def test_scope_quarters_since_the_half(self):
        self.assertEqual(
            self._scope_of("Three Kansas City touchdowns since the half."),
            frozenset({3, 4}),
        )

    def test_scope_quarters_since_the_break(self):
        self.assertEqual(
            self._scope_of("Three Kansas City touchdowns since the break."),
            frozenset({3, 4}),
        )

    def test_normalize_scope_after_a_nn_rewrite(self):
        self.assertEqual(
            facts._normalize_scope_token("after a 14-12 halftime"),
            "after halftime",
        )
        self.assertEqual(
            self._scope_of(
                "Five Kansas City touchdowns after a 14-12 halftime."
            ),
            frozenset({3, 4}),
        )
        leftovers = _leftover_issues(self._issues(_review(KAREN_FIVE)))
        self.assertTrue(
            any("touchdown count 5 disagrees with ESPN 3" in item for item in leftovers),
            leftovers,
        )

    def test_normalize_scope_after_a_nn_endash_rewrite(self):
        self.assertEqual(
            facts._normalize_scope_token("after a 14–12 halftime"),
            "after halftime",
        )

    def test_normalize_scope_following_rewrite(self):
        self.assertEqual(
            facts._normalize_scope_token("following halftime"),
            "after halftime",
        )

    def test_normalize_scope_turned_rewrite(self):
        self.assertEqual(
            facts._normalize_scope_token("turned a 14-12 halftime"),
            "after halftime",
        )

    def test_normalize_scope_flipped_rewrite(self):
        self.assertEqual(
            facts._normalize_scope_token("flipped a game that was 14-12"),
            "after halftime",
        )

    # --- I8: Q3 and Q4 independently ---

    def test_window_quarter_3(self):
        # Official 2H = Q3(2)+Q4(1)=3. Claiming 1 flags. Drop Q3 → official 1,
        # this sentence would pass, so the test dies.
        self._assert_window_flag("One Kansas City touchdown after halftime.")

    def test_window_quarter_4(self):
        # Claiming 2 flags (2≠3). Drop Q4 → official 2, this would pass.
        self._assert_window_flag("Two Kansas City touchdowns after halftime.")

    # --- I9: _scoring_plays_verifiable branches ---

    def test_verifiable_recap_none(self):
        self.assertFalse(facts._scoring_plays_verifiable(None))
        window = self._window(_review(KAREN_FIVE), recap=None)
        self.assertTrue(any("cannot be verified" in item for item in window), window)

    def test_verifiable_recap_not_dict(self):
        self.assertFalse(facts._scoring_plays_verifiable(["scoringPlays"]))
        window = self._window(_review(KAREN_FIVE), recap=["scoringPlays"])
        self.assertTrue(any("cannot be verified" in item for item in window), window)

    def test_verifiable_missing_key(self):
        recap = {"oppAbbr": "NYJ", "kc": {"totalYards": "391"}}
        self.assertFalse(facts._scoring_plays_verifiable(recap))
        window = self._window(_review(KAREN_FIVE), recap=recap)
        self.assertTrue(any("cannot be verified" in item for item in window), window)

    def test_verifiable_plays_not_list(self):
        recap = {"scoringPlays": {"team": "KC"}}
        self.assertFalse(facts._scoring_plays_verifiable(recap))
        window = self._window(_review(KAREN_FIVE), recap=recap)
        self.assertTrue(any("cannot be verified" in item for item in window), window)

    def test_verifiable_play_not_dict(self):
        recap = {"scoringPlays": ["not-a-play"]}
        self.assertFalse(facts._scoring_plays_verifiable(recap))
        window = self._window(_review(KAREN_FIVE), recap=recap)
        self.assertTrue(any("cannot be verified" in item for item in window), window)

    def test_verifiable_missing_quarter(self):
        recap = {"scoringPlays": [{"team": "KC", "type": "TD"}]}
        self.assertFalse(facts._scoring_plays_verifiable(recap))
        window = self._window(_review(KAREN_FIVE), recap=recap)
        self.assertTrue(any("cannot be verified" in item for item in window), window)

    # --- wiring ---

    def test_check_review_wires_window_guard(self):
        issues = self._issues(_review(KAREN_FIVE))
        self.assertTrue(
            any("window-bound" in item and "5" in item for item in issues),
            issues,
        )

    # --- I10 adversarial table vs head ---

    def test_adversarial_table_head(self):
        for row in ADVERSARIAL_ROWS:
            with self.subTest(row["id"]):
                got = self._classify(row["sentence"])
                self.assertEqual(got, row["head"], row)

    def test_head_not_weaker_than_develop(self):
        # Head may add flags. The one documented cleaner-on-head row is
        # Three-after-14-12 (develop leftover false-positive). Other rows
        # where develop flags must still flag on head.
        for row in ADVERSARIAL_ROWS:
            with self.subTest(row["id"]):
                if row["develop"] == "pass":
                    continue
                if row["id"] in {
                    "three_after_14_12",
                    "non_second_half",
                    "three_following_the_half",
                    "three_following_the_break",
                    "three_following_a_nn",
                    "three_turned",
                    "three_flipped",
                }:
                    # Documented leftover false-positives on develop:
                    # window-scoped whole-game leftover, or un/non prefix.
                    self.assertEqual(self._classify(row["sentence"]), "pass")
                    continue
                got = self._classify(row["sentence"])
                self.assertIn(got, {"FLAG", "leftover"}, row)

    def test_negation_never_five_not_flagged(self):
        self._assert_window_clean(
            "Kansas City never five touchdowns after halftime."
        )

    def test_non_prefix_does_not_bind(self):
        self._assert_window_clean(
            "Five Kansas City touchdowns in the non-second-half."
        )

    def test_non_after_score_reached_does_not_bind(self):
        self._assert_window_clean(
            "Five Kansas City touchdowns non-after the score reached 14-12."
        )

    def test_following_the_score_reached_does_not_bind(self):
        self._assert_window_clean(
            "Five Kansas City touchdowns following the score reached 14-12."
        )

    def test_after_the_score_had_reached_does_not_bind(self):
        self._assert_window_clean(
            "Five Kansas City touchdowns after the score had reached 14-12."
        )

    def test_after_falling_behind_does_not_bind(self):
        self._assert_window_clean(
            "Five Kansas City touchdowns after falling behind 0-6."
        )

    def test_once_trailing_does_not_bind(self):
        self._assert_window_clean(
            "Five Kansas City touchdowns once trailing 6-0."
        )

    def test_never_trailed_is_negation(self):
        self._assert_window_clean(
            "Kansas City never trailed 6-0 after the score reached 14-12."
        )

    def test_scoring_play_yardage_list_not_volume(self):
        self._assert_window_clean(
            "After halftime the Chiefs added touchdowns of 1, 2, and 6 yards "
            "and won by 23."
        )

    def test_far_fourth_quarter_mention_not_a_bind(self):
        self._assert_window_clean(
            "Fans should weigh whether 399 yards and 6-of-14 on third down "
            "are the identity until a lead survives the fourth quarter."
        )


if __name__ == "__main__":
    unittest.main()
