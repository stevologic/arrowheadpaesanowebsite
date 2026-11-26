"""FROZEN-38 r3 behavioural rows (Kenny 2:32 PM / Karen 2:35 PM MST)."""
from __future__ import annotations

import copy
import json
import os
import re
import tempfile
import unittest
from pathlib import Path

from tools.chiefs_narrative.review_gate import (
    COMPARE_FILE_CAP,
    COMMIT_PAGE_CAP,
    git_raw_changed,
    merger_identity,
    provenance_diff_paths,
)
from tools.tests.frozen38_harness import (
    SLUG,
    bash_c,
    build_promo,
    dumps,
    edition_files,
    extracted_step_script,
    git,
    preview_payload,
    repair_bytes,
    run_hold_override,
    start_github_api,
    write_gh_stub,
)
from tools.tests.test_frozen38_r2 import _bot_pr, _human_pr, _karen

FIX = Path(__file__).resolve().parent / "fixtures" / "frozen38_r3"
REPO_NAME = "stevologic/arrowheadpaesanowebsite"
_MERGED_BY = re.compile(r"merged_by")
_COMMITS_PULLS_NAME = re.compile(r"^commits_.*_pulls\.json$")


def commits_pulls_merged_by_hits(root: Path) -> list[str]:
    """F1f: same pattern and commits/.../pulls-shape paths as `rg merged_by`."""
    hits: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if not _COMMITS_PULLS_NAME.fullmatch(name):
                continue
            path = Path(dirpath) / name
            text = path.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), 1):
                if _MERGED_BY.search(line):
                    hits.append(f"{path}:{lineno}:{line}")
    return hits


def _load(name: str):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def _keys(obj) -> set[str]:
    found: set[str] = set()

    def walk(value):
        if isinstance(value, dict):
            found.update(value.keys())
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(obj)
    return found


