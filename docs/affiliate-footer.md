# Chiefs merchandise footer

Status on 2026-10-21: **prepared; not commission-ready or published**.

The existing repository is `stevologic/arrowheadpaesanowebsite`; the connected
GitHub identity `stevologic` owns it with admin/push access. Work starts from
develop `58315eb7b222a8ba10282ed97cf1daf027fc44f9`. Its configuration and checked-in
Pages workflow name `https://arrowheadpaesano.com/` as the production site.
Main publishes through `pages.yml`; this work must remain a draft PR to develop
until the account/link checks below are completed. Preserve existing review
and publication gates.

## Account verification is still pending

The authorized Chrome session opened `https://affiliate-program.amazon.com/`
and followed **Sign in**. Amazon showed **Sign in or create account**, with an
empty email/mobile field, on its existing-account sign-in route. It did not
show an Associates dashboard, account status, tracking ID, or website list.
This is **unknown membership**, not proof of no account. Prior connected inbox
searches found no Associates evidence; Prime membership is not Associates
verification. No duplicate account was created, form submitted, agreement
accepted, credential changed, or tax/bank information entered.

Smallest next action: the owner signs in to the existing Amazon account in the
waiting Associates tab, completing any secure authentication themselves.
Then inspect the dashboard's account status, the existing tracking ID, and
the website/mobile-app list for `https://arrowheadpaesano.com/`. If the site is
absent, get the owner to add/approve its coverage before enabling links.
If Amazon explicitly confirms that no Associates account exists, continue
the signup using the existing Amazon login; do not create another retail
Amazon account. The live signup form must establish which identity/payee,
address, contact, website/profile, and traffic facts are missing. Those facts
cannot be inferred from Prime. Leave any required legal acceptance, tax,
banking and secure identity/authentication steps to the owner.

## Product evidence

These Amazon product detail pages were read on 2026-10-21. They establish real
product identities, not commission eligibility, current stock, or purchase
endorsement. Recheck each selected variant and offer when generating its link.

| Product | ASIN | Evidence |
| --- | --- | --- |
| Rico Industries Chiefs 15 oz matte-black bistro mug | B0C2W495W4 | [Product page](https://www.amazon.com/dp/B0C2W495W4); selected team name was Kansas City Chiefs |
| FOCO Chiefs Team Property Of sherpa fleece blanket | B0BTTNTZKS | [Product page](https://www.amazon.com/FOCO-Kansas-Chiefs-Property-Blanket/dp/B0BTTNTZKS); product title identifies Chiefs variant |
| WinCraft Chiefs fan cave wall/door banner | B099Z354KR | [Product page](https://www.amazon.com/WinCraft-Kansas-Chiefs-Banner-Scroll/dp/B099Z354KR); product title identifies Chiefs banner |

No prices, ratings, reviews, product photos, or official-licensing claims are
copied into the footer. All cards are text-only. `sourceUrl` records research;
it is never emitted as a customer purchase link. No affiliate tag is invented.
The required disclosure is based on the [Amazon Associates Operating
Agreement](https://affiliate-program.amazon.com/help/operating/agreement).

## Preview

```sh
npm ci
npm run dev:affiliates
# Open http://localhost:1516/#chiefs-finds
```

The explicit development config shows an identified local design preview
with inert link labels and `noindex, nofollow`. The production environment
ignores the preview flag. Normal builds currently omit the entire product
section and disclosure, so no inactive or untagged purchase links can ship.

## Activation

In `data/affiliate_products.json`, record the actual existing account's
`trackingId`, `accountVerifiedOn`, `siteVerifiedOn`, and exact `siteUrl` only
after observing them. For each of the three products, use SiteStripe to
generate a **full text link** from the correct product/Chiefs variant, verify
its destination and tag, and store that exact link as `affiliateUrl` with
`siteStripeVerifiedOn`. Do not construct a tagged link from the research URL.
Short links are intentionally rejected here so the build can validate the
tag and ASIN directly; use SiteStripe's full-link option.

Set `enabled` to true only after all account and product checks pass. The
build rejects missing attestations, fewer than 3/more than 4 products,
non-HTTPS or non-Amazon links, wrong ASINs, and mismatched tracking IDs.
These checks validate structure; a successful build cannot prove membership,
site approval, attribution, stock, or that a human actually used SiteStripe.
Final verification must still happen in the owner account. Follow the
existing develop review, CI, and reviewed promotion to main workflow.

The live cards include descriptive product headings, keyboard-focus styling,
48px minimum link height, new-tab announcements, and `sponsored nofollow
noopener`. The disclosure appears before the cards, including the exact text
“As an Amazon Associate I earn from qualifying purchases.” The static markup
does not inject ads, load Amazon images, or add third-party scripts.

## Verification

```sh
npm run check:affiliates
npm run build
```

The small offline test uses a disposable miniature Hugo site and fixture-only
tags. It checks production hiding, development preview, live link semantics,
and unsafe/incomplete activation failures. It does not contact Amazon or
touch the real product data. CI runs it after the Hugo installer check.

Local evidence on 2026-10-21: extended Hugo 0.152.2 normal and preview builds
passed (94 pages). The 16 render checks passed. Desktop browser preview had
three equal-height cards, no horizontal overflow, zero purchase anchors and
zero media/scripts inside the section. Responsive DOM inspection at the
mobile viewport confirmed one-column cards and equal document/client widths.
The browser viewport screenshot override produced a blank mobile capture;
mobile visual approval and real affiliate click/attribution testing remain
pending. The full narrative/Unity suites were not run for this isolated
footer; no game files, narrative data, or daily generator were changed.
