# Arrowhead Paesano — The Chiefs Narrative

The official static website for the [Arrowhead Paesano YouTube channel](https://www.youtube.com/@arrowheadpaesano). The site is a single purpose: the daily **Chiefs Narrative**. The current edition lives at `/`. Earlier editions stay at `/narrative/<slug>/`. `/narrative/` serves the same current edition as the homepage.

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

After a local build, check:

- `dist/index.html` — current Narrative (homepage)
- `dist/narrative/index.html` — same current edition
- `dist/about/index.html` (and the other retired paths) — redirect stubs to `/`
- `dist/404.html` — sends unknown URLs home
- Footer “YouTube channel” link — `https://www.youtube.com/@arrowheadpaesano`

## Deploy with GitHub Pages

1. Push this project to the repository's `main` branch.
2. On GitHub, open **Settings → Pages**.
3. Set **Source** to **GitHub Actions**.
4. Open the **Actions** tab and let the “Deploy Hugo site to GitHub Pages” workflow finish.

The workflow builds with GitHub's actual Pages base URL, so project-site paths such as `/arrowheadpaesanowebsite/` work correctly. A push to `main` deploys automatically, and the workflow can also be run manually.

GitHub Pages has no server-side redirects. Retired URLs are checked-in as HTML stubs (meta refresh + canonical + `location.replace`) generated from Hugo aliases on the homepage.

## The Chiefs Narrative engine

The homepage is an automated Kansas City Chiefs analysis desk. Each edition reviews the last game, names the current state of the roster (what to work on, what to think about), then looks ahead to the next opponent with a game plan and matchup read. It also carries training-camp battles, X's-and-O's with hand-drawn field diagrams, injuries, personnel, a model projection, the Vegas line, prediction-market odds, cited sources, and a ready-to-shoot YouTube run-of-show. It regenerates itself and evolves the story toward the next Sunday.

### How it works

The engine lives in [`tools/chiefs_narrative/`](tools/chiefs_narrative/):

1. **Collect** — reads the live 2026 schedule (ESPN preseason + regular +
   postseason, falling back to the checked-in slate if ESPN is down), the
   last completed game's ESPN recap (box + scoring plays), the Chiefs news
   wire (Chiefs.com, Arrowhead Pride, Arrowhead Addict, ESPN RSS), the model
   projection + Vegas line (ESPN FPI / DraftKings), and prediction markets
   (Polymarket). Every network call fails soft.
2. **Phase** — detects where the season is (offseason, training camp, preseason,
   a specific game week, playoffs) from the schedule + today's date, so the
   framing changes automatically.
3. **Write** — an LLM provider (or the built-in deterministic *offline* writer)
   turns the signals into a structured, source-cited edition with three
   required acts: last-game review, current state, next-game plan.
4. **Diagram** — renders clean X's-and-O's SVGs from a concept library
   (`diagrams.py`) into `public/images/narrative/`.
5. **Publish** — writes `data/narrative.json` (+ a rolling `data/narrative_archive.json`),
   the 2026 slate, and today's wire headlines, which the Hugo templates render
   on `/` and `/narrative/`.

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

- `XAI_API_KEY` (or `GROK_API_KEY`, plus optional `GROK_MODEL`, default `grok-4.6`) — **Grok/xAI**,
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
arrowheadpaesano.com). The generator still writes `data/narrative.json`; Hugo
renders that file on the homepage.

Add **`XAI_API_KEY`** (or `GROK_API_KEY`) as a repository secret to have Grok
write it, or `OPENAI_API_KEY` for OpenAI — Grok wins if both are set. With no
secret at all the offline writer still runs. Optional repository *variables*:
`GROK_MODEL` (defaults to `grok-4.6`), `OPENAI_MODEL`, `XAI_BASE_URL`.

Trigger it by hand any time from the **Actions** tab (**Run workflow**), where
the *provider* input can force `grok`, `openai`, `anthropic`, or `offline`.

## Project map

- Site URL, navigation, and YouTube channel URL: `hugo.yaml`
- Page templates: `layouts/`
- Channel snapshot and curated content: `data/`
- Styles and browser behavior: `public/css/` and `public/js/`
- Optimized channel imagery: `public/images/channel/`
- GitHub Pages deployment: `.github/workflows/pages.yml`
