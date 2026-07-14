const amazonFinds = require('../data/amazon_finds.json');

const DISCLOSURE = 'As an Amazon Associate I earn from qualifying purchases.';
const DEFAULT_MARKETPLACE = 'www.amazon.com';
const ASSOCIATE_TAG_PATTERN = /^(?=.{1,50}$)[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)*$/;

function normalizeAssociateTag(value) {
  const tag = typeof value === 'string' ? value.trim() : '';
  return ASSOCIATE_TAG_PATTERN.test(tag) ? tag : '';
}

function normalizeMarketplace(value) {
  const marketplace = typeof value === 'string' ? value.trim().toLowerCase() : '';
  if (marketplace === 'amazon.com' || marketplace === DEFAULT_MARKETPLACE) {
    return DEFAULT_MARKETPLACE;
  }
  return DEFAULT_MARKETPLACE;
}

function buildAmazonSearchUrl(query, options = {}) {
  const normalizedQuery = typeof query === 'string' ? query.trim() : '';
  if (!normalizedQuery) {
    throw new TypeError('Amazon search query must be a non-empty string');
  }

  const marketplace = normalizeMarketplace(options.marketplace);
  const associateTag = normalizeAssociateTag(options.associateTag);
  const url = new URL('/s', `https://${marketplace}`);
  url.searchParams.set('k', normalizedQuery);
  if (associateTag) url.searchParams.set('tag', associateTag);
  return url.toString();
}

function getAmazonFinds(env = process.env) {
  const marketplace = normalizeMarketplace(env.AMAZON_MARKETPLACE);
  const associateTag = normalizeAssociateTag(env.AMAZON_ASSOCIATE_TAG);
  const items = amazonFinds.map((item) => ({
    ...item,
    url: buildAmazonSearchUrl(item.query, { marketplace, associateTag }),
  }));

  return {
    configured: Boolean(associateTag),
    disclosure: DISCLOSURE,
    marketplace,
    items,
  };
}

module.exports = {
  ASSOCIATE_TAG_PATTERN,
  DEFAULT_MARKETPLACE,
  DISCLOSURE,
  buildAmazonSearchUrl,
  getAmazonFinds,
  normalizeAssociateTag,
  normalizeMarketplace,
};
