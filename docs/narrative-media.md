# Narrative imagery and schematic views

Every current and archived narrative receives a hero through the same partial.
No event-specific photograph is currently cleared. The former channel-portrait
fallback is deliberately retired: it does not illustrate an article's event.

Until a matching photograph has documented reuse rights, the hero is a clearly
labeled original vector illustration. It uses the **viewed edition's** headline,
phase, opponent and event-date label. Preview editions use their next game;
Review editions use their last-game review; camp and other reports retain their
own topic. It never borrows today's opponent for an archived story. This is a
temporary illustration, not fulfillment of the event-photograph requirement.
It depicts no person, stadium scene, actual play or purported game outcome.

Photo review must match the article's event, event date and pictured identity,
in addition to checking rights. A licensed but unrelated archival photograph is
not an acceptable replacement. Record the exact source gallery/post, frame,
photographer, capture date, permission and any required credit. A public gallery
or social post by itself is not permission to download, hotlink or republish.

The generator overwrites model-supplied hero IDs. It may select only a reviewed
`data/hero_media.json` entry for the exact edition slug. An entry requires a
named rightsholder, license terms, attribution, commercial-use permission, dated review and
expiry, documentary evidence under `docs/media-rights/`, a supplied image under
`images/hero/`, and its exact SHA-256. These are human-review records, not an
automated legal determination. Review the evidence before approving a catalog
entry. A public account, verified badge, attribution, `og:image`, or embed support
does not establish reuse rights. Do not extract, crop, screenshot or rehost an
X post's media. If the rightsholder supplies a separately licensed original,
record that permission and source post. Honor license terms and adaptations.

This hero implementation does not load X scripts, scrape X or create new
tracking consent. No licensed record means the labeled article illustration. A removed or
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

Raw `injuries[].image` and `pressConferenceLinks[].image` fields are source
candidates, not license records. Rendering uses the shared reviewed people
catalog; absent permission, show the silhouette and retain the source link.
This does not alter the separate user-activated official YouTube player embeds.
