# Hero evidence pack (PR #280)

Approach: **unsigned sidecar**. Week 8 (`2026-10-28-1750`) is **unpinned**.
The previous pin (`2115471247190438112`) is a DraftKings Over/Under graphic.
No other verified `@Chiefs` Denver-week post had a clean, non-sponsored photo,
so the SVG fallback is the published hero. Edition JSON under
`data/narrative.json` and `data/narrative_editions/` is byte-identical to
develop. Signed Reviews are untouched.

Pinned post: **none** (edition left unpinned; SVG fallback).
The 2026-10-28-1750 whitelist is removed. Deleted-post renders pin a
fixture only inside `render_deleted_hero.py`; published `pins` stay `{}`.

| Artifact | Path |
| --- | --- |
| 390×844 Week 8, JS on | `renders/week8-390x844-js-on.png` |
| 390×844 Week 8, JS off | `renders/week8-390x844-js-off.png` |
| 1280×800 Week 8, JS on | `renders/week8-1280x800-js-on.png` |
| 1280×800 Week 8, JS off | `renders/week8-1280x800-js-off.png` |
| 390×844 fallback 404, JS on/off | `renders/fallback-404-390x844-js-{on,off}.png` |
| 1280×800 fallback 404, JS on/off | `renders/fallback-404-1280x800-js-{on,off}.png` |
| 390×844 fallback all-X-blocked, JS on/off | `renders/fallback-blocked-390x844-js-{on,off}.png` |
| 1280×800 fallback all-X-blocked, JS on/off | `renders/fallback-blocked-1280x800-js-{on,off}.png` |
| 390×844 8s wait, JS on/off | `renders/wait-390x844-js-{on,off}.png` |
| 1280×800 8s wait, JS on/off | `renders/wait-1280x800-js-{on,off}.png` |
| 390×844 working pin, JS on/off | `renders/working-390x844-js-{on,off}.png` |
| 1280×800 working pin, JS on/off | `renders/working-1280x800-js-{on,off}.png` |
| Box metrics for those renders | `renders/render-metrics.json` |
| Mutation report | `mutation-report.md` |
| Python vs Hugo parity | `parity-table.md` |
| Fixed SVGs + alt text | `svgs-and-alts.md` |
| Local gate output | `local-gates.txt` |
| Edition byte-identity | `edition-diff.txt` |
