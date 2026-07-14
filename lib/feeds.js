const Parser = require('rss-parser');
const { sources, CHIEFS_KEYWORDS } = require('./sources');

const parser = new Parser({
  timeout: 10000,
  headers: { 'User-Agent': 'ArrowheadPaesano/1.0 (+https://arrowheadpaesano.example)' },
  customFields: {
    item: [
      ['media:group', 'mediaGroup'],
      ['yt:videoId', 'ytVideoId'],
      ['yt:channelId', 'ytChannelId'],
    ],
  },
});

const cache = new Map(); // sourceId -> { fetchedAt, items }
const CACHE_MS = (Number(process.env.FEED_CACHE_MINUTES) || 15) * 60 * 1000;

function pickImage(item) {
  if (item.enclosure && item.enclosure.url) return item.enclosure.url;
  if (item['media:thumbnail'] && item['media:thumbnail'].$ && item['media:thumbnail'].$.url) {
    return item['media:thumbnail'].$.url;
  }
  if (item['media:content'] && item['media:content'].$ && item['media:content'].$.url) {
    return item['media:content'].$.url;
  }
  const html = item['content:encoded'] || item.content || '';
  const m = html.match(/<img[^>]+src=["']([^"']+)["']/i);
  return m ? m[1] : null;
}

function stripHtml(html) {
  if (!html) return '';
  return html.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
}

const KEYWORD_RE = new RegExp(
  '\\b(' + CHIEFS_KEYWORDS.map((k) => k.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|') + ')\\b',
  'i'
);

function isChiefsRelated(item) {
  const haystack = `${item.title || ''} ${item.summary || ''}`;
  return KEYWORD_RE.test(haystack);
}

async function fetchSource(source) {
  const cached = cache.get(source.id);
  if (cached && Date.now() - cached.fetchedAt < CACHE_MS) {
    return cached.items;
  }
  try {
    const feed = await parser.parseURL(source.feed);
    let items = (feed.items || []).map((item) => ({
      title: item.title || 'Untitled',
      link: item.link,
      pubDate: item.isoDate || item.pubDate || null,
      pubTimestamp: item.isoDate ? new Date(item.isoDate).getTime() : (item.pubDate ? new Date(item.pubDate).getTime() : 0),
      summary: stripHtml(item.contentSnippet || item.summary || item.content || '').slice(0, 240),
      image: pickImage(item),
      source: source.id,
      sourceName: source.name,
      sourceUrl: source.url,
      sourceAccent: source.accent,
      category: source.category,
    }));

    if (!source.chiefsOnly) {
      items = items.filter(isChiefsRelated);
    }

    items = items.slice(0, 12);
    cache.set(source.id, { fetchedAt: Date.now(), items });
    return items;
  } catch (err) {
    console.error(`[feeds] failed to fetch ${source.id}: ${err.message}`);
    if (cached) return cached.items; // serve stale on error
    cache.set(source.id, { fetchedAt: Date.now(), items: [] });
    return [];
  }
}

async function getAllItems() {
  const results = await Promise.all(sources.map(fetchSource));
  return results.flat();
}

async function getLatestMixed(limit = 30) {
  const all = await getAllItems();
  return all
    .filter((it) => it.pubTimestamp)
    .sort((a, b) => b.pubTimestamp - a.pubTimestamp)
    .slice(0, limit);
}

async function getBySource() {
  return Promise.all(sources.map(async (source) => ({
    source,
    items: (await fetchSource(source)).slice(0, 5),
  })));
}

function firstNode(value) {
  return Array.isArray(value) ? value[0] : value;
}

function mediaValue(group, key) {
  const node = firstNode(group && group[key]);
  return node && typeof node === 'object' && '_' in node ? node._ : node;
}

function mediaAttribute(group, key, attribute) {
  const node = firstNode(group && group[key]);
  return node && node.$ ? node.$[attribute] : undefined;
}

function youtubeStats(group) {
  const community = firstNode(group && group['media:community']);
  const rating = firstNode(community && community['media:starRating']);
  const statistics = firstNode(community && community['media:statistics']);
  return {
    views: Number(statistics && statistics.$ && statistics.$.views) || 0,
    likes: Number(rating && rating.$ && rating.$.count) || 0,
  };
}

async function getYouTubeVideos(channelId) {
  if (!channelId) return [];
  const cacheKey = `yt:${channelId}`;
  const cached = cache.get(cacheKey);
  if (cached && Date.now() - cached.fetchedAt < CACHE_MS) return cached.items;
  try {
    const feed = await parser.parseURL(`https://www.youtube.com/feeds/videos.xml?channel_id=${channelId}`);
    const items = (feed.items || []).slice(0, 15).map((item) => {
      const videoId = firstNode(item.ytVideoId) || (item.id || '').replace('yt:video:', '');
      const group = firstNode(item.mediaGroup) || {};
      const stats = youtubeStats(group);
      return {
        title: item.title,
        link: item.link,
        videoId,
        pubDate: item.isoDate || item.pubDate || null,
        thumbnail: mediaAttribute(group, 'media:thumbnail', 'url')
          || (videoId ? `https://i.ytimg.com/vi/${videoId}/hqdefault.jpg` : null),
        description: String(mediaValue(group, 'media:description') || '').slice(0, 500),
        views: stats.views,
        likes: stats.likes,
        isShort: (() => {
          try {
            return new URL(item.link).pathname.startsWith('/shorts/');
          } catch (_) {
            return false;
          }
        })(),
      };
    });
    cache.set(cacheKey, { fetchedAt: Date.now(), items });
    return items;
  } catch (err) {
    console.error(`[feeds] youtube fetch failed: ${err.message}`);
    if (cached) return cached.items;
    return [];
  }
}

function warm() {
  sources.forEach((s) => fetchSource(s).catch(() => {}));
  getYouTubeVideos(process.env.YOUTUBE_CHANNEL_ID || 'UCZfgwB3XweP-PehJYiQFfNw').catch(() => {});
}

module.exports = { getAllItems, getLatestMixed, getBySource, getYouTubeVideos, warm, isChiefsRelated };
