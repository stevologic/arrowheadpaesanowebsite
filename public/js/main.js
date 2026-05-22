const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

function initMobileNav() {
  const toggle = $('.nav-toggle');
  const nav = $('.primary-nav');
  if (!toggle || !nav) return;

  toggle.addEventListener('click', () => {
    const open = nav.classList.toggle('open');
    toggle.setAttribute('aria-expanded', String(open));
  });
}

function initScrollProgress() {
  const bar = $('.scroll-progress-bar');
  const header = $('[data-header]');

  function onScroll() {
    const page = document.documentElement;
    const max = page.scrollHeight - page.clientHeight;
    if (bar && max > 0) {
      bar.style.width = `${(page.scrollTop / max) * 100}%`;
    }
    if (header) {
      header.classList.toggle('is-scrolled', page.scrollTop > 4);
    }
  }

  document.addEventListener('scroll', onScroll, { passive: true });
  onScroll();
}

function initReveal() {
  const items = $$('.reveal');
  if (!items.length) return;

  if (!('IntersectionObserver' in window)) {
    items.forEach((item) => item.classList.add('is-in'));
    return;
  }

  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry, index) => {
      if (!entry.isIntersecting) return;
      entry.target.style.transitionDelay = `${Math.min(index * 40, 240)}ms`;
      entry.target.classList.add('is-in');
      observer.unobserve(entry.target);
    });
  }, { threshold: 0.08, rootMargin: '0px 0px -8% 0px' });

  items.forEach((item) => observer.observe(item));
}

function initButtonSpotlight() {
  $$('.btn').forEach((button) => {
    button.addEventListener('pointermove', (event) => {
      const rect = button.getBoundingClientRect();
      button.style.setProperty('--mx', `${event.clientX - rect.left}px`);
      button.style.setProperty('--my', `${event.clientY - rect.top}px`);
    });
  });
}

function initLogoOverride() {
  $$('img[data-png-override]').forEach((img) => {
    const png = img.getAttribute('data-png-override');
    if (!png) return;
    const probe = new Image();
    probe.onload = () => { img.src = png; };
    probe.src = png;
  });
}

function initXWidgets() {
  if (!$('[data-x-feed]')) return;
  if (document.querySelector('script[src*="platform.twitter.com/widgets.js"]')) return;

  const script = document.createElement('script');
  script.async = true;
  script.charset = 'utf-8';
  script.src = 'https://platform.twitter.com/widgets.js';
  document.head.appendChild(script);
}

initMobileNav();
initScrollProgress();
initReveal();
initButtonSpotlight();
initLogoOverride();
initXWidgets();
