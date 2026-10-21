// Small, offline render-contract checks. Fixture tags/links never enter site data.
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';

const root = resolve(import.meta.dirname, '..');
const fixture = mkdtempSync(join(tmpdir(), 'paesano-affiliate-check-'));
const original = JSON.parse(readFileSync(join(root, 'data/affiliate_products.json'), 'utf8'));
let passed = 0;
try {
  mkdirSync(join(fixture, 'layouts/partials'), { recursive: true });
  mkdirSync(join(fixture, 'data'));
  writeFileSync(join(fixture, 'layouts/index.html'), '<!doctype html><html lang="en"><body>{{ partial "affiliate-footer.html" . }}</body></html>');
  writeFileSync(join(fixture, 'layouts/partials/affiliate-footer.html'), readFileSync(join(root, 'layouts/partials/affiliate-footer.html')));
  const live = structuredClone(original);
  Object.assign(live, { enabled: true, accountVerifiedOn: '2026-10-21', siteVerifiedOn: '2026-10-21', siteUrl: 'https://arrowheadpaesano.com/', trackingId: 'fixture-only-20' });
  for (const p of live.products) Object.assign(p, { affiliateUrl: `https://www.amazon.com/dp/${p.asin}?tag=fixture-only-20`, siteStripeVerifiedOn: '2026-10-21' });

  function render(label, data, environment = 'production', preview = false, failure = false) {
    writeFileSync(join(fixture, 'data/affiliate_products.json'), JSON.stringify(data));
    writeFileSync(join(fixture, 'hugo.json'), JSON.stringify({ baseURL: 'https://example.invalid/', disableKinds: ['taxonomy', 'term', 'RSS', 'sitemap'], params: { affiliateFooterPreview: preview } }));
    const result = spawnSync(process.execPath, [join(root, 'node_modules/hugo-bin/bin/cli.js'), '--source', fixture, '--environment', environment, '--quiet'], { encoding: 'utf8' });
    assert.equal(result.error, undefined, label);
    if (failure) assert.notEqual(result.status, 0, `${label}: must reject build`);
    else assert.equal(result.status, 0, `${label}: ${result.stderr}`);
    passed++;
    return failure ? '' : readFileSync(join(fixture, 'public/index.html'), 'utf8');
  }

  assert.doesNotMatch(render('disabled in production', original), /chiefs-finds|amazon\.com|Amazon Associate/);
  assert.doesNotMatch(render('production ignores preview flag', original, 'production', true), /chiefs-finds|amazon\.com|Amazon Associate/);
  const incomplete = structuredClone(original);
  incomplete.products[0].affiliateUrl = '';
  const preview = render('incomplete development preview', incomplete, 'development', true);
  assert.match(preview, /Local design preview/);
  assert.equal((preview.match(/class="amazon-find-card"/g) || []).length, 3);
  assert.doesNotMatch(preview, /<a\b|<img\b|<script\b|<iframe\b/);
  assert.match(preview, /As an Amazon Associate I earn from qualifying purchases\./);
  const html = render('verified fixture', live);
  assert.equal((html.match(/rel="sponsored nofollow noopener"/g) || []).length, 3);
  assert.equal((html.match(/aria-label="View .+ on Amazon \(opens in a new tab\)"/g) || []).length, 3);
  assert.doesNotMatch(html, /Link pending|<img\b|<script\b|<iframe\b/);
  const configuredPreview = render('configured full-link preview', original, 'development', true);
  const configuredLive = render('configured full-link production', { ...original, enabled: true });
  for (const rendered of [configuredPreview, configuredLive]) {
    assert.equal((rendered.match(/rel="sponsored nofollow noopener"/g) || []).length, 3);
    assert.doesNotMatch(rendered, /Link pending|<img\b|<script\b|<iframe\b/);
    for (const p of original.products) assert.ok(rendered.replaceAll('&amp;', '&').includes(`href="${p.affiliateUrl}"`));
  }

  const cases = [
    ['account missing', d => { d.accountVerifiedOn = ''; }],
    ['site missing', d => { d.siteVerifiedOn = ''; }],
    ['different site', d => { d.siteUrl = 'https://example.invalid/'; }],
    ['empty tag', d => { d.trackingId = ''; }],
    ['missing link', d => { d.products[0].affiliateUrl = ''; }],
    ['unverified SiteStripe', d => { d.products[0].siteStripeVerifiedOn = ''; }],
    ['wrong product', d => { d.products[0].affiliateUrl = 'https://www.amazon.com/dp/B000000000?tag=fixture-only-20'; }],
    ['wrong tag', d => { d.products[0].affiliateUrl = `https://www.amazon.com/dp/${d.products[0].asin}?tag=wrong-20`; }],
    ['duplicate tag', d => { d.products[0].affiliateUrl += '&tag=wrong-20'; }],
    ['lookalike host', d => { d.products[0].affiliateUrl = `https://www.amazon.com.example.invalid/dp/${d.products[0].asin}?tag=fixture-only-20`; }],
    ['unsafe scheme', d => { d.products[0].affiliateUrl = 'javascript:alert(1)'; }],
    ['too few products', d => { d.products.pop(); }],
    ['too many products', d => { d.products.push(d.products[0], d.products[1]); }],
  ];
  for (const [label, mutate] of cases) { const data = structuredClone(live); mutate(data); render(label, data, 'production', false, true); }
  console.log(`PASS: ${passed} affiliate footer render checks (offline fixtures only).`);
} finally {
  rmSync(fixture, { recursive: true, force: true });
}
