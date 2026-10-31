#!/usr/bin/env python3
"""On-disk mutants for the no-synthesized-media tests. Edit, run, revert."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable


def _run(tests: list[str]) -> tuple[int, str]:
    result = subprocess.run(
        [PYTHON, "-m", "unittest", *tests],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    out = (result.stdout or "") + (result.stderr or "")
    return result.returncode, out


def _mutate(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise AssertionError(f"anchor missing in {path}: {old!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _restore(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def main() -> int:
    rows = []
    mutants = [
        {
            "id": "silhouette-render",
            "desc": "re-add a silhouette render",
            "path": ROOT / "layouts" / "partials" / "person-media.html",
            "old": "{{- /* Person thumbnails render nothing until a credited X photograph exists. */ -}}",
            "new": (
                '<span class="person-media person-media--silhouette" aria-hidden="true">'
                "<svg viewBox=\"0 0 48 48\"></svg></span>"
            ),
            "tests": [
                "tools.tests.test_imageless_hero.ImagelessLiveEdition.test_every_built_page_omits_synthesized_media"
            ],
        },
        {
            "id": "illustration-partial",
            "desc": "re-add an illustration partial",
            "path": ROOT / "layouts" / "partials" / "narrative-edition.html",
            "old": "{{/* Heroes render nothing: no silhouette, illustration, or hotlinked photo. */}}",
            "new": (
                '<figure class="nrt-event-illustration">'
                "<img alt=\"stadium illustration\"></figure>"
            ),
            "tests": [
                "tools.tests.test_imageless_hero.ImagelessLiveEdition.test_every_built_page_omits_synthesized_media"
            ],
        },
        {
            "id": "not-a-photograph-caption",
            "desc": "uppercase NOT A PHOTOGRAPH in a caption",
            "path": ROOT / "layouts" / "partials" / "narrative-edition.html",
            "old": "{{ with .dek }}<p class=\"nrt-dek\">{{ . }}</p>{{ end }}",
            "new": (
                "{{ with .dek }}<p class=\"nrt-dek\">{{ . }}</p>{{ end }}"
                "<p class=\"nrt-hero-media__caption\">NOT A PHOTOGRAPH</p>"
            ),
            "tests": [
                "tools.tests.test_imageless_hero.ImagelessLiveEdition.test_every_built_page_omits_synthesized_media"
            ],
        },
        {
            "id": "non-narrative-marker",
            "desc": "inject the marker on a non-narrative page only",
            "path": ROOT / "layouts" / "404.html",
            "old": "<p>That page is gone. Continue to <a href=\"{{ \"\" | relURL }}\">The Chiefs Narrative</a>.</p>",
            "new": (
                "<p class=\"person-media--silhouette\">That page is gone. Continue to "
                "<a href=\"{{ \"\" | relURL }}\">The Chiefs Narrative</a>.</p>"
            ),
            "tests": [
                "tools.tests.test_imageless_hero.ImagelessLiveEdition.test_every_built_page_omits_synthesized_media"
            ],
        },
        {
            "id": "skip-page-directory",
            "desc": "have the test skip a page directory",
            "path": ROOT / "tools" / "tests" / "test_imageless_hero.py",
            "old": "            for path in pages:",
            "new": (
                "            for path in pages:\n"
                "                if \"404\" in path.parts:\n"
                "                    continue"
            ),
            "tests": [
                "tools.tests.test_imageless_hero.ImagelessLiveEdition.test_scanner_walks_every_html_page_without_skip"
            ],
        },
        {
            "id": "network-media-call",
            "desc": "re-enable a network media call in the generator",
            "path": ROOT / "tools" / "chiefs_narrative" / "hero_media.py",
            "old": '    narrative["heroMediaId"] = FALLBACK_ID\n    narrative["heroMediaVerifiedAt"] = ""',
            "new": (
                '    import requests\n'
                '    requests.get("https://api.fxtwitter.com/status/1", timeout=1)\n'
                '    narrative["heroMediaId"] = FALLBACK_ID\n'
                '    narrative["heroMediaVerifiedAt"] = ""'
            ),
            "tests": [
                "tools.tests.test_imageless_hero.ImagelessLiveEdition.test_generator_and_edition_produce_no_media_without_network"
            ],
        },
        {
            "id": "hero-media-id",
            "desc": "generator writing a heroMediaId other than none",
            "path": ROOT / "tools" / "chiefs_narrative" / "hero_media.py",
            "old": 'FALLBACK_ID = "none"',
            "new": 'FALLBACK_ID = "article-illustration"',
            "tests": [
                "tools.tests.test_imageless_hero.ImagelessLiveEdition.test_generator_default_and_attach_fallback_write_none",
                "tools.tests.test_imageless_hero.ImagelessLiveEdition.test_generator_and_edition_produce_no_media_without_network",
            ],
        },
    ]

    killed = 0
    for mutant in mutants:
        path: Path = mutant["path"]
        original = path.read_text(encoding="utf-8")
        try:
            _mutate(path, mutant["old"], mutant["new"])
            code, out = _run(mutant["tests"])
            status = "KILLED" if code != 0 else "SURVIVED"
            if code != 0:
                killed += 1
            last = ""
            for line in out.splitlines()[::-1]:
                if line.strip():
                    last = line.strip()
                    break
            rows.append((mutant["id"], mutant["desc"], status, last[:160]))
            print(f"{mutant['id']}: {status}", flush=True)
            if last:
                print(f"  {last}", flush=True)
        finally:
            _restore(path, original)

    print()
    print("| id | description | result | evidence |")
    print("| --- | --- | --- | --- |")
    for mid, desc, status, evidence in rows:
        safe = evidence.replace("|", "\\|")
        print(f"| `{mid}` | {desc} | **{status}** | {safe} |")
    print()
    print(f"killed {killed} / {len(mutants)}")
    return 0 if killed == len(mutants) else 1


if __name__ == "__main__":
    raise SystemExit(main())
