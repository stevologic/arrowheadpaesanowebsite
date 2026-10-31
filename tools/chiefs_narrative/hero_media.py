"""Select only independently reviewed media; a public X post is not a license.

The writer cannot set rights, media URLs, or verification timestamps. The
reviewed catalog is the sole authority. No X image extraction is performed.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from . import config, schema

FALLBACK_ID = "none"
ASSET = re.compile(r"^images/hero/[A-Za-z0-9_/-]+\.(?:webp|png|jpe?g)$")
EVIDENCE = re.compile(r"^docs/media-rights/[A-Za-z0-9_-]+\.md$")


def catalog(root=None):
    try:
        value = json.loads((Path(root or config.REPO_ROOT)/"data/hero_media.json").read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def approved(item, slug, *, root=None, now=None):
    """Validate documentary scope and exact provided asset, never infer rights."""
    root = Path(root or config.REPO_ROOT)
    now = now or datetime.now(timezone.utc)
    if not isinstance(item, dict) or slug not in item.get("editionSlugs", []):
        return False
    rights = item.get("rights") or {}
    if not isinstance(rights, dict) or rights.get("status") != "approved" or rights.get("commercialUse") is not True:
        return False
    if not all(isinstance(rights.get(k), str) and rights[k].strip() for k in ("rightsholder", "license", "attribution", "evidence", "reviewedAt", "expiresAt")):
        return False
    if not EVIDENCE.fullmatch(rights["evidence"]) or not (root/rights["evidence"]).is_file():
        return False
    try:
        start = datetime.fromisoformat(rights["reviewedAt"]).date()
        end = datetime.fromisoformat(rights["expiresAt"]).date()
        if not start <= now.date() <= end:
            return False
    except ValueError:
        return False
    post = schema._norm_x_embed(item.get("sourceUrl"))
    if not post:
        return False
    if item.get("kind") != "licensed-image" or rights.get("providedImage") is not True:
        return False
    image = item.get("image", "")
    if not ASSET.fullmatch(image) or ".." in image or not (root/"public"/image).is_file():
        return False
    if not isinstance(item.get("alt"), str) or not item["alt"].strip():
        return False
    if any(type(item.get(k)) is not int or not 16 <= item[k] <= 4096 for k in ("width", "height")):
        return False
    return hashlib.sha256((root/"public"/image).read_bytes()).hexdigest() == item.get("sha256")


def attach(narrative, *, root=None, now=None):
    """Assign a reviewed catalog ID, else none — never a synthesized illustration."""
    now = now or datetime.now(timezone.utc)
    narrative["heroMediaId"] = FALLBACK_ID
    narrative["heroMediaVerifiedAt"] = ""
    items = catalog(root).get("items") or {}
    if not isinstance(items, dict):
        return narrative
    for key, item in items.items():
        if not re.fullmatch(r"[a-z0-9-]+", key) or not approved(item, narrative.get("slug"), root=root, now=now):
            continue
        narrative["heroMediaId"] = key
        narrative["heroMediaVerifiedAt"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        break
    return narrative
