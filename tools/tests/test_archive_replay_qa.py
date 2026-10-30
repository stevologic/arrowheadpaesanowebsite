"""Archive-replay gates for Week 8 QA misses and #276 mutants."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tools.chiefs_narrative import collect, edition_merge, facts, generate
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

    def test_run_41087549083_kelce_led_receivers_digit_form_fails(self):
        sentence = (
            "Travis Kelce led the receivers with 4 catches for 44 yards "
            "and then said the standard cannot be mediocre."
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

    def test_run_41087549083_third_down_was_box_is_unverifiable(self):
        sentence = (
            "Third down was 3-of-11, with 11 first downs and eight drives "
            "that rarely stayed on schedule."
        )
        issues = facts.check_review(
            _review(sentence, opponent="Seattle Seahawks", score="KC 3–20"),
            self.SEA_LAST,
            self.SEA,
            schedule=frozen_schedule(),
        )
        blob = " ".join(issues)
        self.assertTrue(issues, sentence)
        self.assertIn("unverifiable", blob)
        self.assertIn("Third down", blob)

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

    def test_salvage_drops_polymarket_the_edge(self):
        last = _slate_game("401873022")
        recap = _load_recap("espn_401873022_recap.json")
        narrative = {
            "generatedAt": "2026-10-28T17:50:00+00:00",
            "theEdge": (
                "ESPN FPI at 69.6 percent and DraftKings listing Chiefs "
                "-4.5, while Polymarket's 8.6 percent championship number "
                "still taxes a 3-0 team."
            ),
            "lastGameReview": {
                "opponent": "Seattle Seahawks",
                "result": "L",
                "score": "KC 3–20",
                "lede": "Seattle beat Kansas City 20-3.",
            },
        }
        issues = facts.check_review(
            narrative, last, recap, schedule=frozen_schedule()
        )
        repaired = facts.repair_offending_copy(narrative, issues, last, recap)
        self.assertNotIn("Polymarket", facts.edition_text(repaired))
        leftover = facts.check_review(
            repaired, last, recap, schedule=frozen_schedule()
        )
        self.assertFalse(
            any("prediction-market" in item for item in leftover), leftover
        )


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

    def test_opener_score_ignores_review_header_on_prior_line(self):
        """Miami in the review header must not steal Week 1's 31-10."""
        last = {
            "id": "401872952",
            "week": 3,
            "seasonType": "reg",
            "date": "2026-09-27T17:00:00Z",
            "opponent": "Miami Dolphins",
            "completed": True,
            "kcScore": 24,
            "oppScore": 10,
        }
        recap = _load_recap("espn_401872952_recap.json")
        narrative = {
            "lastGameReview": {
                "opponent": "Miami Dolphins",
                "result": "W",
                "score": "KC 24–10",
                "lede": "Kansas City won the opener 31-10.",
            }
        }
        self.assertEqual(
            facts.check_review(
                narrative, last, recap, schedule=frozen_schedule()
            ),
            [],
        )