class Frozen38R3(unittest.TestCase):
    def _full(self, tmp, **kwargs):
        files = edition_files(SLUG, preview_payload(), **kwargs)
        return build_promo(Path(tmp), files)

    def _refuse(self, result, needle):
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(needle, result.stderr, result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def _pass(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def _env(self, promo, routes, **server_kw):
        server, root = start_github_api(routes, **server_kw)
        env = {"FROZEN38_GITHUB_API_ROOT": root}
        body = f"Edition-only promote of #417 (develop squash {promo.develop_sha[:8]}, Karen r3)."
        return server, env, body

    def _hold(self, promo, routes, *, base="", **server_kw):
        server, env, body = self._env(promo, routes, **server_kw)
        try:
            return run_hold_override(promo, pr_body=body, env=env, base=base)
        finally:
            server.shutdown()
            server.server_close()

    def _python_on_path(self, tmp_path: Path) -> None:
        target = tmp_path / "python"
        if not target.exists():
            target.symlink_to(os.environ.get("PYTHON", "/usr/bin/python3"))
        py3 = tmp_path / "python3"
        if not py3.exists():
            py3.symlink_to(os.environ.get("PYTHON", "/usr/bin/python3"))

    def _bind(self, prs, sha):
        bound = copy.deepcopy(prs)
        for item in bound:
            item["merge_commit_sha"] = sha
        return bound

    def _human_routes(self, *shas, head="a" * 40, number=417):
        routes = {f"/commits/{head}/statuses": _karen("success"), "/statuses": _karen("success")}
        for sha in shas:
            pr = self._bind(_human_pr(head, number=number), sha)
            routes[f"/commits/{sha}/pulls"] = pr
            routes[f"/pulls/{number}"] = pr[0]
            routes[f"/pulls/{number}/commits"] = [{"sha": sha}]
        return routes

    def test_R3_F1a_human_merger_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._full(tmp, hold=True)
            routes = self._human_routes(promo.develop_sha)
            self._pass(self._hold(promo, routes))

    def test_R3_F1b_bot_refuses(self):
        cases = (
            {"login": "github-actions[bot]", "type": "Bot"},
            {"login": "cursor[bot]", "type": "User"},
            {"login": "github-actions[bot]", "type": "User"},
        )
        for merged_by in cases:
            with self.subTest(merged_by=merged_by):
                with tempfile.TemporaryDirectory() as tmp:
                    promo = self._full(tmp, hold=True)
                    pr = _human_pr("a" * 40)
                    pr[0]["merged_by"] = merged_by
                    routes = {
                        f"/commits/{promo.develop_sha}/pulls": pr,
                        "/pulls": pr,
                        "/statuses": _karen("success"),
                    }
                    self._refuse(self._hold(promo, routes), "bot merge")

    def test_R3_F1c_merged_by_null(self):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._full(tmp, hold=True)
            pr = _human_pr("a" * 40)
            pr[0]["merged_by"] = None
            routes = {
                f"/commits/{promo.develop_sha}/pulls": pr,
                "/pulls": pr,
                "/statuses": _karen("success"),
            }
            self._refuse(self._hold(promo, routes), "merged_by null")

    def test_R3_F1d_merged_by_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._full(tmp, hold=True)
            pr = _human_pr("a" * 40)
            pr[0].pop("merged_by", None)
            routes = {
                f"/commits/{promo.develop_sha}/pulls": pr,
                "/pulls": pr,
                "/statuses": _karen("success"),
            }
            self._refuse(self._hold(promo, routes), "merged_by missing")

    def test_R3_F1e_pulls_errors(self):
        cases = (
            (
                "404",
                {},
                {
                    "status_for": {
                        "/repos/stevologic/arrowheadpaesanowebsite/pulls/417": 404
                    }
                },
                "pulls HTTP 404",
            ),
            (
                "500",
                {},
                {
                    "status_for": {
                        "/repos/stevologic/arrowheadpaesanowebsite/pulls/417": 500
                    }
                },
                "pulls HTTP 500",
            ),
            (
                "nonjson",
                {},
                {
                    "raw_for": {"/repos/stevologic/arrowheadpaesanowebsite/pulls/417": b"not-json"},
                    "status_for": {
                        "/repos/stevologic/arrowheadpaesanowebsite/pulls/417": 200
                    },
                },
                "non-JSON",
            ),
        )
        for name, extra_routes, server_kw, needle in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    promo = self._full(tmp, hold=True)
                    pr = _human_pr("a" * 40)
                    routes = {
                        f"/commits/{promo.develop_sha}/pulls": pr,
                        **extra_routes,
                    }
                    result = self._hold(promo, routes, **server_kw)
                    self._refuse(result, needle)

    def test_R3_F1e_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._full(tmp, hold=True)
            env = {"FROZEN38_GITHUB_API_ROOT": "http://127.0.0.1:1"}
            body = f"develop squash {promo.develop_sha}"
            result = run_hold_override(promo, pr_body=body, env=env)
            self._refuse(result, "timeout")

    def test_R3_F1f_fixture_keys_and_rg(self):
        captured_831 = _load("commits_831ae92_pulls.json")
        captured_3a = _load("commits_3a220bd_pulls.json")
        disk_831 = json.loads((FIX / "commits_831ae92_pulls.json").read_text())
        disk_3a = json.loads((FIX / "commits_3a220bd_pulls.json").read_text())
        self.assertEqual(_keys(captured_831), _keys(disk_831))
        self.assertEqual(_keys(captured_3a), _keys(disk_3a))
        for item in captured_831 + captured_3a:
            self.assertNotIn("merged_by", item)
        for name in ("pulls_417.json", "pulls_423.json"):
            payload = _load(name)
            self.assertIn("merged_by", payload)
            ok, why = merger_identity(payload)
            self.assertTrue(ok, why)
        hits = commits_pulls_merged_by_hits(FIX)
        self.assertEqual(hits, [])

    def _replay_423(self, *, relabel_bot: bool):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._full(tmp, hold=True)
            pulls = self._bind(_load("commits_3a220bd_pulls.json"), promo.develop_sha)
            pr423 = copy.deepcopy(_load("pulls_423.json"))
            if relabel_bot:
                pr423["merged_by"] = {"login": "github-actions[bot]", "type": "Bot"}
            statuses = _load("commits_423head_statuses.json")
            head = str((pr423.get("head") or {}).get("sha") or "")
            routes = {
                f"/commits/{promo.develop_sha}/pulls": pulls,
                "/pulls/423": pr423,
                f"/pulls/423/commits": [{"sha": promo.develop_sha}],
                f"/commits/{head}/statuses": statuses,
            }
            return self._hold(promo, routes)

    def test_R3_F1g_relabelled_bot_refuses(self):
        self._refuse(self._replay_423(relabel_bot=True), "bot merge")

    def test_R3_F1h_unrelabelled_outcome(self):
        result = self._replay_423(relabel_bot=False)
        outcome = "pass" if result.returncode == 0 else "refuse"
        self.assertIn(outcome, {"pass", "refuse"})
        self._pass(result)
        self.assertEqual(outcome, "pass")

    def test_R3_F1j_user_without_karen_qa(self):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._full(tmp, hold=True)
            pr = _human_pr("a" * 40)
            routes = {
                f"/commits/{promo.develop_sha}/pulls": pr,
                "/pulls": pr,
                "/statuses": [],
            }
            self._refuse(self._hold(promo, routes), "karen-qa absent")

    def test_R3_N4a_open_pr_does_not_pass_bot(self):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._full(tmp, hold=True)
            open_pr = {
                "number": 422,
                "head": {"sha": "0" * 40},
                "merged_at": None,
                "base": {"ref": "develop"},
                "merged_by": {"login": "stevologic", "type": "User"},
            }
            bot = _bot_pr("a" * 40, number=425)[0]
            routes = {
                f"/commits/{promo.develop_sha}/pulls": [open_pr, bot],
                "/pulls": [open_pr, bot],
                "/statuses": _karen("success"),
            }
            self._refuse(self._hold(promo, routes), "bot merge")

    def test_R3_N4b_closed_unmerged_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._full(tmp, hold=True)
            closed = {
                "number": 400,
                "head": {"sha": "0" * 40},
                "merged_at": None,
                "state": "closed",
                "base": {"ref": "develop"},
                "merged_by": {"login": "github-actions[bot]", "type": "Bot"},
            }
            human = _human_pr("a" * 40)[0]
            routes = {
                f"/commits/{promo.develop_sha}/pulls": [closed, human],
                "/pulls": [closed, human],
                "/statuses": _karen("success"),
            }
            self._pass(self._hold(promo, routes))

    def test_R3_N4c_two_merged_develop_refuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._full(tmp, hold=True)
            routes = {
                f"/commits/{promo.develop_sha}/pulls": _human_pr("a" * 40, number=417)
                + _human_pr("b" * 40, number=418),
                "/statuses": _karen("success"),
            }
            self._refuse(self._hold(promo, routes), "multiple PRs")

    def test_R3_N4d_non_develop_base_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._full(tmp, hold=True)
            main_pr = _human_pr("a" * 40, number=425)[0]
            main_pr["base"] = {"ref": "main"}
            human = _human_pr("b" * 40, number=423)[0]
            routes = {
                f"/commits/{promo.develop_sha}/pulls": [main_pr, human],
                "/pulls": [main_pr, human],
                "/statuses": _karen("success"),
            }
            self._pass(self._hold(promo, routes))
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._full(tmp, hold=True)
            only_main = _load("pulls_425.json")
            routes = {
                f"/commits/{promo.develop_sha}/pulls": [only_main],
                "/pulls/425": only_main,
                "/statuses": _karen("success"),
            }
            self._refuse(self._hold(promo, routes), "no introducing PR")

    def _touch_develop(self, promo, rel: str, body: bytes) -> str:
        seed = promo.tmp / "seed"
        git(["checkout", "develop"], cwd=seed)
        path = seed / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        git(["add", "--", rel], cwd=seed)
        commit = git(["commit", "-m", f"touch {rel}"], cwd=seed, check=False)
        if commit.returncode != 0:
            path.write_bytes(body + b"\n")
            git(["add", "--", rel], cwd=seed)
            git(["commit", "-m", f"touch {rel}"], cwd=seed)
        git(["push", "origin", "develop"], cwd=seed)
        sha = git(["rev-parse", "HEAD"], cwd=seed).stdout.strip()
        git(["fetch", "origin", "develop"], cwd=promo.pr_tree)
        promo.develop_sha = sha
        return sha

    def _bot_on_path(self, rel: str, body: bytes):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._full(tmp, hold=False)
            intro = promo.develop_sha
            extra = self._touch_develop(promo, rel, body)
            human = self._bind(_human_pr("a" * 40, number=417), intro)
            bot = self._bind(_bot_pr("b" * 40, number=418), extra)
            routes = {
                f"/commits/{intro}/pulls": human,
                f"/commits/{extra}/pulls": bot,
                "/pulls/417": human[0],
                "/pulls/418": bot[0],
                f"/pulls/417/commits": [{"sha": intro}],
                f"/pulls/418/commits": [{"sha": extra}],
                "/statuses": _karen("success"),
            }
            return self._hold(promo, routes, base="origin/main")

    def test_R3_F2a_path_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            main_files = {
                "data/keep.json": b'{"k":1}\n',
                "data/rename-from.json": b'{"r":1}\n',
                "data/delete-me.json": b'{"d":1}\n',
            }
            files = edition_files(SLUG, preview_payload(), hold=False)
            files["data/keep.json"] = b'{"k":2}\n'
            files["data/rename-to.json"] = b'{"r":1}\n'
            promo = build_promo(Path(tmp), files, main_files=main_files)
            seed = promo.tmp / "seed"
            git(["checkout", promo.head_ref], cwd=seed)
            if (seed / "data" / "rename-from.json").exists():
                git(["rm", "-f", "data/rename-from.json"], cwd=seed)
            if (seed / "data" / "delete-me.json").exists():
                git(["rm", "-f", "data/delete-me.json"], cwd=seed)
            git(["add", "-A"], cwd=seed)
            git(["commit", "-m", "f2a mix", "--allow-empty"], cwd=seed)
            changes = git_raw_changed("origin/main", "HEAD", seed)
            renamed = git_raw_changed(
                "origin/main", "HEAD", seed, detect_renames=True
            )
            rows = provenance_diff_paths(changes)
            names = [path for path, _status in rows]
            letters = {path: status[:1] for path, status in rows}
            self.assertIn("data/narrative.json", names)
            self.assertIn("data/keep.json", names)
            self.assertTrue(
                "data/delete-me.json" in names or any(letter == "D" for letter in letters.values()),
                names,
            )
            self.assertTrue(
                "data/rename-from.json" in names or "data/rename-to.json" in names,
                names,
            )
            orig_sides = [change.orig_path for change in renamed if change.orig_path]
            if orig_sides:
                self.assertTrue(
                    any(side in names for side in orig_sides)
                    or any(
                        side in {item.path for item in renamed} for side in orig_sides
                    )
                )

    def test_R3_F2b_bot_edition_json(self):
        self._refuse(
            self._bot_on_path("data/narrative.json", dumps(preview_payload(headline="bot"))),
            "bot merge",
        )

    def test_R3_F2c_bot_hold_flag(self):
        self._refuse(
            self._bot_on_path("data/narrative_repair.json", repair_bytes(hold=True)),
            "bot merge",
        )

    def test_R3_F2d_bot_wire(self):
        self._refuse(self._bot_on_path("data/wire.json", b'{"bot":1}\n'), "bot merge")

    def test_R3_F2e_bot_archive(self):
        self._refuse(self._bot_on_path("data/narrative_archive.json", b"[{}]\n"), "bot merge")

    def test_R3_F2f_bot_svg(self):
        rel = f"public/images/narrative/{SLUG}/xo-cover_two.svg"
        self._refuse(self._bot_on_path(rel, b'<svg xmlns="http://www.w3.org/2000/svg"/>\n'), "bot merge")

    def test_R3_F2g_unresolvable_or_direct_push(self):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._full(tmp, hold=False)
            routes = {f"/commits/{promo.develop_sha}/pulls": []}
            self._refuse(self._hold(promo, routes, base="origin/main"), "no introducing PR")

    def test_R3_F2h_all_contributors(self):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._full(tmp, hold=False)
            intro = promo.develop_sha
            bot_sha = self._touch_develop(
                promo, "data/narrative.json", dumps(preview_payload(headline="bot"))
            )
            human_sha = self._touch_develop(
                promo, "data/narrative.json", dumps(preview_payload(headline="later"))
            )
            bot = self._bind(_bot_pr("b" * 40, number=418), bot_sha)
            human_intro = self._bind(_human_pr("a" * 40, number=417), intro)
            human_later = self._bind(_human_pr("c" * 40, number=419), human_sha)
            routes = {
                f"/commits/{intro}/pulls": human_intro,
                f"/commits/{bot_sha}/pulls": bot,
                f"/commits/{human_sha}/pulls": human_later,
                "/pulls/417": human_intro[0],
                "/pulls/418": bot[0],
                "/pulls/419": human_later[0],
                f"/pulls/417/commits": [{"sha": intro}],
                f"/pulls/418/commits": [{"sha": bot_sha}],
                f"/pulls/419/commits": [{"sha": human_sha}],
                "/statuses": _karen("success"),
            }
            self._refuse(self._hold(promo, routes, base="origin/main"), "bot merge")

    def test_R3_F2i_karen_on_other_sha(self):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._full(tmp, hold=False)
            pr_head = "1" * 40
            other = "2" * 40
            pr = self._bind(_human_pr(pr_head), promo.develop_sha)
            routes = {
                f"/commits/{promo.develop_sha}/pulls": pr,
                "/pulls/417": pr[0],
                f"/pulls/417/commits": [{"sha": promo.develop_sha}],
                f"/commits/{pr_head}/statuses": _karen("failure"),
                f"/commits/{other}/statuses": _karen("success"),
            }
            self._refuse(self._hold(promo, routes, base="origin/main"), "karen-qa")

    def test_R3_F2j_caps(self):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._full(tmp, hold=False)
            human = self._human_routes(promo.develop_sha)
            n_files = [{"filename": f"f{i}.json"} for i in range(COMPARE_FILE_CAP)]
            routes = {
                **human,
                "/compare/": {"files": n_files, "truncated": False},
            }
            self._refuse(self._hold(promo, routes, base="origin/main"), "truncated")
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._full(tmp, hold=False)
            human = self._human_routes(promo.develop_sha)
            n1 = [{"filename": f"f{i}.json"} for i in range(COMPARE_FILE_CAP + 1)]
            routes = {
                **human,
                "/compare/": {"files": n1, "truncated": False},
            }
            self._refuse(self._hold(promo, routes, base="origin/main"), "truncated")
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._full(tmp, hold=False)
            pr = _human_pr("a" * 40)
            pr[0]["merge_commit_sha"] = "f" * 40
            commits_n = [{"sha": f"{i:040x}"} for i in range(COMMIT_PAGE_CAP)]
            routes = {
                f"/commits/{promo.develop_sha}/pulls": pr,
                "/pulls/417": pr[0],
                "/pulls/417/commits": commits_n,
                "/statuses": _karen("success"),
            }
            self._refuse(self._hold(promo, routes, base="origin/main"), "truncated")
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._full(tmp, hold=False)
            pr = _human_pr("a" * 40)
            pr[0]["merge_commit_sha"] = "f" * 40
            commits_n1 = [{"sha": f"{i:040x}"} for i in range(COMMIT_PAGE_CAP + 1)]
            routes = {
                f"/commits/{promo.develop_sha}/pulls": pr,
                "/pulls/417": pr[0],
                "/pulls/417/commits": commits_n1,
                "/statuses": _karen("success"),
            }
            self._refuse(self._hold(promo, routes, base="origin/main"), "truncated")

    def test_R3_F2k_receipt_417(self):
        pr417 = _load("pulls_417.json")
        merged_by = pr417["merged_by"]
        login = merged_by["login"]
        mtype = merged_by["type"]
        self.assertEqual(login, "stevologic")
        self.assertEqual(mtype, "User")
        statuses = _load("commits_417head_statuses.json")
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._full(tmp, hold=False)
            intro = promo.develop_sha
            later = self._touch_develop(
                promo, "data/narrative.json", dumps(preview_payload(headline="423"))
            )
            pr423 = _load("pulls_423.json")
            routes = {
                f"/commits/{intro}/pulls": self._bind(_load("commits_831ae92_pulls.json"), intro),
                f"/commits/{later}/pulls": self._bind(_load("commits_3a220bd_pulls.json"), later),
                "/pulls/417": pr417,
                "/pulls/423": pr423,
                f"/pulls/417/commits": [{"sha": intro}],
                f"/pulls/423/commits": [{"sha": later}],
                f"/commits/{pr417['head']['sha']}/statuses": statuses,
                f"/commits/{pr423['head']['sha']}/statuses": _load(
                    "commits_423head_statuses.json"
                ),
            }
            result = self._hold(promo, routes, base="origin/main")
            outcome = "pass" if result.returncode == 0 else f"refuse:{result.stderr.strip()}"
            self.assertEqual(login, "stevologic")
            self.assertEqual(mtype, "User")
            self.assertIn(result.returncode, (0, 1), outcome)
            if result.returncode != 0:
                self.assertNotIn("Traceback", result.stderr)
            self.assertTrue(
                result.returncode == 0 or "karen-qa" in result.stderr or "provenance" in result.stderr
                or "bot" in result.stderr,
                outcome,
            )
            self._pass(result)

    def test_R3_F2l_skip_non_json(self):
        rel = f"public/images/narrative/{SLUG}/xo-cover_two.svg"
        self._refuse(
            self._bot_on_path(rel, b'<svg xmlns="http://www.w3.org/2000/svg"/>\n'),
            "bot merge",
        )

    def test_R3_F2l_skip_deletes(self):
        with tempfile.TemporaryDirectory() as tmp:
            main_files = {"data/old.json": b'{"orphan-delete": true}\n'}
            files = edition_files(SLUG, preview_payload(), hold=False)
            promo = build_promo(Path(tmp), files, main_files=main_files)
            intro = promo.develop_sha
            extra = self._touch_develop(promo, "data/old.json", b'{"orphan-delete": "bot"}\n')
            seed = promo.tmp / "seed"
            git(["checkout", promo.head_ref], cwd=seed)
            git(["rm", "-f", "data/old.json"], cwd=seed)
            git(["commit", "-m", "delete old.json"], cwd=seed)
            git(["push", "origin", promo.head_ref], cwd=seed)
            git(["fetch", "origin", promo.head_ref], cwd=promo.pr_tree)
            git(["reset", "--hard", "FETCH_HEAD"], cwd=promo.pr_tree)
            rows = provenance_diff_paths(
                git_raw_changed("origin/main", "HEAD", promo.pr_tree)
            )
            self.assertTrue(
                any(path == "data/old.json" and status[:1] == "D" for path, status in rows),
                rows,
            )
            human = self._bind(_human_pr("a" * 40, number=417), intro)
            bot = self._bind(_bot_pr("b" * 40, number=418), extra)
            routes = {
                f"/commits/{intro}/pulls": human,
                f"/commits/{extra}/pulls": bot,
                "/pulls/417": human[0],
                "/pulls/418": bot[0],
                f"/pulls/417/commits": [{"sha": intro}],
                f"/pulls/418/commits": [{"sha": extra}],
                "/statuses": _karen("success"),
            }
            self._refuse(self._hold(promo, routes, base="origin/main"), "bot merge")

    def test_R3_F2l_unresolved_as_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._full(tmp, hold=False)
            routes = {f"/commits/{promo.develop_sha}/pulls": []}
            self._refuse(self._hold(promo, routes, base="origin/main"), "no introducing PR")

    def test_R3_F2m_merge_commit_contributor(self):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._full(tmp, hold=False)
            merge_sha = "c" * 40
            pr = copy.deepcopy(_human_pr("a" * 40))
            pr[0]["merge_commit_sha"] = merge_sha
            routes = {
                f"/commits/{promo.develop_sha}/pulls": pr,
                "/pulls/417": pr[0],
                "/pulls/417/commits": [{"sha": promo.develop_sha}, {"sha": "a" * 40}],
                "/statuses": _karen("success"),
            }
            self._pass(self._hold(promo, routes, base="origin/main"))

    def test_R3_F2n_rebase_merge_contributor(self):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._full(tmp, hold=False)
            pr = copy.deepcopy(_human_pr("a" * 40))
            pr[0]["merge_commit_sha"] = promo.develop_sha
            routes = {
                f"/commits/{promo.develop_sha}/pulls": pr,
                "/pulls/417": pr[0],
                "/pulls/417/commits": [{"sha": "d" * 40}],
                "/statuses": _karen("success"),
            }
            self._pass(self._hold(promo, routes, base="origin/main"))

    def test_R3_Y3a_success_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._full(tmp, hold=True)
            routes = self._human_routes(promo.develop_sha)
            self._pass(self._hold(promo, routes))

    def test_R3_Y3b_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._full(tmp, hold=True)
            pr = _human_pr("a" * 40)
            routes = {
                f"/commits/{promo.develop_sha}/pulls": pr,
                "/pulls": pr,
                "/statuses": _karen("pending"),
            }
            self._refuse(self._hold(promo, routes), "karen-qa pending")

    def test_R3_Y3b_failure_error_missing(self):
        for state, needle in (
            ("failure", "karen-qa failure"),
            ("error", "karen-qa error"),
            (None, "karen-qa absent"),
        ):
            with self.subTest(state=state):
                with tempfile.TemporaryDirectory() as tmp:
                    promo = self._full(tmp, hold=True)
                    pr = _human_pr("a" * 40)
                    statuses = [] if state is None else _karen(state)
                    routes = {
                        f"/commits/{promo.develop_sha}/pulls": pr,
                        "/pulls": pr,
                        "/statuses": statuses,
                    }
                    self._refuse(self._hold(promo, routes), needle)

    def test_R3_Y3c_lookalikes(self):
        for context in ("karen-qa/x", "Karen-QA", "karen-qa-bot"):
            with self.subTest(context=context):
                with tempfile.TemporaryDirectory() as tmp:
                    promo = self._full(tmp, hold=True)
                    pr = _human_pr("a" * 40)
                    routes = {
                        f"/commits/{promo.develop_sha}/pulls": pr,
                        "/pulls": pr,
                        "/statuses": [
                            {
                                "context": context,
                                "state": "success",
                                "updated_at": "2026-11-26T18:00:00Z",
                            }
                        ],
                    }
                    self._refuse(self._hold(promo, routes), "lookalike")

    def test_R3_Y3d_latest_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            promo = self._full(tmp, hold=True)
            pr = _human_pr("a" * 40)
            routes = {
                f"/commits/{promo.develop_sha}/pulls": pr,
                "/pulls": pr,
                "/statuses": [
                    {
                        "context": "karen-qa",
                        "state": "success",
                        "updated_at": "2026-11-26T18:00:00Z",
                    },
                    {
                        "context": "karen-qa",
                        "state": "failure",
                        "updated_at": "2026-11-26T19:00:00Z",
                    },
                ],
            }
            self._refuse(self._hold(promo, routes), "karen-qa failure")

    def test_R3_N5n_merge_not_already_merged(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log = tmp_path / "gh.log"
            write_gh_stub(
                tmp_path / "gh",
                log,
                merge_ok=False,
                merge_error="cannot merge: required status check",
            )
            env = {
                **os.environ,
                "PATH": str(tmp_path) + os.pathsep + "/usr/bin:/bin",
                "GH_TOKEN": "x",
                "PR_URL": "https://example.test/1",
                "HEAD_SHA": "abc",
                "GITHUB_OUTPUT": str(tmp_path / "out"),
            }
            merge = extracted_step_script("ci.yml", "Squash-merge the pull request")
            result = bash_c(merge, cwd=tmp_path, env=env)
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn("already merged; treating as success", result.stdout)


if __name__ == "__main__":
    unittest.main()
