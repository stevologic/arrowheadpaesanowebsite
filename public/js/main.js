const FEED_REFRESH_MS = 15 * 60 * 1000;
const YOUTUBE_URL = 'https://www.youtube.com/@arrowheadpaesano';

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

function escapeHTML(value) {
  return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  }[ch]));
}

function cleanUrl(value) {
  const url = String(value || '');
  if (!url) return '#';
  try {
    const parsed = new URL(url, window.location.origin);
    if (parsed.protocol === 'http:' || parsed.protocol === 'https:') return parsed.href;
  } catch (_) {
    return '#';
  }
  return '#';
}

function accent(value) {
  return /^#[0-9a-f]{3,8}$/i.test(String(value || '')) ? value : '#E31837';
}

function timeAgo(dateStr) {
  if (!dateStr) return '';
  const then = new Date(dateStr).getTime();
  if (!Number.isFinite(then)) return '';
  const diff = Date.now() - then;
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(dateStr).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

async function fetchJSON(url) {
  const response = await fetch(url, { cache: 'no-store' });
  if (!response.ok) throw new Error(`Failed to load ${url}`);
  return response.json();
}

function readEmbeddedSources() {
  const script = $('#site-sources');
  if (!script) return [];
  try {
    return JSON.parse(script.textContent || '[]');
  } catch (_) {
    return [];
  }
}

function emptyState(title, body, actionHTML = '') {
  return `
    <div class="empty-state">
      <h3>${escapeHTML(title)}</h3>
      <p>${escapeHTML(body)}</p>
      ${actionHTML}
    </div>
  `;
}

function sourceChip(item, extraClass = '') {
  return `<span class="source-chip ${extraClass}">${escapeHTML(item.sourceName || 'Chiefs')}</span>`;
}

function fallbackMedia(label = 'AP') {
  return `<div class="mag-fallback" aria-hidden="true"><span>${escapeHTML(label).slice(0, 2).toUpperCase()}</span></div>`;
}

function storyProvenance(item = {}) {
  if (item.source === 'chiefs-official') return 'Official source';
  return `RSS · ${item.sourceName || 'Publisher'}`;
}

function cardHTML(item, variant = '') {
  const variantClass = variant ? ` card--${escapeHTML(variant)}` : '';
  const itemAccent = accent(item.sourceAccent);
  const href = cleanUrl(item.link);
  const title = escapeHTML(item.title || 'Untitled');
  const media = item.image
    ? `<img src="${cleanUrl(item.image)}" alt="" loading="lazy" referrerpolicy="no-referrer" /><span class="card-shade" aria-hidden="true"></span>`
    : `<span class="card-fallback" aria-hidden="true"><span class="card-fallback-mark">AP</span><span class="card-fallback-source">${escapeHTML(item.sourceName || 'Chiefs')}</span></span>`;
  const summary = item.summary && (variant === 'lead' || variant === 'wide')
    ? `<p class="card-summary">${escapeHTML(item.summary)}</p>`
    : '';

  return `
    <a class="card${variantClass} reveal" href="${href}" target="_blank" rel="noopener" style="--accent: ${itemAccent}" aria-label="Read from ${escapeHTML(item.sourceName || 'the source')}: ${title}">
      <div class="card-media">
        ${media}
        <span class="card-chip" aria-hidden="true">${escapeHTML(item.sourceName || 'Chiefs')}</span>
      </div>
      <div class="card-body">
        <div class="card-meta">
          <span class="dot dot-accent"></span>
          <time datetime="${escapeHTML(item.pubDate || '')}" title="${escapeHTML(item.pubDate ? new Date(item.pubDate).toLocaleString() : '')}">${escapeHTML(timeAgo(item.pubDate))}</time>
        </div>
        <h3 class="card-title">${title}</h3>
        ${summary}
        <span class="card-provenance">${escapeHTML(storyProvenance(item))}</span>
      </div>
    </a>
  `;
}

function renderFeatured(card, item) {
  if (!card) return;
  if (!item) {
    card.setAttribute('href', '/sources/');
    card.removeAttribute('target');
    card.removeAttribute('rel');
    card.innerHTML = `
      <div class="hf-tag">News desk</div>
      <div class="hf-body">
        <span class="source-chip source-chip--lg">No stories yet</span>
        <h2>The feed is warming up.</h2>
        <p>Refresh in a moment, or jump straight to YouTube while the news desk checks the sources.</p>
        <span class="hf-time">loading</span>
        <span class="story-provenance">RSS source</span>
      </div>
    `;
    return;
  }

  card.style.setProperty('--accent', accent(item.sourceAccent));
  card.setAttribute('href', cleanUrl(item.link));
  card.setAttribute('target', '_blank');
  card.setAttribute('rel', 'noopener');
  card.setAttribute('aria-label', `Read from ${item.sourceName || 'the source'}: ${item.title || 'Untitled'}`);
  const image = item.image
    ? `<div class="hf-media"><img src="${cleanUrl(item.image)}" alt="" referrerpolicy="no-referrer" loading="eager" /><div class="hf-shade"></div></div>`
    : '';
  card.innerHTML = `
    <div class="hf-tag">Top story</div>
    ${image}
    <div class="hf-body">
      <span class="source-chip source-chip--lg">${escapeHTML(item.sourceName || 'Chiefs')}</span>
      <h2>${escapeHTML(item.title || 'Untitled')}</h2>
      ${item.summary ? `<p>${escapeHTML(item.summary)}</p>` : ''}
      <span class="hf-time">${escapeHTML(timeAgo(item.pubDate))}</span>
      <div class="hf-actions">
        <span class="hf-read-link">Read story</span>
        <span class="hf-source-link">From ${escapeHTML(item.sourceName || 'source')}</span>
      </div>
      <span class="story-provenance">${escapeHTML(storyProvenance(item))}</span>
    </div>
  `;
}

function renderTicker(track, items) {
  if (!track) return;
  const headlines = items.slice(0, 14);
  if (!headlines.length) {
    track.innerHTML = '<span class="stripe-item"><span class="src" style="--accent: #E31837">Live desk</span> Waiting for fresh Chiefs headlines</span>';
    return;
  }

  track.innerHTML = headlines.concat(headlines).map((item, index) => `
    <a class="stripe-item" href="${cleanUrl(item.link)}" target="_blank" rel="noopener"${index >= headlines.length ? ' aria-hidden="true" tabindex="-1"' : ''}>
      <span class="src" style="--accent: ${accent(item.sourceAccent)}">${escapeHTML(item.sourceName || 'Chiefs')}</span>
      ${escapeHTML(item.title || 'Untitled')}
    </a>
  `).join('');
}

function renderMagazine(target, items) {
  if (!target) return;
  if (items.length < 5) {
    target.innerHTML = '';
    return;
  }

  const lead = items[0];
  const leadImage = lead.image
    ? `<div class="mag-media"><img src="${cleanUrl(lead.image)}" alt="" referrerpolicy="no-referrer" /></div>`
    : `<div class="mag-media">${fallbackMedia(lead.sourceName || 'AP')}</div>`;
  target.innerHTML = `
    <a class="mag mag-lead reveal" href="${cleanUrl(lead.link)}" target="_blank" rel="noopener" style="--accent: ${accent(lead.sourceAccent)}">
      ${leadImage}
      <div class="mag-body">
        ${sourceChip(lead)}
        <h3>${escapeHTML(lead.title || 'Untitled')}</h3>
        ${lead.summary ? `<p>${escapeHTML(lead.summary)}...</p>` : ''}
        <span class="muted small">${escapeHTML(timeAgo(lead.pubDate))}</span>
      </div>
    </a>
    <div class="mag-stack">
      ${items.slice(1, 5).map((item) => {
        const image = item.image
          ? `<div class="mag-media"><img src="${cleanUrl(item.image)}" alt="" referrerpolicy="no-referrer" loading="lazy" /></div>`
          : `<div class="mag-media">${fallbackMedia(item.sourceName || 'AP')}</div>`;
        return `
          <a class="mag mag-row reveal" href="${cleanUrl(item.link)}" target="_blank" rel="noopener" style="--accent: ${accent(item.sourceAccent)}">
            ${image}
            <div class="mag-body">
              ${sourceChip(item)}
              <h3>${escapeHTML(item.title || 'Untitled')}</h3>
              <span class="muted small">${escapeHTML(timeAgo(item.pubDate))}</span>
            </div>
          </a>
        `;
      }).join('')}
    </div>
  `;
}

function renderCards(target, items) {
  if (!target) return;
  target.innerHTML = items.length
    ? items.map((item, index) => cardHTML(item, index === 0 ? 'wide' : '')).join('')
    : emptyState('No stories yet', 'The feed API is reachable, but none of the sources returned Chiefs stories yet.');
}

function bySourceHTML(groups, limit = 4) {
  return groups.map((group) => {
    const source = group.source || {};
    const items = Array.isArray(group.items) ? group.items.slice(0, limit) : [];
    return `
      <div class="bysource-col reveal" style="--accent: ${accent(source.accent)}">
        <header>
          <a href="${cleanUrl(source.url)}" target="_blank" rel="noopener" class="bysource-name">${escapeHTML(source.name || 'Source')}</a>
          <span class="muted small">${escapeHTML(source.tagline || '')}</span>
        </header>
        <ul>
          ${items.length ? items.map((item) => `
            <li>
              <a href="${cleanUrl(item.link)}" target="_blank" rel="noopener">${escapeHTML(item.title || 'Untitled')}</a>
              <span class="muted small">${escapeHTML(timeAgo(item.pubDate))}</span>
            </li>
          `).join('') : '<li class="muted">No recent matching Chiefs stories.</li>'}
        </ul>
      </div>
    `;
  }).join('');
}

function renderBySource(target, groups, limit = 4) {
  if (!target) return;
  target.innerHTML = groups.length
    ? bySourceHTML(groups, limit)
    : emptyState('No sources loaded', 'The source list is available, but the feed API has not returned grouped headlines yet.');
}

function videoEmbedUrl(videoId, autoplay = false) {
  const id = String(videoId || '').trim();
  if (!id) return '';
  return `https://www.youtube-nocookie.com/embed/${encodeURIComponent(id)}?rel=0&playsinline=1${autoplay ? '&autoplay=1' : ''}`;
}

function formatCount(value) {
  const count = Number(value);
  if (!Number.isFinite(count) || count <= 0) return '';
  return new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 }).format(count);
}

