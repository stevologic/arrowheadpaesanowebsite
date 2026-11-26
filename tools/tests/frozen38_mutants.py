"""FROZEN-38 mutants M1–M50. A kill counts only on a behavioural AssertionError."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HUNK = "\n@@\n"

MUTANTS: list[tuple[str, str, str, str, str]] = [
    (
        "M1",
        "test_D3_lookalike_paths",
        "tools/chiefs_narrative/review_gate.py",
        "    if not _path_is_exact(path):\n        return \"bad\", None\n    if path in ES_FIXED_PATHS:",
        "    if path.startswith(\"data/\") or path.startswith(\"public/\"):\n        return \"fixed\", None\n    if not _path_is_exact(path):\n        return \"bad\", None\n    if path in ES_FIXED_PATHS:",
    ),
    (
        "M2",
        "test_B2_each_es_member",
        "tools/chiefs_narrative/review_gate.py",
        "        if dev_sha != change.new_sha:"
        + HUNK
        + "        if head_blob != dev_blob:",
        "        if False and dev_sha != change.new_sha:"
        + HUNK
        + "        if False and head_blob != dev_blob:",
    ),
    (
        "M3",
        "test_A1_preview_11",
        "tools/chiefs_narrative/review_gate.py",
        "        dev = _ls_tree(root, develop_sha, change.path)",
        "        dev = _ls_tree(root, base, change.path)",
    ),
    (
        "M4",
        "test_B2_each_es_member",
        "tools/chiefs_narrative/review_gate.py",
        "        dev = _ls_tree(root, develop_sha, change.path)"
        + HUNK
        + "    develop_files = slug_tree_files(root, develop_sha, edition_slug)",
        "        dev = _ls_tree(root, head, change.path)"
        + HUNK
        + "    develop_files = slug_tree_files(root, head, edition_slug)",
    ),
    (
        "M5",
        "test_B9_stale_local_develop_ignored",
        "tools/chiefs_narrative/review_gate.py",
        "        if fetch_develop:\n            develop_sha = fetch_origin_develop(root, develop_ref)\n        else:\n            develop_sha = _resolve_develop_sha(root, develop_ref)",
        "        develop_sha = _resolve_develop_sha(root, \"develop\")",
    ),
    (
        "M6",
        "test_B8_develop_fetch_failed",
        "tools/chiefs_narrative/review_gate.py",
        "        meta[\"verdict\"] = \"refuse\"\n        return msg, meta",
        "        return None, meta",
    ),
    (
        "M7",
        "test_R2_B6a_reverify_ci_runs",
        ".github/workflows/ci.yml",
        "          PINNED_DEVELOP_SHA: ${{ steps.gate.outputs.develop_sha }}\n        run: |\n          set -euo pipefail\n          git fetch --no-tags origin +refs/heads/develop:refs/remotes/origin/develop",
        "          PINNED_DEVELOP_SHA: ${{ steps.gate.outputs.develop_sha }}\n        run: |\n          set -euo pipefail\n          exit 0\n          git fetch --no-tags origin +refs/heads/develop:refs/remotes/origin/develop",
    ),
    (
        "M8",
        "test_C2_lookalike_refs",
        "tools/chiefs_narrative/review_gate.py",
        "    if not text.startswith(PROMOTE_PREFIX):\n        return False",
        "    return True\n    if not text.startswith(PROMOTE_PREFIX):\n        return False",
    ),
    (
        "M9",
        "test_R2_N7c_non_promote_never_automerge",
        "tools/chiefs_narrative/review_gate.py",
        "    promote = is_promote_head_ref(head_ref)",
        "    promote = True",
    ),
    (
        "M10",
        "test_C4_head_ref_env_only",
        ".github/workflows/ci.yml",
        '--head-ref "$HEAD_REF"',
        '--head-ref "${{ github.head_ref }}"',
    ),
    (
        "M11",
        "test_D7_rename_lists_both",
        "tools/chiefs_narrative/review_gate.py",
        '    rename_flag = "-M" if detect_renames else "--no-renames"',
        '    rename_flag = "-M"',
    ),
    (
        "M12",
        "test_D6_deletes",
        "tools/chiefs_narrative/review_gate.py",
        '"--no-ext-diff",',
        '"--diff-filter=AM",\n                "--no-ext-diff",',
    ),
    (
        "M13",
        "test_D8_mode_symlink",
        "tools/chiefs_narrative/review_gate.py",
        '    if change.new_mode == "120000" or change.old_mode == "120000":\n        return f"mode/symlink: {change.path}"\n    if change.new_mode == "160000" or change.old_mode == "160000":\n        return f"mode/symlink: {change.path}"\n    if change.new_mode != "100644":',
        '    if False and (change.new_mode == "120000" or change.old_mode == "120000"):\n        return f"mode/symlink: {change.path}"\n    if False and (change.new_mode == "160000" or change.old_mode == "160000"):\n        return f"mode/symlink: {change.path}"\n    if False and change.new_mode != "100644":',
    ),
    (
        "M14",
        "test_D3_lookalike_paths",
        "tools/chiefs_narrative/review_gate.py",
        '    if path.startswith("data/narrative_editions/") and path.count("/") == 2:',
        '    if path.lower().replace("\\\\", "/").find("narrative_editions") >= 0:',
    ),
    (
        "M15",
        "test_D4_case_tricks",
        "tools/chiefs_narrative/review_gate.py",
        "    if not _path_is_exact(path):\n        return \"bad\", None",
        "    path = path.replace(\"\\\\\", \"/\").lower()\n    if not path:\n        return \"bad\", None",
    ),
    (
        "M16",
        "test_E1_title_ignored",
        "tools/chiefs_narrative/review_gate.py",
        "    if mode in _REVIEW_MODES:\n        return True",
        "    if \"review\" in str(payload.get(\"title\") or payload.get(\"branch\") or edition).lower():\n        return True\n    if False and mode in _REVIEW_MODES:\n        return True",
    ),
    (
        "M17",
        "test_E4_mismatched_copies",
        "tools/chiefs_narrative/review_gate.py",
        '    for rel in ("data/narrative.json", editions_path):',
        '    for rel in ("data/narrative.json",):',
    ),
    (
        "M18",
        "test_E5_E1W_E1S_invalid_pairs",
        "tools/chiefs_narrative/review_gate.py",
        "    if not _verify_ed25519(message=json_bytes, signature=signature, pubkey_path=pubkey):",
        "    if False and not _verify_ed25519(message=json_bytes, signature=signature, pubkey_path=pubkey):",
    ),
    (
        "M19",
        "test_E5_E1W_E1S_invalid_pairs",
        "tools/chiefs_narrative/review_gate.py",
        "    if pair_slug != slug:\n        return \"invalid signoff: slug\"",
        "    if False and pair_slug != slug:\n        return \"invalid signoff: slug\"",
    ),
    (
        "M20",
        "test_E5_E1W_E1S_invalid_pairs",
        "tools/chiefs_narrative/review_gate.py",
        "    if data.get(\"sha\") != sha:\n        return \"invalid signoff: payload sha\"\n    if json_bytes != expected:\n        return \"invalid signoff: payload sha\"",
        "    if False and data.get(\"sha\") != sha:\n        return \"invalid signoff: payload sha\"\n    if False and json_bytes != expected:\n        return \"invalid signoff: payload sha\"",
    ),
    (
        "M21",
        "test_E6_pubkey_from_base",
        "tools/chiefs_narrative/review_gate.py",
        "    return Path(__file__).resolve().parents[2] / PUBLIC_KEY_REL",
        "    return Path(os.environ.get(\"GITHUB_WORKSPACE\") or \".\") / PUBLIC_KEY_REL",
    ),
    (
        "M22",
        "test_F7_no_swallowed_rc",
        ".github/workflows/ci.yml",
        "            --root \"${GITHUB_WORKSPACE}\"",
        "            --root \"${GITHUB_WORKSPACE}\" || true",
    ),
    (
        "M23",
        "test_D1_empty_diff",
        "tools/chiefs_narrative/review_gate.py",
        "    if not changes:\n        meta[\"verdict\"] = \"refuse\"\n        return \"no changes\", meta",
        "    if not changes:\n        return None, meta",
    ),
    (
        "M24",
        "test_G1_G2_cap",
        "tools/chiefs_narrative/review_gate.py",
        "    if len(listed) > AUTOMERGE_PATH_CAP:",
        "    if len(listed) >= AUTOMERGE_PATH_CAP:",
    ),
    (
        "M25",
        "test_G4_listing_completeness",
        "tools/chiefs_narrative/review_gate.py",
        "            orig_path=orig,\n            )\n        )\n    return changes",
        "            orig_path=orig,\n            )\n        )\n    return changes[:100]",
    ),
    (
        "M26",
        "test_D2_non_es_paths",
        "tools/chiefs_narrative/review_gate.py",
        '        if kind == "bad":',
        '        if kind == "bad" and False:',
    ),
    (
        "M27",
        "test_F1_needs_gates",
        ".github/workflows/ci.yml",
        "          github.event.pull_request.head.repo.full_name == github.repository }}",
        "          github.event.pull_request.head.repo.full_name == github.repository && always() }}",
    ),
    (
        "M28",
        "test_R2_B3b_preview_promote_both_pass",
        ".github/workflows/edition-qa.yml",
        "--head-ref \"$HEAD_REF\"",
        "--head-ref \"not-a-promote\"",
    ),
    (
        "M29",
        "test_A_msg_distinct_reasons",
        "tools/chiefs_narrative/review_gate.py",
        "            if reason:\n                print(reason, file=sys.stderr)\n                return 1\n            return 0\n        if args.pages:",
        "            if reason:\n                print(\"Review edition requires Karen's ed25519 sign-off (signoff/review/<slug>.json + <slug>.sig), or this PR is off the automerge allowlist\", file=sys.stderr)\n                return 1\n            return 0\n        if args.pages:",
    ),
    (
        "M30",
        "test_D5z_newline_is_one_path",
        "tools/chiefs_narrative/review_gate.py",
        '    parts = blob.split(b"\\0")',
        '    parts = blob.replace(b"\\n", b"\\0").split(b"\\0")',
    ),
    (
        "M31",
        "test_D11_svg_slug_and_nesting",
        "tools/chiefs_narrative/review_gate.py",
        '    if path.startswith("public/images/narrative/") and path.count("/") == 4:',
        '    if path.startswith("public/"):\n        return "svg", None\n    if False and path.startswith("public/images/narrative/") and path.count("/") == 4:',
    ),
    (
        "M32",
        "test_E3_preview_mode_wins_final",
        "tools/chiefs_narrative/review_gate.py",
        "    if mode in _PREVIEW_MODES:\n        return False",
        "    if False and mode in _PREVIEW_MODES:\n        return False",
    ),
    (
        "M33",
        "test_A6_S2_E1U_unsigned_review",
        "tools/chiefs_narrative/review_gate.py",
        "    if review_paths:",
        "    if review_paths and not promote:",
    ),
    (
        "M34",
        "test_E5_E1W_E1S_invalid_pairs",
        "tools/chiefs_narrative/review_gate.py",
        "    if pair_slug != slug:\n        return \"invalid signoff: slug\"",
        "    if False:\n        return \"invalid signoff: slug\"",
    ),
    (
        "M35",
        "test_E5_E1W_E1S_invalid_pairs",
        "tools/chiefs_narrative/review_gate.py",
        "    if json_bytes != expected:\n        return \"invalid signoff: payload sha\"",
        "    if False and json_bytes != expected:\n        return \"invalid signoff: payload sha\"",
    ),
    (
        "M36",
        "test_H1P_hold_true_identity_passes_gate",
        "tools/chiefs_narrative/review_gate.py",
        "        if repair.get(\"holdAutomerge\"):\n            meta[\"hold_automerge\"] = \"true\"",
        "        if repair.get(\"holdAutomerge\"):\n            meta[\"hold_automerge\"] = \"true\"\n            meta[\"verdict\"] = \"refuse\"\n            return \"holdAutomerge\", meta",
    ),
    (
        "M37",
        "test_H1_override_api",
        "tools/chiefs_narrative/review_gate.py",
        "        if named != develop_sha:\n            return \"hold override refused: b: SHA != tip\"",
        "        if False and named != develop_sha:\n            return \"hold override refused: b: SHA != tip\"",
    ),
    (
        "M38",
        "test_E2H_bot_merge_refused",
        "tools/chiefs_narrative/review_gate.py",
        "    if mtype != \"User\" or not login or login.endswith(\"[bot]\") or mtype == \"Bot\":",
        "    if False and (mtype != \"User\" or not login or login.endswith(\"[bot]\") or mtype == \"Bot\"):",
    ),
    (
        "M39",
        "test_H1_override_api",
        "tools/chiefs_narrative/review_gate.py",
        "        if code != 200:\n            return f\"hold override refused: b: API error: HTTP {code}\"",
        "        if code != 200:\n            return None",
    ),
    (
        "M40",
        "test_R2_B4a_ci_hold_step_present",
        ".github/workflows/ci.yml",
        "          HEAD_REF: ${{ github.head_ref }}\n          BASE_SHA: ${{ github.event.pull_request.base.sha }}\n          PR_NUMBER: ${{ github.event.pull_request.number }}\n          GITHUB_REPOSITORY: ${{ github.repository }}\n        run: |\n          set -euo pipefail\n          GATE_BASE=\"${RUNNER_TEMP}/gate-base\"",
        "          HEAD_REF: ${{ github.head_ref }}\n          BASE_SHA: ${{ github.event.pull_request.base.sha }}\n          PR_NUMBER: ${{ github.event.pull_request.number }}\n          GITHUB_REPOSITORY: ${{ github.repository }}\n        run: |\n          set -euo pipefail\n          exit 0\n          GATE_BASE=\"${RUNNER_TEMP}/gate-base\"",
    ),
    (
        "M41",
        "test_H2_L_D_F_label_reread",
        ".github/workflows/ci.yml",
        "          payload=\"$(gh api \"repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}\")\"",
        "          payload=\"$(gh api \"repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}\" || echo '{\"draft\":false,\"labels\":[]}')\"",
    ),
    (
        "M42",
        "test_H2_L_D_F_label_reread",
        ".github/workflows/ci.yml",
        '          out="$(gh pr merge "$PR_URL" --squash --match-head-commit "$HEAD_SHA" 2>&1)"',
        '          out="$(gh pr merge "$PR_URL" --squash --auto --match-head-commit "$HEAD_SHA" 2>&1)"',
    ),
    (
        "M43",
        "test_R2_B5b_edition_qa_gate_step_clean_no_sentinel",
        ".github/workflows/edition-qa.yml",
        "          cd \"$GATE_BASE\"\n          PYTHONPATH=\"$GATE_BASE\" python -P -m tools.chiefs_narrative.review_gate --automerge \\",
        "          cd \"$GITHUB_WORKSPACE\"\n          PYTHONPATH=\"$GITHUB_WORKSPACE\" python -P -m tools.chiefs_narrative.review_gate --automerge \\",
    ),
    (
        "M44",
        "test_R2_B6b_reverify_edition_qa_runs",
        ".github/workflows/edition-qa.yml",
        "          PINNED_DEVELOP_SHA: ${{ steps.gate.outputs.develop_sha }}\n        run: |\n          set -euo pipefail\n          git fetch --no-tags origin +refs/heads/develop:refs/remotes/origin/develop",
        "          PINNED_DEVELOP_SHA: ${{ steps.gate.outputs.develop_sha }}\n        run: |\n          set -euo pipefail\n          exit 0\n          git fetch --no-tags origin +refs/heads/develop:refs/remotes/origin/develop",
    ),
    (
        "M45",
        "test_R2_B4b_edition_qa_hold_step_present",
        ".github/workflows/edition-qa.yml",
        "          HEAD_REF: ${{ github.head_ref }}\n          BASE_SHA: ${{ github.event.pull_request.base.sha }}\n          PR_NUMBER: ${{ github.event.pull_request.number }}\n          GITHUB_REPOSITORY: ${{ github.repository }}\n        run: |\n          set -euo pipefail\n          GATE_BASE=\"${RUNNER_TEMP}/gate-base\"",
        "          HEAD_REF: ${{ github.head_ref }}\n          BASE_SHA: ${{ github.event.pull_request.base.sha }}\n          PR_NUMBER: ${{ github.event.pull_request.number }}\n          GITHUB_REPOSITORY: ${{ github.repository }}\n        run: |\n          set -euo pipefail\n          exit 0\n          GATE_BASE=\"${RUNNER_TEMP}/gate-base\"",
    ),
    (
        "M46",
        "test_R2_B5a_ci_gate_step_clean_no_sentinel",
        ".github/workflows/ci.yml",
        "          cd \"$GATE_BASE\"\n          PYTHONPATH=\"$GATE_BASE\" python -P -m tools.chiefs_narrative.review_gate --automerge \\",
        "          cd \"$GITHUB_WORKSPACE\"\n          PYTHONPATH=\"$GITHUB_WORKSPACE\" python -P -m tools.chiefs_narrative.review_gate --automerge \\",
    ),
    (
        "M47",
        "test_R2_B4c_karen_pending",
        "tools/chiefs_narrative/review_gate.py",
        "    if state == \"success\":\n        return True, \"ok\"",
        "    if state != \"failure\":\n        return True, \"ok\"",
    ),
    (
        "M48",
        "test_R2_B4d_karen_absent",
        "tools/chiefs_narrative/review_gate.py",
        "        return False, \"karen-qa absent\"\n    state = str(exact[0].get(\"state\") or \"\").lower()",
        "        return True, \"ok\"\n    state = str(exact[0].get(\"state\") or \"\").lower()",
    ),
    (
        "M49",
        "test_R2_B4h_karen_on_wrong_sha",
        "tools/chiefs_narrative/review_gate.py",
        "    ok, why = _karen_qa_success(pr_head, repository)",
        "    ok, why = _karen_qa_success(develop_sha, repository)",
    ),
    (
        "M50",
        "test_R2_B2b_multiple_prs",
        "tools/chiefs_narrative/review_gate.py",
        "        if multi:\n            return f\"hold override refused: b: {multi}\"\n"
        "        if not candidates:\n            return \"hold override refused: b: no introducing PR\"\n"
        "        if len(candidates) != 1:\n            return f\"hold override refused: b: zero or multiple PRs ({len(candidates)})\"",
        "        if False and multi:\n            return f\"hold override refused: b: {multi}\"\n"
        "        if not candidates:\n            return \"hold override refused: b: no introducing PR\"\n"
        "        if False and len(candidates) != 1:\n            return f\"hold override refused: b: zero or multiple PRs ({len(candidates)})\"",
    ),
    (
        "F1i",
        "test_R3_F1b_bot_refuses",
        "tools/chiefs_narrative/review_gate.py",
        "        pr, fetch_why = fetch_pull(repository, number)\n        if fetch_why:\n            return f\"hold override refused: b: {fetch_why}\"\n        if pr is None:\n            return \"hold override refused: b: no introducing PR\"",
        "        pr, fetch_why = summary, None\n        if fetch_why:\n            return f\"hold override refused: b: {fetch_why}\"\n        if pr is None:\n            return \"hold override refused: b: no introducing PR\"",
    ),
    (
        "F2h",
        "test_R3_F2h_all_contributors",
        "tools/chiefs_narrative/review_gate.py",
        "        for sha in shas:\n            pr, why = resolve_contributor_pr(sha, repository)",
        "        for sha in shas[:1]:\n            pr, why = resolve_contributor_pr(sha, repository)",
    ),
    (
        "F2l_json",
        "test_R3_F2l_skip_non_json",
        "tools/chiefs_narrative/review_gate.py",
        "    for path, status in paths:\n        del status\n        try:\n            shas = _commits_touching(root, base, develop_sha, path)",
        "    for path, status in paths:\n        del status\n        if not path.endswith(\".json\"):\n            continue\n        try:\n            shas = _commits_touching(root, base, develop_sha, path)",
    ),
    (
        "F2l_del",
        "test_R3_F2l_skip_deletes",
        "tools/chiefs_narrative/review_gate.py",
        "    for path, status in paths:\n        del status\n        try:\n            shas = _commits_touching(root, base, develop_sha, path)",
        "    for path, status in paths:\n        if str(status)[:1] == \"D\":\n            continue\n        del status\n        try:\n            shas = _commits_touching(root, base, develop_sha, path)",
    ),
    (
        "F2l_unres",
        "test_R3_F2l_unresolved_as_pass",
        "tools/chiefs_narrative/review_gate.py",
        "            if pr is None:\n                return f\"provenance: {why}\"",
        "            if pr is None:\n                continue",
    ),
    (
        "Y3",
        "test_R3_Y3b_pending",
        "tools/chiefs_narrative/review_gate.py",
        "    if state == \"success\":\n        return True, \"ok\"",
        "    if True:\n        return True, \"ok\"",
    ),
    (
        "KN5",
        "test_R3_N5n_merge_not_already_merged",
        ".github/workflows/ci.yml",
        "          if printf '%s\\n' \"$out\" | grep -qi 'already merged'; then",
        "          if printf '%s\\n' \"$out\" | grep -qi 'merge'; then",
    ),
]


R3 = "tools.tests.test_frozen38_r3.Frozen38R3"


GATE = "tools.tests.test_frozen38_promote_gate.Frozen38PromoteGate"
WF = "tools.tests.test_frozen38_workflows.Frozen38Workflows"
R2 = "tools.tests.test_frozen38_r2.Frozen38R2"
WF_PREFIXES = (
    "test_A_two",
    "test_A8",
    "test_A10",
    "test_C3",
    "test_C4",
    "test_C5",
    "test_C6",
    "test_C7",
    "test_F",
    "test_H2",
    "test_H5",
    "test_E_parity",
    "test_B7",
    "test_B10",
    "test_C1b",
    "test_R2_",
)


def _killer_mod(killer: str) -> str:
    if killer.startswith("test_R3_"):
        return R3
    if killer.startswith("test_R2_"):
        return R2
    if killer in {"test_C1_promote_prefix", "test_C2_lookalike_refs"}:
        return GATE
    if killer.startswith(WF_PREFIXES):
        return WF
    return GATE


def apply_mutant(dest: Path, rel: str, old: str, new: str) -> str:
    path = dest / rel
    text = path.read_text(encoding="utf-8")
    chunks = old.split(HUNK)
    news = new.split(HUNK)
    if len(chunks) != len(news):
        return f"HUNK COUNT {rel}: {len(chunks)} vs {len(news)}"
    for hunk_old, hunk_new in zip(chunks, news):
        if hunk_old not in text:
            return f"MISSING {rel}: {hunk_old[:80]!r}"
        text = text.replace(hunk_old, hunk_new, 1)
    path.write_text(text, encoding="utf-8")
    return "ok"


def mutant_old_present(root: Path, rel: str, old: str) -> list[str]:
    text = (root / rel).read_text(encoding="utf-8")
    missing = []
    for hunk_old in old.split(HUNK):
        if hunk_old not in text:
            missing.append(hunk_old[:80])
    return missing


def _is_assertion_kill(stderr: str, stdout: str) -> bool:
    blob = stderr + stdout
    if "AssertionError" in blob or "\nFAIL:" in blob or blob.startswith("FAIL:"):
        return True
    return False


def run_one(mutant: tuple[str, str, str, str, str]) -> dict:
    mid, killer, rel, old, new = mutant
    started = time.time()
    with tempfile.TemporaryDirectory(prefix=f"f38-{mid}-") as tmp:
        dest = Path(tmp)
        shutil.copytree(
            REPO / "tools",
            dest / "tools",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".mypy_cache"),
        )
        wf_dest = dest / ".github" / "workflows"
        wf_dest.mkdir(parents=True, exist_ok=True)
        for name in ("ci.yml", "edition-qa.yml", "narrative.yml", "pages.yml"):
            src = REPO / ".github" / "workflows" / name
            if src.is_file():
                shutil.copy2(src, wf_dest / name)
        scripts_dest = dest / "scripts"
        scripts_dest.mkdir(parents=True, exist_ok=True)
        pubkey = REPO / "scripts" / "review_signoff_pubkey.pem"
        if pubkey.is_file():
            shutil.copy2(pubkey, scripts_dest / pubkey.name)
        status = apply_mutant(dest, rel, old, new)
        if status != "ok":
            return {
                "id": mid,
                "killer": killer,
                "ok": False,
                "why": status,
                "seconds": time.time() - started,
            }
        target = f"{_killer_mod(killer)}.{killer}"
        env = {
            **os.environ,
            "PYTHONPATH": str(dest),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        killed = subprocess.run(
            [sys.executable, "-m", "unittest", target, "-q"],
            cwd=dest,
            capture_output=True,
            text=True,
            env=env,
        )
        assertion = _is_assertion_kill(killed.stderr, killed.stdout)
        return {
            "id": mid,
            "killer": killer,
            "ok": killed.returncode != 0 and assertion,
            "returncode": killed.returncode,
            "assertion": assertion,
            "stderr": (killed.stderr or "")[-1500:],
            "stdout": (killed.stdout or "")[-500:],
            "diff": f"{rel}: {old[:60]!r} -> {new[:60]!r}",
            "seconds": time.time() - started,
        }


def run_clean_control(times: int = 6) -> list[dict]:
    rows = []
    for i in range(times):
        started = time.time()
        env = {**os.environ, "PYTHONPATH": str(REPO), "PYTHONDONTWRITEBYTECODE": "1"}
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "unittest",
                f"{GATE}.test_A1_preview_11",
                f"{R2}.test_R2_B1g_full_set_hold_false",
                f"{WF}.test_A_two_and_C1b_Y_no_auto",
                f"{R3}.test_R3_F1a_human_merger_passes",
                "-q",
            ],
            cwd=REPO,
            capture_output=True,
            text=True,
            env=env,
        )
        rows.append(
            {
                "n": i + 1,
                "returncode": result.returncode,
                "seconds": time.time() - started,
                "ts": time.time(),
                "stderr": (result.stderr or "")[-400:] if result.returncode else "",
            }
        )
        if result.returncode != 0:
            break
    return rows


def run_all(workers: int = 4) -> list[dict]:
    os.nice(15)
    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(run_one, mutant): mutant[0] for mutant in MUTANTS}
        for fut in as_completed(futs):
            results.append(fut.result())
    results.sort(key=lambda row: row["id"])
    return results