class Butker58SalvageReplay(unittest.TestCase):
    """Run 41104758756: joined Butker/58 must salvage the same way replay does.

    Archive replay flattens ``title=Butker`` + ``body=Two misses from 58``
    into one ``_KICKER_FROM`` hit. Isolated body review is clean, so the
    old drop path left the claim and then generate hard-failed.
    """

    LAST = _slate_game("401873022")
    RECAP = _load_recap("espn_401873022_recap.json")

    def _butker_58(self, *, body: str = "Two misses from 58") -> dict:
        pad = (
            "Kansas City is 5-2 and turns to Denver after Seattle. "
            "The tape is early downs, the kick file, and the next snap. "
        ) * 8
        return {
            "headline": "Week 8 Preview: Denver after the Seattle kick file",
            "dek": pad,
            "theEdge": pad,
            "edition": "2026 Week 8 · Preview",
            "phase": {"type": "regular", "mode": "preview", "week": 8},
            "storyline": pad,
            "currentState": pad,
            "gamePlan": pad,
            "lastGameReview": {
                "opponent": "Seattle Seahawks",
                "result": "L",
                "score": "KC 3–20",
                "lede": "Seattle beat Kansas City 20-3. " + pad,
                "takeaways": [
                    {"title": "Butker", "body": body},
                ],
            },
        }

    def _replay_issues(self, edition: dict) -> list[str]:
        return facts.check_review(
            edition,
            self.LAST,
            self.RECAP,
            schedule=frozen_schedule(),
            changed=True,
        )

    def _salvage(self, edition: dict) -> dict:
        issues = self._replay_issues(edition)
        fixed, _logs = facts.apply_fact_corrections(
            edition, issues, self.RECAP, self.LAST, frozen_schedule()
        )
        leftover = self._replay_issues(fixed)
        if leftover:
            return facts.repair_offending_copy(
                fixed, leftover, self.LAST, self.RECAP, frozen_schedule()
            )
        return fixed

    def test_butker_58_join_fails_archive_replay_before_salvage(self):
        dirty = self._butker_58()
        issues = self._replay_issues(dirty)
        blob = " ".join(issues)
        self.assertIn("field-goal yardage 58", blob)
        self.assertIn("not a KC ESPN kick", blob)
        self.assertTrue(
            any("Butker" in item and "58" in item for item in issues),
            issues,
        )
        self.assertIn(58, facts.official_missed_fg_yards(self.RECAP))
        self.assertNotIn(58, facts.official_yards_by_team(self.RECAP, "fg").get("KC", set()))
        isolated = facts.sentence_fails_review(
            "Two misses from 58", self.LAST, self.RECAP, frozen_schedule()
        )
        self.assertFalse(isolated)

    def test_joined_title_drop_keeps_unrelated_sentences(self):
        dirty = self._butker_58(
            body=(
                "Two misses from 58. The offensive line still has to win "
                "first down on early snaps."
            )
        )
        repaired = self._salvage(dirty)
        blob = facts.edition_text(repaired)
        self.assertNotIn("Two misses from 58", blob)
        self.assertIn("offensive line still has to win first down", blob)
        leftover = self._replay_issues(repaired)
        self.assertFalse(
            any("field-goal yardage 58" in item for item in leftover), leftover
        )

    def test_butker_58_count_mismatch_is_dropped_not_rewritten(self):
        dirty = self._butker_58()
        repaired = self._salvage(dirty)
        leftover = self._replay_issues(repaired)
        self.assertFalse(
            any("field-goal yardage 58" in item for item in leftover),
            leftover,
        )
        held, hard = generate.split_replay_issues(
            generate.archive_replay_issues(repaired, frozen_schedule())
        )
        self.assertEqual(hard, [], hard)
        blob = facts.edition_text(repaired)
        self.assertNotIn("Two misses from 58", blob)
        self.assertNotIn("a miss from 58", blob.lower())
        self.assertNotIn("missed from 58", blob.lower())

    def test_make_claim_is_dropped_without_inserting_missed(self):
        for body in (
            "Butker hit from 58 to steady it.",
            "He was good from 58 and the drive died.",
        ):
            repaired = self._salvage(self._butker_58(body=body))
            blob = facts.edition_text(repaired)
            self.assertNotIn("hit missed", blob.lower(), body)
            self.assertNotIn("good missed", blob.lower(), body)
            self.assertNotIn("missed from 58", blob.lower(), body)
            leftover = self._replay_issues(repaired)
            self.assertFalse(
                any("field-goal yardage 58" in item for item in leftover),
                (body, leftover, blob),
            )

    def test_opponent_miss_is_not_credited_to_butker(self):
        recap = json.loads(json.dumps(self.RECAP))
        recap["driveResults"] = [
            row
            for row in recap.get("driveResults") or []
            if not (
                str(row.get("result") or "").lower() == "missed fg"
                and row.get("yards") == 58
            )
        ]
        recap["driveResults"].append(
            {
                "quarter": 2,
                "clock": "0:01",
                "team": "SEA",
                "result": "missed FG",
                "yards": 58,
                "detail": "J.Myers 58 yard field goal is No Good, Wide Left.",
            }
        )
        self.assertEqual(
            facts.official_missed_fg_kick_yards_by_team(recap).get("SEA"),
            [58],
        )
        self.assertNotIn(
            58, facts.official_missed_fg_kick_yards_by_team(recap).get("KC", [])
        )
        dirty = self._butker_58()
        issues = facts.check_review(
            dirty, self.LAST, recap, schedule=frozen_schedule(), changed=True
        )
        fixed, logs = facts.apply_fact_corrections(
            dirty, issues, recap, self.LAST, frozen_schedule()
        )
        leftover = facts.check_review(
            fixed, self.LAST, recap, schedule=frozen_schedule(), changed=True
        )
        if leftover:
            fixed = facts.repair_offending_copy(
                fixed, leftover, self.LAST, recap, frozen_schedule()
            )
        blob = facts.edition_text(fixed)
        self.assertNotIn("a miss from 58", blob.lower())
        self.assertFalse(
            any(" → " in row and "a miss from 58" in row.lower() for row in logs),
            logs,
        )
        after = facts.check_review(
            fixed, self.LAST, recap, schedule=frozen_schedule(), changed=True
        )
        self.assertFalse(
            any("field-goal yardage 58" in item for item in after),
            after,
        )

    def test_miss_count_is_not_silently_changed(self):
        repaired = self._salvage(self._butker_58(body="Three misses from 58."))
        blob = facts.edition_text(repaired)
        self.assertNotIn("Three misses from 58", blob)
        self.assertNotIn("a miss from 58", blob.lower())
        leftover = self._replay_issues(repaired)
        self.assertFalse(
            any("field-goal yardage 58" in item for item in leftover), leftover
        )

    def test_drive_result_yards_are_not_kick_distance(self):
        recap = {
            "oppAbbr": "SEA",
            "driveResults": [
                {
                    "team": "SEA",
                    "result": "missed FG",
                    "yards": 80,
                    "detail": "Seattle stalled after 80 yards and missed.",
                }
            ],
            "plays": [
                {
                    "team": "SEA",
                    "text": "J.Myers 80 yard rush. Field goal is No Good.",
                    "yards": 80,
                }
            ],
            "scoringPlays": [],
        }
        self.assertEqual(facts.official_missed_fg_kick_yards_by_team(recap), {})
        self.assertIsNone(
            facts._correct_fg_yardage(
                "Two misses from 80.",
                80,
                recap,
                self.LAST,
                context="Butker\nTwo misses from 80.",
            )
        )

    def test_joined_recheck_rejects_sentence_alone_clean_rewrite(self):
        dirty = self._butker_58()
        issues = self._replay_issues(dirty)
        with patch.object(
            facts,
            "_correct_fg_yardage",
            return_value="The kick file from 58 is open.",
        ):
            fixed, logs = facts.apply_fact_corrections(
                dirty, issues, self.RECAP, self.LAST, frozen_schedule()
            )
        blob = facts.edition_text(fixed)
        self.assertNotIn("The kick file from 58 is open.", blob)
        self.assertFalse(
            any("kick file from 58" in row.lower() for row in logs), logs
        )
        joined = facts.joined_edition_hard_issues(
            fixed, self.LAST, self.RECAP, frozen_schedule()
        )
        self.assertTrue(
            any("field-goal yardage 58" in item for item in joined),
            joined,
        )

    def test_unfixable_protected_title_refuses_even_when_held(self):
        dirty = self._butker_58()
        dirty["lastGameReview"]["takeaways"] = [
            {
                "title": "Butker from 99",
                "body": "The special-teams file is still open.",
            }
        ]
        issues = self._replay_issues(dirty)
        self.assertTrue(
            any("field-goal yardage 99" in item for item in issues), issues
        )
        repaired = self._salvage(dirty)
        leftover = self._replay_issues(repaired)
        self.assertTrue(
            any("field-goal yardage 99" in item for item in leftover),
            leftover,
        )
        self.assertTrue(
            facts.should_hold_automerge([], repaired, leftover=leftover)
        )
        self.assertEqual(
            edition_merge.refuse_reason(labels=[{"name": "hold"}]),
            "hold label",
        )
        from tools.chiefs_narrative import drop_body

        listed = drop_body.drop_body(
            {"holdAutomerge": True, "leftoverIssues": leftover}
        )
        self.assertIn("Archive replay leftovers", listed)
        self.assertTrue(
            any("99" in line for line in listed.splitlines()), listed
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "narrative.json"
            repair = Path(tmp) / "repair.json"
            path.write_text(json.dumps(repaired) + "\n", encoding="utf-8")
            with patch.object(generate.config, "NARRATIVE_JSON", path), patch.object(
                generate.config, "REPAIR_JSON", repair
            ):
                self.assertEqual(generate.main(["--gate-replay"]), 1)
                repair.write_text(
                    json.dumps({"holdAutomerge": True, "leftoverIssues": leftover})
                    + "\n",
                    encoding="utf-8",
                )
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = generate.main(["--gate-replay"])
                self.assertEqual(rc, 1, buf.getvalue())
                with patch.object(
                    generate,
                    "check_published_edition",
                    return_value=leftover,
                ):
                    check_rc = generate.main(["--check-edition"])
                self.assertEqual(check_rc, 1)


if __name__ == "__main__":
    unittest.main()