function renderVideoStage(video, autoplay = false) {
  const stage = $('[data-youtube-stage]');
  if (!stage) return;
  const embed = videoEmbedUrl(video.videoId, autoplay);
  if (!embed) {
    stage.innerHTML = `
      <div class="yt-stage-poster">
        <span class="yt-stage-label">Now playing</span>
        <strong>Videos are warming up.</strong>
        <p>Jump to the channel while the YouTube feed catches up.</p>
        <a class="btn btn-yt" href="${YOUTUBE_URL}" target="_blank" rel="noopener">Visit channel</a>
      </div>
    `;
    return;
  }

  const title = escapeHTML(video.title || 'Arrowhead Paesano video');
  const views = formatCount(video.views);

  if (!autoplay) {
    const poster = video.thumbnail
      ? `<img src="${cleanUrl(video.thumbnail)}" alt="" referrerpolicy="no-referrer" />`
      : '';
    stage.innerHTML = `
      <button class="yt-stage-poster yt-stage-poster--video" type="button" data-stage-play aria-label="Play ${title}">
        ${poster}
        <span class="yt-stage-poster__shade" aria-hidden="true"></span>
        <span class="yt-stage-poster__play" aria-hidden="true">▶</span>
        <span class="yt-stage-poster__copy">
          <small>Featured full episode</small>
          <strong>${title}</strong>
        </span>
      </button>
      <div class="yt-stage-meta">
        <span class="yt-stage-label">Ready to play</span>
        <div><span>${escapeHTML(timeAgo(video.pubDate))}</span>${views ? `<span>${escapeHTML(views)} views</span>` : ''}<a href="${cleanUrl(video.link || YOUTUBE_URL)}" target="_blank" rel="noopener">Open on YouTube</a></div>
      </div>
    `;
    const play = $('[data-stage-play]', stage);
    if (play) play.addEventListener('click', () => renderVideoStage(video, true));
    return;
  }

  stage.innerHTML = `
      <div class="yt-stage-frame">
        <iframe
          src="${embed}"
          title="${title}"
          loading="eager"
          allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
          referrerpolicy="strict-origin-when-cross-origin"
          allowfullscreen></iframe>
      </div>
      <div class="yt-stage-meta">
        <span class="yt-stage-label">Now playing</span>
        <h2>${title}</h2>
        <div><span>${escapeHTML(timeAgo(video.pubDate))}</span>${views ? `<span>${escapeHTML(views)} views</span>` : ''}<a href="${cleanUrl(video.link || YOUTUBE_URL)}" target="_blank" rel="noopener">Open on YouTube</a></div>
      </div>`;
}

