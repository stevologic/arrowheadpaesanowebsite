"""Named mutants for the edition meta-note gate and publish refuse path."""
from __future__ import annotations

import importlib
import importlib.util
import os
import subprocess
import sys
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from tools.chiefs_narrative import edition_merge, facts, generate


ROOT = Path(__file__).resolve().parents[2]
FACTS = ROOT / "tools" / "chiefs_narrative" / "facts.py"
GENERATE = ROOT / "tools" / "chiefs_narrative" / "generate.py"
MERGE = ROOT / "tools" / "chiefs_narrative" / "edition_merge.py"
WORKFLOW = ROOT / ".github" / "workflows" / "narrative.yml"


def _meta(line, **kwargs):
    payload = {"lastGameReview": {"lede": "Kansas City won 24-10. " + line}}
    payload.update(kwargs)
    return [
        item
        for item in facts.check_review(payload, None, None)
        if "fact-checker note leaked" in item
    ]


def _bust_module_cache(path: Path, module):
    """Same-second rewrites keep a stale .pyc; drop it and bump mtime."""
    cache = Path(importlib.util.cache_from_source(str(path)))
    if cache.is_file():
        cache.unlink()
    importlib.invalidate_caches()
    path.touch()
    os.utime(path, (time.time() + 2, time.time() + 2))
    importlib.reload(module)


@contextmanager
def _mutated(path: Path, old: str, new: str, module):
    original = path.read_text(encoding="utf-8")
    if old not in original:
        raise AssertionError(f"mutant needle missing in {path.name}")
    path.write_text(original.replace(old, new, 1), encoding="utf-8")
    try:
        _bust_module_cache(path, module)
        yield
    finally:
        path.write_text(original, encoding="utf-8")
        _bust_module_cache(path, module)


def _kill_phrase(snippet: str, line: str):
    def check():
        importlib.reload(facts)
        hits = [
            item
            for item in facts.check_review(
                {"lastGameReview": {"lede": "Kansas City won 24-10. " + line}},
                None,
                None,
            )
            if "fact-checker note leaked" in item
        ]
        if hits:
            return f"{snippet!r} still flags {line!r} (overlapping pattern)"
        return None

    return check


def _kill_string_compare():
    leak = {
        "generatedAt": "2026-10-29T18:54:30+01:00",
        "lastGameReview": {
            "lede": "Kansas City won 24-10. That is a recovered snap, not a lost fumble."
        },
    }
    issues = facts.check_review(leak, None, None, changed=False)
    if any("fact-checker note leaked" in item for item in issues):
        return None
    return "string compare still grandfathered the offset stamp"


def _kill_unknown_changed():
    leak = {
        "generatedAt": "2026-10-27T19:15:53Z",
        "lastGameReview": {
            "lede": "Kansas City won 24-10. That is a recovered snap, not a lost fumble."
        },
    }
    issues = facts.check_review(leak, None, None, changed=None)
    if any("fact-checker note leaked" in item for item in issues):
        return "unknown changed state still scanned"
    return None


def _kill_git_none():
    with patch.object(facts, "_diff_base", return_value=None):
        paths = facts.changed_edition_paths(base="")
    if paths is None:
        return "missing develop still returned None"
    return None


def _kill_git_failed_diffs():
    with patch.object(facts, "_diff_base", return_value="origin/develop"):
        with patch.object(facts, "_git_name_only", return_value=None):
            paths = facts.changed_edition_paths(base="")
    if paths is None:
        return "failed develop diff still returned None"
    return None


def _kill_generate_unknown():
    captured = []

    def fake_check(*args, changed=None, **kwargs):
        captured.append(changed)
        return []

    with patch.object(facts, "changed_edition_paths", return_value=None):
        with patch.object(facts, "check_review", side_effect=fake_check):
            with patch.object(facts, "check_diagram_captions", return_value=[]):
                with patch.object(
                    generate.press_conferences, "check_embeds", return_value=[]
                ):
                    with patch.object(
                        generate.locker_room, "check_posts", return_value=[]
                    ):
                        with patch.object(
                            generate.hero_posts, "validate_sidecar", return_value=[]
                        ):
                            generate.check_published_edition()
    if captured == [True]:
        return "unknown git state still scanned as True"
    return None


def _kill_generate_replay():
    captured = []

    def fake_check(*args, changed=None, **kwargs):
        captured.append(changed)
        return []

    with patch.object(generate, "_last_game_from_edition", return_value=None):
        with patch.object(generate, "_recap_for_edition", return_value={}):
            with patch.object(facts, "check_review", side_effect=fake_check):
                generate.archive_replay_issues({"slug": "x"})
    if captured == [True]:
        return "archive_replay_issues still forced changed=True"
    return None


