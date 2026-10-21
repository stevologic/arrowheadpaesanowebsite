# Chiefs merchandise footer

Status on 2026-10-21: **application received; footer still disabled and unpublished**.

The existing repository is `stevologic/arrowheadpaesanowebsite`; the connected
GitHub identity `stevologic` owns it with admin/push access. Work starts from
develop `58315eb7b222a8ba10282ed97cf1daf027fc44f9`. Its configuration and checked-in
Pages workflow name `https://arrowheadpaesano.com/` as the production site.
Main publishes through `pages.yml`; this work must remain a draft PR to develop
until the account/link checks below are completed. Preserve existing review
and publication gates.

## Account verification

After the owner signed in, Amazon explicitly reported that the existing login
was not connected to an Associates account. The owner authorized registration,
confirmed the existing payee/contact, declared that the site is not primarily
directed at children under 13, approved the preferred ID, selected Word of Mouth,
and accepted the Operating Agreement. On resumption the wizard already showed
its receipt; the assistant did not submit it a second time.

Amazon assigned **`arrowheadpaes-20`**, and the Associates Central dashboard and
SiteStripe both show that exact ID. The account website list contains
`https://arrowheadpaesano.com`. This is an application received with Associates
Central access, not final approval: the authenticated welcome email independently
confirms the assigned ID and specifies three qualifying purchases within 180
days before application review. The owner identifies this as a personal
sole-proprietor blog, not an LLC.

Payment/tax setup was deferred via Later. The last account-settings inspection
showed the US tax interview as Incomplete; the welcome email does not establish
current tax status. The owner must privately complete tax classification,
identifiers, certification/signature, and payment details. No tax form or bank
details were entered or exported. The existing retail/Prime account is unchanged.

The mug's full link was captured directly from SiteStripe. The blanket and
banner are real in-stock product variants, but their newer SiteStripe copy-only
UI did not return text through the browser clipboard tool. Their URLs remain
blank. The owner must copy and provide those two full SiteStripe links (with
`arrowheadpaes-20` selected); do not reconstruct them. A browser clipboard-check
page using a data URL was blocked by the URL policy and was not retried or
worked around. The footer remains disabled pending completion and link review.

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
