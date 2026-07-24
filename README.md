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

## The Chiefs Narrative engine

`/narrative/` is an automated, always-looking-ahead weekly Kansas City Chiefs
analysis desk: training-camp battles, game previews/reviews, X's-and-O's with
hand-drawn field diagrams, player/coaching/style matchups, injuries, personnel,
strategies, a model projection, the Vegas line, prediction-market odds, cited
sources, and a ready-to-shoot YouTube run-of-show. It regenerates itself and
evolves the story toward the next Sunday.

### How it works

The engine lives in [`tools/chiefs_narrative/`](tools/chiefs_narrative/):

1. **Collect** — reads the live 2026 schedule (ESPN), the Chiefs news wire
   (Chiefs.com, Arrowhead Pride, Arrowhead Addict, ESPN RSS), the model
   projection + Vegas line (ESPN FPI / DraftKings), and prediction markets
   (Polymarket). Every network call fails soft.
2. **Phase** — detects where the season is (offseason, training camp, preseason,
   a specific game week, playoffs) from the schedule + today's date, so the
   framing changes automatically.
3. **Write** — an LLM provider (or the built-in deterministic *offline* writer)
   turns the signals into a structured, source-cited edition.
4. **Diagram** — renders clean X's-and-O's SVGs from a concept library
   (`diagrams.py`) into `public/images/narrative/`.
5. **Publish** — writes `data/narrative.json` (+ a rolling `data/narrative_archive.json`),
   which the Hugo template at `layouts/narrative/single.html` renders.

### Run it locally

```bash
# Windows
./tools/run_local.ps1 -Serve

# macOS / Linux
./tools/run_local.sh --serve
```

With **no** configuration it uses the offline writer and still ships a complete,
cited edition. To upgrade the writing, copy `tools/.env.example` to `tools/.env`
and set one of:

- `XAI_API_KEY` (or `GROK_API_KEY`, plus optional `GROK_MODEL`) — **Grok/xAI**,
- `OPENAI_API_KEY` (and optionally `OPENAI_MODEL`) — or run ChatGPT Codex's
  `codex` CLI locally,
- `ANTHROPIC_API_KEY` — or run Claude Code's `claude` CLI locally.

Providers are auto-detected in this order: **Grok → OpenAI → Anthropic →
`claude` CLI → `codex` CLI → offline**. Setting a Grok key makes Grok the writer
even if an OpenAI key is also present. Force any one explicitly with
`CHIEFS_PROVIDER=grok` or `--provider grok`. An optional `ODDS_API_KEY` adds a
sportsbook consensus line.

### Daily automation

[`.github/workflows/narrative.yml`](.github/workflows/narrative.yml) runs every
day: it regenerates the edition, opens a pull request, auto-merges it, and
publishes the refreshed site to the `gh-pages` branch (which serves
arrowheadpaesano.com).

Add **`XAI_API_KEY`** (or `GROK_API_KEY`) as a repository secret to have Grok
write it, or `OPENAI_API_KEY` for OpenAI — Grok wins if both are set. With no
secret at all the offline writer still runs. Optional repository *variables*:
`GROK_MODEL`, `OPENAI_MODEL`, `XAI_BASE_URL`.

Trigger it by hand any time from the **Actions** tab (**Run workflow**), where
the *provider* input can force `grok`, `openai`, `anthropic`, or `offline`.

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