def _kill_head_approval():
    pr = {
        "number": 12,
        "labels": [],
        "reviews": [{"state": "APPROVED", "commit": "old"}],
        "reviewDecision": "APPROVED",
        "headRefOid": "new",
    }
    with patch.object(edition_merge, "fetch_pr", return_value=pr):
        with patch.object(edition_merge, "fetch_review_threads", return_value=[]):
            result = edition_merge.inspect_pr("12")
    if result.get("refused") and result.get("reason") == "no approval at current head":
        return "inspect_pr still required head approval"
    return None


def _kill_missing_number():
    try:
        with patch.object(edition_merge, "fetch_pr", return_value={"labels": []}):
            edition_merge.inspect_pr("12")
    except edition_merge.MergeLookupError:
        return "missing PR number still failed closed"
    return None


def _kill_missing_cursor():
    page = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "nodes": [{"isResolved": True}],
                        "pageInfo": {"hasNextPage": True, "endCursor": ""},
                    }
                }
            }
        }
    }
    try:
        with patch.dict("os.environ", {"GITHUB_REPOSITORY": "org/repo"}):
            with patch.object(edition_merge, "_gh_json", return_value=page):
                edition_merge.fetch_review_threads(12)
    except edition_merge.MergeLookupError:
        return "missing page cursor still failed closed"
    return None


def _kill_gh_empty():
    try:
        with patch.object(edition_merge.subprocess, "check_output", return_value=""):
            edition_merge._gh_json(["pr", "view", "1"])
    except edition_merge.MergeLookupError:
        return "empty gh JSON still failed closed"
    return None


def _kill_gh_bad_json():
    try:
        with patch.object(
            edition_merge.subprocess, "check_output", return_value="not-json"
        ):
            edition_merge._gh_json(["pr", "view", "1"])
    except edition_merge.MergeLookupError:
        return "bad gh JSON still failed closed"
    return None


def _kill_main_lookup():
    # Fresh interpreter so earlier in-process reloads cannot mask the flip.
    script = (
        "from unittest.mock import patch\n"
        "from tools.chiefs_narrative import edition_merge\n"
        "def boom(*_a, **_k):\n"
        "    raise edition_merge.MergeLookupError('boom')\n"
        "with patch.object(edition_merge, 'inspect_pr', side_effect=boom):\n"
        "    raise SystemExit(edition_merge.main(['--pr', '1']))\n"
    )
    rc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(ROOT),
        check=False,
    ).returncode
    if rc == 0:
        return None
    return f"main() returned {rc} after lookup-error mutant"


def _promote_script(source: str) -> str:
    start = source.find("name: Promote develop to main")
    return source[start:] if start >= 0 else source


def _kill_first_promote_exit(source: str):
    first = _promote_script(source).split("gh pr create", 1)[0]
    if "exit 0" in first:
        return None
    return "first promote refusal still exits 1"


def _kill_orphan_close_existing(source: str):
    first = _promote_script(source).split("gh pr create", 1)[0]
    if "gh pr close" not in first:
        return None
    return "first promote refusal still closes EXISTING"


def _kill_orphan_close_url(source: str):
    if 'gh pr close "$PROMOTE_URL"' not in source:
        return None
    return "second promote refusal still closes $PROMOTE_URL"


