"""Edition overlay allow-list for Karen-signed Review sign-off files.

Only ``signoff/review/<slug>.json`` and ``signoff/review/<slug>.sig`` may
pass, and only when the same PR adds or changes the matching slug JSON
under the editions directory. Isolating tests are named so a
drop-one mutant of a single inventory member changes only that result.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.chiefs_narrative import edition_overlay

SLUG_331 = "2026-11-09-2011"
_EDITION_ROOT = "data/" + "narrative" + "_editions"
_LIVE_NARRATIVE = "data/" + "narrative" + ".json"
EDITION_331 = f"{_EDITION_ROOT}/{SLUG_331}.json"
SIGNOFF_JSON_331 = f"signoff/review/{SLUG_331}.json"
SIGNOFF_SIG_331 = f"signoff/review/{SLUG_331}.sig"
EDITION_BODY = '{"slug":"%s","lastGameReview":{"result":"W"}}\n' % SLUG_331
NARRATIVE = '{"slug":"%s","xsandos":[]}\n' % SLUG_331

ADVERSARIAL_ROWS = (
    {
        "id": "331_pair_with_edition",
        "files": {
            EDITION_331: EDITION_BODY,
            SIGNOFF_JSON_331: '{"slug":"%s","verdict":"PASS"}\n' % SLUG_331,
            SIGNOFF_SIG_331: "c2ln\n",
        },
        "head": "pass",
        "develop": "FLAG",
        "note": "#331 file pair alongside its edition must overlay",
    },
    {
        "id": "other_signoff_path",
        "files": {
            EDITION_331: EDITION_BODY,
            f"signoff/other/{SLUG_331}.json": "{}\n",
        },
        "head": "FLAG",
        "develop": "FLAG",
        "note": "any other signoff/ path",
    },
    {
        "id": "other_extension",
        "files": {
            EDITION_331: EDITION_BODY,
            f"signoff/review/{SLUG_331}.txt": "nope\n",
        },
        "head": "FLAG",
        "develop": "FLAG",
        "note": "other extensions",
    },
    {
        "id": "extra_file_beside_valid_pair",
        "files": {
            EDITION_331: EDITION_BODY,
            SIGNOFF_JSON_331: '{"slug":"%s","verdict":"PASS"}\n' % SLUG_331,
            SIGNOFF_SIG_331: "c2ln\n",
            f"signoff/review/{SLUG_331}.txt": "extra\n",
        },
        "head": "FLAG",
        "develop": "FLAG",
        "note": "extra file next to the valid json+sig pair",
    },
    {
        "id": "slug_mismatch",
        "files": {
            EDITION_331: EDITION_BODY,
            "signoff/review/2026-10-05-0937.json": "{}\n",
            "signoff/review/2026-10-05-0937.sig": "c2ln\n",
        },
        "head": "FLAG",
        "develop": "FLAG",
        "note": "slug that doesn't match an edition changed in the PR",
    },
    {
        "id": "nested_path",
        "files": {
            EDITION_331: EDITION_BODY,
            f"signoff/review/nested/{SLUG_331}.json": "{}\n",
        },
        "head": "FLAG",
        "develop": "FLAG",
        "note": "nested paths",
    },
    {
        "id": "dotdot_traversal",
        "files": {
            EDITION_331: EDITION_BODY,
            "signoff/review/../review/2026-11-09-2011.json": "{}\n",
        },
        "head": "FLAG",
        "develop": "FLAG",
        "note": "../ traversal",
    },
    {
        "id": "case_variant",
        "files": {
            EDITION_331: EDITION_BODY,
            f"Signoff/review/{SLUG_331}.json": "{}\n",
        },
        "head": "FLAG",
        "develop": "FLAG",
        "note": "case variants",
    },
    {
        "id": "unicode_lookalike",
        "files": {
            EDITION_331: EDITION_BODY,
            "signoff/review/2026-11-09-２０１１.json": "{}\n",
        },
        "head": "FLAG",
        "develop": "FLAG",
        "note": "Unicode lookalikes (fullwidth digits)",
    },
    {
        "id": "cf_zwsp",
        "files": {
            EDITION_331: EDITION_BODY,
            f"signoff/review/{SLUG_331}\u200b.json": "{}\n",
        },
        "head": "FLAG",
        "develop": "FLAG",
        "note": "Cf characters (ZWSP)",
    },
    {
        "id": "trailing_slash",
        "files": {
            EDITION_331: EDITION_BODY,
            f"signoff/review/{SLUG_331}.json/": "{}\n",
        },
        "head": "FLAG",
        "develop": "FLAG",
        "note": "trailing slash",
    },
    {
        "id": "tools_facts_still_blocked",
        "files": {
            EDITION_331: EDITION_BODY,
            "tools/chiefs_narrative/facts.py": "broken = True\n",
        },
        "head": "FLAG",
        "develop": "FLAG",
        "note": "code path still refused",
    },
    {
        "id": "signoff_without_edition",
        "files": {
            SIGNOFF_JSON_331: "{}\n",
            SIGNOFF_SIG_331: "c2ln\n",
        },
        "head": "FLAG",
        "develop": "FLAG",
        "note": "signoff pair with no matching edition change",
    },
)


def _overlay(files: dict[str, str]) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "src"
        dst = Path(tmp) / "dst"
        narrative = Path(_LIVE_NARRATIVE)
        (dst / narrative).parent.mkdir(parents=True, exist_ok=True)
        (dst / narrative).write_text(NARRATIVE, encoding="utf-8")
        for rel, content in files.items():
            dest = src / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
        return edition_overlay.overlay_edition_data(src, dst)


def _classify(files: dict[str, str]) -> str:
    edition_slugs = frozenset(
        Path(path).stem
        for path in files
        if path.startswith(_EDITION_ROOT + "/") and path.endswith(".json")
    )
    raw_paths = [
        path
        for path in files
        if "/../" in path or path.endswith("/") or "\u200b" in path
    ]
    if raw_paths:
        allowed = all(
            edition_overlay.is_allowed_rel(path, edition_slugs=edition_slugs)
            for path in files
        )
        return "pass" if allowed else "FLAG"
    try:
        _overlay(files)
        return "pass"
    except ValueError as exc:
        if "outside the edition allow-list" in str(exc):
            return "FLAG"
        raise


class OverlaySignoffAllowList(unittest.TestCase):
    def test_331_pair_passes_alongside_edition(self):
        copied = _overlay(
            {
                EDITION_331: EDITION_BODY,
                SIGNOFF_JSON_331: '{"slug":"%s"}\n' % SLUG_331,
                SIGNOFF_SIG_331: "c2ln\n",
            }
        )
        self.assertEqual(copied, 3)

    def test_allow_signoff_json(self):
        copied = _overlay(
            {EDITION_331: EDITION_BODY, SIGNOFF_JSON_331: "{}\n"}
        )
        self.assertEqual(copied, 2)

    def test_allow_signoff_sig(self):
        copied = _overlay(
            {EDITION_331: EDITION_BODY, SIGNOFF_SIG_331: "c2ln\n"}
        )
        self.assertEqual(copied, 2)

    def test_refuse_other_signoff_path(self):
        with self.assertRaises(ValueError) as ctx:
            _overlay(
                {
                    EDITION_331: EDITION_BODY,
                    f"signoff/preview/{SLUG_331}.json": "{}\n",
                }
            )
        self.assertIn("outside the edition allow-list", str(ctx.exception))
        self.assertIn("signoff/preview/", str(ctx.exception))

    def test_refuse_other_extension(self):
        with self.assertRaises(ValueError) as ctx:
            _overlay(
                {
                    EDITION_331: EDITION_BODY,
                    f"signoff/review/{SLUG_331}.pem": "nope\n",
                }
            )
        self.assertIn(f"signoff/review/{SLUG_331}.pem", str(ctx.exception))

    def test_refuse_extra_file_beside_valid_pair(self):
        extra = f"signoff/review/{SLUG_331}.txt"
        with self.assertRaises(ValueError) as ctx:
            _overlay(
                {
                    EDITION_331: EDITION_BODY,
                    SIGNOFF_JSON_331: '{"slug":"%s"}\n' % SLUG_331,
                    SIGNOFF_SIG_331: "c2ln\n",
                    extra: "extra\n",
                }
            )
        self.assertIn("outside the edition allow-list", str(ctx.exception))
        self.assertIn(extra, str(ctx.exception))

    def test_refuse_slug_mismatch(self):
        with self.assertRaises(ValueError) as ctx:
            _overlay(
                {
                    EDITION_331: EDITION_BODY,
                    "signoff/review/2026-10-05-0937.json": "{}\n",
                }
            )
        self.assertIn("2026-10-05-0937.json", str(ctx.exception))

    def test_refuse_nested_path(self):
        with self.assertRaises(ValueError) as ctx:
            _overlay(
                {
                    EDITION_331: EDITION_BODY,
                    f"signoff/review/extra/{SLUG_331}.json": "{}\n",
                }
            )
        self.assertIn("signoff/review/extra/", str(ctx.exception))

    def test_refuse_dotdot_traversal(self):
        posix = "signoff/review/../review/2026-11-09-2011.json"
        self.assertFalse(edition_overlay._posix_is_clean(posix))
        self.assertFalse(edition_overlay.is_allowed_rel(posix))
        with patch.object(
            edition_overlay,
            "_src_additions",
            return_value=[("A", EDITION_331, ""), ("A", posix, "")],
        ):
            with self.assertRaises(ValueError) as ctx:
                _overlay({EDITION_331: EDITION_BODY})
        self.assertIn("outside the edition allow-list", str(ctx.exception))
        self.assertIn("..", str(ctx.exception))

    def test_refuse_case_variant(self):
        posix = f"Signoff/review/{SLUG_331}.json"
        self.assertFalse(edition_overlay.is_allowed_rel(Path(posix)))
        with self.assertRaises(ValueError) as ctx:
            _overlay({EDITION_331: EDITION_BODY, posix: "{}\n"})
        self.assertIn("Signoff/review/", str(ctx.exception))

    def test_refuse_unicode_lookalike(self):
        posix = "signoff/review/2026-11-09-２０１１.json"
        self.assertIsNone(edition_overlay._signoff_slug(posix))
        self.assertFalse(edition_overlay._posix_is_clean(posix))
        self.assertFalse(edition_overlay.is_allowed_rel(posix))
        with self.assertRaises(ValueError) as ctx:
            _overlay({EDITION_331: EDITION_BODY, posix: "{}\n"})
        self.assertIn("outside the edition allow-list", str(ctx.exception))

    def test_refuse_cf_character(self):
        posix = f"signoff/review/{SLUG_331}\u200b.json"
        self.assertTrue(edition_overlay._has_cf_or_control(posix))
        self.assertFalse(edition_overlay._posix_is_clean(posix))
        self.assertFalse(edition_overlay.is_allowed_rel(posix))
        with self.assertRaises(ValueError) as ctx:
            _overlay({EDITION_331: EDITION_BODY, posix: "{}\n"})
        self.assertIn("outside the edition allow-list", str(ctx.exception))

    def test_refuse_trailing_slash(self):
        posix = f"signoff/review/{SLUG_331}.json/"
        self.assertTrue(edition_overlay._has_trailing_slash(posix))
        self.assertFalse(edition_overlay._posix_is_clean(posix))
        self.assertFalse(edition_overlay.is_allowed_rel(posix))
        with patch.object(
            edition_overlay,
            "_src_additions",
            return_value=[("A", EDITION_331, ""), ("A", posix, "")],
        ):
            with self.assertRaises(ValueError) as ctx:
                _overlay({EDITION_331: EDITION_BODY})
        self.assertIn("outside the edition allow-list", str(ctx.exception))

    def test_refuse_signoff_without_matching_edition(self):
        with self.assertRaises(ValueError) as ctx:
            _overlay({SIGNOFF_JSON_331: "{}\n", SIGNOFF_SIG_331: "c2ln\n"})
        self.assertIn(SIGNOFF_JSON_331, str(ctx.exception))

    def test_is_allowed_rel_signoff_fail_closed_without_edition_slugs(self):
        self.assertFalse(
            edition_overlay.is_allowed_rel(Path(SIGNOFF_JSON_331))
        )
        self.assertTrue(
            edition_overlay.is_allowed_rel(
                Path(SIGNOFF_JSON_331),
                edition_slugs=frozenset({SLUG_331}),
            )
        )
        self.assertFalse(
            edition_overlay.is_allowed_rel(
                Path(SIGNOFF_JSON_331),
                edition_slugs=frozenset({"2026-10-05-0937"}),
            )
        )

    def test_changed_edition_slugs_only_add_modify(self):
        entries = [
            ("A", EDITION_331, ""),
            ("D", f"{_EDITION_ROOT}/2026-10-05-0937.json", ""),
            ("M", _LIVE_NARRATIVE, ""),
        ]
        self.assertEqual(
            edition_overlay.changed_edition_slugs(entries),
            frozenset({SLUG_331}),
        )

    def test_workflow_paths_include_signoff_review(self):
        qa = (
            Path(__file__).resolve().parents[2]
            / ".github"
            / "workflows"
            / "edition-qa.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("signoff/review/**", qa)
        self.assertIn(
            "git add -A -- data public/images/narrative signoff/review", qa
        )

    def test_adversarial_table_head(self):
        for row in ADVERSARIAL_ROWS:
            with self.subTest(row["id"]):
                self.assertEqual(_classify(row["files"]), row["head"], row)

    def test_head_not_weaker_than_develop(self):
        for row in ADVERSARIAL_ROWS:
            with self.subTest(row["id"]):
                if row["id"] == "331_pair_with_edition":
                    self.assertEqual(_classify(row["files"]), "pass")
                    continue
                if row["develop"] == "FLAG":
                    self.assertEqual(_classify(row["files"]), "FLAG", row)


if __name__ == "__main__":
    unittest.main()
