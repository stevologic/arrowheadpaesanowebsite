"""FROZEN-7 Chiefs Narrative: stop failing on drops-only.

Row ids and frozen wording match Karen's list (J*, K*, N*, MG*, R*, MR*,
A*, MW*, X*). Each test executes the real hold / salvage / workflow
function or extracted step. Source-text greps are not evidence except
where the row is a workflow permission or step name.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import yaml

from tools.chiefs_narrative import (
    collect,
    drop_body,
    drop_issues,
    edition_merge,
    facts,
    generate,
    hero_posts,
    known_errors,
    phase,
    providers,
    review_gate,
)
from tools.tests.frozen_slate import frozen_schedule
from tools.tests.test_narrative import (
    _job_step_script,
    _run_fail_held_script,
)
from tools.tests.test_score_progression_budget_frozen import (
    ScoreProgressionBudgetExhaustion,
)
from tools.tests.test_archive_replay_qa import Week9PreviewLeftoverReplay

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
REPO = Path(__file__).resolve().parents[2]


def _workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _workflow_source(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def _fat_preview(**overrides) -> dict:
    pad = (
        "Kansas City is 5-2 and turns to the Jets after Denver. "
        "The tape is early downs, the kick file, and the next snap. "
    ) * 120
    payload = _thin_preview(dek=pad, theEdge=pad, storyline=pad, currentState=pad, gamePlan=pad)
    payload.update(overrides)
    return payload


def _thin_preview(**overrides) -> dict:
    payload = {
        "headline": "Week 9 Preview: remainder ships after body drops",
        "dek": "Kansas City turns to the Jets.",
        "theEdge": "The tape is early downs.",
        "storyline": "The slot job is the question.",
        "currentState": "The secondary has to settle.",
        "gamePlan": "Stay on early downs.",
        "lastGameReview": {
            "opponent": "Denver Broncos",
            "result": "L",
            "score": "KC 24–28",
            "lede": "Kansas City lost 24-28 in Denver. The preview stays on the Jets.",
        },
        "edition": "2026 Week 9 · Preview",
        "slug": "2026-11-07-1200",
        "phase": {"type": "regular", "mode": "preview", "week": 9},
    }
    payload.update(overrides)
    return payload


def _exact_words(edition: dict, target: int) -> dict:
    payload = json.loads(json.dumps(edition))
    payload.setdefault("dek", "")
    guard = 0
    while facts.edition_word_count(payload) < target and guard < target + 10:
        payload["dek"] = (payload.get("dek") or "") + " pad"
        guard += 1
    guard = 0
    while facts.edition_word_count(payload) > target and guard < 20000:
        words = (payload.get("dek") or "").split()
        if not words:
            break
        words.pop()
        payload["dek"] = " ".join(words)
        guard += 1
    return payload


def _extract_heredoc(script: str) -> str:
    lines = script.splitlines()
    collected: list[str] = []
    inside = False
    for line in lines:
        if not inside:
            if "<<'PY'" in line or '<<"PY"' in line:
                inside = True
            continue
        if line.strip() == "PY":
            break
        collected.append(line)
    return textwrap.dedent("\n".join(collected))


def _pinned() -> dict:
    path = REPO / "tools" / "tests" / "fixtures" / "edition_run_38582366273.json"
    return json.loads(path.read_text(encoding="utf-8"))["pinned"]


def _body_drops(count: int, start: int = 0) -> list[str]:
    return [
        f"The depth chart stayed on early downs through the Jets week {start + i}."
        for i in range(count)
    ]


def _hold(
    edition,
    drops=None,
    leftover=None,
    corrections=None,
    **kwargs,
) -> bool:
    return facts.should_hold_automerge(
        list(drops or []),
        edition,
        leftover=leftover,
        corrections=corrections,
        **kwargs,
    )


class Frozen7PlanGaps(unittest.TestCase):
    """J1-J6 restated against the code that landed."""

    def test_j1_hold_only_sentinels_use_code_flag(self):
        sentinel = facts.hold_only_sentinel(facts.HOLD_ONLY_SALVAGE_NOISY)
        self.assertTrue(facts.is_hold_only_sentinel(sentinel))
        self.assertTrue(sentinel.get("holdOnly"))
        self.assertEqual(sentinel.get("code"), facts.HOLD_ONLY_SALVAGE_NOISY)
        self.assertFalse(facts.is_hold_only_sentinel(sentinel["text"]))
        edition = _fat_preview()
        self.assertTrue(_hold(edition, hold_only_sentinels=[sentinel]))
        self.assertTrue(_hold(edition, leftover=[sentinel["text"]]))

    def test_j2_repair_blockers_are_leftover(self):
        source = Path(generate.__file__).read_text(encoding="utf-8")
        self.assertIn("repair_publish_blockers", source)
        routed = source.index("blockers = facts.repair_publish_blockers")
        printed = source.index("repair blocker:")
        returned = source.index("leftover.append(item)")
        self.assertLess(routed, printed)
        self.assertLess(routed, returned)

    def test_j4_r1_body_correction_ships(self):
        edition = _fat_preview()
        self.assertFalse(
            _hold(edition, corrections=["A body claim. → A fixed body claim."])
        )

    def test_j5_update_job_stays_draft(self):
        script = _job_step_script(
            _workflow("narrative.yml"),
            "update",
            name="Queue draft pull request for approved PR-event gates",
        )
        self.assertIn("gh pr create --draft", script)
        self.assertNotIn("gh pr ready", script)
        self.assertNotIn("--undraft", script)

    def test_j6_issues_write_on_update_job_only(self):
        parsed = _workflow("narrative.yml")
        jobs = parsed.get("jobs") or {}
        self.assertEqual(
            (jobs.get("update") or {}).get("permissions"),
            {
                "contents": "write",
                "pull-requests": "write",
                "issues": "write",
            },
        )
        for name in ("generate", "test", "publish"):
            perms = (jobs.get(name) or {}).get("permissions") or {}
            self.assertNotEqual(perms.get("issues"), "write", name)
        self.assertIn(
            "python -m tools.chiefs_narrative.drop_issues",
            _job_step_script(
                parsed, "update", name="File dropped-lines tracking issue"
            ),
        )


class Frozen7HoldCauses(unittest.TestCase):
    """K1-K13 still hold when they are the only cause, except relaxed K3."""

    def test_k1_review_requires_human(self):
        edition = _fat_preview(
            phase={"type": "regular", "mode": "review", "week": 9},
            edition="2026 Week 9 · Review",
        )
        self.assertTrue(facts.review_requires_human(edition))
        self.assertTrue(_hold(edition, drops=[]))
        self.assertTrue(_hold(edition, drops=["a body drop"]))

    def test_k2a_hard_archive_replay_leftover(self):
        edition = _fat_preview()
        leftover = ["score progression 7-3 after Q2 disagrees with ESPN"]
        self.assertTrue(_hold(edition, leftover=leftover))
        self.assertTrue(_hold(edition, drops=["body drop"], leftover=leftover))

    def test_k2b_leaked_fact_checker_notes(self):
        edition = _fat_preview()
        leftover = ["fact-checker note leaked ('wire'): Do not tell that story."]
        self.assertTrue(_hold(edition, leftover=leftover))
        self.assertTrue(_hold(edition, drops=["body drop"], leftover=leftover))

    def test_k2c_replay_not_run(self):
        edition = _fat_preview()
        leftover = [generate.REPLAY_NOT_RUN]
        self.assertTrue(_hold(edition, leftover=leftover))
        self.assertTrue(_hold(edition, drops=["body drop"], leftover=leftover))

    def test_k2d_synthetic_noisy_salvage_sentinel(self):
        edition = _fat_preview()
        sentinel = facts.hold_only_sentinel(facts.HOLD_ONLY_SALVAGE_NOISY)
        self.assertTrue(_hold(edition, hold_only_sentinels=[sentinel]))
        self.assertTrue(
            _hold(edition, drops=["body drop"], hold_only_sentinels=[sentinel])
        )

    def test_k2e_repair_blockers_once_routed(self):
        edition = _fat_preview()
        leftover = [
            "Chiefs Narrative fact-check left fragments after repair: "
            "orphan opener after repair ('That is the credit line.')"
        ]
        self.assertTrue(_hold(edition, leftover=leftover))
        self.assertTrue(_hold(edition, drops=["body drop"], leftover=leftover))

    def test_k3_drops_only_relaxed(self):
        edition = _fat_preview()
        self.assertFalse(_hold(edition, drops=["one body drop"]))
        self.assertFalse(_hold(edition, drops=[]))

    def test_k4_body_correction_ships_headline_holds(self):
        edition = _fat_preview()
        self.assertFalse(
            _hold(edition, corrections=["A body claim. → fixed."])
        )
        self.assertTrue(
            _hold(
                edition,
                corrections=[edition["headline"] + " → new title"],
                before=edition,
            )
        )
        self.assertTrue(
            _hold(
                edition,
                drops=["one body drop"],
                corrections=[edition["headline"] + " → new title"],
                before=edition,
            )
        )

    def test_k5_missing_headline(self):
        edition = _fat_preview()
        self.assertTrue(_hold(edition, missing_headline=True))
        self.assertTrue(_hold(edition, drops=["body"], missing_headline=True))

    def test_k6_empty_headline_after_restore(self):
        edition = _fat_preview(headline="")
        self.assertTrue(_hold(edition, drops=[]))
        self.assertTrue(_hold(edition, drops=["body"]))

    def test_k7_offseason_while_slate_open(self):
        slate = frozen_schedule()
        edition = _fat_preview(
            phase={"type": "offseason", "mode": "offseason"}
        )
        self.assertTrue(_hold(edition, schedule=slate))
        self.assertTrue(_hold(edition, drops=["body"], schedule=slate))

    def test_k8_playoff_next_unknown(self):
        wild = {
            "id": "wc",
            "week": 1,
            "seasonType": "post",
            "date": "2027-01-16T21:00:00Z",
            "opponent": "Houston Texans",
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
        edition = _fat_preview(
            phase={"type": "postseason", "mode": "preview", "lastGame": wild}
        )
        with patch.object(facts, "REVIEW_REQUIRES_HUMAN", False):
            self.assertTrue(_hold(edition, schedule=slate))
            self.assertTrue(_hold(edition, drops=["body"], schedule=slate))

    def test_k9_word_floor(self):
        thin = _exact_words(_thin_preview(), 3899)
        self.assertTrue(_hold(thin, drops=[]))
        flexed_under = _exact_words(_thin_preview(), 3314)
        self.assertTrue(_hold(flexed_under, drops=["The depth chart stayed on early downs."]))
        ships = _exact_words(_thin_preview(), 3900)
        self.assertFalse(_hold(ships, drops=[]))

    def test_k10_check_edition_force_hold(self):
        parsed = _workflow("narrative.yml")
        script = _job_step_script(parsed, "test", name="Gate generated content")
        self.assertIn("python -m tools.chiefs_narrative.generate --check-edition", script)
        self.assertIn('payload["holdAutomerge"] = True', script)
        inner = _extract_heredoc(script)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "data"
            data.mkdir()
            repair = data / "narrative_repair.json"
            repair.write_text("{}\n", encoding="utf-8")
            ran = subprocess.run(
                [sys.executable, "-c", inner],
                cwd=root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(ran.returncode, 0, ran.stderr)
            payload = json.loads(repair.read_text(encoding="utf-8"))
            self.assertTrue(payload.get("holdAutomerge"))
        hold = _run_fail_held_script(
            _job_step_script(parsed, "test", name="Fail held editions"),
            payload={"holdMerge": True},
        )
        self.assertEqual(hold.returncode, 1, hold.stderr)
        clear = _run_fail_held_script(
            _job_step_script(parsed, "test", name="Fail held editions"),
            payload={"holdMerge": False},
        )
        self.assertEqual(clear.returncode, 0, clear.stderr)

    def test_k11_gate_replay_hard_fails(self):
        parsed = _workflow("narrative.yml")
        gate = _job_step_script(parsed, "test", name="Gate generated content")
        self.assertIn("python -m tools.chiefs_narrative.generate --gate-replay", gate)
        leftover = ["score progression 7-3 after Q2 disagrees with ESPN"]
        with tempfile.TemporaryDirectory() as tmp:
            narrative_json = Path(tmp) / "n.json"
            repair_json = Path(tmp) / "r.json"
            narrative_json.write_text(json.dumps(_fat_preview()) + "\n")
            repair_json.write_text(json.dumps({"holdAutomerge": True}) + "\n")
            buf = io.StringIO()
            with patch.object(generate.config, "NARRATIVE_JSON", narrative_json), patch.object(
                generate.config, "REPAIR_JSON", repair_json
            ), patch.object(
                generate, "archive_replay_issues", return_value=leftover
            ), patch.object(
                generate, "replay_fixture_missing", return_value=False
            ), redirect_stdout(buf):
                rc = generate.main(["--gate-replay"])
            self.assertEqual(rc, generate.HARD_REPLAY_RC)
            self.assertNotEqual(rc, 0)

    def test_k12_pages_gate_unsigned_is_hold_other_fails(self):
        parsed = _workflow("narrative.yml")
        gate = _job_step_script(parsed, "test", name="Gate generated content")
        self.assertIn("python -m tools.chiefs_narrative.review_gate --pages", gate)
        self.assertIn('"$pages_rc" -eq 2', gate)
        pages = _workflow_source("pages.yml")
        self.assertIn('"$rc" -eq 2', pages)
        self.assertIn("Skip unsigned Review editions", pages)

    def test_k13_diagram_drift_fails(self):
        parsed = _workflow("narrative.yml")
        gate = _job_step_script(parsed, "test", name="Gate generated content")
        self.assertIn("git diff --exit-code -- public/images/narrative", gate)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "public" / "images" / "narrative"
            target.mkdir(parents=True)
            (target / "xo.svg").write_text("<svg/>\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "add", "public/images/narrative"], cwd=root, check=True)
            subprocess.run(
                ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "svg"],
                cwd=root,
                check=True,
            )
            (target / "xo.svg").write_text("<svg id='drift'/>\n", encoding="utf-8")
            ran = subprocess.run(
                ["git", "diff", "--exit-code", "--", "public/images/narrative"],
                cwd=root,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(ran.returncode, 0)


class Frozen7Cases(unittest.TestCase):
    LAST = Week9PreviewLeftoverReplay.LAST
    RECAP = Week9PreviewLeftoverReplay.RECAP

    def _helpers(self) -> ScoreProgressionBudgetExhaustion:
        return ScoreProgressionBudgetExhaustion("test_p1_seven_invalid_claims_drop_clean")

    def test_n1_clean_remainder_no_drops(self):
        edition = _exact_words(_thin_preview(), 3900)
        self.assertFalse(_hold(edition, drops=[]))
        parsed = _workflow("narrative.yml")
        clear = _run_fail_held_script(
            _job_step_script(parsed, "test", name="Fail held editions"),
            payload={"holdMerge": False},
        )
        self.assertEqual(clear.returncode, 0, clear.stderr)
        self.assertIsNone(
            drop_issues.sync_drop_issue(
                slug=edition["slug"], drops=[], corrections=[], hold=False
            )
        )

    def test_n2_drops_only_ships_and_files_one_issue(self):
        edition = _exact_words(_thin_preview(), 3900)
        parsed = _workflow("narrative.yml")
        hold_step = _job_step_script(parsed, "test", name="Fail held editions")
        for count in (1, 3, 6):
            with self.subTest(drops=count):
                drops = [f"Dropped body line {i}." for i in range(count)]
                self.assertFalse(_hold(edition, drops=drops))
                ran = _run_fail_held_script(
                    hold_step, payload={"holdMerge": False, "dropBody": "x"}
                )
                self.assertEqual(ran.returncode, 0, ran.stderr)
        body = drop_issues.build_issue_body(
            slug=edition["slug"],
            drops=["Dropped body line 0.", "line with `$(touch PWNED)` and @karen"],
            run_id="43272341853",
            edition=edition["edition"],
        )
        self.assertIn(edition["slug"], body)
        self.assertIn("43272341853", body)
        self.assertIn("Dropped body line 0.", body)
        self.assertIn("@\u200bkaren", body)
        self.assertNotIn("@karen", body.replace("@\u200b", ""))

    def test_n4_remainder_gate_failures_hold(self):
        edition = _fat_preview()
        rows = {
            "archive replay hard": ["score progression 7-3 disagrees with ESPN"],
            "check-edition other": ["press conference: embed missing"],
            "press/locker": ["locker room: unresolved post"],
            "leak": ["fact-checker note leaked ('wire'): Do not tell that story."],
        }
        for name, leftover in rows.items():
            with self.subTest(name=name):
                self.assertTrue(_hold(edition, leftover=leftover))
                self.assertTrue(_hold(edition, drops=["body"], leftover=leftover))
        thin = _exact_words(_thin_preview(), 3314)
        self.assertTrue(
            _hold(thin, drops=["The depth chart stayed on early downs."])
        )

    def test_n5_dangling_reference_holds_via_j2(self):
        before = _fat_preview(
            theEdge=(
                "Mahomes threw the pick. That is the credit line. "
                + _fat_preview()["theEdge"]
            )
        )
        after = json.loads(json.dumps(before))
        after["theEdge"] = "That is the credit line. " + _fat_preview()["theEdge"]
        dropped = ["Mahomes threw the pick."]
        orphans = facts.check_repair_orphans(after, dropped, before=before)
        self.assertTrue(orphans, orphans)
        blockers = facts.repair_publish_blockers([], after, orphans, before=before)
        self.assertTrue(blockers, blockers)
        self.assertTrue(_hold(after, drops=dropped, leftover=blockers, orphans=orphans))
        he_before = _fat_preview(
            theEdge="Hunt scored from the 1. He did it again. " + _fat_preview()["theEdge"]
        )
        he_after = json.loads(json.dumps(he_before))
        he_after["theEdge"] = "He did it again. " + _fat_preview()["theEdge"]
        he_orphans = facts.check_repair_orphans(
            he_after, ["Hunt scored from the 1."], before=he_before
        )
        self.assertTrue(he_orphans, he_orphans)
        helper = self._helpers()
        blocker = (
            "Chiefs Narrative fact-check left fragments after repair: "
            "orphan opener after repair ('That is the credit line.')"
        )
        raw = helper._fat_nonlede(
            helper.CLEAN + " " + helper._claim("7-3") + " That is the credit line."
        )
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(
                facts, "repair_publish_blockers", return_value=[blocker]
            ):
                result, _repair = helper._through_build(Path(tmp), raw)
            leftover = result.get("leftoverIssues") or []
            self.assertIn(blocker, leftover)
            self.assertTrue(
                facts.should_hold_automerge(
                    result.get("droppedSentences") or [],
                    result["narrative"],
                    leftover=leftover,
                )
            )

    def test_n6_empty_edition_holds_safe_lede_is_not_content(self):
        empty = {
            "headline": "",
            "lastGameReview": {"lede": facts.safe_score_lede(None)},
        }
        self.assertTrue(facts.only_safe_score_lede_content(empty))
        self.assertTrue(_hold(empty, drops=["everything"]))
        fat = _fat_preview()
        self.assertFalse(facts.only_safe_score_lede_content(fat))

    def test_n7_word_floor_boundaries(self):
        ships = _exact_words(_thin_preview(), 3900)
        under = _exact_words(_thin_preview(), 3899)
        self.assertEqual(facts.edition_word_count(ships), 3900)
        self.assertEqual(facts.edition_word_count(under), 3899)
        self.assertFalse(_hold(ships, drops=[]))
        self.assertTrue(_hold(under, drops=[]))
        started = _exact_words(_thin_preview(), 3000)
        fell = _exact_words(_thin_preview(), 2999)
        blockers = facts.repair_publish_blockers([], fell, [], before=started)
        self.assertTrue(blockers, blockers)
        self.assertTrue(_hold(fell, leftover=blockers))
        self.assertFalse(
            facts.repair_publish_blockers([], started, [], before=started)
        )
        helper = self._helpers()
        blocker = (
            "Chiefs Narrative fact-check repair left 2999 words; "
            f"floor is {facts.OFFLINE_WORD_FLOOR}."
        )
        raw = helper._fat_nonlede(helper.CLEAN + " " + helper._claim("7-3"))
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(
                facts, "repair_publish_blockers", return_value=[blocker]
            ):
                result, _repair = helper._through_build(Path(tmp), raw)
            leftover = result.get("leftoverIssues") or []
            self.assertIn(blocker, leftover)
            self.assertTrue(
                facts.should_hold_automerge(
                    result.get("droppedSentences") or [],
                    result["narrative"],
                    leftover=leftover,
                )
            )

    def test_n8_hold_repair_drops_8_and_9(self):
        self.assertEqual(facts.HOLD_REPAIR_DROPS, 8)
        self.assertFalse(8 > facts.HOLD_REPAIR_DROPS)
        self.assertTrue(9 > facts.HOLD_REPAIR_DROPS)
        source = Path(generate.__file__).read_text(encoding="utf-8")
        self.assertIn("len(gone) > facts.HOLD_REPAIR_DROPS", source)
        self.assertIn("facts.drop_ceiling_exceeded(dropped)", source)
        self.assertFalse(facts.drop_ceiling_exceeded(_body_drops(8)))
        self.assertTrue(facts.drop_ceiling_exceeded(_body_drops(9)))
        edition = _exact_words(_thin_preview(), 3900)
        eight = _body_drops(8)
        nine = _body_drops(9)
        self.assertFalse(_hold(edition, drops=eight))
        self.assertTrue(_hold(edition, drops=nine))
        self.assertTrue(
            _hold(
                edition,
                drops=nine,
                hold_only_sentinels=[
                    facts.hold_only_sentinel(facts.HOLD_ONLY_SALVAGE_NOISY)
                ],
            )
        )

    def _split_stage_build(self, tmp: Path, gone_by_stage: list[list[str]]):
        helper = self._helpers()
        raw = helper._fat_nonlede(helper.CLEAN)
        stages = {"n": 0}

        def fake_salvage(payload, problems, *args, **kwargs):
            idx = stages["n"]
            stages["n"] += 1
            gone = list(gone_by_stage[idx]) if idx < len(gone_by_stage) else []
            return payload, gone, [], []

        archive_n = {"n": 0}

        def fake_archive(*_args, **_kwargs):
            archive_n["n"] += 1
            if archive_n["n"] == 1:
                return ["score progression 7-3 after Q2 disagrees with ESPN"]
            return []

        result, repair_json = helper._through_build(
            tmp,
            raw,
            patches=(
                patch.object(
                    generate, "_salvage_until_clean", side_effect=fake_salvage
                ),
                patch.object(
                    facts,
                    "check_review",
                    return_value=[
                        "storyline: leftover score-progression claim (stage bait)"
                    ],
                ),
                patch.object(
                    generate, "archive_replay_issues", side_effect=fake_archive
                ),
                patch.object(
                    generate, "replay_fixture_missing", return_value=False
                ),
                patch.object(
                    facts,
                    "restore_required_copy",
                    side_effect=lambda target, *args, **kwargs: target,
                ),
            ),
        )
        self.assertEqual(stages["n"], 3, stages)
        return result, json.loads(repair_json.read_text(encoding="utf-8"))

    def _assert_n8_stage_outcome(self, result, repair, *, hold: bool):
        drops = result.get("droppedSentences") or []
        sentinels = result.get("holdOnlySentinels") or []
        noisy = [
            row
            for row in sentinels
            if isinstance(row, dict)
            and row.get("code") == facts.HOLD_ONLY_SALVAGE_NOISY
        ]
        parsed = _workflow("narrative.yml")
        hold_step = _job_step_script(
            parsed, "test", name="Fail held editions"
        )
        ran = _run_fail_held_script(
            hold_step, payload={"holdMerge": bool(repair.get("holdAutomerge"))}
        )
        if hold:
            self.assertTrue(noisy, sentinels)
            self.assertTrue(repair.get("holdAutomerge"), repair)
            self.assertEqual(ran.returncode, 1, ran.stderr)
            self.assertTrue(
                facts.should_hold_automerge(
                    drops,
                    result["narrative"],
                    leftover=result.get("leftoverIssues") or [],
                    corrections=result.get("correctedSentences") or [],
                    hold_only_sentinels=sentinels,
                    headline_or_lede_touched=bool(
                        result.get("headlineOrLedeTouched")
                    ),
                )
            )
        else:
            self.assertFalse(noisy, sentinels)
            self.assertFalse(repair.get("holdAutomerge"), repair)
            self.assertEqual(ran.returncode, 0, ran.stderr)

    def test_n8_aggregate_ceiling_split_across_stages(self):
        """A8/N8: ceiling is aggregate droppedSentences across every stage."""
        eight = [_body_drops(3, 0), _body_drops(3, 3), _body_drops(2, 6)]
        nine_split = [_body_drops(4, 0), _body_drops(3, 4), _body_drops(2, 7)]
        nine_archive = [_body_drops(5, 0), [], _body_drops(4, 5)]
        with tempfile.TemporaryDirectory() as tmp:
            result, repair = self._split_stage_build(Path(tmp), eight)
            self.assertEqual(len(result.get("droppedSentences") or []), 8)
            self._assert_n8_stage_outcome(result, repair, hold=False)
        with tempfile.TemporaryDirectory() as tmp:
            result, repair = self._split_stage_build(Path(tmp), nine_split)
            drops = result.get("droppedSentences") or []
            self.assertEqual(len(drops), 9)
            self.assertEqual(drops, _body_drops(4) + _body_drops(3, 4) + _body_drops(2, 7))
            self._assert_n8_stage_outcome(result, repair, hold=True)
        with tempfile.TemporaryDirectory() as tmp:
            result, repair = self._split_stage_build(Path(tmp), nine_archive)
            drops = result.get("droppedSentences") or []
            self.assertEqual(len(drops), 9)
            self.assertEqual(drops[-4:], _body_drops(4, 5))
            self._assert_n8_stage_outcome(result, repair, hold=True)

    def test_n9_issue_dedupe_update_and_safe_quoting(self):
        self.assertEqual(facts.DROPS_ISSUE_LABEL, "narrative-drops")
        self.assertEqual(
            drop_issues.issue_title("2026-11-07-1200"),
            "Chiefs Narrative dropped lines: 2026-11-07-1200",
        )
        calls = []

        def fake_gh(args, *, check=True):
            record = {"args": args}
            calls.append(record)
            result = subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            if args[:2] == ["issue", "list"]:
                if any(row["args"][:2] == ["issue", "create"] for row in calls[:-1]):
                    result.stdout = json.dumps(
                        [
                            {
                                "number": 11,
                                "title": drop_issues.issue_title("2026-11-07-1200"),
                                "body": drop_issues.build_issue_body(
                                    slug="2026-11-07-1200",
                                    drops=["first"],
                                    run_id="111",
                                ),
                            }
                        ]
                    )
                else:
                    result.stdout = "[]"
            if args[:2] == ["issue", "create"]:
                result.stdout = "https://github.com/stevologic/arrowheadpaesanowebsite/issues/11\n"
            return result

        with patch.object(drop_issues, "_gh", side_effect=fake_gh):
            first = drop_issues.sync_drop_issue(
                slug="2026-11-07-1200",
                drops=["first"],
                run_id="111",
            )
            self.assertTrue(first)
            second = drop_issues.sync_drop_issue(
                slug="2026-11-07-1200",
                drops=["first"],
                run_id="111",
            )
            self.assertEqual(second, "#11")
            updated = drop_issues.sync_drop_issue(
                slug="2026-11-07-1200",
                drops=["second `$(rm)` and @mention"],
                run_id="222",
            )
            self.assertEqual(updated, "#11")
        creates = [row for row in calls if row["args"][:2] == ["issue", "create"]]
        edits = [row for row in calls if row["args"][:2] == ["issue", "edit"]]
        self.assertEqual(len(creates), 1)
        self.assertTrue(edits)
        line = drop_issues.safe_issue_line("see @karen and `$(touch PWNED)`")
        self.assertIn("@\u200bkaren", line)
        self.assertIn("$(touch PWNED)", line)

    def test_n10_remainder_roster_error_holds(self):
        edition = _fat_preview()
        leftover = ["roster: unnamed Chiefs player"]
        self.assertTrue(
            _hold(
                edition,
                drops=["The depth chart stayed on early downs."],
                leftover=leftover,
            )
        )
        source = Path(generate.__file__).read_text(encoding="utf-8")
        self.assertIn("facts.check_diagram_captions", source)
        check_src = Path(generate.__file__).read_text(encoding="utf-8")
        self.assertIn("press_conferences.check_embeds", check_src)
        self.assertIn("locker_room.check_posts", check_src)
        self.assertIn("hero_posts.validate_sidecar", check_src)
        self.assertIn("archive_replay_issues", check_src)

    def test_n11_drop_body_lists_drops_without_held_when_shipping(self):
        shipping = drop_body.drop_body(
            {
                "holdAutomerge": False,
                "droppedSentences": ["Kelce scored from the 12."],
            }
        )
        self.assertIn("## Dropped sentences", shipping)
        self.assertIn("Kelce scored from the 12.", shipping)
        self.assertNotIn("Automerge is held.", shipping)
        held = drop_body.drop_body(
            {
                "holdAutomerge": True,
                "droppedSentences": ["Kelce scored from the 12."],
            }
        )
        self.assertIn("Automerge is held.", held)


class Frozen7Addenda(unittest.TestCase):
    def test_r1_body_correction_ships_and_logs(self):
        edition = _exact_words(_thin_preview(), 3900)
        correction = "A body claim stayed on early downs. → A fixed body claim stayed on early downs."
        self.assertFalse(_hold(edition, corrections=[correction]))
        body = drop_issues.build_issue_body(
            slug=edition["slug"],
            drops=[],
            corrections=[correction],
        )
        self.assertIn("A body claim stayed on early downs.", body)
        calls = []

        def fake_gh(args, *, check=True):
            calls.append(args)
            result = subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            if args[:2] == ["issue", "list"]:
                result.stdout = "[]"
            if args[:2] == ["issue", "create"]:
                result.stdout = "https://github.com/example/site/issues/12\n"
            return result

        with patch.object(drop_issues, "_gh", side_effect=fake_gh):
            filed = drop_issues.sync_drop_issue(
                slug=edition["slug"],
                drops=[],
                corrections=[correction],
                run_id="r1",
            )
        self.assertTrue(filed)
        self.assertTrue(
            any(args[:2] == ["issue", "create"] for args in calls),
            calls,
        )

    def test_r2_headline_correction_holds(self):
        edition = _fat_preview()
        self.assertTrue(
            _hold(
                edition,
                corrections=[f"{edition['headline']} → other"],
                before=edition,
            )
        )

    def test_r3_lede_correction_holds_next_sentence_does_not(self):
        lede = "Kansas City lost 24-28 in Denver."
        nxt = "The preview stays on the Jets."
        edition = _fat_preview(
            lastGameReview={
                "opponent": "Denver Broncos",
                "result": "L",
                "score": "KC 24–28",
                "lede": lede,
                "analysis": [nxt],
            }
        )
        self.assertEqual(facts.lede_paragraph(edition), lede)
        self.assertTrue(
            _hold(edition, corrections=[f"{lede} → rewritten"], before=edition)
        )
        self.assertFalse(
            _hold(edition, corrections=[f"{nxt} → rewritten"], before=edition)
        )
        pinned = _pinned()
        p2 = "The preview stays on the Jets after Denver."
        para1 = _fat_preview(
            lastGameReview={
                "opponent": "Denver Broncos",
                "result": "L",
                "score": "KC 24–28",
                "lede": pinned["quinton"] + "\n\n" + p2,
            }
        )
        fixed_p1, logs_p1 = known_errors.apply_known_errors(para1, None)
        self.assertTrue(logs_p1, logs_p1)
        self.assertIn("\n\n", logs_p1[0].split(" → ", 1)[0])
        self.assertEqual(facts.lede_paragraph(para1), pinned["quinton"])
        self.assertTrue(facts.copy_touches_headline_or_lede(logs_p1, para1))
        self.assertTrue(_hold(fixed_p1, corrections=logs_p1, before=para1))
        para2 = _fat_preview(
            lastGameReview={
                "opponent": "Denver Broncos",
                "result": "L",
                "score": "KC 24–28",
                "lede": lede + "\n\n" + pinned["quinton"],
            }
        )
        fixed_p2, logs_p2 = known_errors.apply_known_errors(para2, None)
        self.assertTrue(logs_p2, logs_p2)
        self.assertIn("\n\n", logs_p2[0].split(" → ", 1)[0])
        self.assertEqual(facts.lede_paragraph(para2), lede)
        self.assertFalse(facts.copy_touches_headline_or_lede(logs_p2, para2))
        self.assertFalse(_hold(fixed_p2, corrections=logs_p2, before=para2))

    def test_r4_dangling_reference_on_correction_holds(self):
        before = _fat_preview(
            theEdge=(
                "Mahomes threw the pick. As noted, the next snap is the Jets. "
                + _fat_preview()["theEdge"]
            )
        )
        after = json.loads(json.dumps(before))
        after["theEdge"] = (
            "As noted, the next snap is the Jets. " + _fat_preview()["theEdge"]
        )
        dropped = ["Mahomes threw the pick."]
        orphans = facts.check_repair_orphans(after, dropped, before=before)
        self.assertTrue(orphans, orphans)
        self.assertTrue(
            any("as-noted" in item or "orphan" in item for item in orphans)
        )
        blockers = facts.repair_publish_blockers(
            [], after, orphans, before=before
        )
        self.assertTrue(
            _hold(
                after,
                corrections=["Mahomes threw the pick. → dropped"],
                leftover=blockers,
                orphans=orphans,
                before=before,
            )
        )

    def test_r5_correction_plus_other_k_cause_holds(self):
        edition = _fat_preview()
        leftover = ["score progression 7-3 after Q2 disagrees with ESPN"]
        self.assertTrue(
            _hold(
                edition,
                corrections=["A body claim. → A fixed body claim."],
                leftover=leftover,
            )
        )
        untitled = _fat_preview(headline="")
        self.assertTrue(
            _hold(
                untitled,
                corrections=["A body claim. → A fixed body claim."],
            )
        )

    def test_a8_hold_repair_drops_ceiling_is_eight(self):
        self.assertEqual(facts.HOLD_REPAIR_DROPS, 8)
        self.assertFalse(8 > facts.HOLD_REPAIR_DROPS)
        self.assertTrue(9 > facts.HOLD_REPAIR_DROPS)
        self.assertFalse(facts.drop_ceiling_exceeded(_body_drops(8)))
        self.assertTrue(facts.drop_ceiling_exceeded(_body_drops(9)))

    def test_a9_computed_repair_blockers_are_holding_leftover(self):
        source = Path(generate.__file__).read_text(encoding="utf-8")
        routed = source.index("blockers = facts.repair_publish_blockers")
        appended = source.index("leftover.append(item)")
        printed = source.index("repair blocker:")
        self.assertLess(routed, appended)
        self.assertLess(appended, printed)

    def test_a10_one_lede_drop_holds_p2s1_ships(self):
        lede = "Kansas City lost 24-28 in Denver."
        p2s1 = "The slot job is the first question."
        edition = _exact_words(
            _fat_preview(
                lastGameReview={
                    "opponent": "Denver Broncos",
                    "result": "L",
                    "score": "KC 24–28",
                    "lede": lede,
                    "analysis": [p2s1 + " The rest of the paragraph stays."],
                }
            ),
            3900,
        )
        self.assertTrue(
            _hold(edition, drops=[lede], before=edition, headline_or_lede_touched=True)
        )
        self.assertTrue(facts.copy_touches_headline_or_lede([lede], edition))
        self.assertFalse(facts.copy_touches_headline_or_lede([p2s1], edition))
        self.assertFalse(_hold(edition, drops=[p2s1], before=edition))
        token = "Kansas"
        self.assertIn(token, lede)
        self.assertFalse(facts.copy_touches_headline_or_lede([token], edition))
        self.assertFalse(_hold(edition, drops=[token], before=edition))
        headline = edition["headline"]
        self.assertTrue(facts.copy_touches_headline_or_lede([headline], edition))
        self.assertTrue(_hold(edition, drops=[headline], before=edition))

    def _retry_known_error_build(self, tmp: Path, *, where: str):
        helper = ScoreProgressionBudgetExhaustion(
            "test_p1_seven_invalid_claims_drop_clean"
        )
        pinned = _pinned()
        first = helper._fat_nonlede(helper.CLEAN)
        second = json.loads(json.dumps(first))
        p2 = "The preview stays on the Jets after Denver."
        if where == "headline":
            second["headline"] = pinned["quinton"]
        elif where == "lede":
            review = dict(second.get("lastGameReview") or {})
            review["lede"] = pinned["quinton"] + "\n\n" + p2
            second["lastGameReview"] = review
        elif where == "p2s1":
            review = dict(second.get("lastGameReview") or {})
            review["lede"] = "Kansas City lost 24-28 in Denver."
            review["analysis"] = [pinned["quinton"]]
            second["lastGameReview"] = review
        else:
            raise AssertionError(where)
        reviews = [
            ["storyline: leftover score-progression claim (retry bait)"],
        ]
        llm = Mock(side_effect=[(first, "grok"), (second, "grok")])

        def fake_review(*_args, **_kwargs):
            if reviews:
                return reviews.pop(0)
            return []

        return helper._through_build(
            tmp,
            first,
            llm=llm,
            patches=(
                patch.object(facts, "check_review", side_effect=fake_review),
                patch.object(generate, "archive_replay_issues", return_value=[]),
                patch.object(
                    generate, "replay_fixture_missing", return_value=False
                ),
                patch.object(
                    facts,
                    "restore_required_copy",
                    side_effect=lambda target, *args, **kwargs: target,
                ),
            ),
        )

    def test_a10_retry_known_error_snapshot(self):
        """A10/R2/R3: retry apply_known_errors snapshots headline/lede first."""
        with tempfile.TemporaryDirectory() as tmp:
            result, repair_json = self._retry_known_error_build(
                Path(tmp), where="headline"
            )
            repair = json.loads(repair_json.read_text(encoding="utf-8"))
            self.assertTrue(result.get("headlineOrLedeTouched"), result)
            self.assertTrue(repair.get("holdAutomerge"), repair)
            self.assertTrue(
                any("Quinton Walker" in row for row in result.get("correctedSentences") or []),
                result.get("correctedSentences"),
            )
        with tempfile.TemporaryDirectory() as tmp:
            result, repair_json = self._retry_known_error_build(
                Path(tmp), where="lede"
            )
            repair = json.loads(repair_json.read_text(encoding="utf-8"))
            self.assertTrue(result.get("headlineOrLedeTouched"), result)
            self.assertTrue(repair.get("holdAutomerge"), repair)
        with tempfile.TemporaryDirectory() as tmp:
            result, repair_json = self._retry_known_error_build(
                Path(tmp), where="p2s1"
            )
            repair = json.loads(repair_json.read_text(encoding="utf-8"))
            self.assertTrue(result.get("correctedSentences"), result)
            self.assertFalse(result.get("headlineOrLedeTouched"), result)
            self.assertFalse(repair.get("holdAutomerge"), repair)

    def test_a11_lede_is_first_body_paragraph(self):
        edition = _fat_preview(
            lastGameReview={
                "lede": "Paragraph one sentence one. Paragraph one sentence two.",
                "analysis": ["Paragraph two sentence one."],
            }
        )
        lede = facts.lede_paragraph(edition)
        self.assertIn("Paragraph one sentence one.", lede)
        self.assertNotIn("Paragraph two sentence one.", lede)
        pinned = _pinned()
        two_para = _fat_preview(
            lastGameReview={
                "lede": pinned["quinton"] + "\n\n" + "The preview stays on the Jets after Denver."
            }
        )
        self.assertEqual(facts.lede_paragraph(two_para), pinned["quinton"])
        self.assertNotIn("Jets after Denver", facts.lede_paragraph(two_para))

    def test_a12_flexed_floor_boundaries(self):
        self.assertEqual(facts.DROP_REDUCED_WORD_FLOOR, 3315)
        drops = ["one body drop"]
        ships = _exact_words(_thin_preview(), 3315)
        under = _exact_words(_thin_preview(), 3314)
        self.assertEqual(facts.edition_word_count(ships), 3315)
        self.assertEqual(facts.edition_word_count(under), 3314)
        self.assertFalse(_hold(ships, drops=drops))
        self.assertTrue(_hold(under, drops=drops))
        dangling = ["orphan opener after repair ('That is the credit line.')"]
        self.assertTrue(_hold(ships, drops=drops, leftover=dangling, orphans=dangling))
        self.assertTrue(_hold(ships, drops=drops, leftover=[], orphans=dangling))
        self.assertTrue(_hold(ships, drops=[]))
        self.assertEqual(
            facts.word_shortfall_below_publish_floor(ships),
            facts.PUBLISH_WORD_FLOOR - 3315,
        )
        body = drop_issues.build_issue_body(
            slug="2026-11-07-1200",
            drops=drops,
            shortfall=facts.word_shortfall_below_publish_floor(ships),
        )
        self.assertIn("585", body)

    def test_a13_score_progression_budget_constant_used(self):
        source = Path(facts.__file__).read_text(encoding="utf-8")
        self.assertIn("SCORE_PROGRESSION_REPAIR_BUDGET", source)
        self.assertIn("_score_progression_remaining", source)
        self.assertIn("budget_ledger", source)
        self.assertNotIn("for _ in range(8):", source)


class Frozen7Callers(unittest.TestCase):
    """N12 real callers. Do not mock both --gate-replay and --check-edition."""

    def test_n12_narrative_yml_test_job_steps_by_name(self):
        parsed = _workflow("narrative.yml")
        names = [
            step.get("name")
            for step in ((parsed.get("jobs") or {}).get("test") or {}).get("steps")
            or []
            if isinstance(step, dict)
        ]
        self.assertIn("Gate generated content", names)
        self.assertIn("Record publish metadata", names)
        self.assertIn("Fail held editions", names)
        gate = _job_step_script(parsed, "test", name="Gate generated content")
        self.assertIn("python -m unittest discover -s tools/tests -v", gate)
        self.assertIn("python -m tools.chiefs_narrative.generate --gate-replay", gate)
        self.assertIn("python -m tools.chiefs_narrative.generate --check-edition", gate)
        self.assertIn("python -m tools.chiefs_narrative.generate --diagrams-only", gate)
        self.assertIn("git diff --exit-code -- public/images/narrative", gate)
        self.assertIn("hugo --gc --minify", gate)
        self.assertIn("python -m tools.chiefs_narrative.review_gate --pages", gate)
        meta = _job_step_script(parsed, "test", name="Record publish metadata")
        self.assertIn("holdMerge", meta)
        self.assertIn("drop_body.drop_body", meta)

    def test_n12_record_publish_metadata_and_fail_held(self):
        parsed = _workflow("narrative.yml")
        meta = _job_step_script(parsed, "test", name="Record publish metadata")
        hold = _job_step_script(parsed, "test", name="Fail held editions")
        inner = _extract_heredoc(meta)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            desk = root / "data"
            desk.mkdir()
            (desk / "narrative.json").write_text(
                json.dumps(_fat_preview()) + "\n", encoding="utf-8"
            )
            (desk / "narrative_repair.json").write_text(
                json.dumps(
                    {
                        "holdAutomerge": False,
                        "droppedSentences": ["body drop"],
                        "leftoverIssues": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["DESK_MODE"] = "full"
            env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
            ran = subprocess.run(
                [sys.executable, "-c", inner],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(ran.returncode, 0, ran.stderr + ran.stdout)
            published = json.loads(
                (root / ".narrative-publish.json").read_text(encoding="utf-8")
            )
            self.assertFalse(published.get("holdMerge"))
            self.assertIn("Dropped sentences", published.get("dropBody") or "")
        clear = _run_fail_held_script(hold, payload={"holdMerge": False})
        self.assertEqual(clear.returncode, 0, clear.stderr)

    def test_n12_update_job_label_and_issue(self):
        parsed = _workflow("narrative.yml")
        update = (parsed.get("jobs") or {}).get("update") or {}
        self.assertEqual(update.get("permissions", {}).get("issues"), "write")
        publish = _job_step_script(
            parsed,
            "update",
            name="Queue draft pull request for approved PR-event gates",
        )
        issue_step = _job_step_script(
            parsed, "update", name="File dropped-lines tracking issue"
        )
        self.assertIn("--add-label hold", publish)
        self.assertIn("python -m tools.chiefs_narrative.drop_issues", issue_step)
        from tools.tests.test_maintenance import PublicationPolicy

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = PublicationPolicy._fake_commands(root)
            env["DESK_MODE"] = "full"
            env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
            (root / ".narrative-publish.json").write_text(
                json.dumps(
                    {
                        "holdMerge": False,
                        "dropBody": drop_body.drop_body(
                            {
                                "holdAutomerge": False,
                                "droppedSentences": ["body drop"],
                            }
                        ),
                        "edition": "2026 Week 9 · Preview",
                        "headline": "Week 9 Preview",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            data = root / "data"
            data.mkdir()
            (data / "narrative.json").write_text(
                json.dumps(_fat_preview()) + "\n", encoding="utf-8"
            )
            (data / "narrative_repair.json").write_text(
                json.dumps(
                    {
                        "droppedSentences": ["body drop"],
                        "correctedSentences": [],
                        "holdAutomerge": False,
                        "wordShortfall": 0,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            ran = subprocess.run(
                ["bash", "-c", publish],
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
            self.assertFalse(labels)
            ran_issue = subprocess.run(
                [sys.executable, "-m", "tools.chiefs_narrative.drop_issues"],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(ran_issue.returncode, 0, ran_issue.stderr)
            issue_calls = [
                json.loads(line)
                for line in (root / "calls.jsonl").read_text().splitlines()
            ]
            created_issue = [
                row
                for row in issue_calls
                if row["command"] == "gh" and row["args"][:2] == ["issue", "create"]
            ]
            self.assertTrue(created_issue, issue_calls)

    def test_n12_publish_job_edition_merge_refuse_and_proceed(self):
        parsed = _workflow("narrative.yml")
        refuse = _job_step_script(
            parsed, "publish", name="Refuse hold or unresolved review findings"
        )
        self.assertIn("tools.chiefs_narrative.edition_merge", refuse)
        squash = None
        for step in ((parsed.get("jobs") or {}).get("publish") or {}).get("steps") or []:
            if step.get("name") == "Squash-merge the edition pull request":
                squash = step
        self.assertEqual(squash.get("if"), "steps.refuse.outputs.refused != 'true'")
        self.assertEqual(
            edition_merge.refuse_reason(labels=[{"name": "hold"}]),
            "hold label",
        )
        self.assertIsNone(edition_merge.refuse_reason(labels=[]))

    def test_n12_ci_yml_l129_schedule_mode_holdmerge_fixture(self):
        source = _workflow_source("ci.yml")
        self.assertIn("{'mode': 'schedule', 'holdMerge': True}", source)
        parsed = _workflow("ci.yml")
        step = _job_step_script(
            parsed, "gates", name="Prepare publication artifact compatibility fixture"
        )
        self.assertIn("holdMerge", step)
        self.assertIn("schedule", step)
        verify = _job_step_script(
            parsed, "gates", name="Verify publication artifact bytes and hidden metadata"
        )
        self.assertIn(".narrative-publish.json", verify)

    def test_n12_pages_unsigned_review_exit_2_no_deploy(self):
        parsed = _workflow("pages.yml")
        skip = _job_step_script(
            parsed, "build-deploy", name="Skip unsigned Review editions"
        )
        self.assertIn("review_gate --pages --dist dist", skip)
        self.assertIn('"$rc"-eq2', skip.replace(" ", ""))
        publish = None
        for step in ((parsed.get("jobs") or {}).get("build-deploy") or {}).get("steps") or []:
            if step.get("name") == "Publish to gh-pages":
                publish = step
        self.assertEqual(publish.get("if"), "steps.review.outputs.skip != 'true'")

    def test_n12_unmocked_salvage_path_through_generate_build(self):
        """One fixture end to end through generate.py salvage. N2 shape."""
        helper = ScoreProgressionBudgetExhaustion(
            "test_p1_seven_invalid_claims_drop_clean"
        )
        claims = " ".join(helper._claims(3))
        raw = helper._fat_nonlede(helper.CLEAN + " " + claims)
        with tempfile.TemporaryDirectory() as tmp:
            result, repair_json = helper._through_build(Path(tmp), raw)
            leftover = result.get("leftoverIssues") or []
            drops = result.get("droppedSentences") or []
            self.assertTrue(drops, result)
            self.assertLessEqual(len(drops), facts.HOLD_REPAIR_DROPS)
            budget = [
                item
                for item in leftover
                if facts.is_score_progression_budget_exhausted(item)
            ]
            self.assertEqual(budget, [])
            self.assertFalse(
                facts.should_hold_automerge(
                    drops,
                    result["narrative"],
                    leftover=leftover,
                    corrections=result.get("correctedSentences") or [],
                    hold_only_sentinels=result.get("holdOnlySentinels") or [],
                    headline_or_lede_touched=bool(
                        result.get("headlineOrLedeTouched")
                    ),
                ),
                (drops, leftover, result.get("holdOnlySentinels")),
            )
            repair = json.loads(repair_json.read_text(encoding="utf-8"))
            self.assertFalse(repair.get("holdAutomerge"), repair)


class Frozen7O1(unittest.TestCase):
    def test_o1_base_contains_323_merge(self):
        ran = subprocess.run(
            [
                "git",
                "merge-base",
                "--is-ancestor",
                "f7666b880291648d0762bfd995d9e9f61423e349",
                "HEAD",
            ],
            cwd=REPO,
            capture_output=True,
            text=True,
        )
        self.assertEqual(ran.returncode, 0, ran.stderr)


if __name__ == "__main__":
    unittest.main()
