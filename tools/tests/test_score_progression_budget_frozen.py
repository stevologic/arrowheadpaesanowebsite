"""FROZEN-4-paesano-323 / ADDENDUM-karen-2140 behavioural rows.

Row ids and wording match Karen's frozen list. Each test executes the
real drop / repair / leftover / gate functions on the fixture shapes.
Source-text greps of production files are not evidence except P12, which
greps the written output files of P3/P4/P8/P10.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import time
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import yaml

from tools.chiefs_narrative import (
    collect,
    drop_body,
    edition_merge,
    facts,
    generate,
    hero_posts,
    phase,
    providers,
    review_gate,
)
from tools.tests.frozen_slate import frozen_schedule
from tools.tests.test_archive_replay_qa import Week9PreviewLeftoverReplay
from tools.tests.test_maintenance import PublicationPolicy
from tools.tests.test_narrative import _job_step_script, _run_fail_held_script

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"


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

    R1_B1 = (
        ("without-the", "A 7-3 lead after Q2 7:40 touchdown became 24-28"),
        ("following", "A 7-3 lead following the Q2 7:40 touchdown became 24-28"),
        ("advantage", "A 7-3 advantage after the Q2 7:40 touchdown became 24-28"),
        (
            "linewrap-before-after",
            "A 7-3 lead\nafter Q2 7:40 touchdown became 24-28",
        ),
        ("edge", "A 7-3 edge after the Q2 7:40 touchdown became 24-28"),
        ("deficit", "A 7-3 deficit following Q2 7:40 touchdown became 24-28"),
    )
    R1_B3 = "A 24-13 lead after the Q3\n1:38 touchdown became 17-13"
    R2_N5_TD = "A 24-13 lead after the Q3 1:38 TD became 24-28"

    def _week9_phase(self) -> dict:
        return {
            "type": "regular",
            "label": "Week 9",
            "mode": "preview",
            "week": 9,
            "edition": "2026 Week 9 · Preview",
            "lastGame": self.LAST,
            "nextGame": {
                "opponent": "New York Jets",
                "homeAway": "home",
                "week": 9,
            },
        }

    def _p3_artifact(self) -> tuple[dict, list[str]]:
        return self._repair(self._fat_edition(self._field(8, ninth=True)))

    def _workflow(self, name: str) -> dict:
        return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))

    def _fat_nonlede(self, text: str, *, field: str = "storyline") -> dict:
        """A13: budget claims live in a non-lede field so A10 does not hold.

        storyline is not a required-copy key, so restore_required_copy cannot
        replace the surviving ninth claim with safe_score_lede.
        """
        payload = self._fat_edition(self.CLEAN)
        payload[field] = text
        return payload

    def _through_build(
        self,
        tmp: Path,
        raw: dict,
        *,
        llm=None,
        patches=(),
    ) -> tuple[dict, Path]:
        llm = llm if llm is not None else Mock(return_value=(raw, "grok"))
        archive = tmp / "archive.json"
        editions = tmp / "editions"
        editions.mkdir()
        archive.write_text("[]\n", encoding="utf-8")
        narrative_json = tmp / "out-narrative.json"
        repair_json = tmp / "out-repair.json"
        wire_json = tmp / "wire.json"
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    collect,
                    "collect_all",
                    return_value={
                        "schedule": frozen_schedule(),
                        "news": [],
                        "markets": {},
                        "lastGameRecap": self.RECAP,
                    },
                )
            )
            stack.enter_context(
                patch.object(collect, "fetch_game_recap", return_value=self.RECAP)
            )
            stack.enter_context(
                patch.object(collect, "prior_completed_game", return_value=None)
            )
            stack.enter_context(
                patch.object(phase, "detect", return_value=self._week9_phase())
            )
            stack.enter_context(
                patch.object(
                    phase,
                    "next_games",
                    return_value=[self._week9_phase()["nextGame"]],
                )
            )
            stack.enter_context(patch.object(phase, "current_record", return_value="5-2"))
            stack.enter_context(patch.object(phase, "any_live", return_value=False))
            stack.enter_context(patch.object(providers, "generate_via_llm", llm))
            stack.enter_context(patch.object(generate, "_render_diagrams"))
            stack.enter_context(patch.object(generate, "_write_schedule"))
            stack.enter_context(patch.object(generate, "_write_wire"))
            stack.enter_context(
                patch.object(generate, "_load_recent_editions", return_value=[])
            )
            stack.enter_context(
                patch.object(generate, "_should_advance_to_preview", return_value=False)
            )
            stack.enter_context(
                patch.object(
                    generate, "_signed_review_already_published", return_value=False
                )
            )
            stack.enter_context(
                patch.object(facts, "check_diagram_captions", return_value=[])
            )
            stack.enter_context(
                patch.object(generate.config, "NARRATIVE_JSON", narrative_json)
            )
            stack.enter_context(patch.object(generate.config, "REPAIR_JSON", repair_json))
            stack.enter_context(patch.object(generate.config, "ARCHIVE_JSON", archive))
            stack.enter_context(patch.object(generate.config, "EDITIONS_DIR", editions))
            stack.enter_context(patch.object(generate.config, "WIRE_JSON", wire_json))
            stack.enter_context(patch.object(generate.config, "ensure_dirs", lambda: None))
            stack.enter_context(patch.object(hero_posts, "attach_sidecar"))
            for extra in patches:
                stack.enter_context(extra)
            result = generate.build("grok", persist_schedule=False)
            leftover = result.get("leftoverIssues") or []
            drops = result.get("droppedSentences") or []
            corrections = result.get("correctedSentences") or []
            hold_only = result.get("holdOnlySentinels") or []
            headline_or_lede = bool(result.get("headlineOrLedeTouched"))
            repair_json.write_text(
                json.dumps(
                    {
                        "droppedSentences": drops,
                        "correctedSentences": corrections,
                        "leftoverIssues": leftover,
                        "holdOnlySentinels": hold_only,
                        "headlineOrLedeTouched": headline_or_lede,
                        "holdAutomerge": facts.should_hold_automerge(
                            drops,
                            result["narrative"],
                            leftover=leftover,
                            corrections=corrections,
                            hold_only_sentinels=hold_only,
                            headline_or_lede_touched=headline_or_lede,
                        ),
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        return result, repair_json

    def _p3_through_build(self, tmp: Path) -> tuple[dict, Path]:
        return self._through_build(
            tmp, self._fat_edition(self._field(8, ninth=True))
        )

    def test_c1_build_salvage_repair_leftover_and_hold(self):
        """C1: generate.build → `_salvage_until_clean` → `facts.repair_offending_copy`.

        A13 HARD GATE: EXACTLY one budget leftover at HOLD_REPAIR_DROPS=8
        for 9 and 50 claims in a non-lede field. Claim 9 stays
        byte-present. leftoverIssues is non-empty and holdAutomerge=true.
        This kills M4-5 (merge dropped at generate.py ~1105-1107).
        """
        self.assertEqual(facts.HOLD_REPAIR_DROPS, 8)
        ninth = self.NINTH
        cases = (
            (9, self._field(8, ninth=True)),
            (50, self._wrapped_field(50)),
        )
        for count, text in cases:
            with self.subTest(claims=count):
                with tempfile.TemporaryDirectory() as tmp:
                    result, repair_json = self._through_build(
                        Path(tmp), self._fat_nonlede(text)
                    )
                    leftover = result.get("leftoverIssues") or []
                    self.assertTrue(leftover, leftover)
                    budget = self._budget_rows(leftover)
                    self.assertEqual(len(budget), 1, leftover)
                    blob = facts.edition_text(result["narrative"])
                    if count == 9:
                        self.assertIn(ninth, blob)
                        storyline = result["narrative"].get("storyline")
                        if isinstance(storyline, dict):
                            storyline_text = " ".join(
                                str(storyline.get(part) or "")
                                for part in ("lede", "body")
                            )
                        else:
                            storyline_text = str(storyline or "")
                        self.assertIn(ninth, storyline_text)
                    repair = json.loads(repair_json.read_text(encoding="utf-8"))
                    self.assertTrue(repair.get("leftoverIssues"), repair)
                    self.assertTrue(repair.get("holdAutomerge"), repair)
                    self.assertEqual(
                        len(self._budget_rows(repair.get("leftoverIssues") or [])),
                        1,
                        repair,
                    )

    def test_c2_narrative_yml_gate_replay_and_fail_held(self):
        """C2: narrative.yml test job 'Gate generated content'.

        `--gate-replay` exits non-zero on the P3 artifact, and 'Fail held
        editions' exits 1. Run those exact commands from the workflow.
        """
        parsed = self._workflow("narrative.yml")
        gate = _job_step_script(parsed, "test", name="Gate generated content")
        self.assertIn("python -m tools.chiefs_narrative.generate --gate-replay", gate)
        hold = _job_step_script(parsed, "test", name="Fail held editions")
        self.assertTrue(hold.strip())
        repaired, leftover = self._p3_artifact()
        with tempfile.TemporaryDirectory() as tmp:
            narrative_json = Path(tmp) / "out-narrative.json"
            repair_json = Path(tmp) / "out-repair.json"
            narrative_json.write_text(json.dumps(repaired) + "\n", encoding="utf-8")
            repair_json.write_text(
                json.dumps({"holdAutomerge": True, "leftoverIssues": leftover})
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
            ), redirect_stdout(buf):
                gate_rc = generate.main(["--gate-replay"])
            self.assertNotEqual(gate_rc, 0, buf.getvalue())
        held = _run_fail_held_script(hold, payload={"holdMerge": True})
        self.assertEqual(held.returncode, 1, held.stderr)

    def test_c3_narrative_yml_update_draft_hold_dropbody(self):
        """C3: narrative.yml update job.

        The PR opens as a draft with the `hold` label, and dropBody lists the
        hard leftover under 'Archive replay leftovers'.
        """
        _repaired, leftover = self._p3_artifact()
        listed = drop_body.drop_body(
            {"holdAutomerge": True, "leftoverIssues": leftover}
        )
        self.assertIn("## Archive replay leftovers", listed)
        self.assertTrue(
            any(facts.is_score_progression_budget_exhausted(item) for item in leftover),
            leftover,
        )
        self.assertIn("score-progression repair budget exhausted", listed)
        parsed = self._workflow("narrative.yml")
        script = _job_step_script(
            parsed,
            "update",
            name="Queue draft pull request for approved PR-event gates",
        ) or _job_step_script(parsed, "update", step_id="publish")
        self.assertIn("gh pr create --draft", script)
        self.assertIn("--add-label hold", script)
        if os.name == "nt":
            self.skipTest("Actions update job is bash")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = PublicationPolicy._fake_commands(root)
            env["DESK_MODE"] = "full"
            metadata = {
                "holdMerge": True,
                "dropBody": listed,
                "edition": "2026 Week 9 · Preview",
                "headline": "Week 9 Preview: score-progression budget hold",
            }
            (root / ".narrative-publish.json").write_text(
                json.dumps(metadata) + "\n", encoding="utf-8"
            )
            data_dir = root / "data"
            data_dir.mkdir()
            (data_dir / "narrative.json").write_text(
                json.dumps(metadata) + "\n", encoding="utf-8"
            )
            ran = subprocess.run(
                ["bash", "-c", script],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(ran.returncode, 0, ran.stderr)
            calls = [
                json.loads(line)
                for line in (root / "calls.jsonl").read_text().splitlines()
            ]
            create = next(
                row
                for row in calls
                if row["command"] == "gh" and row["args"][:2] == ["pr", "create"]
            )
            self.assertIn("--draft", create["args"])
            labels = [
                row
                for row in calls
                if row["command"] == "gh" and "--add-label" in row["args"]
            ]
            self.assertTrue(labels)
            self.assertIn("hold", labels[0]["args"])
            self.assertIn("## Archive replay leftovers", create["body"])
            self.assertIn("score-progression repair budget exhausted", create["body"])

    def test_c4_publish_job_refuse_hold_label(self):
        """C4: publish job → `edition_merge` refuse_reason 'hold label'.

        No squash-merge, no promote, no pages dispatch. Unit-test refuse_reason
        with the labels the update job sets.
        """
        self.assertEqual(
            edition_merge.refuse_reason(labels=[{"name": "hold"}]),
            "hold label",
        )
        self.assertTrue(edition_merge.merge_refused(labels=[{"name": "hold"}]))
        parsed = self._workflow("narrative.yml")
        publish = (parsed.get("jobs") or {}).get("publish") or {}
        steps = publish.get("steps") or []
        refuse = next(
            step
            for step in steps
            if isinstance(step, dict)
            and step.get("name") == "Refuse hold or unresolved review findings"
        )
        squash = next(
            step
            for step in steps
            if isinstance(step, dict)
            and step.get("name") == "Squash-merge the edition pull request"
        )
        promote = next(
            step
            for step in steps
            if isinstance(step, dict) and step.get("name") == "Promote develop to main"
        )
        self.assertEqual(refuse.get("id"), "refuse")
        self.assertEqual(squash.get("if"), "steps.refuse.outputs.refused != 'true'")
        self.assertEqual(promote.get("if"), "steps.refuse.outputs.refused != 'true'")
        promote_run = str(promote.get("run") or "")
        self.assertIn("gh workflow run", promote_run)
        self.assertIn("Deploy Hugo site to GitHub Pages", promote_run)
        self.assertLess(
            promote_run.index("edition_merge"),
            promote_run.index("gh pr merge"),
        )
        self.assertLess(
            promote_run.index("edition_merge"),
            promote_run.index("gh workflow run"),
        )

    def test_c5_ci_yml_gates_job_p3_fixture_goes_red(self):
        """C5: the ci.yml `gates` job (PR to develop).

        The same --gate-replay/--check-edition commands go red on a P3
        edition fixture.
        """
        parsed = self._workflow("ci.yml")
        gates = (parsed.get("jobs") or {}).get("gates") or {}
        blob = "\n".join(
            str(step.get("run") or "")
            for step in gates.get("steps") or []
            if isinstance(step, dict)
        )
        self.assertIn(
            "python -m tools.chiefs_narrative.generate --check-edition", blob
        )
        narrative = self._workflow("narrative.yml")
        gate = _job_step_script(narrative, "test", name="Gate generated content")
        self.assertIn("python -m tools.chiefs_narrative.generate --gate-replay", gate)
        self.assertIn("python -m tools.chiefs_narrative.generate --check-edition", gate)
        repaired, leftover = self._p3_artifact()
        with tempfile.TemporaryDirectory() as tmp:
            narrative_json = Path(tmp) / "out-narrative.json"
            repair_json = Path(tmp) / "out-repair.json"
            narrative_json.write_text(json.dumps(repaired) + "\n", encoding="utf-8")
            repair_json.write_text(
                json.dumps({"holdAutomerge": True, "leftoverIssues": leftover})
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
            self.assertNotEqual(gate_rc, 0, buf.getvalue())
            self.assertNotEqual(check_rc, 0, buf.getvalue())

    def test_c6_walker_cards_body_and_protected_keys(self):
        """C6: the `_drop_invalid_score_progression` dict/list walkers.

        Put an exhausted value inside `lastGameReview.cards[i].body` and
        inside a `_PROTECTED_KEYS` sibling. Protected keys are untouched,
        and exhaustion is still reported.
        """
        protected_claim = self._claim("7-3")
        payload = {
            "lastGameReview": {
                "opponent": "Denver Broncos",
                "result": "L",
                "score": protected_claim,
                "cards": [
                    {
                        "title": protected_claim,
                        "body": self._field(8, ninth=True),
                    }
                ],
            }
        }
        self.assertIn("score", facts._PROTECTED_KEYS)
        self.assertIn("title", facts._PROTECTED_KEYS)
        self.assertIn("result", facts._PROTECTED_KEYS)
        sink: list[dict] = []
        out = facts._drop_invalid_score_progression(
            payload,
            self.RECAP,
            self.LAST,
            exhausted=sink,
            path="",
            root_key="",
        )
        review = out["lastGameReview"]
        self.assertEqual(review["score"], protected_claim)
        self.assertEqual(review["result"], "L")
        self.assertEqual(review["cards"][0]["title"], protected_claim)
        self.assertIn(self.NINTH, review["cards"][0]["body"])
        self.assertEqual(len(sink), 1)
        self.assertIn("score-progression repair budget exhausted", sink[0]["issue"])
        self.assertIn("cards", sink[0]["path"])
        self.assertIn("body", sink[0]["path"])

    def test_r1_four_mandated_r1_b1_inputs_drop_6_of_6(self):
        """R1: all four mandated R1-B1 exact inputs (without-the, following,
        advantage, linewrap-before-after) reject and drop 6/6, and
        24-13→24-28 true progression stays clean.
        """
        self.assertEqual(len(self.R1_B1), 6)
        dropped = 0
        for name, sentence in self.R1_B1:
            with self.subTest(name=name):
                dirty = self._lede(sentence)
                issues = facts.check_review(dirty, self.LAST, self.RECAP)
                self.assertTrue(
                    any("score progression" in item for item in issues),
                    (name, issues),
                )
                out, sink = self._drop_text(sentence)
                self.assertEqual(sink, [], name)
                self.assertNotIn("7-3", out, name)
                repaired, leftover = self._repair(dirty)
                blob = facts.edition_text(repaired)
                self.assertNotIn("7-3", blob, name)
                self.assertFalse(self._budget_rows(leftover), leftover)
                dropped += 1
        self.assertEqual(dropped, 6)
        true_out, true_sink = self._drop_text(self.TRUE_PROGRESSION)
        self.assertEqual(true_sink, [])
        self.assertIn("24-13", true_out)
        self.assertEqual(
            facts.check_review(
                self._lede(self.TRUE_PROGRESSION), self.LAST, self.RECAP
            ),
            [],
        )

    def test_r2_seattle_tails_reject_mahomes_kc_chiefs_present(self):
        """R2: Seattle without Walker, with Rice, not Mahomes, and
        Seattle–Mahomes reject. Patrick Mahomes, KC and the Chiefs stay present.
        """
        box = "was 18 first downs, 88 rush, 246 net pass, 25:39."
        rejects = (
            "Seattle without Walker",
            "Seattle with Rice",
            "Seattle not Mahomes",
            "Seattle-Mahomes",
        )
        slate = frozen_schedule()
        for subject in rejects:
            sentence = f"{subject} {box}"
            with self.subTest(subject=subject):
                unknown = facts._unknown_was_opponent(
                    sentence, self.LAST, self.RECAP, slate
                )
                self.assertTrue(unknown, sentence)
                dirty = self._lede(sentence)
                issues = facts.check_review(
                    dirty, self.LAST, self.RECAP, schedule=slate
                )
                self.assertTrue(
                    any("unverifiable" in item for item in issues),
                    issues,
                )
                repaired = facts.repair_offending_copy(
                    dirty, issues, self.LAST, self.RECAP, slate
                )
                self.assertNotIn(subject, facts.edition_text(repaired))
        mahomes = Week9PreviewLeftoverReplay.MAHOMES_BOX
        for name in ("Patrick Mahomes", "KC", "Chiefs"):
            sentence = mahomes.replace("Patrick Mahomes", name, 1)
            with self.subTest(keep=name):
                self.assertEqual(
                    facts._unknown_was_opponent(
                        sentence, self.LAST, self.RECAP, slate
                    ),
                    "",
                    name,
                )
                issues = facts.check_review(
                    self._lede(sentence),
                    self.LAST,
                    self.RECAP,
                    schedule=slate,
                )
                self.assertFalse(
                    any("unverifiable" in item and "opponent" in item for item in issues),
                    (name, issues),
                )
                self.assertIn(sentence, facts.edition_text(self._lede(sentence)))

    def test_r3_r1_b3_linewrap_clock_drops_fully_not_held(self):
        """R3: the exact R1-B3 single-claim linewrap-clock repro drops fully
        and is not held.
        """
        dirty = self._lede(self.R1_B3)
        issues = facts.check_review(dirty, self.LAST, self.RECAP)
        self.assertTrue(any("score progression" in item for item in issues), issues)
        out, sink = self._drop_text(self.R1_B3)
        self.assertEqual(sink, [])
        self.assertNotIn("A 24-13 lead after the Q3", out)
        self.assertNotIn("24-13", out)
        repaired, leftover = self._repair(dirty)
        blob = facts.edition_text(repaired)
        self.assertNotIn("A 24-13 lead after the Q3", blob)
        self.assertNotIn("24-13", blob)
        self.assertEqual(self._budget_rows(leftover), [])
        self.assertFalse(
            any("score progression" in item for item in leftover), leftover
        )

    def test_r5_r2_n5_td_abbreviation_overdrop_unfixed(self):
        """R5: R2-N5 (TD abbreviation over-drop) is unchanged and unfixed here,
        because it's #347's scope. Paste the probe so it's visibly not
        regressed further.
        """
        true = self.TRUE_PROGRESSION
        td = self.R2_N5_TD
        true_issues = facts.check_review(self._lede(true), self.LAST, self.RECAP)
        self.assertEqual(true_issues, [])
        td_issues = facts.check_review(self._lede(td), self.LAST, self.RECAP)
        self.assertTrue(
            any("score progression" in item for item in td_issues),
            td_issues,
        )
        true_out, true_sink = self._drop_text(true)
        self.assertEqual(true_sink, [])
        self.assertIn("24-13", true_out)
        td_out, td_sink = self._drop_text(td)
        self.assertEqual(td_sink, [])
        self.assertNotIn("24-13", td_out)
        claim = facts._clocked_score_after_claim(
            "A 24-13 lead ",
            "the Q3 1:38 TD became 24-28.",
        )
        self.assertIsNotNone(claim)
        self.assertNotEqual(claim[-1], "td", claim)


if __name__ == "__main__":
    unittest.main()