function videoCardHTML(video, index, options = {}) {
  const href = cleanUrl(video.link);
  const thumb = video.thumbnail ? `<img src="${cleanUrl(video.thumbnail)}" alt="" loading="lazy" />` : '';
  const featureClass = index === 0 ? ' yt-card--feature' : '';
  const title = escapeHTML(video.title || 'Untitled video');
  const views = formatCount(video.views);
  const body = `
    <div class="yt-thumb">${thumb}<span class="yt-play" aria-hidden="true">▶</span></div>
    <div class="yt-body">
      <h3>${title}</h3>
      <span class="yt-time">${escapeHTML(timeAgo(video.pubDate))}${views ? ` · ${escapeHTML(views)} views` : ''}</span>
    </div>
  `;

  if (options.interactive) {
    return `
      <button
        class="yt-card${featureClass} reveal"
        type="button"
        data-video-card
        data-video-id="${escapeHTML(video.videoId || '')}"
        data-video-title="${title}"
        data-video-pub="${escapeHTML(video.pubDate || '')}"
        data-video-thumb="${escapeHTML(video.thumbnail || '')}"
        data-video-link="${escapeHTML(href)}"
        aria-label="Play ${title}">
        ${body}
      </button>
    `;
  }

  return `
    <a class="yt-card${featureClass} reveal" href="${href}" target="_blank" rel="noopener">
      ${body}
    </a>
  `;
}

