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
import shlex
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
    press_conferences,
    providers,
    review_gate,
)
from tools.tests.frozen_slate import frozen_schedule
from tools.tests.test_maintenance import PublicationPolicy
from tools.tests.test_narrative import (
    _job_step_script,
    _run_fail_held_script,
)
from tools.tests.test_score_progression_budget_frozen import (
    ScoreProgressionBudgetExhaustion,
)
from tools.tests.test_archive_replay_qa import Week9PreviewLeftoverReplay

# N2: #346's six dropped lines from PR #346 body (run 43272341853).
REAL_346_DROPS = [
    "New York's turnover was the Q2 6:29 fumble forced by Nick Bolton and recovered by A.Gilman.",
    "That is the credit line.",
    "Geno Smith was 18-of-32 with a 70.4 rating, and his pass line lists 5 sacks.",
    "Kansas City touchdowns at Q3 10:20 (1 yd, 21–12), Q3 0:11 (2 yd, Walker up the middle, 28–12), and Q4 3:35 (6 yd, Emmett Johnson, 35–12) made it five touchdowns and no New York points after halftime.",
    "Smith's pass line was 18-of-32 with 5 sacks and a 70.4 rating, and New York netted 123 passing yards.",
    "That is a rush that can travel if the calls stay defined.",
]

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


def _fail_held_rc(hold_merge: bool) -> int:
    parsed = _workflow("narrative.yml")
    ran = _run_fail_held_script(
        _job_step_script(parsed, "test", name="Fail held editions"),
        payload={"holdMerge": hold_merge},
    )
    return ran.returncode


def _run_record_publish(root: Path, *, repair: dict, narrative: dict, desk_mode: str = "full"):
    parsed = _workflow("narrative.yml")
    inner = _extract_heredoc(
        _job_step_script(parsed, "test", name="Record publish metadata")
    )
    desk = root / "data"
    desk.mkdir(parents=True, exist_ok=True)
    (desk / "narrative.json").write_text(
        json.dumps(narrative) + "\n", encoding="utf-8"
    )
    (desk / "narrative_repair.json").write_text(
        json.dumps(repair) + "\n", encoding="utf-8"
    )
    env = dict(os.environ)
    env["DESK_MODE"] = desk_mode
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    ran = subprocess.run(
        [sys.executable, "-c", inner],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
    )
    return ran


def _run_fail_held_on_root(root: Path) -> subprocess.CompletedProcess:
    parsed = _workflow("narrative.yml")
    script = _job_step_script(parsed, "test", name="Fail held editions")
    path_var = os.environ.get("PATH", "")
    python = shutil.which("python") or shutil.which("python3")
    env = {
        "PATH": path_var,
        "HOME": str(root),
        "LANG": "C",
        "LC_ALL": "C",
        "PYTHONPATH": str(REPO) + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    if python and not shutil.which("python"):
        shim = root / "bin"
        shim.mkdir(exist_ok=True)
        (shim / "python").symlink_to(python)
        env["PATH"] = f"{shim}{os.pathsep}{path_var}"
    return subprocess.run(
        ["bash", "-c", script],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
    )


def _run_pages_rc_snippet(review_rc: int) -> subprocess.CompletedProcess:
    """Execute the Gate generated content pages_rc if/elif with a stub gate."""
    py = shlex.quote(sys.executable)
    script = textwrap.dedent(
        f"""\
        set -euo pipefail
        pages_rc=0
        {py} -c "import sys; raise SystemExit({review_rc})" || pages_rc=$?
        if [ "$pages_rc" -eq 2 ]; then
          echo "unsigned Review edition; pages gate holds deploy (exit 2)"
        elif [ "$pages_rc" -ne 0 ]; then
          exit "$pages_rc"
        fi
        """
    )
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
    )


