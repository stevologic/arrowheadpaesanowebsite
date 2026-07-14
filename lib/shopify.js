const staticMerch = require('../data/merch.json');

const DEFAULT_API_VERSION = '2026-04';
const DEFAULT_CACHE_MINUTES = 10;
const REQUEST_TIMEOUT_MS = 10000;
const MAX_PRODUCTS = 50;
const MAX_CART_LINES = 25;
const MAX_LINE_QUANTITY = 20;

const CATALOG_QUERY = `#graphql
  query ArrowheadPaesanoCatalog(
    $productCount: Int!
    $collectionHandle: String!
    $useCollection: Boolean!
  ) {
    allProducts: products(first: $productCount) @skip(if: $useCollection) {
      nodes {
        ...CatalogProduct
      }
    }
    selectedCollection: collection(handle: $collectionHandle) @include(if: $useCollection) {
      products(first: $productCount) {
        nodes {
          ...CatalogProduct
        }
      }
    }
  }

  fragment CatalogProduct on Product {
    id
    handle
    title
    description
    availableForSale
    onlineStoreUrl
    featuredImage {
      url
      altText
      width
      height
    }
    priceRange {
      minVariantPrice {
        amount
        currencyCode
      }
    }
    variants(first: 100) {
      nodes {
        id
        title
        availableForSale
        price {
          amount
          currencyCode
        }
        image {
          url
          altText
          width
          height
        }
      }
    }
  }
`;

const CART_CREATE_MUTATION = `#graphql
  mutation ArrowheadPaesanoCartCreate($input: CartInput!) {
    cartCreate(input: $input) {
      cart {
        id
        checkoutUrl
        totalQuantity
        cost {
          subtotalAmount {
            amount
            currencyCode
          }
          totalAmount {
            amount
            currencyCode
          }
        }
      }
      userErrors {
        code
        field
        message
      }
    }
  }
`;

class ShopifyError extends Error {
  constructor(message, options = {}) {
    super(message);
    this.name = 'ShopifyError';
    this.code = options.code || 'SHOPIFY_ERROR';
    this.statusCode = options.statusCode || 502;
    this.details = options.details;
  }
}

let catalogCache = {
  hasValue: false,
  fetchedAt: 0,
  products: [],
};
let catalogRequest;

function cacheMs() {
  const minutes = Number(process.env.SHOPIFY_CACHE_MINUTES);
  const safeMinutes = Number.isFinite(minutes) && minutes > 0
    ? Math.min(minutes, 24 * 60)
    : DEFAULT_CACHE_MINUTES;
  return safeMinutes * 60 * 1000;
}

function normalizeStoreDomain(value) {
  let domain = String(value || '').trim().toLowerCase();
  if (!domain) return '';

  domain = domain.replace(/^https?:\/\//, '').replace(/\/+$/, '');
  if (!/^[a-z0-9][a-z0-9-]*\.myshopify\.com$/.test(domain)) {
    throw new ShopifyError(
      'SHOPIFY_STORE_DOMAIN must be a *.myshopify.com hostname',
      { code: 'SHOPIFY_CONFIGURATION_ERROR', statusCode: 500 }
    );
  }
  return domain;
}

function configuration() {
  const rawDomain = String(process.env.SHOPIFY_STORE_DOMAIN || '').trim();
  const token = String(process.env.SHOPIFY_STOREFRONT_ACCESS_TOKEN || '').trim();

  if (!rawDomain || !token) return null;

  const apiVersion = String(process.env.SHOPIFY_API_VERSION || DEFAULT_API_VERSION).trim();
  if (!/^\d{4}-\d{2}$/.test(apiVersion)) {
    throw new ShopifyError(
      'SHOPIFY_API_VERSION must use YYYY-MM format',
      { code: 'SHOPIFY_CONFIGURATION_ERROR', statusCode: 500 }
    );
  }

  return {
    domain: normalizeStoreDomain(rawDomain),
    token,
    apiVersion,
    collectionHandle: String(process.env.SHOPIFY_COLLECTION_HANDLE || '').trim(),
  };
}

function isShopifyConfigured() {
  return Boolean(
    String(process.env.SHOPIFY_STORE_DOMAIN || '').trim()
    && String(process.env.SHOPIFY_STOREFRONT_ACCESS_TOKEN || '').trim()
  );
}

function safeHttpsUrl(value) {
  try {
    const url = new URL(String(value || ''));
    return url.protocol === 'https:' ? url.href : '';
  } catch (_) {
    return '';
  }
}

function formatMoney(amount, currencyCode) {
  const numericAmount = Number(amount);
  const currency = String(currencyCode || 'USD').toUpperCase();
  if (!Number.isFinite(numericAmount)) return '';

  try {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency,
    }).format(numericAmount);
  } catch (_) {
    return `${currency} ${numericAmount.toFixed(2)}`;
  }
}

function moneyValue(value) {
  if (!value) return null;
  const amount = String(value.amount || '');
  const currencyCode = String(value.currencyCode || 'USD').toUpperCase();
  return {
    amount,
    currencyCode,
    formatted: formatMoney(amount, currencyCode),
  };
}

