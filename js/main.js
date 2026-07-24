const YOUTUBE_URL = 'https://www.youtube.com/@arrowheadpaesano';

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

function escapeHTML(value) {
  return String(value ?? '').replace(/[&<>"']/g, (character) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  }[character]));
}

function cleanUrl(value) {
  try {
    const url = new URL(String(value || ''), window.location.href);
    return ['http:', 'https:'].includes(url.protocol) ? url.href : '#';
  } catch (_) {
    return '#';
  }
}

function readEmbeddedJSON(id, fallback = {}) {
  const script = document.getElementById(id);
  if (!script) return fallback;
  try {
    return JSON.parse(script.textContent || '');
  } catch (_) {
    return fallback;
  }
}

function timeAgo(dateString) {
  if (!dateString) return '';
  const then = new Date(dateString).getTime();
  if (!Number.isFinite(then)) return '';
  const minutes = Math.max(0, Math.floor((Date.now() - then) / 60000));
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(dateString).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: new Date(dateString).getFullYear() === new Date().getFullYear() ? undefined : 'numeric',
  });
}

function formatCount(value) {
  const count = Number(value);
  if (!Number.isFinite(count) || count <= 0) return '';
  return new Intl.NumberFormat('en-US', {
    notation: 'compact',
    maximumFractionDigits: 1,
  }).format(count);
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

function channelVideos() {
  const feed = readEmbeddedJSON('site-channel-feed', {});
  return Array.isArray(feed) ? feed : (Array.isArray(feed.videos) ? feed.videos : []);
}

function videoEmbedUrl(videoId, autoplay = false) {
  const id = String(videoId || '').trim();
  if (!id) return '';
  return `https://www.youtube-nocookie.com/embed/${encodeURIComponent(id)}?rel=0&playsinline=1${autoplay ? '&autoplay=1' : ''}`;
}

function renderVideoStage(video, autoplay = false) {
  const stage = $('[data-youtube-stage]');
  if (!stage) return;

  const embed = videoEmbedUrl(video && video.videoId, autoplay);
  if (!embed) {
    stage.innerHTML = `
      <div class="yt-stage-poster">
        <span class="yt-stage-label">Channel archive</span>
        <strong>Open Arrowhead Paesano on YouTube.</strong>
        <p>The site snapshot does not have a featured episode yet.</p>
        <a class="btn btn-yt" href="${YOUTUBE_URL}" target="_blank" rel="noopener">Visit channel</a>
      </div>
    `;
    return;
  }

  const title = escapeHTML(video.title || 'Arrowhead Paesano video');
  const views = formatCount(video.views);
  const link = cleanUrl(video.link || YOUTUBE_URL);

  if (!autoplay) {
    const poster = video.thumbnail
      ? `<img src="${cleanUrl(video.thumbnail)}" alt="" referrerpolicy="no-referrer" />`
      : '';
    stage.innerHTML = `
      <button class="yt-stage-poster yt-stage-poster--video" type="button" data-stage-play aria-label="Play ${title}">
        ${poster}
        <span class="yt-stage-poster__shade" aria-hidden="true"></span>
        <span class="yt-stage-poster__play" aria-hidden="true">▶</span>
        <span class="yt-stage-poster__copy"><small>Featured full episode</small><strong>${title}</strong></span>
      </button>
      <div class="yt-stage-meta">
        <span class="yt-stage-label">Ready to play</span>
        <div><span>${escapeHTML(timeAgo(video.pubDate))}</span>${views ? `<span>${escapeHTML(views)} views</span>` : ''}<a href="${link}" target="_blank" rel="noopener">Open on YouTube</a></div>
      </div>
    `;
    $('[data-stage-play]', stage)?.addEventListener('click', () => renderVideoStage(video, true));
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
      <div><span>${escapeHTML(timeAgo(video.pubDate))}</span>${views ? `<span>${escapeHTML(views)} views</span>` : ''}<a href="${link}" target="_blank" rel="noopener">Open on YouTube</a></div>
    </div>
  `;
}

function videoCardHTML(video, index, options = {}) {
  const href = cleanUrl(video.link || YOUTUBE_URL);
  const title = escapeHTML(video.title || 'Arrowhead Paesano video');
  const views = formatCount(video.views);
  const featureClass = index === 0 ? ' yt-card--feature' : '';
  const thumbnail = video.thumbnail
    ? `<img src="${cleanUrl(video.thumbnail)}" alt="" loading="lazy" referrerpolicy="no-referrer" />`
    : '';
  const body = `
    <div class="yt-thumb">${thumbnail}<span class="yt-play" aria-hidden="true">▶</span></div>
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

  return `<a class="yt-card${featureClass} reveal" href="${href}" target="_blank" rel="noopener">${body}</a>`;
}

function renderVideos(target, videos, limit = 3, options = {}) {
  if (!target) return;
  const chosen = videos.slice(0, limit);
  target.innerHTML = chosen.length
    ? chosen.map((video, index) => videoCardHTML(video, index, options)).join('')
    : emptyState(
      'Watch on YouTube',
      'The local channel snapshot is empty, but every upload is still available on YouTube.',
      `<a class="btn btn-yt" href="${YOUTUBE_URL}" target="_blank" rel="noopener">Visit channel</a>`
    );

  if (options.interactive && chosen.length) {
    const featured = chosen.find((video) => video.videoId === options.featuredId) || chosen[0];
    renderVideoStage(featured, false);
  }
}

function shortCardHTML(video) {
  const title = escapeHTML(video.title || 'Arrowhead Paesano Short');
  const views = formatCount(video.views);
  return `
    <a class="short-card reveal" href="${cleanUrl(video.link || `${YOUTUBE_URL}/shorts`)}" target="_blank" rel="noopener" aria-label="Watch Short on YouTube: ${title}">
      <div class="short-card__media">
        ${video.thumbnail ? `<img src="${cleanUrl(video.thumbnail)}" alt="" loading="lazy" referrerpolicy="no-referrer" />` : ''}
        <span aria-hidden="true">▶</span>
      </div>
      <div class="short-card__body"><h3>${title}</h3><small>${escapeHTML(timeAgo(video.pubDate))}${views ? ` · ${escapeHTML(views)} views` : ''}</small></div>
    </a>
  `;
}

function renderShorts(target, videos) {
  if (!target) return;
  target.innerHTML = videos.length
    ? videos.map(shortCardHTML).join('')
    : emptyState(
      'Browse every Short',
      'No Shorts are included in this snapshot yet.',
      `<a class="btn btn-yt" href="${YOUTUBE_URL}/shorts" target="_blank" rel="noopener">Open Shorts</a>`
    );
}

function loadYouTubeSnapshot() {
  const videos = channelVideos();
  const homeGrid = $('[data-youtube-grid]');
  if (homeGrid) {
    const latestEpisode = videos.find((video) => !video.isShort);
    const homeVideos = latestEpisode
      ? [latestEpisode, ...videos.filter((video) => video.videoId !== latestEpisode.videoId)]
      : videos;
    renderVideos(homeGrid, homeVideos, 4);
    initDynamicUI(homeGrid);
  }

  const pageGrid = $('[data-youtube-page-grid]');
  if (!pageGrid) return;
  const page = $('[data-youtube-page]');
  const longForm = videos.filter((video) => !video.isShort);
  const shorts = videos.filter((video) => video.isShort);
  renderVideos(pageGrid, longForm.length ? longForm : videos, 15, {
    interactive: true,
    featuredId: page?.dataset.featuredVideoId || '',
  });
  renderShorts($('[data-youtube-shorts-grid]'), shorts);
  initDynamicUI(pageGrid);
  initDynamicUI($('[data-youtube-shorts-grid]') || document);
}

function initSourceDirectory() {
  const toolbar = $('[data-news-toolbar]');
  const cards = $$('[data-source-card]');
  if (!toolbar || !cards.length || toolbar.dataset.bound) return;
  toolbar.dataset.bound = 'true';

  const applyFilters = () => {
    const query = String($('[data-news-search]', toolbar)?.value || '').trim().toLowerCase();
    const category = String($('[data-news-source]', toolbar)?.value || '');
    let visible = 0;

    cards.forEach((card) => {
      const matchesQuery = !query || String(card.dataset.sourceSearch || '').includes(query);
      const matchesCategory = !category || card.dataset.sourceCategory === category;
      card.hidden = !(matchesQuery && matchesCategory);
      if (!card.hidden) visible += 1;
    });

    const empty = $('[data-source-empty]');
    if (empty) empty.hidden = visible > 0;
  };

  toolbar.addEventListener('input', applyFilters);
  toolbar.addEventListener('change', applyFilters);
  toolbar.addEventListener('reset', () => window.setTimeout(applyFilters, 0));
  applyFilters();
}

function palette(value) {
  const colors = Array.isArray(value) ? value : [];
  const safe = colors
    .map((color) => String(color || '').trim())
    .filter((color) => /^#[0-9a-f]{3,8}$/i.test(color))
    .slice(0, 4);
  return safe.length ? safe.join(', ') : '#E31837, #FFB81C';
}

function merchCardHTML(item) {
  const image = item.image
    ? `<img src="${cleanUrl(item.image)}" alt="${escapeHTML(item.imageAlt || item.name || '')}" loading="lazy" referrerpolicy="no-referrer" />`
    : '';
  const action = item.availableForSale && item.variantId
    ? `<button class="btn btn-sm btn-primary" type="button" data-shop-add data-variant-id="${escapeHTML(item.variantId)}">Add to cart</button>`
    : `<a class="btn btn-sm btn-primary" href="${cleanUrl(item.shopUrl)}" target="_blank" rel="noopener">View product →</a>`;
  return `
    <article class="merch-card reveal">
      <div class="merch-art" style="background:linear-gradient(135deg, ${palette(item.palette)});">
        ${image || '<span class="merch-mono">AP</span>'}
        <span class="merch-badge">${escapeHTML(item.badge || 'Shopify')}</span>
      </div>
      <div class="merch-info">
        <h3>${escapeHTML(item.name || 'Paesano merch')}</h3>
        <p>${escapeHTML(item.description || 'Arrowhead Paesano gear for loud Sundays.')}</p>
        <div class="merch-foot"><span class="merch-price">${escapeHTML(item.price || '')}</span>${action}</div>
      </div>
    </article>
  `;
}

let shopifyClient = null;

function initShopButtons(root = document) {
  if (!shopifyClient) return;
  $$('[data-shop-add]:not([data-shop-bound])', root).forEach((button) => {
    button.dataset.shopBound = 'true';
    button.addEventListener('click', async () => {
      const original = button.textContent;
      button.disabled = true;
      button.textContent = 'Creating checkout…';
      try {
        const cart = await shopifyClient.createCart([{
          variantId: button.dataset.variantId,
          quantity: 1,
        }]);
        window.location.assign(cleanUrl(cart.checkoutUrl));
      } catch (_) {
        button.disabled = false;
        button.textContent = 'Try checkout again';
        window.setTimeout(() => { button.textContent = original; }, 2600);
      }
    });
  });
}

async function loadShopifyStorefront() {
  const grid = $('[data-shop-grid]');
  if (!grid) return;
  const status = $('[data-shop-status]');
  const note = $('[data-shop-note]');
  const config = readEmbeddedJSON('shopify-config', {});
  const configured = Boolean(String(config.domain || '').trim() && String(config.publicStorefrontToken || '').trim());

  if (!configured || !window.ArrowheadShopify) {
    if (status) {
      status.textContent = 'Store preview · add the public Shopify token when ready';
      status.dataset.state = 'fallback';
    }
    return;
  }

  try {
    shopifyClient = window.ArrowheadShopify.createClient(config);
    const catalog = await shopifyClient.fetchProducts();
    const products = Array.isArray(catalog.products) ? catalog.products : [];
    if (!products.length) throw new Error('Shopify returned no products.');
    grid.innerHTML = products.map(merchCardHTML).join('');
    if (status) {
      status.textContent = 'Shopify storefront connected';
      status.dataset.state = 'live';
    }
    if (note) note.textContent = 'Live products and secure checkout are connected directly to Shopify.';
    initDynamicUI(grid);
  } catch (_) {
    shopifyClient = null;
    if (status) {
      status.textContent = 'Store preview · Shopify connection unavailable';
      status.dataset.state = 'fallback';
    }
    if (note) note.textContent = 'The preview is still available. Check the public Storefront token and store domain in hugo.yaml.';
  }
}

function initMobileNav() {
  const toggle = $('.nav-toggle');
  const nav = $('.primary-nav');
  if (!toggle || !nav || toggle.dataset.bound) return;
  toggle.dataset.bound = 'true';
  const setOpen = (open) => {
    nav.classList.toggle('open', open);
    toggle.setAttribute('aria-expanded', String(open));
    toggle.setAttribute('aria-label', open ? 'Close navigation' : 'Open navigation');
  };
  toggle.addEventListener('click', () => setOpen(!nav.classList.contains('open')));
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
  const update = () => {
    const page = document.documentElement;
    const max = page.scrollHeight - page.clientHeight;
    if (bar) bar.style.width = `${max > 0 ? (page.scrollTop / max) * 100 : 0}%`;
    if (header) header.classList.toggle('is-scrolled', page.scrollTop > 4);
  };
  document.addEventListener('scroll', update, { passive: true });
  update();
}

let revealObserver;

function initReveal(root = document) {
  const elements = $$('.reveal:not([data-reveal-bound])', root);
  if (!elements.length) return;
  if (!('IntersectionObserver' in window)) {
    elements.forEach((element) => element.classList.add('is-in'));
    return;
  }
  if (!revealObserver) {
    revealObserver = new IntersectionObserver((entries) => {
      entries.forEach((entry, index) => {
        if (!entry.isIntersecting) return;
        entry.target.style.transitionDelay = `${Math.min(index * 40, 240)}ms`;
        entry.target.classList.add('is-in');
        revealObserver.unobserve(entry.target);
      });
    }, { threshold: 0.08, rootMargin: '0px 0px -8% 0px' });
  }
  elements.forEach((element) => {
    element.dataset.revealBound = 'true';
    revealObserver.observe(element);
  });
}

function initButtonSpotlight(root = document) {
  $$('.btn:not([data-spotlight-bound])', root).forEach((button) => {
    button.dataset.spotlightBound = 'true';
    button.addEventListener('pointermove', (event) => {
      const rect = button.getBoundingClientRect();
      button.style.setProperty('--mx', `${event.clientX - rect.left}px`);
      button.style.setProperty('--my', `${event.clientY - rect.top}px`);
    });
  });
}

function initImageHandling(root = document) {
  $$('img:not([data-image-bound])', root).forEach((image) => {
    image.dataset.imageBound = 'true';
    image.addEventListener('error', () => {
      const media = image.closest('.card-media');
      if (media) media.classList.add('card-media--noimg');
      image.remove();
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
      $('.watch-hero')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  });
}

function initLogoOverride() {
  $$('img[data-png-override]:not([data-logo-bound])').forEach((image) => {
    image.dataset.logoBound = 'true';
    const source = image.getAttribute('data-png-override');
    if (!source) return;
    const probe = new Image();
    probe.onload = () => { image.src = source; };
    probe.src = source;
  });
}

let xWidgetsScriptLoading = false;

function initXWidgets(root = document) {
  const feeds = $$('[data-x-feed]:not([data-x-bound])', root);
  if (!feeds.length) return;
  feeds.forEach((feed) => { feed.dataset.xBound = 'true'; });
  const loadWidgets = () => window.twttr?.widgets?.load?.(root);
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
initSourceDirectory();
initDynamicUI(document);
initLogoOverride();
loadYouTubeSnapshot();
loadShopifyStorefront();
