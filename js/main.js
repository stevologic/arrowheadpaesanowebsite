const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

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

let xWidgetsReady = null;
const NRT_X_FALLBACK_MS = 5000;

function ensureTwitterWidgets() {
  if (window.twttr?.widgets) {
    return Promise.resolve(window.twttr);
  }
  if (xWidgetsReady) {
    return xWidgetsReady;
  }
  xWidgetsReady = new Promise((resolve, reject) => {
    const existing = document.querySelector('script[src*="platform.twitter.com/widgets.js"]');
    const done = () => {
      if (window.twttr?.widgets) {
        resolve(window.twttr);
        return;
      }
      reject(new Error('X widgets unavailable'));
    };
    const fail = () => reject(new Error('X widgets blocked'));
    if (existing) {
      if (window.twttr?.widgets) {
        resolve(window.twttr);
        return;
      }
      existing.addEventListener('load', done, { once: true });
      existing.addEventListener('error', fail, { once: true });
      return;
    }
    const script = document.createElement('script');
    script.async = true;
    script.charset = 'utf-8';
    script.src = 'https://platform.twitter.com/widgets.js';
    script.onload = done;
    script.onerror = fail;
    document.head.appendChild(script);
  });
  return xWidgetsReady;
}

function nrtXHasRendered(el) {
  return !!(el.querySelector('iframe') || el.querySelector('twitter-widget'));
}

function nrtXWidgetFromRendered(event) {
  // widgets.js 'rendered' fires an Event; contains() needs a Node.
  const target = event && event.target;
  if (target && typeof target.nodeType === 'number') return target;
  if (event && typeof event.nodeType === 'number') return event;
  return null;
}

function nrtXMarkReady(el) {
  if (el._nrtXFallbackTimer) {
    clearTimeout(el._nrtXFallbackTimer);
    el._nrtXFallbackTimer = null;
  }
  el.classList.remove('is-loading', 'is-fallback');
  el.classList.add('is-ready');
  const fallback = el.querySelector('.nrt-x-embed__fallback');
  if (fallback) fallback.hidden = true;
}

function nrtXMarkFallback(el) {
  if (el.classList.contains('is-ready') || nrtXHasRendered(el)) {
    nrtXMarkReady(el);
    return;
  }
  if (el._nrtXFallbackTimer) {
    clearTimeout(el._nrtXFallbackTimer);
    el._nrtXFallbackTimer = null;
  }
  el.classList.remove('is-loading');
  el.classList.add('is-fallback');
  const fallback = el.querySelector('.nrt-x-embed__fallback');
  if (fallback) fallback.hidden = false;
}

function nrtXWatchRender(el) {
  if (nrtXHasRendered(el)) {
    nrtXMarkReady(el);
    return;
  }
  const mo = new MutationObserver(() => {
    if (nrtXHasRendered(el)) {
      mo.disconnect();
      nrtXMarkReady(el);
    }
  });
  mo.observe(el, { childList: true, subtree: true });
  window.twttr?.events?.bind?.('rendered', (event) => {
    const widget = nrtXWidgetFromRendered(event);
    if (widget && (el === widget || el.contains(widget))) {
      mo.disconnect();
      nrtXMarkReady(el);
    }
  });
}

function initNarrativeXEmbeds(root = document) {
  const embeds = $$('[data-nrt-x-embed]:not([data-nrt-x-bound])', root);
  if (!embeds.length) return;
  embeds.forEach((el) => { el.dataset.nrtXBound = 'true'; });

  const hydrate = (el) => {
    if (el.dataset.nrtXHydrated === 'true') return;
    el.dataset.nrtXHydrated = 'true';
    el.classList.add('is-loading');
    el._nrtXFallbackTimer = setTimeout(() => nrtXMarkFallback(el), NRT_X_FALLBACK_MS);
    ensureTwitterWidgets()
      .then((twttr) => {
        nrtXWatchRender(el);
        return twttr?.widgets?.load?.(el);
      })
      .then(() => {
        if (nrtXHasRendered(el)) nrtXMarkReady(el);
      })
      .catch(() => nrtXMarkFallback(el));
  };

  if (typeof IntersectionObserver !== 'function') {
    embeds.forEach(hydrate);
    return;
  }

  const io = new IntersectionObserver((entries, observer) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      hydrate(entry.target);
      observer.unobserve(entry.target);
    });
  }, { rootMargin: '240px 0px', threshold: 0.01 });

  embeds.forEach((el) => io.observe(el));
}

function initDynamicUI(root = document) {
  initReveal(root);
  initButtonSpotlight(root);
  initImageHandling(root);
  initNarrativeXEmbeds(root);
}

initMobileNav();
initScrollProgress();
initDynamicUI(document);