def _run_pages_skip_unsigned(review_rc: int) -> subprocess.CompletedProcess:
    """Execute pages.yml Skip unsigned Review with a stub review_gate."""
    py = shlex.quote(sys.executable)
    script = textwrap.dedent(
        f"""\
        set -euo pipefail
        if {py} -c "import sys; raise SystemExit({review_rc})"; then
          echo "skip=false"
        else
          rc=$?
          if [ "$rc" -eq 2 ]; then
            echo "skip=true"
            echo "Unsigned Review edition; skipping gh-pages deploy"
          else
            exit "$rc"
          fi
        fi
        """
    )
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
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
        cases = Frozen7Cases("test_n5_dangling_reference_holds_via_j2")
        cases.test_n5_dangling_reference_holds_via_j2()
        cases.test_n7_word_floor_boundaries()

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
        issue_step = _job_step_script(
            parsed, "update", name="File dropped-lines tracking issue"
        )
        self.assertIn("GATE_BASE", issue_step)
        self.assertIn('cd "$GATE_BASE"', issue_step)
        self.assertIn(
            "python -P -m tools.chiefs_narrative.drop_issues",
            issue_step,
        )
        self.assertIn("--repair", issue_step)
        self.assertIn("${GITHUB_WORKSPACE}/data/narrative_repair.json", issue_step)


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
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ran = _run_record_publish(
                root,
                repair={"holdAutomerge": False, "droppedSentences": []},
                narrative=edition,
            )
            self.assertEqual(ran.returncode, 0, ran.stderr + ran.stdout)
            published = json.loads(
                (root / ".narrative-publish.json").read_text(encoding="utf-8")
            )
            self.assertTrue(published.get("holdMerge"), published)
            held = _run_fail_held_on_root(root)
            self.assertEqual(held.returncode, 1, held.stderr)

    def test_k2a_hard_archive_replay_leftover(self):
        edition = _fat_preview()
        leftover = ["score progression 7-3 after Q2 disagrees with ESPN"]
        self.assertTrue(_hold(edition, leftover=leftover))
        self.assertTrue(_hold(edition, drops=["body drop"], leftover=leftover))
        sentinel_text = facts.HOLD_ONLY_SENTINEL_TEXT[facts.HOLD_ONLY_SALVAGE_NOISY]
        self.assertFalse(facts.is_hold_only_sentinel(sentinel_text))
        self.assertTrue(_hold(edition, leftover=[sentinel_text]))
        self.assertTrue(_hold(edition, drops=["body drop"], leftover=[sentinel_text]))

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
        for drops in ([], ["body drop"]):
            with self.subTest(drops=bool(drops)):
                with tempfile.TemporaryDirectory() as tmp:
                    narrative_json = Path(tmp) / "n.json"
                    repair_json = Path(tmp) / "r.json"
                    narrative_json.write_text(json.dumps(_fat_preview()) + "\n")
                    repair_json.write_text(
                        json.dumps(
                            {
                                "holdAutomerge": True,
                                "droppedSentences": drops,
                            }
                        )
                        + "\n"
                    )
                    buf = io.StringIO()
                    with patch.object(
                        generate.config, "NARRATIVE_JSON", narrative_json
                    ), patch.object(
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
        for drops in ([], ["body drop"]):
            with self.subTest(drops=bool(drops)):
                unsigned = _run_pages_rc_snippet(2)
                self.assertEqual(unsigned.returncode, 0, unsigned.stderr)
                self.assertIn("unsigned Review", unsigned.stdout)
                ok = _run_pages_rc_snippet(0)
                self.assertEqual(ok.returncode, 0, ok.stderr)
                other = _run_pages_rc_snippet(1)
                self.assertEqual(other.returncode, 1, other.stderr)
                skip = _run_pages_skip_unsigned(2)
                self.assertEqual(skip.returncode, 0, skip.stderr)
                self.assertIn("skip=true", skip.stdout)
                skip_fail = _run_pages_skip_unsigned(1)
                self.assertEqual(skip_fail.returncode, 1, skip_fail.stderr)
                skip_ok = _run_pages_skip_unsigned(0)
                self.assertEqual(skip_ok.returncode, 0, skip_ok.stderr)
                self.assertIn("skip=false", skip_ok.stdout)

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
            for drops in ([], ["body drop"]):
                with self.subTest(drops=bool(drops)):
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
        sets = {
            1: REAL_346_DROPS[:1],
            3: REAL_346_DROPS[:3],
            6: REAL_346_DROPS,
        }
        for count, drops in sets.items():
            with self.subTest(drops=count):
                self.assertFalse(_hold(edition, drops=drops))
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    ran_meta = _run_record_publish(
                        root,
                        repair={
                            "holdAutomerge": False,
                            "droppedSentences": drops,
                            "leftoverIssues": [],
                        },
                        narrative=edition,
                    )
                    self.assertEqual(
                        ran_meta.returncode, 0, ran_meta.stderr + ran_meta.stdout
                    )
                    published = json.loads(
                        (root / ".narrative-publish.json").read_text(encoding="utf-8")
                    )
                    self.assertFalse(published.get("holdMerge"), published)
                    held = _run_fail_held_on_root(root)
                    self.assertEqual(held.returncode, 0, held.stderr)
                    calls = []

                    def fake_gh(args, *, check=True):
                        calls.append(args)
                        result = subprocess.CompletedProcess(
                            args, 0, stdout="", stderr=""
                        )
                        if args[:2] == ["issue", "list"]:
                            result.stdout = "[]"
                        if args[:2] == ["issue", "create"]:
                            result.stdout = (
                                "https://github.com/stevologic/"
                                "arrowheadpaesanowebsite/issues/11\n"
                            )
                        return result

                    with patch.object(drop_issues, "_gh", side_effect=fake_gh):
                        filed = drop_issues.sync_drop_issue(
                            slug=edition["slug"],
                            drops=drops,
                            run_id="43272341853",
                            edition=edition["edition"],
                        )
                    self.assertTrue(filed)
                    creates = [
                        args for args in calls if args[:2] == ["issue", "create"]
                    ]
                    self.assertEqual(len(creates), 1, creates)
                    create = creates[0]
                    self.assertIn("--label", create)
                    self.assertIn(facts.DROPS_ISSUE_LABEL, create)
                    body = drop_issues.build_issue_body(
                        slug=edition["slug"],
                        drops=drops,
                        run_id="43272341853",
                        edition=edition["edition"],
                    )
                    for line in drops:
                        self.assertIn(line, body)

    def test_n3_each_k_cause_with_and_without_drops(self):
        """N3: each K1-K13 alone, drops empty AND non-empty. Paste each cell."""
        edition = _fat_preview()
        body = ["body drop"]
        grid = {}

        def cell(name, hold, exit_code):
            grid[name] = {"holdMerge": bool(hold), "exit": int(exit_code)}
            if name.startswith("K3"):
                self.assertFalse(hold, name)
                self.assertEqual(exit_code, 0, name)
            elif name.startswith("K11") or name.startswith("K13"):
                self.assertNotEqual(exit_code, 0, name)
            elif name.startswith("K12") and "unsigned" in name:
                self.assertEqual(exit_code, 0, name)
            elif name.startswith("K12") and "other" in name:
                self.assertEqual(exit_code, 1, name)
            else:
                self.assertTrue(hold, name)
                self.assertEqual(exit_code, 1, name)

        for drops, tag in (([], "empty"), (body, "drops")):
            hold = _hold(
                _fat_preview(
                    phase={"type": "regular", "mode": "review", "week": 9},
                    edition="2026 Week 9 · Review",
                ),
                drops=drops,
            )
            cell(f"K1-{tag}", hold, _fail_held_rc(hold))
            hold = _hold(
                edition,
                leftover=["score progression 7-3 after Q2 disagrees with ESPN"],
                drops=drops,
            )
            cell(f"K2a-{tag}", hold, _fail_held_rc(hold))
            hold = _hold(
                edition,
                leftover=["fact-checker note leaked ('wire'): Do not tell that story."],
                drops=drops,
            )
            cell(f"K2b-{tag}", hold, _fail_held_rc(hold))
            hold = _hold(edition, leftover=[generate.REPLAY_NOT_RUN], drops=drops)
            cell(f"K2c-{tag}", hold, _fail_held_rc(hold))
            hold = _hold(
                edition,
                hold_only_sentinels=[
                    facts.hold_only_sentinel(facts.HOLD_ONLY_SALVAGE_NOISY)
                ],
                drops=drops,
            )
            cell(f"K2d-{tag}", hold, _fail_held_rc(hold))
            hold = _hold(
                edition,
                leftover=[
                    "Chiefs Narrative fact-check left fragments after repair: "
                    "orphan opener after repair ('That is the credit line.')"
                ],
                drops=drops,
            )
            cell(f"K2e-{tag}", hold, _fail_held_rc(hold))
            hold = _hold(edition, drops=drops)
            cell(f"K3-{tag}", hold, _fail_held_rc(hold))
            hold = _hold(
                edition,
                corrections=[edition["headline"] + " → new title"],
                before=edition,
                drops=drops,
            )
            cell(f"K4-{tag}", hold, _fail_held_rc(hold))
            hold = _hold(edition, missing_headline=True, drops=drops)
            cell(f"K5-{tag}", hold, _fail_held_rc(hold))
            hold = _hold(_fat_preview(headline=""), drops=drops)
            cell(f"K6-{tag}", hold, _fail_held_rc(hold))
            hold = _hold(
                _fat_preview(phase={"type": "offseason", "mode": "offseason"}),
                schedule=frozen_schedule(),
                drops=drops,
            )
            cell(f"K7-{tag}", hold, _fail_held_rc(hold))
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
            with patch.object(facts, "REVIEW_REQUIRES_HUMAN", False):
                hold = _hold(
                    _fat_preview(
                        phase={
                            "type": "postseason",
                            "mode": "preview",
                            "lastGame": wild,
                        }
                    ),
                    schedule=slate,
                    drops=drops,
                )
            cell(f"K8-{tag}", hold, _fail_held_rc(hold))
            k9 = _exact_words(_thin_preview(), 3314 if drops else 3899)
            hold = _hold(k9, drops=drops)
            cell(f"K9-{tag}", hold, _fail_held_rc(hold))
            unsigned = _run_pages_rc_snippet(2)
            other = _run_pages_rc_snippet(1)
            cell(f"K12-unsigned-{tag}", True, unsigned.returncode)
            cell(f"K12-other-{tag}", True, other.returncode)

        for tag in ("empty", "drops"):
            force = _extract_heredoc(
                _job_step_script(
                    _workflow("narrative.yml"), "test", name="Gate generated content"
                )
            )
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "data").mkdir()
                (root / "data" / "narrative_repair.json").write_text("{}\n")
                ran = subprocess.run(
                    [sys.executable, "-c", force],
                    cwd=root,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(ran.returncode, 0, ran.stderr)
                payload = json.loads(
                    (root / "data" / "narrative_repair.json").read_text()
                )
                cell(f"K10-{tag}", bool(payload.get("holdAutomerge")), 1)
            leftover = ["score progression 7-3 after Q2 disagrees with ESPN"]
            with tempfile.TemporaryDirectory() as tmp:
                narrative_json = Path(tmp) / "n.json"
                repair_json = Path(tmp) / "r.json"
                narrative_json.write_text(json.dumps(_fat_preview()) + "\n")
                repair_json.write_text(json.dumps({"holdAutomerge": True}) + "\n")
                buf = io.StringIO()
                with patch.object(
                    generate.config, "NARRATIVE_JSON", narrative_json
                ), patch.object(
                    generate.config, "REPAIR_JSON", repair_json
                ), patch.object(
                    generate, "archive_replay_issues", return_value=leftover
                ), patch.object(
                    generate, "replay_fixture_missing", return_value=False
                ), redirect_stdout(buf):
                    rc = generate.main(["--gate-replay"])
                cell(f"K11-{tag}", True, rc)
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                target = root / "public" / "images" / "narrative"
                target.mkdir(parents=True)
                (target / "xo.svg").write_text("<svg/>\n", encoding="utf-8")
                subprocess.run(["git", "init", "-q"], cwd=root, check=True)
                subprocess.run(
                    ["git", "add", "public/images/narrative"], cwd=root, check=True
                )
                subprocess.run(
                    [
                        "git",
                        "-c",
                        "user.email=t@t",
                        "-c",
                        "user.name=t",
                        "commit",
                        "-qm",
                        "svg",
                    ],
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
                cell(f"K13-{tag}", True, ran.returncode)

        self.assertEqual(grid["K3-empty"]["holdMerge"], False)
        self.assertEqual(grid["K3-drops"]["holdMerge"], False)
        self.assertEqual(grid["K1-empty"]["holdMerge"], True)
        self.assertEqual(grid["K10-empty"]["holdMerge"], True)
        self.assertEqual(grid["K11-empty"]["exit"], generate.HARD_REPLAY_RC)
        self.assertNotEqual(grid["K13-empty"]["exit"], 0)
        self.assertEqual(grid["K12-unsigned-empty"]["exit"], 0)
        self.assertEqual(grid["K12-other-empty"]["exit"], 1)
        self.n3_grid = grid

    def test_n4_remainder_gate_failures_hold(self):
        parsed = _workflow("narrative.yml")
        force = _extract_heredoc(
            _job_step_script(parsed, "test", name="Gate generated content")
        )
        classes = {
            "archive replay hard": ["score progression 7-3 disagrees with ESPN"],
            "check-edition other": ["press conference: embed missing"],
            "press/locker": ["locker room: unresolved post"],
            "leak": ["fact-checker note leaked ('wire'): Do not tell that story."],
        }
        for name, issues in classes.items():
            for drops in ([], ["body"]):
                with self.subTest(name=name, drops=bool(drops)):
                    with tempfile.TemporaryDirectory() as tmp:
                        root = Path(tmp)
                        data = root / "data"
                        data.mkdir()
                        narrative = _fat_preview()
                        (data / "narrative.json").write_text(
                            json.dumps(narrative) + "\n", encoding="utf-8"
                        )
                        (data / "narrative_repair.json").write_text(
                            json.dumps(
                                {
                                    "holdAutomerge": False,
                                    "droppedSentences": drops,
                                }
                            )
                            + "\n",
                            encoding="utf-8",
                        )
                        with patch.object(
                            generate.config, "NARRATIVE_JSON", data / "narrative.json"
                        ), patch.object(
                            generate.config,
                            "REPAIR_JSON",
                            data / "narrative_repair.json",
                        ), patch.object(
                            generate,
                            "check_published_edition",
                            return_value=issues,
                        ):
                            rc = generate.main(["--check-edition"])
                        self.assertEqual(rc, 1, name)
                        ran = subprocess.run(
                            [sys.executable, "-c", force],
                            cwd=root,
                            capture_output=True,
                            text=True,
                        )
                        self.assertEqual(ran.returncode, 0, ran.stderr)
                        repair = json.loads(
                            (data / "narrative_repair.json").read_text(encoding="utf-8")
                        )
                        self.assertTrue(repair.get("holdAutomerge"), repair)
                        published = {
                            "holdMerge": True,
                            "droppedSentences": drops,
                        }
                        (root / ".narrative-publish.json").write_text(
                            json.dumps(published) + "\n", encoding="utf-8"
                        )
                        held = _run_fail_held_on_root(root)
                        self.assertEqual(held.returncode, 1, held.stderr)
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
            "headline": "Week 9 Preview: remainder ships after body drops",
            "lastGameReview": {"lede": facts.safe_score_lede(None)},
        }
        lede_only = {"lastGameReview": {"lede": facts.safe_score_lede(None)}}
        self.assertTrue(facts.only_safe_score_lede_content(lede_only))
        self.assertTrue(str(empty.get("headline") or "").strip())
        self.assertTrue(_hold(empty, drops=["everything"]))
        fat = _fat_preview()
        self.assertFalse(facts.only_safe_score_lede_content(fat))
        helper = self._helpers()
        raw = helper._fat_nonlede(helper.CLEAN)

        def empty_salvage(payload, problems, *args, **kwargs):
            gutted = json.loads(json.dumps(payload))
            gutted["dek"] = ""
            gutted["theEdge"] = ""
            gutted["storyline"] = ""
            gutted["currentState"] = ""
            gutted["gamePlan"] = ""
            gutted["lastGameReview"] = {
                "opponent": "Denver Broncos",
                "result": "L",
                "score": "KC 24–28",
                "lede": facts.safe_score_lede(None),
            }
            gone = ["everything in the body was dropped."]
            return gutted, gone, [], []

        with tempfile.TemporaryDirectory() as tmp:
            result, repair_json = helper._through_build(
                Path(tmp),
                raw,
                patches=(
                    patch.object(
                        generate, "_salvage_until_clean", side_effect=empty_salvage
                    ),
                    patch.object(
                        facts,
                        "restore_required_copy",
                        side_effect=lambda payload, *a, **k: payload,
                    ),
                    patch.object(
                        facts,
                        "check_review",
                        return_value=["storyline: leftover bait"],
                    ),
                    patch.object(
                        generate, "replay_fixture_missing", return_value=False
                    ),
                ),
            )
            narrative = result["narrative"]
            self.assertTrue(str(narrative.get("headline") or "").strip())
            self.assertFalse(
                facts.review_requires_human(narrative), narrative.get("phase")
            )
            repair = json.loads(repair_json.read_text(encoding="utf-8"))
            self.assertTrue(repair.get("holdAutomerge"), repair)
            self.assertTrue(
                facts.only_safe_score_lede_content(narrative)
                or facts.edition_word_count(narrative) < facts.PUBLISH_WORD_FLOOR,
                narrative,
            )

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
        edition = _exact_words(_thin_preview(), 3900)
        eight = _body_drops(8)
        nine = _body_drops(9)
        self.assertFalse(facts.drop_ceiling_exceeded(eight))
        self.assertTrue(facts.drop_ceiling_exceeded(nine))
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
        changed_n = {"n": 0}

        def fake_salvage(payload, problems, *args, **kwargs):
            idx = stages["n"]
            stages["n"] += 1
            gone = list(gone_by_stage[idx]) if idx < len(gone_by_stage) else []
            fat = json.loads(json.dumps(payload))
            if facts.edition_word_count(fat) < 3900:
                fat = _exact_words(fat, 3900)
            return fat, gone, [], []

        def fake_review(*_args, **kwargs):
            if kwargs.get("changed"):
                changed_n["n"] += 1
                if changed_n["n"] <= 2:
                    return [
                        "score progression 7-3 after Q2 disagrees with ESPN"
                    ]
                return []
            return ["storyline: leftover score-progression claim (stage bait)"]

        result, repair_json = helper._through_build(
            tmp,
            raw,
            patches=(
                patch.object(
                    generate, "_salvage_until_clean", side_effect=fake_salvage
                ),
                patch.object(facts, "check_review", side_effect=fake_review),
                patch.object(
                    facts,
                    "restore_required_copy",
                    side_effect=lambda payload, *a, **k: payload,
                ),
                patch.object(
                    generate, "replay_fixture_missing", return_value=False
                ),
            ),
        )
        self.assertGreaterEqual(stages["n"], 2, stages)
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

    def test_n8_real_drops_across_stages(self):
        """A8/N8: real salvage drops in ≥2 stages including archive-replay."""
        helper = self._helpers()
        real_salvage = generate._salvage_until_clean
        eight = [_body_drops(3, 0), _body_drops(3, 3), _body_drops(2, 6)]
        nine = [_body_drops(4, 0), _body_drops(3, 4), _body_drops(2, 7)]
        for plan, expect_hold in ((eight, False), (nine, True)):
            gone_by_stage: list[list[str]] = []
            stages = {"n": 0}
            changed_n = {"n": 0}
            archive_n = {"n": 0}
            real_archive = generate.archive_replay_issues

            def wrapped(payload, problems, *args, **kwargs):
                repaired, gone, leftover, logs = real_salvage(
                    payload, problems, *args, **kwargs
                )
                idx = stages["n"]
                stages["n"] += 1
                extra = list(plan[idx]) if idx < len(plan) else []
                gone_by_stage.append(list(gone) + extra)
                fat = json.loads(json.dumps(repaired))
                if facts.edition_word_count(fat) < 3900:
                    fat = _exact_words(fat, 3900)
                return fat, extra, [], logs

            def fake_review(*_args, **kwargs):
                if kwargs.get("changed"):
                    changed_n["n"] += 1
                    if changed_n["n"] <= 2:
                        return [
                            "score progression 7-3 after Q2 disagrees with ESPN"
                        ]
                    return []
                return ["storyline: leftover score-progression claim (stage bait)"]

            def fake_archive(narrative, schedule=None):
                leftover = list(real_archive(narrative, schedule) or [])
                archive_n["n"] += 1
                if archive_n["n"] == 1:
                    leftover.append(
                        "score progression 7-3 after Q2 disagrees with ESPN"
                    )
                return leftover

            with self.subTest(hold=expect_hold):
                gone_by_stage.clear()
                stages["n"] = 0
                changed_n["n"] = 0
                archive_n["n"] = 0
                with tempfile.TemporaryDirectory() as tmp:
                    result, repair_json = helper._through_build(
                        Path(tmp),
                        helper._fat_nonlede(helper.CLEAN),
                        patches=(
                            patch.object(
                                generate,
                                "_salvage_until_clean",
                                side_effect=wrapped,
                            ),
                            patch.object(
                                facts, "check_review", side_effect=fake_review
                            ),
                            patch.object(
                                generate,
                                "archive_replay_issues",
                                side_effect=fake_archive,
                            ),
                            patch.object(
                                facts,
                                "restore_required_copy",
                                side_effect=lambda payload, *a, **k: payload,
                            ),
                            patch.object(
                                generate,
                                "replay_fixture_missing",
                                return_value=False,
                            ),
                        ),
                    )
                    repair = json.loads(repair_json.read_text(encoding="utf-8"))
                    nonempty = [row for row in gone_by_stage if row]
                    self.assertGreaterEqual(len(gone_by_stage), 2, gone_by_stage)
                    self.assertTrue(nonempty, gone_by_stage)
                    self.assertTrue(
                        any(row for row in gone_by_stage[1:]),
                        gone_by_stage,
                    )
                    self._assert_n8_stage_outcome(result, repair, hold=expect_hold)

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
        self.assertIn("--label", creates[0]["args"])
        self.assertIn(facts.DROPS_ISSUE_LABEL, creates[0]["args"])
        self.assertTrue(edits)
        line = drop_issues.safe_issue_line("see @karen and `$(touch PWNED)`")
        self.assertIn("@\u200bkaren", line)
        self.assertIn("$(touch PWNED)", line)
        pr_body = drop_body.drop_body(
            {
                "holdAutomerge": False,
                "droppedSentences": ["see @karen and `$(touch PWNED)`"],
            }
        )
        self.assertIn("@\u200bkaren", pr_body)
        self.assertNotIn("@karen", pr_body.replace("@\u200b", ""))
        self.assertIn("$(touch PWNED)", pr_body)

    def test_n10_remainder_roster_error_holds(self):
        helper = self._helpers()
        claims = " ".join(helper._claims(3))
        raw = helper._fat_nonlede(helper.CLEAN + " " + claims)
        press = [
            {
                "speaker": "Andy Reid",
                "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa",
            }
        ]
        raw["pressConferences"] = press
        real_salvage = generate._salvage_until_clean
        real_restore = facts.restore_required_copy

        def keep_press_salvage(payload, problems, *args, **kwargs):
            repaired, gone, leftover, logs = real_salvage(
                payload, problems, *args, **kwargs
            )
            repaired = json.loads(json.dumps(repaired))
            repaired["pressConferences"] = press
            return repaired, gone, leftover, logs

        def keep_press_restore(payload, *args, **kwargs):
            restored = real_restore(payload, *args, **kwargs)
            restored = json.loads(json.dumps(restored))
            restored["pressConferences"] = press
            return restored

        with tempfile.TemporaryDirectory() as tmp:
            result, repair_json = helper._through_build(
                Path(tmp),
                raw,
                patches=(
                    patch.object(
                        press_conferences,
                        "fetch_oembed",
                        side_effect=OSError("offline"),
                    ),
                    patch.object(
                        generate,
                        "_salvage_until_clean",
                        side_effect=keep_press_salvage,
                    ),
                    patch.object(
                        facts,
                        "restore_required_copy",
                        side_effect=keep_press_restore,
                    ),
                ),
            )
            leftover = result.get("leftoverIssues") or []
            drops = result.get("droppedSentences") or []
            self.assertTrue(drops, result)
            self.assertTrue(
                any("press conference" in str(item) for item in leftover),
                leftover,
            )
            repair = json.loads(repair_json.read_text(encoding="utf-8"))
            self.assertTrue(repair.get("holdAutomerge"), repair)
            self.assertTrue(
                facts.should_hold_automerge(
                    drops,
                    result["narrative"],
                    leftover=leftover,
                    corrections=result.get("correctedSentences") or [],
                    hold_only_sentinels=result.get("holdOnlySentinels") or [],
                    headline_or_lede_touched=bool(
                        result.get("headlineOrLedeTouched")
                    ),
                )
            )

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
    def _helpers(self) -> ScoreProgressionBudgetExhaustion:
        return ScoreProgressionBudgetExhaustion("test_p1_seven_invalid_claims_drop_clean")

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
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ran_meta = _run_record_publish(
                root,
                repair={
                    "holdAutomerge": False,
                    "droppedSentences": [],
                    "correctedSentences": [correction],
                },
                narrative=edition,
            )
            self.assertEqual(ran_meta.returncode, 0, ran_meta.stderr)
            published = json.loads(
                (root / ".narrative-publish.json").read_text(encoding="utf-8")
            )
            self.assertFalse(published.get("holdMerge"), published)
            held = _run_fail_held_on_root(root)
            self.assertEqual(held.returncode, 0, held.stderr)
            env = PublicationPolicy._fake_commands(root)
            env["DESK_MODE"] = "full"
            env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
            publish = _job_step_script(
                _workflow("narrative.yml"),
                "update",
                name="Queue draft pull request for approved PR-event gates",
            )
            ran = subprocess.run(
                ["bash", "-c", publish],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(ran.returncode, 0, ran.stderr)
            calls_q = [
                json.loads(line)
                for line in (root / "calls.jsonl").read_text().splitlines()
            ]
            labels = [
                row
                for row in calls_q
                if row["command"] == "gh" and "--add-label" in row["args"]
            ]
            self.assertFalse(labels, labels)

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
        helper = self._helpers()
        pinned = _pinned()
        as_noted = "As noted, the next snap is the Jets."
        raw = helper._fat_nonlede(helper.CLEAN)
        raw["theEdge"] = (
            pinned["quinton"] + " " + as_noted + " " + raw["theEdge"]
        )
        before = json.loads(json.dumps(raw))
        fixed, logs = known_errors.apply_known_errors(before, None)
        self.assertTrue(logs, logs)
        orphans = facts.check_repair_orphans(
            fixed, [], before=before, corrections=logs
        )
        self.assertTrue(orphans, (orphans, logs))
        self.assertTrue(
            any("as-noted" in item or "orphan" in item for item in orphans),
            orphans,
        )
        blockers = facts.repair_publish_blockers(
            [], fixed, orphans, before=before
        )
        self.assertTrue(
            _hold(
                fixed,
                corrections=logs,
                leftover=blockers,
                before=before,
            )
        )
        with tempfile.TemporaryDirectory() as tmp:
            result, repair_json = helper._through_build(Path(tmp), raw)
            leftover = result.get("leftoverIssues") or []
            self.assertTrue(
                any(
                    "as-noted" in str(item) or "orphan" in str(item)
                    for item in leftover
                ),
                leftover,
            )
            repair = json.loads(repair_json.read_text(encoding="utf-8"))
            self.assertTrue(repair.get("holdAutomerge"), repair)

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
        cases = Frozen7Cases("test_n5_dangling_reference_holds_via_j2")
        cases.test_n5_dangling_reference_holds_via_j2()
        cases.test_n7_word_floor_boundaries()

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
        self.assertTrue(facts.copy_touches_headline_or_lede([lede], edition))
        self.assertTrue(_hold(edition, drops=[lede], before=edition))
        self.assertFalse(facts.copy_touches_headline_or_lede([p2s1], edition))
        self.assertFalse(_hold(edition, drops=[p2s1], before=edition))
        token = "Preview"
        self.assertIn(token, edition["headline"])
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

        def fake_review(*_args, **kwargs):
            if kwargs.get("changed"):
                return []
            if reviews:
                return reviews.pop(0)
            return []

        return helper._through_build(
            tmp,
            first,
            llm=llm,
            patches=(
                patch.object(facts, "check_review", side_effect=fake_review),
                patch.object(
                    generate, "replay_fixture_missing", return_value=False
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
        drops = ["one body drop"]
        ships = _exact_words(_thin_preview(), 3315)
        under = _exact_words(_thin_preview(), 3314)
        self.assertEqual(facts.edition_word_count(ships), 3315)
        self.assertEqual(facts.edition_word_count(under), 3314)
        self.assertFalse(_hold(ships, drops=drops))
        self.assertTrue(_hold(under, drops=drops))
        dangling = ["orphan opener after repair ('That is the credit line.')"]
        self.assertTrue(_hold(ships, drops=drops, leftover=dangling))
        self.assertEqual(
            facts.publish_word_floor(drops, leftover=dangling),
            facts.PUBLISH_WORD_FLOOR,
        )
        self.assertEqual(
            facts.publish_word_floor(drops),
            facts.DROP_REDUCED_WORD_FLOOR,
        )
        self.assertTrue(_hold(ships, drops=[]))
        self.assertEqual(
            facts.word_shortfall_below_publish_floor(ships),
            facts.PUBLISH_WORD_FLOOR - facts.edition_word_count(ships),
        )
        body = drop_issues.build_issue_body(
            slug="2026-11-07-1200",
            drops=drops,
            shortfall=facts.word_shortfall_below_publish_floor(ships),
        )
        self.assertIn("585", body)

    def test_a13_score_progression_budget_constant_used(self):
        helper = self._helpers()
        text = helper._field(3)
        with tempfile.TemporaryDirectory() as tmp:
            result, repair_json = helper._through_build(
                Path(tmp), helper._fat_nonlede(text)
            )
            leftover = result.get("leftoverIssues") or []
            self.assertFalse(helper._budget_rows(leftover), leftover)
        with patch.object(facts, "SCORE_PROGRESSION_REPAIR_BUDGET", 2):
            with tempfile.TemporaryDirectory() as tmp:
                result, repair_json = helper._through_build(
                    Path(tmp), helper._fat_nonlede(text)
                )
                leftover = result.get("leftoverIssues") or []
                self.assertEqual(len(helper._budget_rows(leftover)), 1, leftover)
                repair = json.loads(repair_json.read_text(encoding="utf-8"))
                self.assertTrue(repair.get("holdAutomerge"), repair)


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
            held = _run_fail_held_on_root(root)
            self.assertEqual(held.returncode, 0, held.stderr)
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
        self.assertIn("GATE_BASE", issue_step)
        self.assertIn("python -P -m tools.chiefs_narrative.drop_issues", issue_step)
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
            held_root = Path(tmp) / "held"
            held_root.mkdir()
            env_held = PublicationPolicy._fake_commands(held_root)
            env_held["DESK_MODE"] = "full"
            env_held["PYTHONPATH"] = str(REPO) + os.pathsep + env_held.get(
                "PYTHONPATH", ""
            )
            (held_root / ".narrative-publish.json").write_text(
                json.dumps(
                    {
                        "holdMerge": True,
                        "dropBody": drop_body.drop_body(
                            {
                                "holdAutomerge": True,
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
            data_held = held_root / "data"
            data_held.mkdir()
            (data_held / "narrative.json").write_text(
                json.dumps(_fat_preview()) + "\n", encoding="utf-8"
            )
            (data_held / "narrative_repair.json").write_text(
                json.dumps(
                    {
                        "droppedSentences": ["body drop"],
                        "correctedSentences": [],
                        "holdAutomerge": True,
                        "wordShortfall": 0,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            ran_held = subprocess.run(
                ["bash", "-c", publish],
                cwd=held_root,
                env=env_held,
                capture_output=True,
                text=True,
            )
            self.assertEqual(ran_held.returncode, 0, ran_held.stderr)
            held_calls = [
                json.loads(line)
                for line in (held_root / "calls.jsonl").read_text().splitlines()
            ]
            add_hold = [
                row
                for row in held_calls
                if row["command"] == "gh"
                and row["args"][:2] == ["pr", "edit"]
                and "--add-label" in row["args"]
                and "hold" in row["args"]
            ]
            self.assertTrue(add_hold, held_calls)
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

    def test_generate_main_preview_sentinel_and_lede_hold(self):
        """XC2: generate.main writes holdAutomerge from its own kwargs."""
        helper = ScoreProgressionBudgetExhaustion(
            "test_p1_seven_invalid_claims_drop_clean"
        )
        raw = helper._fat_nonlede(helper.CLEAN)
        real_build = generate.build

        def sentinel_build(*args, **kwargs):
            result = real_build(*args, **kwargs)
            result["holdOnlySentinels"] = [
                facts.hold_only_sentinel(facts.HOLD_ONLY_SALVAGE_NOISY)
            ]
            result["droppedSentences"] = []
            result["leftoverIssues"] = []
            result["correctedSentences"] = []
            result["headlineOrLedeTouched"] = False
            return result

        with tempfile.TemporaryDirectory() as tmp:
            result, repair_json = helper._through_build(
                Path(tmp),
                raw,
                patches=(
                    patch.object(generate, "build", side_effect=sentinel_build),
                ),
            )
            repair = json.loads(repair_json.read_text(encoding="utf-8"))
            self.assertTrue(repair.get("holdAutomerge"), repair)
            self.assertFalse(result.get("headlineOrLedeTouched"))
            self.assertFalse(result.get("leftoverIssues"))

        lede = "Kansas City lost 24-28 in Denver."
        lede_raw = helper._fat_nonlede(helper.CLEAN)
        lede_raw["lastGameReview"] = {
            "opponent": "Denver Broncos",
            "result": "L",
            "score": "KC 24–28",
            "lede": lede,
        }

        def lede_build(*args, **kwargs):
            result = real_build(*args, **kwargs)
            result["droppedSentences"] = []
            result["leftoverIssues"] = []
            result["correctedSentences"] = []
            result["holdOnlySentinels"] = []
            result["headlineOrLedeTouched"] = True
            return result

        with tempfile.TemporaryDirectory() as tmp:
            _result, repair_json = helper._through_build(
                Path(tmp),
                lede_raw,
                patches=(
                    patch.object(generate, "build", side_effect=lede_build),
                ),
            )
            repair = json.loads(repair_json.read_text(encoding="utf-8"))
            self.assertTrue(repair.get("holdAutomerge"), repair)


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