function renderVideos(target, videos, limit = 3, options = {}) {
  if (!target) return;
  target.innerHTML = videos.length
    ? videos.slice(0, limit).map((video, index) => videoCardHTML(video, index, options)).join('')
    : emptyState(
      'Videos are warming up',
      'Refresh in a moment, or visit the channel directly.',
      `<a class="btn btn-yt" href="${YOUTUBE_URL}" target="_blank" rel="noopener">Visit channel</a>`
    );

  if (options.interactive && videos.length) {
    const featured = videos.find((video) => video.videoId === options.featuredId) || videos[0];
    renderVideoStage(featured, false);
  }
}

function shortCardHTML(video) {
  const title = escapeHTML(video.title || 'Arrowhead Paesano Short');
  const views = formatCount(video.views);
  return `
    <a class="short-card" href="${cleanUrl(video.link)}" target="_blank" rel="noopener" aria-label="Watch Short on YouTube: ${title}">
      <div class="short-card__media">
        ${video.thumbnail ? `<img src="${cleanUrl(video.thumbnail)}" alt="" loading="lazy" referrerpolicy="no-referrer" />` : ''}
        <span aria-hidden="true">▶</span>
      </div>
      <div class="short-card__body"><h3>${title}</h3><small>${escapeHTML(timeAgo(video.pubDate))}${views ? ` · ${escapeHTML(views)} views` : ''}</small></div>
    </a>`;
}

