#!/usr/bin/env python3
"""On-disk mutants for the no-synthesized-media tests. Edit, run, revert."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable
BUILT = (
    "tools.tests.test_imageless_hero.ImagelessLiveEdition.test_every_built_page_omits_synthesized_media"
)
PROD = (
    "tools.tests.test_locker_room.LockerRoomVerification."
    "test_production_callers_resolve_author_and_verify_retweet"
)


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


def _apply(mutant: dict) -> tuple[list[tuple[Path, str]], list[Path]]:
    backups: list[tuple[Path, str]] = []
    created: list[Path] = []
    edits = list(mutant.get("edits") or [])
    if "path" in mutant and "old" in mutant:
        edits.append(
            {"path": mutant["path"], "old": mutant["old"], "new": mutant["new"]}
        )
    for edit in edits:
        path = Path(edit["path"])
        backups.append((path, path.read_text(encoding="utf-8")))
        _mutate(path, edit["old"], edit["new"])
    for item in mutant.get("create") or []:
        path = Path(item["path"])
        if path.exists():
            raise AssertionError(f"create path already exists: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(item["text"], encoding="utf-8")
        created.append(path)
    return backups, created


def _revert(backups: list[tuple[Path, str]], created: list[Path]) -> None:
    for path in created:
        if path.exists():
            path.unlink()
    for path, text in backups:
        _restore(path, text)


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
            "tests": [BUILT],
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
            "tests": [BUILT],
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
            "tests": [BUILT],
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
            "tests": [BUILT],
        },
        {
            "id": "skip-page-directory",
            "desc": "have the test skip a page directory",
            "path": ROOT / "tools" / "tests" / "test_imageless_hero.py",
            "old": "            for path in pages:",
            "new": (
                "            for path in pages:\n"
                "                if path.name == \"404.html\":\n"
                "                    continue"
            ),
            "tests": [BUILT],
        },
        {
            "id": "network-media-call",
            "desc": "re-enable a network media call in the generator",
            "path": ROOT / "tools" / "chiefs_narrative" / "hero_media.py",
            "old": '    narrative["heroMediaId"] = FALLBACK_ID\n    narrative["heroMediaVerifiedAt"] = ""',
            "new": (
                "    import requests\n"
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
        {
            "id": "renamed-svg-in-slot",
            "desc": "drawn SVG with a renamed class and no caption inside person-media",
            "path": ROOT / "layouts" / "partials" / "person-media.html",
            "old": "{{- /* Person thumbnails render nothing until a credited X photograph exists. */ -}}",
            "new": (
                '<span class="person-media"><svg class="blob-standin" '
                'viewBox="0 0 48 48"></svg></span>'
            ),
            "tests": [BUILT],
        },
        {
            "id": "hotlinked-pbs-img",
            "desc": "hotlinked pbs.twimg.com img in person-media.html",
            "path": ROOT / "layouts" / "partials" / "person-media.html",
            "old": "{{- /* Person thumbnails render nothing until a credited X photograph exists. */ -}}",
            "new": '<img src="https://pbs.twimg.com/media/hotlink.jpg" alt="">',
            "tests": [BUILT],
        },
        {
            "id": "og-image-illustration",
            "desc": "og:image pointing at an illustration",
            "path": ROOT / "layouts" / "_default" / "baseof.html",
            "old": '<meta property="og:image" content="{{ "og.png" | absURL }}" />',
            "new": (
                '<meta property="og:image" '
                'content="{{ "images/hero-scenes/preview-fallback.svg" | absURL }}" />'
            ),
            "tests": [BUILT],
        },
        {
            "id": "scanner-first-20",
            "desc": "scanner limited to [:20] pages",
            "path": ROOT / "tools" / "tests" / "test_imageless_hero.py",
            "old": 'pages = sorted(path for path in dest.rglob("*.html") if path.is_file())',
            "new": (
                'pages = sorted(path for path in dest.rglob("*.html") '
                "if path.is_file())[:20]"
            ),
            "tests": [BUILT],
        },
        {
            "id": "resolve-author-default-none",
            "desc": "resolve_author default returns None",
            "path": ROOT / "tools" / "chiefs_narrative" / "locker_room.py",
            "old": (
                "    status_id = status_id_from_url(url)\n"
                "    fetch = fetch_syndication_author if syndication_fetch is None else syndication_fetch\n"
            ),
            "new": (
                "    if syndication_fetch is None:\n"
                "        return None\n"
                "    status_id = status_id_from_url(url)\n"
                "    fetch = syndication_fetch\n"
            ),
            "tests": [PROD],
        },
        {
            "id": "verify-retweet-default-false",
            "desc": "verify_retweet default returns False",
            "path": ROOT / "tools" / "chiefs_narrative" / "locker_room.py",
            "old": (
                "    fetch = fetch_syndication if syndication_payload_fetch is None else syndication_payload_fetch\n"
            ),
            "new": (
                "    if syndication_payload_fetch is None:\n"
                "        return False\n"
                "    fetch = syndication_payload_fetch\n"
            ),
            "tests": [PROD],
        },
        {
            "id": "object-svg-in-slot",
            "desc": "an <object> svg in person-media",
            "path": ROOT / "layouts" / "partials" / "person-media.html",
            "old": "{{- /* Person thumbnails render nothing until a credited X photograph exists. */ -}}",
            "new": (
                '<span class="person-media">'
                '<object data="stand-in.svg" type="image/svg+xml"></object>'
                "</span>"
            ),
            "tests": [BUILT],
        },
        {
            "id": "embed-svg-in-slot",
            "desc": "an <embed> svg",
            "path": ROOT / "layouts" / "partials" / "person-media.html",
            "old": "{{- /* Person thumbnails render nothing until a credited X photograph exists. */ -}}",
            "new": (
                '<span class="person-media">'
                '<embed src="stand-in.svg" type="image/svg+xml">'
                "</span>"
            ),
            "tests": [BUILT],
        },
        {
            "id": "object-data-uri-svg",
            "desc": 'an <object data="data:image/svg+xml,...">',
            "path": ROOT / "layouts" / "partials" / "person-media.html",
            "old": "{{- /* Person thumbnails render nothing until a credited X photograph exists. */ -}}",
            "new": (
                '<span class="person-media">'
                '<object data="data:image/svg+xml,'
                "<svg xmlns='http://www.w3.org/2000/svg'></svg>\"></object>"
                "</span>"
            ),
            "tests": [BUILT],
        },
        {
            "id": "background-url-shorthand",
            "desc": "the background:url() shorthand in an inline style",
            "path": ROOT / "layouts" / "partials" / "person-media.html",
            "old": "{{- /* Person thumbnails render nothing until a credited X photograph exists. */ -}}",
            "new": (
                '<span class="person-media" '
                'style="background:url(stand-in.svg)"></span>'
            ),
            "tests": [BUILT],
        },
        {
            "id": "canvas-in-slot",
            "desc": "a <canvas>",
            "path": ROOT / "layouts" / "partials" / "person-media.html",
            "old": "{{- /* Person thumbnails render nothing until a credited X photograph exists. */ -}}",
            "new": (
                '<span class="person-media">'
                '<canvas width="48" height="48"></canvas>'
                "</span>"
            ),
            "tests": [BUILT],
        },
        {
            "id": "twitter-image-svg",
            "desc": "twitter:image pointed at an svg",
            "path": ROOT / "layouts" / "_default" / "baseof.html",
            "old": '<meta name="twitter:image" content="{{ "og.png" | absURL }}" />',
            "new": (
                '<meta name="twitter:image" '
                'content="{{ "images/hero-scenes/preview-fallback.svg" | absURL }}" />'
            ),
            "tests": [BUILT],
        },
        {
            "id": "og-image-illustration-path",
            "desc": "og:image pointed at an illustration",
            "path": ROOT / "layouts" / "_default" / "baseof.html",
            "old": '<meta property="og:image" content="{{ "og.png" | absURL }}" />',
            "new": (
                '<meta property="og:image" '
                'content="{{ "images/illustrations/og.png" | absURL }}" />'
            ),
            "tests": [BUILT],
        },
        {
            "id": "text-only-stray-node",
            "desc": "a text-only stray node in a slot",
            "path": ROOT / "layouts" / "partials" / "person-media.html",
            "old": "{{- /* Person thumbnails render nothing until a credited X photograph exists. */ -}}",
            "new": '<span class="person-media">drawn stand-in</span>',
            "tests": [BUILT],
        },
        {
            "id": "renamed-wrapper-svg",
            "desc": "renamed-wrapper svg at the call site",
            "path": ROOT / "layouts" / "partials" / "narrative-edition.html",
            "old": (
                '<div class="playmaker">{{ partial "person-media.html" '
                '(dict "name" .name "site" $site) }}'
            ),
            "new": (
                '<div class="playmaker"><span class="playmaker__art">'
                '<svg viewBox="0 0 48 48"></svg></span>'
                '{{ partial "person-media.html" (dict "name" .name "site" $site) }}'
            ),
            "tests": [BUILT],
        },
        {
            "id": "sibling-pbs-srcset",
            "desc": "sibling pbs srcset",
            "path": ROOT / "layouts" / "partials" / "narrative-edition.html",
            "old": (
                '<div class="playmaker">{{ partial "person-media.html" '
                '(dict "name" .name "site" $site) }}'
            ),
            "new": (
                '<div class="playmaker">'
                '<img class="playmaker__pic" '
                'srcset="https://pbs.twimg.com/media/x.jpg 1x" alt="">'
                '{{ partial "person-media.html" (dict "name" .name "site" $site) }}'
            ),
            "tests": [BUILT],
        },
        {
            "id": "person-art-partial",
            "desc": "a new person-art.html partial",
            "path": ROOT / "layouts" / "partials" / "narrative-edition.html",
            "old": (
                '<div class="playmaker">{{ partial "person-media.html" '
                '(dict "name" .name "site" $site) }}'
            ),
            "new": (
                '<div class="playmaker">'
                '{{ partial "person-media.html" (dict "name" .name "site" $site) }}'
                '{{ partial "person-art.html" . }}'
            ),
            "create": [
                {
                    "path": ROOT / "layouts" / "partials" / "person-art.html",
                    "text": (
                        '<svg class="person-art" viewBox="0 0 48 48"></svg>\n'
                    ),
                }
            ],
            "tests": [BUILT],
        },
        {
            "id": "style-block-background-url",
            "desc": "a <style> block with background url",
            "path": ROOT / "layouts" / "partials" / "narrative-edition.html",
            "old": "{{/* Heroes render nothing: no silhouette, illustration, or hotlinked photo. */}}",
            "new": (
                "{{/* Heroes render nothing: no silhouette, illustration, or hotlinked photo. */}}"
                "<style>.playmaker{background:url(stand-in.svg)}</style>"
            ),
            "tests": [BUILT],
        },
        {
            "id": "css-content-url",
            "desc": "content:url in editorial-layout.css",
            "path": ROOT / "public" / "css" / "editorial-layout.css",
            "old": "  .nrt-report__list--personnel li { grid-template-columns: 1fr; }\n}",
            "new": (
                "  .nrt-report__list--personnel li { grid-template-columns: 1fr; }\n"
                "}\n"
                ".playmaker::before{content:url(/images/hero-scenes/x.svg)}"
            ),
            "tests": [BUILT],
        },
        {
            "id": "css-list-style-image",
            "desc": "list-style-image in editorial-layout.css",
            "path": ROOT / "public" / "css" / "editorial-layout.css",
            "old": "  .nrt-report__list--personnel li { grid-template-columns: 1fr; }\n}",
            "new": (
                "  .nrt-report__list--personnel li { grid-template-columns: 1fr; }\n"
                "}\n"
                ".roster-people{list-style-image:url(/images/hero-scenes/x.svg)}"
            ),
            "tests": [BUILT],
        },
        {
            "id": "jsonld-og-svg",
            "desc": "$og in seo-jsonld.html pointed at an svg",
            "path": ROOT / "layouts" / "partials" / "seo-jsonld.html",
            "old": '{{- $og := "og.png" | absURL -}}',
            "new": '{{- $og := "images/hero-scenes/preview-fallback.svg" | absURL -}}',
            "tests": [BUILT],
        },
        {
            "id": "itemprop-image-svg",
            "desc": "meta itemprop=image pointed at an svg",
            "path": ROOT / "layouts" / "_default" / "baseof.html",
            "old": '<meta property="og:image" content="{{ "og.png" | absURL }}" />',
            "new": (
                '<meta property="og:image" content="{{ "og.png" | absURL }}" />'
                '<meta itemprop="image" '
                'content="{{ "images/hero-scenes/preview-fallback.svg" | absURL }}" />'
            ),
            "tests": [BUILT],
        },
    ]

    killed = 0
    for mutant in mutants:
        backups: list[tuple[Path, str]] = []
        created: list[Path] = []
        try:
            backups, created = _apply(mutant)
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
            _revert(backups, created)

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
