(function attachArrowheadShopify(root) {
  'use strict';

  const DEFAULT_API_VERSION = '2026-04';
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

  class ShopifyStorefrontError extends Error {
    constructor(message, options = {}) {
      super(message);
      this.name = 'ShopifyStorefrontError';
      this.code = options.code || 'SHOPIFY_ERROR';
      this.status = Number(options.status) || 0;
      this.details = options.details;
    }
  }

  function configurationError(message) {
    return new ShopifyStorefrontError(message, {
      code: 'SHOPIFY_CONFIGURATION_ERROR',
    });
  }

  function normalizeStoreDomain(value) {
    const raw = String(value || '').trim().toLowerCase();
    if (!raw) throw configurationError('A Shopify store domain is required.');

    let parsed;
    try {
      parsed = new URL(raw.includes('://') ? raw : `https://${raw}`);
    } catch (_) {
      throw configurationError('The Shopify store domain is invalid.');
    }

    const hasUnexpectedUrlParts = parsed.protocol !== 'https:'
      || parsed.username
      || parsed.password
      || parsed.port
      || (parsed.pathname && parsed.pathname !== '/')
      || parsed.search
      || parsed.hash;
    if (hasUnexpectedUrlParts
      || !/^[a-z0-9][a-z0-9-]*\.myshopify\.com$/.test(parsed.hostname)) {
      throw configurationError('The store domain must be a *.myshopify.com HTTPS hostname.');
    }

    return parsed.hostname;
  }

  function readPublicToken(config) {
    const forbiddenKeys = [
      'adminAccessToken',
      'adminToken',
      'privateAccessToken',
      'privateStorefrontToken',
    ];
    if (forbiddenKeys.some((key) => String(config[key] || '').trim())) {
      throw configurationError('Only a public Shopify Storefront access token may be used in browser code.');
    }

    const token = String(
      config.publicStorefrontToken
      || config.storefrontAccessToken
      || config.token
      || ''
    ).trim();

    if (!token) throw configurationError('A public Shopify Storefront access token is required.');
    if (token.length > 1024 || !/^[\x21-\x7e]+$/.test(token)) {
      throw configurationError('The public Shopify Storefront access token is invalid.');
    }
    if (/^(shpat|shpca|shppa|shpss)_/i.test(token)) {
      throw configurationError('An Admin or private Shopify token cannot be used in browser code.');
    }

    return token;
  }

  function normalizeConfiguration(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      throw configurationError('Shopify Storefront configuration must be an object.');
    }

    const apiVersion = String(value.apiVersion || DEFAULT_API_VERSION).trim();
    if (!/^\d{4}-\d{2}$/.test(apiVersion)) {
      throw configurationError('Shopify API version must use YYYY-MM format.');
    }

    const collectionHandle = String(value.collectionHandle || '').trim().toLowerCase();
    if (collectionHandle
      && (collectionHandle.length > 255 || !/^[a-z0-9][a-z0-9-]*$/.test(collectionHandle))) {
      throw configurationError('The Shopify collection handle is invalid.');
    }

    return Object.freeze({
      domain: normalizeStoreDomain(value.domain),
      token: readPublicToken(value),
      apiVersion,
      collectionHandle,
    });
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
    if (!amount || !Number.isFinite(Number(amount))) return null;
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
      width: Number(image.width) > 0 ? Number(image.width) : null,
      height: Number(image.height) > 0 ? Number(image.height) : null,
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
    const title = String(product.title || 'Paesano merch');

    return {
      id: String(product.id || handle || title),
      handle,
      title,
      name: title,
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
        credentials: 'omit',
        signal: controller.signal,
      });

      if (!response.ok) {
        throw new ShopifyStorefrontError(`Shopify Storefront API returned ${response.status}.`, {
          code: 'SHOPIFY_UPSTREAM_ERROR',
          status: response.status,
        });
      }

      let payload;
      try {
        payload = await response.json();
      } catch (_) {
        throw new ShopifyStorefrontError('Shopify Storefront API returned an invalid response.', {
          code: 'SHOPIFY_RESPONSE_ERROR',
          status: response.status,
        });
      }

      if (Array.isArray(payload.errors) && payload.errors.length) {
        throw new ShopifyStorefrontError('Shopify Storefront API returned GraphQL errors.', {
          code: 'SHOPIFY_GRAPHQL_ERROR',
          status: response.status,
          details: payload.errors.slice(0, 5).map((error) => String(error.message || 'Unknown GraphQL error')),
        });
      }
      return payload.data || {};
    } catch (error) {
      if (error && error.name === 'AbortError') {
        throw new ShopifyStorefrontError('Shopify Storefront API timed out.', {
          code: 'SHOPIFY_TIMEOUT',
        });
      }
      if (error instanceof ShopifyStorefrontError) throw error;
      throw new ShopifyStorefrontError('Shopify Storefront API request failed.', {
        code: 'SHOPIFY_NETWORK_ERROR',
      });
    } finally {
      clearTimeout(timeout);
    }
  }

  async function fetchProducts(config) {
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
      throw new ShopifyStorefrontError(
        `Shopify collection "${config.collectionHandle}" was not found.`,
        { code: 'SHOPIFY_COLLECTION_NOT_FOUND' }
      );
    }

    const nodes = connection && Array.isArray(connection.nodes) ? connection.nodes : [];
    return {
      configured: true,
      provider: 'shopify',
      stale: false,
      fetchedAt: Date.now(),
      products: nodes.filter(Boolean).map((product) => normalizeProduct(product, config.domain)),
    };
  }

  function validateCartLines(lines) {
    if (!Array.isArray(lines) || lines.length < 1 || lines.length > MAX_CART_LINES) {
      throw new ShopifyStorefrontError(`Cart must contain between 1 and ${MAX_CART_LINES} lines.`, {
        code: 'INVALID_CART_LINES',
      });
    }

    const normalized = lines.map((line, index) => {
      const variantId = String((line && (line.variantId || line.merchandiseId)) || '').trim();
      const quantity = Number(line && line.quantity);
      if (!/^gid:\/\/shopify\/ProductVariant\/[A-Za-z0-9_-]+$/.test(variantId)) {
        throw new ShopifyStorefrontError(`Cart line ${index + 1} has an invalid Shopify variant ID.`, {
          code: 'INVALID_VARIANT_ID',
        });
      }
      if (!Number.isInteger(quantity) || quantity < 1 || quantity > MAX_LINE_QUANTITY) {
        throw new ShopifyStorefrontError(
          `Cart line ${index + 1} quantity must be an integer between 1 and ${MAX_LINE_QUANTITY}.`,
          { code: 'INVALID_QUANTITY' }
        );
      }
      return { merchandiseId: variantId, quantity };
    });

    const merged = new Map();
    normalized.forEach((line) => {
      const quantity = (merged.get(line.merchandiseId) || 0) + line.quantity;
      if (quantity > MAX_LINE_QUANTITY) {
        throw new ShopifyStorefrontError(
          `Combined quantity for a variant cannot exceed ${MAX_LINE_QUANTITY}.`,
          { code: 'INVALID_QUANTITY' }
        );
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

  async function createCart(config, lines) {
    const normalizedLines = validateCartLines(lines);
    const data = await storefrontRequest(config, CART_CREATE_MUTATION, {
      input: { lines: normalizedLines },
    });
    const result = data.cartCreate || {};
    const userErrors = Array.isArray(result.userErrors) ? result.userErrors : [];

    if (userErrors.length) {
      throw new ShopifyStorefrontError('Shopify could not create the cart.', {
        code: 'SHOPIFY_CART_REJECTED',
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
      throw new ShopifyStorefrontError('Shopify returned an incomplete cart.', {
        code: 'SHOPIFY_CART_ERROR',
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

  function createClient(rawConfig) {
    const config = normalizeConfiguration(rawConfig);
    const publicConfiguration = Object.freeze({
      domain: config.domain,
      apiVersion: config.apiVersion,
      collectionHandle: config.collectionHandle,
    });

    return Object.freeze({
      fetchProducts: () => fetchProducts(config),
      createCart: (lines) => createCart(config, lines),
      getConfiguration: () => publicConfiguration,
    });
  }

  let activeClient = null;

  function requireActiveClient() {
    if (!activeClient) {
      throw new ShopifyStorefrontError('Shopify Storefront is not configured.', {
        code: 'SHOPIFY_NOT_CONFIGURED',
      });
    }
    return activeClient;
  }

  root.ArrowheadShopify = Object.freeze({
    Error: ShopifyStorefrontError,
    createClient,
    configure(config) {
      activeClient = createClient(config);
      return activeClient;
    },
    clearConfiguration() {
      activeClient = null;
    },
    isConfigured() {
      return Boolean(activeClient);
    },
    fetchProducts() {
      return requireActiveClient().fetchProducts();
    },
    createCart(lines) {
      return requireActiveClient().createCart(lines);
    },
  });
}(typeof window !== 'undefined' ? window : globalThis));
