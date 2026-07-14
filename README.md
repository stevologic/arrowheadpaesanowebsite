# Arrowhead Paesano

The official website for the [Arrowhead Paesano YouTube channel](https://www.youtube.com/@arrowheadpaesano): a Hugo-built, Express-served Chiefs fan hub with live sourced news, privacy-friendly YouTube playback, a channel archive, and a Shopify-ready storefront.

## Run locally

```bash
npm install
npm run build
npm start
```

Open `http://localhost:1515`.

`npm run dev` performs a fresh Hugo build and starts the Express server. The generated site lives in `dist/` and is intentionally ignored by Git.

## Live integrations

- News comes from the named RSS feeds in `data/sources.json` and is served through `/api/feed.json` and `/api/by-source.json`.
- YouTube uploads come from the channel's official public RSS feed. No YouTube API key is required.
- The Watch page separates standard uploads from Shorts, uses `youtube-nocookie.com`, and loads the player only after a visitor presses play.
- Shopify products and checkout stay server-side behind fixed Storefront GraphQL operations.
- Curated Amazon fan-find links are built locally from fixed searches; no Amazon Product Advertising API or external fetch is required.

## Connect Shopify

Copy `.env.example` to `.env` and fill in these values:

```dotenv
SHOPIFY_STORE_DOMAIN=your-store.myshopify.com
SHOPIFY_STOREFRONT_ACCESS_TOKEN=your_storefront_token
SHOPIFY_API_VERSION=2026-04
SHOPIFY_COLLECTION_HANDLE=arrowhead-paesano
```

The collection handle is optional. Without it, the storefront loads the store's available products. Until both the permanent `*.myshopify.com` domain and Storefront access token are present, visitors see the polished collection preview with no active purchase buttons.

The Storefront token is used only by the Express server. Do not use a Shopify Admin token here and do not commit `.env`.

## Connect Amazon Associates

Add your US Amazon Associates tracking ID to `.env`:

```dotenv
AMAZON_ASSOCIATE_TAG=your-tracking-id-20
AMAZON_MARKETPLACE=www.amazon.com
```

`AMAZON_MARKETPLACE` is optional and defaults to `www.amazon.com`; only the US marketplace is allowed. The server preserves a valid tracking ID exactly as supplied after trimming surrounding whitespace. When the tag is blank or contains unsafe characters, the curated Chiefs searches still work but contain no affiliate tag. No prices or unverified product images are stored or displayed by this integration.

As an Amazon Associate I earn from qualifying purchases.

## Useful endpoints

- `GET /healthz`
- `GET /api/feed.json`
- `GET /api/by-source.json`
- `GET /api/youtube.json`
- `GET /api/sources.json`
- `GET /api/amazon-finds.json`
- `GET /api/shopify-products.json`
- `POST /api/shopify-cart.json`

## Project map

- Site settings/navigation: `hugo.yaml`
- Homepage: `layouts/index.html`
- Shared header/footer/metadata: `layouts/_default/baseof.html`
- Watch, Sources, Shop, About: `layouts/*/single.html`
- News feeds and filtering: `lib/feeds.js`, `data/sources.json`, `data/chiefs-keywords.json`
- Amazon Associates searches: `lib/amazon.js`, `data/amazon_finds.json`
- Shopify adapter: `lib/shopify.js`
- Channel archive: `data/video_archive.json`
- Styles and browser behavior: `public/css/`, `public/js/main.js`
- Optimized channel media: `public/images/channel/`

## Docker

```bash
docker compose up --build
```

The container builds the Hugo site in a separate stage, installs production-only runtime packages, and runs Express as the non-root `node` user on port `1515`.