function normalizeImage(image) {
  if (!image) return null;
  const url = safeHttpsUrl(image.url);
  if (!url) return null;
  return {
    url,
    altText: String(image.altText || ''),
    width: Number(image.width) || null,
    height: Number(image.height) || null,
  };
}

function normalizeProduct(product, domain) {
  const variants = product && product.variants && Array.isArray(product.variants.nodes)
    ? product.variants.nodes.filter(Boolean)
    : [];
  const availableVariant = variants.find((variant) => variant.availableForSale) || null;
  const variantPrice = moneyValue(availableVariant && availableVariant.price);
  const rangePrice = moneyValue(product && product.priceRange && product.priceRange.minVariantPrice);
  const price = variantPrice || rangePrice;
  const image = normalizeImage((availableVariant && availableVariant.image) || product.featuredImage);
  const handle = String(product.handle || '');
  const productUrl = safeHttpsUrl(product.onlineStoreUrl)
    || (handle ? `https://${domain}/products/${encodeURIComponent(handle)}` : `https://${domain}`);
  const availableForSale = Boolean(product.availableForSale && availableVariant);

  return {
    id: String(product.id || handle || product.title || ''),
    handle,
    title: String(product.title || 'Paesano merch'),
    name: String(product.title || 'Paesano merch'),
    description: String(product.description || '').slice(0, 800),
    availableForSale,
    variantId: availableVariant ? String(availableVariant.id || '') : '',
    variant: availableVariant ? {
      id: String(availableVariant.id || ''),
      title: String(availableVariant.title || ''),
      availableForSale: Boolean(availableVariant.availableForSale),
      price: variantPrice,
      image: normalizeImage(availableVariant.image),
    } : null,
    price: price ? price.formatted : '',
    priceAmount: price ? price.amount : '',
    currencyCode: price ? price.currencyCode : 'USD',
    image: image ? image.url : '',
    imageAlt: image ? image.altText : '',
    productUrl,
    shopUrl: productUrl,
    badge: availableForSale ? 'Shopify' : 'Sold out',
    palette: ['#E31837', '#FFB81C'],
  };
}

async function storefrontRequest(config, query, variables) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  const endpoint = `https://${config.domain}/api/${config.apiVersion}/graphql.json`;

  try {
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        'X-Shopify-Storefront-Access-Token': config.token,
      },
      body: JSON.stringify({ query, variables }),
      signal: controller.signal,
    });

    if (!response.ok) {
      throw new ShopifyError(`Shopify Storefront API returned ${response.status}`, {
        code: 'SHOPIFY_UPSTREAM_ERROR',
        statusCode: 502,
      });
    }

    const payload = await response.json();
    if (Array.isArray(payload.errors) && payload.errors.length) {
      throw new ShopifyError('Shopify Storefront API returned GraphQL errors', {
        code: 'SHOPIFY_GRAPHQL_ERROR',
        statusCode: 502,
        details: payload.errors.slice(0, 5).map((error) => String(error.message || 'Unknown GraphQL error')),
      });
    }
    return payload.data || {};
  } catch (error) {
    if (error && error.name === 'AbortError') {
      throw new ShopifyError('Shopify Storefront API timed out', {
        code: 'SHOPIFY_TIMEOUT',
        statusCode: 504,
      });
    }
    if (error instanceof ShopifyError) throw error;
    throw new ShopifyError('Shopify Storefront API request failed', {
      code: 'SHOPIFY_UPSTREAM_ERROR',
      statusCode: 502,
    });
  } finally {
    clearTimeout(timeout);
  }
}

async function fetchCatalog(config) {
  const useCollection = Boolean(config.collectionHandle);
  const data = await storefrontRequest(config, CATALOG_QUERY, {
    productCount: MAX_PRODUCTS,
    collectionHandle: config.collectionHandle || '',
    useCollection,
  });

  const connection = useCollection
    ? data.selectedCollection && data.selectedCollection.products
    : data.allProducts;

  if (useCollection && !data.selectedCollection) {
    throw new ShopifyError(`Shopify collection "${config.collectionHandle}" was not found`, {
      code: 'SHOPIFY_COLLECTION_NOT_FOUND',
      statusCode: 502,
    });
  }

  const nodes = connection && Array.isArray(connection.nodes) ? connection.nodes : [];
  return nodes.filter(Boolean).map((product) => normalizeProduct(product, config.domain));
}

function staleCatalog(error) {
  if (catalogCache.hasValue) {
    return {
      configured: true,
      products: catalogCache.products,
      provider: 'shopify',
      stale: true,
      fetchedAt: catalogCache.fetchedAt,
      warning: 'Live Shopify products are temporarily unavailable; showing the last successful catalog.',
      errorCode: error.code || 'SHOPIFY_ERROR',
    };
  }

  return {
    configured: true,
    products: staticMerch,
    provider: 'shopify',
    stale: true,
    fallback: true,
    warning: 'Live Shopify products are temporarily unavailable; showing the static catalog.',
    errorCode: error.code || 'SHOPIFY_ERROR',
  };
}

