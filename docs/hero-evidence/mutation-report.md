# Hero mutation report (Karen IDs)

Evaluated 44 named mutants. Survivors are listed below.

| Mutant | Killer | Result |
| --- | --- | --- |
| O2 | Hugo @ prefix must still mark the edition away | killed |
| O4 | markets.homeAway must still resolve an away Denver preview | killed |
| O5 | week/opponent mismatch must not return the week-8 Denver scene | killed |
| O7 | Python @ prefix must still mark the edition away | killed |
| O8 | training-camp must still resolve to camp-fallback | killed |
| O8-hugo | Hugo training-camp must still use camp-fallback | killed |
| N6 | a 9-digit id must still be rejected | killed |
| N7 | url/handle mismatch must still be rejected | killed |
| N9 | sidecar allowlist drift must still fail the gate | killed |
| N11 | Hugo must reject a short id | killed |
| N12 | unpinned pages must not load widgets.js | killed |
| N13 | Hugo must reject a handle/url mismatch | killed |
| D4 | Python denylist must scan caption | killed |
| D6 | Hugo denylist must reject DraftKings in caption | killed |
| D7 | Hugo must reject a sponsored flag | killed |
| A2 | auto-pin must require mode agreement | killed |
| A3 | auto-pin must refuse a missing week or opponent | killed |
| J1 | client must treat an unavailable post as fallback | killed |
| J1-regex | unavailable() must still test the tombstone copy | killed |
| J3 | ready must still require a 240px embed | killed |
| J4 | the widgets.js poll must still call inspect() | killed |
| J2 | client must fall back after 8s without a real embed | killed |
| O1 | week-first lookup must still require opponent agreement | killed |
| O3 | unknown/away fallback caption must not mention Arrowhead | killed |
| O6 | London vs Miami must not resolve as Hard Rock | killed |
| N16 | matchup SVGs must keep both helmets inside the viewBox | killed |
| D1 | sponsored=true in pin metadata must be rejected | killed |
| D2 | DraftKings / betting copy must be rejected | killed |
| N14 | auto-pin must match week and opponent, not season alone | killed |
| S1 | a fabricated @NFL pin on a signed edition must fail the gate | killed |
| S6 | a missing edition file must fail closed | killed |
| F3 | a non-object edition file must fail closed | killed |
| F4 | a signature-check GateError must fail closed | killed |
| N8 | Hugo week lookup must still require opponent agreement | killed |
| N10 | Hugo must reject an off-list handle even if the id is well-formed | killed |
| S7 | Hugo must refuse a pin on a signed Review | killed |
| N4 | a pinned embed must still keep the SVG in the document | killed |
| N5 | the embed box must reserve ~640px and not crop at 16:10 | killed |
| N17 | fallback must keep the reserved embed height | killed |
| N18 | JS-off must not keep an unguarded reserved embed box | killed |
| N19 | wait and fallback must contain the SVG so both helmets stay in frame | killed |
| N15 | edition_overlay must allow data/hero_posts.json | killed |
| N1 | the daily workflow must git-add data/hero_posts.json | killed |
| S2 | attach_sidecar must refuse already-signed editions | killed |

No survivors.