function renderShorts(target, videos) {
  if (!target) return;
  target.innerHTML = videos.length
    ? videos.map(shortCardHTML).join('')
    : emptyState('No recent Shorts', 'Visit the channel to browse every quick take.');
}

function merchCardHTML(item, options = {}) {
  const configured = Boolean(options.configured);
  const palette = Array.isArray(item.palette) && item.palette.length ? item.palette.join(', ') : '#E31837, #FFB81C';
  const image = item.image
    ? `<img src="${cleanUrl(item.image)}" alt="${escapeHTML(item.imageAlt || item.name || item.title || '')}" loading="lazy" referrerpolicy="no-referrer" />`
    : (!configured ? '<img class="merch-preview-logo" src="/images/channel/logo.webp" alt="" loading="lazy" />' : '');
  const badgeText = configured ? (item.badge || 'Shopify') : 'Collection preview';
  const badge = `<span class="merch-badge">${escapeHTML(badgeText)}</span>`;
  let action = '<span class="shop-coming-soon">Opening soon</span>';
  if (configured && item.availableForSale && item.variantId) {
    action = `<button class="btn btn-sm btn-primary" type="button" data-shop-add data-variant-id="${escapeHTML(item.variantId)}">Add to cart</button>`;
  } else if (configured && item.shopUrl) {
    action = `<a class="btn btn-sm btn-primary" href="${cleanUrl(item.shopUrl)}" target="_blank" rel="noopener">View product →</a>`;
  }
  return `
    <article class="merch-card reveal">
      <div class="merch-art" style="background: linear-gradient(135deg, ${escapeHTML(palette)});">
        ${image}
        ${image ? '' : '<span class="merch-mono">AP</span>'}
        ${badge}
      </div>
      <div class="merch-info">
        <h3>${escapeHTML(item.name || 'Paesano merch')}</h3>
        <p>${escapeHTML(item.description || 'Arrowhead Paesano gear for the lot, the couch, and every loud Sunday.')}</p>
        <div class="merch-foot">
          <span class="merch-price">${configured ? escapeHTML(item.price || '') : 'First drop preview'}</span>
          ${action}
        </div>
      </div>
    </article>
  `;
}

function initShopButtons(root = document) {
  $$('[data-shop-add]:not([data-shop-bound])', root).forEach((button) => {
    button.dataset.shopBound = 'true';
    button.addEventListener('click', async () => {
      const original = button.textContent;
      button.disabled = true;
      button.textContent = 'Creating checkout…';
      try {
        const response = await fetch('/api/shopify-cart.json', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ lines: [{ variantId: button.dataset.variantId, quantity: 1 }] }),
        });
        const data = await response.json();
        if (!response.ok || !data.checkoutUrl) throw new Error('Checkout unavailable');
        window.location.assign(cleanUrl(data.checkoutUrl));
      } catch (_) {
        button.disabled = false;
        button.textContent = 'Try checkout again';
        setTimeout(() => { button.textContent = original; }, 2600);
      }
    });
  });
}

async function loadShopPage() {
  const grid = $('[data-shop-grid]');
  if (!grid) return;
  const status = $('[data-shop-status]');
  const note = $('[data-shop-note]');

  try {
    const data = await fetchJSON('/api/shopify-products.json');
    const products = Array.isArray(data.products) ? data.products : [];
    grid.innerHTML = products.map((item) => merchCardHTML(item, { configured: data.configured })).join('');
    if (status) {
      status.textContent = data.configured ? (data.stale ? 'Shopify connected · cached catalog' : 'Shopify store connected') : 'Store preview · Shopify credentials needed';
      status.dataset.state = data.configured ? 'live' : 'fallback';
    }
    if (note) note.textContent = data.configured
      ? 'Products and checkout are securely connected through Shopify.'
      : 'These concepts are previews. Add the Shopify Storefront credentials to publish live products and checkout.';
    initDynamicUI(grid);
    initShopButtons(grid);
  } catch (_) {
    if (status) {
      status.textContent = 'Store preview · connection unavailable';
      status.dataset.state = 'fallback';
    }
  }
}

