# Narrative imagery and schematic views

A hero image is optional. Editions never generate, synthesize, or illustrate
one. If no verified photograph or official embed is available, the page
renders no image element.

The first choice is a verified X photograph attached after writing
(`xPhoto`): an allowlisted post URL, a `pbs.twimg.com/media/` URL that
downloads as a still JPEG/PNG/WebP, and the credit line
`Photo: @handle on X` linking that exact post. See
`tools/chiefs_narrative/x_photo.py`.

The second choice is an official X embed of a topic-matched post. Pins live
in the unsigned sidecar `data/hero_posts.json`, keyed by edition slug, so
signed Review JSON is never rewritten. The allowlist is kept in
`tools/chiefs_narrative/hero_posts.py`: `@Chiefs`, verified Chiefs staff
(`@ChiefsPR`), `@NFL`, and verified Athletic / ESPN accounts that employ
credentialed Chiefs beat writers. Fan, meme, parody, betting, Polymarket, and
sponsored handles fail the gate. Validation is offline (allowlist + snowflake
id). Build and gate time do not fetch X.

The betting/sponsored denylist reads **text fields only** (`text`, `alt`,
`caption`, `note`, plus optional `ocr` / `mediaAlt` / `imageText`). It cannot
see pixels. Pin review must open the media: look at the actual photo or
graphic, write an alt that describes that image, and set `mediaReviewed: true`.
A sportsbook panel that only lives in the image will pass the text gate if
the alt is dishonest. There is no paid OCR and no network at build; the
`mediaReviewed` flag is the offline image-content attestation.

The embed is a server-rendered blockquote with the visible source credit and
the post link. There is no illustrated backdrop, no SVG scene, and no
placeholder art while the widget loads. If the embed is unavailable, times
out, or never reaches a usable height, the page keeps the plain post link
and shows no image. `widgets.js` loads async, only on pages that have a
pinned post, and only from `https://platform.twitter.com`. Media is never
downloaded or rehosted. Pins are refused on already-signed Reviews and on a
missing, unreadable, or non-object edition file, and when the signature
check raises.

The third choice is a rights-reviewed **real photograph** in
`data/hero_media.json` for the exact edition slug. An entry requires a named
rightsholder, license terms, attribution, commercial-use permission, dated
review and expiry, documentary evidence under `docs/media-rights/`, a
supplied still image under `images/hero/` (webp/png/jpeg, never SVG), and
its exact SHA-256. These are human-review records, not an automated legal
determination. A public account, verified badge, attribution, `og:image`, or
embed support does not establish reuse rights. Do not extract, crop,
screenshot or rehost an X post's media. Illustrations, generated art, and
topic-matched SVG scenes are not catalog entries.

Otherwise the edition publishes without an image. Historic editions follow
the same rule. `og:image` / `twitter:image` use the verified X photograph
when one is attached, otherwise the site logo (`og.png`) — never a
synthesized scene.

Photo review must match the article's event, event date and pictured identity,
in addition to checking rights. A licensed but unrelated archival photograph is
not an acceptable replacement. Record the exact source gallery/post, frame,
photographer, capture date, permission and any required credit. A public gallery
or social post by itself is not permission to download, hotlink or republish.

A pinned official embed loads only `platform.twitter.com/widgets.js`. Nothing
else third-party is added for the hero. If a Content-Security-Policy is
introduced, it must allow exactly that origin for the widget script. The
generator writes a new edition's pin into the sidecar from the offline
catalog only when week and opponent both match; it does not invent status IDs
and it does not rewrite historic edition JSON. A removed or expired licensed-image permission must be removed
from `data/hero_media.json`; the template also checks scope, permission dates
and asset hash at build time. Historic signed narrative JSON is not rewritten
to add heroes.

People thumbnails follow Stephen's rule: a real X photograph credited to its
source post, or no image. `data/x-photo/people.json` is the optional per-person
catalog. A roster, matchup, or playmaker name renders a thumbnail only when
that catalog (or the edition `xPhoto` for the same post / named subject)
verifies. Drawn silhouettes and stand-in captions are not published. A roster
move is not evidence of playing time; a name on a play diagram identifies a
proposed teaching role, not tracked participation.

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
catalog; absent permission, omit the thumbnail and retain the source link.
This does not alter the separate user-activated official YouTube player embeds.
