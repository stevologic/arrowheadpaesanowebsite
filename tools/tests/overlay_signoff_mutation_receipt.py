#!/usr/bin/env python3
"""Drop-one mutants of the overlay signoff allow-list vs repository tests."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OVERLAY = ROOT / "tools" / "chiefs_narrative" / "edition_overlay.py"
PYTHON = sys.executable
N = 6
TEST_MOD = "tools.tests.test_edition_overlay_signoff.OverlaySignoffAllowList"

MEMBERS: list[dict] = [
    {
        "id": "signoff_json_ext",
        "old": '_SIGNOFF_JSON_EXT = "json"',
        "new": '_SIGNOFF_JSON_EXT = "zzz"',
        "test": "test_allow_signoff_json",
        "fixture": "signoff/review/<slug>.json with matching edition",
    },
    {
        "id": "signoff_sig_ext",
        "old": '_SIGNOFF_SIG_EXT = "sig"',
        "new": '_SIGNOFF_SIG_EXT = "zzz"',
        "test": "test_allow_signoff_sig",
        "fixture": "signoff/review/<slug>.sig with matching edition",
    },
    {
        "id": "signoff_dir",
        "old": '_SIGNOFF_DIR = "signoff/review"',
        "new": '_SIGNOFF_DIR = "signoff/other"',
        "test": "test_331_pair_passes_alongside_edition",
        "fixture": "#331 pair under signoff/review/",
    },
    {
        "id": "allowed_json_and_sig_together",
        "old": "    sig_match = _SIGNOFF_SIG_RE.fullmatch(posix)\n    if sig_match:\n        return sig_match.group(1)\n",
        "new": "",
        "test": "test_331_pair_passes_alongside_edition",
        "fixture": "matching slug .json AND .sig together with edition",
    },
    {
        "id": "matching_edition_slug",
        "old": "        return signoff_slug in (edition_slugs or frozenset())",
        "new": "        return True",
        "test": "test_refuse_slug_mismatch",
        "fixture": "signoff slug != edition changed in the PR",
    },
    {
        "id": "other_signoff_path",
        "old": '    rf"^{re.escape(_SIGNOFF_DIR)}/({_EDITION_SLUG_ASCII})\\.{_SIGNOFF_JSON_EXT}$"\n',
        "new": '    rf"^signoff/[^/]+/({_EDITION_SLUG_ASCII})\\.{_SIGNOFF_JSON_EXT}$"\n',
        "test": "test_refuse_other_signoff_path",
        "fixture": "signoff/preview/<slug>.json",
    },
    {
        "id": "other_extension",
        "old": '_SIGNOFF_JSON_EXT = "json"',
        "new": '_SIGNOFF_JSON_EXT = "pem"',
        "test": "test_refuse_other_extension",
        "fixture": "signoff/review/<slug>.pem",
    },
    {
        "id": "extra_file_beside_valid_pair",
        "old": '    rf"^{re.escape(_SIGNOFF_DIR)}/({_EDITION_SLUG_ASCII})\\.{_SIGNOFF_JSON_EXT}$"\n',
        "new": '    rf"^{re.escape(_SIGNOFF_DIR)}/({_EDITION_SLUG_ASCII})\\.[A-Za-z0-9]+$"\n',
        "test": "test_refuse_extra_file_beside_valid_pair",
        "fixture": "valid json+sig plus signoff/review/<slug>.txt",
    },
    {
        "id": "nested_path",
        "old": '    rf"^{re.escape(_SIGNOFF_DIR)}/({_EDITION_SLUG_ASCII})\\.{_SIGNOFF_JSON_EXT}$"\n',
        "new": '    rf"^{re.escape(_SIGNOFF_DIR)}/.*/({_EDITION_SLUG_ASCII})\\.{_SIGNOFF_JSON_EXT}$"\n',
        "test": "test_refuse_nested_path",
        "fixture": "signoff/review/extra/<slug>.json",
    },
    {
        "id": "dotdot",
        "old": '    if any(part in {"", ".", ".."} for part in parts):\n        return False\n',
        "new": "",
        "test": "test_refuse_dotdot_traversal",
        "fixture": "signoff/review/../review/<slug>.json",
    },
    {
        "id": "case_variant",
        "old": "_SIGNOFF_JSON_RE = re.compile(\n    rf\"^{re.escape(_SIGNOFF_DIR)}/({_EDITION_SLUG_ASCII})\\.{_SIGNOFF_JSON_EXT}$\"\n)",
        "new": "_SIGNOFF_JSON_RE = re.compile(\n    rf\"^{re.escape(_SIGNOFF_DIR)}/({_EDITION_SLUG_ASCII})\\.{_SIGNOFF_JSON_EXT}$\",\n    re.I,\n)",
        "test": "test_refuse_case_variant",
        "fixture": "Signoff/review/<slug>.json",
    },
    {
        "id": "unicode_lookalike",
        "old": '_EDITION_SLUG_ASCII = r"[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{4}"',
        "new": r'_EDITION_SLUG_ASCII = r"\d{4}-\d{2}-\d{2}-\d{4}"',
        "test": "test_refuse_unicode_lookalike",
        "fixture": "fullwidth digits in slug",
    },
    {
        "id": "cf_character",
        "old": 'def _has_cf_or_control(text: str) -> bool:\n    """True when any code point is a Unicode control or format (Cf) character."""\n    return any(unicodedata.category(char).startswith("C") for char in text)\n',
        "new": 'def _has_cf_or_control(text: str) -> bool:\n    return False\n',
        "test": "test_refuse_cf_character",
        "fixture": "ZWSP in signoff filename",
    },
    {
        "id": "trailing_slash",
        "old": 'def _has_trailing_slash(raw: str) -> bool:\n    """True when the git path still has a trailing slash (Path.as_posix strips it)."""\n    return raw.endswith("/") or raw.endswith("\\\\")\n',
        "new": 'def _has_trailing_slash(raw: str) -> bool:\n    return False\n',
        "test": "test_refuse_trailing_slash",
        "fixture": "signoff/review/<slug>.json/",
    },
    {
        "id": "require_changed_edition",
        "old": "    for status, path, dest in entries:\n        if status[:1] not in {\"A\", \"M\"}:\n            continue\n",
        "new": "    for status, path, dest in entries:\n        if False:\n            continue\n",
        "test": "test_changed_edition_slugs_only_add_modify",
        "fixture": "deleted edition json does not authorize signoff",
    },
    {
        "id": "fail_closed_no_slugs",
        "old": "        return signoff_slug in (edition_slugs or frozenset())",
        "new": "        return signoff_slug in (edition_slugs or frozenset({signoff_slug}))",
        "test": "test_is_allowed_rel_signoff_fail_closed_without_edition_slugs",
        "fixture": "is_allowed_rel(signoff) without edition_slugs",
    },
]

COARSE = [
    {
        "id": "allow-all-signoff",
        "old": "    signoff_slug = _signoff_slug(posix)\n    if signoff_slug is not None:\n        return signoff_slug in (edition_slugs or frozenset())\n",
        "new": "    signoff_slug = _signoff_slug(posix)\n    if posix.startswith(\"signoff/\"):\n        return True\n    if signoff_slug is not None:\n        return signoff_slug in (edition_slugs or frozenset())\n",
    },
    {
        "id": "drop-signoff-allow",
        "old": "    signoff_slug = _signoff_slug(posix)\n    if signoff_slug is not None:\n        return signoff_slug in (edition_slugs or frozenset())\n",
        "new": "",
    },
    {
        "id": "ignore-edition-slugs",
        "old": "        return signoff_slug in (edition_slugs or frozenset())",
        "new": "        return True",
    },
    {
        "id": "noop-clean",
        "old": "def _posix_is_clean(posix: str) -> bool:\n    \"\"\"Refuse traversal, trailing slash, controls, Cf, and non-ASCII lookalikes.\"\"\"\n",
        "new": "def _posix_is_clean(posix: str) -> bool:\n    return True\n    \"\"\"Refuse traversal, trailing slash, controls, Cf, and non-ASCII lookalikes.\"\"\"\n",
    },
]


def _run_unittest(selector: str) -> tuple[int, str]:
    proc = subprocess.run(
        [PYTHON, "-m", "unittest", selector, "-v"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    return proc.returncode, (proc.stderr or "") + (proc.stdout or "")


def _parse_fail(out: str) -> str:
    m = re.search(r"AssertionError: (.*?)(?:\n\n|\n-|\n : |\n$)", out, re.S)
    if m:
        return " ".join(m.group(1).split())[:240]
    lines = [ln for ln in out.splitlines() if ln.strip()]
    return " | ".join(lines[-8:])[:240]


def _apply(old: str, new: str) -> None:
    src = OVERLAY.read_text(encoding="utf-8")
    if old not in src:
        raise SystemExit(f"ANCHOR MISSING:\n{old[:200]!r}")
    if src.count(old) != 1:
        raise SystemExit(f"ANCHOR NOT UNIQUE ({src.count(old)}):\n{old[:200]!r}")
    OVERLAY.write_text(src.replace(old, new, 1), encoding="utf-8")


def _restore(original: str) -> None:
    OVERLAY.write_text(original, encoding="utf-8")


def _repeat(selector: str, n: int) -> tuple[int, int, str]:
    passes = fails = 0
    detail = ""
    for _ in range(n):
        rc, out = _run_unittest(selector)
        if rc == 0:
            passes += 1
        else:
            fails += 1
            detail = _parse_fail(out)
    return passes, fails, detail


def main() -> int:
    original = OVERLAY.read_text(encoding="utf-8")
    for member in MEMBERS + COARSE:
        n = original.count(member["old"])
        if n != 1:
            print("ANCHOR BAD", member["id"], n, file=sys.stderr)
            print(member["old"][:180], file=sys.stderr)
            return 2
    rows = []
    coarse_rows = []
    try:
        for member in MEMBERS:
            selector = f"{TEST_MOD}.{member['test']}"
            ctrl_ok, _, _ = _repeat(selector, N)
            _apply(member["old"], member["new"])
            try:
                _, mut_fail, detail = _repeat(selector, N)
            finally:
                _restore(original)
            vacuous = mut_fail == 0
            rows.append(
                {
                    "id": member["id"],
                    "fixture": member["fixture"],
                    "mutant": f"drop {member['id']}",
                    "test": member["test"],
                    "control": f"{ctrl_ok}/{N} pass",
                    "kills": f"{mut_fail}/{N}",
                    "actual": "FAIL" if mut_fail else "PASS",
                    "expected": "FAIL",
                    "detail": detail if mut_fail else "control-like pass",
                    "vacuous": vacuous,
                }
            )
            print(
                f"{'VACUOUS' if vacuous else 'KILLED':8} {member['id']:28} "
                f"{member['test']} {mut_fail}/{N}"
            )

        full = "tools.tests.test_edition_overlay_signoff"
        ctrl_ok, _, _ = _repeat(full, N)
        print(f"I12 control {ctrl_ok}/{N} pass")
        skip_re = re.compile(r"(meta|source.?text|grep_source)", re.I)
        for mutant in COARSE:
            _apply(mutant["old"], mutant["new"])
            try:
                _, mut_fail, detail = _repeat(full, N)
                _, out = _run_unittest(full)
            finally:
                _restore(original)
            names = [
                name
                for _, name in re.findall(r"^(FAIL|ERROR): (\S+)", out, re.M)
                if not skip_re.search(name)
            ]
            coarse_rows.append(
                {
                    "id": mutant["id"],
                    "kills": f"{mut_fail}/{N}",
                    "actual": "FAIL" if mut_fail else "PASS",
                    "expected": "FAIL",
                    "tests": names[:8],
                    "detail": detail,
                    "control": f"{ctrl_ok}/{N} pass",
                }
            )
            print(f"I12 {mutant['id']:24} {mut_fail}/{N} tests={names[:5]}")
    finally:
        _restore(original)

    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/overlay-signoff-receipt")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "class_coverage.json").write_text(
        json.dumps(rows, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "i12_coarse.json").write_text(
        json.dumps(coarse_rows, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# Class-coverage receipt",
        "",
        "| fixture | mutant | repository test | n/6 kills | actual vs expected | vacuous? |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['fixture']} | {row['mutant']} | `{row['test']}` | "
            f"{row['kills']} | {row['actual']} vs {row['expected']} | "
            f"{'yes' if row['vacuous'] else 'no'} |"
        )
    lines += [
        "",
        "# I12 coarse mutants vs repository tests",
        "",
        "| mutant | n/6 kills | actual vs expected | failing tests | detail |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in coarse_rows:
        tests = ", ".join(f"`{name}`" for name in row["tests"][:6]) or "(none)"
        lines.append(
            f"| {row['id']} | {row['kills']} | {row['actual']} vs {row['expected']} | "
            f"{tests} | {row['detail']} |"
        )
    (out_dir / "receipt.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(out_dir / "receipt.md")
    vacuous = [row["id"] for row in rows if row["vacuous"]]
    if vacuous:
        print("VACUOUS MEMBERS", vacuous)
    survived = [row["id"] for row in coarse_rows if row["actual"] != "FAIL"]
    if survived:
        print("I12 SURVIVORS", survived)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
