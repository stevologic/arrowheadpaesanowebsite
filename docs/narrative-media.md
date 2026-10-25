# Narrative imagery and schematic views

Every current and archived narrative receives a hero through the same partial.
The initial catalog deliberately contains **no rights-cleared X photographs**.
The fallback is the existing first-party Arrowhead Paesano channel image at
`images/channel/host-studio-red.webp`, introduced in commit
`5937845efa65f48c0013c019d035f327daca209b` and inspected for this change.
It is labeled a channel image, never a current-game or X photograph.

The generator overwrites model-supplied hero IDs. It may select only a reviewed
`data/hero_media.json` entry for the exact edition slug. An entry requires a
named rightsholder, attribution, commercial-use permission, dated review and
expiry, documentary evidence under `docs/media-rights/`, a supplied image under
`images/hero/`, and its exact SHA-256. These are human-review records, not an
automated legal determination. Review the evidence before approving a catalog
entry. A public account, verified badge, attribution, `og:image`, or embed support
does not establish reuse rights. Do not extract, crop, screenshot or rehost an
X post's media. If the rightsholder supplies a separately licensed original,
record that permission and source post. Honor license terms and adaptations.

This hero implementation does not load X scripts, scrape X or create new
tracking consent. No licensed record means the owned fallback. A removed or
expired permission must be removed from the catalog; the template also checks
scope, permission dates and asset hash at build time. Historic signed narrative
JSON is not rewritten to add heroes.

People thumbnails use the same review principle in `data/people_media.json`.
No new player portraits are initially licensed, so roster entries, matchup
panels and proposed playmaker roles use explicitly illustrative silhouettes.
Each future portrait needs exact identity, rightsholder, commercial permission,
evidence and file hash. A roster move is not evidence of playing time; a name
on a play diagram identifies a proposed teaching role, not tracked participation.

The old general Chiefs X timeline is removed. Requested press-conference
players and existing individual-source links remain separate.

Three.js 0.186.1 is MIT licensed and bundled locally, with its license preserved.
`npm run vendor:three` builds the pinned module; `npm run check:three` verifies
reproducibility. No CDN script loads. Readers opt into a single 3D view; there
is at most one WebGL context, pixel ratio is capped at 1.5, and leaving the play
or tab disposes its resources. Reduced motion or unavailable WebGL keeps the
2D board and all stage controls. Both views use identical schematic coordinates.
