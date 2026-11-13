"""FROZEN-4-paesano-323 / ADDENDUM-karen-2140 behavioural rows.

Row ids and wording match Karen's frozen list. Each test executes the
real drop / repair / leftover / gate functions on the fixture shapes.
Source-text greps of production files are not evidence except P12, which
greps the written output files of P3/P4/P8/P10.
"""
from __future__ import annotations

import io
import json
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tools.chiefs_narrative import facts, generate, known_errors, review_gate
from tools.tests.frozen_slate import frozen_schedule
from tools.tests.test_archive_replay_qa import Week9PreviewLeftoverReplay


class ScoreProgressionBudgetExhaustion(unittest.TestCase):
    """Every FROZEN-4 row id, in file order."""

    LAST = Week9PreviewLeftoverReplay.LAST
    RECAP = Week9PreviewLeftoverReplay.RECAP
    CLEAN = "The preview stays on the Jets."
    BUDGET_SCORES = (
        "7-3",
        "21-10",
        "9-0",
        "3-0",
        "6-0",
        "10-3",
        "13-10",
        "16-13",
    )
    NINTH = "A 22-17 lead after the Q2\n7:40 touchdown became 24-28."
    TRUE_PROGRESSION = Week9PreviewLeftoverReplay.TRUE_PROGRESSION
    BANNED_SCORES = frozenset({"14-6", "24-13", "24-28", "18-17"})

    def _claim(self, score: str) -> str:
        return f"A {score} lead after Q2 7:40 touchdown became 24-28."

    def _wrapped(self, score: str) -> str:
        return f"A {score} lead after the Q2\n7:40 touchdown became 24-28."

    def _fake_scores(self, count: int) -> list[str]:
        out: list[str] = []
        a, b = 2, 1
        while len(out) < count:
            pair = f"{a}-{b}"
            if pair not in self.BANNED_SCORES:
                out.append(pair)
            a += 1
            if a > 90:
                a = 2
                b += 1
            if b > 45:
                b = 1
        return out

    def _claims(self, count: int) -> list[str]:
        return [self._claim(score) for score in self.BUDGET_SCORES[:count]]

    def _field(self, count: int, *, ninth: bool = False, clean: bool = True) -> str:
        parts = []
        if clean:
            parts.append(self.CLEAN)
        parts.extend(self._claims(count))
        if ninth:
            parts.append(self.NINTH)
        return " ".join(parts)

    def _wrapped_field(self, count: int) -> str:
        parts = [self.CLEAN]
        parts.extend(self._wrapped(score) for score in self._fake_scores(count))
        return " ".join(parts)

    def _lede(self, text: str) -> dict:
        return {
            "lastGameReview": {
                "opponent": "Denver Broncos",
                "result": "L",
                "score": "KC 24–28",
                "lede": text,
            }
        }

    def _fat_edition(self, text: str) -> dict:
        pad = (
            "Kansas City is 5-2 and turns to the Jets after Denver. "
            "The tape is early downs, the kick file, and the next snap. "
        ) * 120
        payload = self._lede(text)
        payload.update(
            {
                "headline": "Week 9 Preview: score-progression budget hold",
                "dek": pad,
                "theEdge": pad,
                "storyline": pad,
                "currentState": pad,
                "gamePlan": pad,
                "edition": "2026 Week 9 · Preview",
                "slug": "2026-11-07-1200",
                "phase": {"type": "regular", "mode": "preview", "week": 9},
            }
        )
        return payload

    def _hard(self, leftover: list[str]) -> list[str]:
        _held, hard = generate.split_replay_issues(leftover)
        return hard

    def _budget_rows(self, leftover: list[str]) -> list[str]:
        return [
            item
            for item in leftover
            if facts.is_score_progression_budget_exhausted(item)
        ]

    def _repair(self, narrative: dict) -> tuple[dict, list[str]]:
        sink: list[str] = []
        repaired = facts.repair_offending_copy(
            narrative,
            facts.check_review(narrative, self.LAST, self.RECAP),
            self.LAST,
            self.RECAP,
            leftover_sink=sink,
        )
        leftover = generate.leftover_issues_with_score_progression_budget(
            generate.merge_score_progression_budget_leftover(
                facts.check_review(repaired, self.LAST, self.RECAP),
                sink,
            )
        )
        return repaired, leftover

    def _drop_text(self, text: str, *, path: str = "lastGameReview.lede") -> tuple[str, list[dict]]:
        sink: list[dict] = []
        out = facts._drop_invalid_score_progression_text(
            text,
            self.RECAP,
            self.LAST,
            exhausted=sink,
            path=path,
            root_key=path.split(".", 1)[0],
        )
        return out, sink

    def _orphan_hits(self, blob: str, scores: list[str]) -> list[str]:
        hits = []
        if "A 24-13 lead after the Q3" in blob and "1:38" not in blob:
            hits.append("A 24-13 lead after the Q3")
        for score in scores:
            prefix = f"A {score} lead after the Q2"
            full = self._wrapped(score)
            compact = f"A {score} lead after the Q2 7:40 touchdown became 24-28."
            if prefix in blob and full not in blob and compact not in blob:
                hits.append(prefix)
        return hits

    def test_p1_seven_invalid_claims_drop_clean(self):
        """P1: 7 invalid claims (under budget) all drop. No exhaustion leftover."""
        text = self._field(7)
        out, sink = self._drop_text(text)
        self.assertEqual(sink, [])
        self.assertIn(self.CLEAN, out)
        for claim in self._claims(7):
            self.assertNotIn(claim, out)
        self.assertEqual(
            facts._check_score_progression(out, self.RECAP, self.LAST),
            [],
        )

    def test_p2_eight_invalid_claims_exact_budget_drop_clean(self):
        """P2: 8 invalid claims (exact cap) all drop. No exhaustion leftover."""
        text = self._field(8)
        out, sink = self._drop_text(text)
        self.assertEqual(sink, [])
        self.assertIn(self.CLEAN, out)
        for claim in self._claims(8):
            self.assertNotIn(claim, out)
        self.assertNotIn("16-13", out)
        self.assertEqual(
            facts._check_score_progression(out, self.RECAP, self.LAST),
            [],
        )

    def test_p3_ninth_surviving_claim_fully_present(self):
        """P3: on exhaustion the surviving claim stays fully present, byte-identical."""
        original = self.NINTH
        text = self._field(8, ninth=True)
        out, sink = self._drop_text(text)
        self.assertTrue(sink)
        self.assertIn(original, out)
        start = out.find(original)
        self.assertEqual(out[start : start + len(original)], original)
        self.assertIn(self.CLEAN, out)
        self.assertNotIn("7-3", out)
        self.assertNotIn("16-13", out)

    def test_p4_twelve_claims_one_field_zero_orphan_prefixes(self):
        """P4: 12 claims in one field, 0 orphan prefixes. @7c3eb212 leaves 4."""
        scores = self._fake_scores(12)
        text = " ".join([self.CLEAN] + [self._wrapped(score) for score in scores])
        out, sink = self._drop_text(text)
        self.assertTrue(sink)
        remaining = [score for score in scores if self._wrapped(score) in out]
        self.assertEqual(len(remaining), 4, remaining)
        self.assertEqual(self._orphan_hits(out, scores), [])
        repaired, leftover = self._repair(self._lede(text))
        blob = facts.edition_text(repaired)
        self.assertEqual(self._orphan_hits(blob, scores), [])
        self.assertEqual(len(self._budget_rows(leftover)), 1)

    def test_p5_nine_claims_split_5_plus_4_per_string_budget(self):
        """P5: 9 claims split 5+4 across two fields and two list items.

        They all drop because the budget is per string, so there's no exhaustion.
        """
        five = self._fake_scores(5)
        four = self._fake_scores(9)[5:]
        narrative = {
            "dek": " ".join(self._claim(score) for score in five),
            "gamePlan": [
                " ".join(self._claim(score) for score in four[:2]),
                " ".join(self._claim(score) for score in four[2:]),
            ],
        }
        sink: list[dict] = []
        out = facts._drop_invalid_score_progression(
            narrative,
            self.RECAP,
            self.LAST,
            exhausted=sink,
            path="",
            root_key="",
        )
        self.assertEqual(sink, [])
        blob = facts.edition_text(out)
        for score in five + four:
            self.assertNotIn(self._claim(score), blob)
            self.assertNotIn(score, blob)
        repaired, leftover = self._repair(narrative)
        self.assertEqual(self._budget_rows(leftover), [])
        for score in five + four:
            self.assertNotIn(score, facts.edition_text(repaired))

    def test_p6_fifty_claims_under_five_seconds_fail_closed(self):
        """P6: 50 claims, timed. Must finish in under 5s and fail closed."""
        scores = self._fake_scores(50)
        text = " ".join([self.CLEAN] + [self._claim(score) for score in scores])
        started = time.perf_counter()
        out, sink = self._drop_text(text)
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 5.0, elapsed)
        self.assertEqual(len(sink), 1)
        self.assertIn("score-progression repair budget exhausted", sink[0]["issue"])
        dropped = [score for score in scores if self._claim(score) not in out]
        self.assertEqual(len(dropped), 8)
        self.assertIn(self._claim(scores[8]), out)

    def test_p7_byte_equality_on_budget_exhaustion_pass(self):
        """P7: byte-equality on the pass where the budget runs out."""
        original = self.NINTH
        text = self._field(8, ninth=True)
        cuts: list[tuple[str, str]] = []
        real = facts._cut_in_field_sentence

        def _track(current, index):
            nxt = real(current, index)
            cuts.append((current, nxt))
            return nxt

        sink: list[dict] = []
        with patch.object(facts, "_cut_in_field_sentence", side_effect=_track):
            out = facts._drop_invalid_score_progression_text(
                text,
                self.RECAP,
                self.LAST,
                exhausted=sink,
                path="lastGameReview.lede",
                root_key="lastGameReview",
            )
        self.assertEqual(len(cuts), 8)
        before_last, after_last = cuts[-1]
        self.assertIn(original, before_last)
        self.assertIn(original, after_last)
        start = after_last.find(original)
        self.assertEqual(after_last[start : start + len(original)], original)
        self.assertEqual(out[out.find(original) : out.find(original) + len(original)], original)
        self.assertNotIn(self._claim("16-13"), after_last)
        self.assertIn(self._claim("16-13"), before_last)

    def test_p8_stall_on_pass_one_fails_closed(self):
        """P8: progressed=False then break on pass 1 fails closed, claim intact."""
        claim = self._claim("7-3")
        calls = {"n": 0}

        def _noop(text, index):
            calls["n"] += 1
            return text

        sink: list[dict] = []
        with patch.object(facts, "_cut_in_field_sentence", side_effect=_noop):
            out = facts._drop_invalid_score_progression_text(
                claim,
                self.RECAP,
                self.LAST,
                exhausted=sink,
                path="lede",
                root_key="lede",
            )
        self.assertEqual(out, claim)
        self.assertEqual(len(sink), 1)
        self.assertIn("score-progression repair budget exhausted", sink[0]["issue"])
        self.assertIn(claim.rstrip("."), sink[0]["claim"])
        self.assertLessEqual(calls["n"], 8)

    def test_p9_exhaustion_on_the_last_pass(self):
        """P9: exhaustion on the last pass."""
        text = self._field(8, ninth=True)
        calls = {"n": 0}
        real = facts._cut_in_field_sentence

        def _count(current, index):
            calls["n"] += 1
            return real(current, index)

        sink: list[dict] = []
        with patch.object(facts, "_cut_in_field_sentence", side_effect=_count):
            out = facts._drop_invalid_score_progression_text(
                text,
                self.RECAP,
                self.LAST,
                exhausted=sink,
                path="lastGameReview.lede",
                root_key="lastGameReview",
            )
        self.assertEqual(calls["n"], 8)
        self.assertEqual(len(sink), 1)
        self.assertIn(self.NINTH, out)
        self.assertIn("score-progression repair budget exhausted", sink[0]["issue"])

    def test_p10_cut_noop_fails_closed_not_infinite(self):
        """P10: cut no-op removes nothing and fails closed; it does not loop."""
        claim = self._claim("21-10")
        calls = {"n": 0}

        def _noop(text, index):
            calls["n"] += 1
            if calls["n"] > 32:
                raise AssertionError("cut no-op looped")
            return text

        sink: list[dict] = []
        with patch.object(facts, "_cut_in_field_sentence", side_effect=_noop):
            out = facts._drop_invalid_score_progression_text(
                claim,
                self.RECAP,
                self.LAST,
                exhausted=sink,
                path="theEdge",
                root_key="theEdge",
            )
        self.assertEqual(out, claim)
        self.assertTrue(sink)
        self.assertLessEqual(calls["n"], 8)
        self.assertIn("theEdge", sink[0]["issue"])

    def test_p11_eight_removable_plus_one_stall(self):
        """P11: mixed 8 removable plus 1 stall claim. Fails closed, and the 8 are removed."""
        removable = self._claims(8)
        stall = self.NINTH
        text = " ".join([self.CLEAN, stall] + removable)
        real = facts._cut_in_field_sentence

        def _mixed(current, index):
            sentence = facts._sentence_at(current, index)
            if "22-17" in sentence:
                return current
            return real(current, index)

        sink: list[dict] = []
        with patch.object(facts, "_cut_in_field_sentence", side_effect=_mixed):
            out = facts._drop_invalid_score_progression_text(
                text,
                self.RECAP,
                self.LAST,
                exhausted=sink,
                path="lastGameReview.lede",
                root_key="lastGameReview",
            )
        self.assertIn(stall, out)
        for claim in removable:
            self.assertNotIn(claim, out)
        self.assertEqual(len(sink), 1)
        self.assertIn("score-progression repair budget exhausted", sink[0]["issue"])

    def test_p12_grep_written_files_for_fragments(self):
        """P12: for P3, P4, P8 and P10, grep every written file for fragments."""
        p3_text = self._field(8, ninth=True)
        p4_scores = self._fake_scores(12)
        p4_text = " ".join([self.CLEAN] + [self._wrapped(score) for score in p4_scores])
        p3_out, _ = self._drop_text(p3_text)
        p4_out, _ = self._drop_text(p4_text)
        p8_claim = self._claim("7-3")
        p10_claim = self._claim("21-10")

        def _noop(text, index):
            return text

        with patch.object(facts, "_cut_in_field_sentence", side_effect=_noop):
            p8_out, _ = self._drop_text(p8_claim, path="lede")
            p10_out, _ = self._drop_text(p10_claim, path="theEdge")
        p3_rep, _ = self._repair(self._lede(p3_text))
        p4_rep, _ = self._repair(self._lede(p4_text))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "p3.txt").write_text(p3_out, encoding="utf-8")
            (root / "p4.txt").write_text(p4_out, encoding="utf-8")
            (root / "p8.txt").write_text(p8_out, encoding="utf-8")
            (root / "p10.txt").write_text(p10_out, encoding="utf-8")
            (root / "p3.json").write_text(json.dumps(p3_rep), encoding="utf-8")
            (root / "p4.json").write_text(json.dumps(p4_rep), encoding="utf-8")
            hits = []
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                if path.suffix == ".json":
                    blob = facts.edition_text(json.loads(path.read_text(encoding="utf-8")))
                else:
                    blob = path.read_text(encoding="utf-8")
                found = self._orphan_hits(blob, p4_scores + ["22-17", "7-3", "21-10"])
                if found:
                    hits.append((path.name, found))
            self.assertEqual(hits, [])

    def test_p13_no_sign_ok_or_signoff_json_on_exhaustion(self):
        """P13: no SIGN-OK or sign-off json is produced or kept for an exhausted edition."""
        narrative = self._fat_edition(self._field(8, ninth=True))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            signoff = root / "signoff" / "review"
            signoff.mkdir(parents=True)
            planted = signoff / f"{narrative['slug']}.json"
            planted.write_text('{"ok": true}\n', encoding="utf-8")
            (signoff / f"{narrative['slug']}.sig").write_text("planted\n", encoding="utf-8")
            repaired, leftover = self._repair(narrative)
            blob = json.dumps(repaired) + json.dumps(leftover)
            self.assertNotIn("SIGN-OK", blob)
            self.assertFalse(
                any("SIGN-OK" in item for item in leftover),
                leftover,
            )
            written = [
                path
                for path in signoff.glob("*")
                if path.name not in {planted.name, f"{narrative['slug']}.sig"}
            ]
            self.assertEqual(written, [])
            self.assertFalse(
                review_gate.review_signed_off(repaired, root=root)
            )
            edition_path = root.joinpath("data", "narrative.json")
            edition_path.parent.mkdir(parents=True)
            edition_path.write_text(json.dumps(repaired), encoding="utf-8")
            self.assertFalse(
                review_gate.review_signed_off(
                    repaired, root=root, edition_file=edition_path
                )
            )

    def test_p14_gate_replay_and_check_edition_on_p3_output(self):
        """P14: `--gate-replay` and `--check-edition` on the P3 output."""
        repaired, leftover = self._repair(
            self._fat_edition(self._field(8, ninth=True))
        )
        hard = self._hard(leftover)
        self.assertTrue(hard)
        with tempfile.TemporaryDirectory() as tmp:
            narrative_json = Path(tmp) / "narrative.json"
            repair_json = Path(tmp) / "repair.json"
            narrative_json.write_text(json.dumps(repaired) + "\n", encoding="utf-8")
            repair_json.write_text(
                json.dumps(
                    {
                        "holdAutomerge": True,
                        "leftoverIssues": leftover,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            buf = io.StringIO()
            with patch.object(generate.config, "NARRATIVE_JSON", narrative_json), patch.object(
                generate.config, "REPAIR_JSON", repair_json
            ), patch.object(
                generate, "archive_replay_issues", return_value=leftover
            ), patch.object(
                generate, "replay_fixture_missing", return_value=False
            ), patch.object(
                generate, "check_published_edition", return_value=leftover
            ), redirect_stdout(buf):
                gate_rc = generate.main(["--gate-replay"])
                check_rc = generate.main(["--check-edition"])
            self.assertEqual(gate_rc, 1, buf.getvalue())
            self.assertEqual(check_rc, 1, buf.getvalue())

    def test_c1_drop_invalid_score_progression_text(self):
        """C1: `_drop_invalid_score_progression_text` records exhaustion."""
        out, sink = self._drop_text(self._field(8, ninth=True))
        self.assertIn(self.NINTH, out)
        self.assertEqual(len(sink), 1)
        self.assertIn("lastGameReview.lede", sink[0]["issue"])

    def test_c2_drop_invalid_score_progression_walker(self):
        """C2: `_drop_invalid_score_progression` names nested section."""
        payload = self._lede(self._field(8, ninth=True))["lastGameReview"]
        sink: list[dict] = []
        out = facts._drop_invalid_score_progression(
            payload,
            self.RECAP,
            self.LAST,
            exhausted=sink,
            path="lastGameReview",
            root_key="lastGameReview",
        )
        self.assertIn(self.NINTH, out["lede"])
        self.assertEqual(len(sink), 1)
        self.assertEqual(sink[0]["root"], "lastGameReview")
        self.assertIn("lastGameReview.lede", sink[0]["path"])

    def test_c3_repair_offending_copy(self):
        """C3: `repair_offending_copy` leftover_sink + skip `_drop_value`."""
        narrative = self._lede(self._field(8, ninth=True))
        sink: list[str] = []
        repaired = facts.repair_offending_copy(
            narrative,
            facts.check_review(narrative, self.LAST, self.RECAP),
            self.LAST,
            self.RECAP,
            leftover_sink=sink,
        )
        self.assertEqual(len(sink), 1)
        self.assertIn(self.NINTH, repaired["lastGameReview"]["lede"])
        self.assertIn("score-progression repair budget exhausted", sink[0])

    def test_c4_generate_merge_leftover_issues(self):
        """C4: generate.py merge carries the HARD leftover into leftoverIssues."""
        score_leftover = (
            "score progression 22-17 after Q2 7:40 is not an ESPN "
            "score-after ('A 22-17 lead after the Q2\\n7:40 touchdown became 24-28')"
        )
        budget = facts.score_progression_budget_issue(
            "lastGameReview.lede",
            "A 22-17 lead after the Q2\n7:40 touchdown became 24-28",
        )
        merged = generate.merge_score_progression_budget_leftover(
            [score_leftover, "unrelated leftover"],
            [budget],
        )
        self.assertEqual(merged, ["unrelated leftover", budget])
        self.assertIn(budget, self._hard(merged))

    def test_c5_salvage_stops_on_budget_exhaustion(self):
        """C5: generate stops a second salvage pass so the ninth survives."""
        first, leftover = self._repair(self._lede(self._field(8, ninth=True)))
        self.assertIn(self.NINTH, first["lastGameReview"]["lede"])
        self.assertTrue(
            generate.salvage_should_stop_for_score_progression_budget(leftover)
        )
        second = facts.repair_offending_copy(
            first,
            leftover,
            self.LAST,
            self.RECAP,
            leftover_sink=[],
        )
        self.assertNotIn(
            self.NINTH,
            (second.get("lastGameReview") or {}).get("lede") or "",
            "control: a second salvage pass would drop the ninth",
        )

    def test_c6_hold_automerge_from_leftover_issues(self):
        """C6: leftoverIssues write-path holdAutomerge follows the HARD leftover."""
        _repaired, leftover = self._repair(
            self._fat_edition(self._field(8, ninth=True))
        )
        published = generate.leftover_issues_with_score_progression_budget(leftover)
        self.assertEqual(len(self._budget_rows(published)), 1)
        edition = self._fat_edition(self.NINTH)
        self.assertTrue(
            facts.should_hold_automerge([], edition, leftover=published)
        )

    def test_r1_false_clocked_score_still_drops(self):
        """R1: R1-B1 under-budget false 7-3 after/following still drops."""
        sentence = "A 7-3 lead following the Q2 7:40 touchdown became 24-28."
        out, sink = self._drop_text(sentence)
        self.assertEqual(sink, [])
        self.assertNotIn("7-3", out)

    def test_r2_incidental_player_does_not_verify_unknown_club(self):
        """R2: R1-B2 `_unknown_was_opponent` still rejects Seattle without Walker."""
        sentence = (
            "Seattle without Walker was 18 first downs, 88 rush, "
            "246 net pass, 25:39."
        )
        self.assertTrue(
            facts._unknown_was_opponent(
                sentence, self.LAST, self.RECAP, frozen_schedule()
            )
        )

    def test_r3_true_progression_still_clean(self):
        """R3: true 24-13 after Q3 1:38 → 24-28 stays clean."""
        out, sink = self._drop_text(self.TRUE_PROGRESSION)
        self.assertEqual(sink, [])
        self.assertIn("24-13", out)
        self.assertEqual(
            facts.check_review(self._lede(self.TRUE_PROGRESSION), self.LAST, self.RECAP),
            [],
        )

    def test_r4_fifty_claim_repair_fail_closed(self):
        """R4: 50-claim field through repair_offending_copy fails closed, timed."""
        scores = self._fake_scores(50)
        text = " ".join([self.CLEAN] + [self._claim(score) for score in scores])
        started = time.perf_counter()
        repaired, leftover = self._repair(self._lede(text))
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 5.0, elapsed)
        lede = repaired["lastGameReview"]["lede"]
        self.assertEqual(len(self._budget_rows(leftover)), 1)
        self.assertIn(self._claim(scores[8]), lede)
        self.assertNotIn(self._claim(scores[0]), lede)

    def test_r5_clocked_score_after_class_unchanged(self):
        """R5: `_clocked_score_after_claim` still classifies following/advantage."""
        following = "A 7-3 lead following the Q2 7:40 touchdown became 24-28."
        advantage = "A 7-3 advantage after the Q2 7:40 touchdown became 24-28."
        for sentence in (following, advantage):
            hits = list(facts._iter_clocked_score_after(sentence))
            self.assertTrue(hits, sentence)
        self.assertIsNotNone(
            facts._clocked_score_after_claim(
                "A 7-3 lead ",
                "the Q2 7:40 touchdown became 24-28.",
            )
        )

    def test_f1_exactly_one_hard_budget_leftover(self):
        """F1: exactly one HARD leftoverIssues row names field and surviving claim."""
        repaired, leftover = self._repair(self._lede(self._field(8, ninth=True)))
        budget = self._budget_rows(leftover)
        self.assertEqual(len(budget), 1, leftover)
        issue = budget[0]
        self.assertIn("score-progression repair budget exhausted", issue)
        self.assertIn("lastGameReview.lede", issue)
        self.assertIn("A 22-17 lead after the Q2", issue)
        self.assertIn("7:40 touchdown became 24-28", issue)
        self.assertEqual(self._budget_rows(self._hard(leftover)), budget)
        self.assertFalse(known_errors.is_hold_only_issue(issue))
        self.assertIn(self.NINTH, repaired["lastGameReview"]["lede"])

    def test_f2_hold_automerge_on_exhaustion(self):
        """F2: exhaustion sets holdAutomerge even when no sentences were dropped."""
        claim = self._claim("7-3")
        sink: list[dict] = []

        def _noop(text, index):
            return text

        with patch.object(facts, "_cut_in_field_sentence", side_effect=_noop):
            out = facts._drop_invalid_score_progression_text(
                claim,
                self.RECAP,
                self.LAST,
                exhausted=sink,
                path="lede",
                root_key="lede",
            )
        self.assertEqual(out, claim)
        leftover = [row["issue"] for row in sink]
        edition = self._fat_edition(out)
        self.assertGreaterEqual(
            facts.edition_word_count(edition), facts.PUBLISH_WORD_FLOOR
        )
        merged = generate.merge_score_progression_budget_leftover([], leftover)
        self.assertEqual(self._budget_rows(merged), leftover)
        self.assertTrue(
            facts.should_hold_automerge([], edition, leftover=merged)
        )
        self.assertFalse(
            facts.should_hold_automerge([], edition, leftover=[]),
            "hold must come from the budget leftover, not the fixture",
        )

    def test_f3_leftover_names_field_and_surviving_claim(self):
        """F3: HARD leftover names the field/section and surviving claim text."""
        _out, sink = self._drop_text(self._field(8, ninth=True))
        self.assertEqual(len(sink), 1)
        issue = sink[0]["issue"]
        self.assertEqual(sink[0]["path"], "lastGameReview.lede")
        self.assertIn(self.NINTH.rstrip("."), sink[0]["claim"])
        self.assertIn("lastGameReview.lede", issue)
        self.assertIn("A 22-17 lead after the Q2", issue)
        self.assertIn("7:40 touchdown became 24-28", issue)

    def test_o1_no_orphaned_claim_fragment_in_any_output(self):
        """O1: no orphaned claim fragment appears in any output."""
        text = self._field(8, ninth=True)
        repaired, leftover = self._repair(self._lede(text))
        blob = facts.edition_text(repaired)
        self.assertIn(self.NINTH, blob)
        self.assertEqual(self._orphan_hits(blob, ["22-17"]), [])
        self.assertNotIn("A 24-13 lead after the Q3", blob)
        for item in leftover:
            self.assertNotIn("A 24-13 lead after the Q3", item)
        snippets = facts.violation_snippets(
            facts.check_review(self._lede(text), self.LAST, self.RECAP)
        )
        clipped = facts._drop_value(
            text, snippets, self.LAST, self.RECAP, generated_at=""
        )
        self.assertNotIn(self.NINTH, clipped)
        lede = repaired["lastGameReview"]["lede"]
        self.assertIn(self.NINTH, lede)


if __name__ == "__main__":
    unittest.main()