async function refreshCatalog(config) {
  try {
    const products = await fetchCatalog(config);
    catalogCache = {
      hasValue: true,
      fetchedAt: Date.now(),
      products,
    };
    return {
      configured: true,
      products,
      provider: 'shopify',
      stale: false,
      fetchedAt: catalogCache.fetchedAt,
    };
  } catch (error) {
    const shopifyError = error instanceof ShopifyError
      ? error
      : new ShopifyError('Shopify catalog refresh failed');
    console.error(`[shopify] ${shopifyError.code}: ${shopifyError.message}`);
    return staleCatalog(shopifyError);
  }
}

async function getShopifyProducts() {
  if (!isShopifyConfigured()) {
    return { configured: false, products: staticMerch };
  }

  let config;
  try {
    config = configuration();
  } catch (error) {
    console.error(`[shopify] ${error.code || 'SHOPIFY_CONFIGURATION_ERROR'}: ${error.message}`);
    return staleCatalog(error);
  }

  if (catalogCache.hasValue && Date.now() - catalogCache.fetchedAt < cacheMs()) {
    return {
      configured: true,
      products: catalogCache.products,
      provider: 'shopify',
      stale: false,
      cached: true,
      fetchedAt: catalogCache.fetchedAt,
    };
  }

  if (!catalogRequest) {
    catalogRequest = refreshCatalog(config).finally(() => {
      catalogRequest = null;
    });
  }
  return catalogRequest;
}

function validateCartLines(lines) {
  if (!Array.isArray(lines) || lines.length < 1 || lines.length > MAX_CART_LINES) {
    throw new ShopifyError(`Cart must contain between 1 and ${MAX_CART_LINES} lines`, {
      code: 'INVALID_CART_LINES',
      statusCode: 400,
    });
  }

  const normalized = lines.map((line, index) => {
    const variantId = String((line && (line.variantId || line.merchandiseId)) || '').trim();
    const quantity = Number(line && line.quantity);

    if (!/^gid:\/\/shopify\/ProductVariant\/[A-Za-z0-9_-]+$/.test(variantId)) {
      throw new ShopifyError(`Cart line ${index + 1} has an invalid Shopify variant ID`, {
        code: 'INVALID_VARIANT_ID',
        statusCode: 400,
      });
    }
    if (!Number.isInteger(quantity) || quantity < 1 || quantity > MAX_LINE_QUANTITY) {
      throw new ShopifyError(
        `Cart line ${index + 1} quantity must be an integer between 1 and ${MAX_LINE_QUANTITY}`,
        { code: 'INVALID_QUANTITY', statusCode: 400 }
      );
    }

    return { merchandiseId: variantId, quantity };
  });

  const merged = new Map();
  normalized.forEach((line) => {
    const quantity = (merged.get(line.merchandiseId) || 0) + line.quantity;
    if (quantity > MAX_LINE_QUANTITY) {
      throw new ShopifyError(`Combined quantity for a variant cannot exceed ${MAX_LINE_QUANTITY}`, {
        code: 'INVALID_QUANTITY',
        statusCode: 400,
      });
    }
    merged.set(line.merchandiseId, quantity);
  });

  return Array.from(merged, ([merchandiseId, quantity]) => ({ merchandiseId, quantity }));
}

function normalizeCartCost(cost) {
  if (!cost) return null;
  return {
    subtotal: moneyValue(cost.subtotalAmount),
    total: moneyValue(cost.totalAmount),
  };
}

async function createShopifyCart(lines) {
  const normalizedLines = validateCartLines(lines);

  if (!isShopifyConfigured()) {
    throw new ShopifyError('Shopify is not configured', {
      code: 'SHOPIFY_NOT_CONFIGURED',
      statusCode: 503,
    });
  }

  const config = configuration();
  const data = await storefrontRequest(config, CART_CREATE_MUTATION, {
    input: { lines: normalizedLines },
  });
  const result = data.cartCreate || {};
  const userErrors = Array.isArray(result.userErrors) ? result.userErrors : [];

  if (userErrors.length) {
    throw new ShopifyError('Shopify could not create the cart', {
      code: 'SHOPIFY_CART_REJECTED',
      statusCode: 422,
      details: userErrors.slice(0, 10).map((error) => ({
        code: String(error.code || ''),
        field: Array.isArray(error.field) ? error.field.map(String) : [],
        message: String(error.message || 'Cart line was rejected'),
      })),
    });
  }

  const cart = result.cart;
  const checkoutUrl = cart && safeHttpsUrl(cart.checkoutUrl);
  if (!cart || !checkoutUrl) {
    throw new ShopifyError('Shopify returned an incomplete cart', {
      code: 'SHOPIFY_CART_ERROR',
      statusCode: 502,
    });
  }

  return {
    provider: 'shopify',
    cartId: String(cart.id || ''),
    checkoutUrl,
    totalQuantity: Number(cart.totalQuantity) || 0,
    cost: normalizeCartCost(cart.cost),
  };
}

module.exports = {
  ShopifyError,
  createShopifyCart,
  getShopifyProducts,
  isShopifyConfigured,
  validateCartLines,
};
