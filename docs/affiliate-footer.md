# Chiefs merchandise footer

Status on 2026-10-21: **application received; verified footer enabled for approved publication**.

The existing repository is `stevologic/arrowheadpaesanowebsite`; the connected
GitHub identity `stevologic` owns it with admin/push access. Work starts from
develop `b0a424d553423fae18a3ba1dc2ba32e57483a7d8`. Its configuration and checked-in
Pages workflow name `https://arrowheadpaesano.com/` as the production site.
Main publishes through `pages.yml`. The owner explicitly approved publication
after remote checks pass. Preserve existing review and publication gates.

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

All three full links were captured directly from SiteStripe with
`arrowheadpaes-20` selected. The blanket and banner were reverified in stock;
their standard Copy affiliate link controls now return text through the
supported browser clipboard API. No link was reconstructed. The footer remains
enabled for the owner's approved publication after the preparation commit's
full remote checks passed. The development preview shows the same links.

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

## Provisional enrollment and publication

[Amazon's application-review guidance](https://affiliate-program.amazon.com/help/node/topic/G8TW5AE9XL2VX9VM)
requires three qualifying purchases within 180 days before review. This implies
that compliant links can be used during provisional enrollment. It also forbids
requests to use links to support the publisher; the disclosure stays neutral.
[Amazon's tax-interview guidance](https://affiliate-program.amazon.com/help/node/topic/GMVSBL86R9337EB6)
requires validated tax information before commission payments. It does not state
that unfinished tax setup blocks link publication. Account-specific restrictions
would still govern. The owner explicitly approved publication after checks pass.

## Preview

```sh
npm ci
npm run dev:affiliates
# Open http://localhost:1516/affiliate-preview/
```

The explicit development config shows an identified local design preview
with verified links and `noindex, nofollow`. Incomplete configurations show
inert labels instead. The short draft-only preview page permits focused mobile
testing; normal production builds exclude it. The production environment
ignores the preview flag. Normal builds now include the verified product
section and disclosure. Disabling the data flag omits the whole section;
inactive or untagged purchase links cannot ship.
The opt-in settings live in `tools/affiliate-preview.yaml`, outside the
auto-loaded `config/` directory, so the existing publication-surface gate is
preserved without exceptions.

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
passed. The 19 render checks passed, including the three actual configured
links, preview-only rendering, and duplicate-tag rejection. Desktop browser preview had
three equal-height cards, no horizontal overflow, and zero media/scripts inside
the section. DOM layout checks also passed at effective CSS widths 800px (two
columns), 433px and 355px (one column), without horizontal overflow. Keyboard
navigation reached the first product link and showed its visible focus ring.
Card heights remained unchanged across desktop observations. The browser's
viewport-override screenshot compositing was inconsistent, so responsive
geometry is verified through DOM measurements rather than a reliable full
mobile capture. The three product links have unique accessible labels, reference
the nearby disclosure, and use the exact captured URLs. Real affiliate
attribution still requires genuine qualifying activity; no automated purchase
or synthetic commission test is performed. The full narrative/Unity suites
were not run for this isolated
footer; no game files, narrative data, or daily generator were changed.