MUTANTS = (
    {
        "name": "phrase-supplied-data",
        "path": FACTS,
        "module": "facts",
        "old": '    r"|\\bsupplied data\\b"\n',
        "new": "",
        "killer": "Only the supplied data is in scope.",
        "kind": "reload",
        "check": _kill_phrase("supplied data", "Only the supplied data is in scope."),
    },
    {
        "name": "phrase-source-packet",
        "path": FACTS,
        "module": "facts",
        "old": '    r"|\\bsource packet\\b"\n',
        "new": "",
        "killer": "The source packet is silent on inactives.",
        "kind": "reload",
        "check": _kill_phrase(
            "source packet", "The source packet is silent on inactives."
        ),
    },
    {
        "name": "phrase-per-the-supplied",
        "path": FACTS,
        "module": "facts",
        "old": '    r"|\\bper the supplied\\b"\n',
        "new": "",
        "killer": "Cut the extra names per the supplied note.",
        "kind": "reload",
        "check": _kill_phrase(
            "per the supplied", "Cut the extra names per the supplied note."
        ),
    },
    {
        "name": "phrase-this-wire",
        "path": FACTS,
        "module": "facts",
        "old": '    r"|\\bthis wire(?!-to-wire)\\b"\n',
        "new": "",
        "killer": "The look is untested on this wire.",
        "kind": "reload",
        "check": _kill_phrase("this wire", "The look is untested on this wire."),
    },
    {
        "name": "string-compare",
        "path": FACTS,
        "module": "facts",
        "old": "    if changed is False and stamp < _FACT_CHECKER_NOTE_BEFORE:\n",
        "new": (
            '    if changed is False and str((narrative or {}).get("generatedAt") or "")'
            ' < "2026-10-29T17:54:31+00:00":\n'
        ),
        "killer": "parsed datetimes, not string compares",
        "kind": "reload",
        "check": _kill_string_compare,
    },
    {
        "name": "unknown-changed-grandfathers",
        "path": FACTS,
        "module": "facts",
        "old": "    if changed is False and stamp < _FACT_CHECKER_NOTE_BEFORE:\n",
        "new": "    if changed is not True and stamp < _FACT_CHECKER_NOTE_BEFORE:\n",
        "killer": "unknown changed state must still scan",
        "kind": "reload",
        "check": _kill_unknown_changed,
    },
    {
        "name": "git-missing-base-empty",
        "path": FACTS,
        "module": "facts",
        "old": "    if not resolved:\n        return None\n",
        "new": "    if not resolved:\n        return set()\n",
        "killer": "missing develop must return None",
        "kind": "reload",
        "check": _kill_git_none,
    },
    {
        "name": "git-failed-diff-empty",
        "path": FACTS,
        "module": "facts",
        "old": "    if triple is None and two_dot is None:\n        return None\n",
        "new": "    if triple is None and two_dot is None:\n        return set()\n",
        "killer": "failed develop diffs must return None",
        "kind": "reload",
        "check": _kill_git_failed_diffs,
    },
    {
        "name": "generate-unknown-skip",
        "path": GENERATE,
        "module": "generate",
        "old": "    if paths is None:\n        changed = True\n",
        "new": "    if paths is None:\n        changed = False\n",
        "killer": "check_published_edition scans when git is unknown",
        "kind": "reload",
        "check": _kill_generate_unknown,
    },
    {
        "name": "generate-replay-skip",
        "path": GENERATE,
        "module": "generate",
        "old": "        narrative, last, recap, schedule=schedule, changed=True\n",
        "new": "        narrative, last, recap, schedule=schedule, changed=False\n",
        "killer": "archive_replay_issues always rechecks",
        "kind": "reload",
        "check": _kill_generate_replay,
    },
    {
        "name": "head-approval-off",
        "path": MERGE,
        "module": "edition_merge",
        "old": "REQUIRE_HEAD_APPROVAL = True\n",
        "new": "REQUIRE_HEAD_APPROVAL = False\n",
        "killer": "inspect_pr requires approval at the current head",
        "kind": "reload",
        "check": _kill_head_approval,
    },
    {
        "name": "missing-pr-number",
        "path": MERGE,
        "module": "edition_merge",
        "old": (
            "    if number is None:\n"
            '        raise MergeLookupError("gh pr view omitted number")\n'
            "    threads = fetch_review_threads(int(number))\n"
        ),
        "new": (
            "    threads = fetch_review_threads(int(number)) if number is not None else []\n"
        ),
        "killer": "missing PR number fails closed",
        "kind": "reload",
        "check": _kill_missing_number,
    },
    {
        "name": "missing-page-cursor",
        "path": MERGE,
        "module": "edition_merge",
        "old": (
            "        if not cursor:\n"
            '            raise MergeLookupError("review thread page missing endCursor")\n'
        ),
        "new": "        if not cursor:\n            return rows\n",
        "killer": "missing page cursor fails closed",
        "kind": "reload",
        "check": _kill_missing_cursor,
    },
    {
        "name": "gh-empty-object",
        "path": MERGE,
        "module": "edition_merge",
        "old": (
            "    if not text:\n"
            '        raise MergeLookupError("edition merge lookup returned no JSON")\n'
        ),
        "new": "    if not text:\n        return {}\n",
        "killer": "empty gh JSON fails closed",
        "kind": "reload",
        "check": _kill_gh_empty,
    },
    {
        "name": "gh-bad-json-object",
        "path": MERGE,
        "module": "edition_merge",
        "old": (
            "    except json.JSONDecodeError as exc:\n"
            '        raise MergeLookupError("edition merge lookup returned invalid JSON") from exc\n'
        ),
        "new": "    except json.JSONDecodeError:\n        return {}\n",
        "killer": "invalid gh JSON fails closed",
        "kind": "reload",
        "check": _kill_gh_bad_json,
    },
    {
        "name": "main-lookup-ok",
        "path": MERGE,
        "module": "edition_merge",
        "old": (
            "    except MergeLookupError as exc:\n"
            '        print(f"ERROR: {exc}", file=sys.stderr)\n'
            "        return 1\n"
        ),
        "new": (
            "    except MergeLookupError as exc:\n"
            '        print(f"ERROR: {exc}", file=sys.stderr)\n'
            "        return 0\n"
        ),
        "killer": "main() returns 1 on MergeLookupError",
        "kind": "reload",
        "check": _kill_main_lookup,
    },
    {
        "name": "first-promote-exit-0",
        "path": WORKFLOW,
        "module": None,
        "old": (
            '            if [ -n "$EXISTING" ]; then\n'
            '              gh pr close "$EXISTING" --comment "Refusing promote: hold or unresolved review findings"\n'
            "            fi\n"
            "            exit 1\n"
        ),
        "new": (
            '            if [ -n "$EXISTING" ]; then\n'
            '              gh pr close "$EXISTING" --comment "Refusing promote: hold or unresolved review findings"\n'
            "            fi\n"
            "            exit 0\n"
        ),
        "killer": "first promote refusal exits 1",
        "kind": "source",
        "check": _kill_first_promote_exit,
    },
    {
        "name": "orphan-close-existing",
        "path": WORKFLOW,
        "module": None,
        "old": (
            '            if [ -n "$EXISTING" ]; then\n'
            '              gh pr close "$EXISTING" --comment "Refusing promote: hold or unresolved review findings"\n'
            "            fi\n"
        ),
        "new": "",
        "killer": "first refuse closes this edition's promote PR",
        "kind": "source",
        "check": _kill_orphan_close_existing,
    },
    {
        "name": "orphan-close-url",
        "path": WORKFLOW,
        "module": None,
        "old": (
            '            gh pr close "$PROMOTE_URL" --comment "Refusing promote: hold or unresolved review findings"\n'
        ),
        "new": "",
        "killer": "second refuse closes $PROMOTE_URL",
        "kind": "source",
        "check": _kill_orphan_close_url,
    },
    {
        "name": "refuse-exit-green",
        "path": WORKFLOW,
        "module": None,
        "old": "            gh pr merge \"${{ github.event.pull_request.html_url }}\" --disable-auto || true\n            exit 1\n",
        "new": "            gh pr merge \"${{ github.event.pull_request.html_url }}\" --disable-auto || true\n            exit 0\n",
        "killer": "publish refuse exits 1",
        "kind": "source",
        "check": lambda src: (
            None
            if "exit 0" in src.split("publish:")[1].split("Squash-merge")[0]
            else "refuse step still exits 1"
        ),
    },
    {
        "name": "merge-if-dropped",
        "path": WORKFLOW,
        "module": None,
        "old": "        if: steps.refuse.outputs.refused != 'true'\n        env:\n          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}\n        run: |\n          set -euo pipefail\n          gh pr merge \"${{ github.event.pull_request.html_url }}\" \\",
        "new": "        env:\n          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}\n        run: |\n          set -euo pipefail\n          gh pr merge \"${{ github.event.pull_request.html_url }}\" \\",
        "killer": "squash-merge stays behind the refuse output",
        "kind": "source",
        "check": lambda src: (
            None
            if "steps.refuse.outputs.refused != 'true'"
            not in src.split("Squash-merge the edition pull request")[1].split(
                "Promote develop to main"
            )[0]
            else "merge if: guard still present"
        ),
    },
)

