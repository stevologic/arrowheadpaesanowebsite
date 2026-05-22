# Arrowhead Paesano

Official website for the Arrowhead Paesano YouTube Channel.

This is a static Hugo site designed for GitHub Pages. There is no required Node or Docker server in production, so it is simple to update from GitHub or with Codex.

## Update Content

- Site title, tagline, YouTube link, and navigation: `hugo.yaml`
- Homepage layout: `layouts/index.html`
- About page: `layouts/about/single.html`
- YouTube feature cards: `data/videos.json`
- Merch catalog: `data/merch.json`
- Trusted source list: `data/sources.json`
- Weekly focus page: `data/weekly_focus.json`
- Social/X embeds: `data/social_feeds.json`
- Styles: `public/css/style.css`
- Images and favicon: `public/images/`, `public/favicon.svg`

## Local Preview

Install Hugo, then run:

```bash
hugo server -D
```

Open the local URL Hugo prints in the terminal.

To create the production build locally:

```bash
hugo --minify
```

The generated site goes to `public_site/`.

## GitHub Pages Deployment

The workflow at `.github/workflows/pages.yml` builds and deploys the site whenever changes are pushed to `main`.

In GitHub, enable Pages with:

1. Repository `Settings`
2. `Pages`
3. `Build and deployment`
4. Source: `GitHub Actions`

## Notes For Future Codex Updates

Prefer editing the JSON files in `data/` for routine updates. Only edit Hugo templates when changing the structure of a page, and only edit CSS when changing the visual design.

This repository is intentionally static. Keep runtime services out of the production path unless the hosting target changes away from GitHub Pages.