function amazonFindCardHTML(item) {
  const title = escapeHTML(item.title || 'Chiefs fan gear');
  const category = escapeHTML(item.category || 'Fan find');
  const mark = escapeHTML(item.mark || 'KC');
  const description = escapeHTML(item.description || 'Browse this Chiefs shopping category on Amazon.');
  return `
    <article class="amazon-find-card reveal" style="--find-accent:${accent(item.accent)}">
      <div class="amazon-find-card__top"><span>${category}</span><b aria-hidden="true">${mark}</b></div>
      <div class="amazon-find-card__body"><h3>${title}</h3><p>${description}</p></div>
      <a href="${cleanUrl(item.url)}" target="_blank" rel="sponsored noopener" aria-label="Browse ${title} on Amazon, paid affiliate link">Browse on Amazon <small>(paid link)</small><span aria-hidden="true">↗</span></a>
    </article>`;
}

async function loadAmazonFinds() {
  const grid = $('[data-amazon-grid]');
  if (!grid) return;
  const note = $('[data-amazon-note]');
  try {
    const data = await fetchJSON('/api/amazon-finds.json');
    const items = Array.isArray(data.items) ? data.items : [];
    if (!items.length) return;
    grid.innerHTML = items.map(amazonFindCardHTML).join('');
    if (note) note.textContent = 'Paid links open on Amazon. Purchases, prices, availability, shipping, and returns are handled by Amazon.';
    initDynamicUI(grid);
  } catch (_) {
    // The server-rendered, non-tagged Amazon search links remain usable.
  }
}

function feedSignature(items) {
  return items.slice(0, 10).map((item) => `${item.link}|${item.pubDate}`).join('::');
}

let lastHomeSignature = '';
let homeNewsItems = [];

function applyNewsFilters() {
  const target = $('[data-card-grid]');
  if (!target) return;
  const query = String($('[data-news-search]')?.value || '').trim().toLowerCase();
  const source = String($('[data-news-source]')?.value || '');
  const filtered = homeNewsItems.filter((item) => {
    const matchesSource = !source || item.source === source;
    const haystack = `${item.title || ''} ${item.summary || ''} ${item.sourceName || ''}`.toLowerCase();
    return matchesSource && (!query || haystack.includes(query));
  });
  if (!filtered.length) {
    target.innerHTML = emptyState('No matching headlines', 'Try a broader search or switch back to all publishers.');
  } else {
    renderCards(target, filtered.slice(0, 16));
  }
  initDynamicUI(target);
}

function initNewsToolbar() {
  const toolbar = $('[data-news-toolbar]');
  if (!toolbar || toolbar.dataset.bound) return;
  toolbar.dataset.bound = 'true';
  toolbar.addEventListener('input', applyNewsFilters);
  toolbar.addEventListener('change', applyNewsFilters);
  toolbar.addEventListener('reset', () => setTimeout(applyNewsFilters, 0));
}

async function loadHome(silent = false) {
  const home = $('[data-feed-home]');
  if (!home) return;

  const [feedResult, youtubeResult] = await Promise.allSettled([
      fetchJSON('/api/feed.json'),
      fetchJSON('/api/youtube.json'),
  ]);

  if (feedResult.status === 'fulfilled') {
    const feed = feedResult.value;
    const items = Array.isArray(feed.items) ? feed.items : [];
    const signature = feedSignature(items);
    const featured = items[0] || null;
    homeNewsItems = items.slice(1);

    renderFeatured($('[data-featured-card]'), featured);
    renderTicker($('[data-ticker-track]'), items);
    applyNewsFilters();
    const status = $('[data-feed-status]');
    if (status) status.innerHTML = `<span></span>${silent && signature && lastHomeSignature && signature !== lastHomeSignature ? 'New headlines just landed' : `Updated ${escapeHTML(timeAgo(feed.updated))}`}`;

    lastHomeSignature = signature || lastHomeSignature;
  } else if (!silent) {
    renderFeatured($('[data-featured-card]'), null);
    renderCards($('[data-card-grid]'), []);
  }

  if (youtubeResult.status === 'fulfilled') {
    const youtube = youtubeResult.value;
    const videos = Array.isArray(youtube.videos) ? youtube.videos : [];
    const latestEpisode = videos.find((video) => !video.isShort);
    const homeVideos = latestEpisode
      ? [latestEpisode, ...videos.filter((video) => video.videoId !== latestEpisode.videoId)]
      : videos;
    renderVideos($('[data-youtube-grid]'), homeVideos, 4);
  } else if (!silent) {
    renderVideos($('[data-youtube-grid]'), [], 4);
  }

  initDynamicUI(document);
}