MODULES = {
    "facts": facts,
    "generate": generate,
    "edition_merge": edition_merge,
}


class EditionGuardMutations(unittest.TestCase):
    def test_named_mutants_are_killed(self):
        rows = []
        survivors = []
        for mutant in MUTANTS:
            source = mutant["path"].read_text(encoding="utf-8")
            self.assertIn(mutant["old"], source, mutant["name"])
            mutated = source.replace(mutant["old"], mutant["new"], 1)
            self.assertNotEqual(mutated, source, mutant["name"])
            reason = None
            if mutant["kind"] == "source":
                reason = mutant["check"](mutated)
            else:
                module = MODULES[mutant["module"]]
                try:
                    with _mutated(mutant["path"], mutant["old"], mutant["new"], module):
                        reason = mutant["check"]()
                except AssertionError:
                    raise
                except Exception:  # noqa: BLE001 — mutant crash means the test detected it
                    reason = None
            killed = reason is None
            rows.append(
                {
                    "name": mutant["name"],
                    "killer": mutant["killer"],
                    "killed": killed,
                    "detail": "killed" if killed else reason,
                }
            )
            if not killed:
                survivors.append(f"{mutant['name']}: {reason}")
        table = [
            f"{row['name']}: {'killed' if row['killed'] else 'SURVIVED — ' + row['detail']}"
            for row in rows
        ]
        self.assertEqual(
            survivors,
            [],
            "Survivors:\n" + "\n".join(survivors) + "\n\nKill table:\n" + "\n".join(table),
        )
        print("\n".join(table))
