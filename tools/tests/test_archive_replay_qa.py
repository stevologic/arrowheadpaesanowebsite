"""Archive-replay gates for Week 8 QA misses and #276 mutants."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.chiefs_narrative import collect, facts, generate
from tools.tests import archive_replay
from tools.tests.frozen_slate import frozen_schedule

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load_recap(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


_SLATE_SCORES = {
    "401873022": (3, 20),
    "401873006": (22, 6),
}


def _slate_game(event_id: str) -> dict:
    for game in frozen_schedule():
        if str(game.get("id")) == event_id:
            row = dict(game)
            row["completed"] = True
            pair = _SLATE_SCORES.get(event_id)
            if pair:
                row["kcScore"], row["oppScore"] = pair
            return row
    raise AssertionError(f"missing slate row {event_id}")


def _review(sentence: str, *, opponent: str, score: str, generated_at: str = "") -> dict:
    payload = {
        "lastGameReview": {
            "opponent": opponent,
            "result": "L" if score.startswith("KC 3") else "W",
            "score": score,
            "lede": sentence,
            "analysis": [{"body": sentence}],
        }
    }
    if generated_at:
        payload["generatedAt"] = generated_at
    return payload


class ParsePlayDownDistance(unittest.TestCase):
    def test_parse_plays_reads_espn_start_down(self):
        drives = {
            "previous": [
                {
                    "team": {"abbreviation": "KC"},
                    "plays": [
                        {
                            "period": {"number": 4},
                            "clock": {"displayValue": "3:19"},
                            "statYardage": 3,
                            "start": {"down": 4, "distance": 10},
                            "text": (
                                "(Shotgun) P.Mahomes pass short right to "
                                "T.Kelce to KC 45 for 3 yards (E.Jones)."
                            ),
                        }
                    ],
                }
            ]
        }
        plays = collect.parse_plays(drives)
        self.assertEqual(len(plays), 1)
        self.assertEqual(plays[0]["down"], 4)
        self.assertEqual(plays[0]["distance"], 10)
        self.assertEqual(plays[0]["target"], "T.Kelce")


class DownAndDistanceReplay(unittest.TestCase):
    LAST = _slate_game("401873022")
    RECAP = _load_recap("espn_401873022_recap.json")

    def _issues(self, sentence: str) -> list[str]:
        return facts.check_review(
            _review(
                sentence,
                opponent="Seattle Seahawks",
                score="KC 3–20",
            ),
            self.LAST,
            self.RECAP,
            schedule=frozen_schedule(),
        )

    def test_third_and_short_to_kelce_fails(self):
        sentence = (
            "A late third-and-short to Kelce for three yards ended in a "
            "turnover on downs."
        )
        blob = " ".join(self._issues(sentence))
        self.assertTrue(blob, sentence)
        self.assertIn("down-and-distance", blob)
        self.assertIn("4th-and-10", blob)

    def test_fourth_and_10_to_kelce_passes(self):
        sentence = (
            "A late 4th-and-10 to Kelce for three yards ended in a "
            "turnover on downs."
        )
        self.assertEqual(self._issues(sentence), [], sentence)

    def test_scheme_third_and_long_without_play_yards_passes(self):
        sentence = (
            "Denver will try to live in that third-and-long window after "
            "Seattle won 11 of 16."
        )
        self.assertEqual(self._issues(sentence), [], sentence)


class BoxLeaderReplay(unittest.TestCase):
    SEA_LAST = _slate_game("401873022")
    SEA = _load_recap("espn_401873022_recap.json")
    LAC_LAST = _slate_game("401873006")
    LAC = _load_recap("espn_401873006_recap.json")

    def test_kelce_led_receivers_fails_on_catch_tie(self):
        sentence = (
            "Travis Kelce led the receivers with four catches for 44 yards."
        )
        issues = facts.check_review(
            _review(sentence, opponent="Seattle Seahawks", score="KC 3–20"),
            self.SEA_LAST,
            self.SEA,
            schedule=frozen_schedule(),
        )
        blob = " ".join(issues)
        self.assertTrue(issues, sentence)
        self.assertIn("led the receivers", blob)
        self.assertIn("solo box-score lead", blob)

    def test_kelce_led_team_receiving_yards_fails_on_tie(self):
        sentence = (
            "Kelce still led the team with 44 receiving yards on four catches."
        )
        issues = facts.check_review(
            _review(sentence, opponent="Seattle Seahawks", score="KC 3–20"),
            self.SEA_LAST,
            self.SEA,
            schedule=frozen_schedule(),
        )
        blob = " ".join(issues)
        self.assertTrue(issues, sentence)
        self.assertIn("led the team", blob)

    def test_worthy_solo_chargers_lead_passes(self):
        sentence = "Xavier Worthy led the receivers with seven catches."
        issues = facts.check_review(
            _review(sentence, opponent="Los Angeles Chargers", score="KC 22–6"),
            self.LAC_LAST,
            self.LAC,
            schedule=frozen_schedule(),
        )
        self.assertEqual(issues, [], sentence)


class PredictionMarketReplay(unittest.TestCase):
    def test_polymarket_lede_fails_after_cutover(self):
        narrative = {
            "generatedAt": "2026-10-28T17:50:00+00:00",
            "storyline": {
                "lede": (
                    "Kansas City is a 1.5-point favorite with a championship "
                    "price on Polymarket that has cooled to 10.1 percent."
                )
            },
        }
        blob = " ".join(facts.check_review(narrative, None, None))
        self.assertIn("prediction-market", blob)
        self.assertIn("Polymarket", blob)

    def test_kalshi_and_championship_price_fail(self):
        narrative = {
            "generatedAt": "2026-10-28T17:50:00+00:00",
            "currentState": {
                "thinkAbout": [
                    {
                        "body": "Kalshi has the Chiefs at 17.5 percent to win the AFC."
                    }
                ]
            },
        }
        blob = " ".join(facts.check_review(narrative, None, None))
        self.assertIn("prediction-market", blob)

    def test_draftkings_and_fpi_stay(self):
        narrative = {
            "generatedAt": "2026-10-28T17:50:00+00:00",
            "spotlight": [
                {
                    "body": (
                        "DraftKings has Kansas City minus 1.5. ESPN FPI has "
                        "the Chiefs at 55.7 percent."
                    )
                }
            ],
        }
        self.assertEqual(facts.check_review(narrative, None, None), [])

    def test_pre_cutover_polymarket_is_grandfathered(self):
        narrative = {
            "generatedAt": "2026-10-21T14:46:49+00:00",
            "theEdge": (
                "Polymarket sits at 11.2 percent to win the championship "
                "and 18.5 percent to win the AFC."
            ),
        }
        self.assertEqual(facts.check_review(narrative, None, None), [])

    def test_futures_polymarket_fails_after_cutover(self):
        narrative = {
            "generatedAt": "2026-10-28T17:50:00+00:00",
            "markets": {
                "futures": [
                    {
                        "label": "Pro Football: 2027 Champion",
                        "chiefsPct": 10.1,
                        "source": "Polymarket",
                    }
                ]
            },
        }
        blob = " ".join(facts.check_review(narrative, None, None))
        self.assertIn("prediction-market", blob)
        self.assertIn("markets.futures", blob)


class ChargersPriorMutants(unittest.TestCase):
    def test_fixture_opponent_stays_lac_not_kc(self):
        self.assertEqual(
            archive_replay._FIXTURE_OPPONENT["401873006"],
            ("Los Angeles Chargers", "LAC"),
        )
        self.assertNotEqual(
            archive_replay._FIXTURE_OPPONENT["401873006"][1],
            "KC",
        )
        self.assertEqual(
            generate._FIXTURE_OPPONENT["espn_401873006_recap.json"],
            ("Los Angeles Chargers", "LAC"),
        )
        self.assertNotEqual(
            generate._FIXTURE_OPPONENT["espn_401873006_recap.json"][1],
            "KC",
        )

    def test_label_recap_overwrites_opp_abbr_kc(self):
        labeled = archive_replay._label_recap(
            {"oppAbbr": "KC", "opponent": "Kansas City Chiefs"},
            "401873006",
        )
        self.assertEqual(labeled["oppAbbr"], "LAC")
        self.assertEqual(labeled["opponent"], "Los Angeles Chargers")

    def test_attach_prior_overwrites_opp_abbr_kc(self):
        original = json.loads
        def _loads(raw, *args, **kwargs):
            payload = original(raw, *args, **kwargs)
            if isinstance(payload, dict) and str(payload.get("eventId")) == "401873006":
                payload = dict(payload)
                payload["oppAbbr"] = "KC"
                payload["opponent"] = "Kansas City Chiefs"
            return payload

        recap = {"eventId": "401873022", "oppAbbr": "SEA"}
        with patch(
            "tools.chiefs_narrative.generate.json.loads", side_effect=_loads
        ):
            attached = generate._attach_prior_recap(
                recap,
                "espn_401873022_recap.json",
                FIXTURES,
            )
        prior = attached.get("prior") or {}
        self.assertEqual(prior.get("oppAbbr"), "LAC")
        self.assertEqual(prior.get("opponent"), "Los Angeles Chargers")

    def test_one_chargers_to_raiders_fails_nearest_team(self):
        """A whole-paragraph bind used to let one Raiders swap pass."""
        slate = frozen_schedule()
        preview = {
            "edition": "Week 8 · Preview",
            "phase": {"mode": "preview", "week": 8},
            "lastGameReview": {
                "opponent": "Seattle Seahawks",
                "result": "L",
                "score": "KC 3–20",
                "lede": "Seattle beat Kansas City 20-3.",
            },
            "currentState": {
                "lede": (
                    "The staff's job is to keep the Raiders identity — "
                    "422 yards, 22 first downs, a 22-6 win — from being "
                    "treated as ancient history, and the prior afternoon "
                    "against the Chargers — 150 rushing yards, 272 net "
                    "passing yards, 22 first downs, 30:26 of possession "
                    "in a 22-6 win — is the identity Reid still wants."
                )
            },
        }
        leftover = archive_replay.replay_edition(
            preview, slate, archive_replay.recaps_by_event()
        )
        self.assertTrue(
            any("22-6" in item and "official final" in item for item in leftover),
            leftover,
        )

    def test_two_chargers_mentions_still_bind_22_6(self):
        slate = frozen_schedule()
        preview = {
            "edition": "Week 8 · Preview",
            "phase": {"mode": "preview", "week": 8},
            "lastGameReview": {
                "opponent": "Seattle Seahawks",
                "result": "L",
                "score": "KC 3–20",
                "lede": "Seattle beat Kansas City 20-3.",
            },
            "currentState": {
                "lede": (
                    "The staff's job is to keep the Chargers identity — "
                    "422 yards, 22 first downs, a 22-6 win — from being "
                    "treated as ancient history."
                )
            },
            "storyline": {
                "body": [
                    "The prior afternoon against the Chargers — 150 rushing "
                    "yards, 272 net passing yards, 22 first downs, 30:26 of "
                    "possession in a 22-6 win — is the identity Reid still wants."
                ]
            },
        }
        self.assertEqual(
            archive_replay.replay_edition(
                preview, slate, archive_replay.recaps_by_event()
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