async function loadSourcesPage() {
  const grid = $('[data-sources-grid]');
  if (!grid) return;
  try {
    const data = await fetchJSON('/api/by-source.json');
    renderBySource(grid, Array.isArray(data.groups) ? data.groups : [], 20);
    initDynamicUI(grid);
  } catch (_) {
    grid.innerHTML = emptyState('Sources unavailable', 'The live source API could not be reached. Try refreshing in a moment.');
  }
}

async function loadYouTubePage() {
  const grid = $('[data-youtube-page-grid]');
  if (!grid) return;
  const shortsGrid = $('[data-youtube-shorts-grid]');
  try {
    const data = await fetchJSON('/api/youtube.json');
    const videos = Array.isArray(data.videos) ? data.videos : [];
    const longForm = videos.filter((video) => !video.isShort);
    const shorts = videos.filter((video) => video.isShort);
    const page = $('[data-youtube-page]');
    renderVideos(grid, longForm.length ? longForm : videos, 15, {
      interactive: true,
      featuredId: page ? page.dataset.featuredVideoId : '',
    });
    renderShorts(shortsGrid, shorts);
    initDynamicUI(grid);
    initDynamicUI(shortsGrid);
  } catch (_) {
    grid.innerHTML = emptyState(
      'Videos unavailable',
      'The YouTube feed could not be reached. You can still visit the channel directly.',
      `<a class="btn btn-yt" href="${YOUTUBE_URL}" target="_blank" rel="noopener">Visit channel</a>`
    );
    if (shortsGrid) {
      shortsGrid.innerHTML = emptyState(
        'Shorts unavailable',
        'Open the channel to catch every quick take.'
      );
    }
  }
}

function initMobileNav() {
  const toggle = $('.nav-toggle');
  const nav = $('.primary-nav');
  if (!toggle || !nav || toggle.dataset.bound) return;
  toggle.dataset.bound = 'true';
  function setOpen(open) {
    nav.classList.toggle('open', open);
    toggle.setAttribute('aria-expanded', String(open));
    toggle.setAttribute('aria-label', open ? 'Close navigation' : 'Open navigation');
  }
  toggle.addEventListener('click', () => {
    setOpen(!nav.classList.contains('open'));
  });
  nav.addEventListener('click', (event) => {
    if (event.target.closest('a')) setOpen(false);
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && nav.classList.contains('open')) {
      setOpen(false);
      toggle.focus();
    }
  });
}

function initScrollProgress() {
  const bar = $('.scroll-progress-bar');
  const header = $('[data-header]');
  function onScroll() {
    const h = document.documentElement;
    const scrolled = h.scrollTop;
    const max = h.scrollHeight - h.clientHeight;
    if (bar && max > 0) bar.style.width = `${(scrolled / max) * 100}%`;
    if (header) header.classList.toggle('is-scrolled', scrolled > 4);
  }
  document.addEventListener('scroll', onScroll, { passive: true });
  onScroll();
}

let revealObserver;

function initReveal(root = document) {
  const els = $$('.reveal:not([data-reveal-bound])', root);
  if (!els.length) return;
  if (!('IntersectionObserver' in window)) {
    els.forEach((el) => el.classList.add('is-in'));
    return;
  }

  if (!revealObserver) {
    revealObserver = new IntersectionObserver((entries) => {
      entries.forEach((entry, index) => {
        if (entry.isIntersecting) {
          entry.target.style.transitionDelay = `${Math.min(index * 40, 240)}ms`;
          entry.target.classList.add('is-in');
          revealObserver.unobserve(entry.target);
        }
      });
    }, { threshold: 0.08, rootMargin: '0px 0px -8% 0px' });
  }

  els.forEach((el) => {
    el.dataset.revealBound = 'true';
    revealObserver.observe(el);
  });
}

