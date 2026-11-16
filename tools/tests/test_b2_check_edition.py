"""Isolating B2 --check-edition fixtures: TD totals, kickoff/TV, red zone, 3rd down."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tools.chiefs_narrative import edition_merge, facts, generate, review_gate
from tools.tests.archive_replay import load_recap
from tools.tests.frozen_slate import frozen_schedule
from tools.tests.test_window_scoring_counts import LAST_JETS, _review

M_TOTAL = (
    "Walker scored one touchdown all day, the 2-yard run at Q3 0:11."
)
M_AFTER = (
    "Kansas City scored five touchdowns after the score reached 14–12."
)
M_RUSHING = (
    "Walker scored one rushing touchdown on 18 carries."
)
M_RED_ZONE = (
    "A red-zone finish that produced five touchdowns."
)
M_THIRD = "Kansas City converted 7 of 11 third downs."
TRUE_THIRD = "Kansas City converted 6 of 11 third downs."
TRUE_RED_ZONE = "Kansas City was 4-for-4 in the red zone."
TRUE_TRIPS = "Four red-zone trips, all cashed."


def _slate_atl():
    slate = frozen_schedule()
    for game in slate:
        if str(game.get("id")) == "401873045":
            game["completed"] = True
            game["kcScore"] = 35
            game["oppScore"] = 12
        if str(game.get("id")) == "401873058":
            game["completed"] = False
            game["tv"] = "CBS"
            game["date"] = "2026-11-15T18:00:00Z"
            game["kickoff"] = "Sun, Nov 15 · 12:00 PM CT"
    return slate


def _preview(next_game: dict, extra: str = "") -> dict:
    payload = {
        "edition": "2026 Week 10 · Preview",
        "phase": {"type": "regular", "mode": "preview", "week": 10},
        "headline": "Atlanta next.",
        "dek": extra or "Kansas City goes to Atlanta.",
        "nextGame": next_game,
        "lastGameReview": {
            "opponent": "New York Jets",
            "result": "W",
            "score": "KC 35–12",
            "lede": "Kansas City beat the Jets 35-12.",
        },
    }
    return payload


class PlayerTdTotals(unittest.TestCase):
    def setUp(self):
        self.recap = load_recap("401873045")
        self.recap = dict(self.recap)
        self.recap["opponent"] = "New York Jets"
        self.recap["oppAbbr"] = "NYJ"

    def test_m_total_flags_one_vs_espn_three(self):
        issues = facts.check_review(_review(M_TOTAL), LAST_JETS, self.recap)
        self.assertTrue(
            any(
                "touchdown total 1 disagrees with ESPN 3" in item
                and "Walker" in item
                for item in issues
            ),
            issues,
        )

    def test_one_rushing_touchdown_passes(self):
        issues = facts._check_player_td_totals(M_RUSHING, self.recap)
        self.assertEqual(issues, [])

    def test_three_total_passes(self):
        issues = facts._check_player_td_totals(
            "Walker scored three touchdowns all day.",
            self.recap,
        )
        self.assertEqual(issues, [])

    def test_player_td_sum_is_rush_plus_rec(self):
        totals = facts.player_td_totals(self.recap)
        walker = totals.get("walker") or {}
        self.assertEqual(facts._player_td_sum(walker), 3)
        self.assertEqual(walker.get("rush"), 1)
        self.assertEqual(walker.get("rec"), 2)

    def test_player_scored_a_touchdown_flags(self):
        issues = facts._check_player_td_totals(
            "Walker scored a touchdown all day.",
            self.recap,
        )
        self.assertTrue(
            any("touchdown total 1 disagrees with ESPN 3" in item for item in issues),
            issues,
        )

    def test_bare_player_td_all_day_flags(self):
        issues = facts._check_player_td_totals(
            "Walker had one touchdown all day.",
            self.recap,
        )
        self.assertTrue(
            any("touchdown total 1 disagrees with ESPN 3" in item for item in issues),
            issues,
        )

    def test_typed_rushing_td_flags(self):
        issues = facts._check_player_td_totals(
            "Walker had two rushing touchdowns.",
            self.recap,
        )
        self.assertTrue(
            any("rushing" in item and "2" in item and "1" in item for item in issues),
            issues,
        )

    def test_headline_td_perf_two_flags(self):
        issues = facts.source_td_conflicts(
            [
                {
                    "title": "Walker's two-touchdown performance",
                    "publisher": "Chiefs.com",
                }
            ],
            self.recap,
        )
        self.assertTrue(
            any("total TDs 2 disagrees with ESPN 3" in item for item in issues),
            issues,
        )
        self.assertIsNotNone(
            facts._HEADLINE_TD_PERF.search("Walker's two-touchdown performance")
        )

    def test_headline_td_bare_four_flags(self):
        issues = facts.source_td_conflicts(
            [
                {
                    "title": "Four-touchdown performance from Walker",
                    "publisher": "Chiefs.com",
                }
            ],
            self.recap,
        )
        self.assertTrue(
            any("total TDs 4 disagrees with ESPN 3" in item for item in issues),
            issues,
        )

    def test_whole_game_td_scope_all_game(self):
        issues = facts._check_player_td_totals(
            "Walker had one touchdown all game.",
            self.recap,
        )
        self.assertTrue(
            any("touchdown total 1 disagrees with ESPN 3" in item for item in issues),
            issues,
        )

    def test_scoring_td_line_pass_is_rec(self):
        totals = facts.player_td_totals(
            {
                "scoring": ["— KC Hunt 12 Yd pass"],
                "touches": [],
                "plays": [],
                "scoringPlays": [],
            }
        )
        self.assertEqual((totals.get("hunt") or {}).get("rec"), 1)
        self.assertEqual((totals.get("hunt") or {}).get("rush"), 0)

    def test_pbp_pass_td_is_rec(self):
        totals = facts.player_td_totals(
            {
                "touches": [],
                "scoring": [],
                "plays": [
                    {"text": "P.Mahomes pass to H.Hunt for 12 yards TOUCHDOWN"}
                ],
                "scoringPlays": [],
            }
        )
        self.assertEqual((totals.get("hunt") or {}).get("rec"), 1)
        self.assertEqual((totals.get("hunt") or {}).get("rush"), 0)

    def test_pbp_rush_td_is_rush(self):
        totals = facts.player_td_totals(
            {
                "touches": [],
                "scoring": [],
                "plays": [{"text": "K.Hunt 1 Yd run TOUCHDOWN"}],
                "scoringPlays": [],
            }
        )
        self.assertEqual((totals.get("hunt") or {}).get("rush"), 1)
        self.assertEqual((totals.get("hunt") or {}).get("rec"), 0)

    def test_add_player_td_rush_vs_rec(self):
        out: dict = {}
        facts._add_player_td(out, "Hunt", "rush")
        self.assertEqual(out["hunt"]["rush"], 1)
        self.assertEqual(out["hunt"]["rec"], 0)
        facts._add_player_td(out, "Hunt", "rec")
        self.assertEqual(out["hunt"]["rec"], 1)
        self.assertEqual(out["hunt"]["total"], 2)


class SourceTdScope(unittest.TestCase):
    def setUp(self):
        self.recap = load_recap("401873045")

    def test_rushing_leader_vs_all_purpose_is_not_a_conflict(self):
        news = [
            {
                "title": "Walker's Three-Touchdown Performance",
                "publisher": "Chiefs.com",
            }
        ]
        self.assertEqual(facts.source_td_conflicts(news, self.recap), [])

    def test_rushing_headline_compares_to_rushing_tds(self):
        news = [
            {
                "title": "Walker's two-rushing-touchdown performance",
                "publisher": "Chiefs.com",
            }
        ]
        issues = facts.source_td_conflicts(news, self.recap)
        self.assertTrue(
            any("rushing TDs 2 disagrees with ESPN 1" in item for item in issues),
            issues,
        )

    def test_all_purpose_four_is_a_conflict(self):
        news = [
            {
                "title": "Walker's Four-Touchdown Performance",
                "publisher": "Chiefs.com",
            }
        ]
        issues = facts.source_td_conflicts(news, self.recap)
        self.assertTrue(
            any("total TDs 4 disagrees with ESPN 3" in item for item in issues),
            issues,
        )


class NextKickoffTv(unittest.TestCase):
    def test_m_fox_flags_tv(self):
        narrative = _preview(
            {
                "label": "Week 10 · Sun, Nov 15 · 12:00 PM CT",
                "opponent": "Atlanta Falcons",
                "tv": "FOX",
            }
        )
        issues = facts._check_next_kickoff_tv(narrative, _slate_atl())
        self.assertTrue(
            any("TV FOX disagrees with ESPN CBS" in item for item in issues),
            issues,
        )

    def test_m_fox_flags_kickoff(self):
        narrative = _preview(
            {
                "label": "Week 10 · Sun, Nov 15 · 3:25 PM CT",
                "opponent": "Atlanta Falcons",
                "tv": "CBS",
            }
        )
        issues = facts._check_next_kickoff_tv(narrative, _slate_atl())
        self.assertTrue(
            any("kickoff" in item and "3:25" in item for item in issues),
            issues,
        )

    def test_m_fox_flags_tv_and_kickoff(self):
        narrative = _preview(
            {
                "label": "Week 10 · Sun, Nov 15 · 3:25 PM CT",
                "opponent": "Atlanta Falcons",
                "tv": "FOX",
                "note": "Kickoff Sun Nov 15, 3:25 PM CT on FOX.",
            }
        )
        issues = facts._check_next_kickoff_tv(narrative, _slate_atl())
        self.assertTrue(
            any("TV FOX disagrees with ESPN CBS" in item for item in issues),
            issues,
        )
        self.assertTrue(
            any("kickoff" in item and "3:25" in item for item in issues),
            issues,
        )

    def test_espn_prime_time_is_not_amazon_prime(self):
        narrative = {
            "nextGame": {
                "label": "Week 1 · Mon Sep 14",
                "opponent": "Denver Broncos",
                "tv": "ESPN",
                "note": "AFC West opener in ESPN prime time.",
            }
        }
        self.assertEqual(
            facts._check_next_kickoff_tv(narrative, frozen_schedule()),
            [],
        )

    def test_true_cbs_noon_passes(self):
        narrative = _preview(
            {
                "label": "Week 10 · Sun, Nov 15 · 12:00 PM CT",
                "opponent": "Atlanta Falcons",
                "tv": "CBS",
                "note": "Kickoff Sun Nov 15, 12:00 PM CT on CBS.",
            }
        )
        self.assertEqual(facts._check_next_kickoff_tv(narrative, _slate_atl()), [])

    def test_preseason_week_does_not_use_regular_rematch(self):
        narrative = {
            "nextGame": {
                "label": "Week 4 · Sat Aug 29",
                "opponent": "vs Seattle Seahawks",
                "tv": "ESPN Unlmtd",
            }
        }
        self.assertEqual(
            facts._check_next_kickoff_tv(narrative, frozen_schedule()),
            [],
        )

    def test_completed_week_keeps_its_own_slate_row(self):
        narrative = {
            "nextGame": {
                "label": "Week 4 · Sun, Oct 4 · 3:25 PM CT",
                "opponent": "Las Vegas Raiders",
                "tv": "CBS",
                "note": "Sun Oct 4, 3:25 PM CT",
            }
        }
        self.assertEqual(
            facts._check_next_kickoff_tv(narrative, frozen_schedule()),
            [],
        )

    def test_tv_matches_is_like_with_like(self):
        self.assertTrue(facts._tv_matches("CBS", "CBS"))
        self.assertTrue(facts._tv_matches("ESPN", "ESPN Unlmtd"))
        self.assertTrue(facts._tv_matches("NFL Net", "NFL Network"))
        self.assertFalse(facts._tv_matches("FOX", "CBS"))
        self.assertFalse(facts._tv_matches("NFL Net", "Prime Video"))
        self.assertFalse(facts._kickoff_matches("3:25 PM CT", "12:00 PM CT"))
        self.assertTrue(facts._kickoff_matches("12:00 PM CT", "12:00 PM CT"))

    def test_kickoff_am_pm_variants(self):
        self.assertTrue(facts._kickoff_matches("12:00 P.M. CT", "12:00 PM CT"))
        self.assertTrue(facts._kickoff_matches("12:00 pm CT", "12:00 PM CT"))
        self.assertFalse(facts._kickoff_matches("12:00 AM CT", "12:00 PM CT"))

    def test_tv_aliases_nbc_peacock_and_prime_video(self):
        self.assertEqual(facts._norm_tv("NBC"), "nbc")
        self.assertEqual(facts._norm_tv("Prime Video"), "prime")
        self.assertFalse(facts._tv_matches("Prime Video", "NBC"))
        self.assertFalse(facts._tv_matches("FOX", "Prime Video"))
        self.assertEqual(facts._norm_tv("Peacock"), "nbc")
        self.assertTrue(facts._tv_matches("Peacock", "NBC"))
        self.assertEqual(facts._norm_tv("ABC"), "abc")
        self.assertEqual(facts._norm_tv("NFLN"), "nfl network")
        self.assertEqual(facts._norm_tv("Amazon"), "prime")
        self.assertTrue(facts._tv_matches("Amazon", "Prime Video"))
        self.assertEqual(facts._norm_tv("ESPN Unlmtd"), "espn")

    def test_tv_espn_is_not_espn2_substring(self):
        self.assertFalse(facts._tv_matches("ESPN", "ESPN2"))
        self.assertFalse(facts._tv_matches("ESPN2", "ESPN"))


class RedZoneThirdDown(unittest.TestCase):
    def setUp(self):
        self.recap = load_recap("401873045")
        self.recap = dict(self.recap)
        kc = dict(self.recap.get("kc") or {})
        kc["redZoneEff"] = "4-4"
        kc["thirdDownEff"] = "6-11"
        self.recap["kc"] = kc
        opp = dict(self.recap.get("opp") or {})
        opp["thirdDownEff"] = "2-12"
        self.recap["opp"] = opp

    def test_five_red_zone_tds_flags(self):
        issues = facts._check_red_zone_counts(M_RED_ZONE, self.recap)
        self.assertTrue(
            any(
                "red-zone touchdown count 5 disagrees with ESPN 4" in item
                for item in issues
            ),
            issues,
        )

    def test_true_red_zone_passes(self):
        self.assertEqual(
            facts._check_red_zone_counts(TRUE_RED_ZONE, self.recap),
            [],
        )
        self.assertEqual(
            facts._check_red_zone_counts(TRUE_TRIPS, self.recap),
            [],
        )

    def test_red_zone_trips_flag(self):
        issues = facts._check_red_zone_counts(
            "Five red-zone trips against the Jets.", self.recap
        )
        self.assertTrue(
            any("red-zone trips 5 disagrees with ESPN 4" in item for item in issues),
            issues,
        )

    def test_third_down_seven_of_eleven_flags(self):
        issues = facts._check_third_down_counts(M_THIRD, self.recap, LAST_JETS)
        self.assertTrue(
            any(
                "third-down conversions 7-of-11 disagree with ESPN 6-11" in item
                for item in issues
            ),
            issues,
        )

    def test_true_third_down_passes(self):
        self.assertEqual(
            facts._check_third_down_counts(TRUE_THIRD, self.recap, LAST_JETS),
            [],
        )
        self.assertEqual(
            facts._check_third_down_counts(
                "New York went 2 of 12 on third down.", self.recap, LAST_JETS
            ),
            [],
        )

    def test_first_downs_near_third_downs_are_not_conversions(self):
        sentence = (
            "Miami still won first downs 19–18, third downs 8-of-16 to 3-of-7."
        )
        last = {
            "opponent": "Miami Dolphins",
            "opponentAbbr": "MIA",
            "opponentShort": "Dolphins",
        }
        recap = dict(self.recap)
        recap["oppAbbr"] = "MIA"
        recap["kc"] = dict(recap["kc"], thirdDownEff="3-7")
        recap["opp"] = dict(recap["opp"], thirdDownEff="8-16")
        self.assertEqual(
            facts._check_third_down_counts(sentence, recap, last),
            [],
        )

    def test_score_24_10_near_third_down_is_not_a_conversion(self):
        sentence = (
            "KC 24–10. The unit is 3-of-7 on third down and 18 first downs "
            "against Miami’s 8-of-16 and 19 first downs."
        )
        last = {
            "opponent": "Miami Dolphins",
            "opponentAbbr": "MIA",
            "opponentShort": "Dolphins",
        }
        recap = dict(self.recap)
        recap["oppAbbr"] = "MIA"
        recap["kc"] = dict(recap["kc"], thirdDownEff="3-7")
        recap["opp"] = dict(recap["opp"], thirdDownEff="8-16")
        self.assertEqual(
            facts._check_third_down_counts(sentence, recap, last),
            [],
        )

    def test_score_then_third_down_heading_is_not_a_conversion(self):
        recap = dict(self.recap)
        recap["kc"] = dict(recap["kc"], thirdDownEff="3-7")
        self.assertEqual(
            facts._check_third_down_counts(
                "KC 24–10\nThird down and the 25:39 problem",
                recap,
                LAST_JETS,
            ),
            [],
        )

    def test_if_atlanta_2_of_12_is_not_kc(self):
        self.assertEqual(
            facts._check_third_down_counts(
                "If Atlanta is 2 of 12 on third down, take the points.",
                self.recap,
                LAST_JETS,
            ),
            [],
        )

    def test_red_zone_eff_5_of_4_flags(self):
        issues = facts._check_red_zone_counts(
            "Kansas City was 5-of-4 in the red zone.",
            self.recap,
            LAST_JETS,
        )
        self.assertTrue(
            any("red-zone efficiency" in item and "5-for-4" in item for item in issues),
            issues,
        )

    def test_red_zone_space_separator_flags(self):
        issues = facts._check_red_zone_counts(
            "A red zone finish that produced five touchdowns.",
            self.recap,
            LAST_JETS,
        )
        self.assertTrue(
            any("red-zone touchdown count 5 disagrees with ESPN 4" in item for item in issues),
            issues,
        )

    def test_red_zone_trips_five_flags(self):
        issues = facts._check_red_zone_counts(
            "Five red-zone trips, all cashed.",
            self.recap,
            LAST_JETS,
        )
        self.assertTrue(
            any("red-zone trips 5 disagrees with ESPN 4" in item for item in issues),
            issues,
        )

    def test_red_zone_tds_not_trips(self):
        recap = dict(self.recap)
        recap["kc"] = dict(recap["kc"], redZoneEff="4-6")
        self.assertEqual(
            facts._check_red_zone_counts(
                "A red-zone finish that produced four touchdowns.",
                recap,
                LAST_JETS,
            ),
            [],
        )
        issues = facts._check_red_zone_counts(
            "Five red-zone trips against the Jets.",
            recap,
            LAST_JETS,
        )
        self.assertTrue(
            any("red-zone trips 5 disagrees with ESPN 6" in item for item in issues),
            issues,
        )

    def test_opponent_red_zone_is_not_kc(self):
        self.assertEqual(
            facts._check_red_zone_counts(
                "Five Jets touchdowns in the red zone.",
                self.recap,
                LAST_JETS,
            ),
            [],
        )

    def test_red_zone_fg_kind_is_not_td(self):
        self.assertEqual(
            facts._check_red_zone_counts(
                "Kansas City kicked five field goals in the red zone.",
                self.recap,
                LAST_JETS,
            ),
            [],
        )

    def test_red_zone_score_kind_flags(self):
        issues = facts._check_red_zone_counts(
            "Kansas City scored five scores in the red zone.",
            self.recap,
            LAST_JETS,
        )
        self.assertTrue(
            any("red-zone touchdown count 5 disagrees with ESPN 4" in item for item in issues),
            issues,
        )

    def test_third_down_n_of_m_words(self):
        issues = facts._check_third_down_counts(
            "Kansas City converted 7 of 11 third downs.",
            self.recap,
            LAST_JETS,
        )
        self.assertTrue(
            any("7-of-11" in item and "6-11" in item for item in issues),
            issues,
        )

    def test_third_down_singular(self):
        issues = facts._check_third_down_counts(
            "Kansas City converted 7 of 11 on third down.",
            self.recap,
            LAST_JETS,
        )
        self.assertTrue(
            any("7-of-11" in item for item in issues),
            issues,
        )

    def test_third_down_n_hyphen_m(self):
        issues = facts._check_third_down_counts(
            "Kansas City converted 7-11 on third down.",
            self.recap,
            LAST_JETS,
        )
        self.assertTrue(
            any("7-of-11" in item for item in issues),
            issues,
        )

    def test_third_down_not_across_newline(self):
        self.assertEqual(
            facts._check_third_down_counts(
                "Kansas City converted 7 of 11\nthird downs.",
                self.recap,
                LAST_JETS,
            ),
            [],
        )

    def test_third_down_multiple_pairs(self):
        issues = facts._check_third_down_counts(
            "Kansas City third downs 6 of 11 to 3 of 12.",
            self.recap,
            LAST_JETS,
        )
        self.assertTrue(
            any("opponent third-down" in item and "3-of-12" in item for item in issues),
            issues,
        )

    def test_rightmost_team_seattle_third_down(self):
        last = {
            "opponent": "Seattle Seahawks",
            "opponentAbbr": "SEA",
            "opponentShort": "Seahawks",
        }
        recap = dict(self.recap)
        recap["oppAbbr"] = "SEA"
        recap["opp"] = dict(recap.get("opp") or {}, thirdDownEff="4-12")
        recap["kc"] = dict(recap.get("kc") or {}, thirdDownEff="6-11")
        issues = facts._check_third_down_counts(
            "Seattle's 4 of 11 on third down was the problem.",
            recap,
            last,
        )
        self.assertTrue(
            any("opponent third-down" in item and "4-of-11" in item for item in issues),
            issues,
        )
        self.assertEqual(
            facts._check_third_down_counts(
                "Seattle's 4 of 12 on third down was the problem.",
                recap,
                last,
            ),
            [],
        )
        issues = facts._check_third_down_counts(
            "Seattle's 3 of 12 on third down was the problem.",
            recap,
            last,
        )
        self.assertTrue(
            any("opponent third-down" in item and "3-of-12" in item for item in issues),
            issues,
        )


class HoldAutomergePaths(unittest.TestCase):
    def _root(self, hold: bool) -> Path:
        tmp = Path(tempfile.mkdtemp())
        data = tmp / "data"
        data.mkdir()
        (data / "narrative_repair.json").write_text(
            json.dumps({"holdAutomerge": hold}) + "\n", encoding="utf-8"
        )
        return tmp

    def test_inspect_pr_refuses_hold_automerge(self):
        held = self._root(True)
        with patch.object(edition_merge, "fetch_pr") as fetch:
            result = edition_merge.inspect_pr("12", root=held)
        fetch.assert_not_called()
        self.assertTrue(result["refused"])
        self.assertIn("holdAutomerge", result["reason"])

    def test_inspect_pr_open_when_hold_false(self):
        open_root = self._root(False)
        pr = {
            "number": 12,
            "url": "https://example.test/12",
            "labels": [],
            "reviews": [{"state": "APPROVED", "commit": "abc"}],
            "reviewDecision": "APPROVED",
            "headRefOid": "abc",
        }
        with patch.object(edition_merge, "fetch_pr", return_value=pr):
            with patch.object(edition_merge, "fetch_review_threads", return_value=[]):
                result = edition_merge.inspect_pr("12", root=open_root)
        self.assertFalse(result["refused"])

    def test_hold_repair_refuses(self):
        held = self._root(True)
        err = io.StringIO()
        with patch("sys.stderr", err):
            rc = review_gate.main(["--hold-repair", "--root", str(held)])
        self.assertEqual(rc, 1)
        self.assertIn("holdAutomerge", err.getvalue())
        open_root = self._root(False)
        self.assertEqual(
            review_gate.main(["--hold-repair", "--root", str(open_root)]),
            0,
        )

    def test_automerge_refuses_hold(self):
        held = self._root(True)
        err = io.StringIO()
        with patch("sys.stderr", err):
            rc = review_gate.main(["--automerge", "--root", str(held)])
        self.assertEqual(rc, 1)
        self.assertIn("holdAutomerge", err.getvalue())

    def test_load_repair_missing_is_held(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "data").mkdir()
        loaded = edition_merge.load_repair(tmp)
        self.assertEqual(loaded.get("holdAutomerge"), True)
        self.assertTrue(edition_merge.repair_holds_automerge(tmp))

    def test_load_repair_invalid_json_is_held(self):
        tmp = self._root(False)
        (tmp / "data" / "narrative_repair.json").write_text("{", encoding="utf-8")
        self.assertTrue(edition_merge.repair_holds_automerge(tmp))

    def test_load_repair_non_object_is_held(self):
        tmp = self._root(False)
        (tmp / "data" / "narrative_repair.json").write_text("[]\n", encoding="utf-8")
        self.assertTrue(edition_merge.repair_holds_automerge(tmp))

    def test_load_repair_unreadable_is_held(self):
        tmp = self._root(False)
        repair = tmp / "data" / "narrative_repair.json"
        real = Path.read_text

        def boom(self, *args, **kwargs):
            if self == repair:
                raise OSError("boom")
            return real(self, *args, **kwargs)

        with patch.object(Path, "read_text", boom):
            self.assertTrue(edition_merge.repair_holds_automerge(tmp))

    def test_hold_repair_missing_repair_file_is_held(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "data").mkdir()
        err = io.StringIO()
        with patch("sys.stderr", err):
            rc = review_gate.main(["--hold-repair", "--root", str(tmp)])
        self.assertEqual(rc, 1)
        self.assertIn("holdAutomerge", err.getvalue())

    def test_ci_gates_runs_hold_repair(self):
        ci = (
            Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"
        ).read_text(encoding="utf-8")
        gates = ci.split("automerge:")[0]
        self.assertIn("review_gate --hold-repair", gates)
        self.assertLess(
            gates.index("review_gate --pages"),
            gates.index("review_gate --hold-repair"),
        )


class CheckEditionRc(unittest.TestCase):
    def _run(self, narrative: dict) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "narrative.json"
            path.write_text(json.dumps(narrative) + "\n", encoding="utf-8")
            buf = io.StringIO()
            err = io.StringIO()
            with patch.object(generate.config, "NARRATIVE_JSON", path), patch.object(
                generate, "check_published_edition",
                side_effect=lambda: facts.check_review(
                    narrative,
                    LAST_JETS,
                    load_recap("401873045"),
                    schedule=_slate_atl(),
                    changed=True,
                ),
            ):
                with redirect_stdout(buf), patch("sys.stderr", err):
                    rc = generate.main(["--check-edition"])
            return rc, buf.getvalue() + err.getvalue()

    def test_m_after_check_edition_rc_1(self):
        narrative = _review(M_AFTER)
        rc, blob = self._run(narrative)
        self.assertEqual(rc, 1, blob)
        self.assertIn("window-bound", blob)
        self.assertIn("5", blob)
        self.assertIn("3", blob)

    def test_m_total_check_edition_rc_1(self):
        rc, blob = self._run(_review(M_TOTAL))
        self.assertEqual(rc, 1, blob)
        self.assertIn("ESPN 3", blob)

    def test_m_fox_check_edition_rc_1(self):
        narrative = _preview(
            {
                "label": "Week 10 · Sun, Nov 15 · 3:25 PM CT",
                "opponent": "Atlanta Falcons",
                "tv": "FOX",
            }
        )
        rc, blob = self._run(narrative)
        self.assertEqual(rc, 1, blob)
        self.assertTrue("FOX" in blob and "CBS" in blob, blob)

    def test_true_rushing_and_five_still_rc_0(self):
        narrative = _review(
            "Walker scored one rushing touchdown. Five Kansas City touchdowns "
            "after a 0-6 start."
        )
        rc, blob = self._run(narrative)
        self.assertEqual(rc, 0, blob)


class N3ChangedEditions(unittest.TestCase):
    """Changed archive files and live==latest must fail --check-edition / replay."""

    LIVE_SLUG = "2026-11-10-1726"
    ARCHIVE_SLUG = "2026-11-09-1200"

    def _payload(self, *, slug: str, generated_at: str, strategies: list[str]) -> dict:
        return {
            "slug": slug,
            "generatedAt": generated_at,
            "edition": "2026 Week 10 · Preview",
            "phase": {"type": "regular", "mode": "preview", "week": 10},
            "headline": "Atlanta next.",
            "dek": "Kansas City goes to Atlanta.",
            "theEdge": "Kansas City goes to Atlanta.",
            "nextGame": {
                "label": "Week 10 · Sun, Nov 15 · 12:00 PM CT",
                "opponent": "Atlanta Falcons",
                "tv": "CBS",
                "kickoff": "Sun, Nov 15 · 12:00 PM CT",
            },
            "lastGameReview": {
                "opponent": "New York Jets",
                "result": "W",
                "score": "KC 35–12",
                "lede": "Kansas City beat the Jets 35-12.",
                "analysis": ["Kelce scored on a 1-yard rush."],
                "whatWorked": ["Walker scored one rushing touchdown."],
                "whatDidnt": ["Two turnovers."],
            },
            "strategies": strategies,
        }

    def _run(
        self,
        argv: list[str],
        *,
        archive_changed: bool,
        live_differs: bool,
        archive_lie: bool = True,
        latest_lie: bool = False,
        git_unknown: bool = False,
        unreadable: bool = False,
        git_bare: bool = False,
        live_lie: bool = False,
    ) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "data"
            editions = data / "narrative_editions"
            editions.mkdir(parents=True)
            live_strategies = (
                ["A red-zone finish that produced five touchdowns."]
                if live_lie
                else ["Protect the pocket."]
            )
            live = self._payload(
                slug=self.LIVE_SLUG,
                generated_at="2026-11-10T17:26:00Z",
                strategies=live_strategies,
            )
            latest = dict(live)
            archive_strategies = (
                ["A red-zone finish that produced five touchdowns."]
                if archive_lie
                else ["Protect the pocket."]
            )
            archive = self._payload(
                slug=self.ARCHIVE_SLUG,
                generated_at="2026-11-09T12:00:00Z",
                strategies=archive_strategies,
            )
            if live_differs:
                live = dict(live)
                live["headline"] = "A different live headline."
            if latest_lie:
                latest = dict(latest)
                latest["strategies"] = [
                    "A red-zone finish that produced five touchdowns."
                ]
            (data / "narrative.json").write_text(
                json.dumps(live) + "\n", encoding="utf-8"
            )
            (editions / f"{self.LIVE_SLUG}.json").write_text(
                json.dumps(latest) + "\n", encoding="utf-8"
            )
            (editions / f"{self.ARCHIVE_SLUG}.json").write_text(
                json.dumps(archive) + "\n", encoding="utf-8"
            )
            if unreadable:
                (editions / "broken.json").write_text("{", encoding="utf-8")
            changed: set[str] | None
            if git_unknown:
                changed = None
            else:
                changed = set()
                editions_rel = "data/" + "narrative" + "_editions"
                if archive_changed:
                    changed.add(f"{editions_rel}/{self.ARCHIVE_SLUG}.json")
                if latest_lie or live_lie:
                    changed.add(f"{editions_rel}/{self.LIVE_SLUG}.json")
                if unreadable:
                    changed.add(f"{editions_rel}/broken.json")
                if git_bare:
                    changed.add(f"{self.ARCHIVE_SLUG}.json")
            buf = io.StringIO()
            err = io.StringIO()
            with patch.object(
                generate.config, "NARRATIVE_JSON", data / "narrative.json"
            ), patch.object(
                generate.config, "EDITIONS_DIR", editions
            ), patch.object(
                generate.config, "DATA_DIR", data
            ), patch.object(
                generate.config, "REPAIR_JSON", data / "narrative_repair.json"
            ), patch.object(
                facts, "changed_edition_paths", return_value=changed
            ), patch.object(
                generate.hero_posts, "validate_sidecar", return_value=[]
            ), patch.object(
                generate.press_conferences, "check_embeds", return_value=[]
            ), patch.object(
                generate.locker_room, "check_posts", return_value=[]
            ), patch.object(
                facts, "check_diagram_captions", return_value=[]
            ):
                with redirect_stdout(buf), patch("sys.stderr", err):
                    rc = generate.main(argv)
            return rc, buf.getvalue() + err.getvalue()

    def test_n3_archived_false_count_fails_check_edition(self):
        rc_check, blob_check = self._run(
            ["--check-edition"], archive_changed=True, live_differs=False
        )
        rc_replay, blob_replay = self._run(
            ["--gate-replay"], archive_changed=True, live_differs=False
        )
        self.assertEqual(rc_check, 1, blob_check)
        self.assertEqual(rc_replay, 1, blob_replay)
        for blob in (blob_check, blob_replay):
            self.assertIn(f"{self.ARCHIVE_SLUG}.json", blob)
            self.assertIn("five touchdowns", blob)
            self.assertIn("ESPN 4", blob)
        self.assertNotIn("does not equal latest edition", blob_check)

    def test_n3_live_differs_from_latest_fails(self):
        rc_check, blob_check = self._run(
            ["--check-edition"],
            archive_changed=False,
            live_differs=True,
            archive_lie=True,
        )
        rc_replay, blob_replay = self._run(
            ["--gate-replay"],
            archive_changed=False,
            live_differs=True,
            archive_lie=True,
        )
        self.assertEqual(rc_check, 1, blob_check)
        self.assertEqual(rc_replay, 1, blob_replay)
        for blob in (blob_check, blob_replay):
            self.assertIn("does not equal latest edition", blob)
            self.assertIn(f"{self.LIVE_SLUG}.json", blob)
        self.assertNotIn("five touchdowns", blob_check)

    def test_n3_unchanged_archive_lie_is_grandfathered(self):
        rc, blob = self._run(
            ["--check-edition"], archive_changed=False, live_differs=False
        )
        self.assertEqual(rc, 0, blob)
        rc_replay, blob_replay = self._run(
            ["--gate-replay"], archive_changed=False, live_differs=False
        )
        self.assertEqual(rc_replay, 0, blob_replay)

    def test_n3_latest_edition_false_count_fails(self):
        rc, blob = self._run(
            ["--check-edition"],
            archive_changed=False,
            live_differs=False,
            archive_lie=False,
            latest_lie=True,
        )
        self.assertEqual(rc, 1, blob)
        self.assertIn(f"{self.LIVE_SLUG}.json", blob)
        self.assertIn("five touchdowns", blob)

    def test_n3_unreadable_edition_fails(self):
        rc, blob = self._run(
            ["--check-edition"],
            archive_changed=False,
            live_differs=False,
            archive_lie=False,
            unreadable=True,
        )
        self.assertEqual(rc, 1, blob)
        self.assertIn("unreadable", blob)

    def test_n3_unknown_git_scans_archives(self):
        rc, blob = self._run(
            ["--check-edition"],
            archive_changed=False,
            live_differs=False,
            archive_lie=True,
            git_unknown=True,
        )
        self.assertEqual(rc, 1, blob)
        self.assertIn("five touchdowns", blob)

    def test_n3_bare_name_changed_path_is_scanned(self):
        rc, blob = self._run(
            ["--check-edition"],
            archive_changed=False,
            live_differs=False,
            archive_lie=True,
            git_bare=True,
        )
        self.assertEqual(rc, 1, blob)
        self.assertIn("five touchdowns", blob)
        self.assertIn(f"{self.ARCHIVE_SLUG}.json", blob)

    def test_n3_live_twin_skip_when_equal(self):
        rc, blob = self._run(
            ["--check-edition"],
            archive_changed=False,
            live_differs=False,
            archive_lie=False,
            live_lie=True,
        )
        self.assertEqual(rc, 1, blob)
        self.assertIn("five touchdowns", blob)
        self.assertNotIn(f"{self.LIVE_SLUG}.json:", blob)

    def test_latest_edition_z_vs_offset_sort(self):
        with tempfile.TemporaryDirectory() as tmp:
            editions = Path(tmp) / "editions"
            editions.mkdir()
            older = {
                "generatedAt": "2026-11-10T13:00:00+01:00",
                "slug": "aaa",
            }
            newer = {
                "generatedAt": "2026-11-10T12:30:00Z",
                "slug": "zzz",
            }
            (editions / "aaa.json").write_text(
                json.dumps(older) + "\n", encoding="utf-8"
            )
            (editions / "zzz.json").write_text(
                json.dumps(newer) + "\n", encoding="utf-8"
            )
            with patch.object(generate.config, "EDITIONS_DIR", editions):
                latest = generate._latest_edition_file()
            self.assertIsNotNone(latest)
            self.assertEqual(latest.name, "zzz.json")

    def test_edition_paths_aligned_raises_off_data_dir(self):
        with patch.object(
            generate.config, "NARRATIVE_JSON", Path("/tmp/narrative.json")
        ):
            with self.assertRaises(generate.EditionPathError) as caught:
                generate._edition_paths_aligned()
        self.assertIn("DATA_DIR", str(caught.exception))

    def test_check_edition_errors_when_paths_misaligned(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "narrative.json"
            path.write_text(
                json.dumps(
                    self._payload(
                        slug=self.LIVE_SLUG,
                        generated_at="2026-11-10T17:26:00Z",
                        strategies=["Protect the pocket."],
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            buf = io.StringIO()
            err = io.StringIO()
            with patch.object(generate.config, "NARRATIVE_JSON", path), patch.object(
                generate.hero_posts, "validate_sidecar", return_value=[]
            ), patch.object(
                generate.press_conferences, "check_embeds", return_value=[]
            ), patch.object(
                generate.locker_room, "check_posts", return_value=[]
            ), patch.object(
                facts, "check_diagram_captions", return_value=[]
            ):
                with redirect_stdout(buf), patch("sys.stderr", err):
                    rc = generate.main(["--check-edition"])
            blob = buf.getvalue() + err.getvalue()
            self.assertEqual(rc, 1, blob)
            self.assertIn("DATA_DIR", blob)
