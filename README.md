# Arrowhead Paesano

The official static website for the [Arrowhead Paesano YouTube channel](https://www.youtube.com/@arrowheadpaesano). It combines a checked-in channel snapshot, privacy-friendly video playback, direct Chiefs news bookmarks, Amazon fan-find links, and a client-side Shopify Storefront connection.

There is no application server, database, runtime API, Docker container, or secret environment file. Hugo builds ordinary HTML, CSS, JavaScript, images, and JSON that GitHub Pages can host directly.

## Run locally

```bash
npm install
npm run dev
```

Open `http://localhost:1515/`.

Create the production build with:

```bash
npm run build
```

Hugo writes the generated site to `dist/`. That directory is intentionally ignored because GitHub Actions rebuilds it on every deployment.

## Deploy with GitHub Pages

1. Push this project to the repository's `main` branch.
2. On GitHub, open **Settings → Pages**.
3. Set **Source** to **GitHub Actions**.
4. Open the **Actions** tab and let the “Deploy Hugo site to GitHub Pages” workflow finish.

The workflow builds with GitHub's actual Pages base URL, so project-site paths such as `/arrowheadpaesanowebsite/` work correctly. A push to `main` deploys automatically, and the workflow can also be run manually.

## YouTube snapshot

The homepage and Watch page read `data/channel_feed.json` during the Hugo build. This keeps the public site fast and avoids a server or YouTube API key. Update that file when you want to refresh the featured uploads and Shorts; visitors can always open the live channel directly from every video section.

## Connect Shopify

Set these public Storefront values in `hugo.yaml`:

```yaml
params:
  shopifyStoreDomain: "your-store.myshopify.com"
  shopifyStorefrontPublicToken: "your-public-storefront-token"
  shopifyApiVersion: "2026-04"
  shopifyCollectionHandle: "arrowhead-paesano" # optional
```

The browser then loads products and creates a Shopify cart directly through the Storefront API. A public Storefront token is designed for browser storefronts and will be included in the generated JavaScript configuration. Never put a Shopify Admin token, private token, or other secret in this repository.

Until the public Storefront configuration is present, the shop displays the polished collection preview with no checkout buttons.

## Connect Amazon Associates

Set the public Associates tag in `hugo.yaml`:

```yaml
params:
  amazonAssociateTag: "your-tracking-id-20"
  amazonMarketplace: "www.amazon.com"
```

The curated shopping cards are built as direct Amazon search links. Prices and product images are intentionally not copied or cached.

As an Amazon Associate I earn from qualifying purchases.

## Project map

- Site URL, navigation, Amazon, and Shopify settings: `hugo.yaml`
- Page templates: `layouts/`
- Channel snapshot and curated content: `data/`
- Styles and browser behavior: `public/css/` and `public/js/`
- Optimized channel imagery: `public/images/channel/`
- GitHub Pages deployment: `.github/workflows/pages.yml`