function initButtonSpotlight(root = document) {
  $$('.btn:not([data-spotlight-bound])', root).forEach((btn) => {
    btn.dataset.spotlightBound = 'true';
    btn.addEventListener('pointermove', (event) => {
      const rect = btn.getBoundingClientRect();
      btn.style.setProperty('--mx', `${event.clientX - rect.left}px`);
      btn.style.setProperty('--my', `${event.clientY - rect.top}px`);
    });
  });
}

function initImageHandling(root = document) {
  $$('.card-media img, .mag-media img, .hf-media img, .yt-thumb img', root).forEach((img) => {
    if (img.dataset.imageBound) return;
    img.dataset.imageBound = 'true';
    if (img.complete && img.naturalWidth > 0) return;
    img.style.opacity = '0';
    img.style.transition = 'opacity .5s ease';
    img.addEventListener('load', () => { img.style.opacity = '1'; });
    img.addEventListener('error', () => {
      const card = img.closest('.card');
      if (card) {
        const media = img.closest('.card-media');
        if (media) media.classList.add('card-media--noimg');
      }
      img.remove();
    });
  });
}

function initVideoCards(root = document) {
  $$('[data-video-card]:not([data-video-bound])', root).forEach((card) => {
    card.dataset.videoBound = 'true';
    card.addEventListener('click', () => {
      renderVideoStage({
        videoId: card.dataset.videoId,
        title: card.dataset.videoTitle,
        pubDate: card.dataset.videoPub,
        thumbnail: card.dataset.videoThumb,
        link: card.dataset.videoLink,
      }, true);
      $$('[data-video-card].is-playing').forEach((item) => item.classList.remove('is-playing'));
      card.classList.add('is-playing');
      const hero = $('.watch-hero') || $('.youtube-hero');
      if (hero) hero.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  });
}

function initLogoOverride() {
  $$('img[data-png-override]:not([data-logo-bound])').forEach((img) => {
    img.dataset.logoBound = 'true';
    const png = img.getAttribute('data-png-override');
    if (!png) return;
    const probe = new Image();
    probe.onload = () => { img.src = png; };
    probe.src = png;
  });
}

let xWidgetsScriptLoading = false;

function initXWidgets(root = document) {
  const feeds = $$('[data-x-feed]:not([data-x-bound])', root);
  if (!feeds.length) return;

  feeds.forEach((feed) => {
    feed.dataset.xBound = 'true';
  });

  const loadWidgets = () => {
    if (window.twttr && window.twttr.widgets && typeof window.twttr.widgets.load === 'function') {
      window.twttr.widgets.load(root);
    }
  };

  if (document.querySelector('script[src*="platform.twitter.com/widgets.js"]')) {
    loadWidgets();
    return;
  }

  if (xWidgetsScriptLoading) return;
  xWidgetsScriptLoading = true;
  const script = document.createElement('script');
  script.async = true;
  script.charset = 'utf-8';
  script.src = 'https://platform.twitter.com/widgets.js';
  script.onload = loadWidgets;
  document.head.appendChild(script);
}

function initDynamicUI(root = document) {
  initReveal(root);
  initButtonSpotlight(root);
  initImageHandling(root);
  initVideoCards(root);
  initShopButtons(root);
  initXWidgets(root);
}

initMobileNav();
initScrollProgress();
initDynamicUI(document);
initLogoOverride();

if ($('[data-feed-home]')) {
  initNewsToolbar();
  loadHome();
  setInterval(() => loadHome(true), FEED_REFRESH_MS);
}

loadSourcesPage();
loadYouTubePage();
loadShopPage();
loadAmazonFinds();
