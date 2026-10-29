# Narrative imagery and schematic views

Every current and archived narrative receives a hero through the same partial.
The first choice is an official X embed of a topic-matched post. Pins live in
the unsigned sidecar `data/hero_posts.json`, keyed by edition slug, so signed
Review JSON is never rewritten. The allowlist is kept in
`tools/chiefs_narrative/hero_posts.py`: `@Chiefs`, verified Chiefs staff
(`@ChiefsPR`), `@NFL`, and verified Athletic / ESPN accounts that employ
credentialed Chiefs beat writers. Fan, meme, parody, betting, Polymarket, and
sponsored handles fail the gate. Validation is offline (allowlist + snowflake
id). Build and gate time do not fetch X.

The embed is a server-rendered blockquote with the visible source credit and
the post link inside a reserved 16:10 box. `widgets.js` loads async, only on
pages that have a pinned post, and only from `https://platform.twitter.com`.
With JavaScript off the page is still complete: credit, link, and text stay
visible and the box does not collapse. Media is never downloaded or rehosted.

Archived editions without a pin, and any pin that fails the allowlist or id
shape, use the original vector illustration selected at render time from the
**viewed edition's** phase, opponent, venue, home or away, and headline.
Preview editions use their next game; Review editions use their last-game
review; camp, bye, and offseason reports use a phase fallback. It never
borrows today's opponent for an archived story. Committed scenes cover every
2026 slate opponent and venue, plus home Arrowhead bowls and the extra
neutral / postseason cards. The artwork is generated in-repo: stadium
silhouettes, landmarks, field shapes, and helmet shapes in team colors — no
likenesses, no league or club wordmarks, no photographs. The caption stays
honest: illustration, not a photograph. This is not fulfillment of the
event-photograph requirement.

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

A pinned official embed loads only `platform.twitter.com/widgets.js`. Nothing
else third-party is added for the hero. If a Content-Security-Policy is
introduced, it must allow exactly that origin for the widget script. The
generator writes a new edition's pin into the sidecar from the offline
catalog; it does not invent status IDs and it does not rewrite historic
edition JSON. A removed or expired licensed-image permission must be removed
from `data/hero_media.json`; the template also checks scope, permission dates
and asset hash at build time. Historic signed narrative JSON is not rewritten
to add heroes.

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
