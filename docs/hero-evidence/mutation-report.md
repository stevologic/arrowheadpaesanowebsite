# Hero mutation report

Evaluated 22 named mutants. Survivors are listed below.

| Mutant | Killer | Result |
| --- | --- | --- |
| week-first-ignores-opponent | week/opponent mismatch must not return the week-8 Denver scene | killed |
| left-helmet-negates-translate | matchup SVGs must keep both helmets inside the viewBox | killed |
| fallback-caption-names-arrowhead | unknown/away fallback caption must not mention Arrowhead | killed |
| drop-extra-neutral-cards | London vs Miami must not resolve as Hard Rock | killed |
| neutral-alt-says-road | neutral-site alt text must not say road venue | killed |
| mexico-city-not-neutral | Mexico City must resolve as neutral, not a road venue | killed |
| levis-stadium-not-mapped | Levi's Stadium Super Bowl must be a neutral venue | killed |
| match-schedule-ignores-season | postseason DEN with no venue must not steal week-8 Denver | killed |
| ignore-sponsored-flag | sponsored=true in pin metadata must be rejected | killed |
| ignore-betting-keywords | DraftKings / betting copy must be rejected | killed |
| select-catalog-season-only | auto-pin must match week and opponent, not season alone | killed |
| allow-signed-edition-pins | a fabricated @NFL pin on a signed edition must fail the gate | killed |
| empty-signed-whitelist | the signed-pin whitelist must contain only 2026-10-28-1750 | killed |
| hugo-week-opponent-check | Hugo week lookup must still require opponent agreement | killed |
| hugo-allowlist-check | Hugo must reject an off-list handle even if the id is well-formed | killed |
| hugo-mexico-not-neutral | Hugo Mexico City must not resolve as a road/home Chargers scene | killed |
| hugo-pin-omits-svg | a pinned embed must still keep the SVG in the document | killed |
| hugo-sponsored-ignored | Hugo must reject a sponsored flag in pin metadata | killed |
| embed-aspect-16-10-crop | the embed box must reserve ~640px and not crop at 16:10 | killed |
| workflow-omits-sidecar | the daily workflow must git-add data/hero_posts.json | killed |
| overlay-omits-sidecar | edition_overlay must allow data/hero_posts.json | killed |
| attach-skips-signed-check | attach_sidecar must refuse already-signed editions | killed |

No survivors.
