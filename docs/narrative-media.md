# Narrative imagery and schematic views

Heroes and person thumbnails render nothing until a credited X photograph
exists. Drawn silhouettes, event illustrations, and hotlinked photos are
not used. The X-photo attach path is off: no daily manifest, no xAI
`x_search`, no FixTweet, and no syndication HTTP. A missing API key must
not error. The generator writes `heroMediaId: "none"` and does not invent
or attach media.

Pins for a later official-embed path live in the unsigned sidecar
`data/hero_posts.json`, keyed by edition slug, so signed Review JSON is
never rewritten. The allowlist is kept in
`tools/chiefs_narrative/hero_posts.py`: `@Chiefs`, verified Chiefs staff
(`@ChiefsPR`), `@NFL`, and verified Athletic / ESPN accounts that employ
credentialed Chiefs beat writers. Fan, meme, parody, betting, Polymarket, and
sponsored handles fail the gate. Validation is offline (allowlist + snowflake
id). Build and gate time do not fetch X. The current templates do not render
those pins.

The betting/sponsored denylist reads **text fields only** (`text`, `alt`,
`caption`, `note`, plus optional `ocr` / `mediaAlt` / `imageText`). It cannot
see pixels. There is no paid OCR and no network at build.

Photo review must match the article's event, event date and pictured identity,
in addition to checking rights. A licensed but unrelated archival photograph is
not an acceptable replacement. Record the exact source gallery/post, frame,
photographer, capture date, permission and any required credit. A public gallery
or social post by itself is not permission to download, hotlink or republish.

The generator overwrites model-supplied hero IDs with `none`. Historic signed
narrative JSON is not rewritten; leftover `heroMediaId` values are ignored by
the template. A reviewed `data/hero_media.json` catalog may still exist for
documentary records. It is not rendered.

People thumbnails use the same rule: no silhouette, no placeholder circle, no
unlicensed roster photo. Playmaker names and roster-move names remain as text.

The old general Chiefs X timeline is removed. Requested press-conference
players and existing individual-source links remain separate.

Three.js 0.186.1 is MIT licensed and bundled locally, with its license preserved.
`npm run vendor:three` builds the pinned module; `npm run check:three` verifies
reproducibility. No CDN script loads. Readers opt into a single 3D view; there
is at most one WebGL context, pixel ratio is capped at 1.5, and leaving the play
or tab disposes its resources. Reduced motion or unavailable WebGL keeps the
2D board and all stage controls. Both views use identical schematic coordinates.

Playbook and X's-and-O's schematic SVGs stay. They illustrate concepts, not
tracked game film, and they are not person or event photographs.

Raw `injuries[].image` and `pressConferenceLinks[].image` fields are source
candidates, not license records. Rendering does not republish them. This does
not alter the separate user-activated official YouTube player embeds.
