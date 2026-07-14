require('dotenv').config({ quiet: true });

const path = require('path');
const express = require('express');
const { getAmazonFinds } = require('./lib/amazon');
const { sources } = require('./lib/sources');
const { getLatestMixed, getBySource, getYouTubeVideos, warm } = require('./lib/feeds');
const {
  createShopifyCart,
  getShopifyProducts,
} = require('./lib/shopify');

const app = express();
const PORT = process.env.PORT || 1515;
const YT_CHANNEL_ID = process.env.YOUTUBE_CHANNEL_ID || 'UCZfgwB3XweP-PehJYiQFfNw';
const publicDir = path.join(__dirname, 'dist');

app.disable('x-powered-by');
app.use(express.json({ limit: '16kb', type: 'application/json' }));

function asyncRoute(handler) {
  return (req, res, next) => Promise.resolve(handler(req, res, next)).catch(next);
}

function noStore(res) {
  res.set('Cache-Control', 'no-store');
}

app.get('/healthz', (req, res) => {
  res.json({ ok: true, ts: Date.now() });
});

app.get('/api/feed.json', asyncRoute(async (req, res) => {
  noStore(res);
  const items = await getLatestMixed(50);
  res.json({ updated: Date.now(), count: items.length, items });
}));

app.get('/api/by-source.json', asyncRoute(async (req, res) => {
  noStore(res);
  const groups = await getBySource();
  res.json({ updated: Date.now(), count: groups.length, groups });
}));

app.get('/api/youtube.json', asyncRoute(async (req, res) => {
  noStore(res);
  const videos = await getYouTubeVideos(YT_CHANNEL_ID);
  res.json({
    updated: Date.now(),
    channelConfigured: Boolean(YT_CHANNEL_ID),
    channel: {
      id: YT_CHANNEL_ID,
      name: 'Arrowhead Paesano',
      handle: '@arrowheadpaesano',
      url: 'https://www.youtube.com/@arrowheadpaesano',
      uploadsPlaylistId: `UU${YT_CHANNEL_ID.slice(2)}`,
    },
    count: videos.length,
    videos,
  });
}));

app.get('/api/sources.json', (req, res) => {
  noStore(res);
  res.json({ count: sources.length, sources });
});

app.get('/api/amazon-finds.json', (req, res) => {
  res.set('Cache-Control', 'public, max-age=300, stale-while-revalidate=3600');
  res.json(getAmazonFinds());
});

app.get('/api/shopify-products.json', asyncRoute(async (req, res) => {
  res.set('Cache-Control', 'public, max-age=60, stale-while-revalidate=600');
  const result = await getShopifyProducts();
  res.json(result);
}));

// Compatibility alias for the current shop UI. The response is Shopify-backed.
app.get('/api/printful-products.json', asyncRoute(async (req, res) => {
  res.set('Cache-Control', 'public, max-age=60, stale-while-revalidate=600');
  const result = await getShopifyProducts();
  res.set('Deprecation', 'true');
  res.set('Link', '</api/shopify-products.json>; rel="successor-version"');
  res.json({ provider: 'shopify', deprecatedAlias: true, ...result });
}));

app.post('/api/shopify-cart.json', asyncRoute(async (req, res) => {
  noStore(res);
  const cart = await createShopifyCart(req.body && req.body.lines);
  res.status(201).json(cart);
}));

app.use(express.static(publicDir, {
  extensions: ['html'],
  maxAge: process.env.NODE_ENV === 'production' ? '1h' : 0,
}));

app.use((req, res) => {
  res.status(404).sendFile(path.join(publicDir, '404.html'));
});

app.use((error, req, res, next) => {
  if (res.headersSent) return next(error);

  const malformedJson = error instanceof SyntaxError
    && error.status === 400
    && Object.prototype.hasOwnProperty.call(error, 'body');
  const statusCode = malformedJson ? 400 : Number(error.statusCode) || 500;
  const code = malformedJson ? 'INVALID_JSON' : error.code || 'INTERNAL_ERROR';
  const isClientError = statusCode >= 400 && statusCode < 500;

  if (statusCode >= 500) {
    console.error(`[server] ${code}: ${error.message}`);
  }

  if (req.path.startsWith('/api/')) {
    const payload = {
      error: {
        code,
        message: malformedJson
          ? 'Request body must contain valid JSON'
          : (isClientError ? error.message : 'The requested service is temporarily unavailable'),
      },
    };
    if (isClientError && error.details) payload.error.details = error.details;
    return res.status(statusCode).json(payload);
  }

  return res.status(statusCode).send('The requested page is temporarily unavailable.');
});

app.listen(PORT, () => {
  console.log(`Arrowhead Paesano Hugo site running on http://localhost:${PORT}`);
  warm();
  setInterval(warm, (Number(process.env.FEED_CACHE_MINUTES) || 15) * 60 * 1000);
});
