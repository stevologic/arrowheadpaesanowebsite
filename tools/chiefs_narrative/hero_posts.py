"""Unsigned hero-pin sidecar: official X embeds, validated offline.

Edition JSON and Review signatures are never rewritten. Pins live in
``data/hero_posts.json`` keyed by edition slug. Build and gate time do
not touch the network: validation is allowlist + snowflake-id shape,
sponsored/betting denylist, a curator mediaReviewed flag, and
signed-edition rules. Missing or unreadable edition files fail closed.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from . import config, hero_scenes, review_gate

SIDECAR_PATH = config.DATA_DIR / "hero_posts.json"
SCHEMA_VERSION = 1

# Official embed only. Compared lowercase without a leading @.
# Team + verified staff + league + verified Athletic/ESPN outlet accounts
# that employ credentialed Chiefs beat writers. KC Star / individual
# beat-writer handles were not verified at lookup, so they are omitted.
# Fan, meme, parody, betting, Polymarket, and sponsored handles are not
# on this list; the gate fails any off-list pin.
ALLOWLIST_DISPLAY = (
    "Chiefs",
    "ChiefsPR",
    "NFL",
    "TheAthletic",
    "TheAthleticNFL",
    "ESPN",
    "ESPNNFL",
)
ALLOWLIST = frozenset(handle.lower() for handle in ALLOWLIST_DISPLAY)
DENIED_HANDLES = frozenset(
    {
        "arrowheadpride",
        "arrowheadaddict",
        "polymarket",
        "kalshi",
        "draftkings",
        "fanduel",
        "betmgm",
        "caesarsnfl",
        "barstoolsports",
        "chiefskingdommemes",
    }
)

POST_ID_RE = re.compile(r"^[0-9]{10,20}$")
HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
_STATUS_RE = re.compile(
    r"^https://(?:www\.)?(?:twitter|x)\.com/([A-Za-z0-9_]+)/status/(\d+)/?(?:\?.*)?$",
    re.IGNORECASE,
)
BETTING_DENY_RE = re.compile(
    r"draftkings|fanduel|\bodds\b|o/u|over\s*/\s*under|sponsored|presented by|\bbet\b",
    re.IGNORECASE,
)
_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


def _s(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _handle(value) -> str:
    return _s(value).lstrip("@")


def _truthy(value) -> bool:
    if value is True:
        return True
    if value is False or value is None:
        return False
    return _s(value).lower() in {"true", "1", "yes"}


def _norm_posted(value) -> str:
    text = _s(value).replace(" ", "T")
    if text.endswith("Z"):
        body = text[:-1]
    elif "+" in text[10:]:
        body = text.split("+", 1)[0]
    else:
        body = text
    body = body.replace("T", " ")
    try:
        dt = datetime.fromisoformat(body)
    except ValueError:
        return ""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def posted_label(value) -> str:
    iso = _norm_posted(value)
    if not iso:
        return ""
    dt = datetime.fromisoformat(iso.replace("Z", ""))
    return f"{_MONTHS[dt.month - 1]} {dt.day}, {dt.year}"


def status_url(handle: str, post_id: str) -> str:
    return f"https://x.com/{handle}/status/{post_id}"


def _betting_blob(value: dict) -> str:
    """Text fields only. Image pixels are never fetched; reviewers must open media."""
    return " ".join(
        _s(value.get(key) or "")
        for key in (
            "text", "alt", "caption", "note",
            "ocr", "mediaAlt", "imageText", "imageAlt",
        )
    )


def is_sponsored_or_betting(value: dict) -> bool:
    """True when pin metadata flags a sponsor or betting copy."""
    if not isinstance(value, dict):
        return True
    if _truthy(value.get("sponsored")):
        return True
    return bool(BETTING_DENY_RE.search(_betting_blob(value)))


def edition_pin_allowed(slug: str, *, root=None) -> bool:
    """Pins are allowed only on a readable unsigned edition. Missing files fail closed."""
    key = _s(slug)
    base = Path(root or config.REPO_ROOT)
    path = base / "data" / "narrative_editions" / f"{key}.json"
    if not path.is_file():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    payload.setdefault("slug", key)
    try:
        return not review_gate.review_signed_off(payload, root=base, edition_file=path)
    except review_gate.GateError:
        return False


def normalize_pin(value) -> dict | None:
    """Keep a pin only when the handle is allowlisted and the id is a snowflake."""
    if not isinstance(value, dict):
        return None
    if is_sponsored_or_betting(value):
        return None
    if not _truthy(value.get("mediaReviewed")):
        return None
    handle = _handle(value.get("handle") or value.get("account") or "")
    post_id = _s(value.get("id") or value.get("postId") or "")
    url = _s(value.get("url") or value.get("embedUrl") or "")
    if url:
        match = _STATUS_RE.match(url.split("#", 1)[0])
        if not match:
            return None
        url_handle, url_id = match.group(1), match.group(2)
        handle = handle or url_handle
        post_id = post_id or url_id
        if handle.lower() != url_handle.lower() or post_id != url_id:
            return None
    if not HANDLE_RE.fullmatch(handle) or not POST_ID_RE.fullmatch(post_id):
        return None
    key = handle.lower()
    if key in DENIED_HANDLES or key not in ALLOWLIST:
        return None
    posted = _norm_posted(value.get("postedAt") or value.get("createdAt") or "")
    text = _s(value.get("text") or "")
    alt = _s(value.get("alt") or "")
    if not alt:
        alt = f"Official @{handle} post" + (f": {text}" if text else ".")
    display = next((item for item in ALLOWLIST_DISPLAY if item.lower() == key), handle)
    pin = {
        "id": post_id,
        "handle": display,
        "account": f"@{display}",
        "url": status_url(display, post_id),
        "postedAt": posted,
        "postedLabel": posted_label(posted) if posted else "",
        "text": text,
        "alt": alt,
        "sponsored": False,
        "mediaReviewed": True,
    }
    return pin


def load_sidecar(root=None) -> dict:
    path = Path(root or config.REPO_ROOT) / "data" / "hero_posts.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schemaVersion": SCHEMA_VERSION, "allowlist": list(ALLOWLIST_DISPLAY),
                "catalog": [], "pins": {}}
    if not isinstance(payload, dict):
        return {"schemaVersion": SCHEMA_VERSION, "allowlist": list(ALLOWLIST_DISPLAY),
                "catalog": [], "pins": {}}
    return payload


def pin_for_slug(slug: str, *, root=None) -> dict | None:
    payload = load_sidecar(root)
    pins = payload.get("pins") or {}
    if not isinstance(pins, dict):
        return None
    if not edition_pin_allowed(slug, root=root):
        return None
    return normalize_pin(pins.get(slug))


def validate_sidecar(root=None) -> list[str]:
    """Offline gate: every catalog row and every pin must be allowlisted."""
    issues = []
    payload = load_sidecar(root)
    listed = payload.get("allowlist") or []
    if [str(item) for item in listed] != list(ALLOWLIST_DISPLAY):
        issues.append("hero pin allowlist in data/hero_posts.json does not match code")
    for index, row in enumerate(payload.get("catalog") or []):
        if normalize_pin(row) is None:
            issues.append(f"hero pin catalog[{index}] failed allowlist or id format")
    pins = payload.get("pins") or {}
    if not isinstance(pins, dict):
        issues.append("hero pin sidecar pins must be an object keyed by edition id")
        return issues
    for slug, row in pins.items():
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}-\d{4}", str(slug)):
            issues.append(f"hero pin key {slug!r} is not an edition id")
            continue
        if normalize_pin(row) is None:
            handle = _handle((row or {}).get("handle") if isinstance(row, dict) else "")
            issues.append(
                f"hero pin {slug} failed allowlist or id format"
                + (f" (@{handle})" if handle else "")
            )
            continue
        if not edition_pin_allowed(str(slug), root=root):
            issues.append(f"hero pin {slug} is attached to a signed edition")
    return issues


def select_catalog_pin(edition: dict, *, root=None) -> dict | None:
    """Pick a topic-matched catalog pin for a new edition. No network.

    A catalog row is used only when week and opponent both match. Season
    alone is not enough. No match leaves the edition unpinned.
    """
    payload = load_sidecar(root)
    mode = hero_scenes.mode_of(edition)
    event = hero_scenes.event_of(edition, mode)
    abbr = hero_scenes.opponent_abbr(event.get("opponent"))
    week = None
    phase = edition.get("phase") or {}
    raw_week = phase.get("week")
    if isinstance(raw_week, int) and raw_week > 0:
        week = raw_week
    elif isinstance(raw_week, str) and raw_week.isdigit():
        week = int(raw_week)
    if not week or not abbr:
        return None
    season = hero_scenes.season_code(phase.get("type") or "")
    venue = hero_scenes.venue_key(hero_scenes._venue_blob(edition, event))
    home_away = hero_scenes._home_away(edition, event, mode, venue)
    ranked: list[tuple[int, dict]] = []
    for row in payload.get("catalog") or []:
        pin = normalize_pin(row)
        if not pin:
            continue
        if _s(row.get("mode") or "preview") != mode:
            continue
        row_abbr = _s(row.get("opponentAbbr") or "").upper()
        if row_abbr != abbr or row.get("week") != week:
            continue
        score = 16
        if season and _s(row.get("seasonType") or "") == season:
            score += 3
        if home_away and _s(row.get("homeAway") or "") == home_away:
            score += 4
        ranked.append((score, pin))
    if not ranked:
        return None
    ranked.sort(key=lambda item: -item[0])
    return ranked[0][1]


def attach_sidecar(narrative: dict, *, root=None) -> dict | None:
    """Pin a new edition in the unsigned sidecar. Never writes edition JSON."""
    slug = _s(narrative.get("slug"))
    if not slug:
        return None
    if not edition_pin_allowed(slug, root=root):
        return None
    pin = select_catalog_pin(narrative, root=root)
    if not pin:
        return None
    write_sidecar_pin(slug, pin, root=root)
    return pin


def write_sidecar_pin(slug: str, pin: dict, *, root=None) -> dict:
    root = Path(root or config.REPO_ROOT)
    path = root / "data" / "hero_posts.json"
    payload = load_sidecar(root)
    pins = payload.get("pins")
    if not isinstance(pins, dict):
        pins = {}
    pins[slug] = {
        "id": pin["id"],
        "handle": pin["handle"],
        "postedAt": pin.get("postedAt") or "",
        "postedLabel": pin.get("postedLabel") or "",
        "text": pin.get("text") or "",
        "alt": pin.get("alt") or "",
        "url": pin["url"],
        "sponsored": False,
        "mediaReviewed": True,
    }
    payload = {
        "schemaVersion": SCHEMA_VERSION,
        "allowlist": list(ALLOWLIST_DISPLAY),
        "catalog": payload.get("catalog") or [],
        "pins": pins,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return pin


def sidecar_for_hugo(payload: dict) -> dict:
    pins = {}
    for slug, row in (payload.get("pins") or {}).items():
        pin = normalize_pin(row)
        if pin:
            pins[slug] = pin
    catalog = []
    for row in payload.get("catalog") or []:
        pin = normalize_pin(row)
        if not pin:
            continue
        entry = dict(pin)
        for key in ("week", "seasonType", "opponentAbbr", "homeAway", "mode"):
            if key in row:
                entry[key] = row[key]
        catalog.append(entry)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "allowlist": list(ALLOWLIST_DISPLAY),
        "catalog": catalog,
        "pins": pins,
    }
